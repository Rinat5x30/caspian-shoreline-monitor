import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wheatwatch.climate import season_climate  # noqa: E402
from wheatwatch.ndvi import season_ndvi  # noqa: E402
from wheatwatch.regions import load_regions  # noqa: E402
from wheatwatch.validate import build_features, validate_with_sensitivity  # noqa: E402
from wheatwatch.yields import load_yields  # noqa: E402

YEARS = list(range(2020, 2026))


def main() -> None:
    yields = load_yields(ROOT / "data" / "raw" / "yields.csv")
    regions = load_regions(set(yields["region_id"]))
    yields = yields[yields["region_id"].isin(regions["region_id"])]
    features = build_features(season_climate(regions, YEARS), season_ndvi(regions, YEARS))
    everyone, without = validate_with_sensitivity(features, yields)
    print("all districts:", everyone, f"beats_baseline={everyone.beats_baseline}")
    print("excluding liberated:", without, f"beats_baseline={without.beats_baseline}")


if __name__ == "__main__":
    main()
