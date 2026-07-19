# Weather flags
> algo_version: n/a (connectors/weather, threshold constants) · data source: external API (Open-Meteo) + engine · editability: parameters in code

## What this is

Cautious, graded weather flags treated as a co-factor in recovery: "pressure is dropping by 9 hPa — possibly a bad day if you are weather-sensitive". Weather is context of the same class as the calendar and travel; never a diagnosis. The data comes from Open-Meteo (free, no key), with the home location in `~/.openhealth/weather.json` (0600).

## Formula / algorithm

Flags are set by threshold against the canonical day (t_min/t_max/t_mean, mean pressure and its 24-hour change, humidity, precipitation, wind). Each flag carries a confidence grade and a message that states the uncertainty explicitly:

| flag | condition | grade (population) |
|---|---|---|
| pressure_drop | 24 h pressure change <= −8 hPa | C3 (observational evidence is mixed) |
| heat | t_max >= 30° | **C4** — heat worsens sleep, a consistent finding (our strongest claim) |
| cold | t_min <= 0° | C2 — context only, no established effect |
| humidity | mean humidity >= 85% | C2 — mostly meaningful in combination with heat |
| precipitation | precipitation >= 1 mm | C2 — a usual walk may have been skipped |

**Personal grades.** If the user's profile records a sensitivity to a flag: `declared` → a personal pattern, capped at C2; `validated` (repeated on/off) → can rise to C3. With no record, the population grade from the table is used.

**Bridge to correlations**: each factor becomes a boolean "habit" (day with a pressure drop yes/no, hot day yes/no, and so on) and goes into the same correlations engine with the 5/5 threshold — "does my recovery drop on days when pressure falls" is computed on personal data (see correlations.md).

`pressure_change_24h` = the day's mean pressure minus yesterday's mean pressure (means taken over hourly values); for the first day of a range, an extra day is requested.

## Parameters (code constants)

| parameter | value | where in code | why |
|---|---|---|---|
| pressure drop | 8.0 hPa/24h | `openhealth/connectors/weather.py: PRESSURE_DROP_HPA` | >= 8 hPa/day counts as a "rapid drop" in synoptic terms (a strong front) |
| heat | 30.0 °C | `openhealth/connectors/weather.py: HEAT_T_MAX_C` | daytime heat >= 30° degrades night sleep (sleep research) |
| cold | 0.0 °C | `openhealth/connectors/weather.py: COLD_T_MIN_C` | below zero is simply context for the day |
| humidity | 85.0 % | `openhealth/connectors/weather.py: HUMIDITY_HIGH_PCT` | on its own a weak signal |
| precipitation | 1.0 mm | `openhealth/connectors/weather.py: RAIN_MM` | enough to derail a walk |

## Sources and confidence

- Heat → poor sleep: consistent sleep research — C4 ("probable").
- Pressure → headaches and joints: observational and mixed population evidence — C3 at most; a raw personal pattern is C2.
- Every message in the UI names its grade and the uncertainty; the capping rule lives in `openhealth/evidence.py`.

## Known limitations

- Weather is taken for the home location, so travel to a different climate is not picked up by the flags (see the travel context).
- Open-Meteo: the forecast endpoint covers roughly 92 days back, beyond which the ERA5 archive is used (the switch is built in, but the data models differ slightly).
- Weather sensitivity is scientifically shaky, so the flags are deliberately phrased as hypotheses to test on your own data, not as facts.
