"""Micro-benchmark des étapes de _predict(), sans réseau (TP5, étape 3).

Chronomètre les quatre phases du chemin de prédiction :
  1. Sérialisation Pydantic (model_dump)
  2. Construction des features (add_features)
  3. Prédiction du modèle (model.predict)
  4. Construction de la réponse (PredictionResponse)

Usage :
    python scripts/bench_predict.py --n 500
    python scripts/bench_predict.py --n 500 --batch 1 100 1000

IA déclarée : script généré avec Claude Code, corrigé pour utiliser perf_counter,
importer add_features/FEATURES depuis velov.features et exclure l'échauffement.
"""

from __future__ import annotations

import argparse
import random
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from velov.api.schemas import PredictionRequest, PredictionResponse
from velov.features import FEATURES, add_features


def make_request() -> PredictionRequest:
    """Génère une requête plausible (même distribution que le locustfile)."""
    capacity = random.randint(15, 40)
    instant = datetime.now(UTC) - timedelta(minutes=random.randint(0, 30 * 24 * 60))
    return PredictionRequest(
        station_id=random.randint(1, 20),
        timestamp=instant.replace(second=0, microsecond=0),
        capacity=capacity,
        bikes_available=random.randint(0, capacity),
        temperature=round(min(max(random.gauss(14, 7), -30), 50), 1),
        is_raining=random.random() < 0.08,
    )


def bench_steps(model, metadata: dict, items: list[PredictionRequest], n: int, warmup: int = 50):
    """Chronomètre chaque étape de _predict() sur n itérations."""
    times = {"model_dump": [], "add_features": [], "predict": [], "response": []}

    for i in range(warmup + n):
        # 1. Sérialisation Pydantic
        t0 = time.perf_counter()
        raw = pd.DataFrame([item.model_dump() for item in items])
        t1 = time.perf_counter()

        # 2. Construction des features
        features = add_features(raw)[FEATURES]
        t2 = time.perf_counter()

        # 3. Prédiction
        preds = np.clip(model.predict(features), 0, raw["capacity"].to_numpy())
        t3 = time.perf_counter()

        # 4. Construction de la réponse
        version = metadata["model_version"]
        [
            PredictionResponse(
                station_id=item.station_id,
                target_timestamp=item.timestamp + timedelta(hours=1),
                predicted_bikes=round(float(p), 2),
                model_version=version,
            )
            for item, p in zip(items, preds, strict=True)
        ]
        t4 = time.perf_counter()

        if i >= warmup:
            times["model_dump"].append((t1 - t0) * 1000)
            times["add_features"].append((t2 - t1) * 1000)
            times["predict"].append((t3 - t2) * 1000)
            times["response"].append((t4 - t3) * 1000)

    return times


def print_results(times: dict, batch_size: int):
    print(f"\n--- Batch size: {batch_size} ({len(times['model_dump'])} itérations, échauffement exclu) ---")
    print(f"{'Étape':<20} {'p50 (ms)':>10} {'p95 (ms)':>10} {'moyenne (ms)':>12}")
    print("-" * 55)
    total_p50 = 0
    total_p95 = 0
    for step, values in times.items():
        p50 = statistics.median(values)
        p95 = sorted(values)[int(len(values) * 0.95)]
        avg = statistics.mean(values)
        total_p50 += p50
        total_p95 += p95
        print(f"{step:<20} {p50:>10.3f} {p95:>10.3f} {avg:>12.3f}")
    print("-" * 55)
    print(f"{'TOTAL':<20} {total_p50:>10.3f} {total_p95:>10.3f}")


def main():
    parser = argparse.ArgumentParser(description="Benchmark des étapes de _predict()")
    parser.add_argument("--n", type=int, default=500, help="Nombre d'itérations (défaut 500)")
    parser.add_argument(
        "--batch", type=int, nargs="*", default=None, help="Tailles de lot à tester (défaut : 1 seule requête)"
    )
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    args = parser.parse_args()

    # Charger le modèle comme le fait l'API
    from velov.api.main import load_model

    model, metadata = load_model(args.model_dir)

    batch_sizes = args.batch if args.batch else [1]

    for bs in batch_sizes:
        items = [make_request() for _ in range(bs)]
        times = bench_steps(model, metadata, items, args.n)
        print_results(times, bs)


if __name__ == "__main__":
    main()
