# Sleep (sleep debt and behavioural markers)
> algo_version: sleep_debt@v2 · data source: engine (from WHOOP raw) · editability: parameters in code

## What this is

Two layers. The first is **sleep debt**: how far actual sleep falls short of your *personal* need, both for a single night and cumulatively across a window of nights (the multi-night picture used by WHOOP/Rise: several short nights add up). The second is the behavioural sleep markers from the `sleep` module (duration, midsleep, social jetlag).

## Formula / algorithm

**Sleep debt (`sleep_debt`, `openhealth/modules/recovery.py`):**

- single night: `sleep_debt_h = max(0, need − actual)`; surplus: `surplus_h = max(0, actual − need)`;
- cumulative (v2): over the last 14 nights, `accumulated_debt_h = Σ max(0, need − night_i)` — the sum of each night's shortfall against need; a surplus on one night does not cancel the debt from others (a deliberate choice: oversleeping does not arithmetically "make up" for it);
- actual sleep for a night: `total_in_bed_time_milli − total_awake_time_milli` from WHOOP; if duration is missing, the dashboard approximates hours as `need × sleep_performance% / 100` (low confidence, flagged as such).

**Need is personal**: the `sleep_need_h` parameter, default 8.0 h, configurable per user (the `sleep_need_h` field in the payload / config). It is an estimate rather than a measurement, so debt records carry confidence 0.3 (C2).

**Behavioural markers (`openhealth/modules/sleep.py`):** duration; midsleep (the midpoint of sleep, as a time of day); a circadian phase proxy DLMO ~ sleep onset − 2 h (an explicit assumption, C2); social jetlag = |mean midsleep on free days − on work days| (Wittmann/Roenneberg).

## Parameters (code constants)

| parameter | value | where in code | why |
|---|---|---|---|
| default need | 8.0 | `openhealth/modules/recovery.py: DEFAULT_SLEEP_NEED_H` | a starting estimate before personal tuning |
| accumulation window, nights | 14 | `openhealth/modules/recovery.py: DEFAULT_SLEEP_DEBT_WINDOW_NIGHTS` | multi-night debt à la WHOOP/Rise |
| weekly debt attention | 5.0 | `openhealth/insights.py: SLEEP_DEBT_WEEK_ATTENTION_H` | ~43 min/night — where the effects of short sleep on recovery become consistent |
| weekly debt warning | 10.0 | `openhealth/insights.py: SLEEP_DEBT_WEEK_WARNING_H` | ~1.4 h/night — a pronounced chronic deficit |
| duration spread | 1.2 | `openhealth/insights.py: SLEEP_CONSISTENCY_STDEV_H` | an SD above this over 14 nights means an unstable schedule |
| plausible duration | 0-16 h | `openhealth/data_quality.py: PLAUSIBLE_BOUNDS["sleep_h"]` | more than 16 h/day is a data error |

## Sources and confidence

- Multi-night debt follows the public WHOOP/Rise methodology, accumulating against a personal need.
- Social jetlag comes from Wittmann et al. / Roenneberg (chronobiology).
- Need is an estimate (C2); debt is derived from that estimate, so it is C2 as well. The summary always shows "slept X of Y h of need".

## Known limitations

- Personal need is still set manually rather than derived from the data (there is no auto-calibration from "woke up rested without an alarm" days).
- Approximating sleep hours from sleep performance % (in the dashboard) is crude and marked as low confidence.
- The DLMO proxy "sleep onset − 2 h" is an assumption; the real phase requires a melatonin test.
