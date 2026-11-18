import ast
import math
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from numpy.typing import NDArray
from rasterio.transform import Affine, from_origin
from shapely.geometry import LineString

from caspianwatch import shoreline
from caspianwatch.landsat import WaterMask
from caspianwatch.shoreline import (
    exposed_area_km2,
    is_measurable,
    profile_position,
    shore_point_weights,
    site_displacement_detail,
    site_displacement_series,
)
from caspianwatch.sites import Profile, Site, make_profiles

SRC = Path(__file__).resolve().parents[2] / "src" / "caspianwatch"
WIDTH, HEIGHT = 20, 40
TOP = HEIGHT * 30.0
LENGTH = 585.0
ROWS = [int((TOP - (1000.0 - 200.0 * i)) // 30) for i in range(5)]
TRANSFORM = from_origin(0.0, TOP, 30.0, 30.0)


def _site() -> Site:
    return Site(
        "syn",
        "Synthetic",
        LineString([(300.0, 1000.0), (300.0, 200.0)]),
        (1.0, 0.0),
        False,
        LENGTH,
        300.0,
    )


def _mask(
    water: NDArray[np.bool_],
    valid: NDArray[np.bool_] | None = None,
    fractions: tuple[float, ...] = (0.9, 0.9, 0.9),
    composite: float = 1.0,
    transform: Affine = TRANSFORM,
) -> WaterMask:
    ok = np.ones_like(water) if valid is None else valid
    return WaterMask(
        water & ok, transform, "EPSG:32639", len(fractions), ok, 0.0, composite, fractions, None
    )


def _cols(default: int, per_row: Mapping[int, int | None] | None = None) -> NDArray[np.bool_]:
    """Sea east of column `default`; per_row gives a row its own shore column (None: all land)."""
    water = np.zeros((HEIGHT, WIDTH), dtype=bool)
    water[:, default:] = True
    for row, col in (per_row or {}).items():
        water[row, :] = False
        if col is not None:
            water[row, col:] = True
    return water


def _row_water(pattern: str) -> NDArray[np.bool_]:
    """One char per column: 'w' water, 'l' land; applied to every row."""
    assert len(pattern) == WIDTH
    return np.tile(np.array([c == "w" for c in pattern]), (HEIGHT, 1))


def _profile(row: int = ROWS[2]) -> Profile:
    return Profile("syn", 0, (0.0, TOP - row * 30.0 - 15.0), (1.0, 0.0), LENGTH)


def _years(mask_for_year: dict[int, WaterMask | None]) -> dict[int, WaterMask | None]:
    return mask_for_year


def test_position_is_seaward_edge_of_land_run() -> None:
    assert profile_position(_profile(), _mask(_cols(5))) == pytest.approx(150.0)


def test_censored_profile_is_nan_not_clipped() -> None:
    water = _cols(5)
    water[:, -1] = False
    assert math.isnan(profile_position(_profile(), _mask(water)))
    all_land = np.zeros((HEIGHT, WIDTH), dtype=bool)
    assert math.isnan(profile_position(_profile(), _mask(all_land)))


def test_invalid_seaward_end_is_nan() -> None:
    valid = np.ones((HEIGHT, WIDTH), dtype=bool)
    valid[:, -1] = False
    assert math.isnan(profile_position(_profile(), _mask(_cols(5), valid)))


def test_lagoon_behind_barrier_is_not_the_shoreline() -> None:
    water = _row_water("lllllwwlllwwwwwwwwww")
    assert profile_position(_profile(), _mask(water)) == pytest.approx(300.0)


def test_isolated_land_speck_is_ignored() -> None:
    water = _row_water("lllllwwwwwllwwwwwwww")
    assert profile_position(_profile(), _mask(water)) == pytest.approx(150.0)


def test_three_pixel_run_counts() -> None:
    water = _row_water("lllllwwwwwlllwwwwwww")
    assert profile_position(_profile(), _mask(water)) == pytest.approx(390.0)


def test_invalid_pixel_breaks_land_run() -> None:
    valid = np.ones((HEIGHT, WIDTH), dtype=bool)
    valid[:, 12] = False
    water = _row_water("lllllwwwwwlllwwwwwww")
    assert profile_position(_profile(), _mask(water, valid)) == pytest.approx(150.0)


def test_less_than_90_percent_valid_pixels_is_nan() -> None:
    valid = np.ones((HEIGHT, WIDTH), dtype=bool)
    valid[:, 0:4] = False
    assert math.isnan(profile_position(_profile(), _mask(_cols(10), valid)))
    valid = np.ones((HEIGHT, WIDTH), dtype=bool)
    valid[:, 0:2] = False
    assert profile_position(_profile(), _mask(_cols(10), valid)) == pytest.approx(300.0)


def test_retreating_sea_gives_positive_displacement() -> None:
    masks = {2000 + i: _mask(_cols(5 + i)) for i in range(5)}
    series = site_displacement_series(_site(), masks)
    assert list(series.index) == [2000, 2001, 2002, 2003, 2004]
    assert series.tolist() == pytest.approx([150.0, 180.0, 210.0, 240.0, 270.0])
    assert (series.diff().dropna() > 0).all()


def test_missing_mask_yields_nan_not_zero() -> None:
    masks: dict[int, WaterMask | None] = {y: _mask(_cols(5)) for y in range(2000, 2010)}
    masks[2004] = None
    series = site_displacement_series(_site(), masks)
    assert 2004 in series.index
    assert math.isnan(series[2004])
    assert series.drop(2004).notna().all()


def test_less_than_80_percent_profiles_yields_nan() -> None:
    masks: dict[int, WaterMask | None] = {y: _mask(_cols(5)) for y in range(2000, 2020)}
    masks[2004] = _mask(_cols(5, {ROWS[0]: None}))
    masks[2005] = _mask(_cols(5, {ROWS[0]: None, ROWS[1]: None}))
    detail = site_displacement_detail(_site(), masks)
    assert detail.complete_profiles == (0, 1, 2, 3, 4)
    assert detail.series[2004] == pytest.approx(150.0)
    assert math.isnan(detail.series[2005])
    assert detail.per_year_valid_share[2004] == pytest.approx(0.8)
    assert detail.per_year_valid_share[2005] == pytest.approx(0.6)


def test_complete_set_excludes_late_censored_profile_from_every_year() -> None:
    masks: dict[int, WaterMask | None] = {}
    for year in range(2000, 2010):
        late: dict[int, int | None] = {ROWS[4]: None if year >= 2007 else 15}
        masks[year] = _mask(_cols(5, late))
    detail = site_displacement_detail(_site(), masks)
    assert detail.complete_profiles == (0, 1, 2, 3)
    assert detail.series.tolist() == pytest.approx([150.0] * 10)
    assert detail.positions.loc[2000, 4] == pytest.approx(450.0)
    assert np.isnan(detail.positions.to_numpy(dtype=float)[8, 4])


def test_complete_set_is_fixed_and_independent_of_year_order() -> None:
    masks: dict[int, WaterMask | None] = {}
    for year in range(2000, 2010):
        masks[year] = _mask(_cols(5 + year - 2000, {ROWS[1]: None} if year == 2003 else None))
    forward = site_displacement_detail(_site(), masks)
    backward = site_displacement_detail(_site(), dict(reversed(list(masks.items()))))
    assert forward.complete_profiles == backward.complete_profiles
    pd.testing.assert_series_equal(forward.series, backward.series)
    assert list(backward.series.index) == sorted(masks)


def test_date_gate_needs_three_dates_with_valid_fraction() -> None:
    masks: dict[int, WaterMask | None] = {y: _mask(_cols(5)) for y in range(2000, 2010)}
    masks[2002] = _mask(_cols(5), fractions=(0.05, 0.09, 0.9))
    masks[2003] = _mask(_cols(5), fractions=(0.1, 0.1, 0.1))
    series = site_displacement_series(_site(), masks)
    assert math.isnan(series[2002])
    assert series[2003] == pytest.approx(150.0)


def test_composite_valid_fraction_gate() -> None:
    masks: dict[int, WaterMask | None] = {y: _mask(_cols(5)) for y in range(2000, 2010)}
    masks[2002] = _mask(_cols(5), composite=0.79)
    masks[2003] = _mask(_cols(5), composite=0.8)
    series = site_displacement_series(_site(), masks)
    assert math.isnan(series[2002])
    assert series[2003] == pytest.approx(150.0)


def test_gated_year_does_not_count_for_complete_set() -> None:
    masks: dict[int, WaterMask | None] = {y: _mask(_cols(5)) for y in range(2000, 2010)}
    for year in (2000, 2001, 2002):
        masks[year] = _mask(_cols(5), composite=0.5)
    detail = site_displacement_detail(_site(), masks)
    assert detail.complete_profiles == (0, 1, 2, 3, 4)
    assert detail.positions.loc[[2000, 2001, 2002]].isna().all().all()


def test_no_usable_year_gives_empty_complete_set_and_all_nan() -> None:
    detail = site_displacement_detail(_site(), {2000: None, 2001: None})
    assert detail.complete_profiles == ()
    assert detail.series.isna().all()
    assert list(detail.series.index) == [2000, 2001]


def test_shore_point_weights_hand_computed() -> None:
    points = np.array([[0.0, 0.0], [30.0, 0.0], [0.0, 60.0], [500.0, 0.0]])
    assert shore_point_weights(points).tolist() == pytest.approx([1 / 3, 1 / 3, 1 / 3, 1.0])
    assert shore_point_weights(np.array([[0.0, 0.0], [100.0, 0.0]])).tolist() == pytest.approx(
        [0.5, 0.5]
    )


def test_weighted_diagnostic_differs_from_unweighted_on_clustered_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ys = [1005.0, 1035.0, 975.0, 405.0]
    profiles = [Profile("syn", i, (0.0, y), (1.0, 0.0), LENGTH) for i, y in enumerate(ys)]
    monkeypatch.setattr(shoreline, "make_profiles", lambda site: profiles)
    rows = {int((TOP - y) // 30): (5 if y > 900 else 15) for y in ys}
    masks: dict[int, WaterMask | None] = {y: _mask(_cols(5, rows)) for y in range(2000, 2003)}
    detail = site_displacement_detail(_site(), masks)
    assert detail.series.tolist() == pytest.approx([225.0] * 3)
    assert detail.weighted_series.tolist() == pytest.approx([300.0] * 3)


def test_weighted_equals_unweighted_when_profiles_are_apart() -> None:
    masks: dict[int, WaterMask | None] = {y: _mask(_cols(5 + y - 2000)) for y in range(2000, 2005)}
    detail = site_displacement_detail(_site(), masks)
    assert len(make_profiles(_site())) == 5
    assert detail.weighted_series.tolist() == pytest.approx(detail.series.tolist())


def test_is_measurable_needs_20_valid_years() -> None:
    period = list(range(2000, 2026))
    values = [1.0] * 20 + [float("nan")] * 6
    assert is_measurable(pd.Series(values, index=period), period)
    values = [1.0] * 19 + [float("nan")] * 7
    assert not is_measurable(pd.Series(values, index=period), period)
    extra = pd.Series([1.0] * 19 + [1.0] * 3, index=list(range(2000, 2019)) + [1990, 1991, 1992])
    assert not is_measurable(extra, period)
    assert is_measurable(pd.Series([1.0] * 5, index=period[:5]), period[:5], min_years=5)


def test_exposed_area_counts_only_water_to_land_pixels() -> None:
    first_water = np.array([[1, 1, 0, 1], [1, 1, 1, 0], [0, 1, 1, 1]], dtype=bool)
    last_water = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [1, 1, 1, 0]], dtype=bool)
    valid_first = np.ones((3, 4), dtype=bool)
    valid_last = np.ones((3, 4), dtype=bool)
    valid_last[0, 3] = False
    valid_first[1, 0] = False
    transform = from_origin(0.0, 90.0, 30.0, 30.0)
    first = WaterMask(
        first_water & valid_first, transform, "EPSG:32639", 3, valid_first, 0, 1, (), None
    )
    last = WaterMask(
        last_water & valid_last, transform, "EPSG:32639", 3, valid_last, 0, 1, (), None
    )
    # water->land in both-valid pixels: (0,1), (1,2), (2,3); (0,3) and (1,0) are invalid in one mask
    assert exposed_area_km2(first, last) == pytest.approx(3 * 900.0 / 1e6)
    assert exposed_area_km2(last, first) == pytest.approx(1 * 900.0 / 1e6)


def test_exposed_area_rejects_different_grids() -> None:
    water = np.ones((3, 4), dtype=bool)
    first = _mask(water, transform=from_origin(0.0, 90.0, 30.0, 30.0))
    shifted = _mask(water, transform=from_origin(30.0, 90.0, 30.0, 30.0))
    with pytest.raises(ValueError, match="grid"):
        exposed_area_km2(first, shifted)
    with pytest.raises(ValueError, match="grid"):
        exposed_area_km2(first, _mask(np.ones((4, 4), dtype=bool), transform=first.transform))


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_shoreline_module_does_not_import_level() -> None:
    names = _imports(SRC / "shoreline.py")
    assert not any("level" in name.split(".") for name in names), names


def test_fixed_profiles_default_is_unchanged() -> None:
    masks: dict[int, WaterMask | None] = {}
    for year in range(2000, 2010):
        late: dict[int, int | None] = {ROWS[4]: None if year >= 2007 else 15}
        masks[year] = _mask(_cols(5, late))
    default = site_displacement_detail(_site(), masks)
    explicit = site_displacement_detail(_site(), masks, fixed_profiles=None)
    assert default.complete_profiles == explicit.complete_profiles == (0, 1, 2, 3)
    pd.testing.assert_series_equal(default.series, explicit.series)
    pd.testing.assert_series_equal(
        site_displacement_series(_site(), masks), site_displacement_series(_site(), masks, 0.8)
    )


def test_fixed_profiles_overrides_the_complete_set() -> None:
    masks: dict[int, WaterMask | None] = {
        year: _mask(_cols(5, {ROWS[1]: 10})) for year in range(2000, 2010)
    }
    free = site_displacement_detail(_site(), masks)
    fixed = site_displacement_detail(_site(), masks, fixed_profiles=(0, 1))
    assert fixed.complete_profiles == (0, 1)
    assert fixed.series.tolist() == pytest.approx([225.0] * 10)
    assert free.series.tolist() != pytest.approx([225.0] * 10)
    only = site_displacement_series(_site(), masks, fixed_profiles=[1])
    assert only.tolist() == pytest.approx([300.0] * 10)


def test_fixed_profiles_unknown_index_raises() -> None:
    masks: dict[int, WaterMask | None] = {2000: _mask(_cols(5))}
    with pytest.raises(ValueError):
        site_displacement_detail(_site(), masks, fixed_profiles=(0, 99))


def test_importing_shoreline_loads_no_level_module() -> None:
    code = "import sys, caspianwatch.shoreline; sys.exit('caspianwatch.level' in sys.modules)"
    env = {**os.environ, "PYTHONPATH": str(SRC.parent)}
    assert subprocess.run([sys.executable, "-c", code], env=env, check=False).returncode == 0
