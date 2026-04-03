#!/usr/bin/env python3
"""
IVS Real-Time Stage Group Agent powered by Deepgram

A passive voice agent that joins a multi-participant IVS stage, transcribes
all speakers, and responds only when invoked by a configurable wake word.
Combines the scribe's multi-participant subscribe with the agent's voice
response capabilities.

Architecture:
  - Subscribes to all publishing participants via stage events WebSocket
  - Each participant gets a Deepgram Nova-3 STT connection with keyterm boosting
  - Maintains a rolling transcript buffer for conversation context
  - When the wake word is detected, injects context + user message into the
    Deepgram Voice Agent, which generates a spoken response
  - After responding, enters an active listening window where follow-ups
    don't require the wake word
  - Publishes agent audio/video and all transcripts via SEI
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
from collections import deque

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
)

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType

# SEI patch before aiortc encoder is used
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
import stages_sei.h264_sei_patch
from stages_sei import SeiPublisher, set_global_sei_publisher

# Reuse audio/video tracks from Nova demo
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "stages-nova-s2s"))
from agent_audio_track import AgentAudioTrack
from agent_video_track import AgentVideoTrack

from deepgram_agent_manager import DeepgramAgentManager, INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE

warnings.filterwarnings("ignore")

# ── ICE timeout patch ───────────────────────────────────────────
ICE_TIMEOUT = 1
_orig = aioice.ice.Connection.get_component_candidates


async def _patched(self, component, addresses, timeout=None):
    return await _orig(self, component, addresses, timeout or ICE_TIMEOUT)


aioice.ice.Connection.get_component_candidates = _patched

# ── Logging ─────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("ivs-stage-deepgram-group-agent")
for noisy in ["aiortc", "aioice", "aioice.stun", "websockets"]:
    logging.getLogger(noisy).setLevel(logging.ERROR)

STT_SAMPLE_RATE = 16000
CHANNELS = 1


# ── Utility functions ───────────────────────────────────────────


def parse_jwt(token):
    try:
        parts = token.split(".")
        payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except Exception as e:
        logger.error(f"Error parsing JWT: {e}")
        return {}


def fix_ivs_answer_sdp(sdp):
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


# ── Group Agent ─────────────────────────────────────────────────


class GroupAgent:
    """Multi-participant voice agent with wake word activation"""

    def __init__(
        self,
        token,
        deepgram_api_key,
        wake_word="hey assistant",
        sleep_word="thank you assistant",
        context_window=60,
        active_listening_window=10,
        model="nova-3",
        language="en",
        voice="aura-2-asteria-en",
        think_model="gpt-4o-mini",
        think_provider="open_ai",
        prompt="You are a helpful assistant participating in a group meeting.",
        enable_frame_analysis=True,
        bedrock_model_id="us.anthropic.claude-sonnet-4-6",
        bedrock_region="us-east-1",
        speak_config=None,
    ):
        self.token = token
        self.deepgram_api_key = deepgram_api_key
        self.wake_word = wake_word.lower().strip()
        self.sleep_word = sleep_word.lower().strip() if sleep_word else ""
        self.context_window = context_window
        self.active_listening_window = active_listening_window
        self.model = model
        self.language = language

        self.token_payload = parse_jwt(token)
        self.my_jti = self.token_payload.get("jti", "")

        # Rolling transcript buffer: list of (timestamp, speaker, text)
        self._transcript_log: deque = deque()

        # Active listening state
        self._active_until: float = 0  # timestamp when active listening expires
        self._agent_speaking: bool = False  # True while agent is responding
        self._last_trigger_speaker: str = ""

        # Subscriptions
        self._subscriptions: Dict[str, Dict[str, Any]] = {}
        self._subscribing: set = set()  # Guard against duplicate subscribe attempts
        self._connections: List[RTCPeerConnection] = []

        # Agent manager (voice responses) — also creates the SEI publisher
        self.agent_video_track = AgentVideoTrack(width=640, height=360, fps=20)
        self.agent_audio_track = AgentAudioTrack(
            agent_video_track=self.agent_video_track,
            sample_rate=OUTPUT_SAMPLE_RATE,
            channels=CHANNELS,
        )
        self.agent_manager = DeepgramAgentManager(
            api_key=deepgram_api_key,
            agent_audio_track=self.agent_audio_track,
            agent_video_track=self.agent_video_track,
            voice=voice,
            think_model=think_model,
            think_provider=think_provider,
            prompt=self._build_agent_prompt(prompt),
            greeting="",  # No greeting — passive until wake word
            language=language,
            output_sample_rate=OUTPUT_SAMPLE_RATE,
            enable_frame_analysis=enable_frame_analysis,
            bedrock_model_id=bedrock_model_id,
            bedrock_region=bedrock_region,
            speak_config=speak_config,
        )

        # Use the agent manager's SEI publisher — it's set as global for the H.264 patch
        self.sei_publisher = self.agent_manager.sei_publisher

        # We'll hook into agent events after initialize() — see start()

    def _build_agent_prompt(self, base_prompt):
        return (
            f"{base_prompt}\n\n"
            "You are a passive participant in a group meeting. You only speak when "
            "someone addresses you. When you receive a message, it will include recent "
            "conversation context from the meeting so you can provide informed responses. "
            "Keep your responses concise and relevant to the discussion. "
            "If asked to summarize, reference the conversation context provided."
        )

    def _get_context(self) -> str:
        """Get recent conversation context within the configured time window"""
        cutoff = time.time() - self.context_window
        lines = []
        for ts, speaker, text in self._transcript_log:
            if ts >= cutoff:
                lines.append(f"[{speaker}] {text}")
        return "\n".join(lines) if lines else "(no recent conversation)"

    def _is_active_listening(self) -> bool:
        return self._agent_speaking or time.time() < self._active_until

    def _activate_listening(self):
        self._active_until = time.time() + self.active_listening_window
        logger.info(f"👂 Active listening for {self.active_listening_window}s")

    def _on_transcript(self, participant_id: str, speaker_label: str, text: str):
        """Called for every final transcript from any participant"""
        now = time.time()
        self._transcript_log.append((now, speaker_label, text))

        # Prune old entries beyond 2x context window
        cutoff = now - (self.context_window * 2)
        while self._transcript_log and self._transcript_log[0][0] < cutoff:
            self._transcript_log.popleft()

        # Publish as SEI
        sei_data = {
            "type": "group_agent_transcript",
            "speaker": speaker_label,
            "participant_id": participant_id,
            "content": text,
            "timestamp": now,
        }
        asyncio.ensure_future(self.sei_publisher.publish_json(sei_data, repeat_count=3))

        # Strip punctuation for reliable wake/sleep word matching
        # Deepgram may transcribe "hey assistant" as "Hey, assistant." etc.
        import re as _re
        text_normalized = _re.sub(r'[^\w\s]', '', text.lower())

        # Check for sleep word first — silences agent and/or ends active listening
        if self.sleep_word and self.sleep_word in text_normalized and (self._agent_speaking or self._is_active_listening()):
            logger.info(f"😴 Sleep word detected from {speaker_label} — going passive")
            self._agent_speaking = False
            self._active_until = 0
            self.agent_manager._muted = True
            if self.agent_manager.agent_audio_track:
                asyncio.ensure_future(self.agent_manager._clear_agent_audio())
            return

        # Check for wake word or active listening
        triggered = False
        user_message = text

        if self.wake_word in text_normalized:
            # Extract the part after the wake word from the original text
            idx = text_normalized.index(self.wake_word)
            # Map back to original text approximately
            after = text[idx + len(self.wake_word):].strip().lstrip(".,!? ")
            user_message = after if after else text
            triggered = True
            self._last_trigger_speaker = speaker_label
            logger.info(f"🎯 Wake word detected from {speaker_label}: '{text}'")
        elif self._is_active_listening():
            triggered = True
            logger.info(f"👂 Active listening — processing from {speaker_label}: '{text}'")

        if triggered:
            # Mark as active — agent is about to speak, accept follow-ups/interruptions
            self._agent_speaking = True
            context = self._get_context()
            full_message = f"[Meeting context]\n{context}\n\n[{speaker_label} says]: {user_message}"
            asyncio.ensure_future(self._inject_to_agent(full_message))

    async def _inject_to_agent(self, message: str):
        """Inject a user message into the Deepgram Agent"""
        try:
            from deepgram.agent.v1.types.agent_v1inject_user_message import AgentV1InjectUserMessage

            inject = AgentV1InjectUserMessage(
                type="InjectUserMessage",
                content=message,
            )
            if self.agent_manager._connection:
                await self.agent_manager._connection.send_inject_user_message(inject)
                logger.info(f"💬 Injected message to agent ({len(message)} chars)")
        except Exception as e:
            logger.error(f"❌ Error injecting message to agent: {e}")

    async def start(self):
        """Initialize agent, publish tracks, and start listening for participants"""
        # Initialize the Deepgram Voice Agent (for responses)
        await self.agent_manager.initialize()

        # Register our own handler on the agent connection for tracking speaking state.
        # Must happen AFTER initialize() since that's when the connection is created.
        def _on_agent_event(message, *args, **kwargs):
            if isinstance(message, bytes):
                return
            msg_type = getattr(message, "type", "")
            if msg_type == "AgentStartedSpeaking":
                self._agent_speaking = True
            elif msg_type == "AgentAudioDone":
                self._agent_speaking = False
                self._activate_listening()
                logger.info(f"👂 Agent finished speaking — active listening for {self.active_listening_window}s")

        if self.agent_manager._connection:
            self.agent_manager._connection.on(EventType.MESSAGE, _on_agent_event)

        # Start keep-alive task — the agent expects audio or keep-alives to stay connected.
        # Since we use text injection (not audio streaming), we send periodic keep-alives.
        self._keepalive_task = asyncio.create_task(self._agent_keepalive())

        # Publish agent audio/video to stage
        logger.info("📹 Publishing agent tracks to stage...")
        config = RTCConfiguration()
        config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
        pc = RTCPeerConnection(config)
        pc.addTransceiver(self.agent_audio_track, direction="sendrecv")
        pc.addTransceiver(self.agent_video_track, direction="sendrecv")
        self.agent_audio_track.set_peer_connection(pc)

        await pc.setLocalDescription(await pc.createOffer())
        answer = await get_remote_sdp("https://global.whip.live-video.net", self.token, pc.localDescription.sdp)
        if not answer:
            logger.error("❌ Failed to publish agent tracks")
            return
        await pc.setRemoteDescription(RTCSessionDescription(sdp=fix_ivs_answer_sdp(answer), type="answer"))
        self._connections.append(pc)
        logger.info("✅ Agent tracks published")

        # Connect to stage events
        events_url = self.token_payload.get("events_url")
        topic = self.token_payload.get("topic")
        if events_url and topic:
            ws_url = f"{events_url}/{topic}"
            logger.info(f"🔌 Connecting to stage events: {ws_url}")
            await self._event_loop(ws_url)
        else:
            logger.error("❌ No events_url/topic in token")

    async def _event_loop(self, ws_url):
        try:
            async with websockets.connect(ws_url, subprotocols=[self.token]) as ws:
                logger.info("✅ Connected to stage events")
                await ws.send(json.dumps({"type": "PING"}))
                async for message in ws:
                    try:
                        await self._handle_event(json.loads(message))
                    except json.JSONDecodeError:
                        pass
        except ConnectionClosed:
            logger.warning("🔌 Stage events WebSocket closed")
        except Exception as e:
            logger.error(f"❌ Events error: {e}")

    async def _handle_event(self, event):
        if event.get("type") != "STAGE_STATE":
            return
        participants = event.get("payload", {}).get("participants", [])
        current_ids = set()

        for p in participants:
            pid = p.get("id", "")
            is_pub = p.get("isPublishing", False)
            user_id = p.get("userId", "")
            if pid == self.my_jti:
                continue
            current_ids.add(pid)
            if is_pub and pid not in self._subscriptions and pid not in self._subscribing:
                self._subscribing.add(pid)
                logger.info(f"👤 New participant: {pid} (userId: {user_id})")
                asyncio.create_task(self._subscribe_to(pid, user_id))

        for pid in list(self._subscriptions.keys()):
            if pid not in current_ids:
                logger.info(f"👋 Participant left: {pid}")
                await self._unsubscribe(pid)

    async def _subscribe_to(self, participant_id, user_id=""):
        try:
            config = RTCConfiguration()
            config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
            pc = RTCPeerConnection(config)
            pc.addTransceiver("audio", direction="recvonly")
            pc.addTransceiver("video", direction="recvonly")

            resampler = av.AudioResampler(format="s16", layout="mono", rate=STT_SAMPLE_RATE)
            dg_client = AsyncDeepgramClient(api_key=self.deepgram_api_key)

            connect_params = {
                "model": self.model,
                "encoding": "linear16",
                "sample_rate": str(STT_SAMPLE_RATE),
                "channels": "1",
                "punctuate": "true",
                "smart_format": "true",
                "interim_results": "false",
                "endpointing": "300",
                "vad_events": "true",
                "keyterm": [self.wake_word] + ([self.sleep_word] if self.sleep_word else []),
            }
            if self.language != "auto":
                connect_params["language"] = self.language
            else:
                connect_params["request_options"] = {"additional_query_parameters": {"detect_language": "true"}}

            ctx = dg_client.listen.v1.connect(**connect_params)
            dg_conn = await ctx.__aenter__()

            sub = {"pc": pc, "dg_ctx": ctx, "dg_conn": dg_conn, "user_id": user_id, "resampler": resampler}
            self._subscriptions[participant_id] = sub
            self._connections.append(pc)

            speaker_label = user_id or participant_id[:8]

            def on_message(message, *args, **kwargs):
                msg_type = getattr(message, "type", "")
                if msg_type != "Results":
                    return
                if not message.channel or not message.channel.alternatives:
                    return
                alt = message.channel.alternatives[0]
                text = alt.transcript
                if not text or not getattr(message, "is_final", False):
                    return

                confidence = getattr(alt, "confidence", 0.0)
                print(f"[{speaker_label}] {text}  (confidence: {confidence:.2f})")
                self._on_transcript(participant_id, speaker_label, text)

            dg_conn.on(EventType.OPEN, lambda *a, **k: logger.info(f"✅ STT connected for {speaker_label}"))
            dg_conn.on(EventType.MESSAGE, on_message)
            dg_conn.on(EventType.ERROR, lambda e, *a, **k: logger.error(f"❌ STT error ({speaker_label}): {e}"))
            dg_conn.on(EventType.CLOSE, lambda *a, **k: logger.info(f"STT closed for {speaker_label}"))

            listen_task = asyncio.create_task(dg_conn.start_listening())
            sub["listen_task"] = listen_task

            @pc.on("track")
            def on_track(track):
                if track.kind == "audio":
                    asyncio.create_task(self._process_audio(track, pc, dg_conn, resampler, speaker_label))
                elif track.kind == "video":
                    asyncio.create_task(self._consume(track))

            await pc.setLocalDescription(await pc.createOffer())
            whip_url = self.token_payload.get("whip_url", "")
            answer = await get_remote_sdp(f"{whip_url}/subscribe/{participant_id}", self.token, pc.localDescription.sdp)
            if not answer:
                logger.error(f"❌ Failed to subscribe to {participant_id}")
                return
            await pc.setRemoteDescription(RTCSessionDescription(sdp=fix_ivs_answer_sdp(answer), type="answer"))
            logger.info(f"✅ Subscribed to {speaker_label} ({participant_id})")
            self._subscribing.discard(participant_id)

        except Exception as e:
            self._subscribing.discard(participant_id)
            logger.error(f"❌ Subscribe error for {participant_id}: {e}")
            traceback.print_exc()

    async def _process_audio(self, track, pc, dg_conn, resampler, label):
        while pc.connectionState not in ("connected", "completed"):
            await asyncio.sleep(0.1)
        frame_count = 0
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(track.recv(), timeout=5.0)
                    for resampled in resampler.resample(frame):
                        await dg_conn.send_media(resampled.to_ndarray().tobytes())
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

    async def _consume(self, track):
        try:
            while True:
                frame = await track.recv()
                # Store frame for vision tool
                self.agent_manager.frame = frame
        except Exception:
            pass

    async def _agent_keepalive(self):
        """Send periodic keep-alives to prevent the agent from timing out"""
        try:
            while True:
                await asyncio.sleep(5)
                if self.agent_manager._connection and not self.agent_manager._should_stop:
                    try:
                        await self.agent_manager._connection.send_keep_alive()
                    except Exception:
                        break
        except asyncio.CancelledError:
            pass

    async def _unsubscribe(self, pid):
        sub = self._subscriptions.pop(pid, None)
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
        logger.info(f"🧹 Unsubscribed from {sub.get('user_id', pid)}")

    async def shutdown(self):
        if hasattr(self, "_keepalive_task"):
            self._keepalive_task.cancel()
        await self.agent_manager.shutdown()
        for pid in list(self._subscriptions.keys()):
            await self._unsubscribe(pid)
        for pc in self._connections:
            try:
                await pc.close()
            except Exception:
                pass
        logger.info("✅ Group agent shutdown complete")


# ── CLI ─────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(
        description="IVS Real-Time Stage Group Agent — wake-word activated voice assistant for multi-participant stages",
        epilog="""
