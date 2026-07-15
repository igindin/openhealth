"""Unit tests for the dashboard bridge server (ui/web/server.py).

No real agent CLIs are launched: shutil.which and subprocess.run are mocked.
The module is loaded by path because ui/web is not a package.
"""

import importlib.util
import json
import stat
import subprocess
from pathlib import Path

import pytest

_SERVER_PATH = Path(__file__).resolve().parent.parent / "ui" / "web" / "server.py"
_spec = importlib.util.spec_from_file_location("bridge_server", _SERVER_PATH)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)


@pytest.fixture(autouse=True)
def oh_home(tmp_path, monkeypatch):
    """Isolate every test from the real ~/.openhealth (config + memory)."""
    home = tmp_path / "oh-home"
    monkeypatch.setenv("OPENHEALTH_HOME", str(home))
    monkeypatch.delenv("OPENHEALTH_LLM_BASE_URL", raising=False)
    return home


# --- prompt assembly ---------------------------------------------------------


def test_build_prompt_insight_has_safety_and_data():
    prompt = server.build_prompt("insight", data_summary="- Recovery: 78%")
    assert "не врач" in prompt
    assert "C1-C5" in prompt
    assert "Recovery: 78%" in prompt
    assert "Отвечай по-русски" in prompt


def test_build_prompt_research_uses_param_with_default():
    assert "«сон»" in server.build_prompt("research", param="сон")
    assert "«HRV»" in server.build_prompt("research")  # default topic


def test_build_prompt_without_data_says_so():
    prompt = server.build_prompt("correlations")
    assert "data.local.json не найден" in prompt
    assert "не выдумывай" in prompt


def test_build_prompt_lang_en():
    assert "Answer in English." in server.build_prompt("insight", lang="en")


def test_build_prompt_rejects_unknown_task():
    with pytest.raises(ValueError):
        server.build_prompt("rm -rf /")


# --- param sanitizing --------------------------------------------------------


def test_sanitize_param_collapses_and_caps():
    assert server.sanitize_param("  a\n\tb   c ") == "a b c"
    assert len(server.sanitize_param("x" * 1000)) == server.MAX_PARAM_LEN
    assert server.sanitize_param(None) == ""


def test_sanitize_param_rejects_non_string():
    with pytest.raises(ValueError):
        server.sanitize_param({"cmd": "evil"})


# --- task whitelist via handle_agent_request ---------------------------------


@pytest.mark.parametrize("task", ["", "evil; rm -rf /", "insights", None, 42])
def test_whitelist_rejects_unknown_tasks(tmp_path, task):
    status, body = server.handle_agent_request({"task": task}, tmp_path)
    assert status == 400
    assert body["status"] == "error"


def test_whitelist_rejects_non_dict_body(tmp_path):
    status, body = server.handle_agent_request(["task", "insight"], tmp_path)
    assert status == 400


def test_transcript_is_honest_stub(tmp_path):
    status, body = server.handle_agent_request({"task": "transcript"}, tmp_path)
    assert status == 200
    assert body["status"] == "ok"
    assert body["stub"] is True
    assert "не подключён" in body["result"]


# --- data summary ------------------------------------------------------------


def test_summarize_data_compact_and_capped():
    data = {
        "date": "понедельник, 9 июня 2026",
        "recovery": 78,
        "hrv": 96,
        "rhr": 52,
        "sleep": 7.2,
        "sleepNeeded": 8.0,
        "trendRec": list(range(40)),  # only the last 14 should appear
        "readiness": "Recovery 78% (зелёная зона)",
        "biomarkers": [{"name": "Bifidobacterium", "value": 5, "unit": "%", "status": "ok"}],
        "connections": {"whoop": {"connected": True}, "oura": {"connected": False}},
    }
    text = server.summarize_data(data)
    assert "Recovery: 78%" in text
    assert "[26," in text and "25," not in text  # trend cut to the last 14
    assert "whoop" in text and "oura" not in text
    assert len(text) <= server.MAX_SUMMARY_CHARS
    assert server.summarize_data(None) == ""
    assert server.summarize_data({}) == ""


