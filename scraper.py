#!/usr/bin/env python3
"""Scrapes alertready.ca and gov.bc.ca for the next B.C. Alert Ready test
date/time and (re)writes bc-alert-test.ics with a 15-minute buffer window
around it.
"""
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

VANCOUVER = ZoneInfo("America/Vancouver")
ICS_PATH = "bc-alert-test.ics"
UID = "bc-alert-ready-test@alert-ready-blocker.github.io"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AlertReadyBlocker/1.0)"}

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}


def fetch(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        print(f"WARN: failed to fetch {url}: {exc}", file=sys.stderr)
        return None


def parse_time(time_text):
    """'1:55 PM PCT' / '1:55pm' -> (hour, minute) in 24h, local to the zone."""
    m = re.search(r"(\d{1,2}):(\d{2})\s*([AP]M)", time_text, re.I)
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if ampm == "PM" and hour != 12:
        hour += 12
    if ampm == "AM" and hour == 12:
        hour = 0
    return hour, minute


def parse_alertready(html):
    """Find the British Columbia row in the testing-schedule table."""
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        label = cells[0].get_text(strip=True)
        if "british columbia" not in label.lower():
            continue
        date_text = cells[1].get_text(strip=True)
        time_text = cells[2].get_text(strip=True)
        date_match = re.search(
            r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", date_text
        )
        if not date_match:
            continue
        month_name, day, year = date_match.groups()
        month = MONTHS.get(month_name.lower())
        if not month:
            continue
        time_parts = parse_time(time_text)
        if not time_parts:
            continue
        hour, minute = time_parts
        return datetime(int(year), month, int(day), hour, minute, tzinfo=VANCOUVER)
    return None


def parse_govbc(html):
    """Find 'next test is scheduled for <Month Day, Year> at <time>' sentence."""
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    m = re.search(
        r"next test is scheduled for\s+([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})"
        r"\s+at\s+(\d{1,2}:\d{2}\s*[AaPp]\.?[Mm]\.?)",
        text,
    )
    if not m:
        return None
    month_name, day, year, time_text = m.groups()
    month = MONTHS.get(month_name.lower())
    if not month:
        return None
    time_parts = parse_time(time_text)
    if not time_parts:
        return None
    hour, minute = time_parts
    return datetime(int(year), month, int(day), hour, minute, tzinfo=VANCOUVER)


def find_next_test():
    """Returns the next future BC test datetime, or None if none is listed."""
    alertready_html = fetch("https://alertready.ca/testing-schedule/")
    govbc_html = fetch(
        "https://www2.gov.bc.ca/gov/content/safety/public-safety/"
        "emergency-alerts/alert-test"
    )

    candidates = [
        dt
        for dt in (parse_alertready(alertready_html), parse_govbc(govbc_html))
        if dt is not None
    ]

    now = datetime.now(VANCOUVER)
    future = [dt for dt in candidates if dt >= now]
    if not future:
        return None
    return min(future)


def format_ics_dt(dt):
    return dt.strftime("%Y%m%dT%H%M%S")


VTIMEZONE_BLOCK = """BEGIN:VTIMEZONE
TZID:America/Vancouver
BEGIN:DAYLIGHT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
TZNAME:PDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
TZNAME:PST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE"""


def build_ics(test_dt):
    dtstamp = datetime.now(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//alert-ready-blocker//bc-alert-test//EN",
        "CALSCALE:GREGORIAN",
    ]

    if test_dt is not None:
        start = test_dt - timedelta(minutes=5)
        end = test_dt + timedelta(minutes=10)
        lines.append(VTIMEZONE_BLOCK)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{UID}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART;TZID=America/Vancouver:{format_ics_dt(start)}",
            f"DTEND;TZID=America/Vancouver:{format_ics_dt(end)}",
            "SUMMARY:Alert Ready Test",
            "DESCRIPTION:Scheduled B.C. Alert Ready test window.",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main():
    test_dt = find_next_test()

    if test_dt is not None:
        print(f"Next BC Alert Ready test: {test_dt.isoformat()}")
        ics_content = build_ics(test_dt)
        with open(ICS_PATH, "w", newline="") as f:
            f.write(ics_content)
        return

    print("No future BC test date found on either source.", file=sys.stderr)
    try:
        with open(ICS_PATH, "r") as f:
            existing = f.read()
        if "BEGIN:VCALENDAR" in existing:
            print("Preserving existing bc-alert-test.ics.", file=sys.stderr)
            return
    except FileNotFoundError:
        pass

    print("Writing empty VCALENDAR shell.", file=sys.stderr)
    with open(ICS_PATH, "w", newline="") as f:
        f.write(build_ics(None))


if __name__ == "__main__":
    main()
