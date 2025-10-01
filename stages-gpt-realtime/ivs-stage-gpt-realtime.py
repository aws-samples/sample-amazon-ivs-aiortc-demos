#!/usr/bin/env python3

# Apply H.264 SEI patches BEFORE importing aiortc
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
import stages_sei.h264_sei_patch
import stages_sei.h264_sei_decoder_patch
from stages_sei import SeiSubscriber, log_sei_message, set_global_sei_subscriber
from stages_sei.sei_subscriber import ReceivedSeiMessage

# Global SEI subscriber for packet interception
global_sei_subscriber = None
global_gpt_manager = None


def handle_received_sei_message(sei_message: ReceivedSeiMessage):
    """
    Handle received SEI messages from the video stream.

    Args:
        sei_message: The received SEI message object
    """
    global global_gpt_manager

    try:
        # Parse the message payload
        message_data = sei_message.to_dict()
        payload = message_data.get("payload", {})

        # Log the received message
        logger.info(f"📡 Received SEI message: {payload}")

        # Handle different types of SEI messages
        if isinstance(payload, dict):
            # Check for specific message types
            message_type = payload.get("type")
            sender = payload.get("sender", "unknown")
            content = payload.get("content") or payload.get("message")

            if message_type == "chat":
                logger.info(f"💬 Chat message from {sender}: {content}")
                # Could potentially add to conversation context
            elif message_type == "user_input":
                logger.info(f"🎤 User input from {sender}: {content}")
                # Could potentially inject as user message
            elif message_type == "assistant_response":
                logger.info(f"🤖 Assistant response from {sender}: {content}")
                # Could display or log assistant responses from other participants
            elif message_type == "system":
                logger.info(f"⚙️ System message from {sender}: {content}")
                # Handle system notifications
            elif message_type == "metadata":
                # Handle metadata messages (participant info, etc.)
                logger.info(f"📊 Metadata from {sender}: {content or payload}")
            elif message_type == "command":
                # Handle command messages (e.g., mute, unmute, etc.)
                command = payload.get("command")
                logger.info(f"🎮 Command from {sender}: {command}")
                handle_sei_command(command, payload)
            else:
                # Handle generic messages or unknown formats
                if content:
                    logger.info(f"📝 Message from {sender}: {content}")
                else:
                    logger.info(f"📦 Data from {sender}: {payload}")
        else:
            # Handle non-dict payloads (strings, etc.)
            logger.info(f"📄 Raw SEI message: {payload}")
    except Exception as e:
        logger.error(f"❌ Error handling SEI message: {e}")
        logger.debug(f"SEI message data: {sei_message.to_dict()}")


def handle_sei_command(command: str, payload: dict):
    """
    Handle SEI command messages.

    Args:
        command: The command string
        payload: The full message payload
    """
    try:
        if command == "mute":
            logger.info("🔇 Received mute command via SEI")
            # Could integrate with audio track muting here
        elif command == "unmute":
            logger.info("🔊 Received unmute command via SEI")
            # Could integrate with audio track unmuting here
        elif command == "ping":
            logger.info("🏓 Received ping command via SEI")
            # Could send a pong response back
        elif command == "status_request":
            logger.info("📊 Received status request via SEI")
            # Could send back system status
        else:
            logger.info(f"❓ Unknown SEI command: {command}")

    except Exception as e:
        logger.error(f"❌ Error handling SEI command '{command}': {e}")


def setup_global_sei_hooks():
    """Setup global hooks for SEI extraction at the PyAV level"""
    global global_sei_subscriber

    # Disable global hooks for now to avoid connection issues
    logger.info("📡 Global SEI hooks disabled to prevent connection interference")
    return False

    if not global_sei_subscriber:
        return

    try:
        import av

        # Hook into PyAV Packet processing
        if hasattr(av.Packet, "__bytes__"):
            original_packet_bytes = av.Packet.__bytes__

            def sei_aware_packet_bytes(self):
                """Wrapper for Packet.__bytes__ that extracts SEI"""
                packet_bytes = original_packet_bytes(self)

                try:
                    # Check if this looks like H.264 video data
                    if len(packet_bytes) > 4:
                        # Periodically log that we're intercepting packets
                        import time

                        current_time = time.time()
                        if not hasattr(self, "_last_sei_log"):
                            self._last_sei_log = 0

                        if (current_time - self._last_sei_log) > 5.0:  # Log every 5 seconds
                            self._last_sei_log = current_time
                            logger.debug(f"📡 Global packet hook: processing {len(packet_bytes)} bytes")

                        # Run SEI extraction asynchronously to avoid blocking
                        import asyncio

                        try:
                            loop = asyncio.get_event_loop()
                            if loop and not loop.is_closed():
                                asyncio.create_task(global_sei_subscriber.process_packet_data(packet_bytes))
                        except RuntimeError:
                            # No event loop running, skip SEI extraction
                            pass
                except Exception as e:
                    # Don't log here to avoid spam, just silently continue
                    pass

                return packet_bytes

            av.Packet.__bytes__ = sei_aware_packet_bytes
            logger.info("📡 Hooked into PyAV Packet.__bytes__ for global SEI extraction")
            return True

    except Exception as e:
        logger.debug(f"Could not setup global SEI hooks: {e}")
        return False

    return False  # Default return if no hooks were installed


