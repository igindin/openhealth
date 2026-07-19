"""Unit tests for openhealth.agent_memory (local agent memory, stdlib only).

All storage goes into tmp_path via the explicit ``home`` argument —
the real ~/.openhealth is never touched.
"""

import json
import stat
import time

from openhealth import agent_memory


def _entries_path(home):
    return agent_memory.memory_home(home) / agent_memory.ENTRIES_FILE


def _digest_path(home):
    return agent_memory.memory_home(home) / agent_memory.DIGEST_FILE


# --- summarize ----------------------------------------------------------------


def test_summarize_result_takes_first_sentences_and_caps():
    text = "First observation. Second! Third? Fourth must not get in."
    summary = agent_memory.summarize_result(text)
    assert "First" in summary and "Third" in summary
    assert "Fourth" not in summary

    long_text = "word " * 200 + "."
    assert len(agent_memory.summarize_result(long_text)) <= agent_memory.MAX_SUMMARY_CHARS
    assert agent_memory.summarize_result("") == ""
    assert agent_memory.summarize_result("   \n\t  ") == ""


def test_summarize_result_collapses_whitespace():
    assert agent_memory.summarize_result("a\n\n  b\tc.") == "a b c."


# --- remember -------------------------------------------------------------------


def test_remember_creates_entry_and_files(tmp_path):
    entry = agent_memory.remember("insight", "Recovery is low. Sleep is short. Action: go to bed earlier.", home=tmp_path)
    assert entry["task"] == "insight"
    assert entry["summary"].startswith("Recovery is low.")
    assert entry["tags"] == []
    assert entry["ts"].endswith("Z")

    lines = _entries_path(tmp_path).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == entry

    digest = _digest_path(tmp_path).read_text(encoding="utf-8")
    assert "## insight" in digest
    assert "Recovery is low." in digest


def test_remember_appends_and_keeps_private_modes(tmp_path):
    agent_memory.remember("insight", "One.", home=tmp_path)
    agent_memory.remember("research", "Two.", tags=["magnesium", ""], home=tmp_path)

    lines = _entries_path(tmp_path).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["tags"] == ["magnesium"]

    mode = stat.S_IMODE(_entries_path(tmp_path).stat().st_mode)
    assert mode == 0o600
    dir_mode = stat.S_IMODE(agent_memory.memory_home(tmp_path).stat().st_mode)
    assert dir_mode == 0o700


def test_digest_groups_by_task_and_caps_at_30(tmp_path):
    for i in range(35):
        agent_memory.remember("insight" if i % 2 else "research", "Entry number {}.".format(i), home=tmp_path)
    digest = _digest_path(tmp_path).read_text(encoding="utf-8")
    assert "## insight" in digest and "## research" in digest
    assert "Entry number 34." in digest
    assert "Entry number 0." not in digest  # older than the last 30
    assert digest.count("- 2") <= agent_memory.DIGEST_ENTRIES


# --- load / recall ---------------------------------------------------------------


def test_load_entries_skips_corrupt_lines(tmp_path):
    agent_memory.remember("insight", "A normal entry.", home=tmp_path)
    with _entries_path(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write("{broken json\n")
        fh.write('"not a dict"\n')
    entries = agent_memory.load_entries(home=tmp_path)
    assert len(entries) == 1
    assert entries[0]["summary"] == "A normal entry."


def test_recall_filters_by_task_and_scores_by_terms(tmp_path):
    agent_memory.remember("insight", "About sleep and caffeine.", home=tmp_path)
    agent_memory.remember("research", "Magnesium improves HRV according to a review.", home=tmp_path)
    agent_memory.remember("research", "Caffeine after lunch reduces deep sleep.", home=tmp_path)

    out = agent_memory.recall("research", query="magnesium HRV", home=tmp_path)
    assert out[0]["summary"].startswith("Magnesium")
    assert all(e["task"] == "research" for e in out)

    assert agent_memory.recall("correlations", home=tmp_path) == []
    assert agent_memory.recall("insight", home=tmp_path)[0]["summary"] == "About sleep and caffeine."


def test_recall_without_query_returns_newest_first_with_limit(tmp_path):
    for i in range(7):
        agent_memory.remember("insight", "Finding {}.".format(i), home=tmp_path)
    out = agent_memory.recall("insight", limit=3, home=tmp_path)
    assert len(out) == 3
    assert out[0]["summary"] == "Finding 6."  # newest first when scores tie


def test_recall_empty_memory(tmp_path):
    assert agent_memory.recall("insight", home=tmp_path) == []


# --- format_memory_block ----------------------------------------------------------


def test_format_memory_block_capped_and_labeled(tmp_path):
    entries = [
        {"ts": "2026-06-10T10:00:00Z", "task": "insight", "summary": "x" * 700},
        {"ts": "2026-06-09T10:00:00Z", "task": "insight", "summary": "y" * 700},
    ]
    block = agent_memory.format_memory_block(entries)
    assert block.startswith("Memory of past analyses")
    assert "[2026-06-10 insight]" in block
    assert len(block) <= agent_memory.MAX_MEMORY_BLOCK_CHARS
    assert agent_memory.format_memory_block([]) == ""


# --- forget / clear -----------------------------------------------------------------


def test_forget_drops_only_old_entries(tmp_path):
    agent_memory.remember("insight", "A fresh entry.", home=tmp_path)
    old_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 90 * 86400))
    with _entries_path(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": old_ts, "task": "insight", "summary": "An old entry.", "tags": []}) + "\n")

    assert agent_memory.forget(30, home=tmp_path) == 1
    entries = agent_memory.load_entries(home=tmp_path)
    assert len(entries) == 1
    assert entries[0]["summary"] == "A fresh entry."
    digest = _digest_path(tmp_path).read_text(encoding="utf-8")
    assert "An old entry." not in digest


def test_forget_noop_when_nothing_old(tmp_path):
    agent_memory.remember("insight", "An entry.", home=tmp_path)
    assert agent_memory.forget(30, home=tmp_path) == 0
    assert len(agent_memory.load_entries(home=tmp_path)) == 1


def test_clear_removes_files_and_reports_count(tmp_path):
    agent_memory.remember("insight", "One.", home=tmp_path)
    agent_memory.remember("insight", "Two.", home=tmp_path)
    assert agent_memory.clear(home=tmp_path) == 2
    assert not _entries_path(tmp_path).exists()
    assert not _digest_path(tmp_path).exists()
    assert agent_memory.clear(home=tmp_path) == 0  # idempotent


# --- home resolution ------------------------------------------------------------------


def test_memory_home_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENHEALTH_HOME", str(tmp_path / "custom"))
    assert agent_memory.memory_home() == tmp_path / "custom" / "memory"
    assert agent_memory.memory_home(tmp_path) == tmp_path / "memory"  # explicit arg wins
