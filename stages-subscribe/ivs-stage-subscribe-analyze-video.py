#!/usr/bin/env python3

import av
import asyncio
import json
import logging
import argparse
import base64
import requests
import time
import io
import os
import tempfile
import uuid
import subprocess
from typing import Dict, Any, List, Optional
from fractions import Fraction
import numpy as np 
from aiortc import (
    RTCBundlePolicy,
    RTCConfiguration,
    RTCPeerConnection,
    RTCSessionDescription,
    MediaStreamTrack,
)
import boto3
from PIL import Image


# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ivs-stage-subscribe-analyze-video")
logger.setLevel(logging.DEBUG)
aiortc_logger = logging.getLogger("aiortc")
aiortc_logger.setLevel(logging.ERROR)

# Suppress noisy STUN transaction timeout errors
aioice_logger = logging.getLogger("aioice")
aioice_logger.setLevel(logging.CRITICAL)
stun_logger = logging.getLogger("aioice.stun")
stun_logger.setLevel(logging.CRITICAL)


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
          
        if not self.recorded_audio_frames:
            logger.error("No audio frames were recorded")
            return None
            
        logger.info(f"✅ Recorded {len(self.recorded_video_frames)} video frames")
        logger.info(f"✅ Recorded {len(self.recorded_audio_frames)} audio frames")
        return self.recorded_video_frames.copy(), self.recorded_audio_frames.copy()
        
    async def process_video_frame(self, frame):
        """Process a video frame"""
        if self.recording:
            self.recorded_video_frames.append(frame)
            
    async def process_audio_frame(self, frame):
        """Process an audio frame"""
        if self.recording:
            self.recorded_audio_frames.append(frame)
            
    def encode_video_to_mp4(self, video_frames, audio_frames):
        """
        Encode audio and video frames to MP4 format in memory using pure Python (no ffmpeg)
        
        Args:
            video_frames: List of video frames
            audio_frames: List of audio frames
            
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
            output = av.open(output_buffer, mode='w', format='mp4')
            
            # Add video stream with explicit framerate
            video_stream = output.add_stream("h264", rate=30)
            video_stream.width = target_width
            video_stream.height = target_height
            video_stream.pix_fmt = "yuv420p"
            video_stream.options = {
                "preset": "ultrafast",
                "profile": "baseline"
            }
            
            audio_stream = output.add_stream("opus")
            audio_stream.format = "s16"
            
            new_video_pts = 0
            new_audio_pts = 0
            
            for i, video_frame in enumerate(video_frames):
                try:
                    # Convert frame to PIL Image for resizing
                    frame_array = video_frame.to_ndarray(format="rgb24")
                    img = Image.fromarray(frame_array)
                    img = img.resize((target_width, target_height), Image.LANCZOS)
                    resized_array = np.array(img)
                    # Create new frame from numpy array
                    resized_video_frame = av.VideoFrame.from_ndarray(resized_array, format='rgb24')
                    video_frame.pts = new_video_pts
                    video_frame.time_base = Fraction(1, 30)  # Match the stream's rate
                    video_packets = video_stream.encode(resized_video_frame)
                    new_video_pts += 1
                    for packet in video_packets:
                        output.mux(packet)
                except Exception as frame_error:
                    logger.warning(f"Skipping video frame {i} due to error: {frame_error}")
                    continue
                  
            for i, audio_frame in enumerate(audio_frames):
              try:
                  audio_frame.pts = new_audio_pts
                  audio_frame.time_base = Fraction(1, 48000)
                  audio_packets = audio_stream.encode(audio_frame)
                  new_audio_pts += 960
                  for packet in audio_packets:
                      output.mux(packet)
              except Exception as frame_error:
                  logger.warning(f"Skipping audio frame {i} due to error: {frame_error}")
                  continue
            
            # Flush the encoder
            for packet in video_stream.encode(None):
                output.mux(packet)
                
            for packet in audio_stream.encode(None):
                output.mux(packet)
                
            # Close the container
            output.close()
            
            # Get the encoded data
            encoded_data = output_buffer.getvalue()
            
            # tmp_file = f"/tmp/{time.time()}.mp4"
            # with open(tmp_file, "wb") as file:
            #   file.write(encoded_data)
            #   logger.info(f"Write tmp file: {tmp_file}")
                
            # Convert to base64
            base64_data = base64.b64encode(encoded_data).decode('utf-8')
            
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

    async def analyze_video(self, video_base64: str, participant_id: str) -> Optional[str]:
        """
        Analyze a video using TwelveLabs Pegasus

        Args:
            video_base64: Base64 encoded video data
            participant_id: ID of the participant being analyzed

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
                "mediaSource": {
                    "base64String": video_base64
                },
                "temperature": 0.2
            }

            # Call Bedrock
            logger.info(f"🔍 Analyzing video for participant {participant_id}...")

            response = self.bedrock_client.invoke_model(
                modelId=self.model_id,
                body=json.dumps(request_body),
                contentType="application/json",
                accept="application/json"
            )

            # Parse response
            response_body = json.loads(response["body"].read())
            analysis_result = response_body.get("message", "No analysis result")
            finish_reason = response_body.get("finishReason", "unknown")

            logger.info(f"✅ Video analysis completed for participant {participant_id} (finish reason: {finish_reason})")
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


