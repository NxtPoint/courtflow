# diary/calendar.py — generate an iCalendar (.ics) for a booking.
#
# Two consumers (now + later):
#   • NOW: the in-app "Add to calendar" download (GET /api/diary/bookings/<id>/calendar.ics).
#   • LATER: attach the same .ics to the confirmation email once SES/Klaviyo is wired (the
#     booking_confirmed/lesson_accepted payloads already carry `ics_url`).
#
# Pure, dependency-free RFC-5545 output (CRLF line endings, escaped text, 75-octet folding).
import datetime as _dt


def _esc(s):
    s = "" if s is None else str(s)
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ts(v):
    """A datetime (aware/naive) or ISO string -> UTC basic format YYYYMMDDTHHMMSSZ."""
    if isinstance(v, str):
        v = _dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    if v.tzinfo is None:
        v = v.replace(tzinfo=_dt.timezone.utc)
    return v.astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fold(line):
    """RFC-5545 content-line folding: split at <=75 octets, continue with CRLF + a space."""
    if len(line.encode("utf-8")) <= 75:
        return line
    out = []
    while len(line.encode("utf-8")) > 75:
        cut = 75
        while len(line[:cut].encode("utf-8")) > 75:
            cut -= 1
        out.append(line[:cut]); line = " " + line[cut:]
    out.append(line)
    return "\r\n".join(out)


def build_ics(*, uid, summary, starts_at, ends_at, description="", location="",
              organizer_email=None, url=None, status="CONFIRMED", now=None):
    """Return a single-event VCALENDAR string for a booking. `starts_at`/`ends_at` accept a
    datetime or ISO-8601 string; everything is emitted in UTC."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CourtFlow//Booking//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        "UID:" + _esc(uid),
        "DTSTAMP:" + _ts(now),
        "DTSTART:" + _ts(starts_at),
        "DTEND:" + _ts(ends_at),
        "SUMMARY:" + _esc(summary),
    ]
    if description:
        lines.append("DESCRIPTION:" + _esc(description))
    if location:
        lines.append("LOCATION:" + _esc(location))
    if url:
        lines.append("URL:" + _esc(url))
    if organizer_email:
        lines.append("ORGANIZER:mailto:" + _esc(organizer_email))
    lines.append("STATUS:" + (status or "CONFIRMED"))
    lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"
