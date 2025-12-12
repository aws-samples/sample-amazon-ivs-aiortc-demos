#!/usr/bin/env python3

# Apply H.264 SEI patch BEFORE importing aiortc
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
import stages_sei.h264_sei_patch

import asyncio
import json
import logging
import argparse
import base64
import requests
import warnings
import os
from typing import Dict, Any, List, Optional
from aiortc import (
    RTCBundlePolicy,
    RTCConfiguration,
    RTCPeerConnection,
    RTCSessionDescription,
    MediaStreamTrack,
)
import av

# Monkey patch aioice to reduce ICE gathering timeout from default 5s
import aioice.ice

# Global variable for ICE timeout (will be set from command line argument)
ICE_TIMEOUT = 1  # Default 1 second

# Store original function
_original_get_component_candidates = aioice.ice.Connection.get_component_candidates


async def patched_get_component_candidates(self, component, addresses, timeout=None):
    """Patched version with configurable timeout instead of 5"""
    if timeout is None:
        timeout = ICE_TIMEOUT
    return await _original_get_component_candidates(self, component, addresses, timeout)


# Apply the patch
aioice.ice.Connection.get_component_candidates = patched_get_component_candidates

# Local imports
from agent_video_track import AgentVideoTrack
from agent_audio_track import AgentAudioTrack
from bedrock_stream_manager import BedrockStreamManager

# Suppress warnings
warnings.filterwarnings("ignore")

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("ivs-stage-nova-s2s")
logger.setLevel(logging.INFO)

# Set DEBUG level for our modules
agent_audio_logger = logging.getLogger("agent_audio_track")
agent_audio_logger.setLevel(logging.INFO)
bedrock_logger = logging.getLogger("bedrock_stream_manager")
bedrock_logger.setLevel(logging.INFO)
agent_tools_logger = logging.getLogger("agent_tools")
agent_tools_logger.setLevel(logging.INFO)

# Suppress noisy external loggers
aiortc_logger = logging.getLogger("aiortc")
aiortc_logger.setLevel(logging.ERROR)
aioice_logger = logging.getLogger("aioice")
aioice_logger.setLevel(logging.CRITICAL)
stun_logger = logging.getLogger("aioice.stun")
stun_logger.setLevel(logging.CRITICAL)

# Suppress AWS SDK debug noise
aws_event_stream_logger = logging.getLogger("smithy_aws_event_stream")
aws_event_stream_logger.setLevel(logging.WARNING)
aws_event_stream_aio_logger = logging.getLogger("smithy_aws_event_stream.aio")
aws_event_stream_aio_logger.setLevel(logging.WARNING)
aws_bedrock_logger = logging.getLogger("aws_sdk_bedrock_runtime")
aws_bedrock_logger.setLevel(logging.WARNING)
smithy_core_logger = logging.getLogger("smithy_aws_core")
smithy_core_logger.setLevel(logging.WARNING)

# Log that the aioice timeout patch was applied
logger.info(f"🧊 Applied aioice timeout patch: ICE gathering timeout reduced from 5s to {ICE_TIMEOUT}s")

# Audio configuration for Nova
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
CHANNELS = 1
# CHUNK_SIZE is now calculated automatically based on timing (40ms chunks)
# This provides better audio quality and reduces choppiness


def parse_jwt(token: str) -> Dict[str, Any]:
    """Parse JWT token without verification to extract payload"""
    try:
        parts: List[str] = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT format")

        payload: str = parts[1]
        # Add padding if needed
        payload += "=" * (4 - len(payload) % 4)
        decoded_bytes: bytes = base64.urlsafe_b64decode(payload)
        payload_json: Dict[str, Any] = json.loads(decoded_bytes.decode("utf-8"))

        return payload_json
    except Exception as e:
        logger.error(f"Error parsing JWT: {e}")
        return {}


def validate_token_capability(token_payload: Dict[str, Any], capability: str) -> bool:
    """
    Validate that the token has the specified capability

    Args:
        token_payload: Parsed JWT token payload
        capability: Capability to validate ("publish" or "subscribe")

    Returns:
        True if token has the capability, False otherwise
    """
    capabilities = token_payload.get("capabilities", {})
    capability_key = f"allow_{capability}"
    has_capability = capabilities.get(capability_key, False)

    if not has_capability:
        logger.error(f"Token does not have {capability} capabilities (capabilities.{capability_key} != true)")
        return False

    logger.info(f"✅ Token has {capability} capabilities")
    return True