def test_load_local_data_reads_only_existing_valid_json(tmp_path):
    assert server.load_local_data(tmp_path) is None
    (tmp_path / server.DATA_FILE).write_text("{not json", encoding="utf-8")
    assert server.load_local_data(tmp_path) is None
    (tmp_path / server.DATA_FILE).write_text(json.dumps({"recovery": 70}), encoding="utf-8")
    assert server.load_local_data(tmp_path) == {"recovery": 70}


# --- agent runner (mocked subprocess) ----------------------------------------


def _which_factory(available):
    return lambda name: "/usr/bin/" + name if name in available else None


def test_run_agent_prefers_claude(tmp_path, monkeypatch):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="ответ агента\n", stderr="")

    monkeypatch.setattr(server.shutil, "which", _which_factory({"claude", "codex"}))
    monkeypatch.setattr(server.subprocess, "run", fake_run)

    out = server.run_agent("промпт с «кавычками» и $(дырками)", tmp_path)
    assert out["status"] == "ok"
    assert out["agent"] == "claude"
    assert out["result"] == "ответ агента"
    assert out["took_ms"] >= 0
    # prompt is a single argv element, no shell involved
    assert calls["cmd"] == ["claude", "-p", "промпт с «кавычками» и $(дырками)", "--output-format", "text"]
    assert "shell" not in calls["kwargs"] or calls["kwargs"]["shell"] is False
    assert calls["kwargs"]["timeout"] == server.AGENT_TIMEOUT_S


def test_run_agent_falls_back_to_codex(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        # codex writes the clean final answer into --output-last-message <file>
        out_file = cmd[cmd.index("--output-last-message") + 1]
        Path(out_file).write_text("чистый ответ", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="session log...\nчистый ответ", stderr="")

    monkeypatch.setattr(server.shutil, "which", _which_factory({"codex"}))
    monkeypatch.setattr(server.subprocess, "run", fake_run)
    out = server.run_agent("p", tmp_path)
    assert out["agent"] == "codex"
    assert out["status"] == "ok"
    assert out["result"] == "чистый ответ"  # file wins over the session log


def test_codex_command_is_safe_and_prompt_is_last():
    cmd = server.build_agent_command("codex", "промпт", "/tmp/x.txt")
    assert cmd[0:2] == ["codex", "exec"]
    assert "--skip-git-repo-check" in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert cmd[-1] == "промпт"  # single argv element, no shell


def test_run_agent_no_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(server.shutil, "which", _which_factory(set()))
    out = server.run_agent("p", tmp_path)
    assert out["status"] == "no_agent"
    assert "Claude Code" in out["message"]


def test_run_agent_timeout(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.setattr(server.shutil, "which", _which_factory({"claude"}))
    monkeypatch.setattr(server.subprocess, "run", fake_run)
    out = server.run_agent("p", tmp_path)
    assert out["status"] == "timeout"
    assert out["agent"] == "claude"


def test_run_agent_cli_error_returns_redacted_stderr_tail(tmp_path, monkeypatch):
    stderr = "boom\napi_key=sk-secret1234567890abc\n" + "x" * 1000
    monkeypatch.setattr(server.shutil, "which", _which_factory({"claude"}))
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr=stderr),
    )
    out = server.run_agent("p", tmp_path)
    assert out["status"] == "error"
    assert "sk-secret" not in out["message"]
    assert len(out["message"]) <= server.MAX_STDERR_TAIL + 32  # tail + "exit 1: " prefix


def test_run_agent_cli_error_falls_back_to_stdout_tail(tmp_path, monkeypatch):
    # e.g. claude prints auth errors to stdout, stderr stays empty
    monkeypatch.setattr(server.shutil, "which", _which_factory({"claude"}))
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="API Error: 401", stderr=""),
    )
    out = server.run_agent("p", tmp_path)
    assert out["status"] == "error"
    assert "401" in out["message"]


def test_handle_agent_request_runs_insight_with_data(tmp_path, monkeypatch):
    (tmp_path / server.DATA_FILE).write_text(json.dumps({"recovery": 64}), encoding="utf-8")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["prompt"] = cmd[2]  # claude -p <prompt> ...
        return subprocess.CompletedProcess(cmd, 0, stdout="наблюдения", stderr="")

    monkeypatch.setattr(server.shutil, "which", _which_factory({"claude"}))
    monkeypatch.setattr(server.subprocess, "run", fake_run)

    status, body = server.handle_agent_request({"task": "insight", "lang": "ru"}, tmp_path)
    assert status == 200
    assert body["status"] == "ok"
    assert body["task"] == "insight"
    assert "Recovery: 64%" in seen["prompt"]
    assert "не врач" in seen["prompt"]


