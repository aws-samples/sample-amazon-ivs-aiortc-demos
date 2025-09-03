#!/usr/bin/env python3

import av
import cv2
import asyncio
import json
import logging
import argparse
import base64
import time
import io
import sys
from typing import Dict, Any, List, Optional
from fractions import Fraction
import numpy as np
import boto3
from PIL import Image
import m3u8

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ivs-channel-m3u8-video-analyzer")
logger.setLevel(logging.DEBUG)


class AudioVideoChunkRecorder:
    """Records audio and video chunks in memory for analysis"""

    def __init__(self, chunk_duration: float = 10.0):
        """
        Initialize the audio and video chunk recorder

        Args:
            chunk_duration: Duration in seconds for each chunk
        """
        self.chunk_duration = chunk_duration
        self.recorded_video_frames = []
        self.recorded_audio_frames = []
        self.should_stop = False
        self.recording = False
        self.start_time = 0

    async def start_recording(self):
        """Start recording a new chunk"""
        if self.recording:
            logger.warning("Already recording a chunk")
            return False

        logger.info("🎥 Starting audio and video chunk recording")
        self.recorded_video_frames = []
        self.recorded_audio_frames = []
        self.recording = True
        self.start_time = time.time()
        return True

    async def stop_recording(self):
        """Stop recording and return the recorded frames"""
        if not self.recording:
            logger.warning("Not currently recording")
            return None

        logger.info(f"⏹️  Stopping recording after {time.time() - self.start_time:.2f}s")
        self.recording = False

        if not self.recorded_video_frames:
            logger.error("No video frames were recorded")
            return None

        logger.info(f"✅ Recorded {len(self.recorded_video_frames)} video frames")
        logger.info(f"✅ Recorded {len(self.recorded_audio_frames)} audio frames")
        return self.recorded_video_frames.copy(), self.recorded_audio_frames.copy()

    def process_video_frame(self, cv_frame):
        """Process a video frame from OpenCV"""
        if self.recording:
            # Convert OpenCV frame (BGR) to RGB
            rgb_frame = cv2.cvtColor(cv_frame, cv2.COLOR_BGR2RGB)
            # Convert to av.VideoFrame
            av_frame = av.VideoFrame.from_ndarray(rgb_frame, format="rgb24")
            self.recorded_video_frames.append(av_frame)

    def encode_video_to_mp4(self, video_frames, audio_frames=None):
        """
        Encode video frames to MP4 format in memory using pure Python (no ffmpeg)

        Args:
            video_frames: List of video frames
            audio_frames: List of audio frames (optional for video-only encoding)

        Returns:
            Base64 encoded MP4 video or None if failed
        """
        if not video_frames:
            logger.error("No video frames to encode")
            return None

        try:
            # Create in-memory buffer
            output_buffer = io.BytesIO()

            target_width = 640
            target_height = 360

            # output container
            output = av.open(output_buffer, mode="w", format="mp4")

            # Add video stream with explicit framerate
            video_stream = output.add_stream("h264", rate=30)
            video_stream.width = target_width
            video_stream.height = target_height
            video_stream.pix_fmt = "yuv420p"
            video_stream.options = {"preset": "ultrafast", "profile": "baseline"}

            new_video_pts = 0

            for i, video_frame in enumerate(video_frames):
                try:
                    # Convert frame to PIL Image for resizing
                    if hasattr(video_frame, "to_ndarray"):
                        # av.VideoFrame
                        frame_array = video_frame.to_ndarray(format="rgb24")
                    else:
                        # Already numpy array
                        frame_array = video_frame

                    img = Image.fromarray(frame_array)
                    img = img.resize((target_width, target_height), Image.LANCZOS)
                    resized_array = np.array(img)

                    # Create new frame from numpy array
                    resized_video_frame = av.VideoFrame.from_ndarray(resized_array, format="rgb24")
                    resized_video_frame.pts = new_video_pts
                    resized_video_frame.time_base = Fraction(1, 30)  # Match the stream's rate

                    video_packets = video_stream.encode(resized_video_frame)
                    new_video_pts += 1
                    for packet in video_packets:
                        output.mux(packet)
                except Exception as frame_error:
                    logger.warning(f"Skipping video frame {i} due to error: {frame_error}")
                    continue

            # Flush the encoder
            for packet in video_stream.encode(None):
                output.mux(packet)

            # Close the container
            output.close()

            # Get the encoded data
            encoded_data = output_buffer.getvalue()

            # Convert to base64
            base64_data = base64.b64encode(encoded_data).decode("utf-8")

            logger.info(f"✅ Successfully encoded video: {len(encoded_data)} bytes, {len(base64_data)} base64 chars")

            return base64_data

        except Exception as e:
            logger.error(f"Error encoding video: {e}")
            import traceback

            traceback.print_exc()
            return None


