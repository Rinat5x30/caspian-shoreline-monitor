import json
import math
from pathlib import Path
from typing import Any

import pytest
from shapely.geometry import LineString, Point, Polygon, box

from caspianwatch.sites import (
    MIN_SITES,
    Profile,
    Site,
    count_crossings,
    load_sites,
    make_profiles,
    site_to_dict,
)

X0, Y0 = 400_000.0, 4_400_000.0


def _site(
    site_id: str = "a",
    *,
    deltaic: bool = False,
    length: float = 3000.0,
    inland: float = 1000.0,
    x: float = X0,
    dropped: tuple[int, ...] = (),
) -> Site:
    # Coast runs south along x, so the sea (left side) is to the east.
    line = LineString([(x, Y0 + 2000.0), (x, Y0)])
    return Site(site_id, f"Site {site_id}", line, (1.0, 0.0), deltaic, length, inland, dropped)


def _write(tmp_path: Path, sites: list[dict[str, Any]], crs: str = "EPSG:32639") -> Path:
    path = tmp_path / "sites.json"
    path.write_text(json.dumps({"crs": crs, "sites": sites}), encoding="utf-8")
    return path


def _three() -> list[dict[str, Any]]:
    return [
        site_to_dict(_site("a")),
        site_to_dict(_site("b", deltaic=True, x=X0 + 50_000)),
        site_to_dict(_site("c", x=X0 + 90_000)),
    ]


def _end(p: Profile) -> tuple[float, float]:
    return (p.origin[0] + p.direction[0] * p.length_m, p.origin[1] + p.direction[1] * p.length_m)


def test_profiles_are_unit_direction_and_evenly_spaced() -> None:
    profiles = make_profiles(_site(), spacing_m=200.0)
    assert len(profiles) == 11
    assert [p.index for p in profiles] == list(range(11))
    for p in profiles:
        assert math.hypot(*p.direction) == pytest.approx(1.0)
        assert p.site_id == "a"
    gaps = [math.dist(a.origin, b.origin) for a, b in zip(profiles, profiles[1:], strict=False)]
    assert gaps == pytest.approx([200.0] * 10)
    assert profiles[0].origin == pytest.approx((X0 - 1000.0, Y0 + 2000.0))
    assert profiles[-1].origin == pytest.approx((X0 - 1000.0, Y0))


def test_profiles_point_seaward() -> None:
    sea = box(X0, Y0 - 10_000.0, X0 + 50_000.0, Y0 + 10_000.0)
    for p in make_profiles(_site()):
        assert sea.contains(Point(_end(p)))
        assert not sea.intersects(Point(p.origin))
        assert p.direction == pytest.approx((1.0, 0.0))


def test_local_normals_on_45_degree_coast_are_perpendicular() -> None:
    line = LineString([(X0, Y0), (X0 + 3000.0, Y0 + 3000.0)])
    site = Site("d", "Diagonal", line, (-math.sqrt(0.5), math.sqrt(0.5)), False, 2500.0, 1000.0)
    tangent = (math.sqrt(0.5), math.sqrt(0.5))
    for p in make_profiles(site):
        assert p.direction[0] * tangent[0] + p.direction[1] * tangent[1] == pytest.approx(
            0.0, abs=1e-9
        )
        assert p.direction == pytest.approx((-math.sqrt(0.5), math.sqrt(0.5)))
        coast_point = (p.origin[0] + p.direction[0] * 1000.0, p.origin[1] + p.direction[1] * 1000.0)
        assert line.distance(Point(coast_point)) == pytest.approx(0.0, abs=1e-6)


def test_concave_bay_profiles_converge_but_stay_valid() -> None:
    radius = 4000.0
    arc = [
        (X0 + radius * math.cos(math.radians(a)), Y0 + radius * math.sin(math.radians(a)))
        for a in range(90, 271, 5)
    ]
    sea = Polygon(arc + [(X0 + 20_000.0, Y0 - radius), (X0 + 20_000.0, Y0 + radius)])
    site = Site("bay", "Bay", LineString(arc), (1.0, 0.0), False, 2500.0, 1000.0)
    profiles = make_profiles(site)
    assert len(profiles) > 20
    for p in profiles:
        assert not sea.contains(Point(p.origin))
        assert sea.contains(Point(_end(p)))
    gap_origin = math.dist(profiles[10].origin, profiles[20].origin)
    gap_end = math.dist(_end(profiles[10]), _end(profiles[20]))
    assert gap_end < gap_origin