def parse_jwt(token: str) -> Dict[str, Any]:
    """Parse JWT token without verification to extract payload"""
    try:
        parts: List[str] = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT format")

        payload: str = parts[1]
        # Add padding if needed
        payload += "=" * (4 - len(payload) % 4)
        decoded_bytes: bytes = base64.urlsafe_b64decode(payload)
        payload_json: Dict[str, Any] = json.loads(decoded_bytes.decode("utf-8"))

        return payload_json
    except Exception as e:
        logger.error(f"Error parsing JWT: {e}")
        return {}


def validate_token_capability(token_payload: Dict[str, Any], capability: str) -> bool:
    """
    Validate that the token has the specified capability

    Args:
        token_payload: Parsed JWT token payload
        capability: Capability to validate ("publish" or "subscribe")

    Returns:
        True if token has the capability, False otherwise
    """
    capabilities = token_payload.get("capabilities", {})
    capability_key = f"allow_{capability}"
    has_capability = capabilities.get(capability_key, False)

    if not has_capability:
        logger.error(f"Token does not have {capability} capabilities (capabilities.{capability_key} != true)")
        return False

    logger.info(f"✅ Token has {capability} capabilities")
    return True


def fix_ivs_answer_sdp(sdp: str) -> str:
    """Fix IVS's SDP answer to ensure ICE candidates are in both audio and video sections"""

    lines = sdp.split("\n")
    ice_candidates = []

    # First pass: collect all ICE candidates from audio section
    in_audio_section = False
    for line in lines:
        if line.startswith("m=audio"):
            in_audio_section = True
        elif line.startswith("m=video") or line.startswith("m=application"):
            in_audio_section = False

        # Collect ICE candidates from audio section
        if in_audio_section and line.startswith("a=candidate:"):
            ice_candidates.append(line)

    # Second pass: add candidates to video section
    fixed_lines = []
    in_video_section = False
    candidates_added = False

    for i, line in enumerate(lines):
        if line.startswith("m=video"):
            in_video_section = True
            candidates_added = False
        elif line.startswith("m=") and not line.startswith("m=video"):
            in_video_section = False

        # Check if we're at the end of video section
        if in_video_section and not candidates_added:
            # Look ahead to see if this is the last line of video section
            is_last_line = i == len(lines) - 1
            next_is_new_section = i < len(lines) - 1 and lines[i + 1].startswith("m=")
            is_empty_line = i < len(lines) - 1 and lines[i + 1].strip() == ""

            # If this is the last line of video section, add candidates after it
            if is_last_line or next_is_new_section or is_empty_line:
                fixed_lines.append(line)
                # Add all the ICE candidates from audio section
                for candidate in ice_candidates:
                    fixed_lines.append(candidate)
                # Add end-of-candidates
                fixed_lines.append("a=end-of-candidates")
                candidates_added = True
                continue

        fixed_lines.append(line)

    result = "\n".join(fixed_lines)

    return result


