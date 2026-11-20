"""Entraînement du modèle et production de l'artefact distribuable.

Ce qu'on distribue (S1) n'est pas "un modèle" mais un paquet :
- le pipeline sklearn complet (prétraitement + modèle) -> models/model.joblib
- ses métadonnées (version, features, hyperparamètres, métriques, empreinte SHA-256,
  versions des librairies, empreinte des données) -> models/metadata.json

S4 : chaque entraînement peut être tracé dans MLflow et enregistré dans le registre
(voir velov/tracking.py). Le serveur MLflow se désigne par MLFLOW_TRACKING_URI,
jamais en dur dans le code.

Usage :
    python -m velov.train --data data/velov_history.csv --out models/
    python -m velov.train --mlflow                  # trace le run
    python -m velov.train --mlflow --register       # trace et enregistre une version "challenger"
    python -m velov.train --data data/velov.duckdb --test-days 1   # table gold du pipeline (S3)
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

# Hyperparamètres par défaut : tout ce qui change le modèle est un paramètre tracé.
DEFAULT_PARAMS = {"max_iter": 200, "learning_rate": 0.1, "max_leaf_nodes": 31, "seed": 42, "test_days": 14}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def data_fingerprint(df: pd.DataFrame) -> str:
    """Empreinte du CONTENU de la table, indépendante du format et de l'ordre des lignes.

    Le SHA-256 d'un fichier .duckdb change dès que la base est réécrite, même à données égales :
    on hache les lignes, triées par la clé (station_id, timestamp). L'empreinte dépend de la
    version de pandas : elle sert à comparer deux runs d'un même environnement.
    """
    ordered = df[RAW_COLUMNS].sort_values(["station_id", "timestamp"]).reset_index(drop=True)
    return hashlib.sha256(pd.util.hash_pandas_object(ordered, index=False).to_numpy().tobytes()).hexdigest()


def load_table(path: Path) -> pd.DataFrame:
    """Lit la table d'entraînement : CSV du simulateur (S1-S2) ou table gold DuckDB (S3).

    La table gold est vérifiée contre le contrat Pandera avant tout entraînement (EX-12).
    Les imports sont locaux : duckdb et pandera ne font pas partie de l'image de serving.
    """
    if not path.exists():
        raise FileNotFoundError(f"{path} introuvable : lancer `python -m velov.data` ou le pipeline S3")
    if path.suffix == ".duckdb":
        from velov.contract import StationHourly, load

        return StationHourly.validate(load(str(path)), lazy=True)
    return pd.read_csv(path, parse_dates=["timestamp"])


def build_pipeline(params: dict) -> Pipeline:
    preprocess = ColumnTransformer(
        [
            ("station", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
            ("num", "passthrough", NUMERIC_FEATURES),
        ]
    )
    model = HistGradientBoostingRegressor(
        max_iter=params["max_iter"],
        learning_rate=params["learning_rate"],
        max_leaf_nodes=params["max_leaf_nodes"],
        random_state=params["seed"],
    )
    return Pipeline([("preprocess", preprocess), ("model", model)])


def temporal_split(df: pd.DataFrame, test_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Découpage temporel : on teste sur le futur, jamais sur un échantillon aléatoire."""
    ts = pd.to_datetime(df["timestamp"])
    cutoff = ts.max() - pd.Timedelta(days=test_days)
    train, test = df[ts <= cutoff], df[ts > cutoff]
    if train.empty or test.empty:
        raise ValueError("Split vide : augmenter l'historique ou réduire test_days")
    return train, test


