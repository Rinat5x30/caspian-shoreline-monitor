"""Seasonal peak NDVI over cropland per region from MODIS MOD13Q1 v061 (Terra, 250 m, 16-day).

Data-quality caveat: a 250 m MODIS pixel mixes land covers, and the cropland mask is a 100 m
ESA WorldCover grid aggregated per pixel (a pixel counts as cropland if at least
CROPLAND_FRACTION_MIN of the class-40 mask cells inside it are cropland). Mountain districts may
have few cropland pixels, so their NDVI is noisy or NaN. Raw NDVI levels differ between regions
for reasons unrelated to drought (crop mix, irrigation, mixing); downstream features must use
per-region standardized anomalies (z-scores), not raw NDVI.

Composites are 16-day products dated by their first day; a composite counts for the season if
that date lies in the climate season window (both ends inclusive).
Source: MODIS/NASA LP DAAC via Microsoft Planetary Computer (anonymous signing).
"""

import json
import logging
import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from functools import partial
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.warp import reproject
from rasterio.windows import Window, from_bounds
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from wheatwatch.climate import (
    BACKOFF_BASE_S,
    MAX_ATTEMPTS,
    SEASON_END,
    SEASON_START,
    HttpStatusError,
)
from wheatwatch.regions import AZ_BBOX, CACHE_DIR, CROPLAND_CLASS, STAC_URL, _atomic_write

log = logging.getLogger(__name__)

COLLECTION = "modis-13Q1-061"
PLATFORM_PREFIX = "MOD13Q1."
NDVI_ASSET = "250m_16_days_NDVI"
QUALITY_ASSET = "250m_16_days_pixel_reliability"
QUALITY_KEEP = [0]  # pixel reliability: 0 good; dropped: -1 fill, 1 marginal, 2 snow/ice, 3 cloudy
NDVI_SCALE = 0.0001
NDVI_FILL = -3000
NDVI_VALID_RANGE = (-2000, 10000)
CROPLAND_FRACTION_MIN = 0.5
MIN_PIXELS = 5
SOURCE = "planetary-computer-modis-mod13q1-v061"
_COLUMNS = ["region_id", "year", "ndvi_peak"]
_MASK_MARGIN_DEG = 0.01
_READ_WORKERS = 8
_PIXEL_LOCK = threading.Lock()
_GDAL_OPTIONS = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
    "GDAL_HTTP_MULTIPLEX": "YES",
}


@dataclass(frozen=True)
class Scene:
    day: date
    ndvi_href: str
    quality_href: str
    bbox: tuple[float, float, float, float]


Search = Callable[[int], list[Scene]]
Sleep = Callable[[float], None]


def _window(year: int) -> tuple[str, str]:
    return date(year, *SEASON_START).isoformat(), date(year, *SEASON_END).isoformat()


def _settings(year: int) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "collection": COLLECTION,
        "window": list(_window(year)),
        "ndvi_asset": NDVI_ASSET,
        "quality_asset": QUALITY_ASSET,
        "quality_keep": QUALITY_KEEP,
        "scale": NDVI_SCALE,
        "fill": NDVI_FILL,
        "valid_range": list(NDVI_VALID_RANGE),
        "cropland_class": CROPLAND_CLASS,
        "cropland_fraction_min": CROPLAND_FRACTION_MIN,
        "min_pixels": MIN_PIXELS,
    }


def _retry[T](fn: Callable[[], T], sleep: Sleep) -> T:
    for attempt in range(MAX_ATTEMPTS):
        try:
            return fn()
        except HttpStatusError as exc:
            if not (exc.status == 429 or exc.status >= 500) or attempt == MAX_ATTEMPTS - 1:
                raise
        except OSError:
            if attempt == MAX_ATTEMPTS - 1:
                raise
        sleep(BACKOFF_BASE_S * 2**attempt)
    raise AssertionError("unreachable")


