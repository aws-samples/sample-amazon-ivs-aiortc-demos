# Managing OpenAI Real-time Assistants Demo

This document demonstrates how to use the **IVS Stage OpenAI Assistant Manager** to dynamically launch and manage multiple OpenAI real-time API assistants based on WebSocket messages from IVS Chat.

## Overview

The OpenAI Assistant Manager is a WebSocket-based service that:

- **Listens to IVS Chat messages** via WebSocket connection
- **Automatically launches OpenAI real-time assistants** when requested
- **Manages multiple concurrent instances** with configurable limits
- **Handles cleanup and monitoring** of spawned processes
- **Supports full OpenAI configuration** including voice, VAD settings, and native vision capabilities

## Architecture

```
┌─────────────────┐    WebSocket     ┌──────────────────────────┐
│   Frontend/     │◄────────────────►│  OpenAI Assistant        │
│   IVS Chat      │    Messages      │  Manager                 │
└─────────────────┘                  └──────────────────────────┘
                                                    │
                                                    │ Spawns
                                                    ▼
                                     ┌──────────────────────────────┐
                                     │  OpenAI Real-time            │
                                     │  Assistant Instances         │
                                     │  (ivs-stage-gpt-realtime.py) │
                                     └──────────────────────────────┘
                                                    │
                                                    │ Connects to
                                                    ▼
                                     ┌──────────────────────────┐
                                     │  IVS Real-Time Stage     │
                                     │  + OpenAI Real-time API  │
                                     └──────────────────────────┘
```

## Prerequisites

### Required Services

1. **Amazon IVS Chat Room** - For WebSocket messaging
2. **Amazon IVS Real-Time Stage** - For video/audio streaming
3. **OpenAI API Access** - With real-time API capabilities

### AWS Permissions

Your AWS credentials need:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["ivschat:CreateChatToken", "ivs-realtime:CreateParticipantToken"],
      "Resource": "*"
    }
  ]
}
```

### Environment Variables

```bash
export OPENAI_API_KEY="your-openai-api-key-here"
export AWS_REGION="us-east-1"
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
```

## Usage

### Basic Usage

```bash
python ivs-stage-gpt-realtime-assistant-manager.py \
  --chat-room-arn "arn:aws:ivschat:us-east-1:123456789012:room/abcdefgh" \
  --ws-endpoint "wss://edge.ivschat.us-east-1.amazonaws.com" \
  --openai-key "sk-your-key-here"
```

### Advanced Usage

```bash
python ivs-stage-gpt-realtime-assistant-manager.py \
  --chat-room-arn "arn:aws:ivschat:us-east-1:123456789012:room/abcdefgh" \
  --ws-endpoint "wss://edge.ivschat.us-east-1.amazonaws.com" \
  --openai-key "sk-your-key-here" \
  --max-instances 10 \
  --region "us-west-2" \
  --verbose
