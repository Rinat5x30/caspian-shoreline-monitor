import json
import logging
import math
from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import Affine, from_origin
from shapely.geometry import box

from wheatwatch import ndvi as ndvi_mod
from wheatwatch.climate import MAX_ATTEMPTS, HttpStatusError
from wheatwatch.ndvi import Scene, _scene_values, _scenes_from_items, season_ndvi

SINU = "+proj=sinu +lon_0=0 +x_0=0 +y_0=0 +R=6371007.181 +units=m +no_defs"
R = 6371007.181
PIXEL = 231.6563582
SIZE = 80
LON0, LAT0 = 47.0, 40.0
X0 = R * math.radians(LON0) * math.cos(math.radians(LAT0))
Y0 = R * math.radians(LAT0)
STEP = 1 / 1200

Search = Callable[[int], list[Scene]]


def _regions(half: float = 0.01) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"region_id": ["alpha"]},
        geometry=[box(LON0 - half, LAT0 - half, LON0 + half, LAT0 + half)],
        crs="EPSG:4326",
    )


def _write_mask(path: Path, north: int = 40, south: int = 40) -> Path:
    n = 240
    data = np.full((n, n), south, dtype="uint8")
    data[: n // 2, :] = north
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=n,
        width=n,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(LON0 - 120 * STEP, LAT0 + 120 * STEP, STEP, STEP),
        nodata=0,
    ) as dst:
        dst.write(data, 1)
    return path


def _write_tile(path: Path, data: np.ndarray, dtype: str, y_top: float | None = None) -> str:
    top = Y0 + SIZE / 2 * PIXEL if y_top is None else y_top
    transform = Affine(PIXEL, 0, X0 - SIZE / 2 * PIXEL, 0, -PIXEL, top)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=SIZE,
        width=SIZE,
        count=1,
        dtype=dtype,
        crs=SINU,
        transform=transform,
    ) as dst:
        dst.write(data.astype(dtype), 1)
    return str(path)


def _scene(
    tmp: Path,
    day: date,
    value: float | np.ndarray = 0.6,
    rel: int | np.ndarray = 0,
    name: str = "",
    raw: bool = False,
    y_top: float | None = None,
    bbox: tuple[float, float, float, float] = (40.0, 35.0, 55.0, 45.0),
) -> Scene:
    ndvi = np.zeros((SIZE, SIZE), dtype="int16")
    ndvi[:] = value if raw else np.round(np.asarray(value) * 10000)
    quality = np.zeros((SIZE, SIZE), dtype="int8")
    quality[:] = rel
    stem = f"{name}{day.isoformat()}"
    return Scene(
        day=day,
        ndvi_href=_write_tile(tmp / f"ndvi_{stem}.tif", ndvi, "int16", y_top),
        quality_href=_write_tile(tmp / f"rel_{stem}.tif", quality, "int8", y_top),
        bbox=bbox,
    )


def _fixed(scenes: list[Scene]) -> Search:
    return lambda year: [s for s in scenes if s.day.year == year]


def _run(
    tmp: Path,
    scenes: list[Scene] | Search,
    years: list[int] | None = None,
    regions: gpd.GeoDataFrame | None = None,
    mask: Path | None = None,
    sleep: Callable[[float], None] = lambda s: None,
) -> pd.DataFrame:
    tmp.mkdir(parents=True, exist_ok=True)
    search = _fixed(scenes) if isinstance(scenes, list) else scenes
    return season_ndvi(
        regions if regions is not None else _regions(),
        years or [2020],
        search=search,
        sleep=sleep,
        cache_dir=tmp / "cache",
        mask_path=mask or _write_mask(tmp / "mask.tif"),
    )


def _peak(frame: pd.DataFrame) -> float:
    return float(frame["ndvi_peak"].iloc[0])


def test_ndvi_peak_ignores_non_cropland_pixels(tmp_path: Path) -> None:
    rows = np.arange(SIZE)[:, None] * np.ones((1, SIZE))
    centre = SIZE // 2
    ndvi = np.where(rows < centre, 0.5, 0.9)
    scene = _scene(tmp_path, date(2020, 5, 1), value=ndvi)
    mask = _write_mask(tmp_path / "m.tif", north=40, south=10)
    assert _peak(_run(tmp_path, [scene], mask=mask)) == pytest.approx(0.5, abs=1e-6)