def _search_scenes(year: int) -> list[Scene]:
    import planetary_computer
    import pystac_client
    from pystac_client.exceptions import APIError

    start, end = _window(year)
    try:
        catalog = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
        items = catalog.search(
            collections=[COLLECTION], bbox=list(AZ_BBOX), datetime=f"{start}/{end}"
        ).item_collection()
    except APIError as exc:
        if exc.status_code is None:
            raise
        raise HttpStatusError(exc.status_code) from exc
    scenes: list[Scene] = []
    for item in items:
        if not item.id.startswith(PLATFORM_PREFIX):
            continue
        stamp = item.properties.get("start_datetime") or item.properties.get("datetime")
        if stamp is None or item.bbox is None:
            raise ValueError(f"STAC item {item.id} has no date or bbox")
        scenes.append(
            Scene(
                day=date.fromisoformat(str(stamp)[:10]),
                ndvi_href=item.assets[NDVI_ASSET].href,
                quality_href=item.assets[QUALITY_ASSET].href,
                bbox=(item.bbox[0], item.bbox[1], item.bbox[2], item.bbox[3]),
            )
        )
    return scenes


def _pixel_window(
    bounds: tuple[float, float, float, float], transform: Any, width: int, height: int
) -> Window | None:
    win = from_bounds(*bounds, transform=transform)
    col0 = max(math.floor(win.col_off), 0)
    row0 = max(math.floor(win.row_off), 0)
    col1 = min(math.ceil(win.col_off + win.width), width)
    row1 = min(math.ceil(win.row_off + win.height), height)
    if col1 <= col0 or row1 <= row0:
        return None
    return Window(col0, row0, col1 - col0, row1 - row0)


def _cropland_pixels(
    mask_path: Path, geom_wgs84: BaseGeometry, crs: Any, transform: Any, shape: tuple[int, int]
) -> np.ndarray:
    minx, miny, maxx, maxy = geom_wgs84.buffer(_MASK_MARGIN_DEG).bounds
    fraction = np.zeros(shape, dtype="float32")
    with rasterio.open(mask_path) as mask:
        win = _pixel_window((minx, miny, maxx, maxy), mask.transform, mask.width, mask.height)
        if win is None:
            return np.zeros(shape, dtype=bool)
        cropland = (mask.read(1, window=win) == CROPLAND_CLASS).astype("float32")
        reproject(
            cropland,
            fraction,
            src_transform=mask.window_transform(win),
            src_crs=mask.crs,
            dst_transform=transform,
            dst_crs=crs,
            resampling=Resampling.average,
        )
    result: np.ndarray = fraction >= CROPLAND_FRACTION_MIN - 1e-6
    return result


def _scene_values(
    scene: Scene,
    geom_wgs84: BaseGeometry,
    mask_path: Path,
    pixel_cache: dict[Any, np.ndarray],
) -> np.ndarray:
    with rasterio.open(scene.ndvi_href) as nd, rasterio.open(scene.quality_href) as ql:
        geom = gpd.GeoSeries([geom_wgs84], crs="EPSG:4326").to_crs(nd.crs).iloc[0]
        win = _pixel_window(geom.bounds, nd.transform, nd.width, nd.height)
        if win is None:
            return np.empty(0, dtype="float64")
        raw = nd.read(1, window=win)
        quality = ql.read(1, window=win)
        transform = nd.window_transform(win)
        key = (nd.crs.to_wkt(), tuple(transform), raw.shape)
        with _PIXEL_LOCK:
            if key not in pixel_cache:
                inside = geometry_mask(
                    [mapping(geom)], out_shape=raw.shape, transform=transform, invert=True
                )
                cropland = _cropland_pixels(mask_path, geom_wgs84, nd.crs, transform, raw.shape)
                pixel_cache[key] = inside & cropland
    good = (
        (raw != NDVI_FILL)
        & (raw >= NDVI_VALID_RANGE[0])
        & (raw <= NDVI_VALID_RANGE[1])
        & np.isin(quality, QUALITY_KEEP)
    )
    selected = raw[pixel_cache[key] & good]
    values: np.ndarray = selected.astype("float64") * NDVI_SCALE
    return values


