#!/usr/bin/env python3
"""
IVS Real-Time Stage Meeting Scribe powered by ElevenLabs

Joins an IVS stage as a silent participant, subscribes to all other
participants' audio, transcribes everything with ElevenLabs Scribe v2
Realtime, and publishes transcripts back to the stage via SEI metadata
embedded in a static ElevenLabs-branded video track.
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
import traceback
from typing import Dict, List, Any, Optional

import av
import numpy as np
import requests
import websockets
from websockets.exceptions import ConnectionClosed
import aioice

from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    RTCBundlePolicy,
    MediaStreamTrack,
    AudioStreamTrack,
)
from av import AudioFrame
from fractions import Fraction

# Apply H.264 SEI patch BEFORE aiortc encoder is used
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
import stages_sei.h264_sei_patch
from stages_sei import SeiPublisher, set_global_sei_publisher

from scribe_video_track import ScribeVideoTrack

warnings.filterwarnings("ignore")

# ── ICE timeout patch ───────────────────────────────────────────
ICE_TIMEOUT = 1
_orig_get_candidates = aioice.ice.Connection.get_component_candidates


async def _patched_get_candidates(self, component, addresses, timeout=None):
    return await _orig_get_candidates(self, component, addresses, timeout or ICE_TIMEOUT)


aioice.ice.Connection.get_component_candidates = _patched_get_candidates

# ── Logging ─────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("ivs-stage-elevenlabs-meeting-transcriber")
for noisy in ["aiortc", "aioice", "aioice.stun", "websockets"]:
    logging.getLogger(noisy).setLevel(logging.ERROR)

SAMPLE_RATE = 16000

# ElevenLabs WebSocket endpoint
ELEVENLABS_WS_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"


# ── Silent audio track (required for publish) ──────────────────


class SilentAudioTrack(AudioStreamTrack):
    """Generates silence — needed so the scribe can publish video with an audio transceiver"""

    def __init__(self, sample_rate=48000):
        super().__init__()
        self.sample_rate = sample_rate
        self._samples_per_frame = sample_rate // 50  # 20ms
        self._frame_count = 0

    async def recv(self):
        samples = np.zeros(self._samples_per_frame, dtype=np.int16)
        frame = AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = self.sample_rate
        frame.pts = self._frame_count * self._samples_per_frame
        frame.time_base = Fraction(1, self.sample_rate)
        self._frame_count += 1
        await asyncio.sleep(0.02)
        return frame


# ── Utility functions ───────────────────────────────────────────


def parse_jwt(token: str) -> Dict[str, Any]:
    try:
        parts = token.split(".")
        payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except Exception as e:
        logger.error(f"Error parsing JWT: {e}")
        return {}


def fix_ivs_answer_sdp(sdp: str) -> str:
    lines = sdp.split("\n")
    ice = [l for l in lines if l.startswith("a=candidate:")]
    fixed, in_vid, added = [], False, False
    for i, line in enumerate(lines):
        if line.startswith("m=video"):
            in_vid, added = True, False
        elif line.startswith("m=") and not line.startswith("m=video"):
            in_vid = False
        if in_vid and not added:
            is_end = i == len(lines) - 1 or lines[i + 1].startswith("m=") or not lines[i + 1].strip()
            if is_end:
                fixed.append(line)
                fixed.extend(ice)
                fixed.append("a=end-of-candidates")
                added = True
                continue
        fixed.append(line)
    return "\n".join(fixed)


async def get_remote_sdp(url, token, sdp_offer, max_redirects=5):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"}
    current_url = url
    for _ in range(max_redirects):
        try:
            resp = requests.post(current_url, data=sdp_offer, headers=headers, allow_redirects=False, timeout=10)
            if resp.status_code in (301, 302, 303, 307, 308):
                current_url = resp.headers.get("Location")
                if not current_url:
                    return None
                continue
            elif resp.status_code == 201:
                return resp.text
            else:
                logger.error(f"SDP request failed: {resp.status_code}")
                return None
        except Exception as e:
            logger.error(f"SDP request error: {e}")
            return None
    return None


# ── Meeting Scribe ──────────────────────────────────────────────


class MeetingScribe:
    """Orchestrates multi-participant transcription on an IVS stage using ElevenLabs Scribe"""

    def __init__(
        self,
        token,
        elevenlabs_api_key,
        model_id="scribe_v2_realtime",
        language_code="en",
        commit_strategy="vad",
        vad_silence_threshold_secs=1.5,
        vad_threshold=0.4,
        include_timestamps=True,
        include_language_detection=False,
    ):
        self.token = token
        self.elevenlabs_api_key = elevenlabs_api_key
        self.model_id = model_id
        self.language_code = language_code
        self.commit_strategy = commit_strategy
        self.vad_silence_threshold_secs = vad_silence_threshold_secs
        self.vad_threshold = vad_threshold
        self.include_timestamps = include_timestamps
        self.include_language_detection = include_language_detection

        self.token_payload = parse_jwt(token)
        self.my_jti = self.token_payload.get("jti", "")

        # SEI publisher
        self.sei_publisher = SeiPublisher(max_retry_attempts=3)
        set_global_sei_publisher(self.sei_publisher)

        # Track active subscriptions: participant_id -> {pc, ws, tasks, ...}
        self._subscriptions: Dict[str, Dict[str, Any]] = {}
        self._publish_pc = None
        self._connections: List[RTCPeerConnection] = []

    def _build_ws_url(self) -> str:
        """Build the ElevenLabs WebSocket URL with query parameters"""
        params = {
            "model_id": self.model_id,
            "audio_format": "pcm_16000",
            "commit_strategy": self.commit_strategy,
            "include_timestamps": str(self.include_timestamps).lower(),
            "include_language_detection": str(self.include_language_detection).lower(),
        }
        if self.commit_strategy == "vad":
            params["vad_silence_threshold_secs"] = str(self.vad_silence_threshold_secs)
            params["vad_threshold"] = str(self.vad_threshold)
        if self.language_code and self.language_code != "auto":
            params["language_code"] = self.language_code

        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{ELEVENLABS_WS_URL}?{query_string}"

    async def start(self):
        """Publish the scribe's video track and start listening for stage events"""
        # Publish logo video + silent audio
        logger.info("📹 Publishing scribe video track...")
        video_track = ScribeVideoTrack(width=640, height=360, fps=5)
        silent_audio = SilentAudioTrack()

        config = RTCConfiguration()
        config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
        pc = RTCPeerConnection(config)
        pc.addTransceiver(silent_audio, direction="sendrecv")
        pc.addTransceiver(video_track, direction="sendrecv")

        await pc.setLocalDescription(await pc.createOffer())
        answer = await get_remote_sdp("https://global.whip.live-video.net", self.token, pc.localDescription.sdp)
        if not answer:
            logger.error("❌ Failed to publish scribe video")
            return
        await pc.setRemoteDescription(RTCSessionDescription(sdp=fix_ivs_answer_sdp(answer), type="answer"))
        self._publish_pc = pc
        self._connections.append(pc)
        logger.info("✅ Scribe video published to stage")

        # Connect to stage events WebSocket
        events_url = self.token_payload.get("events_url")
        topic = self.token_payload.get("topic")
        if events_url and topic:
            ws_url = f"{events_url}/{topic}"
            logger.info(f"🔌 Connecting to stage events: {ws_url}")
            await self._event_loop(ws_url)
        else:
            logger.error("❌ No events_url/topic in token — cannot track participants")

    async def _event_loop(self, ws_url):
        """Listen for stage events and manage participant subscriptions"""
        try:
            async with websockets.connect(ws_url, subprotocols=[self.token]) as ws:
                logger.info("✅ Connected to stage events WebSocket")
                await ws.send(json.dumps({"type": "PING"}))

                async for message in ws:
                    try:
                        event = json.loads(message)
                        await self._handle_event(event)
                    except json.JSONDecodeError:
                        pass
        except ConnectionClosed:
            logger.warning("🔌 Stage events WebSocket closed")
        except Exception as e:
            logger.error(f"❌ Events WebSocket error: {e}")

    async def _handle_event(self, event):
        event_type = event.get("type")
        if event_type == "STAGE_STATE":
            participants = event.get("payload", {}).get("participants", [])
            current_ids = set()

            for p in participants:
                pid = p.get("id", "")
                is_pub = p.get("isPublishing", False)
                user_id = p.get("userId", "")

                if pid == self.my_jti:
                    continue  # Skip ourselves

                current_ids.add(pid)

                if is_pub and pid not in self._subscriptions:
                    logger.info(f"👤 New participant publishing: {pid} (userId: {user_id})")
                    asyncio.create_task(self._subscribe_to(pid, user_id))

            # Clean up participants who left
            for pid in list(self._subscriptions.keys()):
                if pid not in current_ids:
                    logger.info(f"👋 Participant left: {pid}")
                    await self._unsubscribe(pid)

    async def _subscribe_to(self, participant_id, user_id=""):
        """Subscribe to a participant's audio and start transcribing with ElevenLabs"""
        try:
            config = RTCConfiguration()
            config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
            pc = RTCPeerConnection(config)
            pc.addTransceiver("audio", direction="recvonly")
            pc.addTransceiver("video", direction="recvonly")

            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            speaker_label = user_id or participant_id[:8]

            # Connect to ElevenLabs WebSocket for this participant
            ws_url = self._build_ws_url()
            headers = {"xi-api-key": self.elevenlabs_api_key}
            el_ws = await websockets.connect(ws_url, additional_headers=headers)

            # Wait for session_started
            session_msg = await el_ws.recv()
            session_data = json.loads(session_msg)
            if session_data.get("message_type") == "session_started":
                logger.info(f"✅ ElevenLabs connected for {speaker_label} (session: {session_data.get('session_id', 'N/A')})")
            else:
                logger.warning(f"Unexpected first message for {speaker_label}: {session_data}")

            # Store subscription info
            sub: Dict[str, Any] = {
                "pc": pc,
                "el_ws": el_ws,
                "user_id": user_id,
                "resampler": resampler,
                "should_stop": False,
            }
            self._subscriptions[participant_id] = sub
            self._connections.append(pc)

            # Start receiving transcripts in background
            recv_task = asyncio.create_task(self._receive_transcripts(el_ws, speaker_label, participant_id, sub))
            sub["recv_task"] = recv_task

            # Audio processing
            @pc.on("track")
            def on_track(track):
                if track.kind == "audio":
                    asyncio.create_task(self._process_audio(track, pc, el_ws, resampler, speaker_label, sub))
                elif track.kind == "video":
                    asyncio.create_task(self._consume_track(track))

            await pc.setLocalDescription(await pc.createOffer())
            whip_url = self.token_payload.get("whip_url", "")
            whep_url = f"{whip_url}/subscribe/{participant_id}"
            answer = await get_remote_sdp(whep_url, self.token, pc.localDescription.sdp)
            if not answer:
                logger.error(f"❌ Failed to subscribe to {participant_id}")
                return
            await pc.setRemoteDescription(RTCSessionDescription(sdp=fix_ivs_answer_sdp(answer), type="answer"))
            logger.info(f"✅ Subscribed to {speaker_label} ({participant_id})")

        except Exception as e:
            logger.error(f"❌ Error subscribing to {participant_id}: {e}")
            traceback.print_exc()

    async def _process_audio(self, track, pc, el_ws, resampler, label, sub):
        """Stream audio from a participant to their ElevenLabs connection"""
        while pc.connectionState not in ("connected", "completed"):
            await asyncio.sleep(0.1)

        frame_count = 0
        try:
            while not sub.get("should_stop", False):
                try:
                    frame = await asyncio.wait_for(track.recv(), timeout=5.0)
                    for resampled in resampler.resample(frame):
                        audio_bytes = resampled.to_ndarray().tobytes()
                        msg = {
                            "message_type": "input_audio_chunk",
                            "audio_base_64": base64.b64encode(audio_bytes).decode("utf-8"),
                            "commit": False,
                            "sample_rate": SAMPLE_RATE,
                        }
                        await el_ws.send(json.dumps(msg))
                        frame_count += 1
                        if frame_count == 1:
                            logger.info(f"🎤 First audio from {label}")
                        elif frame_count % 1000 == 0:
                            logger.info(f"🎤 {label}: {frame_count} frames")
                except asyncio.TimeoutError:
                    continue
        except Exception as e:
            if "closed" not in str(e).lower():
                logger.error(f"Audio error ({label}): {e}")

    async def _receive_transcripts(self, el_ws, speaker_label, participant_id, sub):
        """Receive transcripts from ElevenLabs for a participant"""
        while not sub.get("should_stop", False):
            try:
                message = await el_ws.recv()
                data = json.loads(message)
                msg_type = data.get("message_type", "")

                if msg_type == "partial_transcript":
                    # Scribe only cares about final transcripts
                    pass

                elif msg_type == "committed_transcript":
                    text = data.get("text", "")
                    if text:
                        print(f"[{speaker_label}] {text}")

                        # Publish as SEI
                        sei_data = {
                            "type": "scribe_transcript",
                            "speaker": speaker_label,
                            "participant_id": participant_id,
                            "content": text,
                            "timestamp": time.time(),
                        }
                        asyncio.ensure_future(self.sei_publisher.publish_json(sei_data, repeat_count=3))

                elif msg_type == "committed_transcript_with_timestamps":
                    text = data.get("text", "")
                    words = data.get("words", [])
                    lang = data.get("language_code", "")

                    # Extract speaker_id from word-level data if available
                    word_entries = [w for w in (words or []) if w.get("type") == "word"]
                    speaker_id = ""
                    if word_entries and word_entries[0].get("speaker_id"):
                        speaker_id = word_entries[0]["speaker_id"]

                    display_speaker = speaker_id or speaker_label
                    lang_info = f"  (lang: {lang})" if lang else ""

                    if text:
                        print(f"[{display_speaker}] {text}{lang_info}")

                        # Publish as SEI
                        sei_data = {
                            "type": "scribe_transcript",
                            "speaker": display_speaker,
                            "participant_id": participant_id,
                            "content": text,
                            "timestamp": time.time(),
                        }
                        if lang:
                            sei_data["language_code"] = lang
                        asyncio.ensure_future(self.sei_publisher.publish_json(sei_data, repeat_count=3))

                elif msg_type in (
                    "error",
                    "auth_error",
                    "quota_exceeded",
                    "rate_limited",
                    "commit_throttled",
                    "input_error",
                    "chunk_size_exceeded",
                    "resource_exhausted",
                    "session_time_limit_exceeded",
                    "insufficient_audio_activity",
                    "transcriber_error",
                    "queue_overflow",
                    "unaccepted_terms",
                ):
                    error_msg = data.get("error", "Unknown error")
                    logger.error(f"❌ ElevenLabs {msg_type} ({speaker_label}): {error_msg}")
                    if msg_type in ("auth_error", "quota_exceeded", "unaccepted_terms"):
                        break

                elif msg_type == "session_started":
                    pass

            except websockets.exceptions.ConnectionClosed:
                logger.info(f"ElevenLabs WebSocket closed for {speaker_label}")
                break
            except Exception as e:
                if not sub.get("should_stop", False):
                    logger.error(f"Transcript receive error ({speaker_label}): {e}")
                break

    async def _consume_track(self, track):
        try:
            while True:
                await track.recv()
        except Exception:
            pass

    async def _unsubscribe(self, participant_id):
        sub = self._subscriptions.pop(participant_id, None)
        if not sub:
            return
        sub["should_stop"] = True
        try:
            if sub.get("recv_task"):
                sub["recv_task"].cancel()
            if sub.get("el_ws"):
                await sub["el_ws"].close()
            if sub.get("pc"):
                await sub["pc"].close()
        except Exception:
            pass
        logger.info(f"🧹 Unsubscribed from {sub.get('user_id', participant_id)}")

    async def shutdown(self):
        for pid in list(self._subscriptions.keys()):
            await self._unsubscribe(pid)
        for pc in self._connections:
            try:
                await pc.close()
            except Exception:
                pass
        logger.info("✅ Scribe shutdown complete")


