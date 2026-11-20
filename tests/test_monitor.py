"""velov.monitor : lecture des prédictions journalisées, vérité à t + 1 h, décision d'alerte (S6, EX-21).

Evidently n'est pas installé en CI (requirements-monitoring.txt) : la dérive n'est pas testée ici,
seulement ce qui décide d'une alerte.
"""

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import insert

from velov import monitor
from velov.api import storage

T0 = datetime(2026, 11, 9, 0, tzinfo=UTC)


def _row(station, hour, bikes, predicted, created=None, version="0.1.0"):
    return {
        "created_at": created or datetime(2026, 11, 20, 10, tzinfo=UTC),
        "station_id": station,
        "observed_at": T0 + timedelta(hours=hour),
        "capacity": 20,
        "bikes_available": bikes,
        "temperature": 10.0,
        "is_raining": False,
        "predicted_bikes": predicted,
        "model_version": version,
    }


@pytest.fixture
def db_url(tmp_path):
    url = f"sqlite:///{tmp_path / 'pred.db'}"
    storage.init(url)
    return url


def _insert(url, rows):
    engine = storage.init(url)
    with engine.begin() as conn:
        conn.execute(insert(storage.predictions), rows)
    engine.dispose()


def test_truth_is_next_hour_same_station(db_url):
    _insert(db_url, [_row(1, 0, 5, 6.0), _row(1, 1, 8, 7.0), _row(2, 1, 3, 3.0)])
    df = monitor.load_current(db_url, "0.1.0", None).set_index(["station_id", "observed_at"])
    assert df.loc[(1, pd.Timestamp(T0)), "truth"] == 8  # observée à t + 1 h, même station
    assert pd.isna(df.loc[(1, pd.Timestamp(T0 + timedelta(hours=1))), "truth"])  # pas encore de vérité
    assert pd.isna(df.loc[(2, pd.Timestamp(T0 + timedelta(hours=1))), "truth"])  # jamais une autre station


def test_replayed_hour_keeps_last_prediction(db_url):
    early = datetime(2026, 11, 20, 9, tzinfo=UTC)
    late = datetime(2026, 11, 20, 11, tzinfo=UTC)
    _insert(db_url, [_row(1, 0, 5, 1.0, created=early), _row(1, 0, 5, 9.0, created=late)])
    df = monitor.load_current(db_url, "0.1.0", None)
    assert len(df) == 1
    assert df["predicted_bikes"].iloc[0] == 9.0


def test_since_excludes_older_traffic_and_other_versions(db_url):
    old = datetime(2026, 11, 17, 12, tzinfo=UTC)  # un test de charge passé
    _insert(db_url, [_row(1, 0, 5, 5.0, created=old), _row(2, 0, 5, 5.0), _row(3, 0, 5, 5.0, version="0.2.0")])
    df = monitor.load_current(db_url, "0.1.0", pd.Timestamp("2026-11-20T00:00:00Z"))
    assert df["station_id"].tolist() == [2]


def test_window_is_counted_from_last_observation(db_url):
    _insert(db_url, [_row(1, 0, 5, 5.0), _row(1, 24 * 8, 5, 5.0)])  # 8 jours d'écart
    df = monitor.load_current(db_url, "0.1.0", None)
    assert df["observed_at"].tolist() == [pd.Timestamp(T0 + timedelta(hours=24 * 8))]


def _perf(rows):
    return monitor.performance(
        pd.DataFrame(rows, columns=["station_id", "predicted_bikes", "bikes_available", "truth"])
    )


def test_station_worse_than_persistence_raises_alert_even_if_global_is_fine():
    good = [(1, 10.0, 8, 10)] * 60  # modèle exact, persistance à 2
    bad = [(2, 13.0, 10, 10)] * 60  # modèle à 3, persistance exacte
    perf = _perf(good + bad)
    assert perf["ratio"] == pytest.approx(1.5)
    alerts = monitor.decide(perf, seuil_global=2.0, seuil_station=1.0, min_station=50)
    assert alerts == ["station 2 : MAE 3.00 contre persistance 0.00 (ratio inf > 1.0)"]


def test_small_station_sample_is_not_judged():
    perf = _perf([(1, 13.0, 9, 10)] * 10)  # ratio 3, mais 10 paires seulement
    assert monitor.decide(perf, seuil_global=5.0, seuil_station=1.0, min_station=50) == []


def test_reference_is_scored_by_the_served_model(model_dir):
    ref, metadata = monitor.load_reference(model_dir.parent / "history.csv", model_dir)
    assert metadata["model_version"] == "0.0.0-test"
    assert ref["predicted_bikes"].between(0, ref["capacity"]).all()
    assert ref["truth"].notna().all()
