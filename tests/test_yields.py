from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from wheatwatch.yields import detrended_anomaly_pct, load_yields


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "yields.csv"
    path.write_text("region_id,year,yield_c_ha\n" + body, encoding="utf-8")
    return path


def test_load_yields_sorts_and_types(tmp_path: Path) -> None:
    df = load_yields(_write(tmp_path, "b,2021,30.0\na,2021,20.0\na,2020,10.5\n"))
    assert list(df["region_id"]) == ["a", "a", "b"]
    assert list(df["year"]) == [2020, 2021, 2021]
    assert df["yield_c_ha"].dtype == float


@pytest.mark.parametrize("body", ["a,2020,10.0\na,2020,11.0\n", "a,2020,10.0\na,2021,\n"])
def test_load_yields_rejects_duplicates_and_nans(tmp_path: Path, body: str) -> None:
    with pytest.raises(ValueError):
        load_yields(_write(tmp_path, body))


def _linear(held_out: float) -> pd.DataFrame:
    years = list(range(2000, 2006))
    values = [10.0 + 2.0 * (y - 2000) for y in years[:-1]] + [held_out]
    return pd.DataFrame({"region_id": "a", "year": years, "yield_c_ha": values})


def test_detrended_anomaly_uses_only_train_years() -> None:
    train = [2000, 2001, 2002, 2003, 2004]
    base = detrended_anomaly_pct(_linear(25.0), train)
    assert base.iloc[-1] == pytest.approx((25.0 - 20.0) / 20.0 * 100)
    assert base.iloc[:-1].abs().max() == pytest.approx(0.0, abs=1e-9)

    shifted = detrended_anomaly_pct(_linear(5.0), train)
    assert shifted.iloc[-1] == pytest.approx((5.0 - 20.0) / 20.0 * 100)
    pd.testing.assert_series_equal(base.iloc[:-1], shifted.iloc[:-1])


def test_detrended_anomaly_is_per_region_and_keeps_index() -> None:
    df = pd.concat(
        [_linear(25.0), _linear(25.0).assign(region_id="b", yield_c_ha=lambda d: d.yield_c_ha * 2)],
        ignore_index=True,
    ).sample(frac=1.0, random_state=0)
    out = detrended_anomaly_pct(df, [2000, 2001, 2002, 2003, 2004])
    assert out.index.equals(df.index)
    held_out = df["year"] == 2005
    assert out[held_out].tolist() == pytest.approx([25.0, 25.0])


def test_detrended_anomaly_is_nan_when_trend_not_positive() -> None:
    years = list(range(2000, 2007))
    values = [10.0, 8.0, 6.0, 4.0, 2.0, 3.0, 1.0]
    df = pd.DataFrame({"region_id": "a", "year": years, "yield_c_ha": values})
    out = detrended_anomaly_pct(df, years[:5])
    assert out.iloc[:4].notna().all()
    assert np.isnan(out.iloc[5]) and np.isnan(out.iloc[6])
    assert not np.isinf(out.to_numpy()).any()


def test_detrended_anomaly_rejects_missing_columns() -> None:
    with pytest.raises(ValueError, match="yield_c_ha"):
        detrended_anomaly_pct(pd.DataFrame({"region_id": ["a"], "year": [2000]}), [2000])


def test_load_yields_rejects_missing_columns(tmp_path: Path) -> None:
    path = tmp_path / "yields.csv"
    path.write_text("region_id,year\na,2020\n", encoding="utf-8")
    with pytest.raises(ValueError, match="yield_c_ha"):
        load_yields(path)
