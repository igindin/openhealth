"""WHOOP window re-sync: delete a stored record only when the window proves it stale.

WHOOP filters every collection on provider timestamps, not dates: an item
comes back when its interval overlaps the requested window (an in-progress
item always does), and a recovery comes back exactly when its linked sleep
does. The sync used to delete every stored record whose attributed date fell
inside the window and was not re-emitted. A scheduled window starts two days
before the last success, often mid-morning, so each run deleted the items of
that first day which had ended before the window opened: the morning
recovery (created after a sleep that ended earlier), an early workout, a
night nap. No later window, starting later still, ever fetched them again.

The contract under test: a stored record that was not re-emitted is stale
only when its whole provider interval [start, end] lies inside the window
with ``WHOOP_PURGE_MARGIN`` to spare at both edges. Recoveries borrow the
interval of their linked sleep (this run's version first), or of their cycle
when no sleep is known. Anything unknown is kept. A metric whose parent event
was re-emitted with other metrics but without it is stale whatever its
interval; a parent re-emitted without any metric keeps its stored ones. An
answer of a full page or more proves nothing missing at its old edge (up to
the margin past its oldest start), since WHOOP pages newest first. A
per-dataset breaker keeps every stale record of a dataset when more in-window
events are missing (old-edge ones included) than a sound answer could
explain. All data is synthetic.

Run directly:  PYTHONPATH=$PWD python3 tests/test_whoop_purge_interval.py
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from openhealth import index, whoop
from openhealth.storage import ensure_repo_structure
from openhealth.whoop import WHOOP_SOURCE_ID, normalize_whoop_payload, plan_whoop_purge, sync_whoop

ALL_DATASETS = ("cycles", "recoveries", "sleeps", "workouts")

# A scheduled window: the last success (2026-07-29T08:20:15Z) minus two days,
# up to now. The margin window lies 6 h inside it at both edges.
WINDOW_START = "2026-07-27T08:20:15Z"
WINDOW_END = "2026-07-29T10:11:50Z"
INNER_START = "2026-07-27T14:20:15Z"
INNER_END = "2026-07-29T04:11:50Z"
FETCHED_AT = "2026-07-29T10:11:50+00:00"
PREVIOUS_SYNC_AT = "2026-07-29T08:20:15+00:00"


def _instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(instant):
    """WHOOP's own timestamp format: UTC with milliseconds."""
    return instant.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _window_iso(instant):
    """The sync window's format: UTC in whole seconds."""
    return instant.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _shift(value, **delta):
    return _iso(_instant(value) + timedelta(**delta))


def _uuid(number):
    """A synthetic UUID-shaped provider id (WHOOP v2 sleeps and workouts use UUIDs)."""
    return "00000000-0000-4000-8000-%012d" % number


def _cycle(cycle_id, start, end, *, kilojoule=True):
    score = {"strain": 10.4}
    if kilojoule:
        score["kilojoule"] = 8100.0
    return {
        "id": cycle_id,
        "user_id": 1001,
        "created_at": start,
        "updated_at": end or start,
        "start": start,
        "end": end,
        "timezone_offset": "+02:00",
        "score_state": "SCORED",
        "score": score,
    }


def _sleep(number, cycle_id, start, end, *, nap=False):
    return {
        "id": _uuid(number),
        "cycle_id": cycle_id,
        "user_id": 1001,
        "created_at": end,
        "updated_at": end,
        "start": start,
        "end": end,
        "timezone_offset": "+02:00",
        "nap": nap,
        "score_state": "SCORED",
        "score": {"respiratory_rate": 15.2, "sleep_performance_percentage": 87.0},
    }


def _recovery(cycle_id, sleep_number, created_at, *, skin_temp=True):
    score = {"recovery_score": 58.0, "hrv_rmssd_milli": 44.0, "resting_heart_rate": 53.0}
    if skin_temp:
        score["skin_temp_celsius"] = 33.4
    return {
        "cycle_id": cycle_id,
        "sleep_id": _uuid(sleep_number),
        "user_id": 1001,
        "created_at": created_at,
        "updated_at": created_at,
        "score_state": "SCORED",
        "score": score,
    }


def _workout(number, start, end, *, kilojoule=True):
    score = {"strain": 7.1}
    if kilojoule:
        score["kilojoule"] = 950.0
    return {
        "id": _uuid(number),
        "user_id": 1001,
        "created_at": end,
        "updated_at": end,
        "start": start,
        "end": end,
        "timezone_offset": "+02:00",
        "sport_name": "running",
        "score_state": "SCORED",
        "score": score,
    }


# Provider history around the window. Cycle 101 spans the window start, so
# WHOOP returns it. Sleep 101 ended at 06:12, before the window start, so
# WHOOP returns neither it nor its recovery, which was created at 07:31 on
# the window start's date. The morning workout and the night nap ended before
# the window start too. Cycle 103 is still open.
CYCLE_101 = _cycle(101, "2026-07-26T22:05:12.250Z", "2026-07-27T22:47:31.120Z")
SLEEP_101 = _sleep(101, 101, "2026-07-26T22:05:12.250Z", "2026-07-27T06:12:40.660Z")
RECOVERY_101 = _recovery(101, 101, "2026-07-27T07:31:05.340Z")
NAP_701 = _sleep(701, 101, "2026-07-27T00:05:00.000Z", "2026-07-27T00:50:00.000Z", nap=True)
WORKOUT_MORNING = _workout(9001, "2026-07-27T06:25:00.000Z", "2026-07-27T07:10:00.000Z")
CYCLE_102 = _cycle(102, "2026-07-27T22:47:31.120Z", "2026-07-28T21:30:00.000Z")
SLEEP_102 = _sleep(102, 102, "2026-07-27T22:47:31.120Z", "2026-07-28T06:10:00.000Z")
RECOVERY_102 = _recovery(102, 102, "2026-07-28T06:40:00.000Z")
WORKOUT_AFTERNOON = _workout(9002, "2026-07-28T12:00:00.000Z", "2026-07-28T12:50:00.000Z")
CYCLE_103 = _cycle(103, "2026-07-28T21:30:00.000Z", None)
SLEEP_103 = _sleep(103, 103, "2026-07-28T21:30:00.000Z", "2026-07-29T05:20:00.000Z")
RECOVERY_103 = _recovery(103, 103, "2026-07-29T05:45:00.000Z")

PROVIDER_HISTORY = {
    "cycles": [CYCLE_101, CYCLE_102, CYCLE_103],
    "recoveries": [RECOVERY_101, RECOVERY_102, RECOVERY_103],
    "sleeps": [NAP_701, SLEEP_101, SLEEP_102, SLEEP_103],
    "workouts": [WORKOUT_MORNING, WORKOUT_AFTERNOON],
}

# Items WHOOP deleted after the previous sync stored them, one per dataset,
# each wholly inside the margin window.
STALE_ITEMS = {
    "cycles": _cycle(150, "2026-07-28T01:00:00.000Z", "2026-07-28T03:00:00.000Z"),
    "recoveries": _recovery(150, 150, "2026-07-28T02:45:00.000Z"),
    "sleeps": _sleep(150, 150, "2026-07-28T01:00:00.000Z", "2026-07-28T02:30:00.000Z"),
    "workouts": _workout(9050, "2026-07-28T15:00:00.000Z", "2026-07-28T15:40:00.000Z"),
}


def _merged(*fetches):
    merged = {dataset: [] for dataset in ALL_DATASETS}
    for fetch in fetches:
        for dataset, items in fetch.items():
            merged[dataset].extend(items if isinstance(items, list) else [items])
    return merged


def _records(fetch, fetched_at=FETCHED_AT):
    """Normalize a {dataset: [items]} fetch exactly as the sync does."""
    records = []
    for dataset in ALL_DATASETS:
        items = fetch.get(dataset) or []
        if items:
            records.extend(
                normalize_whoop_payload(
                    dataset_name=dataset,
                    payload={"records": list(items)},
                    artifact_id="artifact-synthetic",
                    source_id=WHOOP_SOURCE_ID,
                    fetched_at=fetched_at,
                )
            )
    return records


_EVENT_ID = {
    "cycles": lambda item: "whoop-cycle-%s" % item["id"],
    "recoveries": lambda item: "whoop-recovery-%s" % item["cycle_id"],
    "sleeps": lambda item: "whoop-sleep-%s" % item["id"],
    "workouts": lambda item: "whoop-workout-%s" % item["id"],
}


def _family(records, dataset, item):
    """The record ids one provider item normalizes to: its event and metrics."""
    event_id = _EVENT_ID[dataset](item)
    (event,) = [record for record in records if record["id"] == event_id]
    return {event_id, *event["related_record_ids"]}


def _ids(records):
    return {record["id"] for record in records}


def _overlap_fetch(history, start, end):
    """What WHOOP answers for [start, end]: every item whose interval overlaps it
    (an open item always), and the recoveries of exactly the sleeps it returns."""
    lo, hi = _instant(start), _instant(end)

    def overlaps(item):
        return (item["end"] is None or _instant(item["end"]) >= lo) and _instant(item["start"]) < hi

    fetch = {
        dataset: [item for item in history.get(dataset, []) if overlaps(item)]
        for dataset in ("cycles", "sleeps", "workouts")
    }
    returned_sleeps = {item["id"] for item in fetch["sleeps"]}
    fetch["recoveries"] = [
        item for item in history.get("recoveries", []) if item["sleep_id"] in returned_sleeps
    ]
    return fetch


def _plan_ids(existing, fresh, start, end, datasets):
    """The ids a re-sync replaces: what ``purge_existing_whoop_records`` deletes."""
    return plan_whoop_purge(existing, fresh, start, end, datasets).ids


def _select(existing, fresh, *, start=WINDOW_START, end=WINDOW_END, datasets=ALL_DATASETS):
    return set(_plan_ids(existing, fresh, start, end, list(datasets)))


def _stale(existing, fresh, **window):
    """Stored ids a re-sync deletes for good: selected, but not rewritten."""
    return _select(existing, fresh, **window) - _ids(fresh)


