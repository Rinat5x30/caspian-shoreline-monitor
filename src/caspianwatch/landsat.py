"""Seasonal Landsat water composite per site and year (water mask only, no shoreline here).

Landsat 5/7/8/9 Collection 2 Level-2, tier T1, eo:cloud_cover <= MAX_CLOUD, scenes inside the
chosen season months. Per scene: surface reflectance of green and swir1, QA_PIXEL bits masked,
MNDWI on a common 30 m EPSG:32639 grid; same-date tiles are mosaicked; the composite is the
per-pixel median over the valid observations; pixels with fewer than MIN_OBSERVATIONS valid
observations are no data (valid False), neither land nor water. Water = MNDWI > Otsu threshold
clipped to THRESHOLD_BOUNDS. The QA water bit (7) is not used for classification.
"""

import calendar
import io
import json
import logging
import time
import warnings
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from numpy.typing import NDArray
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.warp import reproject

from caspianwatch.cache import atomic_write, retry
from caspianwatch.season import (
    CACHE_DIR,
    CLOUD_PROPERTY,
    COLLECTION,
    SEASON_PATH,
    USABLE_SCENE_MAX_CLOUD,
    BBox,
    Season,
    season_months,
)
from caspianwatch.site_selection import Grid
from caspianwatch.sites import CRS, Site, make_profiles, profile_end

log = logging.getLogger(__name__)

BoolArray = NDArray[np.bool_]
FloatArray = NDArray[np.float64]
Sleep = Callable[[float], None]

TIER = "T1"
TIER_PROPERTY = "landsat:collection_category"
MAX_CLOUD = USABLE_SCENE_MAX_CLOUD
MIN_SCENES: int = 3
MIN_OBSERVATIONS = 2
RESOLUTION_M = 30.0
BBOX_BUFFER_M = 500.0
SCALE = 0.0000275
OFFSET = -0.2
REFLECTANCE_RANGE = (-0.2, 1.6)
QA_BITS: tuple[int, ...] = (0, 1, 2, 3, 4)
THRESHOLD_BOUNDS = (-0.2, 0.4)
THRESHOLD_METHOD = "otsu, 256 bins on [-1, 1], plateau midpoint, threshold clipped to bounds"
OTSU_BINS = 256
BAND_NAMES: dict[str, tuple[str, str]] = {
    "landsat-5": ("SR_B2", "SR_B5"),
    "landsat-7": ("SR_B2", "SR_B5"),
    "landsat-8": ("SR_B3", "SR_B6"),
    "landsat-9": ("SR_B3", "SR_B6"),
}
ASSET_KEYS = ("green", "swir16", "qa_pixel")
CACHE_VERSION = 1


@dataclass(frozen=True)
class WaterMask:
    water: NDArray[np.bool_]
    transform: Affine
    crs: str
    n_scenes: int
    valid: NDArray[np.bool_]
    threshold: float


@dataclass(frozen=True)
class SceneRef:
    scene_id: str
    day: date
    platform: str
    cloud_cover: float | None
    tier: str | None
    hrefs: dict[str, str]


@dataclass(frozen=True)
class SceneBands:
    green_dn: NDArray[np.float64]
    swir_dn: NDArray[np.float64]
    qa: NDArray[np.uint16]


Search = Callable[[BBox, Season, int], list[SceneRef]]
Read = Callable[[SceneRef, Grid], SceneBands]
Layer = tuple[NDArray[np.float64], NDArray[np.bool_]]


def _same_band(stac_name: Any, expected: str) -> bool:
    return str(stac_name).split("_")[-1] == expected.split("_")[-1]


