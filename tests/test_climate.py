import json
import logging
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box

from wheatwatch.climate import HttpStatusError, season_climate


def _regions(ids: tuple[str, ...] = ("alpha",)) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"region_id": list(ids)},
        geometry=[box(46 + i, 40, 47 + i, 41) for i in range(len(ids))],
        crs="EPSG:4326",
    )


def _payload(
    first: int,
    last: int,
    precip: float = 1.0,
    tmax: float = 20.0,
    overrides: Mapping[str, tuple[float | None, float | None]] | None = None,
) -> dict[str, Any]:
    days = [
        (date(first, 1, 1) + timedelta(d)).isoformat()
        for d in range((date(last, 12, 31) - date(first, 1, 1)).days + 1)
    ]
    p: list[float | None] = [precip] * len(days)
    t: list[float | None] = [tmax] * len(days)
    for day, (pv, tv) in (overrides or {}).items():
        p[days.index(day)] = pv
        t[days.index(day)] = tv
    return {"daily": {"time": days, "precipitation_sum": p, "temperature_2m_max": t}}


class FakeHttp:
    def __init__(self, payload: dict[str, Any] | None = None, fail: bool = False) -> None:
        self.urls: list[str] = []
        self.payload = payload
        self.fail = fail

    def __call__(self, url: str) -> dict[str, Any]:
        self.urls.append(url)
        if self.fail:
            raise OSError("network down")
        if self.payload is not None:
            return self.payload
        assert "start_date=2020-01-01" in url and "end_date=2021-12-31" in url
        return _payload(2020, 2021)


def _run(
    tmp_path: Path, http: Any, years: list[int] | None = None, ids: tuple[str, ...] = ("alpha",)
) -> Any:
    return season_climate(
        _regions(ids), years or [2020, 2021], fetch=http, sleep=lambda _s: None, cache_dir=tmp_path
    )


def test_season_climate_uses_cache_on_second_call(tmp_path: Path) -> None:
    http = FakeHttp()
    first = _run(tmp_path, http)
    second = _run(tmp_path, http)
    assert len(http.urls) == 1
    assert first.equals(second)
    assert list(first.columns) == ["region_id", "year", "precip_mm", "tmax_mean_c"]
    assert len(first) == 2
    assert first["precip_mm"].iloc[0] == pytest.approx(122.0)
    assert first["tmax_mean_c"].iloc[0] == pytest.approx(20.0)
    assert (tmp_path / "climate_alpha_2020.json").exists()


