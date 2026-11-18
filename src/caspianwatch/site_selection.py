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
from caspianwatch.sites import CRS, DEFAULT_SITES, Site, make_profiles, site_to_dict

BBox = tuple[float, float, float, float]
Sleep = Callable[[float], None]
FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]

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
STATION_SPACING_M = 200.0
RAY_HALF_M = 12_000.0
CONTOUR_LEVEL_M = -24.0
CONTOUR_SUSTAIN_M = 300.0
CONTOUR_MAX_INLAND_M = 8000.0
CONTOUR_MIN_FRACTION = 0.70
FALLBACK_OFFSET_M = 1500.0
SMOOTH_MIN_STATIONS = 5
SMOOTH_MEAN_STATIONS = 11
SIMPLIFY_M = 30.0
PROFILE_MARGIN_M = 2000.0
PROFILE_SEARCH_M = 25_000.0
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


def coast_crossing(t: FloatArray, is_sea: BoolArray) -> float | None:
    steps = np.flatnonzero(~is_sea[:-1] & is_sea[1:])
    if steps.size == 0:
        return None
    mids = (t[steps] + t[steps + 1]) / 2.0
    return float(mids[np.argmin(np.abs(mids))])


def contour_offset(
    t: FloatArray,
    dem: FloatArray,
    t_c: float,
    *,
    level: float = CONTOUR_LEVEL_M,
    sustain_m: float = CONTOUR_SUSTAIN_M,
    max_inland_m: float = CONTOUR_MAX_INLAND_M,
) -> float | None:
    """Most seaward t <= t_c from which dem >= level holds for sustain_m further inland."""
    eps = 1e-6
    above = dem >= level
    for i in range(len(t) - 1, -1, -1):
        if t[i] > t_c + eps:
            continue
        if t[i] < t_c - max_inland_m - eps or t[i] - sustain_m < t[0] - eps:
            return None
        window = (t >= t[i] - sustain_m - eps) & (t <= t[i] + eps)
        if above[window].all():
            return float(t[i])
    return None


def _rolling(values: FloatArray, window: int, reduce: Callable[[FloatArray], float]) -> FloatArray:
    half = window // 2
    out = np.full(values.shape, np.nan)
    for i in range(len(values)):
        if np.isnan(values[i]):
            continue
        chunk = values[max(0, i - half) : i + half + 1]
        out[i] = reduce(chunk[~np.isnan(chunk)])
    return out


def smooth_offsets(
    raw: FloatArray,
    min_window: int = SMOOTH_MIN_STATIONS,
    mean_window: int = SMOOTH_MEAN_STATIONS,
) -> FloatArray:
    landward = _rolling(np.asarray(raw, dtype=float), min_window, lambda c: float(c.min()))
    return _rolling(landward, mean_window, lambda c: float(c.mean()))


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
) -> Layers:
    size = int(round(2 * GRID_HALF_M / PIXEL_M))
    x0 = math.floor((center[0] - GRID_HALF_M) / PIXEL_M) * PIXEL_M
    y0 = math.ceil((center[1] + GRID_HALF_M) / PIXEL_M) * PIXEL_M
    grid = Grid(x0, y0, PIXEL_M, size, size)
    local_classes = _local(classes, transform, grid)
    sea = _local(caspian.astype(np.uint8), transform, grid) == 1
    cols = grid.x0 + (np.arange(size) + 0.5) * PIXEL_M
    rows = grid.y0 - (np.arange(size) + 0.5) * PIXEL_M
    disk = np.hypot(cols[None, :] - center[0], rows[:, None] - center[1]) <= SEGMENT_RADIUS_M
    return Layers(grid, center, local_classes, sea, disk, dem_fetch(grid))


def band_stats(layers: Layers) -> tuple[BandStats, dict[int, float]]:
    band = band_mask(layers.sea, layers.grid.transform, BAND_WIDTH_M) & layers.disk
    shares = class_shares(layers.classes, band)
    stats = BandStats(
        mean_slope_deg=mean_slope_deg(layers.dem, PIXEL_M, band),
        built_share=shares.get(BUILT, 0.0),
        tree_share=shares.get(TREE, 0.0),
        wetland_share=shares.get(WETLAND, 0.0),
        n_pixels=int(band.sum()),
    )
    return stats, shares


def _sample(layers: Layers, xs: FloatArray, ys: FloatArray) -> tuple[FloatArray, BoolArray]:
    g = layers.grid
    cols = np.floor((xs - g.x0) / g.pixel_m).astype(int)
    rows = np.floor((g.y0 - ys) / g.pixel_m).astype(int)
    ok = (cols >= 0) & (cols < g.width) & (rows >= 0) & (rows < g.height)
    dem = np.full(xs.shape, np.nan)
    sea = np.zeros(xs.shape, dtype=bool)
    dem[ok] = layers.dem[rows[ok], cols[ok]]
    sea[ok] = layers.sea[rows[ok], cols[ok]]
    return dem, sea


def coast_axis(layers: Layers) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
    """Coast vertices in the disk, their centroid, the along-coast axis and the seaward normal."""
    g = layers.grid
    rows, cols = np.nonzero(_boundary(layers.sea) & layers.disk)
    pts = np.column_stack(
        [g.x0 + (cols + 0.5) * g.pixel_m, g.y0 - (rows + 0.5) * g.pixel_m]
    ).astype(float)
    centroid = pts.mean(axis=0)
    _, vecs = np.linalg.eigh(np.cov((pts - centroid).T))
    axis = vecs[:, -1]
    normal = np.array([-axis[1], axis[0]])

    def mean_xy(mask: BoolArray) -> FloatArray:
        r, c = np.nonzero(mask)
        return np.array([g.x0 + (c.mean() + 0.5) * g.pixel_m, g.y0 - (r.mean() + 0.5) * g.pixel_m])

    if (
        float(
            np.dot(normal, mean_xy(layers.sea & layers.disk) - mean_xy(~layers.sea & layers.disk))
        )
        < 0
    ):
        normal = -normal
    return pts, centroid, axis, normal