def test_missing_scenes_year_returns_nan_not_zero(tmp_path: Path) -> None:
    scenes = [_scene(tmp_path, date(2020, 5, 1), value=-3000, raw=True)]
    frame = _run(tmp_path, scenes)
    assert math.isnan(_peak(frame))
    assert frame["ndvi_peak"].dtype == "float64"


def test_year_with_scenes_only_outside_window_is_nan(tmp_path: Path) -> None:
    scenes = [_scene(tmp_path, date(2020, 2, 28)), _scene(tmp_path, date(2020, 7, 1))]
    assert math.isnan(_peak(_run(tmp_path, scenes)))


def test_empty_search_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no MODIS scenes"):
        _run(tmp_path, [])


def test_fill_values_excluded_not_averaged(tmp_path: Path) -> None:
    rows = np.arange(SIZE)[:, None] * np.ones((1, SIZE))
    ndvi = np.where(rows % 2 == 0, -3000, 6000)
    scene = _scene(tmp_path, date(2020, 5, 1), value=ndvi, raw=True)
    assert _peak(_run(tmp_path, [scene])) == pytest.approx(0.6)


def test_out_of_range_ndvi_excluded(tmp_path: Path) -> None:
    rows = np.arange(SIZE)[:, None] * np.ones((1, SIZE))
    ndvi = np.where(rows % 2 == 0, 15000, 6000)
    scene = _scene(tmp_path, date(2020, 5, 1), value=ndvi, raw=True)
    assert _peak(_run(tmp_path, [scene])) == pytest.approx(0.6)


@pytest.mark.parametrize("bad", [1, 2, 3, -1])
def test_only_good_quality_pixels_kept(tmp_path: Path, bad: int) -> None:
    rows = np.arange(SIZE)[:, None] * np.ones((1, SIZE))
    rel = np.where(rows % 2 == 0, bad, 0)
    scene = _scene(tmp_path, date(2020, 5, 1), value=np.where(rows % 2 == 0, 0.9, 0.6), rel=rel)
    other = _scene(tmp_path, date(2020, 5, 17), value=0.2, rel=0, name="b")
    assert _peak(_run(tmp_path, [scene, other])) == pytest.approx(0.6)
    all_bad = _scene(tmp_path, date(2020, 5, 1), value=0.9, rel=bad, name="c")
    assert math.isnan(_peak(_run(tmp_path / "y", [all_bad])))


def test_peak_is_max_over_dates_of_per_date_median(tmp_path: Path) -> None:
    rows = np.arange(SIZE)[:, None] * np.ones((1, SIZE))
    spiky = np.where(rows % 5 == 0, 0.95, 0.3)
    scenes = [
        _scene(tmp_path, date(2020, 4, 1), value=0.4),
        _scene(tmp_path, date(2020, 4, 17), value=0.7),
        _scene(tmp_path, date(2020, 5, 3), value=0.55),
    ]
    assert _peak(_run(tmp_path, scenes)) == pytest.approx(0.7)
    only_spiky = [_scene(tmp_path, date(2020, 4, 1), value=spiky, name="s")]
    assert _peak(_run(tmp_path / "z", only_spiky)) == pytest.approx(0.3)


@pytest.mark.parametrize(
    "inside_day", [date(2020, 3, 1), date(2020, 6, 30)], ids=["start-inclusive", "end-inclusive"]
)
def test_window_boundaries_inclusive_and_outside_ignored(tmp_path: Path, inside_day: date) -> None:
    scenes = [
        _scene(tmp_path, date(2020, 2, 28), value=0.99, name="a"),
        _scene(tmp_path, date(2020, 7, 1), value=0.98, name="b"),
        _scene(tmp_path, inside_day, value=0.8, name="c"),
        _scene(tmp_path, date(2020, 4, 15), value=0.5, name="d"),
    ]
    assert _peak(_run(tmp_path, scenes)) == pytest.approx(0.8)


