#!/usr/bin/env python3

import asyncio
import traceback
import argparse
import json
import logging
import av
import time
import numpy as np
import sys
from typing import List, Optional, Dict, Any
import whisper
import m3u8
from ivs_metadata_publisher import IVSMetadataPublisher

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ivs-channel-m3u8-transcribe")

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
        chunk_duration: int = CHUNK_DURATION,
        language: str = "en",
        metadata_publisher: Optional[IVSMetadataPublisher] = None,
        playlist_url: Optional[str] = None,
    ) -> None:
        self.chunk_duration: int = chunk_duration
        self.language: str = language
        self.metadata_publisher: Optional[IVSMetadataPublisher] = metadata_publisher
        self.playlist_url: Optional[str] = playlist_url
        self._recording_frames: List[av.AudioFrame] = []
        self._count: int = 0
        self._start_time: Optional[float] = None
        self._should_stop: bool = False
        self._resampler: av.AudioResampler = av.AudioResampler(format="s16", layout="mono" if CHANNELS == 1 else "stereo", rate=SAMPLE_RATE)

    def start_recording(self) -> None:
        """Start recording a new chunk"""
        logger.info("🎤 Starting audio chunk recording")
        self._recording_frames = []
        self._start_time = time.time()

    def stop_recording(self) -> bool:
        """Stop recording and return whether we have enough data"""
        if not self._recording_frames:
            return False

        duration = time.time() - self._start_time if self._start_time else 0
        logger.info(f"⏹️  Stopping recording after {duration:.2f}s with {len(self._recording_frames)} frames")
        return True

    def process_audio_frame(self, frame: av.AudioFrame) -> None:
        """Process an audio frame from PyAV"""
        try:
            # Resample the audio frame to s16 format
            resampled_frames = self._resampler.resample(frame)
            if resampled_frames:
                self._recording_frames.append(resampled_frames[0])
        except Exception as e:
            logger.warning(f"Error resampling audio frame: {e}")

    async def process_chunk_in_memory(self) -> None:
        """Process the recorded chunk for transcription"""
        if not self._recording_frames:
            logger.warning("No audio frames to process")
            return

        try:
            # Convert frames to numpy array for Whisper
            audio_data: np.ndarray = self._frames_to_numpy(self._recording_frames)

            if len(audio_data) == 0:
                logger.warning("No audio data after conversion")
                return

            self._count += 1
            logger.info(f"Processing audio chunk {self._count} in memory (shape: {audio_data.shape})")

            # Transcribe asynchronously to avoid blocking
            language_param: Optional[str] = None if self.language == "auto" else self.language

            # Run Whisper transcription in a separate thread
            try:
                # Use asyncio.to_thread for Python 3.9+, fallback to run_in_executor for older versions
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

                    # Publish transcript as timed metadata if enabled
                    if self.metadata_publisher and self.playlist_url:
                        try:
                            # Format transcript as JSON
                            transcript_json = json.dumps({"transcript": text}, ensure_ascii=False)
                            asyncio.create_task(self.metadata_publisher.publish_transcript(self.playlist_url, transcript_json))
                        except Exception as publish_error:
                            logger.warning(f"⚠️  Failed to publish transcript metadata: {publish_error}")
                else:
                    logger.info("No speech detected in chunk")

            except Exception as transcribe_error:
                logger.error(f"Error during Whisper transcription: {transcribe_error}")

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
                frame_array: np.ndarray = frame.to_ndarray()

                # Handle different array shapes
                if frame_array.ndim == 2:
                    # Multi-channel audio - take first channel or average
                    if CHANNELS == 1:
                        frame_array = frame_array[0]  # Take first channel
                    else:
                        frame_array = np.mean(frame_array, axis=0, dtype=np.float32)

                audio_samples.append(frame_array)

            # Concatenate all samples
            if audio_samples:
                audio_data: np.ndarray = np.concatenate(audio_samples)

                # Convert to float32 and normalize to [-1, 1] range
                if audio_data.dtype == np.int16:
                    audio_data = audio_data.astype(np.float32) / INT16_MAX
                elif audio_data.dtype == np.int32:
                    audio_data = audio_data.astype(np.float32) / INT32_MAX
                elif audio_data.dtype != np.float32:
                    audio_data = audio_data.astype(np.float32)

                # Ensure the sample rate matches what Whisper expects (16kHz)
                if SAMPLE_RATE != WHISPER_SAMPLE_RATE:
                    target_length: int = int(len(audio_data) * WHISPER_SAMPLE_RATE / SAMPLE_RATE)
                    x_old: np.ndarray = np.arange(len(audio_data), dtype=np.float32)
                    x_new: np.ndarray = np.linspace(0, len(audio_data) - 1, target_length, dtype=np.float32)
                    audio_data = np.interp(x_new, x_old, audio_data.astype(np.float32))

                return audio_data.astype(np.float32)
            else:
                return np.array([], dtype=np.float32)

        except Exception as e:
            logger.error(f"Error converting frames to numpy: {e}")
            traceback.print_exc()
            return np.array([], dtype=np.float32)


