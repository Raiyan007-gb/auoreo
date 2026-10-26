"""Calendar connector for AUOREO: CalDAV (RFC 4791) and Google Calendar v3, over httpx.

`calendar_list_events`: `REPORT` calendar-query with a `time-range` filter on VEVENTs; the
multistatus answer is parsed with `xml.etree` and each `calendar-data` with a small iCalendar
reader (line unfolding, SUMMARY/DTSTART/DTEND/UID/LOCATION/DESCRIPTION). Recurring events are
returned as their master (RRULE shown), not expanded.
`calendar_create_event`: `PUT <collection>/<uid>.ics` with `If-None-Match: *` (never
overwrites). Times are ISO 8601; naive times use the configured `timezone`.

I7 `provider: google`: Calendar API v3 REST with an OAuth access token from the host
(`HostAPI.oauth_token("google")`, minted from `auoreo login google`; refreshed + cached host
side). List = `GET /calendars/{id}/events?timeMin&timeMax&singleEvents=true&orderBy=startTime`;
create = `POST /calendars/{id}/events` with a fresh client-chosen event id — an insert never
replaces an existing event, and a 409 (id taken) is reported, not retried as an update.
"""

from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

NAME = "calendar"
NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
GOOGLE_API = "https://www.googleapis.com/calendar/v3"
TOOLS: list[dict[str, Any]] = [
    {
        "name": "calendar_list_events",
        "description": "List calendar events between start and end (ISO 8601; default: the next 7 days).",
        "parameters": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "calendar_create_event",
        "description": "Create a calendar event (asks for approval).",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "minLength": 1, "maxLength": 500},
                "start": {"type": "string", "description": "ISO 8601 start"},
                "end": {"type": "string", "description": "ISO 8601 end (default start + 1 h)"},
                "description": {"type": "string", "maxLength": 10000},
                "location": {"type": "string", "maxLength": 500},
            },
            "required": ["summary", "start"],
            "additionalProperties": False,
        },
    },
]


class CalendarError(RuntimeError):
    pass


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CalendarError(f"unknown timezone {name!r}") from exc


def parse_time(value: str, tz: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalendarError(f"not an ISO 8601 time: {value!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=_zone(tz))


def ical_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def ical_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ical_unescape(text: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), text)


def fold(line: str) -> str:
    """RFC 5545 §3.1: lines over 75 octets continue on the next line after a space."""
    out, cur = [], line
    while len(cur.encode()) > 75:
        cut = 75
        while len(cur[:cut].encode()) > 75:
            cut -= 1
        out.append(cur[:cut])
        cur = " " + cur[cut:]
    return "\r\n".join([*out, cur])


def build_event(
    uid: str, summary: str, start: datetime, end: datetime, description: str = "", location: str = ""
) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AUOREO//calendar plugin//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{ical_utc(datetime.now(UTC))}",
        f"DTSTART:{ical_utc(start)}",
        f"DTEND:{ical_utc(end)}",
        f"SUMMARY:{ical_escape(summary)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{ical_escape(description)}")
    if location:
        lines.append(f"LOCATION:{ical_escape(location)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


def parse_events(ics: str) -> list[dict[str, str]]:
    unfolded = re.sub(r"\r?\n[ \t]", "", ics)
    events: list[dict[str, str]] = []
    cur: dict[str, str] | None = None
    for line in unfolded.splitlines():
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            key, _, value = line.partition(":")
            prop = key.split(";", 1)[0].upper()
            if prop in {"UID", "SUMMARY", "DTSTART", "DTEND", "LOCATION", "DESCRIPTION", "RRULE"}:
                cur[prop.lower()] = ical_unescape(value)
    return events


def time_range_query(start: datetime, end: datetime) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        "<d:prop><d:getetag/><c:calendar-data/></d:prop>"
        '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
        f'<c:time-range start="{ical_utc(start)}" end="{ical_utc(end)}"/>'
        "</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>"
    )


