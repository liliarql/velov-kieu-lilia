#!/usr/bin/env bash
# Depuis une copie propre, apres installation de requirements-dev et pip install -e .
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pip check
python -m ruff check src tests
python -m pytest
# Une base jetable pour verifier aussi les commandes du README hors fixture pytest.
verify_dir=$(mktemp -d)
trap 'rm -rf -- "$verify_dir"' EXIT
export VELOV_DB_PATH="$verify_dir/demo.duckdb"
cd velov_dbt
dbt build --profiles-dir . --vars '{"as_of":"2026-10-26T16:20:00+01:00","bronze_dir":"../data/demo/bronze"}'
cd ..
python -m velov.train --data "$VELOV_DB_PATH" --validate-only

# Scenario 48 h synthetique : meme chaine dbt/Pandera puis vrai entrainement H+1.
python -m velov.demo --out "$verify_dir/synthetic"
export VELOV_DB_PATH="$verify_dir/synthetic.duckdb"
cd velov_dbt
dbt build --profiles-dir . --vars "{\"as_of\":\"2026-10-06T23:00:00Z\",\"bronze_dir\":\"$verify_dir/synthetic/bronze\"}"
cd ..
python -m velov.train --data "$VELOV_DB_PATH" --validate-only
python -m velov.train --data "$VELOV_DB_PATH" --out "$verify_dir/models" --test-days 1
