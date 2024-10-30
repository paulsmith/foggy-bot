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
            os.makedirs(output_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(output_dir, f"capture_{timestamp}.jpg")
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


class WeatherReporter:
    def __init__(self):
        self.model = llm.get_model("gpt-4o-mini")

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
    def _prepare_forecast_prompt(weather_data: Dict[str, Any]) -> str:
        forecasts = "Below is the weather forecast for Evanston, Illinois: \n"
        for period in weather_data["forecast"]:
            forecasts = (
                forecasts + "\n - " + period["name"] + ": " + period["detailedForecast"]
            )

        # Convert the current time to local time
        tz = timezone("America/Chicago")
        time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        forecasts += f"\n\nCurrent local time: {time}"

        forecasts += "\n\nReview this image and assess the weather, specifically looking for where any preciptitation is, the clarity of the day, and more. The image is a view of the beach in Evanston, looking East from a parks department building, towards Lake Michigan. \n\nConsidering the weather forecast and the image, please write a weather report for Evanston capturing the current conditions; the expected weather for the day; how pleasant or unpleasant it looks; how rough or calm the waves look; how wet it is; how one might best dress for the weather; how seasonable the temperature is for the region; and what one might do given the conditions, day, and time. Remember: you will generate this report many times a day, your recommended activities should be relatively mundane and not too cliche or stereotypical."

        forecasts += "\n\nDo not use headers or other formatting in your response. Just write one to two single paragraphs that are elegant, don't use bullet points or exclamation marks, don't mention the images as input, and use emotive words more often than numbers and figures – but don't be flowery. You write like a straight news journalist describing the scene, producing a work suitable for someone calmly reading it on a classical radio station between songs. With a style somewhere between anchor Bill Kurtis, meteorologist Tom Skilling, and Studs Terkel."

        forecasts += "\n\nRemember to keep the response under 500 words."

        forecasts += "\n\nAfter the weather report, please put an HTML color code that best represents the weather forecast, time of day, and the images."

        return forecasts


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
