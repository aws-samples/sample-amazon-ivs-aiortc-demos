#!/usr/bin/env python3

import asyncio
import logging
import json
import time
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
import av

logger = logging.getLogger(__name__)


@dataclass
class ReceivedSeiMessage:
    """Represents a received SEI message"""

    payload: bytes
    timestamp: float
    frame_timestamp: Optional[float] = None
    message_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        try:
            # Try to decode as JSON
            payload_str = self.payload.decode("utf-8")
            payload_data = json.loads(payload_str)
            return {"payload": payload_data, "timestamp": self.timestamp, "frame_timestamp": self.frame_timestamp, "message_id": self.message_id}
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Fallback to base64 for binary data
            import base64

            return {
                "payload": base64.b64encode(self.payload).decode("ascii"),
                "payload_type": "binary",
                "timestamp": self.timestamp,
                "frame_timestamp": self.frame_timestamp,
                "message_id": self.message_id,
            }


class SeiSubscriber:
    """
    SEI message subscriber for extracting metadata from H.264 video streams.

    This class handles the detection and extraction of SEI (Supplemental Enhancement Information)
    NAL units from incoming H.264 video streams, enabling reception of synchronized metadata.
    """

    # UUID for identifying Amazon IVS SEI messages (from IVS web broadcast SDK)
    TARGET_SEI_UUID = bytes([0x9E, 0x50, 0x4E, 0xA5, 0xEE, 0x5A, 0x4F, 0x02, 0x94, 0x9F, 0xB0, 0x33, 0xA3, 0x76, 0x8D, 0xA2])

    def __init__(self, message_callback: Optional[Callable[[ReceivedSeiMessage], None]] = None):
        """
        Initialize the SEI subscriber.

        Args:
            message_callback: Optional callback function to handle received messages
        """
        self.message_callback = message_callback
        self.received_messages: List[ReceivedSeiMessage] = []
        self._lock = asyncio.Lock()
        self._message_cache: Dict[str, float] = {}  # For deduplication
        self._cache_cleanup_interval = 30.0  # seconds
        self._last_cleanup = time.time()
        self._frame_count = 0  # For debugging frame processing
        self._last_debug_log = time.time()

    def set_message_callback(self, callback: Callable[[ReceivedSeiMessage], None]):
        """Set or update the message callback function"""
        self.message_callback = callback

    async def process_frame(self, frame: av.VideoFrame) -> List[ReceivedSeiMessage]:
        """
        Process a video frame and extract any SEI messages.

        Args:
            frame: PyAV VideoFrame object

        Returns:
            List of extracted SEI messages
        """
        messages = []
        self._frame_count += 1

        # Process frames silently unless we find SEI messages

        try:
            # Get frame data - try different methods to access raw data
            frame_data = None

            # Method 1: Try to get packet data if available
            if hasattr(frame, "packet") and frame.packet:
                frame_data = bytes(frame.packet)
            # Method 2: Skip decoded frames - we need encoded data for SEI extraction
            elif hasattr(frame, "to_ndarray"):
                pass

            # Extract SEI messages if we have encoded data
            if frame_data:
                extracted_messages = self._extract_sei_from_data(frame_data, frame.time)
                messages.extend(extracted_messages)

        except Exception as e:
            logger.debug(f"Error processing frame for SEI: {e}")

        return messages

    async def process_packet(self, packet: av.Packet) -> List[ReceivedSeiMessage]:
        """
        Process a video packet and extract any SEI messages.

        Args:
            packet: PyAV Packet object

        Returns:
            List of extracted SEI messages
        """
        messages = []

        try:
            packet_data = bytes(packet)
            if packet_data:
                extracted_messages = self._extract_sei_from_data(packet_data, packet.time)
                messages.extend(extracted_messages)

        except Exception as e:
            logger.debug(f"Error processing packet for SEI: {e}")

        return messages

    async def process_packet_data(self, packet_data: bytes, timestamp: Optional[float] = None) -> List[ReceivedSeiMessage]:
        """
        Process raw packet data and extract any SEI messages.

        Args:
            packet_data: Raw packet data bytes
            timestamp: Optional timestamp

        Returns:
            List of extracted SEI messages
        """
        messages = []

        # Only log if we actually find SEI messages (no periodic spam)

        try:
            if packet_data:
                extracted_messages = self._extract_sei_from_data(packet_data, timestamp)
                messages.extend(extracted_messages)

        except Exception as e:
            logger.debug(f"Error processing packet data for SEI: {e}")

        return messages

    def process_packet_data_sync(self, packet_data: bytes, timestamp: Optional[float] = None) -> List[ReceivedSeiMessage]:
        """
        Synchronous version of process_packet_data for use in decoder threads.

        Args:
            packet_data: Raw packet data bytes
            timestamp: Optional timestamp

        Returns:
            List of extracted SEI messages
        """
        messages = []

        try:
            if packet_data:
                extracted_messages = self._extract_sei_from_data(packet_data, timestamp)
                messages.extend(extracted_messages)

        except Exception as e:
            logger.debug(f"Error processing packet data for SEI: {e}")

        return messages

    def _extract_sei_from_data(self, data: bytes, timestamp: Optional[float] = None) -> List[ReceivedSeiMessage]:
        """
        Extract SEI messages from raw H.264 data.

        Args:
            data: Raw H.264 data bytes
            timestamp: Optional timestamp from frame/packet

        Returns:
            List of extracted SEI messages
        """
        messages = []

        try:
            # Detect H.264 format
            is_h264, format_type, nal_type = self._detect_h264_format(data)

            if not is_h264:
                logger.debug(f"🔍 Data is not H.264 format: {len(data)} bytes")
                return messages

            # Convert to Annex B format for easier parsing
            annex_b_data = self._convert_to_annex_b(data, format_type)

            # Find and extract SEI NAL units
            sei_messages = self._find_sei_nal_units(annex_b_data)

            for i, sei_payload in enumerate(sei_messages):
                # Check if this is our target SEI message
                if self._is_target_sei_message(sei_payload):
                    logger.info(f"📡 Found target SEI message with Amazon IVS UUID!")

                    # Extract the actual payload (after UUID)
                    if len(sei_payload) > len(self.TARGET_SEI_UUID):
                        message_payload = sei_payload[len(self.TARGET_SEI_UUID) :]

                        # Create message object
                        message = ReceivedSeiMessage(payload=message_payload, timestamp=time.time(), frame_timestamp=timestamp)

                        # Check for deduplication
                        if self._should_process_message(message):
                            messages.append(message)

                            # Call callback if set
                            if self.message_callback:
                                try:
                                    self.message_callback(message)
                                except Exception as e:
                                    logger.error(f"Error in SEI message callback: {e}")

                            logger.info(f"📡 Received SEI message: {len(message_payload)} bytes")
                        # Skip duplicate messages silently
                    else:
                        logger.warning(f"📡 SEI payload too short: {len(sei_payload)} bytes (UUID is {len(self.TARGET_SEI_UUID)} bytes)")
                else:
                    logger.debug(f"📡 SEI message {i+1} does not match target UUID")
                    # Log first few bytes for debugging
                    if len(sei_payload) >= 16:
                        found_uuid = sei_payload[:16]
                        logger.debug(f"📡 Found UUID: {list(found_uuid)}")
                        logger.debug(f"📡 Target UUID: {list(self.TARGET_SEI_UUID)}")

        except Exception as e:
            logger.error(f"Error extracting SEI from data: {e}")

        return messages

    def _detect_h264_format(self, data: bytes) -> tuple[bool, str, int]:
        """
        Detect if data contains H.264 and determine the format.
        Returns: (is_h264, format_type, nal_type)
        """
        if len(data) < 4:
            return False, "unknown", 0

        # Check for Annex B start codes (0x000001 or 0x00000001)
        if data[:3] == b"\x00\x00\x01":
            nal_header = data[3]
            nal_type = nal_header & 0x1F
            return True, "annex_b", nal_type
        elif data[:4] == b"\x00\x00\x00\x01":
            nal_header = data[4]
            nal_type = nal_header & 0x1F
            return True, "annex_b", nal_type

        # Check for AVCC format (length-prefixed NAL units)
        if len(data) >= 5:
            length = int.from_bytes(data[:4], "big")
            if 0 < length <= len(data) - 4:
                nal_header = data[4]
                forbidden_bit = (nal_header >> 7) & 1
                nal_type = nal_header & 0x1F
                if forbidden_bit == 0 and 1 <= nal_type <= 31:
                    return True, "avcc", nal_type

        # Check for RTP H.264 payload (RFC 6184)
        if len(data) >= 1:
            nal_header = data[0]
            forbidden_bit = (nal_header >> 7) & 1
            nal_type = nal_header & 0x1F

            # Check for valid H.264 NAL unit types
            if forbidden_bit == 0 and 1 <= nal_type <= 31:
                # Additional heuristics for RTP payload
                if nal_type in [1, 5, 6, 7, 8]:  # Common NAL types
                    return True, "rtp", nal_type
                # Check for FU-A fragmentation (type 28)
                elif nal_type == 28 and len(data) >= 2:
                    fu_header = data[1]
                    original_nal_type = fu_header & 0x1F
                    return True, "rtp_fu", original_nal_type

        return False, "unknown", 0

    def _convert_to_annex_b(self, data: bytes, format_type: str) -> bytes:
        """Convert H.264 data to Annex B format for parsing."""
        if format_type == "annex_b":
            return data

        elif format_type == "avcc":
            # Convert AVCC to Annex B
            result = b""
            pos = 0
            while pos < len(data):
                if pos + 4 > len(data):
                    break
                length = int.from_bytes(data[pos : pos + 4], "big")
                if pos + 4 + length > len(data):
                    break
                result += b"\x00\x00\x00\x01" + data[pos + 4 : pos + 4 + length]
                pos += 4 + length
            return result

        elif format_type in ["rtp", "rtp_fu"]:
            # Add start code to RTP payload
            return b"\x00\x00\x00\x01" + data

        return data

    def _remove_emulation_prevention(self, data: bytes) -> bytes:
        """
        Remove emulation prevention bytes from H.264 RBSP data.
        Reverses the process done by the publisher's _do_emulation_prevention method.
        """
        if len(data) < 3:
            return data

        result = bytearray()
        i = 0

        while i < len(data):
            if i < len(data) - 2 and data[i] == 0x00 and data[i + 1] == 0x00 and data[i + 2] == 0x03:
                # Found emulation prevention sequence, skip the 0x03 byte
                result.append(data[i])  # 0x00
                result.append(data[i + 1])  # 0x00
                i += 3  # Skip the 0x03 byte
            else:
                result.append(data[i])
                i += 1

        return bytes(result)

    def _find_sei_nal_units(self, annex_b_data: bytes) -> List[bytes]:
        """
        Find and extract SEI NAL units from Annex B formatted data.

        Args:
            annex_b_data: H.264 data in Annex B format

        Returns:
            List of SEI payloads (without NAL headers)
        """
        sei_payloads = []
        pos = 0
        data_len = len(annex_b_data)
        sei_nal_count = 0

        # Search for SEI NAL units silently

        while pos < data_len - 4:
            # Look for start codes
            start_code_len = 0
            if annex_b_data[pos : pos + 3] == b"\x00\x00\x01":
                start_code_len = 3
            elif pos < data_len - 5 and annex_b_data[pos : pos + 4] == b"\x00\x00\x00\x01":
                start_code_len = 4
            else:
                pos += 1
                continue

            nal_start = pos + start_code_len
            if nal_start >= data_len:
                break

            # Check NAL unit type
            nal_header = annex_b_data[nal_start]
            nal_type = nal_header & 0x1F

            # Found NAL unit - only log if it's SEI

            if nal_type == 6:  # SEI NAL unit
                sei_nal_count += 1
                logger.info(f"📡 Found SEI NAL unit #{sei_nal_count} at position {pos}")

                # Find the end of this NAL unit
                nal_end = self._find_next_start_code(annex_b_data, nal_start + 1)
                if nal_end == -1:
                    nal_end = data_len

                # Extract SEI payload (skip NAL header)
                sei_data = annex_b_data[nal_start + 1 : nal_end]
                # Remove emulation prevention before parsing
                sei_data_clean = self._remove_emulation_prevention(sei_data)

                # Parse SEI message(s) within this NAL unit
                sei_messages = self._parse_sei_messages(sei_data_clean)
                logger.info(f"📡 Extracted {len(sei_messages)} SEI messages from NAL unit")
                sei_payloads.extend(sei_messages)

                pos = nal_end
            else:
                pos = nal_start + 1

        # Only log if we found SEI messages
        if sei_nal_count > 0:
            logger.info(f"📡 Found {sei_nal_count} SEI NAL units with {len(sei_payloads)} messages")

        return sei_payloads

    def _find_next_start_code(self, data: bytes, start_pos: int) -> int:
        """Find the next start code position in the data."""
        pos = start_pos
        while pos < len(data) - 3:
            if data[pos : pos + 3] == b"\x00\x00\x01":
                return pos
            elif pos < len(data) - 4 and data[pos : pos + 4] == b"\x00\x00\x00\x01":
                return pos
            pos += 1
        return -1

    def _parse_sei_messages(self, sei_data: bytes) -> List[bytes]:
        """
        Parse SEI messages from SEI NAL unit data.

        Args:
            sei_data: SEI NAL unit payload (without NAL header)

        Returns:
            List of individual SEI message payloads
        """
        messages = []
        pos = 0

        while pos < len(sei_data):
            # Parse SEI message type
            if pos >= len(sei_data):
                break

            sei_type = 0
            while pos < len(sei_data) and sei_data[pos] == 0xFF:
                sei_type += 255
                pos += 1
            if pos < len(sei_data):
                sei_type += sei_data[pos]
                pos += 1

            # Parse SEI message size
            if pos >= len(sei_data):
                break

            sei_size = 0
            while pos < len(sei_data) and sei_data[pos] == 0xFF:
                sei_size += 255
                pos += 1
            if pos < len(sei_data):
                sei_size += sei_data[pos]
                pos += 1

            # Extract SEI payload
            if pos + sei_size <= len(sei_data):
                sei_payload = sei_data[pos : pos + sei_size]

                # Check if this is user_data_unregistered (type 5)
                if sei_type == 5:
                    messages.append(sei_payload)

                pos += sei_size
            else:
                break

            # Skip trailing bits if we hit them
            if pos < len(sei_data) and sei_data[pos] == 0x80:
                break

        return messages

    def _is_target_sei_message(self, sei_payload: bytes) -> bool:
        """
        Check if the SEI payload contains our target UUID.

        Args:
            sei_payload: SEI message payload

        Returns:
            True if this is our target message type
        """
        if len(sei_payload) < len(self.TARGET_SEI_UUID):
            return False

        return sei_payload[: len(self.TARGET_SEI_UUID)] == self.TARGET_SEI_UUID

    def _should_process_message(self, message: ReceivedSeiMessage) -> bool:
        """
        Check if we should process this message (deduplication).

        Args:
            message: The received SEI message

        Returns:
            True if message should be processed
        """
        try:
            # Try to extract timestamp from payload for deduplication
            payload_str = message.payload.decode("utf-8")
            payload_data = json.loads(payload_str)

            if "timestamp" in payload_data:
                message_id = f"{payload_data['timestamp']}_{hash(message.payload)}"
                current_time = time.time()

                # Clean up old cache entries
                if current_time - self._last_cleanup > self._cache_cleanup_interval:
                    self._cleanup_message_cache(current_time)

                # Check if we've seen this message recently
                if message_id in self._message_cache:
                    return False

                # Add to cache
                self._message_cache[message_id] = current_time
                message.message_id = message_id

        except (UnicodeDecodeError, json.JSONDecodeError):
            # For non-JSON messages, use payload hash
            message_id = f"{message.timestamp}_{hash(message.payload)}"
            message.message_id = message_id

        return True

    def _cleanup_message_cache(self, current_time: float):
        """Clean up old entries from the message cache."""
        cutoff_time = current_time - 60.0  # Keep entries for 60 seconds

        keys_to_remove = [key for key, timestamp in self._message_cache.items() if timestamp < cutoff_time]

        for key in keys_to_remove:
            del self._message_cache[key]

        self._last_cleanup = current_time

        if keys_to_remove:
            logger.debug(f"Cleaned up {len(keys_to_remove)} old SEI message cache entries")

    async def get_received_messages(self) -> List[ReceivedSeiMessage]:
        """Get all received messages."""
        async with self._lock:
            return self.received_messages.copy()

    async def clear_received_messages(self):
        """Clear the received messages list."""
        async with self._lock:
            cleared_count = len(self.received_messages)
            self.received_messages.clear()
            if cleared_count > 0:
                logger.info(f"🗑️ Cleared {cleared_count} received SEI messages")


