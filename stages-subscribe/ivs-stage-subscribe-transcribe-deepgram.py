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
import os
from datetime import datetime
from typing import List, Optional, Dict, Any
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ivs-stage-subscribe-transcribe-deepgram")

# Audio processing configuration
SAMPLE_RATE: int = 16000  # Deepgram supports 16kHz natively
CHANNELS: int = 1

# Audio normalization constants
INT16_MAX: float = 32768.0
INT32_MAX: float = 2147483648.0


class VTTWriter:
    """Handles writing transcriptions to VTT format files"""

    def __init__(self, output_path: str) -> None:
        self.output_path: str = output_path
        self.start_time: Optional[float] = None
        self.sequence_number: int = 1

        # Create directory if it doesn't exist
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # Initialize VTT file with header
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("WEBVTT\n\n")

        logger.info(f"VTT transcription output initialized: {output_path}")

    def write_transcription(self, text: str, start_seconds: float, end_seconds: float) -> None:
        """Write a transcription segment to the VTT file using Deepgram word timestamps"""
        if not text.strip():
            return

        start_time_str = self._format_vtt_timestamp(start_seconds)
        end_time_str = self._format_vtt_timestamp(end_seconds)

        with open(self.output_path, "a", encoding="utf-8") as f:
            f.write(f"{self.sequence_number}\n")
            f.write(f"{start_time_str} --> {end_time_str}\n")
            f.write(f"{text.strip()}\n\n")

        self.sequence_number += 1
        logger.debug(f"VTT cue written: {start_time_str} --> {end_time_str}: {text.strip()}")

    def _format_vtt_timestamp(self, seconds: float) -> str:
        """Format seconds as VTT timestamp (HH:MM:SS.mmm)"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


class DeepgramTranscriber:
    """Streams audio from an IVS stage track to Deepgram for real-time transcription"""

    def __init__(
        self,
        track: MediaStreamTrack,
        deepgram_api_key: str,
        language: str = "en",
        model: str = "nova-3",
        smart_format: bool = True,
        diarize: bool = False,
        filler_words: bool = False,
        interim_results: bool = True,
        utterance_end_ms: int = 1000,
        endpointing: int = 300,
        vtt_writer: Optional[VTTWriter] = None,
    ) -> None:
        self.track: MediaStreamTrack = track
        self.deepgram_api_key: str = deepgram_api_key
        self.language: str = language
        self.model: str = model
        self.smart_format: bool = smart_format
        self.diarize: bool = diarize
        self.filler_words: bool = filler_words
        self.interim_results: bool = interim_results
        self.utterance_end_ms: int = utterance_end_ms
        self.endpointing: int = endpointing
        self.vtt_writer: Optional[VTTWriter] = vtt_writer

        self._should_stop: bool = False
        self._resampler: av.AudioResampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        self._connection = None
        self._listen_task: Optional[asyncio.Task] = None

        # Stats
        self._start_time: Optional[float] = None
        self._frames_sent: int = 0
        self._bytes_sent: int = 0

    async def start(self) -> None:
        """Connect to Deepgram and start streaming audio from the IVS stage track"""
        logger.info("Starting Deepgram real-time transcription...")
        self._start_time = time.time()

        client = AsyncDeepgramClient(api_key=self.deepgram_api_key)

        # Build connection parameters
        # Note: The Deepgram Python SDK v6 uses typed parameters. Some API features
        # like filler_words and detect_language are passed via request_options since
        # they aren't exposed as named parameters on connect().
        connect_params: Dict[str, Any] = {
            "model": self.model,
            "encoding": "linear16",
            "sample_rate": str(SAMPLE_RATE),
            "channels": str(CHANNELS),
            "punctuate": "true",
            "smart_format": str(self.smart_format).lower(),
            "diarize": str(self.diarize).lower(),
            "interim_results": str(self.interim_results).lower(),
            "utterance_end_ms": str(self.utterance_end_ms),
            "endpointing": str(self.endpointing),
            "vad_events": "true",
        }

        # Additional query params not directly on the SDK's connect() signature
        additional_query_params: Dict[str, str] = {}
        if self.filler_words:
            additional_query_params["filler_words"] = "true"

        # Language handling: 'auto' uses detect_language, otherwise set language directly
        if self.language == "auto":
            additional_query_params["detect_language"] = "true"
        else:
            connect_params["language"] = self.language

        if additional_query_params:
            connect_params["request_options"] = {
                "additional_query_parameters": additional_query_params,
            }

        async with client.listen.v1.connect(**connect_params) as connection:
            self._connection = connection

            # Register event handlers
            connection.on(EventType.OPEN, self._on_open)
            connection.on(EventType.MESSAGE, self._on_message)
            connection.on(EventType.ERROR, self._on_error)
            connection.on(EventType.CLOSE, self._on_close)

            # Start the Deepgram listener in the background
            self._listen_task = asyncio.create_task(connection.start_listening())

            # Stream audio frames to Deepgram
            await self._stream_audio()

            # Finalize transcription
            if not self._should_stop:
                try:
                    await connection.send_finalize()
                    await asyncio.sleep(1)  # Allow final results to arrive
                except Exception:
                    pass

            # Cancel listener
            if self._listen_task and not self._listen_task.done():
                self._listen_task.cancel()
                try:
                    await self._listen_task
                except asyncio.CancelledError:
                    pass

        elapsed = time.time() - self._start_time
        logger.info(
            f"Deepgram transcription stopped. " f"Duration: {elapsed:.1f}s, Frames sent: {self._frames_sent}, " f"Bytes sent: {self._bytes_sent:,}"
        )

    async def _stream_audio(self) -> None:
        """Receive audio frames from the IVS track and send to Deepgram"""
        while not self._should_stop:
            try:
                frame: av.AudioFrame = await self.track.recv()

                # Resample to 16kHz mono s16
                resampled_frames = self._resampler.resample(frame)
                if not resampled_frames:
                    continue
                resampled_frame: av.AudioFrame = resampled_frames[0]

                # Convert to raw PCM bytes
                audio_bytes = self._frame_to_bytes(resampled_frame)
                if audio_bytes and self._connection:
                    await self._connection.send_media(audio_bytes)
                    self._frames_sent += 1
                    self._bytes_sent += len(audio_bytes)

            except Exception as e:
                if not self._should_stop:
                    traceback.print_exc()
                    logger.error(f"Error streaming audio to Deepgram: {e}")
                break

    def _frame_to_bytes(self, frame: av.AudioFrame) -> bytes:
        """Convert a PyAV audio frame to raw PCM bytes for Deepgram"""
        try:
            arr = frame.to_ndarray()
            # Handle multi-channel: take first channel
            if arr.ndim == 2:
                arr = arr[0]
            # Ensure int16
            if arr.dtype != np.int16:
                if arr.dtype == np.int32:
                    arr = (arr / (INT32_MAX / INT16_MAX)).astype(np.int16)
                elif arr.dtype in (np.float32, np.float64):
                    arr = (arr * INT16_MAX).clip(-INT16_MAX, INT16_MAX - 1).astype(np.int16)
                else:
                    arr = arr.astype(np.int16)
            return arr.tobytes()
        except Exception as e:
            logger.error(f"Error converting frame to bytes: {e}")
            return b""

    def _on_open(self, *args, **kwargs) -> None:
        logger.info("✅ Deepgram WebSocket connection opened")

    def _on_message(self, message, *args, **kwargs) -> None:
        """Handle transcription results from Deepgram"""
        try:
            msg_type = getattr(message, "type", "unknown")

            # Only process Results messages — other types (Metadata, SpeechStarted,
            # UtteranceEnd) have a 'channel' field that is a list of ints, not the
            # channel object with alternatives.
            if msg_type == "UtteranceEnd":
                logger.debug("Utterance end detected")
                return
            elif msg_type == "SpeechStarted":
                logger.debug("Speech started")
                return
            elif msg_type == "Metadata":
                logger.debug(f"Metadata received: request_id={getattr(message, 'request_id', 'N/A')}")
                return
            elif msg_type != "Results":
                logger.debug(f"Unhandled message type: {msg_type}")
                return

            # This is a Results message — channel is a ListenV1ResultsChannel object
            if not message.channel or not message.channel.alternatives:
                return

            alt = message.channel.alternatives[0]

            transcript = alt.transcript
            if not transcript:
                return

            is_final = getattr(message, "is_final", False)
            speech_final = getattr(message, "speech_final", False)

            # Get timing info from words if available
            words = getattr(alt, "words", [])
            start_time = words[0].start if words else 0.0
            end_time = words[-1].end if words else 0.0

            # Get speaker info if diarization is enabled
            speaker = ""
            if words and hasattr(words[0], "speaker") and words[0].speaker is not None:
                speaker = f"[Speaker {words[0].speaker}] "

            if is_final:
                confidence = getattr(alt, "confidence", 0.0)
                print(f"[TRANSCRIPT] {speaker}{transcript}  (confidence: {confidence:.2f})")

                # Write to VTT if enabled
                if self.vtt_writer and start_time and end_time:
                    self.vtt_writer.write_transcription(f"{speaker}{transcript}", start_time, end_time)
            else:
                # Interim result - show on same line
                print(f"[INTERIM] {speaker}{transcript}", end="\r", flush=True)

        except Exception as e:
            logger.error(f"Error processing Deepgram message: {e}")
            traceback.print_exc()

    def _on_error(self, error, *args, **kwargs) -> None:
        logger.error(f"❌ Deepgram error: {error}")

    def _on_close(self, *args, **kwargs) -> None:
        logger.info("Deepgram WebSocket connection closed")

    def stop(self) -> None:
        """Gracefully stop the transcription"""
        logger.info("Stopping Deepgram transcription...")
        self._should_stop = True


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
    args: argparse.Namespace = parse_args()

    # Resolve Deepgram API key
    deepgram_api_key: str = args.deepgram_api_key or os.environ.get("DEEPGRAM_API_KEY", "")
    if not deepgram_api_key:
        logger.error("Deepgram API key is required. Provide via --deepgram-api-key or DEEPGRAM_API_KEY env var.")
        return

    logger.info(f"Subscribing to participant: {args.participant_id}")
    logger.info(f"Using token: {args.token[:50]}... (truncated)")
    logger.info(f"Deepgram model: {args.model}")
    logger.info(f"Language: {args.language}")
    logger.info(f"Smart format: {args.smart_format}")
    logger.info(f"Diarize: {args.diarize}")
    logger.info(f"Filler words: {args.filler_words}")
    logger.info(f"Interim results: {args.interim_results}")
    logger.info(f"Endpointing: {args.endpointing}ms")

    if args.transcription_output_path:
        logger.info(f"Transcription output enabled: {args.transcription_output_path} " f"(format: {args.transcription_output_format})")

    # Initialize VTT writer if configured
    vtt_writer: Optional[VTTWriter] = None
    if args.transcription_output_path and args.transcription_output_format:
        if args.transcription_output_format.lower() == "vtt":
            vtt_writer = VTTWriter(args.transcription_output_path)
        else:
            logger.warning(f"Unsupported transcription output format: {args.transcription_output_format}")
    elif args.transcription_output_path or args.transcription_output_format:
        logger.warning("Both --transcription-output-path and --transcription-output-format " "must be specified to enable transcription output")

    # Create peer connection
    pc: RTCPeerConnection = RTCPeerConnection()
    pc.addTransceiver("audio", direction="recvonly")

    # Store reference to transcriber for cleanup
    transcriber: Optional[DeepgramTranscriber] = None

    @pc.on("track")
    async def on_track(track: MediaStreamTrack) -> None:
        nonlocal transcriber
        logger.info(f"Track received: {track.kind}, id={track.id}")

        if track.kind == "audio":
            logger.info("Audio track received - starting Deepgram transcription")

            transcriber = DeepgramTranscriber(
                track=track,
                deepgram_api_key=deepgram_api_key,
                language=args.language,
                model=args.model,
                smart_format=args.smart_format,
                diarize=args.diarize,
                filler_words=args.filler_words,
                interim_results=args.interim_results,
                utterance_end_ms=args.utterance_end_ms,
                endpointing=args.endpointing,
                vtt_writer=vtt_writer,
            )
            await transcriber.start()

    # Create offer
    await pc.setLocalDescription(await pc.createOffer())

    # Get token from arguments
    token: str = args.token

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
        response: requests.Response = requests.post(whep_url, data=pc.localDescription.sdp, headers=headers, allow_redirects=False, timeout=10)
    except requests.exceptions.Timeout:
        logger.error(f"Request to {whep_url} timed out after 10 seconds")
        return
    except requests.exceptions.RequestException as e:
        logger.error(f"Request to {whep_url} failed: {e}")
        return

    if response.status_code in [301, 302, 307, 308]:
        redirect_url: Optional[str] = response.headers.get("Location")
        logger.info(f"Redirected to: {redirect_url}")
        try:
            response = requests.post(redirect_url, data=pc.localDescription.sdp, headers=headers, timeout=10)
        except requests.exceptions.Timeout:
            logger.error(f"Request to {redirect_url} timed out after 10 seconds")
            return
        except requests.exceptions.RequestException as e:
            logger.error(f"Request to {redirect_url} failed: {e}")
            return

    response.raise_for_status()

    # Set remote description
    logger.info("Connection established, setting remote description")
    await pc.setRemoteDescription(RTCSessionDescription(sdp=response.text, type="answer"))

    logger.info(f"Subscription active to participant {args.participant_id}. Press Ctrl+C to exit.")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        if transcriber:
            logger.info("Stopping transcriber")
            transcriber.stop()

        logger.info("Closing connection")
        await pc.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="IVS Real-Time Audio Transcription with Deepgram",
        epilog="""
