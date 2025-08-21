# SEI (Supplemental Enhancement Information) Publishing

This directory contains a complete SEI publishing system for injecting metadata into H.264 video streams in real-time. SEI NAL units allow you to embed custom data alongside video frames, enabling synchronized metadata delivery in WebRTC applications.

## 🎯 What is SEI?

SEI (Supplemental Enhancement Information) is part of the H.264/AVC video compression standard that allows embedding additional metadata within the video stream itself. This metadata travels with the video frames, ensuring perfect synchronization between video content and associated data.

**Message Identification**: This implementation uses a custom UUID (`b16d7d56-892e-419c-8d82-e069cd3aa5c1`) to identify SEI messages, enabling client-side filtering and deduplication.

### Key Benefits:

-   **Perfect Synchronization**: Metadata is embedded directly in video frames
-   **Low Latency**: No separate data channels needed
-   **Standards Compliant**: Uses official H.264 specification
-   **Cross-Platform**: Works with any H.264-compatible decoder
-   **Reliable Delivery**: Metadata survives video transcoding and streaming

## 📁 Components

### Core Files:

-   **`sei_publisher.py`** - High-level SEI message publishing interface
-   **`h264_sei_patch.py`** - Low-level H.264 encoder patching system
-   **`SEI.md`** - This documentation file
-   **`__init__.py`** - Python package initialization

## 🚀 Quick Start

### 1. Basic Setup

```python
import asyncio
from stages_sei import SeiPublisher, patch_h264_encoder, set_global_sei_publisher

# Apply H.264 encoder patch (do this early in your application)
patch_result = patch_h264_encoder()
print(f"H.264 patch applied: {patch_result}")

# Create SEI publisher
sei_publisher = SeiPublisher()

# Connect the publisher to the H.264 patch
set_global_sei_publisher(sei_publisher)
```

### 2. Publishing SEI Messages

```python
# Publish text content
await sei_publisher.publish_text("Hello from SEI!", repeat_count=3)

# Publish JSON data
metadata = {
    "type": "chat_message",
    "user": "alice",
    "message": "Hello world!",
    "timestamp": time.time()
}
await sei_publisher.publish_json(metadata, repeat_count=3)
```

### 3. Integration with Video Streams

The SEI system automatically intercepts H.264 video data from:

-   **aiortc** H.264 encoders (`_encode_frame` method)
-   **PyAV** packet conversion methods (`__bytes__`, `to_bytes`)
-   Multiple H.264 formats: Annex B, AVCC, RTP payloads

## 🔧 Technical Details

### H.264 Format Support

The system automatically detects and handles multiple H.264 formats:

| Format      | Description                    | Example Use Case  |
| ----------- | ------------------------------ | ----------------- |
| **Annex B** | Start code prefixed (0x000001) | Raw H.264 streams |
| **AVCC**    | Length prefixed                | MP4 containers    |
| **RTP**     | Raw NAL units                  | WebRTC streams    |

### SEI NAL Unit Structure

```
[Start Code] [NAL Header] [SEI Type] [Size] [UUID] [Payload] [Trailing Bits]
   3-4 bytes    1 byte      1 byte   1-N bytes  16 bytes   N bytes    1 byte
```

-   **Start Code**: `0x000001` (Annex B format)
-   **NAL Header**: `0x06` (SEI NAL unit type)
-   **SEI Type**: `0x05` (User Data Unregistered)
-   **UUID**: Custom identifier for message deduplication (`b16d7d56-892e-419c-8d82-e069cd3aa5c1`)
-   **Payload**: Your JSON/text data
-   **Trailing Bits**: RBSP alignment (`0x80`)

### Message Reliability

-   **Default Repeat Count**: 3x per message
-   **Automatic Deduplication**: Client-side based on timestamp
-   **Error Recovery**: Graceful fallback on encoding failures
-   **Size Limits**: Automatic truncation for large payloads

## 📊 Usage Patterns

### Real-Time Chat Integration

```python
class ChatSeiIntegration:
    def __init__(self):
        self.sei_publisher = SeiPublisher()
        set_global_sei_publisher(self.sei_publisher)

    async def send_chat_message(self, user: str, message: str):
        sei_data = {
            "type": "chat_message",
            "user": user,
            "message": message,
            "timestamp": time.time()
        }
        await self.sei_publisher.publish_json(sei_data)
```

### AI Assistant Integration

```python
class AiAssistantSei:
    def __init__(self):
        self.sei_publisher = SeiPublisher()
        set_global_sei_publisher(self.sei_publisher)

    async def publish_ai_response(self, role: str, content: str):
        sei_data = {
            "type": "ai_response",
            "role": role,  # "assistant", "user", etc.
            "content": content,
            "timestamp": time.time()
        }
        await self.sei_publisher.publish_json(sei_data, repeat_count=3)
```