def fix_ivs_answer_sdp(sdp: str) -> str:
    """Fix IVS's SDP answer to ensure ICE candidates are in both audio and video sections"""

    lines = sdp.split("\n")
    ice_candidates = []

    # First pass: collect all ICE candidates from audio section
    in_audio_section = False
    for line in lines:
        if line.startswith("m=audio"):
            in_audio_section = True
        elif line.startswith("m=video") or line.startswith("m=application"):
            in_audio_section = False

        # Collect ICE candidates from audio section
        if in_audio_section and line.startswith("a=candidate:"):
            ice_candidates.append(line)

    # Second pass: add candidates to video section
    fixed_lines = []
    in_video_section = False
    candidates_added = False

    for i, line in enumerate(lines):
        if line.startswith("m=video"):
            in_video_section = True
            candidates_added = False
        elif line.startswith("m=") and not line.startswith("m=video"):
            in_video_section = False

        # Check if we're at the end of video section
        if in_video_section and not candidates_added:
            # Look ahead to see if this is the last line of video section
            is_last_line = i == len(lines) - 1
            next_is_new_section = i < len(lines) - 1 and lines[i + 1].startswith("m=")
            is_empty_line = i < len(lines) - 1 and lines[i + 1].strip() == ""

            # If this is the last line of video section, add candidates after it
            if is_last_line or next_is_new_section or is_empty_line:
                fixed_lines.append(line)
                # Add all the ICE candidates from audio section
                for candidate in ice_candidates:
                    fixed_lines.append(candidate)
                # Add end-of-candidates
                fixed_lines.append("a=end-of-candidates")
                candidates_added = True
                continue

        fixed_lines.append(line)

    result = "\n".join(fixed_lines)

    return result


