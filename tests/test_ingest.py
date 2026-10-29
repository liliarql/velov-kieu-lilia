import json

import pytest

from velov import ingest


def test_bronze_replay_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "BRONZE_DIR", tmp_path)
    payload = {"last_updated": 1793000000, "data": {"stations": []}}
    first, created = ingest.save_bronze(payload, "station_status")
    second, replay_created = ingest.save_bronze(dict(reversed(list(payload.items()))), "station_status")
    assert created and not replay_created
    assert first == second
    assert json.loads(first.read_text()) == payload
    assert len(list(tmp_path.rglob("*.json"))) == 1
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("timestamp", [None, True, "bad", "2026-10-26T10:00:00", float("nan"), 1e30])
def test_invalid_source_time_never_writes_bronze(tmp_path, monkeypatch, timestamp):
    monkeypatch.setattr(ingest, "BRONZE_DIR", tmp_path / "bronze")
    with pytest.raises(ValueError):
        ingest.save_bronze({"last_updated": timestamp, "data": {"stations": []}}, "station_status")
    assert not (tmp_path / "bronze").exists()


def test_weather_replay_keeps_first_collection_time(tmp_path, monkeypatch):
    from velov import weather_ingest

    monkeypatch.setattr(weather_ingest, "BRONZE_DIR", tmp_path)
    payload = {"current": {"time": 1793000000, "temperature_2m": 15.0, "rain": 0.0}}
    path, created = weather_ingest.save_weather(payload)
    before = path.read_bytes()
    same, replay_created = weather_ingest.save_weather(payload)
    assert created and not replay_created and path == same
    assert path.read_bytes() == before
    assert len(list(tmp_path.rglob("*.json"))) == 1