def _plan(existing, fresh, *, start=WINDOW_START, end=WINDOW_END, datasets=ALL_DATASETS):
    return whoop.plan_whoop_purge(existing, fresh, start, end, list(datasets))


def _stored_event(record_id, event_kind, metadata, date="2026-07-28"):
    """A hand-made stored event with arbitrary (possibly broken) metadata."""
    record = {
        "id": record_id,
        "record_type": "TimelineEvent",
        "source_id": WHOOP_SOURCE_ID,
        "title": "Stored WHOOP record",
        "summary": "Stored record with hand-made metadata",
        "artifact_ids": [],
        "evidence_class": "personal",
        "confidence": 0.95,
        "date": date,
        "start_date": date,
        "event_kind": event_kind,
        "related_record_ids": [],
    }
    if metadata is not _NO_METADATA:
        record["metadata"] = metadata
    return record


_NO_METADATA = object()


class IncidentShapeTests(unittest.TestCase):
    """Items that ended before the window start are absent, not deleted."""

    def setUp(self):
        self.stored = _records(PROVIDER_HISTORY)
        self.fetch = _overlap_fetch(PROVIDER_HISTORY, WINDOW_START, WINDOW_END)
        self.fresh = _records(self.fetch)

    def test_recovery_created_on_the_window_start_date_is_kept(self):
        # The incident: the recovery's attributed date is the window start's
        # date, the recoveries dataset is not empty, and WHOOP did not return
        # the recovery because its sleep ended before the window started.
        recovery = _family(self.stored, "recoveries", RECOVERY_101)
        self.assertEqual(len(recovery), 5, "the event and its four metrics")
        (event,) = [record for record in self.stored if record["id"] == "whoop-recovery-101"]
        self.assertEqual(event["date"], WINDOW_START[:10])
        self.assertEqual(self.fetch["recoveries"], [RECOVERY_102, RECOVERY_103])
        self.assertLess(_instant(SLEEP_101["end"]), _instant(WINDOW_START))

        self.assertEqual(_stale(self.stored, self.fresh) & recovery, set())

    def test_morning_workout_that_ended_before_the_window_start_is_kept(self):
        workout = _family(self.stored, "workouts", WORKOUT_MORNING)
        self.assertEqual(WORKOUT_MORNING["start"][:10], WINDOW_START[:10])
        self.assertEqual(self.fetch["workouts"], [WORKOUT_AFTERNOON])

        self.assertEqual(_stale(self.stored, self.fresh) & workout, set())

    def test_nap_that_ended_before_the_window_start_is_kept(self):
        nap = _family(self.stored, "sleeps", NAP_701)
        self.assertEqual(NAP_701["start"][:10], WINDOW_START[:10])
        self.assertNotIn(NAP_701, self.fetch["sleeps"])
        self.assertTrue(self.fetch["sleeps"])

        self.assertEqual(_stale(self.stored, self.fresh) & nap, set())

    def test_resync_of_unchanged_history_deletes_nothing(self):
        selected = _select(self.stored, self.fresh)

        self.assertTrue(_ids(self.fresh) <= selected, "every re-emitted id is rewritten")
        self.assertEqual(selected - _ids(self.fresh), set())


class StaleRecordTests(unittest.TestCase):
    """A record WHOOP no longer has goes, with its metrics, once the window proves it."""

    def test_stale_item_inside_the_window_is_purged_with_its_metrics(self):
        stored = _records(_merged(PROVIDER_HISTORY, STALE_ITEMS))
        fresh = _records(_overlap_fetch(PROVIDER_HISTORY, WINDOW_START, WINDOW_END))

        stale = _stale(stored, fresh)

        expected = set()
        for dataset, item in STALE_ITEMS.items():
            with self.subTest(dataset=dataset):
                family = _family(stored, dataset, item)
                self.assertGreater(len(family), 1, "the event and its metrics")
                self.assertTrue(family <= stale)
            expected |= _family(stored, dataset, item)
        self.assertEqual(stale, expected)

    def test_margin_edges_are_inclusive(self):
        on_start_edge = _workout(9301, _iso(_instant(INNER_START)), "2026-07-27T15:00:00.000Z")
        before_start_edge = _workout(9302, _shift(INNER_START, seconds=-1), "2026-07-27T15:30:00.000Z")
        on_end_edge = _workout(9303, "2026-07-29T03:00:00.000Z", _iso(_instant(INNER_END)))
        after_end_edge = _workout(9304, "2026-07-29T03:10:00.000Z", _shift(INNER_END, seconds=1))
        returned = WORKOUT_AFTERNOON
        stored = _records({"workouts": [on_start_edge, before_start_edge, on_end_edge, after_end_edge, returned]})
        fresh = _records({"workouts": [returned]})

        stale = _stale(stored, fresh)

        for item in (on_start_edge, on_end_edge):
            self.assertTrue(_family(stored, "workouts", item) <= stale, item["start"])
        for item in (before_start_edge, after_end_edge):
            self.assertEqual(_family(stored, "workouts", item) & stale, set(), item["start"])

    def test_window_shorter_than_both_margins_deletes_nothing_stale(self):
        stored = _records({"workouts": [WORKOUT_AFTERNOON]})

        stale = _stale(
            stored,
            _records({"workouts": [WORKOUT_MORNING]}),
            start="2026-07-28T06:00:00Z",
            end="2026-07-28T17:59:59Z",
        )

        self.assertEqual(stale, set())

    def test_window_within_the_margin_of_the_datetime_limits_proves_nothing_stale(self):
        # No representable instant lies the margin inside a window that opens
        # less than the margin before the latest instant (with or without an
        # earlier end), or closes less than the margin after the earliest:
        # nothing is interval-stale, and nothing overflows. A metric dropped by
        # a re-emitted parent still goes.
        stored = _records(_merged(PROVIDER_HISTORY, STALE_ITEMS))
        fresh = _records({"cycles": [dict(CYCLE_101, score={"strain": 10.4})]})
        for start, end in (
            ("9999-12-31T20:00:00Z", "9999-12-31T23:59:59Z"),
            ("9999-12-31T20:00:00Z", WINDOW_END),
            ("0001-01-01T00:00:00Z", "0001-01-01T05:00:00Z"),
        ):
            with self.subTest(start=start, end=end):
                plan = _plan(stored, fresh, start=start, end=end)

                self.assertEqual(set(plan.ids) - _ids(fresh), {"whoop-cycle-101-kilojoule"})
                self.assertEqual(plan.stale_purged, {"cycles": 1, "recoveries": 0, "sleeps": 0, "workouts": 0})
                self.assertEqual(plan.purge_suppressed, {})

    def test_start_time_and_end_time_stand_in_for_start_and_end(self):
        # The normalizers read start_time/end_time when start/end are absent,
        # and the interval does the same.
        legacy = _workout(9601, "2026-07-28T15:00:00.000Z", "2026-07-28T15:40:00.000Z")
        legacy["start_time"], legacy["end_time"] = legacy.pop("start"), legacy.pop("end")
        stored = _records({"workouts": [legacy, WORKOUT_AFTERNOON]})
        fresh = _records({"workouts": [WORKOUT_AFTERNOON]})

        self.assertEqual(_stale(stored, fresh), _family(stored, "workouts", legacy))

    def test_zero_length_interval_inside_the_window_is_stale(self):
        # Only an end before the start makes an interval unknown.
        instant = "2026-07-28T15:00:00.000Z"
        point = _workout(9701, instant, instant)
        stored = _records({"workouts": [point, WORKOUT_AFTERNOON]})
        fresh = _records({"workouts": [WORKOUT_AFTERNOON]})

        self.assertEqual(_stale(stored, fresh), _family(stored, "workouts", point))


