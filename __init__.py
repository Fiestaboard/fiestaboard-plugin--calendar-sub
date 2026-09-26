"""Calendar Subscription plugin for FiestaBoard.

Fetches a public iCalendar (.ics) URL and displays upcoming events.
Supports both normal template variables and event-based triggers that
interrupt the board display a configurable number of minutes before
each event starts.
"""

import hashlib
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytz
import recurring_ical_events
import requests
from icalendar import Calendar

from src.plugins.base import PluginBase, PluginResult, TriggerResult
from src.text_to_board import count_tiles, take_tiles

logger = logging.getLogger(__name__)

# How far ahead to scan for upcoming events when building template variables
_LOOK_AHEAD_DAYS = 30

# Duration used when display_duration_minutes is 0 ("stay until overwritten")
_INDEFINITE_DURATION_SECONDS = 86400

# Fallback geometry when no board is bound (self.board is None) -- matches
# the Flagship, the device every board-agnostic caller historically assumed.
_DEFAULT_ROWS = 6
_DEFAULT_COLS = 22


def _center(text: str, width: int) -> str:
    """Center *text* in a field *width* tiles wide, truncating if needed.

    Tile-aware (via count_tiles/take_tiles) rather than character-based, so
    a color marker would cost one tile, not several characters -- matching
    how the board itself measures width. This plugin's own text has no such
    markers today, but the helper is safe if that ever changes.
    """
    if width <= 0:
        return ""
    text, _ = take_tiles(text, width)
    pad = width - count_tiles(text)
    left = pad // 2
    right = pad - left
    return (" " * left) + text + (" " * right)


def _normalize_url(url: str) -> str:
    """Rewrite webcal:// to https:// for HTTP transport."""
    if url.startswith("webcal://"):
        return "https://" + url[len("webcal://"):]
    if url.startswith("webcal:"):
        return "https:" + url[len("webcal:"):]
    return url


def _dt_to_aware(dt: Any, tz: Any) -> datetime:
    """Convert a date or datetime to a timezone-aware datetime.

    All-day events come back as ``date`` objects; we treat them as
    midnight in the configured timezone.
    """
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return tz.localize(dt)
        return dt.astimezone(tz)
    # Plain date (all-day event) — treat as midnight local time
    return tz.localize(datetime(dt.year, dt.month, dt.day, 0, 0, 0))


def _format_time(dt: datetime) -> str:
    """Return a short human-readable time string, e.g. '3:30 PM'."""
    return dt.strftime("%-I:%M %p").lstrip("0") if dt.hour != 0 or dt.minute != 0 else "All Day"


def _format_date(dt: datetime) -> str:
    """Return a short human-readable date string, e.g. 'Apr 3'."""
    return dt.strftime("%b %-d")


def _event_trigger_id(event: Dict[str, Any]) -> str:
    """Build a stable dedup key from the event UID and start time."""
    uid = str(event.get("uid", ""))
    start = str(event.get("start_raw", ""))
    return "cal_" + hashlib.md5(f"{uid}:{start}".encode(), usedforsecurity=False).hexdigest()[:12]


