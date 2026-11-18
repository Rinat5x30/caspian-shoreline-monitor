import json
import math
import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString

from caspianwatch import mapdata, validate
from caspianwatch.landsat import WaterMask, site_grid
from caspianwatch.mapdata import (
    ReproductionError,
    analyse_sites,
    build_caspian_data,
    build_level_vs_shore_json,
    build_report,
    build_shoreline_geojson,
    build_summary_table,
    check_reproduction,
    serialise,
    split_segments,
    validation_note,
)
from caspianwatch.sites import Site
from caspianwatch.validate import SiteReport

ROOT = Path(__file__).resolve().parents[2]
RUN_FILE = ROOT / "docs" / "caspian" / "validation-run.txt"
CACHE = ROOT / "data" / "raw" / "cache" / "caspian"
PERIOD = list(range(2000, 2026))
REPRO = {
    "protocol_commit": "aaaaaaa",
    "run_commit": "bbbbbbb",
    "head_commit": "ccccccc",
    "run_sha256": "0" * 64,
}


def _site(site_id: str = "syn_a", deltaic: bool = False) -> Site:
    line = LineString([(300000.0, 4400000.0), (300000.0, 4401000.0)])
    return Site(site_id, f"Synthetic {site_id}", line, (-1.0, 0.0), deltaic, 1000.0, 200.0)


def _mask(site: Site, shore_x: float, *, usable: bool = True) -> WaterMask:
    grid = site_grid(site)
    xs = grid.x0 + (np.arange(grid.width) + 0.5) * grid.pixel_m
    water = np.tile(xs < shore_x, (grid.height, 1))
    valid = np.ones_like(water)
    fractions = (0.9, 0.9, 0.9) if usable else (0.9,)
    return WaterMask(
        water,
        grid.transform,
        "EPSG:32639",
        len(fractions),
        valid,
        0.0,
        1.0,
        fractions,
        np.where(water, 0.5, -0.5).astype(np.float32),
    )


def _masks(site: Site, shore: dict[int, float | None]) -> dict[int, WaterMask | None]:
    return {y: None if x is None else _mask(site, x) for y, x in shore.items()}


def _report(verdict: str, site_id: str = "s", reason: str = "r") -> SiteReport:
    nan = math.nan
    if verdict == "not_measurable":
        return SiteReport(site_id, "not_measurable", reason, 0, nan, nan, nan, nan, nan)
    return SiteReport(
        site_id, verdict, reason, 25, 0.2, 0.3, 0.55, 0.2, 0.9, 23, -0.0005, 0.8, 0.0  # type: ignore[arg-type]
    )


def _levels(values: dict[int, float]) -> pd.DataFrame:
    rows = [
        (pd.Timestamp(year=y, month=m, day=d), "SYN", v)
        for y, v in values.items()
        for m, d in ((7, 5), (7, 25), (8, 14), (8, 24))
    ]
    return pd.DataFrame(rows, columns=["date", "mission", "level_m"])


def _synthetic_world() -> tuple[list[Site], dict[str, dict[int, WaterMask | None]], pd.DataFrame]:
    rng = np.random.default_rng(1)
    sites = [_site("syn_a", True), _site("syn_b")]
    masks: dict[str, dict[int, WaterMask | None]] = {}
    for k, site in enumerate(sites):
        shore: dict[int, float | None] = {}
        for i, y in enumerate(PERIOD):
            shore[y] = 300050.0 - 12.0 * i + float(rng.integers(-2, 3)) * 30.0 * k
        shore[2003] = None
        masks[site.site_id] = _masks(site, shore)
    levels = _levels({y: 0.3 - 0.07 * (y - 2000) + float(rng.normal(0, 0.05)) for y in PERIOD})
    return sites, masks, levels


# ---------- shoreline lines ----------


