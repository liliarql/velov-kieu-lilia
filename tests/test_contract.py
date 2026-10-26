import pandas as pd
import pandera.errors
import pytest

from velov.contract import validate_gold
from velov.data import simulate


@pytest.fixture
def gold() -> pd.DataFrame:
    return simulate(days=2, n_stations=2)


def test_valid_gold_passes(gold):
    assert len(validate_gold(gold)) == len(gold)


def test_duplicate_station_hour_is_rejected(gold):
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_gold(pd.concat([gold, gold.head(1)], ignore_index=True))


def test_unknown_column_is_rejected(gold):
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_gold(gold.assign(extra=1))


def test_null_is_rejected(gold):
    gold.loc[0, "temperature"] = None
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_gold(gold)


def test_over_capacity_tolerated_below_threshold(gold):
    gold.loc[0, "bikes_available"] = gold.loc[0, "capacity"] + 1  # 1 ligne sur 96 > seuil de 1 %
    with pytest.raises(pandera.errors.SchemaErrors):
        validate_gold(gold)
    big = simulate(days=10, n_stations=2)  # 480 lignes : 1 violation < 1 %
    big.loc[0, "bikes_available"] = big.loc[0, "capacity"] + 1
    assert len(validate_gold(big)) == len(big)
