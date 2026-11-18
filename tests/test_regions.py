from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box

from wheatwatch import regions
from wheatwatch.regions import (
    AZ_BBOX,
    EXCLUDED_IDS,
    build_cropland_mask,
    load_regions,
)
from wheatwatch.yields import load_yields


def _boundaries() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"shapeName": ["Alpha District", "Beta District", "Beta District"]},
        geometry=[box(46, 40, 47, 41), box(47, 40, 48, 41), box(48, 40, 49, 41)],
        crs="EPSG:4326",
    )


def _aliases(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "aliases.csv"
    path.write_text("region_id,geometry_name\n" + body, encoding="utf-8")
    return path


def test_load_regions_lists_unmatched_ids(tmp_path: Path) -> None:
    aliases = _aliases(tmp_path, "alpha,Alpha District\nghost,Ghost District\n")
    with pytest.raises(ValueError, match="ghost") as exc:
        load_regions({"alpha", "ghost", "missing"}, _boundaries(), aliases)
    assert "missing" in str(exc.value)
    assert "alpha" not in str(exc.value)


def test_regions_cover_only_azerbaijan(tmp_path: Path) -> None:
    aliases = _aliases(tmp_path, "alpha,Alpha District\nbeta,Beta District\n")
    result = load_regions({"alpha", "beta"}, _boundaries(), aliases)
    minx, miny, maxx, maxy = result.total_bounds
    assert AZ_BBOX[0] <= minx and AZ_BBOX[1] <= miny and maxx <= AZ_BBOX[2] and maxy <= AZ_BBOX[3]
    assert list(result.columns) == ["region_id", "geometry"]


def test_duplicate_geometry_names_are_dissolved(tmp_path: Path) -> None:
    aliases = _aliases(tmp_path, "beta,Beta District\n")
    result = load_regions({"beta"}, _boundaries(), aliases)
    assert len(result) == 1
    assert result.geometry.iloc[0].area == pytest.approx(2.0)


def test_geometry_outside_azerbaijan_raises(tmp_path: Path) -> None:
    far = gpd.GeoDataFrame(
        {"shapeName": ["Alpha District"]},
        geometry=[Polygon([(10, 10), (11, 10), (11, 11)])],
        crs="EPSG:4326",
    )
    with pytest.raises(ValueError, match="outside"):
        load_regions({"alpha"}, far, _aliases(tmp_path, "alpha,Alpha District\n"))


def test_build_cropland_mask_downsamples_with_mode(tmp_path: Path) -> None:
    step = 1 / 1200 / 10
    data = np.full((20, 20), 30, dtype="uint8")
    data[:10, :10] = 40
    data[0, 0] = 30
    src = tmp_path / "tile.tif"
    with rasterio.open(
        src,
        "w",
        driver="GTiff",
        height=20,
        width=20,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(46.0, 41.0, step, step),
    ) as dst:
        dst.write(data, 1)
    out = tmp_path / "out.tif"
    build_cropland_mask([str(src)], (46.0, 41.0 - 20 * step, 46.0 + 20 * step, 41.0), out)
    with rasterio.open(out) as ds:
        arr = ds.read(1)
    assert arr.shape == (2, 2)
    assert arr[0, 0] == 40 and arr[1, 1] == 30


def _tile(path: Path, value: int = 40) -> Path:
    step = 1 / 12000
    data = np.full((40, 40), value, dtype="uint8")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=40,
        width=40,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(46.0, 41.0, step, step),
    ) as dst:
        dst.write(data, 1)
    return path


SMALL_BBOX = (46.0, 41.0 - 4 / 1200, 46.0 + 4 / 1200, 41.0)


