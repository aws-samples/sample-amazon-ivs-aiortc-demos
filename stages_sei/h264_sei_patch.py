#!/usr/bin/env python3

import logging
import threading
from typing import Optional
import av

logger = logging.getLogger(__name__)

# Global SEI publisher reference
_global_sei_publisher: Optional["SeiPublisher"] = None
_sei_lock = threading.Lock()


def set_global_sei_publisher(sei_publisher):
    """Set the global SEI publisher for the H.264 encoder patch"""
    global _global_sei_publisher
    with _sei_lock:
        _global_sei_publisher = sei_publisher
        logger.debug("📡 Global SEI publisher set for H.264 encoder patch")


def get_global_sei_publisher():
    """Get the global SEI publisher"""
    global _global_sei_publisher
    with _sei_lock:
        return _global_sei_publisher


def add_emulation_prevention_bytes(data: bytes) -> bytes:
    """
    Add emulation prevention bytes to H.264 RBSP data.
    Insert 0x03 byte after any 0x00 0x00 sequence to prevent start code emulation.
    """
    result = bytearray()
    zero_count = 0

    for byte in data:
        if zero_count == 2 and byte <= 0x03:
            result.append(0x03)
            zero_count = 0

        result.append(byte)

        if byte == 0x00:
            zero_count += 1
        else:
            zero_count = 0

    return bytes(result)


def create_h264_sei_nal_unit(payload: bytes) -> bytes:
    """
    Create a properly formatted H.264 SEI NAL unit according to H.264 specification.
    Format: [start_code][NAL_header][SEI_type][SEI_size][UUID][payload][rbsp_trailing_bits]
    """
    try:
        # UUID for user data unregistered (16 bytes) - Amazon IVS UUID: [158, 80, 78, 165, 238, 90, 79, 2, 148, 159, 176, 51, 163, 118, 141, 162]
        uuid = bytes([158, 80, 78, 165, 238, 90, 79, 2, 148, 159, 176, 51, 163, 118, 141, 162])
        # SEI payload type 5 = user_data_unregistered (H.264 spec D.1.5)
        sei_type = 5

        # Calculate total payload size (UUID + actual payload)
        total_payload = uuid + payload
        payload_size = len(total_payload)

        # Encode payload size using ff_byte method (H.264 spec 7.3.2.3.1)
        size_bytes = []
        remaining_size = payload_size
        while remaining_size >= 255:
            size_bytes.append(0xFF)
            remaining_size -= 255
        size_bytes.append(remaining_size)

        # Build the SEI message (H.264 spec 7.3.2.3)
        sei_message = bytes([sei_type]) + bytes(size_bytes) + total_payload

        # Add RBSP trailing bits for byte alignment (H.264 spec 7.3.2.11)
        if len(sei_message) % 8 != 0:
            sei_message += b"\x80"  # 10000000 in binary
        else:
            sei_message += b"\x80"

        # Apply emulation prevention to the RBSP data (H.264 spec 7.4.1)
        sei_rbsp = add_emulation_prevention_bytes(sei_message)

        # Create NAL unit header (H.264 spec 7.3.1)
        # For SEI: forbidden=0, ref_idc=0, type=6
        nal_header = 0x06  # 00000110 in binary

        # Create complete NAL unit with Annex B start code (H.264 spec B.1.1)
        start_code = b"\x00\x00\x01"
        nal_unit = start_code + bytes([nal_header]) + sei_rbsp

        logger.debug(f"📡 Created H.264 SEI NAL unit: {len(nal_unit)} bytes (payload: {len(payload)} bytes)")
        return nal_unit

    except Exception as e:
        logger.error(f"❌ Failed to create SEI NAL unit: {e}")
        return b""


def detect_h264_format(data: bytes) -> tuple[bool, str, int]:
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


def convert_to_annex_b(data: bytes, format_type: str) -> bytes:
    """Convert H.264 data to Annex B format for SEI injection."""
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


