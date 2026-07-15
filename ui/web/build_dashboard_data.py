#!/usr/bin/env python3
"""Bridge: real Health OS data -> dashboard data.local.json.

Reads the local Health OS SQLite index (sources / artifacts / records) and emits a
JSON object shaped exactly like the in-page `DATA` object in dashboard.html.

The dashboard loads data.local.json at runtime; if present it renders real data,
otherwise it falls back to the bundled demo DATA. This script does NOT modify the
dashboard and writes only a local, git-ignored file.

PRIVACY: the output (data.local.json) contains real personal health data. It must
stay local and is git-ignored. This script (no data) is safe to commit.

Usage:
    python3 build_dashboard_data.py \
        --db ~/health-os/data/index/health_os.sqlite3 \
        --out ui/web/data.local.json

Defaults assume the Health OS repo lives at ~/health-os.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

# Recovery zone thresholds match the dashboard's col()/word() helpers.
GREEN, YELLOW = 67, 34


def _load_recovery_by_date(con: sqlite3.Connection) -> dict[str, dict[str, float]]:
    """metric_name -> value, grouped by ISO date, for the recovery sources.

    Reads the live wearable sources that share the recovery metric vocabulary
    (recovery_score / hrv_rmssd_milli / resting_heart_rate / sleep_performance_
    percentage). Oura (oura-live) is applied first and WHOOP (whoop-live) second,
    so on any date both cover, WHOOP wins — it carries the native strain and
    sleep-performance the dashboard was built around. With only Oura connected,
    Oura's readiness/HRV/RHR/sleep fill the same tiles.
    """
    by_date: dict[str, dict[str, float]] = defaultdict(dict)
    rows = con.execute(
        "SELECT payload_json FROM records "
        "WHERE record_type='Observation' AND source_id IN ('oura-live', 'whoop-live') "
        "ORDER BY CASE source_id WHEN 'oura-live' THEN 0 ELSE 1 END"
    ).fetchall()
    for (payload,) in rows:
        p = json.loads(payload)
        d = p.get("date")
        mn = p.get("metric_name")
        v = p.get("value")
        if not d or mn is None or v is None:
            continue
        by_date[d][mn] = v
        # Recovery payloads also carry rhr/hrv inside metadata.score; mirror them
        # so a recovery-score record alone is enough to populate the daily row.
        _score = (p.get("metadata") or {}).get("score")
        score = _score if isinstance(_score, dict) else {}
        if "resting_heart_rate" in score and score["resting_heart_rate"] is not None:
            by_date[d].setdefault("resting_heart_rate", score["resting_heart_rate"])
        if "hrv_rmssd_milli" in score and score["hrv_rmssd_milli"] is not None:
            by_date[d].setdefault("hrv_rmssd_milli", score["hrv_rmssd_milli"])
    return by_date


def _series(by_date, dates, key, *, round_to=0):
    """Forward/back-filled numeric series over `dates` for chart trends.

    WHOOP does not score every day (rest days, gaps). Charts want a clean line, so
    we carry the last known value forward and back-fill the leading gap.
    """
    raw = [by_date[d].get(key) for d in dates]
    if not any(v is not None for v in raw):
        return []
    # forward fill
    last = None
    filled = []
    for v in raw:
        if v is not None:
            last = v
        filled.append(last)
    # back fill the leading None run
    first = next(v for v in raw if v is not None)
    filled = [v if v is not None else first for v in filled]
    if round_to == 0:
        return [round(v) for v in filled]
    return [round(v, round_to) for v in filled]


def _latest(by_date, dates, key):
    for d in reversed(dates):
        v = by_date[d].get(key)
        if v is not None:
            return v, d
    return None, None


def _hrv_minutes(value):
    # WHOOP exposes rMSSD in milliseconds (e.g. 82.5); the dashboard labels it "мс".
    return round(value) if value is not None else None


def build_recovery_block(con: sqlite3.Connection) -> dict:
    by_date = _load_recovery_by_date(con)
    dates = sorted(by_date)
    if not dates:
        return {}

    rec, rec_date = _latest(by_date, dates, "recovery_score")
    hrv, _ = _latest(by_date, dates, "hrv_rmssd_milli")
    if hrv is None:
        hrv, _ = _latest(by_date, dates, "hrv_rmssd")
    rhr, _ = _latest(by_date, dates, "resting_heart_rate")
    strain, _ = _latest(by_date, dates, "strain")
    sleep_perf, _ = _latest(by_date, dates, "sleep_performance_percentage")

    # Sleep hours: WHOOP export here only carries performance %, not duration in
    # hours. Approximate hours from sleep_performance against a need baseline so
    # the dashboard's "X / Y ч" stays meaningful; label confidence accordingly.
    sleep_need = 8.0
    sleep_hours = round(sleep_need * (sleep_perf / 100.0), 1) if sleep_perf else None

    trend_rec = _series(by_date, dates, "recovery_score")
    trend_hrv = _series(by_date, dates, "hrv_rmssd_milli") or _series(
        by_date, dates, "hrv_rmssd"
    )
    trend_sleep = [
        round(sleep_need * (v / 100.0), 1)
        for v in _series(by_date, dates, "sleep_performance_percentage")
    ]
    trend_strain = _series(by_date, dates, "strain", round_to=1)

    block = {
        "date": _human_date(rec_date or dates[-1]),
        "recovery": round(rec) if rec is not None else None,
        "hrv": _hrv_minutes(hrv),
        "rhr": round(rhr) if rhr is not None else None,
        "sleep": sleep_hours,
        "sleepNeeded": sleep_need,
        "strain": round(strain, 1) if strain is not None else None,
        "strainTarget": round(max(trend_strain or [10]) , 1) if trend_strain else None,
        # Last 14 / 30 days for the trend charts.
        "trendRec": trend_rec[-14:],
        "trendHrv": trend_hrv[-14:],
        "trendSleep": trend_sleep[-14:],
        "trendStrain": trend_strain[-14:],
        "trend30Rec": trend_rec[-30:],
        "trend30Hrv": trend_hrv[-30:],
    }
    return block


def build_biomarkers(con: sqlite3.Connection) -> list[dict]:
    """Use Atlas microbiota taxa shares as real 'biomarkers'.

    No blood-panel numbers were extracted from the uploaded lab PDF, so instead of
    faking blood markers we surface the real Atlas gut microbiota composition
    (May 2020 snapshot). Reference / optimal bands are rough literature ranges for
    a healthy adult gut; flagged low confidence (historical, single snapshot).
    """
    rows = con.execute(
        "SELECT payload_json FROM records "
        "WHERE json_extract(payload_json,'$.observation_kind')='test_result'"
    ).fetchall()
    shares = {}
    for (payload,) in rows:
        p = json.loads(payload)
        mn = p.get("metric_name")
        v = p.get("value")
        if mn and v is not None:
            shares[mn] = v

    # Curated phylum-level view with rough healthy-adult reference bands (%).
    spec = [
        ("Firmicutes", "%", 20, 80, 40, 65,
         "Доминирующий тип бактерий здорового кишечника. "
         "Исторический снимок микробиоты — давность отражена в уровне доверия."),
        ("Bacteroidetes", "%", 10, 60, 20, 45,
         "Второй по доле тип; важно соотношение Firmicutes/Bacteroidetes. "
         "Текущее состояние может отличаться от снимка."),
        ("Proteobacteria", "%", 0, 10, 0, 5,
         "Низкая доля — хороший признак (высокая связана с дисбиозом)."),
        ("Actinobacteria", "%", 0, 10, 1, 5,
         "Включает Bifidobacterium; типично нижняя часть нормы."),
        ("Faecalibacterium", "%", 2, 15, 5, 12,
         "Ключевой производитель бутирата (противовоспалительный)."),
        ("Akkermansia", "%", 0, 5, 1, 4,
         "Связана со здоровьем слизистой и метаболизмом; присутствие — хороший признак."),
    ]
    grades = {"Firmicutes": "C2", "Bacteroidetes": "C2", "Proteobacteria": "C2",
              "Actinobacteria": "C2", "Faecalibacterium": "C2", "Akkermansia": "C2"}

    out = []
    for name, unit, ref_min, ref_max, opt_min, opt_max, discuss in spec:
        if name not in shares:
            continue
        v = round(shares[name], 1)
        if v < opt_min:
            status = "low"
        elif v > opt_max:
            status = "high"
        elif opt_min <= v <= opt_max:
            status = "optimal"
        else:
            status = "normal"
        out.append({
            "name": name,
            "value": v,
            "unit": unit,
            "refMin": ref_min,
            "refMax": ref_max,
            "optMin": opt_min,
            "optMax": opt_max,
            "status": status,
            "grade": grades.get(name, "C2"),
            "trend": [v],
            "discuss": discuss,
        })

    # Лабораторные маркеры из загруженных панелей (lab-panel): реальные значения
    # с собственными референсами/флагами (например, панель 2024-07-11). Отличаем
    # от микробиоты по наличию reference_low в payload.
    lab_rows = con.execute(
        "SELECT payload_json FROM records WHERE record_type='Observation' "
        "AND json_extract(payload_json,'$.observation_kind')='test_result' "
        "AND json_extract(payload_json,'$.reference_low') IS NOT NULL "
        "ORDER BY rowid"
    ).fetchall()
    lab_out = []
    for (payload,) in lab_rows:
        p = json.loads(payload)
        v = p.get("value")
        if v is None:
            continue
        lo = p.get("reference_low")
        hi = p.get("reference_high")
        flag = (p.get("flag") or "").upper()
        status = "high" if flag == "H" else ("low" if flag == "L" else "optimal")
        lab_out.append({
            "name": p.get("title") or p.get("metric_name"),
            "value": v,
            "unit": p.get("unit", ""),
            "refMin": lo,
            "refMax": hi,
            "optMin": lo,
            "optMax": hi,
            "status": status,
            "grade": "C5",
            "trend": [v],
            "discuss": p.get("summary") or "",
            "date": p.get("date"),
        })
    # Лабораторные — впереди (свежие, измеренные), микробиота Atlas — следом.
    return lab_out + out


def build_connections(con: sqlite3.Connection) -> dict:
    """Real connection status derived from which sources exist in the index."""
    srcs = {
        r[0]: {"type": r[1], "cstart": r[2], "cend": r[3]}
        for r in con.execute(
            "SELECT source_id, source_type, coverage_start, coverage_end FROM sources"
        ).fetchall()
    }
    has_whoop = any(s["type"] == "whoop" for s in srcs.values())
    has_oura = any(s["type"] == "oura" and sid == "oura-live" for sid, s in srcs.items())
    has_labs = any("microbiota" in sid or "pdf" in sid for sid in srcs)
    has_dna = any("genotype" in sid for sid in srcs)

    whoop_cend = next(
        (s["cend"] for s in srcs.values() if s["type"] == "whoop"), None
    )
    oura_cend = next(
        (s["cend"] for sid, s in srcs.items() if s["type"] == "oura" and sid == "oura-live"), None
    )

    return {
        "whoop": {"label": "WHOOP Tracker", "connected": has_whoop,
                  "lastSync": _human_date(whoop_cend) if whoop_cend else None,
                  "icon": "ph-activity"},
        "apple": {"label": "Apple Health", "connected": False, "lastSync": None,
                  "icon": "ph-heart"},
        "oura": {"label": "Oura Ring", "connected": has_oura,
                 "lastSync": _human_date(oura_cend) if oura_cend else None,
                 "icon": "ph-shield"},
        "garmin": {"label": "Garmin Connect", "connected": False, "lastSync": None,
                   "icon": "ph-barbell"},
        "labs": {"label": "Atlas / Лаборатории", "connected": has_labs,
                 "lastSync": "май 2020" if has_labs else None, "icon": "ph-flask"},
        "dna": {"label": "Atlas / ДНК", "connected": has_dna,
                "lastSync": "генотип" if has_dna else None, "icon": "ph-fingerprint"},
    }


def build_readiness(recovery) -> dict:
    if recovery is None:
        return {}
    if recovery >= GREEN:
        zone = "зелёная зона"
        verdict = "организм восстановлен — хороший день для нагрузки"
        title = "Можно дать пиковую тренировку"
        why = "Высокий recovery: HRV и RHR в благоприятной зоне. Реальные данные WHOOP (C5)."
    elif recovery >= YELLOW:
        zone = "жёлтая зона"
        verdict = "умеренный режим, без пиковых перегрузок"
        title = "Держать умеренную нагрузку"
        why = "Recovery средний — лучше аэробная база, чем интенсив. Реальные данные WHOOP (C5)."
    else:
        zone = "красная зона"
        verdict = "нужен глубокий покой и приоритет сну"
        title = "День восстановления"
        why = "Низкий recovery: организму нужен отдых. Реальные данные WHOOP (C5)."
    return {
        "readiness": f"Recovery {recovery}% ({zone}) по реальным данным WHOOP — {verdict}.",
        "action": {"title": title, "why": why},
    }


def _human_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso[:10])
    except ValueError:
        return iso
    months = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    weekdays = ["понедельник", "вторник", "среда", "четверг", "пятница",
                "суббота", "воскресенье"]
    return f"{weekdays[dt.weekday()]}, {dt.day} {months[dt.month - 1]} {dt.year}"


def _daily_from_whoop(by_date: dict) -> dict:
    """Reshape the WHOOP by-date map into the {date: {metric}} contract the
    insights detectors expect (recovery / hrv / rhr / strain / sleep_h).

    Sleep hours are not in this export, so sleep_h is approximated from
    sleep_performance_percentage against an 8h need (the same approximation the
    recovery block uses) and is therefore low-confidence for sleep detectors.
    """
    daily: dict = {}
    for d, m in by_date.items():
        row: dict = {}
        if m.get("recovery_score") is not None:
            row["recovery"] = m["recovery_score"]
        hrv = m.get("hrv_rmssd_milli")
        if hrv is None:
            hrv = m.get("hrv_rmssd")
        if hrv is not None:
            row["hrv"] = hrv
        if m.get("resting_heart_rate") is not None:
            row["rhr"] = m["resting_heart_rate"]
        if m.get("strain") is not None:
            row["strain"] = m["strain"]
        perf = m.get("sleep_performance_percentage")
        if perf is not None:
            row["sleep_h"] = round(8.0 * perf / 100.0, 1)
        if row:
            daily[d] = row
    return daily


def build_insights_block(con: sqlite3.Connection) -> dict:
    """Compute insights + n-of-1 protocols from the engine, if importable.

    Wrapped in try/except so the bridge still runs for users who only have the
    dashboard checked out without the openhealth package on the path.
    """
    try:
        import sys
        # When run as a script, sys.path[0] is ui/web, not the repo root where
        # the openhealth package lives; add the repo root so the import works.
        repo_root = str(Path(__file__).resolve().parents[2])
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from openhealth import insights as _insights
        from openhealth import protocols as _protocols
    except Exception:
        return {"insights": [], "protocols": []}

    daily = _daily_from_whoop(_load_recovery_by_date(con))
    try:
        found = _insights.detect_insights(daily, {"sleep_h": _sleep_goal_h()})
        protos = _protocols.build_protocols(found, correlations=[])
        return {
            "insights": _insights.insights_to_dicts(found),
            "protocols": _protocols.protocols_to_dicts(protos),
        }
    except Exception:
        return {"insights": [], "protocols": []}


def build_calendar_block() -> dict:
    """Today's "day pulse" from the ICS subscription, if one is configured.

    Wrapped in try/except: without ~/.openhealth/calendar.json (or with the
    network down) the dashboard build works exactly as before — no calendar key.
    The ICS URL is a secret and is never printed or written to the payload.
    """
    try:
        import sys
        repo_root = str(Path(__file__).resolve().parents[2])
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from openhealth.connectors import ics_calendar

        config = ics_calendar.load_calendar_config()
        if not config or not config.get("enabled"):
            return {}
        parsed = ics_calendar.parse_ics(ics_calendar.fetch_ics(config["ics_url"]))
        today = datetime.now().date().isoformat()
        block = ics_calendar.day_load(parsed["events"], today)
        block["warnings"] = parsed.get("warnings", [])[:5]
        block["source"] = "ics"
        return block
    except Exception:
        return {}


def _tz_from_offset(offset: str | None):
    """timezone from a WHOOP '+01:00'-style offset string; UTC fallback."""
    try:
        sign = 1 if offset.startswith("+") else -1
        hours, minutes = offset.lstrip("+-").split(":")
        return timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))
    except Exception:
        return timezone.utc


def build_circadian_block(con: sqlite3.Connection) -> dict:
    """Rise-style circadian energy schedule from the real sleep anchor.

    Anchor = weighted habitual wake/bed time over the most recent (<=14 days)
    non-nap WHOOP nights in the index; accumulated sleep debt (sleep_debt@v2)
    deepens the afternoon dip and trims the peaks. Two-process model is
    established science (C3-C4); the personal placement is C2. Returns {}
    gracefully when the engine is not importable or there is no sleep data.
    """
    try:
        import sys
        repo_root = str(Path(__file__).resolve().parents[2])
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from openhealth import circadian as _circadian
        from openhealth.modules.recovery import sleep_debt as _sleep_debt
    except Exception:
        return {}
    try:
        rows = con.execute(
            "SELECT payload_json FROM records WHERE record_type='TimelineEvent' "
            "AND json_extract(payload_json,'$.event_kind')='whoop_sleep'"
        ).fetchall()
        sessions = []
        for (payload,) in rows:
            p = json.loads(payload)
            md = p.get("metadata") or {}
            if md.get("nap") or not md.get("start") or not md.get("end"):
                continue
            tz = _tz_from_offset(md.get("timezone_offset"))
            sessions.append({
                "start": datetime.fromisoformat(md["start"].replace("Z", "+00:00")).astimezone(tz),
                "end": datetime.fromisoformat(md["end"].replace("Z", "+00:00")).astimezone(tz),
            })
        if not sessions:
            return {}
        sessions.sort(key=lambda s: s["end"], reverse=True)
        last_night = sessions[0]["end"].date()
        kept = []
        for s in sessions:
            s["days_ago"] = (last_night - s["end"].date()).days
            if s["days_ago"] <= 14:
                kept.append(s)
        anchor = _circadian.compute_sleep_anchor(kept)
        nights = [(s["end"] - s["start"]).total_seconds() / 3600.0 for s in reversed(kept)]
        debt_h = float(_sleep_debt(nights[-1], recent_nights_h=nights).get("accumulated_debt_h") or 0.0)
        wake_minutes = int(anchor["wake_minutes"]) % (24 * 60)
        wake_dt = datetime.combine(
            date.today(), time(wake_minutes // 60, wake_minutes % 60),
            tzinfo=sessions[0]["end"].tzinfo,
        )
        schedule = _circadian.energy_schedule(wake_dt, anchor=anchor, sleep_debt_h=debt_h)

        def _hhmm(iso: str) -> str:
            return iso[11:16]

        return {
            "wake_time": _hhmm(schedule["wake_time"]),
            "bed_time": _hhmm(schedule["bed_time"]),
            "phases": [
                {
                    "phase": p["phase"],
                    "start": _hhmm(p["start_iso"]),
                    "end": _hhmm(p["end_iso"]),
                    "label": p["label_ru"],
                    "advice": p["advice_ru"],
                    "confidence": p["confidence"],
                }
                for p in schedule["phases"]
            ],
            "curve": [
                {"t": _hhmm(pt["t_iso"]), "e": round(pt["energy"]), "phase": pt["phase"]}
                for pt in schedule["curve"]
            ],
            "points_per_hour": 4,
            "melatonin_window": {
                "start": _hhmm(schedule["melatonin_window"]["start_iso"]),
                "end": _hhmm(schedule["melatonin_window"]["end_iso"]),
            },
            "sleep_debt_h": round(debt_h, 1),
            "debt_note": (
                f"Накопленный долг сна ~{debt_h:.1f} ч (sleep_debt@v2, окно {len(nights)} ноч.); "
                f"анкор по ночам до {last_night.isoformat()}."
            ),
            "anchor_nights": len(kept),
            "anchor_last_date": last_night.isoformat(),
            "model": schedule["model"],
            "confidence": "C2",
        }
    except Exception:
        return {}


def _all_records(con: sqlite3.Connection) -> list[dict]:
    """Every record payload as a light dict, for the lab/quality blocks.

    Flattens the metadata.score sub-object of WHOOP daily records into top-level
    metric rows (recovery / hrv / rhr) so the quality checks see one value per
    metric per date — the same reshape the recovery block uses.
    """
    out: list[dict] = []
    rows = con.execute("SELECT payload_json FROM records").fetchall()
    for (payload,) in rows:
        p = json.loads(payload)
        mn = p.get("metric_name")
        if mn is None:
            continue
        out.append({
            "name": mn,
            "metric_name": mn,
            "value": p.get("value"),
            "unit": p.get("unit"),
            "date": p.get("date") or p.get("start_date"),
        })
        # Mirror rhr / hrv carried inside a recovery score sub-object.
        _score = (p.get("metadata") or {}).get("score")
        score = _score if isinstance(_score, dict) else {}
        d = p.get("date") or p.get("start_date")
        for sk, alias in (("resting_heart_rate", "rhr"), ("hrv_rmssd_milli", "hrv")):
            if score.get(sk) is not None:
                out.append({"name": alias, "metric_name": alias,
                            "value": score[sk], "unit": None, "date": d})
    return out


def build_lab_block(con: sqlite3.Connection) -> dict:
    """Blood-panel analysis from real records, via the openhealth lab engine.

    Emits panels (lipids/glycemia/iron/thyroid/inflammation/vitamins/kidney),
    derived indices, per-marker history for markers that have data, and re-test
    cadence hints. Wrapped in try/except so the bridge still runs without the
    openhealth package importable. Returns {} when no recognised lab markers are
    present (this dataset is WHOOP + microbiota, not a blood panel).
    """
    try:
        import sys
        repo_root = str(Path(__file__).resolve().parents[2])
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from openhealth import lab_panel as _lab
        from openhealth import reference_ranges as _rr
    except Exception:
        return {}
    try:
        records = _all_records(con)
        panels = _lab.panel_summary(records)
        indices = _lab.derived_indices(records)
        # Only markers that actually have data get a history block.
        history = []
        recheck_hints = []
        today = datetime.now().date().isoformat()
        for key, spec in _rr.MARKERS.items():
            hist = _lab.marker_history(records, spec.display_name)
            if not isinstance(hist["latest"], (int, float)):
                continue
            history.append({
                "marker_key": key,
                "display_name": spec.display_name,
                "latest": hist["latest"],
                "unit": hist["unit"],
                "trend": hist["trend"],
                "optimal_status": hist["optimal_status"],
                "points": hist["points"],
            })
            last_date = hist["points"][-1]["date"] if hist["points"] else None
            recheck_hints.append(_lab.next_checkup_hint(spec.display_name, last_date, today))
        has_lab = bool(history) or bool(indices)
        return {
            "available": has_lab,
            "panels": panels,
            "indices": indices,
            "history": history,
            "recheckHints": recheck_hints,
            "note": ("Нет лабораторных маркеров крови в индексе — только WHOOP и "
                     "микробиота." if not has_lab else None),
        }
    except Exception:
        return {}


def build_quality_block(con: sqlite3.Connection) -> dict:
    """Data-quality report (score + top issues) over all real records.

    Wrapped in try/except: without the openhealth package the bridge still runs.
    """
    try:
        import sys
        repo_root = str(Path(__file__).resolve().parents[2])
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from openhealth import data_quality as _dq
    except Exception:
        return {}
    try:
        records = _all_records(con)
        today = datetime.now().date().isoformat()
        report = _dq.validate_records(records, today=today)
        score = _dq.quality_score(report)
        return {
            "score": score["score"],
            "verdict": score["verdict_ru"],
            "breakdown": score["breakdown"],
            "checked": report["checked"],
            "counts": report["counts"],
            "issues": report["issues"][:10],
        }
    except Exception:
        return {}


def build_weather_block() -> dict:
    """Погода как внешний фактор дня (open-meteo, локация из ~/.openhealth/weather.json).

    Без локации/сети — пустой dict (дашборд валиден без погоды). Кроме сводки —
    световой день, UV, изменение давления: и в карточку, и в кандидаты корреляций.
    """
    try:
        from openhealth.connectors import weather

        if weather.load_location() is None:
            return {}
        from datetime import date, timedelta

        today = date.today().isoformat()
        day = weather.fetch_day(today)
        if not day or all(day.get(k) is None for k in ("t_mean", "t_max", "pressure_msl_mean")):
            # forecast на сегодня ещё пуст ранним утром — берём вчера честно.
            day = weather.fetch_day((date.today() - timedelta(days=1)).isoformat()) or day
        flags = weather.weather_context(day)
        return {
            "today_summary": weather.day_summary_ru(day),
            "flags": flags,
            "pressure_change": day.get("pressure_change_24h"),
            "daylight_h": day.get("daylight_h"),
            "sunrise": day.get("sunrise"),
            "sunset": day.get("sunset"),
            "uv_index_max": day.get("uv_index_max"),
            "t_mean": day.get("t_mean"),
            "label": (weather.load_location() or {}).get("label"),
        }
    except Exception:
        return {}


def _sleep_goal_h() -> float:
    """Цель сна для детекторов: настраиваемый параметр, фолбэк 8.0."""
    try:
        from openhealth import params

        return float(params.get("insights.sleep_goal_h"))
    except Exception:
        return 8.0


# --- correlations: реальные связи поведение -> recovery из журнала ----------

# Иконки phosphor по категориям поведения (фронт подставит дефолт, если нет).
_CORR_CATEGORY_ICON = {
    "toxic": "ph-warning",
    "nutrition": "ph-fork-knife",
    "lifestyle": "ph-sun",
    "recovery_activities": "ph-heart",
    "mental_wellbeing": "ph-brain",
    "activity": "ph-barbell",
    "supps": "ph-pill",
    "habit": "ph-leaf",
    "health_symptoms": "ph-first-aid",
    "hormonal_health": "ph-drop",
    "custom": "ph-note-pencil",
}

# Порог пересечения: нужно >= MIN_YES и >= MIN_NO дней с recovery в те же дни.
_CORR_INSUFFICIENT_NOTE = (
    "нужно ≥{min_yes} дней с отметкой и ≥{min_no} без неё, при наличии recovery в те "
    "же дни; сейчас пересечения журнала и recovery недостаточно"
)


def _journal_pairs(con: sqlite3.Connection):
    """behavior_id -> {name, category, days: {date: bool}} из индекса.

    Берём только boolean journal_entry (корреляции считаются по yes/no).
    Числовые отметки (вода, mood) сюда не попадают — это отдельный расчёт.
    """
    rows = con.execute(
        "SELECT payload_json FROM records WHERE record_type='Observation' "
        "AND json_extract(payload_json,'$.observation_kind')='journal_entry'"
    ).fetchall()
    by_behavior: dict = {}
    for (payload,) in rows:
        p = json.loads(payload)
        value = p.get("value")
        if not isinstance(value, bool):
            continue
        day = p.get("date")
        if not day:
            continue
        md = p.get("metadata") or {}
        bid = md.get("behavior_id") or p.get("metric_name")
        if not bid:
            continue
        slot = by_behavior.setdefault(bid, {
            "name": md.get("behavior_name") or bid,
            "category": md.get("category") or "unknown",
            "days": {},
        })
        slot["days"][day] = value
    return by_behavior


def _recovery_by_day(con: sqlite3.Connection) -> dict:
    """date -> recovery_score из реального индекса.

    В индексе recovery лежит как metric_name='recovery_score' (observation_kind
    здесь whoop_recovery_metric), поэтому фильтруем по metric_name — иначе пары
    с recovery никогда не соберутся.
    """
    rows = con.execute(
        "SELECT payload_json FROM records WHERE record_type='Observation' "
        "AND json_extract(payload_json,'$.metric_name')='recovery_score'"
    ).fetchall()
    out: dict = {}
    for (payload,) in rows:
        p = json.loads(payload)
        d = p.get("date")
        v = p.get("value")
        if d and v is not None:
            try:
                out[d] = float(v)
            except (TypeError, ValueError):
                continue
    return out


def build_correlations_block(con: sqlite3.Connection) -> dict:
    """Честные корреляции поведение↔recovery из журнала + recovery индекса.

    Тот же расчёт, что и в openhealth.modules.correlations: для каждого
    boolean-поведения сравниваем средний recovery в дни «да» против дней «нет»
    (порог 5yes/5no, cap C2; C3 только при >=2 переключениях). Recovery берём из
    индекса по совпадающим датам.

    Возвращает {"correlations": [...], "allBehaviors": [...], "status": "ok"|
    "insufficient_data", "note": "..."}. Если пересечения недостаточно (ни одна
    пара не прошла порог) — correlations=[] и status=insufficient_data: фронт
    отличает «нет данных» от реальных значений и не показывает демо.
    """
    behaviors_raw = _journal_pairs(con)
    rec_by_day = _recovery_by_day(con)

    # allBehaviors: реальный список логированных поведений из журнала.
    all_behaviors = []
    for bid, slot in sorted(behaviors_raw.items()):
        all_behaviors.append({
            "id": bid,
            "label": slot["name"],
            "category": slot["category"],
            "icon": _CORR_CATEGORY_ICON.get(slot["category"]),
            "selected": True,
        })

    # Движок корреляций (тот же расчёт). Без пакета — честный пустой результат.
    try:
        import sys
        repo_root = str(Path(__file__).resolve().parents[2])
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from openhealth.modules import correlations as _corr
        from openhealth import params as _params
    except Exception:
        _corr = None
        _params = None

    min_yes = min_no = 5
    if _params is not None:
        try:
            min_yes = int(_params.get("correlations.min_yes_days"))
            min_no = int(_params.get("correlations.min_no_days"))
        except Exception:
            min_yes = min_no = 5

    note_insufficient = _CORR_INSUFFICIENT_NOTE.format(min_yes=min_yes, min_no=min_no)

    if _corr is None or not behaviors_raw or not rec_by_day:
        return {
            "correlations": [],
            "allBehaviors": all_behaviors,
            "status": "insufficient_data",
            "note": note_insufficient,
        }

    # Собираем вход для analyze(): только пары, где есть recovery в тот же день.
    engine_input = []
    for bid, slot in behaviors_raw.items():
        pairs = [
            {"date": day, "yes": yes, "recovery": rec_by_day.get(day)}
            for day, yes in slot["days"].items()
            if day in rec_by_day
        ]
        engine_input.append({
            "behavior_id": bid,
            "behavior_name": slot["name"],
            "category": slot["category"],
            "pairs": pairs,
        })

    insights = _corr.analyze(engine_input)
    if not insights:
        return {
            "correlations": [],
            "allBehaviors": all_behaviors,
            "status": "insufficient_data",
            "note": note_insufficient,
        }

    # Insight движка -> компактная строка корреляции для фронта.
    correlations = []
    for ins in insights:
        meta = ins.get("metadata") or {}
        impact = meta.get("impact")
        bid = meta.get("behavior_id")
        title = ins.get("title") or ""
        name = (title[len("Impact: "):] if title.startswith("Impact: ") else title) or bid
        category = meta.get("category") or "unknown"
        correlations.append({
            "id": bid,
            "label": name,
            "delta": round(impact) if isinstance(impact, (int, float)) else None,
            "dir": "up" if (impact or 0) >= 0 else "down",
            "grade": meta.get("confidence_grade", "C2"),
            "icon": _CORR_CATEGORY_ICON.get(category),
            "yesDays": meta.get("n_yes"),
            "noDays": meta.get("n_no"),
            "meanYes": meta.get("mean_recovery_yes"),
            "meanNo": meta.get("mean_recovery_no"),
        })

    return {
        "correlations": correlations,
        "allBehaviors": all_behaviors,
        "status": "ok",
        "note": "реальные связи поведение↔recovery из журнала (порог {}/{} дней).".format(
            min_yes, min_no
        ),
    }


# Доказательные образ-жизненные варианты для зоны ДНК. Показываем РЕАЛЬНЫЙ генотип
# (факт из raw-файла) + осторожную трактовку с C-грейдом. Не диагноз. Где ориентация
# аллеля стренд-зависима (MTHFR/VDR) — направленную трактовку не утверждаем (interp=None).
_DNA_SPEC = [
    ("rs762551", "CYP1A2", "Метаболизм кофеина", "C3",
     {"AA": "быстрый метаболизм кофеина", "AC": "промежуточный", "CC": "медленный — кофе действует сильнее/дольше"}),
    ("rs4988235", "MCM6/LCT", "Переносимость лактозы", "C4",
     {"AA": "лактаза сохраняется (молоко обычно ок)", "AG": "сниженная переносимость", "GG": "склонность к непереносимости лактозы"}),
    ("rs1815739", "ACTN3", "Тип мышечных волокон", "C3",
     {"CC": "есть α-актинин-3 — уклон в силу/спринт", "CT": "смешанный тип", "TT": "нет α-актинина-3 — уклон в выносливость"}),
    ("rs9939609", "FTO", "Аппетит / склонность к весу", "C3",
     {"TT": "нет рискового аллеля", "AT": "один рисковый аллель (промежуточно)", "AA": "два рисковых — выше тяга к еде"}),
    ("rs1800562", "HFE C282Y", "Перегрузка железом", "C3",
     {"GG": "нет мутации C282Y — гемохроматоз маловероятен", "AG": "носитель C282Y", "AA": "C282Y/C282Y — риск перегрузки железом"}),
    ("rs1799945", "HFE H63D", "Перегрузка железом", "C3",
     {"CC": "нет мутации H63D", "CG": "носитель H63D", "GG": "H63D/H63D"}),
    ("rs671", "ALDH2", "Метаболизм алкоголя", "C4",
     {"GG": "нормальный — без флаш-реакции", "AG": "сниженный — флаш, выше вред алкоголя", "AA": "очень низкий"}),
    ("rs4680", "COMT Val158Met", "Дофамин / стресс", "C2",
     {"GG": "Val/Val — быстрый распад дофамина", "AG": "Val/Met — промежуточно", "AA": "Met/Met — медленный распад (стресс-чувствительность, фокус)"}),
    ("rs7903146", "TCF7L2", "Регуляция глюкозы", "C3",
     {"CC": "нет рискового T — благоприятно", "CT": "один рисковый T", "TT": "два рисковых T — выше риск по глюкозе"}),
    ("rs6265", "BDNF Val66Met", "Нейропластичность", "C2",
     {"CC": "Val/Val (типичный)", "CT": "Val/Met", "TT": "Met/Met"}),
    ("rs1801133", "MTHFR C677T", "Фолатный обмен", "C2", None),
    ("rs1801131", "MTHFR A1298C", "Фолатный обмен", "C2", None),
    ("rs2228570", "VDR FokI", "Рецептор витамина D", "C1", None),
]


def _genotypes_from_23andme(fh, want: set[str]) -> dict[str, str]:
    """Raw 23andMe / AncestryDNA export: rsid<TAB>chromosome<TAB>position<TAB>genotype."""
    found: dict[str, str] = {}
    for line in fh:
        if not line or line[0] != "r":
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 4:
            continue
        if parts[0] in want:
            found[parts[0]] = parts[3].strip()
            if len(found) == len(want):
                break
    return found


def _genotypes_from_vcf(fh, want: set[str]) -> dict[str, str]:
    """Single-sample VCF (Genotek, and array/WGS labs generally): decode GT plus
    REF/ALT into the same two-base call the 23andMe path yields.

    Alleles are taken on the reference's forward strand, which is the convention
    23andMe reports on too, so both paths feed _DNA_SPEC the same way. Only
    diploid bi-allelic SNV calls are used; indels, no-calls (./.) and anything
    non-single-base are skipped — the curated set is all SNVs.
    """
    found: dict[str, str] = {}
    for line in fh:
        if not line or line[0] == "#":
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 10:  # CHROM..FORMAT + at least one sample column
            continue
        rsid = parts[2]
        if rsid not in want:
            continue
        # The spec mandates GT-first when present, but locate it in FORMAT
        # explicitly rather than assume position 0 — cheap insurance against a
        # non-conforming exporter reordering fields.
        try:
            gt_pos = parts[8].split(":").index("GT")
        except ValueError:
            continue
        sample = parts[9].split(":")
        if gt_pos >= len(sample):
            continue
        idx = sample[gt_pos].replace("|", "/").split("/")
        if len(idx) != 2 or not all(i.isdigit() for i in idx):
            continue
        alleles = [parts[3]] + parts[4].split(",")
        try:
            bases = [alleles[int(i)] for i in idx]
        except IndexError:
            continue
        if not all(len(b) == 1 for b in bases):
            continue
        found[rsid] = "".join(bases)
        if len(found) == len(want):
            break
    return found


def build_dna(con: sqlite3.Connection) -> list[dict]:
    """Read the raw genotype file (local) and surface real genotypes for a
    curated set of well-established lifestyle variants. Factual call + cautious
    note + C-grade. Never a diagnosis; strand-ambiguous loci show no direction.

    Accepts either a raw 23andMe-style export or a single-sample VCF — the
    format is detected from the first line."""
    row = con.execute(
        "SELECT payload_json FROM sources WHERE source_id LIKE '%genotype%' LIMIT 1"
    ).fetchone()
    if not row:
        return []
    files = json.loads(row[0]).get("files") or []
    path = files[0] if files else None
    if not path or not os.path.exists(path):
        return []
    want = {s[0] for s in _DNA_SPEC}
    try:
        # utf-8-sig: a BOM'd export must not make the sniff below miss and fall
        # through to the 23andMe reader — that's the exact silent-empty-block
        # bug this function exists to fix, just moved one format over.
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            is_vcf = fh.readline().startswith("##fileformat=VCF")
            fh.seek(0)
            found = (_genotypes_from_vcf if is_vcf else _genotypes_from_23andme)(fh, want)
    except OSError:
        return []
    out = []
    for rsid, gene, trait, grade, interp in _DNA_SPEC:
        gt = found.get(rsid)
        if not gt:
            continue
        if interp:
            note = interp.get(gt) or next(
                (v for k, v in interp.items() if sorted(k) == sorted(gt)), None
            ) or "вариант определён; трактовка — у агента"
        else:
            note = "трактовка зависит от ориентации аллеля/контекста — разбор у агента"
        out.append({"rsid": rsid, "gene": gene, "trait": trait,
                    "genotype": gt, "note": note, "grade": grade})
    return out


def build_payload(db_path: Path) -> dict:
    con = sqlite3.connect(str(db_path))
    try:
        rec_block = build_recovery_block(con)
        biomarkers = build_biomarkers(con)
        connections = build_connections(con)
        readiness = build_readiness(rec_block.get("recovery"))
        insights_block = build_insights_block(con)
        circadian_block = build_circadian_block(con)
        lab_block = build_lab_block(con)
        quality_block = build_quality_block(con)
        correlations_block = build_correlations_block(con)
        dna_block = build_dna(con)
    finally:
        con.close()

    payload: dict = {}
    payload.update(rec_block)
    payload.update(readiness)
    payload["biomarkers"] = biomarkers
    payload["biomarkersConnected"] = bool(biomarkers)
    if dna_block:
        payload["dna"] = dna_block
    payload["connections"] = connections
    payload["insights"] = insights_block.get("insights", [])
    payload["protocols"] = insights_block.get("protocols", [])
    calendar_block = build_calendar_block()
    if calendar_block:
        # "Пульс дня": загрузка 0-100, встречи, первая/последняя, окна >= 1ч.
        payload["calendar"] = calendar_block
    if circadian_block:
        # Rise-уровень: фазы энергии дня + волна 24ч + окно мелатонина.
        payload["circadian"] = circadian_block
    if lab_block:
        # Кровь: панели, производные индексы, история маркеров, частота пересдач.
        payload["lab"] = lab_block
    if quality_block:
        # Проверка данных: score 0-100 + топ-10 проблем (дубли/будущее/невозможные/gap).
        payload["quality"] = quality_block
    weather_block = build_weather_block()
    if weather_block:
        # Внешние факторы: погода/давление/световой день/UV для контекста и корреляций.
        payload["weather"] = weather_block

    # Честные корреляции поведение->recovery из журнала. Всегда кладём ключи
    # correlations и allBehaviors (даже пустые) + статус в _meta, чтобы фронт мог
    # отличить «нет данных» от реальных значений и НЕ показывал демо.
    payload["correlations"] = correlations_block.get("correlations", [])
    payload["allBehaviors"] = correlations_block.get("allBehaviors", [])
    corr_status = correlations_block.get("status", "insufficient_data")
    corr_note = correlations_block.get("note", "")

    payload["_meta"] = {
        "source": "health_os SQLite index",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "correlations_status": corr_status,
        "correlations_note": corr_note,
        "note": "Real personal data. recovery/hrv/rhr/strain/sleep from WHOOP; "
                "biomarkers from Atlas microbiota (2020 snapshot, low confidence); "
                "correlations computed from the real behavior journal (see "
                "correlations_status).",
    }
    return payload


def main() -> None:
    default_db = Path(os.path.expanduser("~/health-os/data/index/health_os.sqlite3"))
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=default_db,
                    help="Path to the Health OS SQLite index.")
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / "data.local.json",
                    help="Output JSON path (git-ignored).")
    args = ap.parse_args()

    if not args.db.exists():
        raise SystemExit(
            f"No Health OS index at {args.db}. Run the engine first, or pass --db. "
            "The dashboard will fall back to demo data without this file."
        )

    payload = build_payload(args.db)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")

    rec = payload.get("recovery")
    bm = len(payload.get("biomarkers", []))
    print(f"Wrote {args.out}")
    print(f"  recovery={rec}  hrv={payload.get('hrv')}  rhr={payload.get('rhr')}  "
          f"sleep={payload.get('sleep')}  strain={payload.get('strain')}")
    print(f"  biomarkers={bm}  trend points(rec)={len(payload.get('trend30Rec', []))}")
    print(f"  insights={len(payload.get('insights', []))}  protocols={len(payload.get('protocols', []))}")
    cal = payload.get("calendar")
    if cal:
        print(f"  calendar: load={cal.get('day_load_score')}/100  meetings={cal.get('meetings_count')}  "
              f"first={cal.get('first_event')}  gaps>1h={cal.get('gaps_over_1h')}")
    else:
        print("  calendar: not configured (POST /api/calendar or ~/.openhealth/calendar.json)")
    circ = payload.get("circadian")
    if circ:
        mel = circ.get("melatonin_window", {})
        print(f"  circadian: wake={circ.get('wake_time')}  bed={circ.get('bed_time')}  "
              f"melatonin={mel.get('start')}-{mel.get('end')}  debt={circ.get('sleep_debt_h')}h  "
              f"curve={len(circ.get('curve', []))}pts")
    else:
        print("  circadian: no sleep sessions in index (skipped)")
    lab = payload.get("lab")
    if lab:
        print(f"  lab: available={lab.get('available')}  history_markers={len(lab.get('history', []))}  "
              f"indices={len(lab.get('indices', []))}")
    else:
        print("  lab: no recognised blood markers in index (skipped)")
    q = payload.get("quality")
    if q:
        print(f"  quality: score={q.get('score')}/100  checked={q.get('checked')}  "
              f"issues={len(q.get('issues', []))}  counts={q.get('counts')}")
    else:
        print("  quality: skipped")
    meta = payload.get("_meta", {})
    print(f"  correlations: {len(payload.get('correlations', []))} "
          f"(status={meta.get('correlations_status')})  "
          f"allBehaviors={len(payload.get('allBehaviors', []))}")
    print("  NOTE: data.local.json is real personal data — keep it local (git-ignored).")


if __name__ == "__main__":
    main()
