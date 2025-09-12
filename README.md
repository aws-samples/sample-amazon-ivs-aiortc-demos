# Amazon IVS Python Demo Scripts

A comprehensive collection of Python demo scripts demonstrating various Amazon IVS (Interactive Video Service) capabilities across both **Real-Time Stages** and **Channels** (low-latency HLS). This project showcases **publishing**, **subscribing**, **transcription**, **AI video analysis**, **AI-powered speech-to-speech**, and **timed metadata publishing** functionality.

**This project is intended for education purposes only and not for production usage.**

## Table of Contents

-   [Overview](#overview)
-   [Project Structure](#project-structure)
-   [Prerequisites](#prerequisites)
-   [Installation](#installation)
-   [Configuration](#configuration)
-   [Sub-Projects](#sub-projects)
    -   [Channels Subscribe](#channels-subscribe)
    -   [Stages Publish](#stages-publish)
    -   [Stages Subscribe](#stages-subscribe)
    -   [Stages Nova Speech-to-Speech](#stages-nova-speech-to-speech)
    -   [Stages SEI Publishing](#stages-sei-publishing)
-   [Usage Examples](#usage-examples)
-   [Troubleshooting](#troubleshooting)
-   [Dependencies](#dependencies)
-   [Contributing](#contributing)
-   [License](#license)
-   [Support](#support)

## Overview

This project demonstrates how to integrate Amazon IVS services with various AI and media processing capabilities:

### IVS Real-Time Stages (WebRTC)

-   **WebRTC Publishing**: Stream video/audio content to IVS stages
-   **WebRTC Subscribing**: Receive and process streams from IVS stages
-   **AI Speech-to-Speech**: Integrate Amazon Nova Sonic for conversational AI
-   **SEI Publishing**: Embed metadata directly into H.264 video streams using SEI NAL units
-   **Event Handling**: Process real-time stage events via WebSocket connections
-   **Audio Visualization**: Generate dynamic audio visualizations

### IVS Channels (Low-Latency HLS)

-   **Channel Subscription**: Subscribe to and analyze IVS channel streams
-   **Frame Analysis**: AI-powered video frame analysis using Amazon Bedrock Claude
-   **Video Analysis**: Comprehensive video segment analysis using TwelveLabs Pegasus
-   **Real-time Transcription**: Convert speech to text using OpenAI Whisper
-   **Timed Metadata Publishing**: Publish analysis results back to IVS as timed metadata
-   **Rendition Selection**: Automatic or manual selection of stream quality

> [!IMPORTANT]
> Using these demos with your AWS account will create and consume AWS resources, which will cost money.

## Project Structure

```
amazon-ivs-python-demos/
├── README.md                                           # This file
├── requirements.txt                                    # Python dependencies
├── channels-subscribe/                                 # IVS Channel analysis tools
│   ├── README.md                                       # Channel tools documentation
│   ├── ivs-channel-subscribe-analyze-frames.py        # Frame analysis with Claude
│   ├── ivs-channel-subscribe-analyze-video.py         # Video analysis with Pegasus
│   ├── ivs-channel-subscribe-analyze-audio-video.py   # Combined audio/video analysis
│   ├── ivs-channel-subscribe-transcribe.py            # Real-time transcription
│   └── ivs_metadata_publisher.py                      # Timed metadata publisher
├── stages-publish/                                     # Real-Time Stages publishing
│   ├── ivs-stage-publish.py                           # Basic media publishing
│   ├── ivs-stage-publish-events.py                    # Publishing with event handling
│   └── ivs-stage-pub-sub.py                           # Simultaneous publish/subscribe
├── stages-subscribe/                                   # Real-Time Stages subscribing
│   ├── ivs-stage-subscribe-transcribe.py              # Subscribe with transcription
│   ├── ivs-stage-subscribe-analyze-frames.py          # Subscribe with AI frame analysis
│   └── ivs-stage-subscribe-analyze-video.py           # Subscribe with AI video analysis
├── stages-nova-s2s/                                    # AI Speech-to-Speech
│   └── ivs-stage-nova-s2s.py                          # Nova Sonic integration
└── stages_sei/                                         # SEI Publishing System
    ├── SEI.md                                          # SEI documentation and usage guide
    ├── sei_publisher.py                                # High-level SEI message publishing
    └── h264_sei_patch.py                               # Low-level H.264 encoder patching
```

## Prerequisites

-   Python 3.8 or higher
-   AWS CLI configured with appropriate credentials
-   Amazon IVS Real-Time Stage ARN and participant tokens
-   FFmpeg (for media processing when using transcription demo - not necessary otherwise)
-   Audio input/output devices (for speech-to-speech functionality)

### AWS Permissions Required

Your AWS credentials need the following permissions:

**For IVS Real-Time Stages:**

-   `ivs:CreateParticipantToken`
-   `bedrock:InvokeModel` (for video frame analysis with Claude)
-   `bedrock:InvokeModelWithBidirectionalStream` (for Nova Sonic)
-   Access to Amazon IVS Real-Time Stages

**For IVS Channels:**

-   `ivs:PutMetadata` (for publishing timed metadata)
-   `bedrock:InvokeModel` (for Claude frame analysis and TwelveLabs Pegasus video analysis)
-   Access to Amazon IVS Channels

## Installation

1. **Clone and navigate to the project directory:**

    ```bash
    cd /amazon-ivs-aiortc-demos
    ```

2. **Create and activate a virtual environment:**

    ```bash
    python3 -m venv .venv
    source .venv/bin/activate  # On macOS/Linux
    # or
    .venv\Scripts\activate     # On Windows
    ```

3. **Install dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

4. **Install system dependencies:**

    **macOS:**

    ```bash
    brew install ffmpeg portaudio
    ```

    **Ubuntu/Debian:**

    ```bash
    sudo apt-get update
    sudo apt-get install ffmpeg portaudio19-dev
    ```

    **Windows:**

    ```bash
    # Install FFmpeg
    # Download from https://ffmpeg.org/download.html and add to PATH
    # Or use chocolatey:
    choco install ffmpeg

    # PortAudio is typically installed automatically with pyaudio
    # If you encounter issues, you may need to install Microsoft Visual C++ Build Tools
    ```

## Configuration

### Environment Variables

Set the following environment variables or ensure AWS CLI is configured:

```bash
export AWS_REGION=us-east-1
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key

# Optional: For weather functionality in Nova speech-to-speech
export WEATHER_API_KEY=your_weather_api_key
```

### Weather API (Optional)

The Nova speech-to-speech script supports weather queries through WeatherAPI.com:

1. Sign up at [WeatherAPI.com](https://www.weatherapi.com/) for a free account
2. Get your API key from the dashboard
3. Set the `WEATHER_API_KEY` environment variable
4. The AI assistant will then be able to answer weather-related questions

## Sub-Projects

### Channels Subscribe

The `channels-subscribe/` directory contains scripts for subscribing to and analyzing Amazon IVS Channels (low-latency HLS streams).

#### Key Features

-   **Frame Analysis**: Analyze individual video frames using Amazon Bedrock Claude models
-   **Video Analysis**: Process video segments using TwelveLabs Pegasus for comprehensive content analysis
-   **Audio/Video Analysis**: Combined audio and video processing with proper synchronization using PyAV
-   **Real-Time Transcription**: Live speech-to-text using OpenAI Whisper with multi-language support
-   **Timed Metadata Publishing**: Publish analysis results back to IVS channels as timed metadata
-   **Rendition Selection**: Automatic or manual selection of stream quality

#### Scripts Overview

**ivs-channel-subscribe-analyze-frames.py**

-   Analyzes individual video frames at configurable intervals using Amazon Bedrock Claude
-   Supports multiple Claude models (Sonnet 4, Claude 3.5 Sonnet, Claude 3.5 Haiku)
-   Configurable analysis intervals for cost control
-   Optional video display and rendition quality selection

**ivs-channel-subscribe-analyze-video.py**

-   Records and analyzes video segments using TwelveLabs Pegasus
-   Encodes video chunks to MP4 for comprehensive analysis
-   OpenCV-based video capture with configurable recording duration

**ivs-channel-subscribe-analyze-audio-video.py**

-   Advanced script using PyAV for proper audio/video stream handling
-   Native audio capture and encoding with H.264 video and AAC audio
-   Complete media analysis with TwelveLabs Pegasus

**ivs-channel-subscribe-transcribe.py**

-   Real-time audio transcription using OpenAI Whisper
-   Support for 99+ languages with auto-detection
-   Multiple Whisper models from tiny to large-v3
-   Optional publishing of transcripts as IVS timed metadata

**ivs_metadata_publisher.py**

-   Reusable module for publishing timed metadata to IVS channels
-   Automatic channel ARN extraction from M3U8 playlist URLs
-   Rate limiting compliance and automatic payload splitting
-   Support for transcripts, events, and custom metadata

#### Usage Examples

```bash
# Frame analysis with Claude Sonnet 4
python channels-subscribe/ivs-channel-subscribe-analyze-frames.py \
  --playlist-url "https://example.com/playlist.m3u8" \
  --highest-quality

# Real-time transcription with metadata publishing
python channels-subscribe/ivs-channel-subscribe-transcribe.py \
  --playlist-url "https://example.com/playlist.m3u8" \
  --language en \
  --whisper-model base \
  --publish-transcript-as-timed-metadata

# Video analysis with TwelveLabs Pegasus
python channels-subscribe/ivs-channel-subscribe-analyze-video.py \
  --playlist-url "https://example.com/playlist.m3u8" \
  --analysis-duration 15 \
  --show-video
```

For detailed documentation, see [`channels-subscribe/README.md`](channels-subscribe/README.md).

### Stages Publish

The `stages-publish/` directory contains scripts for publishing media content to IVS Real-Time Stages.

#### ivs-stage-publish.py

Basic media publishing script that streams video/audio content to an IVS stage.

**Features:**

-   Publishes video and audio tracks from MP4 files to IVS Real-Time Stages
-   JWT token validation and capability checking
-   WebRTC connection management
-   Option to publish video-only streams

**Usage:**

```bash
cd stages-publish
python ivs-stage-publish.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --path-to-mp4 "path/to/video.mp4"
```

**Command-line Arguments:**

-   `--token`: JWT participant token with publish capabilities (required)
-   `--path-to-mp4`: Path to MP4 file to publish (required)
-   `--video-only`: Publish video only, no audio (optional flag)

#### ivs-stage-publish-events.py

Enhanced publishing script with real-time event handling via WebSocket connections.

**Features:**

-   All features of basic publisher
-   Real-time stage event monitoring via WebSocket
-   Participant join/leave notifications
-   Stage state change handling

**Usage:**

```bash
cd stages-publish
python ivs-stage-publish-events.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --path-to-mp4 "path/to/video.mp4"
```

**Command-line Arguments:**

-   `--token`: JWT participant token with publish capabilities (required)
-   `--path-to-mp4`: Path to MP4 file to publish (required)
-   `--video-only`: Publish video only, no audio (optional flag)

#### ivs-stage-pub-sub.py

Advanced script that demonstrates simultaneous publishing and subscribing capabilities.

**Features:**

-   Publishes audio from MP4 file while subscribing to other participants
-   Demonstrates bidirectional communication
-   Audio/video track management
-   SDP (Session Description Protocol) handling

**Usage:**

```bash
cd stages-publish
python ivs-stage-pub-sub.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --path-to-mp4 "path/to/audio.mp4"
```

**Command-line Arguments:**

-   `--token`: JWT participant token with both publish and subscribe capabilities (required)
-   `--path-to-mp4`: Path to MP4 file to publish audio from (required)
-   `--video-only`: Publish video only, no audio (optional flag)
-   `--subscribe-to`: List of participant IDs to subscribe to (optional)

### Stages Subscribe

The `stages-subscribe/` directory contains scripts for receiving and processing streams from IVS Real-Time Stages.

#### ivs-stage-subscribe-transcribe.py

Subscribes to IVS stage audio streams and provides real-time speech-to-text transcription using OpenAI Whisper.

**Features:**

-   Subscribes to audio tracks from specific participants in IVS Real-Time Stages
-   Real-time speech transcription using Whisper
-   Audio chunk processing and buffering
-   Multiple language support
-   Audio format conversion and normalization

**Usage:**

```bash
cd stages-subscribe
python ivs-stage-subscribe-transcribe.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..."
```

**Command-line Arguments:**

-   `--participant-id`: ID of the participant to subscribe to (required)
-   `--token`: JWT participant token with subscribe capabilities (required)
-   `--whisper-model`: Whisper model size - "tiny", "base", "small", "medium", "large" (default: "tiny")
-   `--fp16`: Enable FP16 precision for faster processing (default: true)
-   `--language`: Language code for transcription (default: "en")
-   `--chunk-duration`: Audio chunk duration in seconds (default: 5)

**Supported Languages:**

-   English ("en")
-   Spanish ("es")
-   French ("fr")
-   German ("de")
-   Italian ("it")
-   Portuguese ("pt")
-   And many more supported by Whisper

#### ivs-stage-subscribe-analyze-frames.py

Subscribes to IVS stage video streams and provides AI-powered video frame analysis using Amazon Bedrock Claude models for content discovery, moderation, and accessibility.

**Features:**

-   Subscribes to video tracks from specific participants in IVS Real-Time Stages
-   AI-powered video frame analysis using Claude Sonnet 4
-   Configurable analysis intervals to control costs
-   Support for multiple Claude models (Sonnet 4, Claude 3.5 Sonnet, Claude 3.5 Haiku)
-   Detailed frame descriptions for content moderation and accessibility
-   Background processing to avoid blocking video streams
-   Cost-conscious design with smart frame sampling

**Usage:**

```bash
cd stages-subscribe
python ivs-stage-subscribe-analyze-frames.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123"
```

**Command-line Arguments:**

-   `--token`: JWT participant token with subscribe capabilities (required)
-   `--subscribe-to`: Participant ID to subscribe to (required)
-   `--analysis-interval`: Time in seconds between frame analyses (default: 30.0)
-   `--bedrock-region`: AWS region for Bedrock service (default: "us-east-1")
-   `--bedrock-model-id`: Bedrock model ID for analysis (default: "us.anthropic.claude-sonnet-4-20250514-v1:0")
-   `--disable-analysis`: Disable video frame analysis, just subscribe to video (optional flag)

**Supported Models:**

-   **Claude Sonnet 4** (default): `us.anthropic.claude-sonnet-4-20250514-v1:0` - Most capable, best for complex analysis
-   **Claude 3.5 Sonnet**: `anthropic.claude-3-5-sonnet-20241022-v2:0` - Very capable, good balance of performance and cost
-   **Claude 3.5 Haiku**: `anthropic.claude-3-5-haiku-20241022-v1:0` - Fastest and cheapest, good for basic content moderation

**Use Cases:**

-   **Content Moderation**: Automatically detect inappropriate content in live streams
-   **Content Discovery**: Generate descriptions and tags for video content
-   **Accessibility**: Create detailed descriptions for visually impaired users
-   **Analytics**: Track objects, activities, and engagement in video streams
-   **Compliance**: Monitor streams for regulatory compliance

**Cost Control Features:**

-   Configurable analysis intervals (default 30 seconds to minimize costs)
-   Background processing doesn't block video streaming
-   Option to disable analysis entirely for testing
-   Smart error handling prevents failed analyses from crashing streams

#### ivs-stage-subscribe-analyze-video.py

Subscribes to IVS stage audio and video streams and provides AI-powered video analysis using Amazon Bedrock TwelveLabs Pegasus for comprehensive video understanding.

**Features:**

-   Subscribes to both audio and video tracks from specific participants
-   Records short video clips (configurable duration) for analysis
-   Encodes audio and video to MP4 format in memory
-   AI-powered video analysis using TwelveLabs Pegasus model
-   Detailed video content descriptions including people, objects, activities, and text
-   Asynchronous processing to maintain stream performance
-   Configurable analysis duration and frequency

**Usage:**

```bash
cd stages-subscribe
python ivs-stage-subscribe-analyze-video.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123"
```

**Command-line Arguments:**

-   `--token`: JWT participant token with subscribe capabilities (required)
-   `--subscribe-to`: Participant ID to subscribe to (required)
-   `--analysis-duration`: Duration in seconds for video recording before analysis (default: 10.0)
-   `--bedrock-region`: AWS region for Bedrock service (default: "us-west-2")
-   `--bedrock-model-id`: Bedrock model ID for analysis (default: "us.twelvelabs.pegasus-1-2-v1:0")
-   `--disable-analysis`: Disable video analysis, just subscribe to video (optional flag)

### Stages Nova Speech-to-Speech

The `stages-nova-s2s/` directory contains the most advanced script integrating Amazon Nova Sonic for AI-powered speech-to-speech functionality.

#### ivs-stage-nova-s2s.py

A comprehensive script that combines IVS Real-Time Stages with Amazon Nova Sonic for conversational AI experiences.

**Features:**

-   Bidirectional audio streaming with IVS participants
-   Amazon Nova Sonic integration for AI responses
-   Real-time waveform visualization
-   Audio resampling and format conversion
-   WebRTC track management for both publishing and subscribing
-   Dynamic audio visualization with gradient colormaps
-   AI-powered video frame analysis using Amazon Bedrock Claude models
-   Built-in tools for date/time, weather, and visual analysis
-   Configurable frame analysis with multiple Claude model options

**Usage:**

```bash
cd stages-nova-s2s
python ivs-stage-nova-s2s.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123"
```

**Command-line Arguments:**

-   `--token`: JWT participant token with both publish and subscribe capabilities (required)
-   `--subscribe-to`: Participant ID to subscribe to (required)
-   `--nova-model-id`: Amazon Nova model identifier (default: "amazon.nova-sonic-v1:0")
-   `--nova-region`: AWS region for Nova service (default: "us-east-1")
-   `--disable-frame-analysis`: Disable video frame analysis (default: enabled)
-   `--bedrock-model-id`: Bedrock model ID for frame analysis (default: "us.anthropic.claude-sonnet-4-20250514-v1:0")
-   `--bedrock-region`: AWS region for Bedrock service (default: "us-east-1")
-   `--ice-timeout`: ICE gathering timeout in seconds (default: 1, original: 5) - Lower values speed up connection establishment

**Key Components:**

1. **AgentAudioTrack**: Custom audio track for streaming Nova responses
2. **AgentVideoTrack**: Dynamic waveform visualization with thinking states
3. **BedrockStreamManager**: Manages bidirectional Nova Sonic streaming
4. **Audio Processing**: Handles resampling between IVS (48kHz) and Nova (16kHz)
5. **Tool Support**: Built-in tools for date/time, weather, and video frame analysis
6. **Frame Analysis**: Non-blocking AI-powered video frame analysis using Claude models

**Available Tools:**

-   **Date/Time Tool**: Get current date and time information with timezone support
-   **Weather Tool**: Get current weather and 5-day forecast (requires `WEATHER_API_KEY`)
-   **Frame Analysis Tool**: Analyze video frames for visual assistance and content description

#### Assistant Management

For automated management of multiple Nova assistant instances via WebSocket integration with IVS Chat, see:

**[IVS Stage Assistant Manager Documentation](stages-nova-s2s/MANAGING_ASSISTANT_DEMO.md)**

This companion tool allows you to dynamically launch and manage multiple Nova S2S instances based on chat messages, perfect for scaling AI assistants across multiple participants.

### Stages SEI Publishing

The `stages_sei/` directory contains a comprehensive SEI (Supplemental Enhancement Information) publishing system for embedding metadata directly into H.264 video streams.

**What is SEI?**

SEI NAL units are part of the H.264/AVC video compression standard that allow embedding additional metadata within the video stream itself. This metadata travels with the video frames, ensuring perfect synchronization between video content and associated data.

**Key Features:**

-   **Perfect Synchronization**: Metadata is embedded directly in video frames
-   **Low Latency**: No separate data channels needed
-   **Standards Compliant**: Uses official H.264 specification
-   **Multi-format Support**: Handles Annex B, AVCC, and RTP H.264 formats
-   **Automatic Integration**: Patches aiortc and PyAV encoders automatically
-   **Reliable Delivery**: 3x repetition with client-side deduplication

**Components:**

-   **`sei_publisher.py`**: High-level interface for publishing SEI messages
-   **`h264_sei_patch.py`**: Low-level H.264 encoder patching system
-   **`SEI.md`**: Comprehensive documentation and usage guide

**Usage Example:**

```python
from stages_sei import SeiPublisher, patch_h264_encoder, set_global_sei_publisher

# Apply H.264 encoder patch (do this early in your application)
patch_h264_encoder()

# Create and configure SEI publisher
sei_publisher = SeiPublisher()
set_global_sei_publisher(sei_publisher)

# Publish metadata
await sei_publisher.publish_json({
    "type": "chat_message",
    "user": "alice",
    "message": "Hello world!",
    "timestamp": time.time()
})
```

**Integration:**

The Nova speech-to-speech script (`stages-nova-s2s/ivs-stage-nova-s2s.py`) demonstrates SEI publishing in action, embedding AI assistant responses directly into the video stream for synchronized delivery.

**For detailed documentation, see [`stages_sei/SEI.md`](stages_sei/SEI.md).**

### Utility Scripts

_Note: Utility scripts are excluded from this documentation as they are development/testing tools._

## Usage Examples

### IVS Channel Examples

```bash
# Subscribe to IVS channel and analyze frames with Claude
python channels-subscribe/ivs-channel-subscribe-analyze-frames.py \
  --playlist-url "https://example.com/playlist.m3u8" \
  --highest-quality \
  --analysis-interval 30

# Real-time transcription of IVS channel audio
python channels-subscribe/ivs-channel-subscribe-transcribe.py \
  --playlist-url "https://example.com/playlist.m3u8" \
  --language en \
  --whisper-model base \
  --publish-transcript-as-timed-metadata

# Comprehensive video analysis with TwelveLabs Pegasus
python channels-subscribe/ivs-channel-subscribe-analyze-video.py \
  --playlist-url "https://example.com/playlist.m3u8" \
  --analysis-duration 10 \
  --bedrock-region us-west-2

# Combined audio/video analysis using PyAV
python channels-subscribe/ivs-channel-subscribe-analyze-audio-video.py \
  --playlist-url "https://example.com/playlist.m3u8" \
  --highest-quality \
  --analysis-duration 15
```

### IVS Real-Time Stages Examples

#### Basic Publishing Example

```bash
# Publish MP4 file to IVS stage
python stages-publish/ivs-stage-publish.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --path-to-mp4 "sample-video.mp4"
```

#### Publishing with Events Example

```bash
# Publish with real-time event monitoring
python stages-publish/ivs-stage-publish-events.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --path-to-mp4 "sample-video.mp4"
```

#### Transcription Example

```bash
# Subscribe and transcribe audio in Spanish
python stages-subscribe/ivs-stage-subscribe-transcribe.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --language "es" \
  --whisper-model "medium"
```

#### Video Frame Analysis Examples

```bash
# Basic video frame analysis (every 30 seconds with Claude Sonnet 4)
python stages-subscribe/ivs-stage-subscribe-analyze-frames.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123"

# Frequent analysis for real-time moderation (every 5 seconds)
python stages-subscribe/ivs-stage-subscribe-analyze-frames.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --analysis-interval 5.0

# Cost-effective analysis using Claude 3.5 Haiku (every 60 seconds)
python stages-subscribe/ivs-stage-subscribe-analyze-frames.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --bedrock-model-id "anthropic.claude-3-5-haiku-20241022-v1:0" \
  --analysis-interval 60.0

# Analysis in different AWS region
python stages-subscribe/ivs-stage-subscribe-analyze-frames.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --bedrock-region "eu-west-1"

# Subscribe to video without analysis (testing connectivity)
python stages-subscribe/ivs-stage-subscribe-analyze-frames.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --disable-analysis
```

#### Video Analysis Examples

```bash
# Basic video analysis with TwelveLabs Pegasus
python stages-subscribe/ivs-stage-subscribe-analyze-video.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123"

# Shorter video clips for more frequent analysis
python stages-subscribe/ivs-stage-subscribe-analyze-video.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --analysis-duration 5.0
```

#### AI Speech-to-Speech Example

```bash
# Start Nova Sonic conversation with frame analysis
python stages-nova-s2s/ivs-stage-nova-s2s.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --nova-model-id "amazon.nova-sonic-v1:0" \
  --nova-region "us-east-1"

# Nova conversation without frame analysis
python stages-nova-s2s/ivs-stage-nova-s2s.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --disable-frame-analysis

# Nova conversation with custom Bedrock model and region
python stages-nova-s2s/ivs-stage-nova-s2s.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --bedrock-model-id "anthropic.claude-3-5-sonnet-20241022-v2:0" \
  --bedrock-region "us-west-2"

# Nova conversation with fast connection setup
python stages-nova-s2s/ivs-stage-nova-s2s.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --ice-timeout 1
```

#### Publish and Subscribe Example

```bash
# Simultaneously publish and subscribe
python stages-publish/ivs-stage-pub-sub.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --path-to-mp4 "audio-file.mp4" \
  --subscribe-to "participant1" "participant2"
```

#### Creating Participant Tokens

Use the AWS CLI to create participant tokens:

```bash
# Create a token with publish capabilities
aws ivs-realtime create-participant-token \
  --stage-arn "arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh" \
  --user-id "user123" \
  --capabilities PUBLISH \
  --duration 720

# Create a token with subscribe capabilities
aws ivs-realtime create-participant-token \
  --stage-arn "arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh" \
  --user-id "user456" \
  --capabilities SUBSCRIBE \
  --duration 720

# Create a token with both publish and subscribe capabilities
aws ivs-realtime create-participant-token \
  --stage-arn "arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh" \
  --user-id "user789" \
  --capabilities PUBLISH SUBSCRIBE \
  --duration 720
```

## Troubleshooting

### Common Issues

#### IVS Channels Issues

1. **"No audio stream found"**

    - Check if the M3U8 stream contains audio using `ffprobe`
    - Try different rendition quality options
    - Verify stream accessibility with `curl`

2. **"Unable to open video stream"**

    - Verify M3U8 URL is accessible
    - Check network connectivity and firewall settings
    - Try different rendition selections

3. **Whisper Model Issues**

    - Clear Whisper cache: `rm -rf ~/.cache/whisper/`
    - Use smaller models for memory-constrained environments
    - Enable FP16 for faster processing

4. **Timed Metadata Publishing Issues**
    - Verify AWS credentials have `ivs:PutMetadata` permissions
    - Check rate limiting (5 RPS per channel, 155 RPS per account)
    - Ensure channel ARN extraction is working correctly

#### IVS Real-Time Stages Issues

1. **Audio Quality Problems**

    - Ensure consistent chunk sizes (512 samples recommended)
    - Check audio resampling configuration
    - Verify WebRTC connection stability

2. **WebRTC Connection Failures**

    - Verify JWT token has correct capabilities
    - Check network connectivity and firewall settings
    - Ensure SDP munging is applied correctly

3. **Nova Sonic Issues**
    - Verify AWS credentials have Bedrock permissions
    - Check model availability in your region
    - Ensure proper event sequence (START_SESSION → START_PROMPT → content)

#### General Issues

1. **Video Frame Analysis Issues**

    - Verify AWS credentials have `bedrock:InvokeModel` permissions
    - Check Claude/Pegasus model availability in your region
    - Monitor analysis costs with appropriate intervals
    - Ensure video track is receiving frames before analysis begins

2. **Transcription Accuracy**
    - Use appropriate Whisper model size for your use case
    - Ensure clean audio input
    - Consider language-specific models

### Debug Mode

Enable debug logging for detailed troubleshooting:

```bash
export PYTHONPATH=$PYTHONPATH:.
python -c "import logging; logging.basicConfig(level=logging.DEBUG)"
python your-script.py --your-args
```

### Performance Optimization

#### IVS Channels Optimization

1. **For Channel Transcription:**

    - Use `--whisper-model tiny` or `--whisper-model base` for real-time processing
    - Enable FP16: `--fp16 true`
    - Use shorter chunks: `--chunk-duration 3`
    - Specify language: `--language en` (faster than auto-detect)

2. **For Channel Video Analysis:**

    - Use `--lowest-quality` for faster processing
    - Adjust `--analysis-duration` based on content complexity
    - Run without `--show-video` for headless operation

3. **For Channel Frame Analysis:**
    - Increase `--analysis-interval` for less frequent analysis (cost control)
    - Use `--lowest-quality` for faster frame processing
    - Choose appropriate Claude model for your use case

#### IVS Real-Time Stages Optimization

1. **Connection Speed:**

    - Use `--ice-timeout 1` for faster WebRTC connection establishment (default)
    - Original WebRTC ICE timeout is 5 seconds, optimized to 1 second for better user experience
    - Increase timeout if experiencing connection issues in poor network conditions
    - This optimization reduces startup time from ~11 seconds to ~3 seconds

2. **For Nova Sonic:**

    - Use consistent 1ms delays between audio chunks
    - Implement proper buffering strategies
    - Monitor memory usage during long sessions

3. **For Stage Transcription:**
    - Choose appropriate chunk duration (5-10 seconds)
    - Use smaller Whisper models for real-time processing
    - Consider GPU acceleration for large models

#### General Optimization

1. **For Video Frame Analysis:**
    - Use longer analysis intervals (30+ seconds) to control costs
    - Choose appropriate Claude model for your use case:
        - Claude 3.5 Haiku for basic content moderation
        - Claude 3.5 Sonnet for balanced performance
        - Claude Sonnet 4 for complex analysis requiring highest accuracy
    - Monitor Bedrock usage and costs in AWS console
    - Consider regional model availability and latency

## Dependencies

### Core Dependencies

-   `aiortc>=1.12.0` - WebRTC implementation
-   `av>=10.0.0` - Media processing
-   `requests>=2.28.0` - HTTP client
-   `websockets>=11.0.0` - WebSocket client
-   `numpy>=1.21.0` - Numerical computing

### AI/ML Dependencies

-   `whisper` (from GitHub) - Speech recognition
-   `boto3>=1.34.0` - AWS SDK for Bedrock and IVS
-   `aws-sdk-bedrock-runtime` - Amazon Bedrock client
-   `smithy-aws-core>=0.0.1` - AWS SDK core
-   `pyaudio>=0.2.13` - Audio I/O
-   `rx>=3.2.0` - Reactive extensions
-   `Pillow>=10.0.0` - Image processing for video frame analysis
-   `opencv-python>=4.8.0` - Computer vision for video processing

### Utility Dependencies

-   `pytz` - Timezone handling
-   `tzlocal` - Local timezone detection

### System Requirements

-   Python 3.8+
-   FFmpeg
-   PortAudio (for audio I/O)
-   Sufficient bandwidth for WebRTC streams

## Contributing

See [CONTRIBUTING](CONTRIBUTING.md) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](./LICENSE) file.

## Support

For issues related to:

-   **Amazon IVS Real-Time Stages**: Check the [IVS Real-Time Streaming documentation](https://docs.aws.amazon.com/ivs/latest/RealTimeUserGuide/)
-   **Amazon IVS Channels**: Check the [IVS Low-Latency Streaming documentation](https://docs.aws.amazon.com/ivs/latest/LowLatencyUserGuide/)
-   **Amazon Nova**: Check the [Bedrock documentation](https://docs.aws.amazon.com/bedrock/)
-   **Amazon Bedrock**: Check the [Bedrock User Guide](https://docs.aws.amazon.com/bedrock/latest/userguide/)
-   **aiortc**: Check the [aiortc documentation](https://aiortc.readthedocs.io/)
-   **OpenAI Whisper**: Check the [Whisper repository](https://github.com/openai/whisper)

---

_This project demonstrates advanced integration patterns between Amazon IVS services and AI capabilities. From real-time conversational AI with Nova Sonic to comprehensive video analysis with Claude and TwelveLabs Pegasus, these demos showcase the power of combining live video streaming with cutting-edge AI services._
