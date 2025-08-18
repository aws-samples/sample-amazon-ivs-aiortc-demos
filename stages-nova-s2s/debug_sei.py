#!/usr/bin/env python3

import asyncio
import logging
import time

# Configure logging to show debug messages
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")

# Set specific loggers to show SEI activity
logging.getLogger("sei_publisher").setLevel(logging.DEBUG)
logging.getLogger("sei_frame_processor").setLevel(logging.DEBUG)
logging.getLogger("bedrock_stream_manager").setLevel(logging.INFO)

logger = logging.getLogger(__name__)


def enable_sei_debug():
    """Enable debug logging for SEI components"""
    logger.info("🔍 SEI Debug Mode Enabled")
    logger.info("=" * 60)
    logger.info("This will show detailed SEI processing information:")
    logger.info("- 📡 SEI message queuing")
    logger.info("- 🎬 Frame processing activity")
    logger.info("- 📊 Processing statistics")
    logger.info("- ❌ Any errors or issues")
    logger.info("=" * 60)


if __name__ == "__main__":
    enable_sei_debug()

    # Keep the script running to show logs
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("🛑 SEI debug mode stopped")
