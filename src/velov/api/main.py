"""API de serving du modèle Vélo'v.

Endpoints :
    GET  /health            liveness : le process répond (ne dépend pas du modèle)
    GET  /ready             readiness : le modèle est chargé, on peut recevoir du trafic
    GET  /v1/model          métadonnées du modèle servi
    POST /v1/predict        une prédiction
    POST /v1/predict/batch  jusqu'à 1000 prédictions
    GET  /metrics           métriques Prometheus (S6) ; 404 vu de l'extérieur, filtré par le proxy

Horodatage : les timestamps entrants doivent porter un fuseau ; ils sont normalisés en UTC
(voir schemas.py) et les réponses sont en UTC.

Configuration (variables d'environnement, jamais en dur dans le code) :
    MODEL_DIR     dossier contenant model.joblib et metadata.json (défaut : models)
    MODEL_FORMAT  joblib (défaut) ou onnx, après python -m velov.export_onnx (S5)
    DATABASE_URL  optionnel ; si défini, chaque prédiction est journalisée (S2)

Lancement :
    uvicorn velov.api.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from prometheus_client import Gauge
from prometheus_fastapi_instrumentator import Instrumentator

from velov.api import storage
from velov.api.schemas import (
    BatchPredictionRequest,
    BatchPredictionResponse,
    PredictionRequest,
    PredictionResponse,
)
from velov.features import FEATURES, add_features
from velov.train import METADATA_FILENAME, sha256_of

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("velov.api")

# État du service, rempli au démarrage.
STATE: dict = {"model": None, "metadata": None, "engine": None}

# Seaux de latence de http_request_duration_seconds (S6 · 14) : une borne exactement au seuil du
# SLO (0.2 s, EX-16) pour calculer le SLI sans interpolation, et des bornes jusqu'à 5 s parce
# qu'en S5 le p95 montait à 4 s sous surcharge. Les seaux par défaut (0.1, 0.5, 1) n'ont pas de
# borne à 0.2 : « 95 % sous 200 ms » ne se calculerait pas.
LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1, 2.5, 5)

# Vaut toujours 1 : c'est le label qui porte l'information (quelle version tourne, utile en canary).
MODEL_INFO = Gauge("velov_model_info", "Modèle servi", ["model_version"])


def load_model(model_dir: Path) -> tuple[object, dict]:
    """Charge le modèle APRÈS avoir vérifié son empreinte.

    joblib/pickle exécute du code au chargement : on ne charge qu'un artefact dont
    l'empreinte correspond à celle enregistrée à l'entraînement.
    """
    metadata_path = model_dir / METADATA_FILENAME
    if not metadata_path.exists():
        raise FileNotFoundError(f"{metadata_path} introuvable")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    model_path = model_dir / metadata["artifact"]["file"]
    actual = sha256_of(model_path)
    if actual != metadata["artifact"]["sha256"]:
        raise RuntimeError(f"Empreinte invalide pour {model_path} : artefact modifié ou corrompu")
    return joblib.load(model_path), metadata


def load_engine(model_dir: Path) -> tuple[object, dict]:
    """Charge le moteur choisi par MODEL_FORMAT : joblib (défaut) ou onnx (S5).

    Les deux exposent predict(DataFrame). Pour ONNX, l'empreinte du fichier .onnx est vérifiée
    comme celle du joblib ; l'export (velov.export_onnx) l'a écrite dans metadata.json.
    """
    fmt = os.getenv("MODEL_FORMAT", "joblib")
    if fmt == "joblib":
        model, metadata = load_model(model_dir)
    elif fmt == "onnx":
        metadata = json.loads((model_dir / METADATA_FILENAME).read_text(encoding="utf-8"))
        if "onnx" not in metadata:
            raise RuntimeError("metadata.json sans section onnx : lancer python -m velov.export_onnx")
        onnx_path = model_dir / metadata["onnx"]["file"]
        if sha256_of(onnx_path) != metadata["onnx"]["sha256"]:
            raise RuntimeError(f"Empreinte invalide pour {onnx_path} : artefact modifié ou corrompu")
        from velov.export_onnx import OnnxPredictor  # onnxruntime n'est importé que si on le sert

        model = OnnxPredictor(onnx_path)
    else:
        raise ValueError(f"MODEL_FORMAT inconnu : {fmt!r} (joblib ou onnx)")
    return model, {**metadata, "served_format": fmt}


@asynccontextmanager
async def lifespan(app: FastAPI):
    model_dir = Path(os.getenv("MODEL_DIR", "models"))
    MODEL_INFO.clear()  # sans modèle chargé, aucune version n'est annoncée
    try:
        STATE["model"], STATE["metadata"] = load_engine(model_dir)
        MODEL_INFO.labels(model_version=STATE["metadata"]["model_version"]).set(1)
        logger.info(
            "Modèle %s chargé depuis %s (%s)",
            STATE["metadata"]["model_version"],
            model_dir,
            STATE["metadata"]["served_format"],
        )
    except Exception:
        # Le process démarre quand même : /health répond, /ready signale le problème.
        logger.exception("Échec du chargement du modèle depuis %s", model_dir)

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        try:
            STATE["engine"] = storage.init(database_url)
            logger.info("Journalisation des prédictions activée")
        except Exception:
            logger.exception("Base de données indisponible : journalisation désactivée")
    yield
    STATE.update(model=None, metadata=None, engine=None)
    MODEL_INFO.clear()


app = FastAPI(
    title="Vélo'v availability API",
    version="1.0.0",
    description="Prédit le nombre de vélos disponibles dans 1 h, par station.",
    lifespan=lifespan,
)

# Débit, erreurs et latence par route (golden signals, S6 · 12). handler est le gabarit de la route,
# jamais une donnée de la requête : peu de valeurs, donc peu de séries (cardinalité).
# http_requests_inprogress donne la saturation : le worker unique de S5 qui se remplit.
Instrumentator(
    excluded_handlers=["/metrics"],
    should_instrument_requests_inprogress=True,
    inprogress_labels=True,
).instrument(app, latency_lowr_buckets=LATENCY_BUCKETS).expose(app, include_in_schema=False)


@app.middleware("http")
async def add_process_time(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.2f}"
    return response


@app.get("/health", tags=["ops"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready", tags=["ops"])
def ready() -> dict:
    if STATE["model"] is None:
        raise HTTPException(status_code=503, detail="Modèle non chargé")
    return {"status": "ready", "model_version": STATE["metadata"]["model_version"]}


@app.get("/v1/model", tags=["model"])
def model_info() -> dict:
    if STATE["metadata"] is None:
        raise HTTPException(status_code=503, detail="Modèle non chargé")
    return STATE["metadata"]


def _predict(items: list[PredictionRequest]) -> list[PredictionResponse]:
    if STATE["model"] is None:
        raise HTTPException(status_code=503, detail="Modèle non chargé")
    raw = pd.DataFrame([item.model_dump() for item in items])
    features = add_features(raw)[FEATURES]  # même fonction qu'à l'entraînement
    preds = np.clip(STATE["model"].predict(features), 0, raw["capacity"].to_numpy())
    version = STATE["metadata"]["model_version"]
    return [
        PredictionResponse(
            station_id=item.station_id,
            target_timestamp=item.timestamp + timedelta(hours=1),
            predicted_bikes=round(float(p), 2),
            model_version=version,
        )
        for item, p in zip(items, preds, strict=True)
    ]


def _log(items: list[PredictionRequest], responses: list[PredictionResponse]) -> None:
    if STATE["engine"] is None:
        return
    try:
        storage.log_predictions(STATE["engine"], items, responses)
    except Exception:
        # La journalisation ne doit jamais faire échouer une prédiction.
        logger.exception("Échec de la journalisation des prédictions")


@app.post("/v1/predict", response_model=PredictionResponse, tags=["model"])
def predict(payload: PredictionRequest) -> PredictionResponse:
    response = _predict([payload])
    _log([payload], response)
    return response[0]


@app.post("/v1/predict/batch", response_model=BatchPredictionResponse, tags=["model"])
def predict_batch(payload: BatchPredictionRequest) -> BatchPredictionResponse:
    responses = _predict(payload.items)
    _log(payload.items, responses)
    return BatchPredictionResponse(predictions=responses)
