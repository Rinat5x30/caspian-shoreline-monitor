import inspect
import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from caspianwatch.level import InsufficientLevelData
from caspianwatch.season import (
    CANDIDATE_WINDOWS,
    USABLE_SCENE_MAX_CLOUD,
    BBox,
    SceneRecord,
    Season,
    build_season_record,
    choose_season,
    compute_period,
    fetch_cloud_table,
    fetch_scene_table,
    scenes_from_items,
    season_months,
    window_cloud,
)
from wheatwatch.climate import HttpStatusError

BBOX: BBox = (48.5, 38.3, 50.6, 41.9)


def _cloud(rows: list[tuple[int, int, int, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["year", "month", "scene_count", "cloud_pct"])


def _by_month(per_year: dict[int, tuple[float, float, float, float]]) -> pd.DataFrame:
    rows = [
        (year, month, 2, value)
        for year, values in per_year.items()
        for month, value in zip((7, 8, 9, 10), values, strict=True)
    ]
    return _cloud(rows)


def test_choose_season_picks_lowest_median_cloud_pair() -> None:
    # windows (7,8) (8,9) (9,10): 10 / 40 / 70 in every year
    table = _by_month({y: (10, 10, 70, 70) for y in range(2000, 2005)})
    assert choose_season(table) == Season(7, 8)
    table = _by_month({y: (90, 40, 40, 90) for y in range(2000, 2005)})
    assert choose_season(table) == Season(8, 9)
    table = _by_month({y: (90, 90, 5, 5) for y in range(2000, 2005)})
    assert choose_season(table) == Season(9, 10)


def test_choose_season_only_considers_jul_to_oct() -> None:
    rows = [(y, m, 3, 0.0) for y in range(2000, 2004) for m in (5, 6, 11, 12)]
    rows += [(y, m, 3, 50.0) for y in range(2000, 2004) for m in (7, 8, 9)]
    rows += [(y, 10, 3, 20.0) for y in range(2000, 2004)]
    season = choose_season(_cloud(rows))
    assert season == Season(9, 10)
    assert (season.start_month, season.end_month) in CANDIDATE_WINDOWS


def test_choose_season_ignores_everything_but_cloud() -> None:
    assert list(inspect.signature(choose_season).parameters) == ["cloud"]
    base = _by_month({y: (10, 10, 70, 70) for y in range(2000, 2005)})
    noisy = base.assign(level_m=range(len(base)), shore_m=[-1.0] * len(base), extra="x")
    assert choose_season(noisy) == choose_season(base)


def test_empty_table_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        choose_season(_cloud([]))
    with pytest.raises(ValueError, match="Jul"):
        choose_season(_cloud([(2000, 5, 4, 10.0), (2000, 11, 4, 10.0)]))
    with pytest.raises(ValueError, match="columns"):
        choose_season(pd.DataFrame({"year": [2000], "month": [7]}))


def test_tie_goes_to_earlier_window() -> None:
    table = _by_month({y: (30, 30, 30, 30) for y in range(2000, 2004)})
    assert choose_season(table) == Season(7, 8)
    table = _by_month({y: (80, 30, 30, 30) for y in range(2000, 2004)})
    assert choose_season(table) == Season(8, 9)


def test_median_over_years_is_robust_to_cloudy_outlier_years() -> None:
    # Hand computed. Years 2000-2002: Jul 5 Aug 15 Sep 25 Oct 35 -> windows 10 / 20 / 30.
    # Years 2003-2004: Jul 100 Aug 15 Sep 25 Oct 35 -> windows 57.5 / 20 / 30.
    # (7,8): median 10, mean 29; (8,9): median 20, mean 20. The mean would pick (8,9).
    normal = (5.0, 15.0, 25.0, 35.0)
    outlier = (100.0, 15.0, 25.0, 35.0)
    table = _by_month({2000: normal, 2001: normal, 2002: normal, 2003: outlier, 2004: outlier})
    wc = window_cloud(table)
    a = wc[(wc.start_month == 7)]["cloud_pct"]
    b = wc[(wc.start_month == 8)]["cloud_pct"]
    assert sorted(a) == [10.0, 10.0, 10.0, 57.5, 57.5]
    assert a.median() == 10.0 and b.median() == 20.0
    assert a.mean() == pytest.approx(29.0) and b.mean() == pytest.approx(20.0)
    assert choose_season(table) == Season(7, 8)


def test_window_cloud_is_scene_weighted() -> None:
    table = _cloud([(2000, 7, 1, 0.0), (2000, 8, 9, 100.0)])
    wc = window_cloud(table)
    row = wc.iloc[0]
    assert (row.start_month, row.end_month, row.scene_count) == (7, 8, 10)
    assert row.cloud_pct == pytest.approx(90.0)


def test_nan_cloud_in_table_is_not_silently_skipped() -> None:
    with pytest.raises(ValueError, match="NaN"):
        choose_season(_cloud([(2000, 7, 3, float("nan")), (2000, 8, 3, 10.0)]))


def test_season_months() -> None:
    assert season_months(Season(8, 9)) == (8, 9)


class _Item:
    def __init__(self, stamp: str | None, cloud: float | None, platform: str = "landsat-8") -> None:
        props: dict[str, object] = {"platform": platform}
        if stamp is not None:
            props["datetime"] = stamp
        if cloud is not None:
            props["eo:cloud_cover"] = cloud
        self.properties = props


def test_scenes_from_items_reads_cloud_date_platform() -> None:
    items = [_Item("2020-07-05T07:12:00Z", 12.5), _Item("2005-08-01T07:00:00Z", None, "landsat-5")]
    scenes = scenes_from_items(items)
    assert scenes == [
        SceneRecord(date(2020, 7, 5), 12.5, "landsat-8"),
        SceneRecord(date(2005, 8, 1), None, "landsat-5"),
    ]
    with pytest.raises(ValueError, match="no date"):
        scenes_from_items([_Item(None, 10.0)])


def _scene(y: int, m: int, d: int, cloud: float | None = 10.0) -> SceneRecord:
    return SceneRecord(date(y, m, d), cloud, "landsat-8")


class CountingSearch:
    def __init__(self, scenes: list[SceneRecord] | None = None) -> None:
        self.calls: list[tuple[BBox, int]] = []
        self.scenes = [_scene(2020, 7, 5)] if scenes is None else scenes

    def __call__(self, bbox: BBox, year: int) -> list[SceneRecord]:
        self.calls.append((bbox, year))
        return [
            SceneRecord(date(year, s.day.month, s.day.day), s.cloud_cover, s.platform)
            for s in self.scenes
        ]


def _noop(_: float) -> None:
    return None


def test_scene_without_cloud_property_is_dropped_and_counted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    search = CountingSearch(
        [_scene(2020, 7, 5, 20.0), _scene(2020, 7, 6, None), _scene(2020, 8, 9, 40.0)]
    )
    with caplog.at_level(logging.WARNING):
        table = fetch_scene_table(BBOX, [2020], search=search, sleep=_noop, cache_dir=tmp_path)
    assert len(table) == 2
    assert table["cloud_cover"].notna().all()
    assert "1 scene" in caplog.text and "eo:cloud_cover" in caplog.text
    cloud = fetch_cloud_table(BBOX, [2020], search=search, sleep=_noop, cache_dir=tmp_path)
    assert cloud.to_dict("records") == [
        {"year": 2020, "month": 7, "scene_count": 1, "cloud_pct": 20.0},
        {"year": 2020, "month": 8, "scene_count": 1, "cloud_pct": 40.0},
    ]


def test_cache_hit_avoids_search(tmp_path: Path) -> None:
    search = CountingSearch()
    first = fetch_scene_table(BBOX, [2020, 2021], search=search, sleep=_noop, cache_dir=tmp_path)
    assert len(search.calls) == 2

    def boom(bbox: BBox, year: int) -> list[SceneRecord]:
        raise AssertionError("search must not run on a cache hit")

    second = fetch_scene_table(BBOX, [2020, 2021], search=boom, sleep=_noop, cache_dir=tmp_path)
    pd.testing.assert_frame_equal(first, second)


def test_cache_invalidated_by_bbox_change(tmp_path: Path) -> None:
    search = CountingSearch()
    fetch_scene_table(BBOX, [2020], search=search, sleep=_noop, cache_dir=tmp_path)
    fetch_scene_table(BBOX, [2020], search=search, sleep=_noop, cache_dir=tmp_path)
    assert len(search.calls) == 1
    moved: BBox = (48.5, 38.3, 50.6, 42.0)
    fetch_scene_table(moved, [2020], search=search, sleep=_noop, cache_dir=tmp_path)
    assert len(search.calls) == 2


def test_unreadable_cache_record_is_a_miss(tmp_path: Path) -> None:
    search = CountingSearch()
    fetch_scene_table(BBOX, [2020], search=search, sleep=_noop, cache_dir=tmp_path)
    for path in tmp_path.glob("*.json"):
        path.write_text("{not json", encoding="utf-8")
    fetch_scene_table(BBOX, [2020], search=search, sleep=_noop, cache_dir=tmp_path)
    assert len(search.calls) == 2


def test_failed_search_raises_and_caches_nothing(tmp_path: Path) -> None:
    def failing(bbox: BBox, year: int) -> list[SceneRecord]:
        raise HttpStatusError(500)

    with pytest.raises(HttpStatusError):
        fetch_scene_table(BBOX, [2020], search=failing, sleep=_noop, cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_empty_search_raises_and_caches_nothing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no Landsat scenes"):
        fetch_scene_table(BBOX, [2020], search=lambda b, y: [], sleep=_noop, cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_retry_only_on_429_5xx_and_oserror(tmp_path: Path) -> None:
    attempts: list[int] = []

    def flaky(bbox: BBox, year: int) -> list[SceneRecord]:
        attempts.append(1)
        if len(attempts) == 1:
            raise HttpStatusError(429)
        if len(attempts) == 2:
            raise ConnectionResetError("reset")
        return [_scene(year, 7, 5)]

    table = fetch_scene_table(BBOX, [2020], search=flaky, sleep=_noop, cache_dir=tmp_path)
    assert len(attempts) == 3 and len(table) == 1

    calls: list[int] = []

    def not_found(bbox: BBox, year: int) -> list[SceneRecord]:
        calls.append(1)
        raise HttpStatusError(404)

    with pytest.raises(HttpStatusError):
        fetch_scene_table(BBOX, [2021], search=not_found, sleep=_noop, cache_dir=tmp_path)
    assert len(calls) == 1


def _grealm(per_year: dict[int, int]) -> pd.DataFrame:
    rows = [
        (pd.Timestamp(year, 8, 1 + 2 * i), "J3", 0.1 * i)
        for year, n in per_year.items()
        for i in range(n)
    ]
    return pd.DataFrame(rows, columns=["date", "mission", "level_m"])


def _scenes(per_year: dict[int, list[tuple[int, int, float]]]) -> pd.DataFrame:
    rows = [
        (pd.Timestamp(year, m, d), cloud, "landsat-8")
        for year, items in per_year.items()
        for m, d, cloud in items
    ]
    return pd.DataFrame(rows, columns=["date", "cloud_cover", "platform"])


def test_period_is_longest_run_with_three_usable_dates_each() -> None:
    ok = [(8, 1, 10.0), (8, 9, 20.0), (8, 17, 30.0)]
    scenes = _scenes(
        {
            2000: ok,
            2001: ok,
            2002: ok,
            2003: ok[:2],  # only 2 dates
            2004: ok,
            2005: [(8, 1, 10.0), (8, 1, 12.0), (8, 9, 20.0)],  # 3 scenes, 2 distinct dates
            2006: [(8, 1, 10.0), (8, 9, 20.0), (8, 17, 51.0)],  # third is above 50 %
            2007: ok,
            2008: ok,
            2009: ok,
        }
    )
    levels = _grealm({**dict.fromkeys(range(2000, 2010), 3), 2008: 2})
    period = compute_period(scenes, levels, Season(7, 8), range(2000, 2010))
    assert (period["start"], period["end"]) == (2000, 2002)
    assert period["years"] == [2000, 2001, 2002]
    reasons = {d["year"]: d["reason"] for d in period["dropped"]}
    assert set(reasons) == {2003, 2004, 2005, 2006, 2007, 2008, 2009}
    assert "landsat" in reasons[2003] and "landsat" in reasons[2005] and "landsat" in reasons[2006]
    assert "grealm" in reasons[2008]
    assert "longest" in reasons[2004] and "longest" in reasons[2009]
    assert USABLE_SCENE_MAX_CLOUD == 50.0


def test_period_ignores_scenes_outside_the_season() -> None:
    scenes = _scenes({2000: [(10, 1, 10.0), (10, 9, 10.0), (10, 17, 10.0)]})
    levels = _grealm({2000: 3})
    period = compute_period(scenes, levels, Season(7, 8), [2000])
    assert period["years"] == []
    assert period["start"] is None


def test_build_record_has_everything_and_gate_blocks_short_period() -> None:
    years = list(range(2000, 2016))
    scene_rows = {y: [(8, 1, 10.0), (8, 9, 20.0), (8, 17, 30.0), (9, 2, 80.0)] for y in years}
    scenes = _scenes(scene_rows)
    levels = _grealm(dict.fromkeys(years, 3))
    record = build_season_record(scenes, levels, years, BBOX, run_date=date(2026, 11, 18))
    assert record["chosen"] == {"start_month": 7, "end_month": 8}
    assert record["usable_scene_max_cloud"] == 50.0
    assert record["run_date"] == "2026-11-18"
    assert record["bbox"]["bbox"] == list(BBOX)
    assert record["period"]["years"] == years
    assert {"7-8", "8-9", "9-10"} == set(record["medians"])
    assert len(record["per_window_year"]) > 0
    json.dumps(record)

    short = _grealm(dict.fromkeys(years[:5], 3))
    with pytest.raises(InsufficientLevelData):
        build_season_record(scenes, short, years, BBOX, run_date=date(2026, 11, 18))
