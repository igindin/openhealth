"""Params — user-editable registry of every tunable calculation parameter.

The transparency contract: the person can *see and change* the numbers behind
every derived score / insight, and every record produced with a non-default
parameter is visibly stamped as custom.

How it works
------------
- ``REGISTRY`` declares each tunable parameter: default, allowed range, step,
  a Russian label for the UI, where in the code it is consumed, which outputs
  it affects, and where the methodology is documented.
- Overrides live in ``~/.openhealth/params.json`` (private, 0600; the home is
  overridable via ``OPENHEALTH_HOME`` exactly like the journal / calendar
  config). No file -> pure defaults, zero cost: modules never require it.
- ``get(id)`` returns the effective value (override when valid, else default).
  ``set(id, value)`` validates against min/max and persists. ``reset()`` drops
  one or all overrides. ``list_all()`` feeds a settings UI.
- Reproducibility: modules call ``overrides_for(...)`` at compute time and,
  when any override is active, attach ``metadata.params_overrides`` to the
  record and suffix the ``algo_version`` with ``+custom`` (via ``stamp``), so
  every stored output says exactly which custom numbers produced it.

Recovery weight normalization rule: the four ``recovery.weights.*`` values are
*relative* weights. They do not need to sum to 1 — the recovery score divides
by the sum of the weights of the components actually present, so the effective
shares always sum to 1. ``recovery_weights_normalized()`` exposes that view.

Pure stdlib, zero external deps (core rule).
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PARAMS_FILE = "params.json"

# Marker appended to algo versions computed with any non-default parameter.
CUSTOM_SUFFIX = "+custom"

# id -> spec. Keep ids stable: they are persisted in overrides files and in
# record metadata (params_overrides). ``wired: False`` marks parameters that
# are registered (visible / editable) but whose consumer module has not been
# switched to the registry yet — the UI must show them as "пока не подключено".
REGISTRY: Dict[str, Dict[str, Any]] = {
    # --- recovery score (modules/recovery.py) --------------------------------
    "recovery.weights.hrv": {
        "default": 0.60, "min": 0.05, "max": 0.90, "step": 0.05,
        "label_ru": "Вес HRV в recovery", "unit": "вес",
        "where": "recovery.recovery_score", "affects": ["recovery_score"],
        "doc": "docs/methodology/recovery.md", "group": "recovery_weights", "wired": True,
    },
    "recovery.weights.rhr": {
        "default": 0.20, "min": 0.0, "max": 0.50, "step": 0.05,
        "label_ru": "Вес пульса покоя в recovery", "unit": "вес",
        "where": "recovery.recovery_score", "affects": ["recovery_score"],
        "doc": "docs/methodology/recovery.md", "group": "recovery_weights", "wired": True,
    },
    "recovery.weights.respiratory": {
        "default": 0.15, "min": 0.0, "max": 0.50, "step": 0.05,
        "label_ru": "Вес частоты дыхания в recovery", "unit": "вес",
        "where": "recovery.recovery_score", "affects": ["recovery_score"],
        "doc": "docs/methodology/recovery.md", "group": "recovery_weights", "wired": True,
    },
    "recovery.weights.sleep": {
        "default": 0.05, "min": 0.0, "max": 0.50, "step": 0.05,
        "label_ru": "Вес сна в recovery", "unit": "вес",
        "where": "recovery.recovery_score", "affects": ["recovery_score"],
        "doc": "docs/methodology/recovery.md", "group": "recovery_weights", "wired": True,
    },
    "recovery.baseline_window_days": {
        "default": 28, "min": 7, "max": 60, "step": 1,
        "label_ru": "Окно личного baseline", "unit": "дни",
        "where": "recovery.from_index", "affects": ["recovery_score"],
        "doc": "docs/methodology/recovery.md", "group": "recovery", "wired": True,
    },
    "recovery.hrv_full_swing_sd": {
        "default": 2.0, "min": 1.0, "max": 4.0, "step": 0.25,
        "label_ru": "Насыщение HRV-компонента", "unit": "SD ln(rMSSD)",
        "where": "recovery.hrv_component", "affects": ["recovery_score"],
        "doc": "docs/methodology/recovery.md", "group": "recovery", "wired": True,
    },
    "recovery.sleep_need_h": {
        "default": 8.0, "min": 6.0, "max": 10.0, "step": 0.25,
        "label_ru": "Личная потребность во сне", "unit": "ч",
        "where": "recovery.sleep_debt", "affects": ["sleep_debt"],
        "doc": "docs/methodology/recovery.md", "group": "recovery", "wired": True,
    },
    # --- correlations (modules/correlations.py) ------------------------------
    "correlations.min_yes_days": {
        "default": 5, "min": 3, "max": 10, "step": 1,
        "label_ru": "Минимум дней «да» для анализа", "unit": "дни",
        "where": "correlations.behavior_impact", "affects": ["behavior_impact"],
        "doc": "docs/methodology/correlations.md", "group": "correlations", "wired": True,
    },
    "correlations.min_no_days": {
        "default": 5, "min": 3, "max": 10, "step": 1,
        "label_ru": "Минимум дней «нет» для анализа", "unit": "дни",
        "where": "correlations.behavior_impact", "affects": ["behavior_impact"],
        "doc": "docs/methodology/correlations.md", "group": "correlations", "wired": True,
    },
    "correlations.window_days": {
        "default": 90, "min": 30, "max": 180, "step": 5,
        "label_ru": "Окно анализа поведения", "unit": "дни",
        "where": "correlations.from_index", "affects": ["behavior_impact"],
        "doc": "docs/methodology/correlations.md", "group": "correlations", "wired": True,
    },
    "correlations.lag_days": {
        "default": 0, "min": 0, "max": 3, "step": 1,
        "label_ru": "Лаг: поведение → recovery через N дней", "unit": "дни",
        "where": "correlations.from_index", "affects": ["behavior_impact"],
        "doc": "docs/methodology/correlations.md", "group": "correlations", "wired": True,
    },
    # --- insight detectors (insights.py) --------------------------------------
    "insights.sleep_goal_h": {
        "default": 8.0, "min": 6.0, "max": 10.0, "step": 0.25,
        "label_ru": "Цель сна для детектора недосыпа", "unit": "ч",
        "where": "insights.detect_sleep_debt", "affects": ["insight-sleep_debt"],
        "doc": "docs/methodology/insights.md", "group": "insights", "wired": True,
    },
    "insights.sleep_debt_week_attention_h": {
        "default": 5.0, "min": 2.0, "max": 10.0, "step": 0.5,
        "label_ru": "Недосып за 7 ночей: порог внимания", "unit": "ч",
        "where": "insights.detect_sleep_debt", "affects": ["insight-sleep_debt"],
        "doc": "docs/methodology/insights.md", "group": "insights", "wired": True,
    },
    "insights.sleep_debt_week_warning_h": {
        "default": 10.0, "min": 5.0, "max": 20.0, "step": 0.5,
        "label_ru": "Недосып за 7 ночей: порог тревоги", "unit": "ч",
        "where": "insights.detect_sleep_debt", "affects": ["insight-sleep_debt"],
        "doc": "docs/methodology/insights.md", "group": "insights", "wired": True,
    },
    "insights.hrv_drop_attention_pct": {
        "default": 8.0, "min": 3.0, "max": 20.0, "step": 1.0,
        "label_ru": "Падение HRV: порог внимания", "unit": "%",
        "where": "insights.detect_hrv_downtrend", "affects": ["insight-hrv_downtrend"],
        "doc": "docs/methodology/insights.md", "group": "insights", "wired": True,
    },
    "insights.hrv_drop_warning_pct": {
        "default": 15.0, "min": 8.0, "max": 30.0, "step": 1.0,
        "label_ru": "Падение HRV: порог тревоги", "unit": "%",
        "where": "insights.detect_hrv_downtrend", "affects": ["insight-hrv_downtrend"],
        "doc": "docs/methodology/insights.md", "group": "insights", "wired": True,
    },
    "insights.rhr_rise_attention_bpm": {
        "default": 3.0, "min": 1.0, "max": 8.0, "step": 0.5,
        "label_ru": "Рост пульса покоя: порог внимания", "unit": "уд/мин",
        "where": "insights.detect_rhr_uptrend", "affects": ["insight-rhr_uptrend"],
        "doc": "docs/methodology/insights.md", "group": "insights", "wired": True,
    },
    "insights.rhr_rise_warning_bpm": {
        "default": 6.0, "min": 3.0, "max": 12.0, "step": 0.5,
        "label_ru": "Рост пульса покоя: порог тревоги", "unit": "уд/мин",
        "where": "insights.detect_rhr_uptrend", "affects": ["insight-rhr_uptrend"],
        "doc": "docs/methodology/insights.md", "group": "insights", "wired": True,
    },
    "insights.red_streak_days": {
        "default": 3, "min": 2, "max": 7, "step": 1,
        "label_ru": "Длина серии красных дней", "unit": "дни",
        "where": "insights.detect_recovery_red_streak", "affects": ["insight-recovery_red_streak"],
        "doc": "docs/methodology/insights.md", "group": "insights", "wired": True,
    },
    "insights.weekend_diff_points": {
        "default": 5.0, "min": 2.0, "max": 15.0, "step": 1.0,
        "label_ru": "Разрыв будни/выходные по recovery", "unit": "пункты",
        "where": "insights.detect_weekend_pattern", "affects": ["insight-weekend_pattern"],
        "doc": "docs/methodology/insights.md", "group": "insights", "wired": True,
    },
    "insights.sleep_consistency_stdev_h": {
        "default": 1.2, "min": 0.5, "max": 3.0, "step": 0.1,
        "label_ru": "Допустимый разброс длительности сна", "unit": "ч (SD)",
        "where": "insights.detect_sleep_consistency", "affects": ["insight-sleep_consistency"],
        "doc": "docs/methodology/insights.md", "group": "insights", "wired": True,
    },
    # --- weather factors (weather_insights.py; consumer not switched yet) -----
    "weather.pressure_change_hpa": {
        "default": 6.0, "min": 3.0, "max": 15.0, "step": 1.0,
        "label_ru": "Скачок давления за сутки", "unit": "гПа",
        "where": "weather_insights._derive_active_factors", "affects": ["weather-insight"],
        "doc": "docs/methodology/weather-flags.md", "group": "weather", "wired": False,
    },
    "weather.heat_apparent_max_c": {
        "default": 28.0, "min": 24.0, "max": 35.0, "step": 1.0,
        "label_ru": "Порог жаровой нагрузки", "unit": "°C (ощущаемая)",
        "where": "weather_insights._derive_active_factors", "affects": ["weather-insight"],
        "doc": "docs/methodology/weather-flags.md", "group": "weather", "wired": False,
    },
    # --- calendar day load (connectors/ics_calendar.py; not switched yet) -----
    "day_load.weights.busy_hours": {
        "default": 70, "min": 0, "max": 100, "step": 5,
        "label_ru": "Вес занятых часов в нагрузке дня", "unit": "пункты",
        "where": "ics_calendar.day_load", "affects": ["day_load_score"],
        "doc": "docs/methodology/day-load.md", "group": "day_load", "wired": False,
    },
    "day_load.weights.meetings": {
        "default": 20, "min": 0, "max": 100, "step": 5,
        "label_ru": "Вес числа встреч в нагрузке дня", "unit": "пункты",
        "where": "ics_calendar.day_load", "affects": ["day_load_score"],
        "doc": "docs/methodology/day-load.md", "group": "day_load", "wired": False,
    },
    "day_load.weights.no_recovery_gap": {
        "default": 10, "min": 0, "max": 50, "step": 5,
        "label_ru": "Надбавка за день без окна отдыха", "unit": "пункты",
        "where": "ics_calendar.day_load", "affects": ["day_load_score"],
        "doc": "docs/methodology/day-load.md", "group": "day_load", "wired": False,
    },
}

RECOVERY_WEIGHT_IDS = (
    "recovery.weights.hrv",
    "recovery.weights.rhr",
    "recovery.weights.respiratory",
    "recovery.weights.sleep",
)


# --- storage ------------------------------------------------------------------


def params_home() -> Path:
    """``~/.openhealth``, overridable via ``OPENHEALTH_HOME`` (tests, portability)."""
    return Path(os.environ.get("OPENHEALTH_HOME") or "~/.openhealth").expanduser()


def params_path() -> Path:
    return params_home() / PARAMS_FILE


def _coerce(param_id: str, value: Any) -> float:
    """Validate a candidate value against the registry. Raises on bad input."""
    spec = REGISTRY[param_id]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("param %r: value must be a number, got %r" % (param_id, value))
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        raise ValueError("param %r: value must be finite" % param_id)
    if not (spec["min"] <= out <= spec["max"]):
        raise ValueError(
            "param %r: %s is out of range [%s, %s]" % (param_id, out, spec["min"], spec["max"])
        )
    if isinstance(spec["default"], int):
        return int(round(out))
    return out


def _read_raw() -> Dict[str, Any]:
    """Raw overrides file content; a missing or corrupt file is just empty."""
    path = params_path()
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def load_overrides() -> Dict[str, float]:
    """Validated overrides only: unknown ids and out-of-range values are dropped.

    A hand-edited bad entry must never crash a module — it silently falls back
    to the default (the file stays untouched; ``set``/``reset`` clean it up).
    """
    out: Dict[str, float] = {}
    for key, value in _read_raw().items():
        if key not in REGISTRY:
            continue
        try:
            out[key] = _coerce(key, value)
        except ValueError:
            continue
    return out


def _write_overrides(overrides: Dict[str, float]) -> None:
    home = params_home()
    home.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(home, 0o700)
    except OSError:
        pass
    path = params_path()
    if not overrides:
        # No overrides left -> remove the file entirely (pure-defaults state).
        try:
            path.unlink()
        except OSError:
            pass
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(overrides, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# --- public API -----------------------------------------------------------------


def get(param_id: str) -> float:
    """Effective value: a valid override when present, else the default."""
    if param_id not in REGISTRY:
        raise KeyError("unknown param %r" % param_id)
    overrides = load_overrides()
    if param_id in overrides:
        return overrides[param_id]
    return REGISTRY[param_id]["default"]


def set(param_id: str, value: Any) -> float:  # noqa: A001 - deliberate, mirrors get/set
    """Validate and persist an override. Returns the stored value."""
    if param_id not in REGISTRY:
        raise KeyError("unknown param %r" % param_id)
    coerced = _coerce(param_id, value)
    overrides = load_overrides()
    if coerced == REGISTRY[param_id]["default"]:
        overrides.pop(param_id, None)  # setting back to default == reset
    else:
        overrides[param_id] = coerced
    _write_overrides(overrides)
    return coerced


def reset(param_id: Optional[str] = None) -> int:
    """Drop one override (or all when ``param_id`` is None / "all").

    Returns how many overrides were removed. Unknown ids raise KeyError so a
    typo in the UI/bridge surfaces instead of silently doing nothing.
    """
    overrides = load_overrides()
    if param_id is None or param_id == "all":
        removed = len(overrides)
        _write_overrides({})
        return removed
    if param_id not in REGISTRY:
        raise KeyError("unknown param %r" % param_id)
    removed = 1 if param_id in overrides else 0
    overrides.pop(param_id, None)
    _write_overrides(overrides)
    return removed


def list_all() -> List[Dict[str, Any]]:
    """Full registry view for a settings UI, with effective values.

    For the ``recovery_weights`` group each entry also carries its normalized
    share (weights are relative; effective shares always sum to 1).
    """
    overrides = load_overrides()
    weight_total = sum(
        overrides.get(wid, REGISTRY[wid]["default"]) for wid in RECOVERY_WEIGHT_IDS
    )
    rows: List[Dict[str, Any]] = []
    for param_id in sorted(REGISTRY):
        spec = REGISTRY[param_id]
        value = overrides.get(param_id, spec["default"])
        row = {
            "id": param_id,
            "label_ru": spec["label_ru"],
            "unit": spec["unit"],
            "group": spec["group"],
            "value": value,
            "default": spec["default"],
            "overridden": param_id in overrides,
            "min": spec["min"],
            "max": spec["max"],
            "step": spec["step"],
            "where": spec["where"],
            "affects": list(spec["affects"]),
            "doc": spec["doc"],
            "wired": spec["wired"],
        }
        if param_id in RECOVERY_WEIGHT_IDS and weight_total > 0:
            row["normalized_share"] = round(value / weight_total, 4)
        rows.append(row)
    return rows


def recovery_weights_normalized() -> Dict[str, float]:
    """The four recovery weights normalized to sum exactly 1 (the rule)."""
    raw = {wid.rsplit(".", 1)[1]: get(wid) for wid in RECOVERY_WEIGHT_IDS}
    total = sum(raw.values())
    if total <= 0:
        raise ValueError("recovery weights must sum to a positive value")
    return {key: value / total for key, value in raw.items()}


# --- reproducibility helpers (for module metadata) -----------------------------


def overrides_for(param_ids: Iterable[str]) -> Dict[str, float]:
    """Active overrides among ``param_ids`` — for ``metadata.params_overrides``."""
    overrides = load_overrides()
    return {pid: overrides[pid] for pid in param_ids if pid in overrides}


def stamp(algo_version: str, overrides: Dict[str, Any]) -> str:
    """Suffix an algo version with ``+custom`` when custom params were active."""
    if overrides and not algo_version.endswith(CUSTOM_SUFFIX):
        return algo_version + CUSTOM_SUFFIX
    return algo_version
