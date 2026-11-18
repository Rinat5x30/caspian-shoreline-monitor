from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box

from wheatwatch import regions
from wheatwatch.regions import AZ_BBOX, build_cropland_mask, load_regions


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


def test_cropland_mask_path_uses_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cached = tmp_path / "cropland.tif"
    cached.write_bytes(b"x")
    monkeypatch.setattr(regions, "CROPLAND_PATH", cached)
    assert regions.cropland_mask_path() == cached
