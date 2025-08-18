# SEI NAL Unit Publishing for IVS Stage Nova S2S

This document describes the SEI (Supplemental Enhancement Information) NAL unit publishing functionality integrated into the Nova Speech-to-Speech project.

## Overview

The SEI publishing system allows real-time transmission of metadata alongside video streams in WebRTC connections. This is particularly useful for transmitting Nova's text responses, conversation state, and other metadata that can be consumed by client applications.

## Components

### 1. SeiPublisher (`sei_publisher.py`)

The core class that handles SEI NAL unit creation and insertion into H.264/H.265 video streams.

**Key Features:**

-   Thread-safe message queuing with asyncio locks
-   Automatic retry mechanism (configurable, default 3 attempts)
-   JSON and text payload support
-   Timestamp-based deduplication support
-   H.264/H.265 compliant SEI NAL unit generation
-   Emulation prevention for payload safety

**Usage:**

```python
from sei_publisher import SeiPublisher

# Initialize
sei_publisher = SeiPublisher(max_retry_attempts=3)

# Publish text
await sei_publisher.publish_text("Hello from Nova!", repeat_count=3)

# Publish JSON data
data = {"type": "response", "content": "Hello", "timestamp": time.time()}
await sei_publisher.publish_json(data, repeat_count=3)

# Process video frame (insert queued SEI data)
modified_frame = await sei_publisher.process_frame(original_frame_bytes)
```

### 2. VideoSeiTransform (`video_sei_transform.py`)

WebRTC transform wrapper for applying SEI modifications to video streams.

**Key Features:**

-   WebRTC transceiver integration
-   Conceptual framework for encoded frame processing
-   Transform lifecycle management

**Note:** This is currently a conceptual implementation as Python WebRTC libraries have limited support for encoded frame transforms. In production, this would need to be implemented at a lower level.

### 3. Integration with BedrockStreamManager

The SEI publisher is integrated into the Nova stream manager to automatically publish text responses as metadata.

**Published Data Structure:**

```json
{
    "type": "nova_text_output",
    "role": "USER|ASSISTANT",
    "content": "The actual text content",
    "timestamp": 1692345678.123,
    "session_id": "uuid-session-id",
    "content_id": "uuid-content-id"
}
```

## Technical Details

### SEI NAL Unit Structure

The implementation follows H.264/H.265 standards for SEI NAL units:

1. **Start Code**: `0x00 0x00 0x01`
2. **NAL Unit Type**: `0x06` (SEI)
3. **SEI Type**: `0x05` (User Data Unregistered)
4. **Payload Length**: Variable-length encoding
5. **UUID**: 16-byte identifier `9e504ea5-ee5a-4f02-949f-b033a3768da2`
6. **Payload**: JSON or text data
7. **Termination**: `0x80`

### Emulation Prevention

The system implements H.264/H.265 emulation prevention to ensure SEI payloads don't contain start code sequences that could be misinterpreted by decoders.

**Sequences Replaced:**

-   `0x00 0x00 0x00` → `0x00 0x00 0x03 0x00`
-   `0x00 0x00 0x01` → `0x00 0x00 0x03 0x01`
-   `0x00 0x00 0x02` → `0x00 0x00 0x03 0x02`
-   `0x00 0x00 0x03` → `0x00 0x00 0x03 0x03`

### Insertion Strategy

SEI NAL units are inserted before the first video slice NAL unit (types 1-5) in each frame. This ensures:

-   Compliance with H.264/H.265 standards
-   Proper timing association with video frames
-   Minimal impact on decoder performance

## Configuration

### Command Line Options

The main script supports SEI-related configuration through existing parameters:

```bash
python ivs-stage-nova-s2s.py \
  --token "your-token" \
  --subscribe-to "participant-id" \
  --nova-model-id "amazon.nova-sonic-v1:0" \
  --nova-region "us-east-1"
```

### Environment Variables

No additional environment variables are required for SEI functionality.

## Testing

Run the SEI publisher test to verify functionality:

```bash
python stages-nova-s2s/test_sei_publisher.py
```

**Expected Output:**

-   Successful text and JSON message queuing
-   Frame processing with size increase
-   SEI NAL unit structure verification
-   Queue management operations

## Client-Side Integration

To consume SEI data on the client side, implement:

1. **SEI NAL Unit Detection**: Look for NAL units with type `0x06`
2. **UUID Matching**: Filter for our specific UUID
3. **Payload Extraction**: Remove emulation prevention and extract JSON
4. **Deduplication**: Use timestamps to avoid duplicate processing

**Example Client Code (Conceptual):**

```javascript
// In your WebRTC video frame processing
function processSeiData(encodedFrame) {
    const seiUnits = extractSeiUnits(encodedFrame);

    for (const unit of seiUnits) {
        if (unit.uuid === OUR_SEI_UUID) {
            const payload = JSON.parse(unit.payload);

            if (payload.type === "nova_text_output") {
                displayNovaResponse(payload.content, payload.role);
            }
        }
    }
}
```

## Limitations

1. **Python WebRTC Constraints**: Full encoded frame processing requires lower-level implementation
2. **Bandwidth Impact**: SEI data increases stream size (typically 100-500 bytes per message)
3. **Client Support**: Requires client-side SEI parsing implementation
4. **Timing**: SEI data is associated with video frames, not audio timing

## Future Enhancements

1. **Native Transform Implementation**: Implement at C/C++ level for better performance
2. **Compression**: Add payload compression for large metadata
3. **Selective Publishing**: Configure which message types to publish
4. **Rate Limiting**: Implement bandwidth-aware message queuing
5. **Client Libraries**: Provide JavaScript/TypeScript client libraries

## Troubleshooting

### Common Issues

1. **No SEI Data in Stream**: Check that video track is active and frames are being generated
2. **Client Not Receiving**: Verify client-side SEI parsing implementation
3. **Performance Impact**: Monitor bandwidth usage and adjust repeat_count if needed

### Debug Logging

Enable debug logging to see SEI operations:

```python
logging.getLogger('sei_publisher').setLevel(logging.DEBUG)
```

### Verification

Use the test script to verify SEI functionality:

-   Check frame size increases
-   Verify NAL unit structure
-   Test queue operations

## References

-   [H.264 Standard (ITU-T H.264)](https://www.itu.int/rec/T-REC-H.264)
-   [H.265 Standard (ITU-T H.265)](https://www.itu.int/rec/T-REC-H.265)
-   [WebRTC Specification](https://www.w3.org/TR/webrtc/)
-   [Amazon IVS Real-time Streaming](https://docs.aws.amazon.com/ivs/latest/RealTimeUserGuide/)
