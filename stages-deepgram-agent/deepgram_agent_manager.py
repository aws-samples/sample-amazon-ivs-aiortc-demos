#!/usr/bin/env python3
"""
Deepgram Voice Agent Manager

Manages a bidirectional WebSocket connection to Deepgram's Voice Agent API.
Receives user audio, sends it to Deepgram, and streams back agent audio responses.
"""

import asyncio
import base64
import io
import json
import logging
import time
from typing import Optional, Callable, Any

import boto3
from PIL import Image
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.agent.v1.types.agent_v1settings import AgentV1Settings
from deepgram.agent.v1.types.agent_v1send_function_call_response import AgentV1SendFunctionCallResponse

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
        enable_frame_analysis: bool = True,
        bedrock_model_id: str = "us.anthropic.claude-sonnet-4-6",
        bedrock_region: str = "us-east-1",
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

        # Frame analysis (vision via Bedrock Claude)
        self.enable_frame_analysis = enable_frame_analysis
        self.bedrock_model_id = bedrock_model_id
        self.bedrock_region = bedrock_region
        self.frame = None  # Current video frame, set externally
        self._bedrock_client = None
        if enable_frame_analysis:
            try:
                self._bedrock_client = boto3.client("bedrock-runtime", region_name=bedrock_region)
                logger.info(f"🔍 Frame analysis enabled (model: {bedrock_model_id}, region: {bedrock_region})")
            except Exception as e:
                logger.warning(f"⚠️  Could not initialize Bedrock client: {e}")
                self.enable_frame_analysis = False

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

        # Build the think config
        think_config = {
            "provider": {
                "type": self.think_provider,
                "model": self.think_model,
            },
            "prompt": self._build_prompt(),
        }

        # Add frame analysis function if enabled
        if self.enable_frame_analysis:
            think_config["functions"] = [
                {
                    "name": "analyze_frame",
                    "description": (
                        "Analyze the current video frame from the user's camera. "
                        "Use this when the user asks you to look at something, describe what you see, "
                        "comment on their appearance or environment, or any question that requires "
                        "visual information. Examples: 'what do you see?', 'look at this', "
                        "'what am I wearing?', 'describe my room', 'can you see me?'"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "Optional specific question about what the user wants analyzed in the frame",
                            }
                        },
                    },
                }
            ]

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
                "think": think_config,
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
                    # The SDK may return functions as dicts or objects — handle both
                    if isinstance(func, dict):
                        name = func.get("name", "unknown")
                        func_id = func.get("id", "")
                        arguments = func.get("arguments", "{}")
                    else:
                        name = getattr(func, "name", "unknown")
                        func_id = getattr(func, "id", "")
                        arguments = getattr(func, "arguments", "{}")
                    logger.info(f"🔧 Function call requested: {name} (id={func_id})")

                    if name == "analyze_frame":
                        asyncio.ensure_future(self._handle_analyze_frame(func_id, arguments))
                    else:
                        logger.warning(f"⚠️  Unknown function: {name} — raw: {func}")

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

    def _build_prompt(self) -> str:
        """Build the system prompt, adding vision instructions if frame analysis is enabled"""
        base = self.prompt
        if self.enable_frame_analysis:
            base += (
                "\n\nYou have access to a tool called 'analyze_frame' that lets you see "
                "the user's video feed. If the user asks you to look at something, describe "
                "what you see, comment on their appearance or surroundings, or asks any "
                "question requiring visual information, use this tool. You cannot see by "
                "default — you MUST call the tool to get visual information. When you receive "
                "the analysis result, respond conversationally as if you can see them directly. "
                "Refer to the user as 'you' (not 'the person' or 'they')."
            )
        return base

    async def _handle_analyze_frame(self, func_id: str, arguments_str: str) -> None:
        """Handle the analyze_frame function call by sending the frame to Bedrock Claude"""
        try:
            from deepgram.agent.v1.types.agent_v1inject_agent_message import AgentV1InjectAgentMessage

            # Inject a filler message so the user knows we're working on it
            try:
                import random

                fillers = [
                    "Let me take a look...",
                    "One moment, let me see...",
                    "Sure, looking now...",
                    "Okay, let me check that out...",
                    "Hang on, taking a look...",
                    "Let me see what I can see...",
                ]
                filler = AgentV1InjectAgentMessage(
                    type="InjectAgentMessage",
                    message=random.choice(fillers),
                )
                await self._connection.send_inject_agent_message(filler)
            except Exception:
                pass  # Non-critical — don't let filler failure block analysis

            args = json.loads(arguments_str) if arguments_str else {}
            user_prompt = args.get("prompt", "")

            if self.frame is None:
                result = "No video frame is currently available. The user may not have their camera on."
                logger.warning("🔍 analyze_frame called but no frame available")
            elif not self._bedrock_client:
                result = "Frame analysis is not available — Bedrock client not initialized."
                logger.warning("🔍 analyze_frame called but Bedrock client not available")
            else:
                logger.info("🔍 Analyzing video frame with Bedrock Claude...")
                if self.agent_video_track:
                    self.agent_video_track.set_thinking_state(True)

                result = await self._call_bedrock_vision(self.frame, user_prompt)

                if self.agent_video_track:
                    self.agent_video_track.set_thinking_state(False)

            # Send the function call response back to Deepgram
            response = AgentV1SendFunctionCallResponse(
                type="FunctionCallResponse",
                id=func_id,
                name="analyze_frame",
                content=result if isinstance(result, str) else json.dumps(result),
            )
            await self._connection.send_function_call_response(response)
            logger.info(f"📸 Frame analysis sent back to agent ({len(result)} chars)")

        except Exception as e:
            logger.error(f"❌ Error handling analyze_frame: {e}")
            import traceback

            traceback.print_exc()
            # Send error response so the agent doesn't hang
            try:
                response = AgentV1SendFunctionCallResponse(
                    type="FunctionCallResponse",
                    id=func_id,
                    name="analyze_frame",
                    content=f"Error analyzing frame: {str(e)}",
                )
                await self._connection.send_function_call_response(response)
            except Exception:
                pass

    async def _call_bedrock_vision(self, frame, user_prompt: str = "") -> str:
        """Call Bedrock Claude to analyze a video frame"""
        loop = asyncio.get_event_loop()

        # Convert frame to base64 JPEG
        def _frame_to_base64():
            img = frame.to_image()
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            buf.seek(0)
            return base64.b64encode(buf.getvalue()).decode("utf-8")

        frame_b64 = await asyncio.wait_for(loop.run_in_executor(None, _frame_to_base64), timeout=10)

        if not frame_b64:
            return "Failed to convert video frame to image."

        bedrock_prompt = (
            "Analyze this video frame from a live stream. Describe what you see in detail. "
            "Refer to the subject as 'you' — respond as if speaking directly to them. "
            "Be conversational and specific about people, objects, activities, and environment."
        )
        if user_prompt:
            bedrock_prompt += f" The user specifically asked: '{user_prompt}'"

        message = {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64}},
                {"type": "text", "text": bedrock_prompt},
            ],
        }

        def _bedrock_call():
            return self._bedrock_client.invoke_model(
                modelId=self.bedrock_model_id,
                body=json.dumps(
                    {
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 150,
                        "messages": [message],
                        "temperature": 0.4,
                    }
                ),
            )

        response = await asyncio.wait_for(loop.run_in_executor(None, _bedrock_call), timeout=20)

        body = json.loads(response["body"].read())
        result = body["content"][0]["text"]
        logger.info(f"📸 Frame analysis: {result[:100]}...")
        return result

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
