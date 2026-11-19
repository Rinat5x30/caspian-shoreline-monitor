# Caspian Shoreline Monitor: an open, repeatable check of shoreline retreat off Azerbaijan, with a first partial result

Global Sustainability Challenge 2026-27 | Theme: Adaptation & Resilience | Focus: Water | Stage: Ignite

## Team

I am a student, building this with an AI coding assistant. No external organisation has been involved, and nobody outside the project has reviewed or used the work. This is a sober first result, not a finished product.

## 1. Problem

The Caspian Sea, which Euronews calls the world's largest lake, has been falling. A May 2026 Euronews report describes "a consistent downward trend since the mid 1990s" and notes that along parts of Azerbaijan's coastline, including low-lying areas of the Absheron Peninsula, "even small reductions in water level can lead to large areas of exposed seabed". A 2024 EGU abstract on the Gizil-Aghaj State Reserve (Ahadov, Kadirov and Fielding) reports the sea receding to expose an additional 218 km2 of land over 2014-2023, using optical and radar satellite data. A 2021 study of the Kura sub-region (Gasimov, Acta Geodynamica et Geomaterialia) links a sea-level drop of 1.39 m in 1996-2019 to accumulation of land and a 35 % reduction of coastline length.

In the NASA G-REALM altimetry series used here, the July-October mean level changed by -2.135 m between 2000 and 2025 (from +0.162 m to -1.974 m). This is a relative change against a satellite reference, not an absolute height.

Adaptation planning needs a transparent way to see where the shoreline moves, and how much of that movement follows the level drop.

## 2. What was built

The topic is already studied (EGU24-14488; Gasimov 2021). The contribution is not a new discovery but an open tool that anyone can re-run, with a test fixed in advance so that a weak result is reported as weak. Built so far:

- A data pipeline for the G-REALM level series and for Landsat and Sentinel-2 imagery from Microsoft Planetary Computer (no account needed).
- Three sites chosen by a written rule before any shoreline was computed: Gizil-Aghaj, the Kura delta and Lankaran-Astara.
- Yearly shorelines for 2000-2025, a consistency check between two optical sensors, and the pre-registered test.
- An interactive static page, `web/caspian.html`, with a year slider that animates the shorelines, a card per site, a chart of level against shoreline position, and a verdict badge that is always visible. It opens by double click.

## 3. Method in brief

- Shoreline: Landsat Collection 2 Level-2 water composite for one fixed season, July-August, chosen by a rule from scene cloudiness metadata only, before any water or level data were looked at. Water is mapped with a modified NDWI (MNDWI) and an Otsu threshold per year and site. The shoreline position is measured along fixed seaward profiles every 200 m; the site displacement is the unweighted mean over the complete profile set.
- Sea level: the G-REALM Caspian series, season mean.
- Differences use adjacent years only. Each site has 25 annual values and 23 adjacent-year pairs; 2003 has no usable Landsat composite.
- Statistic: r_diff = Pearson(dD, -dL), where D is the shoreline displacement (positive when the sea retreats) and L the sea level. The negative sign of the level change was a clarification of the convention and was made before any result; it is disclosed here.
- Quality control: Sentinel-2 shorelines were compared with Landsat shorelines under one method. This shows consistency between two optical sensors, not ground-truth accuracy. For Gizil-Aghaj it is preliminary, with 6 common years.

## 4. Data and its limits

Sea level comes from the NASA GSFC G-REALM Caspian series (10-day altimetry, open, no explicit licence; the project states that accuracy and application are not guaranteed). Heights are relative to the mean level of a Jason-2 reference pass, so only changes are used. Altimetry over an inland sea can be affected by wind and seiches. Landsat pixels are 30 m.

## 5. Pre-registered criterion

The criterion was written and committed before the validation code was written and before the sea-level series was opened for this purpose. A site is "confirmed (protocol)" only if three conditions hold together: (1) leave-one-year-out RMSE of the model "level from shoreline displacement" is below that of a linear-time-trend baseline; (2) r_diff is at least 0.5; (3) the lower end of the 95 % bootstrap interval of r_diff is above zero. Two or more such sites give the highest overall level; exactly one gives "partial"; none gives "not confirmed". The raw correlation of two falling series is trivially high and is shown only for that reason.

## 6. Results

The test was run once. The overall verdict is **partial (1 of 3 sites)**. This is not a validation of the monitor.

