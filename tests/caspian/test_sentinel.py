import ast
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest
import rasterio
from numpy.typing import NDArray
from rasterio.transform import Affine
from shapely.geometry import LineString

from caspianwatch import sentinel
from caspianwatch.landsat import MIN_SCENES, WaterMask, site_grid
from caspianwatch.season import Season
from caspianwatch.sentinel import (
    QCReport,
    S2Bands,
    S2SceneRef,
    qc_against_landsat,
    qc_diagnostics,
    refs_from_items,
    s2_reflectance,
    scl_valid,
    season_water_mask_s2,
    select_refs,
    sentinel_displacement_series,
)
from caspianwatch.site_selection import Grid
from caspianwatch.sites import Site
from wheatwatch.climate import HttpStatusError

SRC = Path(__file__).resolve().parents[2] / "src" / "caspianwatch"
X0, Y0 = 400_000.0, 4_400_000.0
SEASON = Season(7, 8)


def _site() -> Site:
    line = LineString([(X0, Y0 + 1500.0), (X0, Y0)])
    return Site("t", "T", line, (1.0, 0.0), False, 1500.0, 500.0)


def _dn(refl: NDArray[np.float64] | float) -> NDArray[np.float64]:
    return np.asarray(np.round(np.asarray(refl) * 10000.0 + 1000.0), dtype=float)


def _ref(day: date, sid: str = "S", cloud: float = 10.0, baseline: str = "05.11") -> S2SceneRef:
    return S2SceneRef(
        sid,
        day,
        "Sentinel-2A",
        cloud,
        "L2A",
        {"green": "g", "swir16": "s", "scl": "c"},
        baseline,
        0.0001,
        -0.1,
    )


def _sea_east(grid: Grid) -> NDArray[np.bool_]:
    x = grid.x0 + (np.arange(grid.width) + 0.5) * grid.pixel_m
    return np.broadcast_to(x > X0, (grid.height, grid.width)).copy()


def _bands(grid: Grid, scl: NDArray[np.uint8] | None = None) -> S2Bands:
    water = _sea_east(grid)
    green = _dn(np.where(water, 0.10, 0.20))
    swir = _dn(np.where(water, 0.02, 0.30))
    return S2Bands(green, swir, np.full(green.shape, 4, np.uint8) if scl is None else scl)


class Fakes:
    def __init__(self, refs: list[S2SceneRef], per: dict[str, S2Bands] | None = None) -> None:
        self.refs, self.per = refs, per or {}
        self.searches = 0
        self.reads = 0

    def search(self, bbox: Any, season: Season, year: int) -> list[S2SceneRef]:
        self.searches += 1
        return list(self.refs)

    def read(self, ref: S2SceneRef, grid: Grid) -> S2Bands:
        self.reads += 1
        return self.per.get(ref.scene_id) or _bands(grid)


def _run(fakes: Fakes, tmp_path: Path, season: Season = SEASON, year: int = 2020) -> Any:
    return season_water_mask_s2(
        _site(),
        year,
        season,
        search=fakes.search,
        read=fakes.read,
        sleep=lambda _s: None,
        cache_dir=tmp_path,
    )


def _three() -> list[S2SceneRef]:
    return [_ref(date(2020, 7, d), f"S{d}") for d in (3, 12, 20)]


def _series(values: dict[int, float]) -> pd.Series:
    return pd.Series(values, dtype=float)


def test_qc_ok_when_median_diff_within_tolerance() -> None:
    landsat = _series({2017: 100.0, 2018: 110.0, 2019: 120.0, 2020: 130.0})
    s2 = _series({2017: 120.0, 2018: 140.0, 2019: 100.0, 2020: 400.0})
    report = qc_against_landsat(landsat, s2, "a")
    assert report == QCReport("a", 4, 25.0, True)
    assert qc_against_landsat(landsat, landsat + 30.0, "a").ok


