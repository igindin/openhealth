from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List

from .config import SYSTEM_SOURCE_IDS
from .models import SourceManifest
from .parsers import derive_insights
from .storage import write_json, write_text


def refresh_contexts(paths, index_module) -> Dict[str, int]:
    records = index_module.list_records(paths.db_path)
    source_records = [record for record in records if record.get("source_id") not in SYSTEM_SOURCE_IDS]
    insights = derive_insights(source_records)
    index_module.delete_records_for_source(paths.db_path, "system-insights")
    for insight in insights:
        index_module.upsert_record(paths.db_path, insight)
    all_records = source_records + insights
    sources = [source for source in index_module.list_sources(paths.db_path) if source["source_id"] not in SYSTEM_SOURCE_IDS]
    artifacts = index_module.list_artifacts(paths.db_path)

    write_text(paths.contexts / "profile.md", build_profile_context(sources, artifacts))
    write_text(paths.timeline_context / "current.md", build_timeline_context(all_records))
    write_text(paths.interventions_context / "current.md", build_interventions_context(all_records))
    write_text(paths.contexts / "patterns.md", build_patterns_context(insights))
    write_text(paths.contexts / "reference-cases.md", build_reference_context(all_records))
    write_text(paths.contexts / "source-status.md", build_source_status_context(sources, artifacts, all_records))
    write_text(paths.contexts / "quick-brief.md", build_quick_brief_context(sources, all_records))
    write_json(paths.records / "system-insights.json", insights)
    return {
        "sources": len(sources),
        "artifacts": len(artifacts),
        "records": len(all_records),
        "insights": len(insights),
    }


def build_source_brief(source: Dict, artifacts: Iterable[Dict], records: Iterable[Dict]) -> str:
    source_artifacts = [artifact for artifact in artifacts if artifact["source_id"] == source["source_id"]]
    source_records = [record for record in records if record["source_id"] == source["source_id"]]
    record_counts = Counter(record["record_type"] for record in source_records)
    lines = [
        "# Source Brief: %s" % source["label"],
        "",
        "- Source ID: `%s`" % source["source_id"],
        "- Source type: `%s`" % source["source_type"],
        "- Owner: `%s`" % source["owner"],
        "- Parser status: `%s`" % source["parser_status"],
        "- Coverage: `%s` -> `%s`" % (source.get("coverage_start") or "unknown", source.get("coverage_end") or "unknown"),
        "",
        "## Artifacts",
    ]
    for artifact in source_artifacts:
        lines.append(
            "- `%s` (%s, %s bytes)" % (Path(artifact["archived_path"]).name, artifact["mime_type"], artifact["size_bytes"])
        )
    lines.extend(["", "## Records"])
    for record_type, count in sorted(record_counts.items()):
        lines.append("- `%s`: %s" % (record_type, count))
    for note in source.get("notes", []):
        lines.append("- Note: %s" % note)
    return "\n".join(lines)


def build_profile_context(sources: List[Dict], artifacts: List[Dict]) -> str:
    by_type = Counter(source["source_type"] for source in sources)
    lines = [
        "# Profile",
        "",
        "This file is the stable top-level context for OpenHealth. Edit it manually to add baseline facts, goals, and constraints.",
        "",
        "## Current Coverage",
    ]
    for source_type, count in sorted(by_type.items()):
        lines.append("- `%s`: %s source batch(es)" % (source_type, count))
    lines.extend(
        [
            "",
            "## Raw Artifact Count",
            "- `%s` archived artifact(s)" % len(artifacts),
            "",
            "## Manual Additions",
            "- Add baseline health context here",
            "- Add known sensitivities or exclusions here",
            "- Add current priorities here",
        ]
    )
    return "\n".join(lines)


