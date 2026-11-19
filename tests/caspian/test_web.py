from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
HTML = WEB / "caspian.html"
JS = WEB / "js" / "caspian.js"
CSS = WEB / "css" / "caspian.css"
DATA_JS = WEB / "data" / "caspian-data.js"

REQUIRED_IDS = [
    "verdict-badge",
    "year-slider",
    "play-button",
    "site-cards",
    "level-chart",
    "legend",
]
ALLOWED_HOSTS = {
    "cdnjs.cloudflare.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "basemaps.cartocdn.com",
    "server.arcgisonline.com",
}
FORBIDDEN = re.compile(
    r"\b(validated|proven|proof|proves|demonstrates|successful|accurate|reliable)\b",
    re.IGNORECASE,
)
DOCS = [WEB / "README.md", ROOT / "docs" / "caspian" / "interface-notes.md"]
BARE_CONFIRMED = re.compile(r"(?<!not )confirmed(?! \(protocol\))", re.IGNORECASE)
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.scripts: list[str] = []
        self.title = ""
        self.lang = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if a.get("id"):
            self.ids.append(a["id"] or "")
        if tag == "script" and a.get("src"):
            self.scripts.append(a["src"] or "")
        if tag == "html":
            self.lang = a.get("lang") or ""
        self._in_title = tag == "title"

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def _parse() -> _Collector:
    parser = _Collector()
    parser.feed(HTML.read_text(encoding="utf-8"))
    return parser


def _load_data() -> dict[str, Any]:
    text = DATA_JS.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text[text.index("{") : text.rindex("}") + 1])
    return data


