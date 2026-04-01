# IVS Stage Deepgram Voice Agent

A conversational AI voice agent powered by [Deepgram's Voice Agent API](https://developers.deepgram.com/docs/voice-agent), integrated with Amazon IVS Real-Time Stages. Unlike the Nova S2S and GPT Real-time demos which use separate STT and TTS pipelines, Deepgram's Voice Agent handles the entire voice conversation — speech-to-text, LLM reasoning, and text-to-speech — through a single WebSocket connection.

## Overview

This sub-project contains two main scripts:

- **`ivs-stage-deepgram-agent.py`** — The voice agent itself. Subscribes to a participant's audio on an IVS stage, pipes it through Deepgram's Voice Agent API, and publishes the agent's audio response and visual feedback back to the stage.
- **`ivs-stage-deepgram-agent-manager.py`** — A WebSocket-based manager that listens on an IVS Chat room and dynamically spawns agent instances based on chat messages.

Supporting module:

- **`deepgram_agent_manager.py`** — Manages the Deepgram Voice Agent WebSocket connection, settings, audio streaming, event handling, and SEI transcript publishing.

## Architecture

```
┌─────────────────┐     WebRTC (WHEP)       ┌──────────────────────┐
│  IVS Stage      │ ──── subscribe ──────▶  │  ivs-stage-deepgram  │
│  (Participant)  │                         │  -agent.py           │
│                 │ ◀──── publish ───────── │                      │
└─────────────────┘     WebRTC (WHIP)       └──────────┬───────────┘
                                                       │
                                            16kHz PCM  │  24kHz PCM
                                            audio in   │  audio out
                                                       ▼
                                           ┌──────────────────────┐
                                           │  Deepgram Voice      │
                                           │  Agent API           │
                                           │  (wss://agent.       │
                                           │   deepgram.com)      │
                                           │                      │
                                           │  STT: Nova-3         │
                                           │  LLM: Configurable   │
                                           │  TTS: Aura 2         │
                                           └──────────────────────┘
```

## Features

- **Single WebSocket** for the full STT → LLM → TTS pipeline
- **Bidirectional audio streaming** with IVS Real-Time Stages via WebRTC
- **Configurable LLM provider** — OpenAI, Anthropic, or Groq
- **50+ TTS voices** via Deepgram Aura 2 (English and Spanish)
- **Custom system prompts and greetings** — define the agent's personality
- **Video frame analysis** — vision via Bedrock Claude tool calling (ask "what do you see?")
- **Automatic barge-in / interruption handling** — agent stops speaking when the user talks
- **Real-time audio visualization** — throbbing circle with thinking state animation
- **SEI transcript publishing** — user and agent transcripts embedded in the H.264 video stream
- **Conversation transcript logging** — all turns printed to console
- **No AWS dependency** for the AI pipeline (only IVS stage access requires AWS)

## How It Compares

| Feature               | Nova S2S              | GPT Real-time     | Deepgram Agent                         |
| --------------------- | --------------------- | ----------------- | -------------------------------------- |
| STT                   | Nova Sonic (built-in) | OpenAI (built-in) | Deepgram Nova-3                        |
| LLM                   | Nova Sonic (built-in) | GPT-4o (built-in) | Configurable (OpenAI, Anthropic, Groq) |
| TTS                   | Nova Sonic (built-in) | OpenAI (built-in) | Deepgram Aura 2 (50+ voices)           |
| Vision                | Bedrock Claude (tool) | OpenAI native     | Bedrock Claude (tool)                  |
| WebSocket Connections | 1 (Bedrock)           | 1 (OpenAI)        | 1 (Deepgram)                           |
| AWS Dependency        | Yes (Bedrock)         | No                | Optional (Bedrock for vision only)     |
| LLM Flexibility       | Fixed                 | Fixed             | Swappable at launch                    |
| SEI Transcripts       | Yes                   | Yes               | Yes                                    |
| Visual Feedback       | Yes                   | Yes               | Yes                                    |

## Prerequisites

- Python 3.8 or higher
- Deepgram API key — sign up at [deepgram.com](https://deepgram.com/) (free tier available)
- AWS CLI configured with IVS Real-Time permissions
- IVS stage participant token with both PUBLISH and SUBSCRIBE capabilities
- FFmpeg and PortAudio (same as other demos)

### Required AWS Permissions

- `ivs-realtime:CreateParticipantToken` (only if using the assistant manager)

### Required API Keys

| Key                               | Required               | Source                                        |
| --------------------------------- | ---------------------- | --------------------------------------------- |
| `DEEPGRAM_API_KEY`                | Yes                    | [deepgram.com](https://console.deepgram.com/) |
| OpenAI / Anthropic / Groq API key | Configured in Deepgram | Set in your Deepgram project settings         |

> **Note**: The LLM API key (OpenAI, Anthropic, etc.) is configured in your Deepgram project, not passed directly to this script. Deepgram proxies the LLM calls for you.

## Installation

```bash
# From the project root
pip install -r requirements.txt

# Set your Deepgram API key
export DEEPGRAM_API_KEY="your-deepgram-api-key"
```

## Usage

### Basic Conversation

```bash
cd stages-deepgram-agent

python ivs-stage-deepgram-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123"
```

### Custom Voice and LLM

```bash
python ivs-stage-deepgram-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --voice "aura-2-orion-en" \
  --think-model "gpt-4o"
```

### Custom Personality

```bash
python ivs-stage-deepgram-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --prompt "You are a pirate captain. Respond in pirate speak. Keep it fun." \
  --greeting "Ahoy matey! What can I do for ye?"
```

### With Anthropic Claude

```bash
python ivs-stage-deepgram-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --think-provider "anthropic" \
  --think-model "claude-sonnet-4-6"
```

### With Groq (Ultra-Low Latency)

```bash
python ivs-stage-deepgram-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --think-provider "groq" \
  --think-model "llama-3.3-70b-versatile"
```

## Command-Line Arguments

| Argument                   | Required | Default                            | Description                                                   |
| -------------------------- | -------- | ---------------------------------- | ------------------------------------------------------------- |
| `--token`                  | Yes      | —                                  | IVS participant token with PUBLISH and SUBSCRIBE capabilities |
| `--subscribe-to`           | Yes      | —                                  | Participant ID to subscribe to                                |
| `--deepgram-api-key`       | No       | `DEEPGRAM_API_KEY` env var         | Deepgram API key                                              |
| `--voice`                  | No       | `aura-2-asteria-en`                | Deepgram TTS voice model                                      |
| `--think-model`            | No       | `gpt-4o-mini`                      | LLM model for agent reasoning                                 |
| `--think-provider`         | No       | `open_ai`                          | LLM provider: `open_ai`, `anthropic`, `groq`                  |
| `--prompt`                 | No       | _(friendly assistant)_             | System prompt for the agent personality                       |
| `--greeting`               | No       | `Hello! How can I help you today?` | Greeting spoken when the session starts                       |
| `--language`               | No       | `en`                               | Language code                                                 |
| `--ice-timeout`            | No       | `1`                                | ICE gathering timeout in seconds                              |
| `--disable-frame-analysis` | No       | _(enabled)_                        | Disable video frame analysis via Bedrock Claude               |
| `--bedrock-model-id`       | No       | `us.anthropic.claude-sonnet-4-6`   | Bedrock model for frame analysis                              |
| `--bedrock-region`         | No       | `us-east-1`                        | AWS region for Bedrock                                        |

## Vision (Frame Analysis)

Deepgram's Voice Agent API doesn't have native vision, but this demo adds it using Deepgram's client-side function calling and Amazon Bedrock Claude. When the user asks the agent to "look at something" or "describe what you see", the agent calls the `analyze_frame` tool, which:

1. Captures the latest video frame from the participant's WebRTC stream
2. Converts it to JPEG and base64-encodes it
3. Sends it to Bedrock Claude for analysis
4. Returns the description to the Deepgram agent, which speaks it naturally

This is the same approach used by the Nova S2S demo — the agent doesn't "see" natively, but the tool gives it vision on demand.

### Usage with Vision

```bash
# Default: vision enabled with Claude Sonnet 4
python ivs-stage-deepgram-agent.py \
  --token "eyJ..." \
  --subscribe-to "participant123"

# With a different Claude model
python ivs-stage-deepgram-agent.py \
  --token "eyJ..." \
  --subscribe-to "participant123" \
  --bedrock-model-id "anthropic.claude-3-5-haiku-20241022-v1:0"

# Disable vision entirely (no AWS dependency)
python ivs-stage-deepgram-agent.py \
  --token "eyJ..." \
  --subscribe-to "participant123" \
  --disable-frame-analysis
```

### How It Works Under the Hood

The `analyze_frame` function is registered as a `client_side: true` tool in the Deepgram agent settings. When the LLM decides to call it:

1. Deepgram sends a `FunctionCallRequest` with `client_side: true`
2. The `DeepgramAgentManager` intercepts it and calls `_handle_analyze_frame`
3. The current video frame is converted to base64 JPEG and sent to Bedrock Claude
4. The Claude response is sent back as a `FunctionCallResponse`
5. Deepgram feeds the result to the LLM, which formulates a spoken response

### Required AWS Permissions for Vision

- `bedrock:InvokeModel` — for Claude frame analysis

## Available Voices

Deepgram Aura 2 offers 50+ voices. Some popular options:

### English Voices

| Voice   | ID                  | Character                      |
| ------- | ------------------- | ------------------------------ |
| Asteria | `aura-2-asteria-en` | Warm, conversational (default) |
| Orion   | `aura-2-orion-en`   | Clear, professional            |
| Luna    | `aura-2-luna-en`    | Soft, friendly                 |
| Atlas   | `aura-2-atlas-en`   | Deep, authoritative            |
| Helios  | `aura-2-helios-en`  | Bright, energetic              |
| Aurora  | `aura-2-aurora-en`  | Smooth, calming                |
| Apollo  | `aura-2-apollo-en`  | Confident, articulate          |
| Athena  | `aura-2-athena-en`  | Wise, measured                 |

### Spanish Voices

| Voice   | ID                  | Character          |
| ------- | ------------------- | ------------------ |
| Sirio   | `aura-2-sirio-es`   | Male, natural      |
| Carina  | `aura-2-carina-es`  | Female, warm       |
| Celeste | `aura-2-celeste-es` | Female, bright     |
| Alvaro  | `aura-2-alvaro-es`  | Male, professional |

See the [Deepgram TTS docs](https://developers.deepgram.com/docs/tts-models) for the full list.

## SEI Transcript Publishing

Both user and agent transcripts are automatically embedded in the H.264 video stream as SEI (Supplemental Enhancement Information) NAL units. This enables synchronized captions on the client side.

### SEI Message Format

```json
{
  "type": "deepgram_agent_text",
  "role": "user",
  "content": "What's the weather like?",
  "timestamp": 1711929600.123
}
```

```json
{
  "type": "deepgram_agent_text",
  "role": "assistant",
  "content": "I'd be happy to help with that!",
  "timestamp": 1711929601.456
}
```

### Client-Side Extraction

Use the `SeiSubscriber` from the `stages_sei` module to extract messages:

```python
from stages_sei import SeiSubscriber

def on_sei_message(message):
    data = message.to_dict()
    payload = data["payload"]
    if payload.get("type") == "deepgram_agent_text":
        role = payload["role"]      # "user" or "assistant"
        content = payload["content"]
        print(f"[{role}] {content}")

subscriber = SeiSubscriber(message_callback=on_sei_message)
```

## Key Components

### DeepgramAgentManager (`deepgram_agent_manager.py`)

Manages the Deepgram Voice Agent WebSocket lifecycle:

- **Settings**: Configures STT (Nova-3), LLM (configurable provider/model), and TTS (Aura 2 voice) via a single `Settings` message
- **Audio Input**: Receives 16kHz mono PCM from the WebRTC subscribe track and sends to Deepgram
- **Audio Output**: Receives TTS audio from Deepgram and feeds it to the `AgentAudioTrack` for WebRTC publishing
- **Events**: Handles `ConversationText`, `UserStartedSpeaking`, `AgentThinking`, `AgentStartedSpeaking`, `AgentAudioDone`, errors, and warnings
- **Barge-in**: On `UserStartedSpeaking`, clears the audio buffer so the agent stops immediately
- **SEI Publishing**: Publishes user and agent transcripts as SEI metadata on every `ConversationText` event

### AgentAudioTrack (reused from `stages-nova-s2s`)

Custom `AudioStreamTrack` that buffers Deepgram's TTS audio and streams it to IVS via WebRTC:

- 24kHz, 16-bit, mono output (matches Deepgram TTS output)
- 20ms chunks (480 samples) at 50 FPS
- Batch buffering (4 chunks / 80ms) for smooth playback
- RMS-based audio level tracking for visual feedback

### AgentVideoTrack (reused from `stages-nova-s2s`)

Custom `VideoStreamTrack` that generates visual feedback:

- **Speaking**: Throbbing blue circle that reacts to audio levels
- **Thinking**: Pulsing orange circle with spinning donut animation
- **Idle**: Static blue circle

---

## Assistant Manager

The `ivs-stage-deepgram-agent-manager.py` script enables dynamic, multi-instance management of Deepgram Voice Agents via IVS Chat WebSocket messages.

### How It Works

1. Connects to an IVS Chat room via WebSocket
2. Listens for `LAUNCH_ASSISTANT` messages
3. Generates a stage participant token for the target stage
4. Spawns `ivs-stage-deepgram-agent.py` as a subprocess with the provided configuration
5. Monitors the subprocess and cleans up when it exits
6. Sends error responses back through IVS Chat if launch fails

### Manager Usage

```bash
python ivs-stage-deepgram-agent-manager.py \
  --chat-room-arn "arn:aws:ivschat:us-east-1:123456789012:room/abcdefgh" \
  --ws-endpoint "wss://edge.ivschat.us-east-1.amazonaws.com" \
  --verbose
```

### Manager Command-Line Arguments

| Argument             | Required | Default                    | Description                                |
| -------------------- | -------- | -------------------------- | ------------------------------------------ |
| `--chat-room-arn`    | Yes      | —                          | IVS Chat room ARN                          |
| `--ws-endpoint`      | Yes      | —                          | WebSocket endpoint URL                     |
| `--deepgram-api-key` | No       | `DEEPGRAM_API_KEY` env var | Deepgram API key                           |
| `--max-instances`    | No       | `5`                        | Maximum concurrent agent instances         |
| `--region`           | No       | `us-east-1`                | AWS region                                 |
| `--verbose`          | No       | `false`                    | Stream output from spawned agent instances |

### Required AWS Permissions (Manager)

- `ivschat:CreateChatToken` — Generate chat tokens for WebSocket connection
- `ivs-realtime:CreateParticipantToken` — Generate stage participant tokens dynamically

### Chat Message Format

Send this JSON payload as an IVS Chat message to launch an agent:

```json
{
  "action": "LAUNCH_ASSISTANT",
  "stageArn": "arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh",
  "participantId": "participant-123",
  "voice": "aura-2-asteria-en",
  "thinkProvider": "open_ai",
  "thinkModel": "gpt-4o-mini",
  "prompt": "You are a friendly assistant.",
  "greeting": "Hello! How can I help you today?",
  "language": "en",
  "iceTimeout": 1,
  "disableFrameAnalysis": false,
  "bedrockModelId": "us.anthropic.claude-sonnet-4-6",
  "bedrockRegion": "us-east-1"
}
```

### Message Fields

| Field                  | Required | Default                          | Description                      |
| ---------------------- | -------- | -------------------------------- | -------------------------------- |
| `action`               | Yes      | —                                | Must be `"LAUNCH_ASSISTANT"`     |
| `stageArn`             | Yes      | —                                | ARN of the IVS Stage to join     |
| `participantId`        | Yes      | —                                | Participant ID to subscribe to   |
| `voice`                | No       | `aura-2-asteria-en`              | Deepgram TTS voice               |
| `thinkProvider`        | No       | `open_ai`                        | LLM provider                     |
| `thinkModel`           | No       | `gpt-4o-mini`                    | LLM model                        |
| `prompt`               | No       | _(default)_                      | System prompt                    |
| `greeting`             | No       | _(default)_                      | Greeting message                 |
| `language`             | No       | `en`                             | Language code                    |
| `iceTimeout`           | No       | `1`                              | ICE gathering timeout in seconds |
| `disableFrameAnalysis` | No       | `false`                          | Disable video frame analysis     |
| `bedrockModelId`       | No       | `us.anthropic.claude-sonnet-4-6` | Bedrock model for frame analysis |
| `bedrockRegion`        | No       | `us-east-1`                      | AWS region for Bedrock           |

### Frontend Integration

```javascript
// Launch a Deepgram agent from your web app
const launchMessage = {
  action: "LAUNCH_ASSISTANT",
  stageArn: stageArn,
  participantId: localParticipantId,
  voice: "aura-2-orion-en",
  thinkModel: "gpt-4o",
  prompt: "You are a helpful coding assistant.",
  greeting: "Hey! Ready to write some code?",
};

chatConnection.send(
  JSON.stringify({
    Action: "SEND_MESSAGE",
    Content: JSON.stringify(launchMessage),
  }),
);
```

### Error Responses

The manager sends error responses back through IVS Chat when launch fails:

```json
{
  "error": "MAX_INSTANCES_REACHED",
  "stageId": "abcdefgh",
  "participantId": "participant-123",
  "message": "Failed to launch Deepgram Agent for participant participant-123"
}
```

| Error Code                | Description                                |
| ------------------------- | ------------------------------------------ |
| `MAX_INSTANCES_REACHED`   | Maximum concurrent instances exceeded      |
| `INSTANCE_ALREADY_EXISTS` | Agent already running for this participant |
| `LAUNCH_FAILED`           | Subprocess failed to start                 |

### Verbose Output

With `--verbose`, the manager streams child process output with prefixed identifiers:

```
[abcdefgh::participant-123] 🎬 Starting IVS Stage + Deepgram Voice Agent
[abcdefgh::participant-123] 🗣️  Voice: aura-2-orion-en
[abcdefgh::participant-123] 🧠 Think: open_ai/gpt-4o
[abcdefgh::participant-123] [🤖 AGENT] Hey! Ready to write some code?
[abcdefgh::participant-123] [🗣️  USER] Can you help me with Python?
```

---

## Troubleshooting

### Agent Connection Issues

- Verify your Deepgram API key is valid and has sufficient credits
- Check that the LLM provider is configured in your Deepgram project settings
- Ensure network connectivity to `agent.deepgram.com`

### No Audio From Agent

- Confirm the IVS token has both PUBLISH and SUBSCRIBE capabilities
- Check that the participant ID matches an active participant on the stage
- Look for "First audio frame sent to Deepgram" in the logs — if missing, the WebRTC subscribe connection didn't establish

### Choppy Agent Audio

- The agent outputs at 24kHz to match the `AgentAudioTrack` expectations
- If you hear choppy audio, check that `OUTPUT_SAMPLE_RATE` is `24000` in `deepgram_agent_manager.py`

### CLIENT_MESSAGE_TIMEOUT Error

- This means Deepgram didn't receive audio within its timeout window
- Check that the WebRTC subscribe connection reaches `connected` state
- Look for "Audio frames sent" log messages to confirm audio is flowing

### SEI Messages Not Appearing

- Ensure `stages_sei.h264_sei_patch` is imported before `aiortc` in the main script
- The SEI publisher must be set globally via `set_global_sei_publisher` before any video encoding starts

## Related Documentation

- [Deepgram Voice Agent Docs](https://developers.deepgram.com/docs/voice-agent)
- [Deepgram TTS Models](https://developers.deepgram.com/docs/tts-models)
- [Deepgram API Reference](https://developers.deepgram.com/reference/deepgram-api-overview)
- [IVS Real-Time Streaming](https://docs.aws.amazon.com/ivs/latest/RealTimeUserGuide/)
- [IVS Chat User Guide](https://docs.aws.amazon.com/ivs/latest/ChatUserGuide/)
- [SEI Publishing System](../stages_sei/SEI.md)