def convert_from_annex_b(data: bytes, original_format: str) -> bytes:
    """Convert Annex B format back to original format after SEI injection."""
    if original_format == "annex_b":
        return data

    elif original_format == "avcc":
        # Convert back to AVCC format
        result = b""
        pos = 0
        while pos < len(data):
            # Find next start code
            start_code_pos = -1
            for i in range(pos, len(data) - 3):
                if data[i : i + 3] == b"\x00\x00\x01":
                    start_code_pos = i
                    break
                elif data[i : i + 4] == b"\x00\x00\x00\x01":
                    start_code_pos = i
                    break

            if start_code_pos == -1:
                break

            # Find start of NAL unit
            nal_start = start_code_pos + 4 if data[start_code_pos : start_code_pos + 4] == b"\x00\x00\x00\x01" else start_code_pos + 3

            # Find next start code or end of data
            next_start = len(data)
            for i in range(nal_start + 1, len(data) - 3):
                if data[i : i + 3] == b"\x00\x00\x01" or data[i : i + 4] == b"\x00\x00\x00\x01":
                    next_start = i
                    break

            # Extract NAL unit
            nal_unit = data[nal_start:next_start]
            if nal_unit:
                result += len(nal_unit).to_bytes(4, "big") + nal_unit

            pos = next_start

        return result

    elif original_format in ["rtp", "rtp_fu"]:
        # Remove start code for RTP format
        if data.startswith(b"\x00\x00\x00\x01"):
            return data[4:]
        elif data.startswith(b"\x00\x00\x01"):
            return data[3:]
        return data

    return data


def find_access_unit_boundary(bitstream: bytes) -> int:
    """
    Find the position where we should insert SEI NAL units.
    SEI must come before VCL NAL units in the same Access Unit.
    Returns the position after SPS/PPS but before first VCL NAL unit.
    Assumes bitstream is in Annex B format.
    """
    pos = 0
    last_non_vcl_end = 0

    while pos < len(bitstream) - 4:
        # Look for start codes (0x000001 or 0x00000001)
        if bitstream[pos : pos + 3] == b"\x00\x00\x01":
            nal_start = pos + 3
            start_code_len = 3
        elif bitstream[pos : pos + 4] == b"\x00\x00\x00\x01":
            nal_start = pos + 4
            start_code_len = 4
        else:
            pos += 1
            continue

        if nal_start >= len(bitstream):
            break

        # Get NAL unit type from header
        nal_header = bitstream[nal_start]
        nal_type = nal_header & 0x1F

        # VCL NAL units are types 1-5 (coded slice NAL units)
        if 1 <= nal_type <= 5:
            return pos

        # Non-VCL NAL units (SPS=7, PPS=8, SEI=6, etc.)
        elif nal_type in [6, 7, 8, 9, 10, 11, 12]:
            # Find the end of this NAL unit
            next_pos = pos + start_code_len + 1
            while next_pos < len(bitstream) - 3:
                if bitstream[next_pos : next_pos + 3] == b"\x00\x00\x01" or bitstream[next_pos : next_pos + 4] == b"\x00\x00\x00\x01":
                    break
                next_pos += 1

            last_non_vcl_end = next_pos
            pos = next_pos
            continue

        # Skip this NAL unit and continue
        pos = nal_start + 1

    # If no VCL NAL unit found, insert after the last non-VCL NAL unit
    return last_non_vcl_end if last_non_vcl_end > 0 else 0


def inject_sei_into_bitstream(original_bitstream: bytes) -> bytes:
    """
    Inject properly formatted H.264 SEI NAL units into bitstream.
    Handles multiple H.264 formats: Annex B, AVCC, and RTP payloads.
    """
    # Detect H.264 format
    is_h264, format_type, nal_type = detect_h264_format(original_bitstream)

    if not is_h264:
        return original_bitstream

    sei_publisher = get_global_sei_publisher()
    if not sei_publisher or not hasattr(sei_publisher, "message_queue"):
        return original_bitstream

    # Check for queued SEI messages
    queue_size = len(sei_publisher.message_queue)
    if queue_size == 0:
        return original_bitstream

    # Get messages to process
    messages_to_process = sei_publisher.message_queue.copy()
    sei_publisher.message_queue.clear()

    if not messages_to_process:
        return original_bitstream

    logger.debug(f"📡 SEI INJECTION: Processing {len(messages_to_process)} messages into {format_type} bitstream")

    try:
        # Convert to Annex B format for processing
        annex_b_data = convert_to_annex_b(original_bitstream, format_type)

        # Find the correct insertion point (before VCL NAL units)
        insertion_point = find_access_unit_boundary(annex_b_data)

        # Create all SEI NAL units (always in Annex B format)
        sei_nal_units = b""
        for message in messages_to_process:
            sei_nal_unit = create_h264_sei_nal_unit(message.payload)
            if sei_nal_unit:
                for _ in range(message.repeat_count):
                    sei_nal_units += sei_nal_unit

        if sei_nal_units:
            # Insert SEI NAL units at the correct position in Annex B format
            modified_annex_b = annex_b_data[:insertion_point] + sei_nal_units + annex_b_data[insertion_point:]

            # Convert back to original format
            final_data = convert_from_annex_b(modified_annex_b, format_type)

            logger.debug(f"📡 SEI INJECTION SUCCESS: {len(original_bitstream)} -> {len(final_data)} bytes ({format_type} format)")
            return final_data

    except Exception as e:
        logger.error(f"❌ Error in SEI injection: {e}")

    return original_bitstream