def test_count_crossings() -> None:
    a = Profile("s", 0, (0.0, 0.0), (1.0, 0.0), 100.0)
    b = Profile("s", 1, (50.0, -50.0), (0.0, 1.0), 100.0)
    c = Profile("s", 2, (0.0, 500.0), (1.0, 0.0), 100.0)
    assert count_crossings([a, b, c]) == 1
    assert count_crossings([a, c]) == 0


def test_profile_length_covers_the_stated_distance() -> None:
    site = _site(length=5300.0)
    assert {p.length_m for p in make_profiles(site)} == {5300.0}
    assert {p.length_m for p in make_profiles(site, length_m=800.0)} == {800.0}
    with pytest.raises(ValueError):
        make_profiles(site, spacing_m=0.0)
    with pytest.raises(ValueError):
        make_profiles(site, length_m=-1.0)


def test_dropped_profiles_are_skipped_with_original_index() -> None:
    profiles = make_profiles(_site(dropped=(0, 3)))
    assert [p.index for p in profiles] == [1, 2, 4, 5, 6, 7, 8, 9, 10]
    with pytest.raises(ValueError, match="200"):
        make_profiles(_site(dropped=(1,)), spacing_m=100.0)


def test_load_sites_requires_at_least_three(tmp_path: Path) -> None:
    assert MIN_SITES == 3
    with pytest.raises(ValueError, match="at least 3"):
        load_sites(_write(tmp_path, _three()[:2]))
    assert len(load_sites(_write(tmp_path, _three()))) == 3


def test_deltaic_flag_loaded(tmp_path: Path) -> None:
    sites = {s.site_id: s for s in load_sites(_write(tmp_path, _three()))}
    assert sites["b"].deltaic is True
    assert sites["a"].deltaic is False
    assert sites["a"].profile_length_m == 3000.0
    assert sites["a"].inland_m == 1000.0
    assert sites["a"].reference_line.equals(_site("a").reference_line)
    assert sites["a"].seaward == (1.0, 0.0)


def test_duplicate_site_id_raises(tmp_path: Path) -> None:
    data = _three()
    data[2]["site_id"] = "a"
    with pytest.raises(ValueError, match="duplicate"):
        load_sites(_write(tmp_path, data))


def test_coordinates_must_be_inside_utm39n_azerbaijan(tmp_path: Path) -> None:
    data = _three()
    data[1]["reference_line"] = [[49.3, 39.3], [49.4, 39.4]]
    with pytest.raises(ValueError, match="outside"):
        load_sites(_write(tmp_path, data))
    with pytest.raises(ValueError, match="EPSG:32639"):
        load_sites(_write(tmp_path, _three(), crs="EPSG:4326"))


def test_non_unit_seaward_or_bad_length_rejected(tmp_path: Path) -> None:
    data = _three()
    data[0]["seaward"] = [2.0, 0.0]
    with pytest.raises(ValueError, match="unit"):
        load_sites(_write(tmp_path, data))
    data = _three()
    data[0]["profile_length_m"] = 900.0
    with pytest.raises(ValueError, match="profile_length_m"):
        load_sites(_write(tmp_path, data))
    data = _three()
    data[0]["inland_m"] = 0
    with pytest.raises(ValueError, match="inland_m"):
        load_sites(_write(tmp_path, data))


def test_stored_profiles_must_match_the_geometry(tmp_path: Path) -> None:
    data = _three()
    profiles = make_profiles(_site("a"))
    data[0]["profiles"] = [
        {"index": p.index, "origin": list(p.origin), "direction": list(p.direction)}
        for p in profiles
    ]
    assert len(load_sites(_write(tmp_path, data))) == 3
    data[0]["profiles"][2]["origin"] = [X0, Y0]
    with pytest.raises(ValueError, match="stored profile"):
        load_sites(_write(tmp_path, data))


def test_profile_is_frozen() -> None:
    p = Profile("a", 0, (X0, Y0), (1.0, 0.0), 100.0)
    with pytest.raises(AttributeError):
        p.index = 1  # type: ignore[misc]


def test_repository_sites_file_loads() -> None:
    sites = load_sites()
    assert len(sites) >= MIN_SITES
    for site in sites:
        assert make_profiles(site)
        assert site.inland_m > 0