Examples:
  %(prog)s --token eyJ... --wake-word "hey assistant"
  %(prog)s --token eyJ... --wake-word "hey deepgram" --context-window 120 --active-listening-window 15
  %(prog)s --token eyJ... --wake-word "ok agent" --voice aura-2-orion-en --think-model gpt-4o
  %(prog)s --token eyJ... --wake-word "hey assistant" --disable-frame-analysis

Environment variables:
  DEEPGRAM_API_KEY    Deepgram API key (alternative to --deepgram-api-key)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--token", required=True, help="IVS participant token (PUBLISH + SUBSCRIBE)")
    parser.add_argument("--deepgram-api-key", default=None, help="Deepgram API key (or DEEPGRAM_API_KEY env var)")
    parser.add_argument("--wake-word", default="hey assistant", help="Wake word/phrase to activate the agent (default: 'hey assistant')")
    parser.add_argument(
        "--sleep-word", default="thank you assistant", help="Sleep word/phrase to immediately silence the agent (default: 'thank you assistant')"
    )
    parser.add_argument(
        "--context-window", type=int, default=60, help="Seconds of conversation context to include when agent is triggered (default: 60)"
    )
    parser.add_argument(
        "--active-listening-window",
        type=int,
        default=10,
        help="Seconds to stay active after responding, allowing follow-ups without wake word (default: 10)",
    )
    parser.add_argument("--model", default="nova-3", help="Deepgram STT model (default: nova-3)")
    parser.add_argument("--language", default="en", help="Language code or 'auto' (default: en)")
    parser.add_argument("--voice", default="aura-2-asteria-en", help="Deepgram TTS voice (default: aura-2-asteria-en)")
    parser.add_argument("--think-model", default="gpt-4o-mini", help="LLM model (default: gpt-4o-mini)")
    parser.add_argument("--think-provider", default="open_ai", choices=["open_ai", "anthropic", "groq"], help="LLM provider (default: open_ai)")
    parser.add_argument("--prompt", default="You are a helpful assistant participating in a group meeting.", help="Base system prompt")
    parser.add_argument("--disable-frame-analysis", action="store_true", help="Disable vision/frame analysis")
    parser.add_argument("--bedrock-model-id", default="us.anthropic.claude-sonnet-4-6", help="Bedrock model for frame analysis")
    parser.add_argument("--bedrock-region", default="us-east-1", help="AWS region for Bedrock")
    parser.add_argument("--ice-timeout", type=int, default=1, help="ICE timeout in seconds (default: 1)")

    # BYO TTS provider options
    tts = parser.add_argument_group("tts provider", "Third-party TTS provider (overrides --voice). See Deepgram docs for details.")
    tts.add_argument("--tts-provider", default=None, choices=["deepgram", "open_ai", "eleven_labs", "cartesia", "aws_polly"],
                     help="TTS provider type (default: deepgram)")
    tts.add_argument("--tts-model", default=None, help="TTS model ID (e.g., 'tts-1' for OpenAI, 'eleven_turbo_v2_5' for ElevenLabs)")
    tts.add_argument("--tts-voice", default=None, help="TTS voice ID (provider-specific)")
    tts.add_argument("--tts-endpoint-url", default=None, help="TTS API endpoint URL (required for BYO providers)")
    tts.add_argument("--tts-api-key", default=None, help="TTS provider API key (used in endpoint headers)")
    tts.add_argument("--tts-language", default=None, help="TTS language code (for providers that require it)")

    return parser.parse_args()


