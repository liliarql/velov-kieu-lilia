"""Entraînement du modèle v0 et production de l'artefact distribuable.

Ce qu'on distribue (S1) n'est pas "un modèle" mais un paquet :
- le pipeline sklearn complet (prétraitement + modèle) -> models/model.joblib
- ses métadonnées (version, features, métriques, empreinte SHA-256, versions des
  librairies, hash des données) -> models/metadata.json

Usage :
    python -m velov.train --data data/velov_history.csv --out models/
    python -m velov.train --data data/velov_history.csv --out models/ --mlflow
"""

from __future__ import annotations

import argparse
import hashlib
import json
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

MODEL_FILENAME = "model.joblib"
METADATA_FILENAME = "metadata.json"
SKOPS_TRUSTED_TYPES = ["sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor"]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_of_frame(df: pd.DataFrame) -> str:
    """Empreinte du contenu normalise, independante du fichier CSV/DuckDB."""
    ordered = df[RAW_COLUMNS].copy()
    if not isinstance(ordered["timestamp"].dtype, pd.DatetimeTZDtype):
        raise ValueError("timestamp doit avoir un fuseau horaire")
    ordered["timestamp"] = ordered["timestamp"].dt.tz_convert("UTC").astype("datetime64[us, UTC]")
    for column in ("station_id", "capacity", "bikes_available"):
        ordered[column] = ordered[column].astype("int64")
    ordered["temperature"] = ordered["temperature"].astype("float64")
    ordered["is_raining"] = ordered["is_raining"].astype(bool)
    ordered = ordered.sort_values(["station_id", "timestamp"])
    values = pd.util.hash_pandas_object(ordered, index=False).to_numpy().tobytes()
    return hashlib.sha256(values).hexdigest()


