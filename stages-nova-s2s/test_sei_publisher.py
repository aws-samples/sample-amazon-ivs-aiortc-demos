#!/usr/bin/env python3

import asyncio
import logging
from sei_publisher import SeiPublisher

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def test_sei_publisher():
    """Test the SEI publisher functionality"""
    logger.info("🧪 Testing SEI Publisher...")

    # Create SEI publisher
    sei_publisher = SeiPublisher(max_retry_attempts=3)

    # Test text publishing
    test_text = "Hello from Nova! This is a test message."
    success = await sei_publisher.publish_text(test_text, repeat_count=3)
    logger.info(f"📡 Text publish result: {success}")

    # Test JSON publishing
    test_data = {
        "type": "agent_response",
        "content": "This is a JSON test message",
        "confidence": 0.95,
        "metadata": {"source": "nova", "version": "1.0"},
    }
    success = await sei_publisher.publish_json(test_data, repeat_count=2)
    logger.info(f"📡 JSON publish result: {success}")

    # Check queue size
    queue_size = await sei_publisher.get_queue_size()
    logger.info(f"📊 Queue size: {queue_size}")

    # Create dummy frame data (simulating H.264 frame)
    dummy_frame = bytes(
        [
            # Start code + SPS NAL unit
            0x00,
            0x00,
            0x01,
            0x67,
            0x42,
            0x00,
            0x1E,
            0x8B,
            0x40,
            0x50,
            0x17,
            0xFC,
            0xB0,
            0x0F,
            0x08,
            0x84,
            0x6A,
            # Start code + PPS NAL unit
            0x00,
            0x00,
            0x01,
            0x68,
            0xCE,
            0x3C,
            0x80,
            # Start code + IDR slice
            0x00,
            0x00,
            0x01,
            0x65,
            0x88,
            0x84,
            0x00,
            0x10,
            0xFF,
            0xFE,
            0xF6,
            0xF0,
            0xFE,
            0x05,
            0x36,
            0x56,
            0x04,
            0x50,
            0x00,
        ]
    )

    # Process frame with SEI data
    logger.info("🎬 Processing dummy frame with SEI data...")
    modified_frame = await sei_publisher.process_frame(dummy_frame)

    logger.info(f"📏 Original frame size: {len(dummy_frame)} bytes")
    logger.info(f"📏 Modified frame size: {len(modified_frame)} bytes")
    logger.info(f"📈 Size increase: {len(modified_frame) - len(dummy_frame)} bytes")

    # Check if SEI data was inserted
    if len(modified_frame) > len(dummy_frame):
        logger.info("✅ SEI data successfully inserted into frame")

        # Look for SEI NAL unit marker
        sei_found = False
        for i in range(len(modified_frame) - 3):
            if modified_frame[i : i + 4] == bytes([0x00, 0x00, 0x01, 0x06]):
                sei_found = True
                logger.info(f"🔍 SEI NAL unit found at position {i}")
                break

        if sei_found:
            logger.info("✅ SEI NAL unit structure verified")
        else:
            logger.warning("⚠️  SEI NAL unit structure not found")
    else:
        logger.warning("⚠️  No size increase detected - SEI insertion may have failed")

    # Clear queue
    await sei_publisher.clear_queue()
    final_queue_size = await sei_publisher.get_queue_size()
    logger.info(f"🗑️  Final queue size after clear: {final_queue_size}")

    logger.info("✅ SEI Publisher test completed")


if __name__ == "__main__":
    asyncio.run(test_sei_publisher())
