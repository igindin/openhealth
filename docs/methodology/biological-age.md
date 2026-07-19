# Biological age (fitness age from VO2max)
> algo_version: n/a (computed in the UI, dashboard.html) · data source: engine (VO2max) + UI norms table · editability: description only

## What this is

The "biological age" card on the dashboard. To be straight about it: this is **not a clinical biological age** (not PhenoAge, not methylation, not a clock built on blood biomarkers). It is a **fitness age**: "what average age does your VO2max look like" according to a table of age norms. C2, an estimate from a single cardio fitness measure. Without VO2max in the data the card is not shown at all.

## Formula / algorithm

The `VO2_AGE_NORMS` table holds mean VO2max for men by age (decade midpoints, mL/kg/min), the "average" category from summary tables of cardiorespiratory fitness:

| age | 25 | 35 | 45 | 55 | 65 | 75 |
|---|---|---|---|---|---|---|
| VO2max | 44 | 42 | 39 | 36 | 33 | 30 |

`fitnessAgeFromVo2max(v)`:

1. **Inside the table** — linear interpolation between adjacent points: find the decades whose norms bracket v and compute the age proportionally.
2. **Above the first point (v >= 44)** — extrapolation along the slope of the first segment (years per unit of VO2max), **with a lower cap: the result is never below 22 years** (`Math.max(22, ...)`; shown in the UI as "<= 22"). The cap exists because extrapolating "biologically 15 years old" from a high VO2max is meaningless.
3. **Below the last point (v < 30)** — extrapolation downward at 0.3 units of VO2max per year, capped at 85 years.

The chips next to it show the direction of each contribution (VO2max, RHR, sleep stability) without exact years: an arrow down means "makes you younger", up means "makes you older".

## Parameters (code constants)

| parameter | value | where in code | why |
|---|---|---|---|
| norms table | [[25,44],[35,42],[45,39],[55,36],[65,33],[75,30]] | `ui/web/dashboard.html: VO2_AGE_NORMS` | mean male norms by decade |
| lower cap | 22 | `ui/web/dashboard.html: fitnessAgeFromVo2max` | we do not show anything younger than 22 (extrapolation stops making sense) |
| upper cap | 85 | `ui/web/dashboard.html: fitnessAgeFromVo2max` | the symmetric guard at the bottom of the table |
| slope below the table | 0.3 units/year | `ui/web/dashboard.html: fitnessAgeFromVo2max` | a rough extrapolation beyond the norms |

## Sources and confidence

- VO2max is the Uth estimate (see vo2max.md), itself already C2; fitness age is derived from that estimate, so it is C2 as well (the grade is shown right on the card).
- The table holds population means for men; the direct source is ACSM-style summary tables of cardiorespiratory fitness.

## Known limitations

- **This is not a clinical biological age** — one measure, no blood, no epigenetics; the caption in the UI says plainly "an estimate from cardio fitness, not clinical".
- The table is for men; values for women are shifted, so the current calculation will carry a systematic error.
- The whole chain rests on an estimated VO2max: an input error (HRmax from age) propagates into the "age".
- The logic lives in the UI rather than the engine: editing the norms means editing dashboard.html (and this file).
