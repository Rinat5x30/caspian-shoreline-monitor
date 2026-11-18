import ast
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_origin

from caspianwatch import site_selection as sel
from caspianwatch.site_selection import (
    BUILT_MAX,
    SLOPE_MAX_DEG,
    TREE_MAX,
    WETLAND_DELTAIC_MIN,
    BandStats,
    Grid,
    band_mask,
    caspian_component,
    class_shares,
    coast_crossing,
    contour_offset,
    evaluate,
    fetch_dem,
    mean_slope_deg,
    required_profile_length,
    smooth_offsets,
)
from wheatwatch.climate import HttpStatusError

SRC = Path(__file__).resolve().parents[2] / "src" / "caspianwatch"


def _stats(
    slope: float = 0.5, built: float = 0.0, tree: float = 0.0, wet: float = 0.0
) -> BandStats:
    return BandStats(slope, built, tree, wet, 1000)


def test_thresholds_are_the_preregistered_values() -> None:
    assert (SLOPE_MAX_DEG, BUILT_MAX, TREE_MAX, WETLAND_DELTAIC_MIN) == (1.0, 0.30, 0.10, 0.10)
    assert sel.BAND_WIDTH_M == 1000.0
    assert sel.SEGMENT_RADIUS_M == 7500.0
    assert sel.CONTOUR_LEVEL_M == -24.0
    assert sel.PROFILE_MARGIN_M >= 1000.0


def test_rule_boundaries_are_inclusive() -> None:
    assert evaluate(_stats(slope=1.0), in_kura_delta=False).passed
    assert evaluate(_stats(built=0.30), in_kura_delta=False).passed
    assert evaluate(_stats(tree=0.10), in_kura_delta=False).passed
    assert evaluate(_stats(slope=1.0, built=0.30, tree=0.10), in_kura_delta=False).passed


def test_rule_rejects_above_thresholds_with_reasons() -> None:
    v = evaluate(_stats(slope=1.0001, built=0.31, tree=0.11), in_kura_delta=False)
    assert not v.passed
    assert len(v.reasons) == 3
    assert not evaluate(_stats(slope=math.nan), in_kura_delta=False).passed


def test_deltaic_flag_rule() -> None:
    assert not evaluate(_stats(wet=0.10), in_kura_delta=False).deltaic
    assert evaluate(_stats(wet=0.1001), in_kura_delta=False).deltaic
    assert evaluate(_stats(wet=0.0), in_kura_delta=True).deltaic
    assert evaluate(_stats(wet=0.5), in_kura_delta=False).passed


def test_mean_slope_on_planes() -> None:
    yy, xx = np.mgrid[0:20, 0:30].astype(float) * 30.0
    band = np.ones(xx.shape, dtype=bool)
    assert mean_slope_deg(0.01 * xx, 30.0, band) == pytest.approx(math.degrees(math.atan(0.01)))
    dem = 0.03 * xx + 0.04 * yy
    assert mean_slope_deg(dem, 30.0, band) == pytest.approx(math.degrees(math.atan(0.05)))
    flat = np.zeros(xx.shape)
    assert mean_slope_deg(flat, 30.0, band) == 0.0


def test_mean_slope_uses_band_only_and_skips_nan() -> None:
    xx = np.mgrid[0:10, 0:10][1].astype(float) * 30.0
    dem = np.where(xx < 150.0, 0.0, 0.02 * xx)
    band = np.zeros(dem.shape, dtype=bool)
    band[:, 0:3] = True
    assert mean_slope_deg(dem, 30.0, band) == 0.0
    dem_nan = 0.01 * xx
    dem_nan[5, 5] = np.nan
    assert mean_slope_deg(dem_nan, 30.0, np.ones(dem.shape, dtype=bool)) == pytest.approx(
        math.degrees(math.atan(0.01))
    )
    with pytest.raises(ValueError, match="no band pixels"):
        mean_slope_deg(dem, 30.0, np.zeros(dem.shape, dtype=bool))


def test_class_shares_hand_counted() -> None:
    classes = np.array([[10, 10, 50, 40], [90, 0, 80, 40]], dtype=np.uint8)
    band = np.ones(classes.shape, dtype=bool)
    shares = class_shares(classes, band)
    assert shares == pytest.approx({10: 2 / 7, 50: 1 / 7, 40: 2 / 7, 90: 1 / 7, 80: 1 / 7})
    band[:, 2:] = False
    assert class_shares(classes, band) == pytest.approx({10: 2 / 3, 90: 1 / 3})
    tens = np.array([[50, 50, 50, 40, 40, 40, 40, 40, 40, 40]], dtype=np.uint8)
    assert class_shares(tens, np.ones(tens.shape, dtype=bool))[50] == 0.3


def test_band_mask_width() -> None:
    sea = np.zeros((20, 100), dtype=bool)
    sea[:, 50:] = True
    transform = from_origin(0.0, 600.0, 30.0, 30.0)
    band = band_mask(sea, transform, 300.0)
    cols = np.flatnonzero(band.any(axis=0))
    assert cols.tolist() == list(range(40, 50))
    assert band[:, 40:50].all()


def test_caspian_component_drops_disconnected_water() -> None:
    classes = np.full((10, 10), 40, dtype=np.uint8)
    classes[:, 6:] = 80
    classes[2:4, 1:3] = 80
    classes[8, 5] = 80
    transform = from_origin(0.0, 10.0, 1.0, 1.0)
    sea = caspian_component(classes, transform, (9.5, 5.5))
    assert sea[:, 6:].all()
    assert not sea[2:4, 1:3].any()
    assert sea[8, 5]
    with pytest.raises(ValueError, match="seed"):
        caspian_component(classes, transform, (0.5, 0.5))


