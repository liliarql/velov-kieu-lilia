
"""Contrat Pandera de la table Gold station_hourly."""

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series


class StationHourly(pa.DataFrameModel):
    station_id: Series[int] = pa.Field(gt=0, coerce=True)

    timestamp: Series[pd.DatetimeTZDtype] = pa.Field(
        dtype_kwargs={"unit": "us", "tz": "UTC"}
    )

    capacity: Series[int] = pa.Field(
        ge=1, le=80, coerce=True
    )

    bikes_available: Series[int] = pa.Field(
        ge=0, coerce=True
    )

    temperature: Series[float] = pa.Field(
        ge=-25, le=50
    )

    is_raining: Series[bool]

    @pa.dataframe_check
    def bikes_le_capacity(cls, df: pd.DataFrame):
        return df["bikes_available"] <= df["capacity"]

    class Config:
        strict = True
        unique = ["station_id", "timestamp"]
