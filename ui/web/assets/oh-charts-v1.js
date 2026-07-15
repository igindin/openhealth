/* oh-charts-v1.js - V1 (classic) chart skin.
 *
 * Registers OHCharts.skins.v1: line-series charts render as smooth AREA charts
 * with a gradient glow + thin stroke, matching V1's legacy "Производительность
 * сна" look (renderAreaChart). Loaded ONLY by the V1 dashboard; the engine
 * dispatches to it when OHCharts.setActiveSkin('v1') is active. Functions are
 * pure (return SVG strings) per the engine contract; the dashboard attaches the
 * draw-in animation + hover crosshair AFTER render (see attachOhV1ChartFx).
 *
 * Classes/markers the post-render layer relies on:
 *   svg.oh-v1-area[data-v1area]  - the chart wrapper
 *   path.oh-v1-area-line         - the stroked line (animated via drawPath)
 *   path.oh-v1-area-fill         - the gradient area (faded in)
 * Points geometry is exposed via data-pts on the svg (JSON) for the crosshair.
 */
(function (global) {
  'use strict';
  var OH = global.OHCharts;
  if (!OH || !OH.skins) return;

  function toNums(data) {
    return (data || []).map(function (x) {
      if (x && typeof x === 'object') return Number(x.value != null ? x.value : (x.h != null ? x.h : NaN));
      return Number(x);
    }).filter(function (x) { return !isNaN(x); });
  }

  function gradId(seed) {
    var s = String(seed), h = 0, i;
    for (i = 0; i < s.length; i++) { h = ((h << 5) - h + s.charCodeAt(i)) | 0; }
    return 'ohv1g' + Math.abs(h);
  }

  // Smooth area chart (cubic-bezier), V1 style. opts: color, width, height, paddingY.
  function areaChart(data, opts) {
    opts = opts || {};
    var arr = toNums(data);
    if (arr.length < 2) return OH.base.sparkline(Object.assign({ data: arr }, opts)); // fallback to base
    var w = opts.width || 600, h = opts.height || 120, pad = opts.paddingY || 16;
    var color = opts.color || '#60a5fa';
    var mn = Math.min.apply(null, arr), mx = Math.max.apply(null, arr), range = (mx - mn) || 1;
    var pts = arr.map(function (y, i) {
      var x = pad + (i / (arr.length - 1)) * (w - 2 * pad);
      var yc = h - pad - ((y - mn) / range) * (h - 2 * pad);
      return { x: Number(x.toFixed(1)), y: Number(yc.toFixed(1)) };
    });
    var d = 'M ' + pts[0].x + ' ' + pts[0].y, i;
    for (i = 0; i < pts.length - 1; i++) {
      var p0 = pts[i], p1 = pts[i + 1], cx = (p0.x + (p1.x - p0.x) / 2).toFixed(1);
      d += ' C ' + cx + ' ' + p0.y + ', ' + cx + ' ' + p1.y + ', ' + p1.x + ' ' + p1.y;
    }
    var fillD = d + ' L ' + pts[pts.length - 1].x + ' ' + h + ' L ' + pts[0].x + ' ' + h + ' Z';
    var gid = gradId(color + ':' + arr.length + ':' + arr[0] + ':' + arr[arr.length - 1]);
    var ptsAttr = JSON.stringify(pts).replace(/"/g, '&quot;');
    return '<svg class="oh-v1-area" viewBox="0 0 ' + w + ' ' + h + '" width="100%" height="' + h + '" preserveAspectRatio="none" data-v1area="1" data-pts="' + ptsAttr + '">' +
      '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="' + color + '" stop-opacity="0.16"/>' +
      '<stop offset="100%" stop-color="' + color + '" stop-opacity="0"/></linearGradient></defs>' +
      '<path class="oh-v1-area-fill" d="' + fillD + '" fill="url(#' + gid + ')" stroke="none"/>' +
      '<path class="oh-v1-area-line" d="' + d + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
      '</svg>';
  }

  // V1 overrides line-series charts with the area look. Bars/rings/zones/gauge
  // fall through to BASE (they already read skin tokens and look fine in V1).
  OH.skins.v1 = {
    sparkline: function (opts) { opts = opts || {}; return areaChart(opts.data || [], opts); }
  };
})(typeof window !== 'undefined' ? window : this);
