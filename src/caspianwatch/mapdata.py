"""Map data, summary table, validation note and report for the Caspian shoreline monitor.

Every number is read from the code paths of the pre-registered run
(docs/caspian/validation-protocol.md); the committed run output docs/caspian/validation-run.txt is
the reference for the reproduction guard.
The overall verdict is never changed or re-tuned here.
"""

from __future__ import annotations

import calendar
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pyproj import Transformer
from rasterio.features import rasterize
from shapely.geometry import LineString
from shapely.ops import unary_union

from caspianwatch import validate
from caspianwatch.landsat import RESOLUTION_M, WaterMask, season_water_mask
from caspianwatch.level import GREALM_URL, load_grealm_caspian, season_level
from caspianwatch.season import SEASON_PATH, Season
from caspianwatch.sentinel import (
    YEARS as S2_YEARS,
)
from caspianwatch.sentinel import (
    qc_against_landsat,
    qc_diagnostics,
    season_water_mask_s2,
    sentinel_displacement_detail,
)
from caspianwatch.shoreline import (
    SiteDisplacement,
    exposed_area_km2,
    is_measurable,
    site_displacement_detail,
)
from caspianwatch.sites import (
    CRS,
    DEFAULT_SITES,
    PROFILE_SPACING_M,
    Site,
    load_sites,
    make_profiles,
    profile_end,
)
from caspianwatch.validate import Overall, SiteReport, validate_site

MIN_UNCERTAINTY_M = 30.0
UNCERTAINTY_M = max(RESOLUTION_M, MIN_UNCERTAINTY_M)
GAP_FACTOR = 3.0
COORD_DECIMALS = 5
SIZE_LIMIT_BYTES = 1_500_000
GUARD_TOLERANCE = 1e-4
THRESHOLDS = (0.0, 0.1, 0.2)
CENSORED_SITE = "gizilaghaj"
CENSORED_LAST_YEAR = 2023
EARLY_PAIRS_LAST_YEAR = 2018
ARTEFACT_PAIR_YEARS = (2020, 2021)
SEED = 0
QC_FULL_YEARS = len(S2_YEARS)
UNINFORMATIVE_PIXELS = 3.0
SENSITIVITY_LABEL = "sensitivity only"
NOT_A_VALIDATION = "not a validation of the monitor"

NOISE_FLOOR_NOTES: dict[str, str] = {
    "gizilaghaj": "100-200 m annual noise floor estimated in review, see qc.md",
}
REVIEWER_ESTIMATES: dict[str, Any] = {
    "source": "reviewers' estimates and simulations; not computed by this pipeline",
    "power_per_site_percent": 30,
    "power_overall_percent": 8,
    "false_confirmation_linear_or_random_walk_percent_below": 1,
    "false_confirmation_independent_accelerating_trend_percent": 5.5,
    "composition_effect_m": 130,
    "gizilaghaj_noise_floor_m": "100-200",
}
LITERATURE = (
    "The topic is already studied (EGU24-14488; Gasimov 2021). The 218 km2 of exposed land in "
    "EGU24-14488 refers to a different place and period and is NOT a validation of this work."
)
QC_NOTE = (
    "Compares two optical sensors (Landsat, Sentinel-2) under one method on one grid: this shows "
    "consistency, not ground-truth accuracy."
)

SOURCES: list[dict[str, str]] = [
    {
        "name": "NASA GSFC Global Water Monitor (G-REALM), Caspian Sea, lake 000270",
        "use": "sea level, heights relative to the Jason-2 reference-pass mean (a satellite datum)",
        "licence": "no explicit licence on the source page; project disclaimer on accuracy and use",
        "attribution": "NASA/GSFC Global Water Monitor (formerly USDA/NASA G-REALM)",
        "url": GREALM_URL,
    },
    {
        "name": "Landsat Collection 2 Level-2 (USGS) via Microsoft Planetary Computer",
        "use": "shoreline 2000-2025",
        "licence": "USGS open data, no restrictions on use",
        "attribution": "Landsat data courtesy of the U.S. Geological Survey",
        "url": "https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2",
    },
    {
        "name": "Sentinel-2 L2A via Microsoft Planetary Computer",
        "use": "optical consistency check 2017-2025",
        "licence": "Copernicus Sentinel data: free, full and open",
        "attribution": "Contains modified Copernicus Sentinel data processed by ESA",
        "url": "https://planetarycomputer.microsoft.com/dataset/sentinel-2-l2a",
    },
    {
        "name": "ESA WorldCover 2021 v200",
        "use": "reference coast for site geometry",
        "licence": "CC BY 4.0",
        "attribution": "Contains modified ESA WorldCover 2021 data (c) ESA WorldCover project",
        "url": "https://esa-worldcover.org",
    },
    {
        "name": "Copernicus DEM GLO-90",
        "use": "site selection (slope) and landward-origin diagnostics",
        "licence": "Copernicus DEM licence: free, full and open",
        "attribution": "Copernicus DEM GLO-90, provided under COPERNICUS by the EU and ESA",
        "url": "https://planetarycomputer.microsoft.com/dataset/cop-dem-glo-90",
    },
]


class ReproductionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SiteAnalysis:
    site: Site
    detail: SiteDisplacement
    report: SiteReport
    measurable: bool


# ---------- helpers ----------


def _num(x: float, digits: int = 6) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), digits)


def verdict_label(verdict: str) -> str:
    return {
        "confirmed": "confirmed (protocol)",
        "not_confirmed": "not confirmed (protocol)",
        "not_measurable": "not measurable (protocol)",
    }[verdict]


