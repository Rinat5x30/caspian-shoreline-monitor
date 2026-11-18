"""Rule-based choice of study sites, pre-registered in docs/caspian/site-selection.md.

Inputs are only the cached ESA WorldCover 2021 raster (class 80 = the coast) and the
Copernicus DEM GLO-90 (GLO-30 has no Azerbaijan tiles on Planetary Computer).
No imagery water mask, shoreline or sea-level data is read here.
"""

import hashlib
import json
import math
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import shapely
from numpy.typing import NDArray
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.features import rasterize, shapes
from rasterio.transform import Affine, from_origin, rowcol
from rasterio.warp import reproject
from rasterio.windows import Window
from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union

from caspianwatch.cache import retry
from caspianwatch.sites import (
    CRS,
    DEFAULT_SITES,
    PROFILE_SPACING_M,
    TANGENT_HALF_M,
    Profile,
    Site,
    count_crossings,
    make_profiles,
    profile_end,
    site_to_dict,
)

BBox = tuple[float, float, float, float]
Sleep = Callable[[float], None]
FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]
WaterTest = Callable[[FloatArray, FloatArray], BoolArray]

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "raw" / "cache" / "caspian"

DEM_COLLECTION = "cop-dem-glo-90"
WATER, BUILT, TREE, WETLAND = 80, 50, 10, 90
CASPIAN_SEED_LONLAT = (50.3, 39.5)
WORLDCOVER_MIN_LON = 48.0

SLOPE_MAX_DEG = 1.0
BUILT_MAX = 0.30
TREE_MAX = 0.10
WETLAND_DELTAIC_MIN = 0.10

PIXEL_M = 30.0
SEGMENT_RADIUS_M = 7500.0
SNAP_MAX_M = 10_000.0
BAND_WIDTH_M = 1000.0
GRID_HALF_M = 16_500.0
GEOMETRY_GRID_HALF_M = 21_000.0
OPEN_SEA_ERODE_M = 300.0
SMOOTH_WINDOW_M = 1000.0
ORIENT_OFFSET_M = 300.0
STORE_STEP_M = 50.0
OBLIQUITY_HALF_M = 150.0
PROFILE_MARGIN_M = 1500.0
MIN_VALID_PROFILES = 30
GEOMETRY_CAVEATS: dict[str, str] = {
    "kura_delta": (
        "only {above:.0%} of unflattened profile origins lie above -26.5 m in the DEM, so the "
        "landward position of the origin relative to the 2000 shoreline is NOT demonstrated by "
        "the DEM; checked per year on imagery (origin must not be water)"
    ),
    "gizilaghaj": (
        "{wetland:.0%} of the 1 km land band is WorldCover wetland (class 90), shoreline "
        "measurability at 30 m is a risk; {open_dropped} profiles that crossed open sea before "
        "their own station were dropped; of the kept origins {below:.0%} of the non-flattened "
        "ones lie at or below -26.5 m and {flat:.0%} sit on flattened DEM ({flat_value:.2f} m), "
        "so the landward origin is not demonstrated everywhere (checked per year on imagery); "
        "seaward ends in lagoon water (class 80, not open sea): {lagoon}"
    ),
}
INLAND_M: dict[str, float] = {
    "gizilaghaj": 10_000.0,
    "kura_delta": 6_000.0,
    "lankaran_astara": 3_000.0,
}
FLAT_TOL_M = 0.005
LEVEL_2000_BRIEF_M = -26.5
LEVEL_RISE_2000_M = 1.35
DISTANCE_UNCERTAINTY_M = 50.0
MIN_DEM_COVERAGE = 0.99

_TO_UTM = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
_TO_LONLAT = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    name: str
    lat: float
    lon: float
    in_kura_delta: bool
    reason: str


CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        "gizilaghaj",
        "Gizil-Aghaj (Kyzylagach) Bay, north-east",
        39.15,
        49.05,
        False,
        "EGU24-14488 (Ahadov, Kadirov, Fielding): about 218 km2 of exposed land in the "
        "Gizil-Aghaj reserve 2014-2023, largest change in the north-east",
    ),
    Candidate(
        "kura_delta",
        "Kura river mouth",
        39.32,
        49.40,
        True,
        "Gasimov 2021 (Acta Geodyn. Geomater. 18(4), doi 10.13168/AGG.2021.0035): Kura delta "
        "zone, 1.39 m level drop 1996-2019 led to land accumulation",
    ),
    Candidate(
        "shirvan_coast",
        "South-East Shirvan coast (Bandovan - Pirsaat)",
        39.70,
        49.40,
        False,
        "Gasimov 2021: Shirvan zone of the Kura sub-region (Pirsaat and Bandovan capes)",
    ),
    Candidate(
        "absheron_ne",
        "North-eastern Absheron (east of Bilgah)",
        40.53,
        50.10,
        False,
        "spec reconnaissance and Euronews 2026: retreat visible on low-lying Absheron",
    ),
    Candidate(
        "absheron_south",
        "Southern Absheron (Hovsan - Turkan - Zira)",
        40.37,
        50.10,
        False,
        "brief (southern Absheron); Euronews 2026 low-lying Absheron; map reading",
    ),
    Candidate(
        "sumgayit",
        "Sumgayit - Jorat",
        40.60,
        49.62,
        False,
        "brief (Sumgayit - Pirallahi area, north Absheron coast to the west); map reading",
    ),
    Candidate(
        "samur_khachmaz",
        "Northern coast near Samur - Khachmaz (Nabran)",
        41.70,
        48.75,
        False,
        "brief; map reading: low Samur-Devechi lowland coast",
    ),
    Candidate(
        "lankaran_astara",
        "Southern coast Lankaran - Astara",
        38.60,
        48.87,
        False,
        "brief; map reading: Lankaran lowland",
    ),
)


@dataclass(frozen=True)
class BandStats:
    mean_slope_deg: float
    built_share: float
    tree_share: float
    wetland_share: float
    n_pixels: int


@dataclass(frozen=True)
class Verdict:
    passed: bool
    deltaic: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Grid:
    x0: float
    y0: float
    pixel_m: float
    width: int
    height: int

    @property
    def transform(self) -> Affine:
        return from_origin(self.x0, self.y0, self.pixel_m, self.pixel_m)

    def bbox_lonlat(self) -> BBox:
        x1 = self.x0 + self.width * self.pixel_m
        y1 = self.y0 - self.height * self.pixel_m
        xs, ys = _TO_LONLAT.transform([self.x0, x1, self.x0, x1], [self.y0, self.y0, y1, y1])
        return (min(xs), min(ys), max(xs), max(ys))


def slope_deg(dem: FloatArray, pixel_m: float) -> FloatArray:
    gy, gx = np.gradient(np.asarray(dem, dtype=float), pixel_m)
    return np.asarray(np.degrees(np.arctan(np.hypot(gx, gy))), dtype=float)


def mean_slope_deg(dem: FloatArray, pixel_m: float, band: BoolArray) -> float:
    slope = slope_deg(dem, pixel_m)
    values = slope[band & np.isfinite(slope)]
    if values.size == 0:
        raise ValueError("no band pixels with a finite slope")
    return float(values.mean())


def class_shares(classes: NDArray[np.uint8], band: BoolArray) -> dict[int, float]:
    values = classes[band]
    values = values[values != 0]
    if values.size == 0:
        raise ValueError("no band pixels with a WorldCover class")
    keys, counts = np.unique(values, return_counts=True)
    return {int(k): int(n) / int(values.size) for k, n in zip(keys, counts, strict=True)}


def evaluate(stats: BandStats, *, in_kura_delta: bool) -> Verdict:
    reasons: list[str] = []
    if not stats.mean_slope_deg <= SLOPE_MAX_DEG:
        reasons.append(f"mean slope {stats.mean_slope_deg:.3f} deg > {SLOPE_MAX_DEG}")
    if not stats.built_share <= BUILT_MAX:
        reasons.append(f"built-up {stats.built_share:.1%} > {BUILT_MAX:.0%}")
    if not stats.tree_share <= TREE_MAX:
        reasons.append(f"tree cover {stats.tree_share:.1%} > {TREE_MAX:.0%}")
    deltaic = in_kura_delta or stats.wetland_share > WETLAND_DELTAIC_MIN
    return Verdict(not reasons, deltaic, tuple(reasons))


def _polygons(mask: BoolArray, transform: Affine) -> Any:
    geoms = [
        shape(geom)
        for geom, _ in shapes(mask.astype(np.uint8), mask=mask, connectivity=4, transform=transform)
    ]
    return unary_union(geoms)


def band_mask(sea: BoolArray, transform: Affine, width_m: float) -> BoolArray:
    if not sea.any():
        raise ValueError("no Caspian pixels on the grid")
    buffered = _polygons(sea, transform).buffer(width_m)
    near = rasterize([buffered], out_shape=sea.shape, transform=transform, fill=0, dtype="uint8")
    return np.asarray((near == 1) & ~sea, dtype=bool)


