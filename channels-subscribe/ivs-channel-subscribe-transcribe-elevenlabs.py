#!/usr/bin/env python3
"""
IVS Channel M3U8 Stream Real-Time Audio Transcription with ElevenLabs Scribe

Reads audio from an IVS Channel HLS stream and streams it to ElevenLabs'
real-time Speech-to-Text API (Scribe v2 Realtime) for live transcription.

Supports:
  - Interim (partial) and final (committed) transcripts
  - Word-level timestamps with speaker IDs
  - Language detection
  - VAD-based or manual commit strategies
  - Timed metadata publishing back to the IVS channel
  - Video display (optional, requires OpenCV)
"""

import asyncio
import traceback
import argparse
import base64
import json
import logging
import av
import time
import numpy as np
import os
import sys
import re
import websockets
from typing import List, Optional, Dict, Any
import m3u8
from ivs_metadata_publisher import IVSMetadataPublisher

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ivs-channel-transcribe-elevenlabs")

# Audio processing configuration
SAMPLE_RATE: int = 16000  # ElevenLabs recommends pcm_16000
CHANNELS: int = 1

# Audio normalization constants
INT16_MAX: float = 32768.0
INT32_MAX: float = 2147483648.0

# ElevenLabs WebSocket endpoint
ELEVENLABS_WS_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"


class ElevenLabsChannelTranscriber:
    """Streams audio from an IVS Channel HLS stream to ElevenLabs Scribe for real-time transcription"""

    def __init__(
        self,
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
        metadata_publisher: Optional[IVSMetadataPublisher] = None,
        playlist_url: Optional[str] = None,
        publish_interim_metadata: bool = False,
    ) -> None:
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
        self.metadata_publisher = metadata_publisher
        self.playlist_url = playlist_url
        self.publish_interim_metadata = publish_interim_metadata

        self._resampler: av.AudioResampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        self._ws = None
        self._should_stop: bool = False
        self._recv_task: Optional[asyncio.Task] = None

        # Stats
        self._start_time: Optional[float] = None
        self._frames_sent: int = 0
        self._bytes_sent: int = 0

    async def connect(self) -> None:
        """Establish the ElevenLabs WebSocket connection"""
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

        self._ws = await websockets.connect(ws_url, additional_headers=headers)
        logger.info("✅ ElevenLabs WebSocket connection opened")

        # Wait for session_started
        session_msg = await self._ws.recv()
        session_data = json.loads(session_msg)
        if session_data.get("message_type") == "session_started":
            sid = session_data.get("session_id", "N/A")
            config = session_data.get("config", {})
            logger.info(f"✅ Session started: {sid}")
            logger.info(
                f"   Config: model={config.get('model_id')}, " f"strategy={config.get('commit_strategy')}, " f"lang={config.get('language_code')}"
            )
        else:
            logger.warning(f"Unexpected first message: {session_data}")

        # Start receiving transcripts in the background
        self._recv_task = asyncio.create_task(self._receive_transcripts())

    async def disconnect(self) -> None:
        """Close the ElevenLabs connection"""
        self._should_stop = True

        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

        if self._start_time:
            elapsed = time.time() - self._start_time
            logger.info(f"ElevenLabs transcription stopped. Duration: {elapsed:.1f}s, Frames: {self._frames_sent}, Bytes: {self._bytes_sent:,}")

    async def send_audio_frame(self, frame: av.AudioFrame) -> None:
        """Resample and send a PyAV audio frame to ElevenLabs"""
        if not self._ws or self._should_stop:
            return

        try:
            resampled_frames = self._resampler.resample(frame)
            if not resampled_frames:
                return
            resampled_frame = resampled_frames[0]

            audio_bytes = self._frame_to_bytes(resampled_frame)
            if audio_bytes:
                msg = {
                    "message_type": "input_audio_chunk",
                    "audio_base_64": base64.b64encode(audio_bytes).decode("utf-8"),
                    "commit": False,
                    "sample_rate": SAMPLE_RATE,
                }
                await self._ws.send(json.dumps(msg))
                self._frames_sent += 1
                self._bytes_sent += len(audio_bytes)

                if self._frames_sent == 1:
                    logger.info(f"🎤 First audio chunk sent ({len(audio_bytes)} bytes)")
                elif self._frames_sent % 500 == 0:
                    logger.info(f"🎤 Audio frames sent: {self._frames_sent}")
        except Exception as e:
            if not self._should_stop:
                logger.error(f"Error sending audio to ElevenLabs: {e}")

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

                        # Publish interim as timed metadata if enabled
                        if self.publish_interim_metadata and self.metadata_publisher and self.playlist_url:
                            try:
                                interim_json = json.dumps(
                                    {"interim_transcript": text, "is_final": False},
                                    ensure_ascii=False,
                                )
                                asyncio.ensure_future(self.metadata_publisher.publish_transcript(self.playlist_url, interim_json))
                            except Exception:
                                pass

                elif msg_type == "committed_transcript":
                    text = data.get("text", "")
                    if text:
                        print(f"[TRANSCRIPT] {text}")
                        self._publish_final_transcript(text)

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
                        self._publish_final_transcript(f"{speaker}{text}")

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
                    pass

                else:
                    logger.debug(f"Unhandled message type: {msg_type}")

            except websockets.exceptions.ConnectionClosed:
                logger.info("ElevenLabs WebSocket closed")
                break
            except Exception as e:
                if not self._should_stop:
                    logger.error(f"Error receiving transcript: {e}")
                break

    def _publish_final_transcript(self, text: str) -> None:
        """Publish a final transcript as timed metadata if enabled"""
        if self.metadata_publisher and self.playlist_url:
            try:
                transcript_json = json.dumps({"transcript": text}, ensure_ascii=False)
                future = asyncio.ensure_future(self.metadata_publisher.publish_transcript(self.playlist_url, transcript_json))

                def _on_done(fut):
                    try:
                        result = fut.result()
                        if result:
                            logger.info("📡 Published transcript as timed metadata")
                        else:
                            logger.warning("⚠️  Metadata publish returned False")
                    except Exception as exc:
                        logger.warning(f"⚠️  Metadata publish failed: {exc}")

                future.add_done_callback(_on_done)
            except Exception as pub_err:
                logger.warning(f"⚠️  Failed to schedule transcript metadata publish: {pub_err}")

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