class RecoveryResolutionTests(unittest.TestCase):
    """A recovery has no interval of its own: it borrows its sleep's, else its cycle's."""

    def test_resolves_through_this_runs_sleep_before_the_stored_one(self):
        # WHOOP revised sleep 160's start from 13:00 to 14:30: only this run's
        # version lies inside the margin window, so the recovery it lost is
        # stale. Sleep 161's end moved past the margin: only the stored
        # version lies inside, and the recovery is kept.
        stored_160 = _sleep(160, 160, "2026-07-27T13:00:00.000Z", "2026-07-27T20:00:00.000Z")
        fresh_160 = dict(stored_160, start="2026-07-27T14:30:00.000Z")
        stored_161 = _sleep(161, 161, "2026-07-29T00:00:00.000Z", "2026-07-29T03:30:00.000Z")
        fresh_161 = dict(stored_161, end="2026-07-29T05:00:00.000Z")
        recovery_160 = _recovery(160, 160, "2026-07-27T20:30:00.000Z")
        recovery_161 = _recovery(161, 161, "2026-07-29T03:45:00.000Z")
        stored = _records(
            {"sleeps": [stored_160, stored_161], "recoveries": [recovery_160, recovery_161, RECOVERY_102]}
        )
        fresh = _records({"sleeps": [fresh_160, fresh_161], "recoveries": [RECOVERY_102]})

        stale = _stale(stored, fresh)

        self.assertEqual(stale, _family(stored, "recoveries", recovery_160))

    def test_resolves_through_a_stored_sleep_only(self):
        inside = _sleep(170, 170, "2026-07-28T01:00:00.000Z", "2026-07-28T02:30:00.000Z")
        crossing = _sleep(171, 171, "2026-07-27T13:00:00.000Z", "2026-07-27T21:00:00.000Z")
        recovery_inside = _recovery(170, 170, "2026-07-28T02:45:00.000Z")
        recovery_crossing = _recovery(171, 171, "2026-07-28T12:00:00.000Z")
        stored = _records(
            {"sleeps": [inside, crossing], "recoveries": [recovery_inside, recovery_crossing, RECOVERY_102]}
        )
        # Neither sleep came back (WHOOP lost the first; the second is the
        # stored version only), so the recoveries resolve through the store.
        fresh = _records({"sleeps": [SLEEP_102], "recoveries": [RECOVERY_102]})

        stale = _stale(stored, fresh)

        self.assertTrue(_family(stored, "recoveries", recovery_inside) <= stale)
        self.assertEqual(_family(stored, "recoveries", recovery_crossing) & stale, set())

    def test_sleep_decides_before_the_cycle(self):
        # The newest day: the sleep has ended inside the margin window while
        # its cycle is still open. WHOOP returned the sleep but no longer the
        # recovery, so the recovery is stale; the open cycle must not veto it.
        sleep = _sleep(185, 185, "2026-07-28T21:30:00.000Z", "2026-07-29T03:30:00.000Z")
        cycle = _cycle(185, "2026-07-28T21:30:00.000Z", None)
        recovery = _recovery(185, 185, "2026-07-29T03:50:00.000Z")
        stored = _records({"cycles": [cycle], "sleeps": [sleep], "recoveries": [recovery, RECOVERY_102]})
        fresh = _records({"cycles": [cycle], "sleeps": [sleep], "recoveries": [RECOVERY_102]})

        self.assertEqual(_stale(stored, fresh), _family(stored, "recoveries", recovery))

    def test_falls_back_to_the_cycle_without_a_sleep(self):
        # Neither run knows sleeps 180/181; the recoveries resolve through
        # their cycles, whose intervals contain their sleeps'.
        cycle_inside = _cycle(180, "2026-07-27T16:00:00.000Z", "2026-07-28T15:00:00.000Z")
        cycle_crossing = _cycle(181, "2026-07-28T15:00:00.000Z", "2026-07-29T09:00:00.000Z")
        recovery_inside = _recovery(180, 180, "2026-07-28T01:00:00.000Z")
        recovery_crossing = _recovery(181, 181, "2026-07-28T23:00:00.000Z")
        stored = _records({"recoveries": [recovery_inside, recovery_crossing, RECOVERY_102]})
        fresh = _records({"cycles": [cycle_inside, cycle_crossing], "recoveries": [RECOVERY_102]})

        stale = _stale(stored, fresh)

        self.assertEqual(stale, _family(stored, "recoveries", recovery_inside))

    def test_unresolvable_recovery_is_kept(self):
        orphan = _recovery(190, 190, "2026-07-28T02:00:00.000Z")
        unlinked = dict(_recovery(191, 191, "2026-07-28T03:00:00.000Z"), sleep_id=None)
        stored = _records({"recoveries": [orphan, unlinked, RECOVERY_102]})
        fresh = _records({"recoveries": [RECOVERY_102]})

        self.assertEqual(_stale(stored, fresh), set())

    def test_known_sleep_with_an_unknown_interval_is_kept(self):
        # The sleep is known but has no end; its cycle lies inside the window.
        # A known link never falls back to a different interval.
        open_sleep = dict(_sleep(195, 195, "2026-07-28T01:00:00.000Z", None), end=None)
        cycle = _cycle(195, "2026-07-27T16:00:00.000Z", "2026-07-28T15:00:00.000Z")
        recovery = _recovery(195, 195, "2026-07-28T02:00:00.000Z")
        stored = _records({"recoveries": [recovery, RECOVERY_102]})
        fresh = _records({"cycles": [cycle], "sleeps": [open_sleep], "recoveries": [RECOVERY_102]})

        self.assertEqual(_stale(stored, fresh), set())


class TimestampParsingTests(unittest.TestCase):
    """Boundaries compare as instants; anything unparseable keeps the record."""

    def test_milliseconds_compare_as_instants_not_strings(self):
        # As strings ".500Z" sorts before "Z": a string comparison would keep
        # the first workout and delete the second.
        half_second_inside = _workout(9401, "2026-07-27T14:20:15.500Z", "2026-07-27T15:00:00.000Z")
        a_millisecond_late = _workout(9402, "2026-07-29T03:00:00.000Z", "2026-07-29T04:11:50.001Z")
        stored = _records({"workouts": [half_second_inside, a_millisecond_late, WORKOUT_AFTERNOON]})
        fresh = _records({"workouts": [WORKOUT_AFTERNOON]})

        stale = _stale(stored, fresh)

        self.assertTrue(_family(stored, "workouts", half_second_inside) <= stale)
        self.assertEqual(_family(stored, "workouts", a_millisecond_late) & stale, set())

    def test_offsets_compare_in_utc(self):
        # 00:00+02:00 is 22:00Z the day before: inside. 16:10+02:00 is 14:10Z,
        # before the margin window, although its local clock reads later.
        inside = _workout(9411, "2026-07-28T00:00:00.000+02:00", "2026-07-28T01:00:00.000+02:00")
        early = _workout(9412, "2026-07-27T16:10:00.000+02:00", "2026-07-27T17:00:00.000+02:00")
        stored = _records({"workouts": [inside, early, WORKOUT_AFTERNOON]})
        fresh = _records({"workouts": [WORKOUT_AFTERNOON]})

        stale = _stale(stored, fresh)

        self.assertTrue(_family(stored, "workouts", inside) <= stale)
        self.assertEqual(_family(stored, "workouts", early) & stale, set())

    def test_unusable_metadata_keeps_the_record(self):
        # One stored record per case, so the breaker (which would also keep a
        # crowd of them) never decides; the control case shows the harness
        # does delete a usable in-window record.
        inside = {"start": "2026-07-28T12:00:00.000Z", "end": "2026-07-28T12:30:00.000Z"}
        cases = [
            ("garbage-start", dict(inside, start="not-a-timestamp")),
            ("garbage-end", dict(inside, end="2026-07-28T25:61:00Z")),
            ("numeric", dict(inside, start=1785240000, end=1785241800)),
            ("none", dict(inside, start=None, end=None)),
            ("open", dict(inside, end=None)),
            ("missing", {}),
            ("date-only", dict(inside, start="2026-07-28", end="2026-07-28")),
            # A zone designator after a bare date still names no instant.
            ("date-zulu", dict(inside, start="2026-07-28Z")),
            ("date-offset", dict(inside, start="2026-07-28+02:00")),
            ("date-negative-offset", dict(inside, start="2026-07-28-04:00")),
            ("reversed", dict(inside, start="2026-07-28T12:30:00.000Z", end="2026-07-28T12:00:00.000Z")),
            ("overflow", dict(inside, start="9999-12-31T23:00:00-05:00")),
            ("list-boundary", dict(inside, start=["2026-07-28T12:00:00Z"])),
            ("text", "2026-07-28T12:00:00Z"),
            ("null", None),
            ("list", [inside]),
            ("absent", _NO_METADATA),
        ]
        recovery_cases = [
            ("garbage-links", {"cycle_id": {"id": 1}, "sleep_id": ["x"], "created_at": "2026-07-28T06:00:00Z"}),
            ("bool-links", {"cycle_id": True, "sleep_id": False, "created_at": "2026-07-28T06:00:00Z"}),
            ("no-links", {"created_at": "2026-07-28T06:00:00Z"}),
            ("text", "2026-07-28T12:00:00Z"),
            ("null", None),
            ("absent", _NO_METADATA),
        ]
        fresh = _records({"workouts": [WORKOUT_AFTERNOON], "recoveries": [RECOVERY_102], "sleeps": [SLEEP_102]})

        def stale_alone(record_id, kind, metadata):
            if isinstance(metadata, dict):
                metadata = dict(metadata, id=record_id)
            return _stale([_stored_event(record_id, kind, metadata)], fresh)

        self.assertEqual(stale_alone("whoop-workout-control", "whoop_workout", inside), {"whoop-workout-control"})
        for name, metadata in cases:
            with self.subTest(kind="whoop_workout", case=name):
                self.assertEqual(stale_alone("whoop-workout-%s" % name, "whoop_workout", metadata), set())
        for name, metadata in recovery_cases:
            with self.subTest(kind="whoop_recovery", case=name):
                self.assertEqual(stale_alone("whoop-recovery-%s" % name, "whoop_recovery", metadata), set())
        for name, metadata in cases:
            with self.subTest(kind="whoop_workout_metric", case=name):
                metric = dict(
                    _stored_event("whoop-workout-%s-strain" % name, None, metadata),
                    record_type="Observation",
                    observation_kind="whoop_workout_metric",
                )
                metric.pop("event_kind")
                self.assertEqual(_stale([metric], fresh), set())


class NeverPurgedTests(unittest.TestCase):
    def test_body_and_profile_records_are_never_selected(self):
        in_window = {"start": "2026-07-28T12:00:00.000Z", "end": "2026-07-28T12:30:00.000Z"}
        body = dict(
            _stored_event("whoop-body-weight-kilogram-2026-07-28", None, dict(in_window, weight_kilogram=75.0)),
            record_type="Observation",
            observation_kind="whoop_body_measurement",
            metric_name="weight_kilogram",
            value=75.0,
            unit="kg",
        )
        body.pop("event_kind")
        profile = dict(
            _stored_event("whoop-profile", None, dict(in_window, user_id=1001)),
            record_type="ContextNote",
            note_kind="whoop_profile",
        )
        profile.pop("event_kind")
        stored = [body, profile] + _records(PROVIDER_HISTORY)
        fresh = _records(_overlap_fetch(PROVIDER_HISTORY, WINDOW_START, WINDOW_END))

        selected = _select(stored, fresh)

        self.assertNotIn(body["id"], selected)
        self.assertNotIn(profile["id"], selected)

    def test_existing_records_may_be_a_one_shot_iterable(self):
        stored = _records(_merged(PROVIDER_HISTORY, STALE_ITEMS))
        fresh = _records(_overlap_fetch(PROVIDER_HISTORY, WINDOW_START, WINDOW_END))

        self.assertEqual(_select(iter(stored), fresh), _select(list(stored), fresh))
        self.assertTrue(_stale(iter(stored), fresh))


