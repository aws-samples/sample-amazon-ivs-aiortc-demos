# IVS Stage Meeting Scribe powered by Deepgram

An AI meeting scribe that joins an IVS Real-Time Stage as a silent participant, automatically transcribes all speakers, runs text intelligence analysis, and publishes everything back to the stage via SEI metadata.

## Overview

The scribe joins the stage with a static Deepgram-branded video track and silent audio. It monitors stage events via WebSocket to detect when participants join or leave, dynamically subscribing to each participant's audio. Each participant gets their own Deepgram Nova-3 STT connection for independent transcription. Transcripts and intelligence results are published as SEI metadata embedded in the scribe's video stream.

## Architecture

```
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│ Participant A │  │ Participant B │  │ Participant C │
│  (publishing) │  │  (publishing) │  │  (publishing) │
└──────┬────────┘  └──────┬────────┘  └───────┬───────┘
       │ audio           │ audio           │ audio
       ▼                 ▼                 ▼
┌───────────────────────────────────────────────────────┐
│              Meeting Scribe                           │
│                                                       │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                │
│  │Deepgram │  │Deepgram │  │Deepgram │  Per-speaker   │
│  │  STT A  │  │  STT B  │  │  STT C  │  connections   │
│  └────┬────┘  └────┬────┘  └────┬────┘                │
│       │            │            │                     │
│       └────────────┼────────────┘                     │
│                    ▼                                  │
│         ┌───────────────────┐                         │
│         │ Text Intelligence │  sentiment, topics,     │
│         │  (Deepgram Read)  │  intents, summary       │
│         └────────┬──────────┘                         │
│                  ▼                                    │
│         ┌───────────────────┐                         │
│         │  SEI Publisher    │  transcripts + insights │
│         └────────┬──────────┘  embedded in H.264      │
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
- **Per-speaker Deepgram connections** — each participant gets their own STT WebSocket for clean separation
- **Speaker diarization** — enabled by default for multi-speaker identification
- **Text Intelligence** — periodic sentiment, topic, intent, and summarization analysis via Deepgram Read API
- **SEI transcript publishing** — all transcripts and intelligence results embedded in the H.264 video stream
- **Static branded video** — publishes a robot icon video track (replace `robot-icon.png` with your own)
- **Silent audio** — doesn't produce any sound on the stage
- **No voice output** — this is a scribe, not an agent — it listens and analyzes, never speaks

## Prerequisites

- Python 3.8+
- Deepgram API key ([deepgram.com](https://deepgram.com/))
- IVS stage participant token with both PUBLISH and SUBSCRIBE capabilities
- The token must also include stage events access (`events_url` and `topic` in JWT payload)

## Usage

```bash
cd stages-deepgram-scribe

# Basic meeting transcription
python ivs-stage-deepgram-scribe.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..."

# With full text intelligence
python ivs-stage-deepgram-scribe.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --sentiment --topics --intents --summarize

# Medical meeting with auto language detection
python ivs-stage-deepgram-scribe.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --model nova-3-medical --language auto \
  --sentiment --summarize --intelligence-interval 60
```

## Command-Line Arguments

| Argument                  | Required | Default                    | Description                                 |
| ------------------------- | -------- | -------------------------- | ------------------------------------------- |
| `--token`                 | Yes      | —                          | IVS participant token (PUBLISH + SUBSCRIBE) |
| `--deepgram-api-key`      | No       | `DEEPGRAM_API_KEY` env var | Deepgram API key                            |
| `--model`                 | No       | `nova-3`                   | Deepgram STT model                          |
| `--language`              | No       | `en`                       | Language code or `auto`                     |
| `--smart-format`          | No       | `true`                     | Smart formatting                            |
| `--diarize`               | No       | `true`                     | Speaker diarization                         |
| `--sentiment`             | No       | —                          | Enable sentiment analysis                   |
| `--topics`                | No       | —                          | Enable topic detection                      |
| `--intents`               | No       | —                          | Enable intent recognition                   |
| `--summarize`             | No       | —                          | Enable rolling summarization                |
| `--intelligence-interval` | No       | `30`                       | Seconds between analyses                    |
| `--ice-timeout`           | No       | `1`                        | ICE gathering timeout                       |

## SEI Message Formats

### Transcript Messages

Published for every final transcript from any participant:

```json
{
  "type": "scribe_transcript",
  "speaker": "user123",
  "participant_id": "abc123def456",
  "content": "I think we should focus on the Q2 roadmap.",
  "confidence": 0.97,
  "timestamp": 1711929600.123
}
```

### Intelligence Messages

Published periodically with analysis results:

```json
{
  "type": "scribe_intelligence",
  "analysis_number": 3,
  "timestamp": 1711929630.456,
  "sentiment": { "positive": 5, "neutral": 3, "negative": 1, "avg_score": 0.42 },
  "topics": ["Q2 roadmap", "product strategy", "engineering resources"],
  "intents": ["planning", "requesting information"],
  "summary": "The team discussed Q2 roadmap priorities, focusing on..."
}
```

## Custom Icon

Replace `robot-icon.png` in this directory with your own 640x360 PNG image. The scribe will display it as its video track on the stage. If the file is missing, a white frame is used.

## How It Works

1. **Publish**: Joins the stage with a static logo video + silent audio via WHIP
2. **Monitor**: Connects to the stage events WebSocket and watches for `STAGE_STATE` events
3. **Subscribe**: When a new participant starts publishing, creates a WHEP subscription to their audio
4. **Transcribe**: Each participant's audio is resampled to 16kHz mono and streamed to a dedicated Deepgram Nova-3 WebSocket
5. **Analyze**: Periodically batches all transcripts and sends them to Deepgram's Read API for sentiment/topics/intents/summarization
6. **Publish SEI**: Transcripts and intelligence results are embedded as SEI metadata in the scribe's H.264 video stream
7. **Cleanup**: When a participant leaves, their subscription and Deepgram connection are torn down

## Related Documentation

- [Deepgram Speech-to-Text](https://developers.deepgram.com/docs/stt/getting-started)
- [Deepgram Text Intelligence](https://developers.deepgram.com/docs/audio-intelligence)
- [IVS Real-Time Streaming](https://docs.aws.amazon.com/ivs/latest/RealTimeUserGuide/)
- [SEI Publishing System](../stages_sei/SEI.md)
