import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from wheatwatch.regions import CACHE_DIR, _atomic_write

log = logging.getLogger(__name__)

API_URL = "https://archive-api.open-meteo.com/v1/archive"
SEASON_START = (3, 1)
SEASON_END = (6, 30)
MAX_ATTEMPTS = 6
BACKOFF_BASE_S = 2.0
REGION_PAUSE_S = 1.0
_COLUMNS = ["region_id", "year", "precip_mm", "tmax_mean_c"]

Fetch = Callable[[str], dict[str, Any]]


class HttpStatusError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


def _http_get_json(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data: dict[str, Any] = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise HttpStatusError(exc.code) from exc
    return data


def _get_with_retry(url: str, fetch: Fetch, sleep: Callable[[float], None]) -> dict[str, Any]:
    for attempt in range(MAX_ATTEMPTS):
        try:
            return fetch(url)
        except HttpStatusError as exc:
            retryable = exc.status == 429 or exc.status >= 500
            if not retryable or attempt == MAX_ATTEMPTS - 1:
                raise
            sleep(BACKOFF_BASE_S * 2**attempt)
        except OSError:
            if attempt == MAX_ATTEMPTS - 1:
                raise
            sleep(BACKOFF_BASE_S * 2**attempt)
    raise AssertionError("unreachable")


def _url(lat: float, lon: float, first: int, last: int) -> str:
    return (
        f"{API_URL}?latitude={lat:.4f}&longitude={lon:.4f}"
        f"&start_date={first}-01-01&end_date={last}-12-31"
        "&daily=precipitation_sum,temperature_2m_max&timezone=GMT"
    )


def _window(year: int) -> tuple[str, str]:
    start = date(year, *SEASON_START).isoformat()
    end = date(year, *SEASON_END).isoformat()
    return start, end


def _season_stats(daily: dict[str, Any], year: int) -> tuple[float, float, int]:
    times = daily["time"]
    precip = daily["precipitation_sum"]
    tmax = daily["temperature_2m_max"]
    if not len(times) == len(precip) == len(tmax):
        raise ValueError("Open-Meteo response arrays differ in length")
    start, end = _window(year)
    expected = (date(year, *SEASON_END) - date(year, *SEASON_START)).days + 1
    rows = [(p, t) for d, p, t in zip(times, precip, tmax, strict=True) if start <= d <= end]
    complete = [(p, t) for p, t in rows if p is not None and t is not None]
    missing = expected - len(complete)
    if missing:
        return float("nan"), float("nan"), missing
    return (
        float(sum(p for p, _ in complete)),
        float(sum(t for _, t in complete) / len(complete)),
        0,
    )


def _cache_path(cache_dir: Path, region_id: str, year: int) -> Path:
    return cache_dir / f"climate_{region_id}_{year}.json"


def _read_cache(path: Path, region_id: str, year: int) -> tuple[float, float] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["region_id"] != region_id or data["year"] != year:
            return None
        precip, tmax = data["precip_mm"], data["tmax_mean_c"]
        if precip is None or tmax is None:
            return float("nan"), float("nan")
        return float(precip), float(tmax)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(path: Path, region_id: str, year: int, precip: float, tmax: float) -> None:
    ok = precip == precip and tmax == tmax
    record = {
        "region_id": region_id,
        "year": year,
        "precip_mm": precip if ok else None,
        "tmax_mean_c": tmax if ok else None,
        "source": "open-meteo-era5",
    }

    def write(tmp: Path) -> None:
        tmp.write_text(json.dumps(record), encoding="utf-8")

    _atomic_write(path, write)


def season_climate(
    regions: gpd.GeoDataFrame,
    years: list[int],
    *,
    fetch: Fetch = _http_get_json,
    sleep: Callable[[float], None] = time.sleep,
    cache_dir: Path = CACHE_DIR,
) -> pd.DataFrame:
    rows: list[tuple[str, int, float, float]] = []
    fetched_any = False
    for region_id, geom in zip(regions["region_id"], regions.geometry, strict=True):
        found: dict[int, tuple[float, float]] = {}
        for year in years:
            cached = _read_cache(_cache_path(cache_dir, region_id, year), region_id, year)
            if cached is not None:
                found[year] = cached
        todo = [y for y in years if y not in found]
        if todo:
            if fetched_any:
                sleep(REGION_PAUSE_S)
            fetched_any = True
            point = geom.representative_point()
            payload = _get_with_retry(_url(point.y, point.x, min(todo), max(todo)), fetch, sleep)
            daily = payload["daily"]
            stats = {y: _season_stats(daily, y) for y in todo}
            for year, (precip, tmax, missing) in stats.items():
                if missing:
                    log.warning(
                        "region %s year %d: %d season day(s) missing, result is NaN",
                        region_id,
                        year,
                        missing,
                    )
                _write_cache(_cache_path(cache_dir, region_id, year), region_id, year, precip, tmax)
                found[year] = (precip, tmax)
        rows.extend((str(region_id), y, *found[y]) for y in years)
    frame = pd.DataFrame(rows, columns=_COLUMNS)
    return frame.astype({"year": "int64", "precip_mm": "float64", "tmax_mean_c": "float64"})
