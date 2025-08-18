#!/usr/bin/env python3

import logging
import threading
from typing import Iterator, Optional
import av

logger = logging.getLogger(__name__)

# Global SEI publisher reference
_global_sei_publisher: Optional["SeiPublisher"] = None
_sei_lock = threading.Lock()


def set_global_sei_publisher(sei_publisher):
    """Set the global SEI publisher for the H.264 encoder patch"""
    global _global_sei_publisher
    with _sei_lock:
        _global_sei_publisher = sei_publisher
        logger.info("📡 Global SEI publisher set for H.264 encoder patch")


def get_global_sei_publisher():
    """Get the global SEI publisher"""
    global _global_sei_publisher
    with _sei_lock:
        return _global_sei_publisher


def inject_sei_into_bitstream(original_bitstream: bytes) -> bytes:
    """
    Inject SEI NAL units into an H.264 bitstream.

    This function finds the appropriate insertion point (before the first slice NAL unit)
    and injects any queued SEI data.
    """
    sei_publisher = get_global_sei_publisher()
    if not sei_publisher:
        return original_bitstream

    try:
        # Check if there are any queued SEI messages
        # Note: We need to make this synchronous since aiortc encode is not async
        import asyncio

        # Try to get the current event loop, create one if none exists
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, we can't use run_until_complete
                # Instead, we'll use a synchronous approach
                queue_size = len(sei_publisher.message_queue) if hasattr(sei_publisher, "message_queue") else 0
                if queue_size == 0:
                    return original_bitstream

                # Process messages synchronously
                modified_bitstream = _process_sei_sync(sei_publisher, original_bitstream)
                return modified_bitstream
            else:
                # Loop exists but not running, we can use it
                queue_size = loop.run_until_complete(sei_publisher.get_queue_size())
                if queue_size == 0:
                    return original_bitstream

                # Process the bitstream with SEI data
                modified_bitstream = loop.run_until_complete(sei_publisher.process_frame(original_bitstream))
                logger.debug(f"📡 H.264 patch: Processed bitstream {len(original_bitstream)} -> {len(modified_bitstream)} bytes")
                return modified_bitstream

        except RuntimeError:
            # No event loop, create a new one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                queue_size = loop.run_until_complete(sei_publisher.get_queue_size())
                if queue_size == 0:
                    return original_bitstream

                modified_bitstream = loop.run_until_complete(sei_publisher.process_frame(original_bitstream))
                logger.debug(f"📡 H.264 patch: Processed bitstream {len(original_bitstream)} -> {len(modified_bitstream)} bytes")
                return modified_bitstream
            finally:
                loop.close()

    except Exception as e:
        logger.error(f"❌ Error in SEI injection: {e}")
        return original_bitstream


def _process_sei_sync(sei_publisher, original_bitstream: bytes) -> bytes:
    """
    Synchronous SEI processing for when async is not available.
    """
    try:
        # Access the message queue directly (not ideal but necessary for sync operation)
        if not hasattr(sei_publisher, "message_queue") or not sei_publisher.message_queue:
            return original_bitstream

        # Get messages to process
        messages_to_process = sei_publisher.message_queue.copy()
        sei_publisher.message_queue.clear()

        if not messages_to_process:
            return original_bitstream

        modified_data = original_bitstream

        for message in messages_to_process:
            try:
                # Create SEI NAL unit for this message
                sei_unit = sei_publisher._create_sei_nal_unit(sei_publisher.SEND_SEI_UUID, message.payload)

                # Insert into bitstream data (repeat for reliability)
                for _ in range(message.repeat_count):
                    modified_data = sei_publisher._insert_sei_unit(modified_data, sei_unit)

                logger.info(f"📡 H.264 patch: Inserted SEI unit: {len(sei_unit)} bytes, repeated {message.repeat_count} times")

            except Exception as e:
                logger.error(f"❌ Failed to insert SEI unit in H.264 patch: {e}")

        return modified_data

    except Exception as e:
        logger.error(f"❌ Error in synchronous SEI processing: {e}")
        return original_bitstream


def patch_h264_encoder():
    """
    Monkey patch the aiortc H.264 encoder to inject SEI data.
    """
    try:
        # Import the aiortc H.264 encoder
        from aiortc.codecs.h264 import H264Encoder

        # Store the original _encode_frame method
        original_encode_frame = H264Encoder._encode_frame

        def patched_encode_frame(self, frame: av.VideoFrame, force_keyframe: bool) -> Iterator[bytes]:
            """
            Patched version of _encode_frame that injects SEI data.
            """
            # Call the original encode_frame method
            for bitstream_chunk in original_encode_frame(self, frame, force_keyframe):
                # Inject SEI data into each bitstream chunk
                modified_chunk = inject_sei_into_bitstream(bitstream_chunk)
                yield modified_chunk

        # Apply the patch
        H264Encoder._encode_frame = patched_encode_frame

        logger.info("✅ H.264 encoder successfully patched for SEI injection")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to patch H.264 encoder: {e}")
        return False


def unpatch_h264_encoder():
    """
    Remove the H.264 encoder patch (for testing/cleanup).
    """
    try:
        # This would require storing the original method, which we could do
        # For now, just log that unpatching was requested
        logger.info("🔄 H.264 encoder unpatch requested (not implemented)")

    except Exception as e:
        logger.error(f"❌ Failed to unpatch H.264 encoder: {e}")


# Auto-apply patch when module is imported
if __name__ != "__main__":
    patch_h264_encoder()
