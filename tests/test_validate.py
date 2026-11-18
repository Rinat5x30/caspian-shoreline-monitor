import numpy as np
import pandas as pd
import pytest

from wheatwatch import validate
from wheatwatch.validate import (
    LIBERATED_DISTRICT_IDS,
    build_features,
    loyo_validate,
    validate_with_sensitivity,
)
from wheatwatch.yields import detrended_anomaly_pct

YEARS = list(range(2020, 2026))
KEYS = ["region_id", "year"]
RAW = ["ndvi_peak", "precip_mm", "tmax_mean_c"]


def _frames(
    n_regions: int, latent: np.ndarray, anomaly: np.ndarray
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ids = [f"r{i:02d}" for i in range(n_regions)]
    keys = pd.DataFrame([(r, y) for r in ids for y in YEARS], columns=["region_id", "year"])
    region_no = np.arange(len(keys)) // len(YEARS)
    climate = keys.assign(
        precip_mm=300 + 10 * region_no + 50 * latent,
        tmax_mean_c=20 + 0.1 * region_no - 3 * latent,
    )
    ndvi = keys.assign(ndvi_peak=0.5 + 0.01 * region_no + 0.1 * latent)
    trend = 20 + 0.5 * (keys["year"] - 2020) + region_no
    yields = keys.assign(yield_c_ha=trend * (1 + anomaly / 100))
    return climate, ndvi, yields


def _known_relation() -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(1)
    grid = rng.normal(size=(20, len(YEARS)))
    t = np.arange(len(YEARS)) - (len(YEARS) - 1) / 2
    grid = grid - grid.mean(axis=1, keepdims=True) - np.outer(grid @ t / (t @ t), t)
    latent = grid.ravel()
    anomaly = 8.0 * latent + rng.normal(scale=0.3, size=latent.size)
    climate, ndvi, yields = _frames(20, latent, anomaly)
    return build_features(climate, ndvi), yields


def _noise() -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(0)
    n = 30 * len(YEARS)
    climate, ndvi, yields = _frames(30, rng.normal(size=n), rng.normal(scale=5.0, size=n))
    return build_features(climate, ndvi), yields


def test_loyo_recovers_known_relation() -> None:
    features, yields = _known_relation()
    report = loyo_validate(features, yields, single_predictor=True)
    assert report.pearson_r > 0.8
    assert report.verdict != "weak"
    assert report.predictors == ("drought_index",)
    assert report.n_years == 6 and report.n_regions == 20 and report.n_dropped == 0
    assert report.rmse_pct < report.baseline_rmse_pct


def test_loyo_three_predictors_lists_them() -> None:
    features, yields = _known_relation()
    report = loyo_validate(features, yields)
    assert report.predictors == ("ndvi_z", "precip_z", "tmax_z")
    assert report.pearson_r > 0.8


def test_loyo_on_random_noise_reports_weak() -> None:
    features, yields = _noise()
    report = loyo_validate(features, yields, single_predictor=True)
    assert report.pearson_r < 0.3
    assert report.verdict == "weak"


def test_loyo_rejects_short_or_constant_region() -> None:
    features, yields = _known_relation()
    short = yields[~((yields["region_id"] == "r00") & (yields["year"] > 2021))]
    with pytest.raises(ValueError, match="r00"):
        loyo_validate(features, short)
    constant = yields.copy()
    constant.loc[constant["region_id"] == "r01", "yield_c_ha"] = 25.0
    with pytest.raises(ValueError, match="r01"):
        loyo_validate(features, constant)


def test_nans_are_dropped_and_counted() -> None:
    features, yields = _known_relation()
    features = features.copy()
    features.loc[3, RAW] = np.nan
    features.loc[10, RAW] = np.nan
    report = loyo_validate(features, yields, single_predictor=True)
    assert report.n_dropped == 2


def test_partially_missing_components_still_give_an_index() -> None:
    features, yields = _known_relation()
    features = features.copy()
    features.loc[3, ["ndvi_peak", "precip_mm"]] = np.nan
    report = loyo_validate(features, yields, single_predictor=True)
    assert report.n_dropped == 0


def test_trend_not_positive_region_year_is_dropped() -> None:
    features, yields = _known_relation()
    yields = yields.copy()
    yields.loc[yields["region_id"] == "r05", "yield_c_ha"] = [10.0, 8.0, 6.0, 4.0, 2.0, 1.0]
    report = loyo_validate(features, yields, single_predictor=True)
    assert report.n_dropped == 1
    assert np.isfinite(report.pearson_r) and np.isfinite(report.rmse_pct)


def test_sign_convention_hotter_means_lower_index() -> None:
    climate = pd.DataFrame(
        {
            "region_id": "a",
            "year": YEARS,
            "precip_mm": [300.0] * 6,
            "tmax_mean_c": [20.0, 21.0, 22.0, 23.0, 24.0, 25.0],
        }
    )
    ndvi = pd.DataFrame({"region_id": "a", "year": YEARS, "ndvi_peak": [0.6] * 6})
    out = build_features(climate, ndvi)
    assert out["tmax_z"].is_monotonic_increasing
    assert out["drought_index"].is_monotonic_decreasing
    assert out["drought_index"].iloc[0] > 0 > out["drought_index"].iloc[-1]


def _raw_features(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "region_id": "a",
            "year": YEARS,
            "ndvi_peak": values,
            "precip_mm": values,
            "tmax_mean_c": values,
        }
    )


