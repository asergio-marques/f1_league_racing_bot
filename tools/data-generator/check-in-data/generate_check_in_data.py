# -*- coding: utf-8 -*-
"""Random check-in generator — emits the blocks /test-mode rsvp set-status expects.

Drivers are read from roster.csv in the parent directory, written there by the sibling
roster generator - this script invents nobody of its own, so the IDs it emits are the
ones the bot actually holds.

Interactively asks which divisions to cover, then writes two files per division beside
this script:

  - <slug>_initial.txt  — the first check-in: who responded, and how
  - <slug>_changed.txt  — the revisions only, one line per driver who changed their mind

Both files are strictly "<ID>, <status>", one driver per line, because that is all the
bulk-set modal will parse - no header, no comments, no mention tags. A driver who never
checked in is simply absent from the initial file; there is no token for NO_RSVP.

Nothing here touches the database or needs a running bot. Paste a division's initial
file into the modal, then its changed file, to walk the division through a full
check-in and a round of revisions.

Usage:
  python tools/data-generator/check-in-data/generate_check_in_data.py
"""

from __future__ import annotations

import csv
import pathlib
import random
import re
import sys
import unicodedata

# ─── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = pathlib.Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent

CSV_PATH = DATA_DIR / "roster.csv"

INITIAL_TEMPLATE = "{slug}_initial.txt"
CHANGED_TEMPLATE = "{slug}_changed.txt"

# ─── Roster columns ──────────────────────────────────────────────────────────

ID_COLUMN = "ID"
NAME_COLUMN = "Driver name"
TEAM_COLUMN = "Team"
DIVISION_COLUMN = "Division"

#: A reserve driver is one whose team is exactly this - there is no column for it. The
#: match is exact rather than a prefix, because a team merely called "Reserve Racing" is
#: a normal team with normal seats.
RESERVE_TEAM = "Reserve"

# ─── Generation rules ────────────────────────────────────────────────────────

#: The share of a division failing to check in at all, as a ceiling on the draw: nought to
#: a fifth. A share rather than a count, so that a division of ten and a division of forty
#: come out equally well attended - a fixed count leaves the small one looking abandoned.
#: Nought is deliberate: a division where everyone answers is a real round, worth generating.
MIN_OMISSIONS = 0
MAX_OMISSION_SHARE = 0.20

#: Seated drivers pushed to "decline" regardless of the weighted draw, so a division
#: reliably comes out with absences to plan around rather than leaving them to chance.
MIN_FORCED_DECLINES = 0
MAX_FORCED_DECLINES = 5

#: Drivers whose status moves between the two files.
MIN_REVISIONS = 1
MAX_REVISIONS = 10

#: The three tokens the modal accepts. NO_RSVP has none - omission is how it is said.
STATUSES = ["accept", "tentative", "decline"]
DECLINE = "decline"

#: Weighted towards accept, because a division where a third of the grid is unsure does
#: not look like a league checking in for a round.
STATUS_WEIGHTS = [65, 25, 10]

LINE_TEMPLATE = "{driver_id}, {status}"


# ─── Roster ──────────────────────────────────────────────────────────────────

def load_roster(path=CSV_PATH):
    """Read the roster CSV into a list of (id, name, team, division) rows.

    File order is preserved throughout, so every generated file can be read down
    alongside the CSV.
    """
    if not path.exists():
        raise ValueError(
            f"Roster not found at {path}. Generate one first with:\n"
            "  python tools/data-generator/test-roster/generate_test_roster.py --record"
        )

    rows = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        missing = [
            column
            for column in (ID_COLUMN, TEAM_COLUMN, DIVISION_COLUMN)
            if column not in fields
        ]
        if missing:
            raise ValueError(
                f"Roster at {path} is missing the column(s): {', '.join(missing)}. "
                "Regenerate it with the roster generator."
            )

        for number, row in enumerate(reader, start=2):
            raw_id = (row.get(ID_COLUMN) or "").strip()
            if not raw_id:
                continue
            try:
                driver_id = int(raw_id)
            except ValueError:
                raise ValueError(
                    f"Roster line {number}: '{raw_id}' is not a numeric driver ID."
                )
            rows.append((
                driver_id,
                (row.get(NAME_COLUMN) or "").strip(),
                (row.get(TEAM_COLUMN) or "").strip(),
                (row.get(DIVISION_COLUMN) or "").strip(),
            ))

    if not rows:
        raise ValueError(f"Roster at {path} holds no drivers.")

    return rows