def get_renditions(playlist_url: str) -> List[Dict]:
    """
    Parse M3U8 playlist and extract available renditions

    Args:
        playlist_url: URL to the M3U8 playlist

    Returns:
        List of rendition dictionaries with quality info
    """
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

            # Sort by bandwidth (quality) - highest first
            renditions.sort(key=lambda x: x["bandwidth"], reverse=True)

        else:
            # Single rendition playlist
            renditions.append({"index": 0, "url": playlist_url, "bandwidth": 0, "resolution": "Unknown", "codecs": "Unknown"})

        logger.info(f"✅ Found {len(renditions)} rendition(s)")
        return renditions

    except Exception as e:
        logger.error(f"❌ Error parsing M3U8 playlist: {e}")
        return []


def display_renditions(renditions: List[Dict]) -> None:
    """Display available renditions to the user"""
    logger.info("📺 Available renditions:")
    print("\n" + "=" * 80)
    print("AVAILABLE RENDITIONS")
    print("=" * 80)

    for rendition in renditions:
        bandwidth_mbps = rendition["bandwidth"] / 1000000 if rendition["bandwidth"] > 0 else 0
        print(f"[{rendition['index']}] {rendition['resolution']} - {bandwidth_mbps:.1f} Mbps - {rendition['codecs']}")

    print("=" * 80 + "\n")