def refs_from_items(items: list[Any]) -> list[SceneRef]:
    refs: list[SceneRef] = []
    for item in items:
        props = item.properties
        platform = str(props.get("platform", ""))
        if platform not in BAND_NAMES:
            continue
        stamp = props.get("datetime") or props.get("start_datetime")
        if stamp is None:
            raise ValueError("STAC item has no date")
        hrefs: dict[str, str] = {}
        for key, expected in zip(ASSET_KEYS, (*BAND_NAMES[platform], None), strict=True):
            if key not in item.assets:
                raise ValueError(f"{item.id}: no asset {key}")
            asset = item.assets[key]
            bands = (getattr(asset, "extra_fields", None) or {}).get("eo:bands")
            if expected is not None and bands and not _same_band(bands[0].get("name"), expected):
                raise ValueError(f"{item.id}: band {bands[0].get('name')} is not {expected}")
            hrefs[key] = str(asset.href)
        raw = props.get(CLOUD_PROPERTY)
        refs.append(
            SceneRef(
                str(item.id),
                date.fromisoformat(str(stamp)[:10]),
                platform,
                None if raw is None else float(raw),
                props.get(TIER_PROPERTY),
                hrefs,
            )
        )
    return sorted(refs, key=lambda r: (r.day, r.scene_id))


def select_refs(refs: list[SceneRef], season: Season, year: int) -> list[SceneRef]:
    first, last = season_months(season)
    return [
        r
        for r in refs
        if r.tier == TIER
        and r.cloud_cover is not None
        and r.cloud_cover <= MAX_CLOUD
        and r.day.year == year
        and first <= r.day.month <= last
    ]


def reflectance(dn: NDArray[np.float64]) -> tuple[FloatArray, BoolArray]:
    refl = np.asarray(dn, dtype=float) * SCALE + OFFSET
    lo, hi = REFLECTANCE_RANGE
    valid = (np.asarray(dn) != 0) & (refl >= lo) & (refl <= hi)
    return refl, valid


def qa_valid(qa: NDArray[np.uint16]) -> BoolArray:
    mask = sum(1 << bit for bit in QA_BITS)
    return np.asarray((np.asarray(qa).astype(np.uint32) & mask) == 0, dtype=bool)


def scene_mndwi(bands: SceneBands) -> Layer:
    green, ok_g = reflectance(bands.green_dn)
    swir, ok_s = reflectance(bands.swir_dn)
    total = green + swir
    valid = ok_g & ok_s & qa_valid(bands.qa) & (total > 0)
    safe = np.where(valid, total, 1.0)
    return np.where(valid, (green - swir) / safe, 0.0), valid


def mosaic_same_date(layers: list[Layer]) -> Layer:
    value, valid = layers[0][0].copy(), layers[0][1].copy()
    for other_value, other_valid in layers[1:]:
        take = ~valid & other_valid
        value[take] = other_value[take]
        valid |= other_valid
    return value, valid


def composite_mndwi(layers: list[Layer]) -> Layer:
    if not layers:
        raise ValueError("no scenes to composite")
    stack = np.stack([np.where(valid, value, np.nan) for value, valid in layers]).astype(np.float32)
    count = np.stack([valid for _, valid in layers]).sum(axis=0)
    enough = count >= MIN_OBSERVATIONS
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        median = np.nanmedian(stack, axis=0).astype(np.float64)
    return np.where(enough, median, np.nan), enough


def otsu_threshold(values: NDArray[np.float64]) -> float:
    data = np.clip(np.asarray(values, dtype=float), -1.0, 1.0)
    if data.size == 0 or not np.isfinite(data).all():
        raise ValueError("Otsu needs finite values")
    hist, edges = np.histogram(data, bins=OTSU_BINS, range=(-1.0, 1.0))
    if np.count_nonzero(hist) < 2:
        raise ValueError("Otsu needs at least two distinct values")
    weight = hist.cumsum().astype(float)
    centers = (edges[:-1] + edges[1:]) / 2
    mass = (hist * centers).cumsum()
    w_back = weight[-1] - weight
    with np.errstate(divide="ignore", invalid="ignore"):
        variance = weight * w_back * (mass / weight - (mass[-1] - mass) / w_back) ** 2
    variance = np.where((weight > 0) & (w_back > 0), variance, 0.0)
    best = np.flatnonzero(variance >= variance.max() * (1 - 1e-9))
    raw = float((edges[best[0] + 1] + edges[best[-1] + 1]) / 2)
    lo, hi = THRESHOLD_BOUNDS
    return float(min(max(raw, lo), hi))


