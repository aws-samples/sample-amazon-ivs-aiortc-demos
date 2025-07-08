#!/usr/bin/env python3

import asyncio
import json
import logging
import argparse
import base64
import requests
import time
import numpy as np
import uuid
import warnings
import io
import os
from typing import Dict, Any, List, Optional
from aiortc import (
    RTCBundlePolicy,
    RTCConfiguration,
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
    MediaStreamTrack,
    AudioStreamTrack,
)
from av import VideoFrame, AudioFrame
from fractions import Fraction
import av
import pytz
import datetime
import tzlocal


# Waveform visualization imports
import matplotlib

matplotlib.use("Agg")  # Use non-interactive backend
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from scipy import signal

# Nova speech-to-speech imports
from rx.subject import Subject
from rx import operators as ops
from rx.scheduler.eventloop import AsyncIOScheduler
from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient, InvokeModelWithBidirectionalStreamOperationInput
from aws_sdk_bedrock_runtime.models import InvokeModelWithBidirectionalStreamInputChunk, BidirectionalInputPayloadPart
from aws_sdk_bedrock_runtime.config import Config, HTTPAuthSchemeResolver, SigV4AuthScheme
from smithy_aws_core.credentials_resolvers.environment import EnvironmentCredentialsResolver

# Suppress warnings
warnings.filterwarnings("ignore")

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ivs-stage-nova-s2s")
logger.setLevel(logging.DEBUG)
aiortc_logger = logging.getLogger("aiortc")
aiortc_logger.setLevel(logging.ERROR)

# Suppress noisy STUN transaction timeout errors
aioice_logger = logging.getLogger("aioice")
aioice_logger.setLevel(logging.CRITICAL)
stun_logger = logging.getLogger("aioice.stun")
stun_logger.setLevel(logging.CRITICAL)

# Audio configuration for Nova
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
CHANNELS = 1
CHUNK_SIZE = 32


class BlankVideoTrack(VideoStreamTrack):
    """
    A video track that generates blank/black frames at a specified frame rate
    """

    def __init__(self, width=1280, height=720, fps=30):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_duration = 1.0 / fps
        self.start_time = time.time()
        self.frame_count = 0

    async def recv(self):
        """Generate and return a black video frame"""
        # Calculate the presentation timestamp (PTS) based on frame count
        pts = int(self.frame_count * (1 / self.fps) * 90000)  # 90kHz clock

        # Create a black frame using numpy
        # Create RGB black frame first, then let av handle the conversion
        frame_array = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        # Create VideoFrame from numpy array
        frame = VideoFrame.from_ndarray(frame_array, format="rgb24")
        frame.pts = pts
        frame.time_base = Fraction(1, 90000)  # Use Fraction for proper time_base

        # Increment frame count for next frame
        self.frame_count += 1

        # Sleep to maintain frame rate
        await asyncio.sleep(self.frame_duration)

        return frame