async def get_remote_sdp(url: str, token: str, sdp_offer: str, max_redirects: int = 5) -> Optional[str]:
    """
    Send a WebRTC offer to a WHIP/WHEP endpoint and return the SDP answer.
    Handles redirects while preserving Authorization headers.

    Args:
        url: The WHIP or WHEP endpoint URL
        token: JWT token for authorization
        sdp_offer: SDP offer string
        max_redirects: Maximum number of redirects to follow

    Returns:
        SDP answer string if successful, None if failed
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"}
    current_url = url
    attempt = 1

    logger.info(f"Sending WebRTC offer to endpoint: {current_url}")

    while attempt <= max_redirects:
        logger.info(f"Sending request to: {current_url} (attempt {attempt})")

        try:
            response = requests.post(
                current_url,
                data=sdp_offer,
                headers=headers,
                allow_redirects=False,
                timeout=10,  # Add explicit timeout of 10 seconds
            )

            if response.status_code in [301, 302, 303, 307, 308]:
                # Handle redirect manually to preserve Authorization header
                redirect_url = response.headers.get("Location")
                if redirect_url:
                    logger.info(f"Redirect {attempt}: {current_url} -> {redirect_url}")
                    current_url = redirect_url
                    attempt += 1
                    continue
                else:
                    logger.error("Redirect response missing Location header")
                    return None
            elif response.status_code == 201:
                logger.info(f"✅ WebRTC offer accepted, received SDP answer")
                return response.text
            else:
                logger.error(f"WebRTC request failed with status {response.status_code}: {response.text}")
                return None

        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            return None

    logger.error(f"Too many redirects (>{max_redirects})")
    return None


async def join_stage_as_publisher(token: str, agent_audio_track: AgentAudioTrack, agent_video_track: AgentVideoTrack):
    """Join the IVS stage as a publisher using WebRTC with Nova audio and agent video"""
    logger.info("🚀 Joining stage as publisher with Nova audio and agent video...")

    # Create peer connection
    config = RTCConfiguration()
    config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
    pc = RTCPeerConnection(config)

    # Use global WHIP base URL for publishing
    whip_base_url = "https://global.whip.live-video.net"
    logger.info(f"🔗 WHIP Base URL: {whip_base_url}")

    # Add tracks to peer connection
    logger.info("🔈 Adding Nova audio track")
    pc.addTransceiver(agent_audio_track, direction="sendrecv")

    logger.info("🔵 Adding agent video track")
    pc.addTransceiver(agent_video_track, direction="sendrecv")

    # Set peer connection for WebRTC stats collection
    logger.info("🔗 About to set peer connection on audio track...")
    try:
        agent_audio_track.set_peer_connection(pc)
        logger.info("🔗 Peer connection set successfully")
    except Exception as e:
        logger.error(f"❌ Failed to set peer connection: {e}")
        import traceback

        traceback.print_exc()

    logger.info("➕ Added tracks")

    await pc.setLocalDescription(await pc.createOffer())

    # Send offer and get answer using consolidated function
    answer_sdp = await get_remote_sdp(whip_base_url, token, pc.localDescription.sdp)

    if not answer_sdp:
        logger.error("❌ Failed to get SDP answer from WHIP endpoint")
        return None

    # Set remote description from answer
    logger.info("🔧 Setting remote description from IVS answer...")

    # Fix the IVS answer SDP to add ICE candidates to video section
    fixed_answer_sdp = fix_ivs_answer_sdp(answer_sdp)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=fixed_answer_sdp, type="answer"))

    logger.info("✅ Successfully joined stage as publisher with Nova audio and agent video")
    return pc


async def subscribe_to_participant(token: str, participant_id: str, nova_stream_manager: BedrockStreamManager):
    """Subscribe to a participant's audio/video streams and process audio through Nova"""
    logger.info(f"🎧 Subscribing to participant: {participant_id}")

    # Create peer connection
    config = RTCConfiguration()
    config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
    pc = RTCPeerConnection(config)

    # Add transceivers for receiving audio and video
    pc.addTransceiver("audio", direction="recvonly")
    pc.addTransceiver("video", direction="recvonly")

    # Audio processing state
    resampler = None

    # Connection state tracking
    pc._should_exit = False

    @pc.on("connectionstatechange")
    def on_connectionstatechange():
        logger.info(f"🔗 Connection state changed to: {pc.connectionState}")
        if pc.connectionState == "closed":
            logger.info(f"🚪 Participant {participant_id} has left - marking for graceful exit")
            pc._should_exit = True

    @pc.on("iceconnectionstatechange")
    def on_iceconnectionstatechange():
        logger.info(f"🧊 ICE connection state changed to: {pc.iceConnectionState}")

    @pc.on("icegatheringstatechange")
    def on_icegatheringstatechange():
        logger.info(f"🧊 ICE gathering state changed to: {pc.iceGatheringState}")

    @pc.on("signalingstatechange")
    def on_signalingstatechange():
        logger.info(f"📡 Signaling state changed to: {pc.signalingState}")

    @pc.on("track")
    def on_track(track: MediaStreamTrack):
        logger.info(f"📺 Received {track.kind} track from participant {participant_id}")
        logger.info(f"Track ID: {track.id}")
        logger.info(f"Track readyState: {track.readyState}")

        if track.kind == "audio":
            logger.info("🔊 Audio track received - processing through Nova")
            logger.info("Creating audio processing task...")
            task = asyncio.create_task(process_audio_track(track))
            logger.info(f"Audio processing task created: {task}")
        elif track.kind == "video":
            logger.info("🎥 Video track received - ignoring for now")
            # Create a task for video processing (just consuming frames)
            task = asyncio.create_task(process_video_track(track))
            logger.info(f"Video processing task created: {task}")

    async def process_audio_track(track: MediaStreamTrack):
        """Process audio track in a separate async task"""
        nonlocal resampler

        logger.info("🎵 Starting audio processing task")
        logger.info(f"Audio track readyState: {track.readyState}")

        # Wait for connection to be established
        logger.info("Waiting for WebRTC connection to be established...")
        while pc.connectionState not in ["connected", "completed"]:
            logger.info(f"Connection state: {pc.connectionState}, waiting...")
            await asyncio.sleep(0.1)
        logger.info(f"✅ Connection established: {pc.connectionState}")

        # Initialize resampler for Nova's expected format
        resampler = av.AudioResampler(format="s16", layout="mono", rate=nova_stream_manager.input_sample_rate)

        # Note: No longer starting base audio content session since we handle per-participant sessions
        # Each participant will automatically start their own content session when they send audio

        # Process audio frames
        try:
            frame_count = 0
            while True:
                try:
                    # Add timeout to recv() to avoid infinite blocking
                    frame = await asyncio.wait_for(track.recv(), timeout=5.0)
                    frame_count += 1

                    # Resample to Nova's expected format (16kHz, mono, s16)
                    resampled_frames = resampler.resample(frame)

                    for i, resampled_frame in enumerate(resampled_frames):
                        # Convert to bytes and send directly to Nova
                        audio_bytes = resampled_frame.to_ndarray().tobytes()
                        nova_stream_manager.add_audio_chunk(audio_bytes)

                except asyncio.TimeoutError:
                    logger.warning(f"Timeout waiting for audio frame {frame_count} - no audio data received in 5 seconds")
                    logger.info(f"Track readyState: {track.readyState}, Connection state: {pc.connectionState}")
                    # Continue trying instead of breaking
                    continue

        except Exception as e:
            logger.error(f"Audio track processing error for participant {participant_id}: {e}")
            import traceback

            traceback.print_exc()

    async def process_video_track(track: MediaStreamTrack):
        """Process video track in a separate async task"""
        logger.info("🎬 Starting video processing task")
        try:
            frame_count = 0
            while True:
                frame = await track.recv()
                nova_stream_manager.frame = frame
                frame_count += 1
        except Exception as e:
            logger.info(f"Video track ended for participant {participant_id}: {e}")

    # Create offer
    await pc.setLocalDescription(await pc.createOffer())

    # Parse token to get WHIP URL
    token_payload = parse_jwt(token)
    if "whip_url" not in token_payload:
        logger.error("No whip_url found in token payload")
        return None

    whip_base_url = token_payload["whip_url"]
    whep_url = f"{whip_base_url}/subscribe/{participant_id}"
    logger.info(f"🔗 WHEP URL: {whep_url}")

    # Send offer and get answer using consolidated function
    answer_sdp = await get_remote_sdp(whep_url, token, pc.localDescription.sdp)

    if not answer_sdp:
        logger.error("❌ Failed to get SDP answer from WHEP endpoint")
        return None

    # Set remote description from answer
    logger.info("🔧 Setting remote description from IVS answer...")
    fixed_answer_sdp = fix_ivs_answer_sdp(answer_sdp)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=fixed_answer_sdp, type="answer"))

    logger.info("✅ Successfully subscribed to participant with Nova processing")
    return pc


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="IVS Stage Publisher/Subscriber with Nova Speech-to-Speech")
    parser.add_argument("--token", required=True, help="IVS stage participant token")
    parser.add_argument("--subscribe-to", required=True, help="Participant ID to subscribe to")

    # Nova options
    parser.add_argument("--nova-model-id", default="amazon.nova-2-sonic-v1:0", help="Nova model ID")
    parser.add_argument("--nova-region", default="us-east-1", help="AWS region for Nova")

    # Frame analysis options
    parser.add_argument(
        "--disable-frame-analysis",
        action="store_true",
        help="Disable video frame analysis (default: enabled)",
    )
    parser.add_argument(
        "--bedrock-region",
        default="us-east-1",
        help="AWS region for Bedrock service (default: us-east-1)",
    )
    parser.add_argument(
        "--bedrock-model-id",
        default="us.anthropic.claude-sonnet-4-20250514-v1:0",
        help="Bedrock model ID for frame analysis (default: us.anthropic.claude-sonnet-4-20250514-v1:0)",
    )

    # Performance options
    parser.add_argument(
        "--ice-timeout",
        type=int,
        default=1,
        help="ICE gathering timeout in seconds (default: 1, original: 5)",
    )

    # Weather API options
    parser.add_argument(
        "--weather-api-key",
        help="Weather API key for weather tool functionality (overrides WEATHER_API_KEY environment variable)",
    )

    # Web search API options
    parser.add_argument(
        "--brave-api-key",
        help="Brave Search API key for web search tool functionality (overrides BRAVE_API_KEY environment variable)",
    )

    return parser.parse_args()


