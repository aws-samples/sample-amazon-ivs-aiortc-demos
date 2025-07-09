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
        self.agent_tools = AgentTools()

        # Weather API configuration
        self.weather_api_key = weather_api_key
        self.weather_tool_available = self.weather_api_key is not None

        if not self.weather_tool_available:
            logger.warning("⚠️  `weather_api_key` not found. Weather tool will not be available.")
        else:
            logger.info("🌤️  Weather tool is available")

        # Session information
        self.prompt_name = str(uuid.uuid4())
        self.content_name = str(uuid.uuid4())
        self.audio_content_name = str(uuid.uuid4())

        # Single audio content session
        self.audio_session_started = False

        # Tool processing
        self.tool_use_content = ""
        self.tool_use_id = ""
        self.tool_name = ""
        self.pending_tool_tasks = {}

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

        # Build tools list dynamically based on availability
        tools_list = [
            {
                "toolSpec": {
                    "name": "getDateAndTimeTool",
                    "description": "Get information about the current date and time",
                    "inputSchema": {"json": self.date_time_schema},
                }
            }
        ]

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
                        "maxTokens": 1024,
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

                                    # Simple thinking state management based on role
                                    if role == "USER":
                                        self.agent_video_track.set_thinking_state(True)
                                    elif role == "ASSISTANT":
                                        self.agent_video_track.set_thinking_state(False)

                                    # Only log if it's not a duplicate (simple dedup)
                                    if not hasattr(self, "_last_text") or self._last_text != text_content:
                                        logger.info(f"🤖 Nova ({role}): {text_content}")
                                        self._last_text = text_content

                                elif "audioOutput" in json_data["event"]:
                                    # Turn off thinking state when we get audio output (always from assistant)
                                    self.agent_video_track.set_thinking_state(False)

                                    audio_content = json_data["event"]["audioOutput"]["content"]
                                    audio_bytes = base64.b64decode(audio_content)

                                    # Send audio to Nova audio track for publishing (it will update video track throb)
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
                                    self.handle_tool_request(self.tool_name, self.tool_use_content, self.tool_use_id)
                                    logger.info("🔧 Processing tool use asynchronously")

                        except json.JSONDecodeError:
                            logger.warning("Failed to decode Nova response JSON")

                except StopAsyncIteration:
                    break
                except Exception as e:
                    logger.error(f"Error receiving Nova response: {e}")
                    break

        except Exception as e:
            logger.error(f"Nova response processing error: {e}")

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

    async def process_tool_async(self, tool_name, tool_content):
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
        else:
            return {"error": f"Unsupported tool: {tool_name}"}

    def handle_tool_request(self, tool_name, tool_content, tool_use_id):
        """Handle a tool request asynchronously"""
        # Create a unique content name for this tool response
        tool_content_name = str(uuid.uuid4())

        # Create an asynchronous task for the tool execution
        task = asyncio.create_task(self._execute_tool_and_send_result(tool_name, tool_content, tool_use_id, tool_content_name))

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

    async def _execute_tool_and_send_result(self, tool_name, tool_content, tool_use_id, content_name):
        """Execute a tool and send the result"""
        try:
            logger.info(f"🔧 Starting tool execution: {tool_name}")

            # Process the tool
            tool_result = await self.process_tool_async(tool_name, tool_content)

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

    async def start_audio_content(self):
        """Start audio content session"""
        content_start_event = self.CONTENT_START_EVENT % (self.prompt_name, self.audio_content_name)
        await self.send_raw_event(content_start_event)

    async def end_audio_content(self):
        """End audio content session"""
        content_end_event = self.CONTENT_END_EVENT % (self.prompt_name, self.audio_content_name)
        await self.send_raw_event(content_end_event)
