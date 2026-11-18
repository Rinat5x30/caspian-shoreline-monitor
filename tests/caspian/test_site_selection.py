import ast
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box

from caspianwatch import site_selection as sel
from caspianwatch.site_selection import (
    BUILT_MAX,
    SLOPE_MAX_DEG,
    TREE_MAX,
    WETLAND_DELTAIC_MIN,
    BandStats,
    Grid,
    Layers,
    band_mask,
    build_layers,
    caspian_component,
    class_shares,
    coast_line,
    dem_diagnostics,
    evaluate,
    fetch_dem,
    geometry_ok,
    mean_slope_deg,
    obliquity_deg,
    open_sea,
    orient_sea_left,
    profile_valid,
    required_profile_length,
    sample_layers,
    site_geometry,
    smooth_line,
)
from caspianwatch.sites import DEFAULT_SITES, Profile, Site, load_sites, make_profiles
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
    assert sel.PROFILE_MARGIN_M == 1500.0
    assert sel.INLAND_M == {
        "gizilaghaj": 10_000.0,
        "kura_delta": 6_000.0,
        "lankaran_astara": 3_000.0,
    }
    assert sel.MIN_VALID_PROFILES == 30


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
RUNTIME_FORBIDDEN = ("caspianwatch.level", "caspianwatch.season")
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


def test_runtime_selection_path_loads_no_level_module(tmp_path: Path) -> None:
    # cache.retry reuses wheatwatch.ndvi._retry (a generic HTTP back-off helper); no NDVI or
    # imagery data is read. Level and season modules must stay unloaded on the real code path.
    code = (
        "import sys, numpy as np\n"
        "from pathlib import Path\n"
        "from caspianwatch.site_selection import Grid, fetch_dem\n"
        "g = Grid(400000.0, 4400000.0, 30.0, 2, 2)\n"
        "fetch_dem(g, search=lambda b: [('t', 'h')], "
        "warp=lambda h, grid: np.zeros((2, 2)), sleep=lambda s: None, "
        f"cache_dir=Path({str(tmp_path)!r}))\n"
        f"print(sorted(m for m in sys.modules if m in {RUNTIME_FORBIDDEN!r}))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": str(SRC.parent)},
    )
    assert out.stdout.strip() == "[]"


PX = 30.0
WORLDCOVER = sel.ROOT / "data" / "raw" / "cache" / "worldcover2021_cropland_az_100m.tif"


def _layers(sea: np.ndarray, dem: np.ndarray | None = None) -> Layers:
    h, w = sea.shape
    grid = Grid(0.0, h * PX, PX, w, h)
    classes = np.where(sea, 80, 30).astype(np.uint8)
    center = (w * PX / 2, h * PX / 2)
    xs = (np.arange(w) + 0.5) * PX
    ys = h * PX - (np.arange(h) + 0.5) * PX
    disk = np.hypot(xs[None, :] - center[0], ys[:, None] - center[1]) <= sel.SEGMENT_RADIUS_M
    return Layers(grid, center, classes, sea, disk, np.zeros(sea.shape) if dem is None else dem)


def _straight_layers(size: int = 800, sea_col: int = 400) -> Layers:
    sea = np.zeros((size, size), dtype=bool)
    sea[:, sea_col:] = True
    return _layers(sea)


