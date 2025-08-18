#!/usr/bin/env python3

import asyncio
import json
import logging
import argparse
import base64
import requests
from typing import Dict, Any, List
from aiortc import (
    RTCBundlePolicy,
    RTCConfiguration,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.contrib.media import MediaPlayer

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ivs-stage-publisher-events")
logger.setLevel(logging.DEBUG)
aiortc_logger = logging.getLogger("aiortc")
aiortc_logger.setLevel(logging.ERROR)


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

    # Create media player
    media = MediaPlayer(path_to_mp4)

    # Add tracks to peer connection
    if not video_only:
        logger.info("🔈 Adding audio track")
        audio_transceiver = pc.addTransceiver(media.audio, direction="sendrecv")

    logger.info("🎥 Adding video track")
    video_transceiver = pc.addTransceiver(media.video, direction="sendrecv")

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

        try:
            response = requests.post(
                current_url, data=pc.localDescription.sdp, headers=headers, allow_redirects=False, timeout=10  # Add explicit timeout of 10 seconds
            )
        except requests.exceptions.Timeout:
            logger.error(f"Request to {current_url} timed out after 10 seconds")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Request to {current_url} failed: {e}")
            return None

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


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="IVS Stage Publisher")
    parser.add_argument("--token", required=True, help="IVS stage participant token")
    parser.add_argument("--path-to-mp4", required=True, help="Path to MP4 file to publish")
    parser.add_argument("--video-only", action="store_true", help="Publish video only (no audio)")
    return parser.parse_args()


async def main():
    """Main function that publishes WebRTC"""
    args = parse_args()

    logger.info("🎬 Starting IVS Stage Publisher")
    logger.info(f"🔑 Using token: {args.token[:50]}... (truncated)")

    # Parse the JWT token to extract required fields
    token_payload = parse_jwt(args.token)

    if not token_payload:
        logger.error("Failed to parse token payload")
        return

    # Validate publish capabilities
    if not validate_publish_capability(token_payload):
        return

    # Extract required fields from token
    events_url = token_payload.get("events_url")
    topic = token_payload.get("topic")
    jti = token_payload.get("jti")

    if not all([events_url, topic, jti]):
        logger.error("Missing required fields in token:")
        logger.error(f"  topic: {'✅' if topic else '❌'}")
        logger.error(f"  jti: {'✅' if jti else '❌'}")
        return

    logger.info(f"ℹ️  Topic: {topic}")
    logger.info(f"ℹ️  Subscriber JTI: {jti}")

    try:
        # Start WebRTC publishing
        publish_task = asyncio.create_task(join_stage_as_publisher(args.token, args.path_to_mp4, args.video_only))

        # Wait for publishing to complete first
        pc = await publish_task

        if pc:
            logger.info("🎉 WebRTC publishing established! ")

            # Keep the connection alive and monitor events
            try:
                while True:
                    await asyncio.sleep(1)  # Keep the event loop running
            except KeyboardInterrupt:
                logger.info("🛑 Shutting down...")
            finally:
                # Clean up peer connection
                await pc.close()
                logger.info("🔌 WebRTC connection closed")
        else:
            logger.error("❌ Failed to establish WebRTC publishing")

    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
    except Exception as e:
        logger.error(f"❌ Error in main: {e}")


if __name__ == "__main__":
    asyncio.run(main())
