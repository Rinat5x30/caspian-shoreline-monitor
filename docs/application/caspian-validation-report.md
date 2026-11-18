# Caspian shoreline monitor: validation report

Generated 2026-11-19 from the data object `web/data/caspian-data.json` by `scripts/build_caspian.py`. All numbers below are copied from that object.

**Overall protocol verdict: partial.** 1 of 3 measurable sites reached the per-site verdict 'confirmed (protocol)'. This is not a validation of the monitor.

## 1. Purpose

The monitor shows annual Landsat shorelines of three low-lying Azerbaijani Caspian sites (2000-2025, season Jul-Aug) next to the satellite-altimetry sea level. The question tested here is narrow: does the year-to-year change of the shoreline position follow the year-to-year change of the sea level, site by site, better than a time trend does? The test was fixed before the results were seen.

## 2. Protocol

The criterion and all decisions are in `docs/caspian/validation-protocol.md`, written and committed before the validation code was written and before the sea-level series was opened for this purpose. A site verdict is 'confirmed (protocol)' only if three conditions hold together: leave-one-year-out RMSE of the level model is below that of the time-trend baseline; r_diff is at least 0.5; the lower end of the 95 % bootstrap interval of r_diff is above zero. Two or more such sites give the highest overall level; exactly one gives 'partial'; none gives 'not_confirmed'.

Sign convention, disclosed: the specification says 'not lower than 0.5' without a sign. The protocol makes it explicit: displacement D is positive when the sea retreats, the level falls then, so r_diff = Pearson(dD, -dL) and a positive value is expected. This is a clarification of the convention, not a change of the physical meaning.

## 3. Data and methods in brief

- Shoreline: Landsat Collection 2 Level-2 water composite (modified NDWI from green and SWIR1, Otsu threshold per year-site), position along fixed seaward profiles every 200 m; site displacement D is the unweighted mean over the complete profile set.
- Sea level: NASA G-REALM Caspian series, season mean Jul-Aug; G-REALM heights are relative to the Jason-2 reference-pass mean, not to a geodetic datum.
- Differences use adjacent years only; 25 annual values and 23 adjacent-year pairs per measurable site (2003 has no usable Landsat composite).
- Pearson(dD, -dL) over adjacent-year differences; iid percentile bootstrap CI; N = 2000 resamples, seed 0.
- Sources and licences: NASA GSFC Global Water Monitor (G-REALM), Caspian Sea, lake 000270 (no explicit licence on the source page; project disclaimer on accuracy and use); Landsat Collection 2 Level-2 (USGS) via Microsoft Planetary Computer (USGS open data, no restrictions on use); Sentinel-2 L2A via Microsoft Planetary Computer (Copernicus Sentinel data: free, full and open); ESA WorldCover 2021 v200 (CC BY 4.0); Copernicus DEM GLO-90 (Copernicus DEM licence: free, full and open).

## 4. Results

| Site | Site verdict | Years / pairs | RMSE model | RMSE baseline | r_diff | CI low | raw r | Exposed km2 (whole site grid) | Exposed km2 (profile strip) | Retreat per cm of level fall |
|---|---|---|---|---|---|---|---|---|---|---|
| gizilaghaj | confirmed (protocol) | 25 / 23 | 0.192 | 0.257 | 0.56 | 0.27 | 0.97 | 225.5 | 84.2 | about 20-22 m |
| kura_delta | not confirmed (protocol) | 25 / 23 | 0.207 | 0.257 | 0.41 | 0.07 | 0.96 | 33.1 | 8.0 | - |
| lankaran_astara | not confirmed (protocol) | 25 / 23 | 0.726 | 0.257 | -0.37 | -0.66 | 0.60 | 0.8 | 0.7 | - |

RMSE in metres of level. Exposed area is land that was water in the first and is land in the last year (2000-2025); it is counted over the whole site grid, and the value inside the profile strip is shown beside it. Raw r is shown only because both series trend; it is not evidence of coupling. Retreat per cm is given only for a site with the per-site verdict 'confirmed (protocol)', as the range between the two regression directions.

