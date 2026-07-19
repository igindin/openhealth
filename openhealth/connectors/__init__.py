"""Data connectors — turn exports from devices/apps into canonical records.

Each connector is a pure function that reads an export and returns Observation-
shaped dicts (see openhealth.models.Observation), graded and dated, ready for the
index. Connectors meet people where they are: the lowest-friction one (Apple
Health export) needs only an iPhone.
"""

from . import ics_calendar, weather, withings  # noqa: F401  (whole modules)
from .apple_health import import_apple_health  # noqa: F401
from .garmin import import_garmin  # noqa: F401
from .google_calendar import (  # noqa: F401
    GoogleCalendarClient,
    GoogleCalendarError,
    ensure_derived_calendar,
    list_available_calendars,
    load_google_calendar_config,
    sync_google_calendar,
)
from .oura import import_oura  # noqa: F401
from .telegram_intake import (  # noqa: F401
    is_allowed,
    load_allowlist,
    update_to_envelope,
    write_card,
    write_envelope,
)
from .todoist import (  # noqa: F401
    TodoistError,
    TodoistNotConfigured,
    fetch_completed,
    fetch_today_tasks,
    health_candidates,
)

__all__ = [
    "weather",
    "ics_calendar",
    "withings",
    "import_apple_health",
    "import_oura",
    "import_garmin",
    "update_to_envelope",
    "write_envelope",
    "write_card",
    "load_allowlist",
    "is_allowed",
    "GoogleCalendarClient",
    "GoogleCalendarError",
    "ensure_derived_calendar",
    "list_available_calendars",
    "load_google_calendar_config",
    "sync_google_calendar",
    "TodoistError",
    "TodoistNotConfigured",
    "fetch_completed",
    "fetch_today_tasks",
    "health_candidates",
]
