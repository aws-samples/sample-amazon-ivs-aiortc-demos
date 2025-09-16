import logging
import datetime
import pytz
import json
import requests
import boto3
import io
import base64
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)


class AgentTools:

    def __init__(self, region="us-east-1", model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0"):
        self.region = region
        self.model_id = model_id
        self.bedrock_client = boto3.client("bedrock-runtime", region_name=self.region)

    def getdateandtime(self, location: str, tz: str = None):
        """
        Get current date and time for a specific location

        Args:
            location: Location name (e.g., "New York", "London", "Tokyo")
            tz: Optional timezone override (e.g., "America/New_York")
        """
        try:
            # If no timezone specified, return error asking for clarification
            if not tz:
                return {
                    "error": f"Unable to determine timezone for location '{location}'. Please specify the timezone (e.g., 'America/New_York', 'Europe/London', 'Asia/Tokyo') or provide a more specific location."
                }

            target_timezone = pytz.timezone(tz)
            now = datetime.datetime.now(target_timezone)

            return {
                "location": location,
                "formattedTime": now.strftime("%I:%M %p"),
                "date": now.strftime("%Y-%m-%d"),
                "year": now.year,
                "month": now.month,
                "day": now.day,
                "dayOfWeek": now.strftime("%A").upper(),
                "timezone": str(target_timezone),
                "timezone_abbreviation": now.strftime("%Z"),
                "utc_offset": now.strftime("%z"),
            }
        except Exception as e:
            logger.error(f"Error getting date/time for location '{location}': {e}")
            return {"error": f"Failed to get date/time for location '{location}': {str(e)}"}

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

    async def analyzeframe(self, frame, prompt=None, timeout=30) -> Optional[str]:
        """
        Analyze a video frame using Claude

        Args:
            frame: Video frame from aiortc
            timeout: Timeout in seconds for the analysis (default: 30)

        Returns:
            Analysis result string or None if failed
        """
        try:
            # Convert frame to base64 (CPU intensive - run in thread pool)
            loop = asyncio.get_event_loop()

            # Add timeout for frame conversion
            frame_base64 = await asyncio.wait_for(
                loop.run_in_executor(None, self.frame_to_base64, frame), timeout=10  # 10 seconds for frame conversion
            )

            if not frame_base64:
                logger.warning("Frame to base64 conversion failed")
                return None

            # Prepare the message for Claude
            bedrock_prompt = "Analyze this video frame from a live stream. Describe what you see in detail, including people, objects, activities, text, and any notable features. This could be used for content discovery, moderation, or accessibility purposes. Be specific and comprehensive. Refer to subjects in the image as 'you' and say things like 'your' or 'you are' instead of talking about the subject in the third-person. Pretend like you know them personally and are responding directly to them conversationally instead of describing the scene to a third-party."
            if prompt:
                bedrock_prompt += f"The user has specifically asked for the following information: '{prompt}'"
            message = {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_base64}},
                    {
                        "type": "text",
                        "text": bedrock_prompt,
                    },
                ],
            }

            # Call Bedrock (I/O bound - run in thread pool)
            logger.info(f"🔍 Analyzing frame for participant...")

            def bedrock_call():
                return self.bedrock_client.invoke_model(
                    modelId=self.model_id,
                    body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 100, "messages": [message], "temperature": 0.4}),
                )

            # Add timeout for Bedrock API call
            response = await asyncio.wait_for(
                loop.run_in_executor(None, bedrock_call), timeout=timeout - 10  # Reserve 10 seconds for frame conversion
            )

            # Parse response
            response_body = json.loads(response["body"].read())
            analysis_result = response_body["content"][0]["text"]

            logger.info(f"✅ Frame analysis completed for participant")
            logger.info(f"📝 Analysis: {analysis_result}")

            return {"frame_analysis": analysis_result}

        except asyncio.TimeoutError:
            logger.error(f"Frame analysis timed out after {timeout} seconds")
            return {"error": f"Frame analysis timed out after {timeout} seconds"}
        except Exception as e:
            logger.error(f"Error analyzing frame: {e}")
            import traceback

            traceback.print_exc()
            return None

    def websearch(self, query: str, brave_api_key: str, count: int = 5):
        """
        Search the web using Brave Search API

        Args:
            query: Search query string
            brave_api_key: Brave Search API key
            count: Number of results to return (default: 5, max: 20)
        """
        try:
            if not query:
                return {"error": "Query parameter is required"}

            if not brave_api_key:
                return {"error": "Brave API key is required"}

            logger.info(f"🔍 Searching web for: {query}")

            # Brave Search API endpoint
            api_url = "https://api.search.brave.com/res/v1/web/search"

            headers = {"Accept": "application/json", "Accept-Encoding": "gzip", "X-Subscription-Token": brave_api_key}

            params = {
                "q": query,
                "count": min(count, 20),  # Limit to max 20 results
                "search_lang": "en",
                "country": "US",
                "safesearch": "moderate",
                "text_decorations": False,
                "spellcheck": True,
            }

            response = requests.get(api_url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            search_data = response.json()

            # Extract relevant information from search results
            results = []
            web_results = search_data.get("web", {}).get("results", [])

            for result in web_results[:count]:
                results.append(
                    {
                        "title": result.get("title", ""),
                        "url": result.get("url", ""),
                        "description": result.get("description", ""),
                        "published": result.get("age", ""),
                        "language": result.get("language", ""),
                    }
                )

            # Include query information and metadata
            query_info = search_data.get("query", {})

            return {
                "query": query,
                "original_query": query_info.get("original", query),
                "altered_query": query_info.get("altered"),
                "spellcheck_off": query_info.get("spellcheck_off", False),
                "results_count": len(results),
                "results": results,
                "search_metadata": {
                    "total_results": search_data.get("web", {}).get("total", 0),
                    "search_time": search_data.get("web", {}).get("search_time", 0),
                },
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"Brave Search API request failed: {e}")
            return {"error": f"Failed to search web: {str(e)}"}
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Brave Search API response: {e}")
            return {"error": "Failed to parse search results"}
        except Exception as e:
            logger.error(f"Web search tool error: {e}")
            return {"error": f"Web search tool error: {str(e)}"}
