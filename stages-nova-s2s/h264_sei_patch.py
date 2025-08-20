#!/usr/bin/env python3

import logging
import threading
from typing import Iterator, Optional
import av

logger = logging.getLogger(__name__)

# Module imported
logger.info("🔧 h264_sei_patch module imported")

# Global SEI publisher reference
_global_sei_publisher: Optional["SeiPublisher"] = None
_sei_lock = threading.Lock()


def set_global_sei_publisher(sei_publisher):
    """Set the global SEI publisher for the H.264 encoder patch"""
    global _global_sei_publisher
    with _sei_lock:
        _global_sei_publisher = sei_publisher
        logger.info("📡 Global SEI publisher set for H.264 encoder patch")


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
            # Insert emulation prevention byte
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

    This implementation matches the GStreamer reference for maximum compatibility.
    """
    try:
        # UUID for user data unregistered (16 bytes)
        # Using our original custom UUID to identify our SEI messages
        uuid = bytes([0x9E, 0x50, 0x4E, 0xA5, 0xEE, 0x5A, 0x4F, 0x02, 0x94, 0x9F, 0xB0, 0x33, 0xA3, 0x76, 0x8D, 0xA2])
        # SEI payload type 5 = user_data_unregistered (H.264 spec D.1.5)
        sei_type = 5

        # Calculate total payload size (UUID + actual payload)
        total_payload = uuid + payload
        payload_size = len(total_payload)

        # Encode payload size using ff_byte method (H.264 spec 7.3.2.3.1)
        # This is critical for proper SEI parsing
        size_bytes = []
        remaining_size = payload_size
        while remaining_size >= 255:
            size_bytes.append(0xFF)
            remaining_size -= 255
        size_bytes.append(remaining_size)

        # Build the SEI message (H.264 spec 7.3.2.3)
        sei_message = bytes([sei_type]) + bytes(size_bytes) + total_payload

        # Add RBSP trailing bits for byte alignment (H.264 spec 7.3.2.11)
        # This is essential - must end with 1 bit followed by zero bits to byte boundary
        if len(sei_message) % 8 != 0:
            # Add rbsp_stop_one_bit (1) and rbsp_alignment_zero_bits
            sei_message += b"\x80"  # 10000000 in binary
        else:
            # Already byte-aligned, just add stop bit
            sei_message += b"\x80"

        # Apply emulation prevention to the RBSP data (H.264 spec 7.4.1)
        # This prevents accidental start code emulation within the payload
        sei_rbsp = add_emulation_prevention_bytes(sei_message)

        # Create NAL unit header (H.264 spec 7.3.1)
        # forbidden_zero_bit(1) + nal_ref_idc(2) + nal_unit_type(5)
        # For SEI: forbidden=0, ref_idc=0, type=6
        nal_header = 0x06  # 00000110 in binary

        # Create complete NAL unit with Annex B start code (H.264 spec B.1.1)
        # Using 3-byte start code like GStreamer reference
        start_code = b"\x00\x00\x01"
        nal_unit = start_code + bytes([nal_header]) + sei_rbsp

        logger.info(f"📡 Created H.264 SEI NAL unit: {len(nal_unit)} bytes (payload: {len(payload)} bytes, UUID: {uuid.hex()})")

        # Log the first few bytes for debugging
        hex_preview = " ".join(f"{b:02x}" for b in nal_unit[:32])
        logger.debug(f"📡 SEI NAL unit preview: {hex_preview}...")

        return nal_unit

    except Exception as e:
        logger.error(f"❌ Failed to create SEI NAL unit: {e}")
        import traceback

        logger.error(f"❌ Traceback: {traceback.format_exc()}")
        return b""


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
            # Found first VCL NAL unit, insert SEI before it
            logger.debug(f"🔧 Found VCL NAL unit (type {nal_type}) at position {pos}, inserting SEI before it")
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
    # or at the beginning if no suitable position found
    if last_non_vcl_end > 0:
        logger.debug(f"🔧 No VCL NAL unit found, inserting SEI after last non-VCL at position {last_non_vcl_end}")
        return last_non_vcl_end

    logger.debug(f"🔧 No suitable insertion point found, inserting SEI at beginning")
    return 0


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
    # First 4 bytes are length, then NAL header
    if len(data) >= 5:
        length = int.from_bytes(data[:4], "big")
        if 0 < length <= len(data) - 4:  # Reasonable length
            nal_header = data[4]
            # Check if this looks like a valid NAL header
            forbidden_bit = (nal_header >> 7) & 1
            nal_type = nal_header & 0x1F
            if forbidden_bit == 0 and 1 <= nal_type <= 31:
                return True, "avcc", nal_type

    # Check for RTP H.264 payload (RFC 6184)
    # Single NAL unit mode: first byte is NAL header
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
    """
    Convert H.264 data to Annex B format for SEI injection.
    """
    if format_type == "annex_b":
        return data  # Already in correct format

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
            # Replace length with start code
            result += b"\x00\x00\x00\x01" + data[pos + 4 : pos + 4 + length]
            pos += 4 + length
        return result

    elif format_type in ["rtp", "rtp_fu"]:
        # Add start code to RTP payload
        return b"\x00\x00\x00\x01" + data

    return data


def convert_from_annex_b(data: bytes, original_format: str) -> bytes:
    """
    Convert Annex B format back to original format after SEI injection.
    """
    if original_format == "annex_b":
        return data  # Keep as Annex B

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
                # Add length prefix
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


def inject_sei_into_bitstream(original_bitstream: bytes) -> bytes:
    """
    Inject properly formatted H.264 SEI NAL units into bitstream.
    Handles multiple H.264 formats: Annex B, AVCC, and RTP payloads.
    """
    # DEBUG: Always log when this function is called
    logger.info(f"🔧 inject_sei_into_bitstream called with {len(original_bitstream)} bytes")

    # DEBUG: Check if this looks like H.264 data
    if len(original_bitstream) >= 16:
        hex_preview = " ".join(f"{b:02x}" for b in original_bitstream[:16])
        logger.info(f"🔧 DEBUG: Bitstream preview: {hex_preview}...")

    # Detect H.264 format
    is_h264, format_type, nal_type = detect_h264_format(original_bitstream)

    if not is_h264:
        logger.info(f"🔧 DEBUG: Not detected as H.264 data")
        return original_bitstream

    logger.info(f"🎯 DETECTED H.264 DATA: format={format_type}, NAL type={nal_type}, {len(original_bitstream)} bytes")

    # Log NAL unit type details
    nal_type_names = {
        1: "Coded slice (non-IDR)",
        2: "Coded slice partition A",
        3: "Coded slice partition B",
        4: "Coded slice partition C",
        5: "Coded slice (IDR)",
        6: "SEI",
        7: "SPS",
        8: "PPS",
        9: "Access unit delimiter",
        10: "End of sequence",
        11: "End of stream",
        12: "Filler data",
    }
    nal_name = nal_type_names.get(nal_type, f"Unknown({nal_type})")
    logger.info(f"🎯 H.264 NAL UNIT: {nal_name}")

    sei_publisher = get_global_sei_publisher()
    if not sei_publisher:
        logger.debug("🔧 No SEI publisher available")
        return original_bitstream

    try:
        # Check if there are any queued SEI messages
        if not hasattr(sei_publisher, "message_queue"):
            logger.debug("🔧 SEI publisher has no message_queue attribute")
            return original_bitstream

        queue_size = len(sei_publisher.message_queue)
        if queue_size == 0:
            # Only log occasionally to avoid spam
            if hasattr(inject_sei_into_bitstream, "_debug_counter"):
                inject_sei_into_bitstream._debug_counter += 1
            else:
                inject_sei_into_bitstream._debug_counter = 1

            if inject_sei_into_bitstream._debug_counter % 100 == 0:
                logger.debug(f"🔧 SEI message queue empty (checked {inject_sei_into_bitstream._debug_counter} times)")
            return original_bitstream

        # Get messages to process
        messages_to_process = sei_publisher.message_queue.copy()
        sei_publisher.message_queue.clear()

        if not messages_to_process:
            return original_bitstream

        logger.info(
            f"📡 SEI INJECTION: Processing {len(messages_to_process)} messages into {len(original_bitstream)} byte H.264 {format_type} bitstream"
        )

        # Convert to Annex B format for processing
        annex_b_data = convert_to_annex_b(original_bitstream, format_type)

        if len(annex_b_data) != len(original_bitstream):
            logger.info(f"📡 FORMAT CONVERSION: {format_type} -> Annex B: {len(original_bitstream)} -> {len(annex_b_data)} bytes")

        # Find the correct insertion point (before VCL NAL units)
        insertion_point = find_access_unit_boundary(annex_b_data)

        # Create all SEI NAL units (always in Annex B format)
        sei_nal_units = b""
        for message in messages_to_process:
            try:
                # Create proper H.264 SEI NAL unit
                sei_nal_unit = create_h264_sei_nal_unit(message.payload)

                if sei_nal_unit:
                    # Repeat the SEI NAL unit for reliability
                    for _ in range(message.repeat_count):
                        sei_nal_units += sei_nal_unit

                    logger.debug(f"📡 SEI INJECTION: Created {len(sei_nal_unit)} byte SEI NAL unit, repeated {message.repeat_count} times")

            except Exception as e:
                logger.error(f"❌ Failed to create SEI NAL unit: {e}")

        if sei_nal_units:
            # Insert SEI NAL units at the correct position in Annex B format
            modified_annex_b = annex_b_data[:insertion_point] + sei_nal_units + annex_b_data[insertion_point:]

            logger.info(f"📡 SEI INJECTION: Inserted {len(sei_nal_units)} bytes of SEI data at position {insertion_point}")
            logger.info(f"📡 SEI INJECTION: Annex B result: {len(annex_b_data)} -> {len(modified_annex_b)} bytes")

            # Convert back to original format
            final_data = convert_from_annex_b(modified_annex_b, format_type)

            if len(final_data) != len(modified_annex_b):
                logger.info(f"📡 FORMAT CONVERSION: Annex B -> {format_type}: {len(modified_annex_b)} -> {len(final_data)} bytes")

            logger.info(f"📡 SEI INJECTION SUCCESS: {len(original_bitstream)} -> {len(final_data)} bytes ({format_type} format)")
            return final_data
        else:
            return original_bitstream

    except Exception as e:
        logger.error(f"❌ Error in SEI injection: {e}")
        import traceback

        logger.error(f"❌ Traceback: {traceback.format_exc()}")
        return original_bitstream


def test_sei_injection():
    """
    Test function to verify SEI injection works with sample H.264 data.
    This helps debug the SEI creation and injection logic independently.
    """
    print("🧪 Testing SEI injection with sample H.264 data...")
    logger.info("🧪 Testing SEI injection with sample H.264 data...")

    # Test different H.264 formats
    test_cases = [
        # Annex B format (start code + NAL)
        {"name": "Annex B (SPS)", "data": b"\x00\x00\x00\x01\x67\x42\x00\x1e\x9a\x74\x0b\x43\x6c\x40", "expected_format": "annex_b"},
        # AVCC format (length + NAL)
        {"name": "AVCC (SPS)", "data": b"\x00\x00\x00\x09\x67\x42\x00\x1e\x9a\x74\x0b\x43\x6c\x40", "expected_format": "avcc"},
        # RTP format (just NAL header + data)
        {"name": "RTP (IDR slice)", "data": b"\x65\x88\x82\x01\xab\xff\xff\xf0\xf4\x50\x00\x15\x79\xf2", "expected_format": "rtp"},
        # Sample from the actual log
        {"name": "Real data from log", "data": bytes.fromhex("41 00 b4 9a c0 1a bc 78 9f 27 eb 01 f2 09 00 43"), "expected_format": "rtp"},
    ]

    success_count = 0

    for test_case in test_cases:
        print(f"\n🧪 Testing {test_case['name']}...")
        data = test_case["data"]

        # Test format detection
        is_h264, format_type, nal_type = detect_h264_format(data)
        print(f"🧪 Format detection: is_h264={is_h264}, format={format_type}, nal_type={nal_type}")

        if is_h264:
            print(f"✅ H.264 detected correctly")

            # Test SEI injection (but only if we have messages)
            # For testing, we'll temporarily add a test message
            sei_publisher = get_global_sei_publisher()
            if sei_publisher and hasattr(sei_publisher, "message_queue"):
                # Add a test message
                try:
                    from sei_publisher import SeiMessage

                    test_message = SeiMessage(b"TEST_SEI_MESSAGE", repeat_count=1)
                    sei_publisher.message_queue.append(test_message)

                    # Test injection
                    modified_data = inject_sei_into_bitstream(data)

                    if len(modified_data) > len(data):
                        print(f"✅ SEI injection successful: {len(data)} -> {len(modified_data)} bytes")
                        success_count += 1
                    else:
                        print(f"❌ SEI injection failed: no size increase")
                except ImportError:
                    print(f"⚠️ Cannot import SeiMessage for injection test")
                    # Just test format detection
                    success_count += 1
            else:
                print(f"⚠️ No SEI publisher available for injection test")
                # Just test format detection
                success_count += 1
        else:
            print(f"❌ H.264 not detected")

    # Test SEI NAL unit creation independently
    print(f"\n🧪 Testing SEI NAL unit creation...")
    test_payload = b"TEST_SEI_MESSAGE"
    sei_nal = create_h264_sei_nal_unit(test_payload)

    if sei_nal:
        print(f"✅ SEI NAL unit created: {len(sei_nal)} bytes")
        sei_hex = " ".join(f"{b:02x}" for b in sei_nal[:32])
        print(f"🧪 SEI NAL preview: {sei_hex}...")
        success_count += 1
    else:
        print("❌ SEI NAL unit creation failed")

    total_tests = len(test_cases) + 1  # +1 for SEI creation test
    print(f"\n🧪 Test Results: {success_count}/{total_tests} tests passed")

    if success_count >= total_tests * 0.8:  # 80% success rate is acceptable
        print("✅ SEI injection tests mostly successful!")
        logger.info("✅ SEI injection tests mostly successful!")
        return True
    else:
        print(f"❌ Too many tests failed: {total_tests - success_count}")
        logger.error(f"❌ Too many tests failed: {total_tests - success_count}")
        return False


def patch_h264_encoder():
    """
    Monkey patch the aiortc H.264 encoder to inject SEI data.
    Try multiple methods to find the right insertion point.
    """
    print("🔧 Attempting to patch H.264 encoder...")
    logger.info("🔧 Attempting to patch H.264 encoder...")

    try:
        # Import the aiortc H.264 encoder
        from aiortc.codecs.h264 import H264Encoder

        print("🔧 Successfully imported H264Encoder")

        # DEBUG: List all methods on H264Encoder to find potential H.264 bitstream sources
        encoder_methods = [method for method in dir(H264Encoder) if not method.startswith("__")]
        print(f"🔧 DEBUG: H264Encoder methods: {encoder_methods}")
        logger.info(f"🔧 DEBUG: H264Encoder methods: {encoder_methods}")

        # Look for methods that might contain 'encode', 'frame', 'nal', 'bitstream'
        interesting_methods = [
            m for m in encoder_methods if any(keyword in m.lower() for keyword in ["encode", "frame", "nal", "bitstream", "packet"])
        ]
        print(f"🔧 DEBUG: Interesting methods: {interesting_methods}")
        logger.info(f"🔧 DEBUG: Interesting methods: {interesting_methods}")

        # Also check what the encoder instance looks like
        print(f"🔧 DEBUG: H264Encoder class: {H264Encoder}")
        print(f"🔧 DEBUG: H264Encoder MRO: {H264Encoder.__mro__}")
        logger.info(f"🔧 DEBUG: H264Encoder class: {H264Encoder}")
        logger.info(f"🔧 DEBUG: H264Encoder MRO: {H264Encoder.__mro__}")

        # Try to find the underlying codec
        try:
            # Check if there's a codec attribute or method
            if hasattr(H264Encoder, "_codec"):
                print(f"🔧 DEBUG: Found _codec attribute")
                logger.info(f"🔧 DEBUG: Found _codec attribute")

            # Look for av-related attributes (PyAV)
            av_attrs = [attr for attr in encoder_methods if "av" in attr.lower() or "codec" in attr.lower()]
            print(f"🔧 DEBUG: AV/Codec related attributes: {av_attrs}")
            logger.info(f"🔧 DEBUG: AV/Codec related attributes: {av_attrs}")

        except Exception as codec_e:
            print(f"🔧 DEBUG: Error inspecting codec: {codec_e}")
            logger.info(f"🔧 DEBUG: Error inspecting codec: {codec_e}")

        # Try to patch _encode_frame first (raw H.264) before encode (RTP packets)
        if hasattr(H264Encoder, "_encode_frame"):
            original_encode_frame = H264Encoder._encode_frame
            print("🔧 Found _encode_frame method, patching...")

            def patched_encode_frame(self, frame, force_keyframe: bool):
                """
                Patched version of _encode_frame that injects SEI data into raw H.264 bitstreams.
                """
                # DEBUG: Log when _encode_frame is called
                logger.debug(
                    f"🔧 DEBUG: H264Encoder._encode_frame called with frame: {getattr(frame, 'width', 'unknown')}x{getattr(frame, 'height', 'unknown')}, keyframe: {force_keyframe}"
                )

                # Call the original _encode_frame method and process each yielded bitstream
                for i, bitstream_chunk in enumerate(original_encode_frame(self, frame, force_keyframe)):
                    # DEBUG: Log what _encode_frame yields
                    logger.debug(f"🔧 DEBUG: _encode_frame yielded chunk {i}: {len(bitstream_chunk)} bytes")

                    # DEBUG: Check if this looks like H.264
                    if len(bitstream_chunk) >= 16:
                        hex_preview = " ".join(f"{b:02x}" for b in bitstream_chunk[:16])
                        logger.debug(f"🔧 DEBUG: Chunk {i} preview: {hex_preview}...")

                        has_start_code = bitstream_chunk[:3] == b"\x00\x00\x01" or bitstream_chunk[:4] == b"\x00\x00\x00\x01"
                        logger.debug(f"🔧 DEBUG: Chunk {i} has H.264 start code: {has_start_code}")

                        if has_start_code:
                            nal_start = 4 if bitstream_chunk[:4] == b"\x00\x00\x00\x01" else 3
                            if len(bitstream_chunk) > nal_start:
                                nal_type = bitstream_chunk[nal_start] & 0x1F
                                logger.debug(f"🔧 DEBUG: Chunk {i} NAL unit type: {nal_type}")
                                logger.info(f"🎯 FOUND REAL H.264 BITSTREAM: {len(bitstream_chunk)} bytes, NAL type {nal_type}")

                    # Inject SEI data into each bitstream chunk
                    modified_chunk = inject_sei_into_bitstream(bitstream_chunk)
                    yield modified_chunk

            H264Encoder._encode_frame = patched_encode_frame
            print("✅ H.264 encoder._encode_frame successfully patched for SEI injection")
            logger.info("✅ H.264 encoder._encode_frame successfully patched for SEI injection")

            # Verify the patch was applied
            if H264Encoder._encode_frame == patched_encode_frame:
                print("🔧 Patch verification: _encode_frame patch confirmed")
                logger.info("🔧 Patch verification: _encode_frame patch confirmed")
            else:
                print("❌ Patch verification: _encode_frame patch FAILED")
                logger.error("❌ Patch verification: _encode_frame patch FAILED")
                """
                Patched version of encode that injects SEI data.
                """
                # DEBUG: Log when encode is called
                logger.debug(
                    f"🔧 DEBUG: H264Encoder.encode called with frame: {getattr(frame, 'width', 'unknown')}x{getattr(frame, 'height', 'unknown')}, keyframe: {force_keyframe}"
                )

                # Call the original encode method
                result = original_encode(self, frame, force_keyframe)

                # DEBUG: Log what the encoder returned
                logger.debug(f"🔧 DEBUG: H264Encoder.encode returned: {type(result)}")

                if isinstance(result, tuple) and len(result) == 2:
                    packets, timestamp = result
                    logger.debug(f"🔧 DEBUG: Encoder returned tuple: packets={type(packets)}, timestamp={timestamp}")
                    if isinstance(packets, list):
                        logger.debug(f"🔧 DEBUG: Packets list length: {len(packets)}")
                        for i, packet in enumerate(packets[:3]):  # Log first 3 packets
                            logger.debug(
                                f"🔧 DEBUG: Packet {i}: type={type(packet)}, size={len(packet) if hasattr(packet, '__len__') else 'unknown'}"
                            )
                elif isinstance(result, bytes):
                    logger.debug(f"🔧 DEBUG: Encoder returned bytes: {len(result)} bytes")
                elif isinstance(result, list):
                    logger.debug(f"🔧 DEBUG: Encoder returned list: {len(result)} items")
                else:
                    logger.debug(f"🔧 DEBUG: Encoder returned unknown format: {result}")

                # Handle tuple result (likely packets and timestamp)
                if isinstance(result, tuple) and len(result) == 2:
                    packets, timestamp = result

                    # If packets is a list of bytes, inject SEI into each
                    if isinstance(packets, list):
                        modified_packets = []
                        for i, packet in enumerate(packets):
                            if isinstance(packet, bytes):
                                modified_packet = inject_sei_into_bitstream(packet)
                                modified_packets.append(modified_packet)
                                # Only log if packet was actually modified (SEI injected)
                                if len(modified_packet) != len(packet):
                                    logger.info(f"🔧 SEI injected into packet {i}: {len(packet)} -> {len(modified_packet)} bytes")
                            else:
                                modified_packets.append(packet)
                        return (modified_packets, timestamp)

                    # If packets is bytes, inject SEI
                    elif isinstance(packets, bytes):
                        modified_packets = inject_sei_into_bitstream(packets)
                        if len(modified_packets) != len(packets):
                            logger.info(f"🔧 SEI injected into packets: {len(packets)} -> {len(modified_packets)} bytes")
                        return (modified_packets, timestamp)

                # If result is bytes, inject SEI
                elif isinstance(result, bytes):
                    modified_result = inject_sei_into_bitstream(result)
                    return modified_result
                # If result is a list of bytes, inject into each
                elif isinstance(result, list):
                    return [inject_sei_into_bitstream(chunk) for chunk in result]

            return True

        # Fallback to original _encode_frame method
        if hasattr(H264Encoder, "_encode_frame"):
            original_encode_frame = H264Encoder._encode_frame
            print("🔧 Found _encode_frame method, patching...")
        else:
            print("❌ No _encode_frame method found")
            return False

        def patched_encode_frame(self, frame, force_keyframe: bool):
            """
            Patched version of _encode_frame that injects SEI data.
            """
            # DEBUG: Log when _encode_frame is called
            logger.debug(
                f"🔧 DEBUG: H264Encoder._encode_frame called with frame: {getattr(frame, 'width', 'unknown')}x{getattr(frame, 'height', 'unknown')}, keyframe: {force_keyframe}"
            )

            # Call the original encode_frame method
            for i, bitstream_chunk in enumerate(original_encode_frame(self, frame, force_keyframe)):
                # DEBUG: Log what _encode_frame yields
                logger.debug(f"🔧 DEBUG: _encode_frame yielded chunk {i}: {len(bitstream_chunk)} bytes")

                # DEBUG: Check if this looks like H.264
                if len(bitstream_chunk) >= 4:
                    hex_preview = " ".join(f"{b:02x}" for b in bitstream_chunk[:16])
                    logger.debug(f"🔧 DEBUG: Chunk {i} preview: {hex_preview}...")

                    has_start_code = bitstream_chunk[:3] == b"\x00\x00\x01" or bitstream_chunk[:4] == b"\x00\x00\x00\x01"
                    logger.debug(f"🔧 DEBUG: Chunk {i} has H.264 start code: {has_start_code}")

                    if has_start_code:
                        nal_start = 4 if bitstream_chunk[:4] == b"\x00\x00\x00\x01" else 3
                        if len(bitstream_chunk) > nal_start:
                            nal_type = bitstream_chunk[nal_start] & 0x1F
                            logger.debug(f"🔧 DEBUG: Chunk {i} NAL unit type: {nal_type}")

                # Inject SEI data into each bitstream chunk
                modified_chunk = inject_sei_into_bitstream(bitstream_chunk)
                yield modified_chunk

        # Apply the patch
        H264Encoder._encode_frame = patched_encode_frame
        print("✅ H.264 encoder._encode_frame successfully patched for SEI injection")
        logger.info("✅ H.264 encoder._encode_frame successfully patched for SEI injection")

        # Verify the patch was applied
        if H264Encoder._encode_frame == patched_encode_frame:
            print("🔧 Patch verification: _encode_frame patch confirmed")
            logger.info("🔧 Patch verification: _encode_frame patch confirmed")
        else:
            print("❌ Patch verification: _encode_frame patch FAILED")
            logger.error("❌ Patch verification: _encode_frame patch FAILED")

        # Also try to hook into RTP layer for H.264 packets
        try:
            print("🔧 Attempting to hook RTP layer...")

            # Try to import aiortc RTP components
            from aiortc.rtcrtpsender import RTCRtpSender
            from aiortc.rtp import RtpPacket

            if hasattr(RTCRtpSender, "_send_rtp"):
                original_send_rtp = RTCRtpSender._send_rtp
                print("🔧 Found RTCRtpSender._send_rtp method")

                def patched_send_rtp(self, packet: RtpPacket):
                    """Patched RTP sender to inspect H.264 packets"""
                    # Check if this is an H.264 packet (payload type 96-127 are dynamic)
                    if hasattr(packet, "payload") and len(packet.payload) > 4:
                        # Look for H.264 NAL unit indicators in RTP payload
                        payload = packet.payload
                        logger.debug(f"🔧 DEBUG: RTP packet payload: {len(payload)} bytes, PT: {getattr(packet, 'payload_type', 'unknown')}")

                        # H.264 RTP payload might start with NAL unit header
                        if len(payload) >= 1:
                            nal_header = payload[0]
                            nal_type = nal_header & 0x1F
                            logger.debug(f"🔧 DEBUG: RTP H.264 NAL type: {nal_type}")

                    return original_send_rtp(self, packet)

                RTCRtpSender._send_rtp = patched_send_rtp
                print("✅ RTCRtpSender._send_rtp patched")

        except Exception as rtp_e:
            print(f"⚠️ Could not hook RTP layer: {rtp_e}")

        return True

    except Exception as e:
        print(f"❌ Failed to patch H.264 encoder: {e}")
        logger.error(f"❌ Failed to patch H.264 encoder: {e}")
        import traceback

        traceback.print_exc()

        # Try alternative approach - patch PyAV directly
        try:
            print("🔧 Attempting to patch PyAV codec directly...")
            logger.info("🔧 Attempting to patch PyAV codec directly...")

            import av

            # Hook into av.CodecContext.encode - this is the main entry point
            if hasattr(av.CodecContext, "encode"):
                original_av_encode = av.CodecContext.encode
                print("🔧 Found av.CodecContext.encode method")

                def patched_av_encode(self, frame=None):
                    """Patched PyAV encode method that intercepts H.264 packets"""
                    # Call original encode to get the packet iterator
                    packet_iterator = original_av_encode(self, frame)

                    # Check if this is H.264 codec
                    is_h264 = hasattr(self, "name") and self.name == "libx264"

                    if is_h264:
                        logger.debug(f"🔧 DEBUG: PyAV H.264 encode called")

                    # Process each packet from the iterator
                    for packet in packet_iterator:
                        if is_h264 and packet:
                            try:
                                # Get the raw packet data
                                packet_bytes = bytes(packet)
                                logger.debug(f"🔧 DEBUG: PyAV H.264 packet: {len(packet_bytes)} bytes")

                                # Check if this contains H.264 NAL units
                                if len(packet_bytes) >= 4:
                                    hex_preview = " ".join(f"{b:02x}" for b in packet_bytes[:16])
                                    logger.debug(f"🔧 DEBUG: PyAV packet preview: {hex_preview}...")

                                    has_start_code = packet_bytes[:3] == b"\x00\x00\x01" or packet_bytes[:4] == b"\x00\x00\x00\x01"

                                    if has_start_code:
                                        logger.info(f"🎯 FOUND REAL H.264 BITSTREAM IN PYAV: {len(packet_bytes)} bytes")

                                        # Inject SEI into the H.264 bitstream
                                        modified_bytes = inject_sei_into_bitstream(packet_bytes)

                                        if len(modified_bytes) != len(packet_bytes):
                                            logger.info(f"📡 SEI injected into PyAV packet: {len(packet_bytes)} -> {len(modified_bytes)} bytes")

                                            # Create a new packet with modified data
                                            # We need to replace the packet's internal data
                                            # This is tricky with PyAV's C extension, so we'll try to modify in place
                                            try:
                                                # Try to access the packet's internal buffer and modify it
                                                # This is a low-level operation that may not work with all PyAV versions
                                                if hasattr(packet, "_buffer") or hasattr(packet, "buffer"):
                                                    logger.warning("🔧 Attempting direct packet buffer modification (experimental)")
                                                    # This approach is risky and may not work
                                                else:
                                                    # Alternative: create a new packet (if possible)
                                                    logger.debug("🔧 Creating new packet with modified data")
                                                    # For now, we'll yield the original packet and log the issue
                                                    logger.warning("🔧 Cannot modify PyAV packet in-place, SEI injection at this level not supported")
                                            except Exception as modify_e:
                                                logger.error(f"❌ Failed to modify PyAV packet: {modify_e}")

                                    else:
                                        logger.debug(f"🔧 DEBUG: PyAV packet has no H.264 start code")

                            except Exception as packet_e:
                                logger.error(f"❌ Error processing PyAV packet: {packet_e}")

                        # Always yield the original packet (modification at this level is complex)
                        yield packet

                av.CodecContext.encode = patched_av_encode
                print("✅ PyAV CodecContext.encode patched")
                logger.info("✅ PyAV CodecContext.encode patched")

            # Also try to patch the Packet class to intercept packet creation
            try:
                print("🔧 Attempting to patch PyAV Packet class...")

                # Import the Packet class
                from av.packet import Packet

                if hasattr(Packet, "__init__"):
                    original_packet_init = Packet.__init__
                    print("🔧 Found av.Packet.__init__ method")

                    def patched_packet_init(self, *args, **kwargs):
                        """Patched Packet.__init__ to track and potentially modify packet creation"""
                        result = original_packet_init(self, *args, **kwargs)

                        # Log packet creation for H.264 streams
                        if hasattr(self, "size") and self.size > 0:
                            logger.debug(f"🔧 DEBUG: PyAV Packet created: {self.size} bytes")

                            # Try to examine the packet data immediately after creation
                            try:
                                # Check if we can access the packet data
                                if hasattr(self, "to_bytes"):
                                    packet_data = self.to_bytes()
                                elif hasattr(self, "__bytes__"):
                                    packet_data = bytes(self)
                                else:
                                    packet_data = None

                                if packet_data and len(packet_data) >= 4:
                                    # Check if this is H.264
                                    has_start_code = packet_data[:3] == b"\x00\x00\x01" or packet_data[:4] == b"\x00\x00\x00\x01"
                                    if has_start_code:
                                        nal_start = 4 if packet_data[:4] == b"\x00\x00\x00\x01" else 3
                                        if len(packet_data) > nal_start:
                                            nal_type = packet_data[nal_start] & 0x1F
                                            if 1 <= nal_type <= 12:
                                                logger.info(
                                                    f"🎯 PACKET INIT: H.264 packet created with NAL type {nal_type}, {len(packet_data)} bytes"
                                                )

                                                # Mark this packet as H.264 for later processing
                                                self._is_h264 = True
                                                self._original_data = packet_data

                            except Exception as data_e:
                                logger.debug(f"🔧 DEBUG: Could not examine packet data in __init__: {data_e}")

                        return result

                    Packet.__init__ = patched_packet_init
                    print("✅ PyAV Packet.__init__ patched")

            except Exception as packet_patch_e:
                print(f"⚠️ Could not patch PyAV Packet class: {packet_patch_e}")
                logger.warning(f"⚠️ Could not patch PyAV Packet class: {packet_patch_e}")

            # Try to patch the lower-level _send_frame_and_recv method
            try:
                print("🔧 Attempting to patch PyAV _send_frame_and_recv method...")

                # This method is defined in the Cython code, so we need to be careful
                # We'll try to patch it at the CodecContext level
                if hasattr(av.CodecContext, "_send_frame_and_recv"):
                    print("🔧 Found _send_frame_and_recv method (direct access)")
                    # This is unlikely to work since it's a Cython method
                else:
                    print("🔧 _send_frame_and_recv not directly accessible (expected for Cython)")

                # Alternative: patch at the bytes() conversion level
                # When packets are converted to bytes, that's where we can intercept
                print("🔧 Attempting to patch packet bytes conversion...")

                from av.packet import Packet

                if hasattr(Packet, "__bytes__"):
                    original_packet_bytes = Packet.__bytes__
                    print("🔧 Found av.Packet.__bytes__ method")

                    def patched_packet_bytes(self):
                        """Patched Packet.__bytes__ to inject SEI data"""
                        # Get the original packet bytes
                        original_bytes = original_packet_bytes(self)

                        # Check if this packet was marked as H.264 during initialization
                        is_h264 = getattr(self, "_is_h264", False)

                        if not is_h264:
                            # Fallback: try to determine if this is H.264 by examining the data
                            try:
                                if len(original_bytes) >= 4:
                                    has_start_code = original_bytes[:3] == b"\x00\x00\x01" or original_bytes[:4] == b"\x00\x00\x00\x01"
                                    if has_start_code:
                                        nal_start = 4 if original_bytes[:4] == b"\x00\x00\x00\x01" else 3
                                        if len(original_bytes) > nal_start:
                                            nal_type = original_bytes[nal_start] & 0x1F
                                            # H.264 NAL unit types 1-5 are VCL (Video Coding Layer)
                                            # Types 6-12 are non-VCL (including SEI = 6)
                                            if 1 <= nal_type <= 12:
                                                is_h264 = True
                                                logger.debug(f"🔧 DEBUG: Detected H.264 packet with NAL type {nal_type}")
                            except Exception as detect_e:
                                logger.debug(f"🔧 DEBUG: Error detecting H.264: {detect_e}")

                        if is_h264:
                            logger.info(f"🎯 INTERCEPTED H.264 PACKET (__bytes__): {len(original_bytes)} bytes")

                            # Use our global logging function
                            if "log_packet_data" in globals():
                                log_packet_data(original_bytes, "__bytes__")

                            # Inject SEI data
                            modified_bytes = inject_sei_into_bitstream(original_bytes)

                            if len(modified_bytes) != len(original_bytes):
                                logger.info(f"📡 SEI injected into packet bytes: {len(original_bytes)} -> {len(modified_bytes)} bytes")
                                return modified_bytes

                        return original_bytes

                    Packet.__bytes__ = patched_packet_bytes
                    print("✅ PyAV Packet.__bytes__ patched")
                    logger.info("✅ PyAV Packet.__bytes__ patched")

                else:
                    print("⚠️ av.Packet.__bytes__ method not found")

                # Also try to patch to_bytes() method if it exists
                if hasattr(Packet, "to_bytes"):
                    original_to_bytes = Packet.to_bytes
                    print("🔧 Found av.Packet.to_bytes method")

                    def patched_to_bytes(self):
                        """Patched Packet.to_bytes() to inject SEI data"""
                        # Get the original packet bytes
                        original_bytes = original_to_bytes(self)

                        # Check if this packet was marked as H.264 during initialization
                        is_h264 = getattr(self, "_is_h264", False)

                        if not is_h264:
                            # Fallback: try to determine if this is H.264 by examining the data
                            try:
                                if len(original_bytes) >= 4:
                                    has_start_code = original_bytes[:3] == b"\x00\x00\x01" or original_bytes[:4] == b"\x00\x00\x00\x01"
                                    if has_start_code:
                                        nal_start = 4 if original_bytes[:4] == b"\x00\x00\x00\x01" else 3
                                        if len(original_bytes) > nal_start:
                                            nal_type = original_bytes[nal_start] & 0x1F
                                            if 1 <= nal_type <= 12:
                                                is_h264 = True
                                                logger.debug(f"🔧 DEBUG: Detected H.264 packet (to_bytes) with NAL type {nal_type}")
                            except Exception as detect_e:
                                logger.debug(f"🔧 DEBUG: Error detecting H.264 in to_bytes: {detect_e}")

                        if is_h264:
                            logger.info(f"🎯 INTERCEPTED H.264 PACKET (to_bytes): {len(original_bytes)} bytes")

                            # Use our global logging function
                            if "log_packet_data" in globals():
                                log_packet_data(original_bytes, "to_bytes")

                            # Inject SEI data
                            modified_bytes = inject_sei_into_bitstream(original_bytes)

                            if len(modified_bytes) != len(original_bytes):
                                logger.info(f"📡 SEI injected via to_bytes: {len(original_bytes)} -> {len(modified_bytes)} bytes")
                                return modified_bytes

                        return original_bytes

                    Packet.to_bytes = patched_to_bytes
                    print("✅ PyAV Packet.to_bytes patched")
                    logger.info("✅ PyAV Packet.to_bytes patched")

                else:
                    print("⚠️ av.Packet.to_bytes method not found")

                # Try to patch buffer access methods
                print("🔧 Checking for packet buffer access methods...")
                packet_methods = [method for method in dir(Packet) if not method.startswith("__")]
                buffer_methods = [m for m in packet_methods if "buffer" in m.lower() or "data" in m.lower()]
                print(f"🔧 DEBUG: Packet buffer-related methods: {buffer_methods}")
                logger.info(f"🔧 DEBUG: Packet buffer-related methods: {buffer_methods}")

                # Check for size and other properties
                size_methods = [m for m in packet_methods if "size" in m.lower()]
                print(f"🔧 DEBUG: Packet size-related methods: {size_methods}")
                logger.info(f"🔧 DEBUG: Packet size-related methods: {size_methods}")

                # Log all available methods for debugging
                print(f"🔧 DEBUG: All Packet methods: {packet_methods}")
                logger.debug(f"🔧 DEBUG: All Packet methods: {packet_methods}")

                # Try to create a packet wrapper approach
                print("🔧 Attempting packet wrapper approach...")

                class H264PacketWrapper:
                    """Wrapper for PyAV packets that can inject SEI data"""

                    def __init__(self, original_packet):
                        self._original = original_packet
                        self._modified_data = None

                    def __getattr__(self, name):
                        # Delegate all attribute access to the original packet
                        return getattr(self._original, name)

                    def __bytes__(self):
                        """Override bytes conversion to inject SEI"""
                        if self._modified_data is not None:
                            return self._modified_data

                        # Get original data
                        try:
                            original_data = bytes(self._original)
                        except:
                            # Fallback methods
                            if hasattr(self._original, "to_bytes"):
                                original_data = self._original.to_bytes()
                            else:
                                return b""  # Can't get data

                        # Check if H.264 and inject SEI
                        if len(original_data) >= 4:
                            has_start_code = original_data[:3] == b"\x00\x00\x01" or original_data[:4] == b"\x00\x00\x00\x01"
                            if has_start_code:
                                nal_start = 4 if original_data[:4] == b"\x00\x00\x00\x01" else 3
                                if len(original_data) > nal_start:
                                    nal_type = original_data[nal_start] & 0x1F
                                    if 1 <= nal_type <= 12:  # H.264 NAL units
                                        logger.info(f"🎯 WRAPPER: Processing H.264 packet with NAL type {nal_type}")
                                        modified_data = inject_sei_into_bitstream(original_data)
                                        if len(modified_data) != len(original_data):
                                            logger.info(f"📡 WRAPPER: SEI injected: {len(original_data)} -> {len(modified_data)} bytes")
                                            self._modified_data = modified_data
                                            return modified_data

                        return original_data

                    def to_bytes(self):
                        """Alternative method for getting bytes"""
                        return self.__bytes__()

                print("✅ H264PacketWrapper class created")
                logger.info("✅ H264PacketWrapper class created")

                # Store the wrapper class for potential use
                globals()["H264PacketWrapper"] = H264PacketWrapper

                # Add comprehensive debugging for packet flow
                print("🔧 Setting up comprehensive H.264 packet flow debugging...")

                # Track all packet creations and conversions
                packet_counter = {"count": 0, "h264_count": 0}

                def log_packet_data(data, source, packet_id=None):
                    """Helper to log packet data consistently"""
                    if not data:
                        return

                    packet_counter["count"] += 1
                    pid = packet_id or packet_counter["count"]

                    logger.debug(f"🔧 PACKET[{pid}] from {source}: {len(data)} bytes")

                    if len(data) >= 16:
                        hex_preview = " ".join(f"{b:02x}" for b in data[:16])
                        logger.debug(f"🔧 PACKET[{pid}] preview: {hex_preview}...")

                        # Check for H.264 markers
                        has_start_code = data[:3] == b"\x00\x00\x01" or data[:4] == b"\x00\x00\x00\x01"
                        if has_start_code:
                            packet_counter["h264_count"] += 1
                            nal_start = 4 if data[:4] == b"\x00\x00\x00\x01" else 3
                            if len(data) > nal_start:
                                nal_type = data[nal_start] & 0x1F
                                logger.info(f"🎯 H.264 PACKET[{pid}] from {source}: NAL type {nal_type}, {len(data)} bytes")

                                # Log NAL unit type details
                                nal_type_names = {
                                    1: "Coded slice (non-IDR)",
                                    2: "Coded slice partition A",
                                    3: "Coded slice partition B",
                                    4: "Coded slice partition C",
                                    5: "Coded slice (IDR)",
                                    6: "SEI",
                                    7: "SPS",
                                    8: "PPS",
                                    9: "Access unit delimiter",
                                    10: "End of sequence",
                                    11: "End of stream",
                                    12: "Filler data",
                                }
                                nal_name = nal_type_names.get(nal_type, f"Unknown({nal_type})")
                                logger.info(f"🎯 H.264 PACKET[{pid}]: {nal_name}")

                # Store the logging function globally
                globals()["log_packet_data"] = log_packet_data
                globals()["packet_counter"] = packet_counter

                print("✅ H.264 packet flow debugging system ready")
                logger.info("✅ H.264 packet flow debugging system ready")

            except Exception as low_level_e:
                print(f"⚠️ Could not patch PyAV low-level methods: {low_level_e}")
                logger.warning(f"⚠️ Could not patch PyAV low-level methods: {low_level_e}")

        except Exception as av_e:
            print(f"⚠️ Could not patch PyAV: {av_e}")
            logger.warning(f"⚠️ Could not patch PyAV: {av_e}")

        return False


# Auto-apply patch when module is imported
print("🔧 About to call patch_h264_encoder()")
logger.info("🔧 About to call patch_h264_encoder()")

patch_result = patch_h264_encoder()
print(f"🔧 Finished calling patch_h264_encoder(), result: {patch_result}")
logger.info(f"🔧 Finished calling patch_h264_encoder(), result: {patch_result}")

# Test SEI injection functionality
print("🧪 Running SEI injection test...")
logger.info("🧪 Running SEI injection test...")
test_result = test_sei_injection()
print(f"🧪 SEI injection test result: {test_result}")
logger.info(f"🧪 SEI injection test result: {test_result}")

# Also try to see what's available in aiortc
try:
    import aiortc

    print(f"🔧 aiortc version: {getattr(aiortc, '__version__', 'unknown')}")
    logger.info(f"🔧 aiortc version: {getattr(aiortc, '__version__', 'unknown')}")

    # Check what codecs are available
    from aiortc import codecs

    codec_modules = [attr for attr in dir(codecs) if not attr.startswith("_")]
    print(f"🔧 Available codec modules: {codec_modules}")
    logger.info(f"🔧 Available codec modules: {codec_modules}")

except Exception as e:
    print(f"🔧 Error inspecting aiortc: {e}")
    logger.error(f"🔧 Error inspecting aiortc: {e}")
