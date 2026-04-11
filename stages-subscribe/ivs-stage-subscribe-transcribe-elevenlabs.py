#!/usr/bin/env python3
"""
IVS Real-Time Stage Audio Transcription with ElevenLabs Scribe

Subscribes to a participant's audio on an IVS Real-Time stage via WebRTC
(WHEP) and streams it to ElevenLabs' real-time Speech-to-Text API
(Scribe v2 Realtime) for live transcription.

Supports:
  - Interim (partial) and final (committed) transcripts
  - Word-level timestamps with speaker IDs
  - Language detection
  - VTT output
  - VAD-based or manual commit strategies
"""

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
import websockets
from typing import List, Optional, Dict, Any
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ivs-stage-subscribe-transcribe-elevenlabs")

# Audio processing configuration
SAMPLE_RATE: int = 16000  # ElevenLabs recommends pcm_16000
CHANNELS: int = 1

# Audio normalization constants
INT16_MAX: float = 32768.0
INT32_MAX: float = 2147483648.0

# ElevenLabs WebSocket endpoint
ELEVENLABS_WS_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"


class VTTWriter:
    """Handles writing transcriptions to VTT format files"""

    def __init__(self, output_path: str) -> None:
        self.output_path: str = output_path
        self.sequence_number: int = 1

        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("WEBVTT\n\n")

        logger.info(f"VTT transcription output initialized: {output_path}")

    def write_transcription(self, text: str, start_seconds: float, end_seconds: float) -> None:
        if not text.strip():
            return

        start_str = self._format_vtt_timestamp(start_seconds)
        end_str = self._format_vtt_timestamp(end_seconds)

        with open(self.output_path, "a", encoding="utf-8") as f:
            f.write(f"{self.sequence_number}\n")
            f.write(f"{start_str} --> {end_str}\n")
            f.write(f"{text.strip()}\n\n")

        self.sequence_number += 1

    def _format_vtt_timestamp(self, seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


class ElevenLabsTranscriber:
    """Streams audio from an IVS stage track to ElevenLabs Scribe for real-time transcription"""

    def __init__(
        self,
        track: MediaStreamTrack,
        api_key: str,
        model_id: str = "scribe_v2_realtime",
        language_code: str = "en",
        commit_strategy: str = "vad",
        vad_silence_threshold_secs: float = 1.5,
        vad_threshold: float = 0.4,
        min_speech_duration_ms: int = 100,
        min_silence_duration_ms: int = 100,
        include_timestamps: bool = True,
        include_language_detection: bool = False,
        vtt_writer: Optional[VTTWriter] = None,
    ) -> None:
        self.track = track
        self.api_key = api_key
        self.model_id = model_id
        self.language_code = language_code
        self.commit_strategy = commit_strategy
        self.vad_silence_threshold_secs = vad_silence_threshold_secs
        self.vad_threshold = vad_threshold
        self.min_speech_duration_ms = min_speech_duration_ms
        self.min_silence_duration_ms = min_silence_duration_ms
        self.include_timestamps = include_timestamps
        self.include_language_detection = include_language_detection
        self.vtt_writer = vtt_writer

        self._should_stop: bool = False
        self._resampler: av.AudioResampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        self._ws = None

        # Stats
        self._start_time: Optional[float] = None
        self._frames_sent: int = 0
        self._bytes_sent: int = 0

    async def start(self) -> None:
        """Connect to ElevenLabs and start streaming audio from the IVS stage track"""
        logger.info("Starting ElevenLabs real-time transcription...")
        self._start_time = time.time()

        # Build WebSocket URL with query parameters
        params = {
            "model_id": self.model_id,
            "audio_format": "pcm_16000",
            "commit_strategy": self.commit_strategy,
            "include_timestamps": str(self.include_timestamps).lower(),
            "include_language_detection": str(self.include_language_detection).lower(),
        }

        # VAD-specific parameters
        if self.commit_strategy == "vad":
            params["vad_silence_threshold_secs"] = str(self.vad_silence_threshold_secs)
            params["vad_threshold"] = str(self.vad_threshold)
            params["min_speech_duration_ms"] = str(self.min_speech_duration_ms)
            params["min_silence_duration_ms"] = str(self.min_silence_duration_ms)

        if self.language_code and self.language_code != "auto":
            params["language_code"] = self.language_code

        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        ws_url = f"{ELEVENLABS_WS_URL}?{query_string}"

        headers = {"xi-api-key": self.api_key}

        try:
            async with websockets.connect(ws_url, additional_headers=headers) as ws:
                self._ws = ws
                logger.info("✅ ElevenLabs WebSocket connection opened")

                # Wait for session_started
                session_msg = await ws.recv()
                session_data = json.loads(session_msg)
                if session_data.get("message_type") == "session_started":
                    sid = session_data.get("session_id", "N/A")
                    config = session_data.get("config", {})
                    logger.info(f"✅ Session started: {sid}")
                    logger.info(
                        f"   Config: model={config.get('model_id')}, "
                        f"strategy={config.get('commit_strategy')}, "
                        f"lang={config.get('language_code')}"
                    )
                else:
                    logger.warning(f"Unexpected first message: {session_data}")

                # Run send and receive concurrently
                send_task = asyncio.create_task(self._stream_audio())
                recv_task = asyncio.create_task(self._receive_transcripts())

                done, pending = await asyncio.wait(
                    [send_task, recv_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"ElevenLabs WebSocket closed: {e}")
        except Exception as e:
            logger.error(f"ElevenLabs connection error: {e}")
            traceback.print_exc()

        elapsed = time.time() - self._start_time if self._start_time else 0
        logger.info(
            f"ElevenLabs transcription stopped. " f"Duration: {elapsed:.1f}s, Frames sent: {self._frames_sent}, " f"Bytes sent: {self._bytes_sent:,}"
        )

    async def _stream_audio(self) -> None:
        """Receive audio frames from the IVS track and send to ElevenLabs"""
        first_chunk = True
        while not self._should_stop:
            try:
                frame: av.AudioFrame = await self.track.recv()

                resampled_frames = self._resampler.resample(frame)
                if not resampled_frames:
                    continue
                resampled_frame = resampled_frames[0]

                audio_bytes = self._frame_to_bytes(resampled_frame)
                if audio_bytes and self._ws:
                    # ElevenLabs expects base64-encoded PCM in JSON messages
                    msg: Dict[str, Any] = {
                        "message_type": "input_audio_chunk",
                        "audio_base_64": base64.b64encode(audio_bytes).decode("utf-8"),
                        "commit": False,
                        "sample_rate": SAMPLE_RATE,
                    }
                    await self._ws.send(json.dumps(msg))
                    self._frames_sent += 1
                    self._bytes_sent += len(audio_bytes)

                    if first_chunk:
                        logger.info(f"🎤 First audio chunk sent ({len(audio_bytes)} bytes)")
                        first_chunk = False
                    elif self._frames_sent % 500 == 0:
                        logger.info(f"🎤 Audio frames sent: {self._frames_sent}")

            except Exception as e:
                if not self._should_stop:
                    logger.error(f"Error streaming audio to ElevenLabs: {e}")
                    traceback.print_exc()
                break

    async def _receive_transcripts(self) -> None:
        """Receive and process transcription results from ElevenLabs"""
        while not self._should_stop and self._ws:
            try:
                message = await self._ws.recv()
                data = json.loads(message)
                msg_type = data.get("message_type", "")

                if msg_type == "partial_transcript":
                    text = data.get("text", "")
                    if text:
                        print(f"[INTERIM] {text}", end="\r", flush=True)

                elif msg_type == "committed_transcript":
                    text = data.get("text", "")
                    if text:
                        print(f"[TRANSCRIPT] {text}")

                elif msg_type == "committed_transcript_with_timestamps":
                    text = data.get("text", "")
                    words = data.get("words", [])
                    lang = data.get("language_code", "")

                    # Build speaker prefix from word-level speaker_id
                    speaker = ""
                    word_entries = [w for w in (words or []) if w.get("type") == "word"]
                    if word_entries and word_entries[0].get("speaker_id"):
                        speaker = f"[{word_entries[0]['speaker_id']}] "

                    lang_info = f"  (lang: {lang})" if lang else ""

                    if text:
                        print(f"[TRANSCRIPT] {speaker}{text}{lang_info}")

                    # Write to VTT if enabled and we have word timestamps
                    if self.vtt_writer and word_entries:
                        start_time = word_entries[0].get("start", 0.0)
                        end_time = word_entries[-1].get("end", 0.0)
                        self.vtt_writer.write_transcription(f"{speaker}{text}", start_time, end_time)

                elif msg_type in (
                    "error",
                    "auth_error",
                    "quota_exceeded",
                    "rate_limited",
                    "commit_throttled",
                    "input_error",
                    "chunk_size_exceeded",
                    "resource_exhausted",
                    "session_time_limit_exceeded",
                    "insufficient_audio_activity",
                    "transcriber_error",
                    "queue_overflow",
                    "unaccepted_terms",
                ):
                    error_msg = data.get("error", "Unknown error")
                    logger.error(f"❌ ElevenLabs {msg_type}: {error_msg}")
                    if msg_type in ("auth_error", "quota_exceeded", "unaccepted_terms"):
                        self._should_stop = True
                        break

                elif msg_type == "session_started":
                    pass  # Already handled on connect

                else:
                    logger.debug(f"Unhandled message type: {msg_type}")

            except websockets.exceptions.ConnectionClosed:
                logger.info("ElevenLabs WebSocket closed")
                break
            except Exception as e:
                if not self._should_stop:
                    logger.error(f"Error receiving transcript: {e}")
                break

    def _frame_to_bytes(self, frame: av.AudioFrame) -> bytes:
        """Convert a PyAV audio frame to raw PCM 16-bit bytes"""
        try:
            arr = frame.to_ndarray()
            if arr.ndim == 2:
                arr = arr[0]
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

    def stop(self) -> None:
        logger.info("Stopping ElevenLabs transcription...")
        self._should_stop = True


def parse_jwt(token: str) -> Dict[str, Any]:
    """Parse JWT token without verification"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT format")
        payload = parts[1]
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except Exception as e:
        logger.error(f"Error parsing JWT: {e}")
        return {}


async def main() -> None:
    args = parse_args()

    # Resolve ElevenLabs API key
    api_key: str = args.elevenlabs_api_key or os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        logger.error("ElevenLabs API key is required. Provide via --elevenlabs-api-key or ELEVENLABS_API_KEY env var.")
        return

    logger.info(f"Subscribing to participant: {args.participant_id}")
    logger.info(f"Using token: {args.token[:50]}... (truncated)")
    logger.info(f"ElevenLabs model: {args.model_id}")
    logger.info(f"Language: {args.language_code}")
    logger.info(f"Commit strategy: {args.commit_strategy}")
    logger.info(f"Include timestamps: {args.include_timestamps}")
    logger.info(f"Include language detection: {args.include_language_detection}")

    if args.transcription_output_path:
        logger.info(f"Transcription output enabled: {args.transcription_output_path} (format: {args.transcription_output_format})")

    # Initialize VTT writer if configured
    vtt_writer: Optional[VTTWriter] = None
    if args.transcription_output_path and args.transcription_output_format:
        if args.transcription_output_format.lower() == "vtt":
            vtt_writer = VTTWriter(args.transcription_output_path)
        else:
            logger.warning(f"Unsupported transcription output format: {args.transcription_output_format}")
    elif args.transcription_output_path or args.transcription_output_format:
        logger.warning("Both --transcription-output-path and --transcription-output-format must be specified")

    # Create peer connection
    pc: RTCPeerConnection = RTCPeerConnection()
    pc.addTransceiver("audio", direction="recvonly")

    transcriber: Optional[ElevenLabsTranscriber] = None

    @pc.on("track")
    async def on_track(track: MediaStreamTrack) -> None:
        nonlocal transcriber
        logger.info(f"Track received: {track.kind}, id={track.id}")

        if track.kind == "audio":
            logger.info("Audio track received - starting ElevenLabs transcription")

            transcriber = ElevenLabsTranscriber(
                track=track,
                api_key=api_key,
                model_id=args.model_id,
                language_code=args.language_code,
                commit_strategy=args.commit_strategy,
                vad_silence_threshold_secs=args.vad_silence_threshold_secs,
                vad_threshold=args.vad_threshold,
                min_speech_duration_ms=args.min_speech_duration_ms,
                min_silence_duration_ms=args.min_silence_duration_ms,
                include_timestamps=args.include_timestamps,
                include_language_detection=args.include_language_detection,
                vtt_writer=vtt_writer,
            )
            await transcriber.start()

    # Create offer
    await pc.setLocalDescription(await pc.createOffer())

    token: str = args.token

    try:
        token_payload = parse_jwt(token)
        if "whip_url" not in token_payload:
            logger.error("No whip_url found in token payload")
            return

        whip_base_url = token_payload["whip_url"]
        logger.info(f"Extracted WHIP URL from token: {whip_base_url}")
    except Exception as e:
        logger.error(f"Failed to parse token or extract WHIP URL: {e}")
        return

    # Connect to WHEP endpoint
    whep_url = f"{whip_base_url}/subscribe/{args.participant_id}"
    logger.info(f"Connecting to WHEP endpoint: {whep_url}")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"}

    try:
        response = requests.post(whep_url, data=pc.localDescription.sdp, headers=headers, allow_redirects=False, timeout=10)
    except requests.exceptions.Timeout:
        logger.error(f"Request to {whep_url} timed out after 10 seconds")
        return
    except requests.exceptions.RequestException as e:
        logger.error(f"Request to {whep_url} failed: {e}")
        return

    if response.status_code in [301, 302, 307, 308]:
        redirect_url = response.headers.get("Location")
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
            transcriber.stop()
        await pc.close()
        logger.info("✅ Cleanup completed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="IVS Real-Time Audio Transcription with ElevenLabs Scribe",
        epilog="""
Examples:
  %(prog)s --participant-id user123 --token eyJ... --elevenlabs-api-key sk_...
  %(prog)s --participant-id user123 --token eyJ... --language-code es
  %(prog)s --participant-id user123 --token eyJ... --language-code auto --include-language-detection
  %(prog)s --participant-id user123 --token eyJ... --commit-strategy manual
  %(prog)s --participant-id user123 --token eyJ... --transcription-output-path output.vtt --transcription-output-format vtt
  %(prog)s --participant-id user123 --token eyJ... --vad-silence-threshold-secs 2.0 --vad-threshold 0.3

Environment variables:
  ELEVENLABS_API_KEY    ElevenLabs API key (alternative to --elevenlabs-api-key)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--participant-id", required=True, help="Participant ID to subscribe to for audio transcription")
    parser.add_argument("--token", required=True, help="IVS Real-Time participant token with SUBSCRIBE capability")
    parser.add_argument("--elevenlabs-api-key", default=None, help="ElevenLabs API key (or set ELEVENLABS_API_KEY env var)")

    parser.add_argument(
        "--model-id",
        default="scribe_v2_realtime",
        help="ElevenLabs STT model ID (default: scribe_v2_realtime)",
    )
    parser.add_argument(
        "--language-code",
        default="en",
        help="Language code (ISO 639-1/639-3, e.g., 'en', 'es', 'fr'). Use 'auto' with --include-language-detection (default: en)",
    )
    parser.add_argument(
        "--commit-strategy",
        default="vad",
        choices=["vad", "manual"],
        help="Transcription commit strategy: 'vad' for automatic voice activity detection, 'manual' for explicit commits (default: vad)",
    )

    vad = parser.add_argument_group("vad settings", "Voice Activity Detection parameters (used when --commit-strategy vad)")
    vad.add_argument(
        "--vad-silence-threshold-secs",
        type=float,
        default=1.5,
        help="Seconds of silence before auto-commit (default: 1.5)",
    )
    vad.add_argument(
        "--vad-threshold",
        type=float,
        default=0.4,
        help="Speech detection sensitivity 0.0-1.0, lower = more sensitive (default: 0.4)",
    )
    vad.add_argument(
        "--min-speech-duration-ms",
        type=int,
        default=100,
        help="Ignore speech shorter than this in ms (default: 100)",
    )
    vad.add_argument(
        "--min-silence-duration-ms",
        type=int,
        default=100,
        help="Ignore silence shorter than this in ms (default: 100)",
    )

    parser.add_argument(
        "--include-timestamps",
        type=lambda x: x.lower() in ["true", "1", "yes", "on"],
        default=True,
        help="Include word-level timestamps in committed transcripts (default: true)",
    )
    parser.add_argument(
        "--include-language-detection",
        type=lambda x: x.lower() in ["true", "1", "yes", "on"],
        default=False,
        help="Include language detection in committed transcripts (default: false)",
    )
    parser.add_argument(
        "--transcription-output-path",
        help="Path to save transcription output file (e.g., 'transcription.vtt'). Must be used with --transcription-output-format",
    )
    parser.add_argument(
        "--transcription-output-format",
        choices=["vtt"],
        help="Format for transcription output file. Currently supported: vtt",
    )

    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main())
