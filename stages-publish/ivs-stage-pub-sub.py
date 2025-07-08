#!/usr/bin/env python3

import asyncio
import json
import logging
import argparse
import base64
import requests
import time
import numpy as np
from typing import Dict, Any, List
from aiortc import (
    RTCBundlePolicy,
    RTCConfiguration,
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
    MediaStreamTrack,
)
from aiortc.contrib.media import MediaPlayer
from av import VideoFrame
from fractions import Fraction

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ivs-stage-pub-sub")
logger.setLevel(logging.DEBUG)
aiortc_logger = logging.getLogger("aiortc")
aiortc_logger.setLevel(logging.ERROR)


class BlankVideoTrack(VideoStreamTrack):
    """
    A video track that generates blank/black frames at a specified frame rate
    """

    def __init__(self, width=1280, height=720, fps=30):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_duration = 1.0 / fps
        self.start_time = time.time()
        self.frame_count = 0

    async def recv(self):
        """Generate and return a black video frame"""
        # Calculate the presentation timestamp (PTS) based on frame count
        pts = int(self.frame_count * (1 / self.fps) * 90000)  # 90kHz clock

        # Create a black frame using numpy
        # Create RGB black frame first, then let av handle the conversion
        frame_array = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        # Create VideoFrame from numpy array
        frame = VideoFrame.from_ndarray(frame_array, format="rgb24")
        frame.pts = pts
        frame.time_base = Fraction(1, 90000)  # Use Fraction for proper time_base

        # Increment frame count for next frame
        self.frame_count += 1

        # Sleep to maintain frame rate
        await asyncio.sleep(self.frame_duration)

        return frame


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


def validate_publish_capability(token_payload: Dict[str, Any]) -> bool:
    """Validate that the token has publish capabilities"""
    capabilities = token_payload.get("capabilities", {})
    allow_publish = capabilities.get("allow_publish", False)

    if not allow_publish:
        logger.error("Token does not have publish capabilities (capabilities.allow_publish != true)")
        return False

    logger.info("✅ Token has publish capabilities")
    return True


def validate_subscribe_capability(token_payload: Dict[str, Any]) -> bool:
    """Validate that the token has subscribe capabilities"""
    capabilities = token_payload.get("capabilities", {})
    allow_subscribe = capabilities.get("allow_subscribe", False)

    if not allow_subscribe:
        logger.error("Token does not have subscribe capabilities (capabilities.allow_subscribe != true)")
        return False

    logger.info("✅ Token has subscribe capabilities")
    return True


def fix_ivs_answer_sdp(sdp: str) -> str:
    """Fix IVS's SDP answer to ensure ICE candidates are in both audio and video sections"""
    # logger.info("=== ORIGINAL IVS ANSWER ===")
    # logger.info(sdp)
    # logger.info("===========================")

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
            # logger.info(f"Found ICE candidate in audio: {line}")

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
                # logger.info(f"Adding {len(ice_candidates)} ICE candidates at end of video section")
                # Add all the ICE candidates from audio section
                for candidate in ice_candidates:
                    fixed_lines.append(candidate)
                # Add end-of-candidates
                fixed_lines.append("a=end-of-candidates")
                candidates_added = True
                continue

        fixed_lines.append(line)

    result = "\n".join(fixed_lines)
    # logger.info("=== FIXED IVS ANSWER ===")
    # logger.info(result)
    # logger.info("========================")

    return result


async def join_stage_as_publisher(token: str, path_to_mp4: str, video_only: bool):
    """Join the IVS stage as a publisher using WebRTC"""
    logger.info("🚀 Joining stage as publisher...")

    # Create peer connection
    config = RTCConfiguration()
    config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
    pc = RTCPeerConnection(config)

    # Use global WHIP base URL for publishing
    whip_base_url = "https://global.whip.live-video.net"
    logger.info(f"🔗 WHIP Base URL: {whip_base_url}")

    # Create media player for audio only
    media = MediaPlayer(path_to_mp4)
    # Create blank video track
    blank_video = BlankVideoTrack(width=1280, height=720, fps=30)

    # Add tracks to peer connection
    if not video_only:
        logger.info("🔈 Adding audio track from MP4")
        audio_transceiver = pc.addTransceiver(media.audio, direction="sendrecv")

    logger.info("🎥 Adding blank/black video track")
    video_transceiver = pc.addTransceiver(blank_video, direction="sendrecv")

    logger.info("➕ Added track(s)")

    await pc.setLocalDescription(await pc.createOffer())

    # Send offer to WHIP endpoint
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"}
    logger.info(f"Sending WebRTC offer to WHIP endpoint: {whip_base_url}")

    # Handle manual redirects to preserve Authorization header
    current_url = whip_base_url
    max_redirects = 5
    attempt = 1

    while attempt <= max_redirects:
        logger.info(f"Sending request to: {current_url} (attempt {attempt})")

        response = requests.post(current_url, data=pc.localDescription.sdp, headers=headers, allow_redirects=False)  # Handle redirects manually

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
            # Success!
            break
        else:
            logger.error(f"WHIP request failed with status {response.status_code}: {response.text}")
            return None

    if attempt > max_redirects:
        logger.error(f"Too many redirects (>{max_redirects})")
        return None

    if response.status_code != 201:
        logger.error(f"Failed to establish WebRTC connection: {response.status_code} - {response.text}")
        return None

    # Set remote description from answer
    logger.info("🔧 Setting remote description from IVS answer...")

    # Fix the IVS answer SDP to add ICE candidates to video section
    fixed_answer_sdp = fix_ivs_answer_sdp(response.text)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=fixed_answer_sdp, type="answer"))

    logger.info("✅ Successfully joined stage as publisher")
    return pc


