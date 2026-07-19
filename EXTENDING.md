# Extending OpenHealth dashboards (BYO design)

This is the agent-readable extension contract for OpenHealth dashboards. Before
writing a new theme, module or skin, read it together with `CAPABILITIES.md`:
a lot already exists, and extending the system usually beats rewriting it.

The engine↔skin boundary is public and works in both directions: bring your own
skin on top of our engine, your own engine under our skin, or take just the
design (tokens + chart kit) standalone.

## Mental model: engine → skin → theme

Three layers, each replaceable on its own.

- **Engine** - everything that describes and feeds the data:
  - registry `ui/web/assets/registry.json` - the single source of truth:
    definitions of skins, sections and metrics (no values).
  - data `ui/web/data.local.json` - real values keyed by metric id (or by
    `data_key`), git-ignored. With no data present, `demo` from the registry is
    used instead.
  - chart kit `ui/web/assets/oh-charts.js` - pure SVG functions shared by all
    skins.
  - loader `ui/web/assets/oh-registry.js` - joins registry and data and exposes
    a single `OH` object that skins render from.
- **Skin** - the layout and navigation that walks the registry and draws from
  `OH`. A skin holds no metric definitions and hardcodes no values.
  - `dashboard.html` (V1) - the classic dark skin with zones.
  - `dashboard-v2.html` (V2) - the light bento skin with long scroll.
- **Theme** - a set of CSS tokens with shared names layered on top of a skin.
  One skin can carry several themes.
  - V1: `dark`, `light`, `brutalist`.
  - V2: `bento`.
  - Token contract (identical names across every theme):
    `--bg`, `--card`, `--text`, `--accent-1..n`, `--radius`, `--shadow`,
    `--bg-fx`.

Metrics and sections are registry entries. Skins lay them out and navigate them.
Themes recolor them. The engine feeds them data. To add anything, start with the
registry.

## What the system already does

The current index of skins, themes, sections and metrics with provenance lives
in `CAPABILITIES.md`. It is generated from the registry
(`python3 ui/web/gen_capabilities.py`), so it always matches what actually
exists. Read it first.

## Engine contract: the global `OH`

The `oh-registry.js` loader exposes a global `OH` object. Skins render through
these methods only (no local copy of definitions or values):

- `OH.load({base, dataUrl}) -> Promise<OH>` - load the registry (`base` is the
  directory holding `registry.json`, default `./assets/`) and the real data
  (`dataUrl`, default `data.local.json`); the promise resolves to `OH`.
- `OH.metric(id)` - the metric definition from the registry (or `null`).
- `OH.section(id)` - the section definition from the registry (or `null`).
- `OH.sectionMetrics(sectionId)` - the section's metric definitions, in order.
- `OH.skin(id)` - the skin definition from the registry (or `null`).
- `OH.value(id)` - the metric's current value: the real one from
  `data.local.json`, otherwise `demo` from the registry.
- `OH.target(id)` - the metric's companion target (sleep need, for example): the
  real one via `target_key`, otherwise `target_default`.
- `OH.raw(key, fb)` - any value from `data.local.json` by key (for example
  `readiness`, `action`), with `fb` as fallback.
- `OH.state(id)` - the single state contract for a block: `'real'` (a real value
  exists), `'insufficient'` (data exists but sits below the `eligibility`
  threshold), `'empty'` (source not connected / section `status:"soon"`),
  `'demo'` (no real data, a labeled example is shown). Skins render non-real
  states muted and attach an honest chip ("not enough data" / "no data" /
  "demo").
- `OH.eligibility(id) -> {ok, have, need, label}` - the eligibility threshold for
  computed metrics (correlations need N days, for example). With no `eligibility`
  in the registry -> `{ok:true}`.
- `OH.evidence(id)` - how well-supported a claim is: `{confidence:C1-C5, type,
  sources}` (personal n=1 patterns never exceed C3). With no `evidence` ->
  `null`.
- `OH.manifest()` - the parity manifest: `sections` → metrics (`id` + `state`).
  This is the reference for what any skin is required to render from the
  registry.

Navigation, personas and the knowledge layer (also registry-driven, read by both
skins):

- `OH.nav.groups()` - up to 9 navigation groups with their subsections (from
  `registry.groups`), filtered by `openhealth.nav.hidden` visibility. Both skins
  build navigation from this and nothing else.
- `OH.personas()` / `OH.persona(id)` / `OH.setPersona(id)` / `OH.personaActive` -
  11 audience presets (`registry.personas`). `OH.personaGroups()` returns the
  groups reordered for the active persona; with no active persona it is exactly
  `OH.nav.groups()` (navigation stays unchanged by default - opt-in).