class ReemittedParentTests(unittest.TestCase):
    """A fresh item that no longer carries a metric retires that metric."""

    def test_metric_dropped_by_a_reemitted_parent_is_stale_whatever_its_interval(self):
        # Cycle 101 crosses the window start and cycle 103 is still open, so
        # neither interval lies inside the margin window. Both came back
        # without their kilojoule metric.
        stored = _records(PROVIDER_HISTORY)
        fresh = _records(
            {
                "cycles": [
                    dict(CYCLE_101, score={"strain": 10.4}),
                    CYCLE_102,
                    dict(CYCLE_103, score={"strain": 10.4}),
                ]
            }
        )

        stale = _stale(stored, fresh, datasets=["cycles"])

        self.assertEqual(stale, {"whoop-cycle-101-kilojoule", "whoop-cycle-103-kilojoule"})

    def test_metric_of_a_parent_that_was_not_reemitted_follows_the_interval_rule(self):
        stored = _records(PROVIDER_HISTORY)
        fresh = _records({"sleeps": [SLEEP_102, SLEEP_103]})

        stale = _stale(stored, fresh, datasets=["sleeps"])

        self.assertEqual(stale & _family(stored, "sleeps", SLEEP_101), set())

    def test_multi_word_metrics_dropped_by_reemitted_recoveries_and_sleeps_are_stale(self):
        # Sleep 103 ends after the margin window closes, so neither it nor its
        # recovery is interval-stale: only the re-emitted parent can retire
        # these metrics, whose names span several id segments.
        self.assertGreater(_instant(SLEEP_103["end"]), _instant(INNER_END))
        stored = _records(PROVIDER_HISTORY)
        no_skin_temp = _recovery(103, 103, RECOVERY_103["created_at"], skin_temp=False)
        no_respiratory_rate = dict(SLEEP_103, score={"sleep_performance_percentage": 87.0})
        rescored = {
            "recoveries": [RECOVERY_101, RECOVERY_102, no_skin_temp],
            "sleeps": [NAP_701, SLEEP_101, SLEEP_102, no_respiratory_rate],
        }
        fresh = _records(_overlap_fetch(dict(PROVIDER_HISTORY, **rescored), WINDOW_START, WINDOW_END))

        stale = _stale(stored, fresh)

        self.assertEqual(
            stale,
            {"whoop-recovery-103-skin-temp-celsius", "whoop-sleep-%s-respiratory-rate" % SLEEP_103["id"]},
        )

    def test_a_reemitted_id_that_prefixes_another_id_retires_none_of_its_metrics(self):
        # A past window re-emits cycle 101 without its kilojoule. Cycle 1010
        # starts after that window, so it is absent, not stale; its ids merely
        # begin with "whoop-cycle-101".
        later = _cycle(1010, "2026-07-29T10:00:00.000Z", "2026-07-30T09:00:00.000Z")
        stored = _records({"cycles": [CYCLE_101, later]})
        fresh = _records({"cycles": [dict(CYCLE_101, score={"strain": 10.4})]})

        stale = _stale(stored, fresh, start="2026-07-26T00:00:00Z", end="2026-07-28T00:00:00Z", datasets=["cycles"])

        self.assertEqual(stale, {"whoop-cycle-101-kilojoule"})
        self.assertTrue(_family(stored, "cycles", later).isdisjoint(stale))


def _workout_series(count, first_number=9100, first_start="2026-07-27T16:00:00.000Z", every_minutes=50):
    """Thirty-minute workouts, all inside the margin window for up to 41 items."""
    begin = _instant(first_start)
    series = []
    for offset in range(count):
        start = begin + timedelta(minutes=every_minutes * offset)
        series.append(_workout(first_number + offset, _iso(start), _iso(start + timedelta(minutes=30))))
    assert _instant(series[-1]["end"]) <= _instant(INNER_END)
    return series


def _hourly_series(dataset, count, first_number=200, first_start="2026-07-27T15:00:00.000Z"):
    """One-hour cycles or sleeps (each sleep in its own cycle), three hours
    apart, all inside the margin window for up to 13 items."""
    begin = _instant(first_start)
    series = []
    for offset in range(count):
        number, start = first_number + offset, begin + timedelta(hours=3 * offset)
        bounds = (_iso(start), _iso(start + timedelta(hours=1)))
        series.append(_cycle(number, *bounds) if dataset == "cycles" else _sleep(number, number, *bounds))
    assert _instant(series[-1]["end"]) <= _instant(INNER_END)
    return series


def _nap_series(count, first_number=400, first_start="2026-07-27T15:00:00.000Z", every_minutes=70):
    """Twenty-minute sleeps (each in its own cycle), all inside the margin
    window for up to 30 items: enough for an answer of a full page."""
    begin = _instant(first_start)
    series = []
    for offset in range(count):
        start = begin + timedelta(minutes=every_minutes * offset)
        number = first_number + offset
        series.append(_sleep(number, number, _iso(start), _iso(start + timedelta(minutes=20)), nap=True))
    assert _instant(series[-1]["end"]) <= _instant(INNER_END)
    return series


def _recoveries_of(sleeps):
    return [_recovery(sleep["cycle_id"], sleep["cycle_id"], _shift(sleep["end"], minutes=15)) for sleep in sleeps]


def _without_score(item, state="PENDING_SCORE"):
    """The item as WHOOP returns it while re-scoring it (or once it gives up):
    the event comes back, but with no score, so it normalizes to no metrics."""
    return dict(item, score_state=state, score=None)


class BreakerTests(unittest.TestCase):
    """Too many in-window events missing at once looks like a truncated answer.

    Answers here stay under a full page (25 items), where the old-edge guard
    stays unarmed, unless a test drops events from inside the answered range.
    """

    def _families(self, stored, items):
        return set().union(*(_family(stored, "workouts", item) for item in items))

    def test_breaker_allows_exactly_the_threshold(self):
        series = _workout_series(10)
        stored = _records({"workouts": series})
        fresh = _records({"workouts": series[3:]})

        plan = _plan(stored, fresh, datasets=["workouts"])

        self.assertEqual(set(plan.ids) - _ids(fresh), self._families(stored, series[:3]))
        self.assertEqual(plan.stale_purged, {"workouts": 9})
        self.assertEqual(plan.purge_suppressed, {})
        self.assertEqual(plan.notes, [])
        self.assertEqual(plan.ids, sorted(set(plan.ids)), "sorted, each id once")

    def test_breaker_trips_one_over_the_threshold_and_keeps_events_and_metrics(self):
        series = _workout_series(10)
        stored = _records({"workouts": series})
        fresh = _records({"workouts": series[4:]})

        plan = _plan(stored, fresh, datasets=["workouts"])

        self.assertEqual(set(plan.ids), _ids(fresh), "only re-emitted ids are replaced")
        self.assertEqual(plan.ids, sorted(plan.ids))
        self.assertEqual(plan.stale_purged, {"workouts": 0})
        self.assertEqual(plan.purge_suppressed, {"workouts": 4})
        self.assertEqual(
            plan.notes,
            [
                "Purge suppressed for workouts: 4 of 10 stored events inside the window were missing "
                "from the answer (limit 3); kept them and their metrics."
            ],
        )

    def test_breaker_trips_for_cycles_and_sleeps_too(self):
        for dataset in ("cycles", "sleeps"):
            with self.subTest(dataset=dataset):
                series = _hourly_series(dataset, 10)
                stored = _records({dataset: series})
                fresh = _records({dataset: series[4:]})

                plan = _plan(stored, fresh, datasets=[dataset])

                self.assertEqual(set(plan.ids), _ids(fresh), "every stored family is kept")
                self.assertEqual(plan.stale_purged, {dataset: 0})
                self.assertEqual(plan.purge_suppressed, {dataset: 4})

    def test_breaker_counts_recoveries_through_their_sleeps(self):
        # A recovery has no interval of its own: it lies in the margin window
        # exactly when its sleep does, and the breaker counts it there too.
        sleeps = _hourly_series("sleeps", 10)
        recoveries = [
            _recovery(sleep["cycle_id"], sleep["cycle_id"], _shift(sleep["end"], minutes=15)) for sleep in sleeps
        ]
        stored = _records({"sleeps": sleeps, "recoveries": recoveries})
        fresh = _records({"sleeps": sleeps, "recoveries": recoveries[4:]})

        plan = _plan(stored, fresh, datasets=["sleeps", "recoveries"])

        self.assertEqual(set(plan.ids), _ids(fresh), "every stored recovery family is kept")
        self.assertEqual(plan.stale_purged, {"recoveries": 0, "sleeps": 0})
        self.assertEqual(plan.purge_suppressed, {"recoveries": 4})

    def test_breaker_limit_is_exactly_ten_percent_of_forty_events(self):
        # 40 in-window events tolerate 4 missing, not 5: a larger percentage,
        # or a rounding that adds one at multiples of ten, would let a fifth
        # missing event go. The oldest events come back, so the missing ones
        # lie inside the answered range and the old-edge guard keeps none.
        series = _workout_series(40)
        stored = _records({"workouts": series})
        head = series[:PAST_THE_EDGE]

        at_limit = _plan(stored, _records({"workouts": head + series[PAST_THE_EDGE + 4 :]}), datasets=["workouts"])
        over_limit = _plan(stored, _records({"workouts": head + series[PAST_THE_EDGE + 5 :]}), datasets=["workouts"])

        self.assertEqual(at_limit.purge_suppressed, {})
        self.assertEqual(at_limit.stale_purged, {"workouts": 12})
        self.assertEqual(at_limit.old_edge_kept, {})
        self.assertEqual(over_limit.purge_suppressed, {"workouts": 5})
        self.assertEqual(over_limit.stale_purged, {"workouts": 0})

    def test_breaker_threshold_is_ten_percent_rounded_up(self):
        # 41 in-window events: the limit is ceil(4.1) = 5 stale events.
        series = _workout_series(41)
        stored = _records({"workouts": series})
        head = series[:PAST_THE_EDGE]

        at_limit = _plan(stored, _records({"workouts": head + series[PAST_THE_EDGE + 5 :]}), datasets=["workouts"])
        over_limit = _plan(stored, _records({"workouts": head + series[PAST_THE_EDGE + 6 :]}), datasets=["workouts"])

        self.assertEqual(at_limit.purge_suppressed, {})
        self.assertEqual(at_limit.stale_purged, {"workouts": 15})
        self.assertEqual(at_limit.old_edge_kept, {})
        self.assertEqual(over_limit.purge_suppressed, {"workouts": 6})
        self.assertEqual(over_limit.stale_purged, {"workouts": 0})

    def test_breaker_is_per_dataset_and_spares_reemitted_parent_metrics(self):
        series = _workout_series(10)
        stale_sleep = STALE_ITEMS["sleeps"]
        stored = _records({"workouts": series, "sleeps": [stale_sleep, SLEEP_102]})
        rescored = dict(series[9], score={"strain": 7.1})  # no kilojoule any more
        fresh = _records({"workouts": series[4:9] + [rescored], "sleeps": [SLEEP_102]})

        plan = _plan(stored, fresh, datasets=["workouts", "sleeps"])

        stale = set(plan.ids) - _ids(fresh)
        dropped_metric = "whoop-workout-%s-kilojoule" % rescored["id"]
        self.assertEqual(stale, {dropped_metric} | _family(stored, "sleeps", stale_sleep))
        self.assertEqual(plan.purge_suppressed, {"workouts": 4})
        self.assertEqual(plan.stale_purged, {"sleeps": 3, "workouts": 1})


