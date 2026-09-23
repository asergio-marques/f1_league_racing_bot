"""`TeamService` — the server's team list, and a season's teams across its divisions.

Issue #208. `tests/unit/test_team_name_validation.py` covers `validate_team_name`, the module
function; `TeamService` itself — the class every `/team` command reaches — was almost entirely
unexecuted, 125 of its 177 statements. Teams are the thing seats hang off, so a fault here
reaches the lineup graphic, the signup wizard's team preferences and the reserve distribution
alike.

Four rules are pinned here, each of which a reader could undo without any other test noticing.

**The Reserve team is protected, and is conjured whenever it is missing.** `_ensure_reserve`
runs on every read and write of the server's list, not only on first setup — the older seeding
fired only when the server had *no* team at all, so a configuration that lost its Reserve row,
or predates the rule, never regained one. `test_reading_the_team_list_restores_a_lost_reserve`
holds that. No `/team` command may add, rename or remove it.

**Only the new names are validated when a team is changed.** Deliberately, per the method's own
comment: a team named before the naming rule existed would otherwise be impossible to rename *or*
to remove, because validating the current name would refuse the very command that fixes it.
`test_a_team_whose_existing_name_breaks_the_rule_can_still_be_changed` sits on that, and it is
the test most likely to be broken by someone "tightening" the validation.

**Uniqueness is scoped differently in the two halves.** The server's default list is unique
across the server; a season's teams are unique **within a division**. The two use different
key helpers against different tables, and conflating them would let a season carry two teams
whose normalised names collide in one division — which is what the lineup template's field
identifiers are keyed on.

**A season's team commands are all-or-nothing across divisions.** `season_team_rename` checks
every division before writing to any, so a name rejected in the third division leaves the
first two untouched. `test_a_rename_rejected_in_one_division_changes_none_of_them` is the one
that would catch a loop that validated and wrote in the same pass.

Everything runs against a real migrated database. What these methods are chiefly at risk of
getting wrong is SQL across four tables, and a double would confirm whatever shape the test
author imagined.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.team_service import TeamService  # noqa: E402

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
    service = TeamService(await _make_db(tmp_path))

    teams = await service.get_default_teams()

    assert [t.name for t in teams] == [RESERVE]
    assert teams[0].is_reserve is True


async def test_reading_the_team_list_restores_a_lost_reserve(tmp_path):
    """The rule the older seeding got wrong. It fired only when the server had no team at
    all, so a configuration that lost its Reserve row never regained one — and every
    division seeded from it would then have no reserve team to distribute into."""
    db_path = await _make_db(tmp_path)
    service = TeamService(db_path)
    await service.add_default_team("Alpha", full_name="Alpha")
    async with get_connection(db_path) as db:
        await db.execute(
            "DELETE FROM default_teams WHERE is_reserve = 1"
        )
        await db.commit()
    assert RESERVE not in await _names(db_path)

    await service.get_default_teams()

    assert RESERVE in await _names(db_path)


async def test_the_reserve_team_sorts_last(tmp_path):
    """It is a configuration listing, ordered non-reserve alphabetically then Reserve, so a
    league reads its own teams first."""
    service = TeamService(await _make_db(tmp_path))
    for name in ("Zeta", "Alpha"):
        await service.add_default_team(name, full_name=name)

    teams = await service.get_default_teams()

    assert [t.name for t in teams] == ["Alpha", "Zeta", RESERVE]


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.add_default_team(RESERVE, full_name=RESERVE),
        lambda s: s.modify_default_team(RESERVE, shorthand="Something"),
        lambda s: s.remove_default_team(RESERVE),
    ],
    ids=["add", "modify", "remove"],
)
async def test_no_team_command_may_manage_the_reserve_team(tmp_path, call):
    service = TeamService(await _make_db(tmp_path))

    with pytest.raises(ValueError, match="protected"):
        await call(service)


async def test_a_reserve_team_cannot_be_renamed_by_its_row_either(tmp_path):
    """The name check catches the usual case; the `is_reserve` check behind it catches a
    Reserve team that was somehow stored under another name."""
    db_path = await _make_db(tmp_path)
    service = TeamService(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO default_teams (name, full_name, max_seats, is_reserve) "
            "VALUES ('Standbys', 'Standbys', -1, 1)"
        )
        await db.commit()

    with pytest.raises(ValueError, match="protected"):
        await service.modify_default_team("Standbys", shorthand="Reserves")
    with pytest.raises(ValueError, match="protected"):
        await service.remove_default_team("Standbys")


async def test_seeding_an_empty_server_creates_only_the_reserve(tmp_path):
    db_path = await _make_db(tmp_path)

    await TeamService(db_path).seed_default_teams_if_empty()

    assert await _names(db_path) == [RESERVE]


async def test_seeding_leaves_an_existing_team_list_alone(tmp_path):
    """`/bot init` may be run again on a configured server."""
    db_path = await _make_db(tmp_path)
    service = TeamService(db_path)
    await service.add_default_team("Alpha", full_name="Alpha")
    before = await _names(db_path)

    await service.seed_default_teams_if_empty()

    assert await _names(db_path) == before


# ---------------------------------------------------------------------------
# The server's default teams
# ---------------------------------------------------------------------------


async def test_a_team_is_added_with_its_seat_count(tmp_path):
    service = TeamService(await _make_db(tmp_path))

    team = await service.add_default_team("Alpha", full_name="Alpha", max_seats=3)

    assert team.name == "Alpha"
    assert team.max_seats == 3
    assert team.is_reserve is False


async def test_a_duplicate_team_name_is_refused(tmp_path):
    service = TeamService(await _make_db(tmp_path))
    await service.add_default_team("Alpha", full_name="Alpha")

    with pytest.raises(ValueError, match="already exists"):
        await service.add_default_team("Alpha", full_name="Alpha")


async def test_a_name_colliding_once_normalised_is_refused(tmp_path):
    """The normalised name is a lineup template's field identifier, so two teams that
    normalise alike cannot both be drawn. The collision is invisible in the raw names,
    which is why this is checked rather than left to the UNIQUE constraint."""
    service = TeamService(await _make_db(tmp_path))
    await service.add_default_team("Red Bull", full_name="Red Bull")

    with pytest.raises(ValueError):
        await service.add_default_team("red-bull", full_name="red-bull")


async def test_a_team_s_shorthand_is_changed(tmp_path):
    db_path = await _make_db(tmp_path)
    service = TeamService(db_path)
    await service.add_default_team("Alpha", full_name="Alpha Racing")

    result = await service.modify_default_team("Alpha", shorthand="Beta")

    assert result == {"name": "Beta", "full_name": "Alpha Racing"}
    assert "Beta" in await _names(db_path)
    assert "Alpha" not in await _names(db_path)


async def test_a_team_s_full_name_is_changed_on_its_own(tmp_path):
    """Either name may change without the other (#381)."""
    db_path = await _make_db(tmp_path)
    service = TeamService(db_path)
    await service.add_default_team("Alpha", full_name="Alpha Racing")

    result = await service.modify_default_team("Alpha", full_name="Alpha Grand Prix")

    assert result == {"name": "Alpha", "full_name": "Alpha Grand Prix"}


async def test_a_change_of_name_is_recorded_in_the_audit_log(tmp_path):
    db_path = await _make_db(tmp_path)
    service = TeamService(db_path)
    await service.add_default_team("Alpha", full_name="Alpha Racing")

    await service.modify_default_team(
        "Alpha", full_name="Alpha Grand Prix", actor_id=7, actor_name="Manager"
    )

    async with get_connection(db_path) as db:
        rows = await (
            await db.execute(
                "SELECT actor_name, old_value, new_value FROM audit_entries "
                "WHERE change_type = 'TEAM_NAMES'"
            )
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["actor_name"] == "Manager"
    assert "Alpha Racing" in rows[0]["old_value"]
    assert "Alpha Grand Prix" in rows[0]["new_value"]


async def test_modifying_a_team_that_does_not_exist_says_so(tmp_path):
    service = TeamService(await _make_db(tmp_path))

    with pytest.raises(ValueError, match="No default team"):
        await service.modify_default_team("Ghost", shorthand="Beta")


async def test_a_shorthand_already_taken_is_refused(tmp_path):
    service = TeamService(await _make_db(tmp_path))
    for name in ("Alpha", "Beta"):
        await service.add_default_team(name, full_name=f"{name} Racing")

    with pytest.raises(ValueError, match="already exists"):
        await service.modify_default_team("Alpha", shorthand="Beta")


async def test_a_full_name_already_taken_is_refused_and_nothing_is_written(tmp_path):
    db_path = await _make_db(tmp_path)
    service = TeamService(db_path)
    for name in ("Alpha", "Beta"):
        await service.add_default_team(name, full_name=f"{name} Racing")

    with pytest.raises(ValueError, match="already the full name"):
        await service.modify_default_team(
            "Alpha", shorthand="Gamma", full_name="beta racing"
        )

    assert "Alpha" in await _names(db_path)


async def test_a_team_whose_existing_name_breaks_the_rule_can_still_be_changed(tmp_path):
    """Only the *new* names are validated, deliberately. A team named before the naming rule
    existed must remain fixable — validating the current name would refuse the very command
    that puts it right, and the team could then be neither renamed nor removed."""
    db_path = await _make_db(tmp_path)
    service = TeamService(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO default_teams (name, full_name, max_seats, is_reserve) "
            "VALUES ('???', '???', 2, 0)"
        )
        await db.commit()

    await service.modify_default_team("???", shorthand="Alpha")

    assert "Alpha" in await _names(db_path)


async def test_a_team_is_removed(tmp_path):
    db_path = await _make_db(tmp_path)
    service = TeamService(db_path)
    await service.add_default_team("Alpha", full_name="Alpha")

    await service.remove_default_team("Alpha")

    assert "Alpha" not in await _names(db_path)


async def test_removing_a_team_that_does_not_exist_says_so(tmp_path):
    service = TeamService(await _make_db(tmp_path))

    with pytest.raises(ValueError, match="No default team"):
        await service.remove_default_team("Ghost")


# ---------------------------------------------------------------------------
# get_teams_with_roles
# ---------------------------------------------------------------------------


async def test_a_team_with_no_role_mapping_reads_as_none(tmp_path):
    db_path = await _make_db(tmp_path)
    service = TeamService(db_path)
    await service.add_default_team("Alpha", full_name="Alpha")

    entries = await service.get_teams_with_roles()

    alpha = next(e for e in entries if e["name"] == "Alpha")
    assert alpha["role_id"] is None


async def test_a_mapped_role_is_carried_through(tmp_path):
    db_path = await _make_db(tmp_path)
    service = TeamService(db_path)
    await service.add_default_team("Alpha", full_name="Alpha")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO team_role_configs (team_name, role_id) VALUES (?, ?)",
            ("Alpha", 12345),
        )
        await db.commit()

    entries = await service.get_teams_with_roles()

    assert next(e for e in entries if e["name"] == "Alpha")["role_id"] == 12345


# ---------------------------------------------------------------------------
# Division seeding
# ---------------------------------------------------------------------------


async def test_seeding_a_division_copies_the_server_s_teams(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=1)
    service = TeamService(db_path)
    await service.add_default_team("Alpha", full_name="Alpha")

    await service.seed_division_teams(1)

    assert sorted(await _division_names(db_path, 1)) == ["Alpha", RESERVE]


async def test_seeding_pre_creates_a_seat_for_every_place_in_a_team(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=1)
    service = TeamService(db_path)
    await service.add_default_team("Alpha", full_name="Alpha", max_seats=3)

    await service.seed_division_teams(1)

    assert await _seat_count(db_path, 1, "Alpha") == 3


async def test_a_team_is_added_with_its_full_name_beside_its_shorthand(tmp_path):
    """A team's shorthand is typed and names its artwork; its full name is what is shown
    (#381). Both are kept, and neither stands in for the other."""
    db_path = await _make_db(tmp_path)
    service = TeamService(db_path)

    added = await service.add_default_team("RBR", full_name="Oracle Red Bull Racing")

    assert (added.name, added.full_name) == ("RBR", "Oracle Red Bull Racing")
    listed = next(t for t in await service.get_default_teams() if t.name == "RBR")
    assert listed.full_name == "Oracle Red Bull Racing"


@pytest.mark.parametrize("full_name", ["oracle RED BULL racing", "reserve"])
async def test_a_team_is_refused_a_full_name_another_team_shows(tmp_path, full_name):
    """Full names are unique across the server's list ignoring case, the Reserve team's
    included, and nothing is added when one is taken (#381)."""
    db_path = await _make_db(tmp_path)
    service = TeamService(db_path)
    await service.add_default_team("RBR", full_name="Oracle Red Bull Racing")

    with pytest.raises(ValueError, match="already the full name"):
        await service.add_default_team("RB2", full_name=full_name)

    assert [t.name for t in await service.get_default_teams()] == ["RBR", RESERVE]


async def test_seeding_a_division_copies_each_team_s_full_name(tmp_path):
    """A division keeps the names its season ran under, whatever becomes of the server's
    list afterwards, so the full name is copied with the shorthand (#381)."""
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=1)
    service = TeamService(db_path)
    await service.add_default_team("RBR", full_name="Oracle Red Bull Racing")

    await service.seed_division_teams(1)

    names = {t["name"]: t["full_name"] for t in await service.get_division_teams(1)}
    assert names == {"RBR": "Oracle Red Bull Racing", RESERVE: RESERVE}