def caspian_component(
    classes: NDArray[np.uint8], transform: Affine, seed: tuple[float, float]
) -> BoolArray:
    water = classes == WATER
    row, col = rowcol(transform, *seed)
    inside = 0 <= row < classes.shape[0] and 0 <= col < classes.shape[1]
    if not inside or not water[row, col]:
        raise ValueError(f"seed {seed} is not on class-{WATER} water")
    point = Point(seed)
    for geom, _ in shapes(water.astype(np.uint8), mask=water, connectivity=4, transform=transform):
        poly = shape(geom)
        if poly.covers(point):
            burned = rasterize(
                [poly], out_shape=classes.shape, transform=transform, fill=0, dtype="uint8"
            )
            return np.asarray(burned == 1, dtype=bool)
    raise AssertionError("seed pixel has no polygon")


def required_profile_length(distances: Sequence[float], margin_m: float) -> float:
    if not distances:
        raise ValueError("no profile reaches the Caspian")
    return float(math.ceil((max(distances) + margin_m) / 100.0 - 1e-9) * 100.0)


def _dem_settings(grid: Grid) -> dict[str, Any]:
    return {
        "collection": DEM_COLLECTION,
        "crs": CRS,
        "grid": asdict(grid),
        "resampling": "bilinear",
        "nodata": "nan",
    }


def _dem_cache_path(cache_dir: Path, settings: dict[str, Any]) -> Path:
    digest = hashlib.sha256(json.dumps(settings, sort_keys=True).encode("utf-8")).hexdigest()
    return cache_dir / f"copdem_{digest[:16]}.tif"


def _read_dem_cache(path: Path, settings: dict[str, Any], grid: Grid) -> FloatArray | None:
    try:
        with rasterio.open(path) as ds:
            if ds.tags().get("settings") != json.dumps(settings, sort_keys=True):
                return None
            data = ds.read(1).astype(float)
    except (OSError, rasterio.errors.RasterioError):
        return None
    if data.shape != (grid.height, grid.width):
        return None
    return np.asarray(data, dtype=float)


def _write_dem_cache(
    path: Path, settings: dict[str, Any], grid: Grid, dem: FloatArray, item_ids: list[str]
) -> None:
    from wheatwatch.regions import _atomic_write

    def write(tmp: Path) -> None:
        with rasterio.open(
            tmp,
            "w",
            driver="GTiff",
            height=grid.height,
            width=grid.width,
            count=1,
            dtype="float32",
            crs=CRS,
            transform=grid.transform,
            nodata=float("nan"),
            compress="deflate",
        ) as dst:
            dst.write(dem.astype(np.float32), 1)
            dst.update_tags(settings=json.dumps(settings, sort_keys=True), items=",".join(item_ids))

    _atomic_write(path, write)


def _search_dem(bbox: BBox) -> list[tuple[str, str]]:
    import planetary_computer
    import pystac_client
    from pystac_client.exceptions import APIError

    from wheatwatch.climate import HttpStatusError
    from wheatwatch.regions import STAC_URL

    try:
        catalog = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
        items = catalog.search(collections=[DEM_COLLECTION], bbox=list(bbox)).item_collection()
    except APIError as exc:
        if exc.status_code is None:
            raise
        raise HttpStatusError(exc.status_code) from exc
    return sorted((str(item.id), str(item.assets["data"].href)) for item in items)


def _warp_dem(hrefs: list[str], grid: Grid) -> FloatArray:
    out = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        for href in hrefs:
            with rasterio.open(href) as src:
                tile = np.full(out.shape, np.nan, dtype=np.float32)
                reproject(
                    rasterio.band(src, 1),
                    tile,
                    src_nodata=src.nodata,
                    dst_transform=grid.transform,
                    dst_crs=CRS,
                    dst_nodata=np.nan,
                    resampling=Resampling.bilinear,
                )
            out = np.where(np.isnan(out), tile, out)
    return np.asarray(out, dtype=float)


Search = Callable[[BBox], list[tuple[str, str]]]
Warp = Callable[[list[str], Grid], FloatArray]


def fetch_dem(
    grid: Grid,
    *,
    search: Search = _search_dem,
    warp: Warp = _warp_dem,
    sleep: Sleep = time.sleep,
    cache_dir: Path = CACHE_DIR,
) -> FloatArray:
    settings = _dem_settings(grid)
    path = _dem_cache_path(cache_dir, settings)
    cached = _read_dem_cache(path, settings, grid)
    if cached is not None:
        return cached
    items = retry(partial(search, grid.bbox_lonlat()), sleep)
    if not items:
        raise ValueError(f"no Copernicus DEM tiles for {grid.bbox_lonlat()}")
    dem = np.asarray(retry(partial(warp, [href for _, href in items], grid), sleep), dtype=float)
    if dem.shape != (grid.height, grid.width):
        raise ValueError(f"DEM shape {dem.shape} does not match the grid")
    coverage = float(np.isfinite(dem).mean())
    if coverage < MIN_DEM_COVERAGE:
        raise ValueError(f"DEM coverage {coverage:.3f} < {MIN_DEM_COVERAGE}")
    _write_dem_cache(path, settings, grid, dem, [item_id for item_id, _ in items])
    return dem


