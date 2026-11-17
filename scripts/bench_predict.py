"""TP5 : chronométrer les quatre étapes de _predict(), sans HTTP ni PostgreSQL.

Le modèle et les entrées sont chargés avant les mesures. Chaque taille de lot
dispose de son propre échauffement, exclu des percentiles.
"""

from __future__ import annotations

import argparse
import json
import platform
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from velov.api.main import STATE, _predict, load_model
from velov.api.schemas import PredictionRequest, PredictionResponse
from velov.features import FEATURES, add_features


def measure(items, model, metadata):
    """Même calcul que _predict ; frontières des quatre étapes explicites."""
    start = perf_counter()
    raw = pd.DataFrame([item.model_dump() for item in items])
    after_raw = perf_counter()
    features = add_features(raw)[FEATURES]
    after_features = perf_counter()
    preds = np.clip(model.predict(features), 0, raw["capacity"].to_numpy())
    after_model = perf_counter()
    version = metadata["model_version"]
    responses = [
        PredictionResponse(
            station_id=item.station_id,
            target_timestamp=item.timestamp + timedelta(hours=1),
            predicted_bikes=round(float(p), 2),
            model_version=version,
        )
        for item, p in zip(items, preds, strict=True)
    ]
    end = perf_counter()
    times = np.diff([start, after_raw, after_features, after_model, end]) * 1000
    return responses, [*times, (end - start) * 1000]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=500)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--batch", nargs="+", type=int, default=[1])
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--payload", type=Path, default=Path("payload.json"))
    parser.add_argument("--out", type=Path, help="Conserver le protocole et les échantillons en JSON")
    args = parser.parse_args()
    if min(args.n, args.warmup, args.threads, *args.batch) < 1 or max(args.batch) > 1000:
        parser.error("n, warmup, threads et batch doivent être positifs ; batch <= 1000")

    payload = json.loads(args.payload.read_text())
    model, metadata = load_model(args.model_dir)
    STATE.update(model=model, metadata=metadata)
    report = {
        "date_utc": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "machine": platform.node(),
        "platform": platform.platform(),
        "threads": args.threads,
        "n": args.n,
        "warmup": args.warmup,
        "model_sha256": metadata["artifact"]["sha256"],
        "payload": payload,
        "scope": "Sans réseau, validation initiale, sérialisation JSON ou écriture PostgreSQL",
        "batches": [],
    }
    labels = ["DataFrame", "Features", "Predict + clip", "Réponses", "Total quatre étapes"]
    print("| Lot | Étape | p50 ms | p95 ms |")
    print("|---:|---|---:|---:|")
    with threadpool_limits(limits=args.threads):
        for size in args.batch:
            # Même entrée répétée : microbenchmark contrôlé, pas une simulation de trafic.
            items = [PredictionRequest.model_validate(payload) for _ in range(size)]
            responses, _ = measure(items, model, metadata)
            if responses != _predict(items):
                raise RuntimeError("Le benchmark ne reproduit pas les réponses de _predict")
            for _ in range(args.warmup):
                measure(items, model, metadata)
            samples = np.asarray([measure(items, model, metadata)[1] for _ in range(args.n)])
            stats = {}
            for i, label in enumerate(labels):
                p50, p95 = np.percentile(samples[:, i], [50, 95])
                stats[label] = {"p50_ms": float(p50), "p95_ms": float(p95)}
                print(f"| {size} | {label} | {p50:.4f} | {p95:.4f} |")

            # model.predict seul, features préparées hors du chronométrage.
            features = add_features(pd.DataFrame([item.model_dump() for item in items]))[FEATURES]
            for _ in range(args.warmup):
                model.predict(features)
            predict_samples = []
            for _ in range(args.n):
                start = perf_counter()
                model.predict(features)
                predict_samples.append((perf_counter() - start) * 1000)
            p50, p95 = np.percentile(predict_samples, [50, 95])
            stats["model.predict seul"] = {"p50_ms": float(p50), "p95_ms": float(p95)}
            print(f"| {size} | model.predict seul | {p50:.4f} | {p95:.4f} |")
            report["batches"].append(
                {"size": size, "stats": stats, "samples_ms": samples.tolist(), "predict_ms": predict_samples}
            )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