- `OH.devices()` / `OH.protocolSources()` / `OH.videosFor(metricId)` - the
  knowledge layer from `assets/knowledge.json`. `OH.evidenceLabel(level)` ->
  `{label, cls}` (high/medium/low or C1-C5) for the evidence badge.
- `OH.sectionView(id)` renders a section; `kind:"knowledge"` ->
  `OH.knowledgeView`, `status:"soon"` -> `OH.sectionStub` (an honest "coming
  soon" placeholder). Do not rewrite the `sectionView` markup: drag-and-drop
  (`[data-metric]`) and provenance (`.oh-q[data-prov]`) depend on it.

## Chart kit contract: the global `OHCharts`

`oh-charts.js` exposes a global `OHCharts`. The functions are pure: they take
data + options and return an SVG string. Colors come from the caller (the skin's
tokens), so the same chart looks right in any skin or theme.

- `OHCharts.ring(opts) -> string` (SVG) - the recovery/strain ring with a
  centered label.
- `OHCharts.sparkline(opts) -> string` (SVG) - a smoothed trend sparkline.

New chart types are added here and become available to both skins immediately.

## Skin contract

Every skin renders from `OH` and must export `window.__renderManifest()` - a
manifest of what it actually drew (the same `sections` → metrics + state). The
parity check compares each skin's `__renderManifest()` against `OH.manifest()`
(the registry reference); any divergence fails the check.

## Three levels of extension

### Level A - your own theme

The cheapest path. Add a token set using **the same variable names** (`--bg`,
`--card`, `--text`, `--accent-1..n`, `--radius`, `--shadow`, `--bg-fx`) on top of
an existing skin (V1 or V2) and you have a new theme. Neither the registry nor
the render code needs touching. A theme is skin-local: theme-for-theme parity
between skins is not required.

### Level B - your own skin

Your own layout and navigation on top of our engine. Implement the render from
`OH` (`OH.section`, `OH.sectionMetrics`, `OH.value`, `OH.target`, `OH.state` and
the `OHCharts` chart kit) and export `window.__renderManifest()`. A skin is valid
once it passes the parity check: its manifest matches `OH.manifest()`. A skin
holds no metric definitions and hardcodes no values.

### Level C - the boundary in both directions

- **Your skin over our engine** = level B.
- **Your engine under our skin.** A skin depends only on the registry+data
  interface (`OH`). Plug in your own data source implementing that same `OH`
  interface (`metric`, `section`, `sectionMetrics`, `value`, `target`, `state`,
  `manifest`, `load`) and reuse our skin as is.
- **Design only.** Take the token system and the chart kit (`oh-charts.js`)
  standalone, without the rest of the engine.

## Grouped navigation, personas and knowledge

- **Navigation group.** Edit `registry.groups` (id, label_ru, icon, order,
  section_ids). Keep it to 9 groups at most - the V2 nav bar is Home + groups +
  Settings, and the parity test enforces that limit. Every `section_id` must
  point at an existing section. Both skins pick it up from `OH.nav.groups()` - no
  extra code needed. Sections not yet migrated show an honest "coming soon"
  placeholder.
- **Persona (audience preset).** Edit `registry.personas` following
  `personas_schema`: id, label_ru, icon, tagline, priority_groups (from groups),
  focus_metrics (from metrics), devices/sources (from knowledge.json), note,
  reference. Every reference must resolve - the parity test checks this. The
  picker in both skins' settings fills itself automatically; picking a persona
  reorders navigation through `OH.personaGroups` (opt-in, off by default).
- **Knowledge-layer entry.** Edit `assets/knowledge.json` (`devices` /
  `protocol_sources` / `video_refs`). Every entry must carry provenance
  (`source_url`/`url` + `checked_at`) and an honest `evidence_level`
  (high/medium/low). A video is bound to a metric through `metric_id` (the metric
  must exist). Invent nothing - real links only.
- **States and honesty.** Never present something invented as real: a metric
  without data -> `demo`/`empty`, below threshold -> `insufficient` with the
  condition and the action to take. To set a threshold, give the metric an
  `eligibility` block (`need`, `have_key`, `label_ru`). Personal n=1 patterns
  never exceed C3 in `evidence`.

## The parity rule

This one is hard. For any new metric or section:

1. it is added to `ui/web/assets/registry.json` (the source of truth);
2. both skins (V1 and V2) must render it - there is no skin-local content,
   Settings included;
3. the capability map is regenerated: `python3 ui/web/gen_capabilities.py`;
4. the parity test must be green.

Content parity (sections, metrics, states) is mandatory; visual parity is not -
themes stay skin-local.
