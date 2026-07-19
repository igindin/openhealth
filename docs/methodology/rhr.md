# RHR (resting heart rate)
> algo_version: recovery_score@v3 (component) · data source: WHOOP raw + engine · editability: parameters in code

## What this is

Resting heart rate, bpm — the overnight resting pulse from WHOOP. The dashboard shows the provider's raw value. The engine uses it twice: as a component of recovery_score@v3 and as a standalone trend detector (insights).

## Formula / algorithm

**Recovery component (`rhr_component`, `openhealth/modules/recovery.py`):**

`component = clamp(50 − 50 × (rhr / baseline − 1) / 0.30, 0, 100)`

Below baseline is better (the sign is inverted relative to HRV). A deviation of ±30% from baseline saturates the component (0 or 100). The baseline is the arithmetic mean RHR over 28 days (no ln transform needed: RHR is distributed almost symmetrically, unlike rMSSD).

**Trend detector (`detect_rhr_uptrend`, `openhealth/insights.py`):**

the mean over the last 7 days is compared against your personal baseline from 14-28 days ago (a 7-28 day window, excluding the most recent week). A rise of >= 3 bpm is attention, >= 6 bpm is warning (with the disclaimer "if you have symptoms, see a doctor").

## Parameters (code constants)

| parameter | value | where in code | why |
|---|---|---|---|
| component saturation | 0.30 | `openhealth/modules/recovery.py: _RHR_FULL_SWING` | ±30% from baseline covers the full 0-100 range |
| baseline window | 28 | `openhealth/modules/recovery.py: DEFAULT_BASELINE_WINDOW_DAYS` | the engine's shared personal baseline window |
| attention threshold | 3.0 | `openhealth/insights.py: RHR_RISE_ATTENTION_BPM` | a sustained +3 bpm is a classic early marker |
| warning threshold | 6.0 | `openhealth/insights.py: RHR_RISE_WARNING_BPM` | +6 bpm is a loud signal |
| plausible bounds | 25-120 | `openhealth/data_quality.py: PLAUSIBLE_BOUNDS["rhr"]` | outside this range it is a data error, not a measurement |

## Sources and confidence

- A sustained rise in resting heart rate as an early marker of stress, illness or overtraining is standard sports physiology (capped at C2 as a personal pattern, phrased as a question).
- The raw WHOOP value is a fact of measurement (C5 as data); interpreting the trend is a separate, cautious layer.

## Known limitations

- RHR responds with a lag (alcohol and late meals show up the following night).
- The 3/6 bpm thresholds are pragmatic personal bands, not a population norm; if you change them, update this file too.