async def get_remote_sdp(url: str, token: str, sdp_offer: str, max_redirects: int = 5) -> Optional[str]:
    """
    Send a WebRTC offer to a WHIP/WHEP endpoint and return the SDP answer.
    Handles redirects while preserving Authorization headers.

    Args:
        url: The WHIP or WHEP endpoint URL
        token: JWT token for authorization
        sdp_offer: SDP offer string
        max_redirects: Maximum number of redirects to follow

    Returns:
        SDP answer string if successful, None if failed
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"}
    current_url = url
    attempt = 1

    logger.info(f"Sending WebRTC offer to endpoint: {current_url}")

    while attempt <= max_redirects:
        logger.info(f"Sending request to: {current_url} (attempt {attempt})")

        try:
            response = requests.post(current_url, data=sdp_offer, headers=headers, allow_redirects=False)

            if response.status_code in [301, 302, 303, 307, 308]:
                # Handle redirect manually to preserve Authorization header
                redirect_url = response.headers.get("Location")
                if redirect_url:
                    logger.info(f"Redirect {attempt}: {current_url} -> {redirect_url}")
                    current_url = redirect_url
                    attempt += 1
                    continue
                else:
                    logger.error("Redirect response missing Location header")
                    return None
            elif response.status_code == 201:
                logger.info(f"✅ WebRTC offer accepted, received SDP answer")
                return response.text
            else:
                logger.error(f"WebRTC request failed with status {response.status_code}: {response.text}")
                return None

        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            return None

    logger.error(f"Too many redirects (>{max_redirects})")
    return None


async def subscribe_to_participant(token: str, participant_id: str, analyzer: VideoAnalyzer = None):
    """Subscribe to a participant's audio/video streams and analyze video"""
    logger.info(f"🎧 Subscribing to participant: {participant_id}")

    # Create peer connection
    config = RTCConfiguration()
    config.bundlePolicy = RTCBundlePolicy.MAX_BUNDLE
    pc = RTCPeerConnection(config)

    # Add transceivers for receiving audio and video
    pc.addTransceiver("audio", direction="recvonly")
    pc.addTransceiver("video", direction="recvonly")
    
    video_track = None
    audio_track = None
    
    # Create video chunk recorder
    recorder = AudioVideoChunkRecorder(analyzer.analysis_duration)
    
    @pc.on("connectionstatechange")
    def on_connectionstatechange():
        logger.info(f"🔗 Connection state changed to: {pc.connectionState}")

    @pc.on("iceconnectionstatechange")
    def on_iceconnectionstatechange():
        logger.info(f"🧊 ICE connection state changed to: {pc.iceConnectionState}")

    @pc.on("icegatheringstatechange")
    def on_icegatheringstatechange():
        logger.info(f"🧊 ICE gathering state changed to: {pc.iceGatheringState}")

    @pc.on("signalingstatechange")
    def on_signalingstatechange():
        logger.info(f"📡 Signaling state changed to: {pc.signalingState}")

    @pc.on("track")
    def on_track(track: MediaStreamTrack):
        nonlocal video_track, audio_track
        logger.info(f"📺 Received {track.kind} track from participant {participant_id}")
        logger.info(f"Track ID: {track.id}")
        logger.info(f"Track readyState: {track.readyState}")

        if track.kind == "audio":
            logger.info("🔊 Audio track received")
            logger.info("Creating audio processing task...")
            task = asyncio.create_task(process_audio_track(track, recorder))
            logger.info(f"Audio processing task created: {task}")
            audio_track = track
        elif track.kind == "video":
            logger.info("🎥 Video track received")
            video_track = track
            
        if(video_track is not None and audio_track is not None):
            asyncio.create_task(process_video_track(video_track, recorder, analyzer, participant_id))
        
    async def process_audio_track(track: MediaStreamTrack, recorder: AudioVideoChunkRecorder):
        """Process audio track in a separate async task"""
        logger.info("🎵 Starting audio processing task")
        try:
            frame_count = 0
            while True:
                frame = await track.recv()
                await recorder.process_audio_frame(frame)
                frame_count += 1

        except Exception as e:
            logger.error(f"Audio track processing error for participant {participant_id}: {e}")
            import traceback
            traceback.print_exc()
            

    async def process_video_track(video_track: MediaStreamTrack, recorder: AudioVideoChunkRecorder, analyzer: VideoAnalyzer = None, participant_id: str = "unknown"):
        """Process video track in a separate async task with in-memory video recording and analysis"""
        logger.info("🧐 Starting audio and video processing task")
        if analyzer:
            logger.info(f"🤖 Video analysis enabled (duration: {analyzer.analysis_duration}s)")

        try:
            # Main processing loop
            frame_count = 0
            while True:
                video_frame = await video_track.recv()
                frame_count += 1
                
                # Process the frame
                await recorder.process_video_frame(video_frame)
                
                # Start recording if analyzer is provided and enough time has passed since last analysis
                if analyzer and analyzer.should_analyze_video() and not recorder.recording:
                    await recorder.start_recording()
                
                # Check if recording duration has been reached
                if recorder.recording and (time.time() - recorder.start_time) >= analyzer.analysis_duration:
                    # Stop recording and get frames
                    video_frames, audio_frames = await recorder.stop_recording()
                    
                    if video_frames:
                        # Encode video to MP4 in memory
                        video_base64 = recorder.encode_video_to_mp4(video_frames, audio_frames)
                        
                        if video_base64:
                            # Analyze the video
                            asyncio.create_task(analyzer.analyze_video(video_base64, participant_id))
                        else:
                            logger.error("Failed to encode video to MP4")
                    else:
                        logger.error("No frames recorded")

        except Exception as e:
            logger.error(f"Video track processing error: {e}")
            import traceback
            traceback.print_exc()

    # Create offer
    await pc.setLocalDescription(await pc.createOffer())

    # Parse token to get WHIP URL
    token_payload = parse_jwt(token)
    if "whip_url" not in token_payload:
        logger.error("No whip_url found in token payload")
        return None

    whip_base_url = token_payload["whip_url"]
    whep_url = f"{whip_base_url}/subscribe/{participant_id}"
    logger.info(f"🔗 WHEP URL: {whep_url}")

    # Send offer and get answer using consolidated function
    answer_sdp = await get_remote_sdp(whep_url, token, pc.localDescription.sdp)

    if not answer_sdp:
        logger.error("❌ Failed to get SDP answer from WHEP endpoint")
        return None

    # Set remote description from answer
    logger.info("🔧 Setting remote description from IVS answer...")
    fixed_answer_sdp = fix_ivs_answer_sdp(answer_sdp)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=fixed_answer_sdp, type="answer"))

    logger.info("✅ Successfully subscribed to participant")
    return pc


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="IVS Stage Subscriber with Video Analyzer")
    parser.add_argument("--token", required=True, help="IVS stage participant token")
    parser.add_argument("--subscribe-to", required=True, help="Participant ID to subscribe to")
    parser.add_argument("--analysis-duration", type=float, default=10.0, help="Duration in seconds for video recording before analysis (default: 10.0)")
    parser.add_argument("--aws-region", default="us-west-2", help="AWS region for Bedrock service (default: us-west-2)")
    parser.add_argument(
        "--model-id",
        default="us.twelvelabs.pegasus-1-2-v1:0",
        help="Bedrock model ID for video analysis (default: us.twelvelabs.pegasus-1-2-v1:0)",
    )
    parser.add_argument("--disable-analysis", action="store_true", help="Disable video analysis (just subscribe to video)")

    return parser.parse_args()