class VideoAnalyzer:
    """Handles video analysis using TwelveLabs Pegasus"""

    def __init__(self, analysis_duration: float = 10.0, region: str = "us-west-2", model_id: str = "us.twelvelabs.pegasus-1-2-v1:0"):
        """
        Initialize the video analyzer

        Args:
            analysis_duration: Duration in seconds for video recording before analysis
            region: AWS region for Bedrock service
            model_id: Bedrock model ID to use for analysis
        """
        self.analysis_duration = analysis_duration
        self.last_analysis_time = 0
        self.bedrock_client = boto3.client("bedrock-runtime", region_name=region)
        self.model_id = model_id
        self.analysis_in_progress = False

        logger.info(f"🤖 VideoAnalyzer initialized with {analysis_duration}s recording duration")
        logger.info(f"🌍 Using Bedrock region: {region}")
        logger.info(f"🧠 Using model: {self.model_id}")

    def should_analyze_video(self) -> bool:
        """Check if enough time has passed since last analysis and no analysis is in progress"""
        current_time = time.time()
        if current_time - self.last_analysis_time >= self.analysis_duration and not self.analysis_in_progress:
            self.last_analysis_time = current_time
            return True
        return False

    async def analyze_video(self, video_base64: str) -> Optional[str]:
        """
        Analyze a video using TwelveLabs Pegasus

        Args:
            video_base64: Base64 encoded video data

        Returns:
            Analysis result string or None if failed
        """
        # Mark analysis as in progress to prevent overlapping recordings
        self.analysis_in_progress = True

        try:
            if not video_base64:
                logger.error("No video data provided for analysis")
                return None

            # Prepare the request for Pegasus
            request_body = {
                "inputPrompt": "Analyze this video from a live stream. Describe what you see in detail, including people, objects, activities, text, and any notable features. This could be used for content discovery, moderation, or accessibility purposes. Be specific and comprehensive.",
                "mediaSource": {"base64String": video_base64},
                "temperature": 0.2,
            }

            # Call Bedrock
            logger.info("🔍 Analyzing video...")

            response = self.bedrock_client.invoke_model(
                modelId=self.model_id, body=json.dumps(request_body), contentType="application/json", accept="application/json"
            )

            # Parse response
            response_body = json.loads(response["body"].read())
            analysis_result = response_body.get("message", "No analysis result")
            finish_reason = response_body.get("finishReason", "unknown")

            logger.info(f"✅ Video analysis completed (finish reason: {finish_reason})")
            logger.info(f"📝 Analysis: {analysis_result}")

            return analysis_result

        except Exception as e:
            logger.error(f"Error analyzing video: {e}")
            import traceback

            traceback.print_exc()
            return None
        finally:
            # Mark analysis as complete
            self.analysis_in_progress = False


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


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="IVS Channel M3U8 Stream Video Analyzer with Bedrock")
    parser.add_argument(
        "--playlist-url",
        required=True,
        help="M3U8 playlist URL",
    )
    parser.add_argument("--show-video", action="store_true", help="Display video frames in a window")
    parser.add_argument(
        "--analysis-duration", type=float, default=10.0, help="Duration in seconds for video recording before analysis (default: 10.0)"
    )
    parser.add_argument("--bedrock-region", default="us-west-2", help="AWS region for Bedrock service (default: us-west-2)")
    parser.add_argument(
        "--bedrock-model-id",
        default="us.twelvelabs.pegasus-1-2-v1:0",
        help="Bedrock model ID for video analysis (default: us.twelvelabs.pegasus-1-2-v1:0)",
    )
    parser.add_argument("--disable-analysis", action="store_true", help="Disable video analysis")

    # Rendition selection arguments
    quality_group = parser.add_mutually_exclusive_group()
    quality_group.add_argument("--highest-quality", action="store_true", help="Automatically select highest quality rendition")
    quality_group.add_argument("--lowest-quality", action="store_true", help="Automatically select lowest quality rendition")

    return parser.parse_args()


