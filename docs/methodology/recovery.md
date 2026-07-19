# Recovery
> algo_version: recovery_score@v3 · data source: engine · editability: parameters in code

## What this is

A daily recovery score from 0 to 100: a weighted blend of HRV (the anchor), resting heart rate, respiratory rate and sleep quality, with each component normalised against your personal baseline over the last 28 days. A component value of 50 means "normal for you", above that is better than baseline, below that is worse.

IMPORTANT: the recovery card on the dashboard shows the WHOOP value from the export (the provider's raw number, C5 as a fact of measurement). Our recovery_score@v3 is a parallel, fully transparent calculation in the engine: the same input signals, but an open formula, a per-component breakdown (`components`, `weights_used` in metadata) and the algorithm version stamped on every record. It does not claim parity with WHOOP; it is a checkable alternative.

## Formula / algorithm

`score = Σ (component_i × weight_i) / Σ weight_i` — missing components are dropped and the remaining weights renormalised (HRV is required, the rest are optional).

Components (each 0-100):

- **HRV (ln-rMSSD, method `ln_rmssd_sd`)**: rMSSD is log-normally distributed, so we work on the ln scale. `z = (ln(hrv) − ln(baseline)) / ln_SD`, where ln_SD is the standard deviation of your personal ln(rMSSD) over the baseline window. `component = clamp(50 + 50 × z / 2.0, 0, 100)`: ±2 SD from baseline saturates the scale (0 or 100). The HRV baseline is the geometric mean `exp(mean(ln rMSSD))` over 28 days. With fewer than 2 points in the window, a conservative default of ln_SD = 0.15 is used (flagged as `hrv_ln_sd_is_default`).
- **RHR**: `component = clamp(50 − 50 × (rhr/baseline − 1) / 0.30, 0, 100)` — a resting heart rate below baseline is better; ±30% from baseline saturates the scale. The baseline is the arithmetic mean over 28 days.
- **Respiratory rate**: any deviation from baseline, in either direction, is penalised (an early marker of illness or stress). A dead band of 1.0 breath/min absorbs overnight noise, beyond which the component falls linearly to 0 at 3.0 breaths/min past the dead band.
- **Sleep**: WHOOP sleep performance % taken as is (already 0-100).

## Parameters (code constants)

| parameter | value | where in code | why |
|---|---|---|---|
| HRV weight | 0.60 | `openhealth/modules/recovery.py: RECOVERY_WEIGHTS["hrv"]` | HRV is the primary readiness signal (WHOOP ballpark: HRV-dominant) |
| RHR weight | 0.20 | `openhealth/modules/recovery.py: RECOVERY_WEIGHTS["rhr"]` | the second strongest autonomic marker |
| respiratory weight | 0.15 | `openhealth/modules/recovery.py: RECOVERY_WEIGHTS["respiratory"]` | early marker of illness or overtraining |
| sleep weight | 0.05 | `openhealth/modules/recovery.py: RECOVERY_WEIGHTS["sleep"]` | a top-up; sleep is already partly reflected in HRV/RHR |
| baseline window | 28 | `openhealth/modules/recovery.py: DEFAULT_BASELINE_WINDOW_DAYS` | 60 days is too sluggish; 28 tracks the person and stays stable (Altini/WHOOP ballpark) |
| short window | 7 | `openhealth/modules/recovery.py: SHORT_BASELINE_WINDOW_DAYS` | the day-to-day "normal range" |
| HRV saturation | 2.0 | `openhealth/modules/recovery.py: _HRV_FULL_SWING_SD` | ±2 SD is the conventional edge of the "normal range" |
| ln-SD default | 0.15 | `openhealth/modules/recovery.py: _HRV_DEFAULT_LN_SD` | typical within-person SD of ln(rMSSD) is ~0.10-0.20 |
| RHR saturation | 0.30 | `openhealth/modules/recovery.py: _RHR_FULL_SWING` | ±30% from baseline covers the component's full range |
| respiratory dead band | 1.0 | `openhealth/modules/recovery.py: _RESP_DEADBAND` | overnight noise per WHOOP (the "meaningful shift" floor) |
| respiratory saturation | 3.0 | `openhealth/modules/recovery.py: _RESP_FULL_SWING` | +3 breaths/min past the dead band drives the component to 0 |

## Sources and confidence

- Ln normalisation and personal SD follow the Altini / HRV4Training convention (smallest worthwhile change), which removes the magic 0.9/0.7 thresholds.
- The weights themselves are a documented choice, not a measurement; if you change them, bump the version.
- The score is written with `evidence_class: derived-metric` and confidence 0.9 as a fact of computation; interpretation is a separate layer.

## Known limitations

- We consume the provider's already-aggregated overnight rMSSD as is; WHOOP's exact aggregation window is publicly ambiguous (marked C-grade in the code).
- With incomplete inputs the score is computed from a subset of components (the `missing` field), so comparing days with different component sets needs care.
- Nothing here diagnoses anything; the number is an invitation to look at your day, not a verdict.
