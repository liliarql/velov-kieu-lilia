"""Contrat de données côté entraînement (Pandera) : ce que train.py exige de la table gold.

dbt garantit le schéma au moment où la table est publiée (contrat enforced). Ce contrat-ci est vérifié
au moment où on la consomme : si gold a changé entre-temps, on refuse d'entraîner plutôt que de produire
un modèle faux en silence.

Pandera n'est utilisé qu'à l'entraînement : il n'est pas dans l'image de serving (requirements-dev.txt).
"""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa

# Part maximale de relevés bikes_available > capacity, même seuil que le test dbt bikes_within_capacity.
MAX_OVER_CAPACITY_RATIO = 0.01


def _over_capacity_ratio_ok(df: pd.DataFrame) -> bool:
    return bool((df["bikes_available"] > df["capacity"]).mean() <= MAX_OVER_CAPACITY_RATIO) if len(df) else True


GOLD_SCHEMA = pa.DataFrameSchema(
    {
        "station_id": pa.Column(int, pa.Check.ge(1)),
        "timestamp": pa.Column(pd.DatetimeTZDtype(tz="UTC")),
        "capacity": pa.Column(int, pa.Check.in_range(1, 100)),
        "bikes_available": pa.Column(int, pa.Check.ge(0)),
        "temperature": pa.Column(float, pa.Check.in_range(-30, 50)),
        "is_raining": pa.Column(bool),
    },
    checks=pa.Check(_over_capacity_ratio_ok, error=f"plus de {MAX_OVER_CAPACITY_RATIO:.0%} de bikes > capacity"),
    unique=["station_id", "timestamp"],
    strict=True,
    coerce=True,
)


def validate_gold(df: pd.DataFrame) -> pd.DataFrame:
    """Valide la table gold (lève pandera.errors.SchemaErrors avec tous les écarts) et la renvoie typée."""
    return GOLD_SCHEMA.validate(df, lazy=True)
