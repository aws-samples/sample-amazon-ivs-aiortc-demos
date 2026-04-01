#!/usr/bin/env python3

import asyncio
import traceback
import argparse
import json
import logging
import av
import time
import numpy as np
import os
import sys
import re
from typing import List, Optional, Dict, Any
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
import m3u8
from ivs_metadata_publisher import IVSMetadataPublisher

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ivs-channel-transcribe-deepgram")

# Audio processing configuration
SAMPLE_RATE: int = 16000  # Deepgram supports 16kHz natively
CHANNELS: int = 1

# Audio normalization constants
INT16_MAX: float = 32768.0
INT32_MAX: float = 2147483648.0


class DeepgramChannelTranscriber:
    """Streams audio from an IVS Channel HLS stream to Deepgram for real-time transcription"""

    def __init__(
        self,
        deepgram_api_key: str,
        language: str = "en",
        model: str = "nova-3",
        smart_format: bool = True,
        diarize: bool = False,
        filler_words: bool = False,
        interim_results: bool = True,
        utterance_end_ms: int = 1000,
        endpointing: int = 300,
        metadata_publisher: Optional[IVSMetadataPublisher] = None,
        playlist_url: Optional[str] = None,
    ) -> None:
        self.deepgram_api_key: str = deepgram_api_key
        self.language: str = language
        self.model: str = model
        self.smart_format: bool = smart_format
        self.diarize: bool = diarize
        self.filler_words: bool = filler_words
        self.interim_results: bool = interim_results
        self.utterance_end_ms: int = utterance_end_ms
        self.endpointing: int = endpointing
        self.metadata_publisher: Optional[IVSMetadataPublisher] = metadata_publisher
        self.playlist_url: Optional[str] = playlist_url

        self._resampler: av.AudioResampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        self._connection = None
        self._listen_task: Optional[asyncio.Task] = None
        self._should_stop: bool = False

        # Stats
        self._start_time: Optional[float] = None
        self._frames_sent: int = 0
        self._bytes_sent: int = 0

    async def connect(self) -> None:
        """Establish the Deepgram WebSocket connection"""
        logger.info("Starting Deepgram real-time transcription...")
        self._start_time = time.time()

        self._client = AsyncDeepgramClient(api_key=self.deepgram_api_key)

        # Build connection parameters
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

        # Additional query params not on the SDK's connect() signature
        additional_query_params: Dict[str, str] = {}
        if self.filler_words:
            additional_query_params["filler_words"] = "true"

        if self.language == "auto":
            additional_query_params["detect_language"] = "true"
        else:
            connect_params["language"] = self.language

        if additional_query_params:
            connect_params["request_options"] = {
                "additional_query_parameters": additional_query_params,
            }

        self._ctx = self._client.listen.v1.connect(**connect_params)
        self._connection = await self._ctx.__aenter__()

        # Register event handlers
        self._connection.on(EventType.OPEN, self._on_open)
        self._connection.on(EventType.MESSAGE, self._on_message)
        self._connection.on(EventType.ERROR, self._on_error)
        self._connection.on(EventType.CLOSE, self._on_close)

        # Start the listener in the background
        self._listen_task = asyncio.create_task(self._connection.start_listening())

    async def disconnect(self) -> None:
        """Close the Deepgram connection"""
        if self._connection:
            try:
                await self._connection.send_finalize()
                await asyncio.sleep(1)  # Allow final results
            except Exception:
                pass

        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        if hasattr(self, "_ctx") and self._ctx:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:
                pass

        if self._start_time:
            elapsed = time.time() - self._start_time
            logger.info(f"Deepgram transcription stopped. " f"Duration: {elapsed:.1f}s, Frames: {self._frames_sent}, " f"Bytes: {self._bytes_sent:,}")

    async def send_audio_frame(self, frame: av.AudioFrame) -> None:
        """Resample and send a PyAV audio frame to Deepgram"""
        if not self._connection or self._should_stop:
            return

        try:
            resampled_frames = self._resampler.resample(frame)
            if not resampled_frames:
                return
            resampled_frame = resampled_frames[0]

            audio_bytes = self._frame_to_bytes(resampled_frame)
            if audio_bytes:
                await self._connection.send_media(audio_bytes)
                self._frames_sent += 1
                self._bytes_sent += len(audio_bytes)
        except Exception as e:
            if not self._should_stop:
                logger.error(f"Error sending audio to Deepgram: {e}")

    def _frame_to_bytes(self, frame: av.AudioFrame) -> bytes:
        """Convert a PyAV audio frame to raw PCM bytes"""
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

    def _on_open(self, *args, **kwargs) -> None:
        logger.info("✅ Deepgram WebSocket connection opened")

    def _on_message(self, message, *args, **kwargs) -> None:
        """Handle transcription results from Deepgram"""
        try:
            msg_type = getattr(message, "type", "unknown")

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

            if not message.channel or not message.channel.alternatives:
                return

            alt = message.channel.alternatives[0]
            transcript = alt.transcript
            if not transcript:
                return

            is_final = getattr(message, "is_final", False)

            # Get timing and speaker info from words
            words = getattr(alt, "words", [])
            speaker = ""
            if words and hasattr(words[0], "speaker") and words[0].speaker is not None:
                speaker = f"[Speaker {int(words[0].speaker)}] "

            if is_final:
                confidence = getattr(alt, "confidence", 0.0)
                print(f"[TRANSCRIPT] {speaker}{transcript}  (confidence: {confidence:.2f})")

                # Publish as timed metadata if enabled
                if self.metadata_publisher and self.playlist_url:
                    try:
                        transcript_json = json.dumps({"transcript": f"{speaker}{transcript}"}, ensure_ascii=False)
                        future = asyncio.ensure_future(self.metadata_publisher.publish_transcript(self.playlist_url, transcript_json))

                        def _on_publish_done(fut):
                            try:
                                result = fut.result()
                                if result:
                                    logger.info(f"📡 Published transcript as timed metadata")
                                else:
                                    logger.warning("⚠️  Metadata publish returned False")
                            except Exception as exc:
                                logger.warning(f"⚠️  Metadata publish failed: {exc}")

                        future.add_done_callback(_on_publish_done)
                    except Exception as pub_err:
                        logger.warning(f"⚠️  Failed to schedule transcript metadata publish: {pub_err}")
            else:
                print(f"[INTERIM] {speaker}{transcript}", end="\r", flush=True)

        except Exception as e:
            logger.error(f"Error processing Deepgram message: {e}")
            traceback.print_exc()

    def _on_error(self, error, *args, **kwargs) -> None:
        logger.error(f"❌ Deepgram error: {error}")

    def _on_close(self, *args, **kwargs) -> None:
        logger.info("Deepgram WebSocket connection closed")

    def stop(self) -> None:
        logger.info("Stopping Deepgram transcription...")
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

    # Resolve Deepgram API key
    deepgram_api_key: str = args.deepgram_api_key or os.environ.get("DEEPGRAM_API_KEY", "")
    if not deepgram_api_key:
        logger.error("Deepgram API key is required. Provide via --deepgram-api-key or DEEPGRAM_API_KEY env var.")
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
    transcriber = DeepgramChannelTranscriber(
        deepgram_api_key=deepgram_api_key,
        language=args.language,
        model=args.model,
        smart_format=args.smart_format,
        diarize=args.diarize,
        filler_words=args.filler_words,
        interim_results=args.interim_results,
        utterance_end_ms=args.utterance_end_ms,
        endpointing=args.endpointing,
        metadata_publisher=metadata_publisher,
        playlist_url=args.playlist_url if args.publish_transcript_as_timed_metadata else None,
    )

    # Connect to Deepgram
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
    # the asyncio event loop and prevents the Deepgram WebSocket from sending/receiving.
    audio_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    demux_done = asyncio.Event()

    def _demux_thread(loop: asyncio.AbstractEventLoop) -> None:
        """Run the blocking PyAV demux in a separate thread, pushing frames to the queue."""
        try:
            logger.info("🔗 Opening stream with PyAV...")
            container = av.open(stream_url)

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
        print("LIVE TRANSCRIPTION (Deepgram)")
        print("=" * 60)

        # Start the blocking demux in a background thread
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _demux_thread, loop)

        # Consume audio frames from the queue and send to Deepgram
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
        description="IVS Channel M3U8 Stream Real-Time Audio Transcription with Deepgram",
        epilog="""
Examples:
  %(prog)s --playlist-url "https://example.com/playlist.m3u8" --highest-quality
  %(prog)s --playlist-url "https://example.com/playlist.m3u8" --model nova-3 --language es --diarize
  %(prog)s --playlist-url "https://example.com/playlist.m3u8" --language auto --publish-transcript-as-timed-metadata
  %(prog)s --playlist-url "https://example.com/playlist.m3u8" --model nova-3-medical --filler-words

Environment variables:
  DEEPGRAM_API_KEY    Deepgram API key (alternative to --deepgram-api-key)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--playlist-url", required=True, help="M3U8 playlist URL for audio transcription")

    parser.add_argument("--deepgram-api-key", default=None, help="Deepgram API key (can also be set via DEEPGRAM_API_KEY env var)")

    parser.add_argument(
        "--model",
        default="nova-3",
        help=(
            "Deepgram model for transcription. Common models: nova-3, nova-3-medical, "
            "nova-2, nova-2-meeting, nova-2-finance, nova-2-medical, enhanced, base (default: nova-3)"
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
        help="Duration in ms of silence before finalizing speech (default: 300)",
    )

    parser.add_argument("--show-video", action="store_true", help="Display video frames in a window (requires OpenCV)")

    parser.add_argument(
        "--publish-transcript-as-timed-metadata",
        action="store_true",
        help="Publish transcripts as IVS timed metadata to the channel",
    )

    quality_group = parser.add_mutually_exclusive_group()
    quality_group.add_argument("--highest-quality", action="store_true", help="Automatically select highest quality rendition")
    quality_group.add_argument("--lowest-quality", action="store_true", help="Automatically select lowest quality rendition")

    return parser.parse_args()


def main() -> None:
    args: argparse.Namespace = parse_args()

    logger.info("🎬 IVS Channel M3U8 Stream Real-Time Audio Transcription (Deepgram)")
    logger.info(f"🔗 Playlist URL: {args.playlist_url}")
    logger.info(f"Deepgram model: {args.model}")
    logger.info(f"Language: {args.language}")
    logger.info(f"Smart format: {args.smart_format}")
    logger.info(f"Diarize: {args.diarize}")
    logger.info(f"Filler words: {args.filler_words}")
    logger.info(f"Endpointing: {args.endpointing}ms")

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
