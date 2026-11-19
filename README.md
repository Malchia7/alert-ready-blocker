# Alert Ready Blocker

Publishes a `.ics` calendar feed of upcoming B.C. Alert Ready test windows, and
provides two iOS Shortcuts that use it to automatically flip Airplane Mode on
and off around the real test time.

Site: https://malchia7.github.io/alert-ready-blocker/

## How it fits together

1. `check_for_upcoming_tests.py` runs daily via GitHub Actions and writes
   `bc-alert-test.ics` to the repo root.
2. GitHub Pages serves `index.html` and `bc-alert-test.ics` directly from
   `main`.
3. You subscribe to the feed in Apple Calendar (`webcal://...`) and install
   two Shortcuts (with their automations built in) from `index.html`.
4. Daily at 8 AM, one automation checks if today has an `Alert Ready Test`
   event and, if so, creates two silent alarms — `AlertBlockerOn` at the
   event's start and `AlertBlockerOff` at its end. The other automation
   fires whenever any alarm goes off, checks which `AlertBlocker*` alarm iOS
   just auto-disabled, and toggles Airplane Mode to match.

## Why it's built this way

**Sources checked in priority order, each with independent fallback.**
`check_for_upcoming_tests.py` tries alertready.ca's RSS feed first (polled by
readers, least likely to trip bot filters), then gov.bc.ca (often updated
2–3 weeks earlier), then the alertready.ca schedule page as a last resort.
Each fetch is wrapped separately so a 403/timeout/parse miss on one source
logs a warning and falls through to the next instead of failing the run.

**Future-only, with a preserve-don't-clobber fallback.** Only dates `>=` now
are considered a match. If no source lists a future BC date, the script
leaves the existing `bc-alert-test.ics` untouched (or writes an empty
`VCALENDAR` shell if no file exists yet) — so the `webcal://` subscription
never 404s, and a day with nothing new to report doesn't wipe out a
previously-found date.

**-5/+10 minute buffer window.** The calendar event spans 5 minutes before
the test to 10 minutes after it, verified to bracket the actual alert time
in both normal cases and day-boundary edge cases (e.g. a test at 11:58 PM).

**A fixed BC time default (1:55 PM) only when a date is confirmed but no
BC-specific time is listed.** Keeps the event from silently disappearing if
a source publishes the date before the time.

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
