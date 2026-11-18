"""Sentinel-2 L2A shoreline control (QC) for the Landsat shoreline series.

Same season, grid, composite, Otsu threshold, gates and shoreline rules as the Landsat series;
only the sensor differs. Bands B03 (10 m) and B11 (20 m) are area-averaged onto the Landsat
30 m EPSG:32639 grid of the site; the SCL class is sampled nearest (coarse: a 30 m pixel is
invalid if the nearest SCL pixel is a masked class). Decisions: data/raw/caspian/SOURCES.md,
section "Sentinel-2 QC". No sea-level data are used here.
"""

import calendar
import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from rasterio.enums import Resampling

from caspianwatch import landsat
from caspianwatch.cache import retry
from caspianwatch.landsat import (
    MIN_OBSERVATIONS,
    MIN_SCENES,
    REFLECTANCE_RANGE,
    Layer,
    SceneRef,
    WaterMask,
    composite_mndwi,
    mosaic_same_date,
    otsu_threshold,
    site_grid,
)
from caspianwatch.season import (
    CACHE_DIR,
    CLOUD_PROPERTY,
    SEASON_PATH,
    USABLE_SCENE_MAX_CLOUD,
    BBox,
    Season,
    season_months,
)
from caspianwatch.shoreline import SiteDisplacement, site_displacement_detail
from caspianwatch.site_selection import Grid
from caspianwatch.sites import CRS, Site

log = logging.getLogger(__name__)

Sleep = Callable[[float], None]
BoolArray = NDArray[np.bool_]
FloatArray = NDArray[np.float64]

COLLECTION = "sentinel-2-l2a"
MAX_CLOUD = USABLE_SCENE_MAX_CLOUD
SCALE = 0.0001
OFFSET_NEW = -0.1
OFFSET_FROM_BASELINE = (4, 0)
MASKED_SCL: tuple[int, ...] = (0, 1, 3, 8, 9, 10)
STAC_BANDS = {"green": "B03", "swir16": "B11", "scl": "SCL"}
YEARS = tuple(range(2017, 2026))
TOLERANCE_M = 30.0
MIN_QC_YEARS = 3
CACHE_VERSION = 1


@dataclass(frozen=True)
class S2SceneRef(SceneRef):
    baseline: str
    scale: float
    offset: float


@dataclass(frozen=True)
class S2Bands:
    green_dn: NDArray[np.float64]
    swir_dn: NDArray[np.float64]
    scl: NDArray[np.uint8]


@dataclass(frozen=True)
class QCReport:
    site_id: str
    n_years: int
    median_abs_diff_m: float
    ok: bool


@dataclass(frozen=True)
class QCDiagnostics:
    mean_signed_diff_m: float
    per_year_diff_m: dict[int, float]


Search = Callable[[BBox, Season, int], list[S2SceneRef]]
Read = Callable[[S2SceneRef, Grid], S2Bands]
Build = Callable[[Site, int, Season], WaterMask | None]


def _baseline_offset(baseline: str) -> float:
    major, minor = (int(part) for part in baseline.split("."))
    return OFFSET_NEW if (major, minor) >= OFFSET_FROM_BASELINE else 0.0


def _raster_scale_offset(asset: Any) -> tuple[float, float] | None:
    bands = (getattr(asset, "extra_fields", None) or {}).get("raster:bands")
    if not bands or "scale" not in bands[0] or "offset" not in bands[0]:
        return None
    return float(bands[0]["scale"]), float(bands[0]["offset"])