async def main():
    """Main function that handles subscribing to Amazon IVS participant"""
    args = parse_args()

    logger.info("🎬 IVS Stage Subscriber with Video Analyzer")
    logger.info(f"🔑 Using token: {args.token[:50]}... (truncated)")
    token_payload = parse_jwt(args.token)

    if not token_payload:
        logger.error("Failed to parse token payload")
        return

    # Validate capabilities
    if args.subscribe_to and not validate_token_capability(token_payload, "subscribe"):
        logger.error("❌ Token missing subscribe capabilities")
        return

    events_url = token_payload.get("events_url")
    topic = token_payload.get("topic")
    jti = token_payload.get("jti")

    logger.info(f"ℹ️  Stage Events URL: {events_url}")
    logger.info(f"ℹ️  Topic: {topic}")
    logger.info(f"ℹ️  JTI: {jti}")

    # Initialize video analyzer if not disabled
    analyzer = None
    if not args.disable_analysis:
        try:
            analyzer = VideoAnalyzer(analysis_duration=args.analysis_duration, region=args.aws_region, model_id=args.model_id)
            logger.info(f"🤖 Video analysis enabled (recording duration: {args.analysis_duration}s)")
        except Exception as e:
            logger.error(f"❌ Failed to initialize VideoAnalyzer: {e}")
            logger.error("Continuing without video analysis...")
    else:
        logger.info("🚫 Video analysis disabled")

    try:
        connections = []

        # Start subscribing to participants if specified
        if args.subscribe_to:
            participant_id = args.subscribe_to
            logger.info(f"👤 Startingn subscribe mode for participant: {participant_id}")

            subscribe_pc = await subscribe_to_participant(args.token, participant_id, analyzer)

            if subscribe_pc:
                logger.info(f"✅ Successfully subscribed to {participant_id}")
                connections.append(subscribe_pc)
            else:
                logger.error(f"❌ Failed to subscribe to {participant_id}")

        if not connections:
            logger.error("❌ No connections established")
            return

        if subscribe_pc:
            logger.info("🎉 WebRTC subscription established!")
            if analyzer:
                logger.info(f"🔍 Video analysis active - recording {args.analysis_duration} second videos")
            else:
                logger.info("📺 Video streaming without analysis")
        else:
            logger.error("❌ Failed to establish WebRTC subscription")
            return

        # Keep all connections alive
        try:
            logger.info(f"🔄 {len(connections)} connection(s) active. Press Ctrl+C to exit.")
            while True:
                await asyncio.sleep(1)  # Keep the event loop running
        except KeyboardInterrupt:
            logger.info("🛑 Shutting down...")
        finally:
            # Clean up all peer connections
            logger.info("🔌 Closing all connections...")
            for pc in connections:
                await pc.close()
            logger.info("✅ All connections closed")

    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
    except Exception as e:
        logger.error(f"❌ Error in main: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())