Reasons for the other sites:

- kura_delta: r_diff 0.4123 < 0.5
- lankaran_astara: rmse_model 0.7263 not below rmse_baseline 0.2573; r_diff -0.3748 < 0.5; ci_low -0.6559 <= 0

Sensor consistency check (Sentinel-2 minus Landsat, shoreline position along the same profiles):

| Site | Common years | Median abs difference, m | Within 30 m | Status |
|---|---|---|---|---|
| gizilaghaj | 6 | 23.4 | yes | preliminary, fewer than the full set of years; year(s) over 30 m: 2017 (35.1 m), 2018 (40.9 m), 2022 (51.2 m) |
| kura_delta | 9 | 2.6 | yes | complete |
| lankaran_astara | 9 | 2.0 | yes | complete; year(s) over 30 m: 2020 (-70.3 m) |

Compares two optical sensors (Landsat, Sentinel-2) under one method on one grid: this shows consistency, not ground-truth accuracy.

## 5. Secondary analyses (sensitivity only)

These analyses are reported beside the verdict and never change it. 'Criterion met' only says that the same three conditions hold under that variant.

| Site | Variant | r_diff | CI low (or interval) | Criterion |
|---|---|---|---|---|
| gizilaghaj | weighted displacement | 0.503 | 0.191 | criterion met |
| gizilaghaj | always-present profiles (63) | 0.573 | 0.299 | criterion met |
| gizilaghaj | fixed MNDWI threshold 0.0 | 0.489 | 0.148 | criterion not met |
| gizilaghaj | fixed MNDWI threshold 0.1 | 0.612 | 0.337 | criterion met |
| gizilaghaj | fixed MNDWI threshold 0.2 | 0.587 | 0.356 | criterion met |
| gizilaghaj | excluding 2024-2025 | 0.561 | 0.201 | criterion met |
| gizilaghaj | block bootstrap CI (length 3) | n/a | [0.122, 0.800] | n/a |
| kura_delta | weighted displacement | 0.412 | 0.067 | criterion not met |
| kura_delta | always-present profiles (30) | 0.497 | 0.132 | criterion not met |
| kura_delta | fixed MNDWI threshold 0.0 | 0.526 | 0.274 | criterion met |
| kura_delta | fixed MNDWI threshold 0.1 | 0.508 | 0.217 | criterion met |
| kura_delta | fixed MNDWI threshold 0.2 | 0.022 | -0.252 | criterion not met |
| kura_delta | block bootstrap CI (length 3) | n/a | [0.028, 0.732] | n/a |
| lankaran_astara | weighted displacement | -0.375 | -0.656 | criterion not met |
| lankaran_astara | always-present profiles (73) | -0.376 | -0.657 | criterion not met |
| lankaran_astara | fixed MNDWI threshold 0.0 | 0.314 | 0.020 | criterion not met |
| lankaran_astara | fixed MNDWI threshold 0.1 | -0.013 | -0.357 | criterion not met |
| lankaran_astara | fixed MNDWI threshold 0.2 | -0.164 | -0.551 | criterion not met |
| lankaran_astara | block bootstrap CI (length 3) | n/a | [-0.656, 0.313] | n/a |

Under fixed thresholds the overall outcome changes between variants (fixed MNDWI 0.0: overall outcome partial (criterion met at kura_delta); fixed MNDWI 0.1: overall outcome at the top level (criterion met at gizilaghaj, kura_delta); fixed MNDWI 0.2: overall outcome partial (criterion met at gizilaghaj)). These are sensitivity analyses; the protocol verdict stands.

## 6. How the result may and may not be described

**Overall.** The protocol verdict is partial (1 of 3 measurable sites). The result must not be described as showing that the monitor or the relation between shoreline and sea level is established; it is not a validation of the monitor.

