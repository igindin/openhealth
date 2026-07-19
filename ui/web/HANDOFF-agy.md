# Antigravity handoff — OpenHealth premium web dashboard

TASK: rewrite `/Users/ilya/Projects/openhealth/ui/web/dashboard.html` (plus the copy in `index.html`) into a PREMIUM OpenHealth web dashboard. The current version is an under-baked Linear clone; it needs premium detailing. Single-file HTML/CSS/JS, runs locally (`python3 -m http.server`), renders demo DATA (the structure), bridges to `openhealth show-summary`.

## Style (primary, not "a theme")
Dark premium in the spirit of Flenteey / an activity dashboard. PNG benchmarks live in `./refs/`:
- `cosmos-activity-dark.img`, `cosmos-then-tracker.img` — dark accent cards, bars, state checklists, golden time. THE main vibe.
- `cosmos-healthcare-bento.img` — colored bento blocks with large numbers.
- `ultrahuman-dark.img` + WHOOP — the GOLD STANDARD for charts: recovery rings, strain bars, trends, green/yellow/red zones. Take the pixel-perfect display logic from here.
- `linear-dashboard.img`, `linear-issues-sidebar.img` — sidebar navigation, density, command palette.
- `substack-checklist.img` — a getting-started checklist with progress → the pattern for the "what you did today" checkboxes.
- `asana-dashboard.img`, `whop-today.img` — dashboard widgets, today stats.

## Navigation (sidebar, fitted to the openhealth modules) — NO Ask/chat
Groups:
- «Сегодня» (Today): **Overview / «Сегодня» (Today)**, **«Пульс дня» (Day Pulse)**.
- «Данные» (Data): **Biomarkers («Анализы» / Labs)**, **WHOOP** (Overview + Correlations), **«Журнал» (Journal)**, **«Тренды» (Trends)**, Timeline, «Состав тела» (Body Composition), «Тренировки» (Workouts).
- Knowledge: Protocols, Research.
- System: «Отчёты» (Reports), «Дайджесты» (Digests), **«Синхронизация» (Sync)**.
REMOVE Ask/chat entirely (it does not work — it comes out).

## Screens (in priority order)
1. **«Сегодня» (Today):** recovery ring (WHOOP logic, color coding) + day stats + **"WHAT YOU DID TODAY" CHECKBOXES** (like the Substack getting-started list: go to bed earlier / morning light / a walk / water — you tick them off). Focus on action, NOT a calendar. Doctor Context (tone set by recovery). Day readiness.
2. **Biomarkers / «Анализы» (Labs):** values with BOTH reference and optimal ranges (C1-C5), change over time, what to raise with your doctor. If there is NO data → an empty state with a CTA: upload a lab export / connect a source.
3. **WHOOP Overview:** recovery/sleep/strain charts, pixel-perfect against WHOOP.
4. **WHOOP Correlations:** what drives recovery (journal ↔ recovery, personal baseline, C-grade).
5. **«Журнал» (Journal):** the user CHOOSES what they want to answer (custom behaviors from a set), then a light daily check-in.
6. **«Тренды» (Trends), «Синхронизация» (Sync)** (connector status: connected or not → a CTA to upload).

## Connection checks
The frontend checks what is connected (WHOOP/Apple/Oura/Garmin/labs/DNA). Nothing there → empty state + a CTA to upload or connect. Never show an empty chart in silence.

## Premium requirements
- Fonts: Geist + Geist Mono (mono for every number and metric), a carefully tuned typographic hierarchy.
- Animation: GSAP — ring fill, card stagger, soft zone transitions, hover. Respect `prefers-reduced-motion`.
- FLAWLESS layout at every resolution: mobile (sidebar → drawer), tablet, desktop. Check the breakpoints.
- Themes: dark as the primary (Flenteey), plus light/brutalist/bauhaus behind a toggle (kazimir/mihaly NOT needed).
- Anti-slop: NOT beige, NOT Inter-by-default, NOT a purple gradient, NOT the default 3-column grid. Every element calibrated against the benchmarks above.

OUTPUT: overwrite `dashboard.html` and `index.html` in this folder. It must open locally and look premium on every screen. Preserve the demo DATA structure and the footer hint pointing at `openhealth show-summary`.
