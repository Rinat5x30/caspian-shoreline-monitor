"""Season choice for the Caspian shoreline series from Landsat scene cloud metadata only.

Pre-registered rules (recorded in data/caspian/season.json):
- candidate seasons: the two-month windows (7,8), (8,9), (9,10);
- per year and window, cloud_pct = mean of eo:cloud_cover over the scenes of the window
  (a scene without the property is dropped and counted);
- the chosen window has the lowest median over years of that value; ties go to the earlier one;
- a scene is usable if eo:cloud_cover <= USABLE_SCENE_MAX_CLOUD;
- a year is in the period if the season has at least MIN_LANDSAT_DATES distinct usable Landsat
  dates and at least MIN_GREALM_DATES G-REALM dates; the period is the longest consecutive run.
Only metadata is read: no pixels, shorelines or level-vs-shore relations are used here.
"""

import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from functools import partial
from pathlib import Path
from typing import Any

import pandas as pd

from caspianwatch.cache import atomic_write
from caspianwatch.level import GATE_MIN_YEARS, check_gate, load_grealm_caspian, usable_years

log = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]
Sleep = Callable[[float], None]

COLLECTION = "landsat-c2-l2"
SOURCE = "planetary-computer-landsat-c2-l2"
CLOUD_PROPERTY = "eo:cloud_cover"
CANDIDATE_WINDOWS: tuple[tuple[int, int], ...] = ((7, 8), (8, 9), (9, 10))
SEARCH_MONTHS = (7, 10)
USABLE_SCENE_MAX_CLOUD = 50.0
MIN_LANDSAT_DATES = 3
MIN_GREALM_DATES = 3
YEARS = tuple(range(2000, 2026))
STUDY_BBOX: BBox = (48.5, 38.3, 50.6, 41.9)
BBOX_JUSTIFICATION = (
    "Azerbaijan Caspian coastal strip: from the Samur river mouth (about 41.9 N, 48.6 E) to the "
    "Astara border (about 38.4 N, 48.9 E), east to the tip of the Absheron peninsula "
    "(about 50.4 E). Metadata search only: scenes whose footprint intersects the box are counted "
    "and eo:cloud_cover is a whole-scene value; the box includes some sea and inland area. "
    "Study sites are chosen later, in another task."
)
ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "raw" / "cache" / "caspian"
SEASON_PATH = ROOT / "data" / "caspian" / "season.json"
_CLOUD_COLUMNS = ["year", "month", "scene_count", "cloud_pct"]


@dataclass(frozen=True)
class Season:
    start_month: int
    end_month: int


@dataclass(frozen=True)
class SceneRecord:
    day: date
    cloud_cover: float | None
    platform: str


Search = Callable[[BBox, int], list[SceneRecord]]


def season_months(season: Season) -> tuple[int, int]:
    return season.start_month, season.end_month


def _window(year: int) -> tuple[str, str]:
    return date(year, SEARCH_MONTHS[0], 1).isoformat(), date(year, SEARCH_MONTHS[1], 31).isoformat()


def _settings(bbox: BBox, year: int) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "collection": COLLECTION,
        "bbox": list(bbox),
        "year": year,
        "window": list(_window(year)),
        "query": "all items in bbox and window, no server-side cloud filter",
        "cloud_property": CLOUD_PROPERTY,
    }


def scenes_from_items(items: list[Any]) -> list[SceneRecord]:
    scenes: list[SceneRecord] = []
    for item in items:
        props = item.properties
        stamp = props.get("datetime") or props.get("start_datetime")
        if stamp is None:
            raise ValueError("STAC item has no date")
        raw = props.get(CLOUD_PROPERTY)
        scenes.append(
            SceneRecord(
                date.fromisoformat(str(stamp)[:10]),
                None if raw is None else float(raw),
                str(props.get("platform", "unknown")),
            )
        )
    return scenes


def _search_scenes(bbox: BBox, year: int) -> list[SceneRecord]:
    import planetary_computer
    import pystac_client
    from pystac_client.exceptions import APIError

    from wheatwatch.climate import HttpStatusError
    from wheatwatch.regions import STAC_URL

    start, end = _window(year)
    try:
        catalog = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
        items = catalog.search(
            collections=[COLLECTION], bbox=list(bbox), datetime=f"{start}/{end}"
        ).item_collection()
    except APIError as exc:
        if exc.status_code is None:
            raise
        raise HttpStatusError(exc.status_code) from exc
    return scenes_from_items(list(items))


def _read_cache(path: Path, settings: dict[str, Any]) -> list[SceneRecord] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["settings"] != json.loads(json.dumps(settings)):
            return None
        records = [
            SceneRecord(date.fromisoformat(d), None if c is None else float(c), str(p))
            for d, c, p in data["records"]
        ]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return records or None


