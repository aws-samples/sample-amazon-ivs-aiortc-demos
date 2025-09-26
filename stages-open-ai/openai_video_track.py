import asyncio
import logging
import numpy as np
import time
import math
from fractions import Fraction
from av import VideoFrame
from aiortc import VideoStreamTrack

logger = logging.getLogger(__name__)


class OpenAIVideoTrack(VideoStreamTrack):
    """
    A video track that provides visual feedback for OpenAI real-time API conversations
    """

    def __init__(self, width=1280, height=720, fps=25):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_count = 0

        # Audio visualization state
        self.audio_level = 0.0
        self.audio_level_smoothed = 0.0
        self.audio_level_lock = asyncio.Lock()

        # Animation state
        self.animation_time = 0.0
        self.last_frame_time = time.time()

        # Colors (RGB)
        self.bg_color = (20, 25, 40)  # Dark blue background
        self.idle_color = (60, 120, 180)  # Blue for idle state
        self.active_color = (100, 200, 100)  # Green for active speaking
        self.accent_color = (255, 255, 255)  # White for accents

        logger.info(f"🎥 OpenAIVideoTrack initialized - {width}x{height} @ {fps}fps")

    def update_audio_level(self, level: float):
        """Update the audio level for visualization (0.0 to 1.0)"""
        asyncio.create_task(self._update_audio_level_async(level))

    async def _update_audio_level_async(self, level: float):
        """Async version of audio level update"""
        async with self.audio_level_lock:
            self.audio_level = max(0.0, min(1.0, level))

    async def recv(self):
        """Generate and return video frames with audio visualization"""
        try:
            current_time = time.time()
            dt = current_time - self.last_frame_time
            self.last_frame_time = current_time
            self.animation_time += dt

            # Smooth audio level changes
            async with self.audio_level_lock:
                target_level = self.audio_level

            # Smooth transition to target level
            smoothing_factor = 0.1
            self.audio_level_smoothed += (target_level - self.audio_level_smoothed) * smoothing_factor

            # Create frame
            frame = self._create_visualization_frame()

            # Set timing information
            frame.pts = self.frame_count
            frame.time_base = Fraction(1, self.fps)
            self.frame_count += 1

            # Maintain target FPS
            await asyncio.sleep(1.0 / self.fps)

            return frame

        except Exception as e:
            logger.error(f"Error in OpenAIVideoTrack.recv: {e}")
            raise

    def _create_visualization_frame(self) -> VideoFrame:
        """Create a video frame with audio visualization"""
        # Create RGB array
        frame_array = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        # Fill background
        frame_array[:, :] = self.bg_color

        # Calculate center
        center_x = self.width // 2
        center_y = self.height // 2

        # Determine if we're in active speaking mode
        is_active = self.audio_level_smoothed > 0.05

        if is_active:
            # Active speaking visualization - pulsing circles
            self._draw_active_visualization(frame_array, center_x, center_y)
        else:
            # Idle visualization - gentle breathing animation
            self._draw_idle_visualization(frame_array, center_x, center_y)

        # Add OpenAI branding text
        self._draw_text_overlay(frame_array)

        # Convert to VideoFrame
        frame = VideoFrame.from_ndarray(frame_array, format="rgb24")
        return frame

    def _draw_active_visualization(self, frame_array, center_x, center_y):
        """Draw active speaking visualization with pulsing circles"""
        # Base radius influenced by audio level
        base_radius = 30 + (self.audio_level_smoothed * 50)

        # Multiple pulsing circles
        for i in range(3):
            # Phase offset for each circle
            phase_offset = i * (2 * math.pi / 3)
            pulse_factor = 1.0 + 0.3 * math.sin(self.animation_time * 4 + phase_offset)
            radius = int(base_radius * pulse_factor * (1.0 - i * 0.2))

            # Color intensity based on audio level and circle index
            intensity = self.audio_level_smoothed * (1.0 - i * 0.3)
            color = self._blend_colors(self.idle_color, self.active_color, intensity)

            self._draw_circle(frame_array, center_x, center_y, radius, color, filled=False, thickness=3)

    def _draw_idle_visualization(self, frame_array, center_x, center_y):
        """Draw idle visualization with gentle breathing animation"""
        # Gentle breathing animation
        breath_factor = 1.0 + 0.1 * math.sin(self.animation_time * 0.8)
        radius = int(40 * breath_factor)

        # Draw main circle
        self._draw_circle(frame_array, center_x, center_y, radius, self.idle_color, filled=False, thickness=2)

        # Draw inner dot
        inner_radius = int(8 * breath_factor)
        self._draw_circle(frame_array, center_x, center_y, inner_radius, self.idle_color, filled=True)

    def _draw_text_overlay(self, frame_array):
        """Draw text overlay with OpenAI branding"""
        # Simple text rendering - just draw "OpenAI" at the bottom
        text_y = self.height - 30
        text_x = 20

        # Draw simple text (very basic implementation)
        text = "OpenAI Real-time API"
        self._draw_simple_text(frame_array, text, text_x, text_y, self.accent_color)

    def _draw_simple_text(self, frame_array, text, x, y, color):
        """Draw simple text (basic implementation)"""
        # This is a very basic text rendering - just draw some pixels
        # In a real implementation, you'd use a proper font rendering library
        char_width = 8
        char_height = 12

        for i, char in enumerate(text[:20]):  # Limit text length
            char_x = x + i * char_width
            if char_x + char_width < self.width and y + char_height < self.height:
                # Draw a simple character representation
                self._draw_char_pixels(frame_array, char, char_x, y, color)

    def _draw_char_pixels(self, frame_array, char, x, y, color):
        """Draw a simple character using pixels"""
        # Very basic character rendering - just draw some pixels for common characters
        if char == "O":
            self._draw_rectangle(frame_array, x + 1, y + 1, x + 6, y + 10, color, filled=False)
        elif char == "p":
            self._draw_rectangle(frame_array, x + 1, y + 3, x + 2, y + 11, color, filled=True)
            self._draw_rectangle(frame_array, x + 1, y + 3, x + 5, y + 7, color, filled=False)
        elif char == "e":
            self._draw_rectangle(frame_array, x + 1, y + 3, x + 5, y + 9, color, filled=False)
            self._draw_rectangle(frame_array, x + 1, y + 6, x + 4, y + 6, color, filled=True)
        elif char == "n":
            self._draw_rectangle(frame_array, x + 1, y + 3, x + 2, y + 9, color, filled=True)
            self._draw_rectangle(frame_array, x + 4, y + 3, x + 5, y + 9, color, filled=True)
            self._draw_rectangle(frame_array, x + 1, y + 3, x + 5, y + 4, color, filled=True)
        elif char == "A":
            self._draw_rectangle(frame_array, x + 1, y + 1, x + 5, y + 2, color, filled=True)
            self._draw_rectangle(frame_array, x + 1, y + 1, x + 2, y + 10, color, filled=True)
            self._draw_rectangle(frame_array, x + 4, y + 1, x + 5, y + 10, color, filled=True)
            self._draw_rectangle(frame_array, x + 1, y + 5, x + 5, y + 6, color, filled=True)
        elif char == "I":
            self._draw_rectangle(frame_array, x + 2, y + 1, x + 4, y + 10, color, filled=True)
        elif char == " ":
            pass  # Space - do nothing
        elif char == "R":
            self._draw_rectangle(frame_array, x + 1, y + 1, x + 2, y + 10, color, filled=True)
            self._draw_rectangle(frame_array, x + 1, y + 1, x + 4, y + 2, color, filled=True)
            self._draw_rectangle(frame_array, x + 1, y + 5, x + 4, y + 6, color, filled=True)
            self._draw_rectangle(frame_array, x + 4, y + 1, x + 5, y + 5, color, filled=True)
            self._draw_rectangle(frame_array, x + 4, y + 6, x + 5, y + 10, color, filled=True)
        elif char == "l":
            self._draw_rectangle(frame_array, x + 2, y + 1, x + 3, y + 10, color, filled=True)
        elif char == "t":
            self._draw_rectangle(frame_array, x + 2, y + 1, x + 3, y + 10, color, filled=True)
            self._draw_rectangle(frame_array, x + 1, y + 3, x + 4, y + 4, color, filled=True)
        elif char == "i":
            self._draw_rectangle(frame_array, x + 2, y + 3, x + 3, y + 9, color, filled=True)
            self._draw_rectangle(frame_array, x + 2, y + 1, x + 3, y + 2, color, filled=True)
        elif char == "m":
            self._draw_rectangle(frame_array, x + 1, y + 3, x + 2, y + 9, color, filled=True)
            self._draw_rectangle(frame_array, x + 3, y + 3, x + 4, y + 9, color, filled=True)
            self._draw_rectangle(frame_array, x + 5, y + 3, x + 6, y + 9, color, filled=True)
            self._draw_rectangle(frame_array, x + 1, y + 3, x + 6, y + 4, color, filled=True)
        elif char == "-":
            self._draw_rectangle(frame_array, x + 1, y + 5, x + 5, y + 6, color, filled=True)
        else:
            # Default character - just draw a small rectangle
            self._draw_rectangle(frame_array, x + 1, y + 3, x + 5, y + 9, color, filled=False)

    def _draw_circle(self, frame_array, center_x, center_y, radius, color, filled=False, thickness=1):
        """Draw a circle on the frame array"""
        for y in range(max(0, center_y - radius - thickness), min(self.height, center_y + radius + thickness + 1)):
            for x in range(max(0, center_x - radius - thickness), min(self.width, center_x + radius + thickness + 1)):
                distance = math.sqrt((x - center_x) ** 2 + (y - center_y) ** 2)

                if filled:
                    if distance <= radius:
                        frame_array[y, x] = color
                else:
                    if radius - thickness <= distance <= radius + thickness:
                        frame_array[y, x] = color

    def _draw_rectangle(self, frame_array, x1, y1, x2, y2, color, filled=True):
        """Draw a rectangle on the frame array"""
        x1, x2 = max(0, min(x1, x2)), min(self.width - 1, max(x1, x2))
        y1, y2 = max(0, min(y1, y2)), min(self.height - 1, max(y1, y2))

        if filled:
            frame_array[y1 : y2 + 1, x1 : x2 + 1] = color
        else:
            # Draw border
            frame_array[y1, x1 : x2 + 1] = color  # Top
            frame_array[y2, x1 : x2 + 1] = color  # Bottom
            frame_array[y1 : y2 + 1, x1] = color  # Left
            frame_array[y1 : y2 + 1, x2] = color  # Right

    def _blend_colors(self, color1, color2, factor):
        """Blend two colors based on factor (0.0 = color1, 1.0 = color2)"""
        factor = max(0.0, min(1.0, factor))
        return tuple(int(c1 * (1 - factor) + c2 * factor) for c1, c2 in zip(color1, color2))
