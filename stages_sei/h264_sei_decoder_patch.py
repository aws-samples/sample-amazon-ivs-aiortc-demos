#!/usr/bin/env python3

# Standard library imports
import asyncio
import logging
import threading
import traceback
from typing import Optional

# Third-party imports
import av

logger = logging.getLogger(__name__)

# Constants
H264_CODEC_NAMES = ["h264", "libx264"]
PYAV_FRAME_LOG_INTERVAL = 100


class H264DecoderPatcher:
    """
    Simple class to encapsulate H.264 decoder patching logic.
    Maintains backward compatibility with existing global functions.
    """

    def __init__(self):
        self.sei_subscriber: Optional["SeiSubscriber"] = None
        self.is_patched = False
        self._lock = threading.Lock()

    def set_sei_subscriber(self, sei_subscriber: "SeiSubscriber") -> None:
        """Set the SEI subscriber for extraction"""
        with self._lock:
            self.sei_subscriber = sei_subscriber

    def get_sei_subscriber(self) -> Optional["SeiSubscriber"]:
        """Get the current SEI subscriber"""
        with self._lock:
            return self.sei_subscriber

    def extract_sei_from_packet(self, packet_data: bytes) -> None:
        """
        Extract SEI messages from H.264 packet data using the class instance.
        """
        subscriber = self.get_sei_subscriber()
        if not subscriber:
            return

        try:
            # Call SEI extraction synchronously since we're in the video-decoder thread
            # which doesn't have an asyncio event loop
            messages = subscriber.process_packet_data_sync(packet_data)

            # If we found SEI messages, log them
            if messages:
                logger.debug(f"📡 Extracted {len(messages)} SEI messages from H.264 data")
                for msg in messages:
                    if hasattr(subscriber, "message_callback") and subscriber.message_callback:
                        subscriber.message_callback(msg)

        except Exception as e:
            logger.debug(f"SEI extraction failed: {e}")

    def patch_h264_decoder(self, enable_patches: bool = True) -> bool:
        """
        Apply H.264 decoder patches using the class instance.
        """
        if not enable_patches:
            logger.info("📡 H.264 decoder patches disabled")
            return False

        if self.is_patched:
            logger.debug("📡 H.264 decoder already patched")
            return True

        patched_methods = []

        # Try to patch aiortc's H.264 decoder (CONSERVATIVE APPROACH)
        if _patch_aiortc_decoder():
            patched_methods.append("H264Decoder.decode")

        # Also try to patch PyAV CodecContext.decode as a backup
        if _patch_pyav_decoder():
            patched_methods.append("av.CodecContext.decode")

        if patched_methods:
            logger.info(f"📡 H.264 decoder patches applied: {', '.join(patched_methods)}")
            self.is_patched = True
            return True
        else:
            logger.warning("❌ No H.264 decoder patches could be applied")
            return False

    def get_patch_status(self) -> dict:
        """
        Get current patch status information.

        Returns:
            Dictionary containing patch status information
        """
        with self._lock:
            return {
                "is_patched": self.is_patched,
                "has_sei_subscriber": self.sei_subscriber is not None,
            }


# Global instance and backward compatibility
_patcher_instance = H264DecoderPatcher()
_global_sei_subscriber: Optional["SeiSubscriber"] = None
_sei_lock = threading.Lock()


def set_global_sei_subscriber(sei_subscriber: "SeiSubscriber") -> None:
    """Set the global SEI subscriber for the H.264 decoder patch"""
    global _global_sei_subscriber
    with _sei_lock:
        _global_sei_subscriber = sei_subscriber
        _patcher_instance.set_sei_subscriber(sei_subscriber)
        logger.info("📡 Global SEI subscriber set for H.264 decoder patch")


def get_global_sei_subscriber() -> Optional["SeiSubscriber"]:
    """Get the global SEI subscriber"""
    global _global_sei_subscriber
    with _sei_lock:
        return _global_sei_subscriber


def extract_sei_from_packet(packet_data: bytes) -> None:
    """
    Extract SEI messages from H.264 packet data before decoding.
    Since the decoder runs in a separate thread without an event loop,
    we need to call the SEI extraction synchronously.
    """
    # Use the class instance method for consistency
    _patcher_instance.extract_sei_from_packet(packet_data)