async def test_the_reserve_team_is_reserve_in_both_names(tmp_path):
    db_path = await _make_db(tmp_path)

    reserve = next(t for t in await TeamService(db_path).get_default_teams() if t.is_reserve)

    assert (reserve.name, reserve.full_name) == (RESERVE, RESERVE)


async def test_the_reserve_team_is_seeded_without_seats(tmp_path):
    """The reserve team has no fixed places — drivers are distributed into it per round —
    so pre-creating seats would invent a capacity it does not have."""
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=1)

    await TeamService(db_path).seed_division_teams(1)

    assert await _seat_count(db_path, 1, RESERVE) == 0


# ---------------------------------------------------------------------------
# A season's teams
# ---------------------------------------------------------------------------


async def test_a_season_team_is_added_to_every_division(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=3)
    service = TeamService(db_path)

    count = await service.season_team_add(SEASON_ID, "Alpha", full_name="Alpha")

    assert count == 3
    for division_id in (1, 2, 3):
        assert "Alpha" in await _division_names(db_path, division_id)


async def test_a_season_team_is_added_with_its_seats(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=1)

    await TeamService(db_path).season_team_add(SEASON_ID, "Alpha", full_name="Alpha", max_seats=4)

    assert await _seat_count(db_path, 1, "Alpha") == 4


