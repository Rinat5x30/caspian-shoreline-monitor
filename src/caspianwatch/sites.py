"""Study sites and local-normal transect profiles, EPSG:32639.

Site.reference_line is the smoothed 2021 coast (WorldCover class 80, sea-connected part),
oriented so that the sea lies on its left. Each profile starts inland_m landward of the line
along the local normal and runs seaward. Sites come from caspianwatch.site_selection;
see docs/caspian/site-selection.md.
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
PROFILE_SPACING_M = 200.0
TANGENT_HALF_M = 100.0
_UNIT_TOL = 1e-6
_MATCH_TOL_M = 0.01


@dataclass(frozen=True)
class Site:
    site_id: str
    name: str
    reference_line: LineString
    seaward: tuple[float, float]
    deltaic: bool
    profile_length_m: float
    inland_m: float
    dropped_profiles: tuple[int, ...] = ()


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
    if not site.inland_m > 0:
        raise ValueError(f"{site.site_id}: inland_m must be positive")
    if not site.profile_length_m > site.inland_m:
        raise ValueError(f"{site.site_id}: profile_length_m must exceed inland_m")


def site_to_dict(site: Site) -> dict[str, Any]:
    return {
        "site_id": site.site_id,
        "name": site.name,
        "reference_line": [[float(x), float(y)] for x, y in site.reference_line.coords],
        "seaward": [float(site.seaward[0]), float(site.seaward[1])],
        "deltaic": site.deltaic,
        "profile_length_m": float(site.profile_length_m),
        "inland_m": float(site.inland_m),
        "dropped_profiles": list(site.dropped_profiles),
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
        inland_m=float(data["inland_m"]),
        dropped_profiles=tuple(int(i) for i in data.get("dropped_profiles", [])),
    )
    _check(site)
    if "profiles" in data:
        _check_stored(site, data["profiles"])
    return site


def _check_stored(site: Site, stored: list[dict[str, Any]]) -> None:
    made = {p.index: p for p in make_profiles(site)}
    if sorted(int(r["index"]) for r in stored) != sorted(made):
        raise ValueError(f"{site.site_id}: stored profile indices differ from the geometry")
    for record in stored:
        p = made[int(record["index"])]
        if math.dist(p.origin, record["origin"]) > _MATCH_TOL_M or (
            math.dist(p.direction, record["direction"]) > _UNIT_TOL
        ):
            raise ValueError(f"{site.site_id}: stored profile {p.index} differs from the geometry")


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


def _local_normal(line: LineString, s: float) -> tuple[float, float]:
    a = line.interpolate(max(0.0, s - TANGENT_HALF_M))
    b = line.interpolate(min(line.length, s + TANGENT_HALF_M))
    tx, ty = b.x - a.x, b.y - a.y
    norm = math.hypot(tx, ty)
    return (-ty / norm, tx / norm)


def make_profiles(
    site: Site, spacing_m: float = PROFILE_SPACING_M, length_m: float | None = None
) -> list[Profile]:
    """Profiles every spacing_m along the coast line, along the local normal (sea on the left)."""
    length = site.profile_length_m if length_m is None else length_m
    if not spacing_m > 0:
        raise ValueError("spacing_m must be positive")
    if not length > 0:
        raise ValueError("length_m must be positive")
    if site.dropped_profiles and spacing_m != PROFILE_SPACING_M:
        raise ValueError(f"dropped profile indices refer to {PROFILE_SPACING_M:.0f} m spacing")
    line = site.reference_line
    dropped = set(site.dropped_profiles)
    count = math.floor(line.length / spacing_m + 1e-9) + 1
    profiles: list[Profile] = []
    for index in range(count):
        if index in dropped:
            continue
        s = index * spacing_m
        point = line.interpolate(s)
        nx, ny = _local_normal(line, s)
        origin = (point.x - nx * site.inland_m, point.y - ny * site.inland_m)
        profiles.append(Profile(site.site_id, index, origin, (nx, ny), length))
    return profiles


def profile_end(profile: Profile) -> tuple[float, float]:
    ox, oy = profile.origin
    dx, dy = profile.direction
    return (ox + dx * profile.length_m, oy + dy * profile.length_m)


def count_crossings(profiles: list[Profile]) -> int:
    segments = [LineString([p.origin, profile_end(p)]) for p in profiles]
    return sum(1 for i, a in enumerate(segments) for b in segments[i + 1 :] if a.intersects(b))
