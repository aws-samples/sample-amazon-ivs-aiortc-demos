# Amazon IVS Channel Analysis Tools

A comprehensive Python toolkit for subscribing to and analyzing Amazon IVS (Interactive Video Service) channels. Includes AI-powered analysis, real-time transcription, and timed metadata publishing capabilities.

## 🚀 Features

### Channel Subscription & Analysis

-   **Frame Analysis**: Analyze individual video frames using Amazon Bedrock Claude
-   **Video Analysis**: Process video segments using TwelveLabs Pegasus for comprehensive content analysis
-   **Audio/Video Analysis**: Combined audio and video processing with proper synchronization
-   **Real-Time Transcription**: Live speech-to-text using OpenAI Whisper
-   **Timed Metadata Publishing**: Publish analysis results back to IVS as timed metadata
-   **Rendition Selection**: Automatic or manual selection of stream quality

## 📋 Requirements

-   Python 3.8+
-   AWS credentials configured (for Bedrock analysis)
-   OpenCV (optional, for video display)

## 🛠 Installation

1. **Clone the repository:**

    ```bash
    git clone <repository-url>
    cd amazon-ivs-python-demos
    ```

2. **Install Python dependencies:**

    ```bash
    pip install -r ../requirements.txt
    ```

> [!NOTE]
> The `requirements.txt` file is located in the root directory of the project and contains dependencies for all sub-projects.

3. **Configure AWS credentials** (for Bedrock analysis):
    ```bash
    aws configure
    # or set environment variables:
    export AWS_ACCESS_KEY_ID=your_access_key
    export AWS_SECRET_ACCESS_KEY=your_secret_key
    export AWS_DEFAULT_REGION=us-east-1
    ```

## 📁 Project Structure

```
amazon-ivs-python-demos/                        # Root project directory
├── channels-subscribe/                          # Channel subscription and analysis tools
│   ├── README.md                                # This file
│   ├── ivs-channel-subscribe-analyze-frames.py      # Frame-by-frame analysis with Claude
│   ├── ivs-channel-subscribe-analyze-video.py       # Video segment analysis with Pegasus
│   ├── ivs-channel-subscribe-analyze-audio-video.py # Combined audio/video analysis
│   ├── ivs-channel-subscribe-transcribe.py          # Real-time transcription with Whisper
│   └── ivs_metadata_publisher.py                   # Reusable IVS timed metadata publisher
├── requirements.txt                             # Python dependencies (shared across all sub-projects)
├── stages-publish/                              # Real-Time Stages publishing scripts
├── stages-subscribe/                            # Real-Time Stages subscribing scripts
├── stages-nova-s2s/                             # AI Speech-to-Speech scripts
├── stages_sei/                                  # SEI Publishing System
└── README.md                                   # Main project documentation
```

## 🎯 Scripts Overview

### 1. Frame Analysis (`ivs-channel-subscribe-analyze-frames.py`)

Analyzes individual video frames at configurable intervals using Amazon Bedrock Claude.

**Key Features:**

-   Frame-by-frame analysis with Claude Sonnet
-   Configurable analysis intervals (default: 30 seconds)
-   Optional video display
-   Rendition quality selection

**Arguments:**

-   `--playlist-url` (required): M3U8 playlist URL
-   `--show-video`: Display video frames in a window
-   `--analysis-interval`: Time in seconds between frame analyses (default: 30.0)
-   `--bedrock-region`: AWS region for Bedrock service (default: us-east-1)
-   `--bedrock-model-id`: Claude model ID (default: us.anthropic.claude-sonnet-4-20250514-v1:0)
-   `--disable-analysis`: Disable video frame analysis
-   `--highest-quality`: Auto-select highest quality rendition
-   `--lowest-quality`: Auto-select lowest quality rendition

**Usage:**

```bash
# Basic frame analysis
python channels-subscribe/ivs-channel-subscribe-analyze-frames.py --playlist-url "https://example.com/playlist.m3u8" --highest-quality

# Custom analysis interval with video display
python channels-subscribe/ivs-channel-subscribe-analyze-frames.py --playlist-url "https://example.com/playlist.m3u8" --analysis-interval 10 --show-video

# Manual rendition selection
python channels-subscribe/ivs-channel-subscribe-analyze-frames.py --playlist-url "https://example.com/playlist.m3u8"
```

### 2. Video Analysis (`ivs-channel-subscribe-analyze-video.py`)

Records and analyzes video segments using TwelveLabs Pegasus for comprehensive content understanding.

**Key Features:**

-   Records video chunks (default: 10 seconds)
-   Encodes to MP4 for analysis
-   Uses TwelveLabs Pegasus model
-   OpenCV-based video capture

**Arguments:**

