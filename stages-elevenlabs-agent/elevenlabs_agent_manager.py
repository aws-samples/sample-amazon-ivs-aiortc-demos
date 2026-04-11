#!/usr/bin/env python3
"""
ElevenLabs Conversational AI Agent Manager

Manages a bidirectional WebSocket connection to ElevenLabs' Conversational AI API.
Receives user audio, sends it to ElevenLabs, and streams back agent audio responses.
The ElevenLabs agent handles STT → LLM → TTS in a single WebSocket connection.
"""

import asyncio
import base64
import io
import json
import logging
import time
from typing import Optional

import boto3
import requests
import websockets
from PIL import Image

# SEI publishing for embedding transcripts in H.264 video stream
try:
    from stages_sei import SeiPublisher, set_global_sei_publisher

    SEI_AVAILABLE = True
except ImportError:
    SEI_AVAILABLE = False

logger = logging.getLogger(__name__)

# Audio configuration — ElevenLabs Conversational AI uses 16kHz input and output
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
CHANNELS = 1

ELEVENLABS_CONVAI_WS_URL = "wss://api.elevenlabs.io/v1/convai/conversation"
ELEVENLABS_AGENTS_API_URL = "https://api.elevenlabs.io/v1/convai/agents/create"


class ElevenLabsAgentManager:
    """Manages the ElevenLabs Conversational AI WebSocket connection"""

    def __init__(
        self,
        api_key: str,
        agent_id: Optional[str] = None,
        agent_audio_track=None,
        agent_video_track=None,
        voice_id: str = "JBFqnCBsd6RMkjVDRZzb",
        llm_model: str = "gemini-2.0-flash",
        prompt: str = "You are a friendly, helpful voice assistant. Keep responses concise and conversational.",
        greeting: str = "Hello! How can I help you today?",
        language: str = "en",
        multilingual: bool = False,
        participant_id: str = "",
        enable_frame_analysis: bool = True,
        bedrock_model_id: str = "us.anthropic.claude-sonnet-4-6",
        bedrock_region: str = "us-east-1",
    ):
        self.api_key = api_key
        self.agent_id = agent_id
        self._auto_created_agent_id = None  # Track if we created the agent
        self.agent_audio_track = agent_audio_track
        self.agent_video_track = agent_video_track
        self.voice_id = voice_id
        self.llm_model = llm_model
        self.prompt = prompt
        self.greeting = greeting
        self.language = language
        self.multilingual = multilingual
        self.participant_id = participant_id

        # Frame analysis (vision via Bedrock Claude)
        self.enable_frame_analysis = enable_frame_analysis
        self.bedrock_model_id = bedrock_model_id
        self.bedrock_region = bedrock_region
        self.frame = None  # Current video frame, set externally
        self._bedrock_client = None
        if enable_frame_analysis:
            try:
                self._bedrock_client = boto3.client("bedrock-runtime", region_name=bedrock_region)
                logger.info(f"🔍 Frame analysis enabled " f"(model: {bedrock_model_id}, region: {bedrock_region})")
            except Exception as e:
                logger.warning(f"⚠️  Could not initialize Bedrock client: {e}")
                self.enable_frame_analysis = False

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._should_stop = False
        self._conversation_id: Optional[str] = None

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

    def _build_prompt(self) -> str:
        """Build the system prompt, adding vision instructions if enabled"""
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

    def _create_agent(self) -> str:
        """Create an ElevenLabs Conversational AI agent via REST API"""
        logger.info("🤖 Creating ElevenLabs Conversational AI agent...")

        prompt_config = {
            "prompt": self._build_prompt(),
            "llm": self.llm_model,
            "temperature": 0.7,
        }

        # Register analyze_frame as a client tool if frame analysis is enabled
        if self.enable_frame_analysis:
            prompt_config["tools"] = [
                {
                    "type": "client",
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
                                "description": "Optional specific question about what to analyze in the frame",
                            }
                        },
                    },
                }
            ]

        # Enable language detection system tool for multilingual support
        if self.multilingual:
            prompt_config["built_in_tools"] = {
                "language_detection": {},
            }

        payload = {
            "name": "IVS Stage Agent",
            "conversation_config": {
                "agent": {
                    "first_message": self.greeting,
                    "language": self.language,
                    "prompt": prompt_config,
                },
                "tts": {
                    "voice_id": self.voice_id,
                    "agent_output_audio_format": "pcm_24000",
                },
                "asr": {
                    "user_input_audio_format": "pcm_16000",
                },
            },
        }

        resp = requests.post(
            ELEVENLABS_AGENTS_API_URL,
            headers={
                "xi-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error(f"❌ Agent creation failed ({resp.status_code}): {resp.text[:500]}")
            resp.raise_for_status()
        agent_id = resp.json()["agent_id"]
        logger.info(f"✅ Created ElevenLabs agent: {agent_id}")
        return agent_id

    def _delete_agent(self, agent_id: str) -> None:
        """Delete an auto-created ElevenLabs agent"""
        try:
            resp = requests.delete(
                f"https://api.elevenlabs.io/v1/convai/agents/{agent_id}",
                headers={"xi-api-key": self.api_key},
                timeout=15,
            )
            if resp.status_code in (200, 204):
                logger.info(f"🗑️  Deleted auto-created agent: {agent_id}")
            else:
                logger.warning(f"⚠️  Failed to delete agent {agent_id}: " f"{resp.status_code} {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"⚠️  Error deleting agent {agent_id}: {e}")

    async def initialize(self) -> None:
        """Create agent if needed and connect to the ElevenLabs WebSocket"""
        logger.info("🤖 Initializing ElevenLabs Conversational AI Agent...")
        self._start_time = time.time()

        # Create agent on-the-fly if no agent_id provided
        if not self.agent_id:
            loop = asyncio.get_event_loop()
            self.agent_id = await loop.run_in_executor(None, self._create_agent)
            self._auto_created_agent_id = self.agent_id

        # Connect to the Conversational AI WebSocket
        ws_url = f"{ELEVENLABS_CONVAI_WS_URL}?agent_id={self.agent_id}"
        extra_headers = {"xi-api-key": self.api_key}

        logger.info(f"🔌 Connecting to ElevenLabs WebSocket (agent: {self.agent_id})...")
        self._ws = await websockets.connect(
            ws_url,
            additional_headers=extra_headers,
            ping_interval=None,  # We handle ping/pong via the protocol
        )
        logger.info("✅ ElevenLabs WebSocket connected")

        # Start listening for responses in the background
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("✅ ElevenLabs Conversational AI Agent initialized")

    async def shutdown(self) -> None:
        """Gracefully close the agent connection"""
        self._should_stop = True

        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

        # Delete auto-created agent
        if self._auto_created_agent_id:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._delete_agent, self._auto_created_agent_id)

        if self._start_time:
            elapsed = time.time() - self._start_time
            logger.info(
                f"ElevenLabs Agent stopped. Duration: {elapsed:.1f}s, "
                f"Audio sent: {self._audio_bytes_sent:,} bytes, "
                f"Audio received: {self._audio_bytes_received:,} bytes"
            )

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Send raw PCM audio to the ElevenLabs agent as base64"""
        if self._ws and not self._should_stop and audio_bytes:
            try:
                b64_audio = base64.b64encode(audio_bytes).decode("utf-8")
                msg = json.dumps({"user_audio_chunk": b64_audio})
                await self._ws.send(msg)
                self._audio_bytes_sent += len(audio_bytes)
            except websockets.ConnectionClosed:
                if not self._should_stop:
                    logger.warning("🔌 ElevenLabs WebSocket closed — stopping audio send")
                    self._should_stop = True
            except Exception as e:
                if not self._should_stop:
                    logger.error(f"Error sending audio to ElevenLabs: {e}")
                    self._should_stop = True

    # ── WebSocket listener ──────────────────────────────────────

    async def _listen_loop(self) -> None:
        """Listen for messages from the ElevenLabs WebSocket"""
        try:
            async for raw_message in self._ws:
                if self._should_stop:
                    break
                try:
                    msg = json.loads(raw_message)
                    await self._handle_message(msg)
                except json.JSONDecodeError:
                    logger.warning(f"Non-JSON message from ElevenLabs: {raw_message[:100]}")
                except Exception as e:
                    logger.error(f"Error processing ElevenLabs message: {e}")
                    import traceback

                    traceback.print_exc()
        except websockets.ConnectionClosed as e:
            if not self._should_stop:
                logger.warning(f"ElevenLabs WebSocket closed: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if not self._should_stop:
                logger.error(f"ElevenLabs listen loop error: {e}")

    async def _handle_message(self, msg: dict) -> None:
        """Route an incoming JSON message by its type field"""
        msg_type = msg.get("type", "")

        if msg_type == "conversation_initiation_metadata":
            self._conversation_id = msg.get("conversation_initiation_metadata_event", {}).get("conversation_id")
            logger.info(f"🤝 ElevenLabs conversation started " f"(id={self._conversation_id})")

        elif msg_type == "audio":
            await self._handle_audio_event(msg)

        elif msg_type == "agent_response":
            text = msg.get("agent_response_event", {}).get("agent_response", "")
            if text:
                print(f"[🤖 AGENT] {text}")
                if self.sei_publisher:
                    asyncio.ensure_future(self._publish_transcript_sei("agent", text))

        elif msg_type == "user_transcript":
            text = msg.get("user_transcription_event", {}).get("user_transcript", "")
            if text:
                print(f"[🗣️  USER] {text}")
                if self.sei_publisher:
                    asyncio.ensure_future(self._publish_transcript_sei("user", text))

        elif msg_type == "interruption":
            logger.debug("🎤 User interrupted agent")
            await self._clear_agent_audio()
            if self.agent_video_track:
                self.agent_video_track.set_thinking_state(False)
                self.agent_video_track.update_throb_level(0.0)

        elif msg_type == "agent_response_correction":
            corrected = msg.get("agent_response_correction_event", {}).get("corrected_agent_response", "")
            if corrected:
                logger.debug(f"📝 Agent response corrected: {corrected[:80]}...")

        elif msg_type == "ping":
            event_id = msg.get("ping_event", {}).get("event_id")
            if event_id is not None:
                pong = json.dumps({"type": "pong", "event_id": event_id})
                await self._ws.send(pong)

        elif msg_type == "client_tool_call":
            await self._handle_tool_call(msg)

        else:
            logger.debug(f"Unhandled ElevenLabs message type: {msg_type}")

    async def _handle_audio_event(self, msg: dict) -> None:
        """Decode base64 audio from an 'audio' event and feed it to the track"""
        audio_event = msg.get("audio_event", {})
        b64_audio = audio_event.get("audio_base_64", "")
        if not b64_audio:
            return

        audio_bytes = base64.b64decode(b64_audio)
        self._audio_bytes_received += len(audio_bytes)

        if self.agent_audio_track:
            await self.agent_audio_track.add_audio_data(audio_bytes)

        if self.agent_video_track:
            self.agent_video_track.set_thinking_state(False)

    # ── Tool calls (frame analysis) ─────────────────────────────

    async def _handle_tool_call(self, msg: dict) -> None:
        """Handle client_tool_call events for frame analysis"""
        tool_call = msg.get("client_tool_call", {})
        tool_name = tool_call.get("tool_name", "")
        tool_call_id = tool_call.get("tool_call_id", "")
        logger.info(f"🔧 Tool call: {tool_name} (id={tool_call_id})")

        if tool_name == "analyze_frame":
            await self._handle_analyze_frame(tool_call_id, tool_call)
        else:
            logger.warning(f"⚠️  Unknown tool call: {tool_name}")
            # Send empty response so the agent doesn't hang
            await self._send_tool_response(tool_call_id, f"Unknown tool: {tool_name}")

    async def _handle_analyze_frame(self, tool_call_id: str, tool_call: dict) -> None:
        """Analyze the current video frame via Bedrock Claude"""
        expects_response = tool_call.get("expects_response", True)
        try:
            params = tool_call.get("parameters", {})
            user_prompt = params.get("prompt", "")

            if self.frame is None:
                result = "No video frame is currently available. " "The user may not have their camera on."
                logger.warning("🔍 analyze_frame called but no frame available")
            elif not self._bedrock_client:
                result = "Frame analysis is not available — " "Bedrock client not initialized."
                logger.warning("🔍 analyze_frame called but Bedrock client not available")
            else:
                logger.info("🔍 Analyzing video frame with Bedrock Claude...")
                if self.agent_video_track:
                    self.agent_video_track.set_thinking_state(True)

                result = await self._call_bedrock_vision(self.frame, user_prompt)

                if self.agent_video_track:
                    self.agent_video_track.set_thinking_state(False)

            # Send result back — either as tool response or contextual update
            if expects_response:
                await self._send_tool_response(tool_call_id, result)
            else:
                # Tool doesn't expect a response — inject as contextual update
                # so the agent incorporates the vision result in its next reply
                await self._send_tool_response(tool_call_id, result)
                # Also send as contextual update as a fallback
                context_msg = {
                    "type": "contextual_update",
                    "text": f"[Vision analysis result]: {result}",
                }
                await self._ws.send(json.dumps(context_msg))
                logger.info(f"📸 Sent vision result as contextual update ({len(result)} chars)")

            logger.info(f"📸 Frame analysis sent back to agent ({len(result)} chars)")

        except Exception as e:
            logger.error(f"❌ Error handling analyze_frame: {e}")
            import traceback

            traceback.print_exc()
            if expects_response:
                await self._send_tool_response(tool_call_id, f"Error analyzing frame: {str(e)}")

    async def _send_tool_response(self, tool_call_id: str, result: str) -> None:
        """Send a client tool result back to the ElevenLabs agent"""
        if self._ws and not self._should_stop:
            msg = {
                "type": "client_tool_result",
                "tool_call_id": tool_call_id,
                "result": result,
                "is_error": False,
            }
            logger.info(f"🔧 Sending tool response (tool_call_id={tool_call_id})")
            await self._ws.send(json.dumps(msg))

    async def _call_bedrock_vision(self, frame, user_prompt: str = "") -> str:
        """Call Bedrock Claude to analyze a video frame"""
        loop = asyncio.get_event_loop()

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
            "Analyze this video frame from a live stream. Describe what you "
            "see in detail. Refer to the subject as 'you' — respond as if "
            "speaking directly to them. Be conversational and specific about "
            "people, objects, activities, and environment."
        )
        if user_prompt:
            bedrock_prompt += f" The user specifically asked: '{user_prompt}'"

        message = {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": frame_b64,
                    },
                },
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

    # ── Helpers ──────────────────────────────────────────────────

    async def _publish_transcript_sei(self, role: str, transcript: str) -> None:
        """Publish transcript as SEI metadata in the H.264 video stream"""
        try:
            sei_data = {
                "type": "elevenlabs_agent_text",
                "role": role,
                "content": transcript,
                "participant_id": self.participant_id,
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
