"""API de serving du modèle Vélo'v.
 
TP1, partie 3 : exposez le modèle. Mode : IA déclarée autorisée pour cette partie.
 
Endpoints attendus (niveaux du TP1 : Must, Should, Stretch) :
    POST /v1/predict        [Must]    une prédiction
    GET  /health            [Should]  liveness : le process répond (ne dépend pas du modèle)
    GET  /ready             [Should]  readiness : 200 si le modèle est chargé, 503 sinon
    GET  /v1/model, POST /v1/predict/batch   [Stretch]
 
Lancement :
    uvicorn velov.api.main:app --reload
"""
 
from __future__ import annotations
from velov.api.db import init_db, save_prediction


import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
 
import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException
 
from velov.api.schemas import PredictionRequest, PredictionResponse
from velov.features import FEATURES, add_features
from velov.train import METADATA_FILENAME, sha256_of
 
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("velov.api")
 
STATE: dict = {"model": None, "metadata": None}
 
 
def load_model(model_dir: Path) -> tuple[object, dict]:
    """Fourni : charge le modèle APRÈS avoir vérifié son empreinte SHA-256."""
    metadata_path = model_dir / METADATA_FILENAME
    if not metadata_path.exists():
        raise FileNotFoundError(f"{metadata_path} introuvable")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    model_path = model_dir / metadata["artifact"]["file"]
    if sha256_of(model_path) != metadata["artifact"]["sha256"]:
        raise RuntimeError(f"Empreinte invalide pour {model_path}")
    return joblib.load(model_path), metadata
 
 
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Fourni : exécuté une fois au démarrage (avant yield) et à l'arrêt (après yield)."""
    model_dir = Path(os.getenv("MODEL_DIR", "models"))
    try:
        STATE["model"], STATE["metadata"] = load_model(model_dir)
        logger.info("Modèle %s chargé", STATE["metadata"]["model_version"])
    except Exception:
        logger.exception("Échec du chargement du modèle depuis %s", model_dir)
    #TP2 init Postgres
    try:
        init_db()
    except Exception:
        logger.exception(
            "Impossible d'initialiser PostgreSQL. L'API reste disponible."
        )

    yield
    STATE.update(model=None, metadata=None)
 
 
app = FastAPI(title="Vélo'v availability API", version="1.0.0", lifespan=lifespan)
 
 
@app.get("/health")
def health() -> dict:
    """Liveness : le process répond. Ne dépend pas du modèle (TODO 5)."""
    return {"status": "ok"}
 
 
@app.get("/ready")
def ready() -> dict:
    """Readiness : 200 si le modèle est chargé, 503 sinon (TODO 6)."""
    if STATE["model"] is None:
        raise HTTPException(status_code=503, detail="Modèle non chargé")
    return {"status": "ready", "model_version": STATE["metadata"]["model_version"]}
 
 
@app.post("/v1/predict", response_model=PredictionResponse)
def predict(request: PredictionRequest) -> PredictionResponse:
    """Prédit le nombre de vélos disponibles à H+1 pour une station (TODO 7)."""
    if STATE["model"] is None:
        raise HTTPException(status_code=503, detail="Modèle non chargé")
 
    # Une seule ligne, avec les colonnes brutes attendues par add_features.
    row = pd.DataFrame(
        [
            {
                "station_id": request.station_id,
                "timestamp": request.timestamp,
                "capacity": request.capacity,
                "bikes_available": request.bikes_available,
                "temperature": request.temperature,
                "is_raining": request.is_raining,
            }
        ]
    )
    # On réutilise add_features (même code qu'à l'entraînement) pour éviter le training-serving skew.
    features = add_features(row)[FEATURES]
    predicted = float(STATE["model"].predict(features)[0])
    predicted = max(0.0, min(predicted, float(request.capacity)))  # borné entre 0 et capacity
 
    result = PredictionResponse(
    station_id=request.station_id,
    target_timestamp=request.timestamp + timedelta(hours=1),
    predicted_bikes=predicted,
    model_version=STATE["metadata"]["model_version"],
    )

    try:
        save_prediction(request, result)
    except Exception:
        logger.exception(
            "Échec de l'enregistrement de la prédiction en base"
        )

    return result