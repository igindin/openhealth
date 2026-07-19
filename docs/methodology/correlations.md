# Correlations (habit impact on recovery)
> algo_version: n/a (correlations module, no version stamp) · data source: engine · editability: parameters in code

## What this is

An answer to "what affects me": for each habit in the journal we compare mean recovery on the days it was present ("yes") against mean recovery on the days it was not ("no"), over a personal 90-day window. The same idea as WHOOP "Impacts".

## Formula / algorithm

**Where a habit's "+8 points" comes from (this is the ± shown in the UI):**

`impact = mean(recovery on "yes" days) − mean(recovery on "no" days)`

A worked example. Over the last 90 days the habit "sauna" is marked "yes" on 12 days and "no" on 40 days (with recovery available for each). Mean recovery on sauna days is 68.4, without it 60.1. Then `impact = 68.4 − 60.1 = +8.3 → rounded to +8`. This is a **difference of means in recovery points (a 0-100 scale)**, not a percentage change and not a probability. Sign: `+` means recovery averages higher on days with the habit, `−` means lower.

Then:

1. Data threshold: at least **5 "yes" days and 5 "no" days** in the window, otherwise the habit is not analysed at all (the signal is too thin; this mirrors WHOOP's threshold).
2. Effect size: `|impact| < 3` is negligible (not shown), `3-7` is small, `>= 7` is moderate.
3. Confidence grade: a raw personal correlation is capped at **C2 (weak signal)**. It can only rise to C3 (hypothesis) if the habit naturally switched on and off at least twice in a row by date (`switches >= 2`, a proxy for a minimal n-of-1/ABAB). A correlation never goes above C3 (the cap lives in `evidence.cap_personal_pattern`).
4. The output is not a bare number but an action prompt with its grade: "On days with X, recovery averages 68 versus 60 (+8 points). Try doing X for a week and see" plus open questions such as "what else changed on those days?".

## Parameters (code constants)

| parameter | value | where in code | why |
|---|---|---|---|
| analysis window, days | 90 | `openhealth/modules/correlations.py: DEFAULT_WINDOW_DAYS` | the personal baseline period (60-90 days balances recency and volume) |
| lag, days | 0 | `correlations.lag_days` (tunable) | pairs "behaviour on day D → recovery on day D+lag". Recovery is measured in the morning, so evening behaviour shows up in the NEXT morning's recovery — set lag = 1 for such inputs. Lag = 0 compares behaviour with the same day's recovery (morning D reflects night D-1). |
| minimum "yes" days | 5 | `openhealth/modules/correlations.py: MIN_YES_DAYS` | below this it is noise; mirrors WHOOP's threshold |
| minimum "no" days | 5 | `openhealth/modules/correlations.py: MIN_NO_DAYS` | a control group of days is needed |
| small threshold | 3.0 | `openhealth/modules/correlations.py: SMALL_IMPACT` | under 3 points is within noise, so it is not shown |
| moderate threshold | 7.0 | `openhealth/modules/correlations.py: MODERATE_IMPACT` | >= 7 points is a noticeable personal effect |

## Sources and confidence

- The "mean yes − mean no" approach is standard for consumer journals (WHOOP Journal Impacts); statistical significance tests are deliberately absent — instead of a p-value there is the hard 5/5 threshold, the effect-size thresholds and the C2 cap.
- Grades follow the `openhealth/evidence.py` canon: C2 is a question, not a claim; C3 only after repeated switches.

## Known limitations

- **Correlation is not causation.** "+8 points" may be explained by a third factor (on weekends there is both a sauna and a long sleep). That is why each output is framed as a question and suggests checking it with a deliberate on/off (see protocols.md).
- Only boolean journal entries are counted; numeric habits (doses, hours) do not make it in.
- Days without recovery are dropped from the pairs, so n_yes/n_no can be smaller than the number of marks in the journal.
