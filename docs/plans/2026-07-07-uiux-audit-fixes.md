# Plan: UI/UX audit fixes + sync integration (2026-07-07)

**Goal:** Close all 15 items from the UI/UX audit (Chrome, live data) and the 4 gaps between the web app's sync integration and the engine.

**Architecture:** Everything in `~/Projects/openhealth`: V1 `ui/web/dashboard.html` (the main skin), `ui/web/assets/oh-i18n.js`, V2 `ui/web/dashboard-v2.html`, bridge `ui/web/server.py`. The data is already shared through the bridge (`/api/journal/*`, `/api/sync`, `/api/data`); we are fixing the UI's reading side and how it presents state.

**Tech Stack:** vanilla JS single-file + GSAP, Python stdlib bridge, pytest.

**Created:** 2026-07-07

**Success Signals:** a second audit pass finds no P0; the July 6 entry is visible in the Day Feed on a clean origin; opening the dashboard with data older than 24h triggers a self-sync; 654+ tests green.

**DON'T DO:** we are not touching the V2 section migration, the calculation engine (apart from the trend avg bug), Hermes phase 1, or multi-user. No commits without a separate instruction.

**Verify First:** the server on 8770 is the old process (no `/api/intake`); run any check that needs the new bridge on a scratch port.

---

## Wave 1 — data trust and synchronization (P0 + gaps A-D)

### Task 1: Finish animations when tab visibility returns
**File:** `ui/web/dashboard.html`
**Action:** Next to the `REDUCED_MOTION` block, add: on `visibilitychange→visible` and `focus`, walk the active GSAP tweens and call `progress(1)`. The ring and counters will stop getting stuck at "5" instead of 22 in a backgrounded or covered window.
**Verify:** Chrome: open the tab in the background, then focus it — numbers and opacity are immediately final.

### Task 2: Data-freshness chip in the top bar
**File:** `ui/web/dashboard.html`
**Action:** Next to the recovery pill, a data-age chip driven by `DATA._meta.generatedAt`/`DATA.date`: "today" is quiet; "N days ago" is amber, and clicking it calls `go('sync')`. Data older than 24h no longer stays silent.
**Verify:** override generatedAt in the console → the chip turns amber; clicking it leads to Data Sources.

### Task 3: Auto-sync on open
**File:** `ui/web/dashboard.html`
**Action:** On load: bridge online && data date < today && debounce satisfied (`openhealth.autosync.last` more than 6h ago) → `POST /api/sync?days=3`; on success reload DATA + show a "Data updated" toast; on error fall back quietly to `POST /api/rebuild`. Use `OHNotify.toast`.
**Verify:** open the page with a stale snapshot — /api/sync goes out in Network, and the footer shows a fresh date afterwards.

### Task 4: Day Feed reads the server journal (gap #1)
**File:** `ui/web/dashboard.html` (`renderTimeline`, `tlDayEventsHTML`)
**Action:** Render the feed from localStorage immediately, and in parallel `GET /api/journal/range?start=&end=` for the feed's window; merge server days into localStorage (`setIfEmpty` semantics per day) and redraw the chips. An entry made on another device or from Telegram becomes visible.
**Verify:** clear localStorage → Timeline: July 6 shows the "In bed by 23:30" chip (it lives on the server), not "no entries".

### Task 5: Feed window runs to the real "today" (gap #2)
**File:** `ui/web/dashboard.html` (`renderTimeline`)
**Action:** Build rows from `todayKey()` backwards over 14 days; look up the recovery value by offset from the export date, and where there is no data show a neutral "—" chip. Log something for yesterday and see yesterday — this works even before the tracker syncs.
**Verify:** with a July 5 snapshot the feed starts at July 7 (today), and the 6th and 7th have no recovery but do carry entries.

### Task 6: Mute demo data (audit #1)
**Files:** `ui/web/dashboard.html`, `ui/web/assets/oh-registry.js` (if the chip lives there)
**Action:** Tiles with "demo" provenance get an `is-demo` class (CSS: desaturate + opacity .55); sections where every tile is demo get a single banner ("Section on demo data — needs an intraday sync") plus a CTA to Data Sources. Demo stops passing itself off as real.
**Verify:** Sleep/Stress: tiles are muted and the banner is present; Workouts (real) are untouched.

