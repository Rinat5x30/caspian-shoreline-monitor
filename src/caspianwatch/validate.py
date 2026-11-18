"""Pre-registered validation of shoreline displacement against sea level.

Rules are fixed in docs/caspian/validation-protocol.md. The primary path uses only its two
input series (indexed by calendar year); D is positive for retreat of the sea.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray

if TYPE_CHECKING:
    from caspianwatch.landsat import WaterMask

R_DIFF_MIN: float = 0.5
N_BOOT: int = 2000
MIN_COMMON_YEARS: int = 10
MAX_DEGENERATE_SHARE = 0.05
CI_PERCENTILES = (2.5, 97.5)
BLOCK_LEN = 3

Verdict = Literal["confirmed", "not_confirmed", "not_measurable"]
Overall = Literal["confirmed", "partial", "not_confirmed"]
FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class SiteReport:
    site_id: str
    verdict: Verdict
    reason: str
    n_years: int
    rmse_model: float
    rmse_baseline: float
    r_diff: float
    r_diff_ci_low: float
    raw_r: float
    n_pairs: int = 0
    slope_b: float = math.nan
    ci_high: float = math.nan
    degenerate_share: float = math.nan


def common_years(displacement: pd.Series, level: pd.Series) -> pd.DataFrame:
    frame = pd.DataFrame({"d": displacement, "l": level}).astype(float)
    return frame.dropna().sort_index()


def paired_differences(displacement: pd.Series, level: pd.Series) -> pd.DataFrame:
    frame = pd.DataFrame({"d": displacement, "l": level}).astype(float).sort_index()
    prev = frame.rename(columns={"d": "d_prev", "l": "l_prev"})
    prev.index = prev.index + 1
    both = frame.join(prev, how="inner").dropna()
    return pd.DataFrame({"dd": both["d"] - both["d_prev"], "dl": both["l"] - both["l_prev"]})


def pearson(x: FloatArray, y: FloatArray) -> float:
    if len(x) < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return math.nan
    return float(np.corrcoef(x, y)[0, 1])


def _resample_corr(xs: FloatArray, ys: FloatArray) -> tuple[FloatArray, float]:
    degenerate = (np.ptp(xs, axis=1) == 0) | (np.ptp(ys, axis=1) == 0)
    xc = xs - xs.mean(axis=1, keepdims=True)
    yc = ys - ys.mean(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = (xc * yc).sum(axis=1) / np.sqrt((xc**2).sum(axis=1) * (yc**2).sum(axis=1))
    return r[~degenerate], float(degenerate.mean())


def _interval(r: FloatArray, share: float) -> tuple[float, float, float]:
    if share > MAX_DEGENERATE_SHARE or len(r) == 0:
        return math.nan, math.nan, share
    low, high = np.percentile(r, CI_PERCENTILES)
    return float(low), float(high), share


def bootstrap_ci(
    x: FloatArray, y: FloatArray, seed: int = 0, n_boot: int | None = None
) -> tuple[float, float, float]:
    n = len(x)
    if n < 3:
        return math.nan, math.nan, math.nan
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot or N_BOOT, n))
    return _interval(*_resample_corr(x[idx], y[idx]))


def block_bootstrap_ci(
    x: FloatArray, y: FloatArray, seed: int = 0, n_boot: int | None = None
) -> tuple[float, float, float]:
    n = len(x)
    if n < max(3, BLOCK_LEN):
        return math.nan, math.nan, math.nan
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(n / BLOCK_LEN)
    starts = rng.integers(0, n - BLOCK_LEN + 1, size=(n_boot or N_BOOT, n_blocks))
    idx = (starts[:, :, None] + np.arange(BLOCK_LEN)).reshape(len(starts), -1)[:, :n]
    return _interval(*_resample_corr(x[idx], y[idx]))


def _fit(x: FloatArray, y: FloatArray) -> tuple[float, float]:
    centre = float(x.mean())
    design = np.column_stack([np.ones(len(x)), x - centre])
    (a, b), *_ = np.linalg.lstsq(design, y, rcond=None)
    return float(a - b * centre), float(b)


def loyo_predictions(displacement: pd.Series, level: pd.Series) -> pd.DataFrame:
    frame = common_years(displacement, level)
    years = frame.index.to_numpy(dtype=float)
    d, lev = frame["d"].to_numpy(), frame["l"].to_numpy()
    rows: list[tuple[float, float, float]] = []
    for i in range(len(frame)):
        train = np.arange(len(frame)) != i
        a_m, b_m = _fit(d[train], lev[train])
        a_b, b_b = _fit(years[train], lev[train])
        rows.append((a_m + b_m * d[i], a_b + b_b * years[i], lev[i]))
    return pd.DataFrame(rows, index=frame.index, columns=["model", "baseline", "actual"])


def _rmse(pred: pd.Series, actual: pd.Series) -> float:
    return float(np.sqrt(np.mean((pred.to_numpy() - actual.to_numpy()) ** 2)))


def _unscored(site_id: str, reason: str, n_years: int) -> SiteReport:
    nan = math.nan
    return SiteReport(site_id, "not_measurable", reason, n_years, nan, nan, nan, nan, nan)


def validate_site(
    displacement: pd.Series,
    level: pd.Series,
    site_id: str,
    *,
    measurable: bool = True,
    seed: int = 0,
) -> SiteReport:
    common = common_years(displacement, level)
    n_years = len(common)
    if not measurable:
        return _unscored(site_id, "shoreline series not measurable", n_years)
    if n_years < MIN_COMMON_YEARS:
        return _unscored(site_id, f"common years {n_years} < {MIN_COMMON_YEARS}", n_years)
    pred = loyo_predictions(displacement, level)
    rmse_model = _rmse(pred["model"], pred["actual"])
    rmse_base = _rmse(pred["baseline"], pred["actual"])
    pairs = paired_differences(displacement, level)
    dd, neg_dl = pairs["dd"].to_numpy(), -pairs["dl"].to_numpy()
    r_diff = pearson(dd, neg_dl)
    low, high, share = bootstrap_ci(dd, neg_dl, seed)
    raw_r = pearson(common["d"].to_numpy(), -common["l"].to_numpy())
    _, slope = _fit(common["d"].to_numpy(), common["l"].to_numpy())
    failed: list[str] = []
    if not rmse_model < rmse_base:
        failed.append(f"rmse_model {rmse_model:.4g} not below rmse_baseline {rmse_base:.4g}")
    if not r_diff >= R_DIFF_MIN:
        failed.append(f"r_diff {r_diff:.4g} < {R_DIFF_MIN}")
    if math.isnan(low):
        failed.append(f"ci_low undefined (degenerate share {share:.3g}, pairs {len(pairs)})")
    elif not low > 0:
        failed.append(f"ci_low {low:.4g} <= 0")
    verdict: Verdict = "not_confirmed" if failed else "confirmed"
    reason = "; ".join(failed) if failed else "all conditions met"
    return SiteReport(
        site_id,
        verdict,
        reason,
        n_years,
        rmse_model,
        rmse_base,
        r_diff,
        low,
        raw_r,
        len(pairs),
        slope,
        high,
        share,
    )


def overall_verdict(reports: Sequence[SiteReport]) -> Overall:
    confirmed = sum(r.verdict == "confirmed" for r in reports)
    if confirmed >= 2:
        return "confirmed"
    return "partial" if confirmed == 1 else "not_confirmed"


def with_threshold(mask: WaterMask, threshold: float) -> WaterMask:
    if mask.mndwi is None:
        raise ValueError("mask has no stored MNDWI")
    return replace(mask, water=mask.valid & (mask.mndwi > threshold), threshold=threshold)
