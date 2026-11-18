from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from wheatwatch.yields import detrended_anomaly_pct

RAW_TO_Z = {"ndvi_peak": "ndvi_z", "precip_mm": "precip_z", "tmax_mean_c": "tmax_z"}
RAW_COLUMNS = list(RAW_TO_Z)
Z_COLUMNS = list(RAW_TO_Z.values())
INDEX_COLUMN = "drought_index"
KEYS = ["region_id", "year"]
MIN_REGION_YEARS = 3
MIN_STD = 1e-12
VERDICT_WEAK_BELOW = 0.3
VERDICT_STRONG_FROM = 0.6
LIBERATED_DISTRICT_IDS: frozenset[str] = frozenset(
    {"jabrayil", "lachin", "zangilan", "kalbajar", "gubadli", "khojaly"}
)

Verdict = Literal["weak", "moderate", "strong"]


@dataclass(frozen=True)
class ValidationReport:
    """n_dropped counts held-out region-years only; training rows with NaN are not counted."""

    pearson_r: float
    rmse_pct: float
    hit_rate: float
    n_years: int
    n_regions: int
    n_dropped: int
    predictors: tuple[str, ...]
    verdict: Verdict
    baseline_rmse_pct: float

    @property
    def beats_baseline(self) -> bool:
        return self.rmse_pct < self.baseline_rmse_pct


def _fold_zscores(raw: pd.DataFrame, train_years: list[int], columns: list[str]) -> pd.DataFrame:
    """Per-region z-scores whose mean/std come from `train_years` rows only."""
    group = raw[raw["year"].isin(train_years)].groupby("region_id")[columns]
    mean, std, count = group.mean(), group.std(ddof=0), group.count()
    std = std.where((count >= 2) & (std > MIN_STD))
    mean_rows = mean.reindex(raw["region_id"]).to_numpy()
    std_rows = std.reindex(raw["region_id"]).to_numpy()
    z = (raw[columns].to_numpy(dtype=float) - mean_rows) / std_rows
    return pd.DataFrame(z, index=raw.index, columns=columns)


def _drought_index(z: pd.DataFrame) -> pd.Series:
    components = pd.DataFrame({"ndvi": z["ndvi_z"], "precip": z["precip_z"], "heat": -z["tmax_z"]})
    return components.mean(axis=1, skipna=True)


def _with_z(raw: pd.DataFrame, train_years: list[int]) -> pd.DataFrame:
    z = _fold_zscores(raw, train_years, RAW_COLUMNS).rename(columns=RAW_TO_Z)
    return z.assign(**{INDEX_COLUMN: _drought_index(z)})


def build_features(climate: pd.DataFrame, ndvi: pd.DataFrame) -> pd.DataFrame:
    merged = climate.merge(ndvi, on=KEYS, how="outer", validate="one_to_one")
    merged = merged[KEYS + RAW_COLUMNS].sort_values(KEYS).reset_index(drop=True)
    years = sorted(int(y) for y in merged["year"].unique())
    return pd.concat([merged, _with_z(merged, years)], axis=1)


def _verdict(r: float) -> Verdict:
    if r < VERDICT_WEAK_BELOW:
        return "weak"
    if r < VERDICT_STRONG_FROM:
        return "moderate"
    return "strong"


def _prepare(
    features: pd.DataFrame, yields: pd.DataFrame, exclude_ids: frozenset[str]
) -> pd.DataFrame:
    for name, frame, needed in (
        ("features", features, KEYS + RAW_COLUMNS),
        ("yields", yields, [*KEYS, "yield_c_ha"]),
    ):
        missing = [c for c in needed if c not in frame.columns]
        if missing:
            raise ValueError(f"{name} is missing columns: {missing}")
        if frame.duplicated(KEYS).any():
            raise ValueError(f"{name} has duplicate (region_id, year) rows")
    kept = yields[~yields["region_id"].isin(exclude_ids)]
    if kept["yield_c_ha"].isna().any():
        raise ValueError("yields contain missing values")
    counts = kept.groupby("region_id")["yield_c_ha"].agg(["count", "nunique"])
    bad = counts[(counts["count"] < MIN_REGION_YEARS) | (counts["nunique"] < 2)]
    if not bad.empty:
        raise ValueError(f"regions with fewer than 3 years or constant yield: {list(bad.index)}")
    merged = kept[[*KEYS, "yield_c_ha"]].merge(features[KEYS + RAW_COLUMNS], on=KEYS, how="left")
    return merged.sort_values(KEYS).reset_index(drop=True)