def _old_edge_note(dataset, kept, answered):
    return (
        "Old edge kept for %s: %d stored events start before, or within 6 h after, the oldest of the %d "
        "items in the answer, which may have been cut short there; kept them and their metrics."
        % (dataset, kept, answered)
    )


def _old_edge_unknown_note(dataset, kept, answered):
    return (
        "Old edge kept for %s: none of the %d items in the answer has a known start and end, so %d stored "
        "events missing from it were kept with their metrics." % (dataset, answered, kept)
    )


def _unscored_note(dataset, kept):
    return (
        "Unscored events for %s: %d stored metrics belong to events that came back without any metric; "
        "kept them." % (dataset, kept)
    )


# _workout_series spaces items 50 minutes apart: the first item more than the
# 6-hour margin after a series' oldest start is the eighth after it.
PAST_THE_EDGE = 8


class OldEdgeGuardTests(unittest.TestCase):
    """WHOOP pages a collection newest first, so an answer cut short loses its
    oldest items. Once a dataset's answer fills a page, nothing that starts at
    or before its oldest returned item can be proven gone."""

    def _families(self, stored, dataset, items):
        return set().union(*(_family(stored, dataset, item) for item in items))

    def test_full_page_answer_cut_short_keeps_its_oldest_events(self):
        series = _workout_series(30)
        stored = _records({"workouts": series})
        fresh = _records({"workouts": series[3:]})

        plan = _plan(stored, fresh, datasets=["workouts"])

        self.assertEqual(set(plan.ids), _ids(fresh), "only re-emitted ids are replaced")
        self.assertEqual(plan.stale_purged, {"workouts": 0})
        self.assertEqual(plan.old_edge_kept, {"workouts": 3})
        self.assertEqual(plan.purge_suppressed, {})
        self.assertEqual(plan.notes, [_old_edge_note("workouts", 3, 27)])

    def test_events_missing_inside_the_answered_range_still_go(self):
        series = _workout_series(30)
        stored = _records({"workouts": series})
        lost = series[PAST_THE_EDGE + 1 : PAST_THE_EDGE + 3]
        fresh = _records({"workouts": series[: PAST_THE_EDGE + 1] + series[PAST_THE_EDGE + 3 :]})

        plan = _plan(stored, fresh, datasets=["workouts"])

        self.assertEqual(set(plan.ids) - _ids(fresh), self._families(stored, "workouts", lost))
        self.assertEqual(plan.old_edge_kept, {})
        self.assertEqual(plan.notes, [])

    def test_a_restoring_answer_sets_its_old_edge_with_items_the_index_lacks(self):
        # The index lost items 3-9 and holds 0-2 and 10-29. The answer brings
        # 3-29 back but lost its last page with 0-2: 27 items, 7 of them new.
        series = _workout_series(30)
        stored = _records({"workouts": series[:3] + series[10:]})
        fresh = _records({"workouts": series[3:]})

        plan = _plan(stored, fresh, datasets=["workouts"])

        self.assertEqual(set(plan.ids), _ids(fresh), "the new items are written, nothing goes")
        self.assertEqual(plan.old_edge_kept, {"workouts": 3})

    def test_each_dataset_arms_on_its_own_answer(self):
        # 24 workouts and 3 cycles: 27 events in all, but the workouts answer
        # is one page that was not full, so its oldest missing event is stale.
        series = _workout_series(25)
        stored = _records({"workouts": series, "cycles": PROVIDER_HISTORY["cycles"]})
        fresh = _records({"workouts": series[1:], "cycles": PROVIDER_HISTORY["cycles"]})

        plan = _plan(stored, fresh, datasets=["workouts", "cycles"])

        self.assertEqual(set(plan.ids) - _ids(fresh), _family(stored, "workouts", series[0]))
        self.assertEqual(plan.old_edge_kept, {})

    def test_history_outside_the_window_is_not_kept_at_the_old_edge(self):
        # The morning workout ended before the window opened: it lies outside
        # the margin window, so it is simply not in question, not "kept".
        series = _workout_series(30)
        stored = _records({"workouts": [WORKOUT_MORNING] + series})
        fresh = _records({"workouts": series})

        plan = _plan(stored, fresh, datasets=["workouts"])

        self.assertEqual(set(plan.ids), _ids(fresh))
        self.assertEqual(plan.old_edge_kept, {})
        self.assertEqual(plan.notes, [])

    def test_the_guard_arms_at_exactly_one_full_page(self):
        # 24 returned items: one page that was not full, so the answer reached
        # the end of the collection and its oldest missing event is stale.
        # 25 returned items: a full page, so a lost next page would look the same.
        under = _workout_series(25)
        stored_under = _records({"workouts": under})
        under_fresh = _records({"workouts": under[1:]})
        full = _workout_series(26)
        stored_full = _records({"workouts": full})

        under_plan = _plan(stored_under, under_fresh, datasets=["workouts"])
        full_plan = _plan(stored_full, _records({"workouts": full[1:]}), datasets=["workouts"])

        self.assertEqual(set(under_plan.ids) - _ids(under_fresh), _family(stored_under, "workouts", under[0]))
        self.assertEqual(under_plan.old_edge_kept, {})
        self.assertEqual(full_plan.stale_purged, {"workouts": 0})
        self.assertEqual(full_plan.old_edge_kept, {"workouts": 1})

    def test_an_event_starting_with_the_oldest_returned_item_is_kept(self):
        # Items sharing a start have no defined order: the twin may sit on the
        # lost page as well as on the returned one.
        series = _workout_series(30)
        twin = _workout(9990, series[3]["start"], series[3]["end"])
        stored = _records({"workouts": series + [twin]})
        fresh = _records({"workouts": series[3:]})

        plan = _plan(stored, fresh, datasets=["workouts"])

        self.assertEqual(set(plan.ids), _ids(fresh))
        self.assertEqual(plan.old_edge_kept, {"workouts": 4})

    def test_the_old_edge_runs_six_hours_past_the_oldest_returned_start(self):
        # WHOOP orders a page by start, and a stored start can lag a revised
        # one, so the edge reaches the margin past the oldest returned start
        # (inclusive), measured from the start, not the end.
        series = _workout_series(30)
        edge = _instant(series[0]["start"]) + timedelta(hours=6)
        at_edge = _workout(9981, _iso(edge), _iso(edge + timedelta(minutes=5)))
        past_edge = _workout(9982, _iso(edge + timedelta(seconds=1)), _iso(edge + timedelta(minutes=5)))
        self.assertLess(_instant(past_edge["start"]), _instant(series[0]["end"]) + timedelta(hours=6))
        stored = _records({"workouts": series + [at_edge, past_edge]})
        fresh = _records({"workouts": series})

        plan = _plan(stored, fresh, datasets=["workouts"])

        self.assertEqual(set(plan.ids) - _ids(fresh), _family(stored, "workouts", past_edge))
        self.assertEqual(plan.old_edge_kept, {"workouts": 1})

    def test_an_edge_past_the_latest_instant_keeps_without_overflowing(self):
        # Every returned item starts within the margin of the latest instant,
        # so the edge cannot be represented: everything stored lies before it.
        begin = _instant("9999-12-31T18:00:00Z")
        returned = []
        for offset in range(25):
            start = begin + timedelta(minutes=offset)
            returned.append(_workout(9500 + offset, _iso(start), _iso(start + timedelta(seconds=30))))
        missing = _workout(9499, "9999-12-31T17:00:00.000Z", "9999-12-31T17:30:00.000Z")
        stored = _records({"workouts": [missing] + returned})
        fresh = _records({"workouts": returned})

        plan = _plan(stored, fresh, start="9999-12-30T00:00:00Z", end="9999-12-31T23:59:59Z", datasets=["workouts"])

        self.assertEqual(set(plan.ids), _ids(fresh))
        self.assertEqual(plan.old_edge_kept, {"workouts": 1})

    def test_recoveries_find_their_old_edge_through_their_sleeps(self):
        sleeps = _nap_series(30)
        recoveries = _recoveries_of(sleeps)
        stored = _records({"sleeps": sleeps, "recoveries": recoveries})
        cut_short = _records({"sleeps": sleeps, "recoveries": recoveries[3:]})
        # Naps are 70 minutes apart: the seventh after the oldest lies past the
        # 6-hour edge, so recoveries 6-8 sit inside the answered range.
        self.assertGreater(_instant(sleeps[6]["start"]), _instant(sleeps[0]["start"]) + timedelta(hours=6))
        with_gap = _records({"sleeps": sleeps, "recoveries": recoveries[:6] + recoveries[9:]})

        cut_plan = _plan(stored, cut_short, datasets=["sleeps", "recoveries"])
        gap_plan = _plan(stored, with_gap, datasets=["sleeps", "recoveries"])

        self.assertEqual(set(cut_plan.ids), _ids(cut_short))
        self.assertEqual(cut_plan.old_edge_kept, {"recoveries": 3})
        self.assertEqual(set(gap_plan.ids) - _ids(with_gap), self._families(stored, "recoveries", recoveries[6:9]))
        self.assertEqual(gap_plan.old_edge_kept, {})
        self.assertEqual(gap_plan.purge_suppressed, {})

    def test_an_answer_with_no_known_start_proves_nothing_stale(self):
        # Every returned item has an open end, so the answer's old edge is
        # unknown: a missing stored event is kept, not judged against a guess.
        series = _workout_series(30)
        stored = _records({"workouts": series})
        fresh = _records({"workouts": [dict(item, end=None) for item in series[1:]]})

        plan = _plan(stored, fresh, datasets=["workouts"])

        self.assertEqual(set(plan.ids), _ids(fresh))
        self.assertEqual(plan.old_edge_kept, {"workouts": 1})
        self.assertEqual(plan.notes, [_old_edge_unknown_note("workouts", 1, 29)])

    def test_old_edge_events_still_count_toward_the_breaker(self):
        # Three events lost at the old edge and three inside the answered
        # range: six of 35 missing trip the breaker (limit 4) exactly as they
        # would without the guard, so nothing goes. Weighing only the three
        # inside the range would have let them go.
        series = _workout_series(35)
        stored = _records({"workouts": series})
        self.assertGreater(_instant(series[12]["start"]), _instant(series[3]["start"]) + timedelta(hours=6))
        fresh = _records({"workouts": series[3:12] + series[15:]})

        plan = _plan(stored, fresh, datasets=["workouts"])

        self.assertEqual(set(plan.ids), _ids(fresh), "nothing goes")
        self.assertEqual(plan.old_edge_kept, {"workouts": 3})
        self.assertEqual(plan.purge_suppressed, {"workouts": 6})
        self.assertEqual(
            plan.notes,
            [
                "Purge suppressed for workouts: 6 of 35 stored events inside the window were missing "
                "from the answer (limit 4); kept them and their metrics.",
                _old_edge_note("workouts", 3, 29),
            ],
        )