def build_timeline_context(records: List[Dict]) -> str:
    dated = sorted(records, key=lambda item: (item.get("date") or item.get("start_date") or "9999-99-99", item["record_type"], item["id"]))
    lines = ["# Timeline", "", "## Chronological View"]
    for record in dated:
        date_value = record.get("date") or record.get("start_date") or "undated"
        lines.append(
            "- `%s` [%s] %s: %s" % (date_value, record["record_type"], record["title"], record["summary"])
        )
    if len(lines) == 3:
        lines.append("- No timeline events yet.")
    return "\n".join(lines)


def build_interventions_context(records: List[Dict]) -> str:
    interventions = sorted(
        [record for record in records if record["record_type"] == "Intervention"],
        key=lambda item: (item.get("start_date") or item.get("date") or "9999-99-99", item["title"]),
    )
    lines = ["# Interventions", "", "## Ledger"]
    for record in interventions:
        lines.append(
            "- `%s` -> `%s` | %s | status=%s | %s"
            % (
                record.get("start_date") or record.get("date") or "unknown",
                record.get("end_date") or "ongoing",
                record["title"],
                record.get("status", "active"),
                record["summary"],
            )
        )
    if len(lines) == 3:
        lines.append("- No interventions recorded yet.")
    return "\n".join(lines)


def build_patterns_context(insights: List[Dict]) -> str:
    lines = ["# Patterns", "", "## Current Hypotheses"]
    for insight in insights:
        lines.append("- `%s` %s" % (insight.get("date") or "undated", insight["statement"]))
        for question in insight.get("open_questions", []):
            lines.append("  Open question: %s" % question)
    if len(lines) == 3:
        lines.append("- No hypotheses have been generated yet.")
    return "\n".join(lines)


def build_reference_context(records: List[Dict]) -> str:
    refs = [record for record in records if record["record_type"] == "ReferenceCase"]
    lines = [
        "# Reference Cases",
        "",
        "External examples stay separate from personal evidence. Use them as prompts, not proof.",
        "",
    ]
    for record in refs:
        lines.append("- `%s` %s (%s)" % (record.get("date") or "undated", record["title"], record.get("origin") or "unknown origin"))
        lines.append("  %s" % record["summary"])
    if not refs:
        lines.append("- No external reference cases imported yet.")
    return "\n".join(lines)


def build_source_status_context(sources: List[Dict], artifacts: List[Dict], records: List[Dict]) -> str:
    artifacts_by_source = defaultdict(int)
    for artifact in artifacts:
        artifacts_by_source[artifact["source_id"]] += 1
    records_by_source = defaultdict(int)
    for record in records:
        if record["source_id"] in SYSTEM_SOURCE_IDS:
            continue
        records_by_source[record["source_id"]] += 1
    lines = ["# Source Status", "", "## Imported Sources"]
    for source in sorted(sources, key=lambda item: item["created_at"]):
        lines.append(
            "- `%s` [%s] artifacts=%s records=%s status=%s"
            % (
                source.get("label") or source["source_id"],
                source["source_type"],
                artifacts_by_source[source["source_id"]],
                records_by_source[source["source_id"]],
                source.get("parser_status") or "unknown",
            )
        )
    if len(lines) == 3:
        lines.append("- No source batches imported yet.")
    return "\n".join(lines)


def build_quick_brief_context(sources: List[Dict], records: List[Dict]) -> str:
    record_counts = Counter(record["record_type"] for record in records)
    latest = sorted(
        [record for record in records if record.get("date") or record.get("start_date")],
        key=lambda item: item.get("date") or item.get("start_date") or "",
        reverse=True,
    )[:5]
    lines = [
        "# Quick Brief",
        "",
        "## Executive Summary",
        "OpenHealth currently tracks %s source batch(es) and %s canonical record(s)." % (len(sources), len(records)),
        "",
        "## Record Mix",
    ]
    for record_type, count in sorted(record_counts.items()):
        lines.append("- `%s`: %s" % (record_type, count))
    lines.extend(["", "## Recent Items"])
    for record in latest:
        lines.append("- `%s` [%s] %s" % (record.get("date") or record.get("start_date"), record["record_type"], record["title"]))
    if not latest:
        lines.append("- No dated records yet.")
    return "\n".join(lines)