def _loyo_predictions(
    features: pd.DataFrame,
    yields: pd.DataFrame,
    single_predictor: bool,
    exclude_ids: frozenset[str],
) -> tuple[pd.DataFrame, int]:
    data = _prepare(features, yields, exclude_ids)
    years = sorted(int(y) for y in data["year"].unique())
    if len(years) < 3:
        raise ValueError("need at least 3 distinct years for leave-one-year-out")
    predictors = [INDEX_COLUMN] if single_predictor else Z_COLUMNS
    folds: list[pd.DataFrame] = []
    n_dropped = 0
    for held_out in years:
        train_years = [y for y in years if y != held_out]
        x = _with_z(data, train_years)[predictors]
        anomaly = detrended_anomaly_pct(data, train_years)
        usable = x.notna().all(axis=1) & anomaly.notna()
        is_test = data["year"] == held_out
        n_dropped += int((is_test & ~usable).sum())
        train = ~is_test & usable
        test = is_test & usable
        if int(train.sum()) < len(predictors) + 1:
            raise ValueError(f"fold {held_out}: too few training rows to fit the regression")
        if not test.any():
            continue
        design = np.column_stack([np.ones(int(train.sum())), x[train].to_numpy()])
        coef, *_ = np.linalg.lstsq(design, anomaly[train].to_numpy(), rcond=None)
        test_design = np.column_stack([np.ones(int(test.sum())), x[test].to_numpy()])
        folds.append(
            pd.DataFrame(
                {
                    "region_id": data.loc[test, "region_id"].to_numpy(),
                    "year": held_out,
                    "predicted": test_design @ coef,
                    "observed": anomaly[test].to_numpy(),
                }
            )
        )
    if not folds:
        raise ValueError("no held-out predictions could be made")
    return pd.concat(folds, ignore_index=True), n_dropped


def loyo_validate(
    features: pd.DataFrame,
    yields: pd.DataFrame,
    single_predictor: bool = False,
    *,
    exclude_ids: frozenset[str] = frozenset(),
) -> ValidationReport:
    predictions, n_dropped = _loyo_predictions(features, yields, single_predictor, exclude_ids)
    predicted = predictions["predicted"].to_numpy()
    observed = predictions["observed"].to_numpy()
    if np.ptp(predicted) < MIN_STD or np.ptp(observed) < MIN_STD:
        raise ValueError("constant predictions or observations: correlation is undefined")
    r = float(np.corrcoef(predicted, observed)[0, 1])
    return ValidationReport(
        pearson_r=r,
        rmse_pct=float(np.sqrt(np.mean((predicted - observed) ** 2))),
        hit_rate=float(np.mean((predicted < 0) == (observed < 0))),
        n_years=int(predictions["year"].nunique()),
        n_regions=int(predictions["region_id"].nunique()),
        n_dropped=n_dropped,
        predictors=tuple([INDEX_COLUMN] if single_predictor else Z_COLUMNS),
        verdict=_verdict(r),
        baseline_rmse_pct=float(np.sqrt(np.mean(observed**2))),
    )


def validate_with_sensitivity(
    features: pd.DataFrame, yields: pd.DataFrame, *, single_predictor: bool = True
) -> tuple[ValidationReport, ValidationReport]:
    everyone = loyo_validate(features, yields, single_predictor)
    without = loyo_validate(features, yields, single_predictor, exclude_ids=LIBERATED_DISTRICT_IDS)
    return everyone, without