# ── CLI ─────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(
        description="IVS Real-Time Stage Meeting Scribe powered by ElevenLabs",
        epilog="""
Examples:
  %(prog)s --token eyJ...
  %(prog)s --token eyJ... --language-code auto --include-language-detection
  %(prog)s --token eyJ... --commit-strategy manual
  %(prog)s --token eyJ... --vad-silence-threshold-secs 2.0 --vad-threshold 0.3

Environment variables:
  ELEVENLABS_API_KEY    ElevenLabs API key (alternative to --elevenlabs-api-key)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--token", required=True, help="IVS participant token with PUBLISH and SUBSCRIBE capabilities")
    parser.add_argument("--elevenlabs-api-key", default=None, help="ElevenLabs API key (or set ELEVENLABS_API_KEY env var)")
    parser.add_argument("--model-id", default="scribe_v2_realtime", help="ElevenLabs STT model ID (default: scribe_v2_realtime)")
    parser.add_argument("--language-code", default="en", help="Language code or 'auto' (default: en)")
    parser.add_argument(
        "--commit-strategy",
        default="vad",
        choices=["vad", "manual"],
        help="Transcription commit strategy (default: vad)",
    )

    vad = parser.add_argument_group("vad settings")
    vad.add_argument("--vad-silence-threshold-secs", type=float, default=1.5, help="Seconds of silence before auto-commit (default: 1.5)")
    vad.add_argument("--vad-threshold", type=float, default=0.4, help="Speech detection sensitivity 0.0-1.0 (default: 0.4)")

    parser.add_argument(
        "--include-timestamps",
        type=lambda x: x.lower() in ("true", "1", "yes"),
        default=True,
        help="Include word-level timestamps (default: true)",
    )
    parser.add_argument(
        "--include-language-detection",
        type=lambda x: x.lower() in ("true", "1", "yes"),
        default=False,
        help="Include language detection (default: false)",
    )
    parser.add_argument("--ice-timeout", type=int, default=1, help="ICE timeout in seconds (default: 1)")
    return parser.parse_args()


async def main():
    args = parse_args()

    global ICE_TIMEOUT
    ICE_TIMEOUT = args.ice_timeout

    elevenlabs_api_key = args.elevenlabs_api_key or os.environ.get("ELEVENLABS_API_KEY", "")
    if not elevenlabs_api_key:
        logger.error("ElevenLabs API key required. Use --elevenlabs-api-key or ELEVENLABS_API_KEY env var.")
        return

    token_payload = parse_jwt(args.token)
    if not token_payload:
        return

    caps = token_payload.get("capabilities", {})
    if not caps.get("allow_publish"):
        logger.error("❌ Token missing PUBLISH capability")
        return
    if not caps.get("allow_subscribe"):
        logger.error("❌ Token missing SUBSCRIBE capability")
        return

    logger.info("📝 Starting IVS Stage Meeting Scribe (ElevenLabs)")
    logger.info(f"🧊 ICE timeout: {ICE_TIMEOUT}s")
    logger.info(f"🎤 Model: {args.model_id}")
    logger.info(f"🌍 Language: {args.language_code}")
    logger.info(f"📋 Commit strategy: {args.commit_strategy}")
    if args.commit_strategy == "vad":
        logger.info(f"   VAD silence: {args.vad_silence_threshold_secs}s, threshold: {args.vad_threshold}")

    scribe = MeetingScribe(
        token=args.token,
        elevenlabs_api_key=elevenlabs_api_key,
        model_id=args.model_id,
        language_code=args.language_code,
        commit_strategy=args.commit_strategy,
        vad_silence_threshold_secs=args.vad_silence_threshold_secs,
        vad_threshold=args.vad_threshold,
        include_timestamps=args.include_timestamps,
        include_language_detection=args.include_language_detection,
    )

    try:
        await scribe.start()
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        traceback.print_exc()
    finally:
        await scribe.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
