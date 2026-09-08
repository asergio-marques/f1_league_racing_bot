# -*- coding: utf-8 -*-
"""Random season-calendar generator — emits the XML /round add-xml expects.

Invents a plausible calendar for one or more divisions: a random circuit per round, one
race a week, and an evening slot each division keeps for its whole season. The output is
the XML payload parsed by parse_round_xml in src/utils/round_import.py, so what this
writes is pasted into the modal whole.

Circuits are read from the bot's own migration (see load_tracks), not copied into this
file, because a circuit the bot does not hold would make the whole import fail. Time
zones are validated with the bot's own is_known_zone for the same reason.

Interactively asks for the divisions, a round count for each, and one time zone for the
run, then writes one file beside this script:

  - calendar.xml  — the whole run, every division, ready to paste

Nothing here touches the database or needs a running bot. The bot applies its own
validation when the payload goes in, and refuses the import whole if any round fails.

This is the one script in the family that reads no roster.csv: a calendar names circuits
and dates, never drivers, so it shares the conventions of its siblings but not their
contract.

Usage:
  python tools/data-generator/calendar-data/generate_calendar_data.py
"""

from __future__ import annotations

import calendar as calendar_module
import datetime
import pathlib
import random
import re
import sys
from xml.sax.saxutils import escape

# ─── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = pathlib.Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent
SRC_DIR = SCRIPT_DIR.parent.parent.parent / "src"

#: The migration that seeds the circuits. Named exactly rather than found by globbing for
#: the newest track migration: a later migration that edits the table would not repeat the
#: seed, and naming this one makes a rename fail loudly instead of silently reading the
#: wrong file.
TRACKS_MIGRATION_PATH = SRC_DIR / "db" / "migrations" / "029_track_data_expansion.sql"

CALENDAR_PATH = SCRIPT_DIR / "calendar.xml"

# ─── Generation rules ────────────────────────────────────────────────────────

#: A division needs three rounds for the three special formats and one more to leave a
#: NORMAL round behind them, so four is the shortest calendar worth generating.
MIN_ROUNDS = 4

#: Exactly one of each per division, at random positions; every other round is NORMAL. A
#: season carrying all three exercises every branch of the parser's format handling,
#: which is the point of generating one.
SPECIAL_FORMATS = ["Sprint", "Mystery", "Endurance"]
DEFAULT_FORMAT = "Normal"

#: The format that takes no <track>. A mystery round's circuit is concealed until the
#: round is run, and _check_track in round_import.py permits the omission for this format
#: alone.
TRACKLESS_FORMAT = "Mystery"

#: The evening slot, inclusive, on the hour. A league races after work; a round drawn at
#: 03:00 would be no use to anybody pasting this in.
EARLIEST_HOUR = 18
LATEST_HOUR = 22

#: Rounds sit exactly one week apart, so the weekday and the time never move across a
#: season — which is what makes a calendar readable at a glance.
DAYS_BETWEEN_ROUNDS = 7

#: Typed at the time-zone question, or taken by pressing Enter.
DEFAULT_TIMEZONE = "Europe/Lisbon"

#: What the XML modal accepts in one paste (XmlRoundModal, max_length=4000). A longer
#: calendar is imported in several passes — the importer adds to a division rather than
#: replacing it — so this is a warning, not a limit to generate within.
MODAL_CHARACTER_LIMIT = 4000

#: Weekdays as datetime numbers them, Monday 0. Drawn without replacement while there are
#: enough to go round, so no two divisions race on the same night.
WEEKDAY_NUMBERS = list(range(7))


# ─── The bot's track list ────────────────────────────────────────────────────

#: One seeded circuit: ``( 1, 'Albert Park Circuit', 'Australian Grand Prix', ...``. Only
#: the id and the name are wanted. The name is single-quoted SQL with no escaping in the
#: seed, no circuit name carrying an apostrophe.
_TRACK_ROW_PATTERN = re.compile(r"^\s*\(\s*(\d+)\s*,\s*'([^']+)'", re.MULTILINE)

_TRACK_INSERT_MARKER = "INSERT OR IGNORE INTO tracks"


def load_tracks(path=TRACKS_MIGRATION_PATH):
    """Return the seeded circuits as a list of (id, name), ordered by id.

    Read from the migration rather than hard-coded here, for the reason the roster
    generator imports the bot's nationality list: a circuit this script invented that the
    bot does not hold would make the whole import fail. The tracks live in SQL rather than
    in a module, though, and importing track_service would mean opening a database, which
    nothing in this family does — so the seed statement is parsed instead.

    Only the rows of the INSERT are read. The UPDATE statements below it in the same
    migration rename old round values and carry no circuit ids.
    """
    if not path.exists():
        raise ValueError(
            f"Track list not found at {path}. This script must be run from a full "
            "checkout."
        )

    sql = path.read_text(encoding="utf-8")
    start = sql.find(_TRACK_INSERT_MARKER)
    if start == -1:
        raise ValueError(
            f"No `{_TRACK_INSERT_MARKER}` statement in {path.name}. The migration has "
            "changed shape and this parser needs updating."
        )

    statement = sql[start:sql.find(";", start)]
    tracks = [(int(id_), name) for id_, name in _TRACK_ROW_PATTERN.findall(statement)]

    if not tracks:
        raise ValueError(
            f"The `{_TRACK_INSERT_MARKER}` statement in {path.name} seeded no circuits. "
            "The migration has changed shape and this parser needs updating."
        )

    return sorted(tracks)


