# IVS Stage Assistant Manager

The IVS Stage Assistant Manager is a WebSocket-based service that automatically launches Nova Speech-to-Speech assistant instances based on incoming chat messages. This allows for dynamic management of multiple AI assistants in IVS Stage sessions.

## Overview

The assistant manager connects to an IVS Chat room via WebSocket and listens for specially formatted JSON messages. When a valid message is received, it automatically launches an instance of the Nova S2S script with the provided stage participant token and participant ID.

## Features

-   **WebSocket Integration**: Connects to IVS Chat rooms for real-time message handling
-   **Dynamic Instance Management**: Automatically launches and monitors Nova S2S instances
-   **Configurable Limits**: Support for up to 5 concurrent assistant instances (configurable)
-   **Process Monitoring**: Tracks active instances and cleans up when they exit
-   **Graceful Shutdown**: Properly terminates all instances on exit
-   **Flexible Message Format**: Uses action-based message filtering for extensibility

## Prerequisites

-   AWS credentials configured with access to IVS Chat and IVS Real-Time
-   Python 3.7+ with required dependencies installed
-   IVS Chat room ARN and WebSocket endpoint
-   IVS Stage ARNs for the stages where assistants should join

### Required AWS Permissions

Your AWS credentials need the following permissions:

**For IVS Chat:**

-   `ivschat:CreateChatToken` - Generate chat tokens for WebSocket connection

**For IVS Real-Time:**

-   `ivs-realtime:CreateParticipantToken` - Generate stage participant tokens dynamically

**For Nova S2S instances (inherited):**

-   `bedrock:InvokeModelWithBidirectionalStream` - For Nova Sonic functionality
-   `bedrock:InvokeModel` - For video frame analysis (if enabled)

## Installation

1. Install the required dependencies:

```bash
pip install -r requirements.txt
```

2. Ensure AWS credentials are configured:

```bash
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_DEFAULT_REGION=us-east-1
```

## Usage

### Basic Usage

```bash
python ivs-stage-assistant-manager.py \
  --chat-room-arn "arn:aws:ivschat:us-east-1:123456789012:room/abcdefgh" \
  --ws-endpoint "wss://edge.ivschat.us-east-1.amazonaws.com"
```

### Advanced Usage

```bash
python ivs-stage-assistant-manager.py \
  --chat-room-arn "arn:aws:ivschat:us-east-1:123456789012:room/abcdefgh" \
  --ws-endpoint "wss://edge.ivschat.us-east-1.amazonaws.com" \
  --max-instances 3 \
  --region us-west-2 \
  --verbose
```

## Command Line Arguments

| Argument          | Required | Default   | Description                                       |
| ----------------- | -------- | --------- | ------------------------------------------------- |
| `--chat-room-arn` | Yes      | -         | IVS Chat room ARN                                 |
| `--ws-endpoint`   | Yes      | -         | WebSocket endpoint URL                            |
| `--max-instances` | No       | 5         | Maximum number of concurrent Nova instances       |
| `--region`        | No       | us-east-1 | AWS region for IVS Chat service                   |
| `--verbose`       | No       | false     | Enable verbose output from spawned Nova instances |

## Message Format

The assistant manager expects JSON messages with the following format:

```json
{
    "action": "LAUNCH_ASSISTANT",
    "stageArn": "arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh",
    "participantId": "participant-123"
}
```

### Message Fields

-   **action** (required): Must be `"LAUNCH_ASSISTANT"` to trigger instance launch
-   **stageArn** (required): ARN of the IVS Stage where the assistant should join
-   **participantId** (required): Unique identifier for the participant to subscribe to

## How It Works

1. **Startup**: The manager generates a chat token and connects to the IVS Chat WebSocket
2. **Listening**: Displays the expected message format and waits for incoming messages
3. **Message Processing**: When a valid `LAUNCH_ASSISTANT` message is received:
    - Filters out messages from the assistant manager itself (prevents processing error responses)
    - Validates the message format and required fields
    - Checks if maximum instance limit has been reached
    - Generates a new stage participant token from the provided stage ARN
    - Launches a new Nova S2S instance as a subprocess with the generated token
    - Monitors the instance and cleans up when it exits
4. **Instance Management**: Tracks active instances and prevents duplicate launches
5. **Error Handling**: Sends error responses back to the frontend when issues occur
6. **Cleanup**: Gracefully terminates all instances when shutting down

