# Caspian shoreline monitor (static page)

## Open

Double-click `caspian.html`. It works from disk (file://): the data are loaded by a plain
script tag (`data/caspian-data.js`), not by fetch. Basemap tiles, Leaflet and fonts need an
internet connection; without it the page still shows the shorelines on a plain background.

If a browser blocks something, serve the folder instead:

```
cd web
python -m http.server 8000
```

then open http://localhost:8000/caspian.html.

## Data

`data/caspian-data.js` (`window.CASPIAN_DATA = {...}`) and `data/caspian-data.json` are one
object serialised twice. Regenerate both from the repository root:

```
python scripts/build_caspian.py
```

Do not edit them by hand. Every verdict text on the page is built from this object.

## Files

- `caspian.html`, `css/caspian.css`, `js/caspian.js` (the pure helpers are exported for node tests).
- Tests: `tests/caspian/test_web.py`.

## Attribution

- Sea level: NASA/GSFC Global Water Monitor (formerly USDA/NASA G-REALM), Caspian Sea, lake 000270.
- Landsat Collection 2 Level-2: courtesy of the U.S. Geological Survey, via Microsoft Planetary Computer.
- Contains modified Copernicus Sentinel data processed by ESA (Sentinel-2 L2A, Planetary Computer).
- Contains modified ESA WorldCover 2021 data (c) ESA WorldCover project (CC BY 4.0).
- Copernicus DEM GLO-90, provided under COPERNICUS by the EU and ESA.
- Basemap tiles: Esri Canvas (World Dark Gray Base, World Light Gray Base): Esri, DeLorme, NAVTEQ,
  TomTom, Intermap, Garmin, FAO, NOAA, USGS, OpenStreetMap contributors.
- Optional imagery: Esri World Imagery (Esri, Maxar, Earthstar Geographics and the GIS User Community).
- Map library: Leaflet 1.9.4 (cdnjs). Fonts: Instrument Serif and Atkinson Hyperlegible (Google Fonts, SIL OFL).

## Basemap tile services

The basemaps use the legacy Esri tile endpoints on server.arcgisonline.com, which require visible
attribution (shown in the map corner and in the footer). Esri's terms for heavy or commercial use may
require an account or API key. This prototype is non-commercial and low-volume; replace the basemap
if that changes. CARTO tiles were tried first and not used because they now require an API key.