def get_renditions(playlist_url: str) -> List[Dict]:
    """Parse M3U8 playlist and extract available renditions"""
    try:
        logger.info(f"🔍 Parsing M3U8 playlist: {playlist_url}")
        playlist = m3u8.load(playlist_url)

        renditions = []

        if playlist.playlists:
            for i, variant in enumerate(playlist.playlists):
                rendition = {
                    "index": i,
                    "url": variant.absolute_uri,
                    "bandwidth": variant.stream_info.bandwidth if variant.stream_info.bandwidth else 0,
                    "resolution": (
                        f"{variant.stream_info.resolution[0]}x{variant.stream_info.resolution[1]}" if variant.stream_info.resolution else "Unknown"
                    ),
                    "codecs": variant.stream_info.codecs if variant.stream_info.codecs else "Unknown",
                }
                renditions.append(rendition)

            renditions.sort(key=lambda x: x["bandwidth"], reverse=True)
        else:
            renditions.append({"index": 0, "url": playlist_url, "bandwidth": 0, "resolution": "Unknown", "codecs": "Unknown"})

        logger.info(f"✅ Found {len(renditions)} rendition(s)")
        return renditions

    except Exception as e:
        logger.error(f"❌ Error parsing M3U8 playlist: {e}")
        return []


def display_renditions(renditions: List[Dict]) -> None:
    """Display available renditions to the user"""
    print("\n" + "=" * 80)
    print("AVAILABLE RENDITIONS")
    print("=" * 80)

    for rendition in renditions:
        bandwidth_mbps = rendition["bandwidth"] / 1000000 if rendition["bandwidth"] > 0 else 0
        print(f"[{rendition['index']}] {rendition['resolution']} - {bandwidth_mbps:.1f} Mbps - {rendition['codecs']}")

    print("=" * 80 + "\n")


def select_rendition(renditions: List[Dict], auto_select: str = None) -> Optional[Dict]:
    """Select a rendition either automatically or via user input"""
    if not renditions:
        logger.error("❌ No renditions available")
        return None

    if auto_select == "highest":
        selected = renditions[0]
        logger.info(f"🔝 Auto-selected highest quality: {selected['resolution']} - {selected['bandwidth']/1000000:.1f} Mbps")
        return selected
    elif auto_select == "lowest":
        selected = renditions[-1]
        logger.info(f"🔻 Auto-selected lowest quality: {selected['resolution']} - {selected['bandwidth']/1000000:.1f} Mbps")
        return selected
    else:
        display_renditions(renditions)
        while True:
            try:
                choice = input(f"Select rendition [0-{len(renditions)-1}] or 'q' to quit: ").strip()
                if choice.lower() == "q":
                    return None
                index = int(choice)
                if 0 <= index < len(renditions):
                    selected = renditions[index]
                    logger.info(f"✅ Selected: {selected['resolution']} - {selected['bandwidth']/1000000:.1f} Mbps")
                    return selected
                else:
                    print(f"❌ Invalid selection. Please choose 0-{len(renditions)-1}")
            except ValueError:
                print("❌ Invalid input. Please enter a number or 'q'")
            except KeyboardInterrupt:
                return None


