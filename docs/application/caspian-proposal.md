# Caspian Shoreline Monitor: a planned open, repeatable check of shoreline retreat off Azerbaijan

Global Sustainability Challenge 2026-27 | Theme: Adaptation & Resilience | Focus: Water | Stage: Ignite

## Team

I am a student, building this with an AI coding assistant. No external organisation has been involved. Everything below is a plan and a prototype in progress, and shoreline results do not exist yet.

## 1. Problem

The Caspian Sea, which Euronews calls the world's largest lake, has been falling. A May 2026 Euronews report describes "a consistent downward trend since the mid 1990s" and notes that along parts of Azerbaijan's coastline, including low-lying areas of the Absheron Peninsula, "even small reductions in water level can lead to large areas of exposed seabed". A 2024 EGU abstract on the Gizil-Aghaj State Reserve (Ahadov, Kadirov and Fielding) reports the sea receding to expose an additional 218 km2 of land over 2014-2023, using optical and radar satellite data. A 2021 study of the Kura sub-region (Gasimov, Acta Geodynamica et Geomaterialia) links a sea-level drop of 1.39 m in 1996-2019 to accumulation of land and a 35 % reduction of coastline length.

In our own data, the July-October mean level in the NASA G-REALM Caspian altimetry series changed by -2.135 m between 2000 and 2025 (from +0.162 m to -1.974 m). This is a relative level change in our G-REALM series, measured against a satellite reference, not an absolute height.

For coastal communities, infrastructure and ecosystems, a falling sea means moving shorelines. Adaptation planning needs a transparent way to see where the shoreline moves. The question this project sets out to test is how much of that movement is explained by the level drop.

## 2. Solution

The planned solution is an open, repeatable monitor of Caspian shoreline retreat off Azerbaijan. It will map yearly shorelines from satellite imagery, test them against independent altimetry sea level under a rule fixed in advance, and publish a static interactive map. The planned map will show shorelines by year with an animated time slider, a card for every site, a chart of level versus shoreline position, and a verdict badge that is always visible, including when the verdict is negative or inconclusive. None of these map elements exists yet.

Honest novelty: the topic is already studied (see the EGU abstract and Gasimov above). Our intended contribution is not a new discovery. It will be an open tool that anyone can re-run for a new year, with a pre-registered success test, so that a weak result is reported as weak.

## 3. Method

The method is planned and its rules are fixed in advance.

- Shorelines: Landsat Collection 2 composites for each year 2000-2025 (30 m), plus Sentinel-2 (10 m, from 2017) for the current shoreline and a cross-check. Imagery will be read from Microsoft Planetary Computer without an account.
- Water mapping: a water index (MNDWI) on a single fixed two-month season within July-October. The season is not chosen yet; it will be chosen by a rule: the continuous two-month window in July-October with the lowest median cloudiness, decided from scene metadata only, before looking at any water, shoreline or level data.
- Shoreline position: the mean signed distance of the waterline from a fixed reference line along cross-shore profiles, in metres (positive means the sea retreats).
- Sites: at least three, to be chosen by a written rule (gently sloping, no vegetation, no delta waterlogging) before the relationship is examined. Delta sites are allowed but will be marked.
- Quality control: Landsat versus Sentinel-2 shorelines over 2017-2025; a median disagreement above 30 m (one Landsat pixel) will flag the site. The map will show an uncertainty of at least 30 m.

## 4. Data and its limits

Sea level comes from the NASA GSFC G-REALM Caspian series (10-day altimetry, file lake000270.10d.2.txt). It is open, needs no account, and has no explicit licence; the project states that accuracy and application of the products are not guaranteed. Heights are relative to the mean level of a Jason-2 reference pass, so we work only with changes, never absolute heights. For July-October, 26 of 26 years (2000-2025) have at least three observation dates. We checked the series for steps: at mission boundaries, none above 0.09 m, comparable to noise (a few isolated 0.20 m flags elsewhere sit at the noise tail; see the jump-search section of data/raw/caspian/SOURCES.md). Altimetry over an inland sea can be affected by wind and seiches, and the product itself flags winter ice.

## 5. Pre-registered success criterion

Fixed before any result is seen, and not to be changed afterwards. For each site and for all sites together, using annual values for 2000-2025:

1. Primary: leave-one-year-out; the model "level from shoreline displacement" must have a lower RMSE than the model "level from a linear time trend only" (parameters of both estimated on training years only).
2. Secondary: the Pearson correlation between year-on-year differences (first increments) of shoreline displacement and level must be at least 0.5, with the lower bound of its 95 % bootstrap interval above 0.
3. A site is confirmed only if both are met; otherwise the verdict is "not confirmed" or "partial" with the reason. The raw correlation will be published separately and labelled as dominated by the shared trend.

Overall verdict: at least two measurable sites meeting the site rule gives "confirmed"; one gives "partial"; otherwise "not confirmed". A site is measurable if the shoreline is found in at least 20 of 26 years on at least 80 % of profiles; otherwise it will be published as "not measurable" with the reason.

The raw correlation of two falling series is trivially high, so the criterion uses differences and a trend baseline instead.

## 6. What the prototype shows

Today the prototype contains the sea-level series and its quality checks: the 26 usable years, the mission-boundary check, and the -2.135 m change quoted above. The map, the shoreline extraction, the site list, the season choice, the Landsat versus Sentinel-2 quality control and the verdict do not exist yet. They are planned, and this application makes no claim about their outcome. The map is planned so that the verdict badge and the uncertainty will stay visible next to every shoreline.

## 7. Limitations

- The topic is already studied; we aim to add a repeatable open tool, not a new phenomenon.
- G-REALM heights are relative to a satellite datum.
- 30 m Landsat pixels on gently sloping shores can hide small retreat, because one pixel may span a large share of a year's change.
- Sites will be chosen by a documented rule before looking at results.
- With a small number of years, results may be inconclusive, and will be reported as such.
- One student with one AI coding assistant, no field data, and no external review so far.

## 8. Next steps (Build phase)

1. Build shoreline extraction for the chosen sites and publish the site list with the reasons for each choice.
2. Run the pre-registered criterion once, unchanged, and publish the verdict whatever it is.
3. Publish the interactive map and the code so the monitor can be repeated for each new year.
4. Report metres of retreat per centimetre of level drop for each measurable site.

## Sources

- Euronews, 15 May 2026: https://euronews.com/2026/05/15/the-worlds-largest-lake-is-shrinking-can-central-asian-nations-save-it-before-its-too-late
- Ahadov, Kadirov, Fielding, EGU24-14488: https://meetingorganizer.copernicus.org/EGU24/EGU24-14488.html
- Gasimov (2021), Acta Geodyn. Geomater. 18(4), DOI 10.13168/AGG.2021.0035: https://www.irsm.cas.cz/materialy/acta_content/2021_doi/Gasimov_AGG_2021_0035.pdf
- NASA GSFC Global Water Monitor, G-REALM Caspian: https://earth.gsfc.nasa.gov/gwm/lake/000270
