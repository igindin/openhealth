#!/usr/bin/env python3
"""Generate CAPABILITIES.md from the dashboard registry.

CAPABILITIES.md is the always-current, agent-readable index of what the
OpenHealth dashboards already do: skins, themes, sections, metrics and their
provenance. It is generated from the single source of truth,
``ui/web/assets/registry.json``, so it never drifts from the live registry.

Usage:
    python3 ui/web/gen_capabilities.py            # write CAPABILITIES.md, print path
    python3 ui/web/gen_capabilities.py --check     # do not write; exit 1 if stale

The ``--check`` mode is for CI / pytest (same pattern as
``tests/test_methodology_docs.py``): it re-renders from the registry and
compares against the committed CAPABILITIES.md. A mismatch means someone
edited the registry without regenerating the map (or edited the map by hand).

Determinism: the output contains no dates, timestamps or randomness, so
``--check`` is stable across runs.

Stdlib only. Compatible with Python 3.8+.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGISTRY_PATH = HERE / "assets" / "registry.json"
REPO_ROOT = HERE.parents[1]
OUTPUT_PATH = REPO_ROOT / "CAPABILITIES.md"

GEN_CMD = "python3 ui/web/gen_capabilities.py"
REGISTRY_REL = "ui/web/assets/registry.json"


def _esc(text) -> str:
    """Escape pipe characters so values stay inside Markdown table cells."""
    if text is None:
        return ""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _dash(text) -> str:
    """Render a value for a table cell, using an em dash when empty."""
    cell = _esc(text)
    return cell if cell else "—"


def render_capabilities(registry: dict) -> str:
    """Render the full CAPABILITIES.md text from a parsed registry dict.

    Pure function: same registry in -> same Markdown out. Importable from tests.
    """
    skins = registry.get("skins", [])
    sections = sorted(
        registry.get("sections", []),
        key=lambda s: (s.get("order", 0), s.get("id", "")),
    )
    metrics = registry.get("metrics", [])
    by_id = {m.get("id"): m for m in metrics}

    lines = []
    lines.append("# OpenHealth - карта возможностей")
    lines.append("")
    lines.append(
        "> Сгенерировано из `%s`. Не править руками - правки затрутся."
        % REGISTRY_REL
    )
    lines.append(
        "> Чтобы обновить после изменения реестра, запустить: `%s`." % GEN_CMD
    )
    lines.append("")
    lines.append(
        "Это всегда актуальный, агентно-читаемый индекс того, что дашборды "
        "OpenHealth уже умеют сегодня: скины, темы, разделы и метрики с "
        "провенансом. Источник правды - реестр; оба скина (V1, V2) рендерятся "
        "из него с паритетом. Как расширять систему - см. `EXTENDING.md`."
    )
    lines.append("")
    lines.append("Версия реестра: %s." % _esc(registry.get("version", "—")))
    lines.append("")

    # --- Skins -------------------------------------------------------------
    lines.append("## Скины")
    lines.append("")
    lines.append(
        "Скин - это раскладка и навигация поверх общего движка. Каждый "
        "перечисленный скин рендерится из реестра и поддерживает свои темы."
    )
    lines.append("")
    lines.append("| id | Название | Файл | Темы |")
    lines.append("| --- | --- | --- | --- |")
    for skin in skins:
        themes = ", ".join(_esc(t) for t in skin.get("themes", [])) or "—"
        lines.append(
            "| `%s` | %s | `%s` | %s |"
            % (
                _esc(skin.get("id")),
                _dash(skin.get("label_ru")),
                _esc(skin.get("file")),
                themes,
            )
        )
    lines.append("")

    # --- Sections ----------------------------------------------------------
    lines.append("## Разделы")
    lines.append("")
    lines.append(
        "Разделы определяются в реестре (включая Настройки) и рисуются в обоих "
        "скинах. Порядок задаётся полем `order`."
    )
    lines.append("")
    lines.append("| id | Название | Иконка | Порядок | Метрики |")
    lines.append("| --- | --- | --- | --- | --- |")
    for section in sections:
        metric_ids = section.get("metric_ids", [])
        metric_cell = (
            ", ".join("`%s`" % _esc(mid) for mid in metric_ids)
            if metric_ids
            else "—"
        )
        lines.append(
            "| `%s` | %s | `%s` | %s | %s |"
            % (
                _esc(section.get("id")),
                _dash(section.get("label_ru")),
                _esc(section.get("icon")),
                _esc(section.get("order")),
                metric_cell,
            )
        )
    lines.append("")

    # --- Navigation groups -------------------------------------------------
    groups = sorted(
        registry.get("groups", []),
        key=lambda g: (g.get("order", 0), g.get("id", "")),
    )
    if groups:
        lines.append("## Навигация (группы)")
        lines.append("")
        lines.append(
            "Навигация обоих скинов строится из этих групп (не больше 9): V1 - "
            "сайдбар, V2 - правый навбар (Дом + группы + Настройки). Выбранная "
            "персона может переупорядочить группы (opt-in, по умолчанию выключено)."
        )
        lines.append("")
        lines.append("| id | Название | Иконка | Порядок | Разделы |")
        lines.append("| --- | --- | --- | --- | --- |")
        for g in groups:
            secs = ", ".join("`%s`" % _esc(s) for s in g.get("section_ids", [])) or "—"
            lines.append(
                "| `%s` | %s | `%s` | %s | %s |"
                % (
                    _esc(g.get("id")),
                    _dash(g.get("label_ru")),
                    _esc(g.get("icon")),
                    _esc(g.get("order")),
                    secs,
                )
            )
        lines.append("")

    # --- Knowledge layer pointer ------------------------------------------
    knowledge_sections = [s for s in sections if s.get("kind") == "knowledge"]
    if knowledge_sections:
        ids = ", ".join("`%s`" % _esc(s.get("id")) for s in knowledge_sections)
        lines.append("## Слой знаний")
        lines.append("")
        lines.append(
            "Кураторские справочники (%s) живут в `ui/web/assets/knowledge.json`: "
            "девайсы по категориям, источники протоколов и короткие видео к "
            "метрикам. У каждой записи есть провенанс (ссылка + дата проверки) и "
            "честный уровень доказательности (high/medium/low, соотнесён с C1-C5). "
            "Рендерятся в обоих скинах через `OH.knowledgeView`; попап «?» метрики "
            "показывает её видео и уровень доказательности." % ids
        )
        lines.append("")

    # --- Audience personas -------------------------------------------------
    personas = registry.get("personas", [])
    if personas:
        lines.append("## Аудиторные пресеты (персоны)")
        lines.append("")
        lines.append(
            "Пресеты переставляют навигацию под аудиторию (opt-in, по умолчанию "
            "выключено; при выборе - через `OH.personaGroups`). Эталонные профили "
            "(отмечены V) прописаны полностью. Схема полей - в `personas_schema` реестра."
        )
        lines.append("")
        lines.append(
            "| id | Название | Эталон | Приоритетные группы | Метрик | Девайсов | Источников |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for p in personas:
            pg = ", ".join("`%s`" % _esc(x) for x in p.get("priority_groups", [])) or "—"
            lines.append(
                "| `%s` | %s | %s | %s | %d | %d | %d |"
                % (
                    _esc(p.get("id")),
                    _dash(p.get("label_ru")),
                    "V" if p.get("reference") else "—",
                    pg,
                    len(p.get("focus_metrics", [])),
                    len(p.get("devices", [])),
                    len(p.get("sources", [])),
                )
            )
        lines.append("")

    # --- Metrics -----------------------------------------------------------
    lines.append("## Метрики")
    lines.append("")
    lines.append(
        "Каждая метрика определяется один раз в реестре и автоматически "
        "доступна обоим скинам. Значения приходят из `data.local.json` по id "
        "метрики (или `data_key`); при отсутствии данных показывается демо из "
        "реестра."
    )
    lines.append("")

    for metric in metrics:
        mid = metric.get("id")
        lines.append("### `%s` - %s" % (_esc(mid), _dash(metric.get("label_ru"))))
        lines.append("")
        lines.append("- id: `%s`" % _esc(mid))
        lines.append("- Название (ru): %s" % _dash(metric.get("label_ru")))
        lines.append("- Единица: %s" % _dash(metric.get("unit")))
        lines.append("- Источник: %s" % _dash(metric.get("source")))
        lines.append("- Тип графика: %s" % _dash(metric.get("chart")))
        lines.append("- Раздел: %s" % _dash(metric.get("section")))
        lines.append("- protocol_ref: %s" % _dash(metric.get("protocol_ref")))
        provenance = metric.get("provenance") or {}
        lines.append("- Провенанс:")
        lines.append("  - что: %s" % _dash(provenance.get("what")))
        lines.append("  - как: %s" % _dash(provenance.get("how")))
        lines.append("  - зачем: %s" % _dash(provenance.get("why")))
        lines.append("")

    # Reference any metric ids listed in sections but missing a definition,
    # so the map surfaces registry drift instead of hiding it.
    orphans = []
    for section in sections:
        for mid in section.get("metric_ids", []):
            if mid not in by_id and mid not in orphans:
                orphans.append(mid)
    if orphans:
        lines.append("## Несостыковки реестра")
        lines.append("")
        lines.append(
            "Метрики, упомянутые в разделах, но без определения в `metrics` "
            "(нужно поправить реестр):"
        )
        lines.append("")
        for mid in orphans:
            lines.append("- `%s`" % _esc(mid))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def load_registry() -> dict:
    with REGISTRY_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv) -> int:
    check = "--check" in argv[1:]
    registry = load_registry()
    rendered = render_capabilities(registry)

    if check:
        if not OUTPUT_PATH.exists():
            sys.stderr.write(
                "CAPABILITIES.md missing. Run: %s\n" % GEN_CMD
            )
            return 1
        current = OUTPUT_PATH.read_text(encoding="utf-8")
        if current != rendered:
            sys.stderr.write(
                "CAPABILITIES.md is stale vs %s.\n"
                "Regenerate with: %s\n" % (REGISTRY_REL, GEN_CMD)
            )
            cur_lines = current.splitlines()
            new_lines = rendered.splitlines()
            for i in range(max(len(cur_lines), len(new_lines))):
                old = cur_lines[i] if i < len(cur_lines) else "<none>"
                new = new_lines[i] if i < len(new_lines) else "<none>"
                if old != new:
                    sys.stderr.write("first diff at line %d:\n" % (i + 1))
                    sys.stderr.write("  current:   %s\n" % old)
                    sys.stderr.write("  generated: %s\n" % new)
                    break
            return 1
        return 0

    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(str(OUTPUT_PATH))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
