import asyncio
import logging
import numpy as np
from fractions import Fraction
from av import AudioFrame
from aiortc import AudioStreamTrack

logger = logging.getLogger(__name__)


class AgentAudioTrack(AudioStreamTrack):
    """
    An audio track that streams Nova speech-to-speech responses with proper chunking
    """

    def __init__(self, agent_video_track=None, sample_rate=24000, channels=1, chunk_size=32):
        super().__init__()
        self.audio_buffer = bytearray()
        self.buffer_lock = asyncio.Lock()
        self.frame_count = 0
        self.sample_rate = sample_rate
        self.channels = channels
        self.agent_video_track = agent_video_track  # Reference to update throb

        # Use same chunk size as nova-sonic.py for consistent timing
        self.chunk_size_bytes = chunk_size * 2  # samples * 2 bytes per sample (16-bit)

        logger.info(f"🔊 AgentAudioTrack initialized - chunk_size: {self.chunk_size_bytes} bytes")

    async def recv(self):
        """Generate and return audio frames from Nova responses"""
        try:
            async with self.buffer_lock:
                if len(self.audio_buffer) >= self.chunk_size_bytes:
                    # Extract a chunk from the buffer
                    chunk_data = bytes(self.audio_buffer[: self.chunk_size_bytes])
                    del self.audio_buffer[: self.chunk_size_bytes]
                else:
                    # Generate silence if not enough data
                    chunk_data = bytes(self.chunk_size_bytes)  # Silent chunk

            # Convert bytes to numpy array
            audio_array = np.frombuffer(chunk_data, dtype=np.int16)

            # Update video throb level based on this audio chunk
            if self.agent_video_track and len(audio_array) > 0:
                # Calculate RMS level for throb
                rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
                normalized_level = min(rms / 2000.0, 1.0)  # High sensitivity
                self.agent_video_track.update_throb_level(normalized_level)

            # Create AudioFrame
            frame = AudioFrame.from_ndarray(audio_array.reshape(1, -1), format="s16", layout="mono")

            # Set timing information
            frame.sample_rate = self.sample_rate
            frame.pts = self.frame_count
            frame.time_base = Fraction(1, self.sample_rate)

            # Update frame count
            self.frame_count += len(audio_array)

            # Add small delay for smooth playback
            await asyncio.sleep(0.001)

            return frame

        except Exception as e:
            logger.error(f"Error in AgentAudioTrack.recv: {e}")
            raise

    async def add_audio_data(self, audio_data: bytes):
        """Add audio data to the buffer for streaming"""
        try:
            async with self.buffer_lock:
                self.audio_buffer.extend(audio_data)

                # Prevent buffer from growing too large
                max_buffer_size = self.sample_rate * 30 * 30
                if len(self.audio_buffer) > max_buffer_size:
                    # Remove oldest data
                    excess = len(self.audio_buffer) - max_buffer_size
                    del self.audio_buffer[:excess]
                    logger.warning(f"Audio buffer too large, removed {excess} bytes")

        except Exception as e:
            logger.error(f"Error adding audio data: {e}")

    async def stop(self):
        """Stop the audio track"""
        async with self.buffer_lock:
            self.audio_buffer.clear()
