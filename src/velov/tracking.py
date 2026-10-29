"""Traçage MLflow d'un entraînement et enregistrement dans le registre (S4).

Un run doit permettre de répondre, des semaines plus tard, à « d'où vient ce modèle ? » :
- paramètres : seed, learning_rate, max_iter, test_days
- métriques de test : mae_model, mae_baseline_persistence, mae_model_peak_hours
- tags : commit git, arbre modifié ou non, empreinte et période des données
- artefacts : le modèle MLflow (avec signature) ET le paquet distribué (model.joblib + metadata.json)

L'URL du serveur vient de l'environnement (MLFLOW_TRACKING_URI), jamais du code.
Chaque run est enregistré comme nouvelle version du modèle, avec l'alias « challenger » :
seule la règle de promotion (velov.promote) peut en faire le « champion ».
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import joblib
import mlflow
import pandas as pd
from mlflow import MlflowClient
from mlflow.models import infer_signature

from velov.features import FEATURES, make_training_frame
from velov.registry import CHALLENGER, EXPERIMENT, METRICS, PACKAGE_DIR, REGISTERED_MODEL
from velov.train import METADATA_FILENAME, MODEL_FILENAME

# Depuis MLflow 3.x, sklearn est sérialisé par défaut en skops (format sûr, sans exécution de
# code arbitraire), plus en pickle. skops refuse les types qu'il ne connaît pas : on déclare
# explicitement ceux qu'on a vérifiés.
SKOPS_TRUSTED_TYPES = ["sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor"]


def git_info() -> dict[str, str]:
    """Commit courant et état de l'arbre : un run issu d'un arbre modifié n'est pas reproductible."""

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()  # noqa: S603, S607

    try:
        return {"git_commit": git("rev-parse", "HEAD"), "git_dirty": str(bool(git("status", "--porcelain"))).lower()}
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": "unknown", "git_dirty": "unknown"}


def _signature_sample() -> pd.DataFrame:
    return make_training_frame(
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


def log_run(metadata: dict, out_dir: Path) -> tuple[str, str]:
    """Trace le run, enregistre le modèle et pose l'alias challenger. Renvoie (run_id, version)."""
    # MLflow affiche un emoji en fin de run : sans ça, une console Windows (cp1252) lève UnicodeEncodeError.
    sys.stdout.reconfigure(errors="replace")
    pipeline = joblib.load(out_dir / MODEL_FILENAME)
    sample = _signature_sample()

    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=f"v{metadata['model_version']}-seed{metadata['params']['seed']}") as run:
        mlflow.log_params({**metadata["params"], "model": "HistGradientBoostingRegressor"})
        mlflow.log_metrics({k: metadata["metrics"][k] for k in METRICS})
        mlflow.set_tags(
            {
                **git_info(),
                "data_sha256": metadata["data_sha256"],
                "data_start": metadata["data_period"]["start"],
                "data_end": metadata["data_period"]["end"],
                "model_version": metadata["model_version"],
            }
        )
        model_info = mlflow.sklearn.log_model(
            pipeline,
            name="model",
            signature=infer_signature(sample, pipeline.predict(sample)),
            input_example=sample,
            serialization_format="skops",
            skops_trusted_types=SKOPS_TRUSTED_TYPES,
        )
        # Le paquet réellement servi par l'API (S1) : c'est lui qu'on livre sur la VM.
        mlflow.log_artifact(str(out_dir / MODEL_FILENAME), artifact_path=PACKAGE_DIR)
        mlflow.log_artifact(str(out_dir / METADATA_FILENAME), artifact_path=PACKAGE_DIR)

    version = mlflow.register_model(model_info.model_uri, REGISTERED_MODEL).version
    MlflowClient().set_registered_model_alias(REGISTERED_MODEL, CHALLENGER, version)
    return run.info.run_id, str(version)
