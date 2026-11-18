import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from caspianwatch.cache import atomic_write, get_with_retry

log = logging.getLogger(__name__)

GATE_MIN_YEARS: int = 15
GREALM_URL = "https://earth.gsfc.nasa.gov/gwm/timeseries/lake000270.10d.2.txt"
RAW_PATH = Path(__file__).resolve().parents[2] / "data" / "raw" / "caspian" / "lake000270.10d.2.txt"
MIN_ROWS_VALID = 100
NO_DATA_LEVEL = 999.99
_N_FIELDS = 16
_DATA_LINE = re.compile(r"^[A-Z0-9]{5}\s+\d+\s+\d{8}\s")


class InsufficientLevelData(Exception):
    pass


@dataclass(frozen=True)
class ParseStats:
    parsed: int
    no_data: int
    nan: int
    unparseable: int


def _is_header(line: str) -> bool:
    return line == "c" or line.startswith(("c ", "Column ")) or " : " in line


def parse_grealm(text: str) -> tuple[pd.DataFrame, ParseStats]:
    dates: list[pd.Timestamp] = []
    levels: list[float] = []
    no_data = nan = unparseable = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _is_header(line):
            continue
        fields = line.split()
        try:
            if len(fields) != _N_FIELDS or not _DATA_LINE.match(line):
                raise ValueError("unexpected layout")
            level = float(fields[5])
            if level == NO_DATA_LEVEL:
                no_data += 1
                continue
            date = pd.to_datetime(fields[2], format="%Y%m%d")
        except ValueError:
            unparseable += 1
            log.warning("G-REALM: unparseable line skipped: %r", line[:120])
            continue
        if level != level:
            nan += 1
            log.warning("G-REALM: NaN height skipped: %r", line[:120])
        else:
            dates.append(date)
            levels.append(level)
    frame = pd.DataFrame({"date": pd.to_datetime(dates), "level_m": levels}).astype(
        {"date": "datetime64[ns]", "level_m": "float64"}
    )
    frame = frame.sort_values("date", kind="stable").reset_index(drop=True)
    stats = ParseStats(len(frame), no_data, nan, unparseable)
    if nan or unparseable:
        log.warning("G-REALM parse: %s", stats)
    return frame, stats


def _cache_valid(path: Path) -> bool:
    try:
        text = path.read_text(encoding="ascii")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        frame, stats = parse_grealm(text)
    except (OSError, UnicodeDecodeError):
        return False
    return (
        "Data Processing Version ID" in text[:200]
        and len(frame) >= MIN_ROWS_VALID
        and stats.unparseable == 0
        and len(lines[-1].split()) == _N_FIELDS
    )


def load_grealm_caspian(
    path: Path = RAW_PATH,
    *,
    fetch: Callable[[str], bytes] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    if not _cache_valid(path):
        path.unlink(missing_ok=True)
        data = get_with_retry(GREALM_URL, fetch=fetch, sleep=sleep)
        atomic_write(path, data)
        if not _cache_valid(path):
            path.unlink(missing_ok=True)
            raise ValueError("downloaded G-REALM file is truncated or unreadable")
    frame, stats = parse_grealm(path.read_text(encoding="ascii"))
    log.info("G-REALM Caspian: %s", stats)
    return frame


def season_level(
    levels: pd.DataFrame, year: int, months: tuple[int, int], min_obs: int = 3
) -> float:
    dates = levels["date"]
    mask = (dates.dt.year == year) & dates.dt.month.between(months[0], months[1])
    window = levels.loc[mask, "level_m"]
    return float(window.mean()) if len(window) >= min_obs else float("nan")


def usable_years(levels: pd.DataFrame, years: Iterable[int], months: tuple[int, int]) -> list[int]:
    return [y for y in years if not pd.isna(season_level(levels, y, months))]


def check_gate(usable: list[int]) -> None:
    if len(usable) < GATE_MIN_YEARS:
        raise InsufficientLevelData(
            f"{len(usable)} usable years, need at least {GATE_MIN_YEARS}: {usable}"
        )


def detect_jumps(levels: pd.DataFrame, threshold_m: float) -> list[pd.Timestamp]:
    steps = levels["level_m"].diff().abs()
    return [pd.Timestamp(d) for d in levels.loc[steps > threshold_m, "date"]]
