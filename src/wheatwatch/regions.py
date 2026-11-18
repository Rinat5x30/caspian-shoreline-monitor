import json
import urllib.request
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


def _download_boundaries(path: Path = BOUNDARIES_PATH) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(GB_API_URL, timeout=60) as resp:
            url = json.load(resp)["gjDownloadURL"]
        urllib.request.urlretrieve(url, path)
    return path


def load_regions(
    yields_ids: set[str],
    boundaries: gpd.GeoDataFrame | None = None,
    aliases_path: Path = ALIASES_PATH,
) -> gpd.GeoDataFrame:
    if boundaries is None:
        boundaries = gpd.read_file(_download_boundaries())
    aliases = pd.read_csv(aliases_path, dtype=str)
    mapping = dict(zip(aliases["region_id"], aliases["geometry_name"], strict=True))
    known = set(boundaries["shapeName"])
    unmatched = sorted(i for i in yields_ids if mapping.get(i) not in known)
    if unmatched:
        raise ValueError(f"no geometry for region ids: {', '.join(unmatched)}")

    names = {mapping[i]: i for i in yields_ids}
    subset = boundaries[boundaries["shapeName"].isin(names)].to_crs("EPSG:4326")
    subset = subset.assign(region_id=subset["shapeName"].map(names))
    result = subset.dissolve(by="region_id").reset_index()[["region_id", "geometry"]]
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
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        out_path,
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


def _worldcover_hrefs() -> list[str]:
    import planetary_computer
    import pystac_client

    catalog = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
    items = catalog.search(
        collections=[WORLDCOVER_COLLECTION],
        bbox=list(AZ_BBOX),
        datetime="2021-01-01/2021-12-31",
    ).item_collection()
    return [item.assets["map"].href for item in items]


def cropland_mask_path() -> Path:
    if not CROPLAND_PATH.exists():
        build_cropland_mask(_worldcover_hrefs(), AZ_BBOX, CROPLAND_PATH)
    return CROPLAND_PATH
