import builtins
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from numpy.typing import NDArray
from rasterio.transform import from_origin

from caspianwatch import validate
from caspianwatch.landsat import WaterMask
from caspianwatch.validate import (
    MIN_COMMON_YEARS,
    N_BOOT,
    R_DIFF_MIN,
    SiteReport,
    block_bootstrap_ci,
    bootstrap_ci,
    loyo_predictions,
    overall_verdict,
    paired_differences,
    validate_site,
    with_threshold,
)

YEARS = list(range(2000, 2026))


def _series(values: NDArray[np.float64] | list[float], years: list[int] | None = None) -> pd.Series:
    idx = pd.Index(years if years is not None else YEARS[: len(values)], name="year")
    return pd.Series(np.asarray(values, dtype=float), index=idx)


def _coupled(seed: int, sign: float = -1.0) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    t = np.arange(26)
    d = 20.0 * t + rng.normal(0.0, 150.0, 26)
    level = sign * 0.01 * d + rng.normal(0.0, 0.01, 26)
    return _series(d), _series(level)


def _from_diffs(dd: list[float], dl_neg: list[float]) -> tuple[pd.Series, pd.Series]:
    n = len(dd) + 1
    d = np.r_[0.0, np.cumsum(dd)]
    level = -np.r_[0.0, np.cumsum(dl_neg)]
    return _series(d, YEARS[:n]), _series(level, YEARS[:n])


def _report(verdict: str, site: str = "s") -> SiteReport:
    nan = math.nan
    return SiteReport(site, verdict, "x", 20, nan, nan, nan, nan, nan)  # type: ignore[arg-type]


def test_coupled_series_is_confirmed() -> None:
    d, level = _coupled(1)
    report = validate_site(d, level, "c")
    assert report.verdict == "confirmed", report.reason
    assert report.rmse_model < report.rmse_baseline
    assert report.r_diff >= R_DIFF_MIN
    assert report.r_diff_ci_low > 0


def test_independent_trending_series_not_confirmed(capsys: pytest.CaptureFixture[str]) -> None:
    confirmed = 0
    for seed in range(200):
        rng = np.random.default_rng(10_000 + seed)
        t = np.arange(26)
        d = 20.0 * t + rng.normal(0.0, 150.0, 26)
        level = -0.05 * t + rng.normal(0.0, 0.05, 26)
        if validate_site(_series(d), _series(level), "i").verdict == "confirmed":
            confirmed += 1
    share = confirmed / 200
    with capsys.disabled():
        print(f"\nTREND_TRAP confirmed {confirmed}/200 share={share:.4f}")
    assert share <= 0.05


def test_differences_not_computed_across_gaps() -> None:
    d = _series([1.0, 3.0, math.nan, 10.0, 15.0, 16.0])
    level = _series([5.0, 4.0, 3.0, 2.0, 0.0, -1.0])
    pairs = paired_differences(d, level)
    assert list(pairs.index) == [2001, 2004, 2005]
    assert pairs["dd"].tolist() == [2.0, 5.0, 1.0]
    assert pairs["dl"].tolist() == [-1.0, -2.0, -1.0]


def test_differences_skip_year_missing_only_in_level() -> None:
    d = _series([1.0, 2.0, 4.0, 8.0])
    level = _series([1.0, math.nan, 3.0, 4.0])
    assert list(paired_differences(d, level).index) == [2003]


def test_r_diff_matches_independent_computation() -> None:
    d, level = _coupled(3)
    dd = np.diff(d.to_numpy())
    dl = np.diff(level.to_numpy())
    assert validate_site(d, level, "r").r_diff == pytest.approx(np.corrcoef(dd, -dl)[0, 1])


def _brute_force(d: pd.Series, level: pd.Series, k: int) -> tuple[float, float]:
    keep = [y for y in YEARS if y != k]
    a_m = np.polyfit(d.loc[keep].to_numpy(), level.loc[keep].to_numpy(), 1)
    a_b = np.polyfit(np.array(keep, dtype=float), level.loc[keep].to_numpy(), 1)
    return (
        float(a_m[0] * d.loc[k] + a_m[1]),
        float(a_b[0] * k + a_b[1]),
    )


def test_loyo_matches_brute_force() -> None:
    d, level = _coupled(4)
    pred = loyo_predictions(d, level)
    for k in (2000, 2012, 2025):
        model, base = _brute_force(d, level, k)
        assert pred.loc[k, "model"] == pytest.approx(model, rel=1e-8, abs=1e-8)
        assert pred.loc[k, "baseline"] == pytest.approx(base, rel=1e-8, abs=1e-8)


