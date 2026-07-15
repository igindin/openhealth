/* oh-onboarding.js — iPhone-setup-style onboarding for OpenHealth.
 *
 * WHY
 * ---
 * OpenHealth ships ~30 sections at once. A first-time person needs a calm,
 * step-by-step setup that starts from their GOAL and assembles only the modules
 * they need — like setting up a new iPhone. This module is that flow, shared by
 * both skins (V1 dark monolith + V2 Bento) and re-runnable from Settings.
 *
 * IT REUSES EXISTING MACHINERY, no schema change:
 *   - OH.registry.groups / .sections / .personas  (definitions)
 *   - OH.nav.setHidden(id,on)  ->  localStorage 'openhealth.nav.hidden'
 *     (both skins already filter nav by this; oh-registry.js sectionView also
 *      returns '' for hidden sections, so bodies disappear too)
 *   - OH.setPersona(id)        ->  localStorage 'oh.persona'  (reorders nav)
 *   - theme                    ->  localStorage 'openhealth.theme' (data-theme)
 *   - accent (new, additive)   ->  localStorage 'oh.accent' (overrides --accent)
 *   - 'oh.onboarded' flag gates the first-run auto-start.
 *
 * The overlay themes itself with the app's own CSS variables (--bg, --ink,
 * --accent, --card-outer, --line, --radius-outer, --font), so it matches any
 * skin/theme/palette automatically.
 *
 * MOTION CONTRACT (ui/web/refs/onboarding/FRAMES.md)
 * --------------------------------------------------
 * WAAPI + CSS transitions, no GSAP (V2 doesn't load it). Hard rule learned in
 * this repo: visibility never depends on an animation finishing — rAF and CSS
 * animations freeze mid-frame in throttled tabs. So every element's BASE state
 * is visible, animations run from→base, and every WAAPI run gets a timeout
 * backstop that force-finishes it. prefers-reduced-motion disables everything.
 *
 * Public API (window.OHOnboarding):
 *   .open({rerun})     open the flow (rerun=true restores the current setup)
 *   .maybeAutoStart()  open once on genuine first run
 *   .reset()           clear the onboarded flag (debug)
 *   .applyPalette()    apply the saved accent (called on load)
 */
