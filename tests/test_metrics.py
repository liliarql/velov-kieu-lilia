"""Métriques Prometheus de l'API (S6, EX-20)."""

from fastapi.testclient import TestClient


def test_metrics_expose_model_version(client):
    body = client.get("/metrics").text
    assert 'velov_model_info{model_version="0.0.0-test"} 1.0' in body


def test_latency_histogram_has_slo_bucket(client, valid_payload):
    # Le SLI (part des /v1/predict sous 200 ms) se lit sur le seau le="0.2" : il doit exister.
    assert client.post("/v1/predict", json=valid_payload).status_code == 200
    body = client.get("/metrics").text
    assert 'http_request_duration_seconds_bucket{handler="/v1/predict",le="0.2",method="POST"}' in body
    assert 'http_requests_total{handler="/v1/predict",method="POST",status="2xx"}' in body


def test_metrics_route_is_not_measured_nor_documented(client):
    client.get("/metrics")
    assert 'handler="/metrics"' not in client.get("/metrics").text
    assert "/metrics" not in client.get("/openapi.json").json()["paths"]


def test_no_model_version_without_model(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))  # dossier vide : modèle non chargé
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from velov.api.main import app

    with TestClient(app) as c:
        assert "velov_model_info{" not in c.get("/metrics").text
