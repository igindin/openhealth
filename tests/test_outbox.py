import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ui" / "web"))

from openhealth import index  # noqa: E402
import build_outbox  # noqa: E402


def _obs(db: Path, source_id: str, metric: str, value, day: str, idx: int = 0) -> None:
    index.upsert_record(db, {
        "id": f"{source_id}-{metric}-{day}-{idx}",
        "record_type": "Observation",
        "source_id": source_id,
        "evidence_class": "personal",
        "date": day,
        "title": metric,
        "summary": "",
        "artifact_ids": [],
        "confidence": 0.95,
        "metric_name": metric,
        "value": value,
        "unit": None,
        "observation_kind": "signal",
    })


def _read(out: Path) -> dict:
    return json.loads(out.read_text(encoding="utf-8"))


def test_outbox_matches_ios_snapshot_shape_from_whoop(tmp_path):
    db = tmp_path / "db.sqlite3"
    index.init_db(db)
    for i, day in enumerate(["2026-06-10", "2026-06-11", "2026-06-12"]):
        _obs(db, "whoop-live", "recovery_score", 60 + i, day)
        _obs(db, "whoop-live", "hrv_rmssd_milli", 80 + i, day)
        _obs(db, "whoop-live", "resting_heart_rate", 55 - i, day)

    snap = _read(build_outbox.write_ios_outbox(db, tmp_path / "outbox"))

    for key in ("greeting_name", "measurements", "panels", "trends", "insights", "alerts", "correlations"):
        assert key in snap
    assert isinstance(snap["correlations"], list)
    assert snap["source"] == "whoop"

    recovery = next(m for m in snap["measurements"] if m["metric"] == "recovery")
    assert set(recovery) == {"metric", "title", "value", "caption"}
    assert recovery["value"].endswith("%")

    hrv_trend = next(t for t in snap["trends"] if t["metric"] == "hrv")
    assert hrv_trend["points"]
    assert set(hrv_trend["points"][0]) == {"date", "value"}


def test_outbox_falls_back_to_apple_when_no_whoop(tmp_path):
    db = tmp_path / "db.sqlite3"
    index.init_db(db)
    for i, day in enumerate(["2026-06-10", "2026-06-11", "2026-06-12"]):
        # Multiple SDNN samples per day -> exercises the SQL daily average.
        _obs(db, "apple-health-bridge", "heart_rate_variability_sdnn", 40 + i, day, idx=0)
        _obs(db, "apple-health-bridge", "heart_rate_variability_sdnn", 44 + i, day, idx=1)
        _obs(db, "apple-health-bridge", "resting_heart_rate", 56 - i, day)
        _obs(db, "apple-health-bridge", "step_count", 1000, day, idx=0)
        _obs(db, "apple-health-bridge", "step_count", 1500, day, idx=1)

    snap = _read(build_outbox.write_ios_outbox(db, tmp_path / "outbox"))

    assert snap["source"] == "apple_health"
    metrics = {m["metric"]: m for m in snap["measurements"]}
    assert "hrv" in metrics
    assert metrics["hrv"]["caption"] == "SDNN"
    assert "steps" in metrics
    hrv_trend = next(t for t in snap["trends"] if t["metric"] == "hrv")
    assert len(hrv_trend["points"]) == 3