-   `--playlist-url` (required): M3U8 playlist URL
-   `--show-video`: Display video frames in a window
-   `--analysis-duration`: Duration in seconds for video recording before analysis (default: 10.0)
-   `--bedrock-region`: AWS region for Bedrock service (default: us-west-2)
-   `--bedrock-model-id`: Pegasus model ID (default: us.twelvelabs.pegasus-1-2-v1:0)
-   `--disable-analysis`: Disable video analysis
-   `--highest-quality`: Auto-select highest quality rendition
-   `--lowest-quality`: Auto-select lowest quality rendition

**Usage:**

```bash
# Basic video analysis
python channels-subscribe/ivs-channel-subscribe-analyze-video.py --playlist-url "https://example.com/playlist.m3u8" --highest-quality

# Custom recording duration
python channels-subscribe/ivs-channel-subscribe-analyze-video.py --playlist-url "https://example.com/playlist.m3u8" --analysis-duration 15 --show-video

# Different Bedrock region
python channels-subscribe/ivs-channel-subscribe-analyze-video.py --playlist-url "https://example.com/playlist.m3u8" --bedrock-region us-west-2
```

### 3. Audio/Video Analysis (`ivs-channel-subscribe-analyze-audio-video.py`)

Advanced script that properly handles both audio and video streams using PyAV for complete media analysis.

**Key Features:**

-   Native audio/video stream handling with PyAV
-   Proper audio capture and encoding
-   MP4 encoding with H.264 video and AAC audio
-   TwelveLabs Pegasus analysis

**Arguments:**

-   `--playlist-url` (required): M3U8 playlist URL
-   `--show-video`: Display video frames in a window (requires OpenCV)
-   `--analysis-duration`: Duration in seconds for video recording before analysis (default: 10.0)
-   `--bedrock-region`: AWS region for Bedrock service (default: us-west-2)
-   `--bedrock-model-id`: Pegasus model ID (default: us.twelvelabs.pegasus-1-2-v1:0)
-   `--disable-analysis`: Disable video analysis
-   `--highest-quality`: Auto-select highest quality rendition
-   `--lowest-quality`: Auto-select lowest quality rendition

**Usage:**

```bash
# Full audio/video analysis
python channels-subscribe/ivs-channel-subscribe-analyze-audio-video.py --playlist-url "https://example.com/playlist.m3u8" --highest-quality

# Headless mode (no video display)
python channels-subscribe/ivs-channel-subscribe-analyze-audio-video.py --playlist-url "https://example.com/playlist.m3u8" --lowest-quality

# Custom analysis duration
python channels-subscribe/ivs-channel-subscribe-analyze-audio-video.py --playlist-url "https://example.com/playlist.m3u8" --analysis-duration 20
```

### 4. Real-Time Transcription (`ivs-channel-subscribe-transcribe.py`)

Live speech-to-text transcription using OpenAI Whisper with support for multiple languages.

**Key Features:**

-   Real-time audio transcription
-   Multiple Whisper models (tiny to large-v3)
-   Multi-language support with auto-detection
-   Configurable chunk duration
-   Optional video display

**Arguments:**

-   `--playlist-url` (required): M3U8 playlist URL for audio transcription
-   `--whisper-model`: Whisper model to use (tiny, base, small, medium, large, large-v2, large-v3) (default: tiny)
-   `--fp16`: Use 16-bit floating point precision for faster processing (default: true)
-   `--language`: Language for transcription (ISO 639-1 code or 'auto' for detection) (default: en)
-   `--chunk-duration`: Duration in seconds for each audio chunk to transcribe (default: 5)
-   `--show-video`: Display video frames in a window (requires OpenCV)
-   `--publish-transcript-as-timed-metadata`: Publish transcripts as IVS timed metadata to the channel
-   `--highest-quality`: Auto-select highest quality rendition
-   `--lowest-quality`: Auto-select lowest quality rendition

**Usage:**

```bash
# Basic English transcription
python channels-subscribe/ivs-channel-subscribe-transcribe.py --playlist-url "https://example.com/playlist.m3u8" --highest-quality

# Spanish transcription with better model
python channels-subscribe/ivs-channel-subscribe-transcribe.py --playlist-url "https://example.com/playlist.m3u8" --language es --whisper-model base

# Auto-detect language with video
python channels-subscribe/ivs-channel-subscribe-transcribe.py --playlist-url "https://example.com/playlist.m3u8" --language auto --show-video --whisper-model small

# Fast transcription with tiny model
python channels-subscribe/ivs-channel-subscribe-transcribe.py --playlist-url "https://example.com/playlist.m3u8" --whisper-model tiny --chunk-duration 3

# Publish transcripts as timed metadata to the IVS channel
python channels-subscribe/ivs-channel-subscribe-transcribe.py --playlist-url "https://example.com/playlist.m3u8" --highest-quality --publish-transcript-as-timed-metadata
```

## 📡 IVS Metadata Publisher Module

The `channels-subscribe/ivs_metadata_publisher.py` module provides a reusable way to publish timed metadata to Amazon IVS channels.

**Key Features:**

-   Automatic channel ARN extraction from M3U8 playlist URLs
-   Rate limiting compliance (5 RPS per channel, 155 RPS per account)
-   Automatic payload splitting for messages > 1KB
-   Graceful error handling and retry logic
-   Support for any type of metadata (transcripts, events, etc.)

