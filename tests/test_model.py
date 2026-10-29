"""Test de modèle (EX-05) : exécuté en CI, il bloque un modèle qui ne bat pas la baseline."""

import json

from velov.train import METADATA_FILENAME


def test_model_beats_persistence_baseline(model_dir):
    metrics = json.loads((model_dir / METADATA_FILENAME).read_text(encoding="utf-8"))["metrics"]
    assert metrics["mae_model"] < metrics["mae_baseline_persistence"]


def test_metadata_traces_params_and_data(model_dir):
    metadata = json.loads((model_dir / METADATA_FILENAME).read_text(encoding="utf-8"))
    assert set(metadata["params"]) == {"seed", "learning_rate", "max_iter", "test_days"}
    assert "mae_model_peak_hours" in metadata["metrics"]
    assert metadata["data_period"]["start"] < metadata["data_period"]["end"]
