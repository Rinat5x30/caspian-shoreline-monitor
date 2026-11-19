# Interface notes (Task 10)

## Decisions

- Direction: a cinematic full-viewport hero. The memorable element is the year readout: a very large
  serif numeral coloured by the year scale (cold blue for 2000 to hot crimson for 2025). The slider track
  is the same colour scale, so the slider is also the legend. Everything else stays quiet.
- Type: Instrument Serif (title, year, big numbers) and Atkinson Hyperlegible (text, chosen for legibility);
  fallback stacks Georgia / system-ui. Tabular numerals for figures.
- Colour: deep ink navy for dark, pale blue-grey for light, all as CSS variables; theme by
  `prefers-color-scheme` and a visible toggle (`data-theme`, stored in localStorage when available).
  Verdict states use colour plus a glyph and text (check, cross, dash), never colour alone.
- Verdict honesty: the badge text, the "What this does and does not show" panel, card chips and the
  sensitivity table are all generated from the data object. The HTML holds no verdict words. The badge
  sits in the first screen at 390, 768 and 1440 px (bottom edge at 274, 241 and 349 px).
  "Metres of retreat per cm" appears only when `metres_per_cm_of_level_fall` is not null (confirmed site only).
- Chart: hand-written SVG, two stacked panels sharing the year axis (level, shoreline distance) instead of
  a dual-axis plot; null values break the line and are marked "no data <year>", never zero. Text alternative in
  `<title>`/`<desc>`; click on the chart sets the year.
- Map: Leaflet 1.9.4 (cdnjs, SRI). Satellite imagery is the default (it shows the retreat best); a toggle
  switches to the Esri gray canvas that follows the theme. Lines: selected year bright with a glow, earlier years
  as a fainter trail, later years hidden. If tiles fail a note appears; if Leaflet itself fails, a plain SVG
  renderer draws the same lines.
- Motion: one sequence on user request (play, 520 ms per year, stops at 2025). `prefers-reduced-motion`
  turns transitions off and uses fitBounds instead of flyTo; there is no autoplay.

## Deviation from the brief

CARTO tiles (basemaps.cartocdn.com) now return a "API KEY REQUIRED" watermark, so they are not used.
The dark and light basemaps are Esri Canvas tiles from server.arcgisonline.com (still within the allowed hosts).

## Attributions

See `web/README.md` (data sources taken from `data.meta.sources`, plus Esri basemaps, Leaflet, Google Fonts).

## Verification

Screenshots in `docs/caspian/screens/` (Playwright, file:// URL): 1440, 768 and 390 px, dark and light, years
2000 and 2025; chart with the 2003 gap; full pages; offline fallback.