# --- agent config (~/.openhealth/agent.json) ----------------------------------


def test_load_agent_config_defaults_when_missing():
    cfg = server.load_agent_config()
    assert cfg == {"agent": "auto", "model": None, "extra_args": [], "base_url": None}


def test_save_and_load_agent_config_roundtrip(oh_home):
    path = server.save_agent_config({"agent": "codex", "model": "gpt-5.2-codex", "extra_args": ["--foo"]})
    assert path == oh_home / "agent.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    cfg = server.load_agent_config()
    assert cfg == {"agent": "codex", "model": "gpt-5.2-codex", "extra_args": ["--foo"], "base_url": None}


def test_load_agent_config_sanitizes_broken_values(oh_home):
    oh_home.mkdir(parents=True)
    (oh_home / "agent.json").write_text(
        json.dumps({"agent": "rm -rf /", "model": "bad model name!", "extra_args": "not-a-list"}),
        encoding="utf-8",
    )
    assert server.load_agent_config() == {"agent": "auto", "model": None, "extra_args": [], "base_url": None}
    (oh_home / "agent.json").write_text("{broken", encoding="utf-8")
    assert server.load_agent_config()["agent"] == "auto"


def test_sanitize_model():
    assert server.sanitize_model(None) is None
    assert server.sanitize_model("  ") is None
    assert server.sanitize_model("claude-sonnet-4-6") == "claude-sonnet-4-6"
    assert server.sanitize_model("openai/o4-mini") == "openai/o4-mini"
    for bad in ("has space", "x" * 200, "$(evil)", "-leading-dash", 42):
        with pytest.raises(ValueError):
            server.sanitize_model(bad)


def test_handle_config_request_validates_and_persists(oh_home):
    status, body = server.handle_config_request({"agent": "openclaw"})
    assert status == 400  # detected but not selectable

    status, body = server.handle_config_request({"agent": "hermes"})
    assert status == 200  # hermes is now a first-class selectable agent
    assert body["config"]["agent"] == "hermes"

    status, body = server.handle_config_request({"agent": "codex", "model": "o4-mini"})
    assert status == 200
    assert body["config"]["agent"] == "codex"
    assert (oh_home / "agent.json").is_file()
    assert any(a["name"] == "antigravity" and a["binary"] == "agy" for a in body["agents"])

    status, body = server.handle_config_request({"model": "x y"})
    assert status == 400
    assert server.load_agent_config()["model"] == "o4-mini"  # bad POST didn't clobber

    status, body = server.handle_config_request({"model": None})  # reset model
    assert status == 200
    assert server.load_agent_config() == {"agent": "codex", "model": None, "extra_args": [], "base_url": None}


def test_agents_status_reports_availability(monkeypatch):
    monkeypatch.setattr(server.shutil, "which", _which_factory({"claude", "agy"}))
    rows = {r["name"]: r for r in server.agents_status()}
    assert set(rows) == {"claude", "codex", "antigravity", "hermes", "openclaw"}
    assert rows["claude"]["available"] is True
    assert rows["antigravity"]["available"] is True  # via the agy binary
    assert rows["codex"]["available"] is False
    assert rows["openclaw"]["selectable"] is False
    assert rows["hermes"]["selectable"] is True  # first-class selectable agent
    assert rows["codex"]["selectable"] is True


# --- model flag in agent commands ----------------------------------------------


def test_build_agent_command_with_model_flags():
    claude = server.build_agent_command("claude", "p", model="claude-sonnet-4-6")
    assert claude[claude.index("--model") + 1] == "claude-sonnet-4-6"

    codex = server.build_agent_command("codex", "p", "/tmp/x.txt", model="o4-mini")
    assert codex[codex.index("-m") + 1] == "o4-mini"
    assert codex[-1] == "p"  # prompt stays the last argv element

    agy = server.build_agent_command("antigravity", "p", model="gemini-3-pro")
    assert agy[0] == "agy"
    assert agy[agy.index("--model") + 1] == "gemini-3-pro"
    assert agy[-2:] == ["--print", "p"]  # flags before the prompt (Go-style flags)


