import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from caspianwatch.cache import atomic_write, get_with_retry

log = logging.getLogger(__name__)

GATE_MIN_YEARS: int = 15
GREALM_URL = "https://earth.gsfc.nasa.gov/gwm/timeseries/lake000270.10d.2.txt"
RAW_PATH = Path(__file__).resolve().parents[2] / "data" / "raw" / "caspian" / "lake000270.10d.2.txt"
MIN_ROWS_VALID = 100
JUMP_WINDOW_DAYS = 30
JUMP_MIN_OBS = 2
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
    missions: list[str] = []
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
            missions.append(fields[0])
            levels.append(level)
    frame = pd.DataFrame(
        {"date": pd.to_datetime(dates), "mission": missions, "level_m": levels}
    ).astype({"date": "datetime64[ns]", "mission": "str", "level_m": "float64"})
    frame = frame.sort_values(["date", "mission"], kind="stable").reset_index(drop=True)
    stats = ParseStats(len(frame), no_data, nan, unparseable)
    if nan or unparseable:
        log.warning("G-REALM parse: %s", stats)
    return frame, stats


def _text_valid(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    frame, stats = parse_grealm(text)
    return (
        "Data Processing Version ID" in text[:200]
        and len(frame) >= MIN_ROWS_VALID
        and stats.unparseable == 0
        and len(lines[-1].split()) == _N_FIELDS
    )


def _file_valid(path: Path) -> bool:
    try:
        return _text_valid(path.read_text(encoding="ascii"))
    except (OSError, UnicodeDecodeError):
        return False


def load_grealm_caspian(
    path: Path = RAW_PATH,
    *,
    fetch: Callable[[str], bytes] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    if not _file_valid(path):
        data = get_with_retry(GREALM_URL, fetch=fetch, sleep=sleep)
        try:
            valid = _text_valid(data.decode("ascii"))
        except UnicodeDecodeError:
            valid = False
        if not valid:
            raise ValueError("downloaded G-REALM file is truncated or unreadable")
        atomic_write(path, data)
    frame, stats = parse_grealm(path.read_text(encoding="ascii"))
    log.info("G-REALM Caspian: %s", stats)
    return frame


def _by_date(levels: pd.DataFrame) -> pd.Series:
    return levels.groupby("date")["level_m"].mean().sort_index()


def season_level(
    levels: pd.DataFrame, year: int, months: tuple[int, int], min_obs: int = 3
) -> float:
    dates = levels["date"]
    mask = (dates.dt.year == year) & dates.dt.month.between(months[0], months[1])
    per_date = _by_date(levels.loc[mask])
    return float(per_date.mean()) if len(per_date) >= min_obs else float("nan")


def usable_years(levels: pd.DataFrame, years: Iterable[int], months: tuple[int, int]) -> list[int]:
    return [y for y in years if not pd.isna(season_level(levels, y, months))]


def check_gate(usable: list[int]) -> None:
    if len(usable) < GATE_MIN_YEARS:
        raise InsufficientLevelData(
            f"{len(usable)} usable years, need at least {GATE_MIN_YEARS}: {usable}"
        )


def detect_jumps(levels: pd.DataFrame, threshold_m: float) -> list[pd.Timestamp]:
    """Step = |median of obs in [t, t+30d) - median of obs in [t-30d, t)| > threshold.

    Missions are averaged per date first; a window with fewer than JUMP_MIN_OBS dates
    (a data gap) cannot be assessed and is skipped. A step flags one date: the best mean split
    in a run.
    """
    series = _by_date(levels)
    days = (
        ((series.index - series.index[0]) / pd.Timedelta(days=1)).to_numpy() if len(series) else []
    )
    values = series.to_numpy()
    flagged: list[tuple[int, float]] = []
    for i in range(len(series)):
        before = values[(days >= days[i] - JUMP_WINDOW_DAYS) & (days < days[i])]
        after = values[(days >= days[i]) & (days < days[i] + JUMP_WINDOW_DAYS)]
        if len(before) < JUMP_MIN_OBS or len(after) < JUMP_MIN_OBS:
            continue
        if abs(float(np.median(after) - np.median(before))) > threshold_m:
            flagged.append((i, abs(float(after.mean() - before.mean()))))
    result: list[pd.Timestamp] = []
    run: list[tuple[int, float]] = []
    for item in [*flagged, (-10, 0.0)]:
        if run and item[0] != run[-1][0] + 1:
            result.append(pd.Timestamp(series.index[max(run, key=lambda r: r[1])[0]]))
            run = []
        run.append(item)
    return result


def mission_offsets(levels: pd.DataFrame) -> pd.DataFrame:
    """Inter-mission offsets (level of mission_b minus mission_a, metres).

    same_date: pairs of missions observing on the same date (n, median, max abs).
    transition: for each mission after the first (by first date), median of its first 3
    observations minus the median of the last 3 dates before it started (other missions).
    """
    rows: list[dict[str, object]] = []
    wide = levels.pivot_table(index="date", columns="mission", values="level_m", aggfunc="mean")
    for a, b in combinations(sorted(wide.columns), 2):
        diff = (wide[b] - wide[a]).dropna()
        if len(diff):
            rows.append(_offset_row("same_date", a, b, diff.to_numpy()))
    firsts = levels.groupby("mission")["date"].min().sort_values(kind="stable")
    for mission, start in list(firsts.items())[1:]:
        own = levels[levels["mission"] == mission].sort_values("date")
        after = own[own["date"] >= start].head(3)["level_m"].to_numpy()
        prior = levels[(levels["date"] < start) & (levels["mission"] != mission)]
        prior_dates = _by_date(prior).tail(3)
        if len(after) < 3 or len(prior_dates) < 3:
            continue
        previous = prior[prior["date"] == prior_dates.index[-1]]["mission"].iloc[0]
        step = np.array([np.median(after) - np.median(prior_dates.to_numpy())])
        rows.append(_offset_row("transition", str(previous), str(mission), step))
    return pd.DataFrame(
        rows, columns=["kind", "mission_a", "mission_b", "n", "median_m", "max_abs_m"]
    )


def _offset_row(kind: str, a: str, b: str, diffs: np.ndarray) -> dict[str, object]:
    return {
        "kind": kind,
        "mission_a": a,
        "mission_b": b,
        "n": len(diffs),
        "median_m": float(np.median(diffs)),
        "max_abs_m": float(np.abs(diffs).max()),
    }