def patch_h264_encoder():
    """
    Monkey patch the aiortc H.264 encoder to inject SEI data.
    """
    try:
        from aiortc.codecs.h264 import H264Encoder

        # Patch the _encode_frame method (raw H.264 bitstreams)
        if hasattr(H264Encoder, "_encode_frame"):
            original_encode_frame = H264Encoder._encode_frame

            def patched_encode_frame(self, frame, force_keyframe: bool):
                """Patched version of _encode_frame that injects SEI data into raw H.264 bitstreams."""
                for bitstream_chunk in original_encode_frame(self, frame, force_keyframe):
                    modified_chunk = inject_sei_into_bitstream(bitstream_chunk)
                    yield modified_chunk

            H264Encoder._encode_frame = patched_encode_frame
            logger.debug("✅ H.264 encoder._encode_frame successfully patched for SEI injection")
            return True

    except Exception as e:
        logger.error(f"❌ Failed to patch H.264 encoder: {e}")

    # Try alternative approach - patch PyAV directly
    try:
        # Hook into av.CodecContext.encode
        if hasattr(av.CodecContext, "encode"):
            original_av_encode = av.CodecContext.encode

            def patched_av_encode(self, frame=None):
                """Patched PyAV encode method that intercepts H.264 packets"""
                packet_iterator = original_av_encode(self, frame)
                is_h264 = hasattr(self, "name") and self.name == "libx264"

                for packet in packet_iterator:
                    if is_h264 and packet:
                        try:
                            packet_bytes = bytes(packet)
                            if len(packet_bytes) >= 4:
                                is_h264_data, _, _ = detect_h264_format(packet_bytes)
                                if is_h264_data:
                                    logger.debug(f"🎯 INTERCEPTED H.264 PACKET: {len(packet_bytes)} bytes")
                        except Exception:
                            pass
                    yield packet

            # Note: This may fail due to immutable type, but we try anyway
            try:
                av.CodecContext.encode = patched_av_encode
                logger.debug("✅ PyAV CodecContext.encode patched")
            except TypeError:
                pass  # Expected for immutable types

    except Exception:
        pass

    # Try to patch PyAV Packet methods
    try:
        from av.packet import Packet

        # Patch __bytes__ method
        if hasattr(Packet, "__bytes__"):
            original_packet_bytes = Packet.__bytes__

            def patched_packet_bytes(self):
                """Patched Packet.__bytes__ to inject SEI data"""
                original_bytes = original_packet_bytes(self)
                is_h264, _, _ = detect_h264_format(original_bytes)

                if is_h264:
                    logger.debug(f"🎯 INTERCEPTED H.264 PACKET (__bytes__): {len(original_bytes)} bytes")
                    modified_bytes = inject_sei_into_bitstream(original_bytes)
                    if len(modified_bytes) != len(original_bytes):
                        logger.debug(f"📡 SEI injected into packet bytes: {len(original_bytes)} -> {len(modified_bytes)} bytes")
                        return modified_bytes

                return original_bytes

            Packet.__bytes__ = patched_packet_bytes
            logger.debug("✅ PyAV Packet.__bytes__ patched")

        # Patch to_bytes method if it exists
        if hasattr(Packet, "to_bytes"):
            original_to_bytes = Packet.to_bytes

            def patched_to_bytes(self):
                """Patched Packet.to_bytes() to inject SEI data"""
                original_bytes = original_to_bytes(self)
                is_h264, _, _ = detect_h264_format(original_bytes)

                if is_h264:
                    logger.debug(f"🎯 INTERCEPTED H.264 PACKET (to_bytes): {len(original_bytes)} bytes")
                    modified_bytes = inject_sei_into_bitstream(original_bytes)
                    if len(modified_bytes) != len(original_bytes):
                        logger.debug(f"📡 SEI injected via to_bytes: {len(original_bytes)} -> {len(modified_bytes)} bytes")
                        return modified_bytes

                return original_bytes

            Packet.to_bytes = patched_to_bytes
            logger.debug("✅ PyAV Packet.to_bytes patched")

    except Exception:
        pass

    return False


# Auto-apply patch when module is imported
logger.debug("🔧 h264_sei_patch module imported")
patch_result = patch_h264_encoder()
logger.debug(f"🔧 H.264 encoder patch result: {patch_result}")
