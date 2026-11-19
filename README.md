# Alert Ready Blocker

Publishes a `.ics` calendar feed of upcoming Alert Ready test windows for
every Canadian province and territory, and provides two iOS Shortcuts that
use it to automatically flip Airplane Mode on and off around the real test
time.

Site: https://malchia7.github.io/alert-ready-blocker/

## How it fits together

1. `check_for_upcoming_tests.py` runs daily via GitHub Actions and writes
   one `<abbr>-alert-test.ics` per province/territory to the repo root
   (e.g. `bc-alert-test.ics`, `ab-alert-test.ics`).
2. GitHub Pages serves `index.html` and all the `.ics` files directly from
   `main`.
3. You pick your province on `index.html`, subscribe to its feed in Apple
   Calendar (`webcal://...`), and install two Shortcuts (with their
   automations built in) — the same two Shortcuts work regardless of which
   province's calendar you subscribed to, since they just look for an
   `Alert Ready Test` event by title, not by province.
4. Daily at 8 AM, one automation checks if today has an `Alert Ready Test`
   event and, if so, creates two silent alarms — `AlertBlockerOn` at the
   event's start and `AlertBlockerOff` at its end. The other automation
   fires whenever any alarm goes off, checks which `AlertBlocker*` alarm iOS
   just auto-disabled, and toggles Airplane Mode to match.

## Why it's built this way

**Sources are fetched once, then matched per province.** Each RSS item and
the schedule page embed a province/time table (`{province: time}`), so
`check_for_upcoming_tests.py` fetches each source exactly once, extracts
every row into that table, and then matches each of the 13
provinces/territories against it — rather than re-fetching per province.
gov.bc.ca remains a B.C.-only secondary source, since no equivalent
government page exists generically across provinces.

**Province labels are normalized before matching**, because sources don't
agree on punctuation — e.g. the HTML schedule page writes
"Newfoundland &amp; Labrador" while the RSS feed writes "Newfoundland and
Labrador". `normalize_label()` collapses both to the same string so the
match isn't silently missed.

**Local time is converted to UTC via `zoneinfo`/`tzdata`, not a hand-written
`VTIMEZONE` block per province.** DST and permanent-offset rules change by
legislation — while building this, Alberta, Manitoba, and the Northwest
Territories all turned out to be moving to a permanent UTC offset in
November 2026, which a hand-rolled set of RRULEs would have gotten wrong.
`tzdata` is pinned as an explicit dependency (rather than relying on
whatever the host OS ships) so this stays correct and consistent between
local runs and the GitHub Actions runner.

**Sources checked in priority order, each with independent fallback.**
alertready.ca's RSS feed is tried first (polled by readers, least likely to
trip bot filters), then gov.bc.ca (B.C. only; often updated 2–3 weeks
earlier), then the alertready.ca schedule page as a last resort. Each fetch
is wrapped separately so a 403/timeout/parse miss on one source logs a
warning and falls through to the next instead of failing the run.

**Future-only, with a preserve-don't-clobber fallback, per province.** Only
dates `>=` now are considered a match. If no source lists a future date for
a given province, that province's existing `.ics` is left untouched (or an
empty `VCALENDAR` shell is written if the file doesn't exist yet) — so no
`webcal://` subscription ever 404s, and a day with nothing new to report
doesn't wipe out a previously-found date for any other province.

**-5/+10 minute buffer window.** Each calendar event spans 5 minutes before
the test to 10 minutes after it, verified to bracket the actual alert time
in both normal cases and day-boundary edge cases (e.g. a test at 11:58 PM).

**A fixed default time (1:55 PM) only applies to the gov.bc.ca source**,
used when a B.C. date is confirmed but the sentence doesn't list a specific
time. It's B.C.-only because that's the only source without a per-row time
table; the RSS feed and schedule page always carry an explicit time for
every province.

**Daily, year-round GitHub Action, not just May/November.** The check is a
no-op on days with nothing new, so there's no cost to running it daily all
year, and no reason to encode an assumption about which months BC runs
tests in.

**Why the iOS automation design changed from "wait inside one Shortcut" to
"alarms + a second Shortcut":** Shortcuts personal automations can only
trigger on a fixed time of day — there's no "when this calendar event
starts" trigger. An earlier design had one Shortcut compute the real
start/end times from the event and use `Wait` to sleep until each, but
background Shortcut execution is subject to iOS's execution-time limits, so
a long `Wait` isn't reliably guaranteed to survive. Alarms are OS-level and
don't have that problem — so the first Shortcut (8 AM, daily) creates
alarms from the event's real times, and the second Shortcut only has to do
its work the instant an alarm actually fires, which is much more reliable.
The 8 AM anchor time itself is a soft assumption: it only works if 8 AM is
earlier than the real test time (iOS schedules an alarm for *tomorrow* if
you create one for a time already past today). BC's test has always been
1:55 PM, so there's a wide margin, but this is worth knowing if that ever
changes.

**Shortcuts are distributed as iCloud share links, not self-hosted
`.shortcut` files.** iCloud share links are signed by Apple's backend.
Current iOS has no "Allow Untrusted Shortcuts" escape hatch anymore (that
setting was removed), so an unsigned, self-hosted `.shortcut` file can't be
imported by any route — not via a plain download link, and not via the
`shortcuts://import-shortcut` URL scheme either. The exported `.shortcut`
files are still kept in the repo as version-controlled backups in case the
Shortcuts ever need to be re-shared.

**Pages are preferred over Actions timing for the "is it synced yet"
question.** The Action runs well before the 8 AM automation (14:00 UTC =
6–7 AM Pacific depending on DST), but the real dependency is that a new
test date gets discovered by the Action — and therefore synced to your
calendar — well before test day. Since test dates are announced weeks in
advance and the Action runs daily, this isn't a practical race condition.