def division_names(roster):
    """List the divisions the roster holds, in first-appearance order."""
    names = []
    seen = set()
    for _, _, _, division in roster:
        key = division.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(division)
    return names


def drivers_in(roster, division):
    """Return the roster rows belonging to *division*, matched case-insensitively.

    Case is ignored because the bot resolves a division name the same way, so a name
    that works in the command works here.
    """
    key = division.casefold()
    return [row for row in roster if row[3].casefold() == key]


def is_reserve(row):
    """Whether a roster row is a reserve rather than a seated driver."""
    return row[2].casefold() == RESERVE_TEAM.casefold()


# ─── Input parsing ───────────────────────────────────────────────────────────

def split_entries(raw):
    """Split a comma-separated response into entries.

    Only a single space immediately following a comma is stripped - the same rule the
    roster generator applies, so a division typed there is typed identically here.
    """
    entries = []
    for index, part in enumerate(raw.split(",")):
        if index > 0 and part.startswith(" "):
            part = part[1:]
        entries.append(part)
    return entries


def validate_divisions(entries, known):
    """Return an error message describing why *entries* is unusable, or None.

    A division absent from the roster is refused rather than skipped: there is nobody to
    generate for, and a silently empty file is worse than being asked again.
    """
    if not entries or all(entry == "" for entry in entries):
        return "Enter at least one division."

    for entry in entries:
        if entry == "":
            return "Empty division name - check for a stray or doubled comma."

    seen = set()
    for entry in entries:
        key = entry.casefold()
        if key in seen:
            return f"Duplicate division name '{entry}'."
        seen.add(key)

    lookup = {name.casefold() for name in known}
    for entry in entries:
        if entry.casefold() not in lookup:
            return (
                f"No division called '{entry}' in {CSV_PATH.name}. "
                f"It holds: {', '.join(known)}."
            )

    return None


def prompt_divisions(known, read_line=input):
    """Ask which divisions to cover, re-prompting until every name is in the roster.

    The roster's own spelling is returned rather than what was typed, so the summary and
    the filenames read consistently however the name was capitalised.
    """
    canonical = {name.casefold(): name for name in known}
    while True:
        raw = read_line("Divisions to generate check-in data for (comma-separated): ")
        entries = split_entries(raw)
        error = validate_divisions(entries, known)
        if error is None:
            return [canonical[entry.casefold()] for entry in entries]
        print(f"  {error}\n")


# ─── Filenames ───────────────────────────────────────────────────────────────

def slugify(name):
    """Reduce a division name to the lower-case, underscored form used in filenames.

    Accents are folded to their base letter before the strip, so "Pró Séries" comes out
    as pro_series rather than losing the letters it was accented on.
    """
    decomposed = unicodedata.normalize("NFKD", name.strip().casefold())
    folded = "".join(char for char in decomposed if not unicodedata.combining(char))
    slug = re.sub(r"\s+", "_", folded)
    slug = re.sub(r"[^a-z0-9_]", "", slug)
    return re.sub(r"_+", "_", slug).strip("_")


def build_slugs(divisions):
    """Settle the filename slug for every division before anything is written.

    Two names can reduce to the same slug - "Div 1" and "Div_1" both give div_1 - so a
    repeat is suffixed rather than left to overwrite the other division's files.
    """
    slugs = []
    used = set()
    for index, division in enumerate(divisions, start=1):
        base = slugify(division) or f"division_{index}"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        slugs.append(candidate)
    return slugs


# ─── Check-in generation ─────────────────────────────────────────────────────

def choose_omissions(drivers, rng):
    """Pick the drivers who will not check in at all.

    Two rules shape the draw:

      - Nought to a fifth of the division is drawn, from the division at large. Drawing
        across seated and reserve alike keeps the omissions in the proportion the division
        is actually made of, rather than falling on the reserves.
      - Should the draw have taken only reserves, one is exchanged for a seated driver, so
        a division that omits anybody at all omits a seat-holder - the case a league
        chases up, and the one worth generating.

    The count is capped so that at least one driver always checks in, and a division too
    small for a fifth to reach one driver simply omits nobody.
    """
    ceiling = max(0, len(drivers) - 1)
    highest = min(int(len(drivers) * MAX_OMISSION_SHARE), ceiling)
    count = rng.randint(MIN_OMISSIONS, highest) if highest >= MIN_OMISSIONS else 0
    if count == 0:
        return []

    omitted = rng.sample(drivers, count)

    if not any(not is_reserve(row) for row in omitted):
        omitted_ids = {row[0] for row in omitted}
        candidates = [
            row for row in drivers if not is_reserve(row) and row[0] not in omitted_ids
        ]
        if candidates:
            omitted[0] = rng.choice(candidates)

    return omitted


