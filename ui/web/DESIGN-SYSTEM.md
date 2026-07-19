# OpenHealth Web — design system and component library

The implemented system (what is already in the code), paired with the vision in [DESIGN-SPEC.md](DESIGN-SPEC.md).
Two skins — **V1** (`dashboard.html`, classic, 3 themes) and **V2** (`dashboard-v2.html`, bento) —
render from a SINGLE engine (`assets/oh-registry.js` + `assets/oh-charts.js`). The `.oh-*` markup is
shared; only the theme differs (CSS tokens). If you change tile/section markup, change it in `oh-registry.js`,
and keep the CSS contract below in sync across both skins, otherwise parity drifts apart.

## Tokens (`:root`, V2)

- **Color:** `--bg-main`, `--bg-card`, `--text-primary`, `--text-muted`; accents `--color-{orange,purple,pink,green,blue,yellow}`; `--color-dark`. Section accents: sleep `#3FA9F5`, strain `#8B6CF0`, stress `#FF7A59`, body `#27C28A`.
- **Radii:** `--radius-lg 24` (cards/sections), `--radius-md 16` (tiles), `--radius-sm 8`.
- **Spacing (8px base):** `--space-1 4` / `--space-2 8` / `--space-3 12` / `--space-4 16` / `--space-5 24` / `--space-6 32`. Every gap is one of these tokens, never a hand-written px value.
- **Tile unit:** `--tile-pad 16`, `--tile-label-h 30` (fixed height that fits a 2-line label), `--tile-icon 16`.
- **Type:** Geist / Geist Mono. **Motion:** GSAP reveal, `--transition-smooth`, safe under `prefers-reduced-motion`.

## Grid and composition (the `website-composition-craft` canon)

- Bento: one large focus card plus satellites; whitespace does the separating, not borders.
- A single modular scale for sizing; vertical rhythm on the 8px base.
- Asymmetry and an explicit scale jump at the focus; icons carry meaning rather than decoration.

## The `.oh-*` component library

- **`.oh-section`** — section card: head (accent icon + title) + `.oh-section__grid`.
- **`.oh-section__grid`** — `grid-auto-rows:1fr; align-items:stretch` → tiles in the same row get EQUAL height (Gestalt similarity). `minmax(180px,1fr)`, gap `--space-3`.
- **`.oh-tile`** — the metric unit, `flex-column; height:100%`. Alignment contract:
  - `.oh-tile__top`: `align-items:flex-start; min-height:--tile-label-h` → values sit on one line, icons share a common top edge.
  - `.oh-tile__icon`: `inline-flex; gap:--space-1` → the "?" (provenance) button and the metric glyph neither collide nor drift. Do NOT add inline spacing in the markup.
  - `.oh-tile__val` is large (the focus), `.oh-tile__unit` is muted.
  - `.oh-tile .oh-chip` is pinned to the bottom (`margin-top:auto`) → demo chips line up across the row.
- **`.oh-chart-card`** (span 2) — a chart from `oh-charts.js`; head aligned to the top.
- **`.oh-q`** — the provenance button (from `oh-provenance.js`); **`[data-metric]`** — the drag-correlation anchor (`oh-correlate.js`).
- **`.rail-nav` / `.rail-btn`** (V2) — the right-hand nav bar: round buttons, `flex:0 0 auto; aspect-ratio:1`; `gap` guarantees clearance even when height runs short; navigation via `scrollIntoView` + scroll-spy (the click handler is delegated to the container so it survives the rebuild that happens on a persona change).
- **`.oh-kcard` / `.oh-kgrid` / `.oh-kcat`** (knowledge layer, the «Девайсы и источники» / Devices and Sources area) — a device/source card: `.oh-kcard__top` (name + evidence badge), measures/meta, `.oh-kcard__actions` (source link + `.oh-kverify`, labeled "Перепроверить" / re-verify). Cards are grouped by category (`.oh-kcat`) into a responsive grid.
- **`.oh-ev--{high,mid,low}`** — the evidence badge (an honest high/medium/low scale mapped to C1-C5): green/amber/red, with a translucent background that works on both the dark and the light theme. The same badge appears in the "?" popover next to the short videos attached to a metric. Knowledge and badge styles self-inject from `oh-provenance.js` and are themed through CSS variables.
- **Group-based navigation** is built from `OH.nav.groups()` (V1 sidebar, V2 `.rail-nav`); the persona picker in both skins' settings reorders it through `OH.personaGroups` (opt-in — by default the navigation does not change).

## Anti-slop hard gate (before you commit)

Ragged tiles of differing heights; icons sitting at different levels; hand-written px instead of tokens; decorative icons with no meaning; centering everything; no focus / no scale jump; accent confetti (one accent = one meaning per page).

## Parity

`window.__renderManifest()` + `tests/test_dashboard_parity.py` verify that V1 and V2 render the same sections and metrics from the registry. Keep the tile CSS contract identical across both skins.
