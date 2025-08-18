/**
 * Visual SEI Decoder for extracting metadata from video frames
 * 
 * This decoder extracts SEI data that has been visually encoded into
 * the bottom rows of video frames by the Python VisualSeiEncoder.
 */

// Constants matching the Python encoder
const SYNC_PATTERN = [0xFF, 0x00, 0xFF, 0x00];
const UUID_BYTES = [0x9e, 0x50, 0x4e, 0xa5, 0xee, 0x5a, 0x4f, 0x02, 0x94, 0x9f, 0xb0, 0x33, 0xa3, 0x76, 0x8d, 0xa2];
const DATA_REGION_HEIGHT = 2;

class VisualSeiDecoder {
  constructor() {
    this.processedFrames = 0;
    this.messagesExtracted = 0;
    this.lastMessageTimestamp = 0;
    this.messageCache = new Set(); // For deduplication
  }

  /**
   * Extract SEI data from a video frame
   * @param {ImageData|HTMLCanvasElement|HTMLVideoElement} frameSource - Source of frame data
   * @returns {Array} Array of extracted messages
   */
  extractFromFrame(frameSource) {
    try {
      this.processedFrames++;

      // Get image data from various sources
      const imageData = this._getImageData(frameSource);
      if (!imageData) {
        return [];
      }

      const { data, width, height } = imageData;
      const messages = [];

      // Extract data from bottom rows
      const dataRegionY = height - DATA_REGION_HEIGHT;

      for (let row = 0; row < DATA_REGION_HEIGHT; row++) {
        const rowY = dataRegionY + row;
        const extractedData = this._extractRowData(data, width, rowY, width);

        if (extractedData.length > 0) {
          const message = this._parseMessage(extractedData);
          if (message && this._isValidMessage(message)) {
            // Check for duplicates using timestamp
            const messageKey = `${message.timestamp}_${message.content}`;
            if (!this.messageCache.has(messageKey)) {
              this.messageCache.add(messageKey);
              messages.push(message);
              this.messagesExtracted++;

              // Clean old cache entries (keep last 100)
              if (this.messageCache.size > 100) {
                const entries = Array.from(this.messageCache);
                this.messageCache = new Set(entries.slice(-50));
              }
            }
          }
        }
      }

      return messages;

    } catch (error) {
      console.error('❌ Error extracting visual SEI data:', error);
      return [];
    }
  }

  /**
   * Get ImageData from various frame sources
   */
  _getImageData(frameSource) {
    if (frameSource instanceof ImageData) {
      return frameSource;
    }

    // Create canvas to extract image data
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');

    if (frameSource instanceof HTMLVideoElement) {
      canvas.width = frameSource.videoWidth;
      canvas.height = frameSource.videoHeight;
      ctx.drawImage(frameSource, 0, 0);
    } else if (frameSource instanceof HTMLCanvasElement) {
      canvas.width = frameSource.width;
      canvas.height = frameSource.height;
      ctx.drawImage(frameSource, 0, 0);
    } else {
      console.warn('⚠️  Unsupported frame source type');
      return null;
    }

    return ctx.getImageData(0, 0, canvas.width, canvas.height);
  }

  /**
   * Extract data from a specific row of pixels
   */
  _extractRowData(imageData, width, rowY, maxLength) {
    const extractedBytes = [];
    const rowStart = rowY * width * 4; // 4 bytes per pixel (RGBA)

    for (let x = 0; x < Math.min(width, maxLength); x++) {
      const pixelStart = rowStart + (x * 4);

      // Extract data from RGB channels
      const red = imageData[pixelStart];     // Actual data
      const green = imageData[pixelStart + 1]; // Inverted data
      const blue = imageData[pixelStart + 2];  // Checksum

      // Verify data integrity
      const expectedGreen = 255 - red;
      const expectedBlue = (red ^ 0xFF) & 0xFF;

      if (green === expectedGreen && blue === expectedBlue) {
        extractedBytes.push(red);
      } else {
        // Data corruption detected, stop extraction
        break;
      }
    }

    return extractedBytes;
  }

