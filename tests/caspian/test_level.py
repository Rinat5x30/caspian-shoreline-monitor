import hashlib
import logging
import re
from pathlib import Path

import pandas as pd
import pytest

from caspianwatch.level import (
    GATE_MIN_YEARS,
    GREALM_URL,
    RAW_PATH,
    InsufficientLevelData,
    check_gate,
    detect_jumps,
    load_grealm_caspian,
    mission_offsets,
    parse_grealm,
    season_level,
    usable_years,
)
from wheatwatch.climate import HttpStatusError

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


def test_season_level_averages_missions_per_date_and_counts_dates() -> None:
    two_dates = _frame([("2010-07-05", 1.0), ("2010-07-05", 3.0), ("2010-08-05", 2.0)])
    assert pd.isna(season_level(two_dates, 2010, (7, 10)))
    three = _frame(
        [("2010-07-05", 1.0), ("2010-07-05", 3.0), ("2010-08-05", 2.0), ("2010-09-05", 6.0)]
    )
    assert season_level(three, 2010, (7, 10)) == pytest.approx((2.0 + 2.0 + 6.0) / 3)


def test_detect_jumps_flags_step_between_missions() -> None:
    base = pd.Timestamp("2015-01-01")
    rows = [
        (str((base + pd.Timedelta(days=10 * k)).date()), -0.8 if k < 6 else -0.2) for k in range(10)
    ]
    assert detect_jumps(_frame(rows), threshold_m=0.3) == [pd.Timestamp("2015-03-02")]


def test_detect_jumps_ignores_gap_and_same_date_offsets() -> None:
    days = [0, 10, 20, 30, 40, 100, 110, 120, 130, 140]
    base = pd.Timestamp("2015-01-01")
    rows = [(str((base + pd.Timedelta(days=d)).date()), 0.001 * d) for d in days]
    rows += [(rows[2][0], rows[2][1] + 0.25), (rows[7][0], rows[7][1] - 0.25)]
    assert detect_jumps(_frame(rows), threshold_m=0.2) == []


def test_detect_jumps_flags_real_step_on_irregular_axis_once() -> None:
    days = [0, 10, 20, 30, 45, 50, 60, 75, 80, 90, 100]
    base = pd.Timestamp("2015-01-01")
    rows = [(str((base + pd.Timedelta(days=d)).date()), 0.0 if d < 60 else -0.4) for d in days]
    assert detect_jumps(_frame(rows), threshold_m=0.2) == [pd.Timestamp("2015-03-02")]


def test_detect_jumps_is_deterministic_on_ties() -> None:
    rows = [(f"2015-01-{d:02d}", 0.0) for d in (1, 11, 21)]
    rows += [("2015-02-10", -0.5), ("2015-02-10", -0.5), ("2015-02-20", -0.5), ("2015-03-02", -0.5)]
    frame = _frame(rows)
    assert detect_jumps(frame, 0.2) == detect_jumps(frame.iloc[::-1], 0.2)


def test_parse_keeps_mission_column() -> None:
    frame, _ = parse_grealm(_text([_row("JASN3", 1, "20170102", -0.8)]))
    assert frame["mission"].tolist() == ["JASN3"]


def _mframe(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime([r[0] for r in rows]),
            "mission": [r[1] for r in rows],
            "level_m": [r[2] for r in rows],
        }
    )


def test_mission_offsets_reports_same_date_and_transition() -> None:
    rows = [(f"2015-01-{d:02d}", "JASN2", 0.0) for d in (1, 11, 21, 31)]
    rows += [("2015-02-10", "JASN2", 0.0), ("2015-02-10", "JASN3", 0.3)]
    rows += [(f"2015-02-{d:02d}", "JASN3", 0.3) for d in (20, 28)]
    rows += [("2015-03-10", "JASN3", 0.3)]
    out = mission_offsets(_mframe(rows))
    same = out[out["kind"] == "same_date"].iloc[0]
    assert (same["mission_a"], same["mission_b"], same["n"]) == ("JASN2", "JASN3", 1)
    assert same["median_m"] == pytest.approx(0.3)
    trans = out[out["kind"] == "transition"].iloc[0]
    assert (trans["mission_a"], trans["mission_b"]) == ("JASN2", "JASN3")
    assert trans["median_m"] == pytest.approx(0.3)


def _good_text() -> str:
    rows = [
        _row("JASN2", i, f"2016{(i - 1) // 28 + 1:02d}{(i - 1) % 28 + 1:02d}", -0.5)
        for i in range(1, 121)
    ]
    return _text(rows)


def _no_fetch(url: str) -> bytes:
    raise AssertionError("must not fetch")


def test_load_uses_valid_cache_without_fetch(tmp_path: Path) -> None:
    path = tmp_path / "g.txt"
    path.write_text(_good_text(), encoding="ascii")
    assert len(load_grealm_caspian(path, fetch=_no_fetch)) == 120


def test_load_refetches_truncated_cache(tmp_path: Path) -> None:
    path = tmp_path / "g.txt"
    good = _good_text()
    path.write_text(good[: len(good) // 2], encoding="ascii")
    frame = load_grealm_caspian(path, fetch=lambda url: good.encode("ascii"))
    assert len(frame) == 120 and path.read_text(encoding="ascii") == good


def test_truncated_download_raises_and_keeps_previous_file(tmp_path: Path) -> None:
    path = tmp_path / "g.txt"
    old = _good_text() + "extra-line-not-valid" + chr(10)
    path.write_text(old, encoding="ascii")
    good = _good_text()
    with pytest.raises(ValueError):
        load_grealm_caspian(path, fetch=lambda url: good[:-40].encode("ascii"))
    assert path.read_text(encoding="ascii") == old
    assert not list(tmp_path.glob("*.part"))


def test_failed_fetch_keeps_previous_file_and_caches_nothing(tmp_path: Path) -> None:
    path = tmp_path / "g.txt"
    old = _good_text() + "extra-line-not-valid" + chr(10)
    path.write_text(old, encoding="ascii")

    def missing(url: str) -> bytes:
        raise HttpStatusError(404)

    with pytest.raises(HttpStatusError):
        load_grealm_caspian(path, fetch=missing)
    assert path.read_text(encoding="ascii") == old
    fresh = tmp_path / "fresh.txt"
    with pytest.raises(HttpStatusError):
        load_grealm_caspian(fresh, fetch=missing)
    assert not fresh.exists()


def test_default_path_is_committed_file_and_hash_matches_sources() -> None:
    assert RAW_PATH.name == "lake000270.10d.2.txt" and GREALM_URL.endswith(RAW_PATH.name)
    frame = load_grealm_caspian(fetch=_no_fetch)
    assert len(frame) > 1000
    sources = (RAW_PATH.parent / "SOURCES.md").read_text(encoding="utf-8")
    match = re.search(r"sha256: `([0-9a-f]{64})`", sources)
    assert match is not None
    assert hashlib.sha256(RAW_PATH.read_bytes()).hexdigest() == match.group(1)


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


def test_importing_caspianwatch_does_not_load_heavy_geo_stack() -> None:
    import subprocess
    import sys

    code = (
        "import sys, caspianwatch.level; "
        "print('rasterio' in sys.modules or 'geopandas' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={**__import__("os").environ, "PYTHONPATH": "src"},
    )
    assert out.stdout.strip() == "False"
