# ===== STAGE 1: BUILD =====

ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# Python virtual environment
RUN python -m venv /opt/venv

#  dependencies use Docker cache
COPY requirements.txt .
RUN pip install -r requirements.txt

# package of project Vélo'v
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-deps .

# Model trained at build time: a clean clone (models/ is not in git) still produces a working image.
# Simulated data with fixed seed -> reproducible artefact.
RUN python -m velov.data --out /build/data/velov_history.csv \
    && python -m velov.train --data /build/data/velov_history.csv --out /build/models


# ===== STAGE 2: RUNTIME =====

FROM ${PYTHON_IMAGE} AS runtime

# Image traceability: version + git commit (passed by compose, see compose.yaml)
ARG VERSION=dev
ARG GIT_COMMIT=unknown
LABEL org.opencontainers.image.title="velov-api" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}"

ENV PATH="/opt/venv/bin:$PATH" \
    MODEL_DIR="/app/models" \
    PYTHONUNBUFFERED=1 \
    APP_VERSION="${VERSION}" \
    GIT_COMMIT="${GIT_COMMIT}"

# User (not use Root)
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

# Copy env Python from stage builder
COPY --from=builder /opt/venv /opt/venv

# Copy model trained in stage builder
COPY --from=builder /build/models/ models/

# RUn API with user 
USER appuser

# Port  FastAPI
EXPOSE 8000

# Are API ready?
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3)"]

# Start FastAPI
CMD ["uvicorn", "velov.api.main:app", "--host", "0.0.0.0", "--port", "8000"]