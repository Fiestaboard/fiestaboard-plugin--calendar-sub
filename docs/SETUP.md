# Calendar Subscription Setup Guide

Subscribe to any public iCalendar (.ics) URL and get automatic board alerts before your events start.

## Overview

The Calendar Subscription plugin connects to a public `.ics` calendar feed and displays upcoming events on your Vestaboard. It can also automatically interrupt the board display a configurable number of minutes before each event to show an alert.

**Prerequisites:**
- A public iCalendar subscription URL (`.ics` or `webcal://`) from Google Calendar, Outlook, Apple Calendar, a school/organization website, or any other iCalendar-compatible calendar service
- No API key required

## Quick Setup

### 1. Enable the Plugin

In the FiestaBoard web UI, go to **Integrations**, find **Calendar Subscription**, and click **Enable**.

### 2. Configure

Click **Configure** and fill in:

- **Calendar URL** — Paste your `.ics` or `webcal://` URL (see "Getting Your Calendar URL" below)
- **Take Over the Board for Events** — Leave **on** for automatic alerts that switch the board to upcoming events. Turn **off** to use the calendar's variables in your own pages without ever interrupting the board (ideal for long events or conditional/Collection layouts). The three settings below only apply when this is on.
- **Lead Time** — How early to show the board alert (pick one of `1`/`2`/`3`/`5`/`10`/`15`/`30`/`60` min). The alert page re-renders every minute, so `{{minutes_until}}` naturally counts down.
- **Stay On Board** — How long the alert remains on the board after the event ends (pick one of `5`/`10`/`15`/`30`/`60`/`120` min, or `Until next page` to leave it up indefinitely)
- **Timezone** — Your local IANA timezone (e.g., `America/New_York`, `America/Los_Angeles`)
- **Trigger Page** *(optional)* — Pick the template page (from a dropdown) to display when the alert fires. Leave at "None" to use the built-in 6-line display. The page must use `{{calendar_sub.*}}` variables.

### 3. Add a Template

Go to **Pages** and create a page using the Calendar Subscription plugin. A simple template:

```
UPCOMING EVENT
{{event_name}}

{{event_start_date}}  {{event_start}}
{{event_location}}
IN {{minutes_until}} MIN
```

### 4. View Your Board

Events from your calendar will appear on the board, and the alert will automatically trigger when an event is approaching.

---

## Getting Your Calendar URL

### Google Calendar

1. Go to [calendar.google.com](https://calendar.google.com)
2. Click the three-dot menu next to the calendar you want to share
3. Select **Settings and sharing**
4. Scroll to **Integrate calendar**
5. Copy the **Public address in iCal format** (ends in `.ics`)

> The calendar must be set to **public** for the subscription URL to work.

### Outlook / Microsoft 365

1. Go to [outlook.com](https://outlook.com) or your Microsoft 365 calendar
2. Click **Settings** → **View all Outlook settings** → **Calendar** → **Shared calendars**
3. Under **Publish a calendar**, choose your calendar and set permissions to **Can view all details**
4. Click **Publish** and copy the **ICS** link

### Apple Calendar (iCloud)

1. Open Calendar on Mac
2. Right-click (Control-click) a calendar and choose **Get Info** or **Share Calendar**
3. Enable **Public Calendar** and copy the URL
4. Change `webcal://` to `https://` if needed (FiestaBoard handles this automatically)

### School / Organization Calendars

Many schools and organizations publish calendar feeds directly. Look for links labeled:
- "Subscribe to calendar"
- "iCal feed"
- "Add to calendar" → right-click the button and copy the link URL
- A URL ending in `.ics`

---

## Template Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{{event_name}}` | Next event name | `Parent Teacher Conf` |
| `{{event_start}}` | Start time | `3:30 PM` |
| `{{event_start_date}}` | Start date | `Apr 3` |
| `{{event_end}}` | End time | `4:30 PM` |
| `{{event_location}}` | Event location | `Room 204` |
| `{{event_description}}` | Description (truncated) | `Bring report card` |
| `{{minutes_until}}` | Minutes until event | `10` |
| `{{is_now}}` | Event in progress now | `false` |
| `{{event_count}}` | Number of upcoming events | `3` |
| `{{event2_name}}` | Name of the event *after* the next one | `Design Review` |
| `{{event2_start}}` | Start time of the event after the next one | `4:00 PM` |
| `{{event2_start_date}}` | Start date of the event after the next one | `Apr 7` |
| `{{events.0.name}}` | First event name (same as `event_name`) | `Weekly Standup` |
| `{{events.0.start}}` | First event start time | `9:00 AM` |
| `{{events.0.start_date}}` | First event date | `Apr 7` |
| `{{events.N.field}}` | Index into the events array — `N` is 0-based, capped at `max_events` | `{{events.2.name}}` |

## Configuration Reference

| Setting | Description | Default | Range |
|---------|-------------|---------|-------|
| `calendar_url` | Public .ics or webcal:// URL | Required | — |
| `enable_triggers` | Take over the board to show events. Off = variables only, no interruption | `true` | true / false |
| `minutes_before` | Lead time before an event (only applies when `enable_triggers` is on) | `5` | 1, 2, 3, 5, 10, 15, 30, 60 |
| `display_duration_minutes` | How long alert stays after event ends (0 = until next page) | `15` | 0, 5, 10, 15, 30, 60, 120 |
| `timezone` | IANA timezone name | `America/Los_Angeles` | any valid IANA zone |
| `max_events` | Max upcoming events to load | `5` | 1–20 |
| `refresh_seconds` | Re-fetch interval in seconds | `300` | 60+ |
| `trigger_page_id` | UUID of the template page to render when a trigger fires (picked from a dropdown) | — | any of your template pages |

### Environment Variables

You can optionally set the calendar URL via environment variable instead of the UI:

| Variable | Description |
|----------|-------------|
| `CALENDAR_SUB_URL` | Calendar subscription URL |
| `TIMEZONE` | Default timezone |

---

## Troubleshooting

**No events showing up**
- Make sure the calendar URL is publicly accessible (not behind a login)
- Verify the URL ends in `.ics` or starts with `webcal://`
- Check that the calendar has events in the next 30 days
- Try pasting the URL directly in a browser — you should see iCalendar text

**Events are in the wrong timezone**
- Set the **Timezone** setting to match your local timezone
- All-day events always show as "All Day" regardless of timezone

**Trigger not firing**
- Make sure **Take Over the Board for Events** is turned **on** — with it off, the plugin only exposes variables and never interrupts the board
- Check that **Lead Time** is set to a value greater than 0
- The trigger fires within the configured window before each event starts
- Events that are already past will not trigger

**I want the variables but not the board takeover**
- Turn **Take Over the Board for Events** off. The `{{calendar_sub.*}}` variables stay available for your own pages, but the board is never interrupted — useful for long events or conditional/Collection layouts

**"Calendar URL is required" error**
- Make sure the URL field is not empty
- The URL must start with `http://`, `https://`, or `webcal://`

**Calendar not updating**
- The plugin re-fetches the calendar based on **Refresh Interval** (default: every 5 minutes)
- Reduce the interval if you need more frequent updates (minimum: 60 seconds)
- Note that the calendar host may also cache updates for some time
