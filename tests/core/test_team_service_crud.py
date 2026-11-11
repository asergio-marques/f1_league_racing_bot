"""`TeamService` — the server's team list, and the division teams seeded from it.

Issue #208. `tests/core/test_team_name_validation.py` covers `validate_team_name`, the module
function; `TeamService` itself — the class every `/team` command reaches — was almost entirely
unexecuted, 125 of its 177 statements. Teams are the thing seats hang off, so a fault here
reaches the lineup graphic, the signup wizard's team preferences and the reserve distribution
alike.

Three rules are pinned here, each of which a reader could undo without any other test noticing.

**The Reserve team is protected, and a new database already holds it.** The baseline migration
seeds its row, so the list ships holding the Reserve team alone and every read of the list only
reads (`test_a_new_server_s_team_list_is_the_reserve_team_alone`,
`test_reading_the_team_list_writes_nothing`). No `/team` command may add, rename or remove it.

**Only the new names are validated when a team is changed.** Deliberately, per the method's own
comment: a team named before the naming rule existed would otherwise be impossible to rename *or*
to remove, because validating the current name would refuse the very command that fixes it.
`test_a_team_whose_existing_name_breaks_the_rule_can_still_be_changed` sits on that, and it is
the test most likely to be broken by someone "tightening" the validation.

**Uniqueness is the server list's.** A division's teams are copied from that list when it is
seeded and are never named on their own, so the list is the one place a collision can be
refused. That includes two shorthands whose normalised forms collide, which would otherwise
seek the same artwork file (`test_a_name_colliding_once_normalised_is_refused`).

Everything runs against a real migrated database. What these methods are chiefly at risk of
getting wrong is SQL across four tables, and a double would confirm whatever shape the test
author imagined.
"""
from __future__ import annotations

import os

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.drivers.team_service import TeamService


SERVER_ID = 8708
SEASON_ID = 1

#: The protected name, as the service spells it.
RESERVE = "Reserve"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, season_status: str | None = None, divisions: int = 0) -> str:
    """A migrated DB with one server, and optionally a season holding *divisions*."""
    db_path = os.path.join(str(tmp_path), "teams.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (SERVER_ID,),
        )
        if season_status is not None:
            await db.execute(
                "INSERT INTO seasons (id, season_number, start_date, status) "
                "VALUES (?, 1, '2026-01-01', ?)",
                (SEASON_ID, season_status),
            )
            for index in range(1, divisions + 1):
                await db.execute(
                    "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (index, SEASON_ID, f"Division {index}", index, 500 + index),
                )
        await db.commit()
    return db_path


async def _add_team(
    db_path: str, name: str, *, full_name: str | None = None, max_seats: int = 2
) -> None:
    """Team *name* on the server's list, written straight into it: the Reserve team is the new
    database's own. The tests that are not of the list's writers place their teams here."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO default_teams (name, full_name, max_seats, is_reserve) "
            "VALUES (?, ?, ?, 0)",
            (name, full_name or name, max_seats),
        )
        await db.commit()


async def _write(db_path: str, write):
    """Run one of the list's writers in its `_on` form on a connection of its own and commit it,
    as a `/team` change's save runs it: *write* takes the service and the connection."""
    async with get_connection(db_path) as db:
        result = await write(TeamService(db_path), db)
        await db.commit()
    return result


async def _audit_rows(db_path: str) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT change_type FROM audit_entries")
        return [r["change_type"] for r in await cursor.fetchall()]


async def _names(db_path: str) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM default_teams ORDER BY name"
        )
        return [r["name"] for r in await cursor.fetchall()]


async def _division_names(db_path: str, division_id: int) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM team_instances WHERE division_id = ? ORDER BY name",
            (division_id,),
        )
        return [r["name"] for r in await cursor.fetchall()]


async def _seat_count(db_path: str, division_id: int, name: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM team_seats ts "
            "JOIN team_instances ti ON ti.id = ts.team_instance_id "
            "WHERE ti.division_id = ? AND ti.name = ?",
            (division_id, name),
        )
        return (await cursor.fetchone())["n"]


