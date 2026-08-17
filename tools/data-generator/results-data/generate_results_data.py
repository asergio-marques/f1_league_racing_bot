# -*- coding: utf-8 -*-
"""Random results generator — emits the blocks the round-results wizard expects.

Drivers are read from roster.csv in the parent directory and the teams they could be
driving for from teams.txt beside it, both written by the roster generator. Who turned
up is read from the check-in files written by the check-in generator, so a round's
results follow from the round's RSVPs rather than being invented afresh.

Interactively asks which divisions to cover and whether the round is a normal or a
sprint one, then writes one file per session beside this script:

  normal round                            sprint round
  <slug>_<stamp>_1_feature_quali.txt      <slug>_<stamp>_1_sprint_quali.txt
  <slug>_<stamp>_2_feature_race.txt       <slug>_<stamp>_2_sprint_race.txt
                                          <slug>_<stamp>_3_feature_quali.txt
                                          <slug>_<stamp>_4_feature_race.txt

The stamp is the moment the run started, as 20260817_091538, and every file the run writes
carries the same one - so a run's output groups together and successive runs sit side by
side rather than overwriting one another.

Every file is a block of "Position, <@ID>, @Team, ..." lines, one driver per line, ready
to paste into the results channel the bot opens after a round. The reserve drivers are
placed into teams by the same algorithm the bot uses at the RSVP deadline, so the teams
they are listed under are the teams the bot expects them to be driving for.

Nothing here touches the database or needs a running bot.

Usage:
  python tools/data-generator/results-data/generate_results_data.py
"""

from __future__ import annotations

import csv
import datetime
import pathlib
import random
import re
import sys
import unicodedata

# ─── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = pathlib.Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent
CHECK_IN_DIR = DATA_DIR / "check-in-data"

CSV_PATH = DATA_DIR / "roster.csv"
TEAMS_PATH = DATA_DIR / "teams.txt"

INITIAL_TEMPLATE = "{slug}_initial.txt"
CHANGED_TEMPLATE = "{slug}_changed.txt"
RESULTS_TEMPLATE = "{slug}_{timestamp}_{index}_{stage}_{session}.txt"

#: The run's stamp, drawn once and shared by every file it writes so that one run's output
#: groups together. Local time rather than UTC: it is read off a wall clock to tell one run
#: from another, never by the bot.
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"

ROSTER_COMMAND = "python tools/data-generator/test-roster/generate_test_roster.py --record"
CHECK_IN_COMMAND = "python tools/data-generator/check-in-data/generate_check_in_data.py"

# ─── Roster columns ──────────────────────────────────────────────────────────

ID_COLUMN = "ID"
NAME_COLUMN = "Driver name"
TEAM_COLUMN = "Team"
DIVISION_COLUMN = "Division"

#: A reserve driver is one whose team is exactly this - there is no column for it. The
#: match is exact rather than a prefix, because a team merely called "Reserve Racing" is
#: a normal team with normal seats.
RESERVE_TEAM = "Reserve"

#: Typed in place of the division list to stand for every division the roster holds. The
#: two sibling generators spell their own token the same way, for the same reason.
DEFAULT_DIVISIONS_TOKEN = "<<<DEFAULT>>>"

# ─── Check-in statuses ───────────────────────────────────────────────────────

#: The three tokens the check-in files carry, mapped to the names the bot stores. A
#: driver in neither file never answered, which the files have no token for.
STATUS_TOKENS = {"accept": "ACCEPTED", "tentative": "TENTATIVE", "decline": "DECLINED"}

ACCEPTED = "ACCEPTED"
TENTATIVE = "TENTATIVE"
DECLINED = "DECLINED"
NO_RSVP = "NO_RSVP"

# ─── Round shape ─────────────────────────────────────────────────────────────

#: The sessions of each round type, in the order the bot asks for them. A normal round
#: runs the feature weekend alone; a sprint round runs a whole sprint weekend first.
NORMAL_SESSIONS = [("feature", "quali"), ("feature", "race")]
SPRINT_SESSIONS = [
    ("sprint", "quali"), ("sprint", "race"),
    ("feature", "quali"), ("feature", "race"),
]

