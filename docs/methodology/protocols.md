# Protocols (n-of-1 experiments)
> algo_version: n/a (protocols module, template constants) · data source: engine · editability: parameters in code

## What this is

The bridge from "there may be a problem" (an insight) to "here is one change and an exact success criterion" (a protocol). This is the only way to lift a personal pattern from C2 (a weak signal) to C3 (a hypothesis): a minimal single-subject experiment with a baseline phase and a numerical criterion declared in advance.

## Formula / algorithm

**Designs:**

- **ABAB** — baseline (A) → intervention (B) → A again → B again. Switching back and forth separates the effect from coincidence. Used for most protocols (7 days per phase; 14 for weekly patterns).
- **AB** — a single switch, for cases where returning to baseline is undesirable or pointless (a red recovery streak, fixing a wake time).

**Design rules (built into the code):**

1. One intervention per protocol, otherwise the result cannot be interpreted.
2. A numerical success criterion is declared before the start (no moving goalposts), for example: "mean recovery in the B phases is higher than in A by >= 5 points".
3. `confidence_cap = C2` until the protocol is finished: merely running an experiment adds no confidence.
4. Every protocol carries a safety note; for a red streak the note is stronger ("see a doctor first, experiment second").

**Construction** (`build_protocols`): from insights, one template per kind (sleep_debt, hrv_downtrend, rhr_uptrend, recovery_red_streak, strain_recovery_mismatch, weekend_pattern, sleep_consistency); from correlations, an ABAB check for C2+ habits (C1 is discarded). Ranking: insight severity, then confidence; at most 3 active suggestions. For the HRV protocol the intervention is targeted: if the negative correlations include a classic HRV suppressor (alcohol / screens / late nights / caffeine — `_HRV_TRIGGER_HINTS`), the protocol proposes removing that specific one.

**Template success criteria (examples):** earlier bedtime → recovery in B higher than in A by >= 5 points; HRV protocol → the 7-day mean HRV in B higher than the baseline by >= 8%; RHR → the 7-day resting heart rate in B within 2 bpm of baseline; from a correlation → a bar of `max(3, round(|impact|))` points (so as not to chase noise).

## Parameters (code constants)

| parameter | value | where in code | why |
|---|---|---|---|
| maximum active | 3 | `openhealth/protocols.py: MAX_ACTIVE_PROTOCOLS` | more parallel experiments make results uninterpretable |
| phase length | 7 days (14 for weekly) | `openhealth/protocols.py: from_insight` | a week covers both weekdays and weekend |
| confidence cap | C2 | `openhealth/protocols.py: Protocol.confidence_cap` | until it finishes, an experiment proves nothing |
| minimum bar | 3 points | `openhealth/protocols.py: _round_points` | a lower criterion is chasing noise |
| HRV triggers | alcohol/screen/late/caffeine | `openhealth/protocols.py: _HRV_TRIGGER_HINTS` | targets the intervention using the correlations |

## Sources and confidence

- n-of-1 / ABAB is the standard methodology for single-subject experiments.
- The confidence canon is `openhealth/evidence.py`: a completed protocol with a repeated switch can lift a pattern to C3, no higher.

## Known limitations

- There is no blinding or placebo control — you know which phase you are in.
- Seasonality or illness can coincide with a phase; the protocol's open questions remind you to check for that.
- The engine proposes and evaluates protocols but does not enforce them: carrying them out and being honest about the phases is up to the person.
