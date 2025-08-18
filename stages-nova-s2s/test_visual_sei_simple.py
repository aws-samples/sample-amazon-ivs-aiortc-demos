#!/usr/bin/env python3

import asyncio
import logging
import numpy as np
from visual_sei_encoder import VisualSeiEncoder
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def test_visual_sei_simple():
    """Simple test of visual SEI encoding"""
    logger.info("🧪 Testing Visual SEI Encoding (Simple)...")

    # Create encoder
    encoder = VisualSeiEncoder(frame_width=640, frame_height=360)

    # Test message
    test_message = {"type": "nova_text_output", "role": "ASSISTANT", "content": "Hello!", "timestamp": time.time()}

    # Queue the message
    success = await encoder.queue_message(test_message, repeat_count=1)
    logger.info(f"📺 Message queued: {success}")

    # Check queue size
    queue_size = await encoder.get_queue_size()
    logger.info(f"📊 Queue size: {queue_size}")

    # Create a test frame
    frame_array = np.zeros((360, 640, 3), dtype=np.uint8)

    # Encode the message into the frame
    encoded_frame = await encoder.encode_frame(frame_array)

    # Check if data was encoded
    data_region_y = 360 - 2  # Bottom 2 rows
    bottom_rows = encoded_frame[data_region_y:, :, :]
    has_data = np.any(bottom_rows > 0)

    if has_data:
        logger.info("✅ Visual SEI data detected in frame")

        # Show first few pixels
        first_row = bottom_rows[0, :10, 0]  # First 10 pixels, red channel
        logger.info(f"📊 First 10 pixels: {list(first_row)}")

        # Check sync pattern
        sync_pattern = [255, 0, 255, 0]
        if list(first_row[:4]) == sync_pattern:
            logger.info("✅ Sync pattern found")
        else:
            logger.warning(f"⚠️  Sync pattern not found: {list(first_row[:4])}")
    else:
        logger.error("❌ No visual SEI data found")

    # Check queue after encoding
    final_queue_size = await encoder.get_queue_size()
    logger.info(f"📊 Final queue size: {final_queue_size}")

    # Get encoding info
    info = encoder.get_encoding_info()
    logger.info(f"📋 Max message length: {info['max_message_length']} bytes")
    logger.info(f"📋 Data region: rows {info['data_region_y']}-{info['data_region_y'] + info['data_region_height'] - 1}")

    logger.info("✅ Simple visual SEI test completed")


if __name__ == "__main__":
    asyncio.run(test_visual_sei_simple())
