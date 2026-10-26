"""Ingestion bronze : open data Vélo'v (GBFS Grand Lyon) et météo (Open-Meteo) vers PostgreSQL.

Bronze = la donnée telle que reçue (payload JSON brut), plus de quoi la dater. Aucun nettoyage ici :
le typage et les règles métier sont dans les modèles dbt silver et gold (dossier pipeline/).

Idempotence (EX-10) : chaque table bronze a une clé naturelle et on insère avec
ON CONFLICT DO NOTHING. Deux passages sur les mêmes données ne créent aucun doublon :
- station_status      : (station_id, feed_last_updated), un relevé par station et par publication du flux
- station_information : (station_id, payload_sha256), une ligne par version de la fiche station
- weather_hourly      : (hour_utc), la première valeur reçue pour une heure écoulée est conservée

Usage :
    python -m velov.ingest              # un passage
    python -m velov.ingest --every 900  # en boucle, toutes les 15 min (service collector du compose)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime

import psycopg
from psycopg.types.json import Jsonb

GBFS_URL = os.getenv(
    "GBFS_URL",
    "https://download.data.grandlyon.com/files/rdata/jcd_jcdecaux.jcdvelov/gbfs.json",
)
OPEN_METEO_URL = os.getenv("OPEN_METEO_URL", "https://api.open-meteo.com/v1/forecast")
# Centre de Lyon : une seule météo pour toutes les stations, comme dans le simulateur.
LYON_LAT, LYON_LON = 45.76, 4.84

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("velov.ingest")

DDL = """
CREATE SCHEMA IF NOT EXISTS bronze;

CREATE TABLE IF NOT EXISTS bronze.station_status (
    station_id        TEXT        NOT NULL,
    feed_last_updated TIMESTAMPTZ NOT NULL,
    payload           JSONB       NOT NULL,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station_id, feed_last_updated)
);

CREATE TABLE IF NOT EXISTS bronze.station_information (
    station_id        TEXT        NOT NULL,
    payload_sha256    TEXT        NOT NULL,
    feed_last_updated TIMESTAMPTZ NOT NULL,
    payload           JSONB       NOT NULL,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station_id, payload_sha256)
);

CREATE TABLE IF NOT EXISTS bronze.weather_hourly (
    hour_utc    TIMESTAMPTZ NOT NULL PRIMARY KEY,
    payload     JSONB       NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def fetch_json(url: str, params: dict | None = None, retries: int = 3) -> dict:
    """GET JSON avec quelques essais : une API publique qui hoquette ne doit pas tuer la collecte."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "velov-mlops-collector"})  # noqa: S310
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (URL https fixée)
                return json.load(response)
        except OSError:
            if attempt == retries:
                raise
            logger.warning("Échec de %s (essai %s/%s), nouvel essai", url, attempt, retries)
            time.sleep(5 * attempt)
    raise AssertionError("inatteignable")


def gbfs_feeds(discovery_url: str) -> dict[str, str]:
    """Lit gbfs.json et renvoie {nom du flux: url}, quelle que soit la langue déclarée."""
    data = fetch_json(discovery_url)["data"]
    feeds = next(iter(data.values()))["feeds"]
    return {feed["name"]: feed["url"] for feed in feeds}


def _feed_time(feed: dict) -> datetime:
    return datetime.fromtimestamp(feed["last_updated"], tz=UTC)


def ingest_stations(conn: psycopg.Connection, feeds: dict[str, str]) -> tuple[int, int]:
    """Insère statuts et fiches stations. Retourne (lignes reçues, lignes nouvelles) pour les statuts."""
    info = fetch_json(feeds["station_information"])
    info_time = _feed_time(info)
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO bronze.station_information (station_id, payload_sha256, feed_last_updated, payload)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            [
                (
                    str(s["station_id"]),
                    hashlib.sha256(json.dumps(s, sort_keys=True).encode()).hexdigest(),
                    info_time,
                    Jsonb(s),
                )
                for s in info["data"]["stations"]
            ],
        )

    status = fetch_json(feeds["station_status"])
    status_time = _feed_time(status)
    rows = [(str(s["station_id"]), status_time, Jsonb(s)) for s in status["data"]["stations"]]
    with conn.cursor() as cur:
        before = cur.execute("SELECT count(*) FROM bronze.station_status").fetchone()[0]
        cur.executemany(
            """
            INSERT INTO bronze.station_status (station_id, feed_last_updated, payload)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            rows,
        )
        after = cur.execute("SELECT count(*) FROM bronze.station_status").fetchone()[0]
    return len(rows), after - before


def ingest_weather(conn: psycopg.Connection, now: datetime) -> int:
    """Insère la météo horaire des heures déjà écoulées (jamais de prévision : EX-11).

    past_days=2 : une collecte interrompue quelques heures se rattrape au passage suivant.
    """
    weather = fetch_json(
        OPEN_METEO_URL,
        {
            "latitude": LYON_LAT,
            "longitude": LYON_LON,
            "hourly": "temperature_2m,precipitation",
            "past_days": 2,
            "forecast_days": 1,
            "timezone": "UTC",
        },
    )
    hourly = weather["hourly"]
    rows = []
    for i, hour in enumerate(hourly["time"]):
        hour_utc = datetime.fromisoformat(hour).replace(tzinfo=UTC)
        if hour_utc > now:
            continue  # heure future : prévision, pas observation
        payload = {"temperature_2m": hourly["temperature_2m"][i], "precipitation": hourly["precipitation"][i]}
        rows.append((hour_utc, Jsonb(payload)))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO bronze.weather_hourly (hour_utc, payload) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            rows,
        )
    return len(rows)


def run_once(database_url: str) -> None:
    """Un passage complet, dans une transaction : tout ou rien."""
    now = datetime.now(UTC)
    with psycopg.connect(database_url, connect_timeout=10) as conn:
        conn.execute(DDL)
        received, new = ingest_stations(conn, gbfs_feeds(GBFS_URL))
        hours = ingest_weather(conn, now)
    logger.info("station_status : %s reçus, %s nouveaux ; météo : %s heures vues", received, new, hours)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion bronze Vélo'v + météo.")
    parser.add_argument("--every", type=int, default=0, help="boucle toutes les N secondes (0 : un seul passage)")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL non défini")

    if not args.every:
        run_once(database_url)
        return
    while True:
        try:
            run_once(database_url)
        except Exception:
            # La boucle survit à une panne passagère (API, réseau, base) ; la cause est dans les logs (EX-07).
            logger.exception("Échec du passage d'ingestion")
        time.sleep(args.every)


if __name__ == "__main__":
    main()