def test_geometry_is_reprojected_to_raster_crs(tmp_path: Path) -> None:
    rows = np.arange(SIZE)[:, None]
    cols = np.arange(SIZE)[None, :]
    inside = (abs(rows - SIZE / 2) < 20) & (abs(cols - SIZE / 2) < 20)
    ndvi = np.where(inside, 0.7, 0.2)
    scene = _scene(tmp_path, date(2020, 5, 1), value=ndvi)
    assert _peak(_run(tmp_path, [scene])) == pytest.approx(0.7)
    far = gpd.GeoDataFrame(
        {"region_id": ["alpha"]}, geometry=[box(46.5, 40.5, 46.6, 40.6)], crs="EPSG:4326"
    )
    assert math.isnan(_peak(_run(tmp_path / "far", [scene], regions=far)))


def test_fewer_than_min_pixels_is_nan_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    scene = _scene(tmp_path, date(2020, 5, 1), value=0.6)
    tiny = _regions(half=0.001)
    with caplog.at_level(logging.WARNING, logger="wheatwatch.ndvi"):
        frame = _run(tmp_path, [scene], regions=tiny)
    assert math.isnan(_peak(frame))
    assert "1 region-year" in caplog.text
    assert _peak(_run(tmp_path / "big", [scene])) == pytest.approx(0.6)


def test_result_schema(tmp_path: Path) -> None:
    frame = _run(tmp_path, [_scene(tmp_path, date(2020, 5, 1))], years=[2020])
    assert list(frame.columns) == ["region_id", "year", "ndvi_peak"]
    assert frame["year"].dtype == "int64" and frame["region_id"].tolist() == ["alpha"]


class CountingSearch:
    def __init__(self, scenes: list[Scene]) -> None:
        self.scenes = scenes
        self.calls = 0

    def __call__(self, year: int) -> list[Scene]:
        self.calls += 1
        return [s for s in self.scenes if s.day.year == year]


def test_cache_hit_avoids_search_and_mask(tmp_path: Path) -> None:
    search = CountingSearch([_scene(tmp_path, date(2020, 5, 1), value=0.6)])
    first = _run(tmp_path, search)
    assert search.calls == 1

    def boom(year: int) -> list[Scene]:
        raise AssertionError("network used")

    second = season_ndvi(
        _regions(), [2020], search=boom, cache_dir=tmp_path / "cache", mask_path=tmp_path / "nope"
    )
    assert _peak(second) == _peak(first)


@pytest.mark.parametrize(
    "attr, value",
    [
        ("SEASON_END", (6, 29)),
        ("SEASON_START", (3, 2)),
        ("CROPLAND_FRACTION_MIN", 0.6),
        ("MIN_PIXELS", 6),
    ],
)
def test_settings_change_invalidates_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attr: str, value: object
) -> None:
    search = CountingSearch([_scene(tmp_path, date(2020, 5, 1), value=0.6)])
    _run(tmp_path, search)
    _run(tmp_path, search)
    assert search.calls == 1
    monkeypatch.setattr(ndvi_mod, attr, value)
    _run(tmp_path, search)
    assert search.calls == 2


def test_nan_results_are_not_cached(tmp_path: Path) -> None:
    search = CountingSearch([_scene(tmp_path, date(2020, 7, 1), value=0.6)])
    assert math.isnan(_peak(_run(tmp_path, search)))
    assert not list((tmp_path / "cache").glob("ndvi_*"))
    _run(tmp_path, search)
    assert search.calls == 2


def test_corrupt_or_old_format_cache_is_a_miss(tmp_path: Path) -> None:
    search = CountingSearch([_scene(tmp_path, date(2020, 5, 1), value=0.6)])
    _run(tmp_path, search)
    (path,) = (tmp_path / "cache").glob("ndvi_*.json")
    path.write_text("{not json", encoding="utf-8")
    assert _peak(_run(tmp_path, search)) == pytest.approx(0.6)
    path.write_text(json.dumps({"region_id": "alpha", "ndvi_peak": 0.1}), encoding="utf-8")
    assert _peak(_run(tmp_path, search)) == pytest.approx(0.6)
    assert search.calls == 3


@pytest.mark.parametrize("bad", [float("nan"), 0.0, float("inf")])
def test_cached_zero_or_nan_value_is_a_miss(tmp_path: Path, bad: float) -> None:
    search = CountingSearch([_scene(tmp_path, date(2020, 5, 1), value=0.6)])
    _run(tmp_path, search)
    (path,) = (tmp_path / "cache").glob("ndvi_*.json")
    record = json.loads(path.read_text(encoding="utf-8"))
    record["ndvi_peak"] = bad
    path.write_text(json.dumps(record), encoding="utf-8")
    assert _peak(_run(tmp_path, search)) == pytest.approx(0.6)
    assert search.calls == 2


