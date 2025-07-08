# IVS Stage Nova Speech-to-Speech

This script integrates Amazon IVS Real-Time Stages with Amazon Nova Sonic for AI-powered speech-to-speech functionality, including real-time waveform visualization and tool capabilities.

## Features

- **Bidirectional Audio Streaming**: Publishes AI responses while subscribing to participant audio
- **Amazon Nova Sonic Integration**: Real-time conversational AI with speech-to-speech capabilities
- **Visual Feedback System**: 
  - Blue throbbing circle when Nova is speaking
  - Orange pulsing animation when Nova is thinking/processing
  - Audio-reactive visualization during responses
- **Tool Support**: Built-in tools for date/time and weather information
- **Audio Processing**: Handles resampling between IVS (48kHz) and Nova (16kHz) formats
- **WebRTC Management**: Proper SDP handling for both publishing and subscribing

## Prerequisites

- Python 3.8+
- AWS credentials configured with Bedrock permissions
- Amazon IVS Real-Time Stage ARN and participant token
- FFmpeg and PortAudio installed
- Optional: Weather API key for weather functionality

## Installation

1. **Install dependencies:**
   ```bash
   pip install -r ../requirements.txt
   ```

2. **Set up environment variables:**
   ```bash
   export AWS_REGION=us-east-1
   export AWS_ACCESS_KEY_ID=your_access_key
   export AWS_SECRET_ACCESS_KEY=your_secret_key
   
   # Optional: For weather functionality
   export WEATHER_API_KEY=your_weather_api_key
   ```

## Usage

### Basic Usage

```bash
python ivs-stage-nova-s2s.py --token "your-jwt-token"
```

### Command-line Arguments

- `--token`: JWT participant token with both publish and subscribe capabilities (required)
- `--video-only`: Publish video only, no audio (optional flag)
- `--subscribe-to`: List of participant IDs to subscribe to (optional)
- `--nova-model`: Amazon Nova model identifier (default: "amazon.nova-sonic-v1:0")
- `--nova-region`: AWS region for Nova service (default: "us-east-1")

### Example with All Options

```bash
python ivs-stage-nova-s2s.py \
  --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzM4NCJ9..." \
  --subscribe-to "participant1" "participant2" \
  --nova-model "amazon.nova-sonic-v1:0" \
  --nova-region "us-east-1"
```

## Tools Available

The Nova AI assistant has access to the following tools:

### 1. Date and Time Tool

**Function**: `getDateAndTimeTool`
**Description**: Get current date and time information
**Parameters**:
- `timezone` (optional): Timezone for the date/time (defaults to local)

**Example conversation**:
- User: "What time is it?"
- Nova: "It's currently 2:30 PM on Tuesday, July 8th, 2025."

### 2. Weather Tool

**Function**: `getWeatherTool`
**Description**: Get current weather information and 5-day forecast for any location
**Parameters**:
- `location` (required): City name, postal code, or coordinates

**Requirements**:
- `WEATHER_API_KEY` environment variable must be set
- Uses WeatherAPI.com service

**Example conversation**:
- User: "What's the weather like in New York?"
- Nova: "The current weather in New York is 75°F with partly cloudy skies. The humidity is 60% with light winds from the southwest. For the next few days, expect highs in the mid-70s with a chance of rain on Thursday."

**Response includes**:
- **Current conditions**: Temperature, humidity, wind, visibility, UV index
- **5-day forecast**: Daily high/low temperatures and conditions for each day

**Supported location formats**:
- City names: "New York", "London", "Tokyo"
- City with state/country: "Atlanta, Georgia", "Paris, France"
- Postal codes: "10001", "SW1A 1AA"
- Coordinates: "40.7128,-74.0060"

## Weather API Setup

1. **Get API Key**:
   - Visit [WeatherAPI.com](https://www.weatherapi.com/)
   - Sign up for a free account
   - Get your API key from the dashboard

2. **Set Environment Variable**:
   ```bash
   export WEATHER_API_KEY=your_api_key_here
   ```

3. **Verify Setup**:
   - When you run the script, you should see: "🌤️ Weather tool is available"
   - If not configured: "⚠️ WEATHER_API_KEY environment variable not found. Weather tool will not be available."

## Technical Details

### Audio Processing

- **Input Format**: 48kHz stereo from IVS participants
- **Nova Input**: 16kHz mono for speech recognition
- **Nova Output**: 24kHz mono for speech synthesis
- **Output Format**: 48kHz stereo for IVS publishing

### Waveform Visualization

- Real-time audio visualization using matplotlib
- Gradient colormap with dynamic amplitude scaling
- Updates at 30 FPS for smooth animation
- **Visual States**:
  - **Speaking**: Blue circle that throbs with audio amplitude
  - **Thinking**: Orange pulsing rings when Nova is processing requests
  - **Idle**: Static blue circle when not active

### WebRTC Configuration

- Proper SDP munging for IVS compatibility
- Bidirectional peer connections (publish + subscribe)
- Audio track management with proper timing
- Error handling and reconnection logic

## Troubleshooting

### Common Issues

1. **No Audio Output**:
   - Check AWS credentials and Bedrock permissions
   - Verify participant token has both PUBLISH and SUBSCRIBE capabilities
   - Ensure audio input device is working

2. **Weather Tool Not Working**:
   - Verify `WEATHER_API_KEY` environment variable is set
   - Check API key validity at WeatherAPI.com
   - Ensure internet connectivity for API requests

3. **Poor Audio Quality**:
   - Check network bandwidth and stability
   - Verify audio input device quality
   - Monitor CPU usage during processing

4. **WebRTC Connection Issues**:
   - Check firewall settings for WebRTC traffic
   - Verify IVS stage ARN and token validity
   - Monitor network connectivity

### Debug Mode

Enable detailed logging:

```bash
export PYTHONPATH=$PYTHONPATH:.
python -c "import logging; logging.basicConfig(level=logging.DEBUG)"
python ivs-stage-nova-s2s.py --token "your-token"
```

### Performance Optimization

1. **Audio Processing**:
   - Use consistent 1ms delays between audio chunks
   - Implement proper buffering strategies
   - Monitor memory usage during long sessions

2. **Visualization**:
   - Reduce frame rate if CPU usage is high
   - Disable visualization for headless operation
   - Use smaller video resolution if needed

## API Limits

### WeatherAPI.com Free Tier
- 1,000,000 calls per month
- 1 call per second rate limit
- Current weather data only

### Amazon Nova Sonic
- Regional availability varies
- Pricing based on audio processing time
- Rate limits apply per account

## Security Notes

- Never commit API keys to version control
- Use environment variables for sensitive data
- Rotate API keys regularly
- Monitor API usage and costs

## Examples

### Weather Queries
- "What's the weather in London?"
- "How's the weather in 90210?"
- "Tell me about the weather in Tokyo, Japan"
- "What's the forecast for this week in Seattle?"
- "Will it rain tomorrow in Miami?"
- "What are the high and low temperatures for the next few days?"

### Date/Time Queries
- "What time is it?"
- "What's today's date?"
- "What day of the week is it?"
- "What time is it in Tokyo?" (with timezone support)

### Combined Queries
- "What's the weather and time in New York?"
- "Is it a good day for outdoor activities in San Francisco?"
- "What's the forecast for the weekend in Chicago?"
- "Should I bring an umbrella tomorrow in London?"

---

*This script demonstrates advanced integration between Amazon IVS Real-Time Stages and Amazon Nova Sonic, showcasing real-time conversational AI capabilities with tool support in live video environments.*
