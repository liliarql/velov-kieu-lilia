
import logging
import os

import psycopg

from velov.api.schemas import PredictionRequest, PredictionResponse

logger = logging.getLogger("velov.db")


def init_db():
    """Creat prediction table."""
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        logger.info("DATABASE_URL not configured")
        return

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id BIGSERIAL PRIMARY KEY,
                station_id INTEGER NOT NULL,
                input_timestamp TIMESTAMPTZ NOT NULL,
                target_timestamp TIMESTAMPTZ NOT NULL,
                capacity INTEGER NOT NULL,
                bikes_available INTEGER NOT NULL,
                predicted_bikes DOUBLE PRECISION NOT NULL,
                model_version TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

    logger.info("Table prediction ok")


def save_prediction(
    request: PredictionRequest,
    response: PredictionResponse
):
    """save prediction in PostgreSQL."""
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        return

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        conn.execute(
            """
            INSERT INTO predictions (
                station_id,
                input_timestamp,
                target_timestamp,
                capacity,
                bikes_available,
                predicted_bikes,
                model_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                request.station_id,
                request.timestamp,
                response.target_timestamp,
                request.capacity,
                request.bikes_available,
                response.predicted_bikes,
                response.model_version,
            )
        )

    logger.info(
        "Prediction enregistrée pour station_id=%s",
        request.station_id
    )
