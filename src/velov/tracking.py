"""Suivi des entraînements et registre de modèles avec MLflow (S4).

Un run MLflow répond à quatre questions sur un modèle, des mois plus tard :
quel code (commit git), quelles données (empreinte), quels paramètres, quels résultats.

Le serveur se désigne par la variable MLFLOW_TRACKING_URI :
    export MLFLOW_TRACKING_URI=http://192.168.1.227:5000   # serveur du compose S4
    export MLFLOW_TRACKING_URI=sqlite:///mlflow.db          # sans serveur, en local

Registre : chaque nouvelle version reçoit l'alias "challenger". Seul promote.py
déplace l'alias "champion", après comparaison sur les mêmes données.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import joblib
import mlflow
from mlflow import MlflowClient
from mlflow.models import infer_signature

from velov.features import FEATURES, RAW_COLUMNS, make_training_frame
from velov.registry import CHALLENGER, EXPERIMENT, MODEL_NAME

MODEL_FILENAME = "model.joblib"
SKOPS_TRUSTED_TYPES = ["sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor"]


def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], capture_output=True, text=True, check=True)  # noqa: S603, S607
    return out.stdout.strip()


def git_context() -> dict:
    """Commit courant et présence de modifications non commitées."""
    try:
        return {
            "git_commit": _git("rev-parse", "HEAD"),
            "git_dirty": str(bool(_git("status", "--porcelain", "--untracked-files=no"))).lower(),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": "unknown", "git_dirty": "unknown"}


def log_training(metadata: dict, out_dir: Path, register: bool = True) -> dict:
    """Trace un entraînement déjà réalisé par velov.train.train().

    Le run contient : paramètres, métriques, tags (commit, données),
    le modèle au format MLflow (skops) et le paquet de déploiement (joblib + metadata.json).
    """
    pipeline = joblib.load(out_dir / MODEL_FILENAME)

    # Échantillon pour la signature — recharge les données d'entraînement.
    from velov.train import load_training_data  # import local : évite le cycle au chargement

    raw = load_training_data(Path(metadata["data_path"]))
    sample = make_training_frame(raw.head(48))[FEATURES].head(5)

    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=f"v{metadata['model_version']}-seed{metadata['params']['seed']}") as run:
        mlflow.log_params(metadata["params"])
        mlflow.log_metrics({k: v for k, v in metadata["metrics"].items() if k.startswith("mae") and v is not None})
        mlflow.set_tags(
            {
                **metadata.get("git", {}),
                "data_sha256": metadata["data_sha256"],
                "data_start": metadata["data_period"]["start"],
                "data_end": metadata["data_period"]["end"],
                "model_version": metadata["model_version"],
            }
        )

        dataset = mlflow.data.from_pandas(
            raw[RAW_COLUMNS],
            source=Path(metadata["data_path"]).resolve().as_uri(),
            name="station_hourly",
            digest=metadata["data_sha256"][:8],
        )
        mlflow.log_input(dataset, context="training")

        info = mlflow.sklearn.log_model(
            pipeline,
            name="model",
            signature=infer_signature(sample, pipeline.predict(sample)),
            input_example=sample,
            serialization_format="skops",
            skops_trusted_types=SKOPS_TRUSTED_TYPES,
            registered_model_name=MODEL_NAME if register else None,
        )
        # Le paquet que l'API sait charger (S1), rangé avec le run qui l'a produit.
        mlflow.log_artifacts(str(out_dir), artifact_path="package")

    result = {"run_id": run.info.run_id, "name": MODEL_NAME, "version": None}
    if register:
        version = str(info.registered_model_version)
        client = MlflowClient()
        client.set_registered_model_alias(MODEL_NAME, CHALLENGER, version)
        for key in ("mae_model", "mae_baseline_persistence"):
            if metadata["metrics"].get(key) is not None:
                client.set_model_version_tag(MODEL_NAME, version, key, str(metadata["metrics"][key]))
        result["version"] = version
    return result


def load_registered(version: str):
    """Charge une version du registre (format skops, types déclarés)."""
    return mlflow.sklearn.load_model(f"models:/{MODEL_NAME}/{version}")


def version_by_alias(alias: str):
    """Version portant cet alias, ou None si l'alias n'existe pas encore."""
    try:
        return MlflowClient().get_model_version_by_alias(MODEL_NAME, alias)
    except mlflow.exceptions.MlflowException:
        return None