class UnscoredParentTests(unittest.TestCase):
    """An event that came back without any score says nothing about its old
    metrics: WHOOP is re-scoring it, or gave up, or the answer is degraded."""

    def _pending_history(self):
        return dict(
            PROVIDER_HISTORY,
            sleeps=[NAP_701, SLEEP_101, _without_score(SLEEP_102), SLEEP_103],
            recoveries=[RECOVERY_101, _without_score(RECOVERY_102), RECOVERY_103],
        )

    def test_metrics_of_events_caught_mid_rescore_are_kept(self):
        # Sleep 102 and its recovery lie inside the margin window, so neither
        # the re-emitted-parent rule nor the interval rule may take them.
        self.assertLessEqual(_instant(INNER_START), _instant(SLEEP_102["start"]))
        self.assertLessEqual(_instant(SLEEP_102["end"]), _instant(INNER_END))
        stored = _records(PROVIDER_HISTORY)
        fresh = _records(_overlap_fetch(self._pending_history(), WINDOW_START, WINDOW_END))
        sleep_metrics = _family(stored, "sleeps", SLEEP_102) - {"whoop-sleep-%s" % SLEEP_102["id"]}
        recovery_metrics = _family(stored, "recoveries", RECOVERY_102) - {"whoop-recovery-102"}
        self.assertEqual((len(sleep_metrics), len(recovery_metrics)), (2, 4))
        self.assertIn("whoop-sleep-%s" % SLEEP_102["id"], _ids(fresh), "the event itself is rewritten")

        plan = _plan(stored, fresh)

        self.assertEqual(set(plan.ids), _ids(fresh))
        self.assertEqual(plan.stale_purged, {"cycles": 0, "recoveries": 0, "sleeps": 0, "workouts": 0})
        self.assertEqual(plan.unscored_kept, {"recoveries": 4, "sleeps": 2})
        self.assertEqual(plan.notes, [_unscored_note("recoveries", 4), _unscored_note("sleeps", 2)])

    def test_unscorable_and_empty_scores_count_as_no_score(self):
        stored = _records({"workouts": [WORKOUT_AFTERNOON]})
        for state, score in (("UNSCORABLE", None), ("SCORED", {}), ("PENDING_SCORE", None)):
            with self.subTest(state=state, score=score):
                fresh = _records({"workouts": [dict(WORKOUT_AFTERNOON, score_state=state, score=score)]})

                plan = _plan(stored, fresh, datasets=["workouts"])

                self.assertEqual(set(plan.ids), _ids(fresh))
                self.assertEqual(plan.unscored_kept, {"workouts": 2})

    def test_a_score_without_any_metric_counts_as_no_score(self):
        # What counts is whether the event normalized to a metric, not the
        # score_state or a non-empty score object: a calibrating recovery
        # comes back SCORED with no metric value and retires nothing.
        stored = _records({"recoveries": [RECOVERY_102], "sleeps": [SLEEP_102]})
        calibrating = dict(RECOVERY_102, score_state="SCORED", score={"user_calibrating": True})
        fresh = _records({"recoveries": [calibrating], "sleeps": [SLEEP_102]})
        self.assertEqual(_ids(fresh) & _family(stored, "recoveries", RECOVERY_102), {"whoop-recovery-102"})

        plan = _plan(stored, fresh, datasets=["recoveries", "sleeps"])

        self.assertEqual(set(plan.ids), _ids(fresh))
        self.assertEqual(plan.unscored_kept, {"recoveries": 4})

    def test_a_scored_event_still_retires_the_metric_it_lost(self):
        stored = _records(PROVIDER_HISTORY)
        no_respiratory_rate = dict(SLEEP_102, score={"sleep_performance_percentage": 87.0})
        history = dict(self._pending_history(), sleeps=[NAP_701, SLEEP_101, no_respiratory_rate, SLEEP_103])
        fresh = _records(_overlap_fetch(history, WINDOW_START, WINDOW_END))

        plan = _plan(stored, fresh)

        self.assertEqual(set(plan.ids) - _ids(fresh), {"whoop-sleep-%s-respiratory-rate" % SLEEP_102["id"]})
        self.assertEqual(plan.unscored_kept, {"recoveries": 4})

    def test_a_degraded_answer_without_any_scores_deletes_nothing(self):
        stored = _records(PROVIDER_HISTORY)
        degraded = {dataset: [_without_score(item) for item in items] for dataset, items in PROVIDER_HISTORY.items()}
        fetch = _overlap_fetch(degraded, WINDOW_START, WINDOW_END)
        fresh = _records(fetch)
        expected = {
            dataset: sum(len(_family(stored, dataset, item)) - 1 for item in fetch[dataset])
            for dataset in ALL_DATASETS
        }

        plan = _plan(stored, fresh)

        self.assertEqual(set(plan.ids), _ids(fresh), "only the events are rewritten")
        self.assertEqual(sum(plan.stale_purged.values()), 0)
        self.assertEqual(plan.unscored_kept, expected)
        self.assertEqual(expected, {"cycles": 6, "recoveries": 8, "sleeps": 4, "workouts": 2})


class ClassificationGuardTests(unittest.TestCase):
    """A windowed kind without an interval rule would silently never be cleaned."""

    def _windowed_kinds(self):
        return {kind for kinds in whoop._WINDOWED_DATASET_KINDS.values() for kind in kinds}

    def test_every_windowed_kind_has_an_interval_rule(self):
        self.assertEqual(set(whoop._PURGE_INTERVAL_SOURCE), self._windowed_kinds())
        metric_kinds = {metric_kind for _, metric_kind in whoop._WINDOWED_DATASET_KINDS.values()}
        self.assertEqual(set(whoop._PURGE_PARENT_ID), metric_kinds)

    def test_every_windowed_kind_resolves_a_realistic_interval(self):
        stored = _records(_merged(STALE_ITEMS))
        self.assertEqual(
            {record.get("observation_kind") or record.get("event_kind") for record in stored},
            self._windowed_kinds(),
        )

        stale = _stale(stored, [])

        self.assertEqual(stale, _ids(stored))

    def test_parent_ids_match_the_normalizers(self):
        records = _records(_merged(PROVIDER_HISTORY, STALE_ITEMS))
        by_id = {record["id"]: record for record in records}
        checked = 0
        for event in records:
            for metric_id in event.get("related_record_ids") or []:
                metric = by_id[metric_id]
                self.assertEqual(
                    whoop._purge_parent_id(metric["observation_kind"], metric["metadata"]), event["id"]
                )
                checked += 1
        self.assertEqual(checked, 4 * 2 + 4 * 4 + 5 * 2 + 3 * 2, "every metric of every fixture item")

    def test_margin_is_six_hours(self):
        self.assertEqual(whoop.WHOOP_PURGE_MARGIN, timedelta(hours=6))

    def test_breaker_tolerates_three_events_or_ten_percent(self):
        self.assertEqual((whoop.WHOOP_PURGE_BREAKER_MIN_EVENTS, whoop.WHOOP_PURGE_BREAKER_PERCENT), (3, 10))

    def test_old_edge_guard_arms_at_the_page_size_the_client_requests(self):
        class _Recorder:
            def __init__(self):
                self.queries = []

            def _get(self, path, query=None):
                self.queries.append(dict(query or {}))
                return {"records": [], "next_token": None}

        recorder = _Recorder()
        whoop.WhoopClient._paginate(recorder, "/activity/workout", None, None)

        self.assertEqual(whoop.WHOOP_PAGE_LIMIT, 25)
        self.assertEqual(recorder.queries, [{"limit": whoop.WHOOP_PAGE_LIMIT}])