def test_geojson_has_line_per_available_year_and_min_uncertainty() -> None:
    site = _site()
    shore: dict[int, float | None] = {2000: 300000.0, 2001: 299940.0, 2002: None, 2003: 299880.0}
    masks = _masks(site, shore)
    masks[2004] = _mask(site, 299800.0, usable=False)
    fc = build_shoreline_geojson(site, masks, [_report("confirmed", "syn_a")])
    assert fc["type"] == "FeatureCollection"
    years = [f["properties"]["year"] for f in fc["features"]]
    assert years == [2000, 2001, 2003]
    for f in fc["features"]:
        assert f["geometry"]["type"] in {"LineString", "MultiLineString"}
        assert f["properties"]["uncertainty_m"] >= 30.0
        assert f["properties"]["site_id"] == "syn_a"
        assert f["properties"]["n_points"] >= 2
    lon, lat = fc["features"][0]["geometry"]["coordinates"][0]
    assert 48.0 < lon < 49.5 and 39.0 < lat < 40.5
    assert round(lon, 5) == lon and round(lat, 5) == lat
    assert fc["properties"]["noise_floor_note"] is None


def test_noise_floor_note_only_for_gizilaghaj() -> None:
    fc = build_shoreline_geojson(
        _site("gizilaghaj"), _masks(_site("gizilaghaj"), {2000: 300000.0}), []
    )
    assert "100-200 m" in fc["properties"]["noise_floor_note"]


def test_split_segments_at_nan_profile_and_wide_gap() -> None:
    idx = [0, 1, 3, 4, 5, 6, 7]
    xy = np.array([[0, 0], [0, 200], [0, 600], [0, 800], [0, 1000], [0, 1200], [0, 1400]], float)
    segs = split_segments(idx, xy)
    assert [len(s) for s in segs] == [2, 5]
    idx2 = [0, 1, 2, 3, 4]
    xy2 = np.array([[0, 0], [0, 200], [0, 400], [0, 1600], [0, 1800]], float)
    assert [len(s) for s in split_segments(idx2, xy2)] == [3, 2]


def test_line_splits_into_multilinestring_when_a_profile_is_nan() -> None:
    site = _site()
    mask = _mask(site, 300000.0)
    mask.valid[:, :] = True
    grid = site_grid(site)
    row = int((grid.y0 - 4400400.0) / grid.pixel_m)
    mask.water[row - 1 : row + 2, :] = True
    fc = build_shoreline_geojson(site, {2000: mask}, [])
    assert fc["features"][0]["geometry"]["type"] == "MultiLineString"


# ---------- table ----------


def test_metres_per_cm_empty_unless_confirmed() -> None:
    reports = [
        _report("confirmed", "a"),
        _report("not_confirmed", "b"),
        _report("not_measurable", "c"),
    ]
    rng = {"a": (20.2, 21.5), "b": (30.0, 31.0), "c": (1.0, 2.0)}
    area = {"a": 1.5, "b": 2.5, "c": None}
    table = build_summary_table(reports, area, area, rng)
    by = table.set_index("site_id")["metres_per_cm"]
    assert by["a"] == "about 20-22 m"
    assert by["b"] is None and by["c"] is None


def test_unmeasurable_site_listed_with_reason() -> None:
    reports = [_report("not_measurable", "c", reason="shoreline series not measurable")]
    table = build_summary_table(reports, {"c": None}, {"c": None}, {"c": None})
    row = table.iloc[0]
    assert row["site_id"] == "c"
    assert "not measurable" in row["verdict"]
    assert "shoreline series not measurable" in row["reason"]
    assert row["exposed_km2_grid"] is None


# ---------- note ----------


def test_not_confirmed_note_says_not_confirmed() -> None:
    note = validation_note("not_confirmed", [_report("not_confirmed", "a")])
    assert "not_confirmed" in note
    assert "not a validation of the monitor" in note
    assert "0 of 1 measurable" in note


@pytest.mark.parametrize("overall", ["confirmed", "partial", "not_confirmed"])
def test_note_states_overall_counts_and_disclaimer(overall: str) -> None:
    reports = [
        _report("confirmed", "a"),
        _report("not_confirmed", "b"),
        _report("not_measurable", "c"),
    ]
    note = validation_note(overall, reports)
    assert f"Overall verdict: {overall}" in note
    assert "1 of 2 measurable" in note
    assert "not a validation of the monitor" in note
    if overall != "confirmed":
        assert "validated" not in note and "proven" not in note


