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

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  var OH = {
    registry: null,
    data: {},
    knowledge: null,
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

    // Единый контракт состояния данных блока. БЕЗ metric.status/eligibility
    // поведение прежнее (real/demo) — обратная совместимость.
    //   real          — есть реальное значение
    //   insufficient  — данные есть, но ниже порога eligibility (рано считать)
    //   empty         — источник не подключён / раздел не наполнен (status:"soon")
    //   demo          — реальных нет, показан ПОМЕЧЕННЫЙ пример
    state: function (id) {
      var m = OH.metric(id);
      if (!m) return 'unknown';
      if (m.status === 'soon' || m.status === 'empty') return 'empty';
      if (m.eligibility && !OH.eligibility(id).ok) return 'insufficient';
      var v = OH.data[OH._key(m)];
      if (v === undefined || v === null || (Array.isArray(v) && v.length === 0)) return 'demo';
      return 'real';
    },

    // Порог пригодности для вычисляемых метрик (корреляции/паттерны n=1).
    // metric.eligibility: { need, have_key, label_ru } — have из data[have_key].
    // Без eligibility -> {ok:true}.
    eligibility: function (id) {
      var m = OH.metric(id); if (!m || !m.eligibility) return { ok: true };
      var need = m.eligibility.need || 0;
      var have = Number((m.eligibility.have_key && OH.data[m.eligibility.have_key]) || 0);
      return { ok: have >= need, have: have, need: need, label: m.eligibility.label_ru || '' };
    },

    // Доказательность утверждения: { confidence: C1-C5, type, sources }.
    // Личные n=1 паттерны cap <= C3 (правило проекта). Без evidence -> null.
    evidence: function (id) {
      var m = OH.metric(id); if (!m) return null;
      return m.evidence || null;
    },

    // Курируемый слой знаний (assets/knowledge.json): девайсы, источники-протоколы,
    // короткие видео к метрикам. У каждой записи есть провенанс (source_url/url +
    // checked_at) и честный evidence_level. Ничего не выдумываем — нет данных -> [].
    devices: function () { return (OH.knowledge && OH.knowledge.devices) || []; },
    protocolSources: function () { return (OH.knowledge && OH.knowledge.protocol_sources) || []; },
    videosFor: function (metricId) {
      if (!OH.knowledge || !OH.knowledge.video_refs) return [];
      return OH.knowledge.video_refs.filter(function (v) { return v.metric_id === metricId; });
    },
    // Единая шкала доказательности для UI. Принимает уровни знаний (high/medium/low)
    // и C1-C5; возвращает { label, cls } — скины красят через .oh-ev--<cls>.
    evidenceLabel: function (level) {
      var map = {
        high: { label: 'высокая', cls: 'high' }, C1: { label: 'C1 мета-анализ', cls: 'high' }, C2: { label: 'C2 RCT', cls: 'high' },
        medium: { label: 'средняя', cls: 'mid' }, C3: { label: 'C3 наблюдательное', cls: 'mid' }, C4: { label: 'C4 мнение эксперта', cls: 'mid' },
        low: { label: 'низкая', cls: 'low' }, C5: { label: 'C5 n=1 / частное', cls: 'low' }
      };
      return map[level] || { label: String(level || '—'), cls: 'mid' };
    },

    // Аудиторные пресеты (registry.personas): под кого перестраивается OpenHealth.
    // Чисто данные + ОПЦИОНАЛЬНОЕ применение. По умолчанию пресет НЕ активен —
    // скины не меняются, пока пользователь явно не выберет персону.
    personas: function () { return (OH.registry && OH.registry.personas) || []; },
    persona: function (id) { return OH.personas().find(function (p) { return p.id === id; }) || null; },
    personaActive: (function () { try { return localStorage.getItem('oh.persona') || null; } catch (e) { return null; } })(),
    setPersona: function (id) { OH.personaActive = id || null; try { id ? localStorage.setItem('oh.persona', id) : localStorage.removeItem('oh.persona'); } catch (e) {} },
    // Группы навигации, переупорядоченные по priority_groups активной персоны
    // (остальные дописываются по обычному порядку). Без активной персоны это
    // ровно OH.nav.groups() — поведение по умолчанию неизменно.
    personaGroups: function () {
      var base = OH.nav.groups();
      var p = OH.persona(OH.personaActive); if (!p) return base;
      var pri = p.priority_groups || [];
      return base.slice().sort(function (a, b) {
        var ia = pri.indexOf(a.id), ib = pri.indexOf(b.id);
        if (ia < 0) ia = 999; if (ib < 0) ib = 999;
        return ia - ib || (a.order || 0) - (b.order || 0);
      });
    },

    // demo-режим: примеры показываются только при ЯВНОМ включении (по умолчанию off).
    demoMode: (function () { try { return localStorage.getItem('oh.demoMode') === 'on'; } catch (e) { return false; } })(),
    setDemoMode: function (on) { OH.demoMode = !!on; try { localStorage.setItem('oh.demoMode', on ? 'on' : 'off'); } catch (e) {} },

    // Single source of navigation for BOTH skins: groups (<=9) -> subsections,
    // ordered, filtered by user visibility prefs (openhealth.nav.hidden — the
    // same key V1 already uses). Each skin renders this in its own layout.
    nav: {
      _hidden: function () {
        try { var a = JSON.parse(localStorage.getItem('openhealth.nav.hidden') || '[]'); var o = {}; (a || []).forEach(function (k) { o[k] = 1; }); return o; }
        catch (e) { return {}; }
      },
      isHidden: function (id) { return !!OH.nav._hidden()[id]; },
      setHidden: function (id, on) {
        var h = OH.nav._hidden(); if (on) h[id] = 1; else delete h[id];
        try { localStorage.setItem('openhealth.nav.hidden', JSON.stringify(Object.keys(h))); } catch (e) {}
      },
      // [{id,label_ru,icon,order, sections:[{id,label_ru,icon,status}]}], settings excluded (pinned by skins)
      groups: function () {
        if (!OH.registry || !OH.registry.groups) return [];
        var hidden = OH.nav._hidden();
        return OH.registry.groups.slice()
          .sort(function (a, b) { return (a.order || 0) - (b.order || 0); })
          .map(function (g) {
            return {
              id: g.id, label_ru: g.label_ru, icon: g.icon, order: g.order,
              sections: (g.section_ids || []).map(OH.section).filter(Boolean)
                .filter(function (s) { return !hidden[s.id]; })
                .map(function (s) { return { id: s.id, label_ru: s.label_ru, icon: s.icon, status: s.status || 'ready' }; })
            };
          })
          .filter(function (g) { return !hidden['group:' + g.id] && g.sections.length; });
      }
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

    // Раздел «Девайсы и источники» из knowledge.json. Нейтральная разметка
    // (.oh-k*), оба скина тематизируют. У каждой карточки: уровень доказательности,
    // ссылка-источник (провенанс) и кнопка «перепроверить» (handoff в oh-provenance).
    // kind: 'devices' | 'sources'. Ничего не выдумываем — пусто -> честная заглушка.
    _devCats: [
      { id: 'hrv-recovery', label: 'HRV и восстановление', icon: 'ph-heartbeat' },
      { id: 'sleep', label: 'Сон', icon: 'ph-moon' },
      { id: 'metabolism-cgm', label: 'Метаболизм (CGM)', icon: 'ph-drop' },
      { id: 'training-power', label: 'Тренировки и мощность', icon: 'ph-lightning' },
      { id: 'vagus-neuro', label: 'Вагус · нейро · восстановление', icon: 'ph-brain' },
      { id: 'lactate', label: 'Лактат', icon: 'ph-flask' }
    ],
    knowledgeView: function (kind, opts) {
      opts = opts || {};
      var accent = opts.accent || 'currentColor';
      var sec = OH.section(kind) || {};
      function head(title, icon, intro) {
        return '<div class="oh-section__head"><span class="oh-section__icon" style="color:' + accent + '"><i class="ph ' + (icon || 'ph-circle') + '"></i></span>' +
          '<h2 class="oh-section__title">' + esc(title) + '</h2></div>' +
          (intro ? '<p class="oh-k-intro">' + esc(intro) + '</p>' : '');
      }
      function evBadge(level) { var e = OH.evidenceLabel(level); return '<span class="oh-ev oh-ev--' + e.cls + '" title="Доказательность">' + esc(e.label) + '</span>'; }
      var html;
      if (kind === 'sources') {
        var list = OH.protocolSources();
        if (!list.length) return OH.sectionStub('sources');
        var rows = list.map(function (s) {
          return '<div class="oh-kcard" data-kind="source" data-id="' + esc(s.id) + '">' +
            '<div class="oh-kcard__top"><span class="oh-kcard__name">' + esc(s.name) + '</span>' + evBadge(s.evidence_level) + '</div>' +
            '<div class="oh-kcard__area">' + esc(s.area) + '</div>' +
            '<div class="oh-kcard__meta">' + esc(s.content_type) + ' · ' + esc(s.format) + '</div>' +
            (s.caveat ? '<div class="oh-kcard__caveat"><i class="ph ph-warning"></i> ' + esc(s.caveat) + '</div>' : '') +
            '<div class="oh-kcard__actions">' +
              '<a class="oh-kbtn" href="' + esc(s.url) + '" target="_blank" rel="noopener noreferrer"><i class="ph ph-link"></i> Источник</a>' +
              '<button class="oh-kbtn oh-kverify" data-kind="source" data-id="' + esc(s.id) + '"><i class="ph ph-arrows-clockwise"></i> Перепроверить</button>' +
              '<span class="oh-kcard__checked">сверено ' + esc(s.checked_at) + '</span></div></div>';
        }).join('');
        html = head(sec.label_ru || 'Источники протоколов', sec.icon || 'ph-graduation-cap',
          'Авторитетные источники протоколов уровня Attia/Huberman. Уровень доказательности и оговорки показаны честно — не все одинаково доказательны.');
        return '<section class="oh-section oh-section--knowledge" id="oh-sec-sources" data-section="sources">' + html + '<div class="oh-kgrid">' + rows + '</div></section>';
      }
      // devices
      var devs = OH.devices();
      if (!devs.length) return OH.sectionStub('devices');
      var byCat = OH._devCats.map(function (c) {
        var items = devs.filter(function (d) { return d.category === c.id; });
        if (!items.length) return '';
        var cards = items.map(function (d) {
          return '<div class="oh-kcard" data-kind="device" data-id="' + esc(d.id) + '">' +
            '<div class="oh-kcard__top"><span class="oh-kcard__name">' + esc(d.name) + '</span>' + evBadge(d.evidence_level) + '</div>' +
            '<div class="oh-kcard__area">' + esc(d.measures) + '</div>' +
            '<div class="oh-kcard__meta"><b>' + esc(d.key_metric) + '</b> · ' + esc(d.price_tier) + '</div>' +
            '<div class="oh-kcard__use">' + esc(d.useful_for) + '</div>' +
            (d.alternatives && d.alternatives.length ? '<div class="oh-kcard__alt">альтернативы: ' + esc(d.alternatives.join(', ')) + '</div>' : '') +
            '<div class="oh-kcard__actions">' +
              '<a class="oh-kbtn" href="' + esc(d.source_url) + '" target="_blank" rel="noopener noreferrer"><i class="ph ph-link"></i> Источник</a>' +
              '<button class="oh-kbtn oh-kverify" data-kind="device" data-id="' + esc(d.id) + '"><i class="ph ph-arrows-clockwise"></i> Перепроверить</button>' +
              '<span class="oh-kcard__checked">сверено ' + esc(d.checked_at) + '</span></div></div>';
        }).join('');
        return '<h3 class="oh-kcat"><i class="ph ' + c.icon + '"></i> ' + esc(c.label) + '</h3><div class="oh-kgrid">' + cards + '</div>';
      }).join('');
      html = head(sec.label_ru || 'Девайсы', sec.icon || 'ph-watch',
        'Что устройство реально измеряет (не маркетинг), зачем в n=1 самоконтроле и насколько подтверждено. Уровень доказательности показан честно.');
      return '<section class="oh-section oh-section--knowledge" id="oh-sec-devices" data-section="devices">' + html + byCat + '</section>';
    },

    // Render a whole registry section as neutral HTML (skins theme the .oh-* classes).
    // Tile metrics -> value cards; chart metrics -> renderChart. Demo metrics (no real
    // data yet) are dimmed with a "демо · <source>" chip = the empty-state preview.
    // opts: accent (chart line color), textColor (labels), bg (dot fill).
    // Honest "coming soon" stub for not-yet-migrated sections (status:'soon').
    // Neutral markup; both skins theme it. Keeps the section anchor id so nav can
    // scroll to it. No fake data — just the section title + a clear note.
    sectionStub: function (sectionId) {
      var s = OH.section(sectionId); if (!s) return '';
      return '<section class="oh-section oh-section--stub" id="oh-sec-' + sectionId + '" data-section="' + sectionId + '">' +
        '<div class="oh-section__head"><span class="oh-section__icon"><i class="ph ' + (s.icon || 'ph-circle') + '"></i></span>' +
        '<h2 class="oh-section__title">' + (s.label_ru || sectionId) + '</h2></div>' +
        '<div class="oh-stub" style="opacity:.7;font-size:13px;line-height:1.5;padding:6px 2px;">' +
        '<i class="ph ph-sparkle"></i> Раздел скоро — данные ещё не подключены к реестру.</div></section>';
    },

    sectionView: function (sectionId, opts) {
      opts = opts || {};
      var s = OH.section(sectionId); if (!s) return '';
      // Onboarding assembles the app: a section the user turned off is hidden in
      // BOTH skins at once here (this is the single body choke point). Nav already
      // filters the same 'openhealth.nav.hidden' key. Core screens stay pinned.
      if (OH.nav && OH.nav.isHidden(sectionId) && sectionId !== 'today' && sectionId !== 'pulse' && sectionId !== 'sync') return '';
      if (s.kind === 'knowledge') return OH.knowledgeView(s.knowledge_view || sectionId, opts);
      if (s.status === 'soon' || s.status === 'empty') return OH.sectionStub(sectionId);
      var accent = opts.accent || 'currentColor', textColor = opts.textColor || 'currentColor';
      var dimCount = 0, totalCount = 0;
      var cards = OH.sectionMetrics(sectionId).map(function (m) {
        var st = OH.state(m.id), si = OH.sourceInfo(m.source);
        var dim = (st === 'demo' || st === 'empty' || st === 'insufficient');
        totalCount++; if (dim) dimCount++;
        var chipText = st === 'insufficient' ? 'мало данных' : (st === 'empty' ? 'нет данных · ' + si.label : (st === 'demo' ? 'демо · ' + si.label : ''));
        var chip = chipText ? '<span class="oh-chip" title="Состояние данных"><i class="ph ' + si.icon + '"></i> ' + chipText + '</span>' : '';
        if (m.chart === 'tile') {
          var val = OH.value(m.id);
          var v = (typeof val === 'number' && !Number.isInteger(val)) ? val.toFixed(1) : val;
          var ser = OH.series ? OH.series(m.id) : null, ind = '';
          if (ser && ser.length >= 4) {
            var last = ser[ser.length - 1], mean = ser.reduce(function (s2, x) { return s2 + x; }, 0) / ser.length, d = last - mean, up = d >= 0;
            ind = ' <span class="oh-ind" style="color:' + (up ? '#27C28A' : '#E0706A') + ';font-size:12px;font-weight:600">' + (up ? '▲' : '▼') + ' ' + Math.abs(d).toFixed(Math.abs(d) % 1 ? 1 : 0) + '</span>';
          }
          return '<div class="oh-tile' + (dim ? ' oh--demo' : '') + '" data-metric="' + m.id + '">' +
            '<div class="oh-tile__top"><span class="oh-tile__label">' + (m.label_ru || m.id) + '</span>' +
            '<span class="oh-tile__icon"><button class="oh-q" data-prov="' + m.id + '" title="Как это считается">?</button><i class="ph ' + (m.icon || 'ph-circle') + '"></i></span></div>' +
            '<div class="oh-tile__val">' + v + (m.unit ? ' <span class="oh-tile__unit">' + m.unit + '</span>' : '') + ind + '</div>' + chip + '</div>';
        }
        var svg = OH.renderChart(m.id, { color: accent, labelColor: textColor, colorHours: accent, colorNeed: 'rgba(127,127,127,0.45)', highlightColor: accent, bg: opts.bg });
        return '<div class="oh-chart-card' + (dim ? ' oh--demo' : '') + '" data-metric="' + m.id + '">' +
          '<div class="oh-chart-card__head"><span class="oh-chart-card__label">' + (m.label_ru || m.id) + '</span>' + chip + '<button class="oh-q" data-prov="' + m.id + '" title="Как это считается">?</button></div>' +
          '<div class="oh-chart-card__svg">' + svg + '</div></div>';
      }).join('');
      // Целиком демо-секция получает один честный баннер вместо тихих чипов:
      // демо не должно читаться как реальные показатели.
      var demoBanner = (totalCount > 0 && dimCount === totalCount)
        ? '<div class="oh-demo-banner"><i class="ph ph-flask"></i><span>Раздел на демо-данных — реальные появятся после подключения источника. ' +
          '<a onclick="if(typeof go===\'function\')go(\'sync\');else location.href=\'dashboard.html\'">Источники данных</a></span></div>'
        : '';
      return '<section class="oh-section" id="oh-sec-' + sectionId + '" data-section="' + sectionId + '">' +
        '<div class="oh-section__head"><span class="oh-section__icon" style="color:' + accent + '"><i class="ph ' + (s.icon || 'ph-circle') + '"></i></span>' +
        '<h2 class="oh-section__title">' + (s.label_ru || sectionId) + '</h2></div>' +
        demoBanner +
        '<div class="oh-section__grid">' + cards + '</div></section>';
    },

    load: function (opts) {
      opts = opts || {};
      var base = opts.base || './assets/';
      return fetch(base + 'registry.json', { cache: 'no-store' })
        .then(function (r) { if (!r.ok) throw new Error('registry.json ' + r.status); return r.json(); })
        .then(function (reg) {
          OH.registry = reg;
          // Curated knowledge layer (non-fatal if absent): devices/sources/videos.
          return fetch(base + 'knowledge.json', { cache: 'no-store' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .catch(function () { return null; })
            .then(function (k) { if (k) OH.knowledge = k; });
        })
        .then(function () {
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
