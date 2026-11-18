"""Build web/data/caspian-data.{js,json} and the validation report (cache only, no network).

The build recomputes every site report in memory and stops if it differs from the committed
single run output docs/caspian/validation-run.txt (see caspianwatch.mapdata.check_reproduction).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from caspianwatch.mapdata import build_real_data, build_report, serialise  # noqa: E402

RUN_PATH = ROOT / "docs" / "caspian" / "validation-run.txt"
PROTOCOL_PATH = ROOT / "docs" / "caspian" / "validation-protocol.md"
WEB_DATA = ROOT / "web" / "data"
REPORT_PATH = ROOT / "docs" / "application" / "caspian-validation-report.md"


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"
    return out or "unavailable"


def reproducibility() -> dict[str, str]:
    return {
        "protocol_commit": _git(
            "log", "-1", "--format=%h", "--", str(PROTOCOL_PATH.relative_to(ROOT))
        ),
        "run_commit": _git("log", "-1", "--format=%h", "--", str(RUN_PATH.relative_to(ROOT))),
        "head_commit": _git("rev-parse", "--short", "HEAD"),
        "run_sha256": hashlib.sha256(RUN_PATH.read_bytes()).hexdigest(),
    }


def main() -> None:
    data = build_real_data(RUN_PATH, reproducibility())
    print("reproduction guard: OK (rmse_model, rmse_baseline, r_diff, ci_low within 1e-4)")
    json_text, js_text = serialise(data)
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    (WEB_DATA / "caspian-data.json").write_bytes(json_text.encode("utf-8"))
    (WEB_DATA / "caspian-data.js").write_bytes(js_text.encode("utf-8"))
    REPORT_PATH.write_bytes(build_report(data).encode("utf-8"))
    for path in (WEB_DATA / "caspian-data.js", WEB_DATA / "caspian-data.json", REPORT_PATH):
        print(f"{path.relative_to(ROOT)}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