@pytest.mark.parametrize("status", ["ACTIVE", "COMPLETED"])
async def test_a_season_not_in_setup_refuses_every_team_change(tmp_path, status):
    """Teams decide seats, and seats decide who scores. Changing them mid-season would
    move drivers out from under results already recorded."""
    db_path = await _make_db(tmp_path, season_status=status, divisions=1)
    service = TeamService(db_path)

    for call in (
        service.season_team_add(SEASON_ID, "Alpha", full_name="Alpha"),
        service.season_team_rename(SEASON_ID, "Alpha", "Beta"),
        service.season_team_remove(SEASON_ID, "Alpha"),
    ):
        with pytest.raises(ValueError, match="season is currently in setup"):
            await call


async def test_a_season_that_does_not_exist_refuses_a_team_change(tmp_path):
    db_path = await _make_db(tmp_path)

    with pytest.raises(ValueError, match="season is currently in setup"):
        await TeamService(db_path).season_team_add(999, "Alpha", full_name="Alpha")


async def test_a_name_already_in_one_division_is_refused_for_the_season(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=2)
    service = TeamService(db_path)
    await service.season_team_add(SEASON_ID, "Alpha", full_name="Alpha")

    with pytest.raises(ValueError, match="already exists"):
        await service.season_team_add(SEASON_ID, "Alpha", full_name="Alpha")


