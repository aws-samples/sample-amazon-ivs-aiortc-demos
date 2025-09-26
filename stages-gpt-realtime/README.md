# IVS Stages OpenAI Real-time API Integration

This module provides speech-to-speech conversation capabilities using OpenAI's `gpt-realtime` API with Amazon IVS stages.

## Features

- Subscribe to IVS stage participants for audio input
- Process audio through OpenAI's `gpt-realtime` API for speech-to-speech conversations
- Publish AI responses back to the IVS stage
- WebSocket-based real-time communication with OpenAI
- **Vision capabilities**: AI-powered video frame analysis using Amazon Bedrock Claude models
- **Function calling**: for vision and other capabilities
- Real-time audio visualization with OpenAI branding
- Voice activity detection and interruption handling
- Multiple voice options (alloy, echo, fable, onyx, nova, shimmer)

## Requirements

- OpenAI API key with real-time API access
- IVS stage token with both subscribe and publish capabilities
- AWS credentials with Bedrock access (for vision capabilities)
- Python 3.8+

## Usage

```bash
python ivs-stage-gpt-realtime.py --token <IVS_TOKEN> --subscribe-to <PARTICIPANT_ID> --openai-key <OPENAI_API_KEY>
```

## Arguments

- `--token`: IVS stage participant token (required)
- `--subscribe-to`: Participant ID to subscribe to (required)
- `--openai-key`: OpenAI API key (optional, uses OPENAI_API_KEY env var if not provided)
- `--model`: OpenAI model to use (default: gpt-realtime)
- `--voice`: Voice to use for responses (default: cedar. options: alloy, ash, ballad, coral, echo, sage, shimmer, verse, marin, cedar)
- `--disable-frame-analysis`: Disable video frame analysis (default: enabled)
- `--bedrock-region`: AWS region for Bedrock service (default: us-east-1)
- `--bedrock-model-id`: Bedrock model ID for frame analysis (default: us.anthropic.claude-sonnet-4-20250514-v1:0)
- `--vad-mode`: VAD mode - server_vad (silence-based) or semantic_vad (context-aware, default: server_vad)
- `--vad-threshold`: VAD sensitivity threshold for server_vad, 0.0-1.0, lower = more sensitive (default: 0.5)
- `--vad-prefix-padding-ms`: Audio padding before speech detection for server_vad in milliseconds (default: 300)
- `--vad-silence-duration-ms`: Silence duration to end speech detection for server_vad in milliseconds (default: 500)
- `--vad-eagerness`: Eagerness for semantic_vad - low, medium, high, auto (default: medium)
- `--ice-timeout`: ICE gathering timeout in seconds (default: 1)

## Environment Variables

- `OPENAI_API_KEY`: OpenAI API key (used if --openai-key not provided)
- `AWS_REGION`: AWS region for Bedrock service
- `AWS_ACCESS_KEY_ID`: AWS access key ID
- `AWS_SECRET_ACCESS_KEY`: AWS secret access key

## Vision Capabilities

The OpenAI agent includes vision capabilities that allow it to see and describe what's happening in the video stream. When users ask questions like:

- "What do you see?"
- "How do I look?"
- "What's in my background?"
- "Can you see me?"

The agent will automatically use the `analyze_frame` function to capture and analyze the current video frame using Amazon Bedrock Claude models, then provide a detailed description of what it sees.

### Vision Features

- **Real-time frame analysis**: Analyzes current video frames on demand
- **Natural conversation**: Refers to the user as "you" in a conversational manner
- **Detailed descriptions**: Provides comprehensive analysis of people, objects, activities, and environment
- **Multiple Claude models**: Supports various Claude models for different use cases and cost optimization

## Voice Activity Detection (VAD)

The OpenAI agent supports two VAD modes to handle different audio environments and conversation styles:

### VAD Modes

#### Server VAD (Default)

Silence-based detection that chunks audio based on periods of silence.

**Parameters:**

- **Threshold** (`--vad-threshold`): Controls sensitivity (0.0-1.0)

  - Lower = more sensitive (picks up quieter speech)
  - Higher = less sensitive (ignores background noise)
  - Default: 0.5

- **Prefix Padding** (`--vad-prefix-padding-ms`): Audio captured before speech detection

  - Ensures beginning of speech isn't cut off
  - Default: 300ms

- **Silence Duration** (`--vad-silence-duration-ms`): Silence needed to end speech detection
  - Shorter = more responsive but may cut off pauses
  - Longer = less responsive but handles natural pauses better
  - Default: 500ms

#### Semantic VAD

Context-aware detection that uses semantic understanding to determine when the user has finished speaking.

**Parameters:**

- **Eagerness** (`--vad-eagerness`): Controls responsiveness
  - `low`: Patient, waits for user to finish completely
  - `medium`: Balanced approach (default)
  - `high`: Responsive, chunks audio quickly
  - `auto`: Same as medium

### Usage Examples

**Server VAD for Noisy Environment:**

```bash
--vad-mode server_vad --vad-threshold 0.7 --vad-silence-duration-ms 600
```

**Server VAD for Quiet Environment:**

```bash
--vad-mode server_vad --vad-threshold 0.3 --vad-silence-duration-ms 300
```

**Semantic VAD for Natural Conversations:**

```bash
--vad-mode semantic_vad --vad-eagerness low
```

**Semantic VAD for Responsive Interactions:**

```bash
--vad-mode semantic_vad --vad-eagerness high
```

### Recommendations

- **Use Server VAD** for predictable environments where silence-based detection works well
- **Use Semantic VAD** for natural conversations where users may pause or trail off with "umm..."
- **Start with defaults** and adjust based on your specific environment and use case

## Assistant Management

For automated management of multiple OpenAI assistant instances via WebSocket integration with IVS Chat, see:

**[IVS Stage OpenAI Assistant Manager Documentation](MANAGING_OPENAI_ASSISTANT_DEMO.md)**

This companion tool allows you to dynamically launch and manage multiple OpenAI real-time instances based on chat messages, perfect for scaling AI assistants across multiple participants with full configuration control.

### Important Limitation: Semantic VAD and Transcriptions

⚠️ **Known Issue**: When using `semantic_vad` mode, user input transcriptions may not be generated or published as SEI metadata, regardless of the `eagerness` setting. This appears to be a platform limitation where OpenAI's semantic VAD does not consistently trigger the `conversation.item.input_audio_transcription.completed` events.

**Impact:**

- User speech is detected and processed for AI responses
- AI responses are transcribed and published as SEI metadata
- **User transcriptions are missing** from logs and SEI metadata

**Workaround:**
If you need reliable user transcriptions for logging, SEI metadata, or debugging purposes, use `server_vad` mode instead:

```bash
# Recommended for reliable transcriptions
--vad-mode server_vad --vad-threshold 0.5 --vad-silence-duration-ms 400
```

**When to use each mode:**

- **Server VAD**: Choose this if you need user transcriptions or predictable behavior
- **Semantic VAD**: Choose this for the most natural conversation flow, but accept that user transcriptions may be missing