def test_unreadable_scene_raises_and_caches_nothing(tmp_path: Path) -> None:
    scene = _scene(tmp_path, date(2020, 5, 1))
    broken = Scene(scene.day, str(tmp_path / "missing.tif"), scene.quality_href, scene.bbox)
    sleeps: list[float] = []
    with pytest.raises(OSError):
        _run(tmp_path, [broken], sleep=sleeps.append)
    assert len(sleeps) == MAX_ATTEMPTS - 1
    assert not list((tmp_path / "cache").glob("ndvi_*"))


def test_search_retries_transient_but_not_client_errors(tmp_path: Path) -> None:
    scene = _scene(tmp_path, date(2020, 5, 1), value=0.6)
    attempts = {"n": 0}

    def flaky(year: int) -> list[Scene]:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise HttpStatusError(503)
        return [scene]

    sleeps: list[float] = []
    assert _peak(_run(tmp_path, flaky, sleep=sleeps.append)) == pytest.approx(0.6)
    assert len(sleeps) == 2

    def forbidden(year: int) -> list[Scene]:
        raise HttpStatusError(403)

    sleeps.clear()
    with pytest.raises(HttpStatusError):
        _run(tmp_path / "f", forbidden, sleep=sleeps.append)
    assert sleeps == []


def _item(item_id: str, stamp: str = "2020-05-01T00:00:00Z") -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        properties={"start_datetime": stamp},
        bbox=[40.0, 35.0, 55.0, 45.0],
        assets={
            ndvi_mod.NDVI_ASSET: SimpleNamespace(href=f"{item_id}-ndvi"),
            ndvi_mod.QUALITY_ASSET: SimpleNamespace(href=f"{item_id}-rel"),
        },
    )


def test_aqua_items_are_dropped_and_terra_kept() -> None:
    items = [
        _item("MOD13Q1.A2020121.h21v05.061.x"),
        _item("MYD13Q1.A2020129.h21v05.061.y", "2020-05-08T00:00:00Z"),
        _item("MOD13Q1.A2020137.h21v04.061.z", "2020-05-16T00:00:00Z"),
    ]
    scenes = _scenes_from_items(items)
    assert [s.ndvi_href for s in scenes] == [
        "MOD13Q1.A2020121.h21v05.061.x-ndvi",
        "MOD13Q1.A2020137.h21v04.061.z-ndvi",
    ]
    assert scenes[0].day == date(2020, 5, 1)


def test_only_aqua_items_raises() -> None:
    with pytest.raises(ValueError, match="no MODIS scenes"):
        _scenes_from_items([_item("MYD13Q1.A2020129.h21v05.061.y")])
    with pytest.raises(ValueError, match="no MODIS scenes"):
        _scenes_from_items([])


def test_same_date_tiles_are_pooled_and_out_of_region_scene_dropped(tmp_path: Path) -> None:
    day = date(2020, 5, 1)
    north = _scene(tmp_path, day, value=0.4, name="n", y_top=Y0 + SIZE * PIXEL)
    south = _scene(tmp_path, day, value=0.8, name="s", y_top=Y0)
    assert _peak(_run(tmp_path, [north, south])) == pytest.approx(0.6)
    decoy = _scene(tmp_path, day, value=0.95, name="d", bbox=(10.0, 10.0, 11.0, 11.0))
    assert _peak(_run(tmp_path / "decoy", [north, south, decoy])) == pytest.approx(0.6)


def test_x_pixel_alignment_exact_count(tmp_path: Path) -> None:
    cols = np.arange(SIZE)[None, :] * np.ones((SIZE, 1))
    ndvi = np.where(cols == SIZE // 2, 5000, -3000)
    scene = _scene(tmp_path, date(2020, 5, 1), value=ndvi, raw=True)
    values = _scene_values(scene, _regions().geometry.iloc[0], _write_mask(tmp_path / "m.tif"), {})
    assert values.size == 10
    assert values == pytest.approx(0.5)
