"""Traçage et registre de bout en bout, sur un MLflow local (SQLite dans un dossier temporaire)."""

import json

import pytest

mlflow = pytest.importorskip("mlflow")

from velov import promote  # noqa: E402
from velov.registry import CHALLENGER, CHAMPION, REGISTERED_MODEL  # noqa: E402
from velov.tracking import log_run  # noqa: E402
from velov.train import METADATA_FILENAME, train  # noqa: E402


@pytest.fixture
def local_mlflow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # les artefacts (mlruns/) restent dans le dossier temporaire
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    yield mlflow.MlflowClient()
    mlflow.set_tracking_uri(None)


def test_run_is_traced_and_registered_as_challenger(model_dir, local_mlflow):
    metadata = json.loads((model_dir / METADATA_FILENAME).read_text(encoding="utf-8"))
    run_id, version = log_run(metadata, model_dir)

    run = local_mlflow.get_run(run_id)
    assert {"seed", "learning_rate", "max_iter", "test_days"} <= set(run.data.params)
    assert {"mae_model", "mae_baseline_persistence", "mae_model_peak_hours"} <= set(run.data.metrics)
    assert {"git_commit", "git_dirty", "data_sha256", "data_start", "data_end"} <= set(run.data.tags)
    artifacts = {a.path for a in local_mlflow.list_artifacts(run_id, "package")}
    assert artifacts == {"package/model.joblib", "package/metadata.json"}
    assert str(local_mlflow.get_model_version_by_alias(REGISTERED_MODEL, CHALLENGER).version) == version

    # Premier modèle du registre : il bat la baseline, la règle le promeut champion.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("sys.argv", ["promote"])
        promote.main()
    assert str(local_mlflow.get_model_version_by_alias(REGISTERED_MODEL, CHAMPION).version) == version


def test_same_seed_same_metrics(tmp_path):
    from velov.data import simulate

    data = tmp_path / "h.csv"
    simulate(days=20, n_stations=3, seed=0).to_csv(data, index=False)
    a = train(data, tmp_path / "a", test_days=3, seed=42)["metrics"]
    b = train(data, tmp_path / "b", test_days=3, seed=42)["metrics"]
    assert a == b


def test_deliver_then_rollback(tmp_path, local_mlflow):
    from velov.data import simulate
    from velov.deliver import deliver
    from velov.registry import PREVIOUS_CHAMPION

    def train_and_register(days: int, name: str) -> str:
        data = tmp_path / f"{name}.csv"
        simulate(days=days, n_stations=5, seed=0).to_csv(data, index=False)
        out = tmp_path / name
        return log_run(train(data, out, test_days=7), out)[1]

    def run_promote(*argv: str) -> None:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.argv", ["promote", *argv])
            promote.main()

    v1 = train_and_register(10, "court")  # historique court : MAE 1,59 (baseline 1,69)
    run_promote()
    v2 = train_and_register(20, "long")  # MAE 1,30 : gain net, pointe meilleure
    run_promote()
    assert str(local_mlflow.get_model_version_by_alias(REGISTERED_MODEL, PREVIOUS_CHAMPION).version) == v1

    served = tmp_path / "served"
    assert deliver(served)["registry"]["version"] == v2
    run_promote("--rollback")
    assert deliver(served)["registry"]["version"] == v1
    assert json.loads((served / METADATA_FILENAME).read_text(encoding="utf-8"))["registry"]["version"] == v1