def test_qc_flags_site_over_tolerance() -> None:
    landsat = _series({2017: 100.0, 2018: 110.0, 2019: 120.0})
    report = qc_against_landsat(landsat, landsat + 45.0, "b")
    assert report.median_abs_diff_m == pytest.approx(45.0)
    assert not report.ok
    assert qc_against_landsat(landsat, landsat + 45.0, "b", tolerance_m=50.0).ok


def test_qc_uses_only_common_years() -> None:
    landsat = _series({2016: 0.0, 2017: 100.0, 2018: float("nan"), 2019: 120.0, 2020: 130.0})
    s2 = _series({2017: 110.0, 2018: 500.0, 2019: 130.0, 2020: 140.0, 2021: 9999.0})
    report = qc_against_landsat(landsat, s2, "c")
    assert report.n_years == 3
    assert report.median_abs_diff_m == pytest.approx(10.0)


def test_qc_requires_at_least_three_common_years() -> None:
    landsat = _series({2017: 1.0, 2018: 2.0, 2019: 3.0, 2020: float("nan")})
    s2 = _series({2017: 1.0, 2018: 2.0, 2020: 5.0, 2021: 6.0})
    with pytest.raises(ValueError):
        qc_against_landsat(landsat, s2, "d")


def test_qc_diagnostics_signed_and_per_year() -> None:
    landsat = _series({2017: 100.0, 2018: 110.0, 2019: 120.0, 2020: float("nan")})
    s2 = _series({2017: 90.0, 2018: 130.0, 2019: 120.0, 2020: 1.0})
    diag = qc_diagnostics(landsat, s2)
    assert diag.mean_signed_diff_m == pytest.approx(10.0 / 3)
    assert diag.per_year_diff_m == {2017: -10.0, 2018: 20.0, 2019: 0.0}


def test_scale_offset_and_nodata() -> None:
    dn = np.array([0.0, 1000.0, 11000.0, 17001.0, 1.0])
    refl, valid = s2_reflectance(dn, 0.0001, -0.1)
    assert valid.tolist() == [False, True, True, False, True]
    assert refl[1] == pytest.approx(0.0)
    assert refl[2] == pytest.approx(1.0)
    old, ok = s2_reflectance(np.array([0.0, 1000.0]), 0.0001, 0.0)
    assert old[1] == pytest.approx(0.1) and ok.tolist() == [False, True]


def _item(
    baseline: str | None = "05.11",
    day: str = "2020-07-03T07:30:00Z",
    cloud: float | None = 10.0,
    raster: dict[str, Any] | None = None,
    b03_name: str = "B03",
) -> Any:
    props: dict[str, Any] = {"datetime": day, "platform": "Sentinel-2B"}
    if baseline is not None:
        props["s2:processing_baseline"] = baseline
    if cloud is not None:
        props["eo:cloud_cover"] = cloud
    extra: dict[str, Any] = {"raster:bands": [raster]} if raster else {}

    def asset(name: str, href: str, with_extra: bool = True) -> Any:
        fields: dict[str, Any] = {"eo:bands": [{"name": name}]} if name else {}
        if with_extra:
            fields.update(extra)
        return SimpleNamespace(href=href, extra_fields=fields)

    assets = {
        "B03": asset(b03_name, "g"),
        "B11": asset("B11", "s"),
        "SCL": asset("", "c", with_extra=False),
    }
    return SimpleNamespace(id=f"id-{day[:10]}-{baseline}", properties=props, assets=assets)


@pytest.mark.parametrize(
    ("baseline", "offset"), [("02.12", 0.0), ("03.01", 0.0), ("04.00", -0.1), ("05.11", -0.1)]
)
def test_refs_offset_rule_by_processing_baseline(baseline: str, offset: float) -> None:
    (ref,) = refs_from_items([_item(baseline)])
    assert (ref.scale, ref.offset) == (0.0001, offset)
    assert ref.baseline == baseline
    assert ref.hrefs == {"green": "g", "swir16": "s", "scl": "c"}


def test_refs_prefer_stac_raster_bands_scale_offset() -> None:
    (ref,) = refs_from_items([_item("02.12", raster={"scale": 0.0002, "offset": -0.5})])
    assert (ref.scale, ref.offset) == (0.0002, -0.5)