  /**
   * Parse extracted bytes into a message
   */
  _parseMessage(extractedBytes) {
    try {
      // Check for sync pattern
      if (extractedBytes.length < SYNC_PATTERN.length + UUID_BYTES.length + 1) {
        return null;
      }

      // Verify sync pattern
      for (let i = 0; i < SYNC_PATTERN.length; i++) {
        if (extractedBytes[i] !== SYNC_PATTERN[i]) {
          return null;
        }
      }

      // Verify UUID
      const uuidStart = SYNC_PATTERN.length;
      for (let i = 0; i < UUID_BYTES.length; i++) {
        if (extractedBytes[uuidStart + i] !== UUID_BYTES[i]) {
          return null;
        }
      }

      // Get message length
      const lengthIndex = SYNC_PATTERN.length + UUID_BYTES.length;
      const messageLength = extractedBytes[lengthIndex];

      if (messageLength === 0 || messageLength > extractedBytes.length - lengthIndex - 1) {
        return null;
      }

      // Extract message data
      const messageStart = lengthIndex + 1;
      const messageBytes = extractedBytes.slice(messageStart, messageStart + messageLength);

      // Convert to string and parse JSON
      const messageString = new TextDecoder().decode(new Uint8Array(messageBytes));
      const messageData = JSON.parse(messageString);

      return messageData;

    } catch (error) {
      console.debug('🔍 Failed to parse visual SEI message:', error);
      return null;
    }
  }

  /**
   * Validate that a message is a Nova SEI message
   */
  _isValidMessage(message) {
    return message &&
      typeof message === 'object' &&
      message.type === 'nova_text_output' &&
      typeof message.content === 'string' &&
      typeof message.role === 'string' &&
      typeof message.timestamp === 'number';
  }

  /**
   * Get decoder statistics
   */
  getStats() {
    return {
      processedFrames: this.processedFrames,
      messagesExtracted: this.messagesExtracted,
      cacheSize: this.messageCache.size
    };
  }

  /**
   * Clear the message cache
   */
  clearCache() {
    this.messageCache.clear();
  }
}

/**
 * Process video frames from a video element
 */
class VideoFrameProcessor {
  constructor(videoElement, onMessage) {
    this.videoElement = videoElement;
    this.onMessage = onMessage;
    this.decoder = new VisualSeiDecoder();
    this.isProcessing = false;
    this.processingInterval = null;
  }

  start(intervalMs = 100) {
    if (this.isProcessing) {
      console.warn('⚠️  Video frame processor already running');
      return;
    }

    this.isProcessing = true;
    this.processingInterval = setInterval(() => {
      this._processCurrentFrame();
    }, intervalMs);

    console.log('🎬 Visual SEI video processor started');
  }

  stop() {
    if (!this.isProcessing) {
      return;
    }

    this.isProcessing = false;
    if (this.processingInterval) {
      clearInterval(this.processingInterval);
      this.processingInterval = null;
    }

    console.log('🛑 Visual SEI video processor stopped');
  }

  _processCurrentFrame() {
    if (!this.videoElement || this.videoElement.readyState < 2) {
      return; // Video not ready
    }

    try {
      const messages = this.decoder.extractFromFrame(this.videoElement);

      for (const message of messages) {
        if (this.onMessage) {
          this.onMessage(message);
        }
      }

    } catch (error) {
      console.error('❌ Error processing video frame:', error);
    }
  }

  getStats() {
    return this.decoder.getStats();
  }
}

// Example usage
function setupVisualSeiDecoding(videoElement) {
  const processor = new VideoFrameProcessor(videoElement, (message) => {
    console.log('📺 Received visual SEI message:', message);

    // Display the message
    displayNovaMessage(message.content, message.role, message.timestamp);
  });

  // Start processing frames every 100ms
  processor.start(100);

  // Log stats every 10 seconds
  setInterval(() => {
    const stats = processor.getStats();
    console.log('📊 Visual SEI Stats:', stats);
  }, 10000);

  return processor;
}

function displayNovaMessage(content, role, timestamp) {
  // Create message element (similar to previous example)
  const messageElement = document.createElement('div');
  messageElement.className = `nova-message nova-${role.toLowerCase()}`;

  const timeElement = document.createElement('span');
  timeElement.className = 'nova-timestamp';
  timeElement.textContent = new Date(timestamp * 1000).toLocaleTimeString();

  const contentElement = document.createElement('span');
  contentElement.className = 'nova-content';
  contentElement.textContent = content;

  const roleElement = document.createElement('span');
  roleElement.className = 'nova-role';
  roleElement.textContent = role === 'USER' ? '👤' : '🤖';

  messageElement.appendChild(roleElement);
  messageElement.appendChild(contentElement);
  messageElement.appendChild(timeElement);

  const messagesContainer = document.getElementById('nova-messages');
  if (messagesContainer) {
    messagesContainer.appendChild(messageElement);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }

  console.log(`📺 Nova (${role}): ${content}`);
}

// Export for use in modules
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    VisualSeiDecoder,
    VideoFrameProcessor,
    setupVisualSeiDecoding
  };
}

// Auto-setup if video element exists
if (typeof window !== 'undefined') {
  document.addEventListener('DOMContentLoaded', () => {
    const videoElement = document.querySelector('video');
    if (videoElement) {
      console.log('📺 Auto-setting up visual SEI decoding for video element');
      setupVisualSeiDecoding(videoElement);
    }
  });
}