class WaveformVideoTrack(VideoStreamTrack):
    """
    A video track that generates waveform visualizations from audio data
    """

    def __init__(self, width=1280, height=720, fps=20):  # Slightly reduced FPS
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_duration = 1.0 / fps
        self.frame_count = 0

        # Audio buffer for waveform visualization
        self.audio_buffer = np.zeros(4096)  # Smaller buffer
        self.buffer_lock = asyncio.Lock()

        # Create custom colormap (orange to blue gradient like the reference image)
        colors = ["#FF6B35", "#F7931E", "#FFD23F", "#06FFA5", "#118AB2", "#073B4C"]
        n_bins = 256
        self.cmap = LinearSegmentedColormap.from_list("waveform", colors, N=n_bins)

        # Setup matplotlib figure
        plt.style.use("dark_background")
        self.fig, self.ax = plt.subplots(figsize=(self.width / 100, self.height / 100), dpi=100)
        self.fig.patch.set_facecolor("black")
        self.ax.set_facecolor("black")

        # Remove axes and margins
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.ax.set_xlim(0, len(self.audio_buffer))
        self.ax.set_ylim(-1.1, 1.1)
        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

        logger.info(f"🌊 WaveformVideoTrack initialized: {width}x{height} @ {fps}fps")

    async def add_audio_data(self, audio_bytes: bytes):
        """Add audio data to the waveform buffer"""
        try:
            # Convert bytes to numpy array
            audio_array = np.frombuffer(audio_bytes, dtype=np.int16)

            # Normalize to [-1, 1] range
            if len(audio_array) > 0:
                audio_normalized = audio_array.astype(np.float32) / 32768.0

                async with self.buffer_lock:
                    # Shift buffer and add new data
                    shift_amount = min(len(audio_normalized), len(self.audio_buffer))
                    self.audio_buffer[:-shift_amount] = self.audio_buffer[shift_amount:]
                    self.audio_buffer[-shift_amount:] = audio_normalized[-shift_amount:]

        except Exception as e:
            logger.error(f"Error adding audio data to waveform: {e}")

    def _generate_waveform_frame(self):
        """Generate a waveform visualization frame"""
        try:
            # Clear the plot
            self.ax.clear()
            self.ax.set_facecolor("black")
            self.ax.set_xlim(0, len(self.audio_buffer))
            self.ax.set_ylim(-1.1, 1.1)
            self.ax.set_xticks([])
            self.ax.set_yticks([])

            # Create x-axis for the waveform
            x = np.arange(len(self.audio_buffer))

            # Simplified waveform for better performance
            # Main waveform with gradient fill
            self.ax.fill_between(x, self.audio_buffer, 0, color="cyan", alpha=0.4, interpolate=True)
            self.ax.plot(x, self.audio_buffer, color="white", linewidth=2, alpha=0.9)

            # Add frequency bars only if there's significant audio
            if np.max(np.abs(self.audio_buffer)) > 0.1:
                # Create frequency spectrum
                freqs = np.fft.fft(self.audio_buffer)
                freqs_mag = np.abs(freqs[: len(freqs) // 2])

                # Normalize and create bars
                if len(freqs_mag) > 0:
                    freqs_mag = freqs_mag / np.max(freqs_mag) if np.max(freqs_mag) > 0 else freqs_mag

                    # Create frequency bars at the bottom
                    bar_width = len(self.audio_buffer) / len(freqs_mag)
                    for i, mag in enumerate(freqs_mag[::8]):  # Subsample for performance
                        x_pos = i * bar_width * 8
                        height = mag * 0.4
                        color_pos = i / (len(freqs_mag[::8]) - 1) if len(freqs_mag[::8]) > 1 else 0
                        color = self.cmap(color_pos)

                        self.ax.bar(x_pos, -height, width=bar_width * 6, bottom=-1.1, color=color, alpha=0.7)

            # Convert plot to image
            buf = io.BytesIO()
            self.fig.savefig(buf, format="png", facecolor="black", bbox_inches="tight", pad_inches=0, dpi=100)
            buf.seek(0)

            # Read image data
            img_data = buf.read()
            buf.close()

            return img_data

        except Exception as e:
            logger.error(f"Error generating waveform frame: {e}")
            # Return None on error
            return None

    async def recv(self):
        """Generate and return a waveform video frame"""
        try:
            # Generate waveform visualization
            async with self.buffer_lock:
                img_data = self._generate_waveform_frame()

            # Convert image data to VideoFrame
            if img_data and len(img_data) > 100:  # Valid image data
                try:
                    from PIL import Image

                    img = Image.open(io.BytesIO(img_data))
                    img_array = np.array(img.convert("RGB"))
                except Exception as e:
                    logger.error(f"Error converting image: {e}")
                    # Fallback to black frame
                    img_array = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            else:
                # Black frame
                img_array = np.zeros((self.height, self.width, 3), dtype=np.uint8)

            # Create VideoFrame
            frame = VideoFrame.from_ndarray(img_array, format="rgb24")

            # Set timing information
            pts = int(self.frame_count * (1 / self.fps) * 90000)  # 90kHz clock
            frame.pts = pts
            frame.time_base = Fraction(1, 90000)

            self.frame_count += 1

            # Sleep to maintain frame rate
            await asyncio.sleep(self.frame_duration)

            return frame

        except Exception as e:
            logger.error(f"Error in WaveformVideoTrack.recv: {e}")
            # Return black frame on error
            frame_array = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            frame = VideoFrame.from_ndarray(frame_array, format="rgb24")
            frame.pts = int(self.frame_count * (1 / self.fps) * 90000)
            frame.time_base = Fraction(1, 90000)
            self.frame_count += 1
            await asyncio.sleep(self.frame_duration)
            return frame


class ThrobCircleVideoTrack(VideoStreamTrack):
    """
    A video track that generates a simple throbbing circle when Nova is speaking
    and a pulsing animation when Nova is thinking/processing
    """

    def __init__(self, width=640, height=360, fps=30):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_duration = 1.0 / fps
        self.frame_count = 0

        # Audio level tracking for throb effect
        self.audio_level = 0.0
        self.audio_lock = asyncio.Lock()

        # Thinking state tracking
        self.is_thinking = False
        self.thinking_phase = 0.0  # Phase for thinking animation (0-1)
        self.thinking_speed = 2.0  # Speed of thinking animation

        # Circle properties
        self.base_radius = min(width, height) // 6  # Base circle size
        self.max_throb = 40  # Maximum additional radius when throbbing

        # Colors for different states
        self.circle_color = (100, 200, 255)  # Light blue for speaking
        self.thinking_color = (255, 200, 100)  # Orange for thinking
        self.glow_color = (50, 150, 255)  # Darker blue for glow
        self.thinking_glow_color = (255, 150, 50)  # Orange glow for thinking

        logger.info(f"🔵 ThrobCircleVideoTrack initialized: {width}x{height} @ {fps}fps")

    def update_throb_level(self, audio_level: float):
        """Update the throb level directly (called by NovaAudioTrack)"""
        self.audio_level = min(audio_level, 1.0)  # Ensure it doesn't exceed 1.0

    def set_thinking_state(self, thinking: bool):
        """Set the thinking state for visual feedback"""
        # Only log when state actually changes to avoid spam
        if self.is_thinking != thinking:
            self.is_thinking = thinking
            if thinking:
                logger.debug("🤔 Nova is thinking...")
            else:
                logger.debug("💭 Nova finished thinking")
        else:
            self.is_thinking = thinking

    def _generate_circle_frame(self):
        """Generate a frame with a throbbing circle or thinking animation"""
        try:
            # Create black background
            frame_array = np.zeros((self.height, self.width, 3), dtype=np.uint8)

            # Calculate circle center
            center_x = self.width // 2
            center_y = self.height // 2

            if self.is_thinking:
                # Thinking animation: pulsing circle with different color
                self.thinking_phase += self.thinking_speed / self.fps
                if self.thinking_phase > 1.0:
                    self.thinking_phase = 0.0

                # Create a smooth pulsing effect using sine wave
                pulse_intensity = (np.sin(self.thinking_phase * 2 * np.pi) + 1) / 2  # 0-1 range
                current_radius = int(self.base_radius + pulse_intensity * 30)

                # Use thinking colors
                circle_color = self.thinking_color
                glow_color = self.thinking_glow_color

                # Create coordinate grids
                y, x = np.ogrid[: self.height, : self.width]
                distance = np.sqrt((x - center_x) ** 2 + (y - center_y) ** 2)

                # Create multiple concentric rings for thinking effect
                for ring in range(3):
                    ring_radius = current_radius + (ring * 15)
                    ring_width = 8
                    ring_mask = (distance <= ring_radius) & (distance > ring_radius - ring_width)

                    # Fade rings based on pulse phase
                    ring_alpha = pulse_intensity * (1.0 - ring * 0.3)
                    ring_alpha = max(0.1, ring_alpha)

                    for i in range(3):
                        # Ensure ring_alpha is applied correctly to avoid float errors
                        ring_color_value = int(circle_color[i] * ring_alpha)
                        frame_array[:, :, i][ring_mask] = ring_color_value

                # Main thinking circle with gradient
                circle_mask = distance <= current_radius
                circle_intensity = 1.0 - (distance / current_radius)
                circle_intensity = np.clip(circle_intensity, 0, 1)

                # Apply main circle with pulsing intensity
                main_alpha = 0.6 + (pulse_intensity * 0.4)  # 0.6-1.0 range
                for i in range(3):
                    # Ensure proper type conversion to avoid float errors
                    main_color_values = (circle_color[i] * circle_intensity[circle_mask] * main_alpha).astype(np.uint8)
                    frame_array[:, :, i][circle_mask] = main_color_values

            else:
                # Normal speaking mode: audio-reactive throb
                throb_amount = self.audio_level * self.max_throb
                current_radius = int(self.base_radius + throb_amount)

                # Use normal speaking colors
                circle_color = self.circle_color
                glow_color = self.glow_color

                # Create coordinate grids
                y, x = np.ogrid[: self.height, : self.width]
                distance = np.sqrt((x - center_x) ** 2 + (y - center_y) ** 2)

                # Create glow effect (outer ring)
                glow_radius = current_radius + 20
                glow_mask = (distance <= glow_radius) & (distance > current_radius - 10)
                glow_intensity = 1.0 - (distance - current_radius + 10) / 30.0
                glow_intensity = np.clip(glow_intensity, 0, 1)

                # Apply glow
                for i in range(3):
                    frame_array[:, :, i][glow_mask] = (glow_color[i] * glow_intensity[glow_mask] * 0.3).astype(np.uint8)

                # Create main circle
                circle_mask = distance <= current_radius

                # Add some gradient to the circle
                circle_intensity = 1.0 - (distance / current_radius)
                circle_intensity = np.clip(circle_intensity, 0, 1)

                # Apply circle color with gradient
                for i in range(3):
                    frame_array[:, :, i][circle_mask] = (circle_color[i] * circle_intensity[circle_mask]).astype(np.uint8)

            return frame_array

        except Exception as e:
            logger.error(f"Error generating circle frame: {e}")
            # Return black frame on error
            return np.zeros((self.height, self.width, 3), dtype=np.uint8)

    async def recv(self):
        """Generate and return a circle video frame"""
        try:
            # Generate circle visualization based on current audio level
            async with self.audio_lock:
                frame_array = self._generate_circle_frame()

            # Create VideoFrame
            frame = VideoFrame.from_ndarray(frame_array, format="rgb24")

            # Set timing information
            pts = int(self.frame_count * (1 / self.fps) * 45000)  # 90kHz clock
            frame.pts = pts
            frame.time_base = Fraction(1, 45000)

            self.frame_count += 1

            # Sleep to maintain frame rate
            await asyncio.sleep(self.frame_duration)
            return frame

        except Exception as e:
            logger.error(f"Error in ThrobCircleVideoTrack.recv: {e}")
            # Return black frame on error
            frame_array = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            frame = VideoFrame.from_ndarray(frame_array, format="rgb24")
            frame.pts = int(self.frame_count * (1 / self.fps) * 45000)
            frame.time_base = Fraction(1, 45000)
            self.frame_count += 1
            await asyncio.sleep(self.frame_duration)
            return frame


class NovaAudioTrack(AudioStreamTrack):
    """
    An audio track that streams Nova speech-to-speech responses with proper chunking
    """

    def __init__(self, circle_video_track=None):
        super().__init__()
        self.audio_buffer = bytearray()
        self.buffer_lock = asyncio.Lock()
        self.frame_count = 0
        self.sample_rate = OUTPUT_SAMPLE_RATE
        self.channels = CHANNELS
        self.circle_video_track = circle_video_track  # Reference to update throb

        # Use same chunk size as nova-sonic.py for consistent timing
        self.chunk_size_bytes = CHUNK_SIZE * 2  # 512 samples * 2 bytes per sample (16-bit)

        logger.info(f"🔊 NovaAudioTrack initialized - chunk_size: {self.chunk_size_bytes} bytes")

    async def recv(self):
        """Generate and return audio frames from Nova responses"""
        try:
            async with self.buffer_lock:
                if len(self.audio_buffer) >= self.chunk_size_bytes:
                    # Extract a chunk from the buffer
                    chunk_data = bytes(self.audio_buffer[: self.chunk_size_bytes])
                    del self.audio_buffer[: self.chunk_size_bytes]
                else:
                    # Generate silence if not enough data
                    chunk_data = bytes(CHUNK_SIZE * 2)  # Silent chunk

            # Convert bytes to numpy array
            audio_array = np.frombuffer(chunk_data, dtype=np.int16)

            # Update circle throb level based on this audio chunk
            if self.circle_video_track and len(audio_array) > 0:
                # Calculate RMS level for throb
                rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
                normalized_level = min(rms / 2000.0, 1.0)  # High sensitivity
                self.circle_video_track.update_throb_level(normalized_level)

            # Create AudioFrame
            frame = AudioFrame.from_ndarray(audio_array.reshape(1, -1), format="s16", layout="mono")

            # Set timing information
            frame.sample_rate = self.sample_rate
            frame.pts = self.frame_count
            frame.time_base = Fraction(1, self.sample_rate)

            # Update frame count
            self.frame_count += len(audio_array)

            # Add small delay for smooth playback
            await asyncio.sleep(0.001)

            return frame

        except Exception as e:
            logger.error(f"Error in NovaAudioTrack.recv: {e}")
            raise

    async def add_audio_data(self, audio_data: bytes):
        """Add audio data to the buffer for streaming"""
        try:
            async with self.buffer_lock:
                self.audio_buffer.extend(audio_data)

                # Prevent buffer from growing too large
                max_buffer_size = self.sample_rate * 30 * 30
                if len(self.audio_buffer) > max_buffer_size:
                    # Remove oldest data
                    excess = len(self.audio_buffer) - max_buffer_size
                    del self.audio_buffer[:excess]
                    logger.warning(f"Audio buffer too large, removed {excess} bytes")

        except Exception as e:
            logger.error(f"Error adding audio data: {e}")

    async def stop(self):
        """Stop the audio track"""
        async with self.buffer_lock:
            self.audio_buffer.clear()


class BedrockStreamManager:
    """Manages bidirectional streaming with AWS Bedrock Nova for speech-to-speech"""

    date_time_schema = json.dumps(
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

    # Check if weather API key is available
    weather_api_key = os.getenv("WEATHER_API_KEY")
    weather_tool_available = weather_api_key is not None

    if not weather_tool_available:
        logger.warning("⚠️  WEATHER_API_KEY environment variable not found. Weather tool will not be available.")
    else:
        logger.info("🌤️  Weather tool is available")

    # Weather tool schema
    weather_schema = json.dumps(
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

    # Event templates (simplified from the original)
    START_SESSION_EVENT = """{
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
    # Build tools list dynamically based on availability
    tools_list = [
        {
            "toolSpec": {
                "name": "getDateAndTimeTool",
                "description": "Get information about the current date and time",
                "inputSchema": {
                    "json": date_time_schema
                }
            }
        }
    ]
    
    # Add weather tool if API key is available
    if weather_tool_available:
        tools_list.append({
            "toolSpec": {
                "name": "getWeatherTool",
                "description": "Get current weather information and 5-day forecast for a specified location",
                "inputSchema": {
                    "json": weather_schema
                }
            }
        })

    # fmt:off
    START_PROMPT_EVENT = (
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
    CONTENT_START_EVENT = """{
        "event": {
            "contentStart": {
                "promptName": "%s",
                "contentName": "%s",
                "type": "AUDIO",
                "interactive": true,
                "role": "USER",
                "audioInputConfiguration": {
                    "mediaType": "audio/lpcm",
                    "sampleRateHertz": 16000,
                    "sampleSizeBits": 16,
                    "channelCount": 1,
                    "audioType": "SPEECH",
                    "encoding": "base64"
                }
            }
        }
    }"""

    AUDIO_EVENT_TEMPLATE = """{
        "event": {
            "audioInput": {
                "promptName": "%s",
                "contentName": "%s",
                "content": "%s"
            }
        }
    }"""

    TEXT_CONTENT_START_EVENT = """{
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

    TEXT_INPUT_EVENT = """{
        "event": {
            "textInput": {
                "promptName": "%s",
                "contentName": "%s",
                "content": "%s"
            }
        }
    }"""

    CONTENT_END_EVENT = """{
        "event": {
            "contentEnd": {
                "promptName": "%s",
                "contentName": "%s"
            }
        }
    }"""

    def __init__(
        self, nova_audio_track: NovaAudioTrack, circle_video_track: ThrobCircleVideoTrack, model_id="amazon.nova-sonic-v1:0", region="us-east-1"
    ):
        self.model_id = model_id
        self.region = region
        self.nova_audio_track = nova_audio_track
        self.circle_video_track = circle_video_track
        self.input_subject = Subject()
        self.audio_subject = Subject()
        self.response_task = None
        self.stream_response = None
        self.is_active = False
        self.bedrock_client = None
        self.scheduler = None

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

            # Send initialization events with proper system message
            system_prompt = (
                "You are a friendly assistant named Tiffany that is participating in a live video call."
                "Keep your responses very brief and conversational, like a natural spoken dialog. "
                "Respond in 1-2 sentences maximum."
            )

            # Create proper initialization sequence
            init_events = [
                self.START_SESSION_EVENT,
                self.START_PROMPT_EVENT % self.prompt_name,
                # Add system message first (required by Nova)
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

    def add_audio_chunk(self, audio_bytes, participant_id="default"):
        """Add an audio chunk to be processed by Nova"""
        # Create unique content name per participant
        content_name = f"{self.audio_content_name}_{participant_id}"
        self.audio_subject.on_next(
            {"audio_bytes": audio_bytes, "prompt_name": self.prompt_name, "content_name": content_name, "participant_id": participant_id}
        )

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
                                        self.circle_video_track.set_thinking_state(True)
                                    elif role == "ASSISTANT":
                                        self.circle_video_track.set_thinking_state(False)

                                    # Only log if it's not a duplicate (simple dedup)
                                    if not hasattr(self, "_last_text") or self._last_text != text_content:
                                        logger.info(f"🤖 Nova ({role}): {text_content}")
                                        self._last_text = text_content

                                elif "audioOutput" in json_data["event"]:
                                    # Turn off thinking state when we get audio output (always from assistant)
                                    self.circle_video_track.set_thinking_state(False)

                                    audio_content = json_data["event"]["audioOutput"]["content"]
                                    audio_bytes = base64.b64decode(audio_content)

                                    # Send audio to Nova audio track for publishing (it will update circle throb)
                                    await self.nova_audio_track.add_audio_data(audio_bytes)

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
        await self.nova_audio_track.stop()

    async def process_tool_async(self, tool_name, tool_content):
        """Process a tool call asynchronously and return the result"""
        logger.info(f"🔧 Processing tool: {tool_name}")

        tool = tool_name.lower()
        if tool == "getdateandtimetool":
            # Get current date and time
            content = tool_content.get("content", {})
            content_data = json.loads(content)
            tz = content_data.get("timezone", "")
            logger.info(tz)
            target_timezone = ""
            now = datetime.datetime.now()
            if tz:
                logger.info(f"🔧 Timezone: {tz}")
                target_timezone = pytz.timezone(tz)
                now = datetime.datetime.now(target_timezone)

            return {
                "formattedTime": now.strftime("%I:%M %p"),
                "date": now.strftime("%Y-%m-%d"),
                "year": now.year,
                "month": now.month,
                "day": now.day,
                "dayOfWeek": now.strftime("%A").upper(),
                "timezone": "Local",
            }
        elif tool == "getweathertool":
            # Get weather information with forecast
            if not self.weather_tool_available:
                return {"error": "Weather tool is not available. WEATHER_API_KEY environment variable not set."}

            try:
                content = tool_content.get("content", {})
                content_data = json.loads(content)
                location = content_data.get("location", "")

                if not location:
                    return {"error": "Location parameter is required"}

                logger.info(f"🌤️ Getting weather forecast for: {location}")

                # Make API request to WeatherAPI forecast endpoint
                api_url = f"http://api.weatherapi.com/v1/forecast.json"
                params = {
                    "key": self.weather_api_key,
                    "q": location,
                    "days": 5,  # Get 5-day forecast
                    "hour": 99,  # Don't include hourly data (hack)
                    "aqi": "no",  # Don't include air quality data
                }

                response = requests.get(api_url, params=params, timeout=10)
                response.raise_for_status()

                weather_data = response.json()

                # Extract current weather information
                current = weather_data.get("current", {})
                location_info = weather_data.get("location", {})

                # Extract forecast information
                forecast_days = []
                forecast_data = weather_data.get("forecast", {}).get("forecastday", [])

                for day_data in forecast_data:
                    day_info = day_data.get("day", {})
                    forecast_days.append(
                        {
                            "date": day_data.get("date", ""),
                            "maxtemp_c": day_info.get("maxtemp_c"),
                            "maxtemp_f": day_info.get("maxtemp_f"),
                            "mintemp_c": day_info.get("mintemp_c"),
                            "mintemp_f": day_info.get("mintemp_f"),
                            "condition": day_info.get("condition", {}).get("text", ""),
                        }
                    )

                return {
                    "location": f"{location_info.get('name', '')}, {location_info.get('region', '')}, {location_info.get('country', '')}",
                    "current": {
                        "temperature_celsius": current.get("temp_c"),
                        "temperature_fahrenheit": current.get("temp_f"),
                        "condition": current.get("condition", {}).get("text", ""),
                        "humidity": current.get("humidity"),
                        "wind_speed_kph": current.get("wind_kph"),
                        "wind_speed_mph": current.get("wind_mph"),
                        "wind_direction": current.get("wind_dir"),
                        "feels_like_celsius": current.get("feelslike_c"),
                        "feels_like_fahrenheit": current.get("feelslike_f"),
                        "visibility_km": current.get("vis_km"),
                        "visibility_miles": current.get("vis_miles"),
                        "uv_index": current.get("uv"),
                        "last_updated": current.get("last_updated"),
                    },
                    "forecast": forecast_days,
                }

            except requests.exceptions.RequestException as e:
                logger.error(f"Weather API request failed: {e}")
                return {"error": f"Failed to fetch weather data: {str(e)}"}
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse weather API response: {e}")
                return {"error": "Failed to parse weather data"}
            except Exception as e:
                logger.error(f"Weather tool error: {e}")
                return {"error": f"Weather tool error: {str(e)}"}
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


def parse_jwt(token: str) -> Dict[str, Any]:
    """Parse JWT token without verification to extract payload"""
    try:
        parts: List[str] = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT format")

        payload: str = parts[1]
        # Add padding if needed
        payload += "=" * (4 - len(payload) % 4)
        decoded_bytes: bytes = base64.urlsafe_b64decode(payload)
        payload_json: Dict[str, Any] = json.loads(decoded_bytes.decode("utf-8"))

        return payload_json
    except Exception as e:
        logger.error(f"Error parsing JWT: {e}")
        return {}


def validate_publish_capability(token_payload: Dict[str, Any]) -> bool:
    """Validate that the token has publish capabilities"""
    capabilities = token_payload.get("capabilities", {})
    allow_publish = capabilities.get("allow_publish", False)

    if not allow_publish:
        logger.error("Token does not have publish capabilities (capabilities.allow_publish != true)")
        return False

    logger.info("✅ Token has publish capabilities")
    return True


def validate_subscribe_capability(token_payload: Dict[str, Any]) -> bool:
    """Validate that the token has subscribe capabilities"""
    capabilities = token_payload.get("capabilities", {})
    allow_subscribe = capabilities.get("allow_subscribe", False)

    if not allow_subscribe:
        logger.error("Token does not have subscribe capabilities (capabilities.allow_subscribe != true)")
        return False

    logger.info("✅ Token has subscribe capabilities")
    return True


def fix_ivs_answer_sdp(sdp: str) -> str:
    """Fix IVS's SDP answer to ensure ICE candidates are in both audio and video sections"""
    # logger.info("=== ORIGINAL IVS ANSWER ===")
    # logger.info(sdp)
    # logger.info("===========================")

    lines = sdp.split("\n")
    ice_candidates = []

    # First pass: collect all ICE candidates from audio section
    in_audio_section = False
    for line in lines:
        if line.startswith("m=audio"):
            in_audio_section = True
        elif line.startswith("m=video") or line.startswith("m=application"):
            in_audio_section = False

        # Collect ICE candidates from audio section
        if in_audio_section and line.startswith("a=candidate:"):
            ice_candidates.append(line)
            # logger.info(f"Found ICE candidate in audio: {line}")

    # Second pass: add candidates to video section
    fixed_lines = []
    in_video_section = False
    candidates_added = False

    for i, line in enumerate(lines):
        if line.startswith("m=video"):
            in_video_section = True
            candidates_added = False
        elif line.startswith("m=") and not line.startswith("m=video"):
            in_video_section = False

        # Check if we're at the end of video section
        if in_video_section and not candidates_added:
            # Look ahead to see if this is the last line of video section
            is_last_line = i == len(lines) - 1
            next_is_new_section = i < len(lines) - 1 and lines[i + 1].startswith("m=")
            is_empty_line = i < len(lines) - 1 and lines[i + 1].strip() == ""

            # If this is the last line of video section, add candidates after it
            if is_last_line or next_is_new_section or is_empty_line:
                fixed_lines.append(line)
                # logger.info(f"Adding {len(ice_candidates)} ICE candidates at end of video section")
                # Add all the ICE candidates from audio section
                for candidate in ice_candidates:
                    fixed_lines.append(candidate)
                # Add end-of-candidates
                fixed_lines.append("a=end-of-candidates")
                candidates_added = True
                continue

        fixed_lines.append(line)

    result = "\n".join(fixed_lines)
    # logger.info("=== FIXED IVS ANSWER ===")
    # logger.info(result)
    # logger.info("========================")

    return result


async def join_stage_as_publisher(token: str, nova_audio_track: NovaAudioTrack, circle_video_track: ThrobCircleVideoTrack, video_only: bool):
    """Join the IVS stage as a publisher using WebRTC with Nova audio and circle video"""
    logger.info("🚀 Joining stage as publisher with Nova audio and circle video...")

    # Create peer connection
    config = RTCConfiguration()
    config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
    pc = RTCPeerConnection(config)

    # Use global WHIP base URL for publishing
    whip_base_url = "https://global.whip.live-video.net"
    logger.info(f"🔗 WHIP Base URL: {whip_base_url}")

    # Add tracks to peer connection
    if not video_only:
        logger.info("🔈 Adding Nova audio track")
        audio_transceiver = pc.addTransceiver(nova_audio_track, direction="sendrecv")

    logger.info("🔵 Adding circle video track")
    video_transceiver = pc.addTransceiver(circle_video_track, direction="sendrecv")

    logger.info("➕ Added track(s)")

    await pc.setLocalDescription(await pc.createOffer())

    # Send offer to WHIP endpoint
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"}
    logger.info(f"Sending WebRTC offer to WHIP endpoint: {whip_base_url}")

    # Handle manual redirects to preserve Authorization header
    current_url = whip_base_url
    max_redirects = 5
    attempt = 1

    while attempt <= max_redirects:
        logger.info(f"Sending request to: {current_url} (attempt {attempt})")

        response = requests.post(current_url, data=pc.localDescription.sdp, headers=headers, allow_redirects=False)  # Handle redirects manually

        if response.status_code in [301, 302, 303, 307, 308]:
            # Handle redirect manually to preserve Authorization header
            redirect_url = response.headers.get("Location")
            if redirect_url:
                logger.info(f"Redirect {attempt}: {current_url} -> {redirect_url}")
                current_url = redirect_url
                attempt += 1
                continue
            else:
                logger.error("Redirect response missing Location header")
                return None
        elif response.status_code == 201:
            # Success!
            break
        else:
            logger.error(f"WHIP request failed with status {response.status_code}: {response.text}")
            return None

    if attempt > max_redirects:
        logger.error(f"Too many redirects (>{max_redirects})")
        return None

    if response.status_code != 201:
        logger.error(f"Failed to establish WebRTC connection: {response.status_code} - {response.text}")
        return None

    # Set remote description from answer
    logger.info("🔧 Setting remote description from IVS answer...")

    # Fix the IVS answer SDP to add ICE candidates to video section
    fixed_answer_sdp = fix_ivs_answer_sdp(response.text)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=fixed_answer_sdp, type="answer"))

    logger.info("✅ Successfully joined stage as publisher with Nova audio and waveform video")
    return pc


async def subscribe_to_participant(token: str, participant_id: str, nova_stream_manager: BedrockStreamManager):
    """Subscribe to a participant's audio/video streams and process audio through Nova"""
    logger.info(f"🎧 Subscribing to participant: {participant_id}")

    # Create peer connection
    config = RTCConfiguration()
    config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
    pc = RTCPeerConnection(config)

    # Add transceivers for receiving audio and video
    audio_transceiver = pc.addTransceiver("audio", direction="recvonly")
    video_transceiver = pc.addTransceiver("video", direction="recvonly")

    # Audio processing state
    resampler = None

    @pc.on("connectionstatechange")
    def on_connectionstatechange():
        logger.info(f"🔗 Connection state changed to: {pc.connectionState}")

    @pc.on("iceconnectionstatechange")
    def on_iceconnectionstatechange():
        logger.info(f"🧊 ICE connection state changed to: {pc.iceConnectionState}")

    @pc.on("icegatheringstatechange")
    def on_icegatheringstatechange():
        logger.info(f"🧊 ICE gathering state changed to: {pc.iceGatheringState}")

    @pc.on("signalingstatechange")
    def on_signalingstatechange():
        logger.info(f"📡 Signaling state changed to: {pc.signalingState}")

    @pc.on("track")
    def on_track(track: MediaStreamTrack):
        logger.info(f"📺 Received {track.kind} track from participant {participant_id}")
        logger.info(f"Track ID: {track.id}")
        logger.info(f"Track readyState: {track.readyState}")

        if track.kind == "audio":
            logger.info("🔊 Audio track received - processing through Nova")
            logger.info("Creating audio processing task...")
            task = asyncio.create_task(process_audio_track(track))
            logger.info(f"Audio processing task created: {task}")
        elif track.kind == "video":
            logger.info("🎥 Video track received - ignoring for now")
            # Create a task for video processing (just consuming frames)
            task = asyncio.create_task(process_video_track(track))
            logger.info(f"Video processing task created: {task}")

    async def process_audio_track(track: MediaStreamTrack):
        """Process audio track in a separate async task"""
        nonlocal resampler

        logger.info("🎵 Starting audio processing task")
        logger.info(f"Audio track readyState: {track.readyState}")

        # Wait for connection to be established
        logger.info("Waiting for WebRTC connection to be established...")
        while pc.connectionState not in ["connected", "completed"]:
            logger.info(f"Connection state: {pc.connectionState}, waiting...")
            await asyncio.sleep(0.1)
        logger.info(f"✅ Connection established: {pc.connectionState}")

        # Initialize resampler for Nova's expected format
        resampler = av.AudioResampler(format="s16", layout="mono", rate=INPUT_SAMPLE_RATE)

        # Note: No longer starting base audio content session since we handle per-participant sessions
        # Each participant will automatically start their own content session when they send audio

        # Process audio frames
        try:
            frame_count = 0
            while True:
                try:
                    # Add timeout to recv() to avoid infinite blocking
                    frame = await asyncio.wait_for(track.recv(), timeout=5.0)
                    frame_count += 1

                    # Resample to Nova's expected format (16kHz, mono, s16)
                    resampled_frames = resampler.resample(frame)

                    for i, resampled_frame in enumerate(resampled_frames):
                        # Convert to bytes and send directly to Nova
                        audio_bytes = resampled_frame.to_ndarray().tobytes()
                        nova_stream_manager.add_audio_chunk(audio_bytes)

                except asyncio.TimeoutError:
                    logger.warning(f"Timeout waiting for audio frame {frame_count} - no audio data received in 5 seconds")
                    logger.info(f"Track readyState: {track.readyState}, Connection state: {pc.connectionState}")
                    # Continue trying instead of breaking
                    continue

        except Exception as e:
            logger.error(f"Audio track processing error for participant {participant_id}: {e}")
            import traceback

            traceback.print_exc()

    async def process_video_track(track: MediaStreamTrack):
        """Process video track in a separate async task"""
        logger.info("🎬 Starting video processing task")
        try:
            frame_count = 0
            while True:
                frame = await track.recv()
                frame_count += 1
        except Exception as e:
            logger.info(f"Video track ended for participant {participant_id}: {e}")

    # Create offer
    await pc.setLocalDescription(await pc.createOffer())

    # Parse token to get WHIP URL
    token_payload = parse_jwt(token)
    if "whip_url" not in token_payload:
        logger.error("No whip_url found in token payload")
        return None

    whip_base_url = token_payload["whip_url"]
    whep_url = f"{whip_base_url}/subscribe/{participant_id}"
    logger.info(f"🔗 WHEP URL: {whep_url}")

    # Send offer to WHEP endpoint
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"}
    logger.info(f"Sending WebRTC offer to WHEP endpoint: {whep_url}")

    response = requests.post(whep_url, data=pc.localDescription.sdp, headers=headers, allow_redirects=False)

    if response.status_code in [301, 302, 307, 308]:
        redirect_url = response.headers.get("Location")
        if redirect_url:
            logger.info(f"Redirect: {whep_url} -> {redirect_url}")
            response = requests.post(redirect_url, data=pc.localDescription.sdp, headers=headers)
        else:
            logger.error("Redirect response missing Location header")
            return None

    if response.status_code != 201:
        logger.error(f"Failed to establish WebRTC subscription: {response.status_code} - {response.text}")
        return None

    # Set remote description from answer
    logger.info("🔧 Setting remote description from IVS answer...")
    fixed_answer_sdp = fix_ivs_answer_sdp(response.text)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=fixed_answer_sdp, type="answer"))

    logger.info("✅ Successfully subscribed to participant with Nova processing")
    return pc


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="IVS Stage Publisher/Subscriber with Nova Speech-to-Speech")
    parser.add_argument("--token", required=True, help="IVS stage participant token")

    # Publishing options
    parser.add_argument("--video-only", action="store_true", help="Publish video only (no audio)")

    # Subscribing options
    parser.add_argument("--subscribe-to", nargs="+", help="List of participant IDs to subscribe to")

    # Nova options
    parser.add_argument("--nova-model", default="amazon.nova-sonic-v1:0", help="Nova model ID")
    parser.add_argument("--nova-region", default="us-east-1", help="AWS region for Nova")

    return parser.parse_args()


async def main():
    """Main function that handles both publishing and subscribing with Nova speech-to-speech"""
    args = parse_args()

    logger.info("🎬 Starting IVS Stage Publisher/Subscriber with Nova Speech-to-Speech")
    logger.info(f"🔑 Using token: {args.token[:50]}... (truncated)")
    logger.info(f"🤖 Nova model: {args.nova_model}")
    logger.info(f"🌍 Nova region: {args.nova_region}")

    # Parse the JWT token to extract required fields
    token_payload = parse_jwt(args.token)

    if not token_payload:
        logger.error("Failed to parse token payload")
        return

    # Validate capabilities
    if not validate_publish_capability(token_payload):
        logger.error("❌ Token missing publish capabilities")
        return

    if args.subscribe_to and not validate_subscribe_capability(token_payload):
        logger.error("❌ Token missing subscribe capabilities")
        return

    # Extract required fields from token
    events_url = token_payload.get("events_url")
    topic = token_payload.get("topic")
    jti = token_payload.get("jti")

    logger.info(f"ℹ️  Topic: {topic}")
    logger.info(f"ℹ️  JTI: {jti}")

    try:
        connections = []
        nova_stream_manager = None

        # Create circle video track for visualization
        circle_video_track = ThrobCircleVideoTrack(width=640, height=360, fps=20)

        # Create Nova audio track for publishing responses (with circle reference)
        nova_audio_track = NovaAudioTrack(circle_video_track=circle_video_track)

        # Initialize Nova stream manager
        logger.info("🤖 Initializing Nova speech-to-speech...")
        nova_stream_manager = BedrockStreamManager(
            nova_audio_track=nova_audio_track, circle_video_track=circle_video_track, model_id=args.nova_model, region=args.nova_region
        )
        await nova_stream_manager.initialize_stream()

        # Start publishing (always happens) - now with Nova audio and circle video
        logger.info("📤 Starting publish mode with Nova audio and circle video...")
        publish_pc = await join_stage_as_publisher(args.token, nova_audio_track, circle_video_track, args.video_only)

        if publish_pc:
            logger.info("🎉 WebRTC publishing established with Nova audio and waveform video!")
            connections.append(publish_pc)
        else:
            logger.error("❌ Failed to establish WebRTC publishing")
            return

        # Start subscribing to participants if specified
        if args.subscribe_to:
            if len(args.subscribe_to) > 1:
                logger.warning("⚠️  Nova is designed for one-on-one conversations. Only subscribing to the first participant.")
                logger.warning(f"⚠️  Ignoring participants: {args.subscribe_to[1:]}")

            participant_id = args.subscribe_to[0]
            logger.info(f"📥 Starting subscribe mode for participant: {participant_id}")

            subscribe_pc = await subscribe_to_participant(args.token, participant_id, nova_stream_manager)

            if subscribe_pc:
                logger.info(f"✅ Successfully subscribed to {participant_id} with Nova processing")
                connections.append(subscribe_pc)
            else:
                logger.error(f"❌ Failed to subscribe to {participant_id}")

        if not connections:
            logger.error("❌ No connections established")
            return

        # Keep all connections alive
        try:
            logger.info(f"🔄 {len(connections)} connection(s) active with Nova speech-to-speech. Press Ctrl+C to exit.")
            logger.info("🎙️  Speak and Nova will respond through the IVS stage!")
            while True:
                await asyncio.sleep(1)  # Keep the event loop running
        except KeyboardInterrupt:
            logger.info("🛑 Shutting down...")
        finally:
            # Clean up Nova stream manager
            if nova_stream_manager:
                logger.info("🤖 Closing Nova stream...")
                await nova_stream_manager.close()

            # Clean up all peer connections
            logger.info("🔌 Closing all connections...")
            for pc in connections:
                await pc.close()
            logger.info("✅ All connections closed")

    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
    except Exception as e:
        logger.error(f"❌ Error in main: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
