#!/usr/bin/env python3

import asyncio
import logging
import numpy as np
from aiortc import VideoStreamTrack
from av import VideoFrame
from fractions import Fraction

# Configure logging
logger = logging.getLogger(__name__)


class AgentVideoTrack(VideoStreamTrack):
    """
    A video track that generates a simple throbbing circle when the agent is speaking
    and a pulsing animation when the agent is thinking/processing
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

        logger.info(f"🔵 AgentVideoTrack initialized: {width}x{height} @ {fps}fps")

    def update_throb_level(self, audio_level: float):
        """Update the throb level directly (called by AgentAudioTrack)"""
        self.audio_level = min(audio_level, 1.0)  # Ensure it doesn't exceed 1.0

    def set_thinking_state(self, thinking: bool):
        """Set the thinking state for visual feedback"""
        # Only log when state actually changes to avoid spam
        if self.is_thinking != thinking:
            self.is_thinking = thinking
            if thinking:
                logger.debug("🤔 Agent is thinking...")
            else:
                logger.debug("💭 Agent finished thinking")
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
            logger.error(f"Error in AgentVideoTrack.recv: {e}")
            # Return black frame on error
            frame_array = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            frame = VideoFrame.from_ndarray(frame_array, format="rgb24")
            frame.pts = int(self.frame_count * (1 / self.fps) * 45000)
            frame.time_base = Fraction(1, 45000)
            self.frame_count += 1
            await asyncio.sleep(self.frame_duration)
            return frame
