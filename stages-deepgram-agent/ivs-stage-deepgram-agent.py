#!/usr/bin/env python3
"""
IVS Real-Time Stage + Deepgram Voice Agent

Subscribes to a participant's audio on an IVS stage, pipes it through
Deepgram's Voice Agent API (STT → LLM → TTS in one WebSocket), and
publishes the agent's audio/video response back to the stage.
"""

import asyncio
import argparse
import base64
import json
import logging
import os
import sys
import time
import warnings
from typing import Dict, List, Any, Optional

# Add parent directory to path for SEI imports — must happen before aiortc import
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

# Apply H.264 SEI patch BEFORE importing aiortc
import stages_sei.h264_sei_patch

import av
import numpy as np
import requests
import aioice
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    RTCBundlePolicy,
    MediaStreamTrack,
)

# ── ICE timeout patch (same as Nova/GPT demos) ─────────────────
ICE_TIMEOUT = 1
_original_get_component_candidates = aioice.ice.Connection.get_component_candidates


async def patched_get_component_candidates(self, component, addresses, timeout=None):
    if timeout is None:
        timeout = ICE_TIMEOUT
    return await _original_get_component_candidates(self, component, addresses, timeout)


aioice.ice.Connection.get_component_candidates = patched_get_component_candidates

# Local imports — reuse proven audio/video track patterns from Nova demo
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "stages-nova-s2s"))
from agent_audio_track import AgentAudioTrack
from agent_video_track import AgentVideoTrack

from deepgram_agent_manager import DeepgramAgentManager, INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE

warnings.filterwarnings("ignore")

# ── Logging ─────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("ivs-stage-deepgram-agent")

# Suppress noisy loggers
for noisy in ["aiortc", "aioice", "aioice.stun", "websockets"]:
    logging.getLogger(noisy).setLevel(logging.ERROR)

CHANNELS = 1


# ── Utility functions (shared with Nova/GPT demos) ──────────────


def parse_jwt(token: str) -> Dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT format")
        payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except Exception as e:
        logger.error(f"Error parsing JWT: {e}")
        return {}


def validate_token_capability(token_payload: Dict[str, Any], capability: str) -> bool:
    has = token_payload.get("capabilities", {}).get(f"allow_{capability}", False)
    if not has:
        logger.error(f"Token missing {capability} capability")
    return has


def fix_ivs_answer_sdp(sdp: str) -> str:
    """Copy ICE candidates from audio section to video section in IVS SDP answers"""
    lines = sdp.split("\n")
    ice_candidates = []
    in_audio = False
    for line in lines:
        if line.startswith("m=audio"):
            in_audio = True
        elif line.startswith("m=video") or line.startswith("m=application"):
            in_audio = False
        if in_audio and line.startswith("a=candidate:"):
            ice_candidates.append(line)

    fixed, in_video, added = [], False, False
    for i, line in enumerate(lines):
        if line.startswith("m=video"):
            in_video, added = True, False
        elif line.startswith("m=") and not line.startswith("m=video"):
            in_video = False
        if in_video and not added:
            is_end = i == len(lines) - 1 or lines[i + 1].startswith("m=") or lines[i + 1].strip() == ""
            if is_end:
                fixed.append(line)
                fixed.extend(ice_candidates)
                fixed.append("a=end-of-candidates")
                added = True
                continue
        fixed.append(line)
    return "\n".join(fixed)


async def get_remote_sdp(url: str, token: str, sdp_offer: str, max_redirects: int = 5) -> Optional[str]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"}
    current_url = url
    for attempt in range(1, max_redirects + 1):
        try:
            resp = requests.post(current_url, data=sdp_offer, headers=headers, allow_redirects=False, timeout=10)
            if resp.status_code in [301, 302, 303, 307, 308]:
                current_url = resp.headers.get("Location")
                if not current_url:
                    return None
                continue
            elif resp.status_code == 201:
                return resp.text
            else:
                logger.error(f"WHIP/WHEP failed: {resp.status_code} {resp.text[:200]}")
                return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            return None
    return None


# ── WebRTC: publish agent audio/video to stage ──────────────────