@dataclass(frozen=True)
class Layers:
    grid: Grid
    center: tuple[float, float]
    classes: NDArray[np.uint8]
    sea: BoolArray
    disk: BoolArray
    dem: FloatArray


def _load_worldcover() -> tuple[NDArray[np.uint8], Affine]:
    from wheatwatch.regions import cropland_mask_path

    with rasterio.open(cropland_mask_path()) as ds:
        col0 = int((WORLDCOVER_MIN_LON - ds.transform.c) / ds.transform.a)
        window = Window(col0, 0, ds.width - col0, ds.height)
        classes = ds.read(1, window=window)
        transform = ds.window_transform(window)
    return np.asarray(classes, dtype=np.uint8), transform


def _boundary(sea: BoolArray) -> BoolArray:
    edge = np.zeros(sea.shape, dtype=bool)
    edge[1:, :] |= ~sea[:-1, :]
    edge[:-1, :] |= ~sea[1:, :]
    edge[:, 1:] |= ~sea[:, :-1]
    edge[:, :-1] |= ~sea[:, 1:]
    edge[0, :] = edge[-1, :] = False
    edge[:, 0] = edge[:, -1] = False
    return sea & edge


def snap_to_coast(
    lon: float, lat: float, coast: BoolArray, transform: Affine
) -> tuple[float, float, float]:
    """Nearest Caspian boundary pixel to (lon, lat): UTM x, y and the snap distance."""
    rows, cols = np.nonzero(coast)
    xs = transform.c + (cols + 0.5) * transform.a
    ys = transform.f + (rows + 0.5) * transform.e
    near = (np.abs(xs - lon) < 0.2) & (np.abs(ys - lat) < 0.2)
    if not near.any():
        raise ValueError(f"no Caspian boundary near {lat}, {lon}")
    ux, uy = _TO_UTM.transform(xs[near], ys[near])
    cx, cy = _TO_UTM.transform(lon, lat)
    dist = np.hypot(np.asarray(ux) - cx, np.asarray(uy) - cy)
    k = int(np.argmin(dist))
    return float(np.asarray(ux)[k]), float(np.asarray(uy)[k]), float(dist[k])


def _local(src: NDArray[np.uint8], src_transform: Affine, grid: Grid) -> NDArray[np.uint8]:
    out = np.zeros((grid.height, grid.width), dtype=np.uint8)
    reproject(
        src,
        out,
        src_transform=src_transform,
        src_crs="EPSG:4326",
        src_nodata=None,
        dst_transform=grid.transform,
        dst_crs=CRS,
        resampling=Resampling.nearest,
    )
    return out


def build_layers(
    center: tuple[float, float],
    classes: NDArray[np.uint8],
    caspian: BoolArray,
    transform: Affine,
    dem_fetch: Callable[[Grid], FloatArray] = fetch_dem,
    half_m: float = GRID_HALF_M,
) -> Layers:
    size = int(round(2 * half_m / PIXEL_M))
    x0 = math.floor((center[0] - half_m) / PIXEL_M) * PIXEL_M
    y0 = math.ceil((center[1] + half_m) / PIXEL_M) * PIXEL_M
    grid = Grid(x0, y0, PIXEL_M, size, size)
    local_classes = _local(classes, transform, grid)
    sea = _local(caspian.astype(np.uint8), transform, grid) == 1
    cols = grid.x0 + (np.arange(size) + 0.5) * PIXEL_M
    rows = grid.y0 - (np.arange(size) + 0.5) * PIXEL_M
    disk = np.hypot(cols[None, :] - center[0], rows[:, None] - center[1]) <= SEGMENT_RADIUS_M
    return Layers(grid, center, local_classes, sea, disk, dem_fetch(grid))


def band_stats(layers: Layers) -> tuple[BandStats, dict[int, float], dict[str, float]]:
    band = band_mask(layers.sea, layers.grid.transform, BAND_WIDTH_M) & layers.disk
    shares = class_shares(layers.classes, band)
    stats = BandStats(
        mean_slope_deg=mean_slope_deg(layers.dem, PIXEL_M, band),
        built_share=shares.get(BUILT, 0.0),
        tree_share=shares.get(TREE, 0.0),
        wetland_share=shares.get(WETLAND, 0.0),
        n_pixels=int(band.sum()),
    )
    return stats, shares, dem_diagnostics(layers.dem, band)


