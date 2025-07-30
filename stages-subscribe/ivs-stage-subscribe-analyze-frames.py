#!/usr/bin/env python3

import asyncio
import json
import logging
import argparse
import base64
import requests
import time
import io
from typing import Dict, Any, List, Optional
from aiortc import (
    RTCBundlePolicy,
    RTCConfiguration,
    RTCPeerConnection,
    RTCSessionDescription,
    MediaStreamTrack,
)
import boto3
from PIL import Image


# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ivs-stage-subscribe-analyze-frames")
logger.setLevel(logging.DEBUG)
aiortc_logger = logging.getLogger("aiortc")
aiortc_logger.setLevel(logging.ERROR)

# Suppress noisy STUN transaction timeout errors
aioice_logger = logging.getLogger("aioice")
aioice_logger.setLevel(logging.CRITICAL)
stun_logger = logging.getLogger("aioice.stun")
stun_logger.setLevel(logging.CRITICAL)


class VideoFrameAnalyzer:
    """Handles video frame analysis using Amazon Bedrock Claude"""

    def __init__(self, analysis_interval: float = 5.0, region: str = "us-east-1", model_id: str = "anthropic.claude-sonnet-4-20250514-v1:0"):
        """
        Initialize the video frame analyzer

        Args:
            analysis_interval: Time in seconds between frame analyses
            region: AWS region for Bedrock service
            model_id: Bedrock model ID to use for analysis
        """
        self.analysis_interval = analysis_interval
        self.last_analysis_time = 0
        self.bedrock_client = boto3.client("bedrock-runtime", region_name=region)
        self.model_id = model_id

        logger.info(f"🤖 VideoFrameAnalyzer initialized with {analysis_interval}s interval")
        logger.info(f"🌍 Using Bedrock region: {region}")
        logger.info(f"🧠 Using model: {self.model_id}")

    def should_analyze_frame(self) -> bool:
        """Check if enough time has passed since last analysis"""
        current_time = time.time()
        if current_time - self.last_analysis_time >= self.analysis_interval:
            self.last_analysis_time = current_time
            return True
        return False

    def frame_to_base64(self, frame) -> str:
        """Convert video frame to base64 encoded JPEG"""
        try:
            # Convert frame to PIL Image
            img = frame.to_image()

            # Convert to RGB if needed
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Save to bytes buffer as JPEG
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            buffer.seek(0)

            # Encode to base64
            img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
            return img_base64

        except Exception as e:
            logger.error(f"Error converting frame to base64: {e}")
            return None

    async def analyze_frame(self, frame, participant_id: str) -> Optional[str]:
        """
        Analyze a video frame using Claude

        Args:
            frame: Video frame from aiortc
            participant_id: ID of the participant being analyzed

        Returns:
            Analysis result string or None if failed
        """
        try:
            # Convert frame to base64
            frame_base64 = self.frame_to_base64(frame)
            if not frame_base64:
                return None

            # Prepare the message for Claude
            message = {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_base64}},
                    {
                        "type": "text",
                        "text": "Analyze this video frame from a live stream. Describe what you see in detail, including people, objects, activities, text, and any notable features. This could be used for content discovery, moderation, or accessibility purposes. Be specific and comprehensive.",
                    },
                ],
            }

            # Call Bedrock
            logger.info(f"🔍 Analyzing frame for participant {participant_id}...")

            response = self.bedrock_client.invoke_model(
                modelId=self.model_id,
                body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 1000, "messages": [message], "temperature": 0.1}),
            )

            # Parse response
            response_body = json.loads(response["body"].read())
            analysis_result = response_body["content"][0]["text"]

            logger.info(f"✅ Frame analysis completed for participant {participant_id}")
            logger.info(f"📝 Analysis: {analysis_result}")

            return analysis_result

        except Exception as e:
            logger.error(f"Error analyzing frame: {e}")
            import traceback

            traceback.print_exc()
            return None


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
                timeout=10  # Add explicit timeout of 10 seconds
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