### Live Transcription

```python
async def publish_transcription(self, text: str, confidence: float):
    sei_data = {
        "type": "transcription",
        "text": text,
        "confidence": confidence,
        "timestamp": time.time()
    }
    await self.sei_publisher.publish_json(sei_data)
```

## 🔍 Debugging & Monitoring

### Log Messages to Watch For:

```
✅ Success Messages:
📡 SEI INJECTION SUCCESS: 1024 -> 1156 bytes (rtp format)
📡 Message queued: assistant - repeat_count: 3
✅ H.264 encoder._encode_frame successfully patched for SEI injection

⚠️ Debug Messages (debug level):
🎯 INTERCEPTED H.264 PACKET: 1024 bytes

❌ Error Messages:
❌ Failed to create SEI NAL unit: [error details]
❌ Error in SEI injection: [error details]
```

### Queue Monitoring:

```python
# Check queue size
queue_size = await sei_publisher.get_queue_size()
print(f"Queued messages: {queue_size}")

# Clear queue if needed
await sei_publisher.clear_queue()
```

## 🛠️ Advanced Configuration

### Custom Repeat Counts

```python
# High priority message - repeat 5 times
await sei_publisher.publish_json(critical_data, repeat_count=5)

# Low priority message - send once
await sei_publisher.publish_json(debug_data, repeat_count=1)
```

### Payload Size Management

```python
# The system automatically truncates large payloads
large_data = {"content": "x" * 1000}  # Will be truncated
await sei_publisher.publish_json(large_data)
```

### Custom Message Types

```python
# Define your own message types
custom_message = {
    "type": "custom_event",
    "event_id": "user_action_123",
    "action": "button_click",
    "element": "subscribe_button",
    "timestamp": time.time()
}
await sei_publisher.publish_json(custom_message)
```

## 🔧 Integration Requirements

### Dependencies:

-   `aiortc` - For WebRTC H.264 encoding
-   `av` (PyAV) - For video codec handling
-   `asyncio` - For async message handling
-   `json` - For payload serialization

### Import Updates:

When integrating into existing projects, update imports:

```python
# Old imports (if moving from stages-nova-s2s)
# from sei_publisher import SeiPublisher
# from h264_sei_patch import patch_h264_encoder

# New imports
from stages_sei import SeiPublisher, patch_h264_encoder, set_global_sei_publisher
```

## 📈 Performance Considerations

### Throughput:

-   **Encoding Overhead**: ~1-2ms per SEI injection
-   **Payload Size**: Keep individual messages under 400 bytes
-   **Queue Management**: Process messages in batches for efficiency

### Memory Usage:

-   **Message Queue**: Automatically cleared after processing
-   **Format Conversion**: Temporary buffers for format conversion
-   **Repeat Storage**: Each repeat creates a separate NAL unit

## 🧪 Testing

### Unit Testing:

```python
# Test format detection
from stages_sei.h264_sei_patch import detect_h264_format

test_data = bytes.fromhex("41 00 b4 9a c0 1a bc 78")
is_h264, format_type, nal_type = detect_h264_format(test_data)
assert is_h264 == True
assert format_type == "rtp"
```

### Integration Testing:

```python
# Test end-to-end SEI publishing
sei_publisher = SeiPublisher()
result = await sei_publisher.publish_text("test message")
assert result == True
```

## 🚨 Troubleshooting

### Common Issues:

1. **SEI not appearing in stream**:

    - Verify `patch_h264_encoder()` was called
    - Check that `set_global_sei_publisher()` was called
    - Ensure H.264 encoder is being used

2. **Messages not synchronized**:

    - SEI is embedded in video frames, sync depends on video timing
    - Check that messages are published before/during video encoding

3. **Large payload truncation**:

    - Keep individual messages under 400 bytes
    - Split large data into multiple messages

4. **Performance issues**:
    - Reduce repeat count for high-frequency messages
    - Monitor queue size and clear if needed

### Debug Mode:

```python
import logging
logging.getLogger("stages_sei").setLevel(logging.DEBUG)
```

## 📚 References

-   [H.264/AVC Standard](https://www.itu.int/rec/T-REC-H.264)
-   [SEI NAL Unit Specification](https://www.itu.int/rec/T-REC-H.264-202108-I/en)
-   [WebRTC H.264 Implementation](https://webrtc.org/)
-   [PyAV Documentation](https://pyav.org/)
-   [aiortc Documentation](https://aiortc.readthedocs.io/)

---

**Built for Amazon IVS Real-Time Streaming Applications** 🚀