```

### Command Line Arguments

| Argument          | Required | Default              | Description                  |
| ----------------- | -------- | -------------------- | ---------------------------- |
| `--chat-room-arn` | ✅       | -                    | IVS Chat room ARN            |
| `--ws-endpoint`   | ✅       | -                    | WebSocket endpoint URL       |
| `--openai-key`    | ⚠️       | `OPENAI_API_KEY` env | OpenAI API key               |
| `--max-instances` | ❌       | 5                    | Maximum concurrent instances |
| `--region`        | ❌       | us-east-1            | AWS region                   |
| `--verbose`       | ❌       | false                | Enable verbose output        |

## Message Protocol

### Launch Assistant Message

Send this JSON message through IVS Chat to launch a gpt-realtime assistant:

```json
{
  "action": "LAUNCH_ASSISTANT",
  "stageArn": "arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh",
  "participantId": "participant-123",
  "voice": "cedar",
  "vadMode": "server_vad",
  "vadThreshold": 0.5,
  "vadEagerness": "medium",
  "disableFrameAnalysis": false
}
```

### Required Fields

- **`action`**: Must be `"LAUNCH_ASSISTANT"`
- **`stageArn`**: ARN of the IVS Real-Time Stage
- **`participantId`**: ID of the participant to subscribe to

### Optional Configuration Fields

#### Voice Options

- **`voice`**: gpt-realtime voice to use
  - Options: `"alloy"`, `"ash"`, `"ballad"`, `"coral"`, `"echo"`, `"sage"`, `"shimmer"`, `"verse"`, `"marin"`, `"cedar"`
  - Default: `"cedar"`

#### VAD (Voice Activity Detection) Options

- **`vadMode`**: VAD mode
  - Options: `"server_vad"`, `"semantic_vad"`
  - Default: `"server_vad"`

**For Server VAD:**

- **`vadThreshold`**: Sensitivity threshold (0.0-1.0)
  - Lower = more sensitive
  - Default: `0.5`

**For Semantic VAD:**

- **`vadEagerness`**: Response eagerness
  - Options: `"low"`, `"medium"`, `"high"`, `"auto"`
  - Default: `"medium"`

#### Vision/Analysis Options

- **`disableFrameAnalysis`**: Disable video frame analysis
  - Default: `false` (uses OpenAI's native image processing)

## Example Configurations

### Basic Assistant

```json
{
  "action": "LAUNCH_ASSISTANT",
  "stageArn": "arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh",
  "participantId": "user-123"
}
```

### Natural Conversation Assistant

```json
{
  "action": "LAUNCH_ASSISTANT",
  "stageArn": "arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh",
  "participantId": "user-123",
  "voice": "nova",
  "vadMode": "semantic_vad",
  "vadEagerness": "low"
}
```

### Noisy Environment Assistant

```json
{
  "action": "LAUNCH_ASSISTANT",
  "stageArn": "arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh",
  "participantId": "user-123",
  "voice": "onyx",
  "vadMode": "server_vad",
  "vadThreshold": 0.7
}
```

### Vision-Disabled Assistant

```json
{
  "action": "LAUNCH_ASSISTANT",
  "stageArn": "arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh",
  "participantId": "user-123",
  "voice": "shimmer",
  "disableFrameAnalysis": true
}
```

## Error Handling

The manager sends error responses back through IVS Chat when issues occur:

### Error Response Format

```json
{
  "error": "ERROR_CODE",
  "stageId": "abcdefgh",
  "participantId": "user-123",
  "message": "Detailed error message"
}
```

### Error Codes

| Error Code                | Description                               | Solution                                                   |
| ------------------------- | ----------------------------------------- | ---------------------------------------------------------- |
| `MAX_INSTANCES_REACHED`   | Too many active instances                 | Wait for instances to finish or increase `--max-instances` |
| `INSTANCE_ALREADY_EXISTS` | Assistant already running for participant | Use different participant ID                               |
| `LAUNCH_FAILED`           | Failed to start assistant process         | Check logs for detailed error                              |

## Monitoring and Logging

### Log Levels

The manager provides detailed logging:

- **INFO**: Instance lifecycle, WebSocket events
- **DEBUG**: Detailed message parsing, configuration
- **ERROR**: Failures and exceptions

### Verbose Mode

Enable `--verbose` to see real-time output from all spawned gpt-realtime assistants:

```bash
python ivs-stage-gpt-realtime-assistant-manager.py \
  --chat-room-arn "..." \
  --ws-endpoint "..." \
  --verbose
```

Output format:

```
[stage-id::participant-id] gpt-realtime assistant log message
```

### Instance Monitoring

The manager automatically:

- **Monitors process health** of all spawned instances
- **Cleans up terminated instances** from memory
- **Logs instance lifecycle events** (start, stop, errors)
- **Tracks resource usage** (active instances count)

## Integration Examples

### Frontend Integration

```javascript
// Connect to IVS Chat
const chatClient = new IVSChatClient({
  roomId: "your-room-id",
  token: "your-chat-token",
});

// Launch OpenAI assistant
function launchAssistant(stageArn, participantId, options = {}) {
  const message = {
    action: "LAUNCH_ASSISTANT",
    stageArn: stageArn,
    participantId: participantId,
    ...options,
  };

  chatClient.sendMessage(JSON.stringify(message));
}

// Example usage
launchAssistant("arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh", "user-123", {
  voice: "nova",
  vadMode: "semantic_vad",
  vadEagerness: "medium",
});
```

### Backend Integration

```python
import boto3
import json