def test_refs_without_baseline_or_with_wrong_band_raise() -> None:
    with pytest.raises(ValueError, match="baseline"):
        refs_from_items([_item(None)])
    with pytest.raises(ValueError, match="band"):
        refs_from_items([_item(b03_name="B04")])
    (ref,) = refs_from_items([_item(None, raster={"scale": 0.0001, "offset": -0.1})])
    assert ref.offset == -0.1


def test_select_refs_filters_cloud_season_and_year() -> None:
    items = [
        _item(cloud=50.0),
        _item(cloud=50.1, day="2020-07-04T07:30:00Z"),
        _item(cloud=None, day="2020-07-05T07:30:00Z"),
        _item(day="2020-06-30T07:30:00Z"),
        _item(day="2020-09-01T07:30:00Z"),
        _item(day="2019-07-10T07:30:00Z"),
        _item(day="2020-08-31T07:30:00Z"),
    ]
    chosen = select_refs(refs_from_items(items), SEASON, 2020)
    assert [r.day.isoformat() for r in chosen] == ["2020-07-03", "2020-08-31"]


@pytest.mark.parametrize("cls", range(12))
def test_scl_masked_classes(cls: int) -> None:
    scl = np.full((2, 2), cls, dtype=np.uint8)
    assert bool(scl_valid(scl).all()) == (cls not in (0, 1, 3, 8, 9, 10))
    assert sentinel.MASKED_SCL == (0, 1, 3, 8, 9, 10)


def test_scene_mndwi_masks_scl_and_fill_dn() -> None:
    grid = Grid(X0, Y0, 30.0, 2, 2)
    bands = _bands(grid)
    scl = np.array([[4, 9], [3, 6]], dtype=np.uint8)
    green = bands.green_dn.copy()
    green[1, 1] = 0.0
    mndwi, valid = sentinel.scene_mndwi_s2(S2Bands(green, bands.swir_dn, scl), 0.0001, -0.1)
    assert valid.tolist() == [[True, False], [False, False]]
    assert np.isfinite(mndwi).all()


def test_water_mask_matches_truth_and_cache_record(tmp_path: Path) -> None:
    result = _run(Fakes(_three()), tmp_path)
    grid = site_grid(_site())
    assert result is not None and result.valid.all()
    assert np.array_equal(result.water, _sea_east(grid))
    assert result.n_scenes == 3 and result.transform == grid.transform
    record = sentinel.read_record(tmp_path, _site(), 2020)
    settings = record["settings"]
    for key in (
        "collection",
        "season",
        "max_cloud",
        "masked_scl",
        "reflectance",
        "threshold_method",
        "threshold_bounds",
        "resampling",
        "grid",
        "min_scenes",
        "min_observations",
        "bands",
    ):
        assert key in settings
    assert settings["collection"] == "sentinel-2-l2a"
    assert settings["masked_scl"] == [0, 1, 3, 8, 9, 10]
    assert settings["resampling"] == {"reflectance": "average", "scl": "nearest"}
    assert record["dates"] == ["2020-07-03", "2020-07-12", "2020-07-20"]
    json.dumps(settings)
    assert (tmp_path / "sentinel_water_t_2020.npz").exists()


def test_scl_cloud_pixels_masked_in_composite(tmp_path: Path) -> None:
    grid = site_grid(_site())
    scl = np.full((grid.height, grid.width), 4, dtype=np.uint8)
    scl[10:20, 10:20] = 9
    bright = _bands(grid, scl)
    bright.green_dn[10:20, 10:20] = _dn(0.5)
    bright.swir_dn[10:20, 10:20] = _dn(0.5)
    result = _run(Fakes(_three(), {"S3": bright}), tmp_path)
    assert result is not None and result.valid.all()
    assert np.array_equal(result.water, _sea_east(grid))


