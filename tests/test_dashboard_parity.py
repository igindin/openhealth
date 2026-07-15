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
KNOWLEDGE_PATH = UIWEB / "assets" / "knowledge.json"
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


class NavGroupsTests(unittest.TestCase):
    def setUp(self):
        self.reg = json.loads(_read(REGISTRY_PATH))

    def test_groups_present_and_capped_at_9(self):
        groups = self.reg.get("groups", [])
        self.assertTrue(groups, "registry must define navigation groups")
        self.assertLessEqual(len(groups), 9, "navigation must stay at <=9 groups in both skins")

    def test_group_shape_and_valid_section_refs(self):
        section_ids = {s["id"] for s in self.reg.get("sections", [])}
        for g in self.reg.get("groups", []):
            with self.subTest(group=g.get("id")):
                for key in ("id", "label_ru", "icon", "order", "section_ids"):
                    self.assertIn(key, g, "group %r missing %s" % (g.get("id"), key))
                for sid in g.get("section_ids", []):
                    self.assertIn(sid, section_ids,
                                  "group %r references unknown section %r" % (g.get("id"), sid))

    def test_both_skins_build_nav_from_groups(self):
        # Nav is derived from the single source (OH.nav / OH.personaGroups), never hardcoded.
        for path in (V1, V2):
            with self.subTest(skin=path.name):
                text = _read(path)
                self.assertTrue("OH.personaGroups" in text or "OH.nav.groups" in text,
                                "%s must build nav from OH.nav/personaGroups" % path.name)


class KnowledgeLayerTests(unittest.TestCase):
    def setUp(self):
        self.reg = json.loads(_read(REGISTRY_PATH))
        self.know = json.loads(_read(KNOWLEDGE_PATH))

    def test_devices_and_sources_are_knowledge_sections(self):
        sections = {s["id"]: s for s in self.reg.get("sections", [])}
        for sid in ("devices", "sources"):
            with self.subTest(section=sid):
                self.assertIn(sid, sections)
                self.assertEqual(sections[sid].get("kind"), "knowledge",
                                 "%s must be a knowledge section" % sid)

    def test_knowledge_entries_carry_provenance_and_evidence(self):
        self.assertTrue(self.know.get("devices"), "knowledge needs devices")
        self.assertTrue(self.know.get("protocol_sources"), "knowledge needs protocol sources")
        for d in self.know.get("devices", []):
            with self.subTest(device=d.get("id")):
                self.assertTrue(d.get("source_url"), "device %r needs a source_url" % d.get("id"))
                self.assertIn("checked_at", d)
                self.assertIn(d.get("evidence_level"), ("high", "medium", "low"))
        for s in self.know.get("protocol_sources", []):
            with self.subTest(source=s.get("id")):
                self.assertTrue(s.get("url"), "source %r needs a url" % s.get("id"))
                self.assertIn(s.get("evidence_level"), ("high", "medium", "low"))

    def test_video_refs_point_at_real_metrics(self):
        metric_ids = {m["id"] for m in self.reg.get("metrics", [])}
        for v in self.know.get("video_refs", []):
            with self.subTest(video=v.get("title")):
                self.assertIn(v.get("metric_id"), metric_ids,
                              "video ref points at unknown metric %r" % v.get("metric_id"))
                self.assertTrue(v.get("url"))


class PersonasTests(unittest.TestCase):
    def setUp(self):
        self.reg = json.loads(_read(REGISTRY_PATH))
        self.know = json.loads(_read(KNOWLEDGE_PATH))

    def test_eleven_personas_with_schema(self):
        self.assertIn("personas_schema", self.reg, "personas need a documented schema")
        self.assertEqual(len(self.reg.get("personas", [])), 11, "expected 11 audience presets")

    def test_three_reference_profiles(self):
        refs = {p["id"] for p in self.reg.get("personas", []) if p.get("reference")}
        self.assertEqual(refs, {"athlete", "biohacker", "low-energy"})

    def test_persona_refs_all_resolve(self):
        group_ids = {g["id"] for g in self.reg.get("groups", [])}
        metric_ids = {m["id"] for m in self.reg.get("metrics", [])}
        device_ids = {d["id"] for d in self.know.get("devices", [])}
        source_ids = {s["id"] for s in self.know.get("protocol_sources", [])}
        for p in self.reg.get("personas", []):
            with self.subTest(persona=p.get("id")):
                for g in p.get("priority_groups", []):
                    self.assertIn(g, group_ids, "persona %r bad group %r" % (p.get("id"), g))
                for m in p.get("focus_metrics", []):
                    self.assertIn(m, metric_ids, "persona %r bad metric %r" % (p.get("id"), m))
                for d in p.get("devices", []):
                    self.assertIn(d, device_ids, "persona %r bad device %r" % (p.get("id"), d))
                for s in p.get("sources", []):
                    self.assertIn(s, source_ids, "persona %r bad source %r" % (p.get("id"), s))

    def test_both_skins_have_persona_picker(self):
        for path in (V1, V2):
            with self.subTest(skin=path.name):
                self.assertIn("personaPicker", _read(path))


class StateContractTests(unittest.TestCase):
    def test_eligibility_metrics_have_required_keys(self):
        reg = json.loads(_read(REGISTRY_PATH))
        for m in reg.get("metrics", []):
            elig = m.get("eligibility")
            if not elig:
                continue
            with self.subTest(metric=m.get("id")):
                for key in ("need", "have_key"):
                    self.assertIn(key, elig, "eligibility of %r needs %s" % (m.get("id"), key))

    def test_engine_exposes_state_contract(self):
        engine = _read(UIWEB / "assets" / "oh-registry.js")
        for token in ("insufficient", "eligibility", "personaGroups", "knowledgeView"):
            with self.subTest(token=token):
                self.assertIn(token, engine, "engine must expose %s" % token)


if __name__ == "__main__":
    unittest.main()