def test_heldout_year_not_used_in_fit() -> None:
    d, level = _coupled(5)
    k = 2013
    scale = pd.Series(np.where(d.index == k, 5.0, 1.0), index=d.index)
    d5, l5 = d * scale, level * scale
    pred = loyo_predictions(d5, l5)
    keep = [y for y in YEARS if y != k]
    a_m = np.polyfit(d.loc[keep].to_numpy(), level.loc[keep].to_numpy(), 1)
    a_b = np.polyfit(np.array(keep, dtype=float), level.loc[keep].to_numpy(), 1)
    assert pred.loc[k, "model"] == pytest.approx(a_m[0] * (5 * d.loc[k]) + a_m[1], rel=1e-8)
    assert pred.loc[k, "baseline"] == pytest.approx(a_b[0] * k + a_b[1], rel=1e-8)
    assert pred.loc[k, "actual"] == pytest.approx(5 * level.loc[k])


def test_baseline_uses_year_only() -> None:
    d, level = _coupled(6)
    rng = np.random.default_rng(0)
    other_d = _series(rng.normal(0.0, 1000.0, 26))
    a = loyo_predictions(d, level)["baseline"].to_numpy()
    b = loyo_predictions(other_d, level)["baseline"].to_numpy()
    assert np.allclose(a, b)


def test_nan_years_excluded_not_filled() -> None:
    d, level = _coupled(7)
    d_nan = d.where(~d.index.isin([2005, 2006]))
    pred = loyo_predictions(d_nan, level)
    assert 2005 not in pred.index and 2006 not in pred.index and len(pred) == 24
    dropped = loyo_predictions(d.drop([2005, 2006]), level.drop([2005, 2006]))
    assert np.allclose(pred.to_numpy(), dropped.to_numpy())
    zero_filled = loyo_predictions(d_nan.fillna(0.0), level)
    assert not np.allclose(
        pred["model"].to_numpy(), zero_filled.loc[pred.index, "model"].to_numpy()
    )


def test_unmeasurable_site_reported_not_scored() -> None:
    d, level = _coupled(8)
    report = validate_site(d, level, "u", measurable=False)
    assert report.verdict == "not_measurable"
    assert "not measurable" in report.reason
    assert math.isnan(report.rmse_model) and math.isnan(report.r_diff)


def test_min_common_years() -> None:
    d, level = _coupled(9)
    few = validate_site(d.iloc[: MIN_COMMON_YEARS - 1], level.iloc[: MIN_COMMON_YEARS - 1], "f")
    assert few.verdict == "not_measurable"
    assert few.n_years == MIN_COMMON_YEARS - 1
    enough = validate_site(d.iloc[:MIN_COMMON_YEARS], level.iloc[:MIN_COMMON_YEARS], "f")
    assert enough.verdict != "not_measurable"


def test_overall_verdict_confirmed_needs_two_sites() -> None:
    c, n, m = _report("confirmed"), _report("not_confirmed"), _report("not_measurable")
    assert overall_verdict([c, c, n]) == "confirmed"
    assert overall_verdict([c, c, c]) == "confirmed"
    assert overall_verdict([c, n, n]) == "partial"
    assert overall_verdict([c, m, m]) == "partial"
    assert overall_verdict([n, n, n]) == "not_confirmed"
    assert overall_verdict([m, m, n]) == "not_confirmed"
    assert overall_verdict([c, m]) == "partial"


def _wide_ci_case() -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(31)
    dd = rng.normal(0.0, 1.0, 9)
    dl_neg = 0.65 * dd + 0.75 * rng.normal(0.0, 1.0, 9)
    return _from_diffs(list(dd), list(dl_neg))


def test_ci_lower_bound_must_exceed_zero() -> None:
    d, level = _wide_ci_case()
    report = validate_site(d, level, "w")
    assert report.r_diff >= R_DIFF_MIN
    assert report.r_diff_ci_low <= 0
    assert report.verdict == "not_confirmed"
    assert "ci_low" in report.reason


def test_raw_r_reported_separately() -> None:
    rng = np.random.default_rng(12)
    t = np.arange(26)
    d = _series(20.0 * t + rng.normal(0.0, 30.0, 26))
    level = _series(-0.2 * t + rng.normal(0.0, 0.3, 26))
    report = validate_site(d, level, "raw")
    assert report.raw_r == pytest.approx(np.corrcoef(d.to_numpy(), -level.to_numpy())[0, 1])
    assert report.raw_r > 0.9
    assert report.verdict == "not_confirmed"


