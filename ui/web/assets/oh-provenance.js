/* oh-provenance.js - "how is this computed?" layer for every metric.
 *
 * A "?" on each metric card opens a provenance popover: what it is / how it's
 * computed / why it matters + source + protocol_ref, with actions "ask the agent"
 * (POST /api/agent, with a copy-prompt handoff to Claude Code / Codex desktop) and
 * "re-verify". OH.algorithmsView() renders the full read-only Algorithms section.
 * Shared across both skins (delegated clicks on .oh-q[data-prov]); self-themes via
 * CSS-var fallbacks. Protocols are read-only in this result (editing comes later).
 */
(function (global) {
  'use strict';
  var OH = global.OH;
  if (!OH) return;

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function bridgePrompt(m, mode) {
    var pr = m.provenance || {};
    var lead = mode === 'recheck'
      ? 'Перепроверь расчёт и, если уместно, предложи улучшение протокола (как опцию, не утверждение). '
      : '';
    return lead +
      'Объясни максимально точно и дотошно, как в OpenHealth считается метрика "' + (m.label_ru || m.id) + '" (id: ' + m.id + '). ' +
      'Источник данных: ' + (m.source || '?') + '. Протокол: ' + (m.protocol_ref || '?') + '. Тип графика: ' + (m.chart || '?') + '. ' +
      'Что это: ' + (pr.what || '') + ' Как считается: ' + (pr.how || '') + ' Почему важно: ' + (pr.why || '') + ' ' +
      'Разбери формулу, входные данные, как они проверяются и сопоставляются с источниками, и какие допущения. ' +
      'Покажи, где в коде/протоколе это живёт, и как пользователь мог бы изменить протокол.';
  }

  function ensureStyle() {
    if (document.getElementById('oh-prov-style')) return;
    var s = document.createElement('style');
    s.id = 'oh-prov-style';
    s.textContent =
      // 18px visual circle, but a ~30px hit-area via an invisible ::after pad so
      // a finger can actually tap it; same trick on both overlay close buttons.
      '.oh-q{all:unset;cursor:pointer;width:18px;height:18px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font:700 11px system-ui,sans-serif;color:var(--mut,var(--text-muted,#888));border:1px solid currentColor;opacity:.55;flex:0 0 auto;position:relative}' +
      '.oh-q::after{content:"";position:absolute;inset:-6px}' +
      '.oh-q:hover{opacity:1}' +
      '#oh-prov-pop{position:fixed;inset:0;z-index:100002;display:none}' +
      '#oh-prov-pop .oh-prov-back{position:absolute;inset:0;background:rgba(0,0,0,.5);-webkit-backdrop-filter:blur(2px);backdrop-filter:blur(2px)}' +
      '#oh-prov-pop .oh-prov-card{position:relative;max-width:min(560px,calc(100vw - 24px));margin:8vh auto 0;max-height:84vh;overflow:auto;background:var(--card-inner,var(--bg-card,#fff));color:var(--ink,var(--text-primary,#111));border-radius:18px;padding:20px 22px;box-shadow:0 20px 60px rgba(0,0,0,.4)}' +
      '.oh-prov-head{display:flex;justify-content:space-between;align-items:center;font-weight:700;font-size:16px;margin-bottom:12px}' +
      '.oh-prov-x{all:unset;cursor:pointer;opacity:.6;font-size:18px;padding:10px;margin:-10px}' +
      '.oh-prov-row{margin-bottom:10px}.oh-prov-row b{display:block;font-size:11px;letter-spacing:.5px;text-transform:uppercase;opacity:.55;margin-bottom:2px}.oh-prov-row p{margin:0;font-size:14px;line-height:1.45}' +
      '.oh-prov-meta{font-size:12px;opacity:.7;margin:10px 0}.oh-prov-meta code{background:rgba(127,127,127,.18);padding:1px 6px;border-radius:6px}' +
      '.oh-prov-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}' +
      '.oh-prov-btn{all:unset;cursor:pointer;font-size:13px;font-weight:600;padding:8px 12px;border-radius:10px;background:rgba(127,127,127,.14);display:inline-flex;align-items:center;gap:6px}' +
      '.oh-prov-btn:hover{background:rgba(127,127,127,.24)}' +
      '.oh-prov-answer{margin-top:12px}.oh-prov-loading{opacity:.7;font-size:13px}' +
      '.oh-prov-result{font-size:13px;line-height:1.5;white-space:pre-wrap;background:rgba(127,127,127,.1);padding:12px;border-radius:10px}' +
      '.oh-prov-offline{font-size:13px;opacity:.8;margin-bottom:8px}' +
      '.oh-prov-prompt{width:100%;min-height:90px;margin-top:8px;font:12px/1.4 ui-monospace,monospace;border-radius:8px;border:1px solid rgba(127,127,127,.3);background:transparent;color:inherit;padding:8px}' +
      '.oh-algos-intro{opacity:.7;font-size:13px;margin:0 0 14px}.oh-algos-sec{font-size:14px;margin:18px 0 8px;opacity:.85}' +
      '.oh-algos-row{padding:10px 0;border-top:1px solid rgba(127,127,127,.15)}.oh-algos-name{font-weight:600;font-size:14px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}' +
      '.oh-algos-name code{background:rgba(127,127,127,.18);padding:1px 6px;border-radius:6px;font-size:12px}.oh-algos-src{font-size:11px;opacity:.6}.oh-algos-how{font-size:13px;opacity:.8;margin-top:3px}' +
      // Knowledge layer (devices/sources) + evidence badge + video refs
      '.oh-section--knowledge .oh-k-intro{opacity:.7;font-size:13px;margin:2px 0 14px;line-height:1.5}' +
      '.oh-kcat{font-size:13px;font-weight:700;letter-spacing:.3px;margin:18px 0 10px;display:flex;align-items:center;gap:8px;opacity:.85}' +
      '.oh-kgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}' +
      '.oh-kcard{background:var(--card-inner,var(--bg-card,rgba(127,127,127,.06)));border:1px solid rgba(127,127,127,.16);border-radius:14px;padding:14px 15px;display:flex;flex-direction:column;gap:6px}' +
      '.oh-kcard__top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}' +
      '.oh-kcard__name{font-weight:700;font-size:14px;line-height:1.25}' +
      '.oh-kcard__area{font-size:12.5px;line-height:1.4;opacity:.85}' +
      '.oh-kcard__meta{font-size:12px;opacity:.7}.oh-kcard__meta b{opacity:.95}' +
      '.oh-kcard__use{font-size:12px;opacity:.8;line-height:1.4}.oh-kcard__alt{font-size:11.5px;opacity:.6}' +
      '.oh-kcard__caveat{font-size:11.5px;opacity:.75;line-height:1.4;display:flex;gap:5px;align-items:flex-start}' +
      '.oh-kcard__actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:4px}' +
      '.oh-kcard__checked{font-size:11px;opacity:.5;margin-left:auto}' +
      '.oh-kbtn{all:unset;cursor:pointer;font-size:12px;font-weight:600;padding:5px 9px;border-radius:8px;background:rgba(127,127,127,.14);display:inline-flex;align-items:center;gap:5px;color:inherit}' +
      '.oh-kbtn:hover{background:rgba(127,127,127,.24)}' +
      '.oh-ev{font-size:10.5px;font-weight:700;letter-spacing:.3px;padding:2px 7px;border-radius:20px;white-space:nowrap;flex:0 0 auto}' +
      '.oh-ev--high{background:rgba(39,194,138,.16);color:#1f9d6e}.oh-ev--mid{background:rgba(230,170,60,.18);color:#b9852a}.oh-ev--low{background:rgba(224,112,106,.18);color:#c75a54}' +
      '.oh-kvid{display:flex;align-items:center;gap:8px;font-size:13px;padding:6px 0;color:inherit;text-decoration:none}.oh-kvid:hover{text-decoration:underline}.oh-kvid i{opacity:.6;flex:0 0 auto}.oh-kvid .oh-ev{margin-left:auto}';
    document.head.appendChild(s);
  }

  function evidenceRow(m) {
    var ev = OH.evidence ? OH.evidence(m.id) : null;
    if (!ev) return '';
    var lab = OH.evidenceLabel ? OH.evidenceLabel(ev.confidence) : { label: ev.confidence, cls: 'mid' };
    var srcs = (ev.sources && ev.sources.length) ? ' · ' + esc(ev.sources.join(', ')) : '';
    return '<div class="oh-prov-row"><b>Доказательность</b><p><span class="oh-ev oh-ev--' + lab.cls + '">' + esc(lab.label) + '</span> ' +
      (ev.type ? esc(ev.type) : '') + srcs + '</p></div>';
  }

  function videoRow(m) {
    var vids = OH.videosFor ? OH.videosFor(m.id) : [];
    if (!vids.length) return '';
    var items = vids.map(function (v) {
      var lab = OH.evidenceLabel ? OH.evidenceLabel(v.evidence_level) : { label: v.evidence_level, cls: 'mid' };
      return '<a class="oh-kvid" href="' + esc(v.url) + '" target="_blank" rel="noopener noreferrer"><i class="ph ph-youtube-logo"></i>' +
        '<span>' + esc(v.title) + ' · ' + esc(v.channel) + '</span><span class="oh-ev oh-ev--' + lab.cls + '">' + esc(lab.label) + '</span></a>';
    }).join('');
    return '<div class="oh-prov-row"><b>Короткие видео-объяснения</b>' + items + '</div>';
  }

  OH.provenanceCard = function (m) {
    var pr = m.provenance || {}, si = OH.sourceInfo(m.source);
    return '<div class="oh-prov-head"><span>' + esc(m.label_ru || m.id) + '</span><button class="oh-prov-x" aria-label="Закрыть">✕</button></div>' +
      '<div class="oh-prov-row"><b>Что это</b><p>' + esc(pr.what || '—') + '</p></div>' +
      '<div class="oh-prov-row"><b>Как считается</b><p>' + esc(pr.how || '—') + '</p></div>' +
      '<div class="oh-prov-row"><b>Почему важно</b><p>' + esc(pr.why || '—') + '</p></div>' +
      evidenceRow(m) +
      videoRow(m) +
      '<div class="oh-prov-meta">Источник: ' + esc(si.label) + ' · протокол: <code>' + esc(m.protocol_ref || '—') + '</code> · график: ' + esc(m.chart) + '</div>' +
      '<div class="oh-prov-actions">' +
        '<button class="oh-prov-btn" data-act="ask" data-id="' + m.id + '"><i class="ph ph-chat-circle-text"></i> Спросить агента</button>' +
        '<button class="oh-prov-btn" data-act="recheck" data-id="' + m.id + '"><i class="ph ph-arrows-clockwise"></i> Перепроверить</button>' +
        '<button class="oh-prov-btn" data-act="algos"><i class="ph ph-function"></i> Алгоритмы</button>' +
      '</div><div class="oh-prov-answer" id="oh-prov-answer"></div>';
  };

  function popover(metricId) {
    var m = OH.metric(metricId); if (!m) return;
    ensureStyle();
    var host = document.getElementById('oh-prov-pop');
    if (!host) { host = document.createElement('div'); host.id = 'oh-prov-pop'; document.body.appendChild(host); }
    host.innerHTML = '<div class="oh-prov-back"></div><div class="oh-prov-card" role="dialog" aria-modal="true">' + OH.provenanceCard(m) + '</div>';
    host.style.display = 'block';
    function close() { host.style.display = 'none'; host.innerHTML = ''; }
    host.querySelector('.oh-prov-back').onclick = close;
    host.querySelector('.oh-prov-x').onclick = close;
  }

  function handoff(prompt) {
    return '<div class="oh-prov-offline">Глубокий разбор — открой в Claude Code / Codex desktop (промпт готов):</div>' +
      '<button class="oh-prov-btn" data-act="copy"><i class="ph ph-copy"></i> Скопировать промпт</button>' +
      '<textarea class="oh-prov-prompt" readonly>' + esc(prompt) + '</textarea>';
  }

  function knowledgePrompt(item, kind) {
    if (kind === 'source') {
      return 'Перепроверь источник протоколов "' + item.name + '" (' + item.area + '). Ссылка: ' + item.url + '. ' +
        'Заявленный уровень доказательности: ' + item.evidence_level + '. Оговорка: ' + (item.caveat || '—') + '. ' +
        'Подтверди или скорректируй: репутация, актуальность, где источник переоценивает доказательность, какие claim принимать осторожно. Дай вывод и свежие ссылки.';
    }
    return 'Перепроверь устройство "' + item.name + '" (категория ' + item.category + '). Что заявлено меряет: ' + item.measures + '. ' +
      'Ключевая метрика: ' + item.key_metric + '. Ссылка: ' + item.source_url + '. Заявленный уровень доказательности: ' + item.evidence_level + '. ' +
      'Подтверди или скорректируй: действительно ли меряет заявленное (валидация против эталона), точность, где маркетинг vs данные. Дай вывод и свежие ссылки.';
  }

  // Verifiability for knowledge cards: level + source link + re-verify handoff.
  OH.knowledgeVerify = function (kind, id) {
    var item = (kind === 'source')
      ? OH.protocolSources().find(function (s) { return s.id === id; })
      : OH.devices().find(function (d) { return d.id === id; });
    if (!item) return;
    ensureStyle();
    var host = document.getElementById('oh-prov-pop');
    if (!host) { host = document.createElement('div'); host.id = 'oh-prov-pop'; document.body.appendChild(host); }
    var ev = OH.evidenceLabel(item.evidence_level);
    var url = item.url || item.source_url || '';
    var prompt = knowledgePrompt(item, kind);
    host.innerHTML = '<div class="oh-prov-back"></div><div class="oh-prov-card" role="dialog" aria-modal="true">' +
      '<div class="oh-prov-head"><span>Перепроверить · ' + esc(item.name) + '</span><button class="oh-prov-x" aria-label="Закрыть">✕</button></div>' +
      '<div class="oh-prov-meta">Уровень доказательности: <span class="oh-ev oh-ev--' + ev.cls + '">' + esc(ev.label) + '</span> · сверено ' + esc(item.checked_at || '—') + '</div>' +
      (url ? '<div class="oh-prov-actions"><a class="oh-prov-btn" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer"><i class="ph ph-link"></i> Открыть источник</a></div>' : '') +
      handoff(prompt) + '</div>';
    host.style.display = 'block';
    function close() { host.style.display = 'none'; host.innerHTML = ''; }
    host.querySelector('.oh-prov-back').onclick = close;
    host.querySelector('.oh-prov-x').onclick = close;
  };

  function ask(metricId, mode) {
    var m = OH.metric(metricId); if (!m) return;
    var box = document.getElementById('oh-prov-answer'); if (!box) return;
    var prompt = bridgePrompt(m, mode);
    box.innerHTML = '<div class="oh-prov-loading">Агент читает протокол…</div>';
    fetch('/api/agent', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ task: 'explain', param: m.id, prompt: prompt, lang: 'ru' }) })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (d) {
        var ans = d.result || d.answer || (d.status ? '' : JSON.stringify(d));
        box.innerHTML = (ans ? '<div class="oh-prov-result">' + esc(ans) + '</div>' : '<div class="oh-prov-offline">Агент не дал ответа.</div>') + handoff(prompt);
      })
      .catch(function () { box.innerHTML = handoff(prompt); });
  }

  // Full read-only Algorithms section rendered from the registry.
  OH.algorithmsView = function () {
    if (!OH.registry) return '';
    var html = '<section class="oh-section"><div class="oh-section__head"><span class="oh-section__icon"><i class="ph ph-function"></i></span><h2 class="oh-section__title">Алгоритмы расчётов</h2></div>' +
      '<p class="oh-algos-intro">Как OpenHealth считает каждую метрику: источник, протокол и провенанс. Read-only — редактирование протоколов появится отдельным результатом.</p>';
    OH.registry.sections.forEach(function (s) {
      var ids = s.metric_ids || []; if (!ids.length) return;
      html += '<h3 class="oh-algos-sec">' + esc(s.label_ru || s.id) + '</h3>';
      ids.forEach(function (id) {
        var m = OH.metric(id); if (!m) return; var pr = m.provenance || {};
        html += '<div class="oh-algos-row"><div class="oh-algos-name"><i class="ph ' + (m.icon || 'ph-circle') + '"></i> ' + esc(m.label_ru || id) +
          ' <code>' + esc(m.protocol_ref || '') + '</code> <span class="oh-algos-src">' + esc(OH.sourceInfo(m.source).label) + '</span></div>' +
          '<div class="oh-algos-how">' + esc(pr.how || pr.what || '') + '</div></div>';
      });
    });
    return html + '</section>';
  };

  function init() {
    if (init._bound) return; init._bound = true;
    ensureStyle();
    document.addEventListener('click', function (e) {
      var t = e.target.closest ? e.target.closest('.oh-q[data-prov], .oh-prov-btn, .oh-kverify') : null;
      if (!t) return;
      if (t.classList.contains('oh-q')) { e.preventDefault(); e.stopPropagation(); popover(t.getAttribute('data-prov')); return; }
      if (t.classList.contains('oh-kverify')) { e.preventDefault(); e.stopPropagation(); OH.knowledgeVerify(t.getAttribute('data-kind'), t.getAttribute('data-id')); return; }
      var act = t.getAttribute('data-act');
      if (act === 'ask') ask(t.getAttribute('data-id'), 'ask');
      else if (act === 'recheck') ask(t.getAttribute('data-id'), 'recheck');
      else if (act === 'copy') {
        // Honest feedback: say "Скопировано" only when the copy actually worked
        // (iOS Safari can reject both paths); otherwise leave the text selected.
        var ta = document.querySelector('.oh-prov-prompt'); if (!ta) return;
        var done = function () { t.innerHTML = '<i class="ph ph-check"></i> Скопировано'; };
        var legacy = function () {
          ta.removeAttribute('readonly'); ta.focus(); ta.setSelectionRange(0, ta.value.length);
          var ok = false; try { ok = document.execCommand('copy'); } catch (x) {}
          ta.setAttribute('readonly', '');
          if (ok) done(); else t.innerHTML = '<i class="ph ph-copy"></i> Выдели и скопируй вручную';
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(ta.value).then(done, legacy);
        } else legacy();
      }
      else if (act === 'algos') {
        var pop = document.getElementById('oh-prov-pop'); if (pop) { pop.style.display = 'none'; pop.innerHTML = ''; }
        var target = document.getElementById('oh-mount-algorithms') || document.getElementById('ohAlgorithms') || document.getElementById('z-r-algos');
        if (typeof go === 'function' && document.getElementById('z-r-algos')) { go('r-algos'); }
        else if (target && target.scrollIntoView) target.scrollIntoView({ behavior: 'smooth' });
      }
    }, true);
  }

  OH.provenance = { init: init, popover: popover, ask: ask };
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})(typeof window !== 'undefined' ? window : this);
