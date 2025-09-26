import asyncio
import json
import logging
import base64
import websockets
import time
import boto3
import av
from PIL import Image
import io
import sys
import os
from typing import Optional, Dict, Any

# Add path for SEI publisher
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from stages_sei import SeiPublisher, set_global_sei_publisher

logger = logging.getLogger(__name__)


class GptRealtimeManager:
    """
    Manages the gpt-realtime API WebSocket connection and handles audio streaming
    """

    def __init__(
        self,
        gpt_realtime_audio_track,
        gpt_realtime_video_track,
        api_key: str,
        model: str = "gpt-realtime",
        voice: str = "cedar",
        enable_frame_analysis: bool = True,
        analysis_model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
        analysis_region: str = "us-east-1",
        vad_mode: str = "server_vad",
        vad_threshold: float = 0.5,
        vad_prefix_padding_ms: int = 300,
        vad_silence_duration_ms: int = 500,
        vad_eagerness: str = "medium",
    ):
        self.gpt_realtime_audio_track = gpt_realtime_audio_track
        self.gpt_realtime_video_track = gpt_realtime_video_track
        self.api_key = api_key
        self.model = model
        self.voice = voice

        # Frame analysis configuration
        self.enable_frame_analysis = enable_frame_analysis
        self.analysis_model_id = analysis_model_id
        self.analysis_region = analysis_region

        # VAD configuration
        self.vad_mode = vad_mode
        self.vad_threshold = vad_threshold
        self.vad_prefix_padding_ms = vad_prefix_padding_ms
        self.vad_silence_duration_ms = vad_silence_duration_ms
        self.vad_eagerness = vad_eagerness

        # WebSocket connection
        self.websocket = None
        self.websocket_url = "wss://api.openai.com/v1/realtime?model=" + model

        # Connection state
        self.connected = False
        self.session_id = None

        # Audio processing
        self.input_sample_rate = 24000  # OpenAI expects 24kHz
        self.output_sample_rate = 24000

        # Task management
        self.receive_task = None
        self.send_task = None

        # Audio input queue
        self.audio_input_queue = asyncio.Queue()

        # Conversation state
        self.conversation_started = False

        # Frame analysis setup
        self.frame = None  # Current video frame for analysis
        if self.enable_frame_analysis:
            try:
                self.bedrock_client = boto3.client("bedrock-runtime", region_name=self.analysis_region)
                logger.info(f"🔍 Frame analysis enabled - model: {self.analysis_model_id}, region: {self.analysis_region}")
            except Exception as e:
                logger.error(f"❌ Failed to initialize Bedrock client: {e}")
                self.enable_frame_analysis = False
        else:
            self.bedrock_client = None
            logger.info("🔍 Frame analysis disabled")

        # SEI Publisher for metadata transmission
        self.sei_publisher = SeiPublisher(max_retry_attempts=3)
        set_global_sei_publisher(self.sei_publisher)
        logger.info("📡 SEI Publisher initialized for H.264 metadata transmission")

        logger.info(f"🤖 GptRealtimeManager initialized - model: {model}, voice: {voice}")

    async def initialize(self):
        """Initialize the gpt-realtime API connection"""
        try:
            logger.info("🔗 Connecting to gpt-realtime API...")

            # Connect to WebSocket with authentication
            headers = {"Authorization": f"Bearer {self.api_key}", "OpenAI-Beta": "realtime=v1"}

            self.websocket = await websockets.connect(self.websocket_url, additional_headers=headers, ping_interval=20, ping_timeout=10)

            self.connected = True
            logger.info("✅ Connected to gpt-realtime API")

            # Start receive and send tasks
            self.receive_task = asyncio.create_task(self._receive_messages())
            self.send_task = asyncio.create_task(self._send_audio_chunks())

            # Send initial session configuration
            await self._configure_session()

        except Exception as e:
            logger.error(f"❌ Failed to initialize gpt-realtime API: {e}")
            raise

    async def _configure_session(self):
        """Configure the gpt-realtime session with desired settings"""
        try:
            session_config = {
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": (
                        "You are a helpful AI assistant participating in a live video conversation. "
                        "Keep your responses conversational and natural. "
                        "Respond to what the user says in a friendly and engaging way."
                    ),
                    "voice": self.voice,
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "input_audio_transcription": {"model": "whisper-1"},
                    "turn_detection": self._get_vad_config(),
                    "tools": self._get_function_definitions(),
                    "tool_choice": "auto",
                    "temperature": 0.8,
                    "max_response_output_tokens": 4096,
                },
            }

            await self.websocket.send(json.dumps(session_config))
            logger.info("📝 Sent session configuration to gpt-realtime")

        except Exception as e:
            logger.error(f"❌ Failed to configure session: {e}")

    def _get_function_definitions(self):
        """Get function definitions for gpt-realtime function calling"""
        functions = []

        if self.enable_frame_analysis:
            functions.append(
                {
                    "type": "function",
                    "name": "analyze_frame",
                    "description": "Analyze the current video frame to describe what you can see. Use this when the user asks about their appearance, environment, or anything visual. This provides you with the ability to see and describe the user and their surroundings.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string", "description": "Optional specific prompt or question about what to look for in the frame"}
                        },
                        "required": [],
                    },
                }
            )

        return functions

    def _get_vad_config(self):
        """Get VAD configuration based on mode"""
        if self.vad_mode == "server_vad":
            return {
                "type": "server_vad",
                "threshold": self.vad_threshold,
                "prefix_padding_ms": self.vad_prefix_padding_ms,
                "silence_duration_ms": self.vad_silence_duration_ms,
                "create_response": True,
                "interrupt_response": True,
            }
        elif self.vad_mode == "semantic_vad":
            return {
                "type": "semantic_vad",
                "eagerness": self.vad_eagerness,
                "create_response": True,
                "interrupt_response": True,
            }
        else:
            # Default to server_vad
            return {
                "type": "server_vad",
                "threshold": self.vad_threshold,
                "prefix_padding_ms": self.vad_prefix_padding_ms,
                "silence_duration_ms": self.vad_silence_duration_ms,
                "create_response": True,
                "interrupt_response": True,
            }

    async def _receive_messages(self):
        """Receive and process messages from gpt-realtime API"""
        try:
            while self.connected and self.websocket:
                try:
                    message = await self.websocket.recv()
                    data = json.loads(message)
                    await self._handle_message(data)

                except websockets.exceptions.ConnectionClosed:
                    logger.info("🔌 gpt-realtime WebSocket connection closed")
                    break
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Failed to decode JSON message: {e}")
                except Exception as e:
                    logger.error(f"❌ Error receiving message: {e}")

        except Exception as e:
            logger.error(f"❌ Error in receive loop: {e}")
        finally:
            self.connected = False

    async def _handle_message(self, data: Dict[str, Any]):
        """Handle incoming messages from gpt-realtime"""
        message_type = data.get("type", "unknown")

        if message_type == "session.created":
            self.session_id = data.get("session", {}).get("id")
            logger.info(f"✅ gpt-realtime session created: {self.session_id}")

        elif message_type == "session.updated":
            logger.info("✅ gpt-realtime session updated")

        elif message_type == "conversation.item.created":
            item = data.get("item", {})
            item_type = item.get("type", "unknown")
            logger.info(f"💬 Conversation item created: {item_type}")

            # Check if this is a user message with audio content
            if item_type == "message" and item.get("role") == "user":
                content = item.get("content", [])
                for content_part in content:
                    if content_part.get("type") == "input_audio":
                        logger.info("🎤 User audio message created (no transcript yet)")
                    elif content_part.get("type") == "input_text":
                        text = content_part.get("text", "")
                        logger.info(f"🎤 User text message: {text}")
                        await self._publish_transcript_sei("user", text)

        elif message_type == "response.created":
            response = data.get("response", {})
            logger.info(f"🤖 Response created: {response.get('id', 'unknown')}")

        elif message_type == "response.output_item.added":
            item = data.get("item", {})
            logger.debug(f"📝 Output item added: {item.get('type', 'unknown')}")

        elif message_type == "response.content_part.added":
            part = data.get("part", {})
            logger.debug(f"📄 Content part added: {part.get('type', 'unknown')}")

        elif message_type == "response.audio.delta":
            # Receive audio data from gpt-realtime
            delta = data.get("delta")
            if delta:
                try:
                    # Decode base64 audio data
                    audio_bytes = base64.b64decode(delta)
                    # Add to audio track for playback
                    await self.gpt_realtime_audio_track.add_audio_data(audio_bytes)
                    logger.debug(f"🔊 Received audio delta: {len(audio_bytes)} bytes")
                except Exception as e:
                    logger.error(f"❌ Error processing audio delta: {e}")

        elif message_type == "response.audio.done":
            logger.info("✅ Audio response completed")

        elif message_type == "response.audio_transcript.delta":
            # Handle streaming audio transcript from gpt-realtime
            delta = data.get("delta")
            if delta:
                logger.debug(f"🤖 gpt-realtime transcript delta: {delta}")

        elif message_type == "response.audio_transcript.done":
            # Handle completed audio transcript from gpt-realtime
            transcript = data.get("transcript", "")
            if transcript:
                logger.info(f"🤖 gpt-realtime said: {transcript}")
                # Publish gpt-realtime response as SEI metadata
                await self._publish_transcript_sei("assistant", transcript)

        elif message_type == "conversation.item.input_audio_transcription.completed":
            # Handle completed input audio transcription (what user said)
            transcript = data.get("transcript", "")
            if transcript:
                logger.info(f"🎤 User said: {transcript}")
                # Publish user input as SEI metadata
                await self._publish_transcript_sei("user", transcript)

        elif message_type == "conversation.item.input_audio_transcription.failed":
            # Handle failed input audio transcription
            error = data.get("error", {})
            logger.warning(f"⚠️ User audio transcription failed: {error.get('message', 'Unknown error')}")

        elif message_type == "conversation.item.truncated":
            # Handle truncated conversation items
            logger.info("✂️ Conversation item truncated")

        elif message_type == "conversation.item.deleted":
            # Handle deleted conversation items
            logger.info("🗑️ Conversation item deleted")

        elif message_type == "conversation.item.done":
            # Handle completed conversation items - might contain transcription
            item = data.get("item", {})
            item_type = item.get("type", "unknown")
            logger.info(f"✅ Conversation item done: {item_type}")

            # Check if this is a completed user message with transcription
            if item_type == "message" and item.get("role") == "user":
                content = item.get("content", [])
                for content_part in content:
                    if content_part.get("type") == "input_audio":
                        transcript = content_part.get("transcript", "")
                        if transcript:
                            logger.info(f"🎤 User said (from item.done): {transcript}")
                            await self._publish_transcript_sei("user", transcript)

        elif message_type == "input_audio_buffer.speech_started":
            logger.info("🗣️ Speech started detected")
            # Stop any current audio output to allow for interruption
            await self.gpt_realtime_audio_track.stop_current_audio()

        elif message_type == "input_audio_buffer.speech_stopped":
            logger.info("🤐 Speech stopped detected")

        elif message_type == "input_audio_buffer.committed":
            logger.debug("🎤 Audio input committed")

        elif message_type == "response.done":
            response = data.get("response", {})
            logger.info(f"✅ Response completed: {response.get('id', 'unknown')}")

            # Check if this response contains function calls
            output = response.get("output", [])
            for item in output:
                if item.get("type") == "function_call":
                    function_name = item.get("name")
                    arguments = item.get("arguments", "{}")
                    call_id = item.get("call_id")

                    logger.info(f"🔧 Function call in response: {function_name}")

                    # Execute the function call
                    result = await self._execute_function_call(function_name, arguments)

                    # Send function call result back to gpt-realtime
                    await self._send_function_result(call_id, result)

        elif message_type == "error":
            error = data.get("error", {})
            logger.error(f"❌ gpt-realtime API error: {error.get('message', 'Unknown error')}")

        elif message_type == "response.function_call_arguments.delta":
            # Handle function call arguments streaming
            logger.debug("🔧 Function call arguments delta received")

        elif message_type == "response.function_call_arguments.done":
            # Handle completed function call arguments
            logger.debug("🔧 Function call arguments completed")

        elif message_type == "rate_limits.updated":
            logger.debug("📊 Rate limits updated")

        else:
            logger.debug(f"🔍 Unhandled message type: {message_type}")
            # Log transcription events for debugging semantic VAD issues
            if "transcription" in message_type:
                logger.debug(f"🔍 Transcription event: {message_type}")
                transcript = data.get("transcript", "")
                if transcript and "input_audio" in message_type:
                    logger.info(f"🎤 User said (from {message_type}): {transcript}")
                    await self._publish_transcript_sei("user", transcript)

    async def _send_audio_chunks(self):
        """Send audio chunks to gpt-realtime API"""
        try:
            while self.connected:
                try:
                    # Get audio chunk from queue with timeout
                    audio_chunk = await asyncio.wait_for(self.audio_input_queue.get(), timeout=1.0)

                    if audio_chunk is None:  # Shutdown signal
                        break

                    # Encode audio as base64
                    audio_base64 = base64.b64encode(audio_chunk).decode("utf-8")

                    # Send audio append message
                    message = {"type": "input_audio_buffer.append", "audio": audio_base64}

                    await self.websocket.send(json.dumps(message))
                    logger.debug(f"🎤 Sent audio chunk: {len(audio_chunk)} bytes")

                except asyncio.TimeoutError:
                    # No audio data available, continue
                    continue
                except Exception as e:
                    logger.error(f"❌ Error sending audio chunk: {e}")

        except Exception as e:
            logger.error(f"❌ Error in send audio loop: {e}")

    async def add_audio_chunk(self, audio_bytes: bytes):
        """Add audio chunk to the input queue for processing"""
        try:
            if self.connected and len(audio_bytes) > 0:
                await self.audio_input_queue.put(audio_bytes)

                # Start conversation if not already started
                if not self.conversation_started:
                    self.conversation_started = True
                    logger.info("🎙️ Conversation started - audio input detected")

        except Exception as e:
            logger.error(f"❌ Error adding audio chunk: {e}")

    async def commit_audio_buffer(self):
        """Commit the current audio buffer to trigger response generation"""
        try:
            if self.connected and self.websocket:
                message = {"type": "input_audio_buffer.commit"}
                await self.websocket.send(json.dumps(message))
                logger.debug("✅ Audio buffer committed")
        except Exception as e:
            logger.error(f"❌ Error committing audio buffer: {e}")

    async def create_response(self):
        """Manually trigger response generation"""
        try:
            if self.connected and self.websocket:
                message = {"type": "response.create", "response": {"modalities": ["audio"], "instructions": "Please respond to the user's input."}}
                await self.websocket.send(json.dumps(message))
                logger.info("🤖 Manual response creation triggered")
        except Exception as e:
            logger.error(f"❌ Error creating response: {e}")

    async def _execute_function_call(self, function_name: str, arguments: str) -> Dict[str, Any]:
        """Execute a function call and return the result"""
        try:
            args = json.loads(arguments) if arguments else {}

            if function_name == "analyze_frame":
                return await self._analyze_current_frame(args.get("prompt"))
            else:
                logger.warning(f"Unknown function call: {function_name}")
                return {"error": f"Unknown function: {function_name}"}

        except Exception as e:
            logger.error(f"❌ Error executing function {function_name}: {e}")
            return {"error": str(e)}

    async def _send_function_result(self, call_id: str, result: Dict[str, Any]):
        """Send function call result back to gpt-realtime"""
        try:
            if self.connected and self.websocket:
                # Send the function result
                message = {
                    "type": "conversation.item.create",
                    "item": {"type": "function_call_output", "call_id": call_id, "output": json.dumps(result)},
                }
                await self.websocket.send(json.dumps(message))
                logger.debug(f"📤 Sent function result for call {call_id}")

                # Trigger a new response generation to incorporate the function result
                response_message = {"type": "response.create"}
                await self.websocket.send(json.dumps(response_message))
                logger.debug("🤖 Triggered response generation after function result")

        except Exception as e:
            logger.error(f"❌ Error sending function result: {e}")

    async def _analyze_current_frame(self, prompt: Optional[str] = None) -> Dict[str, Any]:
        """Analyze the current video frame using Bedrock"""
        try:
            if not self.enable_frame_analysis:
                return {"error": "Frame analysis is disabled"}

            if self.frame is None:
                return {"error": "No video frame available for analysis"}

            logger.info("🔍 Starting frame analysis...")

            # Convert frame to base64 in a thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            frame_base64 = await asyncio.wait_for(loop.run_in_executor(None, self._frame_to_base64, self.frame), timeout=10)

            if not frame_base64:
                return {"error": "Failed to convert frame to base64"}

            # Prepare the message for Claude
            bedrock_prompt = "Analyze this video frame from a live stream. Describe what you see in detail, including people, objects, activities, text, and any notable features. Refer to subjects in the image as 'you' and say things like 'your' or 'you are' instead of talking about the subject in the third-person. Pretend like you know them personally and are responding directly to them conversationally."
            if prompt:
                bedrock_prompt += f" The user has specifically asked: '{prompt}'"

            message = {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_base64}},
                    {
                        "type": "text",
                        "text": bedrock_prompt,
                    },
                ],
            }

            # Call Bedrock in a thread pool
            def bedrock_call():
                return self.bedrock_client.invoke_model(
                    modelId=self.analysis_model_id,
                    body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 200, "messages": [message], "temperature": 0.4}),
                )

            response = await asyncio.wait_for(loop.run_in_executor(None, bedrock_call), timeout=20)

            # Parse response
            response_body = json.loads(response["body"].read())
            analysis_result = response_body["content"][0]["text"]

            logger.info("✅ Frame analysis completed")
            logger.info(f"📝 Analysis: {analysis_result}")

            return {"analysis": analysis_result}

        except asyncio.TimeoutError:
            logger.error("Frame analysis timed out")
            return {"error": "Frame analysis timed out"}
        except Exception as e:
            logger.error(f"❌ Error analyzing frame: {e}")
            import traceback

            traceback.print_exc()
            return {"error": str(e)}

    def _frame_to_base64(self, frame) -> Optional[str]:
        """Convert video frame to base64 JPEG (runs in thread pool)"""
        try:
            # Convert frame to PIL Image
            img = frame.to_image()

            # Convert to JPEG bytes
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format="JPEG", quality=85)
            img_byte_arr = img_byte_arr.getvalue()

            # Encode to base64
            return base64.b64encode(img_byte_arr).decode("utf-8")

        except Exception as e:
            logger.error(f"❌ Error converting frame to base64: {e}")
            return None

    def set_current_frame(self, frame):
        """Set the current video frame for analysis"""
        self.frame = frame

    async def _publish_transcript_sei(self, role: str, transcript: str):
        """Publish transcript as SEI metadata"""
        try:
            sei_data = {
                "type": "openai_text_output",
                "role": role,
                "content": transcript,
                "timestamp": time.time(),
            }

            await self.sei_publisher.publish_json(sei_data, repeat_count=3)
            logger.info(f"📡 Published SEI message: {role} - '{transcript[:30]}{'...' if len(transcript) > 30 else ''}' ({len(transcript)} chars)")

        except Exception as e:
            logger.error(f"❌ Error publishing transcript SEI: {e}")

    async def close(self):
        """Close the gpt-realtime API connection"""
        try:
            logger.info("🔌 Closing gpt-realtime API connection...")

            self.connected = False

            # Signal shutdown to send task
            if self.audio_input_queue:
                await self.audio_input_queue.put(None)

            # Cancel tasks
            if self.receive_task:
                self.receive_task.cancel()
                try:
                    await self.receive_task
                except asyncio.CancelledError:
                    pass

            if self.send_task:
                self.send_task.cancel()
                try:
                    await self.send_task
                except asyncio.CancelledError:
                    pass

            # Close WebSocket
            if self.websocket:
                await self.websocket.close()
                self.websocket = None

            logger.info("✅ gpt-realtime API connection closed")

        except Exception as e:
            logger.error(f"❌ Error closing gpt-realtime connection: {e}")
