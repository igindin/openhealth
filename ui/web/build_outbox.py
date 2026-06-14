"""Build the iOS HealthSnapshot outbox from the engine (Result 2, Mac side).

Reuses the dashboard builders and reshapes them into the exact JSON the iOS
`HealthSnapshot` model decodes - measurements[], trends[], insights[], alerts[] -
then writes it atomically to the iCloud bridge `outbox/snapshot.json`.

Primary source is the engine's WHOOP recovery (rMSSD). When no WHOOP data is
present it falls back to the Apple Health bridge data ingested from the inbox
(HRV=SDNN, resting HR, steps), so the phone still sees Mac-served, engine-graded
output instead of only its on-device estimate.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from build_dashboard_data import (
    build_correlations_block,
    build_insights_block,
    build_recovery_block,
    _load_whoop_by_date,
)

APPLE_SOURCE_ID = "apple-health-bridge"

# C1..C5 -> the numeric confidence the iOS `Confidence(numeric:)` initializer buckets.
_CONFIDENCE = {"C1": 0.1, "C2": 0.3, "C3": 0.45, "C4": 0.7, "C5": 0.9}


# --- WHOOP path (primary) ----------------------------------------------------

def _measurements(rec: dict) -> list[dict]:
    out: list[dict] = []

    def add(metric: str, title: str, value, caption: str) -> None:
        if value is not None:
            out.append({"metric": metric, "title": title, "value": value, "caption": caption})

    r = rec.get("recovery")
    add("recovery", "Recovery", f"{r}%" if r is not None else None, "WHOOP")
    s = rec.get("strain")
    add("strain", "Strain", f"{s}" if s is not None else None, "of 21")
    h = rec.get("hrv")
    add("hrv", "HRV", f"{h} ms" if h is not None else None, "rMSSD")
    rh = rec.get("rhr")
    add("resting_hr", "Resting HR", f"{rh} bpm" if rh is not None else None, "latest")
    sl = rec.get("sleep")
    add("sleep", "Sleep", f"{sl} h" if sl is not None else None, "last night")
    return out


def _trend(by_date: dict, dates: list[str], keys: list[str], metric: str, title: str, unit: str):
    points = []
    for d in dates:
        row = by_date.get(d, {})
        value = next((row[k] for k in keys if row.get(k) is not None), None)
        if value is not None:
            points.append({"date": d[5:], "value": round(value, 1)})
    if not points:
        return None
    return {"metric": metric, "title": title, "unit": unit,
            "reference_low": None, "reference_high": None, "points": points}


def _whoop_trends(con: sqlite3.Connection) -> list[dict]:
    by_date = _load_whoop_by_date(con)
    dates = sorted(by_date)[-30:]
    candidates = [
        _trend(by_date, dates, ["recovery_score"], "recovery", "Recovery", "%"),
        _trend(by_date, dates, ["hrv_rmssd_milli", "hrv_rmssd"], "hrv", "HRV (rMSSD)", "ms"),
        _trend(by_date, dates, ["resting_heart_rate"], "resting_hr", "Resting HR", "bpm"),
    ]
    return [t for t in candidates if t]


# --- Apple Health bridge path (fallback) -------------------------------------

def _apple_daily(con: sqlite3.Connection) -> dict[str, dict]:
    """Daily aggregates of the ingested Apple bridge Observations, computed in
    SQL (so it scales to a year of per-minute heart-rate samples)."""
    rows = con.execute(
        "SELECT date AS d, "
        "json_extract(payload_json,'$.metric_name') AS mn, "
        "AVG(json_extract(payload_json,'$.value')) AS avg_v, "
        "SUM(json_extract(payload_json,'$.value')) AS sum_v "
        "FROM records "
        "WHERE source_id = ? AND record_type='Observation' AND date IS NOT NULL "
        "GROUP BY d, mn",
        (APPLE_SOURCE_ID,),
    ).fetchall()
    daily: dict[str, dict] = {}
    for row in rows:
        d, mn = row["d"], row["mn"]
        if not d or mn is None:
            continue
        bucket = daily.setdefault(d, {})
        if mn == "heart_rate_variability_sdnn" and row["avg_v"] is not None:
            bucket["hrv"] = round(row["avg_v"], 1)
        elif mn == "resting_heart_rate" and row["avg_v"] is not None:
            bucket["rhr"] = round(row["avg_v"])
        elif mn == "step_count" and row["sum_v"] is not None:
            bucket["steps"] = round(row["sum_v"])
    return daily


def _latest(daily: dict, dates: list[str], key):
    for d in reversed(dates):
        if daily[d].get(key) is not None:
            return daily[d][key]
    return None


def _apple_measurements(daily: dict) -> list[dict]:
    if not daily:
        return []
    dates = sorted(daily)
    out: list[dict] = []
    hrv = _latest(daily, dates, "hrv")
    if hrv is not None:
        out.append({"metric": "hrv", "title": "HRV", "value": f"{hrv} ms", "caption": "SDNN"})
    rhr = _latest(daily, dates, "rhr")
    if rhr is not None:
        out.append({"metric": "resting_hr", "title": "Resting HR", "value": f"{rhr} bpm", "caption": "latest"})
    steps = daily[dates[-1]].get("steps")
    if steps is not None:
        out.append({"metric": "steps", "title": "Steps", "value": f"{steps}", "caption": "latest day"})
    return out


def _apple_trends(daily: dict) -> list[dict]:
    dates = sorted(daily)[-30:]
    out = []
    for key, metric, title, unit in [("hrv", "hrv", "HRV (SDNN)", "ms"),
                                     ("rhr", "resting_hr", "Resting HR", "bpm")]:
        points = [{"date": d[5:], "value": daily[d][key]} for d in dates if daily[d].get(key) is not None]
        if points:
            out.append({"metric": metric, "title": title, "unit": unit,
                        "reference_low": None, "reference_high": None, "points": points})
    return out


# --- insight mapping (shared) ------------------------------------------------

def _map_insights(raw: list[dict]):
    insights: list[dict] = []
    alerts: list[dict] = []
    for item in raw:
        question = item.get("question_ru")
        insights.append({
            "id": item.get("id", ""),
            "title": item.get("title_ru", ""),
            "statement": item.get("evidence_text", ""),
            "confidence": _CONFIDENCE.get(str(item.get("confidence", "C1")).upper(), 0.1),
            "open_questions": [question] if question else [],
            "suggested_validation": item.get("action_ru"),
            "sources": item.get("refs") or [],
        })
        if item.get("severity") == "warning":
            alerts.append({
                "id": item.get("id", ""),
                "title": item.get("title_ru", ""),
                "message": item.get("action_ru") or item.get("evidence_text", ""),
                "urgency": "urgent",
            })
    return insights, alerts


def _apple_insights(daily: dict):
    if not daily:
        return [], []
    try:
        from openhealth.insights import detect_insights, insights_to_dicts
        found = detect_insights(daily, {"sleep_h": 8.0})
        return _map_insights(insights_to_dicts(found))
    except Exception:
        return [], []


# --- correlations ("what affects HRV") ---------------------------------------

def _correlations(con: sqlite3.Connection) -> list[dict]:
    block = build_correlations_block(con)
    if block.get("status") != "ok":
        return []
    out = []
    for c in block.get("correlations", []):
        out.append({
            "id": c.get("id") or "",
            "label": c.get("label") or "",
            "delta": c.get("delta"),
            "dir": c.get("dir") or "up",
            "grade": c.get("grade") or "C2",
        })
    return out


# --- snapshot assembly -------------------------------------------------------

def build_ios_snapshot(con: sqlite3.Connection) -> dict:
    rec = build_recovery_block(con) or {}
    measurements = _measurements(rec)
    trends = _whoop_trends(con)
    insights, alerts = _map_insights(build_insights_block(con).get("insights", []))
    source = "whoop"

    if not measurements and not trends:
        daily = _apple_daily(con)
        measurements = _apple_measurements(daily)
        trends = _apple_trends(daily)
        if not insights:
            insights, alerts = _apple_insights(daily)
        source = "apple_health"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": source,
        "greeting_name": "there",
        "measurements": measurements,
        "panels": [],
        "trends": trends,
        "insights": insights,
        "alerts": alerts,
        "correlations": _correlations(con),
    }


def write_ios_outbox(db_path: Path, outbox_dir: Path) -> Path:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        snapshot = build_ios_snapshot(con)
    finally:
        con.close()
    outbox_dir = Path(outbox_dir)
    outbox_dir.mkdir(parents=True, exist_ok=True)
    out = outbox_dir / "snapshot.json"
    tmp = outbox_dir / "snapshot.json.tmp"
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    return out


def default_outbox_dir() -> Path:
    return Path("~/Library/Mobile Documents/iCloud~org~openhealth~app/Documents/outbox").expanduser()


def main() -> None:
    parser = argparse.ArgumentParser(description="Write the iOS HealthSnapshot outbox.")
    parser.add_argument("--db", default="data/index/health_os.sqlite3")
    parser.add_argument("--out", default=None, help="outbox dir (defaults to the iCloud bridge)")
    args = parser.parse_args()
    out_dir = Path(args.out) if args.out else default_outbox_dir()
    print(f"wrote {write_ios_outbox(Path(args.db), out_dir)}")


if __name__ == "__main__":
    main()
