(function () {
  'use strict';

  const NO_DATA = 'no data';
  const MINUS = '−';
  const SCALE = {
    dark: ['#4aa3ff', '#35e0c8', '#f6e05e', '#ff9f43', '#ff4d6d'],
    light: ['#1558c0', '#0b8a7a', '#b08900', '#d9650a', '#c4153f'],
  };

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function fmt(value, digits, unit) {
    if (value == null || Number.isNaN(Number(value))) return NO_DATA;
    const text = Number(value).toLocaleString('en-US', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
    return text.replace('-', MINUS) + (unit ? ' ' + unit : '');
  }

  function verdictKind(key) {
    const k = String(key || '').toLowerCase();
    if (/^not[ _]meas/.test(k)) return 'neutral';
    if (/^not/.test(k)) return 'negative';
    if (/^conf/.test(k)) return 'positive';
    return 'neutral';
  }

  function badgeParts(data) {
    const overall = data.overall || {};
    const note = String(data.validation_note || '');
    const sentences = note.split(/(?<=\.)\s+/);
    const notice = sentences.find(function (s) { return /not a validation/i.test(s); }) || note;
    const positive = (data.sites || []).find(function (s) {
      return verdictKind(s.report && s.report.verdict) === 'positive';
    });
    const label = positive ? positive.report.verdict_label : '';
    const n = overall.n_confirmed;
    const body = n > 0 && label
      ? n + ' of ' + overall.n_measurable + ' sites: ' + label
      : n + ' of ' + overall.n_measurable + ' sites reached the protocol verdict';
    const key = String(overall.verdict || '');
    const state = key === 'partial' ? 'partial' : verdictKind(key);
    return {
      head: 'OVERALL: ' + key.replace(/_/g, ' ').toUpperCase(),
      body: body,
      notice: notice,
      state: state,
    };
  }

  function hexToRgb(hex) {
    const v = parseInt(hex.slice(1), 16);
    return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
  }

  function yearColor(year, y0, y1, theme) {
    const stops = SCALE[theme === 'light' ? 'light' : 'dark'];
    const t = y1 > y0 ? Math.min(1, Math.max(0, (year - y0) / (y1 - y0))) : 1;
    const x = t * (stops.length - 1);
    const i = Math.min(stops.length - 2, Math.floor(x));
    const a = hexToRgb(stops[i]);
    const b = hexToRgb(stops[i + 1]);
    const f = x - i;
    const rgb = a.map(function (c, k) { return Math.round(c + (b[k] - c) * f); });
    return 'rgb(' + rgb.join(',') + ')';
  }

  function linePath(xs, vals, sx, sy) {
    const parts = [];
    let open = false;
    for (let i = 0; i < xs.length; i++) {
      if (vals[i] == null) {
        open = false;
        continue;
      }
      const pt = sx(xs[i]) + ',' + sy(vals[i]);
      parts.push((open ? 'L' : 'M') + pt);
      open = true;
    }
    return parts.join(' ');
  }

  function niceTicks(min, max, count) {
    const span = max - min || 1;
    const raw = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = (norm < 1.5 ? 1 : norm < 3.5 ? 2 : norm < 7.5 ? 5 : 10) * mag;
    const ticks = [];
    for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) {
      ticks.push(Math.round(v / step) * step);
    }
    return ticks;
  }

  function seriesFor(data, siteId) {
    const lvs = data.level_vs_shore;
    const site = lvs.sites[siteId];
    return { years: lvs.years, level: lvs.level_m, disp: site ? site.displacement_m : [] };
  }

  function siteById(data, id) {
    return (data.sites || []).find(function (s) { return s.id === id; });
  }

  function numbersModel(data, siteId, year) {
    const s = seriesFor(data, siteId);
    const i = s.years.indexOf(year);
    const d = i >= 0 ? s.disp[i] : null;
    const l = i >= 0 ? s.level[i] : null;
    const first = s.disp[0];
    const site = siteById(data, siteId);
    return {
      disp: fmt(d, 0, 'm'),
      level: fmt(l, 2, 'm'),
      since: d != null && first != null && year !== s.years[0]
        ? (d - first >= 0 ? '+' : '') + fmt(d - first, 0, 'm')
        : null,
      hasShoreline: d != null,
      area: site && site.exposed_km2 ? fmt(site.exposed_km2.whole_site_grid, 1, 'km²') : NO_DATA,
      areaSpan: site && site.exposed_km2 ? site.exposed_km2.from_year + ' to ' + site.exposed_km2.to_year : '',
    };
  }

  function chartSvg(data, siteId, year) {
    const s = seriesFor(data, siteId);
    const site = siteById(data, siteId);
    const W = 640, H = 380, L = 62, R = 16;
    const panels = [
      { key: 'level', top: 16, bottom: 158, label: 'Relative level (m)', cls: 'ln-level', digits: 1 },
      { key: 'disp', top: 214, bottom: 332, label: 'Shoreline distance (m)', cls: 'ln-shore', digits: 0 },
    ];
    const y0 = s.years[0], y1 = s.years[s.years.length - 1];
    const sx = function (v) { return (L + ((v - y0) / (y1 - y0)) * (W - L - R)).toFixed(1); };
    let out = '';
    const desc = [];
    panels.forEach(function (p) {
      const vals = s[p.key];
      const present = vals.filter(function (v) { return v != null; });
      const lo = Math.min.apply(null, present), hi = Math.max.apply(null, present);
      const pad = (hi - lo) * 0.08 || 1;
      const ticks = niceTicks(lo - pad, hi + pad, 4);
      const dmin = Math.min(lo - pad, ticks[0]), dmax = Math.max(hi + pad, ticks[ticks.length - 1]);
      const sy = function (v) {
        return (p.bottom - ((v - dmin) / (dmax - dmin)) * (p.bottom - p.top)).toFixed(1);
      };
      ticks.forEach(function (t) {
        out += '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + sy(t) + '" y2="' + sy(t) + '"/>' +
          '<text class="tick" x="' + (L - 8) + '" y="' + sy(t) + '" text-anchor="end" dominant-baseline="middle">' +
          esc(fmt(t, p.digits)) + '</text>';
      });
      s.years.forEach(function (yr, i) {
        if (vals[i] == null) {
          out += '<line class="gap" x1="' + sx(yr) + '" x2="' + sx(yr) + '" y1="' + p.top + '" y2="' + p.bottom + '"/>' +
            '<text class="gap-label" x="' + sx(yr) + '" y="' + (p.top + 12) + '" text-anchor="middle">' + NO_DATA + ' ' + yr + '</text>';
        }
      });
      out += '<path class="' + p.cls + '" d="' + linePath(s.years, vals, sx, sy) + '"/>';
      s.years.forEach(function (yr, i) {
        if (vals[i] != null) out += '<circle class="dot ' + p.cls + '" cx="' + sx(yr) + '" cy="' + sy(vals[i]) + '" r="2"/>';
      });
      const i = s.years.indexOf(year);
      if (i >= 0 && vals[i] != null) {
        out += '<circle class="now ' + p.cls + '" cx="' + sx(year) + '" cy="' + sy(vals[i]) + '" r="6"/>';
      }
      out += '<text class="axis-title" transform="rotate(-90)" x="' + (-(p.top + p.bottom) / 2) + '" y="14" text-anchor="middle">' + p.label + '</text>';
      const first = vals.findIndex(function (v) { return v != null; });
      let last = vals.length - 1;
      while (last > 0 && vals[last] == null) last--;
      desc.push(p.label + ' goes from ' + fmt(vals[first], p.digits) + ' in ' + s.years[first] + ' to ' + fmt(vals[last], p.digits) + ' in ' + s.years[last]);
      const gaps = s.years.filter(function (yr, k) { return vals[k] == null; });
      if (gaps.length) desc.push(p.label + ' has no data in ' + gaps.join(', ') + ' (a gap, not zero)');
    });
    for (let yr = y0; yr <= y1; yr += 5) {
      out += '<text class="tick" x="' + sx(yr) + '" y="' + (H - 12) + '" text-anchor="middle">' + yr + '</text>' +
        '<line class="grid v" x1="' + sx(yr) + '" x2="' + sx(yr) + '" y1="' + panels[0].top + '" y2="' + panels[1].bottom + '"/>';
    }
    out += '<line class="sel" x1="' + sx(year) + '" x2="' + sx(year) + '" y1="' + panels[0].top + '" y2="' + panels[1].bottom + '"/>';
    out += '<rect class="hit" x="' + L + '" y="0" width="' + (W - L - R) + '" height="' + H + '" fill="transparent" data-x0="' + L + '" data-x1="' + (W - R) + '"/>';
    const title = 'Relative sea level and shoreline distance by year, ' + (site ? site.name : siteId) + ', year ' + year + ' marked';
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-labelledby="chart-t chart-d" preserveAspectRatio="xMidYMid meet">' +
      '<title id="chart-t">' + esc(title) + '</title><desc id="chart-d">' + esc(desc.join('. ') + '.') + '</desc>' + out + '</svg>';
  }

  function chipHtml(report) {
    const kind = verdictKind(report.verdict);
    return '<span class="vchip" data-kind="' + kind + '">' + esc(report.verdict_label || report.verdict) + '</span>';
  }

  function qcHtml(qc) {
    if (!qc || qc.status !== 'computed') return 'not computed';
    return fmt(qc.median_abs_diff_m, 1, 'm') + ' median Landsat vs Sentinel-2 difference over ' + qc.n_common_years +
      ' common years, flag ok: ' + (qc.ok ? 'yes' : 'no') + (qc.preliminary ? ' (preliminary)' : '');
  }

  function cardHtml(site, data, selectedId) {
    const r = site.report || {};
    const notes = site.caveat_notes || [];
    const more = notes.slice(1);
    const metres = site.metres_per_cm_of_level_fall;
    const selected = site.id === selectedId;
    return '<article class="card" data-site="' + esc(site.id) + '"' + (selected ? ' aria-current="true"' : '') + '>' +
      '<header><h3>' + esc(site.name) + '</h3>' + chipHtml(r) + '</header>' +
      '<p class="reason">' + esc(r.reason || '') + '</p>' +
      '<dl class="metrics">' +
      '<div><dt>r_diff</dt><dd>' + esc(fmt(r.r_diff, 2)) + '</dd></div>' +
      '<div><dt>CI lower bound</dt><dd>' + esc(fmt(r.r_diff_ci_low, 2)) + '</dd></div>' +
      '<div><dt>RMSE model</dt><dd>' + esc(fmt(r.rmse_model, 3)) + '</dd></div>' +
      '<div><dt>RMSE time-trend baseline</dt><dd>' + esc(fmt(r.rmse_baseline, 3)) + '</dd></div>' +
      '<div><dt>Years / pairs</dt><dd>' + esc(r.n_years) + ' / ' + esc(r.n_pairs) + '</dd></div>' +
      '<div><dt>Exposed area, whole grid</dt><dd>' + esc(fmt(site.exposed_km2 && site.exposed_km2.whole_site_grid, 1, 'km²')) + '</dd></div>' +
      '</dl>' +
      (metres ? '<p class="mpc"><strong>' + esc(metres) + '</strong> of retreat per cm of level fall</p>' : '') +
      '<p class="qc"><span>Optical consistency check</span> ' + esc(qcHtml(site.qc)) + '</p>' +
      (notes.length ? '<p class="note first">' + esc(notes[0]) + '</p>' : '') +
      (more.length
        ? '<details><summary>' + more.length + ' more caveat' + (more.length > 1 ? 's' : '') + '</summary><ul>' +
          more.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul></details>'
        : '') +
      '<button type="button" class="chip-btn show" data-site="' + esc(site.id) + '">Show on map</button>' +
      '</article>';
  }

  function panelHtml(data) {
    const m = data.meta || {};
    const re = m.reviewer_estimates || {};
    const items = [
      m.uncertainty_note,
      m.level_datum_note,
      m.qc_note,
      m.literature_note,
      re.power_per_site_percent != null
        ? 'Power of the test: about ' + re.power_per_site_percent + ' % per site and ' + re.power_overall_percent +
          ' % overall (reviewers’ estimate), so a negative result means not shown, not refuted.'
        : null,
      re.composition_effect_m != null
        ? 'Changes in which profiles are valid can move a site mean by about ' + re.composition_effect_m + ' m (reviewers’ estimate).'
        : null,
    ].filter(Boolean);
    return '<p class="note-main">' + esc(data.validation_note) + '</p>' +
      '<div class="cols"><div><h3>It shows</h3><ul>' +
      '<li>Shorelines drawn from Landsat for each year, with the sea level series next to them.</li>' +
      '<li>The result of one pre-registered test per site, run once.</li></ul></div>' +
      '<div><h3>It does not show</h3><ul>' +
      '<li>Accuracy against ground measurements.</li>' +
      '<li>Sea-floor exposure: the area figure counts every water-to-land change on the site grid.</li>' +
      '<li>Anything beyond these three sites.</li></ul></div></div>' +
      '<ul class="facts">' + items.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') + '</ul>';
  }

  function sensitivityHtml(data) {
    const names = {};
    (data.sites || []).forEach(function (s) { names[s.id] = s.name; });
    const rows = Object.keys(data.threshold_overall || {}).map(function (k) {
      const t = data.threshold_overall[k];
      const met = (t.criterion_met_sites || []).map(function (id) { return names[id] || id; });
      return '<tr><th scope="row">' + esc(k) + '</th><td>' + esc(t.overall) + '</td><td>' +
        esc(met.length ? met.join(', ') : 'none') + '</td><td>' + esc(t.label) + '</td></tr>';
    });
    return '<table><caption>Overall result when the water threshold is fixed instead of chosen per image. These rows do not replace the protocol verdict.</caption>' +
      '<thead><tr><th scope="col">Fixed threshold</th><th scope="col">Overall</th><th scope="col">Sites meeting the criterion</th><th scope="col">Status</th></tr></thead>' +
      '<tbody>' + rows.join('') + '</tbody></table>';
  }

  function legendHtml(data) {
    const m = data.meta || {};
    const noise = (data.sites || []).filter(function (s) { return s.noise_floor_note; }).map(function (s) {
      const range = String(s.noise_floor_note).split(' annual')[0];
      return s.name.split(' (')[0] + ': annual noise floor ' + range + ' (reviewers’ estimate)';
    });
    const years = (m.period || [2000, 2025]);
    return '<h2 id="legend-title">How to read the lines</h2>' +
      '<div class="scale" aria-hidden="true"></div>' +
      '<div class="scale-ends"><span>' + years[0] + '</span><span>' + years[1] + '</span></div>' +
      '<p>One line per year: the water edge in July and August. The bright line is the selected year; fainter lines are earlier years.</p>' +
      '<p class="unc">Lines are ±' + esc(m.uncertainty_m) + ' m at least (one Landsat pixel). ' + esc(noise.join('; ')) + '.</p>' +
      '<details><summary>What the protocol verdict means</summary>' +
      '<p>Each site gets one label from a test fixed before the data were opened. It asks whether the sea level model beats a plain time trend, whether the lower bound of the bootstrap interval is above zero, and whether r_diff reaches ' + esc(m.r_diff_min) + '.</p>' +
      '<p>The label describes this one test on one site. It is not a measure of accuracy.</p></details>';
  }

  function sourcesHtml(data) {
    const src = (data.meta && data.meta.sources) || [];
    return '<h2>Data and attribution</h2><ul class="src">' + src.map(function (s) {
      const link = /^https?:\/\//.test(s.url || '')
        ? ' <a href="' + esc(s.url) + '" rel="noopener noreferrer">' + esc(s.url) + '</a>'
        : '';
      return '<li><strong>' + esc(s.name) + '</strong>. ' + esc(s.use) + '. ' + esc(s.attribution) +
        '. Licence: ' + esc(s.licence) + '.' + link + '</li>';
    }).join('') + '</ul>' +
      '<p class="basemap">Basemap: Esri Canvas (Esri, DeLorme, NAVTEQ and others, OpenStreetMap contributors); optional imagery: Esri, Maxar, Earthstar Geographics.</p>';
  }

  function numbersHtml(data, siteId, year) {
    const m = numbersModel(data, siteId, year);
    const site = siteById(data, siteId);
    return '<h2 id="numbers-title">' + esc(site ? site.name : '') + ', ' + year + '</h2>' +
      '<div class="num"><p class="big">' + esc(m.disp) + '</p>' +
      '<p class="cap">Shoreline distance from the landward reference line. ' +
      (m.since ? 'Since 2000: ' + esc(m.since) + '.' : '') + (m.hasShoreline ? '' : ' No shoreline could be drawn for this year.') + '</p></div>' +
      '<div class="num"><p class="big">' + esc(m.level) + '</p>' +
      '<p class="cap">Relative sea level, measured from a satellite datum (not a geodetic height).</p></div>' +
      '<div class="num"><p class="big">' + esc(m.area) + '</p>' +
      '<p class="cap">Exposed area ' + esc(m.areaSpan) + '. Whole site grid: all water-to-land change, not sea-floor exposure.</p></div>';
  }

  const api = {
    esc: esc, fmt: fmt, verdictKind: verdictKind, badgeParts: badgeParts, yearColor: yearColor,
    linePath: linePath, niceTicks: niceTicks, numbersModel: numbersModel, chartSvg: chartSvg,
    cardHtml: cardHtml, panelHtml: panelHtml, sensitivityHtml: sensitivityHtml,
    legendHtml: legendHtml, sourcesHtml: sourcesHtml, numbersHtml: numbersHtml,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined' || !window.CASPIAN_DATA) return;

  /* ---------- browser part ---------- */
  const data = window.CASPIAN_DATA;
  const $ = function (id) { return document.getElementById(id); };
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  const years = data.level_vs_shore.years;
  const Y0 = years[0], Y1 = years[years.length - 1];
  const state = { site: data.sites[0].id, year: Y1, theme: 'dark', sat: true, playing: false };

  function initialTheme() {
    let saved = null;
    try { saved = localStorage.getItem('caspian-theme'); } catch (e) { saved = null; }
    if (saved === 'dark' || saved === 'light') return saved;
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }

  function allCoords(site) {
    const pts = [];
    site.shoreline.features.forEach(function (f) {
      const g = f.geometry;
      const lines = g.type === 'MultiLineString' ? g.coordinates : [g.coordinates];
      lines.forEach(function (ln) { ln.forEach(function (c) { pts.push(c); }); });
    });
    return pts;
  }

  function boundsOf(site) {
    const pts = allCoords(site);
    const lons = pts.map(function (p) { return p[0]; }), lats = pts.map(function (p) { return p[1]; });
    return [[Math.min.apply(null, lats), Math.min.apply(null, lons)], [Math.max.apply(null, lats), Math.max.apply(null, lons)]];
  }

  function featureLines(f) {
    const g = f.geometry;
    return g.type === 'MultiLineString' ? g.coordinates : [g.coordinates];
  }

  function lineStyle(y, year, theme) {
    const color = yearColor(y, Y0, Y1, theme);
    if (y > year) return { color: color, opacity: 0, weight: 0 };
    if (y === year) return { color: color, opacity: 1, weight: 4 };
    return { color: color, opacity: 0.5, weight: 1.6 };
  }

  function glowStyle(y, year, theme) {
    return { color: yearColor(y, Y0, Y1, theme), opacity: y === year ? 0.3 : 0, weight: y === year ? 12 : 0 };
  }

  function fitOptions() {
    const wide = window.matchMedia('(min-width: 1000px)').matches;
    return wide
      ? { paddingTopLeft: [48, 250], paddingBottomRight: [48, 260], maxZoom: 13 }
      : { padding: [32, 32], maxZoom: 13 };
  }

  function createLeafletMap(el, onTileError) {
    const L = window.L;
    const map = L.map(el, { zoomSnap: 0.25, zoomDelta: 0.5, minZoom: 4, maxZoom: 17 });
    const esri = function (service, native, credit) {
      return L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/' + service + '/MapServer/tile/{z}/{y}/{x}', {
        maxZoom: 17, maxNativeZoom: native, attribution: credit,
      });
    };
    const canvasCredit = 'Tiles © Esri, DeLorme, NAVTEQ, TomTom, Intermap, Garmin, FAO, NOAA, USGS, OpenStreetMap contributors';
    const bases = {
      dark: esri('Canvas/World_Dark_Gray_Base', 16, canvasCredit),
      light: esri('Canvas/World_Light_Gray_Base', 16, canvasCredit),
      sat: esri('World_Imagery', 17, 'Tiles © Esri, Maxar, Earthstar Geographics and the GIS User Community'),
    };
    Object.keys(bases).forEach(function (k) { bases[k].on('tileerror', onTileError); });
    const groups = {};
    const layers = {};
    data.sites.forEach(function (site) {
      groups[site.id] = L.layerGroup();
      layers[site.id] = {};
      site.shoreline.features.forEach(function (f) {
        const y = f.properties.year;
        const latlngs = featureLines(f).map(function (ln) { return ln.map(function (c) { return [c[1], c[0]]; }); });
        const glow = L.polyline(latlngs, { interactive: false, lineCap: 'round', lineJoin: 'round', opacity: 0, weight: 0 });
        const line = L.polyline(latlngs, { interactive: false, lineCap: 'round', lineJoin: 'round', opacity: 0, weight: 0 });
        groups[site.id].addLayer(glow).addLayer(line);
        layers[site.id][y] = { line: line, glow: glow };
      });
    });
    let activeBase = null, activeSite = null;
    return {
      setBase: function (theme, sat) {
        const next = bases[sat ? 'sat' : theme];
        if (activeBase === next) return;
        if (activeBase) map.removeLayer(activeBase);
        activeBase = next.addTo(map);
        activeBase.bringToBack();
      },
      setSite: function (id, animate) {
        if (activeSite) map.removeLayer(groups[activeSite]);
        activeSite = id;
        groups[id].addTo(map);
        const b = boundsOf(siteById(data, id));
        const opts = fitOptions();
        if (animate && !reduce.matches) map.flyToBounds(b, Object.assign({ duration: 1.6 }, opts));
        else map.fitBounds(b, opts);
      },
      setYear: function (year, theme) {
        if (!activeSite) return;
        const set = layers[activeSite];
        Object.keys(set).forEach(function (k) {
          const y = Number(k);
          set[k].line.setStyle(lineStyle(y, year, theme));
          set[k].glow.setStyle(glowStyle(y, year, theme));
        });
        if (set[year]) { set[year].glow.bringToFront(); set[year].line.bringToFront(); }
      },
      resize: function () { map.invalidateSize(); },
      refit: function () {
        map.invalidateSize();
        if (activeSite) map.fitBounds(boundsOf(siteById(data, activeSite)), Object.assign(fitOptions(), { animate: false }));
      },
    };
  }

  function createSvgMap(el) {
    let site = data.sites[0], year = Y1, theme = 'dark';
    let paths = {};
    let svg = null;
    function draw() {
      const w = el.clientWidth || 600, h = el.clientHeight || 400, pad = 40;
      const pts = allCoords(site);
      const lat0 = pts.reduce(function (a, p) { return a + p[1]; }, 0) / pts.length;
      const k = Math.cos(lat0 * Math.PI / 180);
      const xs = pts.map(function (p) { return p[0] * k; }), ys = pts.map(function (p) { return p[1]; });
      const x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
      const y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
      const s = Math.min((w - 2 * pad) / ((x1 - x0) || 1), (h - 2 * pad) / ((y1 - y0) || 1));
      const ox = (w - (x1 - x0) * s) / 2, oy = (h - (y1 - y0) * s) / 2;
      const body = site.shoreline.features.map(function (f) {
        const d = featureLines(f).map(function (ln) {
          return 'M' + ln.map(function (c) {
            return (ox + (c[0] * k - x0) * s).toFixed(1) + ',' + (h - oy - (c[1] - y0) * s).toFixed(1);
          }).join(' L');
        }).join(' ');
        return '<path data-year="' + f.properties.year + '" d="' + d + '" fill="none" stroke-linecap="round"/>';
      }).join('');
      el.innerHTML = '<svg class="plain-map" viewBox="0 0 ' + w + ' ' + h + '" role="img" ' +
        'aria-label="Shorelines drawn on a plain background">' + body + '</svg>';
      svg = el.firstChild;
      paths = {};
      svg.querySelectorAll('path').forEach(function (p) { paths[p.dataset.year] = p; });
      paint();
    }
    function paint() {
      if (!svg) return;
      Object.keys(paths).forEach(function (k2) {
        const st = lineStyle(Number(k2), year, theme);
        paths[k2].setAttribute('stroke', st.color);
        paths[k2].setAttribute('stroke-opacity', st.opacity);
        paths[k2].setAttribute('stroke-width', st.weight);
      });
      if (paths[year]) svg.appendChild(paths[year]);
    }
    window.addEventListener('resize', draw);
    return {
      setBase: function () {},
      setSite: function (id) { site = siteById(data, id); draw(); },
      setYear: function (y, t) { year = y; theme = t; paint(); },
      resize: draw,
      refit: draw,
    };
  }

  let view = null;
  let tileErrors = 0;

  function onTileError() {
    tileErrors++;
    if (tileErrors === 6) {
      const s = $('map-status');
      s.textContent = 'Basemap tiles are not loading. Shorelines are shown on a plain background.';
      s.hidden = false;
    }
  }

  function paintScale() {
    const stops = SCALE[state.theme];
    const css = 'linear-gradient(90deg,' + stops.join(',') + ')';
    document.documentElement.style.setProperty('--scale', css);
  }

  function renderYear() {
    const y = state.year;
    const color = yearColor(y, Y0, Y1, state.theme);
    const root = document.documentElement.style;
    root.setProperty('--yearc', color);
    $('year-readout').textContent = String(y);
    $('year-slider').value = String(y);
    $('year-slider').setAttribute('aria-valuetext', String(y));
    const m = numbersModel(data, state.site, y);
    $('year-note').textContent = m.hasShoreline ? '' : 'No shoreline for this year';
    $('numbers').innerHTML = numbersHtml(data, state.site, y);
    if (view) view.setYear(y, state.theme);
    $('level-chart').innerHTML = chartSvg(data, state.site, y);
  }

  function renderSite(animate) {
    const site = siteById(data, state.site);
    document.querySelectorAll('#site-switch button').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.site === state.site));
    });
    $('site-cards').innerHTML = data.sites.map(function (s) { return cardHtml(s, data, state.site); }).join('');
    $('chart-lede').textContent = site.name + '. Years with no value are left as gaps in the lines.';
    if (view) view.setSite(state.site, animate);
    renderYear();
  }

  function applyTheme(theme) {
    state.theme = theme;
    document.documentElement.setAttribute('data-theme', theme);
    const t = $('theme-toggle');
    t.textContent = theme === 'dark' ? 'Light theme' : 'Dark theme';
    t.setAttribute('aria-pressed', String(theme === 'light'));
    paintScale();
    if (view) { view.setBase(theme, state.sat); view.setYear(state.year, theme); }
    const ctx = $('level-chart');
    if (ctx) ctx.dataset.theme = theme;
  }

  let rafId = 0;
  function setPlaying(on) {
    state.playing = on;
    $('play-button').classList.toggle('is-playing', on);
    $('play-button').setAttribute('aria-label', on ? 'Pause the animation' : 'Play the 2000 to 2025 animation');
    if (!on) { cancelAnimationFrame(rafId); return; }
    if (state.year >= Y1) { state.year = Y0; renderYear(); }
    let last = performance.now();
    const step = 520;
    const tick = function (now) {
      if (!state.playing) return;
      if (now - last >= step) {
        last = now;
        if (state.year >= Y1) { setPlaying(false); return; }
        state.year += 1;
        renderYear();
        if (state.year >= Y1) { setPlaying(false); return; }
      }
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
  }

  function boot() {
    const b = badgeParts(data);
    const badge = $('verdict-badge');
    badge.dataset.state = b.state;
    badge.innerHTML = '<span class="verdict-head">' + esc(b.head) + '</span>' +
      '<span class="verdict-body">' + esc(b.body) + '</span>' +
      '<span class="verdict-body">' + esc(b.notice) + '</span>';
    $('caveat-panel').innerHTML = panelHtml(data);
    $('sensitivity-body').innerHTML = sensitivityHtml(data);
    $('legend').innerHTML = legendHtml(data);
    $('sources').innerHTML = sourcesHtml(data);
    $('year-slider').min = Y0;
    $('year-slider').max = Y1;
    $('site-switch').innerHTML = data.sites.map(function (s) {
      return '<button type="button" class="chip-btn" data-site="' + esc(s.id) + '" aria-pressed="false">' + esc(s.name.split(' (')[0].split(',')[0]) + '</button>';
    }).join('');

    view = window.L ? createLeafletMap($('map'), onTileError) : createSvgMap($('map'));
    if (!window.L) {
      const s = $('map-status');
      s.textContent = 'The map library could not be loaded. Shorelines are shown on a plain background.';
      s.hidden = false;
    }
    $('sat-toggle').setAttribute('aria-pressed', 'true');
    applyTheme(initialTheme());
    renderSite(false);

    $('year-slider').addEventListener('input', function (e) {
      if (state.playing) setPlaying(false);
      state.year = Number(e.target.value);
      renderYear();
    });
    $('play-button').addEventListener('click', function () { setPlaying(!state.playing); });
    $('theme-toggle').addEventListener('click', function () {
      const next = state.theme === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem('caspian-theme', next); } catch (e) { /* storage unavailable */ }
      applyTheme(next);
      renderYear();
    });
    $('sat-toggle').addEventListener('click', function () {
      state.sat = !state.sat;
      $('sat-toggle').setAttribute('aria-pressed', String(state.sat));
      if (view) view.setBase(state.theme, state.sat);
    });
    document.addEventListener('click', function (e) {
      const btn = e.target.closest && e.target.closest('button[data-site]');
      if (!btn) return;
      state.site = btn.dataset.site;
      renderSite(true);
      if (btn.classList.contains('show')) $('map').scrollIntoView({ behavior: reduce.matches ? 'auto' : 'smooth', block: 'center' });
    });
    $('level-chart').addEventListener('click', function (e) {
      const hit = e.target.closest && e.target.closest('rect.hit');
      if (!hit) return;
      const r = hit.getBoundingClientRect();
      const t = (e.clientX - r.left) / r.width;
      if (state.playing) setPlaying(false);
      state.year = Math.min(Y1, Math.max(Y0, Math.round(Y0 + t * (Y1 - Y0))));
      renderYear();
    });
    let touched = false;
    ['pointerdown', 'wheel', 'keydown'].forEach(function (ev) {
      $('map').addEventListener(ev, function () { touched = true; }, { passive: true });
    });
    const relayout = function () { if (touched) view.resize(); else view.refit(); };
    if (window.ResizeObserver) new ResizeObserver(relayout).observe($('map'));
    window.addEventListener('load', relayout);
  }

  boot();
})();
