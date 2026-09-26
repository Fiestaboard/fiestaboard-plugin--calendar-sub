"""Board-geometry conformance for calendar_sub.

Renders the plugin across every board shape FiestaBoard supports (Flagship,
Note, and note arrays from 15x3 to 120x24 -- a FiestaPanel is a note array
sized to a TV) and asserts it never overflows a row/column and, since this
plugin renders a list of events, that a taller board reveals strictly more
than a shorter one that was already full.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytz

from calendar_sub import CalendarSubPlugin
from src.plugins.geometry_conformance import assert_board_conformance

_MANIFEST = json.loads((Path(__file__).parent.parent / "manifest.json").read_text())


def _future_utc(days: int, hour: int, minute: int = 0) -> datetime:
    base = datetime.now(pytz.UTC).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return base + timedelta(days=days)


def _ics_dt(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _build_many_events_ics(count: int = 10) -> str:
    """A calendar with enough distinct events to exercise every board size.

    Board sizes in the conformance matrix range from a 3-row Note up to a
    24-row max array. Ten events, each with a name/location/end time, gives
    the plugin far more content than even the tallest board can show in one
    pass, which is what makes "taller board renders more" a meaningful,
    non-vacuous check rather than something that saturates immediately.
    """
    events = []
    for i in range(count):
        start = _future_utc(days=1 + i, hour=9 + (i % 8), minute=15 * (i % 4))
        end = start + timedelta(minutes=45)
        lines = [
            "BEGIN:VEVENT",
            f"UID:conformance-event-{i}@test",
            # Kept within the manifest's declared events.*.name max_length
            # (22 tiles) -- this suite checks board *geometry*, not the
            # pre-existing, separate question of whether event_name should
            # itself be capped when a real calendar's summary is longer.
            f"SUMMARY:Board Event {i}",
            f"DTSTART:{_ics_dt(start)}",
            f"DTEND:{_ics_dt(end)}",
        ]
        # Every third event has no location, exercising the "skip the
        # field if it's blank" path alongside the common case.
        if i % 3 != 2:
            lines.append(f"LOCATION:Room {100 + i}")
        lines.append("END:VEVENT")
        events.append("\n".join(lines))
    body = "\n".join(events)
    return f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
{body}
END:VCALENDAR
"""


MANY_EVENTS_ICS = _build_many_events_ics()


def _mock_response(ics_text: str) -> MagicMock:
    mock = MagicMock()
    mock.status_code = 200
    mock.content = ics_text.encode("utf-8")
    mock.raise_for_status = MagicMock()
    return mock


def make_plugin() -> CalendarSubPlugin:
    """A fresh, ready-to-render plugin.

    ``requests.get`` is already stubbed by the ``@patch`` on the calling
    test for its whole duration, so every plugin this factory returns --
    the suite calls it several times -- reads from the same mocked
    calendar without ever touching the network.
    """
    plugin = CalendarSubPlugin(_MANIFEST)
    plugin.config = {
        "enabled": True,
        "calendar_url": "https://example.com/conformance.ics",
        "timezone": "UTC",
        "minutes_before": 15,
        "display_duration_minutes": 10,
        "max_events": 20,
        "refresh_seconds": 60,
    }
    return plugin


@patch("calendar_sub.requests.get")
def test_renders_on_every_board_shape(mock_get):
    mock_get.return_value = _mock_response(MANY_EVENTS_ICS)

    assert_board_conformance(
        make_plugin,
        manifest=_MANIFEST,
        strict_growth=True,
        require_note_array_preview=True,
    )