def test_distinct_dates_and_same_date_tiles_mosaicked(tmp_path: Path) -> None:
    grid = site_grid(_site())
    west = np.zeros((grid.height, grid.width), dtype=bool)
    west[:, : grid.width // 2] = True

    def half(mask: NDArray[np.bool_]) -> S2Bands:
        return _bands(grid, np.where(mask, 0, 4).astype(np.uint8))

    refs = [
        _ref(date(2020, 7, 3), "T-a"),
        _ref(date(2020, 7, 3), "T-b", baseline="04.00"),
        _ref(date(2020, 7, 12), "U"),
        _ref(date(2020, 7, 20), "V"),
    ]
    result = _run(Fakes(refs, {"T-a": half(~west), "T-b": half(west)}), tmp_path)
    assert result is not None and result.n_scenes == 3 and result.valid.all()
    two = [*refs[:2], _ref(date(2020, 7, 9), "c")]
    fakes = Fakes(two)
    assert _run(fakes, tmp_path / "x", year=2021) is None
    assert fakes.reads == 0


def test_fewer_than_min_scenes_returns_none_and_is_not_cached(tmp_path: Path) -> None:
    assert MIN_SCENES == 3
    assert _run(Fakes(_three()[:2]), tmp_path) is None
    assert _run(Fakes([]), tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_cache_hit_and_invalidation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fakes = Fakes(_three())
    first = _run(fakes, tmp_path)
    base = fakes.searches
    second = _run(fakes, tmp_path)
    assert fakes.searches == base
    assert first is not None and second is not None
    assert np.array_equal(first.water, second.water)
    assert first.mndwi is not None and second.mndwi is not None
    assert np.array_equal(first.mndwi, second.mndwi, equal_nan=True)
    _run(fakes, tmp_path, Season(8, 9))
    assert fakes.searches == base + 1
    monkeypatch.setattr(sentinel, "MASKED_SCL", (0, 1, 3, 8, 9))
    _run(fakes, tmp_path)
    assert fakes.searches == base + 2
    monkeypatch.setattr(sentinel, "MAX_CLOUD", 40.0)
    _run(fakes, tmp_path)
    assert fakes.searches == base + 3


def test_unreadable_cache_is_a_miss(tmp_path: Path) -> None:
    fakes = Fakes(_three())
    _run(fakes, tmp_path)
    (path,) = tmp_path.iterdir()
    path.write_bytes(b"junk")
    before = fakes.searches
    assert _run(fakes, tmp_path) is not None
    assert fakes.searches == before + 1


def test_failed_read_raises_and_caches_nothing(tmp_path: Path) -> None:
    class Broken(Fakes):
        def read(self, ref: S2SceneRef, grid: Grid) -> S2Bands:
            if ref.scene_id == "S12":
                raise ValueError("corrupt")
            return super().read(ref, grid)

    with pytest.raises(ValueError):
        _run(Broken(_three()), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_oserror_retried_and_http_404_not(tmp_path: Path) -> None:
    sleeps: list[float] = []

    def flaky(ref: S2SceneRef, grid: Grid) -> S2Bands:
        raise OSError("reset")

    with pytest.raises(OSError):
        season_water_mask_s2(
            _site(),
            2020,
            SEASON,
            search=Fakes(_three()).search,
            read=flaky,
            sleep=sleeps.append,
            cache_dir=tmp_path,
        )
    assert len(sleeps) >= 1

    def bad(bbox: Any, season: Season, year: int) -> list[S2SceneRef]:
        raise HttpStatusError(404)

    sleeps.clear()
    with pytest.raises(HttpStatusError):
        season_water_mask_s2(
            _site(),
            2020,
            SEASON,
            search=bad,
            read=Fakes([]).read,
            sleep=sleeps.append,
            cache_dir=tmp_path,
        )
    assert sleeps == [] and list(tmp_path.iterdir()) == []


def _tif(path: Path, data: NDArray[Any], transform: Affine, nodata: int | None) -> str:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype=data.dtype,
        crs="EPSG:32639",
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)
    return str(path)


def _s2_ref(hrefs: dict[str, str]) -> S2SceneRef:
    return S2SceneRef("x", date(2020, 7, 3), "Sentinel-2A", 1.0, "L2A", hrefs, "05.11", 1e-4, -0.1)


def test_average_resampling_10m_and_20m_to_30m_hand_computed(tmp_path: Path) -> None:
    grid = Grid(X0, Y0, 30.0, 3, 3)
    means = np.array([[2000, 3000, 4000], [5000, 6000, 7000], [8000, 9000, 10000]])
    wobble = np.array([[-3, 0, 3], [0, 0, 0], [3, 0, -3]])
    b03 = (np.kron(means, np.ones((3, 3))) + np.tile(wobble, (3, 3))).astype(np.uint16)
    cols = np.array([3000, 6000, 9000, 12000, 15000], dtype=np.uint16)
    b11 = np.tile(cols, (5, 1))
    scl = np.full((5, 5), 4, dtype=np.uint8)
    t10 = Affine(10.0, 0.0, X0, 0.0, -10.0, Y0)
    t20 = Affine(20.0, 0.0, X0, 0.0, -20.0, Y0)
    ref = _s2_ref(
        {
            "green": _tif(tmp_path / "g.tif", b03, t10, 0),
            "swir16": _tif(tmp_path / "s.tif", b11, t20, 0),
            "scl": _tif(tmp_path / "c.tif", scl, t20, None),
        }
    )
    bands = sentinel._read_scene(ref, grid)
    assert bands.green_dn.shape == (3, 3)
    assert np.allclose(bands.green_dn, means, atol=0.5)
    expected = np.tile([4000.0, 8000.0, 13000.0], (3, 1))
    assert np.allclose(bands.swir_dn, expected, atol=0.5)
    assert bands.scl.dtype == np.uint8 and (bands.scl == 4).all()


def test_read_scene_nodata_not_averaged_and_scl_nearest(tmp_path: Path) -> None:
    grid = Grid(X0, Y0, 30.0, 3, 3)
    b03 = np.full((9, 9), 3000, dtype=np.uint16)
    b03[0:3, 0:3] = 0
    b11 = np.full((5, 5), 3000, dtype=np.uint16)
    scl = np.full((5, 5), 4, dtype=np.uint8)
    scl[0, 0] = 9
    t10 = Affine(10.0, 0.0, X0, 0.0, -10.0, Y0)
    t20 = Affine(20.0, 0.0, X0, 0.0, -20.0, Y0)
    ref = _s2_ref(
        {
            "green": _tif(tmp_path / "g.tif", b03, t10, 0),
            "swir16": _tif(tmp_path / "s.tif", b11, t20, 0),
            "scl": _tif(tmp_path / "c.tif", scl, t20, None),
        }
    )
    bands = sentinel._read_scene(ref, grid)
    assert bands.green_dn[0, 0] == 0
    assert bands.green_dn[1, 1] == pytest.approx(3000.0, abs=0.5)
    assert bands.scl[0, 0] == 9 and bands.scl[2, 2] == 4


def test_real_read_path_end_to_end(tmp_path: Path) -> None:
    grid = site_grid(_site())
    pad = 2
    refs: list[S2SceneRef] = []
    for day in (3, 12, 20):
        folder = tmp_path / f"s{day}"
        folder.mkdir()
        hrefs: dict[str, str] = {}
        for key, name, size, dtype in (
            ("green", "g", 10.0, np.uint16),
            ("swir16", "s", 20.0, np.uint16),
            ("scl", "c", 20.0, np.uint8),
        ):
            n_x = int((grid.width + 2 * pad) * 30.0 / size)
            n_y = int((grid.height + 2 * pad) * 30.0 / size)
            x = grid.x0 - pad * 30.0 + (np.arange(n_x) + 0.5) * size
            sea = np.broadcast_to(x > X0, (n_y, n_x))
            if key == "green":
                arr = _dn(np.where(sea, 0.10, 0.20))
            elif key == "swir16":
                arr = _dn(np.where(sea, 0.02, 0.30))
            else:
                arr = np.full((n_y, n_x), 4.0)
            transform = Affine(size, 0.0, grid.x0 - pad * 30.0, 0.0, -size, grid.y0 + pad * 30.0)
            nodata = None if key == "scl" else 0
            hrefs[key] = _tif(folder / f"{name}.tif", arr.astype(dtype), transform, nodata)
        refs.append(
            S2SceneRef(
                f"S{day}", date(2020, 7, day), "Sentinel-2A", 5.0, "L2A", hrefs, "05.11", 1e-4, -0.1
            )
        )
    result = season_water_mask_s2(
        _site(),
        2020,
        SEASON,
        search=Fakes(refs).search,
        read=sentinel._read_scene,
        sleep=lambda _s: None,
        cache_dir=tmp_path / "cache",
    )
    assert result is not None and result.n_scenes == 3
    truth = _sea_east(grid)
    edge = int(np.argmax(truth[0]))
    away = np.ones(truth.shape, dtype=bool)
    away[:, abs(np.arange(grid.width) - edge) <= 2] = False
    away[:2, :] = away[-2:, :] = away[:, :2] = away[:, -2:] = False
    assert result.valid[away].all()
    assert (result.water[away] == truth[away]).all()


def test_search_refs_with_fake_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    import planetary_computer
    import pystac_client

    calls: dict[str, Any] = {}

    class FakeSearch:
        def item_collection(self) -> list[Any]:
            return [_item()]

    class Catalog:
        def search(self, **kwargs: Any) -> FakeSearch:
            calls["search"] = kwargs
            return FakeSearch()

    def fake_open(url: str, modifier: Any = None) -> Catalog:
        calls["modifier"] = modifier
        return Catalog()

    monkeypatch.setattr(pystac_client.Client, "open", staticmethod(fake_open))
    refs = sentinel._search_refs((48.0, 38.0, 49.0, 39.0), SEASON, 2020)
    assert calls["search"] == {
        "collections": ["sentinel-2-l2a"],
        "bbox": [48.0, 38.0, 49.0, 39.0],
        "datetime": "2020-07-01/2020-08-31",
    }
    assert calls["modifier"] is planetary_computer.sign_inplace
    assert len(refs) == 1


def test_search_refs_converts_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import pystac_client
    from pystac_client.exceptions import APIError

    def fake_open(url: str, modifier: Any = None) -> Any:
        error = APIError("busy")
        error.status_code = 503
        raise error

    monkeypatch.setattr(pystac_client.Client, "open", staticmethod(fake_open))
    with pytest.raises(HttpStatusError) as info:
        sentinel._search_refs((48.0, 38.0, 49.0, 39.0), SEASON, 2020)
    assert info.value.status == 503


def _mask(grid: Grid, edge_col: int) -> WaterMask:
    water = np.zeros((grid.height, grid.width), dtype=bool)
    water[:, edge_col:] = True
    ok = np.ones_like(water)
    return WaterMask(water, grid.transform, "EPSG:32639", 3, ok, 0.0, 1.0, (0.9, 0.9, 0.9), None)


def test_series_uses_given_complete_profiles_and_none_years_are_nan() -> None:
    site = _site()
    grid = site_grid(site)
    edge = int(np.argmax(_sea_east(grid)[0]))
    built: list[int] = []

    def build(s: Site, year: int, season: Season) -> WaterMask | None:
        built.append(year)
        return None if year == 2018 else _mask(grid, edge)

    series = sentinel_displacement_series(
        site, [2017, 2018, 2019], SEASON, complete_profiles=[0, 1], build=build
    )
    assert built == [2017, 2018, 2019]
    assert list(series.index) == [2017, 2018, 2019]
    assert np.isnan(series[2018]) and np.isfinite(series[2017])
    assert series[2017] == pytest.approx(series[2019])


def test_sentinel_module_does_not_import_level() -> None:
    names: set[str] = set()
    for node in ast.walk(ast.parse((SRC / "sentinel.py").read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    assert not any("level" in n.split(".") for n in names), names
