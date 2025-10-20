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

        # Simple approach based on AWS documentation - small chunks for low latency
        self.chunk_size_bytes = 1024 * 2  # 2048 bytes - matches AWS example approach (1024 samples * 2 bytes)

        # Simple queue-based approach like AWS documentation
        self.audio_queue = asyncio.Queue()
        self.frame_count = 0
        self.max_queue_size = 50  # Limit queue size to prevent excessive buffering

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

        # Simple timing based on AWS documentation
        self.target_sleep = 0.01  # 10ms sleep like AWS example

        logger.info(
            f"🔊 AgentAudioTrack initialized - chunk_size: {self.chunk_size_bytes} bytes (~{self.chunk_size_bytes//2/sample_rate*1000:.1f}ms), "
            f"queue-based approach (AWS documentation style)"
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

            # Get current queue size for stats
            queue_size = self.audio_queue.qsize()

            logger.debug(
                f"📊 Audio Stats - Uptime: {uptime:.1f}s, Frames: {self.frames_sent}, "
                f"FPS: {avg_fps:.1f}, Throughput: {avg_throughput/1024:.1f}KB/s, "
                f"Buffer empty rate: {buffer_empty_rate:.2%}, Queue size: {queue_size}/{self.max_queue_size}"
            )

    async def recv(self):
        """Generate and return audio frames from Nova responses - AWS documentation approach"""
        try:
            # Print debug stats periodically
            await self._print_debug_stats()

            # Simple queue-based approach like AWS documentation
            try:
                # Try to get audio data from queue with a short timeout
                audio_data = await asyncio.wait_for(self.audio_queue.get(), timeout=0.001)
                logger.debug(f"🔊 Playing audio: {len(audio_data)} bytes from queue")
            except asyncio.TimeoutError:
                # No audio available, generate silence
                audio_data = bytes(self.chunk_size_bytes)
                self.buffer_empty_count += 1
                logger.debug(f"🔊 No audio in queue, generating silence: {len(audio_data)} bytes")

            # Track performance metrics
            self.frames_sent += 1
            self.bytes_processed += len(audio_data)

            # Convert bytes to numpy array
            audio_array = np.frombuffer(audio_data, dtype=np.int16)

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

            # Simple timing like AWS documentation
            await asyncio.sleep(self.target_sleep)

            return frame

        except Exception as e:
            logger.error(f"Error in AgentAudioTrack.recv: {e}")
            raise

    async def add_audio_data(self, audio_data: bytes):
        """Add audio data to queue - AWS documentation approach"""
        try:
            # Validate audio data
            if not audio_data or len(audio_data) == 0:
                return

            # Simple queue approach like AWS documentation
            if self.audio_queue.qsize() < self.max_queue_size:
                await self.audio_queue.put(audio_data)
                logger.debug(f"🎵 Audio queued: +{len(audio_data)} bytes, queue size: {self.audio_queue.qsize()}")
            else:
                # Queue is full, drop oldest data to prevent excessive latency
                try:
                    self.audio_queue.get_nowait()  # Remove oldest
                    await self.audio_queue.put(audio_data)  # Add new
                    logger.debug(f"🎵 Audio queue full, replaced oldest: +{len(audio_data)} bytes")
                except asyncio.QueueEmpty:
                    await self.audio_queue.put(audio_data)

        except Exception as e:
            logger.error(f"Error adding audio data: {e}")

    async def stop_current_audio(self):
        """Stop current audio playback by clearing the queue (for interruptions)"""
        # Clear the audio queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        logger.info("🛑 Audio queue cleared due to interruption")

        # Reset video throb to idle state
        if self.agent_video_track:
            self.agent_video_track.update_throb_level(0.0)

    async def stop(self):
        """Stop the audio track"""
        # Clear the audio queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
