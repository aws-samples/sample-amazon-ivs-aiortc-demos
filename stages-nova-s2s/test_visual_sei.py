#!/usr/bin/env python3

import asyncio
import logging
import numpy as np
from visual_sei_encoder import VisualSeiEncoder
import json
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def test_visual_sei_encoding():
    """Test the visual SEI encoding functionality"""
    logger.info("🧪 Testing Visual SEI Encoding...")

    # Create encoder
    encoder = VisualSeiEncoder(frame_width=640, frame_height=360)

    # Test message
    test_message = {
        "type": "nova_text_output",
        "role": "ASSISTANT",
        "content": "Hello from Nova! This is a test message.",
        "timestamp": time.time(),
        "session_id": "test-session",
        "content_id": "test-content",
    }

    # Queue the message
    success = await encoder.queue_message(test_message, repeat_count=3)
    logger.info(f"📺 Message queued: {success}")

    # Create a test frame (black frame)
    frame_array = np.zeros((360, 640, 3), dtype=np.uint8)
    logger.info(f"📏 Original frame shape: {frame_array.shape}")

    # Encode the message into the frame
    encoded_frame = await encoder.encode_frame(frame_array)
    logger.info(f"📏 Encoded frame shape: {encoded_frame.shape}")

    # Check if data was encoded (bottom rows should have data)
    data_region_y = 360 - 2  # Bottom 2 rows

    # Check if there's non-zero data in the bottom rows
    bottom_rows = encoded_frame[data_region_y:, :, :]
    has_data = np.any(bottom_rows > 0)

    if has_data:
        logger.info("✅ Visual SEI data detected in frame")

        # Show some sample data from the encoded region
        sample_pixels = bottom_rows[0, :20, 0]  # First 20 pixels of first data row, red channel
        logger.info(f"📊 Sample encoded data: {list(sample_pixels)}")

        # Verify sync pattern
        sync_pattern = [0xFF, 0x00, 0xFF, 0x00]
        if list(sample_pixels[:4]) == sync_pattern:
            logger.info("✅ Sync pattern verified")
        else:
            logger.warning(f"⚠️  Sync pattern mismatch: expected {sync_pattern}, got {list(sample_pixels[:4])}")

        # Check UUID
        uuid_start = 4
        uuid_bytes = [0x9E, 0x50, 0x4E, 0xA5, 0xEE, 0x5A, 0x4F, 0x02, 0x94, 0x9F, 0xB0, 0x33, 0xA3, 0x76, 0x8D, 0xA2]
        encoded_uuid = list(sample_pixels[uuid_start : uuid_start + 16])

        if encoded_uuid == uuid_bytes:
            logger.info("✅ UUID verified")
        else:
            logger.warning(f"⚠️  UUID mismatch")
            logger.info(f"Expected: {uuid_bytes}")
            logger.info(f"Got:      {encoded_uuid}")

        # Check message length
        length_index = 4 + 16
        message_length = sample_pixels[length_index]
        logger.info(f"📏 Encoded message length: {message_length}")

        # Try to extract the message
        message_start = length_index + 1
        if message_start + message_length <= len(sample_pixels):
            message_bytes = sample_pixels[message_start : message_start + message_length]
            try:
                message_str = bytes(message_bytes).decode("utf-8")
                decoded_message = json.loads(message_str)
                logger.info("✅ Message successfully decoded")
                logger.info(f"📝 Decoded content: {decoded_message.get('content', 'N/A')}")
            except Exception as e:
                logger.warning(f"⚠️  Failed to decode message: {e}")

    else:
        logger.error("❌ No visual SEI data found in frame")

    # Test multiple frames
    logger.info("🔄 Testing multiple frame encoding...")

    for i in range(3):
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        encoded = await encoder.encode_frame(frame)
        queue_size = await encoder.get_queue_size()
        logger.info(f"📺 Frame {i+1}: Queue size after encoding: {queue_size}")

    final_queue_size = await encoder.get_queue_size()
    logger.info(f"📊 Final queue size: {final_queue_size}")

    # Get encoding info
    info = encoder.get_encoding_info()
    logger.info(f"📋 Encoding info: {info}")

    logger.info("✅ Visual SEI encoding test completed")


if __name__ == "__main__":
    asyncio.run(test_visual_sei_encoding())