async def subscribe_to_participant(token: str, participant_id: str, analyzer: VideoFrameAnalyzer = None):
    """Subscribe to a participant's audio/video streams and analyze video frames"""
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

    @pc.on("connectionstatechange")
    def on_connectionstatechange():
        logger.info(f"🔗 Connection state changed to: {pc.connectionState}")

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
            logger.info("🔊 Audio track received")
            logger.info("Creating audio processing task...")
            task = asyncio.create_task(process_audio_track(track))
            logger.info(f"Audio processing task created: {task}")
        elif track.kind == "video":
            logger.info("🎥 Video track received")
            # Create a task for video processing with optional analysis
            task = asyncio.create_task(process_video_track(track, analyzer, participant_id))
            logger.info(f"Video processing task created: {task}")

    async def process_audio_track(track: MediaStreamTrack):
        """Process audio track in a separate async task"""
        logger.info("🎵 Starting audio processing task (noop)")
        try:
            frame_count = 0
            while True:
                frame = await track.recv()
                frame_count += 1

        except Exception as e:
            logger.error(f"Audio track processing error for participant {participant_id}: {e}")
            import traceback

            traceback.print_exc()

    async def process_video_track(track: MediaStreamTrack, analyzer: VideoFrameAnalyzer = None, participant_id: str = "unknown"):
        """Process video track in a separate async task with optional frame analysis"""
        logger.info("🎬 Starting video processing task")
        if analyzer:
            logger.info(f"🤖 Frame analysis enabled (interval: {analyzer.analysis_interval}s)")

        try:
            frame_count = 0
            while True:
                frame = await track.recv()
                frame_count += 1

                # Analyze frame if analyzer is provided and enough time has passed
                if analyzer and analyzer.should_analyze_frame():
                    # Run analysis in background to avoid blocking frame processing
                    asyncio.create_task(analyzer.analyze_frame(frame, participant_id))

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

    logger.info("✅ Successfully subscribed to participant")
    return pc


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="IVS Stage Subscriber with Video Frame Analyzer")
    parser.add_argument("--token", required=True, help="IVS stage participant token")
    parser.add_argument("--subscribe-to", required=True, help="Participant ID to subscribe to")
    parser.add_argument("--analysis-interval", type=float, default=30.0, help="Time in seconds between frame analyses (default: 30.0)")
    parser.add_argument("--aws-region", default="us-east-1", help="AWS region for Bedrock service (default: us-east-1)")
    parser.add_argument(
        "--model-id",
        default="us.anthropic.claude-sonnet-4-20250514-v1:0",
        help="Bedrock model ID for frame analysis (default: us.anthropic.claude-sonnet-4-20250514-v1:0)",
    )
    parser.add_argument("--disable-analysis", action="store_true", help="Disable video frame analysis (just subscribe to video)")

    return parser.parse_args()


async def main():
    """Main function that handles subscribing to Amazon IVS participant"""
    args = parse_args()

    logger.info("🎬 IVS Stage Subscriber with Video Frame Analyzer")
    logger.info(f"🔑 Using token: {args.token[:50]}... (truncated)")
    token_payload = parse_jwt(args.token)

    if not token_payload:
        logger.error("Failed to parse token payload")
        return

    # Validate capabilities
    if args.subscribe_to and not validate_token_capability(token_payload, "subscribe"):
        logger.error("❌ Token missing subscribe capabilities")
        return

    events_url = token_payload.get("events_url")
    topic = token_payload.get("topic")
    jti = token_payload.get("jti")

    logger.info(f"ℹ️  Stage Events URL: {events_url}")
    logger.info(f"ℹ️  Topic: {topic}")
    logger.info(f"ℹ️  JTI: {jti}")

    # Initialize video frame analyzer if not disabled
    analyzer = None
    if not args.disable_analysis:
        try:
            analyzer = VideoFrameAnalyzer(analysis_interval=args.analysis_interval, region=args.aws_region, model_id=args.model_id)
            logger.info(f"🤖 Video frame analysis enabled (every {args.analysis_interval}s)")
        except Exception as e:
            logger.error(f"❌ Failed to initialize VideoFrameAnalyzer: {e}")
            logger.error("Continuing without frame analysis...")
    else:
        logger.info("🚫 Video frame analysis disabled")

    try:
        connections = []

        # Start subscribing to participants if specified
        if args.subscribe_to:
            participant_id = args.subscribe_to
            logger.info(f"📥 Starting subscribe mode for participant: {participant_id}")

            subscribe_pc = await subscribe_to_participant(args.token, participant_id, analyzer)

            if subscribe_pc:
                logger.info(f"✅ Successfully subscribed to {participant_id}")
                connections.append(subscribe_pc)
            else:
                logger.error(f"❌ Failed to subscribe to {participant_id}")

        if not connections:
            logger.error("❌ No connections established")
            return

        if subscribe_pc:
            logger.info("🎉 WebRTC subscription established!")
            if analyzer:
                logger.info(f"🔍 Frame analysis active - analyzing every {args.analysis_interval} seconds")
            else:
                logger.info("📺 Video streaming without analysis")
        else:
            logger.error("❌ Failed to establish WebRTC subscription")
            return

        # Keep all connections alive
        try:
            logger.info(f"🔄 {len(connections)} connection(s) active. Press Ctrl+C to exit.")
            while True:
                await asyncio.sleep(1)  # Keep the event loop running
        except KeyboardInterrupt:
            logger.info("🛑 Shutting down...")
        finally:
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
