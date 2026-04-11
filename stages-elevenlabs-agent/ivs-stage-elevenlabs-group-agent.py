#!/usr/bin/env python3
"""
IVS Real-Time Stage Group Agent powered by ElevenLabs

A passive voice agent that joins a multi-participant IVS stage, transcribes
all speakers using ElevenLabs Scribe v2 Realtime, and responds only when
invoked by a configurable wake word.

Architecture:
  - Subscribes to all publishing participants via stage events WebSocket
  - Each participant gets an ElevenLabs Scribe v2 Realtime STT WebSocket
  - Maintains a rolling transcript buffer for conversation context
  - When the wake word is detected, injects context + user message into the
    ElevenLabs Conversational AI agent, which generates a spoken response
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
import re
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

# SEI patch before aiortc encoder is used
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
import stages_sei.h264_sei_patch
from stages_sei import SeiPublisher, set_global_sei_publisher

# Reuse audio/video tracks from Nova demo
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "stages-nova-s2s"))
from agent_audio_track import AgentAudioTrack
from agent_video_track import AgentVideoTrack

from elevenlabs_agent_manager import ElevenLabsAgentManager, INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE

warnings.filterwarnings("ignore")

# ── ICE timeout patch ───────────────────────────────────────────
ICE_TIMEOUT = 1
_orig = aioice.ice.Connection.get_component_candidates


async def _patched(self, component, addresses, timeout=None):
    return await _orig(self, component, addresses, timeout or ICE_TIMEOUT)


aioice.ice.Connection.get_component_candidates = _patched

# ── Logging ─────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("ivs-stage-elevenlabs-group-agent")
for noisy in ["aiortc", "aioice", "aioice.stun", "websockets"]:
    logging.getLogger(noisy).setLevel(logging.ERROR)

STT_SAMPLE_RATE = 16000
CHANNELS = 1

# ElevenLabs Scribe Realtime endpoint
ELEVENLABS_STT_WS_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"


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
    """Multi-participant voice agent with wake word activation using ElevenLabs"""

    def __init__(
        self,
        token,
        elevenlabs_api_key,
        wake_word="hey assistant",
        sleep_word="thank you assistant",
        context_window=60,
        active_listening_window=10,
        model_id="scribe_v2_realtime",
        language_code="en",
        voice_id="JBFqnCBsd6RMkjVDRZzb",
        llm_model="gemini-2.0-flash",
        agent_id=None,
        prompt="You are a helpful assistant participating in a group meeting.",
        enable_frame_analysis=True,
        bedrock_model_id="us.anthropic.claude-sonnet-4-6",
        bedrock_region="us-east-1",
    ):
        self.token = token
        self.elevenlabs_api_key = elevenlabs_api_key
        self.wake_word = wake_word.lower().strip()
        self.sleep_word = sleep_word.lower().strip() if sleep_word else ""
        self.context_window = context_window
        self.active_listening_window = active_listening_window
        self.model_id = model_id
        self.language_code = language_code

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
        self.agent_manager = ElevenLabsAgentManager(
            api_key=elevenlabs_api_key,
            agent_id=agent_id,
            agent_audio_track=self.agent_audio_track,
            agent_video_track=self.agent_video_track,
            voice_id=voice_id,
            llm_model=llm_model,
            prompt=self._build_agent_prompt(prompt),
            greeting="",  # No greeting — passive until wake word
            language=language_code,
            participant_id=self.my_jti,
            enable_frame_analysis=enable_frame_analysis,
            bedrock_model_id=bedrock_model_id,
            bedrock_region=bedrock_region,
        )

        # Use the agent manager's SEI publisher
        self.sei_publisher = self.agent_manager.sei_publisher

    def _build_agent_prompt(self, base_prompt):
        return (
            f"{base_prompt}\n\n"
            "You are a passive participant in a group meeting. You only speak when "
            "someone addresses you. When you receive a message, it will include recent "
            "conversation context from the meeting so you can provide informed responses. "
            "Keep your responses concise and relevant to the discussion. "
            "If asked to summarize, reference the conversation context provided."
        )

    def _build_stt_ws_url(self) -> str:
        """Build the ElevenLabs Scribe Realtime WebSocket URL"""
        params = {
            "model_id": self.model_id,
            "audio_format": "pcm_16000",
            "commit_strategy": "vad",
        }
        if self.language_code and self.language_code != "auto":
            params["language_code"] = self.language_code
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{ELEVENLABS_STT_WS_URL}?{query_string}"

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
        text_normalized = re.sub(r"[^\w\s]", "", text.lower())

        # Check for sleep word first
        if self.sleep_word and self.sleep_word in text_normalized and (self._agent_speaking or self._is_active_listening()):
            logger.info(f"😴 Sleep word detected from {speaker_label} — going passive")
            self._agent_speaking = False
            self._active_until = 0
            if self.agent_manager.agent_audio_track:
                asyncio.ensure_future(self.agent_manager._clear_agent_audio())
            return

        # Check for wake word or active listening
        triggered = False
        user_message = text

        if self.wake_word in text_normalized:
            idx = text_normalized.index(self.wake_word)
            after = text[idx + len(self.wake_word) :].strip().lstrip(".,!? ")
            user_message = after if after else text
            triggered = True
            self._last_trigger_speaker = speaker_label
            logger.info(f"🎯 Wake word detected from {speaker_label}: '{text}'")
        elif self._is_active_listening():
            triggered = True
            logger.info(f"👂 Active listening — processing from {speaker_label}: '{text}'")

        if triggered:
            self._agent_speaking = True
            context = self._get_context()
            full_message = f"[Meeting context]\n{context}\n\n[{speaker_label} says]: {user_message}"
            asyncio.ensure_future(self._inject_to_agent(full_message))

    async def _inject_to_agent(self, message: str):
        """Inject context and user message into the ElevenLabs Conversational AI agent"""
        try:
            ws = self.agent_manager._ws
            if not ws:
                logger.error("❌ Agent WebSocket not connected")
                return

            # Send context as contextual_update
            context_msg = json.dumps(
                {
                    "type": "contextual_update",
                    "text": message,
                }
            )
            await ws.send(context_msg)

            # Send user message
            user_msg = json.dumps(
                {
                    "type": "user_message",
                    "text": message,
                }
            )
            await ws.send(user_msg)
            logger.info(f"💬 Injected message to agent ({len(message)} chars)")
        except Exception as e:
            logger.error(f"❌ Error injecting message to agent: {e}")

    async def start(self):
        """Initialize agent, publish tracks, and start listening for participants"""
        # Initialize the ElevenLabs Conversational AI Agent (for responses)
        await self.agent_manager.initialize()

        # Register handler on the agent WebSocket for tracking speaking state.
        # The ElevenLabs agent sends audio events when speaking and we detect
        # when it finishes to activate the listening window.
        self._agent_audio_active = False

        # Start a task that monitors agent audio events via the listen loop
        # The agent_manager's _listen_loop already handles audio events and
        # feeds them to agent_audio_track. We track speaking state by monitoring
        # whether audio is being received.
        self._speaking_monitor_task = asyncio.create_task(self._monitor_agent_speaking())

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

    async def _monitor_agent_speaking(self):
        """Monitor agent audio track to detect when agent finishes speaking"""
        last_received = 0
        was_speaking = False
        try:
            while True:
                await asyncio.sleep(0.5)
                current_received = self.agent_manager._audio_bytes_received
                is_receiving = current_received > last_received
                last_received = current_received

                if is_receiving and not was_speaking:
                    # Agent started speaking
                    was_speaking = True
                    self._agent_speaking = True
                elif not is_receiving and was_speaking:
                    # Agent stopped speaking
                    was_speaking = False
                    self._agent_speaking = False
                    self._activate_listening()
                    logger.info(f"👂 Agent finished speaking — active listening for {self.active_listening_window}s")
        except asyncio.CancelledError:
            pass

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
        """Subscribe to a participant's audio and start transcribing with ElevenLabs Scribe"""
        try:
            config = RTCConfiguration()
            config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
            pc = RTCPeerConnection(config)
            pc.addTransceiver("audio", direction="recvonly")
            pc.addTransceiver("video", direction="recvonly")

            resampler = av.AudioResampler(format="s16", layout="mono", rate=STT_SAMPLE_RATE)
            speaker_label = user_id or participant_id[:8]

            # Connect to ElevenLabs Scribe Realtime WebSocket for this participant
            ws_url = self._build_stt_ws_url()
            headers = {"xi-api-key": self.elevenlabs_api_key}
            el_ws = await websockets.connect(ws_url, additional_headers=headers)

            # Wait for session_started
            session_msg = await el_ws.recv()
            session_data = json.loads(session_msg)
            if session_data.get("message_type") == "session_started":
                logger.info(f"✅ STT connected for {speaker_label} (session: {session_data.get('session_id', 'N/A')})")
            else:
                logger.warning(f"Unexpected first message for {speaker_label}: {session_data}")

            sub = {
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

            @pc.on("track")
            def on_track(track):
                if track.kind == "audio":
                    asyncio.create_task(self._process_audio(track, pc, el_ws, resampler, speaker_label, sub))
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

    async def _process_audio(self, track, pc, el_ws, resampler, label, sub):
        """Stream audio from a participant to their ElevenLabs STT connection"""
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
                            "sample_rate": STT_SAMPLE_RATE,
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
        """Receive transcripts from ElevenLabs Scribe for a participant"""
        while not sub.get("should_stop", False):
            try:
                message = await el_ws.recv()
                data = json.loads(message)
                msg_type = data.get("message_type", "")

                if msg_type == "committed_transcript":
                    text = data.get("text", "")
                    if text:
                        print(f"[{speaker_label}] {text}")
                        self._on_transcript(participant_id, speaker_label, text)

                elif msg_type == "committed_transcript_with_timestamps":
                    text = data.get("text", "")
                    if text:
                        print(f"[{speaker_label}] {text}")
                        self._on_transcript(participant_id, speaker_label, text)

                elif msg_type == "partial_transcript":
                    pass  # Only care about final transcripts

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
                    logger.error(f"❌ ElevenLabs STT {msg_type} ({speaker_label}): {error_msg}")
                    if msg_type in ("auth_error", "quota_exceeded", "unaccepted_terms"):
                        break

                elif msg_type == "session_started":
                    pass

            except ConnectionClosed:
                logger.info(f"STT WebSocket closed for {speaker_label}")
                break
            except Exception as e:
                if not sub.get("should_stop", False):
                    logger.error(f"Transcript receive error ({speaker_label}): {e}")
                break

    async def _consume(self, track):
        """Consume video frames and store the latest for frame analysis"""
        try:
            while True:
                frame = await track.recv()
                self.agent_manager.frame = frame
        except Exception:
            pass

    async def _unsubscribe(self, pid):
        sub = self._subscriptions.pop(pid, None)
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
        logger.info(f"🧹 Unsubscribed from {sub.get('user_id', pid)}")

    async def shutdown(self):
        if hasattr(self, "_speaking_monitor_task"):
            self._speaking_monitor_task.cancel()
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
        description="IVS Real-Time Stage Group Agent — wake-word activated voice assistant for multi-participant stages (ElevenLabs)",
        epilog="""
Examples:
  %(prog)s --token eyJ... --wake-word "hey assistant"
  %(prog)s --token eyJ... --wake-word "hey assistant" --context-window 120 --active-listening-window 15
  %(prog)s --token eyJ... --wake-word "ok agent" --voice-id JBFqnCBsd6RMkjVDRZzb --llm-model gpt-4o
  %(prog)s --token eyJ... --wake-word "hey assistant" --disable-frame-analysis
  %(prog)s --token eyJ... --agent-id existing-agent-id

Environment variables:
  ELEVENLABS_API_KEY    ElevenLabs API key (alternative to --elevenlabs-api-key)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--token", required=True, help="IVS participant token (PUBLISH + SUBSCRIBE)")
    parser.add_argument("--elevenlabs-api-key", default=None, help="ElevenLabs API key (or ELEVENLABS_API_KEY env var)")
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
    parser.add_argument("--model-id", default="scribe_v2_realtime", help="ElevenLabs STT model (default: scribe_v2_realtime)")
    parser.add_argument("--language-code", default="en", help="Language code (default: en)")
    parser.add_argument("--agent-id", default=None, help="Existing ElevenLabs Conversational AI agent ID (optional — auto-creates if not set)")
    parser.add_argument("--voice-id", default="JBFqnCBsd6RMkjVDRZzb", help="ElevenLabs voice ID (default: JBFqnCBsd6RMkjVDRZzb = George)")
    parser.add_argument("--llm-model", default="gemini-2.0-flash", help="LLM model for agent reasoning (default: gemini-2.0-flash)")
    parser.add_argument("--prompt", default="You are a helpful assistant participating in a group meeting.", help="Base system prompt")
    parser.add_argument("--disable-frame-analysis", action="store_true", help="Disable vision/frame analysis")
    parser.add_argument("--bedrock-model-id", default="us.anthropic.claude-sonnet-4-6", help="Bedrock model for frame analysis")
    parser.add_argument("--bedrock-region", default="us-east-1", help="AWS region for Bedrock")
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
    if not caps.get("allow_publish") or not caps.get("allow_subscribe"):
        logger.error("❌ Token needs both PUBLISH and SUBSCRIBE capabilities")
        return

    logger.info("🎬 Starting IVS Stage Group Agent (ElevenLabs)")
    logger.info(f"🗣️  Wake word: '{args.wake_word}'")
    logger.info(f"😴 Sleep word: '{args.sleep_word}'")
    logger.info(f"📜 Context window: {args.context_window}s")
    logger.info(f"👂 Active listening window: {args.active_listening_window}s")
    logger.info(f"🎤 STT model: {args.model_id}")
    logger.info(f"🗣️  Voice: {args.voice_id}")
    logger.info(f"🧠 LLM: {args.llm_model}")
    if args.agent_id:
        logger.info(f"🤖 Using existing agent: {args.agent_id}")
    logger.info(f"🔍 Frame analysis: {'disabled' if args.disable_frame_analysis else 'enabled'}")

    agent = GroupAgent(
        token=args.token,
        elevenlabs_api_key=elevenlabs_api_key,
        wake_word=args.wake_word,
        sleep_word=args.sleep_word,
        context_window=args.context_window,
        active_listening_window=args.active_listening_window,
        model_id=args.model_id,
        language_code=args.language_code,
        voice_id=args.voice_id,
        llm_model=args.llm_model,
        agent_id=args.agent_id,
        prompt=args.prompt,
        enable_frame_analysis=not args.disable_frame_analysis,
        bedrock_model_id=args.bedrock_model_id,
        bedrock_region=args.bedrock_region,
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
