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

    def __init__(self, width=1280, height=720, fps=30):
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
        self.spin_phase = 0.0  # Phase for spinning animation (0-2π)
        self.spin_speed = 1.0  # Speed of spinning animation

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

    def _apply_smooth_circle(self, frame_array, center_x, center_y, radius, color, alpha=1.0, smoothness=2.0):
        """Apply a very smooth antialiased circle with enhanced edge smoothing"""
        y, x = np.ogrid[:self.height, :self.width]
        distance = np.sqrt((x - center_x) ** 2 + (y - center_y) ** 2)
        
        # Create ultra-smooth antialiased edges with wider transition zone
        transition_width = smoothness
        edge_alpha = np.clip((radius + transition_width - distance) / transition_width, 0, 1) * alpha
        
        # Apply smooth blending
        for i in range(3):
            current_values = frame_array[:, :, i].astype(np.float32)
            new_values = current_values * (1 - edge_alpha) + color[i] * edge_alpha
            frame_array[:, :, i] = np.clip(new_values, 0, 255).astype(np.uint8)

    def _draw_spinning_donut(self, frame_array, center_x, center_y, inner_radius, outer_radius):
        """Draw a spinning gradient donut around the thinking circle"""
        self.spin_phase += (self.spin_speed * 2 * np.pi) / self.fps
        if self.spin_phase > 2 * np.pi:
            self.spin_phase -= 2 * np.pi
            
        y, x = np.ogrid[:self.height, :self.width]
        distance = np.sqrt((x - center_x) ** 2 + (y - center_y) ** 2)
        
        # Create donut mask
        donut_mask = (distance >= inner_radius) & (distance <= outer_radius)
        
        # Calculate angle for each pixel
        angle = np.arctan2(y - center_y, x - center_x) + np.pi  # 0 to 2π
        
        # Create spinning gradient effect
        gradient_phase = (angle + self.spin_phase) % (2 * np.pi)
        gradient_intensity = (np.sin(gradient_phase * 2) + 1) / 2  # 0-1 range with 2 cycles
        
        # Add radial gradient for donut thickness
        donut_thickness = outer_radius - inner_radius
        radial_pos = (distance - inner_radius) / donut_thickness
        radial_gradient = 1.0 - np.abs(radial_pos - 0.5) * 2  # Peak at center of donut
        
        # Combine gradients
        final_intensity = gradient_intensity * radial_gradient * 0.6
        
        # Apply smooth edges to donut
        inner_edge = np.clip((distance - inner_radius + 1) / 2, 0, 1)
        outer_edge = np.clip((outer_radius + 1 - distance) / 2, 0, 1)
        edge_mask = inner_edge * outer_edge
        
        final_alpha = final_intensity * edge_mask
        
        # Apply the spinning donut
        for i in range(3):
            current_values = frame_array[:, :, i][donut_mask].astype(np.float32)
            new_values = current_values + self.thinking_glow_color[i] * final_alpha[donut_mask]
            frame_array[:, :, i][donut_mask] = np.clip(new_values, 0, 255).astype(np.uint8)

    def _generate_circle_frame(self):
        """Generate a frame with a throbbing circle or thinking animation"""
        try:
            # Create black background
            frame_array = np.zeros((self.height, self.width, 3), dtype=np.uint8)

            # Calculate circle center
            center_x = self.width // 2
            center_y = self.height // 2

            if self.is_thinking:
                # Thinking animation: pulsing circle with spinning dots
                self.thinking_phase += self.thinking_speed / self.fps
                if self.thinking_phase > 1.0:
                    self.thinking_phase = 0.0

                # Create a smooth pulsing effect using sine wave
                pulse_intensity = (np.sin(self.thinking_phase * 2 * np.pi) + 1) / 2  # 0-1 range
                current_radius = self.base_radius + pulse_intensity * 20

                # Create outer glow with smooth falloff
                glow_radius = current_radius + 30
                y, x = np.ogrid[:self.height, :self.width]
                distance = np.sqrt((x - center_x) ** 2 + (y - center_y) ** 2)
                
                # Smooth glow falloff
                glow_mask = distance <= glow_radius
                glow_intensity = np.exp(-(distance - current_radius) / 15) * 0.4 * pulse_intensity
                glow_intensity = np.clip(glow_intensity, 0, 1)
                
                for i in range(3):
                    current_values = frame_array[:, :, i][glow_mask].astype(np.float32)
                    new_values = current_values + self.thinking_glow_color[i] * glow_intensity[glow_mask]
                    frame_array[:, :, i][glow_mask] = np.clip(new_values, 0, 255).astype(np.uint8)

                # Main thinking circle with ultra-smooth antialiasing
                main_alpha = 0.8 + (pulse_intensity * 0.2)
                self._apply_smooth_circle(frame_array, center_x, center_y, current_radius, self.thinking_color, alpha=main_alpha, smoothness=3.0)

                # Add spinning donut closer to the circle
                donut_inner = current_radius + 8
                donut_outer = current_radius + 18
                self._draw_spinning_donut(frame_array, center_x, center_y, donut_inner, donut_outer)

            else:
                # Normal speaking mode: audio-reactive throb with smooth edges
                throb_amount = self.audio_level * self.max_throb
                current_radius = self.base_radius + throb_amount

                # Create coordinate grids
                y, x = np.ogrid[:self.height, :self.width]
                distance = np.sqrt((x - center_x) ** 2 + (y - center_y) ** 2)

                # Create smooth glow effect
                glow_radius = current_radius + 25
                glow_intensity = np.exp(-(distance - current_radius) / 12) * 0.5
                glow_intensity = np.clip(glow_intensity, 0, 1)
                glow_mask = distance <= glow_radius

                for i in range(3):
                    current_values = frame_array[:, :, i][glow_mask].astype(np.float32)
                    new_values = current_values + self.glow_color[i] * glow_intensity[glow_mask]
                    frame_array[:, :, i][glow_mask] = np.clip(new_values, 0, 255).astype(np.uint8)

                # Create main circle with ultra-smooth antialiasing
                self._apply_smooth_circle(frame_array, center_x, center_y, current_radius, self.circle_color, alpha=0.9, smoothness=3.0)

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