# Convenience function for simple SEI message logging
def log_sei_message(message: ReceivedSeiMessage):
    """Simple callback function that logs received SEI messages."""
    try:
        payload_dict = message.to_dict()
        payload_data = payload_dict["payload"]

        if isinstance(payload_data, dict):
            msg_type = payload_data.get("type", "unknown")
            content = payload_data.get("content", payload_data.get("text", str(payload_data)[:100]))
            logger.info(f"📡 SEI Message [{msg_type}]: {content}")
        else:
            logger.info(f"📡 SEI Message [binary]: {len(message.payload)} bytes")

    except Exception as e:
        logger.info(f"📡 SEI Message [raw]: {len(message.payload)} bytes (parse error: {e})")


# Test function for development/debugging
async def test_sei_extraction():
    """Test function to verify SEI extraction works with sample data"""
    import json

    # Create test SEI subscriber
    received_messages = []

    def test_callback(message):
        received_messages.append(message)
        log_sei_message(message)

    subscriber = SeiSubscriber(message_callback=test_callback)

    # Create sample SEI message data
    test_payload = json.dumps({"type": "test_message", "content": "Hello from SEI test!", "timestamp": time.time()}).encode("utf-8")

    # Create a mock SEI NAL unit (simplified for testing)
    uuid = subscriber.TARGET_SEI_UUID
    full_payload = uuid + test_payload

    # Create a simple Annex B formatted H.264 data with SEI
    # This is a minimal example - real H.264 data would be more complex
    start_code = b"\x00\x00\x00\x01"
    nal_header = bytes([0x06])  # SEI NAL unit type
    sei_type = bytes([0x05])  # User data unregistered
    sei_size = bytes([len(full_payload)])  # Payload size
    trailing_bits = b"\x80"

    test_h264_data = start_code + nal_header + sei_type + sei_size + full_payload + trailing_bits

    logger.info(f"🧪 Testing SEI extraction with {len(test_h264_data)} bytes of test data")

    # Test extraction
    messages = await subscriber.process_packet_data(test_h264_data)

    logger.info(f"🧪 Test completed: extracted {len(messages)} messages")

    return len(messages) > 0