#: What the round-type question accepts, either spelt out or by its first letter.
ROUND_TYPES = {"normal": NORMAL_SESSIONS, "n": NORMAL_SESSIONS,
               "sprint": SPRINT_SESSIONS, "s": SPRINT_SESSIONS}

# ─── Reserve distribution ────────────────────────────────────────────────────

#: Seats per team. Mirrors the max_seats default in 008_driver_profiles_teams.sql, which
#: is what team_service.py creates a team instance with.
MAX_SEATS = 2

#: Every team is unranked here: an offline generator has no constructors' standings to
#: read. The bot sorts teams with no standings snapshot after every ranked team, so with
#: none of them ranked the tie-break falls through to team name in every case - which is
#: also what the bot does before the first round of a season.
UNRANKED_SORT_POSITION = 0

# ─── Attendance rules ────────────────────────────────────────────────────────

#: How often a driver who said "tentative" actually turns up. An even draw: tentative is
#: exactly the answer that tells a league nothing, and both readings of it are worth
#: generating.
TENTATIVE_PRESENT_CHANCE = 0.50

#: The share of drivers who accepted and then failed to appear, as a ceiling on the draw.
#: Nought is deliberate and common - a round where everyone who accepted shows up is the
#: ordinary case, and the no-show is the one a league notices.
MAX_NO_SHOW_SHARE = 0.10

# ─── Session rules ───────────────────────────────────────────────────────────

#: Non-finishers per session, weighted so that most sessions have none at all. A session
#: full of retirements is not a session worth generating every time; one that has a
#: couple now and again is what the results table has to cope with.
DNF_COUNTS = [0, 1, 2]
DNF_WEIGHTS = [60, 30, 10]
DSQ_COUNTS = [0, 1]
DSQ_WEIGHTS = [85, 15]
DNS_COUNTS = [0, 1]
DNS_WEIGHTS = [85, 15]

#: Drivers lapped by the leader. Race only - a qualifying session cannot lap anybody.
LAPPED_COUNTS = [0, 1, 2, 3]
LAPPED_WEIGHTS = [55, 25, 15, 5]

#: A retirement can still hold the lap it set before it stopped, and the bot skips
#: fastest-lap validation entirely for a non-finisher, so both readings are valid input.
DNF_FASTEST_LAP_CHANCE = 0.60

#: Whether a session is run in the wet. Tyres are drawn from the matching set, so a
#: session comes out on one family of compounds rather than a scatter of all five.
WET_SESSION_CHANCE = 0.20
DRY_TYRES = ["Soft", "Medium", "Hard"]
DRY_TYRE_WEIGHTS = [70, 25, 5]
WET_TYRES = ["Intermediates", "Wet"]
WET_TYRE_WEIGHTS = [70, 30]

#: Tyre, best lap and gap all read N/A for an entry that set no time.
NOT_AVAILABLE = "N/A"

#: The leader's qualifying lap, and the gap from one car to the next behind it. Roughly a
#: minute and a quarter, covered by a couple of tenths a car, which is what a grid of
#: twenty-odd looks like on the timing screen.
QUALI_LEADER_MS = (65_000, 95_000)
QUALI_STEP_MS = (10, 900)

#: The winner's race time, and the gap from one car to the next behind it.
RACE_LEADER_MS = (1_500_000, 3_300_000)
RACE_STEP_MS = (500, 8_000)

#: A race lap is slower than a qualifying one - no low fuel, no fresh set of softs.
RACE_LAP_MS = (70_000, 100_000)
RACE_LAP_SPREAD_MS = 4_000

#: In-game time penalties are handed out in whole seconds and only in these sizes, and
#: most drivers finish a race without one.
PENALTY_SECONDS = [0, 3, 5, 10]
PENALTY_WEIGHTS = [70, 12, 12, 6]

