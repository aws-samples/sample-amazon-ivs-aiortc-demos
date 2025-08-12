import asyncio
import logging
import numpy as np
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

        # Simple buffering - use original small chunks for responsiveness
        self.chunk_size_bytes = 64 * 2  # 64 samples * 2 bytes per sample (16-bit)

        # Simple buffer management
        self.audio_buffer = bytearray()
        self.buffer_lock = asyncio.Lock()
        self.frame_count = 0
        self.max_buffer_size = sample_rate * 2 * 10  # 10 seconds max

        logger.info(f"🔊 AgentAudioTrack initialized - simple mode, chunk_size: {self.chunk_size_bytes} bytes")

    async def recv(self):
        """Generate and return audio frames from Nova responses - back to basics"""
        try:
            # Capture buffer size for timing logic
            buffer_was_empty = False

            async with self.buffer_lock:
                buffer_size = len(self.audio_buffer)
                if buffer_size >= self.chunk_size_bytes:
                    # Extract a chunk from the buffer
                    chunk_data = bytes(self.audio_buffer[: self.chunk_size_bytes])
                    del self.audio_buffer[: self.chunk_size_bytes]
                    # Audio playing normally - no logging needed
                elif buffer_size > 0:
                    # Use remaining buffer data and pad with silence - this prevents missing audio!
                    remaining_data = bytes(self.audio_buffer)
                    self.audio_buffer.clear()
                    padding_needed = self.chunk_size_bytes - len(remaining_data)
                    chunk_data = remaining_data + bytes(padding_needed)
                    # Playing remaining audio - only log if significant amount
                    if len(remaining_data) > 32:  # Only log if meaningful audio
                        logger.info(f"🔊 Playing remaining audio: used {len(remaining_data)} bytes + {padding_needed} silence")
                else:
                    # Generate silence if no data at all
                    chunk_data = bytes(self.chunk_size_bytes)
                    buffer_was_empty = True

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

            # Simple timing - small delay for smooth playback
            # Add extra delay when buffer was empty to catch any late-arriving audio
            if buffer_was_empty:
                await asyncio.sleep(0.005)  # 5ms delay when no audio to catch final chunks
            else:
                await asyncio.sleep(0.001)  # Normal 1ms delay

            return frame

        except Exception as e:
            logger.error(f"Error in AgentAudioTrack.recv: {e}")
            raise

    async def add_audio_data(self, audio_data: bytes):
        """Add audio data to the buffer for streaming"""
        try:
            async with self.buffer_lock:
                old_buffer_size = len(self.audio_buffer)
                self.audio_buffer.extend(audio_data)
                new_buffer_size = len(self.audio_buffer)

                # Only log significant buffer changes or issues
                # if old_buffer_size == 0 and new_buffer_size > 0:
                #     logger.info(f"🎵 Audio started: +{len(audio_data)} bytes")
                # elif new_buffer_size > 10000:  # Log if buffer gets large
                #     logger.info(f"🎵 Large buffer: {new_buffer_size} bytes")

                # Prevent buffer from growing too large
                if len(self.audio_buffer) > self.max_buffer_size:
                    # Remove oldest data
                    excess = len(self.audio_buffer) - (self.max_buffer_size // 2)
                    del self.audio_buffer[:excess]
                    logger.warning(f"Audio buffer too large, removed {excess} bytes")

        except Exception as e:
            logger.error(f"Error adding audio data: {e}")

    async def stop_current_audio(self):
        """Stop current audio playback by clearing the buffer (for interruptions)"""
        async with self.buffer_lock:
            self.audio_buffer.clear()
            logger.info("🛑 Audio buffer cleared due to interruption")

        # Reset video throb to idle state
        if self.agent_video_track:
            self.agent_video_track.update_throb_level(0.0)

    async def stop(self):
        """Stop the audio track"""
        async with self.buffer_lock:
            self.audio_buffer.clear()