import asyncio
import json
import logging
import argparse
import base64
import requests
import warnings
import websockets
import numpy as np
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
from gpt_realtime_audio_track import GptRealtimeAudioTrack
from gpt_realtime_video_track import GptRealtimeVideoTrack
from gpt_realtime_manager import GptRealtimeManager

# Suppress warnings
warnings.filterwarnings("ignore")

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("ivs-stage-gpt-realtime")
logger.setLevel(logging.INFO)

# Set DEBUG level for our modules
gpt_realtime_audio_logger = logging.getLogger("gpt_realtime_audio_track")
gpt_realtime_audio_logger.setLevel(logging.INFO)
gpt_realtime_realtime_logger = logging.getLogger("gpt_realtime_realtime_manager")
gpt_realtime_realtime_logger.setLevel(logging.INFO)
sei_logger = logging.getLogger("stages_sei.sei_subscriber")
sei_logger.setLevel(logging.DEBUG)
patch_logger = logging.getLogger("stages_sei.h264_sei_decoder_patch")
patch_logger.setLevel(logging.DEBUG)

# Suppress noisy external loggers
aiortc_logger = logging.getLogger("aiortc")
aiortc_logger.setLevel(logging.ERROR)
aioice_logger = logging.getLogger("aioice")
aioice_logger.setLevel(logging.CRITICAL)
stun_logger = logging.getLogger("aioice.stun")
stun_logger.setLevel(logging.CRITICAL)

# Log that the aioice timeout patch was applied
logger.info(f"🧊 Applied aioice timeout patch: ICE gathering timeout reduced from 5s to {ICE_TIMEOUT}s")

# Audio configuration for OpenAI real-time API
INPUT_SAMPLE_RATE = 24000  # OpenAI real-time API expects 24kHz
OUTPUT_SAMPLE_RATE = 24000
CHANNELS = 1


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


async def join_stage_as_publisher(token: str, gpt_realtime_audio_track: GptRealtimeAudioTrack, gpt_realtime_video_track: GptRealtimeVideoTrack):
    """Join the IVS stage as a publisher using WebRTC with gpt-realtime audio and video"""
    logger.info("🚀 Joining stage as publisher with gpt-realtime audio and video...")

    # Create peer connection
    config = RTCConfiguration()
    config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
    pc = RTCPeerConnection(config)

    # Use global WHIP base URL for publishing
    whip_base_url = "https://global.whip.live-video.net"
    logger.info(f"🔗 WHIP Base URL: {whip_base_url}")

    # Add tracks to peer connection
    logger.info("🔈 Adding OpenAI audio track")
    pc.addTransceiver(gpt_realtime_audio_track, direction="sendrecv")

    logger.info("🔵 Adding OpenAI video track")
    pc.addTransceiver(gpt_realtime_video_track, direction="sendrecv")

    # Set peer connection for WebRTC stats collection
    logger.info("🔗 About to set peer connection on audio track...")
    try:
        gpt_realtime_audio_track.set_peer_connection(pc)
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

    logger.info("✅ Successfully joined stage as publisher with OpenAI audio and video")
    return pc


