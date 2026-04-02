#!/usr/bin/env python3
"""
IVS Real-Time Stage Meeting Scribe powered by Deepgram

Joins an IVS stage as a silent participant, subscribes to all other
participants' audio, transcribes everything with Deepgram Nova-3
(with speaker diarization), runs periodic text intelligence analysis
(sentiment, topics, intents, summarization), and publishes transcripts
and insights back to the stage via SEI metadata embedded in a static
Deepgram-branded video track.
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

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType

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
logger = logging.getLogger("ivs-stage-deepgram-scribe")
for noisy in ["aiortc", "aioice", "aioice.stun", "websockets"]:
    logging.getLogger(noisy).setLevel(logging.ERROR)

SAMPLE_RATE = 16000


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


# ── Text Intelligence Analyzer ──────────────────────────────────


class TextIntelligenceAnalyzer:
    """Periodically analyzes accumulated transcripts via Deepgram Read API"""

    def __init__(self, api_key, sentiment=False, topics=False, intents=False, summarize=False, language="en", interval=30, sei_publisher=None):
        self.api_key = api_key
        self.sentiment = sentiment
        self.topics = topics
        self.intents = intents
        self.summarize = summarize
        self.language = language
        self.interval = interval
        self.sei_publisher = sei_publisher
        self._buffer: List[str] = []
        self._all: List[str] = []
        self._task = None
        self._stop = False
        self._count = 0

    def add(self, text):
        if text.strip():
            self._buffer.append(text.strip())
            self._all.append(text.strip())

    def start(self):
        self._task = asyncio.create_task(self._loop())

    def stop(self):
        self._stop = True
        if self._task:
            self._task.cancel()

    async def _loop(self):
        while not self._stop:
            await asyncio.sleep(self.interval)
            if self._stop or not self._buffer:
                continue
            batch = list(self._buffer)
            self._buffer.clear()
            text = " ".join(batch)
            if len(text.split()) < 5:
                continue
            self._count += 1
            await self._analyze(text)

    async def _analyze(self, text):
        try:
            params = {"language": self.language}
            if self.sentiment:
                params["sentiment"] = "true"
            if self.topics:
                params["topics"] = "true"
            if self.intents:
                params["intents"] = "true"
            if self.summarize:
                params["summarize"] = "v2"

            headers = {"Authorization": f"Token {self.api_key}", "Content-Type": "application/json"}
            body = {"text": " ".join(self._all) if self.summarize else text}

            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None, lambda: requests.post("https://api.deepgram.com/v1/read", params=params, headers=headers, json=body, timeout=15)
            )

            if resp.status_code != 200:
                logger.warning(f"⚠️  Read API: {resp.status_code}")
                return

            results = resp.json().get("results", {})
            self._print_and_publish(results)
        except Exception as e:
            logger.error(f"❌ Text Intelligence error: {e}")

    def _print_and_publish(self, results):
        sei_data = {"type": "scribe_intelligence", "analysis_number": self._count, "timestamp": time.time()}

        print(f"\n{'─' * 60}")
        print(f"📊 TEXT INTELLIGENCE (#{self._count})")
        print(f"{'─' * 60}")

        if self.sentiment:
            segs = results.get("sentiments", {}).get("segments", [])
            if segs:
                pos = sum(1 for s in segs if s.get("sentiment") == "positive")
                neg = sum(1 for s in segs if s.get("sentiment") == "negative")
                neu = sum(1 for s in segs if s.get("sentiment") == "neutral")
                avg = sum(s.get("sentiment_score", 0) for s in segs) / len(segs)
                print(f"  💭 Sentiment: +{pos} ={neu} -{neg} (avg: {avg:.2f})")
                sei_data["sentiment"] = {"positive": pos, "neutral": neu, "negative": neg, "avg_score": round(avg, 2)}

        if self.topics:
            all_topics = set()
            for seg in results.get("topics", {}).get("segments", []):
                for t in seg.get("topics", []):
                    all_topics.add(t.get("topic", ""))
            if all_topics:
                print(f"  🏷️  Topics: {', '.join(sorted(all_topics))}")
                sei_data["topics"] = sorted(all_topics)

        if self.intents:
            all_intents = set()
            for seg in results.get("intents", {}).get("segments", []):
                for i in seg.get("intents", []):
                    all_intents.add(i.get("intent", ""))
            if all_intents:
                print(f"  🎯 Intents: {', '.join(sorted(all_intents))}")
                sei_data["intents"] = sorted(all_intents)

        if self.summarize:
            summary = results.get("summary", {}).get("text", "")
            if summary:
                print(f"  📝 Summary: {summary}")
                sei_data["summary"] = summary

        print(f"{'─' * 60}\n")

        if self.sei_publisher:
            asyncio.ensure_future(self.sei_publisher.publish_json(sei_data, repeat_count=3))


# ── Meeting Scribe ──────────────────────────────────────────────


class MeetingScribe:
    """Orchestrates multi-participant transcription on an IVS stage"""

    def __init__(
        self,
        token,
        deepgram_api_key,
        model="nova-3",
        language="en",
        smart_format=True,
        diarize=True,
        sentiment=False,
        topics=False,
        intents=False,
        summarize=False,
        intelligence_interval=30,
    ):
        self.token = token
        self.deepgram_api_key = deepgram_api_key
        self.model = model
        self.language = language
        self.smart_format = smart_format
        self.diarize = diarize

        self.token_payload = parse_jwt(token)
        self.my_jti = self.token_payload.get("jti", "")

        # SEI publisher
        self.sei_publisher = SeiPublisher(max_retry_attempts=3)
        set_global_sei_publisher(self.sei_publisher)

        # Text intelligence
        self.analyzer = None
        if sentiment or topics or intents or summarize:
            self.analyzer = TextIntelligenceAnalyzer(
                api_key=deepgram_api_key,
                sentiment=sentiment,
                topics=topics,
                intents=intents,
                summarize=summarize,
                language=language,
                interval=intelligence_interval,
                sei_publisher=self.sei_publisher,
            )

        # Track active subscriptions: participant_id -> {pc, dg_connection, task, ...}
        self._subscriptions: Dict[str, Dict[str, Any]] = {}
        self._publish_pc = None
        self._connections: List[RTCPeerConnection] = []

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

        # Start text intelligence
        if self.analyzer:
            self.analyzer.start()

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
        """Subscribe to a participant's audio and start transcribing"""
        try:
            config = RTCConfiguration()
            config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
            pc = RTCPeerConnection(config)
            pc.addTransceiver("audio", direction="recvonly")
            pc.addTransceiver("video", direction="recvonly")

            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            dg_client = AsyncDeepgramClient(api_key=self.deepgram_api_key)

            # Build Deepgram connection params
            connect_params = {
                "model": self.model,
                "encoding": "linear16",
                "sample_rate": str(SAMPLE_RATE),
                "channels": "1",
                "punctuate": "true",
                "smart_format": str(self.smart_format).lower(),
                "diarize": str(self.diarize).lower(),
                "interim_results": "false",
                "endpointing": "300",
                "vad_events": "true",
            }
            if self.language != "auto":
                connect_params["language"] = self.language
            else:
                connect_params["request_options"] = {"additional_query_parameters": {"detect_language": "true"}}

            ctx = dg_client.listen.v1.connect(**connect_params)
            dg_conn = await ctx.__aenter__()

            # Store subscription info
            sub = {
                "pc": pc,
                "dg_ctx": ctx,
                "dg_conn": dg_conn,
                "user_id": user_id,
                "resampler": resampler,
            }
            self._subscriptions[participant_id] = sub
            self._connections.append(pc)

            # Register Deepgram event handlers
            speaker_label = user_id or participant_id[:8]

            def on_message(message, *args, **kwargs):
                msg_type = getattr(message, "type", "")
                if msg_type != "Results":
                    return
                if not message.channel or not message.channel.alternatives:
                    return
                alt = message.channel.alternatives[0]
                text = alt.transcript
                if not text:
                    return
                is_final = getattr(message, "is_final", False)
                if not is_final:
                    return

                confidence = getattr(alt, "confidence", 0.0)
                print(f"[{speaker_label}] {text}  (confidence: {confidence:.2f})")

                # Feed to text intelligence
                if self.analyzer:
                    self.analyzer.add(f"{speaker_label}: {text}")

                # Publish as SEI
                sei_data = {
                    "type": "scribe_transcript",
                    "speaker": speaker_label,
                    "participant_id": participant_id,
                    "content": text,
                    "confidence": round(confidence, 2),
                    "timestamp": time.time(),
                }
                asyncio.ensure_future(self.sei_publisher.publish_json(sei_data, repeat_count=3))

            dg_conn.on(EventType.OPEN, lambda *a, **k: logger.info(f"✅ Deepgram connected for {speaker_label}"))
            dg_conn.on(EventType.MESSAGE, on_message)
            dg_conn.on(EventType.ERROR, lambda e, *a, **k: logger.error(f"❌ Deepgram error ({speaker_label}): {e}"))
            dg_conn.on(EventType.CLOSE, lambda *a, **k: logger.info(f"Deepgram closed for {speaker_label}"))

            listen_task = asyncio.create_task(dg_conn.start_listening())
            sub["listen_task"] = listen_task

            # Audio processing
            @pc.on("track")
            def on_track(track):
                if track.kind == "audio":
                    asyncio.create_task(self._process_audio(track, pc, dg_conn, resampler, speaker_label))
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

    async def _process_audio(self, track, pc, dg_conn, resampler, label):
        """Stream audio from a participant to their Deepgram connection"""
        while pc.connectionState not in ("connected", "completed"):
            await asyncio.sleep(0.1)

        frame_count = 0
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(track.recv(), timeout=5.0)
                    for resampled in resampler.resample(frame):
                        audio_bytes = resampled.to_ndarray().tobytes()
                        await dg_conn.send_media(audio_bytes)
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
        try:
            if sub.get("listen_task"):
                sub["listen_task"].cancel()
            if sub.get("dg_ctx"):
                await sub["dg_ctx"].__aexit__(None, None, None)
            if sub.get("pc"):
                await sub["pc"].close()
        except Exception:
            pass
        logger.info(f"🧹 Unsubscribed from {sub.get('user_id', participant_id)}")

    async def shutdown(self):
        if self.analyzer:
            self.analyzer.stop()
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
        description="IVS Real-Time Stage Meeting Scribe powered by Deepgram",
        epilog="""
Examples:
  %(prog)s --token eyJ... --sentiment --topics --summarize
  %(prog)s --token eyJ... --model nova-3-meeting --language auto
  %(prog)s --token eyJ... --sentiment --intents --intelligence-interval 60

Environment variables:
  DEEPGRAM_API_KEY    Deepgram API key (alternative to --deepgram-api-key)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--token", required=True, help="IVS participant token with PUBLISH and SUBSCRIBE capabilities")
    parser.add_argument("--deepgram-api-key", default=None, help="Deepgram API key (or set DEEPGRAM_API_KEY env var)")
    parser.add_argument("--model", default="nova-3", help="Deepgram STT model (default: nova-3)")
    parser.add_argument("--language", default="en", help="Language code or 'auto' (default: en)")
    parser.add_argument("--smart-format", type=lambda x: x.lower() in ("true", "1", "yes"), default=True, help="Smart formatting (default: true)")
    parser.add_argument("--diarize", type=lambda x: x.lower() in ("true", "1", "yes"), default=True, help="Speaker diarization (default: true)")

    ti = parser.add_argument_group("text intelligence")
    ti.add_argument("--sentiment", action="store_true", help="Enable sentiment analysis")
    ti.add_argument("--topics", action="store_true", help="Enable topic detection")
    ti.add_argument("--intents", action="store_true", help="Enable intent recognition")
    ti.add_argument("--summarize", action="store_true", help="Enable rolling summarization")
    ti.add_argument("--intelligence-interval", type=int, default=30, help="Analysis interval in seconds (default: 30)")

    parser.add_argument("--ice-timeout", type=int, default=1, help="ICE timeout in seconds (default: 1)")
    return parser.parse_args()


async def main():
    args = parse_args()

    global ICE_TIMEOUT
    ICE_TIMEOUT = args.ice_timeout

    deepgram_api_key = args.deepgram_api_key or os.environ.get("DEEPGRAM_API_KEY", "")
    if not deepgram_api_key:
        logger.error("Deepgram API key required. Use --deepgram-api-key or DEEPGRAM_API_KEY env var.")
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

    logger.info("📝 Starting IVS Stage Meeting Scribe")
    logger.info(f"🧊 ICE timeout: {ICE_TIMEOUT}s")
    logger.info(f"🎤 Model: {args.model}")
    logger.info(f"🌍 Language: {args.language}")
    logger.info(f"🔍 Diarize: {args.diarize}")

    features = []
    if args.sentiment:
        features.append("sentiment")
    if args.topics:
        features.append("topics")
    if args.intents:
        features.append("intents")
    if args.summarize:
        features.append("summarize")
    if features:
        logger.info(f"🧠 Text Intelligence: {', '.join(features)} (every {args.intelligence_interval}s)")

    scribe = MeetingScribe(
        token=args.token,
        deepgram_api_key=deepgram_api_key,
        model=args.model,
        language=args.language,
        smart_format=args.smart_format,
        diarize=args.diarize,
        sentiment=args.sentiment,
        topics=args.topics,
        intents=args.intents,
        summarize=args.summarize,
        intelligence_interval=args.intelligence_interval,
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