## Integration Example

Here's how you might send a message from a web frontend to trigger an assistant launch:

```javascript
// Assuming you have an IVS Chat connection
const launchMessage = {
    action: "LAUNCH_ASSISTANT",
    stageArn: "arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh",
    participantId: "user-123",
};

// Send via IVS Chat WebSocket
chatConnection.send(
    JSON.stringify({
        Action: "SEND_MESSAGE",
        Content: JSON.stringify(launchMessage),
    })
);
```

### IVS Chat Message Structure

When sent through IVS Chat, messages are wrapped in the following format:

```json
{
    "Type": "MESSAGE",
    "Id": "message-id",
    "Content": "{\"action\":\"LAUNCH_ASSISTANT\",\"stageArn\":\"arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh\",\"participantId\":\"user-123\"}",
    "SendTime": "2025-09-12T12:47:15.458Z",
    "Sender": {
        "UserId": "sender-id",
        "Attributes": {
            "displayName": "Sender Name"
        }
    }
}
```

The assistant manager automatically extracts the payload from the `Content` field and processes it.

## Error Responses

When the assistant manager encounters an error while processing a launch request, it will send an error response back through the IVS Chat WebSocket. This provides immediate feedback to the frontend application.

**Note**: The assistant manager automatically filters out its own messages to prevent processing its own error responses in a loop.

**Multi-Client Support**: Since multiple frontend clients may be connected to the same IVS Chat room, error responses include `stageId` and `participantId` fields. This allows each frontend to only respond to errors relevant to their specific requests, preventing confusion when multiple clients are launching assistants simultaneously.

### Error Response Format

```json
{
    "Action": "SEND_MESSAGE",
    "Content": "{\"error\":\"ERROR_CODE\",\"stageId\":\"stage-id\",\"participantId\":\"participant-id\",\"message\":\"Human readable error message\"}"
}
```

### Error Codes

| Error Code                | Description                              | When it occurs                                                            |
| ------------------------- | ---------------------------------------- | ------------------------------------------------------------------------- |
| `MAX_INSTANCES_REACHED`   | Maximum number of instances exceeded     | When trying to launch more than the configured maximum instances          |
| `INSTANCE_ALREADY_EXISTS` | Instance for participant already running | When trying to launch an assistant for a participant that already has one |
| `LAUNCH_FAILED`           | General launch failure                   | When the Nova S2S script fails to start due to system errors              |

### Example Error Response

```json
{
    "error": "MAX_INSTANCES_REACHED",
    "stageId": "loQQ5xUNMFHs",
    "participantId": "user-123",
    "message": "Failed to launch assistant for participant user-123"
}
```

### Error Response Fields

-   **error** (string): The error code indicating the type of failure
-   **stageId** (string): The stage identifier (extracted from the stage ARN)
-   **participantId** (string): The participant identifier from the original request
-   **message** (string): Human-readable error description

### Frontend Integration

Your frontend can listen for these error messages and provide appropriate user feedback:

```javascript
// Listen for incoming messages
chatConnection.onmessage = (event) => {
    const data = JSON.parse(event.data);

    if (data.Type === "MESSAGE") {
        try {
            const content = JSON.parse(data.Content);

            if (content.error) {
                // Check if this error is for our stage/participant
                const isForOurRequest = content.stageId === ourStageId && content.participantId === ourParticipantId;

                if (isForOurRequest) {
                    // Handle error response for our specific request
                    switch (content.error) {
                        case "MAX_INSTANCES_REACHED":
                            showError("Maximum number of assistants reached. Please try again later.");
                            break;
                        case "INSTANCE_ALREADY_EXISTS":
                            showError("An assistant is already active for this participant.");
                            break;
                        case "LAUNCH_FAILED":
                            showError("Failed to launch assistant. Please try again.");
                            break;
                        default:
                            showError("An error occurred while launching the assistant.");
                    }
                }
                // Ignore errors for other stages/participants
            }
        } catch (e) {
            // Not a JSON message or not an error response
        }
    }
};
```

**Important**: Make sure your frontend tracks the `stageId` and `participantId` for each request so it can properly filter error responses. Only handle errors that match your specific stage and participant combination.

## Logging

The assistant manager provides comprehensive logging:

-   **Connection Status**: WebSocket connection and chat token generation
-   **Message Processing**: Incoming messages and validation results
-   **Instance Management**: Launch, monitoring, and cleanup of Nova instances
-   **Error Handling**: Detailed error messages for troubleshooting

### Verbose Output

When the `--verbose` flag is enabled, the assistant manager will stream the console output from all spawned Nova instances in real-time. Each line is prefixed with the stage ID and participant ID for easy identification:

```
[loQQ5xUNMFHs::participant-123] 🎬 Starting IVS Stage Publisher/Subscriber with Nova Speech-to-Speech
[loQQ5xUNMFHs::participant-123] 🧊 Applied aioice timeout patch: ICE gathering timeout reduced from 5s to 1s
[loQQ5xUNMFHs::participant-123] 🔑 Using token: eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9... (truncated)
[abcd1234EFGH::participant-456] 🤖 Nova model: amazon.nova-sonic-v1:0
[abcd1234EFGH::participant-456] 🌍 Nova region: us-east-1
```

The format is `[stage-id::participant-id]` where:

-   **stage-id**: The last part of the stage ARN (e.g., `loQQ5xUNMFHs` from `arn:aws:ivs:us-east-1:123456789012:stage/loQQ5xUNMFHs`)
-   **participant-id**: The participant identifier from the message payload

This is extremely helpful for:

-   **Debugging**: See exactly what's happening in each Nova instance
-   **Monitoring**: Track the status and health of active conversations
-   **Development**: Understand the flow and identify issues quickly

**Note**: Verbose output can be quite chatty. Use it primarily for debugging and development. In production, consider running without `--verbose` for cleaner logs.

## Limitations

-   Maximum of 5 concurrent assistant instances (configurable)
-   Stage participant tokens are generated dynamically with 12-hour duration
-   WebSocket connection requires valid IVS Chat permissions
-   Nova S2S instances inherit the same AWS credentials as the manager
-   Each message must contain a valid IVS Stage ARN

## Troubleshooting

### Common Issues

1. **Chat Token Generation Fails**

    - Verify AWS credentials have IVS Chat permissions
    - Check that the chat room ARN is correct and accessible

2. **WebSocket Connection Fails**

    - Ensure the WebSocket endpoint URL is correct for your region
    - Verify network connectivity and firewall settings

3. **Stage Token Generation Fails**

    - Verify AWS credentials have `ivs-realtime:CreateParticipantToken` permissions
    - Check that the stage ARN is correct and accessible
    - Ensure the stage exists and is in the correct region

4. **Nova Instance Launch Fails**

    - Check that the generated stage participant token is valid
    - Ensure the `ivs-stage-nova-s2s.py` script is in the same directory
    - Verify all required dependencies are installed

5. **Maximum Instances Reached**
    - Wait for existing instances to complete or increase `--max-instances`
    - Check logs to see which instances are currently active
    - The frontend will receive a `MAX_INSTANCES_REACHED` error response automatically

### Debug Mode

For additional debugging information, you have several options:

1. **Use Verbose Mode**: Add the `--verbose` flag to see real-time output from all Nova instances
2. **Modify Logging Level**: You can modify the logging level in the script for more detailed manager logs
3. **Check Individual Instances**: When verbose mode is enabled, you can see exactly what each Nova instance is doing

**Example with verbose output**:

```bash
python ivs-stage-assistant-manager.py \
  --chat-room-arn "arn:aws:ivschat:us-east-1:123456789012:room/abcdefgh" \
  --ws-endpoint "wss://edge.ivschat.us-east-1.amazonaws.com" \
  --verbose
```

## Security Considerations

-   Chat tokens are generated with minimal required capabilities (`SEND_MESSAGE`)
-   Stage participant tokens should be generated with appropriate time limits
-   AWS credentials should follow the principle of least privilege
-   WebSocket connections use secure WSS protocol

## Related Documentation

-   [IVS Stage Nova S2S README](./README.md) - Main Nova Speech-to-Speech implementation
-   [IVS Chat User Guide](https://docs.aws.amazon.com/ivs/latest/ChatUserGuide/) - Official AWS documentation
-   [IVS Stage User Guide](https://docs.aws.amazon.com/ivs/latest/RealTimeUserGuide/) - IVS Real-time Streaming documentation
