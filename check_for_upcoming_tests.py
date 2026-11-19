#!/usr/bin/env python3
"""Checks for the next Alert Ready test date/time for each Canadian
province/territory and (re)writes one <abbr>-alert-test.ics per region with
a 15-minute buffer window around it.

Sources are checked in priority order, each wrapped independently so a
failure (403, timeout, parse miss) on one falls through to the next instead
of crashing the run:

  1. alertready.ca RSS feed      - polled by readers, least likely to be
                                    blocked; carries the latest announcement
                                    and a province/time table per item.
  2. gov.bc.ca test page         - B.C.-only; often updated 2-3 weeks before
                                    the RSS picks it up.
  3. alertready.ca schedule page - static HTML table, last resort.

Each region's local test time is converted to UTC via zoneinfo (backed by
the `tzdata` package) rather than a hand-written VTIMEZONE block, since
DST/permanent-offset rules change by legislation (e.g. Alberta, Manitoba,
and the Northwest Territories moved to a permanent UTC offset in Nov 2026)
and a maintained IANA database tracks that automatically.
"""
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

UTC = ZoneInfo("UTC")

PROVINCES = [
    {"abbr": "ab", "label": "Alberta", "tz": "America/Edmonton"},
    {"abbr": "bc", "label": "British Columbia", "tz": "America/Vancouver"},
    {"abbr": "mb", "label": "Manitoba", "tz": "America/Winnipeg"},
    {"abbr": "nb", "label": "New Brunswick", "tz": "America/Moncton"},
    {"abbr": "nl", "label": "Newfoundland and Labrador", "tz": "America/St_Johns"},
    {"abbr": "nt", "label": "Northwest Territories", "tz": "America/Yellowknife"},
    {"abbr": "ns", "label": "Nova Scotia", "tz": "America/Halifax"},
    {"abbr": "nu", "label": "Nunavut", "tz": "America/Iqaluit"},
    {"abbr": "on", "label": "Ontario", "tz": "America/Toronto"},
    {"abbr": "pe", "label": "Prince Edward Island", "tz": "America/Halifax"},
    {"abbr": "qc", "label": "Quebec", "tz": "America/Toronto"},
    {"abbr": "sk", "label": "Saskatchewan", "tz": "America/Regina"},
    {"abbr": "yt", "label": "Yukon", "tz": "America/Whitehorse"},
]

DEFAULT_BC_TIME = (13, 55)  # used only for the gov.bc.ca sentence source, which doesn't always list a time

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


def normalize_label(text):
    """Collapses label text so 'Newfoundland & Labrador' and 'Newfoundland
    and Labrador' (and similar punctuation variance between sources) match.
    """
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def parse_time(time_text):
    """'1:55 PM PCT' / '1:55pm' -> (hour, minute) in 24h, local to the region."""
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


def extract_table_times(html_fragment):
    """Returns {normalized_label: (hour, minute)} for every row of a
    province/time table (works for both the 2-cell RSS table and the
    3-cell schedule-page table, since the time is always the last cell).
    """
    soup = BeautifulSoup(html_fragment, "html.parser")
    times = {}
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        label = normalize_label(cells[0].get_text(strip=True))
        time_parts = parse_time(cells[-1].get_text(strip=True))
        if label and time_parts:
            times[label] = time_parts
    return times


def to_utc(year, month, day, time_parts, tz_name):
    hour, minute = time_parts
    local = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz_name))
    return local.astimezone(UTC)


def parse_rss_feed(xml_text):
    """Yields (date_parts, {normalized_label: (h, m)}) for the latest 5 items."""
    root = ET.fromstring(xml_text)
    items = root.findall("./channel/item")[:5]

    for item in items:
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()
        content = (item.findtext(CONTENT_ENCODED_TAG) or "").strip()

        date_parts = find_date(title) or find_date(description) or find_date(content)
        if not date_parts:
            continue

        times = extract_table_times(content) or extract_table_times(description)
        if times:
            yield date_parts, times