def test_partial_note_wording() -> None:
    note = validation_note("partial", [_report("confirmed", "a"), _report("not_confirmed", "b")])
    assert "not shown" in note and "refuted" in note
    assert "confirmed (protocol)" in note


# ---------- level vs shore ----------


def test_level_vs_shore_has_null_not_zero_for_gaps() -> None:
    d = pd.Series([10.0, 20.0, math.nan, 45.0, 50.0], index=[2000, 2001, 2002, 2003, 2004])
    lev = pd.Series([0.1, 0.0, 0.2, math.nan, -0.3], index=[2000, 2001, 2002, 2003, 2004])
    out = build_level_vs_shore_json({"s": d}, lev)
    assert out["years"] == [2000, 2001, 2002, 2003, 2004]
    assert out["sites"]["s"]["displacement_m"] == [10.0, 20.0, None, 45.0, 50.0]
    assert out["level_m"] == [0.1, 0.0, 0.2, None, -0.3]
    diffs = out["sites"]["s"]["differences"]
    assert [x["year"] for x in diffs] == [2001]
    assert diffs[0]["d_displacement_m"] == pytest.approx(10.0)
    assert diffs[0]["d_level_m"] == pytest.approx(-0.1)
    json.dumps(out, allow_nan=False)


# ---------- reproduction guard ----------


def _reports_from_run_file() -> list[SiteReport]:
    out: list[SiteReport] = []
    text = RUN_FILE.read_text(encoding="utf-8")
    for m in re.finditer(
        r"site_id=(\S+)\nverdict=(\S+)\n.*\n.*\nrmse_model=(\S+)  rmse_baseline=(\S+)\n"
        r"r_diff=(\S+)  r_diff_ci_low=(\S+)",
        text,
    ):
        sid, verdict, rm, rb, rd, rl = m.groups()
        out.append(
            SiteReport(
                sid, verdict, "x", 25, float(rm), float(rb), float(rd), float(rl), 0.9  # type: ignore[arg-type]
            )
        )
    return out


def test_guard_passes_on_committed_run_and_raises_when_a_number_is_altered(tmp_path: Path) -> None:
    reports = _reports_from_run_file()
    assert len(reports) == 3
    check_reproduction(reports, RUN_FILE)
    altered = tmp_path / "run.txt"
    shutil.copy(RUN_FILE, altered)
    text = altered.read_text(encoding="utf-8").replace("r_diff=0.563470", "r_diff=0.573470", 1)
    altered.write_text(text, encoding="utf-8")
    with pytest.raises(ReproductionError, match="r_diff"):
        check_reproduction(reports, altered)


def test_guard_raises_on_missing_site(tmp_path: Path) -> None:
    reports = _reports_from_run_file()
    reports.append(_report("confirmed", "ghost"))
    with pytest.raises(ReproductionError, match="ghost"):
        check_reproduction(reports, RUN_FILE)


# ---------- data object, serialisation, text rules ----------


@pytest.fixture(scope="module")
def synthetic_data() -> dict[str, object]:
    sites, masks, levels = _synthetic_world()
    level_series, analyses = analyse_sites(sites, masks, levels, (7, 8), PERIOD)
    return build_caspian_data(
        sites,
        masks,
        level_series,
        analyses,
        season_months=(7, 8),
        period=PERIOD,
        reproducibility=REPRO,
        generated="2026-01-01",
    )


def test_data_file_is_valid_json_and_js_wraps_exactly_that_json(
    synthetic_data: dict[str, object],
) -> None:
    json_text, js_text = serialise(synthetic_data)
    assert js_text.startswith("window.CASPIAN_DATA = ")
    body = js_text[len("window.CASPIAN_DATA = ") :].rstrip()
    assert body.endswith(";")
    assert json.loads(body[:-1]) == json.loads(json_text) == synthetic_data


def test_size_limit_enforced() -> None:
    with pytest.raises(ValueError, match="too large"):
        serialise({"x": "a" * (mapdata.SIZE_LIMIT_BYTES + 1)})


def test_serialise_rejects_nan() -> None:
    with pytest.raises(ValueError):
        serialise({"x": math.nan})


def _strip_allowed(text: str) -> str:
    text = re.sub(r"not[ _]confirmed", "", text, flags=re.I)
    return re.sub(r"confirmed \(protocol\)", "", text, flags=re.I)