def test_failed_fetch_raises_not_zero(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        _run(tmp_path, FakeHttp(fail=True))
    assert not list(tmp_path.glob("climate_*"))


def test_null_in_window_gives_nan_and_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    payload = _payload(2020, 2020, overrides={"2020-04-10": (None, None)})
    with caplog.at_level(logging.WARNING):
        result = _run(tmp_path, FakeHttp(payload), [2020])
    assert np.isnan(result["precip_mm"].iloc[0])
    assert np.isnan(result["tmax_mean_c"].iloc[0])
    assert "alpha" in caplog.text and "2020" in caplog.text


def test_missing_window_days_in_response_give_nan(tmp_path: Path) -> None:
    payload = _payload(2020, 2020)
    keep = [i for i, d in enumerate(payload["daily"]["time"]) if d != "2020-05-01"]
    payload["daily"] = {k: [v[i] for i in keep] for k, v in payload["daily"].items()}
    result = _run(tmp_path, FakeHttp(payload), [2020])
    assert np.isnan(result["precip_mm"].iloc[0])


def test_window_boundaries_inclusive(tmp_path: Path) -> None:
    overrides = {
        "2020-02-28": (50.0, 0.0),
        "2020-03-01": (10.0, 30.0),
        "2020-06-30": (5.0, 30.0),
        "2020-07-01": (50.0, 0.0),
    }
    payload = _payload(2020, 2020, precip=0.0, tmax=10.0, overrides=overrides)
    result = _run(tmp_path, FakeHttp(payload), [2020])
    assert result["precip_mm"].iloc[0] == pytest.approx(15.0)
    assert result["tmax_mean_c"].iloc[0] == pytest.approx((120 * 10.0 + 2 * 30.0) / 122)


def test_retry_on_429_then_success(tmp_path: Path) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def flaky(url: str) -> dict[str, Any]:
        calls.append(url)
        if len(calls) < 3:
            raise HttpStatusError(429)
        return _payload(2020, 2020)

    result = season_climate(
        _regions(), [2020], fetch=flaky, sleep=sleeps.append, cache_dir=tmp_path
    )
    assert len(calls) == 3
    assert len(result) == 1
    assert sleeps[1] > sleeps[0] > 0


def test_connection_reset_is_retried(tmp_path: Path) -> None:
    calls: list[str] = []

    def reset_once(url: str) -> dict[str, Any]:
        calls.append(url)
        if len(calls) == 1:
            raise ConnectionResetError("reset by peer")
        return _payload(2020, 2020)

    result = season_climate(
        _regions(), [2020], fetch=reset_once, sleep=lambda _s: None, cache_dir=tmp_path
    )
    assert len(calls) == 2 and len(result) == 1


def test_retries_exhausted_raises(tmp_path: Path) -> None:
    calls: list[str] = []

    def down(url: str) -> dict[str, Any]:
        calls.append(url)
        raise HttpStatusError(503)

    with pytest.raises(HttpStatusError):
        season_climate(_regions(), [2020], fetch=down, sleep=lambda _s: None, cache_dir=tmp_path)
    assert 1 < len(calls) <= 10
    assert not list(tmp_path.glob("climate_*"))


def test_client_error_is_not_retried(tmp_path: Path) -> None:
    calls: list[str] = []

    def bad(url: str) -> dict[str, Any]:
        calls.append(url)
        raise HttpStatusError(400)

    with pytest.raises(HttpStatusError):
        season_climate(_regions(), [2020], fetch=bad, sleep=lambda _s: None, cache_dir=tmp_path)
    assert len(calls) == 1


def test_corrupt_cache_is_refetched(tmp_path: Path) -> None:
    http = FakeHttp(_payload(2020, 2021))
    _run(tmp_path, http)
    (tmp_path / "climate_alpha_2020.json").write_text("{broken", encoding="utf-8")
    result = _run(tmp_path, http)
    assert len(http.urls) == 2
    assert result["precip_mm"].notna().all()


def test_uses_representative_point_inside_polygon(tmp_path: Path) -> None:
    from shapely.geometry import Polygon

    crescent = Polygon([(46, 40), (48, 40), (48, 41), (47.9, 41), (47.9, 40.1), (46, 40.1)])
    regions = gpd.GeoDataFrame({"region_id": ["c"]}, geometry=[crescent], crs="EPSG:4326")
    http = FakeHttp(_payload(2020, 2020))
    season_climate(regions, [2020], fetch=http, sleep=lambda _s: None, cache_dir=tmp_path)
    point = crescent.representative_point()
    assert f"latitude={point.y:.4f}" in http.urls[0]
    assert f"longitude={point.x:.4f}" in http.urls[0]
    assert crescent.contains(point)


def test_cache_file_content_is_json(tmp_path: Path) -> None:
    _run(tmp_path, FakeHttp())
    data = json.loads((tmp_path / "climate_alpha_2021.json").read_text(encoding="utf-8"))
    assert data["region_id"] == "alpha" and data["year"] == 2021


def test_cache_not_reused_after_window_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = FakeHttp(_payload(2020, 2021))
    _run(tmp_path, http)
    monkeypatch.setattr("wheatwatch.climate.SEASON_END", (6, 15))
    result = _run(tmp_path, http)
    assert len(http.urls) == 2
    assert result["precip_mm"].iloc[0] == pytest.approx(107.0)


def test_cache_with_other_source_or_variables_is_miss(tmp_path: Path) -> None:
    http = FakeHttp(_payload(2020, 2021))
    _run(tmp_path, http)
    path = tmp_path / "climate_alpha_2020.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["variables"] = ["other"]
    path.write_text(json.dumps(record), encoding="utf-8")
    _run(tmp_path, http)
    assert len(http.urls) == 2


def test_nan_result_is_not_cached_and_is_refetched(tmp_path: Path) -> None:
    bad = _payload(2020, 2020, overrides={"2020-04-10": (None, None)})
    http = FakeHttp(bad)
    first = _run(tmp_path, http, [2020])
    assert np.isnan(first["precip_mm"].iloc[0])
    assert not list(tmp_path.glob("climate_*"))
    http.payload = _payload(2020, 2020)
    second = _run(tmp_path, http, [2020])
    assert len(http.urls) == 2
    assert second["precip_mm"].iloc[0] == pytest.approx(122.0)


def test_request_uses_gmt_timezone(tmp_path: Path) -> None:
    http = FakeHttp()
    _run(tmp_path, http)
    assert "timezone=GMT" in http.urls[0]


def test_partly_cached_region_fetches_only_missing_years(tmp_path: Path) -> None:
    _run(tmp_path, FakeHttp(_payload(2020, 2020)), [2020])
    http = FakeHttp(_payload(2021, 2021))
    result = _run(tmp_path, http, [2020, 2021])
    assert len(http.urls) == 1
    assert "start_date=2021-01-01" in http.urls[0] and "end_date=2021-12-31" in http.urls[0]
    assert len(result) == 2
