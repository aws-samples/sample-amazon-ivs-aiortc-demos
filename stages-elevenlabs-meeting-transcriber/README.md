# IVS Stage Meeting Transcriber powered by ElevenLabs

An AI meeting transcriber that joins an IVS Real-Time Stage as a silent participant, automatically transcribes all speakers using ElevenLabs Scribe v2 Realtime, and publishes transcripts back to the stage via SEI metadata embedded in a static video track.

## Overview

The meeting transcriber joins the stage with a static branded video track and silent audio. It monitors stage events via WebSocket to detect when participants join or leave, dynamically subscribing to each participant's audio. Each participant gets their own ElevenLabs Scribe v2 Realtime STT WebSocket connection for independent transcription. Transcripts are published as SEI metadata embedded in the transcriber's video stream.

## Architecture

```
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│ Participant A │  │ Participant B │  │ Participant C │
│  (publishing) │  │  (publishing) │  │  (publishing) │
└──────┬────────┘  └──────┬────────┘  └───────┬───────┘
       │ audio            │ audio             │ audio
       ▼                  ▼                   ▼
┌───────────────────────────────────────────────────────┐
│           Meeting Transcriber                         │
│                                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│  │ElevenLabs│  │ElevenLabs│  │ElevenLabs│ Per-speaker │
│  │Scribe v2 │  │Scribe v2 │  │Scribe v2 │ connections │
│  │Realtime A│  │Realtime B│  │Realtime C│             │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘             │
│       │             │             │                   │
│       └─────────────┼─────────────┘                   │
│                     ▼                                 │
│         ┌───────────────────┐                         │
│         │  SEI Publisher    │  transcripts embedded   │
│         └────────┬──────────┘  in H.264 stream        │
│                  ▼                                    │
│         ┌───────────────────┐                         │
│         │  Logo Video Track │  static robot icon      │
│         │  + Silent Audio   │                         │
│         └────────┬──────────┘                         │
└──────────────────┼────────────────────────────────────┘
                   │ publish (WHIP)
                   ▼
            ┌──────────────┐
            │   IVS Stage  │
            └──────────────┘
```

## Features

- **Multi-participant transcription** — automatically subscribes to every publishing participant
- **Dynamic join/leave** — monitors stage events via WebSocket, subscribes/unsubscribes in real-time
- **Per-speaker ElevenLabs connections** — each participant gets their own Scribe v2 Realtime STT WebSocket for clean separation
- **Word-level timestamps** — optional word-level timing data with speaker IDs
- **Language detection** — optional automatic language identification
- **VAD-based commit** — voice activity detection for automatic transcript segmentation (default)
- **Manual commit** — alternative commit strategy for custom segmentation control
- **SEI transcript publishing** — all transcripts embedded in the H.264 video stream
- **Static branded video** — publishes a robot icon video track (replace `robot-icon.png` with your own)
- **Silent audio** — doesn't produce any sound on the stage
- **No voice output** — this is a transcriber, not an agent — it listens and transcribes, never speaks

## Prerequisites

- Python 3.8+
- ElevenLabs API key ([elevenlabs.io](https://elevenlabs.io/))
- IVS stage participant token with both PUBLISH and SUBSCRIBE capabilities
- The token must also include stage events access (`events_url` and `topic` in JWT payload)

## Usage

```bash
cd stages-elevenlabs-meeting-transcriber

# Basic meeting transcription
python ivs-stage-elevenlabs-meeting-transcriber.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..."

# With language detection
python ivs-stage-elevenlabs-meeting-transcriber.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --language-code auto --include-language-detection true

# Manual commit strategy
python ivs-stage-elevenlabs-meeting-transcriber.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --commit-strategy manual

# Custom VAD settings
python ivs-stage-elevenlabs-meeting-transcriber.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --vad-silence-threshold-secs 2.0 --vad-threshold 0.3
```

## Command-Line Arguments

| Argument                       | Required | Default                      | Description                                           |
| ------------------------------ | -------- | ---------------------------- | ----------------------------------------------------- |
| `--token`                      | Yes      | —                            | IVS participant token (PUBLISH + SUBSCRIBE)           |
| `--elevenlabs-api-key`         | No       | `ELEVENLABS_API_KEY` env var | ElevenLabs API key                                    |
| `--model-id`                   | No       | `scribe_v2_realtime`         | ElevenLabs STT model ID                               |
| `--language-code`              | No       | `en`                         | Language code or `auto`                               |
| `--commit-strategy`            | No       | `vad`                        | Commit strategy: `vad` or `manual`                    |
| `--vad-silence-threshold-secs` | No       | `1.5`                        | Seconds of silence before auto-commit (VAD only)      |
| `--vad-threshold`              | No       | `0.4`                        | Speech detection sensitivity 0.0–1.0 (VAD only)       |
| `--include-timestamps`         | No       | `true`                       | Include word-level timestamps                         |
| `--include-language-detection` | No       | `false`                      | Include language detection per transcript             |
| `--publish-interim-sei`        | No       | `false`                      | Publish interim (partial) transcripts as SEI metadata |
| `--ice-timeout`                | No       | `1`                          | ICE gathering timeout in seconds                      |

## SEI Message Format

Published for every final transcript from any participant:

```json
{
  "type": "scribe_transcript",
  "speaker": "user123",
  "participant_id": "abc123def456",
  "content": "I think we should focus on the Q2 roadmap.",
  "timestamp": 1711929600.123
}
```

When language detection is enabled, a `language_code` field is included:

```json
{
  "type": "scribe_transcript",
  "speaker": "user123",
  "participant_id": "abc123def456",
  "content": "Creo que deberíamos enfocarnos en la hoja de ruta del Q2.",
  "language_code": "es",
  "timestamp": 1711929600.123
}
```

## Custom Icon

Replace `robot-icon.png` in this directory with your own 640×360 PNG image. The transcriber will display it as its video track on the stage. If the file is missing, a white frame is used.

## How It Works

1. **Publish**: Joins the stage with a static logo video + silent audio via WHIP
2. **Monitor**: Connects to the stage events WebSocket and watches for `STAGE_STATE` events
3. **Subscribe**: When a new participant starts publishing, creates a WHEP subscription to their audio
4. **Transcribe**: Each participant's audio is resampled to 16kHz mono and streamed to a dedicated ElevenLabs Scribe v2 Realtime WebSocket (`wss://api.elevenlabs.io/v1/speech-to-text/realtime`)
5. **Publish SEI**: Committed transcripts are embedded as SEI metadata in the transcriber's H.264 video stream
6. **Cleanup**: When a participant leaves, their subscription and ElevenLabs connection are torn down

## Related Documentation

- [ElevenLabs Speech-to-Text](https://elevenlabs.io/docs/api-reference/speech-to-text)
- [ElevenLabs Scribe v2 Realtime](https://elevenlabs.io/docs/api-reference/speech-to-text/realtime)
- [IVS Real-Time Streaming](https://docs.aws.amazon.com/ivs/latest/RealTimeUserGuide/)
- [SEI Publishing System](../stages_sei/SEI.md)
