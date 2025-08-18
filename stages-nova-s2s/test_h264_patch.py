#!/usr/bin/env python3

import asyncio
import logging
import time
from unittest.mock import Mock
import numpy as np

# Apply the H.264 patch before importing aiortc
import h264_sei_patch

# Now import aiortc components
import av
from aiortc.codecs.h264 import H264Encoder

from sei_publisher import SeiPublisher

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def test_h264_patch():
    """Test the H.264 encoder patch with SEI injection"""
    logger.info("🧪 Testing H.264 Encoder Patch...")

    # Create SEI publisher
    sei_publisher = SeiPublisher(max_retry_attempts=3)

    # Set it as the global publisher for the patch
    h264_sei_patch.set_global_sei_publisher(sei_publisher)

    # Create test message
    test_message = {
        "type": "nova_text_output",
        "role": "ASSISTANT",
        "content": "Hello from H.264 patch!",
        "timestamp": time.time(),
        "session_id": "test-session",
    }

    # Queue the message
    success = await sei_publisher.publish_json(test_message, repeat_count=3)
    logger.info(f"📡 Message queued: {success}")

    # Check queue size
    queue_size = await sei_publisher.get_queue_size()
    logger.info(f"📊 Queue size: {queue_size}")

    # Create H.264 encoder
    encoder = H264Encoder()
    logger.info("🎬 H.264 encoder created")

    # Create a test video frame
    frame_array = np.zeros((360, 640, 3), dtype=np.uint8)

    # Add some pattern to make it a valid frame
    frame_array[100:260, 200:440, :] = 128  # Gray rectangle

    # Create av.VideoFrame
    frame = av.VideoFrame.from_ndarray(frame_array, format="rgb24")
    frame.pts = 0
    from fractions import Fraction

    frame.time_base = Fraction(1, 30)

    logger.info(f"📺 Created test frame: {frame.width}x{frame.height}")

    try:
        # Encode the frame (this should trigger our patch)
        logger.info("🔄 Encoding frame with H.264 encoder...")
        packages, timestamp = encoder.encode(frame, force_keyframe=True)

        logger.info(f"📦 Encoded {len(packages)} packages, timestamp: {timestamp}")

        # Check if SEI data was injected
        total_size = sum(len(pkg) for pkg in packages)
        logger.info(f"📏 Total encoded size: {total_size} bytes")

        # Look for SEI NAL units in the packages
        sei_found = False
        for i, package in enumerate(packages):
            # Look for SEI NAL unit type (0x06)
            for j in range(len(package) - 3):
                if package[j : j + 4] == b"\x00\x00\x01\x06" or package[j : j + 3] == b"\x00\x01\x06":
                    sei_found = True
                    logger.info(f"✅ SEI NAL unit found in package {i} at offset {j}")
                    break
            if sei_found:
                break

        if sei_found:
            logger.info("✅ H.264 patch successfully injected SEI data")
        else:
            logger.warning("⚠️  No SEI NAL units found in encoded data")

        # Check queue after encoding
        final_queue_size = await sei_publisher.get_queue_size()
        logger.info(f"📊 Final queue size: {final_queue_size}")

        if final_queue_size < queue_size:
            logger.info("✅ SEI messages were processed from queue")
        else:
            logger.warning("⚠️  SEI messages may not have been processed")

    except Exception as e:
        logger.error(f"❌ Error during encoding: {e}")
        import traceback

        traceback.print_exc()

    logger.info("✅ H.264 patch test completed")


if __name__ == "__main__":
    asyncio.run(test_h264_patch())
