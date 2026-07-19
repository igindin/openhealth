# OpenHealth Web — premium interface specification

> Final brief from Ilya (2026-06-09). Premium design, premium fonts, premium animations.
> Every element professionally calibrated against UX references from top products.

## Primary style (NOT a theme toggle — the main variant)
- **Style benchmark:** a dark dashboard in the spirit of Flenteey (May 2023 task dashboard) - excellent, dense, cards with accent colors, statistics. Treat it as a FULL alternative / the main variant (v2), not as one theme among several.
- **Gold standard for charts:** WHOOP - pixel-perfect chart regions and display logic (recovery/strain/sleep rings, trends, zones).
- **References:** mobbin (UX of top health/admin products) + cosmos search (dashboard style, by aesthetic) + dribbble. Capture as PNG → pixel-perfect benchmark.
- Premium: Geist + Geist Mono, GSAP animations (timeline/stagger/reveal, prefers-reduced-motion), flawless layout at EVERY resolution (via /frontend-design + /frontend-developer).
- Anti-slop: no beige, no Inter-by-default, no purple gradient, no default 3-column grid.

## Navigation (fitted to our feature set and vision, modeled on Ilya's real health-os)
Sidebar, two groups. Adapted to the openhealth modules:
- **«Сегодня» (Today):** Overview / «Сегодня» (Today) (recovery + day stats + action checkboxes), «Пульс дня» (Day Pulse).
- **«Данные» (Data):** Biomarkers («Анализы» / Labs), WHOOP (Overview + Correlations), Timeline, «Журнал» (Journal, the daily diary), «Состав тела» (Body Composition), «Тренировки» (Workouts), «Тренды» (Trends, 30 days).
- **Knowledge / action:** Protocols, Research, Vaccination, Cascade.
- **System:** knowledge base, «Отчёты» (Reports), «Дайджесты» (Digests, scheduler), «Синхронизация» (Sync — connector status), Profiles.
(Not all at once — priority order: «Сегодня» (Today), «Пульс дня» (Day Pulse), Biomarkers, WHOOP overview/correlations, «Журнал» (Journal), «Тренды» (Trends), «Синхронизация» (Sync), «Отчёты»/«Дайджесты» (Reports/Digests).)

## Key screens
- **«Сегодня» (Today) / Overview:** recovery ring + TODAY'S STATS + **CHECKBOXES** (tick off what you did today — the focus is on action, not on a calendar). Doctor Context. Day readiness.
- **Biomarkers / «Анализы» (Labs):** the full treatment — values with BOTH reference and optimal ranges (clinical_optima C1-C5), change over time, what to raise with your doctor. This is the clear gap right now.
- **WHOOP Overview:** recovery/sleep/strain — charts pixel-perfect against the WHOOP benchmark.
- **WHOOP Correlations:** what drives recovery (journal ↔ recovery, personal baseline).
- **«Журнал» (Journal) / diary:** the user CHOOSES what they want to answer (custom behaviors picked from a library of 184), then a light daily check-in.
- **«Тренды» (Trends) / 30 days, Timeline, «Состав тела» (Body Composition), «Тренировки» (Workouts), «Отчёты» (Reports), «Дайджесты» (Digests), «Синхронизация» (Sync).**

## Connection checks (important)
The frontend checks what is already connected and what is not. If a device is not connected or there is no data (labs / DNA / blood / tracker) → **prompt the user to UPLOAD it** (an explicit CTA: upload an export / connect). Empty states lead to an upload.

## Being removed now
- **Ask / chat — REMOVE from openhealth.** It does not work with Claude; it would need wiring to the local CLIs (claude / codex / antigravity), which is a separate task for later. For now the chat comes out entirely — in its current state it is dead weight.

## Execution
- Design: /design harness → mobbin + cosmos + the WHOOP benchmark + dribbble → capture PNGs → Antigravity (agy) rework toward the Flenteey-as-primary style.
- Layout: /frontend-design + /frontend-developer, flawless at every resolution.
- Verify: Claude for Chrome / Preview, checked against the PNG benchmark at every breakpoint.