async def _insert_division_team(
    db_path: str, division_id: int, name: str, *, max_seats: int = 2
) -> None:
    """Write one team, with its seats, straight into a division.

    Seeding copies the server's list in name order, so a division built by seeding cannot tell
    ordering by id from ordering by name. The tests of that ordering place their teams here.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (?, ?, ?, ?, 0)",
            (division_id, name, name, max_seats),
        )
        for seat_number in range(1, max_seats + 1):
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, NULL)",
                (cursor.lastrowid, seat_number),
            )
        await db.commit()


# ---------------------------------------------------------------------------
# The Reserve team
# ---------------------------------------------------------------------------


async def test_a_new_server_s_team_list_is_the_reserve_team_alone(tmp_path):
    """The core specification: the list ships holding the Reserve team alone. A database just
    built by the migrations, before anything has read or written the list, holds one team: the
    Reserve team, its shorthand and full name "Reserve", with no fixed seats (-1). Reading the
    list gives that team alone."""
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        rows = await (
            await db.execute("SELECT name, full_name, max_seats, is_reserve FROM default_teams")
        ).fetchall()
    assert [tuple(row) for row in rows] == [(RESERVE, RESERVE, -1, 1)]

    teams = await TeamService(db_path).get_default_teams()

    assert [t.name for t in teams] == [RESERVE]
    assert teams[0].is_reserve is True
    assert teams[0].full_name == RESERVE


@pytest.mark.parametrize("read", ["get_default_teams", "get_teams_with_roles"])
async def test_reading_the_team_list_writes_nothing(tmp_path, read):
    """Reading the team list commits nothing: every change is made through the change queue,
    and the Reserve team's row is the baseline's. The server's list holds Alpha, and its Reserve
    row has been taken out by hand. Reading the list, as `/team list`, the team autocomplete and
    the signup wizard do, lists Alpha alone and leaves the list as it found it: no Reserve row is
    written back. Takes the place of `test_reading_the_team_list_restores_a_lost_reserve` and
    `test_listing_teams_with_roles_restores_a_lost_reserve`."""
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO default_teams (name, full_name, max_seats, is_reserve) "
            "VALUES ('Alpha', 'Alpha', 2, 0)"
        )
        await db.execute("DELETE FROM default_teams WHERE is_reserve = 1")
        await db.commit()

    listed = await getattr(TeamService(db_path), read)()

    assert [t["name"] if isinstance(t, dict) else t.name for t in listed] == ["Alpha"]
    assert await _names(db_path) == ["Alpha"]


async def test_the_reserve_team_sorts_last(tmp_path):
    """It is a configuration listing, ordered non-reserve alphabetically then Reserve, so a
    league reads its own teams first."""
    db_path = await _make_db(tmp_path)
    for name in ("Zeta", "Alpha"):
        await _add_team(db_path, name)

    teams = await TeamService(db_path).get_default_teams()

    assert [t.name for t in teams] == ["Alpha", "Zeta", RESERVE]


@pytest.mark.parametrize(
    "call",
    [
        lambda s, db: s.add_default_team_on(db, RESERVE, full_name=RESERVE),
        lambda s, db: s.modify_default_team_on(db, RESERVE, shorthand="Something"),
        lambda s, db: s.remove_default_team_on(db, RESERVE),
    ],
    ids=["add", "modify", "remove"],
)
async def test_no_team_command_may_manage_the_reserve_team(tmp_path, call):
    """Adding a team named Reserve, changing the Reserve team's shorthand and removing it are
    each refused as protected by the list's writers, as a `/team` change's save calls them."""
    from leaguebot.core.models.refusal import Refused
    db_path = await _make_db(tmp_path)

    with pytest.raises(Refused, match="protected"):
        await _write(db_path, call)


async def test_a_reserve_team_cannot_be_renamed_by_its_row_either(tmp_path):
    """The name check catches the usual case; the `is_reserve` check behind it catches a
    Reserve team that was somehow stored under another name. The Reserve team's row, the one
    a new database holds, is renamed Standbys by hand; changing Standbys's shorthand, and
    removing it, are each refused as protected."""
    from leaguebot.core.models.refusal import Refused
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE default_teams SET name = 'Standbys', full_name = 'Standbys' "
            "WHERE is_reserve = 1"
        )
        await db.commit()

    with pytest.raises(Refused, match="protected"):
        await _write(db_path, lambda s, db: s.modify_default_team_on(
            db, "Standbys", shorthand="Reserves"
        ))
    with pytest.raises(Refused, match="protected"):
        await _write(db_path, lambda s, db: s.remove_default_team_on(db, "Standbys"))


# ---------------------------------------------------------------------------
# The server's default teams
# ---------------------------------------------------------------------------


async def test_a_team_is_added_with_its_seat_count(tmp_path):
    """Alpha is added with three seats, as a `/team add` change's save adds it. The team the
    writer returns, and the list, carry it as a team of three seats that is not the Reserve; the
    writer leaves the audit entry to the change, writing none itself."""
    db_path = await _make_db(tmp_path)

    team = await _write(db_path, lambda s, db: s.add_default_team_on(
        db, "Alpha", full_name="Alpha", max_seats=3
    ))

    assert team.name == "Alpha"
    assert team.max_seats == 3
    assert team.is_reserve is False
    listed = next(t for t in await TeamService(db_path).get_default_teams() if t.name == "Alpha")
    assert (listed.max_seats, listed.is_reserve) == (3, False)
    assert await _audit_rows(db_path) == []


async def test_a_duplicate_team_name_is_refused(tmp_path):
    from leaguebot.core.models.refusal import Refused
    db_path = await _make_db(tmp_path)
    await _add_team(db_path, "Alpha")

    with pytest.raises(Refused, match="already exists"):
        await _write(db_path, lambda s, db: s.add_default_team_on(db, "Alpha", full_name="Alpha"))


async def test_a_name_colliding_once_normalised_is_refused(tmp_path):
    """The normalised name is a lineup template's field identifier, so two teams that
    normalise alike cannot both be drawn. The collision is invisible in the raw names,
    which is why this is checked rather than left to the UNIQUE constraint."""
    from leaguebot.core.models.refusal import Refused
    db_path = await _make_db(tmp_path)
    await _add_team(db_path, "Red Bull")

    with pytest.raises(Refused):
        await _write(db_path, lambda s, db: s.add_default_team_on(
            db, "red-bull", full_name="red-bull"
        ))


async def test_a_team_s_shorthand_is_changed(tmp_path):
    db_path = await _make_db(tmp_path)
    await _add_team(db_path, "Alpha", full_name="Alpha Racing")

    result = await _write(db_path, lambda s, db: s.modify_default_team_on(
        db, "Alpha", shorthand="Beta"
    ))

    assert (result["name"], result["full_name"]) == ("Beta", "Alpha Racing")
    assert "Beta" in await _names(db_path)
    assert "Alpha" not in await _names(db_path)


async def test_a_team_s_full_name_is_changed_on_its_own(tmp_path):
    """Either name may change without the other (#381): Alpha's full name, "Alpha Racing", is
    changed to "Alpha Grand Prix" and its shorthand left as it was. The writer leaves the
    `TEAM_NAMES` entry to the `/team modify` change, which `test_team_changes.py` pins, and
    writes none itself."""
    db_path = await _make_db(tmp_path)
    await _add_team(db_path, "Alpha", full_name="Alpha Racing")

    result = await _write(db_path, lambda s, db: s.modify_default_team_on(
        db, "Alpha", full_name="Alpha Grand Prix"
    ))

    assert (result["name"], result["full_name"]) == ("Alpha", "Alpha Grand Prix")
    listed = next(t for t in await TeamService(db_path).get_default_teams() if t.name == "Alpha")
    assert listed.full_name == "Alpha Grand Prix"
    assert await _audit_rows(db_path) == []


async def test_modifying_a_team_that_does_not_exist_says_so(tmp_path):
    from leaguebot.core.models.refusal import Refused
    db_path = await _make_db(tmp_path)

    with pytest.raises(Refused, match="No default team"):
        await _write(db_path, lambda s, db: s.modify_default_team_on(db, "Ghost", shorthand="Beta"))


async def test_a_shorthand_already_taken_is_refused(tmp_path):
    from leaguebot.core.models.refusal import Refused
    db_path = await _make_db(tmp_path)
    for name in ("Alpha", "Beta"):
        await _add_team(db_path, name, full_name=f"{name} Racing")

    with pytest.raises(Refused, match="already exists"):
        await _write(db_path, lambda s, db: s.modify_default_team_on(db, "Alpha", shorthand="Beta"))


async def test_a_full_name_already_taken_is_refused_and_nothing_is_written(tmp_path):
    from leaguebot.core.models.refusal import Refused
    db_path = await _make_db(tmp_path)
    for name in ("Alpha", "Beta"):
        await _add_team(db_path, name, full_name=f"{name} Racing")

    with pytest.raises(Refused, match="already the full name"):
        await _write(db_path, lambda s, db: s.modify_default_team_on(
            db, "Alpha", shorthand="Gamma", full_name="beta racing"
        ))

    assert "Alpha" in await _names(db_path)


async def test_a_team_whose_existing_name_breaks_the_rule_can_still_be_changed(tmp_path):
    """Only the *new* names are validated, deliberately. A team named before the naming rule
    existed must remain fixable — validating the current name would refuse the very command
    that puts it right, and the team could then be neither renamed nor removed."""
    db_path = await _make_db(tmp_path)
    await _add_team(db_path, "???")

    await _write(db_path, lambda s, db: s.modify_default_team_on(db, "???", shorthand="Alpha"))

    assert "Alpha" in await _names(db_path)


async def test_a_team_is_removed(tmp_path):
    """Alpha is removed from the list, as a `/team remove` change's save removes it; the writer
    leaves the audit entry to the change, writing none itself."""
    db_path = await _make_db(tmp_path)
    await _add_team(db_path, "Alpha")

    await _write(db_path, lambda s, db: s.remove_default_team_on(db, "Alpha"))

    assert "Alpha" not in await _names(db_path)
    assert await _audit_rows(db_path) == []


async def test_removing_a_team_that_does_not_exist_says_so(tmp_path):
    from leaguebot.core.models.refusal import Refused
    db_path = await _make_db(tmp_path)

    with pytest.raises(Refused, match="No default team"):
        await _write(db_path, lambda s, db: s.remove_default_team_on(db, "Ghost"))


# ---------------------------------------------------------------------------
# get_teams_with_roles
# ---------------------------------------------------------------------------


async def test_a_team_with_no_role_mapping_reads_as_none(tmp_path):
    db_path = await _make_db(tmp_path)
    await _add_team(db_path, "Alpha")

    entries = await TeamService(db_path).get_teams_with_roles()

    alpha = next(e for e in entries if e["name"] == "Alpha")
    assert alpha["role_id"] is None


async def test_a_mapped_role_is_carried_through(tmp_path):
    db_path = await _make_db(tmp_path)
    await _add_team(db_path, "Alpha")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO team_role_configs (team_name, role_id, updated_at) VALUES (?, ?, '2031-03-04T05:06:07+00:00')",
            ("Alpha", 12345),
        )
        await db.commit()

    entries = await TeamService(db_path).get_teams_with_roles()

    assert next(e for e in entries if e["name"] == "Alpha")["role_id"] == 12345


# ---------------------------------------------------------------------------
# Division seeding
# ---------------------------------------------------------------------------


async def _seed(service: TeamService, db_path: str, division_id: int) -> None:
    """Seed division *division_id*'s teams from the league's list, as the save of `/division add`
    or `/division duplicate` seeds them, on a connection of its own, committed."""
    async with get_connection(db_path) as db:
        await service.seed_division_teams_on(db, division_id)
        await db.commit()


async def test_seeding_a_division_copies_the_server_s_teams(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=1)
    service = TeamService(db_path)
    await _add_team(db_path, "Alpha")

    await _seed(service, db_path, 1)

    assert sorted(await _division_names(db_path, 1)) == ["Alpha", RESERVE]


async def test_seeding_pre_creates_a_seat_for_every_place_in_a_team(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=1)
    service = TeamService(db_path)
    await _add_team(db_path, "Alpha", max_seats=3)

    await _seed(service, db_path, 1)

    assert await _seat_count(db_path, 1, "Alpha") == 3


async def test_a_team_is_added_with_its_full_name_beside_its_shorthand(tmp_path):
    """A team's shorthand is typed and names its artwork; its full name is what is shown
    (#381). Both are kept, and neither stands in for the other."""
    db_path = await _make_db(tmp_path)

    added = await _write(db_path, lambda s, db: s.add_default_team_on(
        db, "RBR", full_name="Oracle Red Bull Racing"
    ))

    assert (added.name, added.full_name) == ("RBR", "Oracle Red Bull Racing")
    listed = next(t for t in await TeamService(db_path).get_default_teams() if t.name == "RBR")
    assert listed.full_name == "Oracle Red Bull Racing"


@pytest.mark.parametrize("full_name", ["oracle RED BULL racing", "reserve"])
async def test_a_team_is_refused_a_full_name_another_team_shows(tmp_path, full_name):
    """Full names are unique across the server's list ignoring case, the Reserve team's
    included, and nothing is added when one is taken (#381)."""
    from leaguebot.core.models.refusal import Refused
    db_path = await _make_db(tmp_path)
    await _add_team(db_path, "RBR", full_name="Oracle Red Bull Racing")

    with pytest.raises(Refused, match="already the full name"):
        await _write(db_path, lambda s, db: s.add_default_team_on(db, "RB2", full_name=full_name))

    assert [t.name for t in await TeamService(db_path).get_default_teams()] == ["RBR", RESERVE]


async def test_seeding_a_division_copies_each_team_s_full_name(tmp_path):
    """A division keeps the names its season ran under, whatever becomes of the server's
    list afterwards, so the full name is copied with the shorthand (#381)."""
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=1)
    service = TeamService(db_path)
    await _add_team(db_path, "RBR", full_name="Oracle Red Bull Racing")

    await _seed(service, db_path, 1)

    names = {t["name"]: t["full_name"] for t in await service.get_division_teams(1)}
    assert names == {"RBR": "Oracle Red Bull Racing", RESERVE: RESERVE}


async def test_the_reserve_team_is_seeded_without_seats(tmp_path):
    """The reserve team has no fixed places — drivers are distributed into it per round —
    so pre-creating seats would invent a capacity it does not have."""
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=1)

    await _seed(TeamService(db_path), db_path, 1)

    assert await _seat_count(db_path, 1, RESERVE) == 0