def dem_diagnostics(dem: FloatArray, band: BoolArray) -> dict[str, float]:
    """Share of band pixels at the DEM's flattened-water value (the mode) and slope without them."""
    finite = band & np.isfinite(dem)
    values, counts = np.unique(np.round(dem[finite], 2), return_counts=True)
    flat_value = float(values[np.argmax(counts)])
    flat = np.abs(dem - flat_value) < FLAT_TOL_M
    slope = slope_deg(dem, PIXEL_M)
    rest = finite & ~flat & np.isfinite(slope)
    return {
        "flat_value_m": flat_value,
        "flattened_share": float((finite & flat).sum() / finite.sum()),
        "slope_without_flattened_deg": float(slope[rest].mean()) if rest.any() else math.nan,
    }


def _sample_mask(grid: Grid, mask: BoolArray, xs: FloatArray, ys: FloatArray) -> BoolArray:
    cols = np.floor((xs - grid.x0) / grid.pixel_m).astype(int)
    rows = np.floor((grid.y0 - ys) / grid.pixel_m).astype(int)
    ok = (cols >= 0) & (cols < grid.width) & (rows >= 0) & (rows < grid.height)
    out = np.zeros(np.shape(xs), dtype=bool)
    out[ok] = mask[rows[ok], cols[ok]]
    return out


def sample_layers(layers: Layers, xs: FloatArray, ys: FloatArray) -> tuple[FloatArray, BoolArray]:
    """DEM and Caspian flag at UTM points; outside the grid: NaN and False."""
    g = layers.grid
    cols = np.floor((xs - g.x0) / g.pixel_m).astype(int)
    rows = np.floor((g.y0 - ys) / g.pixel_m).astype(int)
    ok = (cols >= 0) & (cols < g.width) & (rows >= 0) & (rows < g.height)
    dem = np.full(np.shape(xs), np.nan)
    dem[ok] = layers.dem[rows[ok], cols[ok]]
    return dem, _sample_mask(g, layers.sea, xs, ys)


def _cross(mask: BoolArray, erode: bool) -> BoolArray:
    padded = np.pad(mask, 1, mode="edge")
    centre = padded[1:-1, 1:-1]
    neighbours = (padded[:-2, 1:-1], padded[2:, 1:-1], padded[1:-1, :-2], padded[1:-1, 2:])
    out = centre.copy()
    for n in neighbours:
        out = out & n if erode else out | n
    return np.asarray(out, dtype=bool)


def open_sea(sea: BoolArray, erode_px: int) -> BoolArray:
    """Morphological opening of the Caspian mask, keeping only the largest open-water body."""
    eroded = sea.copy()
    for _ in range(erode_px):
        eroded = _cross(eroded, erode=True)
    if not eroded.any():
        raise ValueError("no open water left after erosion")
    identity = Affine.identity()
    polys = [shape(g) for g, _ in shapes(eroded.astype(np.uint8), mask=eroded, connectivity=4)]
    largest = max(polys, key=lambda poly: poly.area)
    core = rasterize([largest], out_shape=sea.shape, transform=identity, fill=0, dtype="uint8")
    grown = core == 1
    for _ in range(erode_px):
        grown = _cross(grown, erode=False) & sea
    return np.asarray(grown, dtype=bool)


def coast_line(opened: BoolArray, transform: Affine, center: tuple[float, float]) -> LineString:
    """Longest piece of the open-water boundary inside the segment disk."""
    disk = Point(center).buffer(SEGMENT_RADIUS_M)
    pieces = shapely.line_merge(_polygons(opened, transform).boundary.intersection(disk))
    lines = list(getattr(pieces, "geoms", [pieces]))
    lines = [line for line in lines if isinstance(line, LineString) and line.length > 0]
    if not lines:
        raise ValueError("no coast inside the segment disk")
    return max(lines, key=lambda line: line.length)


def _resample(line: LineString, step_m: float) -> FloatArray:
    n = max(2, int(math.ceil(line.length / step_m)) + 1)
    pts = [line.interpolate(d) for d in np.linspace(0.0, line.length, n)]
    return np.array([(p.x, p.y) for p in pts])


def smooth_line(
    line: LineString, window_m: float = SMOOTH_WINDOW_M, step_m: float = PIXEL_M
) -> LineString:
    pts = _resample(line, step_m)
    half = max(1, int(round(window_m / step_m / 2)))
    out = np.array([pts[max(0, i - half) : i + half + 1].mean(axis=0) for i in range(len(pts))])
    return LineString(out)