async def subscribe_to_participant(
    token: str, participant_id: str, gpt_realtime_realtime_manager: GptRealtimeManager, enable_sei_subscription: bool = True
):
    """Subscribe to a participant's audio/video streams and process audio through OpenAI"""
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

    # SEI subscriber for extracting metadata from video (if enabled)
    sei_subscriber = None
    if enable_sei_subscription:
        sei_subscriber = SeiSubscriber(message_callback=handle_received_sei_message)
        logger.info("📡 SEI subscriber initialized for incoming video metadata")

        # Set up global decoder patch for SEI extraction
        set_global_sei_subscriber(sei_subscriber)
        logger.info("📡 Global SEI subscriber set for decoder patches")

        # Set up additional global hooks for packet interception
        global global_sei_subscriber
        global_sei_subscriber = sei_subscriber
        hook_success = setup_global_sei_hooks()
        if hook_success:
            logger.info("📡 Additional SEI packet hooks installed successfully")
        else:
            logger.debug("📡 Additional packet hooks not available (decoder patches should handle SEI extraction)")
    else:
        logger.info("📡 SEI subscription disabled")

    # Disable complex packet hooks for now to avoid connection issues
    if enable_sei_subscription and sei_subscriber:
        logger.info("📡 SEI extraction will rely on frame-level processing (packet hooks disabled to prevent connection issues)")

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
            logger.info("🔊 Audio track received - processing through OpenAI")
            logger.info("Creating audio processing task...")
            task = asyncio.create_task(process_audio_track(track))
            logger.info(f"Audio processing task created: {task}")
        elif track.kind == "video":
            logger.info("🎥 Video track received - processing frames and SEI messages")

            # Create a task for video processing (frames + SEI extraction)
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

        # Initialize resampler for OpenAI's expected format (24kHz)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=INPUT_SAMPLE_RATE)

        # Process audio frames
        try:
            frame_count = 0
            while True:
                try:
                    # Add timeout to recv() to avoid infinite blocking
                    frame = await asyncio.wait_for(track.recv(), timeout=5.0)
                    frame_count += 1

                    # Resample to OpenAI's expected format (24kHz, mono, s16)
                    resampled_frames = resampler.resample(frame)

                    for i, resampled_frame in enumerate(resampled_frames):
                        # Convert to bytes and send directly to OpenAI
                        audio_bytes = resampled_frame.to_ndarray().tobytes()
                        await gpt_realtime_realtime_manager.add_audio_chunk(audio_bytes)

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
        logger.info("🎬 Starting video processing task with SEI extraction")
        try:
            frame_count = 0
            while True:
                frame = await track.recv()
                frame_count += 1

                # Set current frame for analysis
                gpt_realtime_realtime_manager.set_current_frame(frame)

                # Extract SEI messages from video frame (if enabled)
                if sei_subscriber:
                    try:
                        sei_messages = await sei_subscriber.process_frame(frame)
                        if sei_messages:
                            logger.info(f"📡 Extracted {len(sei_messages)} SEI messages from frame {frame_count}")
                    except Exception as sei_error:
                        logger.debug(f"SEI extraction error on frame {frame_count}: {sei_error}")

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

    logger.info("✅ Successfully subscribed to participant with OpenAI processing")
    return pc


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="IVS Stage Publisher/Subscriber with OpenAI Real-time API")
    parser.add_argument("--token", required=True, help="IVS stage participant token")
    parser.add_argument("--subscribe-to", required=True, help="Participant ID to subscribe to")

    # OpenAI options
    parser.add_argument(
        "--openai-key",
        help="OpenAI API key (overrides OPENAI_API_KEY environment variable)",
    )
    parser.add_argument(
        "--model",
        default="gpt-realtime",
        help="OpenAI model to use (default: gpt-realtime)",
    )
    parser.add_argument(
        "--voice",
        default="cedar",
        choices=["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"],
        help="Voice to use for responses (default: cedar)",
    )

    # Frame analysis options
    parser.add_argument(
        "--disable-frame-analysis",
        action="store_true",
        help="Disable video frame analysis (default: enabled)",
    )
    # VAD (Voice Activity Detection) options
    parser.add_argument(
        "--vad-mode",
        choices=["server_vad", "semantic_vad"],
        default="server_vad",
        help="VAD mode: server_vad (silence-based) or semantic_vad (context-aware, default: server_vad)",
    )
    parser.add_argument(
        "--vad-threshold",
        type=float,
        default=0.5,
        help="VAD sensitivity threshold for server_vad (0.0-1.0, lower = more sensitive, default: 0.5)",
    )
    parser.add_argument(
        "--vad-prefix-padding-ms",
        type=int,
        default=300,
        help="Audio padding before speech detection for server_vad in milliseconds (default: 300)",
    )
    parser.add_argument(
        "--vad-silence-duration-ms",
        type=int,
        default=500,
        help="Silence duration to end speech detection for server_vad in milliseconds (default: 500)",
    )
    parser.add_argument(
        "--vad-eagerness",
        choices=["low", "medium", "high", "auto"],
        default="medium",
        help="Eagerness for semantic_vad: low (patient), medium (balanced), high (responsive), auto (default: medium)",
    )

    # Performance options
    parser.add_argument(
        "--ice-timeout",
        type=int,
        default=1,
        help="ICE gathering timeout in seconds (default: 1, original: 5)",
    )

    # SEI options
    parser.add_argument(
        "--disable-sei-subscription",
        action="store_true",
        help="Disable SEI message subscription from incoming video (default: enabled)",
    )

    return parser.parse_args()


