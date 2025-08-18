#!/usr/bin/env python3

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class SeiFrameProcessor:
    """
    A frame processor that periodically processes queued SEI messages.

    Since Python WebRTC libraries don't provide easy access to encoded frames,
    this processor simulates frame processing by periodically checking for
    queued SEI messages and logging their processing.
    """

    def __init__(self, sei_publisher, processing_interval: float = 0.033):  # ~30 FPS
        """
        Initialize the frame processor.

        Args:
            sei_publisher: The SEI publisher instance
            processing_interval: How often to process frames (seconds)
        """
        self.sei_publisher = sei_publisher
        self.processing_interval = processing_interval
        self.is_running = False
        self.processor_task = None
        self.frames_processed = 0
        self.sei_messages_processed = 0

    async def start(self):
        """Start the frame processor"""
        if self.is_running:
            logger.warning("⚠️  SEI frame processor already running")
            return

        self.is_running = True
        self.processor_task = asyncio.create_task(self._process_loop())
        logger.info(f"🎬 SEI frame processor started (interval: {self.processing_interval:.3f}s)")

    async def stop(self):
        """Stop the frame processor"""
        if not self.is_running:
            return

        self.is_running = False
        if self.processor_task:
            self.processor_task.cancel()
            try:
                await self.processor_task
            except asyncio.CancelledError:
                pass

        logger.info("🛑 SEI frame processor stopped")

    async def _process_loop(self):
        """Main processing loop"""
        logger.info("🔄 SEI frame processing loop started")

        try:
            while self.is_running:
                await self._process_frame()
                await asyncio.sleep(self.processing_interval)

        except asyncio.CancelledError:
            logger.info("🛑 SEI frame processor cancelled")
        except Exception as e:
            logger.error(f"❌ Error in SEI frame processor: {e}")

    async def _process_frame(self):
        """Process a single frame"""
        self.frames_processed += 1

        # Check if there are queued SEI messages
        queue_size = await self.sei_publisher.get_queue_size()

        if queue_size > 0:
            # Create a dummy frame for processing
            dummy_frame = self._create_dummy_h264_frame()

            # Process the frame (this will consume queued messages)
            modified_frame = await self.sei_publisher.process_frame(dummy_frame)

            if len(modified_frame) > len(dummy_frame):
                self.sei_messages_processed += queue_size
                logger.info(f"📡 Processed {queue_size} SEI messages in frame {self.frames_processed}")
                logger.debug(f"📏 Frame size: {len(dummy_frame)} -> {len(modified_frame)} bytes")

    def _create_dummy_h264_frame(self) -> bytes:
        """Create a dummy H.264 frame for SEI processing"""
        # This simulates a basic H.264 frame structure
        return bytes(
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
            ]
        )

    def get_stats(self) -> dict:
        """Get processing statistics"""
        return {
            "frames_processed": self.frames_processed,
            "sei_messages_processed": self.sei_messages_processed,
            "is_running": self.is_running,
            "processing_interval": self.processing_interval,
        }
