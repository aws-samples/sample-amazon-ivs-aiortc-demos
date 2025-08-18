/**
 * Client-side SEI NAL Unit extraction example
 * 
 * This example shows how to extract and parse SEI data from WebRTC video frames
 * on the client side to receive Nova text responses and metadata.
 */

// Note this uuid generated using https://github.com/uuidjs/uuid and generating
// a v4 random uuid of '9e504ea5-ee5a-4f02-949f-b033a3768da2'. The following is
// it's byte representation in hex:
export const SEND_SEI_UUID = new Uint8Array([
  0x9e, 0x50, 0x4e, 0xa5, 0xee, 0x5a, 0x4f, 0x02,
  0x94, 0x9f, 0xb0, 0x33, 0xa3, 0x76, 0x8d, 0xa2,
]);

// SEI constants
const SEI_TYPE_USER_DATA_UNREGISTERED = 0x05;
const NAL_UNIT_TYPE_SEI = 0x06;

/**
 * Check if a view matches a sequence at a specific index
 */
function viewMatchesSeqAt(view, sequence, viewIndex) {
  if (viewIndex + sequence.length < view.byteLength) {
    return sequence.every((val, i) => view.getUint8(viewIndex + i) === val);
  }
  return false;
}

/**
 * Remove emulation prevention from SEI payload
 */
function removeEmulationPrevention(message) {
  const result = [];
  let i = 0;

  while (i < message.length) {
    if (message[i] === 0x00 && message[i + 1] === 0x00 && message[i + 2] === 0x03) {
      result.push(message[i]);
      result.push(message[i + 1]);
      i += 3; // Skip the 0x03
    } else {
      result.push(message[i]);
      i += 1;
    }
  }

  return new Uint8Array(result);
}

/**
 * Read SEI message from data
 */
function readSeiMessage(data) {
  let i = 2; // Skip SEI type data
  const end = data.byteLength;

  while (i < end) {
    let payloadLength = 0;

    // Read payload size (variable length encoding)
    while (i < end && data[i] === 0xff) {
      payloadLength += 255;
      i++;
    }
    payloadLength += data[i];
    i++;

    // Read UUID and payload
    const payloadEnd = Math.min(i + payloadLength, end);
    const seiData = new Uint8Array(data.slice(i, payloadEnd));
    const uuid = seiData.slice(0, 16);
    const payload = seiData.slice(16);

    return { uuid, payload };
  }

  return null;
}

/**
 * Parse SEI NAL units from encoded video frame
 */
function parseSeiNal(encodedFrame) {
  const view = new DataView(encodedFrame);
  const seiMessages = [];
  let i = 0;

  // Check if this is Annex B format (H.264/H.265)
  const isAnnexB = viewMatchesSeqAt(view, [0x00, 0x00, 0x01], 0) ||
    viewMatchesSeqAt(view, [0x00, 0x00, 0x00, 0x01], 0);

  if (!isAnnexB) {
    return [];
  }

  while (i < view.byteLength - 5) {
    if (viewMatchesSeqAt(view, [0x00, 0x00, 0x01], i)) {
      const nalStart = i + 3; // Skip start code
      i = nalStart;

      const nalUnitType = view.getUint8(i) & 0x1f;
      const seiType = view.getUint8(i + 1) & 0x1f;

      if (nalUnitType === NAL_UNIT_TYPE_SEI && seiType === SEI_TYPE_USER_DATA_UNREGISTERED) {
        // Find next start code to determine NAL unit end
        while (i < view.byteLength) {
          if (viewMatchesSeqAt(view, [0x00, 0x00, 0x01], i)) {
            break;
          }
          i++;
        }

        const nalEnd = i;
        const seiNal = removeEmulationPrevention(
          new Uint8Array(encodedFrame.slice(nalStart, nalEnd))
        );

        const seiMessage = readSeiMessage(seiNal);
        if (seiMessage) {
          seiMessages.push(seiMessage);
        }
      } else {
        i++;
      }
    } else {
      i++;
    }
  }

  return seiMessages;
}

/**
 * Check if UUID matches our SEI UUID
 */
function isOurSeiMessage(uuid) {
  if (uuid.length !== SEND_SEI_UUID.length) {
    return false;
  }

  return uuid.every((byte, index) => byte === SEND_SEI_UUID[index]);
}

/**
 * Process Nova SEI message
 */
function processNovaMessage(payload) {
  try {
    const textDecoder = new TextDecoder();
    const jsonString = textDecoder.decode(payload);
    const data = JSON.parse(jsonString);

    console.log('📡 Received Nova SEI message:', data);

    if (data.type === 'nova_text_output') {
      displayNovaResponse(data.content, data.role, data.timestamp);
    }

    return data;
  } catch (error) {
    console.error('❌ Failed to parse Nova SEI message:', error);
    return null;
  }
}

