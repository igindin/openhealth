"""Time-lagged correlations (Ideabrowser signal): recovery is a morning metric,
so an evening behaviour on day D shows up in the NEXT morning's recovery. The
tunable `correlations.lag_days` pairs behaviour day D with recovery day D+lag."""

import openhealth.index as index
import openhealth.modules.correlations as corr


def _obs(rid, kind, date, value, **meta):
    rec = {
        "id": rid, "source_id": "test", "record_type": "Observation",
        "evidence_class": "personal", "date": date,
        "observation_kind": kind, "value": value,
        "metric_name": meta.pop("metric_name", rid),
    }
    if meta:
        rec["metadata"] = meta
    return rec


def _recovery_of(behaviors, bid):
    for b in behaviors:
        if b["behavior_id"] == bid and b["pairs"]:
            return b["pairs"][0]["recovery"], b.get("lag_days")
    return None, None


def _build(tmp_path):
    db = tmp_path / "idx.sqlite3"
    index.init_db(db)
    # recovery: day D = 50 (from the night BEFORE the behaviour), D+1 = 80.
    index.upsert_record(db, _obs("rec-1", "recovery_score", "2026-05-10", 50.0))
    index.upsert_record(db, _obs("rec-2", "recovery_score", "2026-05-11", 80.0))
    # an evening behaviour logged on day D.
    index.upsert_record(db, _obs("beh-1", "journal_entry", "2026-05-10", True,
                                 behavior_id="alcohol", category="lifestyle"))
    return db


def test_lag0_pairs_same_day(tmp_path):
    db = _build(tmp_path)
    rec, lag = _recovery_of(corr.from_index(db, window_days=90, as_of="2026-05-20", lag_days=0), "alcohol")
    assert rec == 50.0 and lag == 0


def test_lag1_pairs_next_morning(tmp_path):
    db = _build(tmp_path)
    rec, lag = _recovery_of(corr.from_index(db, window_days=90, as_of="2026-05-20", lag_days=1), "alcohol")
    assert rec == 80.0 and lag == 1  # behaviour D -> recovery D+1


def test_lag_recorded_in_analyze_trace(tmp_path):
    # analyze() carries the lag into the "how computed" trace for the UI tooltip.
    behaviors = [{"behavior_id": "alcohol", "category": "lifestyle", "lag_days": 1,
                  "pairs": [{"date": "2026-05-%02d" % d, "yes": d % 2 == 0, "recovery": 70.0 if d % 2 == 0 else 55.0}
                            for d in range(1, 13)]}]
    insights = corr.analyze(behaviors, window_days=90)
    assert insights, "expected an actionable insight from the alternating pairs"
    assert insights[0]["metadata"]["lag_days"] == 1
    assert insights[0]["metadata"]["compute_trace"]["lag_days"] == 1
