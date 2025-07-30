# Amazon-ivs-aiortc-demos

A comprehensive collection of Python scripts demonstrating various Amazon IVS (Interactive Video Service) Real-Time Stages capabilities using the aiortc WebRTC library. This project showcases publishing, subscribing, transcription, and AI-powered speech-to-speech functionality.

## Table of Contents

- [Overview](#overview)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Sub-Projects](#sub-projects)
  - [Stages Publish](#stages-publish)
  - [Stages Subscribe](#stages-subscribe)
  - [Stages Nova Speech-to-Speech](#stages-nova-speech-to-speech)
- [Usage Examples](#usage-examples)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

## Overview

This project demonstrates how to integrate Amazon IVS Real-Time Stages with various AI and media processing capabilities:

- **WebRTC Publishing**: Stream video/audio content to IVS stages
- **WebRTC Subscribing**: Receive and process streams from IVS stages
- **Real-time Transcription**: Convert speech to text using OpenAI Whisper
- **AI Video Analysis**: Analyze video frames using Amazon Bedrock Claude models
- **AI Speech-to-Speech**: Integrate Amazon Nova Sonic for conversational AI
- **Event Handling**: Process real-time stage events via WebSocket connections
- **Waveform Visualization**: Generate dynamic audio visualizations

## Project Structure

```
ivs-aiortc/
├── README.md                           # This file
├── requirements.txt                    # Python dependencies
├── .gitignore                         # Git ignore rules
├── stages-publish/                    # Publishing examples
│   ├── ivs-stage-publish.py          # Basic media publishing
│   ├── ivs-stage-publish-events.py   # Publishing with event handling
│   └── ivs-stage-pub-sub.py          # Simultaneous publish/subscribe
├── stages-subscribe/                  # Subscribing examples
│   ├── ivs-stage-subscribe-transcribe.py  # Subscribe with transcription
│   └── ivs-stage-subscribe-analyze-frames.py  # Subscribe with AI video analysis
└── stages-nova-s2s/                  # AI Speech-to-Speech
    └── ivs-stage-nova-s2s.py         # Nova Sonic integration
```

## Prerequisites

- Python 3.8 or higher
- AWS CLI configured with appropriate credentials
- Amazon IVS Real-Time Stage ARN and participant tokens
- FFmpeg (for media processing when using transcription demo - not necessary otherwise)
- Audio input/output devices (for speech-to-speech functionality)

### AWS Permissions Required

Your AWS credentials need the following permissions:
- `ivs:CreateParticipantToken`
- `bedrock:InvokeModel` (for video frame analysis with Claude)
- `bedrock:InvokeModelWithBidirectionalStream` (for Nova Sonic)
- Access to Amazon IVS Real-Time Stages

## Installation

1. **Clone or navigate to the project directory:**
   ```bash
   cd /Users/shartodd/projects/scratch/ivs-aiortc
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

### Stages Publish

The `stages-publish/` directory contains scripts for publishing media content to IVS Real-Time Stages.

#### ivs-stage-publish.py

Basic media publishing script that streams video/audio content to an IVS stage.

**Features:**
- Publishes video and audio tracks from MP4 files to IVS Real-Time Stages
- JWT token validation and capability checking
- WebRTC connection management
- Option to publish video-only streams

**Usage:**
```bash
cd stages-publish
python ivs-stage-publish.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --path-to-mp4 "path/to/video.mp4"
```

**Command-line Arguments:**
- `--token`: JWT participant token with publish capabilities (required)
- `--path-to-mp4`: Path to MP4 file to publish (required)
- `--video-only`: Publish video only, no audio (optional flag)

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

Subscribes to IVS stage audio streams and provides real-time speech-to-text transcription using OpenAI Whisper.

**Features:**
- Subscribes to audio tracks from specific participants in IVS Real-Time Stages
- Real-time speech transcription using Whisper
- Audio chunk processing and buffering
- Multiple language support
- Audio format conversion and normalization

**Usage:**
```bash
cd stages-subscribe
python ivs-stage-subscribe-transcribe.py \
  --participant-id "participant123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..."
```

**Command-line Arguments:**
- `--participant-id`: ID of the participant to subscribe to (required)
- `--token`: JWT participant token with subscribe capabilities (required)
- `--whisper-model`: Whisper model size - "tiny", "base", "small", "medium", "large" (default: "tiny")
- `--fp16`: Enable FP16 precision for faster processing (default: true)
- `--language`: Language code for transcription (default: "en")
- `--chunk-duration`: Audio chunk duration in seconds (default: 5)

**Supported Languages:**
- English ("en")
- Spanish ("es")
- French ("fr")
- German ("de")
- Italian ("it")
- Portuguese ("pt")
- And many more supported by Whisper

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
- `--aws-region`: AWS region for Bedrock service (default: "us-east-1")
- `--model-id`: Bedrock model ID for analysis (default: "us.anthropic.claude-sonnet-4-20250514-v1:0")
- `--disable-analysis`: Disable video frame analysis, just subscribe to video (optional flag)

**Supported Models:**
- **Claude Sonnet 4** (default): `us.anthropic.claude-sonnet-4-20250514-v1:0` - Most capable, best for complex analysis
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

**Usage:**
```bash
cd stages-nova-s2s
python ivs-stage-nova-s2s.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "ABC123"
```

**Command-line Arguments:**
- `--token`: JWT participant token with both publish and subscribe capabilities (required)
- `--subscribe-to`: Participant ID to subscribe to (required)
- `--nova-model`: Amazon Nova model identifier (default: "amazon.nova-sonic-v1:0")
- `--nova-region`: AWS region for Nova service (default: "us-east-1")

**Key Components:**

1. **AgentAudioTrack**: Custom audio track for streaming Nova responses
2. **WaveformVideoTrack**: Dynamic waveform visualization
3. **BedrockStreamManager**: Manages bidirectional Nova Sonic streaming
4. **Audio Processing**: Handles resampling between IVS (48kHz) and Nova (16kHz)
5. **Tool Support**: Built-in tools for date/time and weather information (requires `WEATHER_API_KEY` environment variable)

### Utility Scripts

*Note: Utility scripts are excluded from this documentation as they are development/testing tools.*

## Usage Examples

### Basic Publishing Example

```bash
# Publish MP4 file to IVS stage
python stages-publish/ivs-stage-publish.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --path-to-mp4 "sample-video.mp4"
```

### Publishing with Events Example

```bash
# Publish with real-time event monitoring
python stages-publish/ivs-stage-publish-events.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --path-to-mp4 "sample-video.mp4"
```

### Transcription Example

```bash
# Subscribe and transcribe audio in Spanish
python stages-subscribe/ivs-stage-subscribe-transcribe.py \
  --participant-id "user123" \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --language "es" \
  --whisper-model "medium"
```

### Video Frame Analysis Examples

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
  --model-id "anthropic.claude-3-5-haiku-20241022-v1:0" \
  --analysis-interval 60.0

# Analysis in different AWS region
python stages-subscribe/ivs-stage-subscribe-analyze-frames.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --aws-region "eu-west-1"

# Subscribe to video without analysis (testing connectivity)
python stages-subscribe/ivs-stage-subscribe-analyze-frames.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --disable-analysis
```

### AI Speech-to-Speech Example

```bash
# Start Nova Sonic conversation
python stages-nova-s2s/ivs-stage-nova-s2s.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --nova-model "amazon.nova-sonic-v1:0" \
  --nova-region "us-east-1"
```

### Publish and Subscribe Example

```bash
# Simultaneously publish and subscribe
python stages-publish/ivs-stage-pub-sub.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --path-to-mp4 "audio-file.mp4" \
  --subscribe-to "participant1" "participant2"
```

### Creating Participant Tokens

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

4. **Video Frame Analysis Issues**
   - Verify AWS credentials have `bedrock:InvokeModel` permissions
   - Check Claude model availability in your region
   - Monitor analysis costs with appropriate intervals
   - Ensure video track is receiving frames before analysis begins

5. **Transcription Accuracy**
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

1. **For Nova Sonic:**
   - Use consistent 1ms delays between audio chunks
   - Implement proper buffering strategies
   - Monitor memory usage during long sessions

2. **For Transcription:**
   - Choose appropriate chunk duration (5-10 seconds)
   - Use smaller Whisper models for real-time processing
   - Consider GPU acceleration for large models

3. **For Video Frame Analysis:**
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
- `boto3>=1.34.0` - AWS SDK for Bedrock
- `aws-sdk-bedrock-runtime` - Amazon Bedrock client
- `smithy-aws-core>=0.0.1` - AWS SDK core
- `pyaudio>=0.2.13` - Audio I/O
- `rx>=3.2.0` - Reactive extensions
- `Pillow>=10.0.0` - Image processing for video frame analysis

### Utility Dependencies
- `pytz` - Timezone handling
- `tzlocal` - Local timezone detection

### System Requirements
- Python 3.8+
- FFmpeg
- PortAudio (for audio I/O)
- Sufficient bandwidth for WebRTC streams

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is provided as-is for educational and demonstration purposes. Please ensure compliance with AWS service terms and conditions.

## Support

For issues related to:
- **Amazon IVS**: Check the [IVS documentation](https://docs.aws.amazon.com/ivs/)
- **Amazon Nova**: Check the [Bedrock documentation](https://docs.aws.amazon.com/bedrock/)
- **aiortc**: Check the [aiortc documentation](https://aiortc.readthedocs.io/)

---

*This project demonstrates advanced integration patterns between Amazon IVS Real-Time Stages and AI services. The Nova speech-to-speech integration showcases cutting-edge conversational AI capabilities in live video environments.*