def main():
    """Main function that handles M3U8 stream processing with video analysis"""
    args = parse_args()

    logger.info("🎬 IVS Channel M3U8 Stream Video Analyzer")
    logger.info(f"🔗 Playlist URL: {args.playlist_url}")

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

    # Initialize video analyzer if not disabled
    analyzer = None
    if not args.disable_analysis:
        try:
            analyzer = VideoAnalyzer(analysis_duration=args.analysis_duration, region=args.bedrock_region, model_id=args.bedrock_model_id)
            logger.info(f"🤖 Video analysis enabled (recording duration: {args.analysis_duration}s)")
        except Exception as e:
            logger.error(f"❌ Failed to initialize VideoAnalyzer: {e}")
            logger.error("Continuing without video analysis...")
    else:
        logger.info("🚫 Video analysis disabled")

    # Create video chunk recorder
    recorder = AudioVideoChunkRecorder(args.analysis_duration)

    # Open video capture
    cap = cv2.VideoCapture(stream_url)
    if not cap.isOpened():
        logger.error("❌ Unable to open video stream")
        sys.exit(-1)

    # Get FPS and calculate wait time
    fps = cap.get(cv2.CAP_PROP_FPS)
    wait_ms = int(1000 / fps) if fps > 0 else 33  # Default to ~30fps if fps is 0
    logger.info(f"📊 Stream FPS: {fps}")

    if args.show_video:
        logger.info("🖥️  Video display enabled - press 'q' to quit")
    else:
        logger.info("🚫 Video display disabled - press Ctrl+C to quit")

    try:
        frame_count = 0
        while True:
            # Read one frame
            ret, frame = cap.read()

            if not ret:
                logger.warning("⚠️  Failed to read frame, retrying...")
                continue

            frame_count += 1

            # Process frame for recording if analyzer is available
            if analyzer:
                # Start recording if enough time has passed and not currently recording
                if analyzer.should_analyze_video() and not recorder.recording:
                    asyncio.run(recorder.start_recording())

                # Process the frame
                recorder.process_video_frame(frame)

                # Check if recording duration has been reached
                if recorder.recording and (time.time() - recorder.start_time) >= analyzer.analysis_duration:
                    # Stop recording and get frames
                    video_frames, audio_frames = asyncio.run(recorder.stop_recording())

                    if video_frames:
                        # Encode video to MP4 in memory
                        video_base64 = recorder.encode_video_to_mp4(video_frames, audio_frames)

                        if video_base64:
                            # Analyze the video
                            asyncio.run(analyzer.analyze_video(video_base64))
                        else:
                            logger.error("Failed to encode video to MP4")
                    else:
                        logger.error("No frames recorded")

            # Display frame if requested
            if args.show_video:
                cv2.imshow("IVS Channel Stream", frame)
                if cv2.waitKey(wait_ms) & 0xFF == ord("q"):
                    logger.info("🛑 User requested quit")
                    break
            else:
                # Just sleep to maintain frame rate without display
                time.sleep(wait_ms / 1000.0)

    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
    except Exception as e:
        logger.error(f"❌ Error in main loop: {e}")
        import traceback

        traceback.print_exc()
    finally:
        # Clean up
        cap.release()
        if args.show_video:
            cv2.destroyAllWindows()
        logger.info("✅ Cleanup completed")


if __name__ == "__main__":
    main()
