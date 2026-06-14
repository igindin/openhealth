/* oh-registry.js - single source of truth loader for OpenHealth skins.
 *
 * Loads assets/registry.json (metric/section definitions, demo values, provenance)
 * and data.local.json (real values, git-ignored). Skins render FROM `OH`, never
 * from a local copy of the definitions. This is what guarantees parity: V1 and V2
 * draw the same metrics/sections/values because they read the same OH.
 *
 * Contract (also documented in EXTENDING.md):
 *   OH.load({base, dataUrl}) -> Promise<OH>   load registry + real data
 *   OH.metric(id) / OH.section(id) / OH.sectionMetrics(sectionId)
 *   OH.value(id)     current value (real if present, else demo)
 *   OH.target(id)    companion target (e.g. sleep need), real or default
 *   OH.raw(key, fb)  any data.local.json key (e.g. readiness, action)
 *   OH.state(id)     'real' | 'demo'
 *   OH.manifest()    parity manifest: sections -> metric ids + state
 */
(function (global) {
  'use strict';

  var OH = {
    registry: null,
    data: {},
    loaded: false,
    real: false,

    metric: function (id) {
      return OH.registry ? (OH.registry.metrics.find(function (m) { return m.id === id; }) || null) : null;
    },
    section: function (id) {
      return OH.registry ? (OH.registry.sections.find(function (s) { return s.id === id; }) || null) : null;
    },
    sectionMetrics: function (sectionId) {
      var s = OH.section(sectionId);
      return s ? (s.metric_ids || []).map(OH.metric).filter(Boolean) : [];
    },
    skin: function (id) {
      return OH.registry ? (OH.registry.skins.find(function (s) { return s.id === id; }) || null) : null;
    },

    _key: function (m) { return (m && m.data_key) || (m && m.id); },

    value: function (id) {
      var m = OH.metric(id);
      if (!m) return OH.data[id];
      var k = OH._key(m);
      if (OH.data[k] !== undefined && OH.data[k] !== null) return OH.data[k];
      return m.demo;
    },

    target: function (id) {
      var m = OH.metric(id);
      if (!m) return undefined;
      if (m.target_key && OH.data[m.target_key] !== undefined && OH.data[m.target_key] !== null) {
        return OH.data[m.target_key];
      }
      return m.target_default;
    },

    raw: function (key, fallback) {
      return (OH.data[key] !== undefined && OH.data[key] !== null) ? OH.data[key] : fallback;
    },

    // Biomarkers (lab values with reference ranges) - single source in the registry,
    // real values from data.local.json override. Both skins seed DATA.biomarkers from this.
    biomarkers: function () {
      if (Array.isArray(OH.data.biomarkers) && OH.data.biomarkers.length) return OH.data.biomarkers;
      return (OH.registry && OH.registry.biomarkers) || [];
    },

    state: function (id) {
      var m = OH.metric(id);
      if (!m) return 'unknown';
      var v = OH.data[OH._key(m)];
      if (v === undefined || v === null || (Array.isArray(v) && v.length === 0)) return 'demo';
      return 'real';
    },

    // Parity manifest: what any skin must render from the registry, with current
    // state. Skins also expose window.__renderManifest() built from this, so a
    // headless check can assert V1 and V2 render the same thing.
    manifest: function () {
      if (!OH.registry) return { sections: [] };
      return {
        sections: OH.registry.sections.map(function (s) {
          return {
            id: s.id,
            metrics: (s.metric_ids || []).map(function (mid) { return { id: mid, state: OH.state(mid) }; })
          };
        })
      };
    },

    // Render a metric's chart via the shared kit (OHCharts), dispatching on the
    // metric's `chart` type. Value comes from OH.value(id) (real or demo). Returns
    // an SVG string, or '' for non-chart tiles. opts pass through to the kit. This
    // is what lets both skins render any registry chart with one call.
    renderChart: function (id, opts) {
      opts = opts || {};
      var m = OH.metric(id);
      if (!m || !global.OHCharts) return '';
      opts = Object.assign({}, m.chart_opts || {}, opts); // registry chart_opts are defaults
      var K = global.OHCharts, v = OH.value(id);
      switch (m.chart) {
        case 'ring': return K.ring(Object.assign({ percent: Number(v) || 0 }, opts));
        case 'sparkline': return K.sparkline(Object.assign({ data: v || [] }, opts));
        case 'week_bars': return K.weekBars(v || [], opts);
        case 'line_dots': return K.lineDots(v || [], opts);
        case 'hypnogram': return K.hypnogram(v || [], opts);
        case 'sleep_stages': return K.sleepStages(v || [], opts);
        case 'hours_vs_need': return K.hoursVsNeed(v || {}, opts);
        case 'hr_zones': return K.hrZones(v || [], opts);
        case 'gauge': return K.gauge(v, opts);
        default: return '';
      }
    },

    // Source-connect copy for empty states.
    sourceInfo: function (src) {
      var map = {
        whoop: { label: 'WHOOP', icon: 'ph-watch' },
        apple: { label: 'Apple Health', icon: 'ph-heart' },
        withings: { label: 'Withings', icon: 'ph-scales' },
        labs: { label: 'Лаборатория', icon: 'ph-flask' },
        derived: { label: 'Расчёт', icon: 'ph-function' },
      };
      return map[src] || { label: src || '', icon: 'ph-database' };
    },

    // Render a whole registry section as neutral HTML (skins theme the .oh-* classes).
    // Tile metrics -> value cards; chart metrics -> renderChart. Demo metrics (no real
    // data yet) are dimmed with a "демо · <source>" chip = the empty-state preview.
    // opts: accent (chart line color), textColor (labels), bg (dot fill).
    sectionView: function (sectionId, opts) {
      opts = opts || {};
      var s = OH.section(sectionId); if (!s) return '';
      var accent = opts.accent || 'currentColor', textColor = opts.textColor || 'currentColor';
      var cards = OH.sectionMetrics(sectionId).map(function (m) {
        var demo = OH.state(m.id) === 'demo', si = OH.sourceInfo(m.source);
        var chip = demo ? '<span class="oh-chip" title="Нет данных - показан пример"><i class="ph ' + si.icon + '"></i> демо · ' + si.label + '</span>' : '';
        if (m.chart === 'tile') {
          var val = OH.value(m.id);
          var v = (typeof val === 'number' && !Number.isInteger(val)) ? val.toFixed(1) : val;
          var ser = OH.series ? OH.series(m.id) : null, ind = '';
          if (ser && ser.length >= 4) {
            var last = ser[ser.length - 1], mean = ser.reduce(function (s2, x) { return s2 + x; }, 0) / ser.length, d = last - mean, up = d >= 0;
            ind = ' <span class="oh-ind" style="color:' + (up ? '#27C28A' : '#E0706A') + ';font-size:12px;font-weight:600">' + (up ? '▲' : '▼') + ' ' + Math.abs(d).toFixed(Math.abs(d) % 1 ? 1 : 0) + '</span>';
          }
          return '<div class="oh-tile' + (demo ? ' oh--demo' : '') + '" data-metric="' + m.id + '">' +
            '<div class="oh-tile__top"><span class="oh-tile__label">' + (m.label_ru || m.id) + '</span>' +
            '<span class="oh-tile__icon"><button class="oh-q" data-prov="' + m.id + '" title="Как это считается">?</button><i class="ph ' + (m.icon || 'ph-circle') + '" style="margin-left:6px"></i></span></div>' +
            '<div class="oh-tile__val">' + v + (m.unit ? ' <span class="oh-tile__unit">' + m.unit + '</span>' : '') + ind + '</div>' + chip + '</div>';
        }
        var svg = OH.renderChart(m.id, { color: accent, labelColor: textColor, colorHours: accent, colorNeed: 'rgba(127,127,127,0.45)', highlightColor: accent, bg: opts.bg });
        return '<div class="oh-chart-card' + (demo ? ' oh--demo' : '') + '" data-metric="' + m.id + '">' +
          '<div class="oh-chart-card__head"><span class="oh-chart-card__label">' + (m.label_ru || m.id) + '</span>' + chip + '<button class="oh-q" data-prov="' + m.id + '" title="Как это считается">?</button></div>' +
          '<div class="oh-chart-card__svg">' + svg + '</div></div>';
      }).join('');
      return '<section class="oh-section" id="oh-sec-' + sectionId + '" data-section="' + sectionId + '">' +
        '<div class="oh-section__head"><span class="oh-section__icon" style="color:' + accent + '"><i class="ph ' + (s.icon || 'ph-circle') + '"></i></span>' +
        '<h2 class="oh-section__title">' + (s.label_ru || sectionId) + '</h2></div>' +
        '<div class="oh-section__grid">' + cards + '</div></section>';
    },

    load: function (opts) {
      opts = opts || {};
      var base = opts.base || './assets/';
      return fetch(base + 'registry.json', { cache: 'no-store' })
        .then(function (r) { if (!r.ok) throw new Error('registry.json ' + r.status); return r.json(); })
        .then(function (reg) {
          OH.registry = reg;
          return fetch((opts.dataUrl || 'data.local.json'), { cache: 'no-store' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .catch(function () { return null; });
        })
        .then(function (data) {
          if (data) {
            Object.keys(data).forEach(function (k) {
              if (k.charAt(0) === '_') return;
              if (data[k] === null || data[k] === undefined) return;
              if (Array.isArray(data[k]) && data[k].length === 0) return;
              OH.data[k] = data[k];
            });
            OH.real = true;
          }
          OH.loaded = true;
          return OH;
        });
    }
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = OH;
  global.OH = OH;
})(typeof window !== 'undefined' ? window : this);
