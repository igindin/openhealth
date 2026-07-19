"""Loader for the journal behavior library (WHOOP-style daily check-in catalog).

The catalog itself lives as a static JSON resource in ``openhealth/data/
journal_behaviors.json`` — transcribed from the WHOOP Journal "Select Behaviors"
screens. This is an observational self-tracking reference, not medical guidance.

Pure stdlib, zero external deps (core rule). The JSON is cached after the first
read so repeated module calls stay cheap.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# data/journal_behaviors.json sits next to this module.
_RESOURCE_PATH = Path(__file__).resolve().parent / "data" / "journal_behaviors.json"

_CACHE: Optional[Dict[str, Any]] = None


def _load() -> Dict[str, Any]:
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(_RESOURCE_PATH.read_text(encoding="utf-8"))
    return _CACHE


def library() -> Dict[str, Any]:
    """Return the whole catalog payload (version, categories, behaviors)."""
    return _load()


def all_behaviors() -> List[Dict[str, Any]]:
    """Flat list of every behavior in the catalog."""
    return list(_load()["behaviors"])


def categories() -> List[Dict[str, Any]]:
    """List of {id, label_ru} category descriptors."""
    return list(_load()["categories"])


def _index() -> Dict[str, Dict[str, Any]]:
    return {b["id"]: b for b in _load()["behaviors"]}


def get_behavior(behavior_id: str) -> Optional[Dict[str, Any]]:
    """Look up a single behavior by its stable id, or None if unknown."""
    return _index().get(behavior_id)


def behaviors_in_category(category_id: str) -> List[Dict[str, Any]]:
    """All behaviors belonging to a category id (e.g. ``nutrition``)."""
    return [b for b in _load()["behaviors"] if b["category"] == category_id]


def resolve(query: str) -> Optional[Dict[str, Any]]:
    """Resolve a behavior by id, exact English name, or case-insensitive name.

    Lets a person pick behaviors by friendly name ("Alcohol") or by id
    ("lifestyle.alcohol"). Returns the behavior dict or None.
    """
    idx = _index()
    if query in idx:
        return idx[query]
    lowered = query.strip().lower()
    for b in _load()["behaviors"]:
        if b["name"].lower() == lowered:
            return b
    return None


def known_ids() -> List[str]:
    """Every valid behavior id (sorted)."""
    return sorted(b["id"] for b in _load()["behaviors"])