def test_duplicate_alias_region_id_raises(tmp_path: Path) -> None:
    aliases = _aliases(tmp_path, "alpha,Alpha District\nalpha,Beta District\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_regions({"alpha"}, _boundaries(), aliases)


def test_two_ids_with_same_geometry_name_raise(tmp_path: Path) -> None:
    aliases = _aliases(tmp_path, "alpha,Alpha District\nalpha2,Alpha District\n")
    with pytest.raises(ValueError, match="share"):
        load_regions({"alpha", "alpha2"}, _boundaries(), aliases)


def test_excluded_ids_are_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(regions, "EXCLUDED_IDS", {"ghost": "no geometry in ADM2"})
    aliases = _aliases(tmp_path, "alpha,Alpha District\n")
    result = load_regions({"alpha", "ghost"}, _boundaries(), aliases)
    assert list(result["region_id"]) == ["alpha"]


def test_aliases_cover_yield_ids_except_excluded() -> None:
    aliases = pd.read_csv(regions.ALIASES_PATH, dtype=str)
    assert aliases["region_id"].is_unique
    yield_ids = set(load_yields(regions.RAW_DIR / "yields.csv")["region_id"])
    assert set(aliases["region_id"]) == yield_ids - set(EXCLUDED_IDS)
    assert set(EXCLUDED_IDS) <= yield_ids


def test_build_cropland_mask_without_sources_raises(tmp_path: Path) -> None:
    out = tmp_path / "out.tif"
    with pytest.raises(ValueError, match="no WorldCover"):
        build_cropland_mask([], SMALL_BBOX, out)
    assert not out.exists()


def test_build_cropland_mask_without_cropland_raises(tmp_path: Path) -> None:
    out = tmp_path / "out.tif"
    with pytest.raises(ValueError, match="no cropland"):
        build_cropland_mask([str(_tile(tmp_path / "t.tif", value=30))], SMALL_BBOX, out)
    assert not out.exists()


def test_build_cropland_mask_partial_failure_leaves_no_file(tmp_path: Path) -> None:
    out = tmp_path / "out.tif"
    good = _tile(tmp_path / "t.tif")
    with pytest.raises(rasterio.errors.RasterioIOError):
        build_cropland_mask([str(good), str(tmp_path / "missing.tif")], SMALL_BBOX, out)
    assert not out.exists()
    assert not list(tmp_path.glob("*.part"))


def test_corrupt_cached_cropland_is_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cached = tmp_path / "cropland.tif"
    cached.write_bytes(b"x")
    tile = _tile(tmp_path / "t.tif")
    monkeypatch.setattr(regions, "CROPLAND_PATH", cached)
    monkeypatch.setattr(regions, "AZ_BBOX", SMALL_BBOX)
    monkeypatch.setattr(regions, "_worldcover_hrefs", lambda: [str(tile)])
    assert regions.cropland_mask_path() == cached
    with rasterio.open(cached) as ds:
        assert (ds.read(1) == 40).any()


def test_valid_cached_cropland_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cached = tmp_path / "cropland.tif"
    build_cropland_mask([str(_tile(tmp_path / "t.tif"))], SMALL_BBOX, cached)

    def fail() -> list[str]:
        raise AssertionError("must not hit the network")

    monkeypatch.setattr(regions, "CROPLAND_PATH", cached)
    monkeypatch.setattr(regions, "_worldcover_hrefs", fail)
    assert regions.cropland_mask_path() == cached


def test_corrupt_cached_boundaries_are_redownloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "gb.geojson"
    path.write_text("{truncated", encoding="utf-8")

    def fetch(dest: Path) -> None:
        _boundaries().to_file(dest, driver="GeoJSON")

    monkeypatch.setattr(regions, "_fetch_boundaries", fetch)
    assert regions._download_boundaries(path) == path
    assert len(gpd.read_file(path)) == 3
    assert not list(tmp_path.glob("*.part"))


def test_failed_boundaries_download_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "gb.geojson"

    def fetch(dest: Path) -> None:
        dest.write_text("{partial", encoding="utf-8")
        raise OSError("connection reset")

    monkeypatch.setattr(regions, "_fetch_boundaries", fetch)
    with pytest.raises(OSError):
        regions._download_boundaries(path)
    assert not path.exists()
    assert not list(tmp_path.glob("*.part"))