def parse_govbc():
    """Finds 'next test is scheduled for <Month Day, Year> at <time>'.
    B.C.-only secondary source; returns (date_parts, times) or None.
    """
    html = fetch(GOVBC_URL)
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
    time_parts = parse_time(time_text) if time_text else DEFAULT_BC_TIME
    date_parts = (int(year), month, int(day))
    return date_parts, {normalize_label("British Columbia"): time_parts}


def parse_alertready_schedule():
    """Returns (date_parts, {normalized_label: (h, m)}) from the schedule
    table; every row shares the same date, so the first one found is used.
    """
    html = fetch(ALERTREADY_SCHEDULE_URL)
    soup = BeautifulSoup(html, "html.parser")
    date_parts = None
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 3:
            candidate = find_date(cells[1].get_text(strip=True))
            if candidate:
                date_parts = candidate
                break
    if not date_parts:
        return None
    times = extract_table_times(html)
    return (date_parts, times) if times else None


def gather_candidates():
    """Fetches each source once; returns [(source_name, date_parts, times)]
    in priority order (RSS first, then gov.bc.ca, then the schedule page).
    """
    candidates = []

    try:
        rss_xml = fetch(RSS_URL)
        for date_parts, times in parse_rss_feed(rss_xml):
            candidates.append(("alertready.ca RSS feed", date_parts, times))
    except Exception as exc:
        print(f"WARN: RSS feed source failed: {exc}", file=sys.stderr)

    try:
        result = parse_govbc()
        if result:
            candidates.append(("gov.bc.ca", *result))
    except Exception as exc:
        print(f"WARN: gov.bc.ca source failed: {exc}", file=sys.stderr)

    try:
        result = parse_alertready_schedule()
        if result:
            candidates.append(("alertready.ca schedule page", *result))
    except Exception as exc:
        print(f"WARN: alertready.ca schedule source failed: {exc}", file=sys.stderr)

    return candidates


def find_next_test(province, candidates):
    """Returns the next future UTC test datetime for a province, checking
    each candidate in priority order, or None if none apply.
    """
    label = normalize_label(province["label"])
    now = datetime.now(UTC)

    for source_name, date_parts, times in candidates:
        if label not in times:
            continue
        year, month, day = date_parts
        dt_utc = to_utc(year, month, day, times[label], province["tz"])
        if dt_utc >= now:
            print(f"Found upcoming {province['label']} test via {source_name}.", file=sys.stderr)
            return dt_utc

    return None


def format_ics_dt(dt_utc):
    return dt_utc.strftime("%Y%m%dT%H%M%SZ")


def build_ics(test_dt_utc, uid):
    dtstamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//alert-ready-blocker//alert-test//EN",
        "CALSCALE:GREGORIAN",
    ]

    if test_dt_utc is not None:
        start = test_dt_utc - timedelta(minutes=5)
        end = test_dt_utc + timedelta(minutes=10)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{format_ics_dt(start)}",
            f"DTEND:{format_ics_dt(end)}",
            "SUMMARY:Alert Ready Test",
            "DESCRIPTION:Scheduled Alert Ready test window.",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def write_ics(province, test_dt_utc):
    ics_path = f"{province['abbr']}-alert-test.ics"
    uid = f"{province['abbr']}-alert-ready-test@malchia7.github.io"

    if test_dt_utc is not None:
        print(f"Next {province['label']} Alert Ready test: {test_dt_utc.isoformat()}")
        with open(ics_path, "w", newline="") as f:
            f.write(build_ics(test_dt_utc, uid))
        return

    print(f"No future {province['label']} test date found on any source.", file=sys.stderr)
    try:
        with open(ics_path, "r") as f:
            existing = f.read()
        if "BEGIN:VCALENDAR" in existing:
            print(f"Preserving existing {ics_path}.", file=sys.stderr)
            return
    except FileNotFoundError:
        pass

    print(f"Writing empty VCALENDAR shell to {ics_path}.", file=sys.stderr)
    with open(ics_path, "w", newline="") as f:
        f.write(build_ics(None, uid))


def main():
    candidates = gather_candidates()
    for province in PROVINCES:
        test_dt_utc = find_next_test(province, candidates)
        write_ics(province, test_dt_utc)


if __name__ == "__main__":
    main()
