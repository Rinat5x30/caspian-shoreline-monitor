import json
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
CACHE_DIR = RAW_DIR / "cache"
ALIASES_PATH = RAW_DIR / "region_aliases.csv"
BOUNDARIES_PATH = CACHE_DIR / "gb-aze-adm2.geojson"
CROPLAND_PATH = CACHE_DIR / "worldcover2021_cropland_az_100m.tif"

GB_API_URL = "https://www.geoboundaries.org/api/current/gbOpen/AZE/ADM2/"
WORLDCOVER_COLLECTION = "esa-worldcover"
STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
CROPLAND_CLASS = 40
AZ_BBOX = (44.7, 38.3, 50.7, 42.0)
_STEP = 1 / 1200
EXCLUDED_IDS: dict[str, str] = {
    "lankaran": (
        "ADM2 has two non-adjacent 'Lankaran District' polygons (542 + 608 km2 = 1150 km2) "
        "against 1540 km2 for the district; no polygon assignment is supported by evidence"
    ),
}


def _atomic_write(path: Path, write: Callable[[Path], None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _fetch_boundaries(dest: Path) -> None:
    with urllib.request.urlopen(GB_API_URL, timeout=60) as resp:
        url = json.load(resp)["gjDownloadURL"]
    urllib.request.urlretrieve(url, dest)


def _boundaries_valid(path: Path) -> bool:
    try:
        return len(gpd.read_file(path)) > 0
    except Exception:
        return False


def _download_boundaries(path: Path = BOUNDARIES_PATH) -> Path:
    if not _boundaries_valid(path):
        _atomic_write(path, _fetch_boundaries)
        if not _boundaries_valid(path):
            path.unlink(missing_ok=True)
            raise ValueError("downloaded boundaries file is unreadable or empty")
    return path


def load_regions(
    yields_ids: set[str],
    boundaries: gpd.GeoDataFrame | None = None,
    aliases_path: Path = ALIASES_PATH,
) -> gpd.GeoDataFrame:
    if boundaries is None:
        boundaries = gpd.read_file(_download_boundaries())
    aliases = pd.read_csv(aliases_path, dtype=str)
    if aliases["region_id"].duplicated().any():
        dup = sorted(aliases.loc[aliases["region_id"].duplicated(), "region_id"])
        raise ValueError(f"duplicate region_id in aliases: {', '.join(dup)}")
    mapping = dict(zip(aliases["region_id"], aliases["geometry_name"], strict=True))
    wanted = yields_ids - set(EXCLUDED_IDS)
    unmatched = sorted(i for i in wanted if mapping.get(i) not in set(boundaries["shapeName"]))
    if unmatched:
        raise ValueError(f"no geometry for region ids: {', '.join(unmatched)}")
    names = {i: mapping[i] for i in wanted}
    if len(set(names.values())) != len(names):
        raise ValueError("several region ids share one geometry name")

    by_name = {name: i for i, name in names.items()}
    subset = boundaries[boundaries["shapeName"].isin(by_name)].to_crs("EPSG:4326")
    subset = subset.assign(region_id=subset["shapeName"].map(by_name))
    result = subset.dissolve(by="region_id").reset_index()[["region_id", "geometry"]]
    if set(result["region_id"]) != wanted:
        raise ValueError("loaded regions differ from requested ids")
    minx, miny, maxx, maxy = result.total_bounds
    if minx < AZ_BBOX[0] or miny < AZ_BBOX[1] or maxx > AZ_BBOX[2] or maxy > AZ_BBOX[3]:
        raise ValueError("region geometry outside the Azerbaijan bounding box")
    return gpd.GeoDataFrame(result, geometry="geometry", crs="EPSG:4326")


def build_cropland_mask(
    sources: list[str],
    bounds: tuple[float, float, float, float],
    out_path: Path,
    step: float = _STEP,
) -> None:
    if not sources:
        raise ValueError("no WorldCover tiles to build the cropland mask from")
    minx, miny, maxx, maxy = bounds
    width = round((maxx - minx) / step)
    height = round((maxy - miny) / step)
    mosaic = np.zeros((height, width), dtype="uint8")
    transform = from_origin(minx, maxy, step, step)
    for src_path in sources:
        with rasterio.open(src_path) as src:
            tile = np.zeros((height, width), dtype="uint8")
            reproject(
                rasterio.band(src, 1),
                tile,
                dst_transform=transform,
                dst_crs="EPSG:4326",
                resampling=Resampling.mode,
            )
        mosaic = np.where(tile > 0, tile, mosaic)
    if not (mosaic == CROPLAND_CLASS).any():
        raise ValueError("cropland mask contains no cropland pixels")

    def write(tmp: Path) -> None:
        with rasterio.open(
            tmp,
            "w",
            driver="GTiff",
            height=height,
            width=width,
            count=1,
            dtype="uint8",
            crs="EPSG:4326",
            transform=transform,
            compress="lzw",
            nodata=0,
        ) as dst:
            dst.write(mosaic, 1)

    _atomic_write(out_path, write)


def _worldcover_hrefs() -> list[str]:
    import planetary_computer
    import pystac_client

    catalog = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
    items = catalog.search(
        collections=[WORLDCOVER_COLLECTION],
        bbox=list(AZ_BBOX),
        datetime="2021-01-01/2021-12-31",
    ).item_collection()
    hrefs = [item.assets["map"].href for item in items]
    if not hrefs:
        raise ValueError("STAC search returned no WorldCover tiles")
    return hrefs


def _raster_valid(path: Path) -> bool:
    try:
        with rasterio.open(path) as ds:
            return bool((ds.read(1) == CROPLAND_CLASS).any())
    except Exception:
        return False


def cropland_mask_path() -> Path:
    if not _raster_valid(CROPLAND_PATH):
        build_cropland_mask(_worldcover_hrefs(), AZ_BBOX, CROPLAND_PATH)
    return CROPLAND_PATH
