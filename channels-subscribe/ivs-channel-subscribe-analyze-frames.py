#!/usr/bin/env python3

import cv2
import sys
import argparse
import logging
import time
import base64
import json
import io
from typing import Optional, List, Dict
import boto3
from PIL import Image
import m3u8

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ivs-channel-m3u8-analyzer")
logger.setLevel(logging.DEBUG)


class VideoFrameAnalyzer:
    """Handles video frame analysis using Amazon Bedrock Claude"""

    def __init__(self, analysis_interval: float = 30.0, region: str = "us-east-1", model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0"):
        """
        Initialize the video frame analyzer

        Args:
            analysis_interval: Time in seconds between frame analyses
            region: AWS region for Bedrock service
            model_id: Bedrock model ID to use for analysis
        """
        self.analysis_interval = analysis_interval
        self.last_analysis_time = 0
        self.bedrock_client = boto3.client("bedrock-runtime", region_name=region)
        self.model_id = model_id

        logger.info(f"🤖 VideoFrameAnalyzer initialized with {analysis_interval}s interval")
        logger.info(f"🌍 Using Bedrock region: {region}")
        logger.info(f"🧠 Using model: {self.model_id}")

    def should_analyze_frame(self) -> bool:
        """Check if enough time has passed since last analysis"""
        current_time = time.time()
        if current_time - self.last_analysis_time >= self.analysis_interval:
            self.last_analysis_time = current_time
            return True
        return False

    def frame_to_base64(self, cv_frame) -> Optional[str]:
        """Convert OpenCV frame to base64 encoded JPEG"""
        try:
            # Convert BGR to RGB (OpenCV uses BGR by default)
            rgb_frame = cv2.cvtColor(cv_frame, cv2.COLOR_BGR2RGB)

            # Convert to PIL Image
            img = Image.fromarray(rgb_frame)

            # Save to bytes buffer as JPEG
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            buffer.seek(0)

            # Encode to base64
            img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
            return img_base64

        except Exception as e:
            logger.error(f"Error converting frame to base64: {e}")
            return None

    def analyze_frame(self, cv_frame) -> Optional[str]:
        """
        Analyze a video frame using Claude

        Args:
            cv_frame: OpenCV frame (numpy array)

        Returns:
            Analysis result string or None if failed
        """
        try:
            # Convert frame to base64
            frame_base64 = self.frame_to_base64(cv_frame)
            if not frame_base64:
                return None

            # Prepare the message for Claude
            message = {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_base64}},
                    {
                        "type": "text",
                        "text": "Analyze this video frame from a live stream. Describe what you see in detail, including people, objects, activities, text, and any notable features. This could be used for content discovery, moderation, or accessibility purposes. Be specific and comprehensive.",
                    },
                ],
            }

            # Call Bedrock
            logger.info("🔍 Analyzing frame...")

            response = self.bedrock_client.invoke_model(
                modelId=self.model_id,
                body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 1000, "messages": [message], "temperature": 0.1}),
            )

            # Parse response
            response_body = json.loads(response["body"].read())
            analysis_result = response_body["content"][0]["text"]

            logger.info("✅ Frame analysis completed")
            logger.info(f"📝 Analysis: {analysis_result}")

            return analysis_result

        except Exception as e:
            logger.error(f"Error analyzing frame: {e}")
            import traceback

            traceback.print_exc()
            return None


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
    parser = argparse.ArgumentParser(description="IVS Channel M3U8 Stream Analyzer with Bedrock")
    parser.add_argument(
        "--playlist-url",
        required=True,
        help="M3U8 playlist URL",
    )
    parser.add_argument("--show-video", action="store_true", help="Display video frames in a window")
    parser.add_argument("--analysis-interval", type=float, default=30.0, help="Time in seconds between frame analyses (default: 30.0)")
    parser.add_argument("--bedrock-region", default="us-east-1", help="AWS region for Bedrock service (default: us-east-1)")
    parser.add_argument(
        "--bedrock-model-id",
        default="us.anthropic.claude-sonnet-4-20250514-v1:0",
        help="Bedrock model ID for frame analysis (default: us.anthropic.claude-sonnet-4-20250514-v1:0)",
    )
    parser.add_argument("--disable-analysis", action="store_true", help="Disable video frame analysis")

    # Rendition selection arguments
    quality_group = parser.add_mutually_exclusive_group()
    quality_group.add_argument("--highest-quality", action="store_true", help="Automatically select highest quality rendition")
    quality_group.add_argument("--lowest-quality", action="store_true", help="Automatically select lowest quality rendition")

    return parser.parse_args()


def main():
    """Main function that handles M3U8 stream processing"""
    args = parse_args()

    logger.info("🎬 IVS Channel M3U8 Stream Analyzer with Frame Analysis")
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

    # Initialize video frame analyzer if not disabled
    analyzer = None
    if not args.disable_analysis:
        try:
            analyzer = VideoFrameAnalyzer(analysis_interval=args.analysis_interval, region=args.bedrock_region, model_id=args.bedrock_model_id)
            logger.info(f"🤖 Video frame analysis enabled (every {args.analysis_interval}s)")
        except Exception as e:
            logger.error(f"❌ Failed to initialize VideoFrameAnalyzer: {e}")
            logger.error("Continuing without frame analysis...")
    else:
        logger.info("🚫 Video frame analysis disabled")

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

            # Perform frame analysis if analyzer is available and interval has passed
            if analyzer and analyzer.should_analyze_frame():
                try:
                    analyzer.analyze_frame(frame)
                except Exception as e:
                    logger.error(f"Frame analysis error: {e}")

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