def _date_rule(existing_records, new_records, start, end, datasets):
    """The selector before this fix, kept as the erosion oracle: a record goes
    when its attributed date falls inside the window's dates."""
    allowed = {kind for name in datasets for kind in whoop._WINDOWED_DATASET_KINDS.get(name, ())}
    selected = {record["id"] for record in new_records}
    for record in existing_records:
        if record["id"] in selected or record.get("observation_kind") == "whoop_body_measurement":
            continue
        date = record.get("date") or record.get("start_date")
        if not (date and start[:10] <= date <= end[:10]):
            continue
        if (record.get("observation_kind") or record.get("event_kind")) in allowed:
            selected.add(record["id"])
    return sorted(selected)


def _daily_history(days, first_day="2026-07-01"):
    """Synthetic provider history, per day d (UTC): a sleep from 22:50 the
    evening before to 06:30, its cycle (until the next sleep) and recovery
    (created 07:45), a morning workout (07:00-07:35), an afternoon workout
    (12:30-13:20), and a nap (15:00-15:40) every third day."""
    base = _instant(first_day + "T00:00:00Z")
    history = {dataset: [] for dataset in ALL_DATASETS}
    for day_number in range(days):
        day = base + timedelta(days=day_number)
        cycle_id = sleep_number = 1000 + day_number
        sleep_start = _at(day, -1, -10)
        history["cycles"].append(_cycle(cycle_id, sleep_start, _at(day, 22, 50)))
        history["sleeps"].append(_sleep(sleep_number, cycle_id, sleep_start, _at(day, 6, 30)))
        history["recoveries"].append(_recovery(cycle_id, sleep_number, _at(day, 7, 45)))
        history["workouts"].append(_workout(5000 + 2 * day_number, _at(day, 7), _at(day, 7, 35)))
        history["workouts"].append(_workout(5001 + 2 * day_number, _at(day, 12, 30), _at(day, 13, 20)))
        if day_number % 3 == 0:
            history["sleeps"].append(_sleep(3000 + day_number, cycle_id, _at(day, 15), _at(day, 15, 40), nap=True))
    return history


def _at(day, hours, minutes=0):
    return _iso(day + timedelta(hours=hours, minutes=minutes))


def _visible(history, now, deleted_at):
    """The provider's history as of ``now``: finished items (a cycle is open
    until it ends), recoveries once created, minus items deleted by then."""
    visible = {}
    for dataset, items in history.items():
        shown = []
        for item in items:
            key = _EVENT_ID[dataset](item)
            if key in deleted_at and deleted_at[key] <= now:
                continue
            if dataset == "recoveries":
                if _instant(item["created_at"]) <= now:
                    shown.append(item)
            elif dataset == "cycles":
                if _instant(item["start"]) <= now:
                    shown.append(item if _instant(item["end"]) <= now else dict(item, end=None))
            elif _instant(item["end"]) <= now:
                shown.append(item)
        visible[dataset] = shown
    return visible


# Daily runs at 13:00, 18:00 and 08:00 local (+02:00) in turn: the next
# window then opens at 11:00, 16:00 or 06:00 UTC two days earlier.
_RUN_HOURS = (11, 16, 6)
_SIMULATED_DAYS = 21
_FIRST_DAY = _instant("2026-07-01T00:00:00Z")


def _deleted_afternoon_workout(day_number, deleted_on_day):
    """WHOOP deletes one day's afternoon workout at midnight of a later day."""
    event_id = "whoop-workout-%s" % _uuid(5001 + 2 * day_number)
    return {event_id: _FIRST_DAY + timedelta(days=deleted_on_day)}


def _sync_times(first_day=3, last_day=_SIMULATED_DAYS - 1):
    return [
        _FIRST_DAY + timedelta(days=day, hours=_RUN_HOURS[day % len(_RUN_HOURS)])
        for day in range(first_day, last_day + 1)
    ]


def _expected_event_ids(history, now, deleted_at):
    return {
        _EVENT_ID[dataset](item)
        for dataset, items in _visible(history, now, deleted_at).items()
        for item in items
    }


class RollingWindowSimulationTests(unittest.TestCase):
    """Daily scheduled windows (start = previous success - 2 days) over three weeks."""

    def _simulate(self, selector, deleted_at):
        history = _daily_history(_SIMULATED_DAYS)
        stored = {}
        ever_stored = set()
        last_success = None
        for now in _sync_times():
            start = _instant("2026-06-30T00:00:00Z") if last_success is None else last_success - timedelta(days=2)
            window_start, window_end = _window_iso(start), _window_iso(now)
            fetch = _overlap_fetch(_visible(history, now, deleted_at), window_start, window_end)
            fresh = _records(fetch, fetched_at=now.isoformat())
            datasets = [dataset for dataset in ALL_DATASETS if fetch[dataset]]
            for record_id in selector(list(stored.values()), fresh, window_start, window_end, datasets):
                stored.pop(record_id, None)
            stored.update((record["id"], record) for record in fresh)
            ever_stored.update(stored)
            last_success = now
        return history, set(stored), ever_stored

    def test_old_date_rule_erodes_and_the_interval_rule_never_does(self):
        deleted = _deleted_afternoon_workout(9, deleted_on_day=11)
        history, old, _ = self._simulate(_date_rule, deleted)
        _, new, ever_stored = self._simulate(_plan_ids, deleted)
        expected = _expected_event_ids(history, _sync_times()[-1], deleted)

        eroded = expected - old
        self.assertTrue(any(record_id.startswith("whoop-recovery-") for record_id in eroded), eroded)
        self.assertTrue(any(record_id.startswith("whoop-workout-") for record_id in eroded), eroded)
        self.assertEqual(expected - new, set(), "no item may erode at the window's leading edge")
        self.assertTrue(set(deleted) <= ever_stored)
        self.assertTrue(set(deleted).isdisjoint(new), "an item WHOOP deleted is still cleaned up")
        self.assertTrue(set(deleted).isdisjoint(old))

    def test_scheduled_syncs_end_to_end_keep_every_item(self):
        history = _daily_history(9)
        deleted = _deleted_afternoon_workout(4, deleted_on_day=6)
        client = _ProviderClient(history, deleted)
        times = _sync_times(first_day=3, last_day=8)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = ensure_repo_structure(root)
            index.init_db(paths.db_path)
            summaries = []
            for now in times:
                client.now = now
                with patch("openhealth.whoop.now_utc", return_value=now.isoformat()):
                    summaries.append(
                        sync_whoop(
                            root,
                            end=_window_iso(now),
                            days_back=5,
                            include_profile=False,
                            include_body_measurements=False,
                            client=client,
                        )
                    )
            remaining = _ids(index.list_records_by_source(paths.db_path, WHOOP_SOURCE_ID))
            windows = client.windows

        self.assertEqual(_expected_event_ids(history, times[-1], deleted) - remaining, set())
        self.assertTrue(set(deleted).isdisjoint(remaining))
        # Every scheduled window started two days before the previous run.
        starts = sorted({start for _, start, _ in windows})
        self.assertEqual(starts[1:], [_window_iso(at - timedelta(days=2)) for at in times[:-1]])
        # Day 6's run deletes the workout (event and two metrics) and nothing
        # else is ever stale.
        self.assertEqual([sum(summary["stale_purged"].values()) for summary in summaries], [0, 0, 0, 3, 0, 0])
        self.assertEqual(summaries[3]["stale_purged"]["workouts"], 3)
        self.assertEqual([summary["purge_suppressed"] for summary in summaries], [{}] * len(times))
        # Scheduled answers stay under a full page and come back scored, so
        # neither guard ever keeps anything in routine operation.
        self.assertEqual([summary["old_edge_kept"] for summary in summaries], [{}] * len(times))
        self.assertEqual([summary["unscored_kept"] for summary in summaries], [{}] * len(times))


class _CannedClient:
    """WhoopClient stand-in: one canned page per windowed dataset, whatever the window."""

    def __init__(self, fetch):
        self.fetch = fetch
        self.windows = []

    def _answer(self, dataset, start, end):
        return list(self.fetch.get(dataset) or [])

    def _pages(self, dataset, start, end):
        self.windows.append((dataset, start, end))
        return [{"records": self._answer(dataset, start, end), "next_token": None}]

    def list_cycles(self, start, end):
        return self._pages("cycles", start, end)

    def list_recoveries(self, start, end):
        return self._pages("recoveries", start, end)

    def list_sleeps(self, start, end):
        return self._pages("sleeps", start, end)

    def list_workouts(self, start, end):
        return self._pages("workouts", start, end)


class _ProviderClient(_CannedClient):
    """WhoopClient stand-in that answers like WHOOP, as of ``now``."""

    def __init__(self, history, deleted_at):
        super().__init__({})
        self.history = history
        self.deleted_at = deleted_at
        self.now = None

    def _answer(self, dataset, start, end):
        return _overlap_fetch(_visible(self.history, self.now, self.deleted_at), start, end)[dataset]


