import logging
import datetime
import pytz
import json
import requests

logger = logging.getLogger(__name__)


class AgentTools:

    def __init__(self):
        pass

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
