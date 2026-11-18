"""Shoreline position along profiles, site displacement series and exposed area.

Definitions are fixed in docs/caspian/shoreline-definition.md. No sea-level data are used here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from caspianwatch.sites import Profile, Site, make_profiles

if TYPE_CHECKING:
    from caspianwatch.landsat import WaterMask

SAMPLE_STEP_M = 15.0
MIN_LAND_RUN = 3
MIN_VALID_PIXEL_SHARE = 0.9
DATE_MIN_VALID_FRACTION = 0.1
MIN_DATES = 3
COMPOSITE_MIN_VALID_FRACTION = 0.8
COMPLETE_SET_MIN_YEAR_SHARE = 0.9
CLUSTER_RADIUS_M = 100.0
_INVALID, _WATER, _LAND = 0, 1, 2

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class SiteDisplacement:
    series: pd.Series
    weighted_series: pd.Series
    positions: pd.DataFrame
    complete_profiles: tuple[int, ...]
    per_year_valid_share: pd.Series


def _classes(profile: Profile, mask: WaterMask) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    n = math.floor(profile.length_m / SAMPLE_STEP_M + 1e-9) + 1
    dist = np.arange(n) * SAMPLE_STEP_M
    xs = profile.origin[0] + profile.direction[0] * dist
    ys = profile.origin[1] + profile.direction[1] * dist
    inv = ~mask.transform
    cols = np.floor(inv.a * xs + inv.b * ys + inv.c).astype(np.int64)
    rows = np.floor(inv.d * xs + inv.e * ys + inv.f).astype(np.int64)
    height, width = mask.water.shape
    inside = (rows >= 0) & (rows < height) & (cols >= 0) & (cols < width)
    r, c = np.where(inside, rows, 0), np.where(inside, cols, 0)
    valid = inside & mask.valid[r, c]
    water = mask.water[r, c]
    classes = np.where(valid, np.where(water, _WATER, _LAND), _INVALID)
    pixel = np.where(inside, rows * width + cols, -1)
    return classes.astype(np.int64), pixel.astype(np.int64)


def profile_position(profile: Profile, mask: WaterMask) -> float:
    classes, pixel = _classes(profile, mask)
    if (classes != _INVALID).mean() < MIN_VALID_PIXEL_SHARE or classes[-1] != _WATER:
        return math.nan
    starts = np.flatnonzero(np.r_[True, pixel[1:] != pixel[:-1]])
    ends = np.r_[starts[1:], len(pixel)]
    run, edge = 0, 0
    for start, end in zip(starts[::-1], ends[::-1], strict=True):
        if classes[start] == _LAND:
            run += 1
            if run == 1:
                edge = int(end)
            if run == MIN_LAND_RUN:
                return edge * SAMPLE_STEP_M
        else:
            run = 0
    return math.nan


def _usable(mask: WaterMask | None) -> bool:
    if mask is None:
        return False
    dates = sum(f >= DATE_MIN_VALID_FRACTION for f in mask.date_valid_fractions)
    return dates >= MIN_DATES and mask.composite_valid_fraction >= COMPOSITE_MIN_VALID_FRACTION


def shore_point_weights(points: FloatArray) -> FloatArray:
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    dist = np.hypot(pts[:, None, 0] - pts[None, :, 0], pts[:, None, 1] - pts[None, :, 1])
    return np.asarray(1.0 / (dist <= CLUSTER_RADIUS_M).sum(axis=1), dtype=float)


def site_displacement_detail(
    site: Site,
    masks: Mapping[int, WaterMask | None],
    min_valid_profile_share: float = 0.8,
    fixed_profiles: Sequence[int] | None = None,
) -> SiteDisplacement:
    profiles = make_profiles(site)
    years = sorted(masks)
    positions = pd.DataFrame(
        math.nan, index=pd.Index(years, name="year"), columns=[p.index for p in profiles]
    )
    usable: list[int] = []
    for year in years:
        mask = masks[year]
        if mask is None or not _usable(mask):
            continue
        usable.append(year)
        positions.loc[year] = [profile_position(p, mask) for p in profiles]
    complete: tuple[int, ...] = ()
    if usable:
        share = positions.loc[usable].notna().mean()
        complete = tuple(int(str(i)) for i in share.index[share >= COMPLETE_SET_MIN_YEAR_SHARE])
    by_index = {p.index: p for p in profiles}
    if fixed_profiles is not None:
        unknown = sorted(set(fixed_profiles) - set(by_index))
        if unknown:
            raise ValueError(f"fixed_profiles not in the site profiles: {unknown}")
        complete = tuple(sorted(set(int(i) for i in fixed_profiles)))
    values: dict[int, float] = {}
    weighted: dict[int, float] = {}
    shares: dict[int, float] = {}
    for year in years:
        row = positions.loc[year, list(complete)].dropna() if complete else positions.iloc[:0, :0]
        share_year = len(row) / len(complete) if complete else 0.0
        shares[year] = share_year
        if not complete or share_year < min_valid_profile_share:
            values[year] = weighted[year] = math.nan
            continue
        values[year] = float(np.mean(row.to_numpy(dtype=float)))
        used = [by_index[int(str(i))] for i in row.index]
        pos = row.to_numpy(dtype=float)
        points = np.array(
            [
                [p.origin[0] + p.direction[0] * d, p.origin[1] + p.direction[1] * d]
                for p, d in zip(used, pos, strict=True)
            ]
        )
        w = shore_point_weights(points)
        weighted[year] = float(np.average(pos, weights=w))
    index = pd.Index(years, name="year")
    return SiteDisplacement(
        pd.Series(values, index=index, dtype=float),
        pd.Series(weighted, index=index, dtype=float),
        positions,
        complete,
        pd.Series(shares, index=index, dtype=float),
    )


def site_displacement_series(
    site: Site,
    masks: Mapping[int, WaterMask | None],
    min_valid_profile_share: float = 0.8,
    fixed_profiles: Sequence[int] | None = None,
) -> pd.Series:
    return site_displacement_detail(site, masks, min_valid_profile_share, fixed_profiles).series


def is_measurable(series: pd.Series, period: Sequence[int], min_years: int = 20) -> bool:
    inside = series.reindex(list(period))
    return int(inside.notna().sum()) >= min_years


def exposed_area_km2(first: WaterMask, last: WaterMask) -> float:
    if first.water.shape != last.water.shape or first.transform != last.transform:
        raise ValueError("masks are on different grids")
    both = first.valid & last.valid
    count = int((both & first.water & ~last.water).sum())
    pixel_m2 = abs(first.transform.a * first.transform.e)
    return float(count * pixel_m2 / 1e6)