class SyncPurgeReportingTests(unittest.TestCase):
    """The sync applies the plan and reports it as counts."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = ensure_repo_structure(self.root)
        index.init_db(self.paths.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _sync(self, fetch, synced_at, **window):
        with patch("openhealth.whoop.now_utc", return_value=synced_at):
            return sync_whoop(
                self.root,
                include_profile=False,
                include_body_measurements=False,
                client=_CannedClient(fetch),
                **window,
            )

    def _stored(self):
        return index.list_records_by_source(self.paths.db_path, WHOOP_SOURCE_ID)

    def _store_history(self, fetch):
        self._sync(fetch, PREVIOUS_SYNC_AT, start="2026-07-20T00:00:00Z", end="2026-07-29T08:20:15Z")
        return self._stored()

    def _resync(self, fetch, **window):
        window = window or {"start": WINDOW_START, "end": WINDOW_END}
        return self._sync(fetch, FETCHED_AT, **window)

    def test_resync_reports_stale_purges_and_deletes_once(self):
        stale_workout = STALE_ITEMS["workouts"]
        before = self._store_history(_merged(PROVIDER_HISTORY, {"workouts": stale_workout}))
        fetch = _overlap_fetch(PROVIDER_HISTORY, WINDOW_START, WINDOW_END)

        with patch.object(index, "delete_records_by_ids", wraps=index.delete_records_by_ids) as delete:
            summary = self._resync(fetch)

        # The WHOOP window is the run's only delete: the plan's ids, once.
        (call,) = delete.call_args_list
        _, delete_ids = call.args
        gone = _family(before, "workouts", stale_workout)
        self.assertEqual(set(delete_ids), _ids(_records(fetch)) | gone)
        self.assertEqual(delete_ids, sorted(set(delete_ids)))
        self.assertEqual(summary["replaced_record_ids"], len(delete_ids))
        self.assertEqual(summary["stale_purged"], {"cycles": 0, "recoveries": 0, "sleeps": 0, "workouts": 3})
        self.assertEqual(summary["purge_suppressed"], {})
        self.assertEqual(summary["old_edge_kept"], {})
        self.assertEqual(summary["unscored_kept"], {})
        self.assertEqual(_ids(self._stored()), _ids(before) - gone)
        for dataset, item in (("recoveries", RECOVERY_101), ("workouts", WORKOUT_MORNING), ("sleeps", NAP_701)):
            self.assertTrue(_family(before, dataset, item) <= _ids(self._stored()), dataset)

    def test_purge_existing_whoop_records_deletes_exactly_the_plan(self):
        # The direct-caller entry point applies the same plan and returns the
        # ids it deleted, sorted.
        stale_workout = STALE_ITEMS["workouts"]
        before = self._store_history(_merged(PROVIDER_HISTORY, {"workouts": stale_workout}))
        fresh = _records(_overlap_fetch(PROVIDER_HISTORY, WINDOW_START, WINDOW_END))
        planned = _plan_ids(before, fresh, WINDOW_START, WINDOW_END, list(ALL_DATASETS))

        deleted = whoop.purge_existing_whoop_records(
            self.paths.db_path, fresh, WINDOW_START, WINDOW_END, list(ALL_DATASETS)
        )

        self.assertEqual(deleted, planned)
        self.assertEqual(deleted, sorted(set(deleted)))
        self.assertEqual(set(deleted) - _ids(fresh), _family(before, "workouts", stale_workout))
        self.assertEqual(_ids(self._stored()), _ids(before) - set(deleted))

    def test_truncated_answer_trips_the_breaker_through_the_sync(self):
        series = _workout_series(12)
        before = self._store_history({"workouts": series})

        summary = self._resync({"workouts": series[:2]})

        self.assertEqual(summary["purge_suppressed"], {"workouts": 10})
        self.assertEqual(summary["stale_purged"], {"workouts": 0})
        self.assertEqual(_ids(self._stored()), _ids(before))
        manifest = json.loads(
            (self.paths.source_manifests / ("%s.json" % WHOOP_SOURCE_ID)).read_text(encoding="utf-8")
        )
        self.assertIn(
            "Purge suppressed for workouts: 10 of 12 stored events inside the window were missing "
            "from the answer (limit 3); kept them and their metrics.",
            manifest["notes"],
        )

    def test_cut_short_full_page_answer_keeps_the_old_edge_through_the_sync(self):
        series = _workout_series(30)
        before = self._store_history({"workouts": series})

        summary = self._resync({"workouts": series[3:]})

        self.assertEqual(summary["old_edge_kept"], {"workouts": 3})
        self.assertEqual(summary["stale_purged"], {"workouts": 0})
        self.assertEqual(summary["purge_suppressed"], {})
        self.assertEqual(summary["unscored_kept"], {})
        self.assertEqual(_ids(self._stored()), _ids(before))
        manifest = json.loads(
            (self.paths.source_manifests / ("%s.json" % WHOOP_SOURCE_ID)).read_text(encoding="utf-8")
        )
        self.assertIn(_old_edge_note("workouts", 3, 27), manifest["notes"])

    def test_events_caught_mid_rescore_keep_their_metrics_through_the_sync(self):
        before = self._store_history(PROVIDER_HISTORY)
        pending = dict(
            PROVIDER_HISTORY,
            sleeps=[NAP_701, SLEEP_101, _without_score(SLEEP_102), SLEEP_103],
            recoveries=[RECOVERY_101, _without_score(RECOVERY_102), RECOVERY_103],
        )

        summary = self._resync(_overlap_fetch(pending, WINDOW_START, WINDOW_END))

        stored = {record["id"]: record for record in self._stored()}
        self.assertEqual(set(stored), _ids(before))
        self.assertEqual(stored["whoop-recovery-102"]["metadata"]["score_state"], "PENDING_SCORE")
        self.assertEqual(stored["whoop-recovery-102-recovery-score"]["value"], 58.0)
        self.assertEqual(summary["unscored_kept"], {"recoveries": 4, "sleeps": 2})
        self.assertEqual(summary["stale_purged"], {"cycles": 0, "recoveries": 0, "sleeps": 0, "workouts": 0})
        self.assertEqual(summary["old_edge_kept"], {})
        manifest = json.loads(
            (self.paths.source_manifests / ("%s.json" % WHOOP_SOURCE_ID)).read_text(encoding="utf-8")
        )
        for note in (_unscored_note("recoveries", 4), _unscored_note("sleeps", 2)):
            self.assertIn(note, manifest["notes"])

    def test_start_within_the_margin_of_the_latest_instant_completes_the_sync(self):
        # The window resolver accepts any parseable --start. WHOOP would refuse
        # this window, but the plan must never fail a run after its fetch.
        before = self._store_history(PROVIDER_HISTORY)

        summary = self._resync(_overlap_fetch(PROVIDER_HISTORY, WINDOW_START, WINDOW_END), start="9999-12-31T20:00:00Z")

        self.assertEqual(summary["stale_purged"], {"cycles": 0, "recoveries": 0, "sleeps": 0, "workouts": 0})
        self.assertEqual(summary["purge_suppressed"], {})
        self.assertEqual(_ids(self._stored()), _ids(before))

    def test_reemitted_parent_keeps_its_metrics_and_drops_the_missing_one(self):
        self._store_history(PROVIDER_HISTORY)
        no_skin_temp = _recovery(102, 102, RECOVERY_102["created_at"], skin_temp=False)
        rescored = {
            "cycles": [dict(CYCLE_101, score={"strain": 12.0})] + PROVIDER_HISTORY["cycles"][1:],
            "recoveries": [RECOVERY_101, no_skin_temp, RECOVERY_103],
            "workouts": [WORKOUT_MORNING, dict(WORKOUT_AFTERNOON, score={"strain": 7.1})],
        }

        summary = self._resync(_overlap_fetch(dict(PROVIDER_HISTORY, **rescored), WINDOW_START, WINDOW_END))

        stored = {record["id"]: record for record in self._stored()}
        afternoon = WORKOUT_AFTERNOON["id"]
        for dropped in (
            "whoop-cycle-101-kilojoule",
            "whoop-recovery-102-skin-temp-celsius",
            "whoop-workout-%s-kilojoule" % afternoon,
        ):
            self.assertNotIn(dropped, stored)
        self.assertEqual(stored["whoop-cycle-101-strain"]["value"], 12.0)
        for kept in ("whoop-recovery-102-recovery-score", "whoop-workout-%s-strain" % afternoon):
            self.assertIn(kept, stored)
        self.assertEqual(summary["stale_purged"], {"cycles": 1, "recoveries": 1, "sleeps": 0, "workouts": 1})

    def test_backfill_from_an_earlier_start_restores_eroded_items(self):
        # The index lost each day's morning recovery and workout to the old
        # date rule. One explicit window from before the gap brings them back,
        # deletes nothing, and leaves every dataset's breaker clear.
        history = _daily_history(12)
        eroded = {
            "recoveries": history["recoveries"][3:8],
            "workouts": [item for item in history["workouts"][6:16] if item["start"][11:13] == "07"],
        }
        damaged = {
            dataset: [item for item in items if item not in eroded.get(dataset, [])]
            for dataset, items in history.items()
        }
        self.assertEqual(len(eroded["workouts"]), 5)
        before = self._store_history(damaged)

        summary = self._resync(history, start="2026-06-30T00:00:00Z", end="2026-07-13T00:00:00Z")

        after = _ids(self._stored())
        restored = _records(eroded)
        self.assertEqual(after, _ids(before) | _ids(restored))
        self.assertEqual(len(_ids(restored)), 5 * 5 + 5 * 3)
        self.assertEqual(summary["stale_purged"], {"cycles": 0, "recoveries": 0, "sleeps": 0, "workouts": 0})
        self.assertEqual(summary["purge_suppressed"], {})
        self.assertEqual(summary["old_edge_kept"], {})
        self.assertEqual(summary["unscored_kept"], {})

    def test_explicit_past_end_keeps_records_reaching_past_the_margin(self):
        # A backfill ending at 2026-07-28T00:00:00Z: its margin window closes
        # at 2026-07-27T18:00:00Z. Whatever WHOOP's end filter does, a record
        # that reaches past that point is kept.
        straddling = _workout(9201, "2026-07-27T23:30:00.000Z", "2026-07-28T00:30:00.000Z")
        evening = _workout(9202, "2026-07-27T17:20:00.000Z", "2026-07-27T18:20:00.000Z")
        deleted = _workout(9203, "2026-07-27T12:00:00.000Z", "2026-07-27T12:45:00.000Z")
        returned = _workout(9204, "2026-07-27T09:00:00.000Z", "2026-07-27T09:40:00.000Z")
        before = self._store_history({"workouts": [straddling, evening, deleted, returned]})

        summary = self._resync({"workouts": [returned]}, start="2026-07-26T00:00:00Z", end="2026-07-28T00:00:00Z")

        remaining = _ids(self._stored())
        for item in (straddling, evening):
            self.assertTrue(_family(before, "workouts", item) <= remaining, item["end"])
        self.assertEqual(_family(before, "workouts", deleted) & remaining, set())
        self.assertEqual(summary["stale_purged"], {"workouts": 3})


if __name__ == "__main__":
    unittest.main()