# Create IVS Chat client
ivschat = boto3.client('ivschat', region_name='us-east-1')

# Send launch message
def launch_gpt_realtime_assistant(room_arn, stage_arn, participant_id, **config):
    # Generate chat token
    token_response = ivschat.create_chat_token(
        roomIdentifier=room_arn,
        userId='backend-launcher',
        capabilities=['SEND_MESSAGE']
    )

    # Send message via chat API
    message = {
        'action': 'LAUNCH_ASSISTANT',
        'stageArn': stage_arn,
        'participantId': participant_id,
        **config
    }

    ivschat.send_event(
        roomIdentifier=room_arn,
        eventName='MESSAGE',
        eventAttributes={
            'Content': json.dumps(message)
        }
    )

# Example usage
launch_gpt_realtime_assistant(
    room_arn='arn:aws:ivschat:us-east-1:123456789012:room/abcdefgh',
    stage_arn='arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh',
    participant_id='user-123',
    voice='alloy',
    vadMode='server_vad',
    vadThreshold=0.6
)
```

## Best Practices

### Performance

1. **Set appropriate max instances** based on your server capacity
2. **Use server VAD** if you need reliable transcriptions
3. **Disable frame analysis** if OpenAI vision capabilities aren't needed
4. **Monitor resource usage** with verbose logging

### Security

1. **Secure your OpenAI API key** - use environment variables
2. **Validate input messages** - the manager does basic validation
3. **Limit chat room access** - control who can send launch messages
4. **Monitor instance creation** - watch for abuse or excessive launches

### Reliability

1. **Handle WebSocket disconnections** - the manager will attempt to reconnect
2. **Monitor process health** - instances are automatically cleaned up
3. **Set reasonable timeouts** - processes are terminated gracefully
4. **Log everything** - enable verbose mode for debugging

## Troubleshooting

### Common Issues

**WebSocket Connection Fails**

```
❌ WebSocket connection error: ...
```

- Check chat room ARN and WebSocket endpoint
- Verify AWS credentials and permissions
- Ensure chat token generation is working

**OpenAI Instance Launch Fails**

```
❌ Failed to launch gpt-realtime instance: LAUNCH_FAILED
```

- Check OpenAI API key validity
- Verify stage ARN and participant ID
- Check system resources and permissions

**No Transcriptions with Semantic VAD**

```
🗣️ Speech started detected
🤐 Speech stopped detected
(No user transcript appears)
```

- This is a known limitation with semantic VAD
- Switch to server VAD for reliable transcriptions
- See [VAD documentation](README.md#voice-activity-detection-vad) for details

### Debug Mode

Enable verbose logging and check:

1. **WebSocket messages** - Are launch messages being received?
2. **Token generation** - Are stage tokens being created successfully?
3. **Process spawning** - Are OpenAI instances starting correctly?
4. **Instance monitoring** - Are processes running and being monitored?

### Resource Monitoring

Monitor system resources:

- **CPU usage** - Each OpenAI instance uses significant CPU
- **Memory usage** - Multiple instances can consume substantial RAM
- **Network bandwidth** - Real-time audio/video streaming
- **OpenAI API limits** - Rate limits and usage quotas

## Conclusion

The OpenAI Realtime Assistant Manager provides a powerful way to dynamically manage multiple OpenAI real-time assistants through simple WebSocket messages. It handles the complexity of:

- **Token management** for both IVS Chat and Real-Time Stages
- **Process lifecycle** management with monitoring and cleanup
- **Configuration flexibility** with full OpenAI parameter support
- **Error handling** with detailed feedback
- **Resource management** with configurable limits

This enables building scalable, interactive applications where OpenAI assistants can be launched on-demand for any participant in an IVS stage, with full control over voice, VAD settings, and native vision capabilities.

## Related Documentation

- [OpenAI Realtime API Integration](README.md)
- [Nova S2S Assistant Manager](../stages-nova-s2s/MANAGING_ASSISTANT_DEMO.md)
- [IVS Real-Time Stages Documentation](https://docs.aws.amazon.com/ivs/latest/RealTimeUserGuide/)
- [OpenAI Realtime API Documentation](https://platform.openai.com/docs/guides/realtime)