# ---------------------------------------------------------------------------
# get_setup_season_team_names
# ---------------------------------------------------------------------------


async def test_the_season_s_team_names_are_reported_without_the_reserve(tmp_path):
    """Used to offer a driver their team preferences, where Reserve is not a choice."""
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=2)
    service = TeamService(db_path)
    await _add_team(db_path, "Alpha")
    await _seed(service, db_path, 1)

    names = await service.get_setup_season_team_names(SEASON_ID)

    assert "Alpha" in names
    assert RESERVE not in names


async def test_a_name_in_two_divisions_is_reported_once(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=2)
    service = TeamService(db_path)
    await _add_team(db_path, "Alpha")
    await _seed(service, db_path, 1)
    await _seed(service, db_path, 2)

    assert await service.get_setup_season_team_names(SEASON_ID) == {"Alpha"}


# ---------------------------------------------------------------------------
# get_division_teams — insertion order, deliberately
# ---------------------------------------------------------------------------


async def test_a_division_s_teams_are_returned_in_insertion_order_not_by_name(tmp_path):
    """A lineup graphic addresses a team by its ordinal in this list, so a team added must
    take the *next free* position and leave the teams already drawn where they are. Sorted
    by name, adding "Alpha" after "Zeta" would shift every later team onto a new block —
    the coupling ordinal addressing exists to remove."""
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=1)
    await _insert_division_team(db_path, 1, "Zeta")
    await _insert_division_team(db_path, 1, "Alpha")

    teams = await TeamService(db_path).get_division_teams(1)

    assert [t["name"] for t in teams] == ["Zeta", "Alpha"]


