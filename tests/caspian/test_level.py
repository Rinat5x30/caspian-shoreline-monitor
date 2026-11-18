import logging
from pathlib import Path

import pandas as pd
import pytest

from caspianwatch.level import (
    GATE_MIN_YEARS,
    InsufficientLevelData,
    check_gate,
    detect_jumps,
    parse_grealm,
    season_level,
    usable_years,
)

HEADER = """\
TPJOJS.2.5                 : Data Processing Version ID
000270 Caspian Sea         : Target database id number and name
  41.970   50.385          : Latitude and longitude (degrees East) of target mid-point
 -0.20                     : Jason-3 Bias (wrt Jason-2, in meters), [adjusted]
c
c   !! CONVERSION FACTORS !!
c     EGM2008   = -26.58 m  std  = 0.15 m
c
Column 1: Satellite mission name
Column 6: Target height variation with respect to Jason-2 reference pass mean level (meters)
"""


def _row(mission: str, cycle: int, ymd: str, level: float | None) -> str:
    h = "999.99" if level is None else f"{level:7.2f}"
    return f"{mission:<5} {cycle:>3} {ymd}  10  4 {h} 0.042  12.21 AMR GIM ECM 1 1 0  9999.99   0"


def _text(rows: list[str], header: str = HEADER) -> str:
    return header + "\n".join(rows) + "\n"


def _frame(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {"date": pd.to_datetime([d for d, _ in rows]), "level_m": [v for _, v in rows]}
    )


def test_parse_grealm_returns_sorted_dates_and_levels() -> None:
    text = _text(
        [
            _row("JASN2", 2, "20160112", -0.91),
            _row("TOPEX", 1, "19921006", -0.01),
            _row("JASN2", 1, "20160102", -0.82),
        ]
    )
    frame, stats = parse_grealm(text)
    assert list(frame["date"]) == sorted(frame["date"])
    assert frame["date"].dtype == "datetime64[ns]"
    assert frame["level_m"].tolist() == [-0.01, -0.82, -0.91]
    assert stats.parsed == 3 and stats.unparseable == 0


def test_parse_grealm_counts_placeholder_and_bad_lines(caplog: pytest.LogCaptureFixture) -> None:
    text = _text(
        [
            _row("JASN2", 1, "20160102", -0.82),
            _row("JASN2", 2, "20160112", None),
            _row("JASN2", 6, "99999999", None),
            "JASN2 3 garbage line",
            _row("JASN2", 4, "20160132", -0.5),
            _row("JASN2", 5, "20160202", float("nan")),
        ]
    )
    with caplog.at_level(logging.WARNING):
        frame, stats = parse_grealm(text)
    assert len(frame) == 1
    assert (stats.parsed, stats.no_data, stats.unparseable, stats.nan) == (1, 2, 2, 1)
    assert "unparseable" in caplog.text


def test_season_level_is_nan_below_min_obs() -> None:
    levels = _frame([("2010-07-05", 1.0), ("2010-08-05", 2.0), ("2010-12-05", 9.0)])
    assert pd.isna(season_level(levels, 2010, (7, 10)))


def test_season_level_mean_of_window_only() -> None:
    levels = _frame(
        [
            ("2010-06-30", 100.0),
            ("2010-07-05", 1.0),
            ("2010-08-05", 2.0),
            ("2010-10-31", 3.0),
            ("2010-11-01", 100.0),
            ("2011-08-05", 100.0),
        ]
    )
    assert season_level(levels, 2010, (7, 10)) == pytest.approx(2.0)


def test_usable_years_lists_years_with_enough_observations() -> None:
    rows = [(f"{y}-{m:02d}-10", 0.0) for y in (2001, 2003) for m in (7, 8, 9)]
    rows.append(("2002-07-10", 0.0))
    assert usable_years(_frame(rows), range(2000, 2005), (7, 10)) == [2001, 2003]


def test_check_gate_raises_below_15_years() -> None:
    assert GATE_MIN_YEARS == 15
    with pytest.raises(InsufficientLevelData):
        check_gate(list(range(2000, 2014)))
    check_gate(list(range(2000, 2015)))


def test_detect_jumps_flags_step_between_missions() -> None:
    rows = [(f"2015-{m:02d}-10", -0.8 + 0.01 * m) for m in range(1, 7)]
    rows += [(f"2016-{m:02d}-10", -0.2 + 0.01 * m) for m in range(1, 4)]
    jumps = detect_jumps(_frame(rows), threshold_m=0.3)
    assert jumps == [pd.Timestamp("2016-01-10")]


def test_get_with_retry_retries_5xx_but_not_4xx() -> None:
    from caspianwatch.cache import get_with_retry
    from wheatwatch.climate import HttpStatusError

    calls: list[int] = []
    sleeps: list[float] = []

    def flaky(url: str) -> bytes:
        calls.append(1)
        if len(calls) < 3:
            raise HttpStatusError(503)
        return b"ok"

    assert get_with_retry("u", fetch=flaky, sleep=sleeps.append) == b"ok"
    assert len(calls) == 3 and sleeps == [2.0, 4.0]

    def missing(url: str) -> bytes:
        raise HttpStatusError(404)

    with pytest.raises(HttpStatusError):
        get_with_retry("u", fetch=missing, sleep=sleeps.append)


def test_atomic_write_leaves_no_part_file(tmp_path: Path) -> None:
    from caspianwatch.cache import atomic_write

    target = tmp_path / "sub" / "f.txt"
    atomic_write(target, b"abc")
    assert target.read_bytes() == b"abc"
    assert not list(target.parent.glob("*.part"))