@dataclass(frozen=True)
class ReferenceResult:
    site: Site
    method: str
    stations: int
    stations_with_coast: int
    contour_fraction: float
    profiles: int
    profiles_reaching_sea: int
    max_distance_m: float


def reference_site(candidate: Candidate, layers: Layers, deltaic: bool) -> ReferenceResult:
    pts, centroid, axis, normal = coast_axis(layers)
    along = (pts - centroid) @ axis
    s_values = np.arange(along.min(), along.max() + 1e-9, STATION_SPACING_M)
    t = np.arange(-RAY_HALF_M, RAY_HALF_M + 1e-9, PIXEL_M)
    coast_t = np.full(s_values.shape, np.nan)
    contour_t = np.full(s_values.shape, np.nan)
    for i, s in enumerate(s_values):
        base = centroid + s * axis
        dem, sea = _sample(layers, base[0] + t * normal[0], base[1] + t * normal[1])
        t_c = coast_crossing(t, sea)
        if t_c is None:
            continue
        coast_t[i] = t_c
        found = contour_offset(t, dem, t_c)
        if found is not None:
            contour_t[i] = found
    with_coast = int(np.isfinite(coast_t).sum())
    if with_coast == 0:
        raise ValueError(f"{candidate.candidate_id}: no coast crossing on any station")
    fraction = float(np.isfinite(contour_t).sum() / with_coast)
    if fraction >= CONTOUR_MIN_FRACTION:
        method, raw = "contour", contour_t
    else:
        method, raw = "fallback_offset", coast_t - FALLBACK_OFFSET_M
    offsets = smooth_offsets(raw)
    keep = np.isfinite(offsets)
    line_pts = [
        tuple(centroid + s * axis + o * normal)
        for s, o in zip(s_values[keep], offsets[keep], strict=True)
    ]
    line = LineString(line_pts).simplify(SIMPLIFY_M)
    seaward = (float(normal[0]), float(normal[1]))
    draft = Site(candidate.candidate_id, candidate.name, line, seaward, deltaic, 1.0)
    distances: list[float] = []
    profiles = make_profiles(draft, spacing_m=STATION_SPACING_M)
    d = np.arange(0.0, PROFILE_SEARCH_M + 1e-9, PIXEL_M)
    for p in profiles:
        xs = p.origin[0] + d * p.direction[0]
        ys = p.origin[1] + d * p.direction[1]
        _, sea = _sample(layers, xs, ys)
        hit = np.flatnonzero(sea)
        if hit.size:
            distances.append(float(d[hit[0]]))
    length = required_profile_length(distances, PROFILE_MARGIN_M)
    site = Site(candidate.candidate_id, candidate.name, line, seaward, deltaic, length)
    return ReferenceResult(
        site,
        method,
        len(s_values),
        with_coast,
        fraction,
        len(profiles),
        len(distances),
        max(distances),
    )


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
    rows: list[dict[str, Any]] = []
    sites: list[dict[str, Any]] = []
    for candidate in CANDIDATES:
        x, y, snap_m = snap_to_coast(candidate.lon, candidate.lat, coast, transform)
        row = _row(candidate, snap_m, (x, y))
        if snap_m > SNAP_MAX_M:
            rows.append({**row, "passed": False, "reasons": ["no Caspian boundary within 10 km"]})
            continue
        layers = build_layers((x, y), classes, caspian, transform)
        stats, shares = band_stats(layers)
        verdict = evaluate(stats, in_kura_delta=candidate.in_kura_delta)
        row.update(
            {
                **asdict(stats),
                "class_shares": {str(k): round(v, 4) for k, v in sorted(shares.items())},
                "passed": verdict.passed,
                "deltaic": verdict.deltaic,
                "reasons": list(verdict.reasons),
            }
        )
        if verdict.passed:
            ref = reference_site(candidate, layers, verdict.deltaic)
            row["reference"] = {
                "method": ref.method,
                "stations": ref.stations,
                "stations_with_coast": ref.stations_with_coast,
                "contour_fraction": round(ref.contour_fraction, 3),
                "reference_line_length_m": round(ref.site.reference_line.length, 1),
                "profiles": ref.profiles,
                "profiles_reaching_sea": ref.profiles_reaching_sea,
                "max_distance_to_class80_m": ref.max_distance_m,
                "profile_length_m": ref.site.profile_length_m,
            }
            sites.append(site_to_dict(ref.site))
        rows.append(row)
        print(json.dumps(row), flush=True)
    return {
        "crs": CRS,
        "run_date": run_date.isoformat(),
        "rule_doc": "docs/caspian/site-selection.md",
        "sources": {
            "coast": "ESA WorldCover 2021 class 80, 4-connected Caspian component",
            "dem": f"Copernicus DEM GLO-90 ({DEM_COLLECTION}, Planetary Computer)",
        },
        "sites": sites,
        "candidates": rows,
    }


def main() -> int:
    from caspianwatch.cache import atomic_write
    from caspianwatch.sites import MIN_SITES

    record = run(date.today())
    passed = len(record["sites"])
    if passed < MIN_SITES:
        print(f"BLOCKED: {passed} candidate(s) pass, need {MIN_SITES}; sites.json not written")
        return 1
    atomic_write(DEFAULT_SITES, (json.dumps(record, indent=2) + "\n").encode("utf-8"))
    print(f"{passed} sites written to {DEFAULT_SITES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