def test_wrong_sign_coupling_not_confirmed() -> None:
    d, level = _coupled(13, sign=+1.0)
    report = validate_site(d, level, "wrong")
    assert report.rmse_model < report.rmse_baseline
    assert report.r_diff < 0
    assert report.verdict == "not_confirmed"
    assert "r_diff" in report.reason


def test_worse_rmse_than_baseline_not_confirmed() -> None:
    rng = np.random.default_rng(14)
    t = np.arange(26)
    e = rng.normal(0.0, 0.05, 26)
    level = _series(-0.2 * t + e)
    d = _series(-100.0 * e)
    report = validate_site(d, level, "rmse")
    assert report.r_diff >= R_DIFF_MIN and report.r_diff_ci_low > 0
    assert report.rmse_model > report.rmse_baseline
    assert report.verdict == "not_confirmed"
    assert "rmse" in report.reason


def test_degenerate_resamples_excluded_and_flagged() -> None:
    x = np.r_[np.ones(11), 5.0]
    y = np.r_[np.ones(11), 3.0]
    low, high, share = bootstrap_ci(x, y, seed=0)
    assert share > 0.05
    assert math.isnan(low) and math.isnan(high)
    dd = [1.0] * 11 + [5.0]
    dl_neg = [1.0] * 11 + [3.0]
    d, level = _from_diffs(dd, dl_neg)
    report = validate_site(d, level, "deg")
    assert report.verdict == "not_confirmed"
    assert math.isnan(report.r_diff_ci_low)
    assert report.degenerate_share > 0.05
    assert "degenerate" in report.reason


def test_nondegenerate_share_zero_and_ci_brackets_r() -> None:
    rng = np.random.default_rng(2)
    x = rng.normal(size=20)
    y = x + rng.normal(scale=0.5, size=20)
    low, high, share = bootstrap_ci(x, y, seed=0)
    assert share == 0.0
    assert low < np.corrcoef(x, y)[0, 1] < high


def test_deterministic_with_seed() -> None:
    d, level = _wide_ci_case()
    a = validate_site(d, level, "s", seed=3)
    b = validate_site(d, level, "s", seed=3)
    c = validate_site(d, level, "s", seed=4)
    assert (a.r_diff_ci_low, a.ci_high) == (b.r_diff_ci_low, b.ci_high)
    assert a.r_diff_ci_low != c.r_diff_ci_low


def test_block_bootstrap_secondary_runs() -> None:
    rng = np.random.default_rng(5)
    x = rng.normal(size=24)
    y = x + rng.normal(scale=0.5, size=24)
    low, high, share = block_bootstrap_ci(x, y, seed=0)
    assert low < high and 0.0 <= share <= 1.0
    assert block_bootstrap_ci(x, y, seed=0) == (low, high, share)


def test_n_boot_constant() -> None:
    assert N_BOOT == 2000 and R_DIFF_MIN == 0.5 and MIN_COMMON_YEARS == 10


def test_primary_path_reads_only_input_series(monkeypatch: pytest.MonkeyPatch) -> None:
    d, level = _coupled(15)
    source = Path(validate.__file__).read_text(encoding="utf-8")

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("file access in the primary path")

    monkeypatch.setattr(builtins, "open", boom)
    monkeypatch.setattr(Path, "read_text", boom)
    monkeypatch.setattr(Path, "read_bytes", boom)
    assert validate_site(d, level, "io").verdict == "confirmed"
    for forbidden in ("import level", "load_grealm", "season_level", "from caspianwatch.level"):
        assert forbidden not in source


def test_with_threshold_rederives_water_from_mndwi() -> None:
    mndwi = np.array([[0.5, 0.15], [0.05, -0.3]], dtype=np.float32)
    valid = np.array([[True, True], [True, False]])
    mask = WaterMask(
        water=mndwi > 0.1,
        transform=from_origin(0, 0, 30, 30),
        crs="EPSG:32639",
        n_scenes=3,
        valid=valid,
        threshold=0.1,
        composite_valid_fraction=0.75,
        date_valid_fractions=(1.0, 1.0, 1.0),
        mndwi=mndwi,
    )
    assert with_threshold(mask, 0.2).water.tolist() == [[True, False], [False, False]]
    assert with_threshold(mask, 0.0).water.tolist() == [[True, True], [True, False]]
    assert with_threshold(mask, 0.0).valid is mask.valid
    bare = WaterMask(**{**mask.__dict__, "mndwi": None})
    with pytest.raises(ValueError):
        with_threshold(bare, 0.1)