### Task 7: Trends — axes, zones, an honest average (audit #4)
**File:** `ui/web/dashboard.html` (trend rendering)
**Action:** (a) min/mid/max labels on the Y axis for recovery/HRV; (b) zone bands behind recovery (≥67 green, 34-66 amber, <34 red); (c) compute the average from real points only (exclude forward-filled gaps — right now HRV reads "120 ms" while today's is 62); (d) soften the smoothing (it creates false plateaus).
**Verify:** avg HRV matches a manual calculation over the `/api/data` values; axis values are visible on the chart.

### Task 8: Methodologies — honest loading (audit #5)
**File:** `ui/web/dashboard.html` (`renderMethodology`)
**Action:** Timeout 4s → 10s; on catch, show an error state with a "Retry" button instead of an eternal spinner.
**Verify:** block the endpoint (devtools offline) → after 10s an error with retry.

### Task 9: Bridge version in /api/health (gap #3)
**Files:** `ui/web/server.py`, `ui/web/dashboard.html`, `tests/test_bridge_server.py`
**Action:** A `BUILD = "YYYY-MM-DD"` constant → `/api/health {..., build}`; when major capabilities diverge (no build, or older than expected) the UI shows a "bridge is out of date — restart OpenHealth.command" badge in Diagnostics and the footer. Add a test for the build field.
**Verify:** `curl /api/health` includes build; pytest green.

## Wave 2 — language and consistency (P1)

### Task 10: Finish the EN i18n pass + fix an artifact
**File:** `ui/web/assets/oh-i18n.js` (+ bump `?v=4` in both skins)
**Action:** Add: zone phrases from word() (red/green), Vitals ring labels (the Russian label for recovery → Recovery), the base Day Pulse strings, Meds form labels (NAME/VACCINE and the untranslated Russian placeholders for "morning" and "self-prescribed"), the footer ("data as of", "Local · your data…" already exist — verify them). Fix "Daily strain (Strain)" → "Daily strain".
**Verify:** EN mode: the Today red/green zone reads in English; Vitals rings are monolingual; the Meds form is monolingual.

### Task 11: Biomarkers — merge the duplicate columns (audit #7)
**File:** `ui/web/dashboard.html` (biomarker table rendering)
**Action:** One "Range" column: the reference range, with "opt. X-Y" next to it only when the optimum differs. Give the freed width to the name and the scale.
**Verify:** rows with identical ranges no longer repeat the value; High/Optimal statuses still work.

### Task 12: Workouts — auto-selection and affordance (audit #8)
**File:** `ui/web/dashboard.html`
**Action:** On entry, auto-select the latest day (the detail pane is filled immediately), add a `selected` row state, hover, and a "strain 0-21" label on the bar scale.
**Verify:** entering Workouts — the right panel already shows the latest day's metrics.

## Wave 3 — polish (P2)

### Task 13: Day Pulse — an ICS field instead of POST instructions (audit #9)
**File:** `ui/web/dashboard.html`
**Action:** An "ICS link" input + a "Connect" button → `POST /api/calendar {ics_url}` (the endpoint exists); keep the Google/Apple steps as a hint and remove the line about "POST /api/calendar with JSON".
**Verify:** entering a link → 200, and the card switches to the "connected" state.

### Task 14: Research — state-aware empty (audit #10)
**File:** `ui/web/dashboard.html`
**Action:** If `BRIDGE.ok`, the text reads "Start deep research from the Biomarkers screen"; the "needs a running bridge" mention appears only when offline.
**Verify:** with the bridge online there is no contradiction in the copy.

### Task 15: Journal — check-in above settings (audit #11)
**File:** `ui/web/dashboard.html`
**Action:** Move today's check-in block to the top and put "Tracking setup" below it. Action-first.
**Verify:** entering Journal — the entries for the selected day are at the top.

### Task 16: Protocols — dedupe the disclaimer (audit #12)
**File:** `ui/web/dashboard.html`
**Action:** Render the "self-observation, not treatment" disclaimer once below the grid and remove it from the cards.
**Verify:** one disclaimer on screen with two cards present.

### Task 17: Timeline — collapse empty runs (audit #13)
**File:** `ui/web/dashboard.html` (`renderTimeline`)
**Action:** 3+ consecutive days with no entries → a single "N days with no entries" row (expandable on click).
**Verify:** on current data the feed compresses, and days with entries stay expanded.

### Task 18: V2 — floating bar and date (audit #14)
**File:** `ui/web/dashboard-v2.html`
**Action:** `padding-bottom` on the content under the bar; in the header, label it "data for <date>" instead of presenting the export date as today.
**Verify:** "Urgent today" is not covered; the date is labeled as the data date.

### Task 19: Small items — Stress ticks, Meds chevron (audit #15)
**File:** `ui/web/dashboard.html`
**Action:** Stress gauges: zone risks on the arc + the range under the value; give the Meds form selects an arrow (CSS `background-image` chevron) and `cursor:pointer`.
**Verify:** visually: gauges stay readable at small values, and selects are distinguishable from inputs.

---

## Final check (VERIFY wave)

1. `python3 -m pytest -q` — all green (654+).
2. `node -c assets/oh-i18n.js`; `python3 -c "ast.parse(server.py)"`.
3. Chrome pass: Today (both zones), Timeline (server entries + today), Trends (axes/zones/avg), Sleep (demo muted), Biomarkers, Workouts, Day Pulse, Journal, Protocols, Methodologies, V2 — on fresh data, light and dark.
4. The skin parity test (`tests/test_dashboard_parity.py`) is not broken.
