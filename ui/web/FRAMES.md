# FRAMES — motion spec for the OpenHealth dashboard

The animation contract for dashboard.html. Library: GSAP 3.12 (CDN, already wired in). All timings are in seconds. Any new animation is checked against this file rather than invented on the spot.

## Standards

| Pattern | Duration | Easing | Details |
|---|---|---|---|
| Zone card enter | 0.5-0.7 | power2.out | y 20→0 + fade, stagger 0.06-0.08 |
| Number count-up | 0.8 | power2.out | snap to the value's step (integers: 1, tenths: 0.1); large numbers in mono |
| Line draw | 1.2 | power1.inOut | stroke-dasharray/dashoffset = getTotalLength; the inline dash is cleared once it finishes |
| Bar grow | 0.6 | power3.out | scaleY 0→1 from the base (`transform-box: fill-box; transform-origin: 50% 100%`), stagger 0.05-0.08 |
| Ring (arc) | 1.2 | back.out(1.2) for values ≤90, otherwise power3.out (so the overshoot never flies past 100%) | the number's count-up runs in sync, same duration |
| Area fill under a line | 0.6 | power1.out | fade 0→1, delay 0.4 (after the draw starts) |
| Micro-feedback (checkbox, save) | 0.3 | power2.out | scale 0.97→1 / fade |
| Zone exit (go) | 0.25 | power2.in | fade + y 10 |

## Reduced motion

`prefers-reduced-motion: reduce` → `gsap.globalTimeline.timeScale(1000)` (the pattern is already in the file): every tween lands on its final frame instantly. CSS animations are silenced by a media block. Print CSS additionally forces lines and opacity to their end state via `!important`.

## Screens

- **«Сегодня» (Today)**: enter 0.7/0.08 → recovery ring 1.2 back.out(1.2) with the number counting up in sync (1.2); metric tiles count up over 0.8 with a 0.06 cascade; habit checklist fade + x(-8) 0.4, stagger 0.05.
- **«Пульс дня» (Day Pulse)**: heart-rate line draw 1.2; HR zones bar-grow on width 0.6, stagger 0.06.
- **Biomarkers**: segment-bar pills fade + drop(y -6) 0.45, stagger 0.05; detail panel expand 0.3 power2.out, the value inside the panel counts up over 0.8.
- **«Обзор показателей» (Vitals Overview)**: three rings 1.0 back.out(1.2), delays 0 / 0.15 / 0.3, values counting up in sync; sparklines line-draw 1.2 + area 0.6/delay 0.4.
- **«Тренды» (Trends)**: both charts line-draw 1.2, area fade 0.6 delay 0.4.
- **«Отчёты» (Reports)**: comparison deltas count up over 0.8; comparison bars bar-grow 0.6, stagger 0.08; recovery line draw 1.2 + day dots fade stagger 0.012; the thin HRV/RHR line draws over 1.2, with the mean segments and labels fading in over 0.4 at delay 0.9; heatmap cells scale 0.6→1 + fade 0.4, stagger 0.012.
- **«Пульс дня» (Day Pulse — wheel/emotions)**: circadian wheel arcs line-draw 1.0, stagger 0.05 (≤0.45 in total), the "ты здесь" (you are here) marker fade+scale 0.4 back.out(1.6) delay 0.9; the emotion check-in ring uses the standard Ring 1.2.
- **«Сегодня» (Today — breakdown)**: recovery contributions bar-grow scaleX 0.6 power3.out, stagger 0.08.
- **«Протоколы» (Protocols)**: the standard card enter, 0.5/0.08.
- **«Тренировки» (Workouts)**: strain bars on width 0.6 power3.out (a narrow progress bar — the width exception), rows fade + x(-8) 0.4/0.04.
- **Zone transition**: exit 0.25 power2.in → standard enter; the zone's render hooks are called from `onComplete` in `go()`.

## Prohibited

- No infinite animations, except the mascot's breathing (CSS) and loading spinners.
- Nothing longer than 1.5 s; total cascade delay ≤ 0.45.
- Do not animate layout properties (width/height/top) where a transform will do; the exception is narrow progress bars (width).
- Printing: every tween is force-completed before `window.print()` (`printReport()` plus a safety net in `@media print`).