(function (global) {
  'use strict';

  var LS = {
    onboarded: 'oh.onboarded',
    accent: 'oh.accent',
    theme: 'openhealth.theme',
    hidden: 'openhealth.nav.hidden'
  };

  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }

  var RM = false;
  try { RM = global.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}

  // WAAPI helper: from→base animation with a force-finish backstop, so a
  // throttled tab can never strand an element in its "from" state.
  function anim(el, keyframes, opts) {
    if (RM || !el || !el.animate) return;
    try {
      var a = el.animate(keyframes, opts);
      setTimeout(function () { try { a.finish(); } catch (e) {} }, (opts.duration || 300) + (opts.delay || 0) + 150);
    } catch (e) {}
  }

  var EASE_OUT = 'cubic-bezier(.2,.8,.2,1)';
  var EASE_SPRING = 'cubic-bezier(.34,1.56,.64,1)';

  // The skins ship different Phosphor weights (V1: ph-light, V2: ph regular).
  // Probe which weight class actually resolves to a Phosphor font here and use
  // it for every overlay icon, so icons render in BOTH skins.
  var ICONW = 'ph';
  function detectIconWeight() {
    var weights = ['ph-light', 'ph', 'ph-duotone'];
    for (var i = 0; i < weights.length; i++) {
      try {
        var probe = document.createElement('i');
        probe.className = weights[i] + ' ph-heart';
        probe.style.cssText = 'position:fixed;left:-99px;top:-99px';
        document.body.appendChild(probe);
        var fam = getComputedStyle(probe).fontFamily || '';
        document.body.removeChild(probe);
        if (fam.indexOf('Phosphor') >= 0) return weights[i];
      } catch (e) {}
    }
    return 'ph';
  }
  function icon(name) { return '<i class="' + ICONW + ' ' + name + '"></i>'; }

  // Sections that are always on — the app is useless without "Сегодня", and
  // Settings must never be hidden (it's how you re-run this onboarding).
  var PINNED = { today: 1, pulse: 1, sync: 1, settings: 1 };

  // --- Palette presets (theme base + accent). Accent recolours the whole app
  // via --accent. Dark stays primary; daylight is the honest light option. ----
  var PALETTES = [
    { id: 'midnight', name: 'Полночь', sub: 'тёмная, изумруд', theme: 'dark', accent: '#10b981' },
    { id: 'amber', name: 'Янтарь', sub: 'тёмная, тёплый медный', theme: 'dark', accent: '#f0a24b' },
    { id: 'coral', name: 'Коралл', sub: 'тёмная, живой коралл', theme: 'dark', accent: '#f2765f' },
    { id: 'iris', name: 'Ирис', sub: 'тёмная, спокойный синий', theme: 'dark', accent: '#6ea8fe' },
    { id: 'daylight', name: 'Дневной', sub: 'светлая, изумруд', theme: 'light', accent: '#0e9f6e' }
  ];

  // --- Goal-first directions. Each maps to an existing registry persona and a
  // starter bundle of groups (the rest is hidden by default, user can tune). ---
  var GOALS = [
    {
      id: 'athletes', persona: 'athlete', icon: 'ph-person-simple-run',
      title: 'Спорт и результат',
      hook: 'Понять, что реально помогает восстановиться и добавляет в форме.',
      keepGroups: ['today', 'activity', 'sleep', 'stress', 'analytics']
    },
    {
      id: 'medical', persona: 'chronic', icon: 'ph-first-aid-kit',
      title: 'Здоровье под контролем',
      hook: 'Собрать разрозненную картину и приходить к врачу подготовленным.',
      keepGroups: ['today', 'medical', 'body', 'analytics', 'journal']
    },
    {
      id: 'longevity', persona: 'seniors', icon: 'ph-plant',
      title: 'Долголетие',
      hook: 'Следить за показателями, которые тихо копятся годами.',
      keepGroups: ['today', 'body', 'analytics', 'medical', 'knowledge']
    },
    {
      id: 'stress-sleep', persona: 'low-energy', icon: 'ph-moon-stars',
      title: 'Сон и спокойствие',
      hook: 'Найти, что мешает сну и энергии, вместо того чтобы гадать.',
      keepGroups: ['today', 'sleep', 'stress', 'journal', 'analytics']
    },
    {
      id: 'curious', persona: null, icon: 'ph-compass',
      title: 'Просто начать',
      hook: 'Первый раз собираю систему здоровья. Начнём с малого.',
      keepGroups: ['today', 'sleep', 'activity', 'analytics']
    }
  ];

  // --- Synthetic inspiration cases (public-safe archetypes, phrased as questions,
  // no medical claims, no real people). ---------------------------------------
  var CASES = [
    { icon: 'ph-person-simple-run', title: 'Марафонец и восстановление', text: 'С ростом объёма пульс покоя чуть подрос. Это ранний сигнал усталости и повод для лёгкого дня, или просто жаркая неделя и короткий сон?' },
    { icon: 'ph-barbell', title: 'Силовые без перетрена', text: 'После трёх тяжёлых дней подряд recovery просел раньше, чем результаты. Может ли такой провал быть подсказкой запланировать отдых?' },
    { icon: 'ph-brain', title: 'Дыхательная практика', text: 'Вечернее дыхание — видно ли его в сигналах нервной системы? Спокойнее ли ночь после практики, чем без неё?' }
  ];

  // Poster ghost word per step (type-as-image; ready uses the numeral instead).
  var GHOSTS = { welcome: 'Health', goal: 'Цель', modules: 'Модули', palette: 'Вид', how: 'Основа', ready: '' };

  // hex -> rgba glow
  function glow(hex, a) {
    var m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex || '');
    if (!m) return 'rgba(16,185,129,' + (a || 0.15) + ')';
    return 'rgba(' + parseInt(m[1], 16) + ',' + parseInt(m[2], 16) + ',' + parseInt(m[3], 16) + ',' + (a || 0.15) + ')';
  }

  // Apply a persisted accent on top of whatever theme the skin set. Idempotent.
  function applyPalette() {
    var a = get(LS.accent);
    if (a) {
      var root = document.documentElement;
      root.style.setProperty('--accent', a);
      root.style.setProperty('--accent-text', a);
      root.style.setProperty('--accent-glow', glow(a, 0.15));
    }
  }

  // Hide sections the user turned off, in either skin. The sectionView seam
  // already blanks registry bodies; this also covers nodes that never pass
  // through it: registry bodies carry data-section, and V2 bespoke bento cards
  // use id="sec-<id>" (biomarkers/correlations/trends/…) with no data-section.
  function hideIfOff(node, id) {
    if (id && !PINNED[id] && OH.nav.isHidden(id)) node.style.display = 'none';
  }
  function applyModuleVisibility() {
    if (!global.OH || !OH.nav) return;
    document.querySelectorAll('[data-section]').forEach(function (n) { hideIfOff(n, n.getAttribute('data-section')); });
    document.querySelectorAll('[id^="sec-"]').forEach(function (n) { hideIfOff(n, n.id.slice(4)); });
  }

  // ---- overlay construction --------------------------------------------------
  var state = null; // {i, goal, chosen:{}, palette, rerun, _touchedModules}
  var el = null;
  var els = null; // persistent chrome refs {num,bar,skip,ghost,step,scroll,back,next}

  function css() {
    if (document.getElementById('ohb-style')) return;
    var s = document.createElement('style');
    s.id = 'ohb-style';
    s.textContent = [
      // NO opacity animation/transition on the root — throttled tabs freeze
      // animations mid-frame and the dashboard bleeds through. Root is opaque;
      // only background-color transitions (palette crossfade), never opacity.
      '.ohb-root{position:fixed;inset:0;z-index:100000;background:var(--bg,#060709);color:var(--ink,#f3f4f6);',
      'font-family:var(--font,system-ui,sans-serif);display:flex;flex-direction:column;opacity:1;transition:background-color .45s ease,color .45s ease}',
      '.ohb-root :focus-visible{outline:2px solid var(--accent,#10b981);outline-offset:2px;border-radius:10px}',
      // chrome: progress rail (persistent — width transition actually plays)
      '.ohb-rail{display:flex;align-items:center;gap:14px;padding:22px 26px 8px;max-width:760px;margin:0 auto;width:100%}',
      '.ohb-num{font-variant-numeric:tabular-nums;font-weight:800;font-size:16px;letter-spacing:.02em;color:var(--accent,#10b981)}',
      '.ohb-num small{color:var(--dim,#768093);font-weight:600;font-size:13px}',
      '.ohb-bar{flex:1;height:3px;border-radius:3px;background:var(--line,#1d212a);overflow:hidden}',
      '.ohb-bar>i{display:block;height:100%;width:0;background:var(--accent,#10b981);transition:width .5s ' + EASE_OUT + '}',
      '.ohb-skip{background:none;border:0;color:var(--dim,#768093);font-size:13px;cursor:pointer;padding:6px 8px;font-family:inherit}',
      '.ohb-skip:hover{color:var(--ink,#fff)}',
      '.ohb-scroll{flex:1;overflow-y:auto;overflow-x:hidden}',
      '.ohb-stage{position:relative;max-width:760px;margin:0 auto;padding:8px 26px 40px;width:100%}',
      // poster ghost word behind every step (type-as-image)
      '.ohb-ghost{position:absolute;top:-6px;left:14px;font-size:clamp(96px,17vw,168px);line-height:.82;font-weight:800;',
      'color:var(--ink,#fff);opacity:.05;letter-spacing:-.045em;pointer-events:none;user-select:none;white-space:nowrap;transition:opacity .4s ease}',
      '.ohb-step{position:relative}',
      // type scale
      '.ohb-hero{position:relative;padding:18px 0 6px}',
      '.ohb-kicker{position:relative;font-size:13px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--accent,#10b981);margin:0 0 10px;transition:color .45s ease}',
      '.ohb-h{position:relative;font-size:clamp(30px,4.6vw,46px);line-height:1.06;font-weight:800;letter-spacing:-.025em;margin:0 0 12px}',
      '.ohb-sub{position:relative;font-size:16px;line-height:1.5;color:var(--mut,#8e96a3);margin:0 0 22px;max-width:52ch}',
      // goal cards
      '.ohb-cards{display:flex;flex-direction:column;gap:12px}',
      '.ohb-card{display:flex;align-items:center;gap:16px;text-align:left;width:100%;background:var(--card-outer,#0f1115);',
      'border:1.5px solid var(--line,#1d212a);border-radius:var(--radius-inner,16px);padding:16px 18px;cursor:pointer;',
      'transition:border-color .18s,background .18s,transform .09s;color:inherit;font-family:inherit}',
      '.ohb-card:hover{border-color:var(--line2,#2a303d)}',
      '.ohb-card:active{transform:scale(.985)}',
      '.ohb-card.sel{border-color:var(--accent,#10b981);background:var(--accent-glow,rgba(16,185,129,.12))}',
      '.ohb-card__ic{flex:none;width:46px;height:46px;border-radius:12px;display:grid;place-items:center;background:var(--card-inner,#14171d);color:var(--accent,#10b981);font-size:24px;transition:background .18s,color .18s}',
      '.ohb-card.sel .ohb-card__ic{background:var(--accent,#10b981);color:#04120c}',
      '.ohb-card__t{display:block;font-size:17px;font-weight:700;margin:0 0 3px}',
      '.ohb-card__d{display:block;font-size:13.5px;color:var(--mut,#8e96a3);line-height:1.4;margin:0}',
      '.ohb-card__chk{margin-left:auto;flex:none;width:22px;height:22px;border-radius:50%;border:1.5px solid var(--line2,#2a303d);display:grid;place-items:center;color:transparent;font-size:13px;transition:background .18s,border-color .18s,color .18s}',
      '.ohb-card.sel .ohb-card__chk{background:var(--accent,#10b981);border-color:var(--accent,#10b981);color:#04120c}',
      // module groups
      '.ohb-grp{margin:0 0 10px;border:1px solid var(--line,#1d212a);border-radius:var(--radius-inner,16px);overflow:hidden;background:var(--card-outer,#0f1115)}',
      '.ohb-grp__h{display:flex;align-items:center;gap:11px;padding:13px 16px;user-select:none}',
      '.ohb-grp__h .gic{color:var(--accent,#10b981);font-size:19px;display:grid;place-items:center}',
      '.ohb-grp__nm{font-weight:700;font-size:15px}',
      '.ohb-grp__ct{margin-left:auto;font-size:12px;color:var(--dim,#768093);font-variant-numeric:tabular-nums}',
      '.ohb-grp__sec{display:flex;flex-wrap:wrap;gap:8px;padding:0 16px 14px}',
      '.ohb-pill{display:inline-flex;align-items:center;gap:7px;padding:7px 12px;border-radius:999px;border:1.5px solid var(--line,#1d212a);',
      'font-size:13px;cursor:pointer;color:var(--mut,#8e96a3);transition:border-color .15s,color .15s,background .15s,transform .09s;background:var(--card-inner,#14171d);font-family:inherit}',
      '.ohb-pill:active{transform:scale(.94)}',
      '.ohb-pill.on{border-color:var(--accent,#10b981);color:var(--ink,#fff);background:var(--accent-glow,rgba(16,185,129,.12))}',
      '.ohb-pill.pinned{opacity:.65;cursor:default}',
      '.ohb-pill i{font-size:14px}',
      '.ohb-soon{margin-left:2px;font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim,#768093);opacity:.8}',
      // palette swatches
      '.ohb-pals{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}',
      '.ohb-pal{border:1.5px solid var(--line,#1d212a);border-radius:var(--radius-inner,16px);padding:14px;cursor:pointer;background:var(--card-outer,#0f1115);transition:border-color .18s,transform .09s;font-family:inherit;color:inherit;text-align:left}',
      '.ohb-pal:hover{border-color:var(--line2,#2a303d)}',
      '.ohb-pal:active{transform:scale(.98)}',
      '.ohb-pal.sel{border-color:var(--accent,#10b981)}',
      '.ohb-pal__sw{display:block;position:relative;height:44px;border-radius:10px;margin-bottom:10px;overflow:hidden;border:1px solid var(--line,#1d212a)}',
      '.ohb-pal__sw>u,.ohb-pal__sw>b{display:block;text-decoration:none}',
      '.ohb-pal__sw>u{position:absolute;left:10px;top:9px;width:44%;height:7px;border-radius:4px;opacity:.9}',
      '.ohb-pal__sw>u+u{top:22px;width:28%;opacity:.45}',
      '.ohb-pal__sw>b{position:absolute;right:10px;top:10px;width:24px;height:24px;border-radius:50%}',
      '.ohb-pal__nm{display:block;font-weight:700;font-size:14px}',
      '.ohb-pal__sub{display:block;font-size:12px;color:var(--dim,#768093)}',
      // how-it-works rows + cases
      '.ohb-rows{display:flex;flex-direction:column;gap:14px;margin:0 0 24px}',
      '.ohb-row{display:flex;gap:14px;align-items:flex-start}',
      '.ohb-row i{flex:none;width:40px;height:40px;border-radius:11px;display:grid;place-items:center;background:var(--card-inner,#14171d);color:var(--accent,#10b981);font-size:20px}',
      '.ohb-row b{display:block;font-size:15px;margin:0 0 2px}',
      '.ohb-row span{font-size:13.5px;color:var(--mut,#8e96a3);line-height:1.45}',
      '.ohb-cases{display:flex;flex-direction:column;gap:10px}',
      '.ohb-case{border:1px solid var(--line,#1d212a);border-radius:14px;padding:13px 15px;background:var(--card-outer,#0f1115)}',
      '.ohb-case__h{display:flex;align-items:center;gap:9px;font-weight:700;font-size:14px;margin:0 0 5px}',
      '.ohb-case__h i{color:var(--accent,#10b981);font-size:17px}',
      '.ohb-case p{margin:0;font-size:13px;color:var(--mut,#8e96a3);line-height:1.45}',
      // summary / assemble
      '.ohb-big{font-variant-numeric:tabular-nums;font-weight:800;font-size:clamp(72px,12vw,120px);line-height:.9;letter-spacing:-.03em;margin:6px 0 2px;color:var(--accent,#10b981)}',
      '.ohb-biglbl{font-size:15px;color:var(--mut,#8e96a3);margin:0 0 22px}',
      '.ohb-chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 8px}',
      '.ohb-chip{display:inline-flex;align-items:center;gap:7px;padding:8px 13px;border-radius:999px;background:var(--card-inner,#14171d);border:1px solid var(--line,#1d212a);font-size:13px;transition:border-color .2s,background .2s}',
      '.ohb-chip i{color:var(--accent,#10b981)}',
      '.ohb-chip.lock{border-color:var(--accent,#10b981);background:var(--accent-glow,rgba(16,185,129,.12))}',
      // footer
      '.ohb-foot{border-top:1px solid var(--line,#1d212a);padding:16px 26px;display:flex;gap:12px;align-items:center;max-width:760px;margin:0 auto;width:100%}',
      '.ohb-back{background:none;border:0;color:var(--mut,#8e96a3);font-size:14px;cursor:pointer;padding:12px;font-family:inherit}',
      '.ohb-back:hover{color:var(--ink,#fff)}',
      '.ohb-foot__note{margin-left:auto;color:var(--mut,#8e96a3);font-size:14px;display:none}',
      '.ohb-next{margin-left:auto;background:var(--accent,#10b981);color:#04120c;border:0;border-radius:999px;padding:14px 30px;',
      'font-size:15px;font-weight:800;cursor:pointer;transition:transform .12s,opacity .15s,background .45s ease;font-family:inherit}',
      '.ohb-next:hover{transform:translateY(-1px)}',
      '.ohb-next:active{transform:scale(.97)}',
      '.ohb-next[disabled]{opacity:.4;cursor:not-allowed;transform:none}',
      '@media(max-width:560px){.ohb-stage{padding:8px 18px 32px}.ohb-rail,.ohb-foot{padding-left:18px;padding-right:18px}.ohb-ghost{left:8px}}',
      '@media(prefers-reduced-motion:reduce){.ohb-root,.ohb-root *{animation:none!important;transition:none!important}}'
    ].join('');
    document.head.appendChild(s);
  }

  function groupById(id) { return (OH.registry.groups || []).find(function (g) { return g.id === id; }); }
  function sectionsOf(groupId) {
    var g = groupById(groupId); if (!g) return [];
    return (g.section_ids || []).map(function (id) { return OH.section(id); }).filter(Boolean);
  }

  function defaultChosen(goal) {
    var chosen = {};
    (OH.registry.groups || []).forEach(function (g) {
      var keep = goal.keepGroups.indexOf(g.id) >= 0;
      (g.section_ids || []).forEach(function (sid) {
        var s = OH.section(sid); if (!s) return;
        // keep every section of a chosen group — matches how nav already renders
        // (incl. "coming soon" stubs); persona picks WHICH groups, not maturity.
        if (keep) chosen[sid] = 1;
      });
    });
    Object.keys(PINNED).forEach(function (k) { if (OH.section(k)) chosen[k] = 1; });
    return chosen;
  }

  // Restore the CURRENT setup for a re-run: goal from the active persona,
  // chosen = every registry section that is not hidden right now.
  function restoreChosen() {
    var chosen = {};
    (OH.registry.sections || []).forEach(function (s) {
      if (PINNED[s.id] || !OH.nav.isHidden(s.id)) chosen[s.id] = 1;
    });
    return chosen;
  }

  var STEPS = ['welcome', 'goal', 'modules', 'palette', 'how', 'ready'];

  function open(opts) {
    opts = opts || {};
    if (!global.OH || !OH.registry) { return; }
    // Single instance: a manual open (Settings) can race the delayed auto-start —
    // tear down any existing overlay before building a fresh one.
    document.removeEventListener('keydown', onKey, true);
    document.querySelectorAll('.ohb-root').forEach(function (n) { if (n.parentNode) n.parentNode.removeChild(n); });
    el = null; els = null;
    ICONW = detectIconWeight();
    css();
    state = { i: 0, goal: null, chosen: {}, palette: PALETTES[0], rerun: !!opts.rerun, _touchedModules: false };
    var curAccent = get(LS.accent);
    if (curAccent) { var p = PALETTES.find(function (x) { return x.accent === curAccent; }); if (p) state.palette = p; }
    if (state.rerun) {
      // restore the live setup so re-running edits reality, not a blank slate
      var personaId = get('oh.persona');
      if (personaId) { var gg = GOALS.find(function (x) { return x.persona === personaId; }); if (gg) state.goal = gg; }
      state.chosen = restoreChosen();
      state._touchedModules = true;
      state.i = 1; // straight to the goal step — welcome pitch is for first-timers
    }

    el = document.createElement('div');
    el.className = 'ohb-root';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', 'Настройка OpenHealth');
    // persistent chrome: rail + ghost/stage + footer render ONCE; steps swap inside
    el.innerHTML =
      '<div class="ohb-rail">' +
        '<span class="ohb-num"><b id="ohbNum">01</b> <small>/ ' + String(STEPS.length).padStart(2, '0') + '</small></span>' +
        '<span class="ohb-bar"><i id="ohbBar"></i></span>' +
        '<button class="ohb-skip" id="ohbSkip">Пропустить</button>' +
      '</div>' +
      '<div class="ohb-scroll" id="ohbScroll"><div class="ohb-stage">' +
        '<div class="ohb-ghost" id="ohbGhost"></div>' +
        '<div class="ohb-step" id="ohbStep"></div>' +
      '</div></div>' +
      '<div class="ohb-foot">' +
        '<button class="ohb-back" id="ohbBack">Назад</button>' +
        '<span class="ohb-foot__note" id="ohbNote">Собираю вашу OpenHealth…</span>' +
        '<button class="ohb-next" id="ohbNext">Поехали</button>' +
      '</div>';
    document.body.appendChild(el);
    els = {
      num: el.querySelector('#ohbNum'), bar: el.querySelector('#ohbBar'),
      skip: el.querySelector('#ohbSkip'), ghost: el.querySelector('#ohbGhost'),
      step: el.querySelector('#ohbStep'), scroll: el.querySelector('#ohbScroll'),
      back: el.querySelector('#ohbBack'), note: el.querySelector('#ohbNote'),
      next: el.querySelector('#ohbNext')
    };
    els.skip.onclick = function () { if (state.rerun) close(); else finishSkip(); };
    els.back.onclick = function () { go(-1); };
    els.next.onclick = function () {
      if (STEPS[state.i] === 'ready') { commit(); return; }
      if (!els.next.disabled) go(1);
    };
    document.addEventListener('keydown', onKey, true);
    renderStep(0);
  }

  function onKey(e) {
    if (!el || !state) return;
    var tag = document.activeElement && document.activeElement.tagName;
    if (e.key === 'Escape') { e.preventDefault(); if (state.rerun) close(); else finishSkip(); return; }
    if (e.key === 'Enter' && tag === 'BUTTON' && el.contains(document.activeElement)) return; // native click
    if (e.key === 'Enter' || e.key === 'ArrowRight') {
      e.preventDefault();
      if (STEPS[state.i] === 'ready') { commit(); return; }
      if (!els.next.disabled) go(1);
    }
    if (e.key === 'ArrowLeft' && state.i > 0) { e.preventDefault(); go(-1); }
  }

  function close() {
    if (!el) return;
    document.removeEventListener('keydown', onKey, true);
    var node = el; el = null; els = null; state = null;
    node.style.transition = 'opacity .3s ease'; node.style.opacity = '0';
    setTimeout(function () { if (node && node.parentNode) node.parentNode.removeChild(node); }, 300);
  }

  function go(delta) {
    var next = state.i + delta;
    if (next < 0 || next >= STEPS.length) return;
    state.i = next;
    renderStep(delta);
  }

  // Directional step swap: quick exit of the old content, then new content
  // enters from the travel direction with a stagger. Base state = visible.
  function renderStep(dir) {
    var step = STEPS[state.i];
    var pct = Math.round((state.i / (STEPS.length - 1)) * 100);

    // persistent chrome updates (rail transition actually animates now)
    els.num.textContent = String(state.i + 1).padStart(2, '0');
    els.bar.style.width = pct + '%';
    els.skip.textContent = (step === 'welcome' && !state.rerun) ? 'Пропустить' : 'Закрыть';
    els.back.style.visibility = state.i > 0 ? 'visible' : 'hidden';
    updateNext();

    var ghost = GHOSTS[step] || '';
    els.ghost.textContent = ghost;
    els.ghost.style.opacity = ghost ? '' : '0';

    var html =
      step === 'welcome' ? viewWelcome() :
      step === 'goal' ? viewGoal() :
      step === 'modules' ? viewModules() :
      step === 'palette' ? viewPalette() :
      step === 'how' ? viewHow() : viewReady();

    // SYNCHRONOUS swap — step progression must never wait on a timer (background
    // tabs throttle timers hard; a deferred swap once stranded the old step on
    // screen). Direction lives in the enter animation; no exit phase.
    els.step.innerHTML = html;
    els.scroll.scrollTop = 0;
    wire(step);
    var dx = dir >= 0 ? 18 : -16;
    anim(els.step, [
      { opacity: 0, transform: 'translate(' + (dir === 0 ? 0 : dx) + 'px,' + (dir === 0 ? '14px' : '0') + ')' },
      { opacity: 1, transform: 'none' }
    ], { duration: 380, easing: EASE_OUT });
    // stagger direct content blocks (cards/groups/rows)
    var kids = els.step.querySelectorAll('.ohb-card,.ohb-grp,.ohb-row,.ohb-case,.ohb-pal');
    for (var i = 0; i < kids.length && i < 10; i++) {
      anim(kids[i], [
        { opacity: 0, transform: 'translateY(10px)' },
        { opacity: 1, transform: 'none' }
      ], { duration: 320, easing: EASE_OUT, delay: 40 * i, fill: 'backwards' });
    }
  }

  // Next-button state machine (label doubles as the inline hint)
  function updateNext() {
    var step = STEPS[state.i];
    var lbl = 'Дальше', disabled = false;
    if (step === 'welcome') lbl = 'Поехали';
    if (step === 'goal' && !state.goal) { lbl = 'Выберите цель'; disabled = true; }
    if (step === 'ready') lbl = 'Собрать мою OpenHealth';
    els.next.textContent = lbl;
    els.next.disabled = disabled;
  }

  function viewWelcome() {
    return '<div class="ohb-hero">' +
      '<p class="ohb-kicker">Настройка</p>' +
      '<h1 class="ohb-h">Соберём вашу OpenHealth<br>под вас</h1>' +
      '<p class="ohb-sub">Это как настройка нового телефона. Несколько спокойных шагов — и на экране останется только то, что важно именно вам. Ничего лишнего, всё можно поменять позже.</p>' +
      '</div>' +
      '<div class="ohb-rows">' +
        row('ph-target', 'Начнём с цели', 'Скажете, ради чего вы здесь — приложение подстроится.') +
        row('ph-squares-four', 'Соберём модули', 'Включим только нужные разделы. Остальные не будут мешать.') +
        row('ph-palette', 'Выберете вид', 'Тема и цвет под ваш вкус.') +
      '</div>';
  }

  function viewGoal() {
    var cards = GOALS.map(function (g) {
      var sel = state.goal && state.goal.id === g.id;
      return '<button class="ohb-card' + (sel ? ' sel' : '') + '" data-goal="' + g.id + '">' +
        '<span class="ohb-card__ic">' + icon(g.icon) + '</span>' +
        '<span><span class="ohb-card__t">' + esc(g.title) + '</span>' +
        '<span class="ohb-card__d">' + esc(g.hook) + '</span></span>' +
        '<span class="ohb-card__chk">' + icon('ph-check') + '</span>' +
        '</button>';
    }).join('');
    return '<p class="ohb-kicker">Шаг 1 · Цель</p>' +
      '<h1 class="ohb-h">Что для вас сейчас главное?</h1>' +
      '<p class="ohb-sub">Выберите одно. Это только старт — приложение соберётся вокруг этой цели, а не вокруг всех функций сразу.</p>' +
      '<div class="ohb-cards">' + cards + '</div>';
  }

  function viewModules() {
    if (!state.goal) state.goal = GOALS[0];
    if (!state._touchedModules) state.chosen = defaultChosen(state.goal);
    var groups = (OH.registry.groups || []).slice().sort(function (a, b) { return (a.order || 0) - (b.order || 0); });
    var html = groups.map(function (g) {
      var secs = sectionsOf(g.id);
      if (!secs.length) return '';
      var cnt = secs.filter(function (s) { return state.chosen[s.id] || PINNED[s.id]; }).length;
      var pills = secs.map(function (s) {
        var pinned = !!PINNED[s.id];
        var onp = pinned || state.chosen[s.id];
        var soon = (s.status === 'soon' || s.status === 'empty');
        return '<button class="ohb-pill' + (onp ? ' on' : '') + (pinned ? ' pinned' : '') + '" data-sec="' + s.id + '"' + (pinned ? ' disabled' : '') + '>' +
          icon(s.icon || 'ph-circle') + esc(s.label_ru || s.id) +
          (soon ? '<span class="ohb-soon">скоро</span>' : '') + '</button>';
      }).join('');
      return '<div class="ohb-grp" data-grp="' + g.id + '"><div class="ohb-grp__h"><span class="gic">' + icon(g.icon || 'ph-folder') + '</span>' +
        '<span class="ohb-grp__nm">' + esc(g.label_ru || g.id) + '</span>' +
        '<span class="ohb-grp__ct" data-grp-ct>' + cnt + ' из ' + secs.length + '</span></div>' +
        '<div class="ohb-grp__sec">' + pills + '</div></div>';
    }).join('');
    return '<p class="ohb-kicker">Шаг 2 · Модули</p>' +
      '<h1 class="ohb-h">Соберём только нужное</h1>' +
      '<p class="ohb-sub">Мы уже отметили разделы под вашу цель (сейчас <b id="ohbOn">' + countOn() + '</b>). Уберите лишнее или добавьте своё — «Сегодня» остаётся всегда.</p>' +
      html;
  }

  function countOn() {
    var n = 0;
    (OH.registry.sections || []).forEach(function (s) { if (state.chosen[s.id] || PINNED[s.id]) n++; });
    return n;
  }

  function viewPalette() {
    var pals = PALETTES.map(function (p) {
      var sel = state.palette && state.palette.id === p.id;
      var bg = p.theme === 'light' ? '#f4f5f7' : '#0d0f13';
      var ln = p.theme === 'light' ? '#d7dade' : '#232833';
      return '<button class="ohb-pal' + (sel ? ' sel' : '') + '" data-pal="' + p.id + '">' +
        '<span class="ohb-pal__sw" style="background:' + bg + '">' +
          '<u style="background:' + p.accent + '"></u><u style="background:' + ln + '"></u>' +
          '<b style="background:' + p.accent + '"></b></span>' +
        '<span class="ohb-pal__nm">' + esc(p.name) + '</span><span class="ohb-pal__sub">' + esc(p.sub) + '</span>' +
        '</button>';
    }).join('');
    return '<p class="ohb-kicker">Шаг 3 · Вид</p>' +
      '<h1 class="ohb-h">Выберите палитру</h1>' +
      '<p class="ohb-sub">Меняет тему и акцентный цвет всего приложения. Превью применяется сразу — потом можно переключить в настройках.</p>' +
      '<div class="ohb-pals">' + pals + '</div>';
  }

  function viewHow() {
    var cases = CASES.map(function (c) {
      return '<div class="ohb-case"><div class="ohb-case__h">' + icon(c.icon) + esc(c.title) + '</div>' +
        '<p>' + esc(c.text) + '</p></div>';
    }).join('');
    return '<p class="ohb-kicker">Шаг 4 · Как это работает</p>' +
      '<h1 class="ohb-h">Коротко о главном</h1>' +
      '<div class="ohb-rows">' +
        row('ph-devices', 'Везде одна база', 'Смотрите на компьютере, а телефон открывает то же самое. Внесли утром в заметку — вечером видно в приложении.') +
        row('ph-lock-simple', 'Данные у вас', 'OpenHealth хранит всё локально, на вашей стороне. Никакого облака по умолчанию.') +
        row('ph-chat-circle-dots', 'Помощник по желанию', 'Можно подключить агента (в том числе свой код или Codex), чтобы он разбирал данные и готовил заметки. Это опция, не обязанность.') +
        row('ph-flask', 'Осторожно с выводами', 'Приложение задаёт вопросы и показывает закономерности, но не ставит диагнозов. Решения — с врачом.') +
      '</div>' +
      '<p class="ohb-kicker">Живые примеры</p>' +
      '<div class="ohb-cases">' + cases + '</div>';
  }

  function viewReady() {
    var secs = chosenSections();
    var chips = secs.map(function (s) {
      return '<span class="ohb-chip">' + icon(s.icon || 'ph-circle') + esc(s.label_ru || s.id) + '</span>';
    }).join('');
    var gname = state.goal ? state.goal.title : '';
    return '<p class="ohb-kicker">Готово</p>' +
      '<div class="ohb-big" id="ohb-count">' + secs.length + '</div>' +
      '<p class="ohb-biglbl">модулей в вашей OpenHealth · цель: ' + esc(gname) + '</p>' +
      '<div class="ohb-chips">' + chips + '</div>' +
      '<p class="ohb-sub" style="margin-top:18px">Нажмите «Собрать» — экран пересоберётся под эти модули и палитру. Захотите иначе — «Перезапустить настройку» живёт в разделе «Настройки».</p>';
  }

  function row(ic, t, d) {
    return '<div class="ohb-row">' + icon(ic) + '<div><b>' + esc(t) + '</b><span>' + esc(d) + '</span></div></div>';
  }

  function chosenSections() {
    var out = [];
    (OH.registry.sections || []).forEach(function (s) {
      if (s.id === 'settings' || s.id === 'sync') return; // infra, not a user module
      if (state.chosen[s.id] || PINNED[s.id]) out.push(s);
    });
    return out;
  }

  // Per-step listeners. Selection changes DON'T re-render the whole step —
  // they toggle classes and update counters in place, so nothing jumps.
  function wire(step) {
    if (step === 'goal') {
      els.step.querySelectorAll('[data-goal]').forEach(function (b) {
        b.onclick = function () {
          state.goal = GOALS.find(function (g) { return g.id === b.getAttribute('data-goal'); });
          state._touchedModules = false;
          els.step.querySelectorAll('[data-goal]').forEach(function (x) { x.classList.toggle('sel', x === b); });
          anim(b, [{ transform: 'scale(.97)' }, { transform: 'scale(1)' }], { duration: 240, easing: EASE_SPRING });
          var wasDisabled = els.next.disabled;
          updateNext();
          if (wasDisabled) anim(els.next, [{ transform: 'scale(.9)', opacity: 0.4 }, { transform: 'scale(1)', opacity: 1 }], { duration: 260, easing: EASE_SPRING });
        };
      });
    }
    if (step === 'modules') {
      els.step.querySelectorAll('[data-sec]').forEach(function (b) {
        if (b.disabled) return;
        b.onclick = function () {
          var id = b.getAttribute('data-sec');
          state.chosen[id] = state.chosen[id] ? 0 : 1;
          state._touchedModules = true;
          b.classList.toggle('on', !!state.chosen[id]);
          anim(b, [{ transform: 'scale(.92)' }, { transform: 'scale(1)' }], { duration: 220, easing: EASE_SPRING });
          // update the group counter + the "сейчас N" note in place
          var grp = b.closest('.ohb-grp');
          if (grp) {
            var gid = grp.getAttribute('data-grp');
            var secs = sectionsOf(gid);
            var cnt = secs.filter(function (s) { return state.chosen[s.id] || PINNED[s.id]; }).length;
            var ct = grp.querySelector('[data-grp-ct]'); if (ct) ct.textContent = cnt + ' из ' + secs.length;
          }
          var on = document.getElementById('ohbOn'); if (on) on.textContent = countOn();
        };
      });
    }
    if (step === 'palette') {
      els.step.querySelectorAll('[data-pal]').forEach(function (b) {
        b.onclick = function () {
          state.palette = PALETTES.find(function (p) { return p.id === b.getAttribute('data-pal'); });
          els.step.querySelectorAll('[data-pal]').forEach(function (x) { x.classList.toggle('sel', x === b); });
          anim(b, [{ transform: 'scale(.96)' }, { transform: 'scale(1)' }], { duration: 240, easing: EASE_SPRING });
          previewPalette(state.palette); // root has a .45s background/color transition — real crossfade
        };
      });
    }
    if (step === 'ready') { countUp(); }
  }

  // live preview while choosing (temporary; committed on finish)
  function previewPalette(p) {
    var root = document.documentElement;
    root.setAttribute('data-theme', p.theme);
    root.style.setProperty('--accent', p.accent);
    root.style.setProperty('--accent-text', p.accent);
    root.style.setProperty('--accent-glow', glow(p.accent, 0.15));
  }

  function countUp() {
    var node = document.getElementById('ohb-count'); if (!node) return;
    var target = parseInt(node.textContent, 10) || 0;
    // rAF freezes in throttled tabs: skip when hidden, and always land on target.
    if (RM || document.hidden || target <= 0) { node.textContent = target; return; }
    var startTs = null;
    function tick(ts) {
      if (startTs == null) startTs = ts;
      var t = Math.min(1, (ts - startTs) / 650);
      node.textContent = Math.round(target * (1 - Math.pow(1 - t, 3)));
      if (t < 1) requestAnimationFrame(tick); else node.textContent = target;
    }
    requestAnimationFrame(tick);
    setTimeout(function () { node.textContent = target; }, 800);
  }

  // "Пропустить" on genuine first run: mark onboarded, hide nothing.
  function finishSkip() {
    set(LS.onboarded, '1');
    close();
  }

  function commit() {
    // persona
    if (OH.setPersona) OH.setPersona(state.goal && state.goal.persona ? state.goal.persona : null);
    // hidden = complement of chosen (sections + fully-off groups)
    var hidden = [];
    (OH.registry.sections || []).forEach(function (s) {
      if (!state.chosen[s.id] && !PINNED[s.id]) hidden.push(s.id);
    });
    (OH.registry.groups || []).forEach(function (g) {
      var any = (g.section_ids || []).some(function (id) { return state.chosen[id] || PINNED[id]; });
      if (!any) hidden.push('group:' + g.id);
    });
    set(LS.hidden, JSON.stringify(hidden));
    // palette
    if (state.palette) { set(LS.theme, state.palette.theme); set(LS.accent, state.palette.accent); }
    set(LS.onboarded, '1');

    // Assembly finale: chips lock in as a wave, the numeral pulses once, then
    // the whole overlay lifts away. The reload is on a hard timeout backstop —
    // it fires no matter what the animations did.
    els.next.disabled = true; els.next.style.display = 'none';
    els.back.style.visibility = 'hidden'; els.skip.style.visibility = 'hidden';
    els.note.style.display = 'block';
    // Wave via WAAPI delays, not timers (throttle-safe): .lock lands instantly
    // (transitions soften it), the scale pop ripples chip by chip.
    var chips = els.step.querySelectorAll('.ohb-chip');
    for (var i = 0; i < chips.length; i++) {
      chips[i].classList.add('lock');
      anim(chips[i], [{ transform: 'scale(.94)' }, { transform: 'scale(1)' }], { duration: 220, easing: EASE_SPRING, delay: 40 * i, fill: 'backwards' });
    }
    var big = document.getElementById('ohb-count');
    if (big) anim(big, [{ transform: 'scale(1)' }, { transform: 'scale(1.06)' }, { transform: 'scale(1)' }], { duration: 320, easing: EASE_SPRING });
    var lift = 40 * chips.length + 380;
    if (el) anim(el, [
      { transform: 'none', opacity: 1, offset: 0 },
      { transform: 'none', opacity: 1, offset: 0.6 },
      { transform: 'translateY(-24px)', opacity: 0.85, offset: 1 }
    ], { duration: lift + 420, easing: 'ease-in' });
    // The reload is the one true finish line — a hard timeout regardless of
    // animation state (an active user tab never throttles this badly).
    setTimeout(function () { location.reload(); }, Math.max(1200, lift + 460));
  }

  // Auto-start only on a genuine first run.
  function maybeAutoStart() {
    if (get(LS.onboarded)) return;
    if (!global.OH || !OH.registry) return;
    if (document.querySelector('.ohb-root')) return; // an overlay is already up — never clobber it
    open({ rerun: false });
  }

  var API = {
    open: open,
    maybeAutoStart: maybeAutoStart,
    applyPalette: applyPalette,
    applyModuleVisibility: applyModuleVisibility,
    reset: function () { try { localStorage.removeItem(LS.onboarded); } catch (e) {} }
  };
  global.OHOnboarding = API;

  // On load: apply saved accent immediately, then enforce module visibility
  // (covers V2 bespoke cards) once and on later async mounts.
  applyPalette();
  function boot() {
    applyModuleVisibility();
    try {
      var mo = new MutationObserver(function () { applyModuleVisibility(); });
      mo.observe(document.body, { childList: true, subtree: true });
      setTimeout(function () { mo.disconnect(); }, 6000);
    } catch (e) {}
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

})(typeof window !== 'undefined' ? window : this);