async def subscribe_to_participant(token: str, participant_id: str):
    """Subscribe to a participant's audio/video streams"""
    logger.info(f"🎧 Subscribing to participant: {participant_id}")

    # Create peer connection
    config = RTCConfiguration()
    config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
    pc = RTCPeerConnection(config)

    # Add transceivers for receiving audio and video
    pc.addTransceiver("audio", direction="recvonly")
    pc.addTransceiver("video", direction="recvonly")

    @pc.on("track")
    async def on_track(track: MediaStreamTrack):
        logger.info(f"📺 Received {track.kind} track from participant {participant_id}")

        if track.kind == "audio":
            logger.info("🔊 Audio track received - you can process audio here")
            # Process audio frames if needed
            # while True:
            #     try:
            #         frame = await track.recv()
            #         # Process audio frame here
            #     except Exception as e:
            #         break
        elif track.kind == "video":
            logger.info("🎥 Video track received - you can process video here")
            # Process video frames if needed
            # while True:
            #     try:
            #         frame = await track.recv()
            #         # Process video frame here
            #     except Exception as e:
            #         break

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

    # Send offer to WHEP endpoint
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"}
    logger.info(f"Sending WebRTC offer to WHEP endpoint: {whep_url}")

    response = requests.post(whep_url, data=pc.localDescription.sdp, headers=headers, allow_redirects=False)

    if response.status_code in [301, 302, 307, 308]:
        redirect_url = response.headers.get("Location")
        if redirect_url:
            logger.info(f"Redirect: {whep_url} -> {redirect_url}")
            response = requests.post(redirect_url, data=pc.localDescription.sdp, headers=headers)
        else:
            logger.error("Redirect response missing Location header")
            return None

    if response.status_code != 201:
        logger.error(f"Failed to establish WebRTC subscription: {response.status_code} - {response.text}")
        return None

    # Set remote description from answer
    logger.info("🔧 Setting remote description from IVS answer...")
    await pc.setRemoteDescription(RTCSessionDescription(sdp=response.text, type="answer"))

    logger.info("✅ Successfully subscribed to participant")
    return pc


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="IVS Stage Publisher/Subscriber")
    parser.add_argument("--token", required=True, help="IVS stage participant token")

    # Publishing options
    parser.add_argument("--path-to-mp4", required=True, help="Path to MP4 file to publish audio from")
    parser.add_argument("--video-only", action="store_true", help="Publish video only (no audio)")

    # Subscribing options
    parser.add_argument("--subscribe-to", nargs="+", help="List of participant IDs to subscribe to")

    return parser.parse_args()


async def main():
    """Main function that handles both publishing and subscribing simultaneously"""
    args = parse_args()

    logger.info("🎬 Starting IVS Stage Publisher/Subscriber")
    logger.info(f"🔑 Using token: {args.token[:50]}... (truncated)")

    # Parse the JWT token to extract required fields
    token_payload = parse_jwt(args.token)

    if not token_payload:
        logger.error("Failed to parse token payload")
        return

    # Validate capabilities
    if not validate_publish_capability(token_payload):
        logger.error("❌ Token missing publish capabilities")
        return

    if args.subscribe_to and not validate_subscribe_capability(token_payload):
        logger.error("❌ Token missing subscribe capabilities")
        return

    # Extract required fields from token
    events_url = token_payload.get("events_url")
    topic = token_payload.get("topic")
    jti = token_payload.get("jti")

    logger.info(f"ℹ️  Topic: {topic}")
    logger.info(f"ℹ️  JTI: {jti}")

    try:
        connections = []

        # Start publishing (always happens)
        logger.info("📤 Starting publish mode...")
        publish_pc = await join_stage_as_publisher(args.token, args.path_to_mp4, args.video_only)

        if publish_pc:
            logger.info("🎉 WebRTC publishing established!")
            connections.append(publish_pc)
        else:
            logger.error("❌ Failed to establish WebRTC publishing")
            return

        # Start subscribing to participants if specified
        if args.subscribe_to:
            logger.info(f"📥 Starting subscribe mode for participants: {args.subscribe_to}")

            for participant_id in args.subscribe_to:
                subscribe_pc = await subscribe_to_participant(args.token, participant_id)

                if subscribe_pc:
                    logger.info(f"✅ Successfully subscribed to {participant_id}")
                    connections.append(subscribe_pc)
                else:
                    logger.error(f"❌ Failed to subscribe to {participant_id}")

        if not connections:
            logger.error("❌ No connections established")
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
