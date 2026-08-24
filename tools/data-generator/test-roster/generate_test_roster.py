# -*- coding: utf-8 -*-
"""Random test-roster generator — emits /test-mode roster add commands.

Driver nicknames are read from names.txt beside this script - extend that file rather
than the code. Nationalities are not a file: they are imported from the bot itself, so
that every one emitted is one the bot accepts (see load_nationalities), and drawn with
weights that favour the nationalities a real sim-racing grid is thick with (see
build_nationality_weights).

Interactively asks for the division names and the team names, invents a plausible
roster across them, then writes:

  - commands.txt  (always, beside this script) — one ready-to-paste command per driver
  - roster.csv    (only with --record, in tools/data-generator/) — the ID / name / team /
    division / nationality mapping, for the sibling generator scripts to consume
  - teams.txt     (only with --record, in tools/data-generator/) — the team names alone,
    because a team whose seats both went empty has no row in the CSV to be found by

Nothing here touches the database or needs a running bot. The synthetic IDs simply
mirror the convention in src/services/test_roster_service.py, so the CSV lines up with
what the bot creates when these commands are pasted into a clean test-mode season.

Usage:
  python tools/data-generator/test-roster/generate_test_roster.py [--record]
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import random
import sys

# ─── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = pathlib.Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent
SRC_DIR = SCRIPT_DIR.parent.parent.parent / "src"

COMMANDS_PATH = SCRIPT_DIR / "commands.txt"
NAMES_PATH = SCRIPT_DIR / "names.txt"
CSV_PATH = DATA_DIR / "roster.csv"
CSV_BACKUP_PATH = DATA_DIR / "roster_old.csv"
TEAMS_PATH = DATA_DIR / "teams.txt"

# ─── Generation rules ────────────────────────────────────────────────────────

#: First synthetic driver ID. Mirrors _SYNTHETIC_ID_BASE + 1 in test_roster_service.py,
#: which is what the bot hands out to the first test driver on a clean database.
FIRST_DRIVER_ID = 9_000_000_000_000_000_001

#: The reserve team is appended to every division and is never taken from user input —
#: the name is protected by TeamService (see src/services/team_service.py).
RESERVE_TEAM = "Reserve"

MAX_DRIVERS_PER_TEAM = 2
MIN_RESERVE_DRIVERS = 0
MAX_RESERVE_DRIVERS = 10

#: How many of a team's two seats are filled, and how often. Weighted towards a full team:
#: a flat draw across 0/1/2 left whole divisions looking abandoned, which is not what a
#: league under test looks like.
TEAM_SIZES = [2, 1, 0]
TEAM_SIZE_WEIGHTS = [60, 30, 10]

#: The first division is always filled completely and always carries at least this many
#: reserves, so every run exercises a full grid and the reserve path.
MIN_GUARANTEED_RESERVE_DRIVERS = 1

#: Typed in place of the team list to stand for the current grid.
DEFAULT_TEAMS_TOKEN = "<<<DEFAULT>>>"

DEFAULT_TEAMS = [
    "Alpine", "Aston Martin", "Audi", "Cadillac", "Ferrari", "Haas",
    "McLaren", "Mercedes", "Red Bull", "VCARB", "Williams",
]

COMMAND_TEMPLATE = (
    "/test-mode roster add driver_name:{name} team_name:{team} division:{division} "
    "nationality:{nationality}"
)

#: Appended, never inserted. The sibling generators read roster.csv by DictReader but then
#: index each row positionally (row[0]..row[3]), so a column added anywhere but the end
#: would break them silently.
CSV_HEADERS = ["ID", "Driver name", "Team", "Division", "Nationality"]


# ─── Name pool ───────────────────────────────────────────────────────────────

def load_names(path=NAMES_PATH):
    """Read the nickname pool from *path*, one name per line.

    Blank lines and lines starting with '#' are ignored, and duplicates are dropped
    so that drawing without replacement really does yield unique names.
    """
    if not path.exists():
        raise ValueError(
            f"Name pool not found at {path}. It ships beside this script - restore it "
            "from version control, or write one name per line."
        )

    names = []
    seen = set()
    duplicates = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        key = name.casefold()
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        names.append(name)

    if not names:
        raise ValueError(f"Name pool at {path} holds no usable names.")

    if duplicates:
        print(f"Note: {duplicates} duplicate name(s) in {path.name} ignored.")

    return names


# ─── Nationality pool ────────────────────────────────────────────────────────

# Nationalities are drawn with weights rather than uniformly. A flat draw over all 192
# gave a grid that looked nothing like a league's: a single Briton among twenty drivers
# from countries that field no sim racers at all. The tiers below tilt the draw towards
# the nationalities that actually crowd an F1 or online-racing grid, while leaving every
# other nationality reachable so the long tail still gets exercised.
#
# Names in a tier must be the bot's canonical values, as load_nationalities returns them.
# One that is not is reported and ignored — see build_nationality_weights.

#: The nationalities a sim-racing grid is thick with: the F1 heartlands and the largest
#: online-racing populations.
NATIONALITY_TIER_1 = [
    "American", "Australian", "Belgian", "Brazilian", "British", "Dutch", "French",
    "German", "Italian", "Portuguese", "Spanish",
]

#: Common without being everywhere: the rest of racing Europe, the countries that host a
#: round or field a driver, and the big gaming markets beyond it.
NATIONALITY_TIER_2 = [
    "Argentine", "Austrian", "Canadian", "Chinese", "Colombian", "Croatian", "Czech",
    "Danish", "Emirati", "Estonian", "Filipino", "Finnish", "Greek", "Hungarian",
    "Indian", "Indonesian", "Irish", "Israeli", "Japanese", "Malaysian", "Mexican",
    "Monegasque", "New Zealander", "Norwegian", "Polish", "Romanian", "Russian", "Saudi",
    "Serbian", "Singaporean", "Slovak", "Slovenian", "South African", "South Korean",
    "Swedish", "Swiss", "Thai", "Turkish", "Ukrainian",
]

#: Relative likelihoods. A single tier-1 nationality is forty times as likely as a single
#: tail one and near four times as likely as a tier-2 one. Across the whole pool that puts
#: roughly 44% of a grid in tier 1, 42% in tier 2 and 14% in the tail — the tail being
#: some 140 nationalities strong, so the odd unlikely flag still turns up, "Other" among
#: them.
NATIONALITY_WEIGHT_TIER_1 = 40
NATIONALITY_WEIGHT_TIER_2 = 11
NATIONALITY_WEIGHT_TAIL = 1


def build_nationality_weights(nationalities):
    """Return a weight per entry of *nationalities*, in the order given.

    A tier naming something the bot does not accept is a typo in the tiers above, not a
    reason to stop: it is reported and the draw carries on without it, because the tool
    is still perfectly usable and the alternative is a maintainer blocked from generating
    a roster by a cosmetic mistake.
    """
    tiers = {}
    for name in NATIONALITY_TIER_1:
        tiers[name] = NATIONALITY_WEIGHT_TIER_1
    for name in NATIONALITY_TIER_2:
        tiers[name] = NATIONALITY_WEIGHT_TIER_2

    unknown = sorted(set(tiers) - set(nationalities))
    if unknown:
        print(
            "Note: the weighting names {}, which the bot does not accept - ignored, and "
            "the rest of the pool is drawn as usual.".format(", ".join(unknown))
        )

    return [tiers.get(name, NATIONALITY_WEIGHT_TAIL) for name in nationalities]


def load_nationalities(src_dir=SRC_DIR):
    """Return every nationality the bot accepts, sorted.

    This one script imports from src/ where its siblings port the rule and say so. The
    pool must be *exactly* what the bot validates against: a nationality it rejects makes
    the whole command fail, and a hand-kept list beside names.txt would drift from
    NATIONALITY_LOOKUP the first time a nationality was added there.

    The canonical values are what is wanted, not the keys: the keys hold both adjectives
    and country names, many mapping to one value, and it is the value the bot stores.
    """
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    try:
        from utils.nationality_data import NATIONALITY_LOOKUP
    except ImportError as error:
        raise ValueError(
            f"Could not import the bot's nationality list from {src_dir} ({error}). "
            "This script must be run from a full checkout."
        ) from error

    return sorted(set(NATIONALITY_LOOKUP.values()))


# ─── Input parsing ───────────────────────────────────────────────────────────

def split_entries(raw):
    """Split a comma-separated response into entries.

    Only a single space immediately following a comma is stripped — everything else,
    including internal and trailing spaces, is left alone so that names such as
    "Red Bull " survive intact.
    """
    entries = []
    for index, part in enumerate(raw.split(",")):
        if index > 0 and part.startswith(" "):
            part = part[1:]
        entries.append(part)
    return entries


def expand_default_teams(entries):
    """Replace any DEFAULT_TEAMS_TOKEN entry with the default grid, in place.

    Expanding before validation means a token mixed with hand-typed teams still has its
    duplicates caught, and the token on its own simply becomes the eleven names.
    """
    expanded = []
    for entry in entries:
        if entry.strip().casefold() == DEFAULT_TEAMS_TOKEN.casefold():
            expanded.extend(DEFAULT_TEAMS)
        else:
            expanded.append(entry)
    return expanded


def validate_entries(entries, label, reject_reserve=False):
    """Return an error message describing why *entries* is unusable, or None."""
    if not entries or all(entry == "" for entry in entries):
        return f"Enter at least one {label}."

    for entry in entries:
        if entry == "":
            return f"Empty {label} name - check for a stray or doubled comma."

    seen = set()
    for entry in entries:
        key = entry.casefold()
        if key in seen:
            return f"Duplicate {label} name '{entry}'."
        seen.add(key)

    if reject_reserve:
        for entry in entries:
            if entry.casefold() == RESERVE_TEAM.casefold():
                return (
                    f"'{RESERVE_TEAM}' is a protected team name and is added to every "
                    "division automatically - leave it out."
                )

    return None


def prompt_entries(prompt, label, reject_reserve=False, expand_default=False, read_line=input):
    """Ask for a comma-separated list, re-prompting until it validates."""
    while True:
        raw = read_line(prompt)
        entries = split_entries(raw)
        if expand_default:
            entries = expand_default_teams(entries)
        error = validate_entries(entries, label, reject_reserve=reject_reserve)
        if error is None:
            return entries
        print(f"  {error}\n")


# ─── Roster generation ───────────────────────────────────────────────────────

def plan_team_sizes(divisions, teams, rng):
    """Draw a driver count for every (division, team) pair, division-wise.

    Returns a list of (division, team, count) in generation order: every division in
    turn, its input teams in order, then its reserve team.

    Three rules shape the draw:

      - A team fills both its seats 60% of the time, one 30%, neither 10%. Weighted rather
        than flat so that a division does not come out looking abandoned.
      - A division takes reserves only once every one of its teams is full. A league does
        not carry a reserve while it still has an empty seat to offer.
      - The first division is always filled completely and always takes at least one
        reserve, so every run exercises a full grid and the reserve path regardless of
        how the other divisions fall.
    """
    sizes = []
    for index, division in enumerate(divisions):
        guaranteed = index == 0

        if guaranteed:
            counts = [MAX_DRIVERS_PER_TEAM] * len(teams)
        else:
            counts = rng.choices(TEAM_SIZES, weights=TEAM_SIZE_WEIGHTS, k=len(teams))

        for team, count in zip(teams, counts):
            sizes.append((division, team, count))

        if guaranteed:
            reserves = rng.randint(MIN_GUARANTEED_RESERVE_DRIVERS, MAX_RESERVE_DRIVERS)
        elif all(count == MAX_DRIVERS_PER_TEAM for count in counts):
            reserves = rng.randint(MIN_RESERVE_DRIVERS, MAX_RESERVE_DRIVERS)
        else:
            reserves = 0

        sizes.append((division, RESERVE_TEAM, reserves))
    return sizes


def build_roster(divisions, teams, rng, pool, nationalities):
    """Generate the roster as a list of (driver_id, name, team, division, nationality) rows.

    Team sizes are drawn for the whole run before any name is assigned, so an
    over-large run fails before anything is written rather than part-way through.

    Names are drawn without replacement and nationalities with it. A driver name must be
    unique — it is how a maintainer tells two mock drivers apart — but a grid on which two
    drivers share a nationality is what a real one looks like, and no pool of nationalities
    is large enough to fill a big grid uniquely anyway.

    Nationalities are also drawn with weights rather than uniformly, so the grid comes out
    looking like a league's rather than like a draw from an atlas. See
    build_nationality_weights.
    """
    sizes = plan_team_sizes(divisions, teams, rng)
    total = sum(count for _, _, count in sizes)

    if total > len(pool):
        raise ValueError(
            f"This roster needs {total} unique driver names but only {len(pool)} "
            f"are available. Reduce the number of divisions or teams, or add more "
            f"names to {NAMES_PATH.name}."
        )

    name_iter = iter(rng.sample(pool, total))
    nationality_iter = iter(
        rng.choices(nationalities, weights=build_nationality_weights(nationalities), k=total)
    )

    roster = []
    driver_id = FIRST_DRIVER_ID
    for division, team, count in sizes:
        for _ in range(count):
            roster.append(
                (driver_id, next(name_iter), team, division, next(nationality_iter))
            )
            driver_id += 1
    return roster


def format_command(row):
    """Render one roster row as its /test-mode roster add command."""
    _, name, team, division, nationality = row
    return COMMAND_TEMPLATE.format(
        name=name, team=team, division=division, nationality=nationality
    )


# ─── Output ──────────────────────────────────────────────────────────────────

def confirm_overwrite(read_line=input):
    """Ask whether the existing CSV may be replaced. Only an explicit yes passes.

    One question covers both recorded files: they describe the same roster and are
    written together, so consenting to one is consenting to the other.
    """
    print(f"\n{CSV_PATH} already exists.")
    print(f"Overwriting it will move the current file to {CSV_BACKUP_PATH.name}, ")
    print(f"and replace {TEAMS_PATH.name} alongside it.")
    answer = read_line("Overwrite? [y/N]: ")
    return answer.strip().casefold() in ("y", "yes")


def write_csv(roster):
    """Back up any existing CSV, then write the roster to CSV_PATH."""
    if CSV_PATH.exists():
        if CSV_BACKUP_PATH.exists():
            CSV_BACKUP_PATH.unlink()
        CSV_PATH.rename(CSV_BACKUP_PATH)
        print(f"Existing roster moved to {CSV_BACKUP_PATH}")

    with CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADERS)
        writer.writerows(roster)


def write_teams(teams):
    """Write the team names to TEAMS_PATH, one per line, in input order.

    The CSV cannot answer "which teams exist" on its own: it lists drivers, so a team
    whose two seats both came out empty leaves no row behind. Those are precisely the
    teams the bot ranks first when it hands out reserves, so a sibling that distributes
    them needs the list itself rather than the names it can infer from the drivers.

    The reserve team is left out. It is added to every division by the bot rather than
    named here, and it is never a candidate to receive a reserve.
    """
    TEAMS_PATH.write_text("\n".join(teams) + "\n", encoding="utf-8")


def write_commands(roster):
    """Write commands.txt and print the same lines to the console."""
    lines = [format_command(row) for row in roster]
    COMMANDS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines:
        print(line)


# ─── Entry point ─────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate random /test-mode roster add commands."
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help=f"Also persist the generated roster to {CSV_PATH}.",
    )
    args = parser.parse_args(argv)

    # Load both pools up front, so a missing names.txt or an unreachable src/ fails
    # before the questions rather than after them.
    try:
        pool = load_names()
        nationalities = load_nationalities()
    except ValueError as error:
        print(error)
        return 1

    divisions = prompt_entries(
        "Divisions to generate rosters for (comma-separated): ", "division"
    )
    teams = prompt_entries(
        f"Teams to generate rosters for (comma-separated, or {DEFAULT_TEAMS_TOKEN} "
        "for the current grid): ",
        "team",
        reject_reserve=True,
        expand_default=True,
    )

    try:
        roster = build_roster(divisions, teams, random.Random(), pool, nationalities)
    except ValueError as error:
        print(f"\n{error}")
        return 1

    # Settle the CSV before writing anything, so a refused overwrite leaves both
    # commands.txt and the CSV exactly as they were.
    if args.record and CSV_PATH.exists() and not confirm_overwrite():
        print("Aborted - nothing was written.")
        return 1

    print()
    write_commands(roster)

    if args.record:
        write_csv(roster)
        write_teams(teams)

    print(
        f"\n{len(roster)} drivers across {len(divisions)} division(s) "
        f"and {len(teams) + 1} team(s) including {RESERVE_TEAM}."
    )
    print(f"Commands written to {COMMANDS_PATH}")
    if args.record:
        print(f"Roster written to {CSV_PATH}")
        print(f"Teams written to {TEAMS_PATH}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (EOFError, KeyboardInterrupt):
        # Ctrl+C, Ctrl+Z, or piped input that ran out mid-question.
        print("\nAborted - nothing was written.")
        sys.exit(1)
