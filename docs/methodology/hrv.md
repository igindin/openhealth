# HRV (heart rate variability)
> algo_version: n/a (pulse module; readiness reading v2 via ln-SD) · data source: engine + WHOOP raw · editability: parameters in code

## What this is

The main personal readiness marker. The core metric is **rMSSD** (root mean square of successive differences, ms): the square root of the mean squared difference between adjacent RR intervals. It reflects parasympathetic tone. The dashboard shows WHOOP's overnight rMSSD (the raw value from the export); the `pulse` module can also compute rMSSD itself from RR intervals (Task Force 1996, golden-tested).

## Formula / algorithm

**Time domain (exact formulas, `openhealth/modules/pulse.py`):**

- RR cleaning: only intervals of 300-2000 ms (30-200 bpm) are kept, artefacts are dropped;
- `rMSSD = sqrt( mean( (RR[i+1] − RR[i])² ) )`;
- also: SDNN (SD of the intervals, ddof=1), pNN50 (% of adjacent differences > 50 ms), mean HR = 60000 / mean RR.

**Why ln.** rMSSD is log-normally distributed (a long right tail): the same shift in ms means different things at low and at high HRV. On the ln scale shifts are comparable, and the "normal range" can be built honestly: baseline ln ± k·SD of your personal ln(rMSSD), instead of the magic 0.9/0.7 thresholds (the Altini / HRV4Training convention, smallest worthwhile change).

**Overnight aggregation.** The engine consumes the provider's already-aggregated overnight rMSSD as is (one value per night). WHOOP's exact aggregation window ("deep sleep" vs a weighted average across the night) is publicly ambiguous, so our ln/SD maths applies to scoring and baselines, not to re-aggregating raw RR.

**Baseline and SWC.** Baseline = the geometric mean `exp(mean(ln rMSSD))` over 28 days (see recovery.md). Your personal "normal band" (smallest worthwhile change): `z = (ln(today) − ln(baseline)) / ln_SD`:

- `z >= −1` — an ordinary day;
- `−2 <= z < −1` — below your usual range, worth noting;
- `z < −2` — clearly outside the range (common after poor sleep, alcohol or illness).

## Parameters (code constants)

| parameter | value | where in code | why |
|---|---|---|---|
| normal band, SD | 1.0 | `openhealth/modules/pulse.py: _READINESS_NORMAL_SD` | within ±1 SD is an ordinary day |
| lower bound, SD | 2.0 | `openhealth/modules/pulse.py: _READINESS_LOW_SD` | below −2 SD is clearly outside the range |
| ln-SD default | 0.15 | `openhealth/modules/pulse.py: _READINESS_DEFAULT_LN_SD` | used before personal spread is known (matches recovery@v3) |
| plausible RR | 300-2000 ms | `openhealth/modules/pulse.py: _clean_rr` | the physiological range of 30-200 bpm |
| LF band | 0.04-0.15 Hz | `openhealth/modules/pulse.py: freq_domain` | standard Task Force 1996 boundaries |
| HF band | 0.15-0.40 Hz | `openhealth/modules/pulse.py: freq_domain` | same |

## Sources and confidence

- Metric definitions come from the Task Force of ESC/NASPE (1996).
- Ln scoring and personal SD bands follow Altini / HRV4Training.
- A single-day readiness reading is a personal pattern, capped at C2 (a question, not a verdict), and is phrased with open questions such as "how did you sleep, was there alcohol".

## Known limitations

- The frequency domain (LF/HF) is computed with a naive DFT over a linearly interpolated tachogram — usable for trend, explicitly low confidence (a switch to Welch is planned).
- The provider's overnight rMSSD is not re-aggregated, so we inherit its methodology sight unseen.
- HRV responds to everything at once (sleep, alcohol, stress, illness, training), so one number does not explain the cause.