def select_rendition(renditions: List[Dict], auto_select: str = None) -> Optional[Dict]:
    """
    Select a rendition either automatically or via user input

    Args:
        renditions: List of available renditions
        auto_select: 'highest', 'lowest', or None for manual selection

    Returns:
        Selected rendition dictionary or None if cancelled
    """
    if not renditions:
        logger.error("❌ No renditions available")
        return None

    if auto_select == "highest":
        selected = renditions[0]  # Already sorted by bandwidth desc
        logger.info(f"🔝 Auto-selected highest quality: {selected['resolution']} - {selected['bandwidth']/1000000:.1f} Mbps")
        return selected

    elif auto_select == "lowest":
        selected = renditions[-1]  # Last in sorted list
        logger.info(f"🔻 Auto-selected lowest quality: {selected['resolution']} - {selected['bandwidth']/1000000:.1f} Mbps")
        return selected

    else:
        # Manual selection
        display_renditions(renditions)

        while True:
            try:
                choice = input(f"Select rendition [0-{len(renditions)-1}] or 'q' to quit: ").strip()

                if choice.lower() == "q":
                    logger.info("🛑 User cancelled rendition selection")
                    return None

                index = int(choice)
                if 0 <= index < len(renditions):
                    selected = renditions[index]
                    logger.info(f"✅ Selected rendition: {selected['resolution']} - {selected['bandwidth']/1000000:.1f} Mbps")
                    return selected
                else:
                    print(f"❌ Invalid selection. Please choose 0-{len(renditions)-1}")

            except ValueError:
                print("❌ Invalid input. Please enter a number or 'q'")
            except KeyboardInterrupt:
                logger.info("\n🛑 User cancelled rendition selection")
                return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="IVS Channel M3U8 Stream Real-Time Audio Transcription with Whisper",
        epilog="""
Examples:
  %(prog)s --playlist-url "https://example.com/playlist.m3u8" --highest-quality
  %(prog)s --playlist-url "https://example.com/playlist.m3u8" --whisper-model base --language es
  %(prog)s --playlist-url "https://example.com/playlist.m3u8" --chunk-duration 10 --language auto
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--playlist-url",
        required=True,
        help="M3U8 playlist URL for audio transcription",
    )

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

    parser.add_argument("--show-video", action="store_true", help="Display video frames in a window (requires OpenCV)")

    parser.add_argument(
        "--publish-transcript-as-timed-metadata", action="store_true", help="Publish transcripts as IVS timed metadata to the channel"
    )

    # Rendition selection arguments
    quality_group = parser.add_mutually_exclusive_group()
    quality_group.add_argument("--highest-quality", action="store_true", help="Automatically select highest quality rendition")
    quality_group.add_argument("--lowest-quality", action="store_true", help="Automatically select lowest quality rendition")

    return parser.parse_args()


def main() -> None:
    global whisper_model, FP16

    args: argparse.Namespace = parse_args()

    logger.info("🎬 IVS Channel M3U8 Stream Real-Time Audio Transcription")
    logger.info(f"🔗 Playlist URL: {args.playlist_url}")
    logger.info(f"Loading Whisper model: {args.whisper_model}")
    logger.info(f"Language: {args.language}")
    logger.info(f"FP16: {args.fp16}")
    logger.info(f"Chunk duration: {args.chunk_duration}s")

    # Set global FP16 variable
    FP16 = args.fp16

    # Load Whisper model
    logger.info("🤖 Loading Whisper model...")
    whisper_model = whisper.load_model(args.whisper_model)
    logger.info("✅ Whisper model loaded successfully")

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

    # Initialize metadata publisher if enabled
    metadata_publisher = None
    if args.publish_transcript_as_timed_metadata:
        try:
            # Extract region from playlist URL for metadata publisher
            import re

            region_match = re.search(r"\.([^.]+)\.playback\.live-video\.net", args.playlist_url)
            region = region_match.group(1) if region_match else "us-east-1"

            metadata_publisher = IVSMetadataPublisher(region=region)
            logger.info("📡 Timed metadata publishing enabled")
        except Exception as e:
            logger.error(f"❌ Failed to initialize metadata publisher: {e}")
            logger.info("Continuing without metadata publishing...")

    # Create audio chunk recorder
    recorder = AudioChunkRecorder(
        args.chunk_duration,
        args.language,
        metadata_publisher=metadata_publisher,
        playlist_url=args.playlist_url if args.publish_transcript_as_timed_metadata else None,
    )

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

    try:
        # Open the stream with PyAV
        logger.info("🔗 Opening stream with PyAV...")
        container = av.open(stream_url)

        # Get stream info
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
            sys.exit(-1)

        if not video_stream and args.show_video:
            logger.warning("⚠️  No video stream found, disabling video display")
            args.show_video = False

        logger.info("🎤 Starting real-time transcription...")
        print("\n" + "=" * 60)
        print("LIVE TRANSCRIPTION")
        print("=" * 60)

        frame_count = 0
        last_display_time = time.time()
        display_interval = 1.0 / 30.0  # 30 FPS display rate
        recording_started = False

        for packet in container.demux(video_stream, audio_stream):
            try:
                for frame in packet.decode():
                    if isinstance(frame, av.VideoFrame):
                        frame_count += 1

                        # Display frame if requested and enough time has passed
                        current_time = time.time()
                        if args.show_video and cv_display and (current_time - last_display_time) >= display_interval:
                            try:
                                # Convert frame to numpy array for OpenCV
                                img_array = frame.to_ndarray(format="bgr24")
                                cv_display.imshow("IVS Channel Stream", img_array)

                                if cv_display.waitKey(1) & 0xFF == ord("q"):
                                    logger.info("🛑 User requested quit")
                                    break

                                last_display_time = current_time
                            except Exception as display_error:
                                logger.warning(f"Display error: {display_error}")

                    elif isinstance(frame, av.AudioFrame):
                        # Start recording if not already started
                        if not recording_started:
                            recorder.start_recording()
                            recording_started = True

                        # Process audio frame
                        recorder.process_audio_frame(frame)

                        # Check if we should process the chunk
                        if recording_started and (time.time() - recorder._start_time) >= args.chunk_duration:
                            if recorder.stop_recording():
                                # Process the chunk asynchronously
                                asyncio.run(recorder.process_chunk_in_memory())

                            # Start new recording
                            recorder.start_recording()

            except Exception as e:
                logger.warning(f"Decode error: {e}")
                continue

    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
    except Exception as e:
        logger.error(f"❌ Error in main loop: {e}")
        import traceback

        traceback.print_exc()
    finally:
        # Clean up
        if args.show_video and cv_display:
            cv_display.destroyAllWindows()
        logger.info("✅ Cleanup completed")


if __name__ == "__main__":
    main()
