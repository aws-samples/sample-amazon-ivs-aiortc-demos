#!/usr/bin/env python3

import asyncio
import traceback
import argparse
import base64
import json
import logging
import requests
import av
import time
import numpy as np
from typing import List, Optional, Dict, Any
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
import whisper

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ivs-stage-subscribe-transcribe")

# Configuration
FP16 = True

# Global whisper model
whisper_model: Optional[whisper.Whisper] = None

# Audio processing configuration
CHUNK_DURATION: int = 5  # seconds
SAMPLE_RATE: int = 48000
CHANNELS: int = 1

# Audio normalization constants
INT16_MAX: float = 32768.0
INT32_MAX: float = 2147483648.0
WHISPER_SAMPLE_RATE: int = 16000


class AudioChunkRecorder:
    def __init__(
        self,
        track: MediaStreamTrack,
        chunk_duration: int = CHUNK_DURATION,
        language: str = "en",
    ) -> None:
        self.track: MediaStreamTrack = track
        self.chunk_duration: int = chunk_duration
        self.language: str = language
        self._recording_frames: List[av.AudioFrame] = []
        self._count: int = 0
        self._start_time: Optional[float] = None
        self._should_stop: bool = False
        self._resampler: av.AudioResampler = av.AudioResampler(format="s16", layout="mono" if CHANNELS == 1 else "stereo", rate=SAMPLE_RATE)

    async def start(self) -> None:
        logger.info("Starting audio recording...")
        self._start_time = time.time()

        # Continuously receive and process audio frames
        while not self._should_stop:
            try:
                frame: av.AudioFrame = await self.track.recv()

                # Resample the audio frame to s16 format
                resampled_frame: av.AudioFrame = self._resampler.resample(frame)[0]

                # Append frame data to buffer
                self._recording_frames.append(resampled_frame)

                # Check if buffer contains enough frames for a chunk
                # Calculate required samples: SAMPLE_RATE * chunk_duration
                required_samples: int = int(SAMPLE_RATE * self.chunk_duration)

                # Sum samples from each frame
                current_samples: int = sum(f.samples for f in self._recording_frames)

                if current_samples >= required_samples:
                    # Process the chunk directly in memory
                    self._count += 1
                    await self._process_chunk_in_memory(self._recording_frames)

                    # Reset the buffer
                    self._recording_frames = []

            except Exception as e:
                if not self._should_stop:  # Only log if not intentionally stopping
                    traceback.print_exc()
                    logger.error(f"Error receiving or processing audio frame: {e}")
                break

        logger.info("Audio recording stopped")

    def stop(self) -> None:
        """Gracefully stop the audio recording"""
        logger.info("Stopping audio recording...")
        self._should_stop = True

    async def _process_chunk_in_memory(self, frames: List[av.AudioFrame]) -> None:
        try:
            # Convert frames to numpy array for Whisper
            audio_data: np.ndarray = self._frames_to_numpy(frames)

            logger.info(f"Processing audio chunk {self._count} in memory (shape: {audio_data.shape})")

            # Transcribe asynchronously to avoid blocking the audio processing loop
            language_param: Optional[str] = None if self.language == "auto" else self.language

            # Run Whisper transcription in a separate thread
            try:
                # Use asyncio.to_thread for Python 3.9+, fallback to run_in_executor for older versions
                import sys

                if sys.version_info >= (3, 9):
                    result: Dict[str, Any] = await asyncio.to_thread(whisper_model.transcribe, audio_data, fp16=FP16, language=language_param)
                else:
                    loop = asyncio.get_event_loop()
                    result: Dict[str, Any] = await loop.run_in_executor(
                        None, lambda: whisper_model.transcribe(audio_data, fp16=FP16, language=language_param)
                    )

                # Process transcription result
                text: str = result["text"].strip()

                if text:
                    print(f"[TRANSCRIPT] {text}")
                else:
                    logger.info("No speech detected in chunk")

            except Exception as transcribe_error:
                logger.error(f"Error during Whisper transcription: {transcribe_error}")
                # Continue processing other chunks even if this one fails

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error processing audio chunk in memory: {e}")

    def _frames_to_numpy(self, frames: List[av.AudioFrame]) -> np.ndarray:
        """Convert PyAV audio frames to numpy array suitable for Whisper"""
        try:
            # Collect all audio data from frames
            audio_samples: List[np.ndarray] = []

            for frame in frames:
                # Convert frame to numpy array
                # PyAV frames have a .to_ndarray() method
                frame_array: np.ndarray = frame.to_ndarray()

                # Handle different array shapes
                if frame_array.ndim == 2:
                    # Multi-channel audio - take first channel or average
                    if CHANNELS == 1:
                        frame_array = frame_array[0]  # Take first channel
                    else:
                        frame_array = np.mean(frame_array, axis=0, dtype=np.float32)  # Average channels with explicit dtype

                audio_samples.append(frame_array)

            # Concatenate all samples
            if audio_samples:
                audio_data: np.ndarray = np.concatenate(audio_samples)

                # Convert to float32 and normalize to [-1, 1] range
                # Assuming input is int16
                if audio_data.dtype == np.int16:
                    audio_data = audio_data.astype(np.float32) / INT16_MAX
                elif audio_data.dtype == np.int32:
                    audio_data = audio_data.astype(np.float32) / INT32_MAX
                elif audio_data.dtype != np.float32:
                    audio_data = audio_data.astype(np.float32)

                # Ensure the sample rate matches what Whisper expects (16kHz)
                # If our sample rate is different, we need to resample
                if SAMPLE_RATE != WHISPER_SAMPLE_RATE:
                    # Simple resampling - ensure all arrays are float32
                    target_length: int = int(len(audio_data) * WHISPER_SAMPLE_RATE / SAMPLE_RATE)
                    # Create interpolation arrays with explicit float32 dtype
                    x_old: np.ndarray = np.arange(len(audio_data), dtype=np.float32)
                    x_new: np.ndarray = np.linspace(0, len(audio_data) - 1, target_length, dtype=np.float32)
                    audio_data = np.interp(x_new, x_old, audio_data.astype(np.float32))

                # Ensure final output is float32
                return audio_data.astype(np.float32)
            else:
                return np.array([], dtype=np.float32)

        except Exception as e:
            logger.error(f"Error converting frames to numpy: {e}")
            traceback.print_exc()
            return np.array([], dtype=np.float32)