def test_build_agent_command_extra_args_and_no_model():
    cmd = server.build_agent_command("claude", "p", model=None, extra_args=["--verbose"])
    assert "--model" not in cmd
    assert "--verbose" in cmd


def test_run_agent_honors_configured_agent(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        out_path = cmd[cmd.index("--output-last-message") + 1] if "--output-last-message" in cmd else None
        if out_path:
            Path(out_path).write_text("ok", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(server.shutil, "which", _which_factory({"claude", "codex"}))
    monkeypatch.setattr(server.subprocess, "run", fake_run)

    out = server.run_agent("p", tmp_path, config={"agent": "codex", "model": "o4-mini", "extra_args": []})
    assert out["agent"] == "codex"
    assert len(calls) == 1  # no cascade: only the configured agent ran
    assert calls[0][0] == "codex"
    assert calls[0][calls[0].index("-m") + 1] == "o4-mini"


def test_run_agent_configured_agent_missing_is_no_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(server.shutil, "which", _which_factory({"claude"}))
    out = server.run_agent("p", tmp_path, config={"agent": "antigravity", "model": None, "extra_args": []})
    assert out["status"] == "no_agent"
    assert "antigravity" in out["message"]


def test_run_agent_reads_config_from_disk(tmp_path, monkeypatch):
    server.save_agent_config({"agent": "claude", "model": "opus", "extra_args": []})
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(server.shutil, "which", _which_factory({"claude", "codex"}))
    monkeypatch.setattr(server.subprocess, "run", fake_run)
    out = server.run_agent("p", tmp_path)  # no explicit config -> loads agent.json
    assert out["agent"] == "claude"
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == "opus"


# --- user context preamble -------------------------------------------------------


def test_build_user_context_empty_when_nothing_found(tmp_path):
    assert server.build_user_context(tmp_path / "sub") == ""


def test_build_user_context_reads_agents_md_and_docs(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Правила\nНе диагностируй.", encoding="utf-8")
    (tmp_path / "goal.md").write_text("Цель: понять, что влияет на HRV.", encoding="utf-8")
    (tmp_path / "about-me.md").write_text("Илья, 30+, утренний хронотип.", encoding="utf-8")
    ctx = server.build_user_context(tmp_path)
    assert ctx.startswith("КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ")
    assert "Инструкции пользователя (AGENTS.md):" in ctx
    assert "Не диагностируй." in ctx
    assert "Цель пользователя (goal.md):" in ctx and "влияет на HRV" in ctx
    assert "О пользователе (about-me.md):" in ctx and "хронотип" in ctx


def test_build_user_context_finds_parent_and_claude_md_and_goal_prefix(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("Инструкции из CLAUDE.md", encoding="utf-8")
    (tmp_path / "GOAL-openhealth.md").write_text("Цель из префиксного файла", encoding="utf-8")
    base = tmp_path / "dashboard"
    base.mkdir()
    ctx = server.build_user_context(base)
    assert "Инструкции из CLAUDE.md" in ctx  # parent fallback
    assert "GOAL-openhealth.md" in ctx and "префиксного" in ctx  # goal*.md match


def test_build_user_context_research_lists_files_and_heads(tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    import os as _os
    for i, name in enumerate(["old.md", "mid.md", "fresh.md", "extra.txt"]):
        p = research / name
        p.write_text("Содержимое {} файла {}".format(i, name), encoding="utf-8")
        _os.utime(p, (1000 + i, 1000 + i))  # mtime order: old < mid < fresh < extra
    ctx = server.build_user_context(tmp_path)
    assert "Личные ресёрчи пользователя (research/)" in ctx
    assert "old.md" in ctx and "extra.txt" in ctx  # all names listed
    assert "Содержимое 3 файла extra.txt" in ctx  # freshest gets an excerpt
    assert "Содержимое 0 файла old.md" not in ctx  # only the 3 freshest excerpted


def test_build_user_context_caps_and_prioritizes_agents(tmp_path):
    (tmp_path / "AGENTS.md").write_text("A" * 5000, encoding="utf-8")
    (tmp_path / "goal.md").write_text("G" * 5000, encoding="utf-8")
    research = tmp_path / "research"
    research.mkdir()
    (research / "big.md").write_text("R" * 5000, encoding="utf-8")
    ctx = server.build_user_context(tmp_path)
    assert len(ctx) <= server.MAX_CONTEXT_CHARS
    assert "A" * 100 in ctx  # AGENTS survives
    assert ctx.count("A") >= server.CONTEXT_AGENTS_CHARS - 10  # got its full budget
    assert "G" * 100 in ctx  # goal survives too (capped at 600)


# --- preamble + memory wired into /api/agent --------------------------------------


def test_handle_agent_request_includes_context_and_remembers(tmp_path, oh_home, monkeypatch):
    from openhealth import agent_memory

    (tmp_path / "AGENTS.md").write_text("Правило: личные данные приоритетнее общих знаний.", encoding="utf-8")
    (tmp_path / server.DATA_FILE).write_text(json.dumps({"recovery": 70}), encoding="utf-8")
    prompts = []

    def fake_run(cmd, **kwargs):
        prompts.append(cmd[2])  # claude -p <prompt>
        return subprocess.CompletedProcess(cmd, 0, stdout="Сон укоротился. Recovery ниже базы.", stderr="")

    monkeypatch.setattr(server.shutil, "which", _which_factory({"claude"}))
    monkeypatch.setattr(server.subprocess, "run", fake_run)

    status, body = server.handle_agent_request({"task": "insight"}, tmp_path)
    assert status == 200 and body["status"] == "ok"
    assert "КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ" in prompts[0]
    assert "личные данные приоритетнее" in prompts[0]
    assert "Память прошлых разборов" not in prompts[0]  # first run: no memory yet

    # the run was remembered (in OPENHEALTH_HOME, not the real home)
    entries = agent_memory.load_entries(home=oh_home)
    assert len(entries) == 1
    assert entries[0]["task"] == "insight"
    assert entries[0]["summary"].startswith("Сон укоротился.")

    # second run sees the memory block before the task
    server.handle_agent_request({"task": "insight"}, tmp_path)
    assert "Память прошлых разборов" in prompts[1]
    assert "Сон укоротился." in prompts[1]
    assert prompts[1].index("Память прошлых разборов") < prompts[1].index("Задача:")


def test_build_prompt_places_preamble_before_task():
    prompt = server.build_prompt(
        "insight", user_context="КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ. X", memory_block="Память прошлых разборов: Y"
    )
    assert prompt.index("КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ") < prompt.index("Память прошлых разборов") < prompt.index("Задача:")
    assert "не врач" in prompt


# --- /api/health build stamp -----------------------------------------------------


def test_health_exposes_build_stamp():
    # The UI and the Hermes gateway detect a stale still-running bridge process
    # by this field; a missing build means "restart the bridge".
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", server.BUILD)


# --- visit-prep: doctor-ready report task (Ideabrowser signal) -------------------


def test_visit_prep_is_whitelisted(tmp_path, monkeypatch):
    assert "visit-prep" in server.ALLOWED_TASKS
    # Accepted (not a 400 "unknown task"); mock away agent CLIs so it resolves
    # to a fast no_agent instead of spawning a real claude/codex process.
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    status, body = server.handle_agent_request({"task": "visit-prep"}, tmp_path)
    assert status != 400
    assert body.get("status") == "no_agent"


def test_visit_prep_prompt_is_safe_and_structured():
    p = server.build_prompt("visit-prep", data_summary="- Recovery: 60%")
    assert "не врач" in p                       # safety block prepended
    assert "не медицинское заключение" in p      # explicit not-a-diagnosis framing
    assert "вопросов врачу" in p                 # questions-for-doctor section
    assert "C1-C5" in p                          # evidence grading required


def test_summarize_data_flags_out_of_range_labs():
    data = {
        "date": "7 июля", "recovery": 60,
        "biomarkers": [
            {"name": "RBC", "value": 5.79, "unit": "", "status": "high"},
            {"name": "INR", "value": 1.07, "status": "optimal"},
        ],
    }
    dg = server.summarize_data(data)
    assert "Вне нормы" in dg and "RBC" in dg
    # optimal labs never appear in the flagged line
    assert "INR" not in dg.split("Вне нормы", 1)[1]
