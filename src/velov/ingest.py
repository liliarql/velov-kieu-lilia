
"""TP3 - Ingestion bronze GBFS Grand Lyon (idempotente)."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests

GBFS_URL = os.getenv(
    "GBFS_URL",
    "https://download.data.grandlyon.com/files/rdata/"
    "jcd_jcdecaux.jcdvelov/gbfs.json",
)

BRONZE_DIR = Path(os.getenv("BRONZE_DIR", "data/bronze"))
FEEDS = ("station_status", "station_information")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("velov.ingest")


def fetch_json(url: str) -> dict:
    """Télécharge et vérifie la réponse JSON."""
    response = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": "velov-mlops-student/1.0"},
    )
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"JSON invalide : {url}")

    return payload


def discover_feeds(discovery: dict) -> dict[str, str]:
    """Supporte les structures GBFS v2 et v3."""
    data = discovery.get("data", {})

    if not isinstance(data, dict):
        raise ValueError("Champ 'data' invalide")

    # GBFS v3: data.feeds
    feeds = data.get("feeds")

    # GBFS v2: data.fr.feeds, data.en.feeds, etc.
    if feeds is None:
        for language in ("fr", "en"):
            candidate = data.get(language)
            if isinstance(candidate, dict):
                feeds = candidate.get("feeds")
                if feeds is not None:
                    break

    if feeds is None:
        for candidate in data.values():
            if isinstance(candidate, dict):
                feeds = candidate.get("feeds")
                if feeds is not None:
                    break

    if not isinstance(feeds, list):
        raise ValueError("Impossible de trouver la liste GBFS feeds")

    urls = {
        item["name"]: urljoin(GBFS_URL, item["url"])
        for item in feeds
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("url"), str)
    }

    missing = set(FEEDS) - set(urls)
    if missing:
        raise ValueError(f"Feeds manquants: {sorted(missing)}")

    return urls


def parse_source_time(payload: dict) -> datetime:
    """Accepte un timestamp epoch ou ISO 8601."""
    value = payload.get("last_updated")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = datetime.fromtimestamp(value, tz=timezone.utc)

    elif isinstance(value, str):
        result = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        if result.tzinfo is None:
            raise ValueError("last_updated sans fuseau horaire")
        result = result.astimezone(timezone.utc)

    else:
        raise ValueError("last_updated manquant ou invalide")

    return result


def canonical_json(payload: dict) -> bytes:
    """Contenu JSON canonique pour calculer l'empreinte."""
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def save_bronze(payload: dict, feed_name: str) -> tuple[Path, bool]:
    """Stocke un instantané unique avec écriture atomique."""
    if feed_name not in FEEDS:
        raise ValueError(f"Feed inconnu : {feed_name}")

    stations = payload.get("data", {}).get("stations")
    if not isinstance(stations, list):
        raise ValueError(f"{feed_name}: liste stations absente")

    content = canonical_json(payload)
    digest = hashlib.sha256(content).hexdigest()

    try:
        timestamp = parse_source_time(payload)
    except ValueError:
        # Repli si le fournisseur omet last_updated :
        # date de collecte UTC, identité garantie par le hash.
        timestamp = datetime.now(timezone.utc)
        logger.warning(
            "%s: last_updated absent, utilisation de l'heure UTC",
            feed_name,
        )

    target_dir = (
        BRONZE_DIR
        / feed_name
        / f"dt={timestamp:%Y-%m-%d}"
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    target = target_dir / (
        f"{timestamp:%Y%m%dT%H%M%SZ}_{digest}.json"
    )

    if target.exists():
        return target, False

    # Ecriture temporaire dans le même répertoire.
    fd, temp_name = tempfile.mkstemp(
        prefix=".snapshot_",
        suffix=".tmp",
        dir=target_dir,
    )
    temp_path = Path(temp_name)

    try:
        with os.fdopen(fd, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())

        # Un lien dur évite de remplacer un fichier déjà publié
        # et échoue si un autre process l'a créé entre-temps.
        try:
            os.link(temp_path, target)
            return target, True
        except FileExistsError:
            return target, False
    finally:
        temp_path.unlink(missing_ok=True)


def ingest() -> None:
    """Collecte les deux flux depuis Grand Lyon."""
    discovery = fetch_json(GBFS_URL)
    urls = discover_feeds(discovery)

    logger.info("Source : %s", GBFS_URL)
    logger.info("Version GBFS : %s", discovery.get("version"))

    for feed_name in FEEDS:
        payload = fetch_json(urls[feed_name])

        logger.info(
            "%s | URL=%s | version=%s | stations=%s",
            feed_name,
            urls[feed_name],
            payload.get("version"),
            len(payload.get("data", {}).get("stations", [])),
        )

        path, created = save_bronze(payload, feed_name)
        logger.info(
            "%s | %s | %s",
            feed_name,
            "NOUVEAU" if created else "DEJA PRESENT",
            path,
        )


def replay(path: Path, feed_name: str) -> None:
    """Rejoue exactement un snapshot déjà enregistré."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    target, created = save_bronze(payload, feed_name)

    logger.info(
        "Replay | %s | %s",
        "NOUVEAU" if created else "DEJA PRESENT",
        target,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--feed", choices=FEEDS)
    args = parser.parse_args()

    if args.replay:
        if not args.feed:
            parser.error("--feed requis avec --replay")
        replay(args.replay, args.feed)
    else:
        ingest()


if __name__ == "__main__":
    main()