def _no_network(*args: object, **kwargs: object) -> Any:
    raise RuntimeError("network access is not allowed in the map-data build")


def _no_scenes(*args: object, **kwargs: object) -> list[Any]:
    return []


@lru_cache(maxsize=1)
def _to_wgs84() -> Transformer:
    return Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)


# ---------- shoreline lines ----------


def split_segments(indices: Sequence[int], xy: NDArray[np.float64]) -> list[NDArray[np.float64]]:
    """Break where profile indices are not consecutive or points are far apart."""
    pts = np.asarray(xy, dtype=float).reshape(-1, 2)
    if len(pts) == 0:
        return []
    steps = np.hypot(*(pts[1:] - pts[:-1]).T) if len(pts) > 1 else np.array([])
    adjacent = np.array([b - a == 1 for a, b in zip(indices[:-1], indices[1:], strict=True)], bool)
    median = float(np.median(steps[adjacent])) if adjacent.any() else math.inf
    segments: list[list[int]] = [[0]]
    for i in range(1, len(pts)):
        if adjacent[i - 1] and steps[i - 1] <= GAP_FACTOR * median:
            segments[-1].append(i)
        else:
            segments.append([i])
    return [pts[s] for s in segments]


def _year_points(site: Site, positions: pd.Series) -> tuple[list[int], NDArray[np.float64]]:
    idx: list[int] = []
    pts: list[tuple[float, float]] = []
    for p in make_profiles(site):
        pos = float(positions[p.index])
        if math.isfinite(pos):
            idx.append(p.index)
            pts.append((p.origin[0] + p.direction[0] * pos, p.origin[1] + p.direction[1] * pos))
    return idx, np.array(pts, dtype=float).reshape(-1, 2)


def _lonlat(seg: NDArray[np.float64]) -> list[list[float]]:
    lon, lat = _to_wgs84().transform(seg[:, 0], seg[:, 1])
    return [
        [round(float(a), COORD_DECIMALS), round(float(b), COORD_DECIMALS)]
        for a, b in zip(lon, lat, strict=True)
    ]


def build_shoreline_geojson(
    site: Site,
    masks: Mapping[int, WaterMask | None],
    reports: Sequence[SiteReport],
    *,
    detail: SiteDisplacement | None = None,
) -> dict[str, Any]:
    detail = detail or site_displacement_detail(site, masks)
    report = next((r for r in reports if r.site_id == site.site_id), None)
    features: list[dict[str, Any]] = []
    for year in sorted(masks):
        if year not in detail.positions.index:
            continue
        idx, pts = _year_points(site, pd.Series(detail.positions.loc[year]))
        lines = [_lonlat(s) for s in split_segments(idx, pts) if len(s) >= 2]
        if not lines:
            continue
        geometry: dict[str, Any] = (
            {"type": "LineString", "coordinates": lines[0]}
            if len(lines) == 1
            else {"type": "MultiLineString", "coordinates": lines}
        )
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "year": int(year),
                    "site_id": site.site_id,
                    "n_points": len(idx),
                    "uncertainty_m": UNCERTAINTY_M,
                    "site_verdict": verdict_label(report.verdict) if report else None,
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "properties": {
            "site_id": site.site_id,
            "name": site.name,
            "deltaic": site.deltaic,
            "noise_floor_note": NOISE_FLOOR_NOTES.get(site.site_id),
            "uncertainty_note": "uncertainty_m is never below 30 m (one Landsat pixel)",
        },
        "features": features,
    }


# ---------- exposed area ----------


def exposed_area_strip_km2(site: Site, first: WaterMask, last: WaterMask) -> float:
    """Exposed land counted only inside the strip of the profiles (half spacing each side)."""
    exposed_area_km2(first, last)
    strip = unary_union(
        [
            LineString([p.origin, profile_end(p)]).buffer(PROFILE_SPACING_M / 2, cap_style="flat")
            for p in make_profiles(site)
        ]
    )
    inside = rasterize(
        [(strip, 1)], out_shape=first.water.shape, transform=first.transform, fill=0, dtype="uint8"
    ).astype(bool)
    both = first.valid & last.valid
    count = int((both & first.water & ~last.water & inside).sum())
    return float(count * abs(first.transform.a * first.transform.e) / 1e6)


# ---------- series, retreat per cm, table ----------


def _series_list(s: pd.Series, years: Sequence[int], digits: int) -> list[float | None]:
    return [_num(float(s.get(y, math.nan)), digits) for y in years]


def build_level_vs_shore_json(
    displacement: Mapping[str, pd.Series], level: pd.Series
) -> dict[str, Any]:
    years = sorted(
        {int(y) for s in displacement.values() for y in s.index} | {int(y) for y in level.index}
    )
    sites: dict[str, Any] = {}
    for sid, d in displacement.items():
        pairs = validate.paired_differences(d, level)
        sites[sid] = {
            "displacement_m": _series_list(d, years, 3),
            "differences": [
                {
                    "year": int(str(y)),
                    "d_displacement_m": _num(float(row["dd"]), 3),
                    "d_level_m": _num(float(row["dl"]), 4),
                }
                for y, row in pairs.iterrows()
            ],
        }
    return {"years": years, "level_m": _series_list(level, years, 4), "sites": sites}


