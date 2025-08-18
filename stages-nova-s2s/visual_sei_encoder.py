#!/usr/bin/env python3

import asyncio
import logging
import json
import numpy as np
from typing import Optional, List, Dict, Any
import time

logger = logging.getLogger(__name__)


class VisualSeiEncoder:
    """
    Encodes SEI data as visual patterns in video frames.

    Since Python WebRTC libraries don't provide easy access to encoded frames,
    this approach embeds SEI data as visual patterns that can be extracted
    from the decoded video frames on the client side.
    """

    def __init__(self, frame_width: int = 1280, frame_height: int = 720):
        """
        Initialize the visual SEI encoder.

        Args:
            frame_width: Width of video frames
            frame_height: Height of video frames
        """
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.message_queue: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()

        # Visual encoding parameters
        self.data_region_height = 2  # Height of data region in pixels
        self.data_region_y = frame_height - self.data_region_height  # Bottom of frame
        self.max_message_length = frame_width // 4  # Max message length in bytes

        # Encoding patterns
        self.sync_pattern = [0xFF, 0x00, 0xFF, 0x00]  # Sync pattern to identify data
        self.uuid_bytes = [0x9E, 0x50, 0x4E, 0xA5, 0xEE, 0x5A, 0x4F, 0x02, 0x94, 0x9F, 0xB0, 0x33, 0xA3, 0x76, 0x8D, 0xA2]

        logger.info(f"📺 Visual SEI encoder initialized ({frame_width}x{frame_height})")

    async def queue_message(self, data: Dict[str, Any], repeat_count: int = 3) -> bool:
        """
        Queue a message for visual encoding.

        Args:
            data: Dictionary data to encode
            repeat_count: Number of frames to repeat the message

        Returns:
            True if successfully queued
        """
        try:
            # Add timestamp if not present
            if "timestamp" not in data:
                data["timestamp"] = time.time()

            # Serialize to JSON
            json_str = json.dumps(data, separators=(",", ":"))  # Compact JSON
            json_bytes = json_str.encode("utf-8")

            # Check if message fits
            if len(json_bytes) > self.max_message_length:
                logger.warning(f"⚠️  Message too long ({len(json_bytes)} > {self.max_message_length}), truncating")
                json_bytes = json_bytes[: self.max_message_length]

            message = {"data": json_bytes, "repeat_count": repeat_count, "frames_remaining": repeat_count}

            async with self._lock:
                self.message_queue.append(message)

            logger.debug(f"📺 Queued visual SEI message: {len(json_bytes)} bytes, {repeat_count} repeats")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to queue visual SEI message: {e}")
            return False

    async def encode_frame(self, frame_array: np.ndarray) -> np.ndarray:
        """
        Encode SEI data into a video frame.

        Args:
            frame_array: RGB frame array (height, width, 3)

        Returns:
            Modified frame array with SEI data encoded
        """
        async with self._lock:
            if not self.message_queue:
                return frame_array

            # Get current message to encode
            current_message = self.message_queue[0]

            # Create a copy of the frame to modify
            modified_frame = frame_array.copy()

            # Encode the message into the bottom rows
            self._encode_message_into_frame(modified_frame, current_message["data"])

            # Decrement repeat count
            current_message["frames_remaining"] -= 1

            # Remove message if done repeating
            if current_message["frames_remaining"] <= 0:
                self.message_queue.pop(0)
                logger.debug(f"📺 Finished encoding visual SEI message")

            return modified_frame

    def _encode_message_into_frame(self, frame_array: np.ndarray, message_data: bytes):
        """
        Encode message data into the frame using visual patterns.

        The encoding uses the bottom 2 rows of pixels:
        Row 1: Sync pattern + UUID + Message length
        Row 2: Message data (padded with zeros if needed)
        """
        try:
            # Prepare data to encode
            header = self.sync_pattern + self.uuid_bytes + [len(message_data)]
            full_data = header + list(message_data)

            # Pad to frame width if needed
            while len(full_data) < self.frame_width:
                full_data.append(0)

            # Encode into bottom rows
            for row in range(self.data_region_height):
                start_idx = row * self.frame_width
                end_idx = min(start_idx + self.frame_width, len(full_data))

                if start_idx < len(full_data):
                    row_y = self.data_region_y + row

                    # Encode data as grayscale values in the red channel
                    # Use green and blue channels for error detection
                    for x in range(end_idx - start_idx):
                        data_byte = full_data[start_idx + x]

                        # Red channel: actual data
                        frame_array[row_y, x, 0] = data_byte

                        # Green channel: inverted data for error detection
                        frame_array[row_y, x, 1] = 255 - data_byte

                        # Blue channel: checksum
                        frame_array[row_y, x, 2] = (data_byte ^ 0xFF) & 0xFF

            logger.debug(f"📺 Encoded {len(message_data)} bytes into frame")

        except Exception as e:
            logger.error(f"❌ Failed to encode message into frame: {e}")

    async def get_queue_size(self) -> int:
        """Get the current number of queued messages"""
        async with self._lock:
            return len(self.message_queue)

    async def clear_queue(self):
        """Clear all queued messages"""
        async with self._lock:
            cleared_count = len(self.message_queue)
            self.message_queue.clear()
            if cleared_count > 0:
                logger.info(f"🗑️  Cleared {cleared_count} queued visual SEI messages")

    def get_encoding_info(self) -> Dict[str, Any]:
        """Get information about the visual encoding format"""
        return {
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "data_region_height": self.data_region_height,
            "data_region_y": self.data_region_y,
            "max_message_length": self.max_message_length,
            "sync_pattern": self.sync_pattern,
            "uuid": self.uuid_bytes,
        }