def parse_jwt(token: str) -> Dict[str, Any]:
    """Parse JWT token without verification"""
    try:
        parts: List[str] = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT format")

        payload: str = parts[1]
        payload += "=" * (4 - len(payload) % 4)
        decoded_bytes: bytes = base64.urlsafe_b64decode(payload)
        payload_json: Dict[str, Any] = json.loads(decoded_bytes.decode("utf-8"))

        return payload_json
    except Exception as e:
        logger.error(f"Error parsing JWT: {e}")
        return {}


async def main() -> None:
    global whisper_model, FP16

    args: argparse.Namespace = parse_args()

    logger.info(f"Subscribing to participant: {args.participant_id}")
    logger.info(f"Using token: {args.token[:50]}... (truncated)")
    logger.info(f"Loading Whisper model: {args.whisper_model}")
    logger.info(f"Language: {args.language}")
    logger.info(f"FP16: {args.fp16}")

    # Set global FP16 variable
    FP16 = args.fp16

    # Load Whisper model
    whisper_model = whisper.load_model(args.whisper_model)

    # Create peer connection
    pc: RTCPeerConnection = RTCPeerConnection()
    pc.addTransceiver("audio", direction="recvonly")

    # Store reference to chunk recorder for cleanup
    chunk_recorder: Optional[AudioChunkRecorder] = None

    @pc.on("track")
    async def on_track(track: MediaStreamTrack) -> None:
        nonlocal chunk_recorder
        logger.info(f"Track received: {track.kind}, id={track.id}")

        if track.kind == "audio":
            logger.info("Audio track received - starting recording and transcription")

            chunk_recorder = AudioChunkRecorder(track, args.chunk_duration, args.language)
            await chunk_recorder.start()

    # Create offer
    await pc.setLocalDescription(await pc.createOffer())

    # Get token from arguments
    token: str = args.token
    logger.info(f"Using provided token: {token[:50]}... (truncated)")

    try:
        token_payload: Dict[str, Any] = parse_jwt(token)
        if "whip_url" not in token_payload:
            logger.error("No whip_url found in token payload")
            return

        whip_base_url: str = token_payload["whip_url"]
        logger.info(f"Extracted WHIP URL from token: {whip_base_url}")
    except Exception as e:
        logger.error(f"Failed to parse token or extract WHIP URL: {e}")
        return

    # Connect to WHEP endpoint
    whep_url: str = f"{whip_base_url}/subscribe/{args.participant_id}"
    logger.info(f"Connecting to WHEP endpoint: {whep_url}")

    headers: Dict[str, str] = {"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"}

    try:
        response: requests.Response = requests.post(
            whep_url, 
            data=pc.localDescription.sdp, 
            headers=headers, 
            allow_redirects=False,
            timeout=10  # Add explicit timeout of 10 seconds
        )
    except requests.exceptions.Timeout:
        logger.error(f"Request to {whep_url} timed out after 10 seconds")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Request to {whep_url} failed: {e}")
        return None

    if response.status_code in [301, 302, 307, 308]:
        redirect_url: Optional[str] = response.headers.get("Location")
        logger.info(f"Redirected to: {redirect_url}")
        try:
            response = requests.post(
                redirect_url, 
                data=pc.localDescription.sdp, 
                headers=headers,
                timeout=10  # Add explicit timeout of 10 seconds
            )
        except requests.exceptions.Timeout:
            logger.error(f"Request to {redirect_url} timed out after 10 seconds")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Request to {redirect_url} failed: {e}")
            return None

    response.raise_for_status()

    # Set remote description
    logger.info("Connection established, setting remote description")
    await pc.setRemoteDescription(RTCSessionDescription(sdp=response.text, type="answer"))

    logger.info(f"Subscription active to participant {args.participant_id}. Press Ctrl+C to exit.")

    try:
        # Keep running
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        # Cleanup
        if chunk_recorder:
            logger.info("Stopping chunk recorder")
            chunk_recorder.stop()

        logger.info("Closing connection")
        await pc.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="IVS Real-Time Audio Transcription with Whisper",
        epilog="""
Examples:
  %(prog)s --participant-id user123 --token eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
  %(prog)s --participant-id user123 --token eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9... --whisper-model base --language es
  %(prog)s --participant-id user123 --token eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9... --chunk-duration 10 --language auto
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--participant-id",
        required=True,
        help="Participant ID to subscribe to for audio transcription",
    )

    parser.add_argument("--token", required=True, help="IVS Real-Time participant token with SUBSCRIBE capability")

    parser.add_argument(
        "--whisper-model",
        default="tiny",
        choices=["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"],
        help="Whisper model to use for transcription. Larger models are more accurate but slower (default: tiny)",
    )

    parser.add_argument(
        "--fp16",
        type=lambda x: x.lower() in ["true", "1", "yes", "on"],
        default=True,
        help="Use 16-bit floating point precision with Whisper for faster processing (default: true)",
    )

    parser.add_argument(
        "--language",
        default="en",
        help="Language for transcription (ISO 639-1 code, e.g., 'en' for English, 'es' for Spanish, 'fr' for French, 'de' for German, 'ja' for Japanese, 'zh' for Chinese). Use 'auto' for automatic detection (default: en)",
    )

    parser.add_argument(
        "--chunk-duration",
        type=int,
        default=CHUNK_DURATION,
        help=f"Duration in seconds for each audio chunk to transcribe (default: {CHUNK_DURATION})",
    )

    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main())
