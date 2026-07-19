"""Tests for the Todoist connector.

No network: ``urlopen`` is replaced by a fake that serves JSON fixtures and
records every requested URL. Fixtures model the public API shapes:
  * Sync v9 ``completed/get_all`` — {"items": [...], "projects": {id: {...}}}
  * REST v2 ``/tasks`` and ``/projects`` — plain JSON arrays.

Run directly:  PYTHONPATH=$PWD python3 tests/test_todoist.py
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openhealth.connectors import todoist

TOKEN = "test-token-123"


# --------------------------------------------------------------------------- #
# Russian task titles under test
#
# The connector matches Cyrillic task titles via word-prefix stems, so these
# fixtures have to stay Russian — they are what proves Russian support works
# and, just as importantly, that the stems do not fire on look-alike words.
# Written as \uXXXX escapes to keep this file ASCII; the comment on each line
# gives the English meaning.
# --------------------------------------------------------------------------- #

RU_WORKOUT_LEGS      = "\u0422\u0440\u0435\u043d\u0438\u0440\u043e\u0432\u043a\u0430: \u043d\u043e\u0433\u0438"  # "Workout: legs"
RU_PAY_BILLS         = "\u041e\u043f\u043b\u0430\u0442\u0438\u0442\u044c \u0441\u0447\u0435\u0442\u0430"  # "Pay the bills"
RU_PROJECT_HEALTH    = "\u0417\u0434\u043e\u0440\u043e\u0432\u044c\u0435"  # "Health" (project name)
RU_PROJECT_HOUSEHOLD = "\u0411\u044b\u0442"  # "Household" (project name)
RU_EVENING_YOGA      = "\u0412\u0435\u0447\u0435\u0440\u043d\u044f\u044f \u0439\u043e\u0433\u0430"  # "Evening yoga"
RU_APPROVE_CONTRACT  = "\u0421\u043e\u0433\u043b\u0430\u0441\u043e\u0432\u0430\u0442\u044c \u0434\u043e\u0433\u043e\u0432\u043e\u0440"  # "Approve the contract"
RU_RUN_5K            = "\u0411\u0435\u0433 5\u043a"  # "Run 5k"
RU_BUY_MILK          = "\u041a\u0443\u043f\u0438\u0442\u044c \u043c\u043e\u043b\u043e\u043a\u043e"  # "Buy milk"
RU_EVENING_PLAN      = "\u0412\u0435\u0447\u0435\u0440\u043d\u0438\u0439 \u043f\u043b\u0430\u043d"  # "Evening plan" - health only via its label
RU_GO_TO_CLASS       = "\u0421\u0445\u043e\u0434\u0438\u0442\u044c \u043d\u0430 \u0437\u0430\u043d\u044f\u0442\u0438\u0435"  # "Go to a class" - health only via its label
RU_YOGA_TONIGHT      = "\u0419\u043e\u0433\u0430 \u0432\u0435\u0447\u0435\u0440\u043e\u043c"  # "Yoga tonight"

# Inflected forms: the stem must still match the declined Russian word.
RU_BOOK_DOCTOR       = "\u0417\u0430\u043f\u0438\u0441\u0430\u0442\u044c\u0441\u044f \u043a \u0432\u0440\u0430\u0447\u0443"  # "Book a doctor visit" - "vrachu" starts with stem "vrach"
RU_BLOOD_TESTS       = "\u0421\u0434\u0430\u0442\u044c \u0430\u043d\u0430\u043b\u0438\u0437\u044b \u043a\u0440\u043e\u0432\u0438"  # "Get blood tests done" - "analizy" starts with stem "analiz"
RU_WALK_IN_PARK      = "\u041f\u0440\u043e\u0433\u0443\u043b\u044f\u0442\u044c\u0441\u044f \u0432 \u043f\u0430\u0440\u043a\u0435"  # "Take a walk in the park" - "progulyatsya" starts with stem "progul"

# Near misses: a stem appears *inside* a word but not at its start, so a
# substring match would produce a false positive and a prefix match must not.
RU_SEASONAL_SALE     = "\u0421\u0435\u0437\u043e\u043d\u043d\u0430\u044f \u0440\u0430\u0441\u043f\u0440\u043e\u0434\u0430\u0436\u0430"  # "Seasonal sale" - "sezonnaya" contains "son" (sleep)
RU_PERSONAL_REPORT   = "\u041f\u0435\u0440\u0441\u043e\u043d\u0430\u043b\u044c\u043d\u044b\u0439 \u043e\u0442\u0447\u0451\u0442"  # "Personal report" - "personalnyy" contains "son" (sleep)
RU_MEET_AT_STATION   = "\u0412\u0441\u0442\u0440\u0435\u0442\u0438\u0442\u044c \u0443 \u0432\u043e\u043a\u0437\u0430\u043b\u0430"  # "Meet at the station" - "vokzala" contains "zal" (gym)

# Stems, as reported back in the ``matched_keyword`` field.
STEM_WORKOUT  = "\u0442\u0440\u0435\u043d\u0438\u0440"  # "trenir-"
STEM_YOGA     = "\u0439\u043e\u0433"  # "yog-"
STEM_MEDITATE = "\u043c\u0435\u0434\u0438\u0442"  # "medit-"


# --------------------------------------------------------------------------- #
# Fake urlopen plumbing
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(responses, calls):
    """Serve queued payloads in order; remember full URLs and auth headers."""

    def fake(request, timeout=None):
        calls.append({"url": request.full_url, "auth": request.get_header("Authorization")})
        if not responses:
            raise AssertionError("unexpected extra request: %s" % request.full_url)
        return _FakeResponse(responses.pop(0))

    return fake


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

COMPLETED_PAGE = {
    "items": [
        {
            "content": RU_WORKOUT_LEGS,
            "completed_at": "2026-06-10T07:30:00.000000Z",
            "project_id": "2203",
            "item_object": {"labels": ["fitness"]},
        },
        {
            "content": RU_PAY_BILLS,
            "completed_at": "2026-06-10T10:00:00.000000Z",
            "project_id": "2204",
        },
    ],
    "projects": {
        "2203": {"name": RU_PROJECT_HEALTH},
        "2204": {"name": RU_PROJECT_HOUSEHOLD},
    },
}

REST_PROJECTS = [
    {"id": "2203", "name": RU_PROJECT_HEALTH},
    {"id": "2204", "name": RU_PROJECT_HOUSEHOLD},
]

REST_TASKS_TODAY = [
    {
        "content": RU_EVENING_YOGA,
        "project_id": "2203",
        "labels": ["health"],
        "priority": 3,
        "due": {"date": "2026-06-10"},
    },
    {
        "content": RU_APPROVE_CONTRACT,
        "project_id": "2204",
        "labels": [],
        "priority": 1,
        "due": {"date": "2026-06-10"},
    },
]


# --------------------------------------------------------------------------- #
# Configuration / token discovery
# --------------------------------------------------------------------------- #


class TokenConfigTests(unittest.TestCase):
    def test_without_token_raises_honest_not_configured(self):
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope" / "todoist.token"
            with patch.dict("os.environ", {}, clear=True):
                with patch.object(todoist, "TODOIST_TOKEN_PATH", missing):
                    with self.assertRaises(todoist.TodoistNotConfigured) as ctx:
                        todoist.fetch_completed("2026-06-10")
        message = str(ctx.exception)
        self.assertIn("Settings", message)
        self.assertIn("Integrations", message)
        self.assertIn("OPENHEALTH_TODOIST_TOKEN", message)

    def test_not_configured_is_a_todoist_error(self):
        self.assertTrue(issubclass(todoist.TodoistNotConfigured, todoist.TodoistError))

    def test_token_from_env(self):
        with patch.dict("os.environ", {"OPENHEALTH_TODOIST_TOKEN": "  env-token "}, clear=True):
            self.assertEqual(todoist.load_todoist_token(), "env-token")

    def test_token_from_file_fallback(self):
        with TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "todoist.token"
            token_path.write_text("file-token\n", encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True):
                with patch.object(todoist, "TODOIST_TOKEN_PATH", token_path):
                    self.assertEqual(todoist.load_todoist_token(), "file-token")


# --------------------------------------------------------------------------- #
# fetch_completed: parsing + pagination
# --------------------------------------------------------------------------- #


class FetchCompletedTests(unittest.TestCase):
    def test_parses_completed_items(self):
        calls = []
        with patch.object(todoist, "urlopen", _fake_urlopen([COMPLETED_PAGE], calls)):
            items = todoist.fetch_completed("2026-06-10", token=TOKEN)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["content"], RU_WORKOUT_LEGS)
        self.assertEqual(first["completed_at"], "2026-06-10T07:30:00.000000Z")
        self.assertEqual(first["project"], RU_PROJECT_HEALTH)
        self.assertEqual(first["labels"], ["fitness"])
        self.assertEqual(items[1]["labels"], [])
        # Projects resolved from the sync payload itself — no extra REST call.
        self.assertEqual(len(calls), 1)
        self.assertIn("sync/v9/completed/get_all", calls[0]["url"])
        self.assertIn("since=2026-06-10T00%3A00%3A00", calls[0]["url"])
        self.assertIn("until=2026-06-10T23%3A59%3A59", calls[0]["url"])
        self.assertIn("annotate_items=true", calls[0]["url"])
        self.assertEqual(calls[0]["auth"], "Bearer %s" % TOKEN)

    def test_paginates_past_the_page_limit(self):
        limit = todoist.COMPLETED_PAGE_LIMIT
        full_page = {
            "items": [
                {"content": "task %d" % i, "completed_at": "2026-06-10T06:00:00Z", "project_id": "2203"}
                for i in range(limit)
            ],
            "projects": {"2203": {"name": RU_PROJECT_HEALTH}},
        }
        tail_page = {
            "items": [{"content": "tail", "completed_at": "2026-06-10T23:00:00Z", "project_id": "2203"}],
            "projects": {"2203": {"name": RU_PROJECT_HEALTH}},
        }
        calls = []
        with patch.object(todoist, "urlopen", _fake_urlopen([full_page, tail_page], calls)):
            items = todoist.fetch_completed("2026-06-10", token=TOKEN)
        self.assertEqual(len(items), limit + 1)
        self.assertEqual(len(calls), 2)
        self.assertIn("offset=0", calls[0]["url"])
        self.assertIn("offset=%d" % limit, calls[1]["url"])

    def test_project_name_falls_back_to_rest_projects(self):
        sync_page = {
            "items": [{"content": RU_RUN_5K, "completed_at": "2026-06-10T07:00:00Z", "project_id": "2203"}],
            # no "projects" map in the sync payload
        }
        calls = []
        with patch.object(todoist, "urlopen", _fake_urlopen([sync_page, REST_PROJECTS], calls)):
            items = todoist.fetch_completed("2026-06-10", token=TOKEN)
        self.assertEqual(items[0]["project"], RU_PROJECT_HEALTH)
        self.assertEqual(len(calls), 2)
        self.assertIn("rest/v2/projects", calls[1]["url"])

    def test_accepts_date_object_and_rejects_garbage(self):
        from datetime import date

        calls = []
        with patch.object(todoist, "urlopen", _fake_urlopen([{"items": []}], calls)):
            self.assertEqual(todoist.fetch_completed(date(2026, 6, 10), token=TOKEN), [])
        self.assertIn("since=2026-06-10", calls[0]["url"])
        with self.assertRaises(ValueError):
            todoist.fetch_completed("next tuesday", token=TOKEN)

    def test_http_error_becomes_todoist_error(self):
        import io
        from urllib.error import HTTPError

        def boom(request, timeout=None):
            raise HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"error":"no"}'))

        with patch.object(todoist, "urlopen", boom):
            with self.assertRaises(todoist.TodoistError) as ctx:
                todoist.fetch_completed("2026-06-10", token=TOKEN)
        self.assertIn("403", str(ctx.exception))


# --------------------------------------------------------------------------- #
# fetch_today_tasks
# --------------------------------------------------------------------------- #


class FetchTodayTasksTests(unittest.TestCase):
    def test_parses_today_tasks(self):
        calls = []
        with patch.object(todoist, "urlopen", _fake_urlopen([REST_TASKS_TODAY, REST_PROJECTS], calls)):
            tasks = todoist.fetch_today_tasks(token=TOKEN)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(
            tasks[0],
            {
                "content": RU_EVENING_YOGA,
                "due": "2026-06-10",
                "project": RU_PROJECT_HEALTH,
                "labels": ["health"],
                "priority": 3,
            },
        )
        self.assertIn("rest/v2/tasks", calls[0]["url"])
        self.assertIn("filter=today", calls[0]["url"])

    def test_empty_list_makes_no_projects_call(self):
        calls = []
        with patch.object(todoist, "urlopen", _fake_urlopen([[]], calls)):
            self.assertEqual(todoist.fetch_today_tasks(token=TOKEN), [])
        self.assertEqual(len(calls), 1)


# --------------------------------------------------------------------------- #
# health_candidates: Russian + English keywords, labels, non-matches
# --------------------------------------------------------------------------- #


class HealthCandidatesTests(unittest.TestCase):
    def test_russian_keyword_match(self):
        out = todoist.health_candidates(
            [{"content": RU_WORKOUT_LEGS, "labels": [], "completed_at": "2026-06-10T07:30:00Z"}]
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["label_ru"], "workout")
        self.assertEqual(out[0]["source"], "todoist")
        self.assertEqual(out[0]["original"], RU_WORKOUT_LEGS)
        self.assertEqual(out[0]["matched_keyword"], STEM_WORKOUT)
        self.assertEqual(out[0]["completed_at"], "2026-06-10T07:30:00Z")

    def test_english_keyword_match(self):
        out = todoist.health_candidates([{"content": "Morning run 5k", "labels": []}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["label_ru"], "running")
        self.assertEqual(out[0]["matched_keyword"], "run")

    def test_label_match_without_keyword(self):
        out = todoist.health_candidates([{"content": RU_EVENING_PLAN, "labels": ["Health"]}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["label_ru"], "health")
        self.assertEqual(out[0]["matched_keyword"], "label:health")

    def test_fitness_label(self):
        out = todoist.health_candidates([{"content": RU_GO_TO_CLASS, "labels": ["fitness"]}])
        self.assertEqual(out[0]["label_ru"], "fitness")
        self.assertEqual(out[0]["matched_keyword"], "label:fitness")

    def test_keyword_wins_over_label(self):
        out = todoist.health_candidates([{"content": RU_YOGA_TONIGHT, "labels": ["health"]}])
        self.assertEqual(out[0]["label_ru"], "yoga")
        self.assertEqual(out[0]["matched_keyword"], STEM_YOGA)

    def test_non_health_tasks_are_excluded(self):
        out = todoist.health_candidates(
            [
                {"content": RU_BUY_MILK, "labels": []},
                {"content": RU_APPROVE_CONTRACT, "labels": ["work"]},
                {"content": "", "labels": []},
            ]
        )
        self.assertEqual(out, [])

    def test_word_prefix_not_substring(self):
        # The "son" (sleep) stem must not fire inside "sezonnaya"/"personalnyy",
        # and the "zal" (gym) stem must not fire inside "vokzala".
        out = todoist.health_candidates(
            [
                {"content": RU_SEASONAL_SALE, "labels": []},
                {"content": RU_PERSONAL_REPORT, "labels": []},
                {"content": RU_MEET_AT_STATION, "labels": []},
            ]
        )
        self.assertEqual(out, [])

    def test_word_prefix_matches_inflections(self):
        out = todoist.health_candidates(
            [
                {"content": RU_BOOK_DOCTOR, "labels": []},
                {"content": RU_BLOOD_TESTS, "labels": []},
                {"content": RU_WALK_IN_PARK, "labels": []},
            ]
        )
        self.assertEqual([c["label_ru"] for c in out], ["doctor", "lab tests", "walk"])

    def test_missing_labels_key_is_fine(self):
        out = todoist.health_candidates([{"content": "Meditate 10 min"}])
        self.assertEqual(out[0]["label_ru"], "meditation")

    def test_keyword_dictionary_is_extensible_constant(self):
        self.assertIsInstance(todoist.HEALTH_KEYWORDS, dict)
        for stem in (STEM_WORKOUT, STEM_MEDITATE, "walk", "gym", "sleep"):
            self.assertIn(stem, todoist.HEALTH_KEYWORDS)


if __name__ == "__main__":
    unittest.main()