async def test_a_season_team_is_renamed_across_every_division(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=3)
    service = TeamService(db_path)
    await service.season_team_add(SEASON_ID, "Alpha", full_name="Alpha")

    count = await service.season_team_rename(SEASON_ID, "Alpha", "Beta")

    assert count == 3
    for division_id in (1, 2, 3):
        assert "Beta" in await _division_names(db_path, division_id)
        assert "Alpha" not in await _division_names(db_path, division_id)


async def test_a_rename_rejected_in_one_division_changes_none_of_them(tmp_path):
    """Every division is validated before any is written. A loop that validated and wrote
    in one pass would leave the season half-renamed, with the divisions disagreeing about
    what the team is called and no command to reconcile them."""
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=3)
    service = TeamService(db_path)
    await service.season_team_add(SEASON_ID, "Alpha", full_name="Alpha")
    # Only the third division already holds the name being renamed to.
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (3, 'Beta', 'Beta', 2, 0)"
        )
        await db.commit()

    with pytest.raises(ValueError):
        await service.season_team_rename(SEASON_ID, "Alpha", "Beta")

    for division_id in (1, 2, 3):
        assert "Alpha" in await _division_names(db_path, division_id)


async def test_a_season_team_is_removed_from_every_division_with_its_seats(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=2)
    service = TeamService(db_path)
    await service.season_team_add(SEASON_ID, "Alpha", full_name="Alpha")

    count = await service.season_team_remove(SEASON_ID, "Alpha")

    assert count == 2
    for division_id in (1, 2):
        assert "Alpha" not in await _division_names(db_path, division_id)
        assert await _seat_count(db_path, division_id, "Alpha") == 0


