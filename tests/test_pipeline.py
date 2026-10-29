"""Preuves EX-10/11/12 : vrais builds dbt dans des repertoires temporaires."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from velov.train import load_training_data, sha256_of_frame

ROOT = Path(__file__).resolve().parents[1]
AS_OF = "2026-10-26T16:20:00+01:00"


def build(work, *options, command="build", succeeds=True):
    env = os.environ.copy()
    env["VELOV_DB_PATH"] = str(work / "velov.duckdb")
    result = subprocess.run(  # noqa: S603 -- executable dbt de l'environnement de test
        [
            str(Path(sys.executable).parent / "dbt"),
            command,
            "--profiles-dir",
            ".",
            "--no-partial-parse",
            "--vars",
            json.dumps({"as_of": AS_OF, "bronze_dir": str(work / "bronze")}),
            *options,
        ],
        cwd=work / "velov_dbt",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) == succeeds, result.stdout + result.stderr
    return result.stdout


def rows(work, table="station_hourly"):
    assert table in {"station_hourly", "station_hourly_candidate"}
    with duckdb.connect(str(work / "velov.duckdb"), read_only=True) as con:
        return con.table(table).order("station_id, timestamp").fetchall()


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    work = tmp_path_factory.mktemp("tp3-baseline")
    shutil.copytree(
        ROOT / "velov_dbt", work / "velov_dbt", ignore=shutil.ignore_patterns("target", "logs", ".user.yml")
    )
    shutil.copytree(ROOT / "data/demo/bronze", work / "bronze")
    build(work)
    assert len(rows(work)) == 36
    return work


@pytest.fixture
def pipeline(tmp_path, baseline):
    work = tmp_path / "pipeline"
    shutil.copytree(baseline, work, ignore=shutil.ignore_patterns("target", "logs"))
    return work


def mutate_snapshot(work, feed, field, value):
    paths = sorted((work / "bronze" / feed).rglob("*.json"))
    # Modifier toutes les versions rend l'anomalie visible pour les lignes eligibles.
    for path in paths:
        payload = json.loads(path.read_text())
        for station in payload["data"]["stations"]:
            station[field] = value
        path.write_text(json.dumps(payload))


def test_ex10_gold_content_is_stable(pipeline):
    build(pipeline)
    before = sha256_of_frame(load_training_data(pipeline / "velov.duckdb"))
    build(pipeline)
    after = sha256_of_frame(load_training_data(pipeline / "velov.duckdb"))
    assert before == after


def test_ex11_future_and_late_arrivals_do_not_change_past_gold(pipeline):
    before = rows(pipeline)
    for feed in ["station_status", "station_information", "weather"]:
        source = sorted((pipeline / "bronze" / feed).rglob("*.json"))[-1]
        payload = json.loads(source.read_text())
        if feed == "weather":
            payload["collected_at"] += 86400
            payload["response"]["current"]["temperature_2m"] = 49.0
        else:
            payload["last_updated"] += 86400
            for station in payload["data"]["stations"]:
                if feed == "station_information":
                    station["capacity"] = 79
                else:
                    station["num_bikes_available"] = 0
            # Une correction tardive d'un ancien releve ne doit pas remplacer le passe.
        source.with_name("future.json").write_text(json.dumps(payload))
    build(pipeline)
    assert rows(pipeline) == before


@pytest.mark.parametrize(
    ("feed", "field", "value"),
    [
        ("station_status", "num_bikes_available", -1),
        ("station_status", "num_bikes_available", 999),
        ("station_status", "station_id", 0),
        ("station_information", "capacity", 81),
        ("station_information", "capacity", 0),
    ],
)
def test_ex12_bad_silver_blocks_and_preserves_gold(pipeline, feed, field, value):
    before = rows(pipeline)
    mutate_snapshot(pipeline, feed, field, value)
    log = build(pipeline, succeeds=False)
    assert "silver_status_quality" in log
    assert rows(pipeline) == before
    with duckdb.connect(str(pipeline / "velov.duckdb"), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM main_dbt_test__audit.silver_status_quality").fetchone()[0] > 0


@pytest.mark.parametrize("change", ["schema", "type"])
def test_ex12_dbt_contract_failure_preserves_gold(pipeline, change):
    before = rows(pipeline)
    path = pipeline / "velov_dbt/models/marts/station_hourly.sql"
    sql = path.read_text()
    if change == "schema":
        sql = sql.replace("AS temperature,", "AS unexpected_temperature,")
    else:
        sql = sql.replace("CAST(capacity AS INTEGER)", "CAST(capacity AS VARCHAR)")
    path.write_text(sql)
    log = build(pipeline, succeeds=False)
    assert "contract" in log.lower()
    assert rows(pipeline) == before


def test_ex12_no_first_publication_on_failure(pipeline):
    (pipeline / "velov.duckdb").unlink()
    mutate_snapshot(pipeline, "station_status", "num_bikes_available", 999)
    build(pipeline, succeeds=False)
    with duckdb.connect(str(pipeline / "velov.duckdb"), read_only=True) as con:
        count = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='station_hourly'"
        ).fetchone()[0]
        assert count == 0


def test_unchecked_or_partial_build_never_publishes(pipeline):
    before = rows(pipeline)
    mutate_snapshot(pipeline, "station_status", "num_bikes_available", 999)
    build(pipeline, command="run")
    assert any(row[3] == 999 for row in rows(pipeline, "station_hourly_candidate"))
    assert rows(pipeline) == before
    # Un candidat invalide ne peut pas etre publie en selectionnant seulement Gold.
    build(pipeline, "--select", "station_hourly", succeeds=False)
    assert rows(pipeline) == before


def test_validate_only_does_not_train(pipeline, tmp_path):
    out = tmp_path / "models"
    result = subprocess.run(  # noqa: S603 -- CLI locale, arguments de test
        [
            sys.executable,
            "-m",
            "velov.train",
            "--data",
            str(pipeline / "velov.duckdb"),
            "--validate-only",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Pandera PASS" in result.stdout
    assert not out.exists()


@pytest.mark.parametrize(
    ("expression", "replacement"),
    [
        ("CAST(capacity AS INTEGER)", "CAST(81 AS INTEGER)"),
        ("CAST(station_id AS INTEGER)", "CAST(0 AS INTEGER)"),
        ("CAST(bikes_available AS INTEGER)", "CAST(999 AS INTEGER)"),
        ("CAST(temperature AS DOUBLE)", "CAST(99 AS DOUBLE)"),
    ],
)
def test_ex12_bad_gold_values_preserve_public_table(pipeline, expression, replacement):
    before = rows(pipeline)
    path = pipeline / "velov_dbt/models/marts/station_hourly.sql"
    path.write_text(path.read_text().replace(expression, replacement))
    build(pipeline, succeeds=False)
    assert rows(pipeline, "station_hourly_candidate") != before
    assert rows(pipeline) == before


def test_successful_partial_build_cannot_publish(pipeline):
    before = rows(pipeline)
    mutate_snapshot(pipeline, "station_status", "num_bikes_available", 0)
    build(pipeline, command="run")
    assert rows(pipeline, "station_hourly_candidate") != before
    log = build(pipeline, "--select", "station_hourly")
    assert "Gold conserve" in log
    assert rows(pipeline) == before


def test_ex12_bad_weather_is_visible_before_gold(pipeline):
    before = rows(pipeline)
    for path in (pipeline / "bronze/weather").rglob("*.json"):
        payload = json.loads(path.read_text())
        payload["response"]["current"]["temperature_2m"] = 99.0
        path.write_text(json.dumps(payload))
    log = build(pipeline, succeeds=False)
    assert "silver_weather_quality" in log
    assert rows(pipeline) == before


def test_validate_only_rejects_invalid_public_gold(pipeline, tmp_path):
    with duckdb.connect(str(pipeline / "velov.duckdb")) as con:
        con.execute("UPDATE station_hourly SET capacity=81")
    out = tmp_path / "models"
    result = subprocess.run(  # noqa: S603 -- CLI locale, arguments de test
        [
            sys.executable,
            "-m",
            "velov.train",
            "--data",
            str(pipeline / "velov.duckdb"),
            "--validate-only",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "SchemaErrors" in result.stderr
    assert not out.exists()


def test_synthetic48h_gold_and_h1_training(pipeline, tmp_path):
    from velov.demo import generate
    from velov.train import train

    work = tmp_path / "synthetic"
    manifest = generate(work)
    original = {str(p.relative_to(work)): p.read_bytes() for p in work.rglob("*.json")}
    assert generate(work) == manifest
    assert {str(p.relative_to(work)): p.read_bytes() for p in work.rglob("*.json")} == original
    shutil.copytree(
        pipeline / "velov_dbt", work / "velov_dbt", ignore=shutil.ignore_patterns("target", "logs", ".user.yml")
    )
    build(work)
    gold = load_training_data(work / "velov.duckdb")
    assert len(gold) == 960
    assert gold["timestamp"].nunique() == 48
    metadata = train(work / "velov.duckdb", work / "models", test_days=1)
    assert metadata["metrics"]["n_train"] + metadata["metrics"]["n_test"] == 940
    assert (work / "models/model.joblib").is_file()
    assert (work / "models/metadata.json").is_file()
