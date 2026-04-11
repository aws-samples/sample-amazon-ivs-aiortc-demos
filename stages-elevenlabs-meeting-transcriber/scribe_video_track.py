#!/usr/bin/env python3
"""
Static video track that displays the ElevenLabs logo.
Used as the scribe's published video on the IVS stage.
"""

import asyncio
import logging
import os
import numpy as np
from aiortc import VideoStreamTrack
from av import VideoFrame
from fractions import Fraction
from PIL import Image

logger = logging.getLogger(__name__)


class ScribeVideoTrack(VideoStreamTrack):
    """A video track that displays a static ElevenLabs logo image"""

    def __init__(self, width=640, height=360, fps=5):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_duration = 1.0 / fps
        self.frame_count = 0
        self._frame_array = self._build_frame()
        logger.info(f"📹 ScribeVideoTrack initialized: {width}x{height} @ {fps}fps")

    def _build_frame(self) -> np.ndarray:
        """Build the static frame — load logo or fall back to branded color"""
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        logo_path = os.path.join(os.path.dirname(__file__), "robot-icon.png")
        if os.path.exists(logo_path):
            try:
                img = Image.open(logo_path).convert("RGB")
                img = img.resize((self.width, self.height), Image.LANCZOS)
                frame = np.array(img)
                logger.info(f"✅ Loaded scribe icon from {logo_path}")
                return frame
            except Exception as e:
                logger.warning(f"⚠️  Could not load icon: {e}")

        # Fallback: white frame (icon file not found)
        logger.info("Using white frame (no icon file found)")
        frame[:] = 255
        return frame

    async def recv(self):
        """Return the static logo frame"""
        frame = VideoFrame.from_ndarray(self._frame_array, format="rgb24")
        frame.pts = int(self.frame_count * (1 / self.fps) * 90000)
        frame.time_base = Fraction(1, 90000)
        self.frame_count += 1
        await asyncio.sleep(self.frame_duration)
        return frame