async def test_a_division_s_reserve_team_comes_last(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=1)
    service = TeamService(db_path)
    # Seeding an empty list gives the division its Reserve alone, so Alpha is inserted after it.
    await _seed(service, db_path, 1)
    await _insert_division_team(db_path, 1, "Alpha")

    teams = await service.get_division_teams(1)

    assert teams[-1]["name"] == RESERVE


async def test_an_empty_seat_is_reported_with_no_driver(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=1)
    service = TeamService(db_path)
    await _add_team(db_path, "Alpha", max_seats=2)
    await _seed(service, db_path, 1)

    teams = await service.get_division_teams(1)

    seats = teams[0]["seats"]
    assert [s["seat_number"] for s in seats] == [1, 2]
    assert all(s["driver_profile_id"] is None for s in seats)


async def test_a_filled_seat_carries_its_driver(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=1)
    service = TeamService(db_path)
    await _add_team(db_path, "Alpha", max_seats=2)
    await _seed(service, db_path, 1)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles "
            "(id, discord_user_id, current_state) VALUES (77, '4242', 'ACTIVE')"
        )
        await db.execute(
            "UPDATE team_seats SET driver_profile_id = 77 "
            "WHERE seat_number = 1 AND team_instance_id = "
            "(SELECT id FROM team_instances WHERE division_id = 1 AND name = 'Alpha')"
        )
        await db.commit()

    teams = await service.get_division_teams(1)

    first_seat = teams[0]["seats"][0]
    assert first_seat["driver_profile_id"] == 77
    assert str(first_seat["discord_user_id"]) == "4242"
