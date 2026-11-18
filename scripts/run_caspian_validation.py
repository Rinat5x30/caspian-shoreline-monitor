"""Single pre-registered run of the shoreline-versus-sea-level validation (cache only, no network).

Protocol: docs/caspian/validation-protocol.md. Output: docs/caspian/validation-run.txt.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from caspianwatch import validate  # noqa: E402
from caspianwatch.landsat import WaterMask, season_water_mask  # noqa: E402
from caspianwatch.level import load_grealm_caspian, season_level  # noqa: E402
from caspianwatch.season import SEASON_PATH, Season  # noqa: E402
from caspianwatch.shoreline import (  # noqa: E402
    SiteDisplacement,
    is_measurable,
    site_displacement_detail,
)
from caspianwatch.sites import Site, load_sites  # noqa: E402
from caspianwatch.validate import SiteReport, validate_site  # noqa: E402

OUT_PATH = ROOT / "docs" / "caspian" / "validation-run.txt"
THRESHOLDS = (0.0, 0.1, 0.2)
CENSORED_SITE = "gizilaghaj"
CENSORED_LAST_YEAR = 2023
SEED = 0

Masks = Mapping[int, WaterMask | None]


def _no_network(*args: object, **kwargs: object) -> Any:
    raise RuntimeError("network access is not allowed in the validation run")


def _no_scenes(*args: object, **kwargs: object) -> list[Any]:
    return []


def num(x: float, digits: int = 6) -> str:
    return "nan" if math.isnan(x) else f"{x:.{digits}f}"


def load_masks(site: Site, season: Season, years: list[int]) -> dict[int, WaterMask | None]:
    return {
        y: season_water_mask(
            site, y, season, search=_no_scenes, read=_no_network, sleep=lambda _: None
        )
        for y in years
    }


def report_lines(r: SiteReport) -> list[str]:
    return [
        f"site_id={r.site_id}",
        f"verdict={r.verdict}",
        f"reason={r.reason}",
        f"n_years={r.n_years}  n_pairs={r.n_pairs}",
        f"rmse_model={num(r.rmse_model)}  rmse_baseline={num(r.rmse_baseline)}",
        f"r_diff={num(r.r_diff)}  r_diff_ci_low={num(r.r_diff_ci_low)}  ci_high={num(r.ci_high)}",
        f"degenerate_share={num(r.degenerate_share)}  raw_r={num(r.raw_r)}  "
        f"slope_b={num(r.slope_b, 8)}",
    ]


def series_table(d: pd.Series, level: pd.Series) -> list[str]:
    frame = pd.DataFrame({"D_m": d, "L_m": level})
    dd = pd.Series(
        {
            y: d[y] - d[y - 1]
            for y in d.index
            if y - 1 in d.index and not (math.isnan(d[y]) or math.isnan(d[y - 1]))
        }
    )
    dl = pd.Series(
        {
            y: level[y] - level[y - 1]
            for y in level.index
            if y - 1 in level.index and not (math.isnan(level[y]) or math.isnan(level[y - 1]))
        }
    )
    lines = ["year  D_m  L_m  dD_m  dL_m  (differences only between adjacent years)"]
    for y, row in frame.iterrows():
        lines.append(
            f"{y}  {num(row['D_m'], 3)}  {num(row['L_m'], 4)}  "
            f"{num(float(dd.get(y, math.nan)), 3)}  {num(float(dl.get(y, math.nan)), 4)}"
        )
    return lines


def brief(r: SiteReport) -> str:
    return (
        f"{r.verdict}  rmse_model={num(r.rmse_model, 4)} rmse_baseline={num(r.rmse_baseline, 4)} "
        f"r_diff={num(r.r_diff, 4)} ci_low={num(r.r_diff_ci_low, 4)} n_years={r.n_years}"
    )


def always_present(detail: SiteDisplacement) -> list[int]:
    pos = detail.positions
    usable = pos.index[pos.notna().any(axis=1)]
    full = pos.loc[usable].notna().all()
    return [int(str(c)) for c in full.index[full]]


def build_text(
    levels: pd.DataFrame,
    sites: list[Site],
    masks_by_site: Mapping[str, Masks],
    months: tuple[int, int],
    period: list[int],
) -> str:
    level_series = pd.Series(
        {y: season_level(levels, y, months, min_obs=3) for y in period}, dtype=float
    )
    level_series.index.name = "year"
    out = [
        "Caspian shoreline vs sea level: pre-registered validation run",
        "protocol=docs/caspian/validation-protocol.md  "
        f"season={months}  period={period[0]}-{period[-1]}",
        f"R_DIFF_MIN={validate.R_DIFF_MIN}  N_BOOT={validate.N_BOOT}  "
        f"MIN_COMMON_YEARS={validate.MIN_COMMON_YEARS}  seed={SEED}",
        "D = unweighted site displacement (m, positive = retreat of the sea); "
        "L = season mean G-REALM level (m, relative)",
        "",
    ]
    for site in sites:
        none_years = [y for y, m in masks_by_site[site.site_id].items() if m is None]
        out.append(f"years without a cached mask (treated as no mask) {site.site_id}: {none_years}")
    out.append("")
    reports: list[SiteReport] = []
    details: dict[str, SiteDisplacement] = {}
    for site in sites:
        detail = site_displacement_detail(site, masks_by_site[site.site_id])
        details[site.site_id] = detail
        measurable = is_measurable(detail.series, period)
        report = validate_site(
            detail.series, level_series, site.site_id, measurable=measurable, seed=SEED
        )
        reports.append(report)
        out += [f"== PRIMARY {site.site_id}  measurable={measurable}"]
        out += report_lines(report) + [""] + series_table(detail.series, level_series) + [""]
    overall = validate.overall_verdict(reports)
    out += [f"== OVERALL VERDICT: {overall}", ""]
    out += ["== SECONDARY (reported next to the verdict, never altering it)", ""]
    for site, report in zip(sites, reports, strict=True):
        sid = site.site_id
        detail, masks = details[sid], masks_by_site[sid]
        out.append(f"-- {sid}")
        out.append(
            f"raw Pearson r(D, -L) = {num(report.raw_r)}  "
            "(NOTE: both series trend; this is not evidence of coupling)"
        )
        weighted = validate_site(
            detail.weighted_series,
            level_series,
            sid,
            measurable=is_measurable(detail.weighted_series, period),
            seed=SEED,
        )
        out.append(f"weighted displacement: {brief(weighted)}")
        fixed = always_present(detail)
        if fixed:
            fseries = site_displacement_detail(site, masks, fixed_profiles=fixed).series
            fixed_report = validate_site(
                fseries, level_series, sid, measurable=is_measurable(fseries, period), seed=SEED
            )
            out.append(f"always-present profiles ({len(fixed)}): {brief(fixed_report)}")
        else:
            out.append("always-present profiles: none")
        for t in THRESHOLDS:
            masks_t = {
                y: None if m is None else validate.with_threshold(m, t) for y, m in masks.items()
            }
            tseries = site_displacement_detail(site, masks_t).series
            tr = validate_site(
                tseries, level_series, sid, measurable=is_measurable(tseries, period), seed=SEED
            )
            out.append(f"fixed MNDWI threshold {t}: {brief(tr)}")
        pairs = validate.paired_differences(detail.series, level_series)
        low, high, share = validate.block_bootstrap_ci(
            pairs["dd"].to_numpy(), -pairs["dl"].to_numpy(), SEED
        )
        out.append(
            f"block bootstrap (length {validate.BLOCK_LEN}) r_diff CI: "
            f"[{num(low)}, {num(high)}] degenerate_share={num(share)}"
        )
        if sid == CENSORED_SITE:
            cut = detail.series.loc[:CENSORED_LAST_YEAR]
            cut_period = [y for y in period if y <= CENSORED_LAST_YEAR]
            cr = validate_site(
                cut, level_series, sid, measurable=is_measurable(cut, cut_period), seed=SEED
            )
            out.append(f"{sid} excluding years after {CENSORED_LAST_YEAR}: {brief(cr)}")
        if report.verdict == "confirmed":
            out.append(f"retreat per 1 cm of level fall = {num(0.01 / abs(report.slope_b), 4)} m")
        out.append("")
    return "\n".join(out) + "\n"


def main() -> None:
    chosen = json.loads(SEASON_PATH.read_text(encoding="utf-8"))
    season = Season(chosen["chosen"]["start_month"], chosen["chosen"]["end_month"])
    period = [int(y) for y in chosen["period"]["years"]]
    sites = load_sites()
    masks = {s.site_id: load_masks(s, season, period) for s in sites}
    text = build_text(
        load_grealm_caspian(fetch=_no_network),
        sites,
        masks,
        (season.start_month, season.end_month),
        period,
    )
    OUT_PATH.write_bytes(text.encode("utf-8"))
    sys.stdout.write(text)
    print(f"sha256={hashlib.sha256(text.encode('utf-8')).hexdigest()}")


def dry_run(level_builder: Callable[[], pd.DataFrame], out: Path) -> None:
    chosen = json.loads(SEASON_PATH.read_text(encoding="utf-8"))
    season = Season(chosen["chosen"]["start_month"], chosen["chosen"]["end_month"])
    period = [int(y) for y in chosen["period"]["years"]]
    sites = load_sites()
    masks = {s.site_id: load_masks(s, season, period) for s in sites}
    text = build_text(level_builder(), sites, masks, (season.start_month, season.end_month), period)
    out.write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