async def main():
    """Main function that handles both publishing and subscribing with OpenAI real-time API"""
    args = parse_args()

    # Set global ICE timeout from command line argument
    global ICE_TIMEOUT
    ICE_TIMEOUT = args.ice_timeout

    logger.info("🎬 Starting IVS Stage Publisher/Subscriber with OpenAI Real-time API")
    logger.info(f"🧊 ICE gathering timeout set to {ICE_TIMEOUT}s (original: 5s)")
    logger.info(f"🔑 Using token: {args.token[:50]}... (truncated)")
    logger.info(f"🤖 OpenAI model: {args.model}")
    logger.info(f"🗣️ Voice: {args.voice}")
    logger.info(f"🔍 Frame analysis: {'enabled' if not args.disable_frame_analysis else 'disabled'}")
    if not args.disable_frame_analysis:
        logger.info("🧠 Using OpenAI native image processing")

    logger.info(f"📡 SEI subscription: {'enabled' if not args.disable_sei_subscription else 'disabled'}")
    if not args.disable_sei_subscription:
        logger.info("📡 Will extract SEI messages from incoming video streams")

    logger.info(f"🎤 VAD mode: {args.vad_mode}")
    if args.vad_mode == "server_vad":
        logger.info(f"🎤 VAD threshold: {args.vad_threshold} (lower = more sensitive)")
        logger.info(f"🎤 VAD prefix padding: {args.vad_prefix_padding_ms}ms")
        logger.info(f"🎤 VAD silence duration: {args.vad_silence_duration_ms}ms")
    elif args.vad_mode == "semantic_vad":
        logger.info(f"🎤 VAD eagerness: {args.vad_eagerness}")

    # Get OpenAI API key from argument or environment variable
    openai_api_key = args.openai_key or os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        logger.error("❌ OpenAI API key not provided. Use --openai-key or set OPENAI_API_KEY environment variable")
        return

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
        gpt_realtime_realtime_manager = None

        # Create OpenAI video track for visualization
        gpt_realtime_video_track = GptRealtimeVideoTrack(width=1280, height=720, fps=25)

        # Create OpenAI audio track for publishing responses
        gpt_realtime_audio_track = GptRealtimeAudioTrack(
            gpt_realtime_video_track=gpt_realtime_video_track, sample_rate=OUTPUT_SAMPLE_RATE, channels=CHANNELS
        )

        # Initialize OpenAI real-time manager
        logger.info("🤖 Initializing gpt-realtime API...")
        gpt_realtime_realtime_manager = GptRealtimeManager(
            gpt_realtime_audio_track=gpt_realtime_audio_track,
            gpt_realtime_video_track=gpt_realtime_video_track,
            api_key=openai_api_key,
            model=args.model,
            voice=args.voice,
            enable_frame_analysis=not args.disable_frame_analysis,
            vad_mode=args.vad_mode,
            vad_threshold=args.vad_threshold,
            vad_prefix_padding_ms=args.vad_prefix_padding_ms,
            vad_silence_duration_ms=args.vad_silence_duration_ms,
            vad_eagerness=args.vad_eagerness,
        )
        await gpt_realtime_realtime_manager.initialize()

        # Set global reference for SEI message handling
        global global_gpt_manager
        global_gpt_manager = gpt_realtime_realtime_manager

        # Start subscribing to participants if specified
        if args.subscribe_to:
            participant_id = args.subscribe_to
            logger.info(f"📥 Starting subscribe mode for participant: {participant_id}")

            subscribe_pc = await subscribe_to_participant(
                args.token, participant_id, gpt_realtime_realtime_manager, enable_sei_subscription=not args.disable_sei_subscription
            )

            if subscribe_pc:
                logger.info(f"✅ Successfully subscribed to {participant_id} with OpenAI processing")
                connections.append(subscribe_pc)
            else:
                logger.error(f"❌ Failed to subscribe to {participant_id}")

        if not connections:
            logger.error("❌ No connections established")
            return

        # Start publishing
        logger.info("📤 Starting publish mode with OpenAI audio and video...")
        publish_pc = await join_stage_as_publisher(args.token, gpt_realtime_audio_track, gpt_realtime_video_track)

        if publish_pc:
            logger.info("🎉 WebRTC publishing established with OpenAI audio and video!")
            connections.append(publish_pc)
        else:
            logger.error("❌ Failed to establish WebRTC publishing")
            return

        # Keep all connections alive
        try:
            logger.info(f"🔄 {len(connections)-1} connection(s) active with OpenAI real-time API. Press Ctrl+C to exit.")
            logger.info("🎙️  Speak and OpenAI will respond through the IVS stage!")
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
            # Clean up OpenAI real-time manager
            if gpt_realtime_realtime_manager:
                logger.info("🤖 Closing OpenAI real-time connection...")
                await gpt_realtime_realtime_manager.close()

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
