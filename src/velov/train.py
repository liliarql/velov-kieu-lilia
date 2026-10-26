"""Entraînement du modèle v0 et production de l'artefact distribuable.

Ce qu'on distribue (S1) n'est pas "un modèle" mais un paquet :
- le pipeline sklearn complet (prétraitement + modèle) -> models/model.joblib
- ses métadonnées (version, features, métriques, empreinte SHA-256, versions des
  librairies, hash des données) -> models/metadata.json

Usage :
    python -m velov.train --data data/velov_history.csv --out models/
    python -m velov.train --data data/velov_history.csv --out models/ --mlflow
    python -m velov.train --source gold --out models/   # table gold du pipeline (S3), DATABASE_URL requis
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from velov.features import (
    CATEGORICAL_FEATURES,
    FEATURES,
    NUMERIC_FEATURES,
    RAW_COLUMNS,
    TARGET,
    make_training_frame,
)

logger = logging.getLogger("velov.train")

MODEL_FILENAME = "model.joblib"
GOLD_TABLE = "gold.gold_station_hourly"
METADATA_FILENAME = "metadata.json"
SKOPS_TRUSTED_TYPES = ["sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor"]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_pipeline(seed: int = 42) -> Pipeline:
    preprocess = ColumnTransformer(
        [
            ("station", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
            ("num", "passthrough", NUMERIC_FEATURES),
        ]
    )
    model = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.1, random_state=seed)
    return Pipeline([("preprocess", preprocess), ("model", model)])


def temporal_split(df: pd.DataFrame, test_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Découpage temporel : on teste sur le futur, jamais sur un échantillon aléatoire."""
    cutoff = pd.to_datetime(df["timestamp"]).max() - pd.Timedelta(days=test_days)
    ts = pd.to_datetime(df["timestamp"])
    train, test = df[ts <= cutoff], df[ts > cutoff]
    if train.empty or test.empty:
        raise ValueError("Split vide : augmenter l'historique ou réduire test_days")
    return train, test


def train(
    data_path: Path,
    out_dir: Path,
    version: str = "0.1.0",
    test_days: int = 14,
    seed: int = 42,
) -> dict:
    """Entraîne depuis un CSV (simulateur), évalue contre une baseline, écrit l'artefact."""
    if not data_path.exists():
        raise FileNotFoundError(f"{data_path} introuvable : lancer d'abord `python -m velov.data`")

    raw = pd.read_csv(data_path, parse_dates=["timestamp"])
    return train_frame(raw, out_dir, sha256_of(data_path), version=version, test_days=test_days, seed=seed)


def load_gold(database_url: str) -> pd.DataFrame:
    """Lit la table gold et vérifie le contrat Pandera AVANT tout entraînement.

    Les relevés bikes_available > capacity (tolérés sous le seuil du contrat) sont écartés :
    l'API les refuse en entrée, le modèle n'a pas à les apprendre.
    """
    import psycopg  # imports locaux : inutiles pour entraîner sur le simulateur (build de l'image)

    from velov.contract import validate_gold

    with psycopg.connect(database_url, connect_timeout=10) as conn:
        cur = conn.execute(f"SELECT {', '.join(RAW_COLUMNS)} FROM {GOLD_TABLE}")  # noqa: S608 (constantes)
        gold = pd.DataFrame(cur.fetchall(), columns=RAW_COLUMNS)

    gold = validate_gold(gold)
    over = gold["bikes_available"] > gold["capacity"]
    if over.any():
        logger.warning("%s relevés bikes_available > capacity écartés", int(over.sum()))
    return gold[~over].reset_index(drop=True)