def test_normalisation_uses_training_years_only() -> None:
    base = _raw_features([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    changed = _raw_features([1.0, 2.0, 3.0, 4.0, 5.0, 600.0])
    train = YEARS[:5]
    z_base = validate._fold_zscores(base, train, RAW)
    z_changed = validate._fold_zscores(changed, train, RAW)
    pd.testing.assert_frame_equal(z_base.iloc[:5], z_changed.iloc[:5])
    expected = (6.0 - 3.0) / np.std([1.0, 2.0, 3.0, 4.0, 5.0])
    assert z_base["ndvi_peak"].iloc[5] == pytest.approx(expected)
    whole = validate._fold_zscores(base, YEARS, RAW)
    assert abs(whole["ndvi_peak"].iloc[5] - z_base["ndvi_peak"].iloc[5]) > 0.1


def test_heldout_year_not_in_training() -> None:
    features, yields = _known_relation()
    wild = yields.copy()
    wild.loc[wild["year"] == 2025, "yield_c_ha"] *= 3.0
    base = validate._loyo_predictions(features, yields, True, frozenset())[0]
    other = validate._loyo_predictions(features, wild, True, frozenset())[0]
    np.testing.assert_allclose(
        base.query("year == 2025")["predicted"].to_numpy(),
        other.query("year == 2025")["predicted"].to_numpy(),
    )
    assert not np.allclose(
        base.query("year == 2025")["observed"].to_numpy(),
        other.query("year == 2025")["observed"].to_numpy(),
    )


def _expected_heldout_prediction(
    features: pd.DataFrame, yields: pd.DataFrame, held_out: int
) -> pd.Series:
    train_years = [y for y in sorted(features["year"].unique()) if y != held_out]
    train = features[features["year"].isin(train_years)]
    mean = train.groupby("region_id")[RAW].mean()
    std = train.groupby("region_id")[RAW].std(ddof=0)
    z = (features[RAW].to_numpy() - mean.loc[features["region_id"]].to_numpy()) / std.loc[
        features["region_id"]
    ].to_numpy()
    index = pd.Series(z[:, 0] + z[:, 1] - z[:, 2], index=features.index) / 3
    anomaly = detrended_anomaly_pct(
        features[KEYS].merge(yields, on=KEYS, how="left"), list(train_years)
    )
    is_train = features["year"].isin(train_years).to_numpy()
    design = np.column_stack([np.ones(is_train.sum()), index[is_train]])
    a, b = np.linalg.lstsq(design, anomaly[is_train].to_numpy(), rcond=None)[0]
    test = ~is_train
    return pd.Series(a + b * index[test].to_numpy(), index=features.loc[test, "region_id"])


def test_heldout_prediction_uses_training_only_parameters() -> None:
    features, yields = _known_relation()
    features = features.copy()
    boosted = (features["region_id"] == "r03") & (features["year"] == 2025)
    features.loc[boosted, RAW] *= 5.0
    got = validate._loyo_predictions(features, yields, True, frozenset())[0]
    got_2025 = got.query("year == 2025").set_index("region_id")["predicted"]
    expected = _expected_heldout_prediction(features, yields, 2025)
    pd.testing.assert_series_equal(
        got_2025.sort_index(), expected.sort_index(), check_names=False, rtol=1e-9
    )


def test_exclude_ids_reduces_n_regions() -> None:
    features, yields = _known_relation()
    full = loyo_validate(features, yields, single_predictor=True)
    cut = loyo_validate(
        features, yields, single_predictor=True, exclude_ids=frozenset({"r00", "r01"})
    )
    assert (full.n_regions, cut.n_regions) == (20, 18)


def test_hit_rate_and_baseline_hand_computed(monkeypatch: pytest.MonkeyPatch) -> None:
    predictions = pd.DataFrame(
        {
            "region_id": ["a", "b", "c", "d"],
            "year": [2020, 2020, 2021, 2021],
            "predicted": [-1.0, 2.0, -3.0, 4.0],
            "observed": [-2.0, -1.0, -4.0, 6.0],
        }
    )
    monkeypatch.setattr(validate, "_loyo_predictions", lambda *a, **k: (predictions, 1))
    features, yields = _known_relation()
    report = loyo_validate(features, yields, single_predictor=True)
    assert report.hit_rate == pytest.approx(0.75)
    assert report.rmse_pct == pytest.approx(np.sqrt((1 + 9 + 1 + 4) / 4))
    assert report.baseline_rmse_pct == pytest.approx(np.sqrt((4 + 1 + 16 + 36) / 4))
    assert (report.n_years, report.n_regions, report.n_dropped) == (2, 4, 1)


def test_constant_predictions_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    predictions = pd.DataFrame(
        {
            "region_id": ["a", "b", "c"],
            "year": [2020, 2020, 2021],
            "predicted": [1.0, 1.0, 1.0],
            "observed": [1.0, -2.0, 3.0],
        }
    )
    monkeypatch.setattr(validate, "_loyo_predictions", lambda *a, **k: (predictions, 0))
    features, yields = _known_relation()
    with pytest.raises(ValueError, match="constant"):
        loyo_validate(features, yields)


@pytest.mark.parametrize(
    ("r", "verdict"), [(0.29, "weak"), (0.3, "moderate"), (0.59, "moderate"), (0.6, "strong")]
)
def test_verdict_thresholds(r: float, verdict: str) -> None:
    assert validate._verdict(r) == verdict


def test_sensitivity_returns_all_and_excluding_liberated() -> None:
    features, yields = _known_relation()
    renamed = {"r00": "jabrayil", "r01": "lachin"}
    features = features.assign(region_id=features["region_id"].replace(renamed))
    yields = yields.assign(region_id=yields["region_id"].replace(renamed))
    everyone, without = validate_with_sensitivity(features, yields)
    assert (everyone.n_regions, without.n_regions) == (20, 18)
    assert {"jabrayil", "lachin"} <= LIBERATED_DISTRICT_IDS