/**
 * Display Nova response in UI
 */
function displayNovaResponse(content, role, timestamp) {
  // Create message element
  const messageElement = document.createElement('div');
  messageElement.className = `nova-message nova-${role.toLowerCase()}`;

  // Add timestamp
  const timeElement = document.createElement('span');
  timeElement.className = 'nova-timestamp';
  timeElement.textContent = new Date(timestamp * 1000).toLocaleTimeString();

  // Add content
  const contentElement = document.createElement('span');
  contentElement.className = 'nova-content';
  contentElement.textContent = content;

  // Add role indicator
  const roleElement = document.createElement('span');
  roleElement.className = 'nova-role';
  roleElement.textContent = role === 'USER' ? '👤' : '🤖';

  messageElement.appendChild(roleElement);
  messageElement.appendChild(contentElement);
  messageElement.appendChild(timeElement);

  // Add to messages container
  const messagesContainer = document.getElementById('nova-messages');
  if (messagesContainer) {
    messagesContainer.appendChild(messageElement);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }

  console.log(`🤖 Nova (${role}): ${content}`);
}

/**
 * WebRTC Transform for processing incoming video frames
 */
class NovaVideoReceiveTransform {
  constructor() {
    this.processedFrames = 0;
    this.seiMessagesReceived = 0;
  }

  transform(encodedFrame, controller) {
    try {
      this.processedFrames++;

      // Extract SEI messages
      const seiMessages = parseSeiNal(encodedFrame.data);

      // Process our SEI messages
      for (const message of seiMessages) {
        if (isOurSeiMessage(message.uuid)) {
          this.seiMessagesReceived++;
          processNovaMessage(message.payload);
        }
      }

      // Pass frame through unchanged
      controller.enqueue(encodedFrame);

    } catch (error) {
      console.error('❌ Error processing video frame:', error);
      controller.enqueue(encodedFrame);
    }
  }

  getStats() {
    return {
      processedFrames: this.processedFrames,
      seiMessagesReceived: this.seiMessagesReceived
    };
  }
}

/**
 * Set up WebRTC video transform for SEI processing
 */
function setupNovaVideoTransform(peerConnection) {
  const transform = new NovaVideoReceiveTransform();

  peerConnection.getReceivers().forEach(receiver => {
    if (receiver.track && receiver.track.kind === 'video') {
      const transformStream = new TransformStream({
        transform: transform.transform.bind(transform)
      });

      // Note: This is conceptual - actual implementation depends on WebRTC library
      if (receiver.transform) {
        receiver.transform = transformStream;
        console.log('📡 Nova video SEI transform activated');
      }
    }
  });

  // Log stats periodically
  setInterval(() => {
    const stats = transform.getStats();
    console.log(`📊 Nova SEI Stats: ${stats.seiMessagesReceived} messages from ${stats.processedFrames} frames`);
  }, 10000);
}

/**
 * Example usage
 */
function initializeNovaClient() {
  // Create messages container in HTML
  const messagesContainer = document.createElement('div');
  messagesContainer.id = 'nova-messages';
  messagesContainer.className = 'nova-messages-container';
  document.body.appendChild(messagesContainer);

  // Add CSS styles
  const style = document.createElement('style');
  style.textContent = `
        .nova-messages-container {
            max-height: 300px;
            overflow-y: auto;
            border: 1px solid #ccc;
            padding: 10px;
            margin: 10px;
            background: #f9f9f9;
        }
        
        .nova-message {
            margin: 5px 0;
            padding: 5px;
            border-radius: 5px;
        }
        
        .nova-user {
            background: #e3f2fd;
            text-align: right;
        }
        
        .nova-assistant {
            background: #f3e5f5;
            text-align: left;
        }
        
        .nova-role {
            font-weight: bold;
            margin-right: 5px;
        }
        
        .nova-timestamp {
            font-size: 0.8em;
            color: #666;
            margin-left: 10px;
        }
    `;
  document.head.appendChild(style);

  console.log('✅ Nova client initialized - ready to receive SEI messages');
}

// Export for use in modules
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    parseSeiNal,
    processNovaMessage,
    NovaVideoReceiveTransform,
    setupNovaVideoTransform,
    initializeNovaClient
  };
}

// Auto-initialize if in browser
if (typeof window !== 'undefined') {
  document.addEventListener('DOMContentLoaded', initializeNovaClient);
}