**Usage:**

```python
from channels_subscribe.ivs_metadata_publisher import IVSMetadataPublisher

# Initialize publisher
publisher = IVSMetadataPublisher(region="us-east-1")

# Publish transcript
await publisher.publish_transcript(playlist_url, "Hello, this is a transcript")

# Publish custom metadata
channel_arn = publisher.extract_channel_arn_from_playlist_url(playlist_url)
await publisher.publish_metadata(channel_arn, "Custom metadata", "event")
```

**Rate Limits:**

-   Maximum 5 requests per second per channel
-   Maximum 155 requests per second per account
-   Maximum 1KB payload per request (automatically split if larger)

## 🌍 Supported Languages (Transcription)

The transcription script supports 99+ languages including:

-   **English** (`en`) - Default
-   **Spanish** (`es`)
-   **French** (`fr`)
-   **German** (`de`)
-   **Italian** (`it`)
-   **Portuguese** (`pt`)
-   **Russian** (`ru`)
-   **Japanese** (`ja`)
-   **Chinese** (`zh`)
-   **Korean** (`ko`)
-   **Arabic** (`ar`)
-   **Hindi** (`hi`)
-   **Auto-detect** (`auto`)

## 🎥 Whisper Model Comparison

| Model      | Size    | Speed   | Accuracy  | Use Case               |
| ---------- | ------- | ------- | --------- | ---------------------- |
| `tiny`     | 39 MB   | Fastest | Good      | Real-time, low latency |
| `base`     | 74 MB   | Fast    | Better    | Balanced performance   |
| `small`    | 244 MB  | Medium  | Good      | General purpose        |
| `medium`   | 769 MB  | Slow    | Very Good | High accuracy needed   |
| `large`    | 1550 MB | Slowest | Best      | Maximum accuracy       |
| `large-v2` | 1550 MB | Slowest | Best      | Latest improvements    |
| `large-v3` | 1550 MB | Slowest | Best      | Most recent model      |

## 🔍 Troubleshooting

### Common Issues

#### 1. "No audio stream found"

```bash
# Check if the M3U8 stream contains audio
ffprobe "https://your-stream-url.m3u8"

# Try a different rendition
python script.py --playlist-url "url" --lowest-quality
```

#### 2. "Unable to open video stream"

```bash
# Verify the M3U8 URL is accessible
curl -I "https://your-stream-url.m3u8"

# Check network connectivity and try again
python script.py --playlist-url "url" --highest-quality
```

#### 3. "AWS credentials not found"

```bash
# Configure AWS credentials
aws configure

# Or set environment variables
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
export AWS_DEFAULT_REGION=us-east-1
```

#### 4. "OpenCV not available"

```bash
# Install OpenCV for video display
pip install opencv-python

# Or run without video display
python script.py --playlist-url "url" # (remove --show-video)
```

#### 5. "Whisper model download fails"

```bash
# Clear Whisper cache and retry
rm -rf ~/.cache/whisper/
python ivs-channel-subscribe-transcribe.py --playlist-url "url" --whisper-model tiny
```

#### 6. "Memory issues with large Whisper models"

```bash
# Use smaller model or enable FP16
python ivs-channel-subscribe-transcribe.py --playlist-url "url" --whisper-model tiny --fp16 true

# Or increase chunk duration to process less frequently
python ivs-channel-subscribe-transcribe.py --playlist-url "url" --chunk-duration 10
```

### Performance Tips

1. **For real-time transcription:**

    - Use `--whisper-model tiny` or `--whisper-model base`
    - Enable FP16: `--fp16 true`
    - Use shorter chunks: `--chunk-duration 3`

2. **For high accuracy transcription:**

    - Use `--whisper-model large-v3`
    - Increase chunk duration: `--chunk-duration 10`
    - Specify language: `--language en` (faster than auto-detect)

3. **For video analysis:**

    - Use `--lowest-quality` for faster processing
    - Adjust `--analysis-duration` based on content complexity
    - Run without `--show-video` for headless operation

4. **For frame analysis:**
    - Increase `--analysis-interval` for less frequent analysis
    - Use `--lowest-quality` for faster frame processing

### Debug Mode

Enable debug logging for troubleshooting:

```python
# Add to the beginning of any script
import logging
logging.getLogger().setLevel(logging.DEBUG)
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgments

-   Amazon Web Services for IVS and Bedrock services
-   OpenAI for Whisper speech recognition
-   TwelveLabs for Pegasus video analysis
-   PyAV team for FFmpeg Python bindings (used in analysis scripts)
-   OpenCV community for computer vision tools

## 📞 Support

For issues and questions:

1. Check the troubleshooting section above
2. Review the script help: `python script.py --help`
3. Open an issue on GitHub with:
    - Script name and version
    - Full error message
    - Command used
    - System information (OS, Python version)

---

**Happy streaming and analyzing!** 🎉
