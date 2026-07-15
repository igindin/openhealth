/* oh-notify.js — local notifications for OpenHealth (self-contained, no deps).
 *
 * WHY: a health dashboard is only useful if it nudges at the right moment. This
 * module turns three local signals into gentle notifications — all computed on
 * the user's machine, nothing leaves the device:
 *   - recovery-ready : "your recovery for today is N%" (once per day)
 *   - checkin        : evening reminder to close the daily check-in
 *   - stale          : tracker data hasn't synced in a while
 *
 * Uses the Web Notifications API when the user opts in (works on http://localhost,
 * so it also fires as a native macOS notification inside the OpenHealth.app window).
 * When permission is absent it degrades to an in-app toast. Both skins load it.
 *
 * State (localStorage, never sent anywhere):
 *   openhealth.notify.enabled  '1' | '0'
 *   openhealth.notify.types    JSON {recovery,checkin,stale}
 *   openhealth.notify.sent.<type>.<YYYY-MM-DD>  dedupe marker (once per day)
 */
(function () {
  "use strict";
  if (window.OHNotify) return;

  var LS = {
    enabled: "openhealth.notify.enabled",
    types: "openhealth.notify.types",
    sent: "openhealth.notify.sent.",
  };
  var DEFAULT_TYPES = { recovery: true, checkin: true, stale: true };
  var ICON = "assets/icon.svg";

  // ---- small helpers ----
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function dayKey(d) {
    d = d || new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function supported() { return typeof window !== "undefined" && "Notification" in window; }
  function permission() { return supported() ? Notification.permission : "unsupported"; }
  function isEnabled() { return lsGet(LS.enabled) === "1"; }
  function types() {
    var t = {};
    try { t = JSON.parse(lsGet(LS.types) || "{}"); } catch (e) { t = {}; }
    return Object.assign({}, DEFAULT_TYPES, t);
  }
  function setType(name, on) {
    var t = types(); t[name] = !!on; lsSet(LS.types, JSON.stringify(t)); renderSettings();
  }

  // ---- recovery zone (matches dashboard col()/word(): green>=67, yellow 34-66, red<34) ----
  function zoneWord(v) {
    if (v >= 67) return "зелёная зона — организм готов к нагрузкам";
    if (v >= 34) return "жёлтая зона — умеренный режим";
    return "красная зона — нужен глубокий покой";
  }

  // ---- data access ----
  function getData() { return (window.DATA && typeof window.DATA === "object") ? window.DATA : null; }
  function staleDays(data) {
    var iso = data && data._meta && data._meta.generatedAt;
    if (!iso) return null;
    var t = Date.parse(iso);
    if (isNaN(t)) return null;
    return Math.floor((Date.now() - t) / 86400000);
  }
  function checkinDone() {
    var list = [];
    try { list = JSON.parse(lsGet("openhealth.habits.list") || "[]"); } catch (e) { list = []; }
    if (!Array.isArray(list) || !list.length) return true; // nothing to nudge about
    var marks = {};
    try { marks = JSON.parse(lsGet("openhealth.habits." + dayKey()) || "{}"); } catch (e) { marks = {}; }
    return list.every(function (id) { return marks && marks[id]; });
  }

  // ---- toast (in-app fallback + confirmations) ----
  function ensureStyles() {
    if (document.getElementById("oh-notify-styles")) return;
    var css = document.createElement("style");
    css.id = "oh-notify-styles";
    css.textContent = [
      "#oh-toast-wrap{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:99999;display:flex;flex-direction:column;gap:8px;align-items:center;pointer-events:none}",
      ".oh-toast{pointer-events:auto;max-width:380px;background:var(--bg-card,#1a1d21);color:var(--text-primary,#f4f4f5);border:1px solid var(--line,rgba(255,255,255,.1));border-radius:12px;padding:12px 16px;font:500 13px/1.4 var(--font-body,system-ui,sans-serif);box-shadow:0 8px 30px rgba(0,0,0,.35);display:flex;gap:10px;align-items:flex-start;opacity:0;transform:translateY(8px);transition:opacity .25s,transform .25s}",
      ".oh-toast.in{opacity:1;transform:translateY(0)}",
      ".oh-toast b{color:var(--text-primary,#fff);font-weight:650;display:block;margin-bottom:2px}",
      ".oh-toast .oh-toast-dot{width:8px;height:8px;border-radius:50%;margin-top:5px;flex:0 0 auto;background:var(--accent,#34d399)}",
      ".oh-notify-switch{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:12px 0;border-top:1px solid var(--line,rgba(255,255,255,.08))}",
      ".oh-notify-switch:first-of-type{border-top:0}",
      ".oh-notify-switch .lbl{font-weight:600}",
      ".oh-notify-switch .sub2{font-size:12px;color:var(--text-muted,#9ca3af);margin-top:2px}",
      ".oh-sw{position:relative;width:42px;height:24px;flex:0 0 auto;border-radius:999px;background:var(--line,rgba(255,255,255,.18));cursor:pointer;transition:background .2s;border:0;padding:0}",
      ".oh-sw::after{content:'';position:absolute;top:2px;left:2px;width:20px;height:20px;border-radius:50%;background:#fff;transition:transform .2s}",
      ".oh-sw[aria-checked=true]{background:var(--accent,#34d399)}",
      ".oh-sw[aria-checked=true]::after{transform:translateX(18px)}",
      ".oh-sw:disabled{opacity:.4;cursor:not-allowed}",
    ].join("\n");
    document.head.appendChild(css);
  }
  function toast(title, body) {
    ensureStyles();
    var wrap = document.getElementById("oh-toast-wrap");
    if (!wrap) { wrap = document.createElement("div"); wrap.id = "oh-toast-wrap"; document.body.appendChild(wrap); }
    var el = document.createElement("div");
    el.className = "oh-toast";
    el.innerHTML = '<span class="oh-toast-dot"></span><span>' +
      (body ? "<b>" + esc(title) + "</b>" + esc(body) : esc(title)) + "</span>";
    wrap.appendChild(el);
    void el.offsetHeight; // force reflow so the enter transition triggers without relying on rAF
    el.classList.add("in");
    setTimeout(function () {
      el.classList.remove("in");
      setTimeout(function () { el.remove(); }, 300);
    }, 5200);
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ---- fire one notification (once per day per type) ----
  function fire(type, title, body, force) {
    var key = LS.sent + type + "." + dayKey();
    if (!force && lsGet(key)) return false;
    var shown = false;
    if (isEnabled() && permission() === "granted") {
      try {
        new Notification(title, { body: body, icon: ICON, tag: "oh-" + type, silent: false });
        shown = true;
      } catch (e) { shown = false; }
    }
    if (!shown) toast(title, body); // in-app fallback (or when not opted in)
    if (!force) lsSet(key, "1");
    return true;
  }

  // ---- evaluate triggers against current data ----
  function check(data) {
    data = data || getData();
    if (!data) return;
    var t = types();
    if (t.recovery && typeof data.recovery === "number" && isFinite(data.recovery)) {
      fire("recovery", "Recovery " + data.recovery + "%", zoneWord(data.recovery));
    }
    if (t.stale) {
      var age = staleDays(data);
      if (age != null && age >= 2) {
        fire("stale", "Данные устарели", "Последняя синхронизация " + age + " дн. назад — обнови трекер.");
      }
    }
    if (t.checkin && new Date().getHours() >= 19 && !checkinDone()) {
      fire("checkin", "Закрой день", "Отметь чек-ин: что удалось сегодня.");
    }
  }

  // ---- opt-in flow ----
  function enable() {
    if (!supported()) { toast("Уведомления не поддерживаются", "Браузер без Web Notifications."); return Promise.resolve(false); }
    var p = Notification.permission;
    var ask = (p === "default") ? Notification.requestPermission() : Promise.resolve(p);
    return Promise.resolve(ask).then(function (res) {
      if (res === "granted") {
        lsSet(LS.enabled, "1");
        toast("Уведомления включены", "OpenHealth будет подсказывать по recovery, чек-ину и синку.");
        renderSettings();
        setTimeout(function () { check(); }, 400);
        return true;
      }
      lsSet(LS.enabled, "0");
      toast("Разрешение не выдано", "Включи уведомления для сайта в настройках браузера.");
      renderSettings();
      return false;
    });
  }
  function disable() { lsSet(LS.enabled, "0"); toast("Уведомления выключены"); renderSettings(); }
  function toggle() { return isEnabled() ? (disable(), Promise.resolve(false)) : enable(); }
  function testFire() { fire("recovery", "Тест уведомления", "Так будет выглядеть напоминание OpenHealth.", true); }

  // ---- settings card (self-injected into the Settings screen, matches native markup) ----
  function permLabel() {
    if (!supported()) return "не поддерживается браузером";
    if (permission() === "denied") return "заблокировано в браузере";
    if (permission() === "granted") return isEnabled() ? "включены" : "разрешены, но выключены";
    return "не запрошены";
  }
  function buildCard() {
    var t = types();
    var master = isEnabled() && permission() === "granted";
    function row(name, label, sub) {
      return '<div class="oh-notify-switch"><div><div class="lbl">' + label + '</div><div class="sub2">' + sub + '</div></div>' +
        '<button type="button" class="oh-sw" role="switch" aria-checked="' + (t[name] ? "true" : "false") + '"' +
        (master ? "" : " disabled") + ' data-notify-type="' + name + '"></button></div>';
    }
    return '' +
      '<div class="card-header"><div class="card-title-group"><h3>Уведомления</h3>' +
      '<div class="sub">локальные напоминания · статус: ' + permLabel() + '</div></div>' +
      '<i class="ph-light ph-bell card-header-icon"></i></div>' +
      '<div class="oh-notify-switch"><div><div class="lbl">Включить уведомления</div>' +
      '<div class="sub2">запросит разрешение браузера · работает и в приложении OpenHealth</div></div>' +
      '<button type="button" class="oh-sw" role="switch" aria-checked="' + (master ? "true" : "false") + '" data-notify-master></button></div>' +
      row("recovery", "Recovery за день", "утренний показатель восстановления, раз в день") +
      row("checkin", "Напоминание о чек-ине", "вечером, если день ещё не отмечен") +
      row("stale", "Устаревшие данные", "если трекер не синкался 2+ дня") +
      '<div style="display:flex;justify-content:flex-end;margin-top:14px;">' +
      '<button class="btn-secondary" type="button" data-notify-test><i class="ph-light ph-bell-ringing"></i> Тест</button></div>' +
      '<p class="privacy-note"><i class="ph-light ph-lock-simple"></i><span>Всё считается локально; тексты уведомлений никуда не отправляются (<span class="mono">openhealth.notify.*</span>).</span></p>';
  }
  function wireCard(inner) {
    inner.addEventListener("click", function (e) {
      var m = e.target.closest("[data-notify-master]");
      if (m) { toggle(); return; }
      var test = e.target.closest("[data-notify-test]");
      if (test) { testFire(); return; }
      var sw = e.target.closest("[data-notify-type]");
      if (sw && !sw.disabled) { var n = sw.getAttribute("data-notify-type"); setType(n, sw.getAttribute("aria-checked") !== "true"); }
    });
  }
  function renderSettings() {
    var host = document.getElementById("oh-notify-card-inner");
    if (host) host.innerHTML = buildCard();
  }
  function injectSettings() {
    if (document.getElementById("oh-notify-sec")) return true;
    ensureStyles();
    var anchor = document.querySelector(".settings-sec");
    if (!anchor || !anchor.parentNode) return false;
    var sec = document.createElement("div");
    sec.className = "settings-sec";
    sec.id = "oh-notify-sec";
    sec.innerHTML = '<h2 class="settings-sec-head">Уведомления</h2>' +
      '<div class="card-outer"><div class="card-inner" id="oh-notify-card-inner"></div></div>';
    // place after the "Оформление" section when present, else after the first section
    var secs = document.querySelectorAll(".settings-sec");
    var after = secs[secs.length - 1];
    after.parentNode.insertBefore(sec, after.nextSibling);
    var inner = sec.querySelector("#oh-notify-card-inner");
    inner.innerHTML = buildCard();
    wireCard(inner);
    return true;
  }

  // ---- boot ----
  function boot() {
    ensureStyles();
    injectSettings();
    // Re-inject if the settings screen is (re)built later.
    try {
      var mo = new MutationObserver(function () {
        if (!document.getElementById("oh-notify-sec") && document.querySelector(".settings-sec")) injectSettings();
      });
      mo.observe(document.body, { childList: true, subtree: true });
    } catch (e) {}
    // Evaluate triggers shortly after data is likely ready, then hourly.
    setTimeout(function () { check(); }, 2500);
    setInterval(function () { check(); }, 60 * 60 * 1000);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();

  window.OHNotify = {
    supported: supported, permission: permission, isEnabled: isEnabled, types: types,
    setType: setType, enable: enable, disable: disable, toggle: toggle,
    check: check, fire: fire, toast: toast, test: testFire, renderSettings: renderSettings,
    injectSettings: injectSettings,
  };
})();
