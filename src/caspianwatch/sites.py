"""Study sites (fixed reference lines) and shore-normal transect profiles, EPSG:32639.

The sites are chosen by caspianwatch.site_selection from WorldCover class 80 and the
Copernicus DEM only; see docs/caspian/site-selection.md.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SITES = ROOT / "data" / "caspian" / "sites.json"
CRS = "EPSG:32639"
# UTM 39N extent of lon 48-51 E, lat 38-42.5 N (the whole Azerbaijan coast with margin).
UTM_BOUNDS = (236_000.0, 4_205_000.0, 500_000.0, 4_710_000.0)
MIN_SITES = 3
_UNIT_TOL = 1e-6


@dataclass(frozen=True)
class Site:
    site_id: str
    name: str
    reference_line: LineString
    seaward: tuple[float, float]
    deltaic: bool
    profile_length_m: float


@dataclass(frozen=True)
class Profile:
    site_id: str
    index: int
    origin: tuple[float, float]
    direction: tuple[float, float]
    length_m: float


def _inside(x: float, y: float) -> bool:
    minx, miny, maxx, maxy = UTM_BOUNDS
    return minx <= x <= maxx and miny <= y <= maxy


def _check(site: Site) -> None:
    if not site.site_id:
        raise ValueError("empty site_id")
    coords = list(site.reference_line.coords)
    if len(coords) < 2 or site.reference_line.length <= 0:
        raise ValueError(f"{site.site_id}: reference_line needs two distinct points")
    if not all(_inside(x, y) for x, y in coords):
        raise ValueError(f"{site.site_id}: reference_line outside UTM 39N Azerbaijan {UTM_BOUNDS}")
    if abs(math.hypot(*site.seaward) - 1.0) > _UNIT_TOL:
        raise ValueError(f"{site.site_id}: seaward must be a unit vector")
    if not site.profile_length_m > 0:
        raise ValueError(f"{site.site_id}: profile_length_m must be positive")


def site_to_dict(site: Site) -> dict[str, Any]:
    return {
        "site_id": site.site_id,
        "name": site.name,
        "reference_line": [[float(x), float(y)] for x, y in site.reference_line.coords],
        "seaward": [float(site.seaward[0]), float(site.seaward[1])],
        "deltaic": site.deltaic,
        "profile_length_m": float(site.profile_length_m),
    }


def site_from_dict(data: dict[str, Any]) -> Site:
    seaward = data["seaward"]
    site = Site(
        site_id=str(data["site_id"]),
        name=str(data["name"]),
        reference_line=LineString([(float(x), float(y)) for x, y in data["reference_line"]]),
        seaward=(float(seaward[0]), float(seaward[1])),
        deltaic=bool(data["deltaic"]),
        profile_length_m=float(data["profile_length_m"]),
    )
    _check(site)
    return site


def load_sites(path: Path = DEFAULT_SITES) -> list[Site]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("crs") != CRS:
        raise ValueError(f"sites file must be in {CRS}")
    sites = [site_from_dict(item) for item in data["sites"]]
    ids = [s.site_id for s in sites]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"duplicate site_id: {', '.join(dup)}")
    if len(sites) < MIN_SITES:
        raise ValueError(f"need at least {MIN_SITES} sites, got {len(sites)}")
    return sites


def make_profiles(
    site: Site, spacing_m: float = 200.0, length_m: float | None = None
) -> list[Profile]:
    length = site.profile_length_m if length_m is None else length_m
    if not spacing_m > 0:
        raise ValueError("spacing_m must be positive")
    if not length > 0:
        raise ValueError("length_m must be positive")
    line = site.reference_line
    count = math.floor(line.length / spacing_m + 1e-9) + 1
    profiles: list[Profile] = []
    for index in range(count):
        point = line.interpolate(index * spacing_m)
        profiles.append(Profile(site.site_id, index, (point.x, point.y), site.seaward, length))
    return profiles
