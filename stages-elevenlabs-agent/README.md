# IVS Stage ElevenLabs Conversational AI Agent

A conversational AI voice agent powered by [ElevenLabs' Conversational AI API](https://elevenlabs.io/docs/conversational-ai/overview), integrated with Amazon IVS Real-Time Stages. ElevenLabs' Conversational AI handles the entire voice conversation — speech-to-text, LLM reasoning, and text-to-speech — through a single WebSocket connection, with support for configurable LLMs, thousands of voices (plus custom voice cloning), and client-side tool calling.

## Overview

This sub-project contains three main scripts:

- **`ivs-stage-elevenlabs-agent.py`** — The voice agent itself. Subscribes to a participant's audio on an IVS stage, pipes it through ElevenLabs' Conversational AI API, and publishes the agent's audio response and visual feedback back to the stage.
- **`ivs-stage-elevenlabs-group-agent.py`** — A passive multi-participant voice agent that transcribes all speakers using ElevenLabs Scribe v2 Realtime and responds only when invoked by a configurable wake word.
- **`ivs-stage-elevenlabs-agent-manager.py`** — A WebSocket-based manager that listens on an IVS Chat room and dynamically spawns agent instances based on chat messages.

Supporting module:

- **`elevenlabs_agent_manager.py`** — Manages the ElevenLabs Conversational AI WebSocket connection, agent creation/deletion, audio streaming, tool call handling, and SEI transcript publishing.

## Architecture

```
┌─────────────────┐     WebRTC (WHEP)       ┌──────────────────────────┐
│  IVS Stage      │ ──── subscribe ──────▶  │  ivs-stage-elevenlabs    │
│  (Participant)  │                         │  -agent.py               │
│                 │ ◀──── publish ───────── │                          │
└─────────────────┘     WebRTC (WHIP)       └──────────┬───────────────┘
                                                       │
                                            16kHz PCM  │  24kHz PCM
                                            audio in   │  audio out
                                                       ▼
                                           ┌──────────────────────────┐
                                           │  ElevenLabs              │
                                           │  Conversational AI       │
                                           │  (wss://api.elevenlabs   │
                                           │   .io/v1/convai/         │
                                           │   conversation)          │
                                           │                          │
                                           │  STT: Built-in           │
                                           │  LLM: Configurable       │
                                           │  TTS: Thousands of    │
                                           └──────────────────────────┘
```

## Features

- **Single WebSocket** for the full STT → LLM → TTS pipeline
- **Bidirectional audio streaming** with IVS Real-Time Stages via WebRTC
- **Configurable LLM** — OpenAI, Anthropic, Google, or ElevenLabs hosted models
- **Thousands of TTS voices** via ElevenLabs Voice Library (multilingual, plus custom voice cloning)
- **Custom system prompts and greetings** — define the agent's personality
- **Video frame analysis** — vision via Bedrock Claude client tool calling (ask "what do you see?")
- **Automatic barge-in / interruption handling** — agent stops speaking when the user talks
- **Real-time audio visualization** — throbbing circle with thinking state animation
- **SEI transcript publishing** — user and agent transcripts embedded in the H.264 video stream
- **Conversation transcript logging** — all turns printed to console
- **Auto-create or bring your own agent** — pass `--agent-id` or let the script create and auto-delete one
- **No AWS dependency** for the AI pipeline (only IVS stage access and optional vision require AWS)

## How It Compares

| Feature               | Nova S2S              | GPT Real-time     | Deepgram Agent                         | ElevenLabs Agent                               |
| --------------------- | --------------------- | ----------------- | -------------------------------------- | ---------------------------------------------- |
| STT                   | Nova Sonic (built-in) | OpenAI (built-in) | Deepgram Nova-3                        | ElevenLabs (built-in)                          |
| LLM                   | Nova Sonic (built-in) | GPT-4o (built-in) | Configurable (OpenAI, Anthropic, Groq) | Configurable (OpenAI, Anthropic, Google, more) |
| TTS                   | Nova Sonic (built-in) | OpenAI (built-in) | Deepgram Aura 2 (50+ voices)           | ElevenLabs (thousands of voices)               |
| Vision                | Bedrock Claude (tool) | OpenAI native     | Bedrock Claude (tool)                  | Bedrock Claude (client tool)                   |
| WebSocket Connections | 1 (Bedrock)           | 1 (OpenAI)        | 1 (Deepgram)                           | 1 (ElevenLabs)                                 |
| AWS Dependency        | Yes (Bedrock)         | No                | Optional (Bedrock for vision only)     | Optional (Bedrock for vision only)             |
| LLM Flexibility       | Fixed                 | Fixed             | Swappable at launch                    | Swappable at launch                            |
| SEI Transcripts       | Yes                   | Yes               | Yes                                    | Yes                                            |
| Visual Feedback       | Yes                   | Yes               | Yes                                    | Yes                                            |

## Prerequisites

- Python 3.8 or higher
- ElevenLabs API key — sign up at [elevenlabs.io](https://elevenlabs.io/)
- AWS CLI configured with IVS Real-Time permissions
- IVS stage participant token with both PUBLISH and SUBSCRIBE capabilities
- FFmpeg and PortAudio (same as other demos)

### Required AWS Permissions

- `ivs-realtime:CreateParticipantToken` (only if using the assistant manager)

### Required API Keys

| Key                  | Required | Source                                      |
| -------------------- | -------- | ------------------------------------------- |
| `ELEVENLABS_API_KEY` | Yes      | [elevenlabs.io](https://elevenlabs.io/app/) |

> **Note**: The LLM is configured when creating the ElevenLabs agent. ElevenLabs proxies the LLM calls — you configure the model name and ElevenLabs handles the rest.

## Installation

```bash
# From the project root
pip install -r requirements.txt

# Set your ElevenLabs API key
export ELEVENLABS_API_KEY="your-elevenlabs-api-key"
```

## Usage

### Basic Conversation

```bash
cd stages-elevenlabs-agent

python ivs-stage-elevenlabs-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123"
```

### Custom Voice

```bash
python ivs-stage-elevenlabs-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --voice-id "EXAVITQu4vr4xnSDxMaL"
```

### Custom Personality

```bash
python ivs-stage-elevenlabs-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --prompt "You are a pirate captain. Respond in pirate speak. Keep it fun." \
  --greeting "Ahoy matey! What can I do for ye?"
```

### With Existing Agent ID

```bash
python ivs-stage-elevenlabs-agent.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant123" \
  --agent-id "agent_abc123"
```

## Command-Line Arguments

| Argument                   | Required | Default                            | Description                                                             |
| -------------------------- | -------- | ---------------------------------- | ----------------------------------------------------------------------- |
| `--token`                  | Yes      | —                                  | IVS participant token with PUBLISH and SUBSCRIBE capabilities           |
| `--subscribe-to`           | Yes      | —                                  | Participant ID to subscribe to                                          |
| `--elevenlabs-api-key`     | No       | `ELEVENLABS_API_KEY` env var       | ElevenLabs API key                                                      |
| `--agent-id`               | No       | _(auto-create)_                    | Existing ElevenLabs agent ID (auto-creates and deletes if not set)      |
| `--voice-id`               | No       | `JBFqnCBsd6RMkjVDRZzb` (George)    | ElevenLabs voice ID                                                     |
| `--llm-model`              | No       | `gemini-2.0-flash`                 | LLM model for agent reasoning                                           |
| `--prompt`                 | No       | _(friendly assistant)_             | System prompt for the agent personality                                 |
| `--greeting`               | No       | `Hello! How can I help you today?` | Greeting spoken when the session starts                                 |
| `--language`               | No       | `en`                               | Language code                                                           |
| `--multilingual`           | No       | _(disabled)_                       | Enable automatic language detection — agent responds in user's language |
| `--ice-timeout`            | No       | `1`                                | ICE gathering timeout in seconds                                        |
| `--disable-frame-analysis` | No       | _(enabled)_                        | Disable video frame analysis via Bedrock Claude                         |
| `--bedrock-model-id`       | No       | `us.anthropic.claude-sonnet-4-6`   | Bedrock model for frame analysis                                        |
| `--bedrock-region`         | No       | `us-east-1`                        | AWS region for Bedrock                                                  |

## Vision (Frame Analysis)

ElevenLabs' Conversational AI supports client-side tool calling, which this demo uses to add vision capabilities via Amazon Bedrock Claude. When the user asks the agent to "look at something" or "describe what you see", the agent calls the `analyze_frame` client tool, which:

1. Captures the latest video frame from the participant's WebRTC stream
2. Converts it to JPEG and base64-encodes it
3. Sends it to Bedrock Claude for analysis
4. Returns the description to the ElevenLabs agent via `client_tool_result`, which speaks it naturally

If the tool's `expects_response` is `false`, the result is also sent as a `contextual_update` fallback so the agent incorporates the vision result in its next reply.

### Usage with Vision

```bash
# Default: vision enabled with Claude Sonnet 4
python ivs-stage-elevenlabs-agent.py \
  --token "eyJ..." \
  --subscribe-to "participant123"

# With a different Claude model
python ivs-stage-elevenlabs-agent.py \
  --token "eyJ..." \
  --subscribe-to "participant123" \
  --bedrock-model-id "anthropic.claude-3-5-haiku-20241022-v1:0"

# Disable vision entirely (no AWS dependency)
python ivs-stage-elevenlabs-agent.py \
  --token "eyJ..." \
  --subscribe-to "participant123" \
  --disable-frame-analysis
```

### How It Works Under the Hood

The `analyze_frame` function is registered as a `client` type tool in the ElevenLabs agent configuration. When the LLM decides to call it:

1. ElevenLabs sends a `client_tool_call` message with the tool name and call ID
2. The `ElevenLabsAgentManager` intercepts it and calls `_handle_analyze_frame`
3. The current video frame is converted to base64 JPEG and sent to Bedrock Claude
4. The Claude response is sent back as a `client_tool_result`
5. ElevenLabs feeds the result to the LLM, which formulates a spoken response

### Required AWS Permissions for Vision

- `bedrock:InvokeModel` — for Claude frame analysis

## Multilingual Auto-Detection

ElevenLabs Conversational AI supports automatic language detection — the agent detects what language the user is speaking and responds in the same language, mid-conversation, without any manual switching.

This is enabled via the `--multilingual` flag, which activates ElevenLabs' `language_detection` system tool. When enabled, the agent automatically switches its TTS model to the multilingual variant for non-English languages.

### Usage

```bash
# Agent that auto-detects and responds in the user's language
python ivs-stage-elevenlabs-agent.py \
  --token "eyJ..." \
  --subscribe-to "participant123" \
  --multilingual

# Multilingual with custom voice and LLM
python ivs-stage-elevenlabs-agent.py \
  --token "eyJ..." \
  --subscribe-to "participant123" \
  --multilingual \
  --voice-id "EXAVITQu4vr4xnSDxMaL" \
  --llm-model "gpt-4o"
```

### How It Works

1. The `--multilingual` flag adds the `language_detection` built-in system tool to the agent configuration
2. ElevenLabs' ASR detects the language of the user's speech
3. The agent automatically switches to respond in the detected language
4. TTS uses the multilingual v2.5 model for non-English languages (English stays on the faster v2 model)

### Supported Languages

ElevenLabs supports 30+ languages for conversational AI, including English, Spanish, French, German, Italian, Portuguese, Japanese, Chinese, Korean, Hindi, Arabic, and many more. See the [ElevenLabs language documentation](https://elevenlabs.io/docs/eleven-agents/customization/voice/customization/language) for the full list.

## Available Voices

ElevenLabs offers thousands of voices via the [Voice Library](https://elevenlabs.io/voice-library), plus custom voice cloning. Some popular premade options:

| Voice     | ID                     | Character                      |
| --------- | ---------------------- | ------------------------------ |
| George    | `JBFqnCBsd6RMkjVDRZzb` | Warm, conversational (default) |
| Sarah     | `EXAVITQu4vr4xnSDxMaL` | Soft, friendly                 |
| Daniel    | `onwK4e9ZLuTAKqWW03F9` | Clear, professional            |
| Charlotte | `XB0fDUnXU5powFXDhCwa` | Bright, articulate             |

See the [ElevenLabs Voice Library](https://elevenlabs.io/voice-library) for the full list. You can also clone custom voices via the ElevenLabs dashboard and use their IDs with `--voice-id`.

## Available LLM Models

The LLM model is configured when the agent is created. Popular options:

| Provider   | Model               | Notes                         |
| ---------- | ------------------- | ----------------------------- |
| Google     | `gemini-2.0-flash`  | Default — fast and capable    |
| OpenAI     | `gpt-4o`            | Most capable OpenAI model     |
| OpenAI     | `gpt-4o-mini`       | Faster, lower cost            |
| Anthropic  | `claude-sonnet-4-6` | Anthropic's latest            |
| ElevenLabs | _(hosted models)_   | ElevenLabs-hosted LLM options |

## SEI Transcript Publishing

Both user and agent transcripts are automatically embedded in the H.264 video stream as SEI (Supplemental Enhancement Information) NAL units. This enables synchronized captions on the client side.

### SEI Message Format

```json
{
  "type": "elevenlabs_agent_text",
  "role": "user",
  "content": "What's the weather like?",
  "timestamp": 1711929600.123
}
```

```json
{
  "type": "elevenlabs_agent_text",
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
    if payload.get("type") == "elevenlabs_agent_text":
        role = payload["role"]      # "user" or "assistant"
        content = payload["content"]
        print(f"[{role}] {content}")

subscriber = SeiSubscriber(message_callback=on_sei_message)
```

## Key Components

### ElevenLabsAgentManager (`elevenlabs_agent_manager.py`)

Manages the ElevenLabs Conversational AI WebSocket lifecycle:

- **Agent Creation**: Auto-creates an agent via `POST https://api.elevenlabs.io/v1/convai/agents/create` with configured voice, LLM, prompt, and tools — or uses an existing `--agent-id`
- **Agent Deletion**: Auto-deletes agents created by the script on shutdown
- **WebSocket**: Connects to `wss://api.elevenlabs.io/v1/convai/conversation?agent_id=<id>`
- **Audio Input**: Receives 16kHz mono PCM from the WebRTC subscribe track and sends as `{"user_audio_chunk": "<base64>"}` messages
- **Audio Output**: Receives `audio` events with `audio_event.audio_base_64` and feeds 24kHz PCM to the `AgentAudioTrack` for WebRTC publishing
- **Events**: Handles `agent_response`, `user_transcript`, `interruption`, `agent_response_correction`, `ping`/`pong`, and `client_tool_call`
- **Barge-in**: On `interruption`, clears the audio buffer so the agent stops immediately
- **SEI Publishing**: Publishes user and agent transcripts as SEI metadata on every transcript event
- **Tool Calls**: Handles `client_tool_call` → `client_tool_result` for frame analysis, with `contextual_update` fallback

### AgentAudioTrack (reused from `stages-nova-s2s`)

Custom `AudioStreamTrack` that buffers ElevenLabs' TTS audio and streams it to IVS via WebRTC:

- 24kHz, 16-bit, mono output (configured via `agent_output_audio_format: pcm_24000`)
- 20ms chunks at 50 FPS
- Batch buffering for smooth playback
- RMS-based audio level tracking for visual feedback

### AgentVideoTrack (reused from `stages-nova-s2s`)

Custom `VideoStreamTrack` that generates visual feedback:

- **Speaking**: Throbbing blue circle that reacts to audio levels
- **Thinking**: Pulsing orange circle with spinning donut animation
- **Idle**: Static blue circle

---

## Group Agent (Multi-Participant Wake Word)

The `ivs-stage-elevenlabs-group-agent.py` script is a passive voice agent for multi-participant stages. It transcribes all speakers using ElevenLabs Scribe v2 Realtime, publishes transcripts via SEI, and only responds when someone says the configurable wake word.

### How It Works

1. Joins the stage and publishes agent audio/video tracks
2. Monitors stage events to detect participants joining/leaving
3. Subscribes to each participant's audio with a dedicated ElevenLabs Scribe v2 Realtime STT WebSocket (`wss://api.elevenlabs.io/v1/speech-to-text/realtime`)
4. Maintains a rolling transcript buffer (configurable window, default 60s)
5. When the wake word is detected in a transcript, injects the conversation context + user message into the ElevenLabs Conversational AI agent via `contextual_update` and `user_message`
6. The agent speaks its response through the published audio track
7. After responding, enters an active listening window (default 10s) where follow-up questions don't need the wake word

### Usage

```bash
# Basic — responds to "hey assistant", silenced by "thank you assistant"
python ivs-stage-elevenlabs-group-agent.py \
  --token "eyJ..." \
  --wake-word "hey assistant"

# Custom wake/sleep words with longer context
python ivs-stage-elevenlabs-group-agent.py \
  --token "eyJ..." \
  --wake-word "ok agent" \
  --sleep-word "goodbye agent" \
  --context-window 120 \
  --active-listening-window 15

# With custom voice and LLM
python ivs-stage-elevenlabs-group-agent.py \
  --token "eyJ..." \
  --wake-word "hey elevenlabs" \
  --voice-id EXAVITQu4vr4xnSDxMaL \
  --llm-model gpt-4o

# With existing agent ID
python ivs-stage-elevenlabs-group-agent.py \
  --token "eyJ..." \
  --wake-word "hey assistant" \
  --agent-id "agent_abc123"

# Without vision (no AWS dependency)
python ivs-stage-elevenlabs-group-agent.py \
  --token "eyJ..." \
  --wake-word "hey assistant" \
  --disable-frame-analysis
```

### Group Agent Arguments

| Argument                    | Default                          | Description                                                   |
| --------------------------- | -------------------------------- | ------------------------------------------------------------- |
| `--token`                   | _(required)_                     | IVS participant token (PUBLISH + SUBSCRIBE)                   |
| `--elevenlabs-api-key`      | `ELEVENLABS_API_KEY` env var     | ElevenLabs API key                                            |
| `--wake-word`               | `hey assistant`                  | Phrase that activates the agent                               |
| `--sleep-word`              | `thank you assistant`            | Phrase that silences the agent and ends active listening      |
| `--context-window`          | `60`                             | Seconds of conversation history to include as context         |
| `--active-listening-window` | `10`                             | Seconds to stay active after responding (no wake word needed) |
| `--model-id`                | `scribe_v2_realtime`             | ElevenLabs STT model for transcription                        |
| `--language-code`           | `en`                             | Language code or `auto`                                       |
| `--agent-id`                | _(auto-create)_                  | Existing ElevenLabs agent ID (auto-creates if not set)        |
| `--voice-id`                | `JBFqnCBsd6RMkjVDRZzb` (George)  | ElevenLabs voice ID                                           |
| `--llm-model`               | `gemini-2.0-flash`               | LLM model                                                     |
| `--prompt`                  | _(meeting assistant)_            | Base system prompt                                            |
| `--disable-frame-analysis`  | _(enabled)_                      | Disable vision via Bedrock Claude                             |
| `--bedrock-model-id`        | `us.anthropic.claude-sonnet-4-6` | Bedrock model for frame analysis                              |
| `--bedrock-region`          | `us-east-1`                      | AWS region for Bedrock                                        |
| `--ice-timeout`             | `1`                              | ICE gathering timeout in seconds                              |

### Group Agent SEI Format

Transcripts from all participants are published as SEI metadata:

```json
{
  "type": "group_agent_transcript",
  "speaker": "user123",
  "participant_id": "abc123def456",
  "content": "Hey assistant, can you summarize what we discussed?",
  "timestamp": 1711929600.123
}
```

---

## Assistant Manager

The `ivs-stage-elevenlabs-agent-manager.py` script enables dynamic, multi-instance management of ElevenLabs Conversational AI Agents via IVS Chat WebSocket messages.

### How It Works

1. Connects to an IVS Chat room via WebSocket
2. Listens for `LAUNCH_ASSISTANT` messages
3. Generates a stage participant token for the target stage
4. Spawns `ivs-stage-elevenlabs-agent.py` as a subprocess with the provided configuration
5. Monitors the subprocess and cleans up when it exits
6. Sends error responses back through IVS Chat if launch fails

### Manager Usage

```bash
python ivs-stage-elevenlabs-agent-manager.py \
  --chat-room-arn "arn:aws:ivschat:us-east-1:123456789012:room/abcdefgh" \
  --ws-endpoint "wss://edge.ivschat.us-east-1.amazonaws.com" \
  --verbose
```

### Manager Command-Line Arguments

| Argument               | Required | Default                      | Description                                |
| ---------------------- | -------- | ---------------------------- | ------------------------------------------ |
| `--chat-room-arn`      | Yes      | —                            | IVS Chat room ARN                          |
| `--ws-endpoint`        | Yes      | —                            | WebSocket endpoint URL                     |
| `--elevenlabs-api-key` | No       | `ELEVENLABS_API_KEY` env var | ElevenLabs API key                         |
| `--max-instances`      | No       | `5`                          | Maximum concurrent agent instances         |
| `--region`             | No       | `us-east-1`                  | AWS region                                 |
| `--verbose`            | No       | `false`                      | Stream output from spawned agent instances |

The manager also accepts agent default arguments (`--voice-id`, `--llm-model`, `--agent-id`, `--prompt`, `--greeting`, `--language`, `--ice-timeout`, `--disable-frame-analysis`, `--bedrock-model-id`, `--bedrock-region`) that are used when the chat message doesn't specify them.

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
  "voiceId": "JBFqnCBsd6RMkjVDRZzb",
  "llmModel": "gemini-2.0-flash",
  "agentId": null,
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
| `voiceId`              | No       | `JBFqnCBsd6RMkjVDRZzb` (George)  | ElevenLabs voice ID              |
| `llmModel`             | No       | `gemini-2.0-flash`               | LLM model                        |
| `agentId`              | No       | _(auto-create)_                  | Existing ElevenLabs agent ID     |
| `prompt`               | No       | _(default)_                      | System prompt                    |
| `greeting`             | No       | _(default)_                      | Greeting message                 |
| `language`             | No       | `en`                             | Language code                    |
| `iceTimeout`           | No       | `1`                              | ICE gathering timeout in seconds |
| `disableFrameAnalysis` | No       | `false`                          | Disable video frame analysis     |
| `bedrockModelId`       | No       | `us.anthropic.claude-sonnet-4-6` | Bedrock model for frame analysis |
| `bedrockRegion`        | No       | `us-east-1`                      | AWS region for Bedrock           |

### Frontend Integration

```javascript
// Launch an ElevenLabs agent from your web app
const launchMessage = {
  action: "LAUNCH_ASSISTANT",
  stageArn: stageArn,
  participantId: localParticipantId,
  voiceId: "EXAVITQu4vr4xnSDxMaL",
  llmModel: "gpt-4o",
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
  "message": "Failed to launch ElevenLabs Agent for participant participant-123"
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
[abcdefgh::participant-123] 🎬 Starting IVS Stage + ElevenLabs Conversational AI Agent
[abcdefgh::participant-123] 🗣️  Voice: JBFqnCBsd6RMkjVDRZzb
[abcdefgh::participant-123] 🧠 LLM: gemini-2.0-flash
[abcdefgh::participant-123] [🤖 AGENT] Hey! Ready to write some code?
[abcdefgh::participant-123] [🗣️  USER] Can you help me with Python?
```

---

## Troubleshooting

### Agent Connection Issues

- Verify your ElevenLabs API key is valid and has sufficient credits
- Ensure network connectivity to `api.elevenlabs.io`
- If using `--agent-id`, verify the agent exists in your ElevenLabs account

### No Audio From Agent

- Confirm the IVS token has both PUBLISH and SUBSCRIBE capabilities
- Check that the participant ID matches an active participant on the stage
- Look for "First audio frame sent to ElevenLabs" in the logs — if missing, the WebRTC subscribe connection didn't establish

### Choppy Agent Audio

- The agent outputs at 24kHz (configured via `agent_output_audio_format: pcm_24000`)
- If you hear choppy audio, check that `OUTPUT_SAMPLE_RATE` is `24000` in `elevenlabs_agent_manager.py`

### Agent Not Responding

- Check the ElevenLabs WebSocket connection status in the logs
- Verify the LLM model name is valid (e.g., `gemini-2.0-flash`, `gpt-4o`)
- Look for `ping`/`pong` messages in debug logs to confirm the connection is alive

### SEI Messages Not Appearing

- Ensure `stages_sei.h264_sei_patch` is imported before `aiortc` in the main script
- The SEI publisher must be set globally via `set_global_sei_publisher` before any video encoding starts

### Auto-Created Agent Not Deleted

- The agent is deleted in the `shutdown()` method — ensure the script exits cleanly (Ctrl+C)
- If the script crashes, you may need to manually delete the agent via the ElevenLabs dashboard

## Related Documentation

- [ElevenLabs Conversational AI](https://elevenlabs.io/docs/conversational-ai/overview)
- [ElevenLabs Voice Library](https://elevenlabs.io/voice-library)
- [ElevenLabs API Reference](https://elevenlabs.io/docs/api-reference)
- [IVS Real-Time Streaming](https://docs.aws.amazon.com/ivs/latest/RealTimeUserGuide/)
- [IVS Chat User Guide](https://docs.aws.amazon.com/ivs/latest/ChatUserGuide/)
- [SEI Publishing System](../stages_sei/SEI.md)