def orient_sea_left(
    line: LineString, is_sea: WaterTest, offset_m: float = ORIENT_OFFSET_M
) -> LineString:
    site = Site("orient", "orient", line, (1.0, 0.0), False, offset_m * 2, offset_m)
    profiles = make_profiles(site, length_m=offset_m * 2)
    left = np.array([profile_end(p) for p in profiles])
    right = np.array([p.origin for p in profiles])
    sea_l, sea_r = is_sea(left[:, 0], left[:, 1]), is_sea(right[:, 0], right[:, 1])
    if int((sea_r & ~sea_l).sum()) > int((sea_l & ~sea_r).sum()):
        return LineString(list(line.coords)[::-1])
    return line


def profile_valid(profiles: list[Profile], is_water: WaterTest) -> list[bool]:
    origins = np.array([p.origin for p in profiles])
    ends = np.array([profile_end(p) for p in profiles])
    on_land = ~is_water(origins[:, 0], origins[:, 1])
    in_sea = is_water(ends[:, 0], ends[:, 1])
    return [bool(a and b) for a, b in zip(on_land, in_sea, strict=True)]


def geometry_ok(n_valid: int) -> bool:
    return n_valid >= MIN_VALID_PROFILES


def obliquity_deg(
    profiles: list[Profile], coast: LineString, half_m: float = TANGENT_HALF_M, *, at_m: float
) -> list[float]:
    """Angle between each profile and the normal of `coast` at the profile's own station."""
    out: list[float] = []
    for p in profiles:
        station = Point(p.origin[0] + p.direction[0] * at_m, p.origin[1] + p.direction[1] * at_m)
        s = coast.project(station)
        a = coast.interpolate(max(0.0, s - half_m))
        b = coast.interpolate(min(coast.length, s + half_m))
        tangent = np.array([b.x - a.x, b.y - a.y]) / math.dist((a.x, a.y), (b.x, b.y))
        dot = abs(float(np.dot(tangent, p.direction)))
        out.append(math.degrees(math.asin(min(1.0, dot))))
    return out


def crosses_open_sea(
    profiles: list[Profile],
    is_open: WaterTest,
    station_m: float,
    step_m: float = PIXEL_M / 2,
    *,
    is_water: WaterTest | None = None,
) -> list[bool]:
    """True if open sea lies between the origin and the station, apart from the sea at the station.

    The water run that reaches the station itself is the coast the profile was cast from
    (the 1 km smoothed station can sit a few pixels seaward of the pixel coast, and the opening
    leaves single non-open water pixels along that edge).
    """
    d = np.append(np.arange(0.0, station_m, step_m), station_m)
    out: list[bool] = []
    for p in profiles:
        xs, ys = p.origin[0] + d * p.direction[0], p.origin[1] + d * p.direction[1]
        flags = is_open(xs, ys)
        edge = flags | is_water(xs, ys) if is_water is not None else flags
        end = len(flags)
        while end > 0 and edge[end - 1]:
            end -= 1
        out.append(bool(flags[:end].any()))
    return out


def _xy(point: Point) -> tuple[float, float]:
    return (point.x, point.y)


@dataclass(frozen=True)
class GeometryResult:
    site: Site
    total: int
    kept: list[dict[str, Any]]
    crossings: int
    obliquity: list[float]
    origin_dem: list[float]
    open_sea_dropped: tuple[int, ...]
    lagoon_ends: tuple[int, ...]


