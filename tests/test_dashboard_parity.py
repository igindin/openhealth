"""Parity tests for the two dashboard skins (Result 1).

Both skins (V1 classic dark = dashboard.html, V2 bento light = dashboard-v2.html)
must render from ONE source of truth: ui/web/assets/registry.json via the shared
loader. These tests lock that in statically (no browser needed) so the skins
cannot silently diverge:

  - registry.json is well-formed and defines the Today + Settings sections;
  - both skins load the shared engine (oh-registry.js + oh-charts.js);
  - both expose __renderManifest() and seed Today from the registry;
  - Settings exists in both skins and both can switch skin;
  - index.html is the skin router (not a stale copy of a dashboard);
  - CAPABILITIES.md is regenerated from the registry (anti-drift, same idea as
    tests/test_methodology_docs.py).
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UIWEB = REPO_ROOT / "ui" / "web"
REGISTRY_PATH = UIWEB / "assets" / "registry.json"
V1 = UIWEB / "dashboard.html"
V2 = UIWEB / "dashboard-v2.html"
INDEX = UIWEB / "index.html"
CAPABILITIES = REPO_ROOT / "CAPABILITIES.md"
EXTENDING = REPO_ROOT / "EXTENDING.md"

TODAY_METRICS = {"recovery", "hrv", "rhr", "sleep", "strain", "recovery_trend_30"}


def _read(path):
    return path.read_text(encoding="utf-8")


def _load_gen():
    """Import ui/web/gen_capabilities.py without polluting sys.path."""
    spec = importlib.util.spec_from_file_location(
        "gen_capabilities", str(UIWEB / "gen_capabilities.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RegistryIntegrityTests(unittest.TestCase):
    def setUp(self):
        with REGISTRY_PATH.open("r", encoding="utf-8") as fh:
            self.reg = json.load(fh)

    def test_has_skins_v1_v2(self):
        ids = {s.get("id") for s in self.reg.get("skins", [])}
        self.assertEqual({"v1", "v2"}, ids)

    def test_today_and_settings_sections_exist(self):
        sections = {s.get("id"): s for s in self.reg.get("sections", [])}
        self.assertIn("today", sections)
        self.assertIn("settings", sections, "Settings must be a registry section")

    def test_today_metric_ids(self):
        sections = {s.get("id"): s for s in self.reg.get("sections", [])}
        self.assertEqual(TODAY_METRICS, set(sections["today"]["metric_ids"]))

    def test_result2_sections_present(self):
        ids = {s.get("id") for s in self.reg.get("sections", [])}
        for sid in ("sleep", "strain", "stress", "body"):
            with self.subTest(section=sid):
                self.assertIn(sid, ids, "Result 2 section %r missing from registry" % sid)

    def test_every_section_metric_is_defined(self):
        by_id = {m.get("id") for m in self.reg.get("metrics", [])}
        for section in self.reg.get("sections", []):
            for mid in section.get("metric_ids", []):
                with self.subTest(metric=mid):
                    self.assertIn(mid, by_id, "metric %r used but not defined" % mid)

    def test_metrics_carry_provenance_and_chart(self):
        for m in self.reg.get("metrics", []):
            with self.subTest(metric=m.get("id")):
                self.assertTrue(m.get("chart"), "metric %r needs a chart type" % m.get("id"))
                self.assertIn("unit", m, "metric %r needs a unit key" % m.get("id"))
                prov = m.get("provenance") or {}
                for key in ("what", "how", "why"):
                    self.assertTrue(prov.get(key), "metric %r provenance.%s empty" % (m.get("id"), key))


class SkinsLoadSharedEngineTests(unittest.TestCase):
    def test_both_skins_load_registry_and_chart_kit(self):
        for path in (V1, V2):
            text = _read(path)
            with self.subTest(skin=path.name):
                self.assertIn("assets/oh-registry.js", text)
                self.assertIn("assets/oh-charts.js", text)
                self.assertIn("assets/oh-correlate.js", text)  # always-on DnD correlations
                self.assertIn("assets/oh-provenance.js", text)  # provenance "?" + algorithms

    def test_both_skins_render_registry_sections(self):
        # Both skins render whole registry sections via the shared OH.sectionView,
        # so sleep/strain/stress/body appear in both with no skin-specific markup.
        for path in (V1, V2):
            with self.subTest(skin=path.name):
                self.assertIn("OH.sectionView", _read(path))

    def test_both_skins_expose_render_manifest(self):
        for path in (V1, V2):
            with self.subTest(skin=path.name):
                self.assertIn("__renderManifest", _read(path))

    def test_both_skins_seed_today_from_registry(self):
        # V1 uses ohSeedToday(), V2 uses seedTodayFromRegistry(); both read OH.value.
        self.assertIn("ohSeedToday", _read(V1))
        self.assertIn("seedTodayFromRegistry", _read(V2))

    def test_both_skins_render_today_tiles_from_registry(self):
        # Tiles are built dynamically from OH.sectionMetrics('today'), not hardcoded
        # markup, so a new tile metric in registry.json shows up in BOTH skins with
        # no skin edits.
        for path in (V1, V2):
            with self.subTest(skin=path.name):
                self.assertIn("OH.sectionMetrics('today')", _read(path))

    def test_v2_no_longer_hardcodes_today_scalars(self):
        text = _read(V2)
        self.assertNotIn("recovery: 64", text, "V2 still hardcodes recovery; must come from registry")
        self.assertNotIn("hrv: 39", text, "V2 still hardcodes hrv; must come from registry")


class SettingsAndSwitcherTests(unittest.TestCase):
    def test_settings_section_in_both_skins(self):
        self.assertIn('id="z-settings"', _read(V1))
        self.assertIn('id="sec-settings"', _read(V2))

    def test_skin_switcher_in_both_skins(self):
        for path in (V1, V2):
            with self.subTest(skin=path.name):
                self.assertIn("setSkin", _read(path))


class IndexRouterTests(unittest.TestCase):
    def test_index_is_a_small_router(self):
        self.assertTrue(INDEX.exists())
        text = _read(INDEX)
        # Router, not a multi-hundred-KB duplicate of a dashboard.
        self.assertLess(len(text), 4000, "index.html should be a tiny skin router")
        self.assertIn("oh.skin", text)
        self.assertIn("dashboard.html", text)
        self.assertIn("dashboard-v2.html", text)


class CapabilitiesAndDocsTests(unittest.TestCase):
    def test_extending_doc_exists(self):
        self.assertTrue(EXTENDING.is_file(), "EXTENDING.md missing")

    def test_capabilities_map_is_up_to_date(self):
        gen = _load_gen()
        rendered = gen.render_capabilities(gen.load_registry())
        self.assertTrue(CAPABILITIES.is_file(), "CAPABILITIES.md missing — run gen_capabilities.py")
        self.assertEqual(
            _read(CAPABILITIES),
            rendered,
            "CAPABILITIES.md is stale. Regenerate: python3 ui/web/gen_capabilities.py",
        )


if __name__ == "__main__":
    unittest.main()