def site_grid(site: Site) -> Grid:
    points: list[tuple[float, float]] = []
    for profile in make_profiles(site):
        points += [profile.origin, profile_end(profile)]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    step = RESOLUTION_M
    x0 = np.floor((min(xs) - BBOX_BUFFER_M) / step) * step
    y0 = np.ceil((max(ys) + BBOX_BUFFER_M) / step) * step
    x1 = np.ceil((max(xs) + BBOX_BUFFER_M) / step) * step
    y1 = np.floor((min(ys) - BBOX_BUFFER_M) / step) * step
    return Grid(float(x0), float(y0), step, round((x1 - x0) / step), round((y0 - y1) / step))


def _settings(season: Season, grid: Grid) -> dict[str, Any]:
    settings: dict[str, Any] = json.loads(
        json.dumps(
            {
                "version": CACHE_VERSION,
                "collection": COLLECTION,
                "tier": TIER,
                "season": list(season_months(season)),
                "max_cloud": MAX_CLOUD,
                "min_scenes": MIN_SCENES,
                "min_observations": MIN_OBSERVATIONS,
                "same_date": "mosaic, first valid tile in scene id order",
                "qa_bits": list(QA_BITS),
                "water_bit_used": False,
                "bands": {k: list(v) for k, v in BAND_NAMES.items()},
                "scale": SCALE,
                "offset": OFFSET,
                "reflectance_range": list(REFLECTANCE_RANGE),
                "index": "MNDWI = (green - swir1) / (green + swir1), valid where sum > 0",
                "composite": "per-pixel median over valid observations",
                "threshold_method": THRESHOLD_METHOD,
                "threshold_bounds": list(THRESHOLD_BOUNDS),
                "resampling": {"reflectance": "bilinear", "qa": "nearest"},
                "grid": {**asdict(grid), "crs": CRS, "buffer_m": BBOX_BUFFER_M},
            }
        )
    )
    return settings


def _path(cache_dir: Path, site: Site, year: int) -> Path:
    return cache_dir / f"landsat_water_{site.site_id}_{year}.npz"


def _load(path: Path) -> tuple[dict[str, Any], BoolArray, BoolArray] | None:
    try:
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(str(data["meta"]))
            water, valid = np.asarray(data["water"]), np.asarray(data["valid"])
    except (OSError, ValueError, KeyError, TypeError, EOFError, zipfile.BadZipFile):
        return None
    if water.dtype != np.bool_ or valid.dtype != np.bool_ or water.shape != valid.shape:
        return None
    return meta, water, valid


def read_record(cache_dir: Path, site: Site, year: int) -> dict[str, Any]:
    loaded = _load(_path(cache_dir, site, year))
    if loaded is None:
        raise FileNotFoundError(f"no cache record for {site.site_id} {year}")
    return loaded[0]


