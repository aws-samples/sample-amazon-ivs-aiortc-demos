#!/usr/bin/env python3
"""
Deepgram Voice Agent Manager

Manages a bidirectional WebSocket connection to Deepgram's Voice Agent API.
Receives user audio, sends it to Deepgram, and streams back agent audio responses.
"""

import asyncio
import json
import logging
import time
from typing import Optional, Callable, Any

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.agent.v1.types.agent_v1settings import AgentV1Settings

# SEI publishing for embedding transcripts in H.264 video stream
try:
    from stages_sei import SeiPublisher, set_global_sei_publisher

    SEI_AVAILABLE = True
except ImportError:
    SEI_AVAILABLE = False

logger = logging.getLogger(__name__)

# Audio configuration — Deepgram Agent uses 16kHz input, 24kHz output to match
# the AgentAudioTrack which is optimized for 24kHz (20ms chunks at 480 samples)
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
CHANNELS = 1


class DeepgramAgentManager:
    """Manages the Deepgram Voice Agent WebSocket connection"""

    def __init__(
        self,
        api_key: str,
        agent_audio_track=None,
        agent_video_track=None,
        voice: str = "aura-2-asteria-en",
        think_model: str = "gpt-4o-mini",
        think_provider: str = "open_ai",
        prompt: str = "You are a friendly, helpful voice assistant.",
        greeting: str = "Hello! How can I help you today?",
        language: str = "en",
        output_sample_rate: int = OUTPUT_SAMPLE_RATE,
    ):
        self.api_key = api_key
        self.agent_audio_track = agent_audio_track
        self.agent_video_track = agent_video_track
        self.voice = voice
        self.think_model = think_model
        self.think_provider = think_provider
        self.prompt = prompt
        self.greeting = greeting
        self.language = language
        self.output_sample_rate = output_sample_rate

        self._client: Optional[AsyncDeepgramClient] = None
        self._connection = None
        self._ctx = None
        self._listen_task: Optional[asyncio.Task] = None
        self._should_stop = False

        # SEI publisher for embedding transcripts in video stream
        self.sei_publisher = None
        if SEI_AVAILABLE:
            self.sei_publisher = SeiPublisher(max_retry_attempts=3)
            set_global_sei_publisher(self.sei_publisher)
            logger.info("📡 SEI Publisher initialized for H.264 metadata transmission")

        # Stats
        self._audio_bytes_sent = 0
        self._audio_bytes_received = 0
        self._start_time: Optional[float] = None

    async def initialize(self) -> None:
        """Connect to Deepgram Agent API and send settings"""
        logger.info("🤖 Initializing Deepgram Voice Agent...")
        self._start_time = time.time()
        self._client = AsyncDeepgramClient(api_key=self.api_key)

        self._ctx = self._client.agent.v1.connect()
        self._connection = await self._ctx.__aenter__()

        # Register event handlers
        self._connection.on(EventType.OPEN, self._on_open)
        self._connection.on(EventType.MESSAGE, self._on_message)
        self._connection.on(EventType.ERROR, self._on_error)
        self._connection.on(EventType.CLOSE, self._on_close)

        # Start listening for responses in the background
        self._listen_task = asyncio.create_task(self._connection.start_listening())

        # Small delay to let the WebSocket connect
        await asyncio.sleep(0.5)

        # Send settings
        settings = AgentV1Settings(
            type="Settings",
            audio={
                "input": {
                    "encoding": "linear16",
                    "sample_rate": INPUT_SAMPLE_RATE,
                },
                "output": {
                    "encoding": "linear16",
                    "sample_rate": self.output_sample_rate,
                    "container": "none",
                },
            },
            agent={
                "language": self.language,
                "listen": {
                    "provider": {
                        "type": "deepgram",
                        "model": "nova-3",
                    }
                },
                "think": {
                    "provider": {
                        "type": self.think_provider,
                        "model": self.think_model,
                    },
                    "prompt": self.prompt,
                },
                "speak": {
                    "provider": {
                        "type": "deepgram",
                        "model": self.voice,
                    }
                },
                "greeting": self.greeting,
            },
        )

        logger.info(f"📤 Sending agent settings (voice={self.voice}, think={self.think_model})")
        await self._connection.send_settings(settings)
        logger.info("✅ Deepgram Voice Agent initialized")

    async def shutdown(self) -> None:
        """Gracefully close the agent connection"""
        self._should_stop = True

        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        if self._ctx:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:
                pass

        if self._start_time:
            elapsed = time.time() - self._start_time
            logger.info(
                f"Deepgram Agent stopped. Duration: {elapsed:.1f}s, "
                f"Audio sent: {self._audio_bytes_sent:,} bytes, "
                f"Audio received: {self._audio_bytes_received:,} bytes"
            )

    def add_audio_chunk(self, audio_bytes: bytes) -> None:
        """Send raw PCM audio to the Deepgram Agent (fire-and-forget, for non-async callers)"""
        if self._connection and not self._should_stop and audio_bytes:
            try:
                asyncio.ensure_future(self._connection.send_media(audio_bytes))
                self._audio_bytes_sent += len(audio_bytes)
            except Exception as e:
                if not self._should_stop:
                    logger.error(f"Error sending audio to Deepgram Agent: {e}")

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Send raw PCM audio to the Deepgram Agent (awaitable)"""
        if self._connection and not self._should_stop and audio_bytes:
            try:
                await self._connection.send_media(audio_bytes)
                self._audio_bytes_sent += len(audio_bytes)
            except Exception as e:
                if not self._should_stop:
                    logger.error(f"Error sending audio to Deepgram Agent: {e}")

    # ── Event handlers ──────────────────────────────────────────

    def _on_open(self, *args, **kwargs) -> None:
        logger.info("✅ Deepgram Agent WebSocket connection opened")

    def _on_message(self, message, *args, **kwargs) -> None:
        """Handle all messages from the Deepgram Agent"""
        try:
            # Binary audio data comes as raw bytes
            if isinstance(message, bytes):
                self._audio_bytes_received += len(message)
                if self.agent_audio_track:
                    asyncio.ensure_future(self.agent_audio_track.add_audio_data(message))
                return

            msg_type = getattr(message, "type", "unknown")

            if msg_type == "Welcome":
                request_id = getattr(message, "request_id", "N/A")
                logger.info(f"🤝 Agent welcome received (request_id={request_id})")

            elif msg_type == "SettingsApplied":
                logger.info("⚙️  Agent settings applied successfully")

            elif msg_type == "ConversationText":
                role = getattr(message, "role", "unknown")
                content = getattr(message, "content", "")
                prefix = "🗣️  USER" if role == "user" else "🤖 AGENT"
                print(f"[{prefix}] {content}")

                # Publish transcript as SEI metadata in the video stream
                if self.sei_publisher:
                    asyncio.ensure_future(self._publish_transcript_sei(role, content))

            elif msg_type == "UserStartedSpeaking":
                logger.debug("🎤 User started speaking")
                # Interrupt: clear any buffered agent audio
                if self.agent_audio_track:
                    asyncio.ensure_future(self._clear_agent_audio())
                if self.agent_video_track:
                    self.agent_video_track.set_thinking_state(False)
                    self.agent_video_track.update_throb_level(0.0)

            elif msg_type == "AgentThinking":
                content = getattr(message, "content", "")
                logger.debug(f"🤔 Agent thinking: {content[:80]}...")
                if self.agent_video_track:
                    self.agent_video_track.set_thinking_state(True)

            elif msg_type == "AgentStartedSpeaking":
                total_latency = getattr(message, "total_latency", 0)
                tts_latency = getattr(message, "tts_latency", 0)
                ttt_latency = getattr(message, "ttt_latency", 0)
                logger.info(f"🔊 Agent speaking (latency: {total_latency:.2f}s, " f"tts: {tts_latency:.2f}s, llm: {ttt_latency:.2f}s)")
                if self.agent_video_track:
                    self.agent_video_track.set_thinking_state(False)

            elif msg_type == "AgentAudioDone":
                logger.debug("🔇 Agent finished speaking")
                if self.agent_video_track:
                    self.agent_video_track.update_throb_level(0.0)

            elif msg_type == "FunctionCallRequest":
                functions = getattr(message, "functions", [])
                for func in functions:
                    name = getattr(func, "name", "unknown")
                    logger.info(f"🔧 Function call requested: {name}")

            elif msg_type == "Error":
                code = getattr(message, "code", "UNKNOWN")
                desc = getattr(message, "description", "No description")
                logger.error(f"❌ Agent error [{code}]: {desc}")

            elif msg_type == "Warning":
                code = getattr(message, "code", "UNKNOWN")
                desc = getattr(message, "description", "No description")
                logger.warning(f"⚠️  Agent warning [{code}]: {desc}")

            else:
                logger.debug(f"Unhandled agent message type: {msg_type}")

        except Exception as e:
            logger.error(f"Error processing agent message: {e}")
            import traceback

            traceback.print_exc()

    async def _publish_transcript_sei(self, role: str, transcript: str) -> None:
        """Publish transcript as SEI metadata embedded in the H.264 video stream"""
        try:
            sei_data = {
                "type": "deepgram_agent_text",
                "role": role,
                "content": transcript,
                "timestamp": time.time(),
            }
            await self.sei_publisher.publish_json(sei_data, repeat_count=3)
            logger.info(f"📡 Published SEI: {role} - " f"'{transcript[:30]}{'...' if len(transcript) > 30 else ''}'")
        except Exception as e:
            logger.error(f"❌ Error publishing transcript SEI: {e}")

    async def _clear_agent_audio(self) -> None:
        """Clear the agent audio buffer on interruption"""
        if self.agent_audio_track:
            async with self.agent_audio_track.buffer_lock:
                self.agent_audio_track.audio_buffer.clear()
                self.agent_audio_track.batch_buffer.clear()

    def _on_error(self, error, *args, **kwargs) -> None:
        logger.error(f"❌ Deepgram Agent WebSocket error: {error}")

    def _on_close(self, *args, **kwargs) -> None:
        logger.info("Deepgram Agent WebSocket connection closed")