**gizilaghaj (confirmed (protocol)).** The margin is small: r_diff 0.56 against the threshold 0.5, and the criterion is not met at fixed MNDWI 0.0 (r_diff 0.489). Using only pairs up to 2018 r_diff is 0.21, so the signal comes mainly from the later, accelerated level fall. Under a null of no relation a false 'confirmed (protocol)' is rare (below 1 % per site for linear-trend or random-walk nulls, about 5.5 % under an independent accelerating trend; reviewers' simulation). Retreat per cm of level fall: about 20-22 m; the level is relative to a satellite datum. The annual noise floor of this site is estimated in review at 100-200 m (see qc.md).

**Sensitivity of the overall verdict.** The overall verdict depends on analytic choices at the noise level. Under fixed thresholds the overall outcome changes between variants (fixed MNDWI 0.0: overall outcome partial (criterion met at kura_delta); fixed MNDWI 0.1: overall outcome at the top level (criterion met at gizilaghaj, kura_delta); fixed MNDWI 0.2: overall outcome partial (criterion met at gizilaghaj)). These are sensitivity analyses; the protocol verdict stands.

**Low power.** The test has about 30 % power per site and about 8 % for an overall 'confirmed (protocol)' even if the relation is real (reviewers' estimate). 'Not confirmed' at a site therefore means not shown, not refuted.

**lankaran_astara is uninformative.** The shoreline moved about 39 m (about 1.3 Landsat pixels) in 25 years. r_diff is -0.37; without the 2020-2021 pairs it is 0.24. The negative value follows one 2020 artefact. This is a limitation of 30 m imagery on a steeper coast (compared with the deltaic sites), not evidence against the physical relation.

**Always stated with the results.** 25 annual values and 23 adjacent-year pairs per site (2003 is missing). At gizilaghaj the last years are right-censored: profiles whose shoreline lies beyond the profile end are NaN, and the set of valid profiles changes (composition effect about +-130 m, reviewers' estimate). Exposed area is counted over the whole site grid; the profile-strip value is shown too. The Sentinel-2 check compares two optical sensors under one method, so it shows consistency, not ground-truth accuracy (preliminary for gizilaghaj: fewer than the full set of years). r_diff = Pearson(dD, -dL) with an iid bootstrap interval. The topic is already studied (EGU24-14488; Gasimov 2021). The 218 km2 of exposed land in EGU24-14488 refers to a different place and period and is NOT a validation of this work.

## 7. Limitations

- Three sites only; one site passed. The verdict structure (one of three) is a weak basis for any general statement.
- Shoreline noise at Landsat 30 m resolution is comparable to the annual change at two sites.
- Site geometry caveats from the site selection are carried with each site:
  - gizilaghaj: 81% of the 1 km land band is WorldCover wetland (class 90), shoreline measurability at 30 m is a risk; 11 profiles that crossed open sea before their own station were dropped; of the kept origins 41% of the non-flattened ones lie at or below -26.5 m and 20% sit on flattened DEM (-28.00 m), so the landward origin is not demonstrated everywhere (checked per year on imagery); seaward ends in lagoon water (class 80, not open sea): profile 50 (dropped)
  - kura_delta: only 10% of unflattened profile origins lie above -26.5 m in the DEM, so the landward position of the origin relative to the 2000 shoreline is NOT demonstrated by the DEM; checked per year on imagery (origin must not be water)
  - lankaran_astara: none
- Sea level is a single basin-wide altimetry series; local effects are not modelled.

## 8. Reproducibility

- Protocol commit: `5ac7d0f`. Run output commit: `bb16513`. Build commit (HEAD at build time): `c51472e`.
- sha256 of `docs/caspian/validation-run.txt`: `00c19d0cfd43edaa2e6b22043d037540765ea00cb058b5081b5f617850cf87f6`.
- The build recomputes all site reports from cached masks and the committed G-REALM file without network and stops with an error if rmse_model, rmse_baseline, r_diff or the lower interval end differs from the committed run output by more than 1e-4.
- Commands: `python scripts/run_caspian_validation.py` (the single pre-registered run; re-running is allowed only to reproduce the file byte for byte) and `python scripts/build_caspian.py` (writes `web/data/caspian-data.js`, `web/data/caspian-data.json` and this report).
