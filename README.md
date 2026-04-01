# Amazon IVS Python Demo Scripts

A comprehensive collection of Python demo scripts demonstrating various Amazon IVS (Interactive Video Service) capabilities across both **Real-Time Stages** and **Channels** (low-latency HLS). This project showcases **publishing**, **subscribing**, **transcription**, **AI video analysis**, **AI-powered speech-to-speech**, and **timed metadata publishing** functionality.

**This project is intended for education purposes only and not for production usage.**

## Table of Contents

- [Overview](#overview)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Sub-Projects](#sub-projects)
  - [Channels Subscribe](#channels-subscribe)
  - [Stages Publish](#stages-publish)
  - [Stages Subscribe](#stages-subscribe)
  - [Stages Nova Speech-to-Speech](#stages-nova-speech-to-speech)
  - [Stages OpenAI Real-time API](#stages-openai-real-time-api)
  - [Stages Deepgram Voice Agent](#stages-deepgram-voice-agent)
  - [Stages SEI Publishing](#stages-sei-publishing)
- [Usage Examples](#usage-examples)
- [Troubleshooting](#troubleshooting)
- [Dependencies](#dependencies)
- [Contributing](#contributing)
- [License](#license)
- [Support](#support)

## Overview

This project demonstrates how to integrate Amazon IVS services with various AI and media processing capabilities:

### IVS Real-Time Stages (WebRTC)

- **WebRTC Publishing**: Stream video/audio content to IVS stages
- **WebRTC Subscribing**: Receive and process streams from IVS stages
- **Real-time Transcription**: Live speech-to-text using OpenAI Whisper or Deepgram
- **AI Speech-to-Speech**: Integrate Amazon Nova Sonic for conversational AI
- **SEI Publishing**: Embed metadata directly into H.264 video streams using SEI NAL units
- **Event Handling**: Process real-time stage events via WebSocket connections
- **Audio Visualization**: Generate dynamic audio visualizations

### IVS Channels (Low-Latency HLS)

- **Channel Subscription**: Subscribe to and analyze IVS channel streams
- **Frame Analysis**: AI-powered video frame analysis using Amazon Bedrock Claude
- **Video Analysis**: Comprehensive video segment analysis using TwelveLabs Pegasus
- **Real-time Transcription**: Convert speech to text using OpenAI Whisper
- **Timed Metadata Publishing**: Publish analysis results back to IVS as timed metadata
- **Rendition Selection**: Automatic or manual selection of stream quality

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
│   ├── ivs-channel-subscribe-transcribe.py            # Real-time transcription (Whisper)
│   ├── ivs-channel-subscribe-transcribe-deepgram.py   # Real-time transcription (Deepgram)
│   └── ivs_metadata_publisher.py                      # Timed metadata publisher
├── stages-publish/                                     # Real-Time Stages publishing
│   ├── ivs-stage-publish.py                           # Basic media publishing
│   ├── ivs-stage-publish-events.py                    # Publishing with event handling
│   └── ivs-stage-pub-sub.py                           # Simultaneous publish/subscribe
├── stages-subscribe/                                   # Real-Time Stages subscribing
│   ├── ivs-stage-subscribe-transcribe.py              # Subscribe with transcription (Whisper)
│   ├── ivs-stage-subscribe-transcribe-deepgram.py     # Subscribe with transcription (Deepgram)
│   ├── ivs-stage-subscribe-analyze-frames.py          # Subscribe with AI frame analysis
│   └── ivs-stage-subscribe-analyze-video.py           # Subscribe with AI video analysis
├── stages-nova-s2s/                                    # AI Speech-to-Speech
│   └── ivs-stage-nova-s2s.py                          # Nova Sonic integration
├── stages-gpt-realtime/                                    # GPT RealTime API
│   └── ivs-stage-gpt-realtime.py                       # gpt-realtime integration
├── stages-deepgram-agent/                                  # Deepgram Voice Agent
│   ├── ivs-stage-deepgram-agent.py                     # Deepgram Agent integration
│   ├── ivs-stage-deepgram-agent-manager.py             # Multi-instance manager via IVS Chat
│   └── deepgram_agent_manager.py                       # Deepgram Agent WebSocket manager
└── stages_sei/                                         # SEI Publishing System
    ├── SEI.md                                          # SEI documentation and usage guide
    ├── sei_publisher.py                                # High-level SEI message publishing
    └── h264_sei_patch.py                               # Low-level H.264 encoder patching
```

## Prerequisites

- Python 3.8 or higher
- AWS CLI configured with appropriate credentials
- Amazon IVS Real-Time Stage ARN and participant tokens
- FFmpeg (for media processing when using transcription demo - not necessary otherwise)
- Deepgram API key (for Deepgram transcription demo - sign up at [deepgram.com](https://deepgram.com/))
- Audio input/output devices (for speech-to-speech functionality)

### AWS Permissions Required

Your AWS credentials need the following permissions:

**For IVS Real-Time Stages:**

- `ivs:CreateParticipantToken`
- `bedrock:InvokeModel` (for video frame analysis with Claude)
- `bedrock:InvokeModelWithBidirectionalStream` (for Nova Sonic)
- Access to Amazon IVS Real-Time Stages

**For IVS Channels:**

- `ivs:PutMetadata` (for publishing timed metadata)
- `bedrock:InvokeModel` (for Claude frame analysis and TwelveLabs Pegasus video analysis)
- Access to Amazon IVS Channels

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

# Optional: For web search functionality in Nova speech-to-speech
export BRAVE_API_KEY=your_brave_api_key

# Optional: For Deepgram real-time transcription
export DEEPGRAM_API_KEY=your_deepgram_api_key
```

### Weather API (Optional)

The Nova speech-to-speech script supports weather queries through WeatherAPI.com:

1. Sign up at [WeatherAPI.com](https://www.weatherapi.com/) for a free account
2. Get your API key from the dashboard
3. Set the `WEATHER_API_KEY` environment variable
4. The AI assistant will then be able to answer weather-related questions

### Web Search API (Optional)

The Nova speech-to-speech script supports web search capabilities through Brave Search API:

1. Sign up at [Brave Search API](https://api.search.brave.com/) for a free account
2. Get your API key from the dashboard
3. Set the `BRAVE_API_KEY` environment variable or use the `--brave-api-key` command line argument
4. The AI assistant will then be able to search the web for current information, news, and facts

## Sub-Projects

### Channels Subscribe

The `channels-subscribe/` directory contains scripts for subscribing to and analyzing Amazon IVS Channels (low-latency HLS streams).

#### Key Features

- **Frame Analysis**: Analyze individual video frames using Amazon Bedrock Claude models
- **Video Analysis**: Process video segments using TwelveLabs Pegasus for comprehensive content analysis
- **Audio/Video Analysis**: Combined audio and video processing with proper synchronization using PyAV
- **Real-Time Transcription**: Live speech-to-text using OpenAI Whisper with multi-language support
- **Timed Metadata Publishing**: Publish analysis results back to IVS channels as timed metadata
- **Rendition Selection**: Automatic or manual selection of stream quality

#### Scripts Overview

**ivs-channel-subscribe-analyze-frames.py**

- Analyzes individual video frames at configurable intervals using Amazon Bedrock Claude
- Supports multiple Claude models (Sonnet 4, Claude 3.5 Sonnet, Claude 3.5 Haiku)
- Configurable analysis intervals for cost control
- Optional video display and rendition quality selection

**ivs-channel-subscribe-analyze-video.py**

- Records and analyzes video segments using TwelveLabs Pegasus
- Encodes video chunks to MP4 for comprehensive analysis
- OpenCV-based video capture with configurable recording duration

**ivs-channel-subscribe-analyze-audio-video.py**

- Advanced script using PyAV for proper audio/video stream handling
- Native audio capture and encoding with H.264 video and AAC audio
- Complete media analysis with TwelveLabs Pegasus

**ivs-channel-subscribe-transcribe.py**

- Real-time audio transcription using OpenAI Whisper
- Support for 99+ languages with auto-detection
- Multiple Whisper models from tiny to large-v3
- Optional publishing of transcripts as IVS timed metadata

**ivs-channel-subscribe-transcribe-deepgram.py**

- Real-time streaming audio transcription using [Deepgram](https://deepgram.com/) (alternative to Whisper)
- True streaming transcription via WebSocket (no chunking delay)
- Speaker diarization, smart formatting, and filler word detection
- Optional publishing of transcripts as IVS timed metadata
- No GPU required — all processing happens on Deepgram's servers

**ivs_metadata_publisher.py**

- Reusable module for publishing timed metadata to IVS channels
- Automatic channel ARN extraction from M3U8 playlist URLs
- Rate limiting compliance and automatic payload splitting
- Support for transcripts, events, and custom metadata

#### Usage Examples

```bash
# Frame analysis with Claude Sonnet 4
python channels-subscribe/ivs-channel-subscribe-analyze-frames.py \
  --playlist-url "https://example.com/playlist.m3u8" \
  --highest-quality

# Real-time transcription with Whisper and metadata publishing
python channels-subscribe/ivs-channel-subscribe-transcribe.py \
  --playlist-url "https://example.com/playlist.m3u8" \
  --language en \
  --whisper-model base \
  --publish-transcript-as-timed-metadata

# Real-time transcription with Deepgram and metadata publishing
python channels-subscribe/ivs-channel-subscribe-transcribe-deepgram.py \
  --playlist-url "https://example.com/playlist.m3u8" \
  --language en \
  --diarize \
  --publish-transcript-as-timed-metadata

# Video analysis with TwelveLabs Pegasus
python channels-subscribe/ivs-channel-subscribe-analyze-video.py \
  --playlist-url "https://example.com/playlist.m3u8" \
  --analysis-duration 15 \
  --show-video
```

For detailed documentation, see [`channels-subscribe/README.md`](channels-subscribe/README.md).

### Stages Publish

The `stages-publish/` directory contains scripts for publishing media content to IVS Real-Time Stages from MP4 files or live HLS streams.

#### ivs-stage-publish.py

Basic media publishing script that streams video/audio content to an IVS stage from MP4 files or HLS streams.

**Features:**

- Publishes video and audio tracks from MP4 files or M3U8 HLS streams to IVS Real-Time Stages
- JWT token validation and capability checking
- WebRTC connection management
- Option to publish video-only streams
- Optional HLS stream health monitoring with automatic exit when stream ends
- Configurable stream check intervals for cost-effective monitoring

**Usage:**

```bash
cd stages-publish

# Publish MP4 file
python ivs-stage-publish.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --path-to-mp4 "path/to/video.mp4"

# Publish HLS stream
python ivs-stage-publish.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --m3u8-url "https://example.com/stream.m3u8"

# Publish HLS stream with automatic exit when stream ends
python ivs-stage-publish.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --m3u8-url "https://example.com/stream.m3u8" \
  --stream-check-interval 30
```

**Command-line Arguments:**

- `--token`: JWT participant token with publish capabilities (required)
- `--path-to-mp4`: Path to MP4 file to publish (mutually exclusive with --m3u8-url)
- `--m3u8-url`: M3U8 playlist URL for HLS stream to publish (mutually exclusive with --path-to-mp4)
- `--video-only`: Publish video only, no audio (optional flag)
- `--stream-check-interval`: Interval in seconds to check HLS stream health - enables automatic exit when stream ends (optional, HLS only)

**HLS Stream Monitoring:**

When using `--stream-check-interval`, the script monitors HLS stream health by periodically checking if the M3U8 playlist is still accessible:

- **Automatic Exit**: Script gracefully exits when the HLS stream stops broadcasting
- **Rapid Verification**: After a health check failure, the next 2 checks use a 1-second interval for quick verification
- **Consecutive Failures**: Requires 3 consecutive failures before declaring the stream offline
- **Cost Control**: Only makes HTTP requests when explicitly enabled with the parameter
- **No Interference**: Stream monitoring doesn't affect video/audio quality or WebRTC performance

**Stream Monitoring Behavior:**

```
Normal check (30s) → ✅ Healthy → Wait 30s
Normal check (30s) → ❌ Failed → Wait 1s (rapid check 1/2)
Rapid check (2s)   → ❌ Failed → Wait 1s (rapid check 2/2)
Rapid check (2s)   → ❌ Failed → Stream declared offline, exit gracefully
```

**Without `--stream-check-interval`**: Script runs indefinitely until manually stopped (Ctrl+C), regardless of stream status.

#### ivs-stage-publish-events.py

Enhanced publishing script with real-time event handling via WebSocket connections.

**Features:**

- All features of basic publisher
- Real-time stage event monitoring via WebSocket
- Participant join/leave notifications
- Stage state change handling

**Usage:**

```bash
cd stages-publish
python ivs-stage-publish-events.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --path-to-mp4 "path/to/video.mp4"
```

**Command-line Arguments:**

- `--token`: JWT participant token with publish capabilities (required)
- `--path-to-mp4`: Path to MP4 file to publish (required)
- `--video-only`: Publish video only, no audio (optional flag)

#### ivs-stage-pub-sub.py

Advanced script that demonstrates simultaneous publishing and subscribing capabilities.

**Features:**

- Publishes audio from MP4 file while subscribing to other participants
- Demonstrates bidirectional communication
- Audio/video track management
- SDP (Session Description Protocol) handling

**Usage:**

```bash
cd stages-publish
python ivs-stage-pub-sub.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --path-to-mp4 "path/to/audio.mp4"
```

**Command-line Arguments:**

- `--token`: JWT participant token with both publish and subscribe capabilities (required)
- `--path-to-mp4`: Path to MP4 file to publish audio from (required)
- `--video-only`: Publish video only, no audio (optional flag)
- `--subscribe-to`: List of participant IDs to subscribe to (optional)

### Stages Subscribe

The `stages-subscribe/` directory contains scripts for receiving and processing streams from IVS Real-Time Stages.

#### ivs-stage-subscribe-transcribe.py

Subscribes to IVS stage audio streams and provides real-time speech-to-text transcription using OpenAI Whisper with optional VTT file output.

**Features:**

- Subscribes to audio tracks from specific participants in IVS Real-Time Stages
- Real-time speech transcription using Whisper
- Audio chunk processing and buffering
- Multiple language support
- Audio format conversion and normalization
- Optional VTT (WebVTT) subtitle file output with proper timestamps
- Real-time transcription file writing for live captioning

**Usage:**

```bash
cd stages-subscribe
python ivs-stage-subscribe-transcribe.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..."

# With VTT output
python ivs-stage-subscribe-transcribe.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --transcription-output-path "output.vtt" \
  --transcription-output-format "vtt"
```

**Command-line Arguments:**

- `--participant-id`: ID of the participant to subscribe to (required)
- `--token`: JWT participant token with subscribe capabilities (required)
- `--whisper-model`: Whisper model size - "tiny", "base", "small", "medium", "large" (default: "tiny")
- `--fp16`: Enable FP16 precision for faster processing (default: true)
- `--language`: Language code for transcription (default: "en")
- `--chunk-duration`: Audio chunk duration in seconds (default: 5)
- `--transcription-output-path`: Path to save transcription output file (optional)
- `--transcription-output-format`: Format for transcription output - currently supports "vtt" (optional)

**Supported Languages:**

- English ("en")
- Spanish ("es")
- French ("fr")
- German ("de")
- Italian ("it")
- Portuguese ("pt")
- And many more supported by Whisper

#### ivs-stage-subscribe-transcribe-deepgram.py

Subscribes to IVS stage audio streams and provides real-time streaming speech-to-text transcription using [Deepgram](https://deepgram.com/) with optional VTT file output. This is an alternative to the Whisper-based transcription demo that uses Deepgram's cloud-based Nova-3 model for true streaming transcription.

**Key Differences from Whisper Demo:**

| Feature             | Whisper                       | Deepgram                             |
| ------------------- | ----------------------------- | ------------------------------------ |
| Processing          | Local, batch (5s chunks)      | Cloud, true streaming (word-by-word) |
| GPU Required        | Recommended for larger models | No (cloud-based)                     |
| Latency             | 5+ seconds (chunk duration)   | Sub-second (streaming)               |
| Speaker Diarization | Not built-in                  | Built-in (`--diarize`)               |
| Interim Results     | No                            | Yes (see words as they're spoken)    |
| VTT Timestamps      | Fixed chunk boundaries        | Word-level precision                 |
| Smart Formatting    | No                            | Yes (numbers, dates, currencies)     |
| Filler Words        | No                            | Yes (`--filler-words`)               |

**Features:**

- True real-time streaming transcription via Deepgram WebSocket API
- Interim (partial) results displayed as speech is detected
- Speaker diarization for multi-speaker scenarios
- Smart formatting for numbers, dates, and currencies
- Word-level timestamps for precise VTT subtitle output
- Filler word detection ("uh", "um")
- Configurable endpointing and utterance detection
- Automatic language detection
- No GPU required — all processing happens on Deepgram's servers

**Prerequisites:**

- Deepgram API key — sign up at [deepgram.com](https://deepgram.com/) (free tier available)
- Set via `--deepgram-api-key` argument or `DEEPGRAM_API_KEY` environment variable

**Usage:**

```bash
cd stages-subscribe

# Basic transcription
python ivs-stage-subscribe-transcribe-deepgram.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..."

# With speaker diarization and VTT output
python ivs-stage-subscribe-transcribe-deepgram.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --diarize \
  --transcription-output-path "output.vtt" \
  --transcription-output-format "vtt"

# Medical transcription with auto language detection
python ivs-stage-subscribe-transcribe-deepgram.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --model nova-3-medical \
  --language auto

# With filler words and custom endpointing
python ivs-stage-subscribe-transcribe-deepgram.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --filler-words \
  --endpointing 500
```

**Command-line Arguments:**

- `--participant-id`: ID of the participant to subscribe to (required)
- `--token`: JWT participant token with subscribe capabilities (required)
- `--deepgram-api-key`: Deepgram API key (or set `DEEPGRAM_API_KEY` env var)
- `--model`: Deepgram model — "nova-3", "nova-3-medical", "nova-2", "nova-2-meeting", "nova-2-finance", etc. (default: "nova-3")
- `--language`: Language code for transcription, e.g., "en", "en-US", "es", "fr". Use "auto" for automatic detection (default: "en")
- `--smart-format`: Enable smart formatting for numbers, dates, etc. (default: true)
- `--diarize`: Enable speaker diarization (default: false)
- `--filler-words`: Transcribe filler words like "uh" and "um" (default: false)
- `--interim-results`: Show interim (partial) transcription results (default: true)
- `--utterance-end-ms`: Silence duration in ms to detect end of utterance (default: 1000)
- `--endpointing`: Duration in ms of silence before finalizing speech (default: 300)
- `--transcription-output-path`: Path to save transcription output file (optional)
- `--transcription-output-format`: Format for transcription output — currently supports "vtt" (optional)

**Available Models:**

- **nova-3** (default): Latest and most accurate general-purpose model
- **nova-3-medical**: Optimized for medical terminology and conversations
- **nova-2**: Previous generation, still highly capable
- **nova-2-meeting**: Optimized for meeting transcription
- **nova-2-finance**: Optimized for financial terminology
- **nova-2-medical**: Medical-focused Nova-2 variant
- **enhanced**: Enhanced accuracy model
- **base**: Lightweight model for basic transcription

#### ivs-stage-subscribe-analyze-frames.py

Subscribes to IVS stage video streams and provides AI-powered video frame analysis using Amazon Bedrock Claude models for content discovery, moderation, and accessibility.

**Features:**

- Subscribes to video tracks from specific participants in IVS Real-Time Stages
- AI-powered video frame analysis using Claude Sonnet 4
- Configurable analysis intervals to control costs
- Support for multiple Claude models (Sonnet 4, Claude 3.5 Sonnet, Claude 3.5 Haiku)
- Detailed frame descriptions for content moderation and accessibility
- Background processing to avoid blocking video streams
- Cost-conscious design with smart frame sampling

**Usage:**

```bash
cd stages-subscribe
python ivs-stage-subscribe-analyze-frames.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123"
```

**Command-line Arguments:**

- `--token`: JWT participant token with subscribe capabilities (required)
- `--subscribe-to`: Participant ID to subscribe to (required)
- `--analysis-interval`: Time in seconds between frame analyses (default: 30.0)
- `--bedrock-region`: AWS region for Bedrock service (default: "us-east-1")
- `--bedrock-model-id`: Bedrock model ID for analysis (default: "us.anthropic.claude-sonnet-4-6")
- `--disable-analysis`: Disable video frame analysis, just subscribe to video (optional flag)

**Supported Models:**

- **Claude Sonnet 4** (default): `us.anthropic.claude-sonnet-4-6` - Most capable, best for complex analysis
- **Claude 3.5 Sonnet**: `anthropic.claude-3-5-sonnet-20241022-v2:0` - Very capable, good balance of performance and cost
- **Claude 3.5 Haiku**: `anthropic.claude-3-5-haiku-20241022-v1:0` - Fastest and cheapest, good for basic content moderation

**Use Cases:**

- **Content Moderation**: Automatically detect inappropriate content in live streams
- **Content Discovery**: Generate descriptions and tags for video content
- **Accessibility**: Create detailed descriptions for visually impaired users
- **Analytics**: Track objects, activities, and engagement in video streams
- **Compliance**: Monitor streams for regulatory compliance

**Cost Control Features:**

- Configurable analysis intervals (default 30 seconds to minimize costs)
- Background processing doesn't block video streaming
- Option to disable analysis entirely for testing
- Smart error handling prevents failed analyses from crashing streams

#### ivs-stage-subscribe-analyze-video.py

Subscribes to IVS stage audio and video streams and provides AI-powered video analysis using Amazon Bedrock TwelveLabs Pegasus for comprehensive video understanding.

**Features:**

- Subscribes to both audio and video tracks from specific participants
- Records short video clips (configurable duration) for analysis
- Encodes audio and video to MP4 format in memory
- AI-powered video analysis using TwelveLabs Pegasus model
- Detailed video content descriptions including people, objects, activities, and text
- Asynchronous processing to maintain stream performance
- Configurable analysis duration and frequency

**Usage:**

```bash
cd stages-subscribe
python ivs-stage-subscribe-analyze-video.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123"
```

**Command-line Arguments:**

- `--token`: JWT participant token with subscribe capabilities (required)
- `--subscribe-to`: Participant ID to subscribe to (required)
- `--analysis-duration`: Duration in seconds for video recording before analysis (default: 10.0)
- `--bedrock-region`: AWS region for Bedrock service (default: "us-west-2")
- `--bedrock-model-id`: Bedrock model ID for analysis (default: "us.twelvelabs.pegasus-1-2-v1:0")
- `--disable-analysis`: Disable video analysis, just subscribe to video (optional flag)

### Stages Nova Speech-to-Speech

The `stages-nova-s2s/` directory contains the most advanced script integrating Amazon Nova Sonic for AI-powered speech-to-speech functionality.

#### ivs-stage-nova-s2s.py

A comprehensive script that combines IVS Real-Time Stages with Amazon Nova Sonic for conversational AI experiences.

**Features:**

- Bidirectional audio streaming with IVS participants
- Amazon Nova Sonic integration for AI responses
- Real-time waveform visualization
- Audio resampling and format conversion
- WebRTC track management for both publishing and subscribing
- Dynamic audio visualization with gradient colormaps
- AI-powered video frame analysis using Amazon Bedrock Claude models
- Built-in tools for date/time, weather, and visual analysis
- Configurable frame analysis with multiple Claude model options

**Usage:**

```bash
cd stages-nova-s2s
python ivs-stage-nova-s2s.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123"
```

**Command-line Arguments:**

- `--token`: JWT participant token with both publish and subscribe capabilities (required)
- `--subscribe-to`: Participant ID to subscribe to (required)
- `--nova-model-id`: Amazon Nova model identifier (default: "amazon.nova-sonic-v1:0")
- `--nova-region`: AWS region for Nova service (default: "us-east-1")
- `--disable-frame-analysis`: Disable video frame analysis (default: enabled)
- `--bedrock-model-id`: Bedrock model ID for frame analysis (default: "us.anthropic.claude-sonnet-4-6")
- `--bedrock-region`: AWS region for Bedrock service (default: "us-east-1")
- `--weather-api-key`: Weather API key for weather tool functionality (overrides WEATHER_API_KEY environment variable)
- `--brave-api-key`: Brave Search API key for web search tool functionality (overrides BRAVE_API_KEY environment variable)
- `--ice-timeout`: ICE gathering timeout in seconds (default: 1, original: 5) - Lower values speed up connection establishment

**Key Components:**

1. **AgentAudioTrack**: Custom audio track for streaming Nova responses
2. **AgentVideoTrack**: Dynamic waveform visualization with thinking states
3. **BedrockStreamManager**: Manages bidirectional Nova Sonic streaming
4. **Audio Processing**: Handles resampling between IVS (48kHz) and Nova (16kHz)
5. **Tool Support**: Built-in tools for date/time, weather, and video frame analysis
6. **Frame Analysis**: Non-blocking AI-powered video frame analysis using Claude models

**Available Tools:**

- **Date/Time Tool**: Get current date and time information for specific locations with timezone support
- **Weather Tool**: Get current weather and 5-day forecast (requires `WEATHER_API_KEY`)
- **Web Search Tool**: Search the web for current information, news, and facts (requires `BRAVE_API_KEY`)
- **Frame Analysis Tool**: Analyze video frames for visual assistance and content description

#### Assistant Management

For automated management of multiple Nova assistant instances via WebSocket integration with IVS Chat, see:

**[IVS Stage Assistant Manager Documentation](stages-nova-s2s/MANAGING_ASSISTANT_DEMO.md)**

This companion tool allows you to dynamically launch and manage multiple Nova S2S instances based on chat messages, perfect for scaling AI assistants across multiple participants.

#### OpenAI Assistant Management

For automated management of multiple OpenAI assistant instances via WebSocket integration with IVS Chat, see:

**[IVS Stage OpenAI Assistant Manager Documentation](stages-gpt-realtime/MANAGING_OPENAI_ASSISTANT_DEMO.md)**

This companion tool allows you to dynamically launch and manage multiple OpenAI real-time instances based on chat messages, with full control over voice, VAD settings, and vision capabilities.

### Stages OpenAI Real-time API

The `stages-gpt-realtime/` directory contains integration with OpenAI's real-time API for speech-to-speech conversations with IVS Real-Time Stages.

#### ivs-stage-gpt-realtime.py

A comprehensive script that integrates OpenAI's gpt-realtime API with IVS Real-Time Stages for conversational AI experiences.

**Features:**

- Bidirectional audio streaming with IVS participants
- OpenAI real-time API integration for AI responses
- WebSocket-based real-time communication with OpenAI
- Real-time audio visualization
- Audio resampling and format conversion (24kHz for OpenAI)
- WebRTC track management for both publishing and subscribing
- Voice activity detection and interruption handling
- Multiple voice options (alloy, ash, ballad, coral, echo, sage, shimmer, verse, marin, cedar)

**Usage:**

```bash
cd stages-gpt-realtime
python ivs-stage-openai-realtime.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --openai-key "sk-..."
```

**Command-line Arguments:**

- `--token`: JWT participant token with both publish and subscribe capabilities (required)
- `--subscribe-to`: Participant ID to subscribe to (required)
- `--openai-key`: OpenAI API key (optional, uses OPENAI_API_KEY environment variable if not provided)
- `--model`: OpenAI model to use (default: "gpt-realtime")
- `--voice`: Voice to use for responses - "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar" (default: "cedar")
- `--disable-frame-analysis`: Disable video frame analysis (default: enabled)
- `--bedrock-region`: AWS region for Bedrock service (default: "us-east-1")
- `--bedrock-model-id`: Bedrock model ID for frame analysis (default: "us.anthropic.claude-sonnet-4-6")
- `--ice-timeout`: ICE gathering timeout in seconds (default: 1, original: 5)

**Key Components:**

1. **OpenAIAudioTrack**: Custom audio track for streaming OpenAI responses
2. **OpenAIVideoTrack**: Dynamic audio visualization with OpenAI branding
3. **OpenAIRealtimeManager**: Manages bidirectional OpenAI real-time API streaming
4. **Audio Processing**: Handles resampling for OpenAI's 24kHz requirement
5. **WebSocket Management**: Handles OpenAI real-time API WebSocket connection
6. **Vision Capabilities**: AI-powered video frame analysis using Amazon Bedrock Claude models

**Available Voices:**

- **cedar** (default): Warm and conversational
- **alloy**: Balanced and natural
- **ash**: Clear and articulate
- **ballad**: Smooth and melodic
- **coral**: Bright and engaging
- **echo**: Clear and articulate
- **sage**: Wise and thoughtful
- **shimmer**: Soft and gentle
- **verse**: Expressive and dynamic
- **marin**: Professional and polished

**Prerequisites:**

- OpenAI API key with real-time API access
- IVS stage token with both publish and subscribe capabilities
- Python 3.8+ with required dependencies

**Known Limitations:**

- **Semantic VAD Transcriptions**: When using `--vad-mode semantic_vad`, user input transcriptions may not be generated. Use `--vad-mode server_vad` if you need reliable user transcriptions for SEI metadata or logging.

**Environment Variables:**

```bash
export OPENAI_API_KEY="sk-your-openai-api-key-here"
```

**Example Usage:**

```bash
# Basic OpenAI conversation
python stages-gpt-realtime/ivs-stage-openai-realtime.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123"

# Using different voice and model
python stages-gpt-realtime/ivs-stage-openai-realtime.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --voice "nova" \
  --model "gpt-4o-realtime-preview-2024-10-01"

# With explicit API key
python stages-gpt-realtime/ivs-stage-openai-realtime.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --openai-key "sk-..."
```

### Stages Deepgram Voice Agent

The `stages-deepgram-agent/` directory contains a conversational AI voice agent powered by [Deepgram's Voice Agent API](https://developers.deepgram.com/docs/voice-agent), integrated with IVS Real-Time Stages.

Unlike the Nova S2S and GPT Real-time demos which use separate STT and TTS pipelines, Deepgram's Voice Agent API handles the entire voice conversation loop — speech-to-text, LLM reasoning, and text-to-speech — through a single WebSocket connection.

#### ivs-stage-deepgram-agent.py

**Features:**

- Single WebSocket for the full STT → LLM → TTS pipeline
- Bidirectional audio streaming with IVS participants
- Configurable LLM provider (OpenAI, Anthropic, Groq)
- Multiple Deepgram Aura TTS voices (50+ options)
- AI-powered video frame analysis via Bedrock Claude (ask "what do you see?")
- SEI transcript publishing (user and agent text embedded in H.264 video stream)
- Real-time audio visualization (reuses proven AgentVideoTrack)
- Automatic barge-in / interruption handling
- Conversation transcript logging
- Custom system prompts and greeting messages

**Usage:**

```bash
cd stages-deepgram-agent

# Basic conversation
python ivs-stage-deepgram-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123"

# Custom voice and LLM
python ivs-stage-deepgram-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --voice "aura-2-orion-en" \
  --think-model "gpt-4o"

# Custom personality
python ivs-stage-deepgram-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --prompt "You are a pirate. Respond in pirate speak." \
  --greeting "Ahoy! What can I do for ye?"
```

**Command-line Arguments:**

- `--token`: JWT participant token with both publish and subscribe capabilities (required)
- `--subscribe-to`: Participant ID to subscribe to (required)
- `--deepgram-api-key`: Deepgram API key (or set `DEEPGRAM_API_KEY` env var)
- `--voice`: Deepgram TTS voice model (default: "aura-2-asteria-en")
- `--think-model`: LLM model for reasoning (default: "gpt-4o-mini")
- `--think-provider`: LLM provider — "open_ai", "anthropic", "groq" (default: "open_ai")
- `--prompt`: System prompt for the agent personality
- `--greeting`: Greeting message spoken when the session starts
- `--language`: Language code (default: "en")
- `--ice-timeout`: ICE gathering timeout in seconds (default: 1)
- `--disable-frame-analysis`: Disable video frame analysis (default: enabled)
- `--bedrock-model-id`: Bedrock model for frame analysis (default: "us.anthropic.claude-sonnet-4-6")
- `--bedrock-region`: AWS region for Bedrock (default: "us-east-1")

**Key Components:**

1. **DeepgramAgentManager**: Manages the Voice Agent WebSocket — settings, audio streaming, event handling
2. **AgentAudioTrack**: Buffers Deepgram TTS audio and streams it to IVS via WebRTC (reused from Nova demo)
3. **AgentVideoTrack**: Visual feedback with throbbing circle animation (reused from Nova demo)

**How It Compares:**

| Feature               | Nova S2S              | GPT Real-time     | Deepgram Agent                         |
| --------------------- | --------------------- | ----------------- | -------------------------------------- |
| STT                   | Nova Sonic (built-in) | OpenAI (built-in) | Deepgram Nova-3                        |
| LLM                   | Nova Sonic (built-in) | GPT-4o (built-in) | Configurable (OpenAI, Anthropic, Groq) |
| TTS                   | Nova Sonic (built-in) | OpenAI (built-in) | Deepgram Aura (50+ voices)             |
| Vision                | Bedrock Claude (tool) | OpenAI native     | Bedrock Claude (tool)                  |
| WebSocket Connections | 1 (Bedrock)           | 1 (OpenAI)        | 1 (Deepgram)                           |
| AWS Dependency        | Yes (Bedrock)         | No                | Optional (Bedrock for vision only)     |
| LLM Flexibility       | Fixed                 | Fixed             | Swappable                              |

**Prerequisites:**

- Deepgram API key — sign up at [deepgram.com](https://deepgram.com/)
- IVS stage token with both publish and subscribe capabilities

**Environment Variables:**

```bash
export DEEPGRAM_API_KEY="your-deepgram-api-key"
```

#### Deepgram Agent Manager

For automated management of multiple Deepgram Agent instances via WebSocket integration with IVS Chat, use `ivs-stage-deepgram-agent-manager.py`. This companion tool dynamically launches and manages agent instances based on chat messages, with full control over voice, LLM provider/model, prompt, and greeting.

```bash
python stages-deepgram-agent/ivs-stage-deepgram-agent-manager.py \
  --chat-room-arn "arn:aws:ivschat:us-east-1:123456789012:room/abcdefgh" \
  --ws-endpoint "wss://edge.ivschat.us-east-1.amazonaws.com" \
  --verbose
```

Chat message payload to launch an agent:

```json
{
  "action": "LAUNCH_ASSISTANT",
  "stageArn": "arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh",
  "participantId": "participant-123",
  "voice": "aura-2-asteria-en",
  "thinkProvider": "open_ai",
  "thinkModel": "gpt-4o-mini",
  "prompt": "You are a friendly assistant.",
  "greeting": "Hello!"
}
```

For detailed documentation including all voices, SEI transcript format, troubleshooting, and frontend integration examples, see **[`stages-deepgram-agent/README.md`](stages-deepgram-agent/README.md)**.

### Stages SEI Publishing

The `stages_sei/` directory contains a comprehensive SEI (Supplemental Enhancement Information) publishing system for embedding metadata directly into H.264 video streams.

**What is SEI?**

SEI NAL units are part of the H.264/AVC video compression standard that allow embedding additional metadata within the video stream itself. This metadata travels with the video frames, ensuring perfect synchronization between video content and associated data.

**Key Features:**

- **Perfect Synchronization**: Metadata is embedded directly in video frames
- **Low Latency**: No separate data channels needed
- **Standards Compliant**: Uses official H.264 specification
- **Multi-format Support**: Handles Annex B, AVCC, and RTP H.264 formats
- **Automatic Integration**: Patches aiortc and PyAV encoders automatically
- **Reliable Delivery**: 3x repetition with client-side deduplication

**Components:**

- **`sei_publisher.py`**: High-level interface for publishing SEI messages
- **`h264_sei_patch.py`**: Low-level H.264 encoder patching system
- **`SEI.md`**: Comprehensive documentation and usage guide

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

# Real-time transcription with Deepgram (streaming, no GPU needed)
python channels-subscribe/ivs-channel-subscribe-transcribe-deepgram.py \
  --playlist-url "https://example.com/playlist.m3u8" \
  --highest-quality \
  --diarize \
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

#### Basic Publishing Examples

```bash
# Publish MP4 file to IVS stage
python stages-publish/ivs-stage-publish.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --path-to-mp4 "sample-video.mp4"

# Publish HLS stream to IVS stage
python stages-publish/ivs-stage-publish.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --m3u8-url "https://example.com/live/stream.m3u8"

# Publish HLS stream with automatic exit when stream ends (check every 30 seconds)
python stages-publish/ivs-stage-publish.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --m3u8-url "https://example.com/live/stream.m3u8" \
  --stream-check-interval 30

# Publish HLS stream with frequent monitoring (check every 10 seconds)
python stages-publish/ivs-stage-publish.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM8NCJ9..." \
  --m3u8-url "https://example.com/live/stream.m3u8" \
  --stream-check-interval 10

# Publish video-only HLS stream
python stages-publish/ivs-stage-publish.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --m3u8-url "https://example.com/live/stream.m3u8" \
  --video-only
```

#### Publishing with Events Example

```bash
# Publish with real-time event monitoring
python stages-publish/ivs-stage-publish-events.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --path-to-mp4 "sample-video.mp4"
```

#### Transcription Examples

```bash
# Basic transcription with Whisper (console output only)
python stages-subscribe/ivs-stage-subscribe-transcribe.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..."

# Subscribe and transcribe audio in Spanish
python stages-subscribe/ivs-stage-subscribe-transcribe.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --language "es" \
  --whisper-model "medium"

# Save transcription to VTT file for live captioning
python stages-subscribe/ivs-stage-subscribe-transcribe.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --transcription-output-path "live_captions.vtt" \
  --transcription-output-format "vtt"

# High-quality transcription with VTT output
python stages-subscribe/ivs-stage-subscribe-transcribe.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --whisper-model "large-v3" \
  --language "en" \
  --chunk-duration "10" \
  --transcription-output-path "meeting_transcript.vtt" \
  --transcription-output-format "vtt"
```

#### Deepgram Transcription Examples

```bash
# Basic Deepgram transcription (streaming, no GPU needed)
python stages-subscribe/ivs-stage-subscribe-transcribe-deepgram.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..."

# With speaker diarization
python stages-subscribe/ivs-stage-subscribe-transcribe-deepgram.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --diarize

# Medical transcription with auto language detection and VTT output
python stages-subscribe/ivs-stage-subscribe-transcribe-deepgram.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --model nova-3-medical \
  --language auto \
  --transcription-output-path "medical_transcript.vtt" \
  --transcription-output-format "vtt"

# Meeting transcription with diarization and filler words
python stages-subscribe/ivs-stage-subscribe-transcribe-deepgram.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --model nova-2-meeting \
  --diarize \
  --filler-words \
  --transcription-output-path "meeting.vtt" \
  --transcription-output-format "vtt"
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

#### OpenAI Real-time API Examples

```bash
# Basic OpenAI real-time conversation
python stages-gpt-realtimeltime/ivs-stage-openai-realtime.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123"

# Using different voice
python stages-gpt-realtime/ivs-stage-openai-realtime.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --voice "nova"

# With explicit OpenAI API key
python stages-gpt-realtime/ivs-stage-openai-realtime.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --openai-key "sk-your-key-here"

# With vision capabilities disabled
python stages-gpt-realtime/ivs-stage-openai-realtime.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --disable-frame-analysis

# With custom Bedrock model for vision
python stages-gpt-realtimeltime/ivs-stage-openai-realtime.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --bedrock-model-id "anthropic.claude-3-5-sonnet-20241022-v2:0"

# Fast connection setup
python stages-gpt-realtime/ivs-stage-openai-realtime.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --ice-timeout 1
```

#### Deepgram Voice Agent Examples

```bash
# Basic Deepgram voice agent conversation
python stages-deepgram-agent/ivs-stage-deepgram-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123"

# With custom voice and GPT-4o
python stages-deepgram-agent/ivs-stage-deepgram-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --voice "aura-2-orion-en" \
  --think-model "gpt-4o"

# With Anthropic Claude as the LLM
python stages-deepgram-agent/ivs-stage-deepgram-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --think-provider "anthropic" \
  --think-model "claude-sonnet-4-6"

# Custom personality
python stages-deepgram-agent/ivs-stage-deepgram-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --prompt "You are a sports commentator. Be energetic and exciting." \
  --greeting "Welcome to the show!"
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

4. **Deepgram Connection Issues**
   - Verify your Deepgram API key is valid and has sufficient credits
   - Check network connectivity to `api.deepgram.com`
   - Ensure the `deepgram-sdk` package is installed: `pip install deepgram-sdk`
   - If transcription stops unexpectedly, check Deepgram's [status page](https://status.deepgram.com/)
   - For language auto-detection issues, try specifying the language explicitly

5. **Timed Metadata Publishing Issues**
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
   - For Deepgram, try domain-specific models (e.g., `nova-3-medical`, `nova-2-meeting`)

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
   - Use VTT output for live captioning applications
   - Specify language explicitly for better accuracy and performance

4. **For Deepgram Transcription:**
   - Use `--endpointing 300` (default) for a good balance of speed and accuracy
   - Lower endpointing values (e.g., 100) give faster results but may split mid-sentence
   - Specify language explicitly rather than using `auto` for better accuracy and lower latency
   - Use `--model nova-3` (default) for best accuracy, or domain-specific models for specialized content
   - Enable `--diarize` only when needed — it adds slight processing overhead

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

- `aiortc>=1.12.0` - WebRTC implementation
- `av>=10.0.0` - Media processing
- `requests>=2.28.0` - HTTP client
- `websockets>=11.0.0` - WebSocket client
- `numpy>=1.21.0` - Numerical computing

### AI/ML Dependencies

- `whisper` (from GitHub) - Speech recognition
- `deepgram-sdk>=3.0.0` - Deepgram real-time transcription
- `boto3>=1.34.0` - AWS SDK for Bedrock and IVS
- `aws-sdk-bedrock-runtime` - Amazon Bedrock client
- `smithy-aws-core>=0.0.1` - AWS SDK core
- `pyaudio>=0.2.13` - Audio I/O
- `rx>=3.2.0` - Reactive extensions
- `Pillow>=10.0.0` - Image processing for video frame analysis
- `opencv-python>=4.8.0` - Computer vision for video processing

### Utility Dependencies

- `pytz` - Timezone handling
- `tzlocal` - Local timezone detection

### System Requirements

- Python 3.8+
- FFmpeg
- PortAudio (for audio I/O)
- Sufficient bandwidth for WebRTC streams

## Contributing

See [CONTRIBUTING](CONTRIBUTING.md) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](./LICENSE) file.

## Support

For issues related to:

- **Amazon IVS Real-Time Stages**: Check the [IVS Real-Time Streaming documentation](https://docs.aws.amazon.com/ivs/latest/RealTimeUserGuide/)
- **Amazon IVS Channels**: Check the [IVS Low-Latency Streaming documentation](https://docs.aws.amazon.com/ivs/latest/LowLatencyUserGuide/)
- **Amazon Nova**: Check the [Bedrock documentation](https://docs.aws.amazon.com/bedrock/)
- **Amazon Bedrock**: Check the [Bedrock User Guide](https://docs.aws.amazon.com/bedrock/latest/userguide/)
- **aiortc**: Check the [aiortc documentation](https://aiortc.readthedocs.io/)
- **OpenAI Whisper**: Check the [Whisper repository](https://github.com/openai/whisper)
- **Deepgram**: Check the [Deepgram documentation](https://developers.deepgram.com/docs) and [API reference](https://developers.deepgram.com/reference/deepgram-api-overview)

---

_This project demonstrates advanced integration patterns between Amazon IVS services and AI capabilities. From real-time conversational AI with Nova Sonic to comprehensive video analysis with Claude and TwelveLabs Pegasus, and streaming transcription with Deepgram and Whisper, these demos showcase the power of combining live video streaming with cutting-edge AI services._