def _write_cache(path: Path, settings: dict[str, Any], records: list[SceneRecord]) -> None:
    payload = {
        "settings": settings,
        "records": [[r.day.isoformat(), r.cloud_cover, r.platform] for r in records],
    }
    atomic_write(path, json.dumps(payload).encode("utf-8"))


def _year_records(
    bbox: BBox, year: int, search: Search, sleep: Sleep, cache_dir: Path
) -> list[SceneRecord]:
    from wheatwatch.ndvi import _retry

    path = cache_dir / f"scenes_{year}.json"
    settings = _settings(bbox, year)
    cached = _read_cache(path, settings)
    if cached is not None:
        return cached
    records = _retry(partial(search, bbox, year), sleep)
    if not records:
        raise ValueError(f"no Landsat scenes for {year} in bbox {bbox}")
    _write_cache(path, settings, records)
    return records


def fetch_scene_table(
    bbox: BBox,
    years: Iterable[int],
    *,
    search: Search = _search_scenes,
    sleep: Sleep = time.sleep,
    cache_dir: Path = CACHE_DIR,
) -> pd.DataFrame:
    """One row per scene with eo:cloud_cover: date, cloud_cover, platform."""
    rows: list[SceneRecord] = []
    for year in years:
        rows.extend(_year_records(bbox, year, search, sleep, cache_dir))
    kept = [r for r in rows if r.cloud_cover is not None]
    if len(kept) < len(rows):
        log.warning(
            "%d scene(s) without %s dropped out of %d",
            len(rows) - len(kept),
            CLOUD_PROPERTY,
            len(rows),
        )
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime([r.day for r in kept]),
            "cloud_cover": [float(r.cloud_cover) for r in kept if r.cloud_cover is not None],
            "platform": [r.platform for r in kept],
        }
    )
    return frame.sort_values(["date", "platform"], kind="stable").reset_index(drop=True)


def cloud_table_from_scenes(scenes: pd.DataFrame) -> pd.DataFrame:
    keys = [scenes["date"].dt.year.rename("year"), scenes["date"].dt.month.rename("month")]
    grouped = scenes.groupby(keys)["cloud_cover"]
    table = grouped.agg(["size", "mean"]).reset_index()
    table.columns = pd.Index(_CLOUD_COLUMNS)
    return table.astype({"year": "int64", "month": "int64", "scene_count": "int64"})


def fetch_cloud_table(
    bbox: BBox,
    years: Iterable[int],
    *,
    search: Search = _search_scenes,
    sleep: Sleep = time.sleep,
    cache_dir: Path = CACHE_DIR,
) -> pd.DataFrame:
    scenes = fetch_scene_table(bbox, years, search=search, sleep=sleep, cache_dir=cache_dir)
    return cloud_table_from_scenes(scenes)


def _checked(cloud: pd.DataFrame) -> pd.DataFrame:
    if not set(_CLOUD_COLUMNS) <= set(cloud.columns):
        raise ValueError(f"cloud table needs columns {_CLOUD_COLUMNS}")
    if cloud.empty:
        raise ValueError("empty cloud table")
    table = cloud[_CLOUD_COLUMNS]
    table = table[table["month"].between(*SEARCH_MONTHS) & (table["scene_count"] > 0)]
    if table.empty:
        raise ValueError("cloud table has no scenes in Jul-Oct")
    if table["cloud_pct"].isna().any():
        raise ValueError("cloud table has NaN cloud_pct for months with scenes")
    return table


def window_cloud(cloud: pd.DataFrame) -> pd.DataFrame:
    """Scene-weighted cloud mean per candidate window and year."""
    table = _checked(cloud)
    rows: list[dict[str, Any]] = []
    for start, end in CANDIDATE_WINDOWS:
        sub = table[table["month"].isin([start, end])]
        for year, group in sub.groupby("year"):
            n = int(group["scene_count"].sum())
            weighted = float((group["cloud_pct"] * group["scene_count"]).sum() / n)
            rows.append(
                {
                    "year": int(str(year)),
                    "start_month": start,
                    "end_month": end,
                    "scene_count": n,
                    "cloud_pct": weighted,
                }
            )
    return pd.DataFrame(
        rows, columns=["year", "start_month", "end_month", "scene_count", "cloud_pct"]
    )


def window_medians(windows: pd.DataFrame) -> dict[tuple[int, int], float]:
    result: dict[tuple[int, int], float] = {}
    for start, end in CANDIDATE_WINDOWS:
        sub = windows[(windows["start_month"] == start) & (windows["end_month"] == end)]
        if len(sub):
            result[(start, end)] = float(sub["cloud_pct"].median())
    return result


