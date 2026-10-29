"""Collecte météo Lyon pour TP3 - Bronze idempotent."""

import hashlib
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import requests

API_URL = "https://api.open-meteo.com/v1/forecast"

PARAMS = {
    "latitude": 45.764,
    "longitude": 4.8357,
    "current": "temperature_2m,rain",
    "timezone": "UTC",
    "timeformat": "unixtime",
}

BRONZE_DIR = Path("data/bronze/weather")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def fetch_weather():
    response = requests.get(
        API_URL,
        params=PARAMS,
        timeout=20,
    )
    response.raise_for_status()

    data = response.json()

    current = data.get("current")
    if not isinstance(current, dict):
        raise ValueError("Champ current absent")

    required = ("time", "temperature_2m", "rain")
    for field in required:
        if current.get(field) is None:
            raise ValueError(f"Champ météo absent: {field}")

    if not isinstance(current["time"], (int, float)):
        raise ValueError("Le timestamp météo doit être Unix")

    return data


def save_weather(data):
    current = data["current"]

    valid_time = datetime.fromtimestamp(current["time"], tz=UTC)
    collected_at = int(datetime.now(UTC).timestamp())

    # Identité du snapshot : heure et valeurs météo.
    key_data = {
        "time": current["time"],
        "temperature_2m": current["temperature_2m"],
        "rain": current["rain"],
        "latitude": PARAMS["latitude"],
        "longitude": PARAMS["longitude"],
    }
    digest = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode("utf-8")).hexdigest()

    folder = BRONZE_DIR / f"dt={valid_time:%Y-%m-%d}"
    folder.mkdir(parents=True, exist_ok=True)

    target = folder / (f"{valid_time:%Y%m%dT%H%M%SZ}_{digest}.json")

    if target.exists():
        return target, False

    # Garde la réponse originale + la date de collecte.
    envelope = {
        "source": "open-meteo",
        "collected_at": collected_at,
        "response": data,
    }

    fd, temp_name = tempfile.mkstemp(
        prefix=".weather_",
        suffix=".tmp",
        dir=folder,
    )
    temp_path = Path(temp_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(envelope, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())

        try:
            os.link(temp_path, target)
            return target, True
        except FileExistsError:
            return target, False
    finally:
        temp_path.unlink(missing_ok=True)


def main():
    data = fetch_weather()
    path, created = save_weather(data)

    current = data["current"]
    logging.info(
        "weather | %s | temp=%s C | rain=%s mm | %s",
        "NOUVEAU" if created else "DEJA PRESENT",
        current["temperature_2m"],
        current["rain"],
        path,
    )


if __name__ == "__main__":
    main()
