"""Demo Bronze SYNTHETIQUE de 48 h, separee des archives reelles.

Disponibilite simulee : chaque status/meteo est connu exactement a son heure UTC.
Ce scenario ne prouve pas la disponibilite historique des API reelles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from velov.data import simulate


def _write(path: Path, payload: dict) -> None:
    content = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"Refus de remplacer un fichier different : {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def generate(out: Path) -> dict:
    """Produit un scenario fixe : 20 stations, 48 heures UTC, graine 42."""
    frame = simulate(days=2, n_stations=20, start="2026-10-05T02:00:00", seed=42)

    def snapshot(feed, epoch, payload):
        payload["simulation"] = True
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        timestamp = datetime.fromtimestamp(epoch, tz=UTC)
        path = out / "bronze" / feed / f"dt={timestamp:%Y-%m-%d}" / f"{timestamp:%Y%m%dT%H%M%SZ}_{digest}.json"
        _write(path, payload)

    first = frame.sort_values("timestamp").groupby("station_id").head(1)
    first_epoch = int(frame["timestamp"].min().timestamp())
    stations = [
        {
            "station_id": str(row.station_id),
            "capacity": int(row.capacity),
            "name": f"SYNTHETIQUE {row.station_id}",
            "address": "Scenario de test",
            "lat": 45.76,
            "lon": 4.84,
        }
        for row in first.itertuples()
    ]
    snapshot(
        "station_information",
        first_epoch,
        {"last_updated": first_epoch, "version": "2.3", "data": {"stations": stations}},
    )
    for timestamp, group in frame.groupby("timestamp", sort=True):
        epoch = int(timestamp.timestamp())
        status = [
            {
                "station_id": str(row.station_id),
                "last_reported": epoch,
                "num_bikes_available": int(row.bikes_available),
                "num_docks_available": int(row.capacity - row.bikes_available),
                "is_installed": True,
                "is_renting": True,
                "is_returning": True,
            }
            for row in group.itertuples()
        ]
        snapshot("station_status", epoch, {"last_updated": epoch, "version": "2.3", "data": {"stations": status}})
        weather = group.iloc[0]
        snapshot(
            "weather",
            epoch,
            {
                "source": "synthetic-48h",
                "collected_at": epoch,
                "response": {
                    "current": {
                        "time": epoch,
                        "temperature_2m": float(weather["temperature"]),
                        "rain": 0.4 if bool(weather["is_raining"]) else 0.0,
                    }
                },
            },
        )
    manifest = {
        "source": "SYNTHETIC - not a real API collection",
        "seed": 42,
        "hours": 48,
        "stations": 20,
        "availability_assumption": "Every observation is available at its simulated UTC timestamp",
        "as_of": frame["timestamp"].max().isoformat(),
        "expected_gold_rows": 960,
    }
    _write(out / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/demo48h"))
    args = parser.parse_args()
    print(json.dumps(generate(args.out), indent=2))


if __name__ == "__main__":
    main()