async def test_removing_a_team_absent_from_a_division_is_not_an_error(tmp_path):
    """The divisions can legitimately disagree — a team added before a division was
    created exists in some and not others — and the command's job is to end with it gone
    everywhere, not to complain about where it already was."""
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=2)
    service = TeamService(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (1, 'Alpha', 'Alpha', 2, 0)"
        )
        await db.commit()

    assert await service.season_team_remove(SEASON_ID, "Alpha") == 2
    assert "Alpha" not in await _division_names(db_path, 1)


async def test_the_season_s_team_names_are_reported_without_the_reserve(tmp_path):
    """Used to offer a driver their team preferences, where Reserve is not a choice."""
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=2)
    service = TeamService(db_path)
    await service.add_default_team("Alpha", full_name="Alpha")
    await service.seed_division_teams(1)

    names = await service.get_setup_season_team_names(SEASON_ID)

    assert "Alpha" in names
    assert RESERVE not in names


async def test_a_name_in_two_divisions_is_reported_once(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=2)
    service = TeamService(db_path)
    await service.add_default_team("Alpha", full_name="Alpha")
    await service.seed_division_teams(1)
    await service.seed_division_teams(2)

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
    await service.seed_division_teams(1)
    await _insert_division_team(db_path, 1, "Alpha")

    teams = await service.get_division_teams(1)

    assert teams[-1]["name"] == RESERVE


async def test_an_empty_seat_is_reported_with_no_driver(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=1)
    service = TeamService(db_path)
    await service.add_default_team("Alpha", full_name="Alpha", max_seats=2)
    await service.seed_division_teams(1)

    teams = await service.get_division_teams(1)

    seats = teams[0]["seats"]
    assert [s["seat_number"] for s in seats] == [1, 2]
    assert all(s["driver_profile_id"] is None for s in seats)


async def test_a_filled_seat_carries_its_driver(tmp_path):
    db_path = await _make_db(tmp_path, season_status="SETUP", divisions=1)
    service = TeamService(db_path)
    await service.add_default_team("Alpha", full_name="Alpha", max_seats=2)
    await service.seed_division_teams(1)
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