def _water_of(layers: Layers) -> sel.WaterTest:
    def is_water(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        return sample_layers(layers, xs, ys)[1]

    return is_water


def test_sample_layers_inside_and_outside_grid() -> None:
    layers = _straight_layers(size=10, sea_col=5)
    layers.dem[2, 7] = 5.0
    xs = np.array([7.5 * PX, 1.5 * PX, -10.0, 400.0])
    ys = np.array([(10 - 2.5) * PX, 150.0, 150.0, 1e6])
    dem, sea = sample_layers(layers, xs, ys)
    assert dem[0] == 5.0 and sea[0]
    assert dem[1] == 0.0 and not sea[1]
    assert np.isnan(dem[2:]).all() and not sea[2:].any()


def test_build_layers_maps_global_rasters_to_the_local_grid() -> None:
    step = 1 / 1200
    lon0, lat0 = 49.0, 40.2
    classes = np.full((480, 480), 30, dtype=np.uint8)
    classes[:, 240:] = 80
    transform = from_origin(lon0, lat0, step, step)
    centre = sel._TO_UTM.transform(lon0 + 240 * step, lat0 - 240 * step)
    calls: list[Grid] = []

    def dem_fetch(grid: Grid) -> np.ndarray:
        calls.append(grid)
        return np.zeros((grid.height, grid.width))

    layers = build_layers(centre, classes, classes == 80, transform, dem_fetch=dem_fetch)
    n = int(round(2 * sel.GRID_HALF_M / sel.PIXEL_M))
    assert layers.sea.shape == (n, n) and calls == [layers.grid]
    assert (layers.classes[layers.sea] == 80).all()
    assert 0.3 < layers.sea[layers.disk].mean() < 0.7
    expected = math.pi * sel.SEGMENT_RADIUS_M**2 / sel.PIXEL_M**2
    assert layers.disk.sum() == pytest.approx(expected, rel=0.01)


def test_open_sea_drops_lagoon_behind_barrier_and_channel() -> None:
    sea = np.zeros((60, 60), dtype=bool)
    sea[:, 40:] = True
    sea[10:50, 10:30] = True
    sea[29:31, 30:40] = True
    opened = open_sea(sea, erode_px=3)
    assert opened[:, 40:].all()
    assert not opened[10:50, 10:30].any()
    assert not opened[29:31, 30:37].any()
    assert not (opened & ~sea).any()


def test_coast_line_and_smoothing_on_straight_coast() -> None:
    layers = _straight_layers()
    line = coast_line(open_sea(layers.sea, 3), layers.grid.transform, layers.center)
    assert line.length == pytest.approx(2 * sel.SEGMENT_RADIUS_M, rel=0.02)
    xs = [x for x, _ in line.coords]
    assert min(xs) == pytest.approx(400 * PX) and max(xs) == pytest.approx(400 * PX)
    zigzag = LineString([(i * 30.0, 30.0 * (i % 2)) for i in range(200)])
    smooth = smooth_line(zigzag, window_m=1000.0)
    inner = [y for x, y in smooth.coords if 1000.0 < x < 5000.0]
    assert max(inner) - min(inner) < 2.0


def test_direction_sign_by_sampling() -> None:
    is_sea = _water_of(_straight_layers())
    north = LineString([(400 * PX, 2000.0), (400 * PX, 20_000.0)])
    south = LineString([(400 * PX, 20_000.0), (400 * PX, 2000.0)])
    assert list(orient_sea_left(south, is_sea).coords) == list(south.coords)
    assert list(orient_sea_left(north, is_sea).coords) == list(south.coords)


def test_delta_lobe_profile_starting_in_sea_is_invalid() -> None:
    lobe = box(-1000.0, 0.0, 1000.0, 10_000.0)

    def is_water(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        return np.array([not lobe.contains(Point(x, y)) for x, y in zip(xs, ys, strict=True)])

    east_flank = LineString([(1000.0, 8000.0), (1000.0, 2000.0)])
    deep = Site("lobe", "Lobe", east_flank, (1.0, 0.0), True, 4500.0, 3000.0)
    shallow = Site("lobe", "Lobe", east_flank, (1.0, 0.0), True, 2000.0, 500.0)
    assert not any(profile_valid(make_profiles(deep), is_water))
    assert all(profile_valid(make_profiles(shallow), is_water))


def test_min_valid_profiles_rule() -> None:
    assert not geometry_ok(29)
    assert geometry_ok(30)
    assert not geometry_ok(0)


def test_obliquity_zero_on_straight_coast_and_45_on_skewed_profile() -> None:
    coast = LineString([(0.0, 10_000.0), (0.0, 0.0)])
    straight = Profile("s", 0, (-1000.0, 5000.0), (1.0, 0.0), 2500.0)
    skew = Profile("s", 1, (-1000.0, 5000.0), (math.sqrt(0.5), math.sqrt(0.5)), 2500.0)
    assert obliquity_deg([straight, skew], coast) == pytest.approx([0.0, 45.0], abs=1e-6)


def test_dem_diagnostics_flattened_share_and_slope() -> None:
    xx = np.mgrid[0:20, 0:20][1].astype(float) * PX
    dem = 0.01 * xx
    dem[:, :10] = -28.0
    band = np.zeros(dem.shape, dtype=bool)
    band[2:-2, 2:-2] = True
    diag = dem_diagnostics(dem, band)
    assert diag["flat_value_m"] == -28.0
    assert diag["flattened_share"] == pytest.approx(8 / 16)
    assert diag["slope_without_flattened_deg"] > 0.0


def test_site_geometry_on_straight_coast() -> None:
    layers = _straight_layers()
    cand = sel.Candidate("syn", "Synthetic", 0.0, 0.0, False, "test")
    result = site_geometry(cand, layers, 3000.0, False, _water_of(layers))
    assert result.site.inland_m == 3000.0
    assert result.site.profile_length_m == 4500.0
    assert result.site.seaward == pytest.approx((1.0, 0.0), abs=1e-3)
    assert result.total == len(result.kept) + len(result.site.dropped_profiles)
    assert result.site.dropped_profiles == () and result.crossings == 0
    assert max(result.obliquity) < 1.0
    for record in result.kept:
        assert record["distance_to_coast_m"] == pytest.approx(3000.0, abs=31.0)
        assert record["origin"][0] == pytest.approx(400 * PX - 3000.0, abs=1.0)


def test_site_geometry_lagoon_is_not_the_coast() -> None:
    sea = np.zeros((800, 800), dtype=bool)
    sea[:, 500:] = True
    sea[300:500, 330:420] = True
    sea[399:401, 420:500] = True
    layers = _layers(sea)
    cand = sel.Candidate("lag", "Lagoon", 0.0, 0.0, False, "test")
    result = site_geometry(cand, layers, 1000.0, False, _water_of(layers))
    xs = [x for x, _ in result.site.reference_line.coords]
    assert min(xs) > 480 * PX


@pytest.mark.skipif(not WORLDCOVER.exists(), reason="cached WorldCover raster absent")
def test_repository_profiles_start_on_land_and_end_in_sea() -> None:
    import rasterio

    with rasterio.open(WORLDCOVER) as ds:
        for site in load_sites():
            profiles = make_profiles(site)
            ends = [
                (
                    p.origin[0] + p.direction[0] * p.length_m,
                    p.origin[1] + p.direction[1] * p.length_m,
                )
                for p in profiles
            ]
            for points, water in (([p.origin for p in profiles], False), (ends, True)):
                lon, lat = sel._TO_LONLAT.transform([x for x, _ in points], [y for _, y in points])
                values = [int(v[0]) for v in ds.sample(list(zip(lon, lat, strict=True)))]
                assert all((v == 80) is water for v in values), site.site_id


def test_invalid_profiles_are_dropped_and_counted() -> None:
    layers = _straight_layers()
    cand = sel.Candidate("syn", "Synthetic", 0.0, 0.0, False, "test")
    sea_of = _water_of(layers)

    def is_water(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        return np.asarray(sea_of(xs, ys) | ((ys > 11_000.0) & (ys < 13_000.0)), dtype=bool)

    result = site_geometry(cand, layers, 3000.0, False, is_water)
    dropped = result.site.dropped_profiles
    assert 8 <= len(dropped) <= 11
    assert len(result.kept) + len(dropped) == result.total
    assert {r["index"] for r in result.kept}.isdisjoint(dropped)
    assert all(11_000.0 < y < 13_000.0 for y in (p.origin[1] for p in _all(result.site, dropped)))
    row = sel._geometry_row(result, -28.0, 0.0)
    assert row["profiles_dropped"] == len(dropped)
    assert row["dropped_share"] == pytest.approx(len(dropped) / result.total, abs=1e-4)
    assert row["status"] == "ok" and row["geometry_caveat"] == "none"
    assert [p.index for p in make_profiles(result.site)] == [r["index"] for r in result.kept]


def _all(site: Site, indices: tuple[int, ...]) -> list[Profile]:
    full = Site(**{**site.__dict__, "dropped_profiles": ()})
    return [p for p in make_profiles(full) if p.index in indices]


def test_site_geometry_is_deterministic() -> None:
    layers = _straight_layers()
    cand = sel.Candidate("syn", "Synthetic", 0.0, 0.0, False, "test")
    first = site_geometry(cand, layers, 3000.0, False, _water_of(layers))
    second = site_geometry(cand, layers, 3000.0, False, _water_of(layers))
    assert first.kept == second.kept and first.site == second.site


def _caches_present() -> bool:
    cache = sel.CACHE_DIR
    return WORLDCOVER.exists() and cache.exists() and len(list(cache.glob("copdem_*.tif"))) >= 11


@pytest.mark.skipif(not _caches_present(), reason="cached WorldCover / DEM rasters absent")
def test_rerun_reproduces_stored_sites_json() -> None:
    import json
    from datetime import date

    stored = json.loads(DEFAULT_SITES.read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(sel.run(date.fromisoformat(stored["run_date"]))))
    assert fresh == stored