def _node(script: str, payload: dict[str, Any]) -> Any:
    assert NODE is not None
    prelude = (
        "const ui = require(process.argv[1]);"
        "let raw = '';"
        "process.stdin.on('data', c => raw += c);"
        "process.stdin.on('end', () => { const data = JSON.parse(raw);"
    )
    code = prelude + script + "});"
    result = subprocess.run(
        [NODE, "-e", code, str(JS)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(result.stdout)


def _all_text() -> list[tuple[str, str]]:
    return [(p.name, p.read_text(encoding="utf-8")) for p in (HTML, JS, CSS)]


def test_page_has_required_element_ids() -> None:
    parser = _parse()
    for element_id in REQUIRED_IDS:
        assert element_id in parser.ids, element_id
    assert parser.lang == "en"
    assert 2 <= len(parser.title.split()) <= 4


def test_data_script_tag_present() -> None:
    scripts = _parse().scripts
    assert "data/caspian-data.js" in scripts
    assert "js/caspian.js" in scripts
    assert scripts.index("data/caspian-data.js") < scripts.index("js/caspian.js")


def test_no_fetch_of_local_data() -> None:
    js = JS.read_text(encoding="utf-8")
    assert "fetch(" not in js
    assert "XMLHttpRequest" not in js
    assert ".json" not in js


def test_page_loads_only_allowed_cdn_hosts() -> None:
    pattern = re.compile(r"(?:https?:)?//([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
    for name, text in _all_text():
        for host in pattern.findall(text):
            assert host in ALLOWED_HOSTS, f"{name}: {host}"


def test_static_text_has_no_forbidden_words() -> None:
    for name, text in _all_text():
        assert not FORBIDDEN.search(text), name
    for doc in DOCS:
        assert not FORBIDDEN.search(doc.read_text(encoding="utf-8")), doc.name


def test_static_text_confirmed_only_as_not_confirmed() -> None:
    for name, text in _all_text():
        stripped = re.sub(r"not confirmed|n_confirmed", "", text, flags=re.IGNORECASE)
        assert "confirmed" not in stripped.lower(), name


def test_verdict_words_not_hardcoded_in_html() -> None:
    html = HTML.read_text(encoding="utf-8").lower()
    assert "partial" not in html
    assert "not a validation" not in html


@needs_node
def test_verdict_text_on_page_matches_validation_note() -> None:
    data = _load_data()
    parts = _node("console.log(JSON.stringify(ui.badgeParts(data)));", data)
    assert parts["head"] == f"OVERALL: {data['overall']['verdict'].upper()}"
    assert parts["notice"] in data["validation_note"]
    assert "not a validation of the monitor" in parts["notice"]
    assert f"{data['overall']['n_confirmed']} of {data['overall']['n_measurable']}" in parts["body"]
    assert "confirmed (protocol)" in parts["body"]


def _all_not_confirmed(data: dict[str, Any]) -> dict[str, Any]:
    fixture = copy.deepcopy(data)
    fixture["overall"].update({"verdict": "not_confirmed", "n_confirmed": 0})
    fixture["validation_note"] = (
        "Overall verdict: not confirmed. 0 of 3 measurable sites reached the protocol verdict. "
        "This is not a validation of the monitor. Per site: not confirmed (protocol)."
    )
    fixture["threshold_overall"] = {
        k: {"overall": "not_confirmed", "criterion_met_sites": [], "label": "sensitivity only"}
        for k in fixture["threshold_overall"]
    }
    for site in fixture["sites"]:
        site["report"]["verdict"] = "not_confirmed"
        site["report"]["verdict_label"] = "not confirmed (protocol)"
        site["metres_per_cm_of_level_fall"] = None
        site["caveat_notes"] = [n for n in site["caveat_notes"] if "onfirmed" not in n]
        site["caveats"] = ""
    for row in fixture["summary_table"]:
        row["verdict"] = "not confirmed (protocol)"
        row["metres_per_cm"] = None
    return fixture


@needs_node
def test_no_confirmed_claim_when_data_says_not_confirmed() -> None:
    fixture = _all_not_confirmed(_load_data())
    script = (
        "const out = [ui.badgeParts(data).head, ui.badgeParts(data).body,"
        " ui.badgeParts(data).notice, ui.panelHtml(data), ui.sensitivityHtml(data),"
        " ui.legendHtml(data), ui.sourcesHtml(data)];"
        "data.sites.forEach(s => out.push(ui.cardHtml(s, data, s.id)));"
        "console.log(JSON.stringify(out.join('\\n')));"
    )
    text: str = _node(script, fixture)
    stripped = re.sub(r"not confirmed", "", text, flags=re.IGNORECASE)
    assert "confirmed" not in stripped.lower()
    assert "not confirmed (protocol)" in text
    assert "per cm of level fall" not in text


@needs_node
def test_metres_per_cm_only_for_non_null_value() -> None:
    data = _load_data()
    script = "console.log(JSON.stringify(data.sites.map(s => ui.cardHtml(s, data, s.id))));"
    cards: list[str] = _node(script, data)
    for site, card in zip(data["sites"], cards, strict=True):
        has_text = "per cm of level fall" in card
        assert has_text == (site["metres_per_cm_of_level_fall"] is not None)
        assert (
            site["caveat_notes"][0][:30].replace("'", "&#39;") in card
            or site["caveat_notes"][0][:30] in card
        )


@needs_node
def test_missing_year_renders_as_gap_not_zero() -> None:
    script = (
        "const sx = v => v * 10, sy = v => 100 - v;"
        "console.log(JSON.stringify({"
        "gap: ui.linePath([0, 1, 2, 3, 4], [5, 6, null, 7, 8], sx, sy),"
        "zero: ui.linePath([0, 1, 2], [5, null, 6], sx, sy)}));"
    )
    out = _node(script, {})
    assert out["gap"] == "M0,95 L10,94 M30,93 L40,92"
    assert out["zero"].count("M") == 2
    assert "L" not in out["zero"]
    assert ",100" not in out["zero"]


@needs_node
def test_numbers_use_no_data_label_for_null_year() -> None:
    data = _load_data()
    script = (
        "const m = ui.numbersModel(data, 'gizilaghaj', 2003);"
        "console.log(JSON.stringify({disp: m.disp, level: m.level, shore: m.hasShoreline}));"
    )
    out = _node(script, data)
    assert out["disp"] == "no data"
    assert out["shore"] is False
    assert out["level"] != "no data"


@needs_node
def test_chart_alt_text_and_gap_marker() -> None:
    data = _load_data()
    script = "console.log(JSON.stringify(ui.chartSvg(data, 'gizilaghaj', 2025)));"
    svg: str = _node(script, data)
    assert "<svg" in svg and 'role="img"' in svg and "<title" in svg
    assert "Relative level (m)" in svg
    assert "Shoreline distance (m)" in svg
    assert "no data" in svg


def test_css_has_theme_and_motion_rules() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert "prefers-color-scheme" in css
    assert "prefers-reduced-motion" in css
    assert '[data-theme="light"]' in css
    assert ":focus-visible" in css


@needs_node
def test_fixture_badge_says_not_confirmed() -> None:
    fixture = _all_not_confirmed(_load_data())
    parts = _node("console.log(JSON.stringify(ui.badgeParts(data)));", fixture)
    assert parts["head"] == "OVERALL: NOT CONFIRMED"
    assert not BARE_CONFIRMED.search(parts["body"] + parts["notice"])
    assert "20-22" not in json.dumps(parts)
    assert parts["state"] == "negative"


@needs_node
def test_sensitivity_never_prints_bare_confirmed() -> None:
    data = _load_data()
    keys = {row["overall"] for row in data["threshold_overall"].values()}
    assert "confirmed" in keys
    html: str = _node("console.log(JSON.stringify(ui.sensitivityHtml(data)));", data)
    assert "confirmed (protocol)" in html
    assert not BARE_CONFIRMED.search(html)


@needs_node
def test_overall_confirmed_key_gets_protocol_label() -> None:
    data = _load_data()
    data["overall"]["verdict"] = "confirmed"
    parts = _node("console.log(JSON.stringify(ui.badgeParts(data)));", data)
    assert parts["head"] == "OVERALL: CONFIRMED (PROTOCOL)"


@needs_node
def test_body_counts_use_label() -> None:
    data = _load_data()
    parts = _node("console.log(JSON.stringify(ui.badgeParts(data)));", data)
    assert parts["body"] == "1 of 3 sites confirmed (protocol)"


@needs_node
def test_mark_current_updates_in_place() -> None:
    script = (
        "const mk = id => { const a = {}; return { dataset: { site: id }, attrs: a,"
        " setAttribute(k, v) { a[k] = v; }, removeAttribute(k) { delete a[k]; } }; };"
        "const nodes = ['a', 'b', 'c'].map(mk);"
        "ui.markCurrent(nodes, 'b');"
        "console.log(JSON.stringify(nodes.map(n => n.attrs)));"
    )
    attrs = _node(script, {})
    assert attrs == [{}, {"aria-current": "true"}, {}]


def test_cards_not_rebuilt_on_site_change() -> None:
    js = JS.read_text(encoding="utf-8")
    assert js.count("$('site-cards').innerHTML") == 1
    body = js[js.index("function renderSite") : js.index("function applyTheme")]
    assert "site-cards').innerHTML" not in body


@needs_node
def test_card_caveat_precedes_estimate_and_qc() -> None:
    data = _load_data()
    script = "console.log(JSON.stringify(ui.cardHtml(data.sites[0], data, data.sites[0].id)));"
    card: str = _node(script, data)
    first = card.index('class="note first"')
    mpc = card.index('class="mpc"')
    qc = card.index('class="qc"')
    assert first < mpc < qc
    assert "protocol-conditional estimate" in card


@needs_node
def test_uncertainty_line_from_data() -> None:
    data = _load_data()
    line: str = _node("console.log(JSON.stringify(ui.uncertaintyLine(data)));", data)
    assert f"±{int(data['meta']['uncertainty_m'])} m at least" in line
    assert "100-200 m" in line
    assert "reviewers" in line


def test_html_has_uncertainty_line_and_live_region() -> None:
    html = HTML.read_text(encoding="utf-8")
    assert 'id="uncertainty-line"' in html
    assert re.search(r'id="numbers"[^>]*aria-live="polite"', html)
    assert "Three sites, three verdicts" not in html


def test_summary_tap_target_and_readme_esri_note() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert re.search(r"summary\s*\{[^}]*min-height:\s*44px", css)
    readme = (WEB / "README.md").read_text(encoding="utf-8")
    assert "attribution" in readme.lower() and "API key" in readme
