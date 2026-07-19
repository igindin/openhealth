# Day load (from the calendar)
> algo_version: n/a (ics_calendar.day_load, transparent formula) · data source: external API (calendar ICS feed) · editability: parameters in code

## What this is

A 0-100 score for "how packed the day is", derived from a personal calendar (an ICS subscription, no OAuth). It is a context signal of the same class as weather or travel: it helps explain a dip in recovery and diagnoses nothing.

## Formula / algorithm

```
day_load_score = 70 × min(busy_hours / 8, 1)        — meeting hours against an 8-hour workday
              + 20 × min(meetings_count / 8, 1)      — context switches
              + 10, if there are >= 3 meetings and no gap >= 1 h  — no "recovery window"
```

Capped at 100. The breakdown is returned in `score_parts` (busy_hours / meetings / no_recovery_gap), so the score is explainable rather than a black box.

Counting details:

- overlapping meetings are **merged** before busy hours are counted, so a double booking is not counted twice;
- meetings are clipped to the boundaries of the local day;
- all-day events add no busy hours (they are listed separately);
- a "recovery window" is a gap of >= 60 min between merged busy intervals.

ICS parsing is an RFC 5545 MVP: VEVENT, all-day, timezones (Z/TZID/floating), RRULE limited to DAILY/WEEKLY within a ±7-day window, EXDATE, RECURRENCE-ID; anything not covered goes honestly into warnings.

## Parameters (code constants)

| parameter | value | where in code | why |
|---|---|---|---|
| busy-hours weight | 70 | `openhealth/connectors/ics_calendar.py: day_load` | the main factor: how much of the day meetings ate |
| hours normaliser | 8.0 | `openhealth/connectors/ics_calendar.py: WORKDAY_HOURS` | 8 h of meetings equals the full 70 points |
| meeting-count weight | 20 | `openhealth/connectors/ics_calendar.py: day_load` | the cost of context switching |
| meetings normaliser | 8 | `openhealth/connectors/ics_calendar.py: MEETINGS_NORM` | 8 meetings equals the full 20 points |
| fragmentation penalty | 10 | `openhealth/connectors/ics_calendar.py: day_load` | >= 3 meetings with no hour-long gap makes a day with no breathing room |
| minimum gap | 60 min | `openhealth/connectors/ics_calendar.py: GAP_MIN_MINUTES` | under an hour is not recovery |
| RRULE window | ±7 days | `openhealth/connectors/ics_calendar.py: RRULE_WINDOW_DAYS` | recurrences are expanded only around "now" |

## Sources and confidence

- The formula is our own heuristic, transparent and tunable; the 70/20/10 weights make no claim to scientific standing, they are a deliberate calibration of "what makes a day heavy".
- The ICS URL is a secret (it grants read access to the calendar): it is stored only in `~/.openhealth/calendar.json` (0600), never logged and never included in errors.

## Known limitations

- The calendar sees meetings, not actual workload: an empty calendar on a deadline day will show 0.
- RRULE other than DAILY/WEEKLY is not expanded (flagged as `recurring_skipped`).
- The score is context for reading recovery, not a standalone health metric.