def _region_year_peak(
    geom: BaseGeometry,
    scenes: list[Scene],
    mask_path: Path,
    year: int,
    sleep: Sleep,
) -> float:
    start, end = (date.fromisoformat(d) for d in _window(year))
    minx, miny, maxx, maxy = geom.bounds
    by_day: dict[date, list[Scene]] = {}
    for scene in scenes:
        sx0, sy0, sx1, sy1 = scene.bbox
        if (
            start <= scene.day <= end
            and sx0 <= maxx
            and sx1 >= minx
            and sy0 <= maxy
            and sy1 >= miny
        ):
            by_day.setdefault(scene.day, []).append(scene)
    pixel_cache: dict[Any, np.ndarray] = {}

    def read(scene: Scene) -> np.ndarray:
        with rasterio.Env(**_GDAL_OPTIONS):
            return _retry(partial(_scene_values, scene, geom, mask_path, pixel_cache), sleep)

    flat = [s for day_scenes in by_day.values() for s in day_scenes]
    with ThreadPoolExecutor(max_workers=_READ_WORKERS) as pool:
        read_values = dict(zip(map(id, flat), pool.map(read, flat), strict=True))
    medians: list[float] = []
    for day_scenes in by_day.values():
        values = np.concatenate([read_values[id(s)] for s in day_scenes])
        if values.size >= MIN_PIXELS:
            medians.append(float(np.median(values)))
    return max(medians) if medians else float("nan")


def _cache_path(cache_dir: Path, region_id: str, year: int) -> Path:
    return cache_dir / f"ndvi_{region_id}_{year}.json"


def _read_cache(path: Path, region_id: str, year: int) -> float | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            data["region_id"] != region_id
            or data["year"] != year
            or data["settings"] != json.loads(json.dumps(_settings(year)))
        ):
            return None
        peak = float(data["ndvi_peak"])
        return None if math.isnan(peak) else peak
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(path: Path, region_id: str, year: int, peak: float) -> None:
    record = {
        "region_id": region_id,
        "year": year,
        "ndvi_peak": peak,
        "settings": _settings(year),
    }

    def write(tmp: Path) -> None:
        tmp.write_text(json.dumps(record), encoding="utf-8")

    _atomic_write(path, write)


def season_ndvi(
    regions: gpd.GeoDataFrame,
    years: list[int],
    *,
    search: Search = _search_scenes,
    sleep: Sleep = time.sleep,
    cache_dir: Path = CACHE_DIR,
    mask_path: Path | None = None,
) -> pd.DataFrame:
    rows: list[tuple[str, int, float]] = []
    missing: list[str] = []
    scenes_by_year: dict[int, list[Scene]] = {}
    mask: Path | None = mask_path
    with rasterio.Env(**_GDAL_OPTIONS):
        for region_id, geom in zip(regions["region_id"], regions.geometry, strict=True):
            for year in years:
                path = _cache_path(cache_dir, region_id, year)
                peak = _read_cache(path, region_id, year)
                if peak is None:
                    if year not in scenes_by_year:
                        found = _retry(partial(search, year), sleep)
                        if not found:
                            raise ValueError(f"no MODIS scenes found for {year}")
                        scenes_by_year[year] = found
                    if mask is None:
                        from wheatwatch.regions import cropland_mask_path

                        mask = cropland_mask_path()
                    peak = _region_year_peak(geom, scenes_by_year[year], mask, year, sleep)
                    if math.isnan(peak):
                        missing.append(f"{region_id}/{year}")
                    else:
                        _write_cache(path, region_id, year, peak)
                rows.append((str(region_id), year, peak))
    if missing:
        log.warning(
            "%d region-year(s) NaN (no composite with >= %d valid cropland pixels): %s",
            len(missing),
            MIN_PIXELS,
            ", ".join(missing),
        )
    frame = pd.DataFrame(rows, columns=_COLUMNS)
    return frame.astype({"year": "int64", "ndvi_peak": "float64"})
