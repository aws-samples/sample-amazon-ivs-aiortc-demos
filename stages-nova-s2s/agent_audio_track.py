import asyncio
import logging
import numpy as np
import time
from fractions import Fraction
from av import AudioFrame
from aiortc import AudioStreamTrack

logger = logging.getLogger(__name__)


class AgentAudioTrack(AudioStreamTrack):
    """
    An audio track that streams Nova speech-to-speech responses - simplified for reliability
    """

    def __init__(self, agent_video_track=None, sample_rate=24000, channels=1):
        super().__init__()

        # Audio configuration
        self.sample_rate = sample_rate
        self.channels = channels
        self.agent_video_track = agent_video_track  # Reference to update throb

        # Larger chunks for smoother playback - optimized for production
        self.chunk_size_bytes = 960 * 4  # Increased from 960 to 3840 bytes (80ms at 24kHz)

        # Buffer management with minimum threshold - increased for production stability
        self.audio_buffer = bytearray()
        self.buffer_lock = asyncio.Lock()
        self.frame_count = 0
        self.max_buffer_size = sample_rate * 2 * 60  # 60 seconds max
        self.min_buffer_threshold = self.chunk_size_bytes * 5  # Keep 5 chunks minimum (increased from 3)

        # Audio batching for performance - optimized for production
        self.batch_buffer = bytearray()
        self.batch_size = self.chunk_size_bytes * 2  # Smaller batches for faster processing (reduced from 4)
        self.last_batch_time = time.time()
        self.batch_timeout = 0.020  # Reduced from 40ms to 20ms for faster processing

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

        # Fixed timing for consistent audio frame rate - optimized for production
        self.target_fps = 100.0  # Increased from 50 to 100 FPS (10ms chunks) for smoother playback

        # Adaptive buffering for production stability
        self.buffer_empty_threshold = 0.3  # Trigger buffer increase if >30% empty rate
        self.buffer_adjustment_factor = 1.5  # Factor to increase buffer when needed

        logger.info(
            f"🔊 AgentAudioTrack initialized - chunk_size: {self.chunk_size_bytes} bytes (~{self.chunk_size_bytes//2/sample_rate*1000:.1f}ms), "
            f"target_fps: {self.target_fps}, min_buffer: {self.min_buffer_threshold} bytes"
        )

    def set_peer_connection(self, pc):
        """Set the peer connection for stats collection"""
        self.peer_connection = pc
        logger.debug(f"🔗 Peer connection set for WebRTC stats: {pc is not None}")

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

            # Enhanced stats with buffer health and adaptive info
            current_buffer_size = len(self.audio_buffer)
            buffer_health = (current_buffer_size / self.min_buffer_threshold) * 100 if self.min_buffer_threshold > 0 else 0

            logger.debug(
                f"📊 Audio Stats - Uptime: {uptime:.1f}s, Frames: {self.frames_sent}, "
                f"FPS: {avg_fps:.1f}, Throughput: {avg_throughput/1024:.1f}KB/s, "
                f"Buffer empty rate: {buffer_empty_rate:.2%}, Buffer health: {buffer_health:.0f}%, "
                f"Min threshold: {self.min_buffer_threshold} bytes, Batch: {batch_buffer_size} bytes"
            )

    async def recv(self):
        """Generate and return audio frames from Nova responses - back to basics"""
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
                    logger.debug(f"🔊 Playing audio: {len(chunk_data)} bytes, {len(self.audio_buffer)} remaining")
                elif buffer_size > 0:
                    # Only use remaining data if we have enough, otherwise wait for more
                    if buffer_size >= self.min_buffer_threshold or buffer_size > self.chunk_size_bytes // 2:
                        remaining_data = bytes(self.audio_buffer)
                        self.audio_buffer.clear()
                        padding_needed = self.chunk_size_bytes - len(remaining_data)
                        chunk_data = remaining_data + bytes(padding_needed)
                        logger.debug(f"🔊 Playing remaining audio: used {len(remaining_data)} bytes + {padding_needed} silence")
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

            # Update video throb level based on this audio chunk
            if self.agent_video_track and len(audio_array) > 0:
                # Calculate RMS level for throb
                rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
                normalized_level = min(rms / 2000.0, 1.0)
                self.agent_video_track.update_throb_level(normalized_level)

            # Create AudioFrame
            frame = AudioFrame.from_ndarray(audio_array.reshape(1, -1), format="s16", layout="mono")

            # Set timing information
            frame.sample_rate = self.sample_rate
            frame.pts = self.frame_count
            frame.time_base = Fraction(1, self.sample_rate)

            # Update frame count
            self.frame_count += len(audio_array)

            # Adaptive timing for production stability
            # Calculate target sleep based on current FPS and buffer state
            target_sleep = 0.010  # Reduced from 15ms to 10ms for more responsive audio

            # Implement adaptive buffering - increase buffer size if empty rate is high
            if self.frames_sent > 100:  # Only after some frames for stable calculation
                empty_rate = self.buffer_empty_count / self.frames_sent
                if empty_rate > self.buffer_empty_threshold:
                    # Increase buffer threshold to reduce empty rate
                    old_threshold = self.min_buffer_threshold
                    self.min_buffer_threshold = int(self.min_buffer_threshold * self.buffer_adjustment_factor)
                    logger.info(
                        f"🔧 Adaptive buffering: increased threshold from {old_threshold} to {self.min_buffer_threshold} bytes (empty rate: {empty_rate:.2%})"
                    )

            # Adaptive sleep timing based on buffer state and FPS
            if self.avg_fps >= self.target_fps:
                if buffer_was_empty:
                    # When buffer is empty, sleep slightly longer to allow buffer to fill
                    await asyncio.sleep(target_sleep * 1.5)
                else:
                    # Normal operation - maintain precise timing
                    await asyncio.sleep(target_sleep)
            else:
                # If FPS is low, reduce sleep time to catch up
                await asyncio.sleep(target_sleep * 0.5)

            return frame

        except Exception as e:
            logger.error(f"Error in AgentAudioTrack.recv: {e}")
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

                    # Log batch processing with buffer health info
                    if old_buffer_size == 0 and new_buffer_size > 0:
                        logger.info(
                            f"🎵 Audio started: +{len(batch_data)} bytes (batched), buffer health: {new_buffer_size}/{self.min_buffer_threshold}"
                        )
                    else:
                        # Calculate buffer health percentage
                        buffer_health = (new_buffer_size / self.min_buffer_threshold) * 100
                        logger.debug(f"🎵 Batch processed: +{len(batch_data)} bytes, buffer: {new_buffer_size} bytes ({buffer_health:.0f}% health)")

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
            logger.info("🛑 Audio buffer cleared due to interruption")

        # Reset video throb to idle state
        if self.agent_video_track:
            self.agent_video_track.update_throb_level(0.0)

    async def stop(self):
        """Stop the audio track"""
        async with self.buffer_lock:
            self.audio_buffer.clear()
            self.batch_buffer.clear()  # Clear batch buffer too
