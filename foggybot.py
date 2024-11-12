# /// script
# dependencies = [
#   "google-api-python-client",
#   "llm",
#   "requests",
#   "pytz",
# ]
# ///
import json
import logging
import os
import re
from datetime import datetime
from typing import Optional, Dict, Any

import llm
import requests
from googleapiclient.discovery import build
from pytz import timezone

# Constants
YOUTUBE_VIDEO_ID = "XP3Gle-S9lE"
EVANSTON_COORDINATES = (42.032931, -87.680432)
OUTPUT_DIR = "captures"
TIMEZONE = "America/Chicago"
WEATHER_BASE_URL = "https://api.weather.gov"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class YouTubeClient:
    def __init__(self, api_key: str):
        self.youtube = build("youtube", "v3", developerKey=api_key)

    def get_live_thumbnail(self, video_id: str) -> Optional[str]:
        """Get the current thumbnail URL for a YouTube livestream."""
        try:
            request = self.youtube.videos().list(
                part="snippet,liveStreamingDetails", id=video_id
            )
            response = request.execute()

            if not response["items"]:
                logger.error(f"Video {video_id} not found")
                return None

            video = response["items"][0]
            if "liveStreamingDetails" not in video:
                logger.error(f"Video {video_id} is not a livestream")
                return None

            thumbnails = video["snippet"]["thumbnails"]
            for quality in ["maxres", "high", "default"]:
                if quality in thumbnails:
                    return thumbnails[quality]["url"]

        except Exception as e:
            logger.error(f"Error getting video details: {e}")
            return None