def train_frame(
    raw: pd.DataFrame,
    out_dir: Path,
    data_sha256: str,
    version: str = "0.1.0",
    test_days: int = 14,
    seed: int = 42,
) -> dict:
    """Entraîne sur un DataFrame au schéma RAW_COLUMNS, évalue contre une baseline, écrit l'artefact."""
    frame = make_training_frame(raw)
    train_df, test_df = temporal_split(frame, test_days)

    pipeline = build_pipeline(seed)
    pipeline.fit(train_df[FEATURES], train_df[TARGET])

    y_test = test_df[TARGET]
    pred = np.clip(pipeline.predict(test_df[FEATURES]), 0, test_df["capacity"])
    mae_model = float(mean_absolute_error(y_test, pred))
    # Baseline "persistance" : dans 1 h, il y aura autant de vélos que maintenant.
    # Le modèle doit justifier sa complexité face à cette baseline (exigence EX-05).
    mae_baseline = float(mean_absolute_error(y_test, test_df["bikes_available"]))

    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / MODEL_FILENAME
    joblib.dump(pipeline, model_path)

    metadata = {
        "model_name": "velov-availability",
        "model_version": version,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "task": "Prédire le nombre de vélos disponibles à H+1 par station",
        "input_columns": RAW_COLUMNS,
        "features": FEATURES,
        "target": TARGET,
        "metrics": {
            "mae_model": round(mae_model, 3),
            "mae_baseline_persistence": round(mae_baseline, 3),
            "test_days": test_days,
            "n_train": int(len(train_df)),
            "n_test": int(len(test_df)),
        },
        "artifact": {"file": MODEL_FILENAME, "sha256": sha256_of(model_path)},
        "data_sha256": data_sha256,
        "runtime": {
            "python": platform.python_version(),
            "scikit-learn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
    }
    (out_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return metadata


def log_to_mlflow(metadata: dict, out_dir: Path) -> None:
    """Journalise le run et le modèle dans MLflow (aperçu S1, approfondi en S4)."""
    import mlflow  # import local : MLflow n'est pas requis pour servir le modèle
    from mlflow.models import infer_signature

    pipeline = joblib.load(out_dir / MODEL_FILENAME)
    sample = make_training_frame(
        pd.DataFrame(
            {
                "station_id": [1, 1],
                "timestamp": pd.to_datetime(["2026-06-01T06:00Z", "2026-06-01T07:00Z"]),
                "capacity": [20, 20],
                "bikes_available": [8, 5],
                "temperature": [21.0, 22.0],
                "is_raining": [False, False],
            }
        )
    )[FEATURES]

    mlflow.set_experiment("velov-availability")
    with mlflow.start_run(run_name=f"v{metadata['model_version']}"):
        mlflow.log_params({"model": "HistGradientBoostingRegressor", "test_days": metadata["metrics"]["test_days"]})
        mlflow.log_metrics({k: v for k, v in metadata["metrics"].items() if k.startswith("mae")})
        mlflow.sklearn.log_model(
            pipeline,
            name="model",
            signature=infer_signature(sample, pipeline.predict(sample)),
            input_example=sample,
            # Depuis MLflow 3.x, sklearn est sérialisé par défaut en skops (format sûr,
            # sans exécution de code arbitraire), plus en pickle. skops refuse les types
            # qu'il ne connaît pas : on déclare explicitement ceux qu'on a vérifiés.
            serialization_format="skops",
            skops_trusted_types=SKOPS_TRUSTED_TYPES,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Entraîne le modèle Vélo'v v0.")
    parser.add_argument("--source", choices=["csv", "gold"], default="csv", help="simulateur (csv) ou table gold")
    parser.add_argument("--data", type=Path, default=Path("data/velov_history.csv"))
    parser.add_argument("--out", type=Path, default=Path("models"))
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--test-days", type=int, default=14)
    parser.add_argument("--mlflow", action="store_true", help="journaliser aussi dans MLflow")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

    if args.source == "gold":
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise SystemExit("DATABASE_URL non défini (requis avec --source gold)")
        gold = load_gold(database_url)
        data_sha256 = hashlib.sha256(gold.to_csv(index=False).encode()).hexdigest()
        metadata = train_frame(gold, args.out, data_sha256, version=args.version, test_days=args.test_days)
    else:
        metadata = train(args.data, args.out, version=args.version, test_days=args.test_days)
    m = metadata["metrics"]
    print(f"MAE modèle   : {m['mae_model']:.3f} vélos")
    print(f"MAE baseline : {m['mae_baseline_persistence']:.3f} vélos (persistance)")
    print(f"Artefact     : {args.out / MODEL_FILENAME} (sha256 {metadata['artifact']['sha256'][:12]}...)")
    if args.mlflow:
        log_to_mlflow(metadata, args.out)
        print("Run journalisé dans MLflow (voir `mlflow ui`)")


if __name__ == "__main__":
    main()