async def join_stage_as_publisher(token: str, agent_audio_track: AgentAudioTrack, agent_video_track: AgentVideoTrack):
    logger.info("🚀 Joining stage as publisher...")
    config = RTCConfiguration()
    config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
    pc = RTCPeerConnection(config)

    pc.addTransceiver(agent_audio_track, direction="sendrecv")
    pc.addTransceiver(agent_video_track, direction="sendrecv")
    agent_audio_track.set_peer_connection(pc)

    await pc.setLocalDescription(await pc.createOffer())

    answer_sdp = await get_remote_sdp("https://global.whip.live-video.net", token, pc.localDescription.sdp)
    if not answer_sdp:
        logger.error("❌ Failed to get SDP answer from WHIP endpoint")
        return None

    await pc.setRemoteDescription(RTCSessionDescription(sdp=fix_ivs_answer_sdp(answer_sdp), type="answer"))
    logger.info("✅ Joined stage as publisher")
    return pc


# ── WebRTC: subscribe to participant and pipe audio to Deepgram ─


async def subscribe_to_participant(token: str, participant_id: str, agent_manager: DeepgramAgentManager):
    logger.info(f"🎧 Subscribing to participant: {participant_id}")
    config = RTCConfiguration()
    config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
    pc = RTCPeerConnection(config)

    pc.addTransceiver("audio", direction="recvonly")
    pc.addTransceiver("video", direction="recvonly")

    resampler = None

    @pc.on("connectionstatechange")
    def on_connectionstatechange():
        logger.info(f"🔗 Subscribe connection state: {pc.connectionState}")

    @pc.on("iceconnectionstatechange")
    def on_iceconnectionstatechange():
        logger.info(f"🧊 Subscribe ICE state: {pc.iceConnectionState}")

    @pc.on("track")
    def on_track(track: MediaStreamTrack):
        logger.info(f"📺 Received {track.kind} track from {participant_id}")
        if track.kind == "audio":
            logger.info("🔊 Audio track received - processing through Deepgram Agent")
            asyncio.create_task(_process_audio(track))
        elif track.kind == "video":
            asyncio.create_task(_consume_video(track))

    async def _process_audio(track: MediaStreamTrack):
        nonlocal resampler

        logger.info(f"🎵 Starting audio processing task (track state: {track.readyState})")

        # Wait for WebRTC connection to be established (same pattern as GPT demo)
        logger.info("Waiting for WebRTC connection to be established...")
        while pc.connectionState not in ["connected", "completed"]:
            logger.info(f"Connection state: {pc.connectionState}, waiting...")
            await asyncio.sleep(0.1)
        logger.info(f"✅ Connection established: {pc.connectionState}")

        resampler = av.AudioResampler(format="s16", layout="mono", rate=INPUT_SAMPLE_RATE)

        frame_count = 0
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(track.recv(), timeout=5.0)
                    frame_count += 1

                    for resampled in resampler.resample(frame):
                        audio_bytes = resampled.to_ndarray().tobytes()
                        await agent_manager.send_audio(audio_bytes)
                        if frame_count == 1:
                            logger.info(f"🎤 First audio frame sent to Deepgram ({len(audio_bytes)} bytes)")
                        elif frame_count % 500 == 0:
                            logger.info(f"🎤 Audio frames sent: {frame_count}")

                except asyncio.TimeoutError:
                    logger.warning(f"Timeout waiting for audio frame {frame_count} - retrying...")
                    logger.info(f"Track state: {track.readyState}, Connection: {pc.connectionState}")
                    continue

        except Exception as e:
            logger.error(f"Audio processing error: {e}")
            import traceback

            traceback.print_exc()

    async def _consume_video(track: MediaStreamTrack):
        """Consume video frames and store the latest for frame analysis"""
        try:
            while True:
                frame = await track.recv()
                agent_manager.frame = frame
        except Exception:
            pass

    await pc.setLocalDescription(await pc.createOffer())

    token_payload = parse_jwt(token)
    whip_base_url = token_payload.get("whip_url", "")
    if not whip_base_url:
        logger.error("No whip_url in token")
        return None

    whep_url = f"{whip_base_url}/subscribe/{participant_id}"
    answer_sdp = await get_remote_sdp(whep_url, token, pc.localDescription.sdp)
    if not answer_sdp:
        logger.error(f"❌ Failed to subscribe to {participant_id}")
        return None

    # Apply SDP fix (same as GPT demo — ensures ICE candidates in video section)
    fixed_answer_sdp = fix_ivs_answer_sdp(answer_sdp)
    await pc.setRemoteDescription(RTCSessionDescription(sdp=fixed_answer_sdp, type="answer"))
    logger.info(f"✅ Subscribed to {participant_id}")
    return pc