def metres_per_cm_range(displacement: pd.Series, level: pd.Series) -> tuple[float, float] | None:
    """Retreat per 1 cm of level fall from both regression directions (L on D and D on L)."""
    common = validate.common_years(displacement, level)
    if (
        len(common) < 3
        or np.ptp(common["d"].to_numpy()) == 0
        or np.ptp(common["l"].to_numpy()) == 0
    ):
        return None
    d, lev = common["d"].to_numpy(), common["l"].to_numpy()
    b_level_on_d = float(np.polyfit(d, lev, 1)[0])
    b_d_on_level = float(np.polyfit(lev, d, 1)[0])
    if b_level_on_d == 0 or b_d_on_level == 0:
        return None
    values = (0.01 / abs(b_level_on_d), 0.01 * abs(b_d_on_level))
    return min(values), max(values)


def _range_text(rng: tuple[float, float]) -> str:
    lo, hi = round(rng[0]), round(rng[1])
    return f"about {lo} m" if lo == hi else f"about {lo}-{hi} m"


def build_summary_table(
    reports: Sequence[SiteReport],
    exposed_grid_km2: Mapping[str, float | None],
    exposed_strip_km2: Mapping[str, float | None],
    retreat_per_cm: Mapping[str, tuple[float, float] | None],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for r in reports:
        rng = retreat_per_cm.get(r.site_id)
        rows.append(
            {
                "site_id": r.site_id,
                "verdict": verdict_label(r.verdict),
                "reason": r.reason,
                "exposed_km2_grid": _num(exposed_grid_km2.get(r.site_id) or math.nan, 2),
                "exposed_km2_strip": _num(exposed_strip_km2.get(r.site_id) or math.nan, 2),
                "metres_per_cm": _range_text(rng) if r.verdict == "confirmed" and rng else None,
            }
        )
    return pd.DataFrame(rows, dtype=object).astype(object).where(lambda f: f.notna(), None)


# ---------- note ----------


def validation_note(overall: str, reports: Sequence[SiteReport]) -> str:
    measurable = [r for r in reports if r.verdict != "not_measurable"]
    confirmed = [r for r in measurable if r.verdict == "confirmed"]
    sites = "; ".join(f"{r.site_id}: {verdict_label(r.verdict)}" for r in reports)
    text = (
        f"Overall verdict: {overall}. {len(confirmed)} of {len(measurable)} measurable sites "
        "reached the per-site protocol verdict 'confirmed (protocol)'. "
        f"This is {NOT_A_VALIDATION}. "
        f"Per site: {sites}."
    )
    skipped = [f"{r.site_id} ({r.reason})" for r in reports if r.verdict == "not_measurable"]
    if skipped:
        text += f" Not measurable: {'; '.join(skipped)}."
    if overall != "confirmed":
        text += (
            " The test has low power (about "
            f"{REVIEWER_ESTIMATES['power_per_site_percent']} % per site, reviewers' estimate), so "
            "'not confirmed' means not shown, not refuted."
        )
    return text


# ---------- analysis ----------


def analyse_sites(
    sites: Sequence[Site],
    masks_by_site: Mapping[str, Mapping[int, WaterMask | None]],
    levels: pd.DataFrame,
    months: tuple[int, int],
    period: Sequence[int],
    seed: int = SEED,
) -> tuple[pd.Series, dict[str, SiteAnalysis]]:
    level_series = pd.Series(
        {y: season_level(levels, y, months, min_obs=3) for y in period}, dtype=float
    )
    level_series.index.name = "year"
    out: dict[str, SiteAnalysis] = {}
    for site in sites:
        detail = site_displacement_detail(site, masks_by_site[site.site_id])
        measurable = is_measurable(detail.series, list(period))
        report = validate_site(
            detail.series, level_series, site.site_id, measurable=measurable, seed=seed
        )
        out[site.site_id] = SiteAnalysis(site, detail, report, measurable)
    return level_series, out


def _rec(r: SiteReport) -> dict[str, Any]:
    return {
        "verdict": r.verdict,
        "criterion_met": r.verdict == "confirmed",
        "rmse_model": _num(r.rmse_model, 4),
        "rmse_baseline": _num(r.rmse_baseline, 4),
        "r_diff": _num(r.r_diff, 4),
        "ci_low": _num(r.r_diff_ci_low, 4),
        "n_years": r.n_years,
    }


def _always_present(detail: SiteDisplacement) -> list[int]:
    pos = detail.positions
    usable = pos.index[pos.notna().any(axis=1)]
    full = pos.loc[usable].notna().all()
    return [int(str(c)) for c in full.index[full]]


def _r_diff_of(pairs: pd.DataFrame) -> float | None:
    return _num(validate.pearson(pairs["dd"].to_numpy(), -pairs["dl"].to_numpy()), 4)


def _secondary(
    a: SiteAnalysis,
    masks: Mapping[int, WaterMask | None],
    level: pd.Series,
    period: Sequence[int],
    seed: int,
) -> tuple[dict[str, Any], dict[float, SiteReport]]:
    sid = a.site.site_id
    periodl = list(period)

    def run(series: pd.Series, per: Sequence[int] = periodl) -> SiteReport:
        return validate_site(
            series, level, sid, measurable=is_measurable(series, list(per)), seed=seed
        )

    out: dict[str, Any] = {"label": SENSITIVITY_LABEL}
    out["weighted"] = _rec(run(a.detail.weighted_series))
    fixed = _always_present(a.detail)
    if fixed:
        fixed_series = site_displacement_detail(a.site, masks, fixed_profiles=fixed).series
        out["always_present"] = {"n_profiles": len(fixed), **_rec(run(fixed_series))}
    else:
        out["always_present"] = None
    thr: list[dict[str, Any]] = []
    by_threshold: dict[float, SiteReport] = {}
    for t in THRESHOLDS:
        masks_t = {
            y: None if m is None else validate.with_threshold(m, t) for y, m in masks.items()
        }
        rep = run(site_displacement_detail(a.site, masks_t).series)
        by_threshold[t] = rep
        thr.append({"threshold": t, **_rec(rep)})
    out["fixed_threshold"] = thr
    pairs = validate.paired_differences(a.detail.series, level)
    low, high, share = validate.block_bootstrap_ci(
        pairs["dd"].to_numpy(), -pairs["dl"].to_numpy(), seed
    )
    out["block_bootstrap"] = {
        "block_length": validate.BLOCK_LEN,
        "ci_low": _num(low),
        "ci_high": _num(high),
        "degenerate_share": _num(share),
    }
    if sid == CENSORED_SITE:
        cut = a.detail.series.loc[:CENSORED_LAST_YEAR]
        cut_period = [y for y in periodl if y <= CENSORED_LAST_YEAR]
        out["excluding_2024_2025"] = _rec(run(cut, cut_period))
    else:
        out["excluding_2024_2025"] = None
    out["r_diff_pairs_to_2018"] = _r_diff_of(pairs.loc[pairs.index <= EARLY_PAIRS_LAST_YEAR])
    out["r_diff_without_2020_2021_pairs"] = _r_diff_of(
        pairs.loc[~pairs.index.isin(ARTEFACT_PAIR_YEARS)]
    )
    return out, by_threshold


def _qc(site: Site, season: Season, a: SiteAnalysis) -> dict[str, Any]:
    def build(s: Site, year: int, sn: Season) -> WaterMask | None:
        return season_water_mask_s2(
            s, year, sn, search=_no_scenes, read=_no_network, sleep=lambda _: None
        )

    s2, _ = sentinel_displacement_detail(
        site, S2_YEARS, season, complete_profiles=a.detail.complete_profiles, build=build
    )
    try:
        rep = qc_against_landsat(a.detail.series, s2.series, site.site_id)
    except ValueError as exc:
        return {"status": "not_computed", "reason": str(exc), "note": QC_NOTE}
    diag = qc_diagnostics(a.detail.series, s2.series)
    return {
        "status": "computed",
        "n_common_years": rep.n_years,
        "median_abs_diff_m": _num(rep.median_abs_diff_m, 2),
        "ok": rep.ok,
        "preliminary": rep.n_years < QC_FULL_YEARS,
        "flagged_years": [
            {"year": y, "diff_m": _num(v, 1)}
            for y, v in sorted(diag.per_year_diff_m.items())
            if abs(v) > 30.0
        ],
        "note": QC_NOTE,
    }


def _exposed(
    a: SiteAnalysis, masks: Mapping[int, WaterMask | None], period: Sequence[int]
) -> dict[str, Any]:
    first, last = masks.get(period[0]), masks.get(period[-1])
    base: dict[str, Any] = {"from_year": period[0], "to_year": period[-1]}
    if first is None or last is None:
        return {**base, "whole_site_grid": None, "profile_strip": None}
    return {
        **base,
        "whole_site_grid": _num(exposed_area_km2(first, last), 2),
        "profile_strip": _num(exposed_area_strip_km2(a.site, first, last), 2),
    }


def _report_dict(r: SiteReport) -> dict[str, Any]:
    return {
        "verdict": r.verdict,
        "verdict_label": verdict_label(r.verdict),
        "reason": r.reason,
        "rmse_model": _num(r.rmse_model),
        "rmse_baseline": _num(r.rmse_baseline),
        "r_diff": _num(r.r_diff),
        "r_diff_ci_low": _num(r.r_diff_ci_low),
        "r_diff_ci_high": _num(r.ci_high),
        "raw_r": _num(r.raw_r),
        "n_years": r.n_years,
        "n_pairs": r.n_pairs,
    }


def build_caspian_data(
    sites: Sequence[Site],
    masks_by_site: Mapping[str, Mapping[int, WaterMask | None]],
    level_series: pd.Series,
    analyses: Mapping[str, SiteAnalysis],
    *,
    season_months: tuple[int, int],
    period: Sequence[int],
    reproducibility: Mapping[str, str],
    generated: str,
    seed: int = SEED,
) -> dict[str, Any]:
    season = Season(*season_months)
    reports = [analyses[s.site_id].report for s in sites]
    overall: Overall = validate.overall_verdict(reports)
    measurable = [r for r in reports if r.verdict != "not_measurable"]
    site_blocks: list[dict[str, Any]] = []
    threshold_reports: dict[float, list[SiteReport]] = {t: [] for t in THRESHOLDS}
    retreat: dict[str, tuple[float, float] | None] = {}
    grid_area: dict[str, float | None] = {}
    strip_area: dict[str, float | None] = {}
    for site in sites:
        a = analyses[site.site_id]
        masks = masks_by_site[site.site_id]
        sensitivity, by_t = _secondary(a, masks, level_series, period, seed)
        for t, rep in by_t.items():
            threshold_reports[t].append(rep)
        exposed = _exposed(a, masks, period)
        grid_area[site.site_id] = exposed["whole_site_grid"]
        strip_area[site.site_id] = exposed["profile_strip"]
        retreat[site.site_id] = metres_per_cm_range(a.detail.series, level_series)
        confirmed = a.report.verdict == "confirmed"
        shoreline = build_shoreline_geojson(site, masks, reports, detail=a.detail)
        site_blocks.append(
            {
                "id": site.site_id,
                "name": site.name,
                "deltaic": site.deltaic,
                "caveats": _caveat(site.site_id),
                "noise_floor_note": NOISE_FLOOR_NOTES.get(site.site_id),
                "valid_years": [int(y) for y in a.detail.series.index[a.detail.series.notna()]],
                "shoreline_years": [f["properties"]["year"] for f in shoreline["features"]],
                "shoreline": shoreline,
                "report": _report_dict(a.report),
                "net_displacement_m": _net(a.detail.series),
                "sensitivity": sensitivity,
                "exposed_km2": exposed,
                "qc": _qc(site, season, a),
                "metres_per_cm_of_level_fall": (
                    _range_text(rng) if confirmed and (rng := retreat[site.site_id]) else None
                ),
            }
        )
    table = build_summary_table(reports, grid_area, strip_area, retreat)
    return {
        "meta": {
            "generated": generated,
            "season": {
                "start_month": season_months[0],
                "end_month": season_months[1],
                "label": "-".join(calendar.month_abbr[m] for m in season_months),
            },
            "period": [int(period[0]), int(period[-1])],
            "r_diff_min": validate.R_DIFF_MIN,
            "n_boot": validate.N_BOOT,
            "min_common_years": validate.MIN_COMMON_YEARS,
            "seed": seed,
            "uncertainty_m": UNCERTAINTY_M,
            "uncertainty_note": "never below 30 m: one Landsat pixel",
            "level_datum_note": "G-REALM heights are relative to the Jason-2 reference-pass mean, "
            "not to a geodetic datum",
            "r_diff_definition": "Pearson(dD, -dL) over adjacent-year differences; "
            "iid percentile bootstrap CI",
            "sources": SOURCES,
            "reproducibility": dict(reproducibility),
            "literature_note": LITERATURE,
            "qc_note": QC_NOTE,
            "reviewer_estimates": REVIEWER_ESTIMATES,
        },
        "overall": {
            "verdict": overall,
            "n_confirmed": sum(r.verdict == "confirmed" for r in reports),
            "n_measurable": len(measurable),
            "n_sites": len(reports),
        },
        "validation_note": validation_note(overall, reports),
        "threshold_overall": {
            str(t): {
                "overall": validate.overall_verdict(reps),
                "criterion_met_sites": [r.site_id for r in reps if r.verdict == "confirmed"],
                "label": SENSITIVITY_LABEL,
            }
            for t, reps in threshold_reports.items()
        },
        "sites": site_blocks,
        "summary_table": json.loads(table.to_json(orient="records")),
        "level_vs_shore": build_level_vs_shore_json(
            {s.site_id: analyses[s.site_id].detail.series for s in sites}, level_series
        ),
    }


def _net(series: pd.Series) -> float | None:
    valid = series.dropna()
    return _num(float(valid.iloc[-1] - valid.iloc[0]), 1) if len(valid) >= 2 else None


def _caveat(site_id: str) -> str | None:
    for item in json.loads(DEFAULT_SITES.read_text(encoding="utf-8"))["sites"]:
        if item["site_id"] == site_id:
            return str(item["geometry_caveat"]) if item.get("geometry_caveat") else None
    return None


# ---------- serialisation ----------


def serialise(data: Mapping[str, Any]) -> tuple[str, str]:
    try:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except ValueError as exc:
        raise ValueError(f"data contains NaN or infinity: {exc}") from exc
    js = f"window.CASPIAN_DATA = {text};\n"
    if len(js.encode("utf-8")) > SIZE_LIMIT_BYTES:
        raise ValueError(f"data file too large: {len(js.encode('utf-8'))} bytes")
    return text, js


# ---------- reproduction guard ----------


def _parse_float(text: str) -> float:
    return math.nan if text == "nan" else float(text)


def parse_run_file(text: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        if line.startswith("site_id="):
            sid = line.split("=", 1)[1].strip()
            current = None if sid in out else out.setdefault(sid, {})
        elif current is not None and "verdict" not in current and line.startswith("verdict="):
            current["verdict"] = line.split("=", 1)[1].strip()
        elif current is not None and "rmse_model" not in current and line.startswith("rmse_model="):
            m = re.match(r"rmse_model=(\S+)\s+rmse_baseline=(\S+)", line)
            if m:
                current["rmse_model"], current["rmse_baseline"] = map(_parse_float, m.groups())
        elif current is not None and "r_diff" not in current and line.startswith("r_diff="):
            m = re.match(r"r_diff=(\S+)\s+r_diff_ci_low=(\S+)", line)
            if m:
                current["r_diff"], current["r_diff_ci_low"] = map(_parse_float, m.groups())
    return out


def check_reproduction(
    reports: Sequence[SiteReport], run_path: Path, tolerance: float = GUARD_TOLERANCE
) -> None:
    parsed = parse_run_file(run_path.read_text(encoding="utf-8"))
    for r in reports:
        ref = parsed.get(r.site_id)
        if (
            ref is None
            or not {"verdict", "rmse_model", "rmse_baseline", "r_diff", "r_diff_ci_low"}
            <= ref.keys()
        ):
            raise ReproductionError(f"{r.site_id}: not found in {run_path.name}")
        if ref["verdict"] != r.verdict:
            raise ReproductionError(
                f"{r.site_id}: verdict {r.verdict} != run file {ref['verdict']}"
            )
        for name in ("rmse_model", "rmse_baseline", "r_diff", "r_diff_ci_low"):
            mine, theirs = float(getattr(r, name)), float(ref[name])
            if math.isnan(mine) and math.isnan(theirs):
                continue
            if math.isnan(mine) or math.isnan(theirs) or abs(mine - theirs) > tolerance:
                raise ReproductionError(
                    f"{r.site_id}: {name} {mine!r} differs from run file {theirs!r} "
                    f"by more than {tolerance}"
                )


# ---------- real data ----------


def load_real_masks(
    site: Site, season: Season, years: Sequence[int]
) -> dict[int, WaterMask | None]:
    return {
        y: season_water_mask(
            site, y, season, search=_no_scenes, read=_no_network, sleep=lambda _: None
        )
        for y in years
    }


def build_real_data(
    run_path: Path, reproducibility: Mapping[str, str], generated: str | None = None
) -> dict[str, Any]:
    import datetime as dt

    chosen = json.loads(SEASON_PATH.read_text(encoding="utf-8"))
    season = Season(chosen["chosen"]["start_month"], chosen["chosen"]["end_month"])
    period = [int(y) for y in chosen["period"]["years"]]
    sites = load_sites()
    masks = {s.site_id: load_real_masks(s, season, period) for s in sites}
    months = (season.start_month, season.end_month)
    level_series, analyses = analyse_sites(
        sites, masks, load_grealm_caspian(fetch=_no_network), months, period
    )
    check_reproduction([analyses[s.site_id].report for s in sites], run_path)
    return build_caspian_data(
        sites,
        masks,
        level_series,
        analyses,
        season_months=months,
        period=period,
        reproducibility=reproducibility,
        generated=generated or dt.date.today().isoformat(),
    )


# ---------- report ----------


def _f(x: float | None, digits: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _site_row(s: dict[str, Any]) -> str:
    r = s["report"]
    ex = s["exposed_km2"]
    return (
        f"| {s['id']} | {r['verdict_label']} | {r['n_years']} / {r['n_pairs']} | "
        f"{_f(r['rmse_model'], 3)} | {_f(r['rmse_baseline'], 3)} | {_f(r['r_diff'])} | "
        f"{_f(r['r_diff_ci_low'])} | {_f(r['raw_r'])} | {_f(ex['whole_site_grid'], 1)} | "
        f"{_f(ex['profile_strip'], 1)} | {s['metres_per_cm_of_level_fall'] or '-'} |"
    )


def _sens_lines(s: dict[str, Any]) -> list[str]:
    sens = s["sensitivity"]

    def line(name: str, rec: dict[str, Any] | None) -> str:
        if rec is None:
            return f"| {s['id']} | {name} | n/a | n/a | n/a |"
        met = "criterion met" if rec["criterion_met"] else "criterion not met"
        return f"| {s['id']} | {name} | {_f(rec['r_diff'], 3)} | {_f(rec['ci_low'], 3)} | {met} |"

    rows = [line("weighted displacement", sens["weighted"])]
    ap = sens["always_present"]
    rows.append(
        line(
            f"always-present profiles ({ap['n_profiles']})" if ap else "always-present profiles", ap
        )
    )
    rows += [line(f"fixed MNDWI threshold {t['threshold']}", t) for t in sens["fixed_threshold"]]
    if sens["excluding_2024_2025"] is not None:
        rows.append(line("excluding 2024-2025", sens["excluding_2024_2025"]))
    bb = sens["block_bootstrap"]
    rows.append(
        f"| {s['id']} | block bootstrap CI (length {bb['block_length']}) | n/a | "
        f"[{_f(bb['ci_low'], 3)}, {_f(bb['ci_high'], 3)}] | n/a |"
    )
    return rows


def build_report(data: Mapping[str, Any]) -> str:
    meta, overall, sites = data["meta"], data["overall"], data["sites"]
    repro = meta["reproducibility"]
    est = meta["reviewer_estimates"]
    thr_min = meta["r_diff_min"]
    n_years = {s["report"]["n_years"] for s in sites if s["report"]["verdict"] != "not_measurable"}
    n_pairs = {s["report"]["n_pairs"] for s in sites if s["report"]["verdict"] != "not_measurable"}
    sample = ", ".join(sorted(f"{y}" for y in n_years)) or "n/a"
    pairs = ", ".join(sorted(f"{y}" for y in n_pairs)) or "n/a"
    out: list[str] = [
        "# Caspian shoreline monitor: validation report",
        "",
        f"Generated {meta['generated']} from the data object `web/data/caspian-data.json` by "
        "`scripts/build_caspian.py`. All numbers below are copied from that object.",
        "",
        f"**Overall protocol verdict: {overall['verdict']}.** {overall['n_confirmed']} of "
        f"{overall['n_measurable']} measurable sites reached the per-site verdict "
        f"'confirmed (protocol)'. This is {NOT_A_VALIDATION}.",
        "",
        "## 1. Purpose",
        "",
        "The monitor shows annual Landsat shorelines of three low-lying Azerbaijani Caspian sites "
        f"({meta['period'][0]}-{meta['period'][1]}, season {meta['season']['label']}) next to the "
        "satellite-altimetry sea level. The question tested here is narrow: does the year-to-year "
        "change of the shoreline position follow the year-to-year change of the sea level, site by "
        "site, better than a time trend does? The test was fixed before the results were seen.",
        "",
        "## 2. Protocol",
        "",
        "The criterion and all decisions are in `docs/caspian/validation-protocol.md`, written and "
        "committed before the validation code was written and before the sea-level series was "
        "opened for this purpose. A site verdict is 'confirmed (protocol)' only if three "
        "conditions "
        "hold together: leave-one-year-out RMSE of the level model is below that of the time-trend "
        f"baseline; r_diff is at least {thr_min}; the lower end of the 95 % bootstrap interval of "
        "r_diff is above zero. Two or more such sites give the highest overall level; "
        "exactly one gives 'partial'; none gives 'not_confirmed'.",
        "",
        "Sign convention, disclosed: the specification says 'not lower than 0.5' without a sign. "
        "The protocol makes it explicit: displacement D is positive when the sea retreats, the "
        "level falls then, so r_diff = Pearson(dD, -dL) and a positive value is expected. This is "
        "a clarification of the convention, not a change of the physical meaning.",
        "",
        "## 3. Data and methods in brief",
        "",
        "- Shoreline: Landsat Collection 2 Level-2 water composite (modified NDWI from green and "
        "SWIR1, Otsu threshold per year-site), position along fixed seaward profiles every 200 m; "
        "site displacement D is the unweighted mean over the complete profile set.",
        f"- Sea level: NASA G-REALM Caspian series, season mean {meta['season']['label']}; "
        f"{meta['level_datum_note']}.",
        f"- Differences use adjacent years only; {sample} annual values and {pairs} adjacent-year "
        "pairs per measurable site (2003 has no usable Landsat composite).",
        f"- {meta['r_diff_definition']}; N = {meta['n_boot']} resamples, seed {meta['seed']}.",
        "- Sources and licences: "
        + "; ".join(f"{x['name']} ({x['licence']})" for x in meta["sources"])
        + ".",
        "",
        "## 4. Results",
        "",
        "| Site | Site verdict | Years / pairs | RMSE model | RMSE baseline | r_diff | CI low | "
        "raw r | Exposed km2 (whole site grid) | Exposed km2 (profile strip) | "
        "Retreat per cm of level fall |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        *[_site_row(s) for s in sites],
        "",
        "RMSE in metres of level. Exposed area is land that was water in the first and is land in "
        f"the last year ({meta['period'][0]}-{meta['period'][1]}); it is counted over the whole "
        "site "
        "grid, and the value inside the profile strip is shown beside it. Raw r is shown only "
        "because both series trend; it is not evidence of coupling. Retreat per cm is given only "
        "for "
        "a site with the per-site verdict 'confirmed (protocol)', as the range between the two "
        "regression directions.",
        "",
        "Reasons for the other sites:",
        "",
        *[
            f"- {s['id']}: {s['report']['reason']}"
            for s in sites
            if s["report"]["verdict"] != "confirmed"
        ],
        "",
        "Sensor consistency check (Sentinel-2 minus Landsat, shoreline position along the same "
        "profiles):",
        "",
        "| Site | Common years | Median abs difference, m | Within 30 m | Status |",
        "|---|---|---|---|---|",
        *[_qc_row(s) for s in sites],
        "",
        meta["qc_note"],
        "",
        "## 5. Secondary analyses (sensitivity only)",
        "",
        "These analyses are reported beside the verdict and never change it. 'Criterion met' only "
        "says that the same three conditions hold under that variant.",
        "",
        "| Site | Variant | r_diff | CI low (or interval) | Criterion |",
        "|---|---|---|---|---|",
        *[row for s in sites for row in _sens_lines(s)],
        "",
        _threshold_sentence(data),
        "",
        "## 6. How the result may and may not be described",
        "",
        *_description_paragraphs(data, est),
        "",
        "## 7. Limitations",
        "",
        *_limitation_lines(data),
        "",
        "## 8. Reproducibility",
        "",
        f"- Protocol commit: `{repro['protocol_commit']}`. "
        f"Run output commit: `{repro['run_commit']}`. "
        f"Build commit (HEAD at build time): `{repro['head_commit']}`.",
        f"- sha256 of `docs/caspian/validation-run.txt`: `{repro['run_sha256']}`.",
        "- The build recomputes all site reports from cached masks and the committed G-REALM file "
        "without network and stops with an error if rmse_model, rmse_baseline, r_diff or the lower "
        "interval end differs from the committed run output by more than 1e-4.",
        "- Commands: `python scripts/run_caspian_validation.py` (the single pre-registered run; "
        "re-running is allowed only to reproduce the file byte for byte) and "
        "`python scripts/build_caspian.py` (writes `web/data/caspian-data.js`, "
        "`web/data/caspian-data.json` and this report).",
        "",
    ]
    return "\n".join(out)


def _qc_row(s: dict[str, Any]) -> str:
    q = s["qc"]
    if q["status"] != "computed":
        return f"| {s['id']} | n/a | n/a | n/a | not computed ({q['reason']}) |"
    status = "preliminary, fewer than the full set of years" if q["preliminary"] else "complete"
    flagged = (
        "; year(s) over 30 m: "
        + ", ".join(f"{x['year']} ({x['diff_m']} m)" for x in q["flagged_years"])
        if q["flagged_years"]
        else ""
    )
    return (
        f"| {s['id']} | {q['n_common_years']} | {_f(q['median_abs_diff_m'], 1)} | "
        f"{'yes' if q['ok'] else 'no'} | {status}{flagged} |"
    )


OVERALL_WORDS = {
    "confirmed": "at the top level",
    "partial": "partial",
    "not_confirmed": "not_confirmed",
}


def _threshold_sentence(data: Mapping[str, Any]) -> str:
    parts = []
    for t, v in data["threshold_overall"].items():
        met = ", ".join(v["criterion_met_sites"]) or "no site"
        parts.append(
            f"fixed MNDWI {t}: overall outcome {OVERALL_WORDS[v['overall']]} "
            f"(criterion met at {met})"
        )
    return (
        "Under fixed thresholds the overall outcome changes between variants ("
        + "; ".join(parts)
        + "). These are sensitivity analyses; the protocol verdict stands."
    )


def _description_paragraphs(data: Mapping[str, Any], est: Mapping[str, Any]) -> list[str]:
    meta, overall, sites = data["meta"], data["overall"], data["sites"]
    thr_min = meta["r_diff_min"]
    paras = [
        f"**Overall.** The protocol verdict is {overall['verdict']} ({overall['n_confirmed']} of "
        f"{overall['n_measurable']} measurable sites). The result must not be described as showing "
        f"that the monitor or the relation between shoreline and sea level is established; it is "
        f"{NOT_A_VALIDATION}.",
    ]
    for s in sites:
        r, sens = s["report"], s["sensitivity"]
        if r["verdict"] == "confirmed":
            margin = f"{_f(r['r_diff'])} against the threshold {thr_min}"
            fixed0 = next((t for t in sens["fixed_threshold"] if t["threshold"] == 0.0), None)
            at0 = (
                ", and the criterion is not met at fixed MNDWI 0.0 "
                f"(r_diff {_f(fixed0['r_diff'], 3)})"
                if fixed0 and not fixed0["criterion_met"]
                else ""
            )
            paras.append(
                f"**{s['id']} (confirmed (protocol)).** The margin is small: r_diff {margin}"
                f"{at0}. Using only pairs up to {EARLY_PAIRS_LAST_YEAR} r_diff is "
                f"{_f(sens['r_diff_pairs_to_2018'])}, so the signal comes mainly from the later, "
                "accelerated level fall. Under a null of no relation a false "
                "'confirmed (protocol)' "
                f"is rare (below {est['false_confirmation_linear_or_random_walk_percent_below']} % "
                "per site for linear-trend or random-walk nulls, about "
                f"{est['false_confirmation_independent_accelerating_trend_percent']} % under an "
                "independent accelerating trend; reviewers' simulation). "
                + (
                    f"Retreat per cm of level fall: {s['metres_per_cm_of_level_fall']}; the "
                    "level is relative to a satellite datum. "
                    if s["metres_per_cm_of_level_fall"]
                    else ""
                )
                + NOISE_FLOOR_TEXT.get(s["id"], "")
            )
    paras.append(
        "**Sensitivity of the overall verdict.** The overall verdict depends on analytic choices "
        "at the noise level. " + _threshold_sentence(data)
    )
    paras.append(
        f"**Low power.** The test has about {est['power_per_site_percent']} % power per site and "
        f"about {est['power_overall_percent']} % for an overall 'confirmed (protocol)' even if the "
        "relation is real (reviewers' estimate). 'Not confirmed' at a site therefore means not "
        "shown, not refuted."
    )
    for s in sites:
        net, r, sens = s["net_displacement_m"], s["report"], s["sensitivity"]
        if (
            net is not None
            and r["verdict"] == "not_confirmed"
            and abs(net) < UNINFORMATIVE_PIXELS * RESOLUTION_M
        ):
            paras.append(
                f"**{s['id']} is uninformative.** The shoreline moved about {abs(net):.0f} m "
                f"(about {abs(net) / RESOLUTION_M:.1f} Landsat pixels) in "
                f"{meta['period'][1] - meta['period'][0]} years. r_diff is {_f(r['r_diff'])}; "
                f"without the 2020-2021 pairs it is {_f(sens['r_diff_without_2020_2021_pairs'])}. "
                "The negative value follows one 2020 artefact. This is a limitation of 30 m "
                "imagery "
                "on a steeper coast (compared with the deltaic sites), not evidence against the "
                "physical relation."
            )
    paras.append(
        "**Always stated with the results.** "
        f"{_list_or(sorted(str(y) for y in {s['report']['n_years'] for s in sites}))} annual "
        "values and "
        f"{_list_or(sorted(str(y) for y in {s['report']['n_pairs'] for s in sites}))} "
        "adjacent-year pairs "
        "per site (2003 is missing). At gizilaghaj the last years are right-censored: profiles "
        "whose "
        "shoreline lies beyond the profile end are NaN, and the set of valid profiles changes "
        f"(composition effect about +-{est['composition_effect_m']} m, reviewers' estimate). "
        "Exposed "
        "area is counted over the whole site grid; the profile-strip value is shown too. The "
        "Sentinel-2 check compares two optical sensors under one method, so it shows consistency, "
        "not ground-truth accuracy" + _preliminary_text(sites) + ". r_diff = "
        "Pearson(dD, -dL) with an iid bootstrap interval. " + meta["literature_note"]
    )
    return [x for p in paras for x in (p, "")][:-1]


NOISE_FLOOR_TEXT = {
    "gizilaghaj": "The annual noise floor of this site is estimated in review at 100-200 m "
    "(see qc.md).",
}


def _preliminary_text(sites: Sequence[Mapping[str, Any]]) -> str:
    names = [s["id"] for s in sites if s["qc"].get("preliminary")]
    return (
        f" (preliminary for {', '.join(names)}: fewer than the full set of years)" if names else ""
    )


def _list_or(items: Sequence[str]) -> str:
    return " / ".join(items) if items else "n/a"


def _limitation_lines(data: Mapping[str, Any]) -> list[str]:
    lines = [
        "- Three sites only; one site passed. The verdict structure (one of three) is a weak basis "
        "for any general statement.",
        "- Shoreline noise at Landsat 30 m resolution is comparable to the annual change at two "
        "sites.",
        "- Site geometry caveats from the site selection are carried with each site:",
    ]
    for s in data["sites"]:
        lines.append(f"  - {s['id']}: {s['caveats'] or 'none'}")
    lines.append(
        "- Sea level is a single basin-wide altimetry series; local effects are not modelled."
    )
    return lines