def _read_cache(
    path: Path, settings: dict[str, Any], grid: Grid, site: Site, year: int
) -> WaterMask | None:
    loaded = _load(path)
    if loaded is None:
        return None
    meta, water, valid = loaded
    try:
        threshold = float(meta["threshold"])
        n_scenes = int(meta["n_scenes"])
        same = (
            meta["settings"] == settings
            and meta["site_id"] == site.site_id
            and meta["year"] == year
            and len(meta["dates"]) == n_scenes
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not same or not np.isfinite(threshold) or water.shape != (grid.height, grid.width):
        return None
    return WaterMask(water, grid.transform, CRS, n_scenes, valid, threshold)


def _write_cache(
    path: Path,
    settings: dict[str, Any],
    site: Site,
    year: int,
    mask: WaterMask,
    refs: list[SceneRef],
    dates: list[date],
) -> None:
    meta = {
        "settings": settings,
        "site_id": site.site_id,
        "year": year,
        "threshold": mask.threshold,
        "n_scenes": mask.n_scenes,
        "dates": [d.isoformat() for d in dates],
        "scene_ids": sorted(r.scene_id for r in refs if r.day in set(dates)),
    }
    buffer = io.BytesIO()
    np.savez_compressed(buffer, water=mask.water, valid=mask.valid, meta=json.dumps(meta))
    atomic_write(path, buffer.getvalue())


def _warp(
    href: str, grid: Grid, dtype: Any, resampling: Resampling, src_nodata: int | None, init: int
) -> NDArray[Any]:
    out = np.full((grid.height, grid.width), init, dtype=dtype)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open(href) as src:
            reproject(
                rasterio.band(src, 1),
                out,
                src_nodata=src_nodata,
                dst_transform=grid.transform,
                dst_crs=CRS,
                dst_nodata=src_nodata,
                resampling=resampling,
                init_dest_nodata=False,
            )
    return out


def _read_scene(ref: SceneRef, grid: Grid) -> SceneBands:
    green = _warp(ref.hrefs["green"], grid, np.float32, Resampling.bilinear, 0, 0)
    swir = _warp(ref.hrefs["swir16"], grid, np.float32, Resampling.bilinear, 0, 0)
    qa = _warp(ref.hrefs["qa_pixel"], grid, np.uint16, Resampling.nearest, None, 1)
    return SceneBands(green.astype(float), swir.astype(float), qa)


def _search_refs(bbox: BBox, season: Season, year: int) -> list[SceneRef]:
    import planetary_computer
    import pystac_client
    from pystac_client.exceptions import APIError

    from wheatwatch.climate import HttpStatusError
    from wheatwatch.regions import STAC_URL

    first, last = season_months(season)
    window = f"{year}-{first:02d}-01/{year}-{last:02d}-{calendar.monthrange(year, last)[1]:02d}"
    try:
        catalog = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
        items = catalog.search(
            collections=[COLLECTION], bbox=list(bbox), datetime=window
        ).item_collection()
    except APIError as exc:
        if exc.status_code is None:
            raise
        raise HttpStatusError(exc.status_code) from exc
    return refs_from_items(list(items))


def season_water_mask(
    site: Site,
    year: int,
    season: Season,
    *,
    search: Search = _search_refs,
    read: Read = _read_scene,
    sleep: Sleep = time.sleep,
    cache_dir: Path = CACHE_DIR,
) -> WaterMask | None:
    grid = site_grid(site)
    settings = _settings(season, grid)
    path = _path(cache_dir, site, year)
    cached = _read_cache(path, settings, grid, site, year)
    if cached is not None:
        return cached
    found = retry(partial(search, grid.bbox_lonlat(), season, year), sleep)
    chosen = select_refs(found, season, year)
    by_day: dict[date, list[SceneRef]] = {}
    for ref in sorted(chosen, key=lambda r: (r.day, r.scene_id)):
        by_day.setdefault(ref.day, []).append(ref)
    if len(by_day) < MIN_SCENES:
        return None
    layers: list[Layer] = []
    used: list[date] = []
    for day, tiles in by_day.items():
        tile_layers = [scene_mndwi(retry(partial(read, ref, grid), sleep)) for ref in tiles]
        layer = mosaic_same_date(tile_layers)
        if layer[1].any():
            layers.append(layer)
            used.append(day)
    if len(used) < MIN_SCENES:
        return None
    median, valid = composite_mndwi(layers)
    if not valid.any():
        return None
    threshold = otsu_threshold(median[valid])
    water = valid & (np.where(valid, median, -np.inf) > threshold)
    mask = WaterMask(water, grid.transform, CRS, len(used), valid, threshold)
    _write_cache(path, settings, site, year, mask, chosen, used)
    return mask


def main(argv: list[str] | None = None) -> None:
    import argparse

    from caspianwatch.sites import load_sites

    parser = argparse.ArgumentParser()
    parser.add_argument("--sites", nargs="*")
    parser.add_argument("--years", nargs="*", type=int)
    args = parser.parse_args(argv)
    chosen = json.loads(SEASON_PATH.read_text(encoding="utf-8"))
    season = Season(chosen["chosen"]["start_month"], chosen["chosen"]["end_month"])
    years = args.years or chosen["period"]["years"]
    for site in load_sites():
        if args.sites and site.site_id not in args.sites:
            continue
        for year in years:
            started = time.time()
            mask = season_water_mask(site, year, season)
            row: dict[str, Any] = {"site": site.site_id, "year": year}
            if mask is None:
                row["result"] = None
            else:
                row.update(
                    dates=mask.n_scenes,
                    threshold=round(mask.threshold, 4),
                    valid_fraction=round(float(mask.valid.mean()), 4),
                    water_fraction_of_valid=round(float(mask.water.sum() / mask.valid.sum()), 4),
                )
            row["seconds"] = round(time.time() - started, 1)
            print(json.dumps(row), flush=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