class ThumbnailDownloader:
    @staticmethod
    def download(url: str, output_dir: str = OUTPUT_DIR) -> Optional[str]:
        """Download thumbnail image from URL."""
        try:
            # Get current timestamp
            now = datetime.now()
            timestamp = now.strftime("%Y%m%d_%H%M%S")

            # Create year/month/day subdirectories
            year = now.strftime("%Y")
            month = now.strftime("%m")
            day = now.strftime("%d")

            # Construct full directory path
            dated_dir = os.path.join(output_dir, year, month, day)
            os.makedirs(dated_dir, exist_ok=True)

            # Create filenames with full paths
            filename = os.path.join(dated_dir, f"capture_{timestamp}.jpg")
            latest_filename = os.path.join(output_dir, "capture_latest.jpg")

            response = requests.get(url, stream=True)
            response.raise_for_status()

            # Write the timestamped file
            with open(filename, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Make a copy as capture_latest.jpg
            with open(filename, "rb") as src, open(latest_filename, "wb") as dst:
                dst.write(src.read())

            logger.info(f"Captured thumbnail saved to: {filename}")
            logger.info(f"Latest capture copied to: {latest_filename}")
            return filename

        except Exception as e:
            logger.error(f"Error downloading thumbnail: {e}")
            return None


class WeatherGov:
    def __init__(self):
        self.headers = {
            "User-Agent": "(WeatherDataScript, your@email.com)",
            "Accept": "application/json",
        }

    def get_weather_data(
        self, latitude: float, longitude: float
    ) -> Optional[Dict[str, Any]]:
        try:
            point_data = self._get_point_data(latitude, longitude)
            forecast_data = self._get_forecast_data(point_data)
            observation_data = self._get_observation_data(point_data)

            return {
                "location": self._format_location(point_data, latitude, longitude),
                "current_conditions": self._format_current_conditions(observation_data),
                "forecast": forecast_data["properties"]["periods"][:2],
            }
        except Exception as e:
            logger.error(f"Error getting weather data: {e}")
            return None

    def _get_point_data(self, latitude: float, longitude: float) -> Dict[str, Any]:
        response = requests.get(
            f"{WEATHER_BASE_URL}/points/{latitude},{longitude}", headers=self.headers
        )
        response.raise_for_status()
        return response.json()

    def _get_forecast_data(self, point_data: Dict[str, Any]) -> Dict[str, Any]:
        response = requests.get(
            point_data["properties"]["forecast"], headers=self.headers
        )
        response.raise_for_status()
        return response.json()

    def _get_observation_data(self, point_data: Dict[str, Any]) -> Dict[str, Any]:
        stations_response = requests.get(
            point_data["properties"]["observationStations"], headers=self.headers
        )
        stations_response.raise_for_status()
        stations_data = stations_response.json()

        nearest_station_url = (
            f"{stations_data['features'][0]['id']}/observations/latest"
        )
        observation_response = requests.get(nearest_station_url, headers=self.headers)
        observation_response.raise_for_status()
        return observation_response.json()

    @staticmethod
    def _celsius_to_fahrenheit(celsius: Optional[float]) -> Optional[float]:
        return None if celsius is None else (celsius * 9 / 5) + 32

    @staticmethod
    def _ms_to_mph(meters_per_second: Optional[float]) -> Optional[float]:
        return None if meters_per_second is None else meters_per_second * 2.237

    def _format_location(
        self, point_data: Dict[str, Any], lat: float, lon: float
    ) -> Dict[str, Any]:
        return {
            "name": point_data["properties"]["relativeLocation"]["properties"]["city"],
            "state": point_data["properties"]["relativeLocation"]["properties"][
                "state"
            ],
            "latitude": lat,
            "longitude": lon,
        }

    def _format_current_conditions(
        self, observation_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        properties = observation_data["properties"]
        return {
            "timestamp": properties["timestamp"],
            "temperature_f": self._celsius_to_fahrenheit(
                properties["temperature"]["value"]
            ),
            "temperature_c": properties["temperature"]["value"],
            "humidity": properties["relativeHumidity"]["value"],
            "wind_speed_mph": self._ms_to_mph(properties["windSpeed"]["value"]),
            "wind_direction": properties["windDirection"]["value"],
            "description": properties["textDescription"],
        }


COMFORT_MATRIX = {
    "temp_ranges": [30, 40, 50, 60, 70, 80, 90, 100],
    "humidity_ranges": [20, 30, 40, 50, 60, 70, 80, 90],
    "comfort_levels": {
        "cold": {"range": "<=40", "desc": "Cold, humidity less relevant"},
        "chilly": {"range": "<=50", "desc": "Chilly"},
        "cool_dry": {"range": "51-65, RH<40", "desc": "Cool & crisp"},
        "cool": {"range": "51-65, RH>=40", "desc": "Pleasant & cool"},
        "comfortable_dry": {"range": "66-75, RH<40", "desc": "Perfect conditions"},
        "comfortable": {"range": "66-75, RH<60", "desc": "Ideal comfort"},
        "warm_humid": {"range": "66-75, RH>=60", "desc": "Slightly muggy"},
        "warm_dry": {"range": "76-85, RH<40", "desc": "Warm but manageable"},
        "warm_sticky": {"range": "76-85, RH<60", "desc": "Warm and sticky"},
        "hot_humid": {"range": "76-85, RH>=60", "desc": "Uncomfortably humid"},
        "hot_dry": {"range": ">85, RH<40", "desc": "Very hot"},
        "dangerous": {"range": ">85, RH>=40", "desc": "Oppressively humid"},
    },
}


class WeatherReporter:
    def __init__(self):
        self.model = llm.get_model("gpt-4o-mini")

    PROMPT_TEMPLATE = """
Here are the current conditions in Evanston, Illinois:
{current_conditions}

Below is the weather forecast for Evanston, Illinois:
{forecast_periods}

Current local date and time: {current_time}

Considering this image and the weather forecast, assess the weather,
specifically looking for where any preciptitation is, the clarity of the day,
and more. The image is a view of the beach in Evanston, Illinois, looking east
from a parks department building towards Lake Michigan.

Considering your assessment of the weather, please write a weather report for
Evanston capturing:

- Current conditions
- Expected weather for the day
- Pleasant/unpleasant appearance
- Wave conditions
- Precipitation
- Recommended attire
- Temperature seasonality - consider the current season and region of the world
  the report is taking place in, and note if the temperature is roughly typical
  or not.
- Suggested activities given conditions, day, time, and location
- If the date happens to be a major U.S. or religious holiday, or election day,
  make note of it in your report. If it's not a holiday, don't mention it,
  unless a holiday is coming up in the next few days or weeks.

Take care not to mistake the current conditions for the upcoming forecast.

Style Guidelines:

- Write 1-2 single paragraphs
- No headers or special formatting
- No bullet points or exclamation marks
- Don't reference the images as input
- Instead of saying the wind speed in MPH, characterize it with standard
  descriptive words, like "still", "blustery", "gentle", "light", "calm",
  "whispering", "soothing", "howling", "fierce", "wild", "gusty", "breezy",
  "gale", etc., but feel free to draw from more synonyms that are appropriate
- Instead of stating humidity directly, characterize the overall feel using
  descriptive phrases that combine temperature and humidity effects, such as:
  "crisp and cool", "perfectly comfortable", "pleasantly dry", "ideal
  conditions", "slightly muggy", "sticky", "uncomfortably humid", or similar
  terms that reflect the comfort matrix below. The description should account
  for both temperature and humidity levels.
- Instead of saying the temperature as a specific number, say where it falls in
  the tens, for example, use "high 70s" for 79, "low 40s" for 42, or "mid 30s"
  for 34.
- Use emotive words more than numbers/figures, but avoid being flowery
- Write like a news journalist describing the scene
- Aim for a style suitable for reading on classical radio
- Combine the voice of Chicago news anchor Bill Kurtis, meteorologist Tom
  Skilling, and raconteur Studs Terkel
- Try to keep response under 500 words

After the weather report, please provide an HTML color code that best
represents the weather forecast, time of day, and the image. Output only the
hex code on a line by itself. Do not refer to the color code at all in the
report otherwise.

COMFORT_MATRIX = {comfort_matrix}
"""

    def generate_report(
        self, weather_data: Dict[str, Any], image_path: str
    ) -> Dict[str, Any]:
        forecasts = self._prepare_forecast_prompt(weather_data)
        response = self.model.prompt(
            forecasts, attachments=[llm.Attachment(path=image_path)]
        )

        response_text = str(response)
        color_code = self._extract_color_code(response_text)

        return {
            "forecast_data": weather_data,
            "weather_report": response_text.replace(color_code or "", "").rstrip(),
            "color_code": color_code,
            "timestamp": datetime.now(timezone(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S"),
        }

    @staticmethod
    def _extract_color_code(text: str) -> Optional[str]:
        color_match = re.search(r"#(?:[0-9a-fA-F]{3}){1,2}\b", text)
        return color_match.group(0) if color_match else None

    @staticmethod
    def _format_forecast_periods(weather_data: Dict[str, Any]) -> str:
        return "\n".join(
            f" - {period['name']}: {period['detailedForecast']}"
            for period in weather_data["forecast"]
        )

    @staticmethod
    def _format_current_conditions(weather_data: Dict[str, Any]) -> str:
        conditions = weather_data["current_conditions"]
        return f"""\
- Temperature (F): {conditions["temperature_f"]}
- Humidity (%): {conditions["humidity"]}
- Wind speed (MPH): {conditions["wind_speed_mph"]}
- Wind direction (degrees): {conditions["wind_direction"]}
- Description: {conditions["description"]}
"""

    def _prepare_forecast_prompt(self, weather_data: Dict[str, Any]) -> str:
        current_time = datetime.now(timezone(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
        forecast_periods = self._format_forecast_periods(weather_data)
        current_conditions = self._format_current_conditions(weather_data)

        return self.PROMPT_TEMPLATE.format(
            forecast_periods=forecast_periods,
            current_time=current_time,
            current_conditions=current_conditions,
            comfort_matrix=COMFORT_MATRIX,
        ).strip()


def main():
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        raise ValueError("Please set YOUTUBE_API_KEY environment variable")

    youtube_client = YouTubeClient(api_key)
    thumbnail_url = youtube_client.get_live_thumbnail(YOUTUBE_VIDEO_ID)
    if not thumbnail_url:
        raise RuntimeError("Failed to get YouTube livestream thumbnail")

    captured_file = ThumbnailDownloader.download(thumbnail_url)
    if not captured_file:
        raise RuntimeError("Failed to download thumbnail")

    weather = WeatherGov()
    lat, lon = EVANSTON_COORDINATES
    weather_data = weather.get_weather_data(lat, lon)
    if not weather_data:
        raise RuntimeError("Failed to get weather data")

    reporter = WeatherReporter()
    result = reporter.generate_report(weather_data, captured_file)

    with open("weather_report.json", "w") as f:
        json.dump(result, f, indent=4)


if __name__ == "__main__":
    main()