async def run_transcription(args: argparse.Namespace, stream_url: str) -> None:
    """Main async transcription loop"""

    # Resolve ElevenLabs API key
    api_key: str = args.elevenlabs_api_key or os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        logger.error("ElevenLabs API key is required. Provide via --elevenlabs-api-key or ELEVENLABS_API_KEY env var.")
        return

    # Initialize metadata publisher if enabled
    metadata_publisher = None
    if args.publish_transcript_as_timed_metadata:
        try:
            region_match = re.search(r"\.([^.]+)\.playback\.live-video\.net", args.playlist_url)
            region = region_match.group(1) if region_match else "us-east-1"
            metadata_publisher = IVSMetadataPublisher(region=region)
            logger.info("📡 Timed metadata publishing enabled")
        except Exception as e:
            logger.error(f"❌ Failed to initialize metadata publisher: {e}")
            logger.info("Continuing without metadata publishing...")

    # Create transcriber
    transcriber = ElevenLabsChannelTranscriber(
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
        metadata_publisher=metadata_publisher,
        playlist_url=args.playlist_url if args.publish_transcript_as_timed_metadata else None,
        publish_interim_metadata=args.publish_interim_metadata,
    )

    # Connect to ElevenLabs
    await transcriber.connect()

    # Initialize OpenCV for display if requested
    cv_display = None
    if args.show_video:
        try:
            import cv2

            cv_display = cv2
            logger.info("🖥️  Video display enabled - press 'q' to quit")
        except ImportError:
            logger.warning("⚠️  OpenCV not available, video display disabled")
            args.show_video = False

    if not args.show_video:
        logger.info("🚫 Video display disabled - press Ctrl+C to quit")

    # Use a queue to bridge the blocking PyAV demux thread with the async event loop.
    # container.demux() blocks waiting for HLS segments from the network, which starves
    # the asyncio event loop and prevents the ElevenLabs WebSocket from sending/receiving.
    audio_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    demux_done = asyncio.Event()

    def _demux_thread(loop: asyncio.AbstractEventLoop) -> None:
        """Run the blocking PyAV demux in a separate thread, pushing frames to the queue."""
        try:
            logger.info("🔗 Opening stream with PyAV...")
            # Use low-latency HLS options to reduce buffering delay
            container = av.open(
                stream_url,
                options={
                    "fflags": "nobuffer",
                    "flags": "low_delay",
                    "analyzeduration": "500000",  # 0.5s instead of default 5s
                    "probesize": "500000",  # 500KB instead of default 5MB
                },
            )

            video_stream = None
            audio_stream = None

            for stream in container.streams:
                if stream.type == "video" and video_stream is None:
                    video_stream = stream
                    logger.info(f"📺 Found video stream: {stream.codec_context.codec.name}, {stream.width}x{stream.height}")
                elif stream.type == "audio" and audio_stream is None:
                    audio_stream = stream
                    logger.info(f"🔊 Found audio stream: {stream.codec_context.codec.name}, {stream.sample_rate}Hz, {stream.channels} channels")

            if not audio_stream:
                logger.error("❌ No audio stream found for transcription")
                return

            demux_streams = [audio_stream]
            if video_stream and args.show_video:
                demux_streams.append(video_stream)

            logger.info("🎤 Starting real-time transcription...")

            for packet in container.demux(*demux_streams):
                if demux_done.is_set():
                    break
                try:
                    for frame in packet.decode():
                        if isinstance(frame, av.AudioFrame):
                            asyncio.run_coroutine_threadsafe(audio_queue.put(frame), loop)
                        elif isinstance(frame, av.VideoFrame) and args.show_video and cv_display:
                            try:
                                img_array = frame.to_ndarray(format="bgr24")
                                cv_display.imshow("IVS Channel Stream", img_array)
                                if cv_display.waitKey(1) & 0xFF == ord("q"):
                                    logger.info("🛑 User requested quit")
                                    demux_done.set()
                                    break
                            except Exception:
                                pass
                except Exception as e:
                    logger.warning(f"Decode error: {e}")
                    continue

        except Exception as e:
            if not demux_done.is_set():
                logger.error(f"❌ Demux thread error: {e}")
                traceback.print_exc()
        finally:
            asyncio.run_coroutine_threadsafe(audio_queue.put(None), loop)

    try:
        print("\n" + "=" * 60)
        print("LIVE TRANSCRIPTION (ElevenLabs Scribe)")
        print("=" * 60)

        # Start the blocking demux in a background thread
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _demux_thread, loop)

        # Consume audio frames from the queue and send to ElevenLabs
        while True:
            frame = await audio_queue.get()
            if frame is None:
                break
            await transcriber.send_audio_frame(frame)

    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
    except Exception as e:
        logger.error(f"❌ Error in main loop: {e}")
        traceback.print_exc()
    finally:
        demux_done.set()
        await transcriber.disconnect()
        if args.show_video and cv_display:
            cv_display.destroyAllWindows()
        logger.info("✅ Cleanup completed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="IVS Channel M3U8 Stream Real-Time Audio Transcription with ElevenLabs Scribe",
        epilog="""
Examples:
  %(prog)s --playlist-url "https://example.com/playlist.m3u8" --highest-quality
  %(prog)s --playlist-url "https://example.com/playlist.m3u8" --language-code es
  %(prog)s --playlist-url "https://example.com/playlist.m3u8" --language-code auto --include-language-detection
  %(prog)s --playlist-url "https://example.com/playlist.m3u8" --publish-transcript-as-timed-metadata
  %(prog)s --playlist-url "https://example.com/playlist.m3u8" --commit-strategy manual
  %(prog)s --playlist-url "https://example.com/playlist.m3u8" --vad-silence-threshold-secs 2.0 --vad-threshold 0.3

Environment variables:
  ELEVENLABS_API_KEY    ElevenLabs API key (alternative to --elevenlabs-api-key)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--playlist-url", required=True, help="M3U8 playlist URL for audio transcription")
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

    parser.add_argument("--show-video", action="store_true", help="Display video frames in a window (requires OpenCV)")

    parser.add_argument(
        "--publish-transcript-as-timed-metadata",
        action="store_true",
        help="Publish transcripts as IVS timed metadata to the channel",
    )
    parser.add_argument(
        "--publish-interim-metadata",
        action="store_true",
        help="Also publish interim (partial) transcripts as timed metadata. Requires --publish-transcript-as-timed-metadata",
    )

    quality_group = parser.add_mutually_exclusive_group()
    quality_group.add_argument("--highest-quality", action="store_true", help="Automatically select highest quality rendition")
    quality_group.add_argument("--lowest-quality", action="store_true", help="Automatically select lowest quality rendition")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logger.info("🎬 IVS Channel M3U8 Stream Real-Time Audio Transcription (ElevenLabs Scribe)")
    logger.info(f"🔗 Playlist URL: {args.playlist_url}")
    logger.info(f"ElevenLabs model: {args.model_id}")
    logger.info(f"Language: {args.language_code}")
    logger.info(f"Commit strategy: {args.commit_strategy}")
    if args.commit_strategy == "vad":
        logger.info(f"VAD silence threshold: {args.vad_silence_threshold_secs}s")
        logger.info(f"VAD threshold: {args.vad_threshold}")
        logger.info(f"Min speech duration: {args.min_speech_duration_ms}ms")
        logger.info(f"Min silence duration: {args.min_silence_duration_ms}ms")

    # Get available renditions
    renditions = get_renditions(args.playlist_url)
    if not renditions:
        logger.error("❌ Failed to get renditions from playlist")
        sys.exit(-1)

    # Select rendition
    auto_select = None
    if args.highest_quality:
        auto_select = "highest"
    elif args.lowest_quality:
        auto_select = "lowest"

    selected_rendition = select_rendition(renditions, auto_select)
    if not selected_rendition:
        logger.info("🛑 No rendition selected, exiting")
        sys.exit(0)

    stream_url = selected_rendition["url"]
    logger.info(f"🎯 Using stream URL: {stream_url}")

    # Run the async transcription loop
    asyncio.run(run_transcription(args, stream_url))


if __name__ == "__main__":
    main()
