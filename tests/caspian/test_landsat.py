import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray
from shapely.geometry import LineString

from caspianwatch import landsat
from caspianwatch.landsat import (
    MIN_SCENES,
    SceneBands,
    SceneRef,
    WaterMask,
    composite_mndwi,
    mosaic_same_date,
    otsu_threshold,
    reflectance,
    refs_from_items,
    scene_mndwi,
    season_water_mask,
    select_refs,
    site_grid,
)
from caspianwatch.season import Season
from caspianwatch.site_selection import Grid
from caspianwatch.sites import Site, make_profiles, profile_end
from wheatwatch.climate import HttpStatusError

X0, Y0 = 400_000.0, 4_400_000.0
SEASON = Season(7, 8)
GREEN_WATER, SWIR_WATER = 0.10, 0.02
GREEN_LAND, SWIR_LAND = 0.20, 0.30


def _dn(refl: NDArray[np.float64] | float) -> NDArray[np.float64]:
    return np.asarray(np.round((np.asarray(refl) + 0.2) / 0.0000275), dtype=float)


def _site() -> Site:
    line = LineString([(X0, Y0 + 1500.0), (X0, Y0)])
    return Site("t", "T", line, (1.0, 0.0), False, 1500.0, 500.0)


def _ref(day: date, sid: str = "S", platform: str = "landsat-8", cloud: float = 10.0) -> SceneRef:
    return SceneRef(sid, day, platform, cloud, "T1", {"green": "g", "swir16": "s", "qa_pixel": "q"})


def _sea_east(grid: Grid) -> NDArray[np.bool_]:
    cols = np.arange(grid.width)
    x = grid.x0 + (cols + 0.5) * grid.pixel_m
    return np.broadcast_to(x > X0, (grid.height, grid.width)).copy()


def _bands(
    grid: Grid,
    *,
    fill: NDArray[np.bool_] | None = None,
    qa: NDArray[np.uint16] | None = None,
    bright: NDArray[np.bool_] | None = None,
) -> SceneBands:
    water = _sea_east(grid)
    green = np.where(water, GREEN_WATER, GREEN_LAND)
    swir = np.where(water, SWIR_WATER, SWIR_LAND)
    if bright is not None:
        green = np.where(bright, 0.5, green)
        swir = np.where(bright, 0.5, swir)
    g, s = _dn(green), _dn(swir)
    quality = np.zeros(g.shape, dtype=np.uint16) if qa is None else qa.copy()
    if fill is not None:
        g[fill] = 0.0
        s[fill] = 0.0
        quality[fill] |= 1
    return SceneBands(g, s, quality)


class Fakes:
    def __init__(self, refs: list[SceneRef], per_scene: dict[str, SceneBands] | None = None):
        self.refs = refs
        self.per_scene = per_scene or {}
        self.searches = 0
        self.reads = 0

    def search(self, bbox: Any, season: Season, year: int) -> list[SceneRef]:
        self.searches += 1
        return list(self.refs)

    def read(self, ref: SceneRef, grid: Grid) -> SceneBands:
        self.reads += 1
        return self.per_scene.get(ref.scene_id) or _bands(grid)


def _run(
    fakes: Fakes, tmp_path: Path, season: Season = SEASON, year: int = 2012
) -> WaterMask | None:
    return season_water_mask(
        _site(),
        year,
        season,
        search=fakes.search,
        read=fakes.read,
        sleep=lambda _s: None,
        cache_dir=tmp_path,
    )


def _three_dates() -> list[SceneRef]:
    return [_ref(date(2012, 7, d), f"S{d}") for d in (3, 12, 20)]


def test_mndwi_water_pixels_above_threshold(tmp_path: Path) -> None:
    result = _run(Fakes(_three_dates()), tmp_path)
    assert result is not None
    grid = site_grid(_site())
    truth = _sea_east(grid)
    assert result.valid.all()
    assert np.array_equal(result.water, truth)
    assert result.n_scenes == 3
    assert result.crs == "EPSG:32639"
    assert result.transform == grid.transform
    lo, hi = landsat.THRESHOLD_BOUNDS
    assert lo <= landsat.read_record(tmp_path, _site(), 2012)["threshold"] <= hi


@pytest.mark.parametrize("bit", [0, 1, 2, 3, 4])
def test_qa_bit_masks_pixel(bit: int) -> None:
    qa = np.zeros((2, 2), dtype=np.uint16)
    qa[0, 0] = 1 << bit
    green = _dn(np.full((2, 2), 0.1))
    swir = _dn(np.full((2, 2), 0.02))
    _, valid = scene_mndwi(SceneBands(green, swir, qa))
    assert not valid[0, 0]
    assert valid.sum() == 3