def choose_season(cloud: pd.DataFrame) -> Season:
    medians = window_medians(window_cloud(cloud))
    best: tuple[int, int] | None = None
    for window in CANDIDATE_WINDOWS:
        if window in medians and (
            best is None or round(medians[window], 9) < round(medians[best], 9)
        ):
            best = window
    assert best is not None
    return Season(*best)


def _longest_run(years: list[int]) -> list[int]:
    best: list[int] = []
    run: list[int] = []
    for year in years:
        run = [*run, year] if run and year == run[-1] + 1 else [year]
        if len(run) > len(best):
            best = run
    return best


def compute_period(
    scenes: pd.DataFrame, levels: pd.DataFrame, season: Season, years: Iterable[int]
) -> dict[str, Any]:
    months = season_months(season)
    all_years = sorted(set(years))
    dates = scenes["date"]
    usable = scenes[
        dates.dt.month.between(*months) & (scenes["cloud_cover"] <= USABLE_SCENE_MAX_CLOUD)
    ]
    landsat = usable.groupby(usable["date"].dt.year)["date"].nunique()
    lvl = levels["date"]
    in_season = levels[lvl.dt.month.between(*months)]
    grealm = in_season.groupby(in_season["date"].dt.year)["date"].nunique()
    grealm_ok = set(usable_years(levels, all_years, months))
    per_year: list[dict[str, Any]] = []
    qualifying: list[int] = []
    for year in all_years:
        n_l = int(landsat.get(year, 0))
        n_g = int(grealm.get(year, 0))
        ok = n_l >= MIN_LANDSAT_DATES and year in grealm_ok
        per_year.append(
            {"year": year, "landsat_usable_dates": n_l, "grealm_dates": n_g, "qualifies": ok}
        )
        if ok:
            qualifying.append(year)
    run = _longest_run(qualifying)
    dropped: list[dict[str, Any]] = []
    for row in per_year:
        if row["year"] in run:
            continue
        reasons = []
        if row["landsat_usable_dates"] < MIN_LANDSAT_DATES:
            reasons.append(
                f"landsat usable dates {row['landsat_usable_dates']} < {MIN_LANDSAT_DATES}"
            )
        if row["year"] not in grealm_ok:
            reasons.append(f"grealm dates {row['grealm_dates']} < {MIN_GREALM_DATES}")
        if not reasons:
            reasons.append("not in the longest consecutive run")
        dropped.append({"year": row["year"], "reason": "; ".join(reasons)})
    return {
        "start": run[0] if run else None,
        "end": run[-1] if run else None,
        "years": run,
        "dropped": dropped,
        "min_landsat_dates": MIN_LANDSAT_DATES,
        "min_grealm_dates": MIN_GREALM_DATES,
        "per_year": per_year,
    }


def build_season_record(
    scenes: pd.DataFrame,
    levels: pd.DataFrame,
    years: Iterable[int],
    bbox: BBox,
    *,
    run_date: date,
) -> dict[str, Any]:
    years = list(years)
    cloud = cloud_table_from_scenes(scenes)
    windows = window_cloud(cloud)
    season = choose_season(cloud)
    period = compute_period(scenes, levels, season, years)
    check_gate(period["years"])
    per_year_scenes = scenes.groupby(scenes["date"].dt.year)["cloud_cover"].agg(["size"])
    return {
        "run_date": run_date.isoformat(),
        "rule": (
            "lowest median over years of the per-year window mean eo:cloud_cover; "
            "ties to the earlier window; windows (7,8) (8,9) (9,10)"
        ),
        "collection": COLLECTION,
        "usable_scene_max_cloud": USABLE_SCENE_MAX_CLOUD,
        "bbox": {"bbox": list(bbox), "justification": BBOX_JUSTIFICATION},
        "years_requested": years,
        "chosen": {"start_month": season.start_month, "end_month": season.end_month},
        "medians": {f"{s}-{e}": m for (s, e), m in window_medians(windows).items()},
        "gate_min_years": GATE_MIN_YEARS,
        "scene_count_per_year": {int(str(y)): int(n) for y, n in per_year_scenes["size"].items()},
        "cloud_table": json.loads(cloud.to_json(orient="records")),
        "per_window_year": json.loads(windows.to_json(orient="records")),
        "period": period,
    }


def main() -> None:
    levels = load_grealm_caspian()
    scenes = fetch_scene_table(STUDY_BBOX, YEARS)
    record = build_season_record(scenes, levels, YEARS, STUDY_BBOX, run_date=date.today())
    atomic_write(SEASON_PATH, (json.dumps(record, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({k: record[k] for k in ("chosen", "medians", "scene_count_per_year")}))
    print(json.dumps({k: record["period"][k] for k in ("start", "end", "years", "dropped")}))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
