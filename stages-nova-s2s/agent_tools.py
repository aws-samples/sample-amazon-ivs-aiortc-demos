import logging
import datetime
import pytz
import json
import requests
import boto3
import io
import base64
from typing import Optional

logger = logging.getLogger(__name__)


class AgentTools:

    def __init__(self, region="us-east-1",model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0"):
        self.region = region
        self.model_id = model_id
        self.bedrock_client = boto3.client("bedrock-runtime", region_name=self.region)

    def getdateandtime(self, tz: str):
        target_timezone = ""
        now = datetime.datetime.now()
        if tz:
            target_timezone = pytz.timezone(tz)
            now = datetime.datetime.now(target_timezone)

        return {
            "formattedTime": now.strftime("%I:%M %p"),
            "date": now.strftime("%Y-%m-%d"),
            "year": now.year,
            "month": now.month,
            "day": now.day,
            "dayOfWeek": now.strftime("%A").upper(),
            "timezone": "Local",
        }

    def getweather(self, location: str, weather_api_key: str):
        try:
            if not location:
                return {"error": "Location parameter is required"}

            logger.info(f"🌤️  Getting weather forecast for: {location}")

            # Make API request to WeatherAPI forecast endpoint
            api_url = f"http://api.weatherapi.com/v1/forecast.json"
            params = {
                "key": weather_api_key,
                "q": location,
                "days": 5,  # Get 5-day forecast
                "hour": 99,  # Don't include hourly data (hack)
                "aqi": "no",  # Don't include air quality data
            }

            response = requests.get(api_url, params=params, timeout=10)
            response.raise_for_status()
            weather_data = response.json()
            current = weather_data.get("current", {})
            location_info = weather_data.get("location", {})
            forecast_days = []
            forecast_data = weather_data.get("forecast", {}).get("forecastday", [])

            for day_data in forecast_data:
                day_info = day_data.get("day", {})
                forecast_days.append(
                    {
                        "date": day_data.get("date", ""),
                        "maxtemp_c": day_info.get("maxtemp_c"),
                        "maxtemp_f": day_info.get("maxtemp_f"),
                        "mintemp_c": day_info.get("mintemp_c"),
                        "mintemp_f": day_info.get("mintemp_f"),
                        "condition": day_info.get("condition", {}).get("text", ""),
                    }
                )

            return {
                "location": f"{location_info.get('name', '')}, {location_info.get('region', '')}, {location_info.get('country', '')}",
                "current": {
                    "temperature_celsius": current.get("temp_c"),
                    "temperature_fahrenheit": current.get("temp_f"),
                    "condition": current.get("condition", {}).get("text", ""),
                    "humidity": current.get("humidity"),
                    "wind_speed_kph": current.get("wind_kph"),
                    "wind_speed_mph": current.get("wind_mph"),
                    "wind_direction": current.get("wind_dir"),
                    "feels_like_celsius": current.get("feelslike_c"),
                    "feels_like_fahrenheit": current.get("feelslike_f"),
                    "visibility_km": current.get("vis_km"),
                    "visibility_miles": current.get("vis_miles"),
                    "uv_index": current.get("uv"),
                    "last_updated": current.get("last_updated"),
                },
                "forecast": forecast_days,
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"Weather API request failed: {e}")
            return {"error": f"Failed to fetch weather data: {str(e)}"}
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse weather API response: {e}")
            return {"error": "Failed to parse weather data"}
        except Exception as e:
            logger.error(f"Weather tool error: {e}")
            return {"error": f"Weather tool error: {str(e)}"}

    def frame_to_base64(self, frame) -> str:
        """Convert video frame to base64 encoded JPEG"""
        try:
            # Convert frame to PIL Image
            img = frame.to_image()

            # Convert to RGB if needed
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Save to bytes buffer as JPEG
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            buffer.seek(0)

            # Encode to base64
            img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
            return img_base64

        except Exception as e:
            logger.error(f"Error converting frame to base64: {e}")
            return None
          
    def analyzeframe(self, frame) -> Optional[str]:
        """
        Analyze a video frame using Claude

        Args:
            frame: Video frame from aiortc

        Returns:
            Analysis result string or None if failed
        """
        try:
            # Convert frame to base64
            frame_base64 = self.frame_to_base64(frame)
            if not frame_base64:
                return None

            # Prepare the message for Claude
            message = {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_base64}},
                    {
                        "type": "text",
                        "text": "Analyze this video frame from a live stream. Describe what you see in detail, including people, objects, activities, text, and any notable features. This could be used for content discovery, moderation, or accessibility purposes. Be specific and comprehensive.",
                    },
                ],
            }

            # Call Bedrock
            logger.info(f"🔍 Analyzing frame for participant...")

            response = self.bedrock_client.invoke_model(
                modelId=self.model_id,
                body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 250, "messages": [message], "temperature": 0.4}),
            )

            # Parse response
            response_body = json.loads(response["body"].read())
            analysis_result = response_body["content"][0]["text"]

            logger.info(f"✅ Frame analysis completed for participant")
            logger.info(f"📝 Analysis: {analysis_result}")

            return {"frame_analysis": analysis_result}

        except Exception as e:
            logger.error(f"Error analyzing frame: {e}")
            import traceback

            traceback.print_exc()
            return None