| Site | Site verdict | Years / pairs | RMSE model (m) | RMSE baseline (m) | r_diff | CI low |
|---|---|---|---|---|---|---|
| Gizil-Aghaj | confirmed (protocol) | 25 / 23 | 0.192 | 0.257 | 0.56 | 0.27 |
| Kura delta | not confirmed | 25 / 23 | 0.207 | 0.257 | 0.41 | 0.07 |
| Lankaran-Astara | not confirmed | 25 / 23 | 0.726 | 0.257 | -0.37 | -0.66 |

RMSE is in metres of level. Exposed area, counted over the whole site grid and not as sea-floor exposure, is 225.5 km2 at Gizil-Aghaj, 33.1 km2 at the Kura delta and 0.8 km2 at Lankaran-Astara.

**How to read it.** Gizil-Aghaj passes with a small margin: r_diff 0.56 against the threshold 0.5. The signal comes mainly from the accelerated level fall of 2021-2025; using pairs up to 2018, r_diff is 0.21. It also fails at a fixed MNDWI threshold of 0.0 (0.489). At this site the retreat is about 20-22 m per centimetre of level fall, relative to a satellite datum; no such figure is given for the other sites. A false "confirmed (protocol)" under no relation is rare in simulation (below 1 % per site for linear-trend or random-walk nulls, about 5.5 % under an independent accelerating trend), but it is not impossible.

The test has low power: about 30 % per site, and about 8 % for an overall "confirmed (protocol)" even if the relation is real. So "not confirmed" at the Kura delta and Lankaran-Astara means not shown, not refuted.

Lankaran-Astara is uninformative. Its shoreline moved only about 39 m (about 1.3 Landsat pixels) in 25 years. The negative r_diff comes from one 2020 artefact; without the 2020-2021 pairs it is +0.24. This is a limit of 30 m imagery, not evidence against the physical relation.

**Sensitivity (secondary analyses only; the protocol verdict stands).** The overall verdict depends on analytic choices at the noise level. With a fixed MNDWI threshold of 0.1, Gizil-Aghaj and the Kura delta would both pass, giving an overall outcome of "confirmed (protocol)". At 0.0 only the Kura delta passes, and at 0.2 only Gizil-Aghaj.

## 7. Limitations

- Three sites, one passing: a weak basis for any general statement.
- Shoreline noise at 30 m is comparable to the annual change at two sites; the annual noise floor is roughly 100-200 m (reviewers' estimate, not computed by the pipeline).
- At Gizil-Aghaj the last years (2024-2025) are right-censored, because shorelines beyond the profile end are missing, and the set of valid profiles changes (a composition effect of about +-130 m).
- Exposed area counts the whole site grid. The 218 km2 in EGU24-14488 refers to another place and period and is not a validation of this work.
- Several method decisions were made after seeing how many profiles were valid, but before any shoreline was compared with the level: the profile geometry rules, the 8 km profile margin and the rule of at least 30 valid profiles per site. They are recorded in the repository.
- Sea level is one basin-wide series. One student, one AI assistant, no field data, no external review.

## 8. Next steps (Build phase, all planned, none done)

1. Add more years and other sites, so that power is higher than about 30 % per site.
2. Test finer imagery (Sentinel-2, 10 m) on coasts where Landsat is too coarse.
3. Seek ground data to move from sensor consistency toward accuracy.
4. Test the page with prospective users and adapt it.

## Reproducibility

Repository branch `feature/caspian-monitor`. The protocol commit is `5ac7d0f` and the run-output commit is `bb16513`; the sha256 of `docs/caspian/validation-run.txt` is `00c19d0cfd43edaa2e6b22043d037540765ea00cb058b5081b5f617850cf87f6`. The build recomputes all site reports offline from cached masks and the committed G-REALM file, and stops if the numbers differ from the run output by more than 1e-4. To open the page, double-click `web/caspian.html`; basemap tiles need an internet connection, otherwise shorelines are shown on a plain background.

## Sources

- Euronews, 15 May 2026: https://euronews.com/2026/05/15/the-worlds-largest-lake-is-shrinking-can-central-asian-nations-save-it-before-its-too-late
- Ahadov, Kadirov, Fielding, EGU24-14488: https://meetingorganizer.copernicus.org/EGU24/EGU24-14488.html
- Gasimov (2021), Acta Geodyn. Geomater. 18(4), DOI 10.13168/AGG.2021.0035: https://www.irsm.cas.cz/materialy/acta_content/2021_doi/Gasimov_AGG_2021_0035.pdf
- NASA GSFC Global Water Monitor, G-REALM Caspian: https://earth.gsfc.nasa.gov/gwm/lake/000270
