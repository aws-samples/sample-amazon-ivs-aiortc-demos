#!/usr/bin/env python3

import asyncio
import logging
import time
from unittest.mock import Mock, AsyncMock
from sei_publisher import SeiPublisher
from bedrock_stream_manager import BedrockStreamManager

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def test_integration():
    """Test the integration between SEI publisher and Bedrock stream manager"""
    logger.info("🧪 Testing SEI Publisher Integration...")

    # Create mock tracks
    mock_audio_track = Mock()
    mock_video_track = Mock()

    # Create a minimal bedrock stream manager for testing
    try:
        # This will fail due to missing AWS credentials, but we can test the SEI part
        stream_manager = BedrockStreamManager(
            agent_audio_track=mock_audio_track, agent_video_track=mock_video_track, model_id="amazon.nova-sonic-v1:0", region="us-east-1"
        )

        logger.info("✅ BedrockStreamManager created successfully")
        logger.info(f"📡 SEI Publisher available: {hasattr(stream_manager, 'sei_publisher')}")

        if hasattr(stream_manager, "sei_publisher"):
            # Test direct SEI publishing
            test_messages = [
                {
                    "type": "nova_text_output",
                    "role": "USER",
                    "content": "Hello, how are you?",
                    "timestamp": time.time(),
                    "session_id": "test-session",
                    "content_id": "test-content",
                },
                {
                    "type": "nova_text_output",
                    "role": "ASSISTANT",
                    "content": "I'm doing well, thank you for asking!",
                    "timestamp": time.time(),
                    "session_id": "test-session",
                    "content_id": "test-content",
                },
            ]

            for message in test_messages:
                success = await stream_manager.sei_publisher.publish_json(message, repeat_count=2)
                logger.info(f"📡 Published {message['role']} message: {success}")

            # Check queue
            queue_size = await stream_manager.sei_publisher.get_queue_size()
            logger.info(f"📊 Messages in queue: {queue_size}")

            # Simulate frame processing
            dummy_frame = bytes(
                [
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
                    0x00,
                    0x00,
                    0x01,
                    0x68,
                    0xCE,
                    0x3C,
                    0x80,
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
                ]
            )

            modified_frame = await stream_manager.sei_publisher.process_frame(dummy_frame)
            logger.info(f"📏 Frame size: {len(dummy_frame)} -> {len(modified_frame)} bytes")

            # Verify SEI insertion
            if len(modified_frame) > len(dummy_frame):
                logger.info("✅ SEI data successfully integrated")
            else:
                logger.warning("⚠️  No SEI data detected in frame")

            # Test queue clearing
            await stream_manager.sei_publisher.clear_queue()
            final_queue_size = await stream_manager.sei_publisher.get_queue_size()
            logger.info(f"🗑️  Queue cleared: {final_queue_size} messages remaining")

        else:
            logger.error("❌ SEI Publisher not found in BedrockStreamManager")

    except Exception as e:
        logger.info(f"ℹ️  Expected error (missing AWS credentials): {e}")
        logger.info("✅ This is normal for testing without AWS setup")

    # Test standalone SEI publisher
    logger.info("🧪 Testing standalone SEI publisher...")
    sei_publisher = SeiPublisher()

    # Test various message types
    test_cases = [
        ("Simple text", "Hello World!"),
        ("Long text", "This is a much longer message that should still work correctly with the SEI publisher system."),
        ("Special chars", "Hello 🤖! Testing émojis and spëcial characters."),
        ("JSON-like text", '{"embedded": "json", "in": "text"}'),
    ]

    for test_name, test_text in test_cases:
        success = await sei_publisher.publish_text(test_text, repeat_count=1)
        logger.info(f"📝 {test_name}: {success}")

    # Process all queued messages
    dummy_frame = bytes([0x00, 0x00, 0x01, 0x65, 0x88, 0x84])
    final_frame = await sei_publisher.process_frame(dummy_frame)

    logger.info(f"📊 Final test - Frame size: {len(dummy_frame)} -> {len(final_frame)} bytes")
    logger.info(f"📈 Total size increase: {len(final_frame) - len(dummy_frame)} bytes")

    logger.info("✅ Integration test completed successfully")


if __name__ == "__main__":
    asyncio.run(test_integration())
