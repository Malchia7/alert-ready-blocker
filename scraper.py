#!/usr/bin/env python3
"""Scrapes for the next B.C. Alert Ready test date/time and (re)writes
bc-alert-test.ics with a 15-minute buffer window around it.

Sources are checked in priority order, each wrapped independently so a
failure (403, timeout, parse miss) on one falls through to the next instead
of crashing the run:

  1. alertready.ca RSS feed      - polled by readers, least likely to be
                                    blocked; carries the latest announcement.
  2. gov.bc.ca test page         - often updated 2-3 weeks before the RSS
                                    picks it up.
  3. alertready.ca schedule page - static HTML table, last resort.
"""
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

VANCOUVER = ZoneInfo("America/Vancouver")
ICS_PATH = "bc-alert-test.ics"
UID = "bc-alert-ready-test@malchia7.github.io"
DEFAULT_BC_TIME = (13, 55)  # 1:55 p.m., used when a date is confirmed but no BC-specific time is listed

RSS_URL = "https://alertready.ca/feed/"
GOVBC_URL = "https://www2.gov.bc.ca/gov/content/safety/public-safety/emergency-alerts/alert-test"
ALERTREADY_SCHEDULE_URL = "https://alertready.ca/testing-schedule/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
}

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}

CONTENT_ENCODED_TAG = "{http://purl.org/rss/1.0/modules/content/}encoded"


def fetch(url):
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


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


def find_date(text):
    """Finds a 'Month Day, Year' date, preferring one anchored to 'test ... on'."""
    anchored = re.search(
        r"test(?:ing)?[^.]*?\bon\s+([A-Za-z]+\s+\d{1,2},?\s*\d{4})", text, re.I
    )
    search_text = anchored.group(1) if anchored else text
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s*(\d{4})", search_text)
    if not m:
        return None
    month = MONTHS.get(m.group(1).lower())
    if not month:
        return None
    return int(m.group(3)), month, int(m.group(2))


def find_bc_time_in_table(html_fragment):
    """Finds the British Columbia row's time in an HTML table fragment."""
    soup = BeautifulSoup(html_fragment, "html.parser")
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True)
        if "british columbia" not in label.lower():
            continue
        time_text = cells[-1].get_text(strip=True)
        return parse_time(time_text)
    return None


def datetime_from_parts(year, month, day, time_parts):
    hour, minute = time_parts if time_parts else DEFAULT_BC_TIME
    return datetime(year, month, day, hour, minute, tzinfo=VANCOUVER)


def parse_rss_feed(xml_text):
    """Checks the latest 5 RSS items for an upcoming BC test date/time."""
    root = ET.fromstring(xml_text)
    items = root.findall("./channel/item")[:5]

    for item in items:
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()
        content = (item.findtext(CONTENT_ENCODED_TAG) or "").strip()

        date_parts = find_date(title) or find_date(description) or find_date(content)
        if not date_parts:
            continue

        time_parts = find_bc_time_in_table(content) or find_bc_time_in_table(description)
        year, month, day = date_parts
        yield datetime_from_parts(year, month, day, time_parts)


def parse_govbc(html):
    """Finds 'next test is scheduled for <Month Day, Year> at <time>'."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    m = re.search(
        r"next test is scheduled for\s+([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})"
        r"(?:\s+at\s+(\d{1,2}:\d{2}\s*[AaPp]\.?[Mm]\.?))?",
        text,
    )
    if not m:
        return None
    month_name, day, year, time_text = m.groups()
    month = MONTHS.get(month_name.lower())
    if not month:
        return None
    time_parts = parse_time(time_text) if time_text else None
    return datetime_from_parts(int(year), month, int(day), time_parts)


def parse_alertready_schedule(html):
    """Finds the British Columbia row in the testing-schedule table."""
    soup = BeautifulSoup(html, "html.parser")
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        label = cells[0].get_text(strip=True)
        if "british columbia" not in label.lower():
            continue
        date_parts = find_date(cells[1].get_text(strip=True))
        if not date_parts:
            continue
        time_parts = parse_time(cells[2].get_text(strip=True))
        year, month, day = date_parts
        return datetime_from_parts(year, month, day, time_parts)
    return None


def future_candidates(candidates, now):
    return [dt for dt in candidates if dt >= now]


def find_next_test():
    """Checks each source in priority order; returns the next future BC test
    datetime, or None if no source currently lists one.
    """
    now = datetime.now(VANCOUVER)

    try:
        rss_xml = fetch(RSS_URL)
        future = future_candidates(list(parse_rss_feed(rss_xml)), now)
        if future:
            print("Found upcoming test via alertready.ca RSS feed.", file=sys.stderr)
            return min(future)
    except Exception as exc:
        print(f"WARN: RSS feed source failed: {exc}", file=sys.stderr)

    try:
        govbc_html = fetch(GOVBC_URL)
        dt = parse_govbc(govbc_html)
        if dt and dt >= now:
            print("Found upcoming test via gov.bc.ca.", file=sys.stderr)
            return dt
    except Exception as exc:
        print(f"WARN: gov.bc.ca source failed: {exc}", file=sys.stderr)

    try:
        schedule_html = fetch(ALERTREADY_SCHEDULE_URL)
        dt = parse_alertready_schedule(schedule_html)
        if dt and dt >= now:
            print("Found upcoming test via alertready.ca schedule page.", file=sys.stderr)
            return dt
    except Exception as exc:
        print(f"WARN: alertready.ca schedule source failed: {exc}", file=sys.stderr)

    return None


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

    print("No future BC test date found on any source.", file=sys.stderr)
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