def assign_statuses(checked_in, rng):
    """Give every checked-in driver a status, as {driver_id: status}.

    Nought to five seated drivers are pushed to decline outright; the rest draw across
    all three statuses weighted towards accept, so a reserve can decline too - just less
    often than a driver whose seat is going empty.
    """
    seated = [row for row in checked_in if not is_reserve(row)]
    forced = min(rng.randint(MIN_FORCED_DECLINES, MAX_FORCED_DECLINES), len(seated))
    decliners = {row[0] for row in rng.sample(seated, forced)}

    statuses = {}
    for row in checked_in:
        if row[0] in decliners:
            statuses[row[0]] = DECLINE
        else:
            statuses[row[0]] = rng.choices(STATUSES, weights=STATUS_WEIGHTS, k=1)[0]
    return statuses


def generate_initial(drivers, rng):
    """Return {driver_id: status} for the division's first check-in.

    Omitted drivers are absent from the mapping entirely, which is how the modal is told
    they never responded.
    """
    omitted_ids = {row[0] for row in choose_omissions(drivers, rng)}
    checked_in = [row for row in drivers if row[0] not in omitted_ids]
    return assign_statuses(checked_in, rng)


def generate_revisions(drivers, initial, rng):
    """Return {driver_id: status} for the drivers who change their check-in.

    One to ten are drawn from the whole division, those who never responded included. A
    driver who did respond moves to one of the *other* two statuses, so a revision always
    revises something; one who did not draws across all three, which is the late check-in
    every division sees a few of.
    """
    count = min(rng.randint(MIN_REVISIONS, MAX_REVISIONS), len(drivers))

    changed = {}
    for row in rng.sample(drivers, count):
        previous = initial.get(row[0])
        changed[row[0]] = rng.choice([s for s in STATUSES if s != previous])
    return changed


# ─── Output ──────────────────────────────────────────────────────────────────

def format_lines(drivers, statuses):
    """Render *statuses* as modal lines, in roster order."""
    return [
        LINE_TEMPLATE.format(driver_id=row[0], status=statuses[row[0]])
        for row in drivers
        if row[0] in statuses
    ]


def write_lines(path, lines):
    """Write the lines to *path*, replacing whatever was there.

    These files are disposable per-run output rather than a shared contract like
    roster.csv, so they are replaced without asking and without a backup.
    """
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def report(division, drivers, initial, changed, initial_path, changed_path):
    """Print what a division came out as, and where its two files went."""
    counts = [
        f"{sum(1 for status in initial.values() if status == name)} {name}"
        for name in STATUSES
    ]
    print(
        f"{division}: {len(initial)} of {len(drivers)} checked in "
        f"({', '.join(counts)}), {len(drivers) - len(initial)} omitted; "
        f"{len(changed)} revised."
    )
    print(f"  {initial_path}")
    print(f"  {changed_path}")


# ─── Entry point ─────────────────────────────────────────────────────────────

def main(argv=None):
    # Load the roster up front, so a missing or malformed CSV fails before the question
    # rather than after it.
    try:
        roster = load_roster()
    except ValueError as error:
        print(error)
        return 1

    divisions = prompt_divisions(division_names(roster))
    slugs = build_slugs(divisions)
    rng = random.Random()

    print()
    for division, slug in zip(divisions, slugs):
        drivers = drivers_in(roster, division)
        initial = generate_initial(drivers, rng)
        changed = generate_revisions(drivers, initial, rng)

        initial_path = SCRIPT_DIR / INITIAL_TEMPLATE.format(slug=slug)
        changed_path = SCRIPT_DIR / CHANGED_TEMPLATE.format(slug=slug)
        write_lines(initial_path, format_lines(drivers, initial))
        write_lines(changed_path, format_lines(drivers, changed))

        report(division, drivers, initial, changed, initial_path, changed_path)

    print(
        "\nFor each division, run /test-mode rsvp set-status and paste its initial file "
        "into the modal, then run it again and paste the changed file."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (EOFError, KeyboardInterrupt):
        # Ctrl+C, Ctrl+Z, or piped input that ran out mid-question.
        print("\nAborted - nothing was written.")
        sys.exit(1)