QUALI_LINE_TEMPLATE = "{position}, <@{driver_id}>, @{team}, {tyre}, {best_lap}, {gap}"
RACE_LINE_TEMPLATE = (
    "{position}, <@{driver_id}>, @{team}, {total_time}, {fastest_lap}, {penalties}"
)


# ─── Roster ──────────────────────────────────────────────────────────────────

def load_roster(path=CSV_PATH):
    """Read the roster CSV into a list of (id, name, team, division) rows.

    File order is preserved throughout, so every generated file can be read down
    alongside the CSV.
    """
    if not path.exists():
        raise ValueError(
            f"Roster not found at {path}. Generate one first with:\n  {ROSTER_COMMAND}"
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


def load_teams(path=TEAMS_PATH):
    """Read the team names from *path*, one per line, in file order.

    This cannot be inferred from the roster: a team whose seats both came out empty has
    no driver row to be found by, and those are exactly the teams the bot puts first in
    line for a reserve.
    """
    if not path.exists():
        raise ValueError(
            f"Team list not found at {path}. It is written alongside the roster:\n"
            f"  {ROSTER_COMMAND}"
        )

    teams = []
    seen = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        team = line.strip()
        if not team:
            continue
        key = team.casefold()
        if key in seen:
            continue
        seen.add(key)
        teams.append(team)

    if not teams:
        raise ValueError(f"Team list at {path} holds no teams.")

    return teams


def check_roster_teams(roster, teams):
    """Raise if the roster seats a driver in a team the team list does not hold.

    The two files are written by one run of the roster generator and describe the same
    league. A driver seated in a team nobody has heard of means they have fallen out of
    step, and a reserve distribution computed from a partial team list would be wrong in
    a way nothing downstream could notice.
    """
    known = {team.casefold() for team in teams}
    known.add(RESERVE_TEAM.casefold())
    unknown = []
    for _, _, team, _ in roster:
        if team.casefold() not in known and team not in unknown:
            unknown.append(team)

    if unknown:
        raise ValueError(
            f"{CSV_PATH.name} seats drivers in team(s) absent from {TEAMS_PATH.name}: "
            f"{', '.join(unknown)}. The two files are written together and have fallen "
            f"out of step - regenerate them with:\n  {ROSTER_COMMAND}"
        )


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

    Always called with the roster's full division list rather than the answer given at
    the prompt, because these slugs also name the check-in files this script has to
    *read*: derived from a subset they could disagree with the ones already on disk.
    """
    slugs = {}
    used = set()
    for index, division in enumerate(divisions, start=1):
        base = slugify(division) or f"division_{index}"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        slugs[division.casefold()] = candidate
    return slugs


# ─── Check-in data ───────────────────────────────────────────────────────────

def check_in_paths(slug):
    """The initial and changed check-in files for a division, in that order."""
    return (
        CHECK_IN_DIR / INITIAL_TEMPLATE.format(slug=slug),
        CHECK_IN_DIR / CHANGED_TEMPLATE.format(slug=slug),
    )


def read_check_in(path):
    """Read one check-in file as a list of (driver_id, status) in file order.

    File order is the order the block was pasted into the modal and therefore the order
    the bot stamped its acceptance times in, which is what decides who gets a seat when
    reserves are handed out.
    """
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",", 1)]
        if len(parts) != 2:
            raise ValueError(f"{path.name} line {number}: expected 'ID, status' - got '{line}'.")
        raw_id, token = parts
        try:
            driver_id = int(raw_id)
        except ValueError:
            raise ValueError(f"{path.name} line {number}: '{raw_id}' is not a numeric driver ID.")
        status = STATUS_TOKENS.get(token.casefold())
        if status is None:
            raise ValueError(
                f"{path.name} line {number}: '{token}' is not one of "
                f"{', '.join(STATUS_TOKENS)}."
            )
        entries.append((driver_id, status))
    return entries


def effective_statuses(drivers, initial, changed):
    """Return {driver_id: status} for the division, the revisions applied over the first
    check-in.

    A driver in neither file never answered. The files have no token for that - omission
    is how it is said - so NO_RSVP is what is left when nothing else was.
    """
    statuses = {row[0]: NO_RSVP for row in drivers}
    for driver_id, status in initial:
        if driver_id in statuses:
            statuses[driver_id] = status
    for driver_id, status in changed:
        if driver_id in statuses:
            statuses[driver_id] = status
    return statuses


def acceptance_order(drivers, statuses, initial, changed):
    """Return the accepted reserves' IDs in the order the bot stamped them accepted.

    The bot orders reserves by accepted_at and re-stamps it every time a driver accepts
    afresh, so a reserve whose acceptance arrives in the revisions is stamped after every
    reserve who accepted in the first check-in. Within one file the order is the order
    the lines were pasted in. Between them, the initial block was pasted first.
    """
    initial_index = {driver_id: index for index, (driver_id, _) in enumerate(initial)}
    changed_index = {driver_id: index for index, (driver_id, _) in enumerate(changed)}

    def stamped(driver_id):
        if driver_id in changed_index:
            return (1, changed_index[driver_id])
        return (0, initial_index.get(driver_id, 0))

    accepted = [
        row[0] for row in drivers
        if is_reserve(row) and statuses[row[0]] == ACCEPTED
    ]
    return sorted(accepted, key=stamped)


# ─── Input parsing ───────────────────────────────────────────────────────────

def split_entries(raw):
    """Split a comma-separated response into entries.

    Only a single space immediately following a comma is stripped - the same rule the
    two sibling generators apply, so a division typed there is typed identically here.
    """
    entries = []
    for index, part in enumerate(raw.split(",")):
        if index > 0 and part.startswith(" "):
            part = part[1:]
        entries.append(part)
    return entries


def expand_default_divisions(entries, known):
    """Replace any DEFAULT_DIVISIONS_TOKEN entry with every division the roster holds.

    Expanding before validation rather than after means the token is simply a way of
    typing the names out. It already covers every division, so naming one alongside it is
    a duplicate and is refused as one.
    """
    expanded = []
    for entry in entries:
        if entry.strip().casefold() == DEFAULT_DIVISIONS_TOKEN.casefold():
            expanded.extend(known)
        else:
            expanded.append(entry)
    return expanded


def validate_divisions(entries, known, slugs):
    """Return an error message describing why *entries* is unusable, or None.

    A division with no check-in files is refused alongside one absent from the roster: a
    round's results follow from its RSVPs, and there is nothing to follow from.
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

    for entry in entries:
        for path in check_in_paths(slugs[entry.casefold()]):
            if not path.exists():
                return (
                    f"No check-in data for '{entry}' - {path.name} is missing. "
                    f"Generate it first with:\n    {CHECK_IN_COMMAND}"
                )

    return None


def prompt_divisions(known, slugs, read_line=input):
    """Ask which divisions to cover, re-prompting until every name can be generated for.

    The roster's own spelling is returned rather than what was typed, so the summary and
    the filenames read consistently however the name was capitalised.
    """
    canonical = {name.casefold(): name for name in known}
    while True:
        raw = read_line(
            f"Divisions to generate results data for (comma-separated, or "
            f"{DEFAULT_DIVISIONS_TOKEN} for all of them): "
        )
        entries = expand_default_divisions(split_entries(raw), known)
        error = validate_divisions(entries, known, slugs)
        if error is None:
            return [canonical[entry.casefold()] for entry in entries]
        print(f"  {error}\n")


def prompt_round_type(read_line=input):
    """Ask whether the round is a normal or a sprint one, returning its session list."""
    while True:
        raw = read_line("Round type - [n]ormal or [s]print: ")
        sessions = ROUND_TYPES.get(raw.strip().casefold())
        if sessions is not None:
            return sessions
        print("  Enter 'normal' or 'sprint'.\n")


# ─── Reserve distribution ────────────────────────────────────────────────────

def build_team_states(teams, drivers, statuses):
    """Describe every team in the division as the reserve distribution sees it.

    One entry per team in the team list, whether or not the roster seats anybody in it:
    a team with both seats empty is the first in line for a reserve, and dropping it here
    would be dropping the case the tiering cares most about.
    """
    states = []
    for team in teams:
        seated = [
            row for row in drivers
            if not is_reserve(row) and row[2].casefold() == team.casefold()
        ]
        counted = [statuses[row[0]] for row in seated]
        states.append({
            "name": team,
            "max_seats": MAX_SEATS,
            "total_drivers": len(seated),
            "accepted": counted.count(ACCEPTED),
            "declined": counted.count(DECLINED),
            "tentative": counted.count(TENTATIVE),
            "no_rsvp": counted.count(NO_RSVP),
        })
    return states


def static_tier(state):
    """Tier a team by its RSVP and seat state alone.

    Transcribed from _static_tier in run_reserve_distribution
    (src/services/rsvp_service.py), which is itself the priority list in the
    "Distribution of reserves" section of the attendance module specification. Tier 5 is
    absent by design: it is not a state a team is in, it is where a team is demoted to
    once it has received a reserve.
    """
    if state["total_drivers"] == 0:
        return 1  # all seats physically vacant
    elif state["declined"] > 0:
        return 2
    elif state["no_rsvp"] > 0:
        return 3
    elif state["total_drivers"] < state["max_seats"]:
        return 4  # at least one seat physically vacant (partial)
    elif state["tentative"] > 0:
        return 6
    else:
        return 99  # all accepted and fully staffed — excluded


def distribute_reserves(team_states, accepted_reserve_ids):
    """Place accepted reserves into teams, returning ({driver_id: team}, [standby ids]).

    A port of run_reserve_distribution in src/services/rsvp_service.py: the same tiers,
    the same demotion to tier 5 once a team holds a reserve so that no team takes a
    second while another still needs its first, and the same re-sort before every single
    placement. What the bot reads from the database is counted off the roster here
    instead, and the constructors'-standings tie-break falls away because there are no
    standings offline - see UNRANKED_SORT_POSITION.

    A reserve left over when every vacancy is filled is on standby, exactly as in the
    bot. They are given no team, and a driver with no team cannot appear in a result.
    """
    candidates = [state for state in team_states if static_tier(state) < 99]

    # Vacancy = no-rsvp + declined + tentative + physically empty seats. An accepted
    # driver's seat is filled and is never counted.
    vacancy = {
        state["name"]: (
            state["no_rsvp"] + state["declined"] + state["tentative"]
            + max(0, state["max_seats"] - state["total_drivers"])
        )
        for state in candidates
    }
    placed_count = {state["name"]: 0 for state in candidates}

    def sort_key(state):
        name = state["name"]
        tier = static_tier(state)
        # Demoted once a reserve has been placed, but never below tier 6, so a team that
        # only had a tentative driver does not leap ahead of one that still has.
        if placed_count[name] >= 1:
            tier = max(5, tier)
        return (tier, UNRANKED_SORT_POSITION, name.casefold())

    allocation = {}
    standby = []
    for driver_id in accepted_reserve_ids:
        eligible = [state for state in candidates if vacancy[state["name"]] > 0]
        if not eligible:
            standby.append(driver_id)
            continue
        eligible.sort(key=sort_key)
        team = eligible[0]["name"]
        allocation[driver_id] = team
        vacancy[team] -= 1
        placed_count[team] += 1

    return allocation, standby


# ─── Who appears in the results ──────────────────────────────────────────────

def choose_attendees(drivers, statuses, allocation, rng):
    """Return {driver_id: team} for every driver appearing in the round's results.

    Three rules shape it, applied in that order:

      - A driver who accepted is there and one who declined or never answered is not. A
        tentative driver is drawn either way, that answer being exactly the one that
        tells a league nothing.
      - A team never fields more than its two cars. Where a reserve was placed against a
        tentative driver's seat and that driver was then drawn in, the tentative driver
        stands down: taking that seat is why the reserve is there at all. It always fits,
        because a team's accepted drivers plus its vacancies come to its seat count.
      - Nought to a tenth of those who accepted fail to appear after all, and miss every
        session of the round rather than some of them.

    A reserve appears only if the distribution gave them a team. One left on standby has
    none, and the bot refuses a result that lists a driver under the reserve team.
    """
    attending = {}
    for row in drivers:
        driver_id, _, team, _ = row
        if is_reserve(row):
            if driver_id in allocation:
                attending[driver_id] = allocation[driver_id]
        elif statuses[driver_id] == ACCEPTED:
            attending[driver_id] = team
        elif statuses[driver_id] == TENTATIVE and rng.random() < TENTATIVE_PRESENT_CHANCE:
            attending[driver_id] = team

    for team in {team for team in attending.values()}:
        fielded = [driver_id for driver_id, name in attending.items() if name == team]
        standing_down = [
            driver_id for driver_id in fielded if statuses[driver_id] == TENTATIVE
        ]
        rng.shuffle(standing_down)
        while len(fielded) > MAX_SEATS and standing_down:
            dropped = standing_down.pop()
            fielded.remove(dropped)
            del attending[dropped]

    accepted = [
        driver_id for driver_id in attending if statuses[driver_id] == ACCEPTED
    ]
    no_shows = rng.randint(0, int(len(accepted) * MAX_NO_SHOW_SHARE))
    for driver_id in rng.sample(accepted, no_shows):
        del attending[driver_id]

    return attending


# ─── Time formatting ─────────────────────────────────────────────────────────

def format_time(milliseconds):
    """Render a duration as the bot's absolute time format, "M:SS.mmm".

    Minutes are carried rather than left to overflow the seconds field, so an hour-long
    race time comes out as 62:14.100 rather than something no parser would take.
    """
    minutes, rest = divmod(milliseconds, 60_000)
    seconds, millis = divmod(rest, 1_000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def format_gap(milliseconds):
    """Render a gap to the leader as the bot's delta format, "+S.mmm" or "+M:SS.mmm".

    Minutes are carried once the gap passes one, so a driver a minute and a half back
    reads "+1:30.000" rather than "+90.000". Both parse, but only one is what a timing
    screen shows, and a race spreads the field far enough for it to matter.
    """
    minutes, rest = divmod(milliseconds, 60_000)
    seconds, millis = divmod(rest, 1_000)
    if minutes:
        return f"+{minutes}:{seconds:02d}.{millis:03d}"
    return f"+{seconds}.{millis:03d}"


def format_penalty(seconds):
    """Render a time penalty in the seconds-and-thousandths form the bot parses."""
    return f"{seconds}.000"


def format_lap_gap(laps):
    """Render a lapped driver's deficit, singular for the first lap down."""
    return f"+{laps} Lap" if laps == 1 else f"+{laps} Laps"


def cumulative_times(count, leader_range, step_range, rng):
    """Draw *count* increasing times: a leader, then each car's gap to the one ahead."""
    if count == 0:
        return []
    times = [rng.randint(*leader_range)]
    for _ in range(count - 1):
        times.append(times[-1] + rng.randint(*step_range))
    return times


def draw_outcomes(driver_ids, counts_and_weights, rng):
    """Split a shuffled field into the non-finishing groups and everyone else.

    Returns (remaining, groups) with the groups in the order they were asked for. The
    draw is capped so that at least one driver is left classified: a session in which
    nobody finished is not a session, and the bot rejects a block with no lead-lap entry.
    """
    available = len(driver_ids) - 1
    groups = []
    end = len(driver_ids)
    for counts, weights in counts_and_weights:
        room = max(0, available - (len(driver_ids) - end))
        drawn = min(rng.choices(counts, weights=weights, k=1)[0], room)
        groups.append(driver_ids[end - drawn: end])
        end -= drawn
    return driver_ids[:end], groups


# ─── Session generation ──────────────────────────────────────────────────────

def generate_qualifying(attending, rng):
    """Return the qualifying rows in finishing order, as formatted field tuples.

    Gaps are derived from the lap times rather than drawn beside them, so the column
    cannot contradict the one it is a gap in. The session runs on one family of tyre
    compounds, and an entry that set no time carries N/A in all three of its columns.

    The order is classified, then DNF, then DSQ - the order the bot's own validator
    insists on for a qualifying block.
    """
    driver_ids = list(attending)
    rng.shuffle(driver_ids)

    classified, (dnfs, dsqs) = draw_outcomes(
        driver_ids, [(DNF_COUNTS, DNF_WEIGHTS), (DSQ_COUNTS, DSQ_WEIGHTS)], rng
    )

    tyres, weights = (
        (WET_TYRES, WET_TYRE_WEIGHTS) if rng.random() < WET_SESSION_CHANCE
        else (DRY_TYRES, DRY_TYRE_WEIGHTS)
    )
    laps = cumulative_times(len(classified), QUALI_LEADER_MS, QUALI_STEP_MS, rng)

    rows = []
    for index, (driver_id, lap) in enumerate(zip(classified, laps)):
        rows.append((
            driver_id,
            attending[driver_id],
            rng.choices(tyres, weights=weights, k=1)[0],
            format_time(lap),
            NOT_AVAILABLE if index == 0 else format_gap(lap - laps[0]),
        ))
    for driver_id in dnfs:
        rows.append((driver_id, attending[driver_id], NOT_AVAILABLE, "DNF", NOT_AVAILABLE))
    for driver_id in dsqs:
        rows.append((driver_id, attending[driver_id], NOT_AVAILABLE, "DSQ", NOT_AVAILABLE))
    return rows


def generate_race(attending, rng):
    """Return the race rows in finishing order, as formatted field tuples.

    The winner carries an absolute race time and every other finisher a gap to it, which
    is what the bot expects: it resolves a classification by taking the first absolute
    entry as its reference and adding each delta back onto it. Only the leader may be
    absolute for that to mean anything.

    The order is the one the bot's validator insists on - lead-lap finishers, then
    lapped drivers, then DNF, DNS and DSQ - and lap counts only ever grow down the
    order. A retirement may still carry the lap it set before it stopped; a driver who
    never started or was disqualified carries nothing.
    """
    driver_ids = list(attending)
    rng.shuffle(driver_ids)

    lead_lap, (lapped, dnfs, dns, dsqs) = draw_outcomes(
        driver_ids,
        [
            (LAPPED_COUNTS, LAPPED_WEIGHTS),
            (DNF_COUNTS, DNF_WEIGHTS),
            (DNS_COUNTS, DNS_WEIGHTS),
            (DSQ_COUNTS, DSQ_WEIGHTS),
        ],
        rng,
    )

    totals = cumulative_times(len(lead_lap), RACE_LEADER_MS, RACE_STEP_MS, rng)
    session_lap = rng.randint(*RACE_LAP_MS)

    def lap_time():
        return format_time(session_lap + rng.randint(0, RACE_LAP_SPREAD_MS))

    def penalty():
        return format_penalty(rng.choices(PENALTY_SECONDS, weights=PENALTY_WEIGHTS, k=1)[0])

    rows = []
    for index, (driver_id, total) in enumerate(zip(lead_lap, totals)):
        # The winner's race time is absolute and everyone else's is a gap to it, which is
        # both how a classification is read and how the bot reconstructs the times: it
        # takes the first absolute entry as the reference and adds each delta back onto it.
        shown = format_time(total) if index == 0 else format_gap(total - totals[0])
        rows.append((driver_id, attending[driver_id], shown, lap_time(), penalty()))

    laps_down = 1
    for driver_id in lapped:
        rows.append((
            driver_id, attending[driver_id], format_lap_gap(laps_down), lap_time(), penalty(),
        ))
        laps_down += rng.choices([0, 1], weights=[60, 40], k=1)[0]

    for driver_id in dnfs:
        fastest = lap_time() if rng.random() < DNF_FASTEST_LAP_CHANCE else NOT_AVAILABLE
        rows.append((driver_id, attending[driver_id], "DNF", fastest, NOT_AVAILABLE))
    for driver_id in dns:
        rows.append((driver_id, attending[driver_id], "DNS", NOT_AVAILABLE, NOT_AVAILABLE))
    for driver_id in dsqs:
        rows.append((driver_id, attending[driver_id], "DSQ", NOT_AVAILABLE, NOT_AVAILABLE))
    return rows


def format_lines(rows, session):
    """Render session rows as submission lines, numbered from 1 down the order."""
    template = QUALI_LINE_TEMPLATE if session == "quali" else RACE_LINE_TEMPLATE
    keys = (
        ("tyre", "best_lap", "gap") if session == "quali"
        else ("total_time", "fastest_lap", "penalties")
    )
    return [
        template.format(
            position=position,
            driver_id=row[0],
            team=row[1],
            **dict(zip(keys, row[2:])),
        )
        for position, row in enumerate(rows, start=1)
    ]


# ─── Output ──────────────────────────────────────────────────────────────────

def write_lines(path, lines):
    """Write the lines to *path*, replacing whatever was there.

    These files are disposable per-run output rather than a shared contract like
    roster.csv, so nothing is asked and nothing is backed up. In practice there is nothing
    to replace: the run's stamp is in the name, so a run only collides with another
    started in the same second.
    """
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def report(division, drivers, attending, allocation, standby, paths):
    """Print what a division came out as, and where its session files went."""
    reserves = [
        f"{driver_id} to {team}" for driver_id, team in sorted(allocation.items())
    ]
    print(
        f"{division}: {len(attending)} of {len(drivers)} in the results; "
        f"{len(allocation)} reserve(s) placed, {len(standby)} on standby."
    )
    if reserves:
        print(f"  reserves: {', '.join(reserves)}")
    for path in paths:
        print(f"  {path}")


# ─── Entry point ─────────────────────────────────────────────────────────────

def main(argv=None):
    # Load both shared files up front, so a missing or malformed one fails before the
    # questions rather than after them.
    try:
        roster = load_roster()
        teams = load_teams()
        check_roster_teams(roster, teams)
    except ValueError as error:
        print(error)
        return 1

    known = division_names(roster)
    slugs = build_slugs(known)

    divisions = prompt_divisions(known, slugs)
    sessions = prompt_round_type()
    rng = random.Random()

    # Drawn once, after the questions are answered, so that every file this run writes
    # carries the same stamp however long the run takes.
    timestamp = datetime.datetime.now().strftime(TIMESTAMP_FORMAT)

    print()
    for division in divisions:
        slug = slugs[division.casefold()]
        drivers = drivers_in(roster, division)

        initial_path, changed_path = check_in_paths(slug)
        try:
            initial = read_check_in(initial_path)
            changed = read_check_in(changed_path)
        except ValueError as error:
            print(f"{division}: {error}")
            return 1

        statuses = effective_statuses(drivers, initial, changed)
        allocation, standby = distribute_reserves(
            build_team_states(teams, drivers, statuses),
            acceptance_order(drivers, statuses, initial, changed),
        )
        attending = choose_attendees(drivers, statuses, allocation, rng)

        if not attending:
            print(
                f"{division}: nobody is in the results - every driver declined, never "
                f"answered or failed to appear. Nothing written."
            )
            continue

        # One draw of who is racing covers the whole round: the bot records a driver
        # under one team across every session of it, and a reserve who stood in for a
        # team in qualifying stands in for it in the race.
        paths = []
        for index, (stage, session) in enumerate(sessions, start=1):
            generate = generate_qualifying if session == "quali" else generate_race
            path = SCRIPT_DIR / RESULTS_TEMPLATE.format(
                slug=slug, timestamp=timestamp, index=index, stage=stage, session=session
            )
            write_lines(path, format_lines(generate(attending, rng), session))
            paths.append(path)

        report(division, drivers, attending, allocation, standby, paths)

    print(
        f"\nThis run is stamped {timestamp}. For each division, paste each session's file "
        f"into the results channel the bot opens after the round, in the order they are "
        f"numbered. Earlier runs are left where they are - delete them when done with."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (EOFError, KeyboardInterrupt):
        # Ctrl+C, Ctrl+Z, or piped input that ran out mid-question.
        print("\nAborted - nothing was written.")
        sys.exit(1)