class CalendarSubPlugin(PluginBase):
    """Calendar Subscription plugin.

    Fetches events from a public .ics URL, exposes them as template
    variables, and fires board triggers before each event.
    """

    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)
        self._events_cache: List[Dict[str, Any]] = []

    @property
    def plugin_id(self) -> str:
        return "calendar_sub"

    # ------------------------------------------------------------------
    # Config validation
    # ------------------------------------------------------------------

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        errors = []

        url = config.get("calendar_url") or os.getenv("CALENDAR_SUB_URL", "")
        if not url:
            errors.append("Calendar URL is required")
        else:
            normalized = _normalize_url(url)
            if not (normalized.startswith("http://") or normalized.startswith("https://")):
                errors.append("Calendar URL must be an http:// or https:// (or webcal://) URL")

        timezone_str = config.get("timezone", "America/Los_Angeles")
        try:
            pytz.timezone(timezone_str)
        except pytz.exceptions.UnknownTimeZoneError:
            errors.append(f"Invalid timezone: {timezone_str}")

        errors.extend(self._validate_refresh_seconds(config))
        return errors

    def on_config_change(self, old_config: Dict[str, Any], new_config: Dict[str, Any]) -> None:
        """Forget cached events so a config change takes effect immediately.

        check_triggers() reuses _events_cache whenever it is non-empty and
        only refills it when empty, so without this a new `calendar_url`
        would keep firing triggers for the previous calendar's events until
        the next fetch_data() replaced them.
        """
        self._events_cache = []
        logger.debug("Cleared cached events after config change")

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def fetch_data(self) -> PluginResult:
        """Fetch calendar and return upcoming events as template variables."""
        try:
            events = self._fetch_events()
        except Exception as e:
            logger.error("Error fetching calendar data: %s", e, exc_info=True)
            return PluginResult(available=False, error=str(e))

        self._events_cache = events

        if not events:
            data = self._empty_data()
            return PluginResult(
                available=True,
                data=data,
                formatted_lines=self._format_display(data),
            )

        next_event = events[0]
        data = self._build_data(next_event, events)
        return PluginResult(
            available=True,
            data=data,
            formatted_lines=self._format_display(data),
        )

    # ------------------------------------------------------------------
    # Trigger support
    # ------------------------------------------------------------------

    def check_triggers(self) -> List[TriggerResult]:
        """Fire triggers for events starting within the configured window.

        Board-takeover triggers are opt-out via the ``enable_triggers``
        setting (issue #1161). When disabled, the plugin still exposes its
        template variables through :meth:`fetch_data`; it just never
        interrupts the board, so those variables can drive a display via
        conditional/Collection logic instead — and long events no longer
        hold the board for their entire duration.
        """
        results: List[TriggerResult] = []

        if not self.config.get("enable_triggers", True):
            return results

        # Use cached events if available to avoid extra HTTP calls
        if not self._events_cache:
            try:
                self._events_cache = self._fetch_events()
            except Exception:
                logger.warning("Could not fetch events for trigger check", exc_info=True)
                return results

        minutes_before = int(self.config.get("minutes_before", 15))
        display_minutes = int(self.config.get("display_duration_minutes", 0))
        duration_seconds = (
            display_minutes * 60 if display_minutes > 0 else _INDEFINITE_DURATION_SECONDS
        )

        tz_str = self.config.get("timezone", "America/Los_Angeles")
        tz = pytz.timezone(tz_str)
        now = datetime.now(tz)

        for event in self._events_cache:
            start = event["start_dt"]
            end = event["end_dt"]

            minutes_until = (start - now).total_seconds() / 60
            is_now = start <= now <= end

            if is_now:
                results.append(TriggerResult(
                    triggered=True,
                    trigger_id=_event_trigger_id(event) + "_now",
                    formatted_lines=self._format_trigger_display(event, now, is_now=True),
                    priority=5,
                    duration_seconds=duration_seconds,
                    data=self._build_data(event, self._events_cache),
                ))
            elif 0 <= minutes_until <= minutes_before:
                results.append(TriggerResult(
                    triggered=True,
                    trigger_id=_event_trigger_id(event),
                    formatted_lines=self._format_trigger_display(event, now, is_now=False),
                    priority=5,
                    duration_seconds=duration_seconds,
                    data=self._build_data(event, self._events_cache),
                ))

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_events(self) -> List[Dict[str, Any]]:
        """Fetch and parse the .ics URL, returning sorted event dicts."""
        url = self.config.get("calendar_url") or os.getenv("CALENDAR_SUB_URL", "")
        if not url:
            raise ValueError("Calendar URL not configured")

        url = _normalize_url(url)
        tz_str = self.config.get("timezone", "America/Los_Angeles")
        tz = pytz.timezone(tz_str)
        max_events = int(self.config.get("max_events", 5))

        response = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": (
                    "FiestaBoard Calendar Subscription Plugin/1.0 "
                    "(+https://github.com/Fiestaboard/fiestaboard-plugin--calendar-sub)"
                )
            },
            allow_redirects=True,
        )
        response.raise_for_status()

        cal = Calendar.from_ical(response.content)

        now = datetime.now(tz)
        look_ahead = now + timedelta(days=_LOOK_AHEAD_DAYS)

        raw_events = recurring_ical_events.of(cal).between(now, look_ahead)

        events = []
        for component in raw_events:
            try:
                events.append(self._parse_component(component, tz))
            except Exception:
                logger.debug("Skipping malformed event component", exc_info=True)

        events.sort(key=lambda e: e["start_dt"])
        return events[:max_events]

    def _parse_component(self, component: Any, tz: Any) -> Dict[str, Any]:
        """Extract a normalized event dict from a VEVENT component."""
        summary = str(component.get("SUMMARY", "Untitled Event"))
        uid = str(component.get("UID", ""))
        location = str(component.get("LOCATION", ""))
        description = str(component.get("DESCRIPTION", ""))

        dtstart = component.get("DTSTART")
        dtend = component.get("DTEND") or component.get("DUE")

        start_raw = dtstart.dt if dtstart else datetime.now(tz)
        end_raw = dtend.dt if dtend else start_raw

        start_dt = _dt_to_aware(start_raw, tz)
        end_dt = _dt_to_aware(end_raw, tz)

        return {
            "uid": uid,
            "name": summary,
            "location": location,
            "description": description,
            "start_dt": start_dt,
            "end_dt": end_dt,
            "start_raw": str(start_raw),
            "start": _format_time(start_dt),
            "start_date": _format_date(start_dt),
            "end": _format_time(end_dt),
        }

    def _build_data(
        self, next_event: Dict[str, Any], events: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Build the template variable dict from the next event and event list."""
        tz_str = self.config.get("timezone", "America/Los_Angeles")
        tz = pytz.timezone(tz_str)
        now = datetime.now(tz)

        start = next_event["start_dt"]
        end = next_event["end_dt"]
        minutes_until = int((start - now).total_seconds() / 60)
        is_now = start <= now <= end

        # event2_* flat shortcuts for the event after the next one — the
        # common "now / next" pattern. For longer lists, users index into
        # the events array: {{calendar_sub.events.2.name}} etc.
        next_index = next((i for i, e in enumerate(events) if e is next_event), 0)
        second = events[next_index + 1] if next_index + 1 < len(events) else None

        return {
            "event_name": next_event["name"],
            "event_start": next_event["start"],
            "event_start_date": next_event["start_date"],
            "event_end": next_event["end"],
            "event_location": next_event["location"],
            "event_description": next_event["description"],
            "minutes_until": str(minutes_until),
            "is_now": "true" if is_now else "false",
            "event_count": str(len(events)),
            "event2_name": second["name"] if second else "",
            "event2_start": second["start"] if second else "",
            "event2_start_date": second["start_date"] if second else "",
            "event2_end": second["end"] if second else "",
            "event2_location": second["location"] if second else "",
            "events": [
                {
                    "name": e["name"],
                    "start": e["start"],
                    "start_date": e["start_date"],
                    "end": e["end"],
                    "location": e["location"],
                }
                for e in events
            ],
        }

    def _empty_data(self) -> Dict[str, Any]:
        """Return a data dict when no upcoming events are found."""
        return {
            "event_name": "",
            "event_start": "",
            "event_start_date": "",
            "event_end": "",
            "event_location": "",
            "event_description": "",
            "minutes_until": "",
            "is_now": "false",
            "event_count": "0",
            "event2_name": "",
            "event2_start": "",
            "event2_start_date": "",
            "event2_end": "",
            "event2_location": "",
            "events": [],
        }

    def _board_dims(self) -> tuple:
        """Effective (rows, cols) for the board being rendered.

        ``self.board`` is ``None`` outside a board-scoped render (unit
        tests, legacy callers); treat that as a Flagship rather than
        crashing or guessing. Every layout decision below derives from
        these two numbers -- there is no dimension literal past this point.
        """
        board = self.board
        if board is None:
            return _DEFAULT_ROWS, _DEFAULT_COLS
        return board.rows, board.cols

    @staticmethod
    def _timing_text(minutes_until: Any, is_now: bool = False) -> str:
        """Human-readable countdown, or '' when there is nothing to say."""
        if is_now:
            return "HAPPENING NOW"
        try:
            mins = int(minutes_until)
        except (TypeError, ValueError):
            return ""
        if mins <= 0:
            return "NOW"
        if mins < 60:
            return f"IN {mins} MIN"
        return f"IN {mins // 60} HR"

    def _event_field_lines(
        self, index: int, event: Dict[str, Any], data: Dict[str, Any]
    ) -> List[str]:
        """Ordered, board-agnostic candidate lines describing one event.

        Ordered most-important-first: name, then when, then where/until.
        ``_render_event_lines`` takes a prefix of this per event depending
        on how many rows are available, so the order here IS the reflow
        priority -- callers never need a board size to decide what to drop.
        """
        name = (event.get("name") or "").upper()
        date_str = (event.get("start_date") or "").upper()
        time_str = (event.get("start") or "").upper()
        date_time = f"{date_str}  {time_str}".strip()
        location = (event.get("location") or "").upper()
        end = (event.get("end") or "").upper()

        fields = [name, date_time]
        if location:
            fields.append(location)
        if index == 0:
            timing = self._timing_text(data.get("minutes_until"), data.get("is_now") == "true")
            if timing:
                fields.append(timing)
        if end:
            fields.append(f"UNTIL {end}")
        return fields

    def _render_event_lines(
        self, events: List[Dict[str, Any]], budget: int, cols: int, data: Dict[str, Any]
    ) -> List[str]:
        """Fill up to *budget* rows from *events*, most-important-first.

        Flattens each event's field list (name, when, where, ...) in order
        and concatenates event-by-event, so the result is a fixed sequence
        independent of *budget* -- taking more of it is always a strict
        extension of taking less. That is what makes growth monotonic: a
        taller board (bigger budget) can only reveal the SAME events in
        more detail or additional events entirely, never something
        unrelated to what a shorter board already showed.
        """
        if budget <= 0 or not events:
            return []
        sequence: List[str] = []
        for i, event in enumerate(events):
            sequence.extend(self._event_field_lines(i, event, data))
        return [_center(text, cols) for text in sequence[:budget]]

    def _empty_display(self, rows: int, cols: int) -> List[str]:
        """Board lines for 'no upcoming events', sized to the board."""
        message = "NO UPCOMING EVENTS" if cols >= len("NO UPCOMING EVENTS") else "NO EVENTS"
        lines = [_center("CALENDAR", cols)]
        if rows >= 3:
            lines.append("")
        lines.append(_center(message, cols))
        while len(lines) < rows:
            lines.append("")
        return lines[:rows]

    def _format_display(self, data: Dict[str, Any]) -> List[str]:
        """Format template data into board lines sized to ``self.board``.

        Reflows instead of truncating: a taller board shows more events (or
        more detail about the ones already shown) rather than padding with
        blank rows, and a wider board gets less-truncated labels instead of
        a fixed 22-character cut. See ``_render_event_lines``.
        """
        rows, cols = self._board_dims()

        if data.get("event_count") == "0" or not data.get("event_name"):
            return self._empty_display(rows, cols)

        events = data.get("events") or []
        lines: List[str] = []

        # A header costs a whole row; on a 3-row Note that is a third of
        # the board, so skip it there and spend every row on content.
        if rows >= 4:
            header = "UPCOMING EVENTS" if len(events) > 1 else "UPCOMING EVENT"
            lines.append(_center(header, cols))

        lines.extend(self._render_event_lines(events, rows - len(lines), cols, data))
        return lines[:rows]

    def _format_trigger_display(
        self, event: Dict[str, Any], now: datetime, is_now: bool
    ) -> List[str]:
        """Format a board display for a trigger notification, sized to ``self.board``.

        Single-event, so "reflow" here means more per-event detail on a
        taller board (description/location/end time) rather than more rows
        of blank padding -- there is nothing else to list.
        """
        rows, cols = self._board_dims()

        name = (event.get("name") or "").upper()
        date_str = (event.get("start_date") or "").upper()
        time_str = (event.get("start") or "").upper()
        date_time = f"{date_str}  {time_str}".strip()
        location = (event.get("location") or "").upper()
        end = (event.get("end") or "").upper()

        if is_now:
            header = "EVENT STARTING NOW"
            timing = "HAPPENING NOW"
        else:
            header = "UPCOMING EVENT"
            start = event["start_dt"]
            minutes_until = int((start - now).total_seconds() / 60)
            timing = f"IN {minutes_until} MINUTES" if minutes_until < 60 else f"IN {minutes_until // 60} HOURS"

        fields = [header, name, date_time]
        if location:
            fields.append(location)
        fields.append(timing)
        if end:
            fields.append(f"UNTIL {end}")

        lines = [_center(text, cols) for text in fields[:rows]]
        # A blank separator after the header reads better on a board with
        # room to spare; only add it when it doesn't cost real content.
        if len(lines) < rows:
            lines.insert(1, "")
        return lines[:rows]


# Export the plugin class
Plugin = CalendarSubPlugin