def site_geometry(
    candidate: Candidate,
    layers: Layers,
    inland_m: float,
    deltaic: bool,
    is_water: WaterTest,
) -> GeometryResult:
    opened = open_sea(layers.sea, int(round(OPEN_SEA_ERODE_M / PIXEL_M)))
    raw = coast_line(opened, layers.grid.transform, layers.center)

    def is_open(xs: FloatArray, ys: FloatArray) -> BoolArray:
        return _sample_mask(layers.grid, opened, xs, ys)

    smooth = orient_sea_left(smooth_line(raw), is_open)
    coast = LineString(_resample(smooth, STORE_STEP_M))
    raw_oriented = orient_sea_left(raw, is_open)
    length = required_profile_length([inland_m], PROFILE_MARGIN_M)
    draft = Site(
        candidate.candidate_id, candidate.name, coast, (1.0, 0.0), deltaic, length, inland_m
    )
    profiles = make_profiles(draft)
    ends_ok = profile_valid(profiles, is_water)
    through_sea = crosses_open_sea(profiles, is_open, inland_m, is_water=is_water)
    valid = [a and not b for a, b in zip(ends_ok, through_sea, strict=True)]
    open_sea_dropped = tuple(
        p.index for p, a, b in zip(profiles, ends_ok, through_sea, strict=True) if a and b
    )
    ends = np.array([profile_end(p) for p in profiles])
    lagoon = is_water(ends[:, 0], ends[:, 1]) & ~is_open(ends[:, 0], ends[:, 1])
    lagoon_ends = tuple(p.index for p, flag in zip(profiles, lagoon, strict=True) if flag)
    kept = [p for p, ok in zip(profiles, valid, strict=True) if ok]
    dropped = tuple(p.index for p, ok in zip(profiles, valid, strict=True) if not ok)
    mean_dir = np.mean([p.direction for p in kept or profiles], axis=0)
    seaward = mean_dir / np.linalg.norm(mean_dir)
    site = Site(
        candidate.candidate_id,
        candidate.name,
        coast,
        (float(seaward[0]), float(seaward[1])),
        deltaic,
        length,
        inland_m,
        dropped,
    )
    records = [
        {
            "index": p.index,
            "origin": [p.origin[0], p.origin[1]],
            "end": list(profile_end(p)),
            "direction": [p.direction[0], p.direction[1]],
            "length_m": p.length_m,
            "distance_to_coast_m": round(
                math.dist(p.origin, _xy(coast.interpolate(p.index * PROFILE_SPACING_M))), 6
            ),
        }
        for p in kept
    ]
    origins = np.array([p.origin for p in kept]) if kept else np.zeros((0, 2))
    dem, _ = sample_layers(layers, origins[:, 0], origins[:, 1])
    return GeometryResult(
        site,
        len(profiles),
        records,
        count_crossings(kept),
        obliquity_deg(kept, raw_oriented, OBLIQUITY_HALF_M, at_m=inland_m),
        [float(v) for v in dem],
        open_sea_dropped,
        lagoon_ends,
    )


def _worldcover_water(classes: NDArray[np.uint8], transform: Affine) -> WaterTest:
    def is_water(xs: FloatArray, ys: FloatArray) -> BoolArray:
        lon, lat = _TO_LONLAT.transform(np.asarray(xs), np.asarray(ys))
        rows, cols = rowcol(transform, np.asarray(lon), np.asarray(lat))
        rows, cols = np.asarray(rows), np.asarray(cols)
        ok = (rows >= 0) & (rows < classes.shape[0]) & (cols >= 0) & (cols < classes.shape[1])
        out = np.zeros(rows.shape, dtype=bool)
        out[ok] = classes[rows[ok], cols[ok]] == WATER
        return out

    return is_water


def _stat(values: list[float]) -> dict[str, float]:
    arr = np.array([v for v in values if math.isfinite(v)])
    if arr.size == 0:
        return {"n": 0}
    return {
        "n": int(arr.size),
        "median": round(float(np.median(arr)), 2),
        "p90": round(float(np.percentile(arr, 90)), 2),
        "max": round(float(arr.max()), 2),
    }


def _geometry_row(geo: GeometryResult, flat_value: float, wetland_share: float) -> dict[str, Any]:
    dem = np.array(geo.origin_dem)
    unflat = np.isfinite(dem) & (np.abs(dem - flat_value) >= FLAT_TOL_M)
    n = int(unflat.sum())

    def above(level: float) -> float | None:
        return round(float((dem[unflat] > level).sum() / n), 3) if n else None

    dropped = len(geo.site.dropped_profiles)
    above_brief = above(LEVEL_2000_BRIEF_M)
    kept_ids = {r["index"] for r in geo.kept}
    lagoon = ", ".join(
        f"profile {i} ({'kept' if i in kept_ids else 'dropped'})" for i in geo.lagoon_ends
    )
    template = GEOMETRY_CAVEATS.get(geo.site.site_id, "none")
    caveat = template.format(
        above=above_brief or 0.0,
        below=1.0 - (above_brief or 0.0),
        wetland=wetland_share,
        open_dropped=len(geo.open_sea_dropped),
        flat=float((np.isfinite(dem) & ~unflat).sum() / dem.size) if dem.size else 0.0,
        flat_value=flat_value,
        lagoon=lagoon or "none",
    )
    return {
        "status": "ok" if geometry_ok(len(geo.kept)) else "FAILED GEOMETRY",
        "geometry_caveat": caveat,
        "dropped_share": round(dropped / geo.total, 4) if geo.total else None,
        "inland_m": geo.site.inland_m,
        "profile_length_m": geo.site.profile_length_m,
        "coast_line_length_m": round(geo.site.reference_line.length, 1),
        "profiles_total": geo.total,
        "profiles_kept": len(geo.kept),
        "profiles_dropped": dropped,
        "dropped_open_sea_before_station": list(geo.open_sea_dropped),
        "lagoon_end_indices": list(geo.lagoon_ends),
        "dropped_indices": list(geo.site.dropped_profiles),
        "crossing_pairs": geo.crossings,
        "obliquity_at_station_vs_raw_coast_deg": _stat(geo.obliquity),
        "distance_to_coast_m": _stat([r["distance_to_coast_m"] for r in geo.kept]),
        "origin_dem_not_flattened": n,
        "origin_share_above_level_2000_brief": above_brief,
        "origin_share_above_flat_plus_rise": above(flat_value + LEVEL_RISE_2000_M),
    }