def _patch_aiortc_decoder() -> bool:
    """
    Patch aiortc H.264 decoder for SEI extraction.

    Returns:
        True if patch was successfully applied, False otherwise
    """
    try:
        from aiortc.codecs.h264 import H264Decoder

        # Patch the decode method (receives encoded H.264 data)
        if hasattr(H264Decoder, "decode"):
            original_decode = H264Decoder.decode

            def patched_decode(self, encoded_frame):
                """Patched version of decode that extracts SEI before decoding (conservative)"""
                # Call original decode method first to ensure normal operation
                result = original_decode(self, encoded_frame)

                # Then try SEI extraction in a safe way
                try:
                    if encoded_frame and hasattr(encoded_frame, "data") and encoded_frame.data:
                        h264_data = encoded_frame.data
                        if len(h264_data) > 0:
                            # Initialize frame counter quietly
                            if not hasattr(self, "_sei_frame_count"):
                                self._sei_frame_count = 0

                            self._sei_frame_count += 1

                            extract_sei_from_packet(h264_data)
                except Exception as e:
                    logger.debug(f"SEI extraction error: {e}")

                return result

            H264Decoder.decode = patched_decode
            logger.info("✅ H.264 decoder.decode patched for SEI extraction (conservative mode)")
            return True

    except Exception as e:
        logger.error(f"Failed to patch H.264 decoder: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")

    return False


def _patch_pyav_decoder() -> bool:
    """
    Patch PyAV CodecContext decoder for SEI extraction as backup.

    Returns:
        True if patch was successfully applied, False otherwise
    """
    try:
        # Hook into av.CodecContext.decode
        if hasattr(av.CodecContext, "decode"):
            original_av_decode = av.CodecContext.decode

            def patched_av_decode(self, packet=None):
                """Patched PyAV decode method that intercepts H.264 packets"""
                # Call original decode method first
                result = original_av_decode(self, packet)

                try:
                    # Check if this is an H.264 decoder
                    is_h264 = hasattr(self, "name") and self.name in H264_CODEC_NAMES

                    if is_h264 and packet:
                        try:
                            packet_bytes = bytes(packet)
                            if len(packet_bytes) > 0:
                                # Only log periodically to avoid spam
                                if not hasattr(self, "_pyav_frame_count"):
                                    self._pyav_frame_count = 0
                                    logger.debug("🎯 PyAV H.264 decode patch active")

                                self._pyav_frame_count += 1

                                # Log every 100 frames
                                if self._pyav_frame_count % PYAV_FRAME_LOG_INTERVAL == 0:
                                    logger.debug(f"🎯 PyAV processed {self._pyav_frame_count} H.264 packets")

                                extract_sei_from_packet(packet_bytes)
                        except Exception as e:
                            logger.debug(f"SEI extraction from PyAV decode failed: {e}")
                except Exception as e:
                    logger.debug(f"PyAV decode patch error: {e}")

                return result

            # Note: This may fail due to immutable type, but we try anyway
            try:
                av.CodecContext.decode = patched_av_decode
                logger.info("✅ PyAV CodecContext.decode patched for SEI extraction (backup method)")
                return True
            except TypeError as e:
                logger.debug(f"Could not patch av.CodecContext.decode (immutable type): {e}")

    except Exception as e:
        logger.debug(f"Failed to patch PyAV decoder: {e}")

    return False


def patch_h264_decoder(enable_patches: bool = True) -> bool:
    """
    Monkey patch the aiortc H.264 decoder to extract SEI data before decoding.
    Conservative approach - only enable the safest patches.
    """
    # Use the class instance method for consistency
    return _patcher_instance.patch_h264_decoder(enable_patches)


def get_patch_status() -> dict:
    """
    Get current patch status (backward compatibility).

    Returns:
        Dictionary containing patch status information
    """
    return _patcher_instance.get_patch_status()


def _initialize_patches() -> None:
    """Initialize patches on module import"""
    logger.info("🔧 h264_sei_decoder_patch module imported")
    patch_result = patch_h264_decoder(enable_patches=True)
    logger.info(f"🔧 H.264 decoder patch result: {patch_result}")


# Auto-apply decoder patch when module is imported (enable conservative patches)
_initialize_patches()