def test_coast_crossing_nearest_axis() -> None:
    t = np.arange(-300.0, 301.0, 30.0)
    is_sea = t > 45.0
    assert coast_crossing(t, is_sea) == pytest.approx(45.0)
    two = (t > 45.0) | ((t > -255.0) & (t < -195.0))
    assert coast_crossing(t, two) == pytest.approx(45.0)
    assert coast_crossing(t, np.zeros(t.shape, dtype=bool)) is None


def test_contour_offset_on_ramp_with_noise_spike() -> None:
    t = np.arange(-5000.0, 1.0, 10.0)
    dem = -28.0 + (-t) / 250.0
    assert contour_offset(t, dem, 0.0) == pytest.approx(-1000.0)
    spiky = dem.copy()
    spiky[(t <= -200.0) & (t >= -300.0)] = -20.0
    assert contour_offset(t, spiky, 0.0) == pytest.approx(-1000.0)
    assert contour_offset(t, np.full(t.shape, -27.0), 0.0) is None
    assert contour_offset(t, dem, 0.0, max_inland_m=900.0) is None


def test_smooth_offsets_moves_landward_and_keeps_nan_out() -> None:
    raw = np.array([-1000.0] * 10 + [-400.0] + [-1000.0] * 10)
    out = smooth_offsets(raw)
    assert np.allclose(out, -1000.0)
    dip = np.array([-1000.0] * 10 + [-2000.0] + [-1000.0] * 10)
    assert smooth_offsets(dip)[10] < -1000.0
    with_nan = np.array([np.nan, -1000.0, -1000.0, np.nan, -1000.0])
    smoothed = smooth_offsets(with_nan)
    assert np.isnan(smoothed[[0, 3]]).all()
    assert np.allclose(smoothed[[1, 2, 4]], -1000.0)


def test_required_profile_length() -> None:
    assert required_profile_length([1234.0, 2950.0], 2000.0) == 5000.0
    assert required_profile_length([3000.0], 2000.0) == 5000.0
    length = required_profile_length([4321.0], sel.PROFILE_MARGIN_M)
    assert length >= 4321.0 + 1000.0 and length % 100 == 0
    with pytest.raises(ValueError):
        required_profile_length([], 2000.0)


GRID = Grid(x0=400_000.0, y0=4_400_000.0, pixel_m=30.0, width=4, height=3)


def _noop(_: float) -> None:
    return None


def _dem_ok(hrefs: list[str], grid: Grid) -> np.ndarray:
    return np.full((grid.height, grid.width), -20.0, dtype=np.float32)


def test_fetch_dem_caches_by_grid(tmp_path: Path) -> None:
    calls: list[str] = []

    def search(bbox: tuple[float, float, float, float]) -> list[tuple[str, str]]:
        calls.append("search")
        return [("tile-1", "https://example/tile-1.tif")]

    dem = fetch_dem(GRID, search=search, warp=_dem_ok, sleep=_noop, cache_dir=tmp_path)
    assert dem.shape == (3, 4) and float(dem[0, 0]) == -20.0
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and files[0].suffix == ".tif"

    def boom(bbox: tuple[float, float, float, float]) -> list[tuple[str, str]]:
        raise AssertionError("no search on cache hit")

    again = fetch_dem(GRID, search=boom, warp=_dem_ok, sleep=_noop, cache_dir=tmp_path)
    assert np.array_equal(again, dem)
    moved = Grid(x0=GRID.x0 + 30.0, y0=GRID.y0, pixel_m=30.0, width=4, height=3)
    fetch_dem(moved, search=search, warp=_dem_ok, sleep=_noop, cache_dir=tmp_path)
    assert calls == ["search", "search"]
    assert len(list(tmp_path.iterdir())) == 2


def test_failed_dem_fetch_raises_and_caches_nothing(tmp_path: Path) -> None:
    def search(bbox: tuple[float, float, float, float]) -> list[tuple[str, str]]:
        return [("tile-1", "https://example/tile-1.tif")]

    def failing(hrefs: list[str], grid: Grid) -> np.ndarray:
        raise HttpStatusError(404)

    with pytest.raises(HttpStatusError):
        fetch_dem(GRID, search=search, warp=failing, sleep=_noop, cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []

    def holes(hrefs: list[str], grid: Grid) -> np.ndarray:
        return np.full((grid.height, grid.width), np.nan, dtype=np.float32)

    with pytest.raises(ValueError, match="coverage"):
        fetch_dem(GRID, search=search, warp=holes, sleep=_noop, cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ValueError, match="no Copernicus DEM"):
        fetch_dem(GRID, search=lambda b: [], warp=_dem_ok, sleep=_noop, cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


FORBIDDEN = ("caspianwatch.level", "caspianwatch.season", "wheatwatch.ndvi")
FORBIDDEN_WORDS = ("shore", "water", "landsat", "sentinel", "mndwi", "grealm", "ndvi")


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("module", ["sites.py", "site_selection.py"])
def test_selection_code_imports_no_shoreline_or_imagery_module(module: str) -> None:
    for name in _imports(SRC / module):
        assert name not in FORBIDDEN, name
        assert not any(word in name.lower() for word in FORBIDDEN_WORDS), name


def test_importing_selection_loads_no_level_or_imagery_module() -> None:
    code = (
        "import sys, caspianwatch.site_selection, caspianwatch.sites; "
        f"print(sorted(m for m in sys.modules if m in {FORBIDDEN!r}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": str(SRC.parent)},
    )
    assert out.stdout.strip() == "[]"