def evaluate(model: Pipeline, test_df: pd.DataFrame) -> dict:
    """MAE du modèle et de la baseline de persistance sur la même fenêtre de test."""
    y = test_df[TARGET].to_numpy()
    pred = np.clip(model.predict(test_df[FEATURES]), 0, test_df["capacity"].to_numpy())
    peak = test_df["hour"].isin([7, 8, 9, 17, 18, 19]).to_numpy()  # heures locales de pointe
    return {
        "mae_model": round(float(mean_absolute_error(y, pred)), 3),
        # Baseline "persistance" : dans 1 h, autant de vélos que maintenant (EX-05).
        "mae_baseline_persistence": round(float(mean_absolute_error(y, test_df["bikes_available"].to_numpy())), 3),
        "mae_model_peak_hours": round(float(mean_absolute_error(y[peak], pred[peak])), 3),
    }


def train(
    data_path: Path,
    out_dir: Path,
    version: str = "0.1.0",
    test_days: int | None = None,
    seed: int | None = None,
    params: dict | None = None,
) -> dict:
    """Entraîne, évalue contre la baseline, écrit le paquet. Retourne les métadonnées."""
    params = {**DEFAULT_PARAMS, **(params or {})}
    if test_days is not None:
        params["test_days"] = test_days
    if seed is not None:
        params["seed"] = seed

    raw = load_table(data_path)
    frame = make_training_frame(raw)
    train_df, test_df = temporal_split(frame, params["test_days"])

    pipeline = build_pipeline(params)
    pipeline.fit(train_df[FEATURES], train_df[TARGET])
    metrics = evaluate(pipeline, test_df)

    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / MODEL_FILENAME
    joblib.dump(pipeline, model_path)

    ts = pd.to_datetime(frame["timestamp"])
    metadata = {
        "model_name": "velov-availability",
        "model_version": version,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "task": "Prédire le nombre de vélos disponibles à H+1 par station",
        "input_columns": RAW_COLUMNS,
        "features": FEATURES,
        "target": TARGET,
        "params": params,
        "metrics": {
            **metrics,
            "test_days": params["test_days"],
            "n_train": int(len(train_df)),
            "n_test": int(len(test_df)),
        },
        "data": {
            "path": str(data_path),
            "sha256": data_fingerprint(raw),
            "from": ts.min().isoformat(),
            "to": ts.max().isoformat(),
        },
        "artifact": {"file": MODEL_FILENAME, "sha256": sha256_of(model_path)},
        "runtime": {
            "python": platform.python_version(),
            "scikit-learn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
    }
    (out_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Entraîne le modèle Vélo'v.")
    parser.add_argument("--data", type=Path, default=Path("data/velov_history.csv"))
    parser.add_argument("--out", type=Path, default=Path("models"))
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--seed", type=int, default=DEFAULT_PARAMS["seed"])
    parser.add_argument("--learning-rate", type=float, default=DEFAULT_PARAMS["learning_rate"])
    parser.add_argument("--max-iter", type=int, default=DEFAULT_PARAMS["max_iter"])
    parser.add_argument("--test-days", type=int, default=DEFAULT_PARAMS["test_days"])
    parser.add_argument("--mlflow", action="store_true", help="tracer le run dans MLflow")
    parser.add_argument("--register", action="store_true", help="enregistrer le modèle (alias challenger)")
    args = parser.parse_args()

    params = {
        "seed": args.seed,
        "learning_rate": args.learning_rate,
        "max_iter": args.max_iter,
        "test_days": args.test_days,
    }
    metadata = train(args.data, args.out, version=args.version, params=params)
    m = metadata["metrics"]
    print(f"MAE modèle   : {m['mae_model']:.3f} vélos")
    print(f"MAE baseline : {m['mae_baseline_persistence']:.3f} vélos (persistance)")
    print(f"Artefact     : {args.out / MODEL_FILENAME} (sha256 {metadata['artifact']['sha256'][:12]}...)")
    if args.mlflow or args.register:
        from velov.tracking import log_training  # MLflow n'est pas requis pour servir le modèle

        info = log_training(metadata, args.out, register=args.register)
        print(f"Run MLflow   : {info['run_id']}")
        if info.get("version"):
            print(f"Registre     : {info['name']} version {info['version']} (alias challenger)")


if __name__ == "__main__":
    main()