def load_zone_validator(src_dir=SRC_DIR):
    """Return the bot's is_known_zone, for validating the time-zone answer.

    Imported rather than reimplemented because the check is deliberately case-sensitive:
    ``ZoneInfo("europe/lisbon")`` resolves on a Windows development machine and raises on
    the Raspberry Pi. A folded name accepted here would produce a payload that imports on
    one host and fails on the other.
    """
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    try:
        from utils.timezones import is_known_zone
    except ImportError as error:
        raise ValueError(
            f"Could not import the bot's time-zone list from {src_dir} ({error}). "
            "This script must be run from a full checkout."
        ) from error

    return is_known_zone


# ─── Input parsing ───────────────────────────────────────────────────────────

def split_entries(raw):
    """Split a comma-separated response into entries.

    Only a single space immediately following a comma is stripped — the same rule the
    sibling generators apply, so a division typed there is typed identically here.
    """
    entries = []
    for index, part in enumerate(raw.split(",")):
        if index > 0 and part.startswith(" "):
            part = part[1:]
        entries.append(part)
    return entries


def validate_divisions(entries):
    """Return an error message describing why *entries* is unusable, or None.

    There is no roster to check a name against, unlike the sibling generators: a division
    is whatever the manager calls it, and the bot matches the name against its own pending
    divisions when the payload goes in.
    """
    if not entries or all(entry == "" for entry in entries):
        return "Enter at least one division."

    for entry in entries:
        if entry == "":
            return "Empty division name — check for a stray or doubled comma."

    seen = set()
    for entry in entries:
        key = entry.casefold()
        if key in seen:
            return f"Duplicate division name '{entry}'."
        seen.add(key)

    return None


def prompt_divisions(read_line=input):
    """Ask which divisions to generate for, re-prompting until the answer is usable."""
    while True:
        raw = read_line("Divisions to generate a calendar for (comma-separated): ")
        entries = split_entries(raw)
        error = validate_divisions(entries)
        if error is None:
            return entries
        print(f"  {error}\n")


def prompt_round_count(division, maximum, read_line=input):
    """Ask how many rounds *division* runs, re-prompting until it is in range.

    Both bounds are named in the question and again in every refusal, because neither is
    guessable: the floor is the three special formats plus a NORMAL round, and the ceiling
    is the number of circuits the bot holds.
    """
    while True:
        raw = read_line(f"Rounds for {division} ({MIN_ROUNDS}-{maximum}): ")
        try:
            count = int(raw.strip())
        except ValueError:
            print(f"  Enter a whole number between {MIN_ROUNDS} and {maximum}.\n")
            continue

        if count < MIN_ROUNDS:
            print(
                f"  {count} is too few — {MIN_ROUNDS} is the minimum, leaving one "
                f"normal round beyond the sprint, mystery and endurance ones.\n"
            )
            continue
        if count > maximum:
            print(
                f"  {count} is too many — the bot holds {maximum} circuits and no "
                f"division repeats one.\n"
            )
            continue

        return count


def prompt_timezone(is_known_zone, read_line=input):
    """Ask for the IANA zone the round times are stated in, once for the whole run.

    An empty answer takes DEFAULT_TIMEZONE, which is the common case. The name is
    validated exactly, case included, because the bot validates it that way.
    """
    while True:
        raw = read_line(f"Time zone for the round times [{DEFAULT_TIMEZONE}]: ")
        name = raw.strip() or DEFAULT_TIMEZONE
        if is_known_zone(name):
            return name
        print(
            f"  '{name}' is not an IANA time zone the bot recognises. Use a name such "
            f"as {DEFAULT_TIMEZONE}, capitalised exactly.\n"
        )


# ─── Calendar generation ─────────────────────────────────────────────────────

def choose_weekdays(count, rng):
    """Pick a weekday for each division, avoiding a clash where that is possible.

    Drawn without replacement so that no two divisions race on the same night, which is
    how a league with several tiers actually runs. There are only seven nights, so a run
    with more divisions than that must repeat one: it falls back to drawing with
    replacement for the remainder, and the caller says so.
    """
    if count <= len(WEEKDAY_NUMBERS):
        return rng.sample(WEEKDAY_NUMBERS, count)

    weekdays = rng.sample(WEEKDAY_NUMBERS, len(WEEKDAY_NUMBERS))
    while len(weekdays) < count:
        weekdays.append(rng.choice(WEEKDAY_NUMBERS))
    return weekdays


def first_round_date(weekday, year, rng):
    """Draw a start date in *year*, moved forward to the next *weekday*.

    The season starts in the year after the run, at a random point in it, so two divisions
    generated together do not begin on the same date. The forward move can carry a
    late-December draw into the following January, which is left alone: a season crossing
    the new year is an ordinary thing and the bot has no opinion about it.
    """
    days_in_year = 366 if calendar_module.isleap(year) else 365
    start = datetime.date(year, 1, 1) + datetime.timedelta(
        days=rng.randrange(days_in_year)
    )
    return start + datetime.timedelta(days=(weekday - start.weekday()) % 7)