@pytest.mark.parametrize("bit", [5, 6, 7])
def test_unused_qa_bits_do_not_mask(bit: int) -> None:
    qa = np.full((2, 2), 1 << bit, dtype=np.uint16)
    green = _dn(np.full((2, 2), 0.1))
    swir = _dn(np.full((2, 2), 0.02))
    _, valid = scene_mndwi(SceneBands(green, swir, qa))
    assert valid.all()


def test_cloud_and_shadow_pixels_masked_by_qa(tmp_path: Path) -> None:
    grid = site_grid(_site())
    cloud = np.zeros((grid.height, grid.width), dtype=bool)
    cloud[10:20, 10:20] = True
    qa = np.zeros(cloud.shape, dtype=np.uint16)
    qa[cloud] = (1 << 3) | (1 << 4)
    wrong = _bands(grid, qa=qa, bright=cloud)
    refs = _three_dates()
    result = _run(Fakes(refs, {"S3": wrong}), tmp_path)
    assert result is not None
    assert np.array_equal(result.water, _sea_east(grid))
    assert result.valid.all()


def test_reflectance_scale_offset_fill_and_range() -> None:
    dn = np.array([0.0, 7273.0, 20000.0, 65535.0, 7000.0])
    refl, valid = reflectance(dn)
    assert valid.tolist() == [False, True, True, False, True]
    assert refl[2] == pytest.approx(20000 * 0.0000275 - 0.2)
    assert refl[1] == pytest.approx(7273 * 0.0000275 - 0.2)


def test_median_composite_and_fewer_than_two_observations_is_no_data() -> None:
    a = np.array([[0.1, 0.1, 0.1]])
    b = np.array([[0.3, 0.3, 0.3]])
    c = np.array([[0.9, 0.9, 0.9]])
    va = np.array([[True, True, False]])
    vb = np.array([[True, False, False]])
    vc = np.array([[True, True, True]])
    median, valid = composite_mndwi([(a, va), (b, vb), (c, vc)])
    assert valid.tolist() == [[True, True, False]]
    assert median[0, 0] == pytest.approx(0.3)
    assert median[0, 1] == pytest.approx(0.5)


def test_composite_of_one_scene_is_all_no_data() -> None:
    a = np.zeros((2, 2))
    _, valid = composite_mndwi([(a, np.ones((2, 2), dtype=bool))])
    assert not valid.any()


def test_slc_off_stripes_do_not_create_land_strips(tmp_path: Path) -> None:
    grid = site_grid(_site())
    stripes = np.zeros((grid.height, grid.width), dtype=bool)
    stripes[:, ::4] = True
    stripes[:, 1::4] = True
    refs = [
        _ref(date(2012, 7, 3), "L7", "landsat-7"),
        _ref(date(2012, 7, 12), "A"),
        _ref(date(2012, 7, 20), "B"),
    ]
    result = _run(Fakes(refs, {"L7": _bands(grid, fill=stripes)}), tmp_path)
    assert result is not None
    assert result.valid.all()
    assert np.array_equal(result.water, _sea_east(grid))


def test_slc_off_gap_with_one_other_scene_stays_no_data_not_land(tmp_path: Path) -> None:
    grid = site_grid(_site())
    stripes = np.zeros((grid.height, grid.width), dtype=bool)
    stripes[:, ::4] = True
    refs = [
        _ref(date(2012, 7, 3), "L7", "landsat-7"),
        _ref(date(2012, 7, 12), "A"),
        _ref(date(2012, 7, 20), "B"),
    ]
    gap = _bands(grid, fill=stripes)
    result = _run(Fakes(refs, {"L7": gap, "A": gap}), tmp_path)
    assert result is not None
    assert not result.valid[:, ::4].any()
    assert not result.water[~result.valid].any()
    assert result.valid[:, 1::4].all()


def test_otsu_threshold_clipped_to_bounds() -> None:
    high = np.concatenate([np.full(500, 0.5), np.full(500, 0.9)])
    low = np.concatenate([np.full(500, -0.9), np.full(500, -0.6)])
    mid = np.concatenate([np.full(500, -0.3), np.full(500, 0.7)])
    assert otsu_threshold(high) == 0.4
    assert otsu_threshold(low) == -0.2
    assert -0.2 < otsu_threshold(mid) < 0.4


def test_otsu_rejects_constant_values() -> None:
    with pytest.raises(ValueError):
        otsu_threshold(np.full(100, 0.3))