def refs_from_items(items: list[Any]) -> list[S2SceneRef]:
    refs: list[S2SceneRef] = []
    for item in items:
        props = item.properties
        stamp = props.get("datetime") or props.get("start_datetime")
        if stamp is None:
            raise ValueError("STAC item has no date")
        hrefs: dict[str, str] = {}
        for key, expected in STAC_BANDS.items():
            if expected not in item.assets:
                raise ValueError(f"{item.id}: no asset {expected}")
            asset = item.assets[expected]
            names = (getattr(asset, "extra_fields", None) or {}).get("eo:bands")
            if names and names[0].get("name") != expected:
                raise ValueError(f"{item.id}: band {names[0].get('name')} is not {expected}")
            hrefs[key] = str(asset.href)
        stac = {_raster_scale_offset(item.assets[STAC_BANDS[k]]) for k in ("green", "swir16")}
        stac.discard(None)
        if len(stac) > 1:
            raise ValueError(f"{item.id}: B03 and B11 scale/offset differ")
        baseline = props.get("s2:processing_baseline")
        if stac:
            scale, offset = next(iter(stac))  # type: ignore[misc]
        elif baseline is None:
            raise ValueError(f"{item.id}: no raster:bands and no s2:processing_baseline")
        else:
            scale, offset = SCALE, _baseline_offset(str(baseline))
        raw = props.get(CLOUD_PROPERTY)
        refs.append(
            S2SceneRef(
                str(item.id),
                date.fromisoformat(str(stamp)[:10]),
                str(props.get("platform", "")),
                None if raw is None else float(raw),
                "L2A",
                hrefs,
                "" if baseline is None else str(baseline),
                scale,
                offset,
            )
        )
    return sorted(refs, key=lambda r: (r.day, r.scene_id))


def select_refs(refs: Sequence[S2SceneRef], season: Season, year: int) -> list[S2SceneRef]:
    first, last = season_months(season)
    return [
        r
        for r in refs
        if r.cloud_cover is not None
        and r.cloud_cover <= MAX_CLOUD
        and r.day.year == year
        and first <= r.day.month <= last
    ]


def s2_reflectance(
    dn: NDArray[np.float64], scale: float, offset: float
) -> tuple[FloatArray, BoolArray]:
    refl = np.asarray(dn, dtype=float) * scale + offset
    lo, hi = REFLECTANCE_RANGE
    valid = (np.asarray(dn) != 0) & (refl >= lo) & (refl <= hi)
    return refl, valid


def scl_valid(scl: NDArray[np.uint8]) -> BoolArray:
    return np.asarray(~np.isin(np.asarray(scl), MASKED_SCL), dtype=bool)


def scene_mndwi_s2(bands: S2Bands, scale: float, offset: float) -> Layer:
    green, ok_g = s2_reflectance(bands.green_dn, scale, offset)
    swir, ok_s = s2_reflectance(bands.swir_dn, scale, offset)
    total = green + swir
    valid = ok_g & ok_s & scl_valid(bands.scl) & (total > 0)
    safe = np.where(valid, total, 1.0)
    return np.where(valid, (green - swir) / safe, 0.0), valid


def _settings(season: Season, grid: Grid) -> dict[str, Any]:
    settings: dict[str, Any] = json.loads(
        json.dumps(
            {
                "version": CACHE_VERSION,
                "collection": COLLECTION,
                "season": list(season_months(season)),
                "max_cloud": MAX_CLOUD,
                "min_scenes": MIN_SCENES,
                "min_observations": MIN_OBSERVATIONS,
                "same_date": "mosaic, first valid tile in scene id order",
                "masked_scl": list(MASKED_SCL),
                "bands": STAC_BANDS,
                "reflectance": (
                    "stac raster:bands scale/offset if present, else DN*0.0001 + "
                    f"{OFFSET_NEW} for s2:processing_baseline >= {OFFSET_FROM_BASELINE}, "
                    "else DN*0.0001; DN 0 = nodata"
                ),
                "reflectance_range": list(landsat.REFLECTANCE_RANGE),
                "index": "MNDWI = (green - swir1) / (green + swir1), valid where sum > 0",
                "composite": "per-pixel median over valid observations",
                "threshold_method": landsat.THRESHOLD_METHOD,
                "threshold_bounds": list(landsat.THRESHOLD_BOUNDS),
                "resampling": {"reflectance": "average", "scl": "nearest"},
                "mndwi_stored": True,
                "grid": {**asdict(grid), "crs": CRS, "buffer_m": landsat.BBOX_BUFFER_M},
            }
        )
    )
    return settings