# ── CLI ─────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(
        description="IVS Real-Time Stage + Deepgram Voice Agent",
        epilog="""
Examples:
  %(prog)s --token eyJ... --subscribe-to user123
  %(prog)s --token eyJ... --subscribe-to user123 --voice aura-2-orion-en --think-model gpt-4o
  %(prog)s --token eyJ... --subscribe-to user123 --prompt "You are a pirate. Respond in pirate speak."

Environment variables:
  DEEPGRAM_API_KEY    Deepgram API key (alternative to --deepgram-api-key)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--token", required=True, help="IVS participant token with PUBLISH and SUBSCRIBE capabilities")
    parser.add_argument("--subscribe-to", required=True, help="Participant ID to subscribe to")
    parser.add_argument("--deepgram-api-key", default=None, help="Deepgram API key (or set DEEPGRAM_API_KEY env var)")
    parser.add_argument(
        "--voice",
        default="aura-2-asteria-en",
        help="Deepgram TTS voice model (default: aura-2-asteria-en). See Deepgram docs for full list.",
    )
    parser.add_argument(
        "--think-model",
        default="gpt-4o-mini",
        help="LLM model for agent reasoning (default: gpt-4o-mini). Supports OpenAI, Anthropic, etc.",
    )
    parser.add_argument(
        "--think-provider",
        default="open_ai",
        choices=["open_ai", "anthropic", "groq"],
        help="LLM provider (default: open_ai)",
    )
    parser.add_argument(
        "--prompt",
        default="You are a friendly, helpful voice assistant. Keep responses concise and conversational.",
        help="System prompt for the agent",
    )
    parser.add_argument(
        "--greeting",
        default="Hello! How can I help you today?",
        help="Agent greeting message spoken when the session starts",
    )
    parser.add_argument("--language", default="en", help="Language code (default: en)")
    parser.add_argument("--ice-timeout", type=int, default=1, help="ICE gathering timeout in seconds (default: 1)")
    parser.add_argument("--disable-frame-analysis", action="store_true", help="Disable video frame analysis (default: enabled)")
    parser.add_argument(
        "--bedrock-model-id",
        default="us.anthropic.claude-sonnet-4-6",
        help="Bedrock model ID for frame analysis (default: Claude Sonnet 4)",
    )
    parser.add_argument("--bedrock-region", default="us-east-1", help="AWS region for Bedrock (default: us-east-1)")

    # BYO TTS provider options
    tts = parser.add_argument_group("tts provider", "Third-party TTS (overrides --voice). See Deepgram docs.")
    tts.add_argument(
        "--tts-provider",
        default=None,
        choices=["deepgram", "open_ai", "eleven_labs", "cartesia", "aws_polly"],
        help="TTS provider type (default: deepgram)",
    )
    tts.add_argument("--tts-model", default=None, help="TTS model ID (provider-specific)")
    tts.add_argument("--tts-voice", default=None, help="TTS voice ID (provider-specific)")
    tts.add_argument("--tts-endpoint-url", default=None, help="TTS API endpoint URL (required for BYO providers)")
    tts.add_argument("--tts-api-key", default=None, help="TTS provider API key")
    tts.add_argument("--tts-language", default=None, help="TTS language code (for providers that require it)")

    return parser.parse_args()


# ── Main ────────────────────────────────────────────────────────


async def main():
    args = parse_args()

    global ICE_TIMEOUT
    ICE_TIMEOUT = args.ice_timeout

    deepgram_api_key = args.deepgram_api_key or os.environ.get("DEEPGRAM_API_KEY", "")
    if not deepgram_api_key:
        logger.error("Deepgram API key required. Use --deepgram-api-key or DEEPGRAM_API_KEY env var.")
        return

    logger.info("🎬 Starting IVS Stage + Deepgram Voice Agent")
    logger.info(f"🧊 ICE timeout: {ICE_TIMEOUT}s")
    logger.info(f"🗣️  Voice: {args.voice}")
    logger.info(f"🧠 Think: {args.think_provider}/{args.think_model}")
    logger.info(f"🌍 Language: {args.language}")

    enable_frame_analysis = not args.disable_frame_analysis
    logger.info(f"🔍 Frame analysis: {'enabled' if enable_frame_analysis else 'disabled'}")
    if enable_frame_analysis:
        logger.info(f"🧠 Analysis model: {args.bedrock_model_id}")
        logger.info(f"🌍 Analysis region: {args.bedrock_region}")

    # Build BYO TTS speak config if specified
    speak_config = None
    if args.tts_provider and args.tts_provider != "deepgram":
        provider_config = {"type": args.tts_provider}
        if args.tts_provider == "open_ai":
            provider_config["model"] = args.tts_model or "tts-1"
            provider_config["voice"] = args.tts_voice or "alloy"
        elif args.tts_provider == "eleven_labs":
            provider_config["model_id"] = args.tts_model or "eleven_turbo_v2_5"
            if args.tts_language:
                provider_config["language_code"] = args.tts_language
        elif args.tts_provider == "cartesia":
            provider_config["model_id"] = args.tts_model or "sonic-2"
            if args.tts_voice:
                provider_config["voice"] = {"mode": "id", "id": args.tts_voice}
            if args.tts_language:
                provider_config["language"] = args.tts_language
        elif args.tts_provider == "aws_polly":
            provider_config["voice"] = args.tts_voice or "Matthew"
            provider_config["engine"] = "standard"
            if args.tts_language:
                provider_config["language_code"] = args.tts_language

        speak_config = {"provider": provider_config}
        if args.tts_endpoint_url:
            endpoint = {"url": args.tts_endpoint_url}
            if args.tts_api_key:
                if args.tts_provider == "open_ai":
                    endpoint["headers"] = {"authorization": f"Bearer {args.tts_api_key}"}
                elif args.tts_provider == "eleven_labs":
                    endpoint["headers"] = {"xi-api-key": args.tts_api_key, "Content-Type": "application/json"}
                elif args.tts_provider == "cartesia":
                    endpoint["headers"] = {"x-api-key": args.tts_api_key}
            speak_config["endpoint"] = endpoint
        logger.info(f"🗣️  BYO TTS: {args.tts_provider} (model: {args.tts_model or 'default'})")

    token_payload = parse_jwt(args.token)
    if not token_payload:
        return

    if not validate_token_capability(token_payload, "publish"):
        return
    if not validate_token_capability(token_payload, "subscribe"):
        return

    connections = []

    try:
        # Create agent video track (visual feedback)
        agent_video_track = AgentVideoTrack(width=640, height=360, fps=20)

        # Create agent audio track (streams Deepgram TTS to IVS)
        agent_audio_track = AgentAudioTrack(
            agent_video_track=agent_video_track,
            sample_rate=OUTPUT_SAMPLE_RATE,
            channels=CHANNELS,
        )

        # Initialize Deepgram Voice Agent
        agent_manager = DeepgramAgentManager(
            api_key=deepgram_api_key,
            agent_audio_track=agent_audio_track,
            agent_video_track=agent_video_track,
            voice=args.voice,
            think_model=args.think_model,
            think_provider=args.think_provider,
            prompt=args.prompt,
            greeting=args.greeting,
            language=args.language,
            output_sample_rate=OUTPUT_SAMPLE_RATE,
            enable_frame_analysis=enable_frame_analysis,
            bedrock_model_id=args.bedrock_model_id,
            bedrock_region=args.bedrock_region,
            speak_config=speak_config,
        )
        await agent_manager.initialize()

        # Subscribe to participant audio → Deepgram Agent
        sub_pc = await subscribe_to_participant(args.token, args.subscribe_to, agent_manager)
        if sub_pc:
            connections.append(sub_pc)
        else:
            logger.error("❌ Failed to subscribe")
            return

        # Publish agent audio/video → IVS stage
        pub_pc = await join_stage_as_publisher(args.token, agent_audio_track, agent_video_track)
        if pub_pc:
            connections.append(pub_pc)
        else:
            logger.error("❌ Failed to publish")
            return

        logger.info("🎙️  Deepgram Voice Agent is live! Press Ctrl+C to stop.")

        # Keep running
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        import traceback

        traceback.print_exc()
    finally:
        await agent_manager.shutdown()
        for pc in connections:
            await pc.close()
        logger.info("✅ Cleanup completed")


if __name__ == "__main__":
    asyncio.run(main())
