# Calculation methodology

One file per dashboard parameter. Every file is written **from the actual code** (exact constants and paths) and follows a strict format so the UI can parse it: a `#` title, a `> algo_version: ... · data source: ... · editability: ...` line, then the sections `## What this is`, `## Formula / algorithm`, `## Parameters (code constants)` (a `parameter | value | where in code | why` table), `## Sources and confidence`, `## Known limitations`.

## Index

| file | parameter | algorithm version | main module |
|---|---|---|---|
| [recovery.md](recovery.md) | recovery score 0-100 | recovery_score@v3 | `openhealth/modules/recovery.py` |
| [correlations.md](correlations.md) | habit impact ("+N points") | n/a | `openhealth/modules/correlations.py` |
| [hrv.md](hrv.md) | rMSSD, readiness, baseline/SWC | n/a (readiness v2) | `openhealth/modules/pulse.py` |
| [rhr.md](rhr.md) | resting heart rate (component + trend) | recovery_score@v3 | `openhealth/modules/recovery.py`, `openhealth/insights.py` |
| [strain.md](strain.md) | strain 0-21 (passthrough) | strain@v1 | `openhealth/modules/recovery.py` |
| [sleep.md](sleep.md) | sleep debt, need, sleep markers | sleep_debt@v2 | `openhealth/modules/recovery.py`, `openhealth/modules/sleep.py` |
| [vo2max.md](vo2max.md) | VO2max (Uth estimate) | vo2max@v1 | `openhealth/modules/vo2max.py` |
| [circadian.md](circadian.md) | day phases, energy curve | two-process-rise@v1 | `openhealth/circadian.py` |
| [insights.md](insights.md) | 7 pattern detectors | n/a | `openhealth/insights.py` |
| [protocols.md](protocols.md) | n-of-1 protocols (ABAB/AB) | n/a | `openhealth/protocols.py` |
| [biological-age.md](biological-age.md) | fitness age from VO2max | n/a (UI) | `ui/web/dashboard.html` |
| [day-load.md](day-load.md) | day load from the calendar | n/a | `openhealth/connectors/ics_calendar.py` |
| [weather-flags.md](weather-flags.md) | weather flags | n/a | `openhealth/connectors/weather.py` |
| [data-quality.md](data-quality.md) | data quality score | n/a | `openhealth/data_quality.py` |

Related: [evidence-and-trust.md](evidence-and-trust.md) — the C1-C5 confidence canon that every file above refers to.

## Sync rule (anti-drift)

**Change a parameter in code → bump the module's `algo_version` (if it has one) and update the matching md in this folder.** Older records stay stamped with the version that produced them; that is the whole point of versioning.

Drift is caught by `tests/test_methodology_docs.py`: it parses the values out of the "Parameters (code constants)" tables and compares them against a live import of the modules (recovery weights, the 28-day baseline window, the Uth coefficient 15.3, the 8 hPa pressure threshold, the busy-hours weight of 70). If the md and the code diverge, the test goes red.

## How to edit this

These files are plain markdown in a local repository, and they are the source of truth for the future "Methodology" page in the dashboard. Editing:

- by hand — any edit to the file (keep the section format, otherwise the UI parser and the test break);
- through an agent — a request like "change threshold X" means a **double edit**: the constant in the code plus the row in the md table (and a version bump if the module is versioned). The agent must do both;
- runtime overrides — the `openhealth/params.py` registry (`~/.openhealth/params.json`): the user changes a value within an allowed range without touching code; records computed with an override are stamped `algo_version+custom` and carry `metadata.params_overrides`. The constants in code remain the canonical defaults, and those are what the anti-drift test checks.

UI contract (for the orchestrator): `GET /api/methodology` → `[{id, title, version, path, content}]`, where `id` is the filename without `.md`, `title` is the first `#` line, `version` comes from `algo_version` in the header, and `content` is the raw markdown.
