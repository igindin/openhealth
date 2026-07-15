/* oh-correlate.js - always-on drag-and-drop correlations across the dashboard.
 *
 * Drag ANY card with [data-metric] onto another card to see how the two metrics
 * relate - no "edit mode", works in every section and in both skins. Operates on
 * [data-metric] + OH (the shared registry), so it needs no per-skin code: each
 * skin just loads this script. Self-injects its overlay CSS (themed via CSS-var
 * fallbacks so it works on the dark V1 and light V2 alike) and self-inits.
 */
(function (global) {
  'use strict';
  var OH = global.OH;
  if (!OH) return;

  function pearson(a, b) {
    var n = Math.min(a.length, b.length);
    if (n < 4) return null;
    var sa = 0, sb = 0, i;
    for (i = 0; i < n; i++) { sa += a[i]; sb += b[i]; }
    var ma = sa / n, mb = sb / n, num = 0, da = 0, db = 0;
    for (i = 0; i < n; i++) { var x = a[i] - ma, y = b[i] - mb; num += x * y; da += x * x; db += y * y; }
    if (da === 0 || db === 0) return null;
    return num / Math.sqrt(da * db);
  }

  // Numeric daily-ish series for a metric, for correlation. Pulls trend arrays from
  // data.local.json where known, else an array-valued metric, else null (scalar).
  OH.series = function (id) {
    var m = OH.metric(id); if (!m) return null;
    var trendKey = { recovery: 'trend30Rec', hrv: 'trend30Hrv' }[id];
    if (trendKey && Array.isArray(OH.data[trendKey])) return OH.data[trendKey].map(Number).filter(function (x) { return !isNaN(x); });
    var v = OH.value(id);
    if (Array.isArray(v)) {
      var nums = v.map(function (x) {
        if (x && typeof x === 'object') return Number(x.value != null ? x.value : (x.h != null ? x.h : NaN));
        return Number(x);
      }).filter(function (x) { return !isNaN(x); });
      return nums.length >= 4 ? nums : null;
    }
    return null;
  };

  function interpret(r) {
    var a = Math.abs(r);
    return {
      strength: a >= 0.6 ? 'сильная' : a >= 0.3 ? 'умеренная' : 'слабая',
      dir: r >= 0 ? 'прямая связь (растут вместе)' : 'обратная связь (один растёт - другой падает)',
    };
  }

  function ensureStyle() {
    if (document.getElementById('oh-corr-style')) return;
    var s = document.createElement('style');
    s.id = 'oh-corr-style';
    s.textContent =
      '#oh-corr-overlay{position:fixed;inset:0;z-index:100001;display:none}' +
      '#oh-corr-overlay .oh-corr-back{position:absolute;inset:0;background:rgba(0,0,0,.5);-webkit-backdrop-filter:blur(2px);backdrop-filter:blur(2px)}' +
      '#oh-corr-overlay .oh-corr-card{position:relative;max-width:min(600px,calc(100vw - 24px));max-height:84vh;overflow:auto;margin:8vh auto 0;background:var(--card-inner,var(--bg-card,#fff));color:var(--ink,var(--text-primary,#111));border-radius:18px;padding:22px 24px;box-shadow:0 20px 60px rgba(0,0,0,.4);font-family:inherit}' +
      '.oh-corr-head{display:flex;justify-content:space-between;align-items:center;font-weight:700;font-size:16px;margin-bottom:14px}' +
      '.oh-corr-x{background:none;border:none;color:inherit;font-size:18px;cursor:pointer;opacity:.6;line-height:1;padding:10px;margin:-10px}' +
      '.oh-corr-r{font-size:34px;font-weight:800;line-height:1}' +
      '.oh-corr-int{opacity:.7;font-size:13px;margin-bottom:14px}' +
      '.oh-corr-series{margin:10px 0}' +
      '.oh-corr-leg{font-size:12px;font-weight:600;display:block;margin-bottom:2px}' +
      '.oh-corr-note{opacity:.55;font-size:11px;margin-top:12px}' +
      '.oh-corr-msg{opacity:.85;font-size:14px;line-height:1.5}' +
      '.oh-drag-ghost{box-shadow:0 16px 40px rgba(0,0,0,.35);border-radius:14px}' +
      '.oh-drag-src{opacity:.35}' +
      '.oh-drop-target{outline:2px dashed currentColor;outline-offset:2px;border-radius:14px}' +
      'body.oh-dragging{cursor:grabbing;user-select:none}' +
      '[data-metric]{cursor:grab}';
    document.head.appendChild(s);
  }

  function overlay(aId, bId) {
    ensureStyle();
    var a = OH.metric(aId), b = OH.metric(bId); if (!a || !b) return;
    var sa = OH.series(aId), sb = OH.series(bId);
    var host = document.getElementById('oh-corr-overlay');
    if (!host) { host = document.createElement('div'); host.id = 'oh-corr-overlay'; document.body.appendChild(host); }
    var body;
    if (sa && sb) {
      var n = Math.min(sa.length, sb.length);
      var r = pearson(sa.slice(-n), sb.slice(-n));
      if (r == null) {
        body = '<p class="oh-corr-msg">Недостаточно совпадающих дней для оценки связи.</p>';
      } else {
        var it = interpret(r);
        var c1 = global.OHCharts ? OHCharts.sparkline({ data: sa.slice(-n), width: 540, height: 84, color: '#5AA9E6', strokeWidth: 2, paddingY: 12 }) : '';
        var c2 = global.OHCharts ? OHCharts.sparkline({ data: sb.slice(-n), width: 540, height: 84, color: '#E667A0', strokeWidth: 2, paddingY: 12 }) : '';
        body =
          '<div class="oh-corr-r" style="color:' + (r >= 0 ? '#27C28A' : '#FF7A59') + '">r = ' + r.toFixed(2) + '</div>' +
          '<div class="oh-corr-int">' + it.strength + ' ' + it.dir + ' · ' + n + ' дн.</div>' +
          '<div class="oh-corr-series"><span class="oh-corr-leg" style="color:#5AA9E6">' + (a.label_ru || aId) + '</span>' + c1 + '</div>' +
          '<div class="oh-corr-series"><span class="oh-corr-leg" style="color:#E667A0">' + (b.label_ru || bId) + '</span>' + c2 + '</div>' +
          '<p class="oh-corr-note">Корреляция не доказывает причинность - это подсказка для проверки, не вывод.</p>';
      }
    } else {
      body = '<p class="oh-corr-msg">У одной из метрик пока нет истории для корреляции. Перетащи метрику с серией - например, тренд recovery или HRV, вес или VO2max.</p>';
    }
    host.innerHTML =
      '<div class="oh-corr-back"></div>' +
      '<div class="oh-corr-card" role="dialog" aria-modal="true">' +
        '<div class="oh-corr-head"><span>' + (a.label_ru || aId) + ' ↔ ' + (b.label_ru || bId) + '</span>' +
        '<button class="oh-corr-x" aria-label="Закрыть">✕</button></div>' + body + '</div>';
    host.style.display = 'block';
    function close() { host.style.display = 'none'; host.innerHTML = ''; }
    host.querySelector('.oh-corr-x').onclick = close;
    host.querySelector('.oh-corr-back').onclick = close;
  }

  var drag = null;
  // Touch: drag activates after a stationary long-press (350ms) so normal page
  // scroll keeps working; a swipe that moves before the hold simply scrolls.
  var TOUCH_HOLD_MS = 350;

  function cleanupDrag() {
    if (!drag) return;
    if (drag.ghost) drag.ghost.remove();
    drag.card.classList.remove('oh-drag-src');
    document.body.classList.remove('oh-dragging');
    document.querySelectorAll('.oh-drop-target').forEach(function (el) { el.classList.remove('oh-drop-target'); });
    drag = null;
  }

  function startDrag(e) {
    drag.started = true;
    var r = drag.card.getBoundingClientRect();
    var g = drag.card.cloneNode(true);
    g.classList.add('oh-drag-ghost');
    g.style.cssText += ';position:fixed;left:0;top:0;margin:0;width:' + r.width + 'px;height:' + r.height + 'px;pointer-events:none;z-index:100000;opacity:.92;';
    drag.offx = e.clientX - r.left; drag.offy = e.clientY - r.top; drag.ghost = g;
    document.body.appendChild(g);
    drag.card.classList.add('oh-drag-src');
    document.body.classList.add('oh-dragging');
  }

  function init() {
    if (init._bound) return; init._bound = true;
    ensureStyle();
    document.addEventListener('pointerdown', function (e) {
      // Second finger / non-left button must not hijack an active drag (a
      // replaced `drag` would leak its ghost element into the page forever).
      if (drag || !e.isPrimary || (e.pointerType === 'mouse' && e.button !== 0)) return;
      var card = e.target.closest && e.target.closest('[data-metric]');
      if (!card) return;
      if (e.target.closest('button,a,input,select,textarea,.oh-corr-card')) return;
      drag = { card: card, id: card.getAttribute('data-metric'), x: e.clientX, y: e.clientY, t0: e.timeStamp, touch: e.pointerType === 'touch', started: false, ghost: null, target: null };
    });
    document.addEventListener('pointermove', function (e) {
      if (!drag || !e.isPrimary) return;
      if (!drag.started) {
        var moved = Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y);
        if (drag.touch) {
          // Moving before the hold elapses = the user is scrolling, not dragging.
          if (e.timeStamp - drag.t0 < TOUCH_HOLD_MS) { if (moved >= 8) drag = null; return; }
          if (moved < 4) return;
        } else if (moved < 8) return;
        startDrag(e);
      }
      drag.ghost.style.transform = 'translate(' + (e.clientX - drag.offx) + 'px,' + (e.clientY - drag.offy) + 'px) rotate(1.5deg) scale(.98)';
      drag.ghost.style.display = 'none';
      var under = document.elementFromPoint(e.clientX, e.clientY);
      drag.ghost.style.display = '';
      var tgt = under && under.closest ? under.closest('[data-metric]') : null;
      document.querySelectorAll('.oh-drop-target').forEach(function (el) { el.classList.remove('oh-drop-target'); });
      drag.target = (tgt && tgt !== drag.card) ? tgt : null;
      if (drag.target) drag.target.classList.add('oh-drop-target');
    });
    // Once a drag is live, keep the page from scrolling under the finger.
    // touchmove is cancelable here because the finger was stationary through the
    // long-press, so the browser has not committed to a scroll yet.
    document.addEventListener('touchmove', function (e) {
      if (drag && drag.started) e.preventDefault();
    }, { passive: false });
    document.addEventListener('pointerup', function (e) {
      if (!drag || !e.isPrimary) return;
      var id = drag.id, target = drag.target, started = drag.started;
      cleanupDrag();
      if (started && target) overlay(id, target.getAttribute('data-metric'));
    });
    // The browser can cancel the pointer (scroll takeover, alert, tab switch):
    // without this cleanup the ghost card stays stuck on screen forever.
    document.addEventListener('pointercancel', cleanupDrag);
  }

  OH.correlate = { init: init, overlay: overlay };

  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})(typeof window !== 'undefined' ? window : this);
