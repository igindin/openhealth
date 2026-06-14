/* oh-charts.js - shared SVG chart kit for OpenHealth skins.
 *
 * Result 1 scope: ring + sparkline. Pure functions: take data + options, return
 * an SVG string. Colors come from the caller (skin tokens), so the SAME chart
 * renders identically in any skin/theme. Both V1 and V2 call these - never their
 * own copy. New chart types (week bars, hypnogram, HR zones, gauge) land here in
 * Result 2 and become available to both skins at once.
 */
(function (global) {
  'use strict';

  // Recovery / strain ring with centered label. opts:
  //   percent (0-100), size, stroke, color, trackColor, label, sub, labelColor
  function ring(opts) {
    opts = opts || {};
    var pct = Math.max(0, Math.min(100, Number(opts.percent) || 0));
    var size = opts.size || 160;
    var stroke = opts.stroke || 12;
    var r = (size - stroke) / 2;
    var cx = size / 2, cy = size / 2;
    var circ = 2 * Math.PI * r;
    var off = circ - (circ * pct / 100);
    var track = opts.trackColor || 'rgba(127,127,127,0.18)';
    var color = opts.color || 'currentColor';
    var label = opts.label != null ? opts.label : (Math.round(pct) + '%');
    var labelColor = opts.labelColor || 'currentColor';
    var sub = opts.sub || '';
    return '' +
      '<svg class="oh-ring" viewBox="0 0 ' + size + ' ' + size + '" width="' + size + '" height="' + size + '" role="img" aria-label="' + (opts.aria || label) + '">' +
        '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="' + track + '" stroke-width="' + stroke + '"/>' +
        '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="' + color + '" stroke-width="' + stroke + '" ' +
          'stroke-linecap="round" stroke-dasharray="' + circ.toFixed(2) + '" stroke-dashoffset="' + off.toFixed(2) + '" ' +
          'transform="rotate(-90 ' + cx + ' ' + cy + ')"/>' +
        '<text x="50%" y="48%" text-anchor="middle" dominant-baseline="middle" fill="' + labelColor + '" ' +
          'font-size="' + (size * 0.26).toFixed(1) + '" font-weight="700">' + label + '</text>' +
        (sub ? '<text x="50%" y="64%" text-anchor="middle" dominant-baseline="middle" fill="' + labelColor + '" ' +
          'font-size="' + (size * 0.085).toFixed(1) + '" opacity="0.6" letter-spacing="1.5">' + sub + '</text>' : '') +
      '</svg>';
  }

  // Smooth sparkline. opts: data (number[]), width, height, color, fill, strokeWidth
  function sparkline(opts) {
    opts = opts || {};
    var data = (opts.data || []).map(Number).filter(function (v) { return !isNaN(v); });
    var w = opts.width || 800, h = opts.height || 120;
    var px = opts.paddingX != null ? opts.paddingX : 10;
    var py = opts.paddingY != null ? opts.paddingY : 20;
    var color = opts.color || 'currentColor';
    var fill = opts.fill || 'none';
    if (data.length < 2) {
      return '<svg class="oh-sparkline" viewBox="0 0 ' + w + ' ' + h + '" width="100%" height="' + h + '" preserveAspectRatio="none"></svg>';
    }
    var min = Math.min.apply(null, data) - 5;
    var max = Math.max.apply(null, data) + 5;
    var range = (max - min) || 1;
    var stepX = (w - px * 2) / (data.length - 1);
    var pts = data.map(function (val, i) {
      return { x: px + i * stepX, y: h - py - ((val - min) / range) * (h - py * 2) };
    });
    var d = 'M ' + pts[0].x.toFixed(1) + ' ' + pts[0].y.toFixed(1);
    for (var i = 1; i < pts.length; i++) {
      var cp1x = pts[i - 1].x + stepX / 2, cp1y = pts[i - 1].y;
      var cp2x = pts[i].x - stepX / 2, cp2y = pts[i].y;
      d += ' C ' + cp1x.toFixed(1) + ' ' + cp1y.toFixed(1) + ', ' + cp2x.toFixed(1) + ' ' + cp2y.toFixed(1) +
        ', ' + pts[i].x.toFixed(1) + ' ' + pts[i].y.toFixed(1);
    }
    var area = (fill !== 'none')
      ? '<path d="' + d + ' L ' + pts[pts.length - 1].x.toFixed(1) + ' ' + h + ' L ' + pts[0].x.toFixed(1) + ' ' + h + ' Z" fill="' + fill + '" stroke="none"/>'
      : '';
    return '' +
      '<svg class="oh-sparkline" viewBox="0 0 ' + w + ' ' + h + '" width="100%" height="' + h + '" preserveAspectRatio="none" role="img">' +
        area +
        '<path d="' + d + '" fill="none" stroke="' + color + '" stroke-width="' + (opts.strokeWidth || 2.5) + '" stroke-linecap="round" stroke-linejoin="round"/>' +
      '</svg>';
  }

  // ---- Result 2 charts (WHOOP-grade kit) -------------------------------------

  // Vertical week bars (WHOOP recovery week). data: [{label, value, highlight}].
  // opts: max (def 100), color, highlightColor, height, unit, labelColor, fmt
  function weekBars(data, opts) {
    opts = opts || {}; data = data || [];
    var w = opts.width || 360, h = opts.height || 180, padBottom = 26, padX = 8;
    var vals = data.map(function (d) { return Number(d.value) || 0; });
    var max = opts.max || Math.max.apply(null, vals.concat([1]));
    var color = opts.color || 'currentColor', hi = opts.highlightColor || color;
    var lc = opts.labelColor || 'currentColor';
    var n = data.length || 1, slot = (w - padX * 2) / n, bw = Math.min(opts.barWidth || 22, slot * 0.62);
    var plotH = h - 26 - padBottom;
    var bars = data.map(function (d, i) {
      var v = Number(d.value) || 0, bh = Math.max(2, (v / max) * plotH);
      var x = padX + slot * i + (slot - bw) / 2, y = h - padBottom - bh, c = d.highlight ? hi : color;
      return '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + bh.toFixed(1) + '" rx="4" fill="' + c + '" opacity="' + (d.highlight ? 1 : 0.5) + '"/>' +
        '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (y - 6).toFixed(1) + '" text-anchor="middle" font-size="12" font-weight="600" fill="' + lc + '">' + (opts.fmt ? opts.fmt(v) : v) + (opts.unit || '') + '</text>' +
        '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (h - 8).toFixed(1) + '" text-anchor="middle" font-size="10" opacity="0.55" fill="' + lc + '">' + (d.label || '') + '</text>';
    }).join('');
    return '<svg class="oh-week-bars" viewBox="0 0 ' + w + ' ' + h + '" width="100%" height="' + h + '" role="img">' + bars + '</svg>';
  }

  // Line with dot markers + value labels (WHOOP RHR/HRV week). data: [{label,value}].
  function lineDots(data, opts) {
    opts = opts || {}; data = data || [];
    var w = opts.width || 360, h = opts.height || 170, padTop = 24, padBottom = 26, padX = 18;
    var vals = data.map(function (d) { return Number(d.value) || 0; });
    var min = Math.min.apply(null, vals.concat([Infinity])), max = Math.max.apply(null, vals.concat([-Infinity]));
    if (!isFinite(min) || !isFinite(max)) { min = 0; max = 1; }
    var pad = (max - min) * 0.25 || 1; min -= pad; max += pad; var range = (max - min) || 1;
    var color = opts.color || 'currentColor', lc = opts.labelColor || 'currentColor';
    var n = data.length || 1, stepX = n > 1 ? (w - padX * 2) / (n - 1) : 0, plotH = h - padTop - padBottom;
    var pts = data.map(function (d, i) { return { x: padX + stepX * i, y: padTop + plotH - ((Number(d.value) - min) / range) * plotH, v: Number(d.value), label: d.label }; });
    var line = pts.length ? ('M ' + pts.map(function (p) { return p.x.toFixed(1) + ' ' + p.y.toFixed(1); }).join(' L ')) : '';
    var dots = pts.map(function (p) {
      return '<circle cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="4" fill="' + (opts.bg || '#fff') + '" stroke="' + color + '" stroke-width="2.5"/>' +
        '<text x="' + p.x.toFixed(1) + '" y="' + (p.y - 10).toFixed(1) + '" text-anchor="middle" font-size="11" font-weight="600" fill="' + lc + '">' + p.v + '</text>' +
        '<text x="' + p.x.toFixed(1) + '" y="' + (h - 8).toFixed(1) + '" text-anchor="middle" font-size="10" opacity="0.55" fill="' + lc + '">' + (p.label || '') + '</text>';
    }).join('');
    return '<svg class="oh-line-dots" viewBox="0 0 ' + w + ' ' + h + '" width="100%" height="' + h + '" role="img">' +
      '<path d="' + line + '" fill="none" stroke="' + color + '" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" opacity="0.85"/>' + dots + '</svg>';
  }

  // Hypnogram: dense noisy night HR line with soft area. data: number[].
  function hypnogram(data, opts) {
    opts = opts || {};
    return sparkline({ data: data, width: opts.width || 700, height: opts.height || 130, color: opts.color || 'currentColor', fill: opts.fill || 'rgba(124,139,255,0.12)', strokeWidth: opts.strokeWidth || 1.4, paddingX: 6, paddingY: 14 }).replace('oh-sparkline', 'oh-hypnogram');
  }

  // Sleep-stage horizontal bars. data: [{stage,pct,duration,color,typicalLow,typicalHigh}].
  function sleepStages(data, opts) {
    opts = opts || {}; data = data || [];
    var w = opts.width || 360, rowH = opts.rowH || 40, gap = 10, barX = 150, barW = w - barX - 64;
    var h = data.length * (rowH + gap), lc = opts.labelColor || 'currentColor';
    var rows = data.map(function (d, i) {
      var y = i * (rowH + gap), pct = Math.max(0, Math.min(100, Number(d.pct) || 0)), c = d.color || opts.color || 'currentColor', fillW = (pct / 100) * barW, typical = '';
      if (d.typicalLow != null && d.typicalHigh != null) {
        var tx = barX + (d.typicalLow / 100) * barW, tw = ((d.typicalHigh - d.typicalLow) / 100) * barW;
        typical = '<rect x="' + tx.toFixed(1) + '" y="' + (y + 6) + '" width="' + tw.toFixed(1) + '" height="' + (rowH - 12) + '" fill="none" stroke="' + c + '" stroke-dasharray="3 3" opacity="0.5" rx="3"/>';
      }
      return '<text x="0" y="' + (y + rowH / 2 + 1) + '" font-size="12" font-weight="600" dominant-baseline="middle" fill="' + lc + '">' + (d.stage || '') + '</text>' +
        '<text x="' + (barX - 10) + '" y="' + (y + rowH / 2 + 1) + '" font-size="11" text-anchor="end" dominant-baseline="middle" opacity="0.6" fill="' + lc + '">' + pct + '%</text>' +
        '<rect x="' + barX + '" y="' + (y + 4) + '" width="' + barW + '" height="' + (rowH - 8) + '" rx="6" fill="' + c + '" opacity="0.14"/>' +
        '<rect x="' + barX + '" y="' + (y + 4) + '" width="' + fillW.toFixed(1) + '" height="' + (rowH - 8) + '" rx="6" fill="' + c + '"/>' + typical +
        '<text x="' + w + '" y="' + (y + rowH / 2 + 1) + '" font-size="12" text-anchor="end" dominant-baseline="middle" font-weight="600" fill="' + lc + '">' + (d.duration || '') + '</text>';
    }).join('');
    return '<svg class="oh-sleep-stages" viewBox="0 0 ' + w + ' ' + h + '" width="100%" height="' + h + '" role="img">' + rows + '</svg>';
  }

  // Hours vs need dual line. data: { labels:[], hours:[], need:[] }.
  function hoursVsNeed(data, opts) {
    opts = opts || {}; data = data || {};
    var w = opts.width || 360, h = opts.height || 170, padTop = 22, padBottom = 26, padX = 18;
    var hours = (data.hours || []).map(Number), need = (data.need || []).map(Number), labels = data.labels || [];
    var all = hours.concat(need); if (!all.length) all = [0, 1];
    var min = Math.min.apply(null, all) - 0.5, max = Math.max.apply(null, all) + 0.5, range = (max - min) || 1;
    var n = Math.max(hours.length, need.length, 1), stepX = n > 1 ? (w - padX * 2) / (n - 1) : 0, plotH = h - padTop - padBottom;
    var lc = opts.labelColor || 'currentColor';
    function mkPath(arr) { return arr.length ? 'M ' + arr.map(function (v, i) { return (padX + stepX * i).toFixed(1) + ' ' + (padTop + plotH - ((v - min) / range) * plotH).toFixed(1); }).join(' L ') : ''; }
    function mkDots(arr, color) { return arr.map(function (v, i) { var x = padX + stepX * i, y = padTop + plotH - ((v - min) / range) * plotH; return '<circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="3.5" fill="' + (opts.bg || '#fff') + '" stroke="' + color + '" stroke-width="2"/>'; }).join(''); }
    var cH = opts.colorHours || 'currentColor', cN = opts.colorNeed || 'currentColor';
    var xl = labels.map(function (l, i) { return '<text x="' + (padX + stepX * i).toFixed(1) + '" y="' + (h - 8) + '" text-anchor="middle" font-size="10" opacity="0.55" fill="' + lc + '">' + l + '</text>'; }).join('');
    return '<svg class="oh-hours-need" viewBox="0 0 ' + w + ' ' + h + '" width="100%" height="' + h + '" role="img">' +
      '<path d="' + mkPath(need) + '" fill="none" stroke="' + cN + '" stroke-width="2" stroke-dasharray="4 4" opacity="0.7"/>' +
      '<path d="' + mkPath(hours) + '" fill="none" stroke="' + cH + '" stroke-width="2.5"/>' + mkDots(need, cN) + mkDots(hours, cH) + xl + '</svg>';
  }

  // HR zones horizontal bars. data: [{zone,value,color}]. value = minutes (or any).
  function hrZones(data, opts) {
    opts = opts || {}; data = data || [];
    var w = opts.width || 360, rowH = opts.rowH || 30, gap = 8, barX = 84, barW = w - barX - 56;
    var h = data.length * (rowH + gap), lc = opts.labelColor || 'currentColor';
    var max = Math.max.apply(null, data.map(function (d) { return Number(d.value) || 0; }).concat([1]));
    var rows = data.map(function (d, i) {
      var y = i * (rowH + gap), v = Number(d.value) || 0, fw = (v / max) * barW, c = d.color || opts.color || 'currentColor';
      return '<text x="0" y="' + (y + rowH / 2 + 1) + '" font-size="11" font-weight="600" dominant-baseline="middle" fill="' + lc + '">' + (d.zone || '') + '</text>' +
        '<rect x="' + barX + '" y="' + (y + 3) + '" width="' + barW + '" height="' + (rowH - 6) + '" rx="5" fill="' + c + '" opacity="0.14"/>' +
        '<rect x="' + barX + '" y="' + (y + 3) + '" width="' + Math.max(2, fw).toFixed(1) + '" height="' + (rowH - 6) + '" rx="5" fill="' + c + '"/>' +
        '<text x="' + w + '" y="' + (y + rowH / 2 + 1) + '" font-size="11" text-anchor="end" dominant-baseline="middle" opacity="0.8" fill="' + lc + '">' + (opts.fmt ? opts.fmt(v) : v) + (opts.unit || '') + '</text>';
    }).join('');
    return '<svg class="oh-hr-zones" viewBox="0 0 ' + w + ' ' + h + '" width="100%" height="' + h + '" role="img">' + rows + '</svg>';
  }

  // Semicircular gauge (WHOOP stress 0-3). data: number|{value}. opts: max, zones[], colors, label, sub.
  function gauge(data, opts) {
    opts = opts || {};
    var val = Number(data && data.value != null ? data.value : data) || 0;
    var max = opts.max || 3;
    var w = opts.width || 220, h = opts.height || 140, cx = w / 2, cy = h - 20, r = Math.min(w / 2 - 16, h - 36), stroke = opts.stroke || 14;
    var lc = opts.labelColor || 'currentColor';
    function pt(frac) { var a = Math.PI * (1 - frac); return { x: cx + r * Math.cos(a), y: cy - r * Math.sin(a) }; }
    function arc(f0, f1, color, op) {
      if (f1 <= f0) return '';
      var a = pt(f0), b = pt(f1), large = (f1 - f0) > 0.5 ? 1 : 0;
      return '<path d="M ' + a.x.toFixed(1) + ' ' + a.y.toFixed(1) + ' A ' + r + ' ' + r + ' 0 ' + large + ' 1 ' + b.x.toFixed(1) + ' ' + b.y.toFixed(1) + '" fill="none" stroke="' + color + '" stroke-width="' + stroke + '" stroke-linecap="round" opacity="' + (op == null ? 1 : op) + '"/>';
    }
    var track = arc(0, 1, opts.trackColor || 'rgba(127,127,127,0.18)');
    var zones = opts.zones || [{ to: 1 / 3, color: '#7CC8FF' }, { to: 2 / 3, color: '#6FE3A5' }, { to: 1, color: '#FFB020' }];
    var frac = Math.max(0, Math.min(1, val / max)), zoneArcs = '', prev = 0;
    for (var i = 0; i < zones.length; i++) { var to = Math.min(frac, zones[i].to); zoneArcs += arc(prev, to, zones[i].color); prev = zones[i].to; if (zones[i].to >= frac) break; }
    var needle = pt(frac), label = opts.label != null ? opts.label : val.toFixed(1);
    return '<svg class="oh-gauge" viewBox="0 0 ' + w + ' ' + h + '" width="100%" height="' + h + '" role="img">' + track + zoneArcs +
      '<circle cx="' + needle.x.toFixed(1) + '" cy="' + needle.y.toFixed(1) + '" r="' + (stroke / 2 + 3) + '" fill="' + (opts.bg || '#fff') + '" stroke="' + (opts.markerColor || '#131416') + '" stroke-width="2"/>' +
      '<text x="' + cx + '" y="' + (cy - 8) + '" text-anchor="middle" font-size="' + (r * 0.42).toFixed(1) + '" font-weight="700" fill="' + lc + '">' + label + '</text>' +
      (opts.sub ? '<text x="' + cx + '" y="' + (cy + 12) + '" text-anchor="middle" font-size="11" letter-spacing="1.5" opacity="0.6" fill="' + lc + '">' + opts.sub + '</text>' : '') + '</svg>';
  }

  var OHCharts = { ring: ring, sparkline: sparkline, weekBars: weekBars, lineDots: lineDots, hypnogram: hypnogram, sleepStages: sleepStages, hoursVsNeed: hoursVsNeed, hrZones: hrZones, gauge: gauge };
  if (typeof module !== 'undefined' && module.exports) module.exports = OHCharts;
  global.OHCharts = OHCharts;
})(typeof window !== 'undefined' ? window : this);