def test_distinct_dates_counted_once_and_tiles_mosaicked(tmp_path: Path) -> None:
    grid = site_grid(_site())
    west = np.zeros((grid.height, grid.width), dtype=bool)
    west[:, : grid.width // 2] = True
    refs = [
        _ref(date(2012, 7, 3), "T-a"),
        _ref(date(2012, 7, 3), "T-b"),
        _ref(date(2012, 7, 12), "U"),
        _ref(date(2012, 7, 20), "V"),
    ]
    per = {"T-a": _bands(grid, fill=~west), "T-b": _bands(grid, fill=west)}
    result = _run(Fakes(refs, per), tmp_path)
    assert result is not None
    assert result.n_scenes == 3
    assert result.valid.all()


def test_mosaic_fills_invalid_pixels_from_next_tile() -> None:
    a = (np.array([[0.1, 0.2]]), np.array([[True, False]]))
    b = (np.array([[0.7, 0.8]]), np.array([[True, True]]))
    value, valid = mosaic_same_date([a, b])
    assert valid.all()
    assert value.tolist() == [[0.1, 0.8]]


def test_two_tiles_same_date_do_not_reach_min_scenes(tmp_path: Path) -> None:
    refs = [
        _ref(date(2012, 7, 3), "a"),
        _ref(date(2012, 7, 3), "b"),
        _ref(date(2012, 7, 9), "c"),
    ]
    fakes = Fakes(refs)
    assert _run(fakes, tmp_path) is None
    assert fakes.reads == 0


def test_fewer_than_min_scenes_returns_none_and_is_not_cached(tmp_path: Path) -> None:
    assert MIN_SCENES == 3
    fakes = Fakes(_three_dates()[:2])
    assert _run(fakes, tmp_path) is None
    assert list(tmp_path.iterdir()) == []
    assert _run(Fakes([]), tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_cache_hit_does_not_search_or_read(tmp_path: Path) -> None:
    fakes = Fakes(_three_dates())
    first = _run(fakes, tmp_path)
    reads, searches = fakes.reads, fakes.searches
    second = _run(fakes, tmp_path)
    assert first is not None and second is not None
    assert (fakes.reads, fakes.searches) == (reads, searches)
    assert np.array_equal(first.water, second.water)
    assert np.array_equal(first.valid, second.valid)
    assert second.n_scenes == first.n_scenes


def test_cache_record_stores_all_parameters(tmp_path: Path) -> None:
    _run(Fakes(_three_dates()), tmp_path)
    record = landsat.read_record(tmp_path, _site(), 2012)
    settings = record["settings"]
    for key in (
        "collection",
        "tier",
        "season",
        "max_cloud",
        "qa_bits",
        "threshold_method",
        "threshold_bounds",
        "bands",
        "grid",
        "scale",
        "offset",
        "reflectance_range",
        "min_scenes",
        "min_observations",
        "resampling",
    ):
        assert key in settings
    assert settings["season"] == [7, 8]
    assert record["scene_ids"] == ["S12", "S20", "S3"]
    assert len(record["dates"]) == 3


def test_cache_invalidated_by_season_threshold_cloud_or_qa_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fakes = Fakes(_three_dates())
    _run(fakes, tmp_path)
    base = fakes.searches
    _run(fakes, tmp_path)
    assert fakes.searches == base
    _run(fakes, tmp_path, Season(8, 9))
    assert fakes.searches == base + 1
    monkeypatch.setattr(landsat, "THRESHOLD_BOUNDS", (-0.1, 0.4))
    _run(fakes, tmp_path)
    assert fakes.searches == base + 2
    monkeypatch.setattr(landsat, "MAX_CLOUD", 40.0)
    _run(fakes, tmp_path)
    assert fakes.searches == base + 3
    monkeypatch.setattr(landsat, "QA_BITS", (0, 1, 2, 3))
    _run(fakes, tmp_path)
    assert fakes.searches == base + 4


def test_unreadable_or_foreign_cache_record_is_a_miss(tmp_path: Path) -> None:
    fakes = Fakes(_three_dates())
    _run(fakes, tmp_path)
    (path,) = tmp_path.iterdir()
    path.write_bytes(b"not an npz")
    before = fakes.searches
    assert _run(fakes, tmp_path) is not None
    assert fakes.searches == before + 1


def test_failed_scene_read_raises_and_caches_nothing(tmp_path: Path) -> None:
    class Broken(Fakes):
        def read(self, ref: SceneRef, grid: Grid) -> SceneBands:
            if ref.scene_id == "S12":
                raise ValueError("corrupt scene")
            return super().read(ref, grid)

    with pytest.raises(ValueError):
        _run(Broken(_three_dates()), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_oserror_in_read_is_retried_then_raises(tmp_path: Path) -> None:
    calls: list[float] = []

    class Flaky(Fakes):
        def read(self, ref: SceneRef, grid: Grid) -> SceneBands:
            raise OSError("reset")

    with pytest.raises(OSError):
        season_water_mask(
            _site(),
            2012,
            SEASON,
            search=Flaky(_three_dates()).search,
            read=Flaky(_three_dates()).read,
            sleep=calls.append,
            cache_dir=tmp_path,
        )
    assert len(calls) >= 1
    assert list(tmp_path.iterdir()) == []


def test_failed_search_raises_and_caches_nothing(tmp_path: Path) -> None:
    def bad(bbox: Any, season: Season, year: int) -> list[SceneRef]:
        raise HttpStatusError(404)

    with pytest.raises(HttpStatusError):
        season_water_mask(
            _site(),
            2012,
            SEASON,
            search=bad,
            read=Fakes([]).read,
            sleep=lambda _s: None,
            cache_dir=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []


def test_grid_covers_all_profile_ends_with_buffer_aligned_to_30m() -> None:
    site = _site()
    grid = site_grid(site)
    pts = []
    for p in make_profiles(site):
        pts += [p.origin, profile_end(p)]
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    assert grid.pixel_m == 30.0
    assert grid.x0 <= min(xs) - 500.0
    assert grid.x0 + grid.width * 30.0 >= max(xs) + 500.0
    assert grid.y0 >= max(ys) + 500.0
    assert grid.y0 - grid.height * 30.0 <= min(ys) - 500.0
    assert grid.x0 % 30.0 == 0 and grid.y0 % 30.0 == 0


def _item(
    platform: str,
    *,
    cloud: float | None = 10.0,
    tier: str | None = "T1",
    day: str = "2012-07-03T07:00:00Z",
    green: str | None = None,
    swir: str | None = None,
) -> Any:
    names = {
        "landsat-5": ("TM_B2", "TM_B5"),
        "landsat-7": ("ETM_B2", "ETM_B5"),
        "landsat-8": ("OLI_B3", "OLI_B6"),
        "landsat-9": ("OLI_B3", "OLI_B6"),
    }[platform]
    props: dict[str, Any] = {"datetime": day, "platform": platform}
    if cloud is not None:
        props["eo:cloud_cover"] = cloud
    if tier is not None:
        props["landsat:collection_category"] = tier
    assets = {
        "green": SimpleNamespace(
            href="g", extra_fields={"eo:bands": [{"name": green or names[0]}]}
        ),
        "swir16": SimpleNamespace(
            href="s", extra_fields={"eo:bands": [{"name": swir or names[1]}]}
        ),
        "qa_pixel": SimpleNamespace(href="q", extra_fields={}),
    }
    return SimpleNamespace(id=f"id-{platform}-{day[:10]}", properties=props, assets=assets)


@pytest.mark.parametrize("platform", ["landsat-5", "landsat-7", "landsat-8", "landsat-9"])
def test_band_mapping_per_sensor(platform: str) -> None:
    (ref,) = refs_from_items([_item(platform)])
    assert ref.platform == platform
    assert ref.hrefs == {"green": "g", "swir16": "s", "qa_pixel": "q"}
    assert landsat.BAND_NAMES[platform] == (
        ("SR_B2", "SR_B5") if platform in ("landsat-5", "landsat-7") else ("SR_B3", "SR_B6")
    )


def test_band_name_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="band"):
        refs_from_items([_item("landsat-8", green="OLI_B2")])


def test_select_refs_filters_tier_cloud_season_and_year() -> None:
    items = [
        _item("landsat-8", cloud=50.0),
        _item("landsat-8", cloud=50.1, day="2012-07-04T07:00:00Z"),
        _item("landsat-8", tier="T2", day="2012-07-05T07:00:00Z"),
        _item("landsat-8", tier=None, day="2012-07-06T07:00:00Z"),
        _item("landsat-8", cloud=None, day="2012-07-07T07:00:00Z"),
        _item("landsat-8", day="2012-06-30T07:00:00Z"),
        _item("landsat-8", day="2012-09-01T07:00:00Z"),
        _item("landsat-8", day="2011-07-10T07:00:00Z"),
        _item("landsat-7", day="2012-08-31T07:00:00Z"),
    ]
    chosen = select_refs(refs_from_items(items), SEASON, 2012)
    assert [r.day.isoformat() for r in chosen] == ["2012-07-03", "2012-08-31"]


def test_record_file_is_json_free_of_nan(tmp_path: Path) -> None:
    _run(Fakes(_three_dates()), tmp_path)
    record = landsat.read_record(tmp_path, _site(), 2012)
    assert np.isfinite(record["threshold"])
    json.dumps(record["settings"])