def test_no_forbidden_words_in_generated_texts(synthetic_data: dict[str, object]) -> None:
    texts = [str(synthetic_data["validation_note"]), build_report(synthetic_data)]
    for text in texts:
        low = text.lower()
        assert "validated" not in low and "proven" not in low
        assert "confirmed" not in _strip_allowed(text).lower()


def test_report_numbers_equal_data_object(synthetic_data: dict[str, object]) -> None:
    report = build_report(synthetic_data)
    for site in synthetic_data["sites"]:  # type: ignore[attr-defined]
        rep = site["report"]
        if rep["verdict"] == "not_measurable":
            continue
        assert f"{rep['r_diff']:.2f}" in report
        assert f"{rep['rmse_model']:.3f}" in report
    assert str(synthetic_data["overall"]["verdict"]) in report  # type: ignore[index]
    assert "Co-" not in report


def test_synthetic_data_structure(synthetic_data: dict[str, object]) -> None:
    sites = synthetic_data["sites"]
    assert [s["id"] for s in sites] == ["syn_a", "syn_b"]  # type: ignore[index, attr-defined]
    s = sites[0]  # type: ignore[index]
    for key in (
        "name",
        "deltaic",
        "caveats",
        "valid_years",
        "shoreline",
        "report",
        "sensitivity",
        "exposed_km2",
        "qc",
        "metres_per_cm_of_level_fall",
        "noise_floor_note",
    ):
        assert key in s
    assert s["qc"]["status"] == "not_computed"
    assert 2003 not in s["valid_years"]
    assert set(s["sensitivity"]["label"].split()) >= {"sensitivity", "only"}
    assert set(synthetic_data["meta"]) >= {"generated", "season", "period", "sources"}  # type: ignore[arg-type]


# ---------- real cached data ----------

needs_cache = pytest.mark.skipif(
    not (CACHE / "landsat_water_gizilaghaj_2025.npz").exists(),
    reason="Landsat mask caches absent",
)


@needs_cache
def test_real_build_reproduces_run_and_report_matches_data() -> None:
    data = mapdata.build_real_data(RUN_FILE, REPRO)
    json_text, js_text = serialise(data)
    assert len(js_text.encode("utf-8")) < mapdata.SIZE_LIMIT_BYTES
    assert data["overall"]["verdict"] == "partial"
    by = {s["id"]: s for s in data["sites"]}
    assert by["gizilaghaj"]["report"]["verdict"] == "confirmed"
    assert by["gizilaghaj"]["metres_per_cm_of_level_fall"].startswith("about 2")
    assert by["kura_delta"]["metres_per_cm_of_level_fall"] is None
    assert by["lankaran_astara"]["metres_per_cm_of_level_fall"] is None
    assert by["gizilaghaj"]["report"]["r_diff"] == pytest.approx(0.563470, abs=1e-6)
    report = build_report(data)
    assert "0.56" in report
    assert validate.R_DIFF_MIN == data["meta"]["r_diff_min"]


@needs_cache
def test_real_texts_have_no_forbidden_words_and_secondary_matches_run_file() -> None:
    data = mapdata.build_real_data(RUN_FILE, REPRO)
    for text in (data["validation_note"], build_report(data)):
        assert "validated" not in text.lower() and "proven" not in text.lower()
        assert "confirmed" not in _strip_allowed(text).lower()
    run = RUN_FILE.read_text(encoding="utf-8")
    for site in data["sites"]:
        header = "-- " + site["id"] + chr(10)
        block = run.split(header, 1)[1].split(chr(10) * 2, 1)[0]
        for rec in site["sensitivity"]["fixed_threshold"]:
            m = re.search(rf"fixed MNDWI threshold {rec['threshold']}: .*? r_diff=(\S+)", block)
            assert m is not None
            assert rec["r_diff"] == pytest.approx(float(m.group(1)), abs=1e-4)
        m = re.search(r"weighted displacement: .*? r_diff=(\S+)", block)
        assert m is not None
        assert site["sensitivity"]["weighted"]["r_diff"] == pytest.approx(
            float(m.group(1)), abs=1e-4
        )