def _row(candidate: Candidate, snap_m: float, center: tuple[float, float]) -> dict[str, Any]:
    lon, lat = _TO_LONLAT.transform(*center)
    return {
        "candidate_id": candidate.candidate_id,
        "name": candidate.name,
        "point_lat_lon": [candidate.lat, candidate.lon],
        "snapped_lat_lon": [round(lat, 5), round(lon, 5)],
        "snap_m": round(snap_m, 1),
        "in_kura_delta": candidate.in_kura_delta,
        "reason": candidate.reason,
    }


def run(run_date: date) -> dict[str, Any]:
    classes, transform = _load_worldcover()
    caspian = caspian_component(classes, transform, CASPIAN_SEED_LONLAT)
    coast = _boundary(caspian)
    is_water = _worldcover_water(classes, transform)
    rows: list[dict[str, Any]] = []
    sites: list[dict[str, Any]] = []
    for candidate in CANDIDATES:
        x, y, snap_m = snap_to_coast(candidate.lon, candidate.lat, coast, transform)
        row = _row(candidate, snap_m, (x, y))
        if snap_m > SNAP_MAX_M:
            rows.append({**row, "passed": False, "reasons": ["no Caspian boundary within 10 km"]})
            continue
        layers = build_layers((x, y), classes, caspian, transform)
        stats, shares, diag = band_stats(layers)
        verdict = evaluate(stats, in_kura_delta=candidate.in_kura_delta)
        row.update(
            {
                **asdict(stats),
                "class_shares": {str(k): round(v, 4) for k, v in sorted(shares.items())},
                "dem_diagnostics": {k: round(v, 4) for k, v in diag.items()},
                "passed": verdict.passed,
                "deltaic": verdict.deltaic,
                "reasons": list(verdict.reasons),
            }
        )
        if verdict.passed:
            inland = INLAND_M[candidate.candidate_id]
            wide = build_layers((x, y), classes, caspian, transform, half_m=GEOMETRY_GRID_HALF_M)
            geo = site_geometry(candidate, wide, inland, verdict.deltaic, is_water)
            geometry = _geometry_row(geo, diag["flat_value_m"], stats.wetland_share)
            row["geometry"] = geometry
            if geometry["status"] == "ok":
                flags = {
                    "dropped_share": geometry["dropped_share"],
                    "profiles_total": geo.total,
                    "flattened_dem_share": round(diag["flattened_share"], 4),
                    "geometry_caveat": geometry["geometry_caveat"],
                }
                sites.append({**site_to_dict(geo.site), **flags, "profiles": geo.kept})
        rows.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "reason"}), flush=True)
    return {
        "crs": CRS,
        "run_date": run_date.isoformat(),
        "rule_doc": "docs/caspian/site-selection.md",
        "sources": {
            "coast": "ESA WorldCover 2021 class 80, 4-connected Caspian component",
            "dem": f"Copernicus DEM GLO-90 ({DEM_COLLECTION}, Planetary Computer)",
        },
        "reference_line": (
            "smoothed 2021 coast of the open-sea-connected class-80 water (opening 300 m, "
            "largest component, 1 km moving average), sea on the left"
        ),
        "distance_uncertainty_m": DISTANCE_UNCERTAINTY_M,
        "sites": sites,
        "candidates": rows,
    }


def main() -> int:
    from caspianwatch.cache import atomic_write
    from caspianwatch.sites import MIN_SITES

    record = run(date.today())
    passed = len(record["sites"])
    if passed < MIN_SITES:
        print(f"BLOCKED: {passed} site(s) pass with usable geometry, need {MIN_SITES}; not written")
        return 1
    atomic_write(DEFAULT_SITES, (json.dumps(record, indent=2) + "\n").encode("utf-8"))
    print(f"{passed} sites written to {DEFAULT_SITES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
