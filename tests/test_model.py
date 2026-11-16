"""EX-05 : le modèle doit battre la baseline de persistance, sinon la CI bloque."""

import json


def test_model_beats_persistence_baseline(model_dir):
    meta = json.loads((model_dir / "metadata.json").read_text())
    assert meta["metrics"]["mae_model"] < meta["metrics"]["mae_baseline_persistence"], (
        f"Le modèle (MAE {meta['metrics']['mae_model']:.3f}) ne bat pas "
        f"la persistance (MAE {meta['metrics']['mae_baseline_persistence']:.3f})"
    )


def test_metadata_traces_params_and_data(model_dir):
    meta = json.loads((model_dir / "metadata.json").read_text())
    for key in ("seed", "learning_rate", "max_iter", "test_days"):
        assert key in meta["params"], f"Paramètre manquant : {key}"
    assert "data_period" in meta
    assert "start" in meta["data_period"]
    assert "end" in meta["data_period"]
    assert "data_sha256" in meta
