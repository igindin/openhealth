# Circadian energy (day phases and curve)
> algo_version: two-process-rise@v1 · data source: engine (anchored on WHOOP sleep) · editability: parameters in code

## What this is

A Rise-style energy schedule for the day, based on the two-process model of sleep regulation (Borbely): sleep inertia after waking, the morning peak, the afternoon dip, the evening peak, the wind-down, the melatonin window and the sleep window. Plus a continuous 0-100 energy curve across 24 hours. Everything is tied to your **personal sleep anchor** rather than to the clock on the wall.

## Formula / algorithm

**Sleep anchor** (`_compute_sleep_anchor`, `openhealth/circadian.py`): from WHOOP sleep sessions over the last 14 days (naps excluded) — a weighted mean of bedtime, wake time and midpoint; nights within the last 7 days are weighted 2.0, older ones 1.0. With no data: wake 08:00, bedtime 00:30.

**Debt factor** `f = min(debt, 8) / 8` — accumulated sleep debt (sleep_debt@v2 over the same nights), saturating at 8 hours. Debt deepens and widens the dip, stretches inertia and trims the peaks.

**Morning light shift** (`_morning_light_shift_minutes`): if the light check-in is more than 15 min later than waking, the circadian phases shift by `min(delta/2, 30)` minutes (sleep inertia does not shift, since it is tied to waking).

**Phases** (from wake w, bedtime b, shift s):

- sleep inertia: w … w + (1.25 + 0.25f) h
- morning peak: w + 2.5 + s … w + (4.0 − 0.5f) + s
- afternoon dip: w + (6.0 − 0.25f) + s … w + (8.0 + 0.5f) + s
- evening peak: w + 9.0 + s … min(w + (11.0 − 0.5f) + s, b − 2:15)
- wind-down: b − 2 h … b
- **melatonin window: b − 60 min … b − 30 min** (the best bedtime window; it takes priority over wind-down when marking points)
- sleep window: b … w + 24 h

**Energy curve**: cosine interpolation between control points (zero slope at each), 4 points per hour. Nodes (hours from waking, energy):

- waking: 33 − 6f
- morning peak (3.25 + s): **92 − 15f**
- afternoon dip (7.0 + 0.25f + s): 46 − 18f
- evening peak (10.0 − 0.25f + s): 80 − 14f
- melatonin window (b − 0.75): 30 − 5f
- bedtime: 22 · mid-sleep: 8 · returning to the waking level at 24 h (continuity)

If the anchor yields a day shorter than 13 h or longer than 20 h, bedtime is taken as waking + 16.5 h (fallback).

## Parameters (code constants)

| parameter | value | where in code | why |
|---|---|---|---|
| model | two-process-rise@v1 | `openhealth/circadian.py: ENERGY_SCHEDULE_MODEL` | version stamp on every output |
| debt saturation, h | 8.0 | `openhealth/circadian.py: ENERGY_DEBT_SATURATION_H` | beyond 8 h of debt the effect stops deepening |
| fallback day length, h | 16.5 | `openhealth/circadian.py: DEFAULT_DAY_LENGTH_H` | used when the anchor is unusable |
| weight of recent nights | 2.0 (up to 7 days) | `openhealth/circadian.py: _compute_sleep_anchor` | the current schedule matters more than last week's |
| light shift | delta/2, capped at 30 min | `openhealth/circadian.py: _morning_light_shift_minutes` | light later than waking shifts phases gently |
| energy peak | 92 − 15f | `openhealth/circadian.py: _energy_nodes` | amplitude of the morning peak, trimmed by debt |

## Sources and confidence

- The two-process model (Borbely) and Rise-style phase shifts are established science (C3-C4).
- The **personal** placement of the windows is fitted from the sleep anchor and debt alone: `personal_fit: "C2"` on every output, plus an evidence_note directly in the payload.
- Generated windows are written as InsightHypothesis / TimelineEvent marked "hypothetical", and into the derived calendar only — never into source calendars.

## Known limitations

- The true circadian phase is not measured (there is no DLMO or core temperature), so everything is inferred from sleep timing.
- Chronotype is not modelled separately; the sleep anchor carries it only indirectly.
- The shift formulas (the numbers 2.5/6.0/9.0, the amplitudes 92/46/80) are a documented calibration against Rise's public methodology, not the result of a personal experiment; if you change them, bump the model version.
