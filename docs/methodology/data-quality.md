# Data quality
> algo_version: n/a (data_quality module, boundary constants) · data source: engine · editability: parameters in code

## What this is

Before reasoning over the data, the system checks how far it can be trusted: duplicates, dates in the future, physiologically impossible values, gaps in the daily series, suspected unit mix-ups. Everything is raised as a **question for review, never as a silent fix**: the module changes nothing and drops nothing, it only reports.

## Formula / algorithm

Five checks (`validate_records`):

1. **future_date** (high): the record's date is strictly later than "today".
2. **duplicate** (medium): one metric on one date with *different* values — only for metrics in `DAILY_UNIQUE_METRICS` (recovery, hrv, rhr, lab markers, and so on); per-event metrics (workout strain, naps) legitimately repeat and are not flagged.
3. **impossible_value** (high): a value outside the hard physiological bounds in `PLAUSIBLE_BOUNDS` (see the table).
4. **series_gap** (low): a gap of more than 4 days in the dated series of a key daily metric (recovery, hrv, rhr).
5. **unit_suspect** (medium): glucose or cholesterol that looks like mmol/L but was recorded without units (the value is unrealistically low for mg/dL, but ×18 lands in the normal range) — flagged for review, not auto-converted.

**Quality score** (`quality_score`): `score = max(0, 100 − Σ penalties)`; the penalty per issue by severity is **high −12, medium −6, low −2**. The per-severity breakdown is returned alongside, so the number is explainable. Verdicts: >= 90 "clean data", >= 70 "minor questions", >= 40 "noticeable problems", below that "too early to trust conclusions".

## Parameters (code constants)

The `PLAUSIBLE_BOUNDS` table (`openhealth/data_quality.py`); a value at or beyond a boundary is almost certainly an entry error:

| metric | bounds | unit / comment |
|---|---|---|
| hrv | 1.0 - 300.0 | ms rMSSD |
| rhr | 25.0 - 120.0 | bpm at rest |
| recovery | 0.0 - 100.0 | % (WHOOP scale) |
| strain | 0.0 - 21.0 | WHOOP scale |
| sleep_h | 0.0 - 16.0 | hours per day |
| spo2 | 50.0 - 100.0 | % saturation |
| glucose | 1.0 - 40.0 | mmol/L (also catches low mg/dL values) |
| temperature | 30.0 - 45.0 | °C body |
| weight_kg | 20.0 - 400.0 | kg |

| parameter | value | where in code | why |
|---|---|---|---|
| high penalty | 12 | `openhealth/data_quality.py: _SEVERITY_PENALTY` | almost certainly an error, so it hits trust hard |
| medium penalty | 6 | `openhealth/data_quality.py: _SEVERITY_PENALTY` | a probable problem |
| low penalty | 2 | `openhealth/data_quality.py: _SEVERITY_PENALTY` | worth a look |
| gap threshold | 4 days | `openhealth/data_quality.py: DEFAULT_GAP_DAYS` | anything shorter is an ordinary missed sync |
| unit multiplier | ×18 | `openhealth/data_quality.py: _check_unit_suspicion` | mg/dL ↔ mmol/L for glucose and cholesterol |

## Sources and confidence

- The bounds are conservative physiological plausibility for a living person, not clinical norms.
- Messages and suggestions always say "check the source", never "we fixed it for you".

## Known limitations

- Metric aliases are matched as substrings of the name, so exotic metric names will slip past the checks.
- The score is linear in the number of issues: nine low-severity gaps will push it down further than one high-severity error — read the breakdown, not just the number.
- Unit suspicion covers only glucose and cholesterol (the ×18 pairs); other unit pairs are not detected.
