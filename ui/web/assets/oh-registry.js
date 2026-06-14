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
