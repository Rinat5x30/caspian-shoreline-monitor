# Wheat Drought-Risk Map for Azerbaijan

Global Sustainability Challenge 2026-27, Ignite stage. Theme: Adaptation & Resilience. Focus: Food resilience.

Team: I am a student, building this with an AI coding assistant. No external organisations were involved in this work, and none are claimed. The yield data are loaded, but the back-test has not been run yet; this document reports no accuracy figure.

## 1. Problem

Wheat is a key staple, and Azerbaijan depends on imports for much of it. On 26 May 2026 the President said the country is only 55% self-sufficient in wheat, as reported by Trend. The same Trend report, reproduced by UkrAgroConsult, gives domestic wheat output of 1.573 million tons and wheat imports of 1.267 million tons (https://ukragroconsult.com/?p=316982). FAO forecasts total cereal import requirements, mainly wheat, at 1.4 million tonnes for 2025/26 (FAO GIEWS country brief, reference date 30 April 2026: https://www.fao.org/giews/countrybrief/country.jsp?code=AZE).

When a dry season cuts the domestic harvest, the gap may be covered by imports. We are not aware of an open district-level view of drought risk for wheat in Azerbaijan. We could not find a published district-level drought-risk product for Azerbaijani wheat; this describes our search, not a claim that none exists.

## 2. Solution

An open district-level drought-risk map for winter wheat in Azerbaijan. It combines satellite vegetation greenness with rainfall and temperature into a seasonal drought index and shows, per district, how far a season departs from its normal. The index is compared with official yields from past years, so a user can see how well the signal tracked real harvests.

## 3. Method

No machine learning. The approach is deliberately simple and explainable:

1. Compute seasonal anomalies (departures from each district's own multi-year normal) for NDVI (a greenness index from MODIS satellite imagery), rainfall and temperature (ERA5 reanalysis, retrieved through Open-Meteo). NDVI is restricted to cropland with the ESA WorldCover mask; district borders come from geoBoundaries.
2. Combine the anomalies into one composite drought index per district and season.
3. Target variable: yield anomaly in percent, i.e. official yield after removing each district's linear trend.
4. Relate the index to the yield anomaly with a simple linear model and evaluate it by leave-one-year-out back-testing.

Because only six seasons are available, the model uses a single composite predictor rather than three separate ones, to limit overfitting.

## 4. Data and its limits

- Yields: State Statistical Committee of Azerbaijan, yearbook "The Agriculture of Azerbaijan", table 2.187 "Yield of wheat, 100 kg/ha" (editions 2024 and 2026, https://www.stat.gov.az/menu/6/statistical_yearbooks/?lang=en). The extracted file covers 66 districts and cities, six years each (2020-2025), 396 rows. Values for 2022 and 2023 appear in both editions and matched.
- Units with gaps in the published tables were excluded (Ganja city, Dashkasan, Khojavand, Mingachevir city, Aghdara).
- Winter and spring wheat are reported together, so the target mixes both.
- Earlier editions could not be retrieved, which is why the series is short.
- NDVI: MODIS, with a history from 2000 and pixels of hundreds of metres. Weather: ERA5, a gridded reanalysis rather than station data.

## 5. Validation plan

The back-test is not finished, so no results are stated here. The plan:

- Leave-one-year-out: for each of the six years, fit the model on the other five and predict the held-out year, for every district.
- Metrics: Pearson correlation between predicted and observed yield anomaly, RMSE in percentage points, and the share of below-normal years correctly flagged.
- Reporting rule: we publish whatever the test shows. A weak or null signal will be reported as such, together with the districts and cities where it fails.
- Sensitivity check: repeat the test with the recently liberated districts removed, to see how much they affect the result.

## 6. What the prototype will show

A static web map (no server) with a district-coloured drought-risk layer for each season, a district panel showing the index next to the official yield anomaly, and a validation page with the back-test table and its caveats. The map will be shown only at the spatial level the back-test covers.

## 7. Limitations

- Lead time: the time between a risk signal and harvest is not defined or tested; the index describes a season, not a forecast.
- Six years of yield data: too few for a robust statistical relationship; estimates will be uncertain.
- One composite predictor: it cannot separate the effects of heat, water shortage and vegetation stress.
- Liberated districts: several recently liberated districts (for example Jabrayil, Lachin, Zangilan, Kalbajar, Gubadli, Khojaly) show low official yields that may reflect land being brought back into use rather than weather. We have not established the causes and will test the sensitivity of the result.
- MODIS resolution: pixels of hundreds of metres mix fields with other land, so field-scale conditions are not resolved.
- No field-level validation: the check is against district yield statistics only. No farm measurements or user feedback are used.
- The problem figures above come from a news agency report and an FAO brief. We did not find a source we could open for a quantified climate trend, so we do not state one.

## 8. Next steps (Build phase)

1. Finish the leave-one-year-out back-test and publish the numbers, whatever they are.
2. Run the sensitivity check on liberated districts, and on winter versus spring wheat if the data allow.
3. Add more years if older yearbook editions can be obtained.
4. Complete the static map and the validation page.
5. Document the method and data so others can reproduce it, and test the map's readability with prospective users, which has not been done so far.
