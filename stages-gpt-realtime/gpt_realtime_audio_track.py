import asyncio
import logging
import numpy as np
import time
from fractions import Fraction
from av import AudioFrame
from aiortc import AudioStreamTrack

logger = logging.getLogger(__name__)


class GptRealtimeAudioTrack(AudioStreamTrack):
    """
    An audio track that streams gpt-realtime API responses
    """

    def __init__(self, gpt_realtime_video_track=None, sample_rate=24000, channels=1, chunk_size=None):
        super().__init__()

        # Audio configuration
        self.sample_rate = sample_rate
        self.channels = channels
        self.gpt_realtime_video_track = gpt_realtime_video_track  # Reference to update visualization

        # Chunk size for 20ms at 24kHz (480 samples * 2 bytes per sample)
        self.chunk_size_bytes = 480 * 2  # 20ms chunks

        # Buffer management
        self.audio_buffer = bytearray()
        self.buffer_lock = asyncio.Lock()
        self.frame_count = 0
        self.max_buffer_size = sample_rate * 2 * 30  # 30 seconds max
        self.min_buffer_threshold = self.chunk_size_bytes * 3  # Keep 3 chunks minimum

        # Audio batching for performance
        self.batch_buffer = bytearray()
        self.batch_size = self.chunk_size_bytes * 4  # Batch 4 chunks at a time (80ms)
        self.last_batch_time = time.time()
        self.batch_timeout = 0.040  # Force batch processing after 40ms max

        # WebRTC stats debugging
        self.last_stats_time = 0
        self.stats_interval = 5.0  # Print stats every 5 seconds
        self.peer_connection = None  # Will be set externally
        self.avg_fps = 0

        # Performance tracking
        self.frames_sent = 0
        self.bytes_processed = 0
        self.buffer_empty_count = 0
        self.start_time = time.time()

        # Fixed timing for consistent audio frame rate
        self.target_fps = 50.0  # Target 50 FPS (20ms chunks)

        logger.info(
            f"🔊 GptRealtimeAudioTrack initialized - chunk_size: {self.chunk_size_bytes} bytes (~{self.chunk_size_bytes//2/sample_rate*1000:.1f}ms)"
        )

    def set_peer_connection(self, pc):
        """Set the peer connection for stats collection"""
        self.peer_connection = pc
        logger.info(f"🔗 Peer connection set for WebRTC stats: {pc is not None}")

    async def _print_debug_stats(self):
        """Print WebRTC and performance stats every 5 seconds"""
        current_time = time.time()
        if current_time - self.last_stats_time >= self.stats_interval:
            self.last_stats_time = current_time

            # Calculate performance metrics
            uptime = current_time - self.start_time
            avg_fps = self.frames_sent / uptime if uptime > 0 else 0
            self.avg_fps = avg_fps
            avg_throughput = self.bytes_processed / uptime if uptime > 0 else 0
            buffer_empty_rate = self.buffer_empty_count / self.frames_sent if self.frames_sent > 0 else 0

            # Get current batch buffer size for stats
            batch_buffer_size = len(self.batch_buffer)

            # logger.info(
            #     f"📊 Gpt Realtime Audio Stats - Uptime: {uptime:.1f}s, Frames: {self.frames_sent}, "
            #     f"FPS: {avg_fps:.1f}, Throughput: {avg_throughput/1024:.1f}KB/s, "
            #     f"Buffer empty rate: {buffer_empty_rate:.2%}, Batch: {batch_buffer_size} bytes"
            # )

    async def recv(self):
        """Generate and return audio frames from Gpt Realtime responses"""
        try:
            # Print debug stats periodically
            await self._print_debug_stats()

            # Check if we need to flush batch due to timeout
            current_time = time.time()
            if len(self.batch_buffer) > 0 and current_time - self.last_batch_time >= self.batch_timeout:
                await self.flush_batch()

            # Capture buffer size for timing logic
            buffer_was_empty = False

            async with self.buffer_lock:
                buffer_size = len(self.audio_buffer)
                if buffer_size >= self.chunk_size_bytes:
                    # Extract a chunk from the buffer
                    chunk_data = bytes(self.audio_buffer[: self.chunk_size_bytes])
                    del self.audio_buffer[: self.chunk_size_bytes]
                elif buffer_size > 0:
                    # Only use remaining data if we have enough, otherwise wait for more
                    if buffer_size >= self.min_buffer_threshold or buffer_size > self.chunk_size_bytes // 2:
                        remaining_data = bytes(self.audio_buffer)
                        self.audio_buffer.clear()
                        padding_needed = self.chunk_size_bytes - len(remaining_data)
                        chunk_data = remaining_data + bytes(padding_needed)
                    else:
                        # Wait for more data to avoid gaps
                        chunk_data = bytes(self.chunk_size_bytes)
                        buffer_was_empty = True
                        logger.debug(f"🔊 Waiting for more audio data: {buffer_size} bytes available, need {self.min_buffer_threshold}")
                else:
                    # Generate silence if no data at all
                    chunk_data = bytes(self.chunk_size_bytes)
                    buffer_was_empty = True

            # Track performance metrics
            self.frames_sent += 1
            self.bytes_processed += len(chunk_data)
            if buffer_was_empty:
                self.buffer_empty_count += 1

            # Convert bytes to numpy array
            audio_array = np.frombuffer(chunk_data, dtype=np.int16)

            # Update video visualization based on this audio chunk
            if self.gpt_realtime_video_track and len(audio_array) > 0:
                # Calculate RMS level for visualization
                rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
                normalized_level = min(rms / 2000.0, 1.0)
                self.gpt_realtime_video_track.update_audio_level(normalized_level)

            # Create AudioFrame
            frame = AudioFrame.from_ndarray(audio_array.reshape(1, -1), format="s16", layout="mono")

            # Set timing information
            frame.sample_rate = self.sample_rate
            frame.pts = self.frame_count
            frame.time_base = Fraction(1, self.sample_rate)

            # Update frame count
            self.frame_count += len(audio_array)

            # Fixed timing to maintain proper audio frame rate
            target_sleep = 0.015  # 20ms = 50 FPS

            if self.avg_fps >= 50:
                if buffer_was_empty:
                    # When buffer is empty, we can sleep a bit longer to reduce CPU usage
                    await asyncio.sleep(target_sleep)
                else:
                    # When we have audio data, maintain precise timing
                    await asyncio.sleep(target_sleep)
            # If FPS < 50, don't sleep - let it run as fast as possible to catch up

            return frame

        except Exception as e:
            logger.error(f"Error in GptRealtimeAudioTrack.recv: {e}")
            raise

    async def add_audio_data(self, audio_data: bytes):
        """Add audio data to the batch buffer for efficient processing"""
        try:
            async with self.buffer_lock:
                old_buffer_size = len(self.audio_buffer)

                # Validate audio data
                if not audio_data or len(audio_data) == 0:
                    return

                # Add to batch buffer first
                self.batch_buffer.extend(audio_data)
                current_time = time.time()

                # Process batch if it's large enough or timeout reached
                should_process_batch = len(self.batch_buffer) >= self.batch_size or (
                    len(self.batch_buffer) > 0 and current_time - self.last_batch_time >= self.batch_timeout
                )

                if should_process_batch:
                    # Move batched data to main buffer
                    batch_data = bytes(self.batch_buffer)
                    self.batch_buffer.clear()
                    self.last_batch_time = current_time

                    self.audio_buffer.extend(batch_data)
                    new_buffer_size = len(self.audio_buffer)

                    # Log batch processing
                    if old_buffer_size == 0 and new_buffer_size > 0:
                        logger.info(f"🎵 Gpt Realtime audio started: +{len(batch_data)} bytes (batched)")
                    else:
                        logger.debug(f"🎵 Batch processed: +{len(batch_data)} bytes, buffer: {new_buffer_size} bytes")

                    # Prevent buffer from growing too large
                    if len(self.audio_buffer) > self.max_buffer_size:
                        # Remove oldest data more conservatively
                        excess = len(self.audio_buffer) - (self.max_buffer_size // 2)
                        del self.audio_buffer[:excess]
                        logger.warning(f"Audio buffer too large, removed {excess} bytes")
                else:
                    # Just accumulating in batch buffer
                    logger.debug(f"🎵 Batching: {len(self.batch_buffer)}/{self.batch_size} bytes")

        except Exception as e:
            logger.error(f"Error adding audio data: {e}")

    async def flush_batch(self):
        """Force process any remaining batched audio data"""
        try:
            async with self.buffer_lock:
                if len(self.batch_buffer) > 0:
                    batch_data = bytes(self.batch_buffer)
                    self.batch_buffer.clear()
                    self.last_batch_time = time.time()

                    self.audio_buffer.extend(batch_data)
                    logger.debug(f"🎵 Batch flushed: +{len(batch_data)} bytes")
        except Exception as e:
            logger.error(f"Error flushing batch: {e}")

    async def stop_current_audio(self):
        """Stop current audio playback by clearing the buffer (for interruptions)"""
        async with self.buffer_lock:
            self.audio_buffer.clear()
            self.batch_buffer.clear()  # Clear batch buffer too
            logger.info("🛑 Gpt Realtime audio buffer cleared due to interruption")

        # Reset video visualization to idle state
        if self.gpt_realtime_video_track:
            self.gpt_realtime_video_track.update_audio_level(0.0)

    async def stop(self):
        """Stop the audio track"""
        async with self.buffer_lock:
            self.audio_buffer.clear()
            self.batch_buffer.clear()  # Clear batch buffer too