async def main():
    """Main function that handles both publishing and subscribing with Nova speech-to-speech"""
    args = parse_args()

    # Set global ICE timeout from command line argument
    global ICE_TIMEOUT
    ICE_TIMEOUT = args.ice_timeout

    # Handle frame analysis flag logic (enabled by default, disabled if --disable-frame-analysis is used)
    enable_frame_analysis = not args.disable_frame_analysis

    logger.info("🎬 Starting IVS Stage Publisher/Subscriber with Nova Speech-to-Speech")
    logger.info(f"🧊 ICE gathering timeout set to {ICE_TIMEOUT}s (original: 5s)")
    logger.info(f"🔑 Using token: {args.token[:50]}... (truncated)")
    logger.info(f"🤖 Nova model: {args.nova_model_id}")
    logger.info(f"🌍 Nova region: {args.nova_region}")
    logger.info(f"🔍 Frame analysis: {'enabled' if enable_frame_analysis else 'disabled'}")
    logger.info(f"🧠 Analysis model: {args.bedrock_model_id}")
    logger.info(f"🌍 Analysis region: {args.bedrock_region}")
    token_payload = parse_jwt(args.token)

    if not token_payload:
        logger.error("Failed to parse token payload")
        return

    # Validate capabilities
    if not validate_token_capability(token_payload, "publish"):
        logger.error("❌ Token missing publish capabilities")
        return

    if args.subscribe_to and not validate_token_capability(token_payload, "subscribe"):
        logger.error("❌ Token missing subscribe capabilities")
        return

    # Extract required fields from token
    events_url = token_payload.get("events_url")
    topic = token_payload.get("topic")
    jti = token_payload.get("jti")

    logger.info(f"ℹ️  Stage Events URL: {events_url}")
    logger.info(f"ℹ️  Topic: {topic}")
    logger.info(f"ℹ️  JTI: {jti}")

    try:
        connections = []
        nova_stream_manager = None

        # Create agent video track for visualization
        agent_video_track = AgentVideoTrack(width=640, height=360, fps=20)

        # Create Nova audio track for publishing responses (with agent video reference)
        # fmt:off
        agent_audio_track = AgentAudioTrack(
            agent_video_track=agent_video_track, 
            sample_rate=OUTPUT_SAMPLE_RATE, 
            channels=CHANNELS
        )
        # fmt:on

        # Get weather API key from argument or environment variable
        weather_api_key = args.weather_api_key or os.getenv("WEATHER_API_KEY")

        # Get Brave Search API key from argument or environment variable
        brave_api_key = args.brave_api_key or os.getenv("BRAVE_API_KEY")

        # Initialize Nova stream manager
        logger.info("🤖 Initializing Nova speech-to-speech...")
        # fmt:off
        nova_stream_manager = BedrockStreamManager(
            agent_audio_track=agent_audio_track, 
            agent_video_track=agent_video_track, 
            model_id=args.nova_model_id, 
            region=args.nova_region,
            weather_api_key=weather_api_key,
            brave_api_key=brave_api_key,
            enable_frame_analysis=enable_frame_analysis,
            analysis_model_id=args.bedrock_model_id,
            analysis_region=args.bedrock_region
        )
        # fmt:on
        await nova_stream_manager.initialize_stream()

        # Start subscribing to participants if specified
        if args.subscribe_to:
            participant_id = args.subscribe_to
            logger.info(f"📥 Starting subscribe mode for participant: {participant_id}")

            subscribe_pc = await subscribe_to_participant(args.token, participant_id, nova_stream_manager)

            if subscribe_pc:
                logger.info(f"✅ Successfully subscribed to {participant_id} with Nova processing")
                connections.append(subscribe_pc)
            else:
                logger.error(f"❌ Failed to subscribe to {participant_id}")

        if not connections:
            logger.error("❌ No connections established")
            return

        # Start publishing
        logger.info("📤 Starting publish mode with Nova audio and agent video...")
        publish_pc = await join_stage_as_publisher(args.token, agent_audio_track, agent_video_track)

        if publish_pc:
            logger.info("🎉 WebRTC publishing established with Nova audio and waveform video!")
            connections.append(publish_pc)
        else:
            logger.error("❌ Failed to establish WebRTC publishing")
            return

        # Keep all connections alive
        try:
            logger.info(f"🔄 {len(connections)-1} connection(s) active with Nova speech-to-speech. Press Ctrl+C to exit.")
            logger.info("🎙️  Speak and Nova will respond through the IVS stage!")
            logger.info(
                "ℹ️  Amazon Nova Sonic may not always get it right. "
                "Amazon Nova Sonic currently does not support session history "
                "and conversations will end after 8 minutes."
            )
            while True:
                await asyncio.sleep(1)  # Keep the event loop running

                # Check if any connection is marked for exit (participant left)
                for pc in connections:
                    if hasattr(pc, "_should_exit") and pc._should_exit:
                        logger.info("🚪 Participant has left the conversation - exiting gracefully")
                        return
        except KeyboardInterrupt:
            logger.info("🛑 Shutting down...")
        finally:
            # Clean up Nova stream manager
            if nova_stream_manager:
                logger.info("🤖 Closing Nova stream...")
                await nova_stream_manager.close()

            # Clean up all peer connections
            logger.info("🔌 Closing all connections...")
            for pc in connections:
                await pc.close()
            logger.info("✅ All connections closed")

    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
    except Exception as e:
        logger.error(f"❌ Error in main: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