class Plugin:
    def __init__(self, host: Any, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.host = host
        self.transport = transport
        self.config: dict[str, Any] = {}

    def initialize(self, config: dict[str, Any], consumes_granted: list[str]) -> dict[str, Any]:
        self.config_update(config)
        return {"tools": TOOLS, "deliveries": [], "connectors": [], "skills": []}

    def config_update(self, config: dict[str, Any]) -> dict[str, Any]:
        self.config = dict(config)
        return {}

    def google(self) -> bool:
        return self.config.get("provider", "caldav") == "google"

    async def _google_client(self) -> httpx.AsyncClient:
        connector = str(self.config.get("oauth_connector") or "google")
        try:
            token = await self.host.oauth_token(connector)
        except Exception as exc:  # RpcError from the host: not signed in / not declared
            raise CalendarError(f"OAuth token for {connector} unavailable: {exc}") from exc
        if not token:
            raise CalendarError(f"OAuth token for {connector} unavailable (run `auoreo login {connector}`)")
        headers = {"Authorization": f"Bearer {token}"}
        return httpx.AsyncClient(headers=headers, transport=self.transport, timeout=30)

    def _events_url(self) -> str:
        cal = quote(str(self.config.get("calendar_id") or "primary"), safe="")
        return f"{GOOGLE_API}/calendars/{cal}/events"

    async def google_list(self, t0: datetime, t1: datetime) -> list[dict[str, str]]:
        params = {
            "timeMin": t0.isoformat(),
            "timeMax": t1.isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": str(int(self.config.get("max_events", 100))),
        }
        async with await self._google_client() as client:
            resp = await client.get(self._events_url(), params=params)
        if resp.status_code != 200:
            raise CalendarError(f"Google events.list answered HTTP {resp.status_code}: {resp.text[:200]}")
        out: list[dict[str, str]] = []
        for item in resp.json().get("items", []):
            start, end = item.get("start") or {}, item.get("end") or {}
            row = {
                "uid": str(item.get("id", "")),
                "summary": str(item.get("summary", "")),
                "dtstart": str(start.get("dateTime") or start.get("date") or ""),
                "dtend": str(end.get("dateTime") or end.get("date") or ""),
            }
            for key in ("location", "description"):
                if item.get(key):
                    row[key] = str(item[key])
            out.append(row)
        return out

    async def google_create(self, args: dict[str, Any], start: datetime, end: datetime) -> str:
        tz = str(self.config.get("timezone") or "UTC")
        event_id = uuid.uuid4().hex  # base32hex alphabet ⊇ [0-9a-f]; 32 chars
        body: dict[str, Any] = {
            "id": event_id,
            "summary": str(args["summary"]),
            "start": {"dateTime": start.isoformat(), "timeZone": tz},
            "end": {"dateTime": end.isoformat(), "timeZone": tz},
        }
        for key in ("description", "location"):
            if args.get(key):
                body[key] = str(args[key])
        async with await self._google_client() as client:
            resp = await client.post(self._events_url(), json=body)
        if resp.status_code == 409:
            raise CalendarError("an event with that id already exists; nothing was overwritten")
        if resp.status_code not in (200, 201):
            raise CalendarError(f"Google events.insert answered HTTP {resp.status_code}: {resp.text[:200]}")
        return str(resp.json().get("id") or event_id)

    async def _client(self) -> httpx.AsyncClient:
        if not self.config.get("url"):
            raise CalendarError("calendar url is not configured")
        auth = None
        if self.config.get("username"):
            ref = str(self.config.get("password") or "secret:calendar/password")
            if not ref.startswith("secret:"):
                raise CalendarError("password must be a secret reference like 'secret:calendar/password'")
            password = await self.host.secret(ref.removeprefix("secret:"))
            if not password:
                raise CalendarError(f"secret {ref.removeprefix('secret:')} is not set")
            auth = httpx.BasicAuth(self.config["username"], password)
        return httpx.AsyncClient(auth=auth, transport=self.transport, timeout=30)

    def _collection(self) -> str:
        url = str(self.config["url"])
        return url if url.endswith("/") else url + "/"

    async def list_events(self, start: str | None, end: str | None) -> list[dict[str, str]]:
        tz = str(self.config.get("timezone") or "UTC")
        t0 = parse_time(start, tz) if start else datetime.now(UTC)
        t1 = parse_time(end, tz) if end else t0 + timedelta(days=7)
        if t1 <= t0:
            raise CalendarError("end must be after start")
        if self.google():
            return await self.google_list(t0, t1)
        async with await self._client() as client:
            resp = await client.request(
                "REPORT",
                self._collection(),
                content=time_range_query(t0, t1),
                headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            )
        if resp.status_code != 207:
            raise CalendarError(f"REPORT answered HTTP {resp.status_code}")
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            raise CalendarError(f"bad multistatus XML: {exc}") from exc
        events: list[dict[str, str]] = []
        for data in root.iterfind(".//c:calendar-data", NS):
            events += parse_events(data.text or "")
        events.sort(key=lambda e: e.get("dtstart", ""))
        return events[: int(self.config.get("max_events", 100))]

    async def create_event(self, args: dict[str, Any]) -> str:
        tz = str(self.config.get("timezone") or "UTC")
        start = parse_time(str(args["start"]), tz)
        end = parse_time(str(args["end"]), tz) if args.get("end") else start + timedelta(hours=1)
        if end <= start:
            raise CalendarError("end must be after start")
        if self.google():
            return await self.google_create(args, start, end)
        uid = f"{uuid.uuid4()}@auoreo"
        body = build_event(
            uid,
            str(args["summary"]),
            start,
            end,
            str(args.get("description") or ""),
            str(args.get("location") or ""),
        )
        async with await self._client() as client:
            resp = await client.put(
                f"{self._collection()}{uid}.ics",
                content=body.encode(),
                headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"},
            )
        if resp.status_code not in (200, 201, 204):
            raise CalendarError(f"PUT answered HTTP {resp.status_code}")
        return uid

    async def call_tool(self, name: str, args: dict[str, Any], session_id: str) -> dict[str, Any]:
        try:
            if name == "calendar_list_events":
                events = await self.list_events(args.get("start"), args.get("end"))
                if not events:
                    return {"content": "no events in that range", "is_error": False}
                rows = [
                    f"- {e.get('dtstart', '?')} → {e.get('dtend', '?')}: {e.get('summary', '(no title)')}"
                    + (f" @ {e['location']}" if e.get("location") else "")
                    + (f" (repeats: {e['rrule']})" if e.get("rrule") else "")
                    for e in events
                ]
                return {"content": "\n".join(rows), "is_error": False}
            if name == "calendar_create_event":
                uid = await self.create_event(args)
                await self.host.audit("created", {"uid": uid})
                return {"content": f"created event {args['summary']!r} (uid {uid})", "is_error": False}
        except (CalendarError, httpx.HTTPError) as exc:
            return {"content": f"calendar error: {exc}", "is_error": True}
        return {"content": f"unknown tool {name}", "is_error": True}