def choose_formats(count, rng):
    """Return *count* formats: one of each special, the rest NORMAL.

    The three are placed at distinct positions drawn across the whole calendar, so a
    season's sprint and mystery rounds fall where they fall rather than at fixed points.
    """
    formats = [DEFAULT_FORMAT] * count
    positions = rng.sample(range(count), len(SPECIAL_FORMATS))
    for index, special in zip(positions, SPECIAL_FORMATS):
        formats[index] = special
    return formats


def generate_division(count, weekday, tracks, year, rng):
    """Build one division's rounds as a list of (datetime, format, track_id or None).

    The time is drawn once and held for every round, and the rounds sit exactly a week
    apart, so a division keeps one slot for its whole season. Tracks are drawn without
    replacement, so no circuit repeats within the division.
    """
    hour = rng.randint(EARLIEST_HOUR, LATEST_HOUR)
    date = first_round_date(weekday, year, rng)
    formats = choose_formats(count, rng)
    drawn = rng.sample(tracks, count)

    rounds = []
    for number, (fmt, (track_id, _name)) in enumerate(zip(formats, drawn)):
        when = datetime.datetime.combine(
            date + datetime.timedelta(days=number * DAYS_BETWEEN_ROUNDS),
            datetime.time(hour=hour),
        )
        rounds.append((when, fmt, None if fmt == TRACKLESS_FORMAT else track_id))

    return rounds


# ─── Output ──────────────────────────────────────────────────────────────────

def render_xml(calendars, zone_name):
    """Render the whole run as the payload /round add-xml parses.

    Built as text rather than through an XML library because the shape is fixed and small,
    and because the file is read by a person before it is pasted. The division name is
    escaped: it is the one part of the document that came from the keyboard, and an
    ampersand in it would otherwise make the payload unparseable.
    """
    lines = ["<config>"]
    for division, rounds in calendars:
        name = escape(division, {'"': "&quot;"})
        lines.append(f'  <division name="{name}">')
        for when, fmt, track_id in rounds:
            stamp = when.isoformat(timespec="minutes")
            lines.append("    <round>")
            lines.append(f"      <datetime>{stamp}</datetime>")
            lines.append(f"      <timezone>{zone_name}</timezone>")
            lines.append(f"      <format>{fmt}</format>")
            if track_id is not None:
                lines.append(f"      <track>{track_id}</track>")
            lines.append("    </round>")
        lines.append("  </division>")
    lines.append("</config>")
    return "\n".join(lines) + "\n"


def report(division, rounds):
    """Print what a division came out as, and where its special rounds landed."""
    first = rounds[0][0]
    last = rounds[-1][0]
    specials = ", ".join(
        f"{fmt.lower()} R{number}"
        for number, (_when, fmt, _track) in enumerate(rounds, start=1)
        if fmt != DEFAULT_FORMAT
    )
    print(
        f"{division}: {len(rounds)} rounds, "
        f"{calendar_module.day_name[first.weekday()]}s at {first:%H:%M} — "
        f"{first:%d %b %Y} to {last:%d %b %Y} ({specials})."
    )


# ─── Entry point ─────────────────────────────────────────────────────────────

def main(argv=None):
    # Both of these read the checkout, so a missing or changed source fails before the
    # first question rather than after a set of answers has been typed.
    try:
        tracks = load_tracks()
        is_known_zone = load_zone_validator()
    except ValueError as error:
        print(error)
        return 1

    divisions = prompt_divisions()
    counts = [prompt_round_count(division, len(tracks)) for division in divisions]
    zone_name = prompt_timezone(is_known_zone)

    rng = random.Random()
    weekdays = choose_weekdays(len(divisions), rng)
    year = datetime.date.today().year + 1

    print()
    if len(divisions) > len(WEEKDAY_NUMBERS):
        print(
            f"{len(divisions)} divisions and only {len(WEEKDAY_NUMBERS)} nights — some "
            f"share a weekday.\n"
        )

    calendars = []
    for division, count, weekday in zip(divisions, counts, weekdays):
        rounds = generate_division(count, weekday, tracks, year, rng)
        calendars.append((division, rounds))
        report(division, rounds)

    xml = render_xml(calendars, zone_name)
    CALENDAR_PATH.write_text(xml, encoding="utf-8")

    print(f"\n  {CALENDAR_PATH}")
    print("\nRun /round add-xml and paste the file into the modal.")
    if len(xml) > MODAL_CHARACTER_LIMIT:
        print(
            f"It is {len(xml)} characters and the modal takes {MODAL_CHARACTER_LIMIT}, "
            "so paste it a few divisions at a time — the importer adds to a division "
            "rather than replacing it, so several passes build one calendar."
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (EOFError, KeyboardInterrupt):
        # Ctrl+C, Ctrl+Z, or piped input that ran out mid-question.
        print("\nAborted — nothing was written.")
        sys.exit(1)
