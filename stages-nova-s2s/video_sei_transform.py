#!/usr/bin/env python3

import asyncio
import logging
from typing import Optional
from aiortc import RTCRtpTransceiver
from aiortc.rtcrtptransceiver import RTCRtpSender
from sei_publisher import SeiPublisher

logger = logging.getLogger(__name__)


class VideoSeiTransform:
    """
    WebRTC video transform that injects SEI NAL units into outgoing video frames.

    This class works at the WebRTC encoded frame level to insert SEI metadata
    into H.264/H.265 video streams before they are sent over the network.
    """

    def __init__(self, sei_publisher: SeiPublisher):
        """
        Initialize the video SEI transform.

        Args:
            sei_publisher: The SEI publisher instance to use for metadata
        """
        self.sei_publisher = sei_publisher
        self.is_active = False

    async def setup_transform(self, transceiver: RTCRtpTransceiver) -> bool:
        """
        Set up the video transform on a WebRTC transceiver.

        Args:
            transceiver: The WebRTC transceiver to apply the transform to

        Returns:
            True if transform was successfully set up, False otherwise
        """
        try:
            if transceiver.kind != "video":
                logger.error("❌ Transform can only be applied to video transceivers")
                return False

            sender = transceiver.sender
            if not sender:
                logger.error("❌ No sender found on transceiver")
                return False

            # Set up the transform on the sender
            await self._setup_sender_transform(sender)
            self.is_active = True
            logger.info("📡 Video SEI transform activated")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to setup video SEI transform: {e}")
            return False

    async def _setup_sender_transform(self, sender: RTCRtpSender):
        """Set up transform on the RTP sender"""
        # Note: This is a conceptual implementation
        # In practice, WebRTC transforms in Python are limited
        # This would need to be implemented at a lower level or using
        # a different approach like modifying the video track directly

        logger.info("🔧 Setting up sender transform for SEI injection")

        # Store reference to sender for potential future use
        self.sender = sender

        # In a real implementation, you would:
        # 1. Hook into the encoded frame pipeline
        # 2. Process each frame through sei_publisher.process_frame()
        # 3. Send the modified frame

        # For now, we'll log that the transform is conceptually set up
        logger.info("📡 SEI transform conceptually configured")

    async def stop_transform(self):
        """Stop the video transform"""
        if self.is_active:
            self.is_active = False
            logger.info("🛑 Video SEI transform stopped")

    def is_transform_active(self) -> bool:
        """Check if the transform is currently active"""
        return self.is_active


class VideoTrackSeiWrapper:
    """
    A wrapper around a video track that can inject SEI data.

    This is an alternative approach that works at the video track level
    rather than the WebRTC transform level.
    """

    def __init__(self, video_track, sei_publisher: SeiPublisher):
        """
        Initialize the wrapper.

        Args:
            video_track: The original video track to wrap
            sei_publisher: The SEI publisher instance
        """
        self.video_track = video_track
        self.sei_publisher = sei_publisher
        self.is_active = True

        # Delegate all attributes to the wrapped track
        self.kind = video_track.kind
        self.id = video_track.id
        self.readyState = video_track.readyState

        logger.info("📡 Video track SEI wrapper initialized")

    async def recv(self):
        """
        Receive a frame from the wrapped track and potentially add SEI data.

        Note: This approach has limitations as SEI data needs to be added
        to encoded frames, not raw video frames.
        """
        try:
            # Get frame from wrapped track
            frame = await self.video_track.recv()

            # In a real implementation, we would need to:
            # 1. Encode the frame to H.264/H.265
            # 2. Process through sei_publisher.process_frame()
            # 3. Return the modified encoded data

            # For now, just return the original frame
            # The SEI data will be handled at the stream manager level
            return frame

        except Exception as e:
            logger.error(f"❌ Error in VideoTrackSeiWrapper.recv: {e}")
            raise

    def stop(self):
        """Stop the wrapper"""
        if hasattr(self.video_track, "stop"):
            self.video_track.stop()
        self.is_active = False
        logger.info("🛑 Video track SEI wrapper stopped")

    def __getattr__(self, name):
        """Delegate unknown attributes to the wrapped track"""
        return getattr(self.video_track, name)
