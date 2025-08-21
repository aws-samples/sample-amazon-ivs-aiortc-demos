import asyncio
import base64
import pytz
import tzlocal
import uuid
import logging
import json
from agent_video_track import AgentVideoTrack
from agent_audio_track import AgentAudioTrack
from agent_tools import AgentTools
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from stages_sei import SeiPublisher, set_global_sei_publisher

from rx.subject import Subject
from rx import operators as ops
from rx.scheduler.eventloop import AsyncIOScheduler
from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient, InvokeModelWithBidirectionalStreamOperationInput
from aws_sdk_bedrock_runtime.models import InvokeModelWithBidirectionalStreamInputChunk, BidirectionalInputPayloadPart
from aws_sdk_bedrock_runtime.config import Config, HTTPAuthSchemeResolver, SigV4AuthScheme
from smithy_aws_core.credentials_resolvers.environment import EnvironmentCredentialsResolver

logger = logging.getLogger(__name__)


class BedrockStreamManager:
    """Manages bidirectional streaming with AWS Bedrock Nova for speech-to-speech"""

    def __init__(
        self,
        agent_audio_track: AgentAudioTrack,
        agent_video_track: AgentVideoTrack,
        model_id="amazon.nova-sonic-v1:0",
        region="us-east-1",
        input_sample_rate=16000,
        weather_api_key=None,
        enable_frame_analysis=True,
        analysis_model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        analysis_region="us-east-1",
    ):
        self.model_id = model_id
        self.region = region
        self.agent_audio_track = agent_audio_track
        self.agent_video_track = agent_video_track
        self.input_sample_rate = input_sample_rate
        self.input_subject = Subject()
        self.audio_subject = Subject()
        self.response_task = None
        self.stream_response = None
        self.is_active = False
        self.bedrock_client = None
        self.scheduler = None

        # Frame analysis configuration
        self.enable_frame_analysis = enable_frame_analysis
        self.analysis_model_id = analysis_model_id
        self.analysis_region = analysis_region
        self.agent_tools = AgentTools(self.analysis_region, self.analysis_model_id)

        # frame analysis
        self.frame = None

        # Weather API configuration
        self.weather_api_key = weather_api_key
        self.weather_tool_available = self.weather_api_key is not None

        if not self.weather_tool_available:
            logger.warning("⚠️  `weather_api_key` not found. Weather tool will not be available.")
        else:
            logger.info("🌤️  Weather tool is available")

        # Frame analysis configuration logging
        if self.enable_frame_analysis:
            logger.info(f"🔍 Frame analysis is enabled (model: {self.analysis_model_id}, region: {self.analysis_region})")
        else:
            logger.info("🔍 Frame analysis is disabled")

        # Session information
        self.prompt_name = str(uuid.uuid4())
        self.content_name = str(uuid.uuid4())
        self.audio_content_name = str(uuid.uuid4())

        # SEI Publisher for metadata transmission
        self.sei_publisher = SeiPublisher(max_retry_attempts=3)
        set_global_sei_publisher(self.sei_publisher)
        logger.info("📡 SEI Publisher initialized for H.264 metadata transmission")

        # Single audio content session
        self.audio_session_started = False
        self.current_response_id = None  # Track current response to avoid duplicates

        # Tool processing
        self.tool_use_content = ""
        self.tool_use_id = ""
        self.tool_name = ""
        self.pending_tool_tasks = {}

        # SEI message tracking (removed publish_sequence for cleaner payload)

        # Initialize schemas and templates
        self._initialize_schemas_and_templates()

    def _initialize_schemas_and_templates(self):
        """Initialize JSON schemas and event templates"""
        self.date_time_schema = json.dumps(
            {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "Time timezone at which to return the date/time. Default to local where script is running.",
                        "enum": pytz.all_timezones,
                        "default": tzlocal.get_localzone().key,
                    }
                },
                "required": [],
            }
        )

        # Weather tool schema
        self.weather_schema = json.dumps(
            {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The location to get weather for. Can be a city name, postal code, or coordinates (e.g., 'New York', '10001', 'London, UK')",
                    },
                },
                "required": ["location"],
            }
        )

        # frame analysis tool schema
        self.frame_analysis_schema = json.dumps(
            {
                "type": "object",
                "properties": {},
                "required": [],
            }
        )

        # Build tools list dynamically based on availability
        tools_list = [
            {
                "toolSpec": {
                    "name": "getDateAndTimeTool",
                    "description": "Get information about the current date and time",
                    "inputSchema": {"json": self.date_time_schema},
                }
            },
        ]

        # Add frame analysis tool if enabled
        if self.enable_frame_analysis:
            tools_list.append(
                {
                    "toolSpec": {
                        "name": "analyzeFrameTool",
                        "description": "The purpose of this tool is to analyze a single image and return a description of what is contained in the image. This provides you - the assistant - the ability describe the user and their environment. This includes the room they are in, surrounding objets, people, physical characteristics, clothing, etc. If the user asks the agent a question related to what the agent can see, or something about the user's physical appearance or environment - for example (but not limited to): 'look at this' or 'what do you see?' or 'what do i look like?' or 'can you see me?' then use this tool to analyze a single frame from the live stream and return the results. If a single person is identified, refer to them as 'you' and say things like 'your' or you are, not 'he', 'she' or they. Refer to the single person just as you normally would in responding to them. If multiple people are identified, avoid any gendered pronouns and say 'they' or 'them' instead.",
                        "inputSchema": {"json": self.frame_analysis_schema},
                    }
                }
            )

        # Add weather tool if API key is available
        if self.weather_tool_available:
            tools_list.append(
                {
                    "toolSpec": {
                        "name": "getWeatherTool",
                        "description": "Get current weather information and 5-day forecast for a specified location",
                        "inputSchema": {"json": self.weather_schema},
                    }
                }
            )

        # Event templates
        self.START_SESSION_EVENT = """{
            "event": {
                "sessionStart": {
                    "inferenceConfiguration": {
                        "maxTokens": 512,
                        "topP": 0.9,
                        "temperature": 0.7
                    }
                }
            }
        }"""
        # fmt:off
        self.START_PROMPT_EVENT = (
            """{
            "event": {
                "promptStart": {
                    "promptName": "%s",
                    "textOutputConfiguration": {
                        "mediaType": "text/plain"
                    },
                    "audioOutputConfiguration": {
                        "mediaType": "audio/lpcm",
                        "sampleRateHertz": 24000,
                        "sampleSizeBits": 16,
                        "channelCount": 1,
                        "voiceId": "tiffany",
                        "encoding": "base64",
                        "audioType": "SPEECH"
                    },
                    "toolUseOutputConfiguration": {
                        "mediaType": "application/json"
                    },
                    "toolConfiguration": {
                        "tools": """ + json.dumps(tools_list) + """
                    }
                }
            }
        }"""
        )
        # fmt:on
        self.CONTENT_START_EVENT = f"""{{
            "event": {{
                "contentStart": {{
                    "promptName": "%s",
                    "contentName": "%s",
                    "type": "AUDIO",
                    "interactive": true,
                    "role": "USER",
                    "audioInputConfiguration": {{
                        "mediaType": "audio/lpcm",
                        "sampleRateHertz": {self.input_sample_rate},
                        "sampleSizeBits": 16,
                        "channelCount": 1,
                        "audioType": "SPEECH",
                        "encoding": "base64"
                    }}
                }}
            }}
        }}"""

        self.AUDIO_EVENT_TEMPLATE = """{
            "event": {
                "audioInput": {
                    "promptName": "%s",
                    "contentName": "%s",
                    "content": "%s"
                }
            }
        }"""

        self.TEXT_CONTENT_START_EVENT = """{
            "event": {
                "contentStart": {
                    "promptName": "%s",
                    "contentName": "%s",
                    "role": "%s",
                    "type": "TEXT",
                    "interactive": true,
                    "textInputConfiguration": {
                        "mediaType": "text/plain"
                    }
                }
            }
        }"""

        self.TEXT_INPUT_EVENT = """{
            "event": {
                "textInput": {
                    "promptName": "%s",
                    "contentName": "%s",
                    "content": "%s"
                }
            }
        }"""

        self.CONTENT_END_EVENT = """{
            "event": {
                "contentEnd": {
                    "promptName": "%s",
                    "contentName": "%s"
                }
            }
        }"""

    def _initialize_client(self):
        """Initialize the Bedrock client"""
        config = Config(
            endpoint_uri=f"https://bedrock-runtime.{self.region}.amazonaws.com",
            region=self.region,
            aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
            http_auth_scheme_resolver=HTTPAuthSchemeResolver(),
            http_auth_schemes={"aws.auth#sigv4": SigV4AuthScheme()},
        )
        self.bedrock_client = BedrockRuntimeClient(config=config)

    async def initialize_stream(self):
        """Initialize the bidirectional stream with Bedrock"""
        if not self.bedrock_client:
            self._initialize_client()

        self.scheduler = AsyncIOScheduler(asyncio.get_event_loop())

        try:
            self.stream_response = await self.bedrock_client.invoke_model_with_bidirectional_stream(
                InvokeModelWithBidirectionalStreamOperationInput(model_id=self.model_id)
            )
            self.is_active = True

            system_prompt = (
                "You are a friendly assistant named Tiffany that is participating in a live video call."
                "Keep your responses very brief and conversational, like a natural spoken dialog."
                "Do not use gendered pronouns like he or she - even when using tools."
                "If and when you analyze frames of a video, refer to a single person as 'you' and say things like 'your' or 'you are'. If multiple people are recognized, refer to them as 'they' or 'them'"
                "Respond in 1-2 sentences maximum."
            )

            # Create proper initialization sequence
            init_events = [
                self.START_SESSION_EVENT,
                self.START_PROMPT_EVENT % self.prompt_name,
                self.TEXT_CONTENT_START_EVENT % (self.prompt_name, self.content_name, "SYSTEM"),
                self.TEXT_INPUT_EVENT % (self.prompt_name, self.content_name, system_prompt),
                self.CONTENT_END_EVENT % (self.prompt_name, self.content_name),
            ]
            for event in init_events:
                await self.send_raw_event(event)

            # Start listening for responses
            self.response_task = asyncio.create_task(self._process_responses())

            # Set up audio processing
            self.audio_subject.pipe(ops.subscribe_on(self.scheduler)).subscribe(
                on_next=lambda audio_data: asyncio.create_task(self._handle_audio_input(audio_data)),
                on_error=lambda e: logger.error(f"Audio stream error: {e}"),
            )

            # Start SEI cleanup task
            logger.info("✅ Nova stream initialized successfully")
            return self

        except Exception as e:
            self.is_active = False
            logger.error(f"Failed to initialize Nova stream: {str(e)}")
            raise

    async def send_raw_event(self, event_json):
        """Send a raw event JSON to the Bedrock stream"""
        if not self.stream_response or not self.is_active:
            return
        event = InvokeModelWithBidirectionalStreamInputChunk(value=BidirectionalInputPayloadPart(bytes_=event_json.encode("utf-8")))

        try:
            await self.stream_response.input_stream.send(event)
        except Exception as e:
            logger.error(f"Error sending event to Nova: {str(e)}")

    async def _handle_audio_input(self, audio_data):
        """Process audio input before sending it to Nova"""
        try:
            audio_bytes = audio_data.get("audio_bytes")

            if not audio_bytes:
                return

            # Start single audio content session if not already started
            if not self.audio_session_started:
                await self.start_audio_content()
                self.audio_session_started = True
                logger.info("🎤 Started audio content session")

            # Base64 encode the audio data
            blob = base64.b64encode(audio_bytes)
            audio_event = self.AUDIO_EVENT_TEMPLATE % (self.prompt_name, self.audio_content_name, blob.decode("utf-8"))

            await self.send_raw_event(audio_event)

        except Exception as e:
            logger.error(f"Error processing audio for Nova: {e}")

    def add_audio_chunk(self, audio_bytes):
        """Add an audio chunk to be processed by Nova"""
        self.audio_subject.on_next({"audio_bytes": audio_bytes})

    async def start_audio_content(self):
        """Start audio content session"""
        content_start_event = self.CONTENT_START_EVENT % (self.prompt_name, self.audio_content_name)
        await self.send_raw_event(content_start_event)

    async def end_audio_content(self):
        """End audio content session"""
        content_end_event = self.CONTENT_END_EVENT % (self.prompt_name, self.audio_content_name)
        await self.send_raw_event(content_end_event)

    async def _process_responses(self):
        """Process incoming responses from Nova"""
        try:
            while self.is_active:
                try:
                    output = await self.stream_response.await_output()
                    result = await output[1].receive()

                    if result.value and result.value.bytes_:
                        try:
                            response_data = result.value.bytes_.decode("utf-8")
                            json_data = json.loads(response_data)

                            if "event" in json_data:
                                if "textOutput" in json_data["event"]:
                                    text_content = json_data["event"]["textOutput"]["content"]
                                    role = json_data["event"]["textOutput"]["role"]

                                    # Check for interruption event
                                    if self._is_interruption_event(text_content):
                                        logger.info("🛑 User interruption detected - stopping agent speech")
                                        await self._handle_interruption()
                                        continue

                                    # Simple thinking state management based on role
                                    if role == "USER":
                                        self.agent_video_track.set_thinking_state(True)
                                    elif role == "ASSISTANT":
                                        self.agent_video_track.set_thinking_state(False)

                                    # Improved deduplication - track by role and content
                                    dedup_key = f"{role}:{text_content}"
                                    if not hasattr(self, "_seen_texts"):
                                        self._seen_texts = set()

                                    if dedup_key not in self._seen_texts:
                                        logger.info(f"🤖 Nova ({role}): {text_content}")
                                        self._seen_texts.add(dedup_key)

                                        # Publish text content as SEI metadata
                                        try:
                                            import time

                                            publish_timestamp = time.time()
                                            sei_data = {
                                                "type": "nova_text_output",
                                                "role": role,
                                                "content": text_content,
                                                "timestamp": publish_timestamp,
                                            }

                                            await self.sei_publisher.publish_json(sei_data, repeat_count=3)

                                            logger.info(
                                                f"📡 Published SEI message: {role} - '{text_content[:30]}{'...' if len(text_content) > 30 else ''}' ({len(text_content)} chars)"
                                            )
                                            logger.debug(
                                                f"📡 Published {role} text to SEI: {text_content[:50]}{'...' if len(text_content) > 50 else ''}"
                                            )
                                        except Exception as sei_error:
                                            logger.error(f"❌ Failed to publish SEI text: {sei_error}")

                                        # Clear old entries to prevent memory growth (keep last 10)
                                        if len(self._seen_texts) > 10:
                                            self._seen_texts = set(list(self._seen_texts)[-5:])

                                elif "audioOutput" in json_data["event"]:
                                    # Turn off thinking state when we get audio output (always from assistant)
                                    self.agent_video_track.set_thinking_state(False)

                                    # Detailed logging to understand the audio stream structure
                                    audio_event = json_data["event"]["audioOutput"]
                                    response_id = audio_event.get("promptName", "")
                                    content_name = audio_event.get("contentName", "")

                                    # Log detailed audio event info
                                    # logger.info(f"🎵 Audio event - Response: {response_id}, Content: {content_name}")

                                    if self.current_response_id != response_id:
                                        if self.current_response_id is not None:
                                            logger.info(f"🔄 Response ID changed: {self.current_response_id} -> {response_id}")
                                        self.current_response_id = response_id

                                    audio_content = audio_event["content"]
                                    audio_bytes = base64.b64decode(audio_content)

                                    # logger.info(f"🎵 Audio chunk: {len(audio_bytes)} bytes")

                                    # Always send audio - let the audio track handle any issues
                                    await self.agent_audio_track.add_audio_data(audio_bytes)

                                elif "toolUse" in json_data["event"]:
                                    # Keep thinking state on during tool use
                                    self.tool_use_content = json_data["event"]["toolUse"]
                                    self.tool_name = json_data["event"]["toolUse"]["toolName"]
                                    self.tool_use_id = json_data["event"]["toolUse"]["toolUseId"]
                                    logger.info(f"🔧 Tool use detected: {self.tool_name}, ID: {self.tool_use_id}")

                                elif "contentEnd" in json_data["event"] and json_data["event"].get("contentEnd", {}).get("type") == "TOOL":
                                    logger.info("🔧 Processing tool use and sending result")
                                    # Start asynchronous tool processing - non-blocking
                                    prompt = getattr(self, "_last_text", None)
                                    self.handle_tool_request(self.tool_name, self.tool_use_content, self.tool_use_id, prompt)
                                    logger.info("🔧 Processing tool use asynchronously")

                        except json.JSONDecodeError:
                            logger.warning("Failed to decode Nova response JSON")

                except StopAsyncIteration:
                    break
                except Exception as e:
                    logger.error(f"Error receiving Nova response: {e}")
                    # Continue processing instead of breaking - don't let transient errors kill the session
                    continue

        except Exception as e:
            logger.error(f"Nova response processing error: {e}")

    def _is_interruption_event(self, text_content):
        """Check if the text content is an interruption event"""
        try:
            # Try to parse as JSON
            parsed = json.loads(text_content.strip())
            return isinstance(parsed, dict) and parsed.get("interrupted") is True
        except (json.JSONDecodeError, AttributeError):
            # Not JSON or doesn't match interruption pattern
            return False

    async def _handle_interruption(self):
        """Handle user interruption by stopping agent speech"""
        try:
            # Stop the agent audio track immediately
            await self.agent_audio_track.stop_current_audio()

            # Set video track to idle state (not thinking, not speaking)
            self.agent_video_track.set_thinking_state(False)

            # Reset deduplication state for new conversation
            self.current_response_id = None
            if hasattr(self, "_seen_texts"):
                self._seen_texts.clear()

            logger.info("🛑 Agent speech stopped due to user interruption")

        except Exception as e:
            logger.error(f"Error handling interruption: {e}")

    async def close(self):
        """Close the Nova stream properly"""
        if not self.is_active:
            return

        self.is_active = False

        # Cancel any pending tool tasks
        for task in self.pending_tool_tasks.values():
            task.cancel()

        if self.response_task and not self.response_task.done():
            self.response_task.cancel()

        # End audio content session if it was started
        if self.audio_session_started:
            await self.end_audio_content()

        if self.stream_response:
            await self.stream_response.input_stream.close()

        # Stop the audio track
        await self.agent_audio_track.stop()

        logger.info("✅ Nova stream closed")

    async def process_tool_async(self, tool_name, tool_content, current_frame=None, prompt=None):
        """Process a tool call asynchronously and return the result"""
        logger.info(f"🔧 Processing tool: {tool_name}")

        tool = tool_name.lower()
        if tool == "getdateandtimetool":
            # Get current date and time
            content = tool_content.get("content", {})
            content_data = json.loads(content)
            tz = content_data.get("timezone", "")
            return self.agent_tools.getdateandtime(tz)
        elif tool == "getweathertool":
            # Get weather information with forecast
            if not self.weather_tool_available:
                return {"error": "Weather tool is not available. WEATHER_API_KEY environment variable not set."}

            content = tool_content.get("content", {})
            content_data = json.loads(content)
            location = content_data.get("location", "")

            return self.agent_tools.getweather(location, self.weather_api_key)
        elif tool == "analyzeframetool":
            # Check if frame analysis is enabled
            if not self.enable_frame_analysis:
                logger.warning("Frame analysis tool called but frame analysis is disabled")
                return {"error": "Frame analysis is disabled"}

            # Use the captured frame or fall back to current frame
            frame_to_analyze = current_frame if current_frame is not None else self.frame

            if frame_to_analyze is None:
                logger.warning("No video frame available for analysis")
                return {"error": "No video frame available for analysis"}

            logger.info(f"🔍 Starting frame analysis with {'captured' if current_frame is not None else 'current'} frame")

            try:
                # Use the async version for non-blocking execution
                analysis = await self.agent_tools.analyzeframe(frame_to_analyze, prompt)

                if analysis is None:
                    logger.warning("Frame analysis returned None")
                    return {"error": "Frame analysis failed - no result returned"}

                logger.info("🔍 Frame analysis completed successfully")
                return analysis

            except Exception as e:
                logger.error(f"Frame analysis error: {e}")
                import traceback

                traceback.print_exc()
                return {"error": f"Frame analysis failed: {str(e)}"}
        else:
            return {"error": f"Unsupported tool: {tool_name}"}

    def handle_tool_request(self, tool_name, tool_content, tool_use_id, prompt=None):
        """Handle a tool request asynchronously"""
        # Create a unique content name for this tool response
        tool_content_name = str(uuid.uuid4())

        # Capture current frame for analysis tools to ensure consistency
        current_frame = None
        if tool_name.lower() == "analyzeframetool" and self.frame is not None:
            # Create a copy of the frame to avoid issues with frame updates during processing
            try:
                current_frame = self.frame.copy() if hasattr(self.frame, "copy") else self.frame
            except Exception as e:
                logger.warning(f"Could not copy frame, using reference: {e}")
                current_frame = self.frame

        # Create an asynchronous task for the tool execution
        task = asyncio.create_task(self._execute_tool_and_send_result(tool_name, tool_content, tool_use_id, tool_content_name, current_frame, prompt))

        # Store the task
        self.pending_tool_tasks[tool_content_name] = task

        # Add error handling
        task.add_done_callback(lambda t: self._handle_tool_task_completion(t, tool_content_name))

    def _handle_tool_task_completion(self, task, content_name):
        """Handle the completion of a tool task"""
        # Remove task from pending tasks
        if content_name in self.pending_tool_tasks:
            del self.pending_tool_tasks[content_name]

        # Handle any exceptions
        if task.done() and not task.cancelled():
            exception = task.exception()
            if exception:
                logger.error(f"Tool task failed: {str(exception)}")

    async def _execute_tool_and_send_result(self, tool_name, tool_content, tool_use_id, content_name, current_frame=None, prompt=None):
        """Execute a tool and send the result"""
        try:
            logger.info(f"🔧 Starting tool execution: {tool_name}")

            # Process the tool
            tool_result = await self.process_tool_async(tool_name, tool_content, current_frame, prompt)

            # Send the result sequence
            await self.send_tool_start_event(content_name, tool_use_id)
            await self.send_tool_result_event(content_name, tool_result)
            await self.send_tool_content_end_event(content_name)

            logger.info(f"🔧 Tool execution complete: {tool_name}")

        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {str(e)}")

            # Try to send an error response if possible
            try:
                error_result = {"error": f"Tool execution failed: {str(e)}"}
                await self.send_tool_start_event(content_name, tool_use_id)
                await self.send_tool_result_event(content_name, error_result)
                await self.send_tool_content_end_event(content_name)
            except Exception as send_error:
                logger.error(f"Failed to send error response: {str(send_error)}")

    async def send_tool_start_event(self, content_name, tool_use_id):
        """Send a tool content start event"""
        event_data = {
            "event": {
                "contentStart": {
                    "promptName": self.prompt_name,
                    "contentName": content_name,
                    "interactive": False,
                    "type": "TOOL",
                    "role": "TOOL",
                    "toolResultInputConfiguration": {"toolUseId": tool_use_id, "type": "TEXT", "textInputConfiguration": {"mediaType": "text/plain"}},
                }
            }
        }
        await self.send_raw_event(json.dumps(event_data))

    async def send_tool_result_event(self, content_name, tool_result):
        """Send a tool result event"""
        if isinstance(tool_result, dict):
            content_string = json.dumps(tool_result)
        else:
            content_string = str(tool_result)

        event_data = {"event": {"toolResult": {"promptName": self.prompt_name, "contentName": content_name, "content": content_string}}}
        await self.send_raw_event(json.dumps(event_data))

    async def send_tool_content_end_event(self, content_name):
        """Send a tool content end event"""
        event_data = {"event": {"contentEnd": {"promptName": self.prompt_name, "contentName": content_name}}}
        await self.send_raw_event(json.dumps(event_data))