async def main():
    args = parse_args()

    global ICE_TIMEOUT
    ICE_TIMEOUT = args.ice_timeout

    deepgram_api_key = args.deepgram_api_key or os.environ.get("DEEPGRAM_API_KEY", "")
    if not deepgram_api_key:
        logger.error("Deepgram API key required.")
        return

    token_payload = parse_jwt(args.token)
    if not token_payload:
        return
    caps = token_payload.get("capabilities", {})
    if not caps.get("allow_publish") or not caps.get("allow_subscribe"):
        logger.error("❌ Token needs both PUBLISH and SUBSCRIBE capabilities")
        return

    logger.info("🎬 Starting IVS Stage Group Agent")
    logger.info(f"🗣️  Wake word: '{args.wake_word}'")
    logger.info(f"� Sleep word: '{args.sleep_word}'")
    logger.info(f"�📜 Context window: {args.context_window}s")
    logger.info(f"👂 Active listening window: {args.active_listening_window}s")
    logger.info(f"🎤 STT model: {args.model}")
    logger.info(f"🗣️  Voice: {args.voice}")
    logger.info(f"🧠 Think: {args.think_provider}/{args.think_model}")
    logger.info(f"🔍 Frame analysis: {'disabled' if args.disable_frame_analysis else 'enabled'}")

    # Build BYO TTS speak config if a third-party provider is specified
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

        # Add endpoint if specified (required for BYO providers)
        if args.tts_endpoint_url:
            endpoint = {"url": args.tts_endpoint_url}
            if args.tts_api_key:
                # Set auth header based on provider type
                if args.tts_provider == "open_ai":
                    endpoint["headers"] = {"authorization": f"Bearer {args.tts_api_key}"}
                elif args.tts_provider == "eleven_labs":
                    endpoint["headers"] = {"xi-api-key": args.tts_api_key, "Content-Type": "application/json"}
                elif args.tts_provider == "cartesia":
                    endpoint["headers"] = {"x-api-key": args.tts_api_key}
            speak_config["endpoint"] = endpoint

        logger.info(f"🗣️  TTS provider: {args.tts_provider} (model: {args.tts_model or 'default'})")
        logger.info(f"📋 Speak config: {json.dumps(speak_config, indent=2)}")
    elif args.tts_provider == "deepgram":
        # Explicit deepgram — use --voice as normal
        logger.info(f"🗣️  TTS: Deepgram ({args.voice})")

    agent = GroupAgent(
        token=args.token,
        deepgram_api_key=deepgram_api_key,
        wake_word=args.wake_word,
        sleep_word=args.sleep_word,
        context_window=args.context_window,
        active_listening_window=args.active_listening_window,
        model=args.model,
        language=args.language,
        voice=args.voice,
        think_model=args.think_model,
        think_provider=args.think_provider,
        prompt=args.prompt,
        enable_frame_analysis=not args.disable_frame_analysis,
        bedrock_model_id=args.bedrock_model_id,
        bedrock_region=args.bedrock_region,
        speak_config=speak_config,
    )

    try:
        await agent.start()
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        traceback.print_exc()
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
