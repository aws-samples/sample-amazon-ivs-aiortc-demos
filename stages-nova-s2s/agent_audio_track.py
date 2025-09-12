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

    def __init__(self, agent_video_track=None, sample_rate=24000, channels=1, chunk_size=None):
        super().__init__()

        # Audio configuration
        self.sample_rate = sample_rate
        self.channels = channels
        self.agent_video_track = agent_video_track  # Reference to update throb

        # Larger chunks for smoother playback - reduces gaps
        self.chunk_size_bytes = 480 * 2  # 480 samples * 2 bytes per sample (20ms at 24kHz)

        # Buffer management with minimum threshold
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

        # Track current audio session
        self.current_audio_session = None

        # WebRTC stats debugging
        self.last_stats_time = 0
        self.stats_interval = 5.0  # Print stats every 5 seconds
        self.peer_connection = None  # Will be set externally

        # Performance tracking
        self.frames_sent = 0
        self.bytes_processed = 0
        self.buffer_empty_count = 0
        self.start_time = time.time()

        # Adaptive timing
        self.target_fps = 50.0  # Target 50 FPS (20ms chunks)
        self.current_delay_empty = 0.010  # Start with 10ms for empty buffer
        self.current_delay_normal = 0.020  # Start with 20ms for normal
        self.last_fps_check = time.time()
        self.fps_check_interval = 0.5  # Adjust every 500ms for more responsive tuning

        # Dynamic chunk sizing
        self.base_chunk_size_bytes = self.chunk_size_bytes  # Store original
        self.last_network_check = time.time()
        self.network_check_interval = 3.0  # Check network every 3 seconds
        self.recent_rtt_samples = []
        self.recent_jitter_samples = []

        logger.info(
            f"🔊 AgentAudioTrack initialized - chunk_size: {self.chunk_size_bytes} bytes (~{self.chunk_size_bytes//2/sample_rate*1000:.1f}ms)"
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
            avg_throughput = self.bytes_processed / uptime if uptime > 0 else 0
            buffer_empty_rate = self.buffer_empty_count / self.frames_sent if self.frames_sent > 0 else 0

            # Get current batch buffer size for stats
            batch_buffer_size = len(self.batch_buffer)

            logger.info(
                f"📊 Audio Stats - Uptime: {uptime:.1f}s, Frames: {self.frames_sent}, "
                f"FPS: {avg_fps:.1f}, Throughput: {avg_throughput/1024:.1f}KB/s, "
                f"Buffer empty rate: {buffer_empty_rate:.2%}, Batch: {batch_buffer_size} bytes"
            )

            # Try to get WebRTC stats if peer connection is available

            if self.peer_connection:
                try:
                    logger.debug("🔍 Attempting to get WebRTC stats...")
                    stats = await self.peer_connection.getStats()
                    logger.debug(f"📊 Got {len(stats)} WebRTC stats objects")

                    # Debug: print all stat types we're seeing
                    stat_types = [getattr(stat, "type", "no-type") for stat in stats.values() if hasattr(stat, "type")]
                    logger.debug(f"📊 Stat types found: {set(stat_types)}")

                    # Look for relevant audio stats
                    found_audio_stats = False
                    found_network_stats = False

                    for stat in stats.values():
                        if hasattr(stat, "type"):
                            # Audio outbound RTP stats
                            if stat.type == "outbound-rtp" and hasattr(stat, "kind") and stat.kind == "audio":
                                found_audio_stats = True
                                logger.debug(
                                    f"📡 WebRTC Audio Out - Packets sent: {getattr(stat, 'packetsSent', 'N/A')}, "
                                    f"Bytes sent: {getattr(stat, 'bytesSent', 'N/A')}"
                                )
                            # Network stats from remote inbound RTP (has RTT and jitter)
                            elif stat.type == "remote-inbound-rtp":
                                found_network_stats = True
                                rtt = getattr(stat, "roundTripTime", None)
                                jitter = getattr(stat, "jitter", None)
                                packets_lost = getattr(stat, "packetsLost", None)
                                if rtt is not None:
                                    logger.debug(f"🌐 Network - RTT: {rtt*1000:.1f}ms, " f"Jitter: {jitter}, Packets lost: {packets_lost}")
                                    # Collect network samples for chunk size adaptation
                                    # DISABLED: self._collect_network_sample(rtt, jitter)

                    if not found_audio_stats:
                        logger.debug("⚠️  No outbound audio RTP stats found")
                    if not found_network_stats:
                        logger.debug("⚠️  No successful candidate-pair stats found")

                except Exception as e:
                    logger.info(f"❌ Could not get WebRTC stats: {e}")
            else:
                logger.debug("⚠️  No peer connection available for stats")

            # Adaptive timing adjustment
            self._adjust_timing_based_on_fps(avg_fps)

            # Adaptive chunk sizing based on network conditions
            # DISABLED: Causes audio jitter due to frequent chunk size changes
            # self._adjust_chunk_size_based_on_network()

    def _adjust_timing_based_on_fps(self, current_fps):
        """Adjust timing delays based on actual FPS performance"""
        current_time = time.time()
        if current_time - self.last_fps_check >= self.fps_check_interval:
            self.last_fps_check = current_time

            if current_fps > 0:  # Only adjust if we have meaningful data
                fps_ratio = current_fps / self.target_fps

                if fps_ratio < 0.8:  # Running too slow (< 40 FPS)
                    # Reduce delays to speed up
                    self.current_delay_empty *= 0.8
                    self.current_delay_normal *= 0.8
                    logger.debug(
                        f"🐌 FPS too low ({current_fps:.1f}/{self.target_fps}), reducing delays to "
                        f"{self.current_delay_empty*1000:.1f}ms/{self.current_delay_normal*1000:.1f}ms"
                    )
                elif fps_ratio > 1.2:  # Running too fast (> 60 FPS)
                    # Increase delays to slow down
                    self.current_delay_empty *= 1.2
                    self.current_delay_normal *= 1.2
                    logger.debug(
                        f"🐰 FPS too high ({current_fps:.1f}/{self.target_fps}), increasing delays to "
                        f"{self.current_delay_empty*1000:.1f}ms/{self.current_delay_normal*1000:.1f}ms"
                    )

                # Keep delays within reasonable bounds
                self.current_delay_empty = max(0.001, min(0.050, self.current_delay_empty))
                self.current_delay_normal = max(0.001, min(0.050, self.current_delay_normal))

    def _collect_network_sample(self, rtt, jitter):
        """Collect network performance samples for chunk size adaptation"""
        current_time = time.time()

        # Keep recent samples (last 10 seconds worth)
        self.recent_rtt_samples.append((current_time, rtt))
        self.recent_jitter_samples.append((current_time, jitter))

        # Remove old samples
        cutoff_time = current_time - 10.0
        self.recent_rtt_samples = [(t, v) for t, v in self.recent_rtt_samples if t > cutoff_time]
        self.recent_jitter_samples = [(t, v) for t, v in self.recent_jitter_samples if t > cutoff_time]

    def _adjust_chunk_size_based_on_network(self):
        """Adjust chunk size based on network conditions"""
        current_time = time.time()
        if current_time - self.last_network_check >= self.network_check_interval:
            self.last_network_check = current_time

            if len(self.recent_rtt_samples) >= 3 and len(self.recent_jitter_samples) >= 3:
                # Calculate network stability metrics
                rtt_values = [v for _, v in self.recent_rtt_samples]
                jitter_values = [v for _, v in self.recent_jitter_samples]

                avg_rtt = sum(rtt_values) / len(rtt_values)
                avg_jitter = sum(jitter_values) / len(jitter_values)
                rtt_variance = max(rtt_values) - min(rtt_values)

                # Determine optimal chunk size based on network conditions
                old_chunk_size = self.chunk_size_bytes

                if avg_jitter > 1500 or rtt_variance > 0.050:  # High jitter or RTT variance
                    # Use larger chunks for stability (40ms)
                    self.chunk_size_bytes = int(self.sample_rate * 0.040 * 2)  # 40ms chunks
                    reason = f"high jitter ({avg_jitter:.0f}) or RTT variance ({rtt_variance*1000:.1f}ms)"
                elif avg_rtt > 0.080:  # High RTT (>80ms)
                    # Use larger chunks to compensate for latency (30ms)
                    self.chunk_size_bytes = int(self.sample_rate * 0.030 * 2)  # 30ms chunks
                    reason = f"high RTT ({avg_rtt*1000:.1f}ms)"
                elif avg_jitter < 100 and avg_rtt < 0.040:  # Excellent network
                    # Use smaller chunks for low latency (15ms)
                    self.chunk_size_bytes = int(self.sample_rate * 0.015 * 2)  # 15ms chunks
                    reason = f"excellent network (RTT: {avg_rtt*1000:.1f}ms, jitter: {avg_jitter:.0f})"
                else:
                    # Use default chunk size (20ms)
                    self.chunk_size_bytes = self.base_chunk_size_bytes
                    reason = "balanced network conditions"

                # Update minimum buffer threshold based on new chunk size
                self.min_buffer_threshold = self.chunk_size_bytes * 3

                if old_chunk_size != self.chunk_size_bytes:
                    chunk_duration_ms = (self.chunk_size_bytes // 2) / self.sample_rate * 1000
                    logger.info(f"📦 Chunk size adapted: {old_chunk_size} → {self.chunk_size_bytes} bytes " f"({chunk_duration_ms:.1f}ms) - {reason}")

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
                    # logger.info(f"🔊 Playing audio: {len(chunk_data)} bytes, {len(self.audio_buffer)} remaining")
                elif buffer_size > 0:
                    # Only use remaining data if we have enough, otherwise wait for more
                    if buffer_size >= self.min_buffer_threshold or buffer_size > self.chunk_size_bytes // 2:
                        remaining_data = bytes(self.audio_buffer)
                        self.audio_buffer.clear()
                        padding_needed = self.chunk_size_bytes - len(remaining_data)
                        chunk_data = remaining_data + bytes(padding_needed)
                        # logger.info(f"🔊 Playing remaining audio: used {len(remaining_data)} bytes + {padding_needed} silence")
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

            # Adaptive timing based on performance
            if buffer_was_empty:
                await asyncio.sleep(self.current_delay_empty)
            else:
                await asyncio.sleep(self.current_delay_normal)

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

                    # Log batch processing
                    if old_buffer_size == 0 and new_buffer_size > 0:
                        logger.info(f"🎵 Audio started: +{len(batch_data)} bytes (batched)")
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
            self.current_audio_session = None
            logger.info("🛑 Audio buffer cleared due to interruption")

        # Reset video throb to idle state
        if self.agent_video_track:
            self.agent_video_track.update_throb_level(0.0)

    async def stop(self):
        """Stop the audio track"""
        async with self.buffer_lock:
            self.audio_buffer.clear()
            self.batch_buffer.clear()  # Clear batch buffer too
