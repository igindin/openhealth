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

  var OHCharts = { ring: ring, sparkline: sparkline };
  if (typeof module !== 'undefined' && module.exports) module.exports = OHCharts;
  global.OHCharts = OHCharts;
})(typeof window !== 'undefined' ? window : this);
