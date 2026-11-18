from pathlib import Path

import numpy as np
import pandas as pd

_REQUIRED = ["region_id", "year", "yield_c_ha"]


def _require_columns(df: pd.DataFrame) -> None:
    missing = [c for c in _REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"yield data is missing columns: {missing}")


def load_yields(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"region_id": str})
    _require_columns(df)
    if df[_REQUIRED].isna().any().any():
        raise ValueError("yield data contains missing values")
    if df.duplicated(["region_id", "year"]).any():
        raise ValueError("duplicate (region_id, year) rows")
    df = df.astype({"year": int, "yield_c_ha": float})
    return df.sort_values(["region_id", "year"]).reset_index(drop=True)


def detrended_anomaly_pct(df: pd.DataFrame, train_years: list[int]) -> pd.Series:
    _require_columns(df)
    result = pd.Series(np.nan, index=df.index, dtype=float)
    for region_id, group in df.groupby("region_id"):
        train = group[group["year"].isin(train_years)]
        if len(train) < 2:
            raise ValueError(f"region {region_id}: fewer than 2 training years")
        slope, intercept = np.polyfit(train["year"], train["yield_c_ha"], deg=1)
        trend = slope * group["year"] + intercept
        anomaly = (group["yield_c_ha"] - trend) / trend * 100
        result.loc[group.index] = anomaly.where(trend > 0)
    return result