def _path(cache_dir: Path, site: Site, year: int) -> Path:
    return cache_dir / f"sentinel_water_{site.site_id}_{year}.npz"


def read_record(cache_dir: Path, site: Site, year: int) -> dict[str, Any]:
    loaded = landsat._load(_path(cache_dir, site, year))
    if loaded is None:
        raise FileNotFoundError(f"no cache record for {site.site_id} {year}")
    return loaded[0]


def _read_scene(ref: S2SceneRef, grid: Grid) -> S2Bands:
    average = Resampling.average
    green = landsat._warp(ref.hrefs["green"], grid, np.float32, average, 0, 0)
    swir = landsat._warp(ref.hrefs["swir16"], grid, np.float32, average, 0, 0)
    scl = landsat._warp(ref.hrefs["scl"], grid, np.uint8, Resampling.nearest, None, 0)
    return S2Bands(green.astype(float), swir.astype(float), scl)


def _search_refs(bbox: BBox, season: Season, year: int) -> list[S2SceneRef]:
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


def season_water_mask_s2(
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
    cached = landsat._read_cache(path, settings, grid, site, year)
    if cached is not None:
        return cached
    found = retry(partial(search, grid.bbox_lonlat(), season, year), sleep)
    chosen = select_refs(found, season, year)
    by_day: dict[date, list[S2SceneRef]] = {}
    for ref in sorted(chosen, key=lambda r: (r.day, r.scene_id)):
        by_day.setdefault(ref.day, []).append(ref)
    if len(by_day) < MIN_SCENES:
        return None
    layers: list[Layer] = []
    used: list[date] = []
    fractions: list[float] = []
    for day, tiles in by_day.items():
        tile_layers = [
            scene_mndwi_s2(retry(partial(read, ref, grid), sleep), ref.scale, ref.offset)
            for ref in tiles
        ]
        layer = mosaic_same_date(tile_layers)
        if layer[1].any():
            layers.append(layer)
            used.append(day)
            fractions.append(float(layer[1].mean()))
    if len(used) < MIN_SCENES:
        return None
    median, valid = composite_mndwi(layers)
    if not valid.any():
        return None
    threshold = otsu_threshold(median[valid])
    water = valid & (np.where(valid, median, -np.inf) > threshold)
    mask = WaterMask(
        water,
        grid.transform,
        CRS,
        len(used),
        valid,
        threshold,
        float(valid.mean()),
        tuple(fractions),
        median.astype(np.float32),
    )
    recorded: list[SceneRef] = [*chosen]
    landsat._write_cache(path, settings, site, year, mask, recorded, used)
    return mask


def period_years() -> list[int]:
    data = json.loads(SEASON_PATH.read_text(encoding="utf-8"))
    return [int(y) for y in data["period"]["years"]]


def landsat_detail(
    site: Site,
    season: Season,
    years: Sequence[int] | None = None,
    build: Build = landsat.season_water_mask,
) -> SiteDisplacement:
    masks = {y: build(site, y, season) for y in (years or period_years())}
    return site_displacement_detail(site, masks)


def sentinel_displacement_detail(
    site: Site,
    years: Sequence[int],
    season: Season,
    *,
    complete_profiles: Sequence[int] | None = None,
    build: Build = season_water_mask_s2,
) -> tuple[SiteDisplacement, dict[int, WaterMask | None]]:
    fixed = (
        landsat_detail(site, season).complete_profiles
        if complete_profiles is None
        else tuple(complete_profiles)
    )
    masks = {y: build(site, y, season) for y in years}
    return site_displacement_detail(site, masks, fixed_profiles=fixed), masks


def sentinel_displacement_series(
    site: Site,
    years: Sequence[int],
    season: Season,
    *,
    complete_profiles: Sequence[int] | None = None,
    build: Build = season_water_mask_s2,
) -> pd.Series:
    detail, _ = sentinel_displacement_detail(
        site, years, season, complete_profiles=complete_profiles, build=build
    )
    return detail.series


def _differences(landsat_series: pd.Series, s2: pd.Series) -> pd.Series:
    both = pd.concat([landsat_series.rename("l"), s2.rename("s")], axis=1, join="inner")
    both = both[np.isfinite(both["l"]) & np.isfinite(both["s"])].sort_index()
    return pd.Series(both["s"] - both["l"], dtype=float)


def qc_against_landsat(
    landsat: pd.Series, s2: pd.Series, site_id: str, tolerance_m: float = TOLERANCE_M
) -> QCReport:
    diff = _differences(landsat, s2)
    if len(diff) < MIN_QC_YEARS:
        raise ValueError(f"{site_id}: {len(diff)} common years, need {MIN_QC_YEARS}")
    median = float(diff.abs().median())
    return QCReport(site_id, len(diff), median, median <= tolerance_m)


def qc_diagnostics(landsat: pd.Series, s2: pd.Series) -> QCDiagnostics:
    diff = _differences(landsat, s2)
    return QCDiagnostics(
        float(diff.mean()) if len(diff) else float("nan"),
        {int(str(y)): float(v) for y, v in diff.items()},
    )


def _json_float(value: float) -> float | None:
    return None if not np.isfinite(value) else round(float(value), 3)


def main(argv: list[str] | None = None) -> None:
    import argparse

    from caspianwatch.sites import load_sites

    parser = argparse.ArgumentParser()
    parser.add_argument("--sites", nargs="*")
    parser.add_argument("--years", nargs="*", type=int)
    args = parser.parse_args(argv)
    chosen = json.loads(SEASON_PATH.read_text(encoding="utf-8"))
    season = Season(chosen["chosen"]["start_month"], chosen["chosen"]["end_month"])
    years = args.years or list(YEARS)
    for site in load_sites():
        if args.sites and site.site_id not in args.sites:
            continue
        land = landsat_detail(site, season)
        started = time.time()

        def timed(s: Site, year: int, sn: Season) -> WaterMask | None:
            begin = time.time()
            mask = season_water_mask_s2(s, year, sn)
            row: dict[str, Any] = {"site": s.site_id, "year": year}
            if mask is None:
                row["result"] = None
            else:
                row.update(
                    dates=mask.n_scenes,
                    threshold=round(mask.threshold, 4),
                    composite_valid_fraction=round(mask.composite_valid_fraction, 4),
                    date_valid_fractions=[round(f, 3) for f in mask.date_valid_fractions],
                )
            row["seconds"] = round(time.time() - begin, 1)
            print(json.dumps({"mask": row}), flush=True)
            return mask

        detail, _ = sentinel_displacement_detail(
            site, years, season, complete_profiles=land.complete_profiles, build=timed
        )
        record: dict[str, Any] = {
            "site": site.site_id,
            "complete_profiles": len(land.complete_profiles),
            "landsat": {int(str(y)): _json_float(v) for y, v in land.series.items()},
            "s2": {int(str(y)): _json_float(v) for y, v in detail.series.items()},
            "seconds": round(time.time() - started, 1),
        }
        try:
            report = qc_against_landsat(land.series, detail.series, site.site_id)
            diag = qc_diagnostics(land.series, detail.series)
            record["qc"] = asdict(report)
            record["diagnostics"] = {
                "mean_signed_diff_m": _json_float(diag.mean_signed_diff_m),
                "per_year_diff_m": diag.per_year_diff_m,
            }
        except ValueError as exc:
            record["qc"] = {"error": str(exc)}
        print(json.dumps({"qc": record}), flush=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