def build_pipeline(seed: int = 42, learning_rate: float = 0.1, max_iter: int = 200) -> Pipeline:
    preprocess = ColumnTransformer(
        [
            ("station", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
            ("num", "passthrough", NUMERIC_FEATURES),
        ]
    )
    model = HistGradientBoostingRegressor(max_iter=max_iter, learning_rate=learning_rate, random_state=seed)
    return Pipeline([("preprocess", preprocess), ("model", model)])


def temporal_split(df: pd.DataFrame, test_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Découpage temporel : on teste sur le futur, jamais sur un échantillon aléatoire."""
    if test_days <= 0:
        raise ValueError("test_days doit etre strictement positif")
    cutoff = pd.to_datetime(df["timestamp"]).max() - pd.Timedelta(days=test_days)
    ts = pd.to_datetime(df["timestamp"])
    train, test = df[ts <= cutoff], df[ts > cutoff]
    if train.empty or test.empty:
        raise ValueError("Split vide : augmenter l'historique ou réduire test_days")
    return train, test


def load_training_data(data_path: Path) -> pd.DataFrame:
    """Charge CSV ou Gold DuckDB ; valide le Gold avant tout calcul de features."""
    if not data_path.exists():
        raise FileNotFoundError(f"Source introuvable : {data_path}")
    if data_path.suffix != ".duckdb":
        # round_trip preserve les flottants du CSV pour la meme empreinte de table.
        raw = pd.read_csv(data_path, parse_dates=["timestamp"], float_precision="round_trip")
    else:
        # Imports locaux : aucune dependance pipeline ajoutee au serving.
        import duckdb

        with duckdb.connect(str(data_path.resolve()), read_only=True) as con:
            raw = con.execute("SELECT * FROM main.station_hourly ORDER BY station_id, timestamp").df()
    if raw.empty:
        raise ValueError("Source d'entrainement vide")
    if not isinstance(raw["timestamp"].dtype, pd.DatetimeTZDtype):
        raise ValueError("timestamp doit avoir un fuseau horaire")
    raw["timestamp"] = raw["timestamp"].dt.tz_convert("UTC").astype("datetime64[us, UTC]")
    if data_path.suffix == ".duckdb":
        from velov.contracts import StationHourly

        raw = StationHourly.validate(raw, lazy=True)
        print(f"Pandera PASS : {len(raw)} lignes Gold valides")
        print(f"Stations : {raw['station_id'].nunique()}")
        print(f"Heures distinctes : {raw['timestamp'].nunique()}")
    return raw


def evaluate(pipeline: Pipeline, frame: pd.DataFrame) -> dict:
    """MAE sur la meme table test ; pointe 7-9h et 17-19h, heure de Lyon."""
    y_test = frame[TARGET].to_numpy()
    pred = np.clip(pipeline.predict(frame[FEATURES]), 0, frame["capacity"].to_numpy())
    hours = frame["timestamp"].dt.tz_convert("Europe/Paris").dt.hour
    peak = hours.isin([7, 8, 9, 17, 18, 19]).to_numpy()
    return {
        "mae_model": float(mean_absolute_error(y_test, pred)),
        "mae_baseline_persistence": float(mean_absolute_error(y_test, frame["bikes_available"])),
        "mae_model_peak_hours": float(mean_absolute_error(y_test[peak], pred[peak])) if peak.any() else None,
    }


def train(
    data_path: Path,
    out_dir: Path,
    version: str = "0.1.0",
    test_days: int = 14,
    seed: int = 42,
    learning_rate: float = 0.1,
    max_iter: int = 200,
) -> dict:
    """Entraîne, évalue contre une baseline, écrit l'artefact. Retourne les métadonnées."""
    raw = load_training_data(data_path)

    if data_path.suffix == ".duckdb" and raw["timestamp"].nunique() < 2:
        raise ValueError(
            "Contrat Pandera valide, mais historique Gold insuffisant "
            "pour construire une cible H+1. "
            "Continuer la collecte des données."
        )

    frame = make_training_frame(raw)
    train_df, test_df = temporal_split(frame, test_days)

    pipeline = build_pipeline(seed, learning_rate, max_iter)
    pipeline.fit(train_df[FEATURES], train_df[TARGET])

    metrics = evaluate(pipeline, test_df)
    from velov.tracking import git_context

    provenance = git_context()
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
            **metrics,
            "test_days": test_days,
            "n_train": int(len(train_df)),
            "n_test": int(len(test_df)),
        },
        "artifact": {"file": MODEL_FILENAME, "sha256": sha256_of(model_path)},
        "data_period": {
            "start": pd.to_datetime(raw["timestamp"]).min().isoformat(),
            "end": pd.to_datetime(raw["timestamp"]).max().isoformat(),
        },
        "data_sha256": sha256_of_frame(raw),
        "data_fingerprint_method": "sha256-pandas-normalized-rows-v1",
        "git": provenance,
        "runtime": {
            "python": platform.python_version(),
            "scikit-learn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
        "params": {"seed": seed, "learning_rate": learning_rate, "max_iter": max_iter, "test_days": test_days},
    }
    (out_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return metadata


def log_to_mlflow(metadata: dict, out_dir: Path) -> dict:
    """Compatibilite : implementation TP4 dans velov.tracking."""
    from velov.tracking import log_training

    return log_training(metadata, out_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Entraîne le modèle Vélo'v v0.")
    parser.add_argument("--data", type=Path, default=Path("data/velov_history.csv"))
    parser.add_argument("--out", type=Path, default=Path("models"))
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--mlflow", action="store_true", help="journaliser aussi dans MLflow")
    parser.add_argument("--validate-only", action="store_true", help="valider Gold sans entrainer ni ecrire de modele")
    parser.add_argument("--test-days", type=int, default=14)
    parser.add_argument("--seed", type=int, default=42, help="Graine aleatoire pour entrainer le modele")
    parser.add_argument("--learning-rate", type=float, default=0.1)
    parser.add_argument("--max-iter", type=int, default=200)
    args = parser.parse_args()

    if args.validate_only:
        if args.data.suffix != ".duckdb":
            parser.error("--validate-only necessite une table Gold .duckdb")
        load_training_data(args.data)
        return

    metadata = train(
        args.data,
        args.out,
        version=args.version,
        test_days=args.test_days,
        seed=args.seed,
        learning_rate=args.learning_rate,
        max_iter=args.max_iter,
    )
    m = metadata["metrics"]
    print(f"MAE modèle   : {m['mae_model']:.3f} vélos")
    print(f"MAE baseline : {m['mae_baseline_persistence']:.3f} vélos (persistance)")
    print(f"Artefact     : {args.out / MODEL_FILENAME} (sha256 {metadata['artifact']['sha256'][:12]}...)")
    if args.mlflow:
        logged = log_to_mlflow(metadata, args.out)
        print(f"Run MLflow : {logged['run_id']}")
        print("Run journalisé dans MLflow (voir `mlflow ui`)")


if __name__ == "__main__":
    main()
