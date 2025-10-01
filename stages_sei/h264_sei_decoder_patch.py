#!/usr/bin/env python3

import logging
import threading
from typing import Optional
import av
import asyncio

logger = logging.getLogger(__name__)

# Global SEI subscriber reference
_global_sei_subscriber: Optional["SeiSubscriber"] = None
_sei_lock = threading.Lock()


def set_global_sei_subscriber(sei_subscriber):
    """Set the global SEI subscriber for the H.264 decoder patch"""
    global _global_sei_subscriber
    with _sei_lock:
        _global_sei_subscriber = sei_subscriber
        logger.info("📡 Global SEI subscriber set for H.264 decoder patch")


def get_global_sei_subscriber():
    """Get the global SEI subscriber"""
    global _global_sei_subscriber
    with _sei_lock:
        return _global_sei_subscriber


def extract_sei_from_packet(packet_data: bytes):
    """
    Extract SEI messages from H.264 packet data before decoding.
    Since the decoder runs in a separate thread without an event loop,
    we need to call the SEI extraction synchronously.
    """
    subscriber = get_global_sei_subscriber()
    if not subscriber:
        return

    try:
        # Call SEI extraction synchronously since we're in the video-decoder thread
        # which doesn't have an asyncio event loop
        messages = subscriber.process_packet_data_sync(packet_data)

        # If we found SEI messages, log them
        if messages:
            logger.info(f"📡 Extracted {len(messages)} SEI messages from H.264 data")
            for msg in messages:
                if hasattr(subscriber, "message_callback") and subscriber.message_callback:
                    subscriber.message_callback(msg)

    except Exception as e:
        logger.debug(f"SEI extraction failed: {e}")


def patch_h264_decoder(enable_patches=True):
    """
    Monkey patch the aiortc H.264 decoder to extract SEI data before decoding.
    Conservative approach - only enable the safest patches.
    """
    if not enable_patches:
        logger.info("📡 H.264 decoder patches disabled")
        return False

    patched_methods = []

    # Try to patch aiortc's H.264 decoder (CONSERVATIVE APPROACH)
    # This is safer than patching PyAV directly
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
            patched_methods.append("H264Decoder.decode")
            logger.info("✅ H.264 decoder.decode patched for SEI extraction (conservative mode)")

    except Exception as e:
        logger.error(f"Failed to patch H.264 decoder: {e}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")

    # Also try to patch PyAV CodecContext.decode as a backup
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
                    is_h264 = hasattr(self, "name") and self.name in ["h264", "libx264"]

                    if is_h264 and packet:
                        try:
                            packet_bytes = bytes(packet)
                            if len(packet_bytes) > 0:
                                # Only log periodically to avoid spam
                                if not hasattr(self, "_pyav_frame_count"):
                                    self._pyav_frame_count = 0
                                    logger.debug(f"🎯 PyAV H.264 decode patch active")

                                self._pyav_frame_count += 1

                                # Log every 100 frames
                                if self._pyav_frame_count % 100 == 0:
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
                patched_methods.append("av.CodecContext.decode")
                logger.info("✅ PyAV CodecContext.decode patched for SEI extraction (backup method)")
            except TypeError as e:
                logger.debug(f"Could not patch av.CodecContext.decode (immutable type): {e}")

    except Exception as e:
        logger.debug(f"Failed to patch PyAV decoder: {e}")

    # DISABLED: RTCRtpReceiver patch (can interfere with connection)
    # try:
    #     from aiortc.rtcrtpreceiver import RTCRtpReceiver
    #     # ... RTP receiver patch code ...
    # except Exception as e:
    #     logger.debug(f"Failed to patch RTCRtpReceiver: {e}")

    # DISABLED: MediaStreamTrack patch (can interfere with connection)
    # try:
    #     from aiortc import MediaStreamTrack
    #     # ... media stream track patch code ...
    # except Exception as e:
    #     logger.debug(f"Failed to patch MediaStreamTrack: {e}")

    if patched_methods:
        logger.info(f"📡 H.264 decoder patches applied: {', '.join(patched_methods)}")
        return True
    else:
        logger.warning("❌ No H.264 decoder patches could be applied")
        return False


# Auto-apply decoder patch when module is imported (enable conservative patches)
logger.info("🔧 h264_sei_decoder_patch module imported")
patch_result = patch_h264_decoder(enable_patches=True)  # Re-enable patches
logger.info(f"🔧 H.264 decoder patch result: {patch_result}")
