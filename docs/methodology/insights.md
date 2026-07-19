# Insights (pattern detectors)
> algo_version: n/a (insights module, threshold constants) · data source: engine · editability: parameters in code

## What this is

The "find the real problem" layer on top of the daily series (recovery, hrv, rhr, sleep_h, strain). Seven detectors, each turning a numerical observation into a cautious conclusion: facts → a question to ask yourself → one concrete step. `severity` (info/attention/warning) is how loud the signal is in the data; `confidence` (C1/C2) is how sure we are of the cause. These are separate axes: a loud unexplained signal means high severity and low confidence.

## Formula / algorithm

The shared mechanic of the trend detectors: the mean over the last 7 days against a personal baseline from 14-28 days ago (a 7-28 window, with the most recent week excluded). At least 5 recent and 7 baseline points are required. Confidence: C2 when data is sufficient, C1 when it is sparse (via `cap_personal_pattern`). Every warning carries the disclaimer "if you have symptoms, see a doctor".

The seven detectors and their thresholds:

1. **Accumulated sleep loss** (`detect_sleep_debt`): a shortfall against the goal over 7 nights of >= 5 h is attention, >= 10 h is warning. The goal is personal (`goals.sleep_h`, default 8.0).
2. **HRV below baseline** (`detect_hrv_downtrend`): the 7-day mean below baseline by >= 8% is attention, >= 15% is warning.
3. **Resting heart rate above baseline** (`detect_rhr_uptrend`): a rise of >= 3 bpm is attention, >= 6 is warning.
4. **Streak of red days** (`detect_recovery_red_streak`): >= 3 consecutive days with recovery < 34 (the dashboard's red zone) is warning.
5. **Load on low recovery** (`detect_strain_recovery_mismatch`): days with strain >= 14 while recovery < 50, over a 14-day window; twice is attention, three times is warning.
6. **Weekend pattern** (`detect_weekend_pattern`): |mean recovery on weekdays − on weekends| >= 5 points; a weekend dip is attention, the reverse is info.
7. **Unstable sleep** (`detect_sleep_consistency`): an SD of sleep duration over 14 nights above 1.2 h is attention.

Output ordering: warning → attention → info, and within each, by descending confidence. One detector failing does not fail the pass (exceptions are swallowed).

## Parameters (code constants)

| parameter | value | where in code | why |
|---|---|---|---|
| default sleep goal | 8.0 | `openhealth/insights.py: DEFAULT_SLEEP_GOAL_H` | used when the user has not set their own |
| sleep loss attention / warning | 5.0 / 10.0 | `openhealth/insights.py: SLEEP_DEBT_WEEK_*_H` | ~43 min and ~1.4 h per night respectively |
| HRV drop attention / warning | 8.0 / 15.0 (%) | `openhealth/insights.py: HRV_DROP_*_PCT` | pragmatic personal trend bands |
| RHR rise attention / warning | 3.0 / 6.0 (bpm) | `openhealth/insights.py: RHR_RISE_*_BPM` | a classic early marker |
| recovery red zone | 34 | `openhealth/insights.py: RECOVERY_RED_MAX` | kept in sync with the dashboard colours |
| red streak length | 3 | `openhealth/insights.py: RED_STREAK_DAYS` | three in a row is no longer chance |
| high strain / low recovery | 14.0 / 50 | `openhealth/insights.py: STRAIN_HIGH, RECOVERY_LOW_FOR_STRAIN` | the definition of a mismatch day |
| mismatch window / attention / warning | 14 / 2 / 3 | `openhealth/insights.py: MISMATCH_*` | repetition makes a pattern |
| weekend difference | 5.0 | `openhealth/insights.py: WEEKEND_DIFF_POINTS` | below this it is noise from the calendar split |
| sleep SD | 1.2 | `openhealth/insights.py: SLEEP_CONSISTENCY_STDEV_H` | regularity matters more than any single night's duration |
| trend windows | 7 / 7-28 | `openhealth/insights.py: RECENT_WINDOW, BASELINE_LO, BASELINE_HI` | the recent week against your personal background |
| minimum points | 5 / 7 | `openhealth/insights.py: MIN_RECENT_POINTS, MIN_BASELINE_POINTS` | below this we do not compute |

## Sources and confidence

- The canon lives in `openhealth/evidence.py`: a personal pattern is capped at C2 until n-of-1 validation; anything at C3 or below is a question, not a claim.
- Personal baselines only, no population norms.
- The thresholds are documented, tunable constants with the rationale in code comments.

## Known limitations

- The detectors are independent and may describe the same event from different angles (illness will raise RHR and produce a red streak).
- The weekday/weekend split is crude and ignores your actual schedule.
- Nothing is diagnosed; a warning means "loud in the data", not "medically dangerous".
