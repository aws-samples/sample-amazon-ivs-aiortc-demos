#!/usr/bin/env python3

import asyncio
import logging
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import json

logger = logging.getLogger(__name__)


@dataclass
class SeiMessage:
    """Represents an SEI message to be published"""

    payload: bytes
    repeat_count: int = 3
    timestamp: Optional[float] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()


class SeiPublisher:
    """
    Portable SEI NAL unit publisher for WebRTC video streams.

    This class handles the creation and insertion of SEI (Supplemental Enhancement Information)
    NAL units into H.264/H.265 video streams for transmitting metadata alongside video frames.
    """

    # Constants for SEI NAL unit construction
    SEI_TYPE_USER_DATA_UNREGISTERED = 0x05
    SEI_PAYLOAD_TERMINATION = 0x80
    NAL_UNIT_TYPE_SEI = 0x06

    # UUID for identifying our SEI messages (v4 UUID: 9e504ea5-ee5a-4f02-949f-b033a3768da2)
    SEND_SEI_UUID = bytes([0x9E, 0x50, 0x4E, 0xA5, 0xEE, 0x5A, 0x4F, 0x02, 0x94, 0x9F, 0xB0, 0x33, 0xA3, 0x76, 0x8D, 0xA2])

    def __init__(self, max_retry_attempts: int = 3):
        """
        Initialize the SEI publisher.

        Args:
            max_retry_attempts: Maximum number of retry attempts for publishing
        """
        self.max_retry_attempts = max_retry_attempts
        self.message_queue: List[SeiMessage] = []
        self._lock = asyncio.Lock()

    async def publish_text(self, text: str, repeat_count: int = 3) -> bool:
        """
        Publish text content as SEI metadata.

        Args:
            text: Text content to publish
            repeat_count: Number of times to repeat the message for reliability

        Returns:
            True if successfully queued for publishing, False otherwise
        """
        try:
            # Create JSON payload with timestamp for deduplication
            payload_data = {"text": text, "timestamp": time.time(), "type": "text_content"}
            payload_bytes = json.dumps(payload_data).encode("utf-8")

            message = SeiMessage(payload=payload_bytes, repeat_count=repeat_count, timestamp=payload_data["timestamp"])

            async with self._lock:
                self.message_queue.append(message)

            logger.info(f"📡 Queued SEI text message: {text[:50]}{'...' if len(text) > 50 else ''}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to queue SEI text message: {e}")
            return False

    async def publish_json(self, data: Dict[str, Any], repeat_count: int = 3) -> bool:
        """
        Publish JSON data as SEI metadata.

        Args:
            data: Dictionary to publish as JSON
            repeat_count: Number of times to repeat the message for reliability

        Returns:
            True if successfully queued for publishing, False otherwise
        """
        try:
            # Add timestamp for deduplication if not present
            if "timestamp" not in data:
                data["timestamp"] = time.time()

            payload_bytes = json.dumps(data).encode("utf-8")

            # Check payload size - IVS has 1KB SEI limit, so keep individual messages small
            MAX_PAYLOAD_SIZE = 400  # Conservative limit for individual message
            if len(payload_bytes) > MAX_PAYLOAD_SIZE:
                # Truncate content if too large
                if "content" in data and len(data["content"]) > 100:
                    original_content = data["content"]
                    data["content"] = original_content[:100] + "..."
                    data["truncated"] = True
                    data["original_length"] = len(original_content)
                    payload_bytes = json.dumps(data).encode("utf-8")
                    logger.warning(
                        f"📡 Truncated SEI message from {len(json.dumps({**data, 'content': original_content}).encode('utf-8'))} to {len(payload_bytes)} bytes"
                    )

            queue_timestamp = time.time()
            message = SeiMessage(payload=payload_bytes, repeat_count=repeat_count, timestamp=data["timestamp"])

            async with self._lock:
                queue_size_before = len(self.message_queue)
                self.message_queue.append(message)
                queue_size_after = len(self.message_queue)

            # Calculate time since original message timestamp
            time_since_creation = queue_timestamp - data["timestamp"]

            logger.info(
                f"📡 Queued SEI message: {len(payload_bytes)} bytes, queue: {queue_size_before}→{queue_size_after}, delay: {time_since_creation*1000:.1f}ms"
            )

            # Log additional details for tracking
            if "publish_sequence" in data:
                logger.info(f"📡 Message #{data['publish_sequence']} queued: {data.get('role', 'unknown')} - repeat_count: {repeat_count}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to queue SEI JSON message: {e}")
            return False

    def _length_to_uint8(self, length: int) -> bytes:
        """Convert length to variable-length encoding used in SEI"""
        payload_length = []
        while length >= 255:
            payload_length.append(0xFF)
            length -= 255
        payload_length.append(length)
        return bytes(payload_length)

    def _create_header(self, uuid: bytes, payload_length: bytes) -> bytes:
        """Create SEI NAL unit header"""
        start = bytes([0x00, 0x00, 0x01])
        return start + bytes([self.NAL_UNIT_TYPE_SEI, self.SEI_TYPE_USER_DATA_UNREGISTERED]) + payload_length + uuid

    def _do_emulation_prevention(self, payload: bytes) -> bytes:
        """
        Apply emulation prevention exactly like reference implementation.
        Replaces 0x00 0x00 0x00, 0x00 0x00 0x01, 0x00 0x00 0x02, 0x00 0x00 0x03
        with 0x00 0x00 0x03 0x00, 0x00 0x00 0x03 0x01, etc.
        """
        new_payload = bytearray(payload)
        i = 2

        while i < len(new_payload) - 2:
            if new_payload[i] == 0x00 and new_payload[i + 1] == 0x00 and new_payload[i + 2] in [0x00, 0x01, 0x02, 0x03]:
                new_payload.insert(i + 2, 0x03)
                i += 3
            else:
                i += 1

        return bytes(new_payload)

    def _create_sei_nal_unit(self, uuid: bytes, input_payload: bytes) -> bytes:
        """Create a complete SEI NAL unit"""
        header = self._create_header(uuid, self._length_to_uint8(len(input_payload) + len(uuid)))
        unit = header + input_payload + bytes([self.SEI_PAYLOAD_TERMINATION])
        return self._do_emulation_prevention(unit)

    def _find_insert_position(self, frame_data: bytes) -> int:
        """Find position to insert SEI NAL unit (before first video slice)"""
        data_len = len(frame_data)
        i = 0

        while i < data_len - 4:
            # Check for 3-byte start code
            if frame_data[i] == 0x00 and frame_data[i + 1] == 0x00 and frame_data[i + 2] == 0x01:

                nal_unit_type = frame_data[i + 3] & 0x1F
                # Insert before video slice NAL units (types 1-5)
                if 1 <= nal_unit_type <= 5:
                    return i
                i += 3

            # Check for 4-byte start code
            elif i < data_len - 5 and frame_data[i] == 0x00 and frame_data[i + 1] == 0x00 and frame_data[i + 2] == 0x00 and frame_data[i + 3] == 0x01:

                nal_unit_type = frame_data[i + 4] & 0x1F
                # Insert before video slice NAL units (types 1-5)
                if 1 <= nal_unit_type <= 5:
                    return i
                i += 4
            else:
                i += 1

        return -1

    def _insert_sei_unit(self, frame_data: bytes, sei_unit: bytes) -> bytes:
        """Insert SEI unit into frame data at optimal position"""
        insert_position = self._find_insert_position(frame_data)

        if insert_position >= 0:
            # Insert SEI unit before the found NAL unit
            new_data = bytearray(len(frame_data) + len(sei_unit))
            new_data[:insert_position] = frame_data[:insert_position]
            new_data[insert_position : insert_position + len(sei_unit)] = sei_unit
            new_data[insert_position + len(sei_unit) :] = frame_data[insert_position:]
            return bytes(new_data)
        else:
            # Fallback: prepend to beginning of frame
            return sei_unit + frame_data

    async def process_frame(self, frame_data: bytes) -> bytes:
        """
        Process a video frame and insert any queued SEI messages.

        Args:
            frame_data: Raw video frame data

        Returns:
            Modified frame data with SEI units inserted
        """
        async with self._lock:
            if not self.message_queue:
                return frame_data

            # Process all queued messages
            messages_to_process = self.message_queue.copy()
            self.message_queue.clear()

        modified_data = frame_data

        for message in messages_to_process:
            try:
                # Create SEI NAL unit for this message
                sei_unit = self._create_sei_nal_unit(self.SEND_SEI_UUID, message.payload)

                # Insert into frame data (repeat for reliability)
                for _ in range(message.repeat_count):
                    modified_data = self._insert_sei_unit(modified_data, sei_unit)

                logger.info(f"📡 Inserted SEI unit: {len(sei_unit)} bytes, repeated {message.repeat_count} times")

            except Exception as e:
                logger.error(f"❌ Failed to insert SEI unit: {e}")

        return modified_data

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
                logger.info(f"🗑️  Cleared {cleared_count} queued SEI messages")