Examples:
  %(prog)s --participant-id user123 --token eyJ... --deepgram-api-key dg-...
  %(prog)s --participant-id user123 --token eyJ... --model nova-3 --language es --diarize
  %(prog)s --participant-id user123 --token eyJ... --model nova-3-medical --filler-words
  %(prog)s --participant-id user123 --token eyJ... --language auto --smart-format
  %(prog)s --participant-id user123 --token eyJ... --endpointing 500 --utterance-end-ms 2000
  %(prog)s --participant-id user123 --token eyJ... --transcription-output-path output.vtt --transcription-output-format vtt

Environment variables:
  DEEPGRAM_API_KEY    Deepgram API key (alternative to --deepgram-api-key)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--participant-id",
        required=True,
        help="Participant ID to subscribe to for audio transcription",
    )

    parser.add_argument(
        "--token",
        required=True,
        help="IVS Real-Time participant token with SUBSCRIBE capability",
    )

    parser.add_argument(
        "--deepgram-api-key",
        default=None,
        help="Deepgram API key (can also be set via DEEPGRAM_API_KEY env var)",
    )

    parser.add_argument(
        "--model",
        default="nova-3",
        help=(
            "Deepgram model for transcription. Common models: nova-3, nova-3-medical, "
            "nova-2, nova-2-meeting, nova-2-finance, nova-2-medical, nova, enhanced, base. "
            "See Deepgram docs for full list (default: nova-3)"
        ),
    )

    parser.add_argument(
        "--language",
        default="en",
        help=(
            "Language for transcription (e.g., 'en', 'en-US', 'es', 'fr', 'de', 'ja', 'zh'). "
            "Use 'auto' for automatic language detection (default: en)"
        ),
    )

    parser.add_argument(
        "--smart-format",
        type=lambda x: x.lower() in ["true", "1", "yes", "on"],
        default=True,
        help="Enable smart formatting for numbers, dates, etc. (default: true)",
    )

    parser.add_argument(
        "--diarize",
        type=lambda x: x.lower() in ["true", "1", "yes", "on"],
        default=False,
        help="Enable speaker diarization (default: false)",
    )

    parser.add_argument(
        "--filler-words",
        type=lambda x: x.lower() in ["true", "1", "yes", "on"],
        default=False,
        help="Transcribe filler words like 'uh' and 'um' (default: false)",
    )

    parser.add_argument(
        "--interim-results",
        type=lambda x: x.lower() in ["true", "1", "yes", "on"],
        default=True,
        help="Show interim (partial) transcription results in real-time (default: true)",
    )

    parser.add_argument(
        "--utterance-end-ms",
        type=int,
        default=1000,
        help="Silence duration in ms to detect end of utterance (default: 1000)",
    )

    parser.add_argument(
        "--endpointing",
        type=int,
        default=300,
        help=(
            "Duration in ms of silence to wait before finalizing speech. "
            "Lower values give faster results but may split mid-sentence. "
            "Set to 0 to disable (default: 300)"
        ),
    )

    parser.add_argument(
        "--transcription-output-path",
        help="Path to save transcription output file (e.g., 'transcription.vtt'). Must be used with --transcription-output-format",
    )

    parser.add_argument(
        "--transcription-output-format",
        choices=["vtt"],
        help="Format for transcription output file. Currently supported: vtt. Must be used with --transcription-output-path",
    )

    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main())
