"""`PlacementService` — lap-time seeding, and the team → Discord role mapping.

Issue #208. `placement_service.py` sat at 42.5%. This file takes its two self-contained
halves: the lap-time arithmetic that decides a driver's seeding, and the `team_role_configs`
layer that decides which Discord role a placed driver is given.

**The seeding total is the number a whole season's divisions are built from.** A league asks
drivers for a time at several tracks, and the sum of those times is the driver's seed — so a
parse that silently returned a wrong number would sort the grid wrongly and nothing downstream
could tell. `_parse_lap_time_ms` therefore returns `None` rather than guessing, and
`_compute_total_lap_ms` propagates that: **one unparseable time discards the whole total**
rather than seeding the driver on a partial sum, which would flatter them against drivers whose
times all parsed. `test_one_unparseable_time_discards_the_whole_total` is what holds that, and
it is the test most likely to be broken by someone making the parser more forgiving.

A lap time is read in the one strict form the whole bot reads times in (#362): exactly three
digits after the dot, which is the form signup accepts it in. Anything else is refused rather
than padded into a seed.

**The role mapping's three writers all audit, and each records a different shape of change.**
Setting records the old role and the new; deleting records the old role and `None`; renaming
records the same role under two names. They are separate statements, so each is checked for
what it actually wrote — an audit entry naming the wrong team would misattribute a role change
in the league's only record of one.

**Deleting and renaming an absent mapping are deliberate no-ops**, not errors: `/team remove`
and `/team rename` call them for every team whether or not it had a role, so raising would make
the common case an error.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.core.services.placement_service import (  # noqa: E402
    PlacementService,
    _compute_total_lap_ms,
    _fmt_ms,
    _parse_lap_time_ms,
)

SERVER_ID = 9408
ACTOR_ID = 77
ROLE_ID = 4242
OTHER_ROLE_ID = 5353


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "placement.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.commit()
    return db_path


async def _audit(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, actor_name FROM audit_entries "
            " ORDER BY id",
        )
        return [dict(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "total_ms,expected",
    [
        (83_456, "1:23.456"),
        (60_000, "1:00.000"),
        (0, "0:00.000"),
        (3_661_001, "61:01.001"),
        (59_999, "0:59.999"),
    ],
)
def test_a_total_is_formatted_as_minutes_seconds_milliseconds(total_ms, expected):
    """Seconds and milliseconds are zero-padded; a league reading `1:3.45` could not tell
    three seconds from thirty."""
    assert _fmt_ms(total_ms) == expected


def test_an_hour_long_total_keeps_counting_in_minutes(tmp_path):
    """Several tracks summed can exceed an hour. Rolling over into hours would make a
    four-track total read as shorter than a three-track one."""
    assert _fmt_ms(3_600_000) == "60:00.000"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1:23.456", 83_456),
        ("0:59.999", 59_999),
        ("  1:23.456  ", 83_456),
        ("10:00.000", 600_000),
        ("58.123", 58_123),
    ],
)
def test_a_lap_time_is_parsed_to_milliseconds(raw, expected):
    assert _parse_lap_time_ms(raw) == expected


@pytest.mark.parametrize("raw", ["1:23.4", "1:23.45", "1:23.4567", "1:23"])
def test_a_lap_time_outside_the_strict_form_is_refused(raw):
    """Signup accepts a lap time in one form only, three digits after the dot (#362), so a
    stored one outside it is refused rather than padded into a seed."""
    assert _parse_lap_time_ms(raw) is None


@pytest.mark.parametrize("raw", ["", "nonsense", "a:bc.def", None])
def test_an_unparseable_time_is_refused_rather_than_guessed(raw):
    """The seed is a number a whole season's divisions are built from, so a wrong one is
    worse than no number at all."""
    assert _parse_lap_time_ms(raw) is None


# ---------------------------------------------------------------------------
# The seeding total
# ---------------------------------------------------------------------------


def test_times_across_tracks_are_summed():
    assert _compute_total_lap_ms({"1": "1:00.000", "2": "0:30.500"}) == 90_500


def test_no_times_at_all_gives_no_total():
    """A league that asked for no lap times seeds nobody, and a zero would seed every
    driver identically at the top of the grid."""
    assert _compute_total_lap_ms({}) is None


def test_one_unparseable_time_discards_the_whole_total():
    """Not a partial sum. Seeding a driver on two of their three times would flatter them
    against drivers whose times all parsed — the most dangerous way this could fail,
    because the result still looks like a plausible seed."""
    assert _compute_total_lap_ms({"1": "1:00.000", "2": "rubbish"}) is None


def test_a_total_of_zero_is_treated_as_no_total():
    """All-zero times are not a real submission."""
    assert _compute_total_lap_ms({"1": "0:00.000"}) is None


async def test_the_total_is_persisted_against_the_signup(tmp_path):
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_records (discord_user_id, discord_username) "
            "VALUES ('4242', 'lewis')"
        )
        await db.commit()

    total = await PlacementService(db_path).store_total_lap_ms(
        "4242", {"1": "1:00.000", "2": "0:30.500"}
    )

    assert total == 90_500
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT total_lap_ms FROM signup_records WHERE discord_user_id = '4242'"
        )
        assert (await cursor.fetchone())["total_lap_ms"] == 90_500


async def test_a_driver_with_no_times_is_stored_as_having_none(tmp_path):
    """NULL rather than zero, so the seeded listing can put them last rather than first."""
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_records (discord_user_id, discord_username) "
            "VALUES ('4242', 'lewis')"
        )
        await db.commit()

    assert await PlacementService(db_path).store_total_lap_ms("4242", {}) is None

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT total_lap_ms FROM signup_records WHERE discord_user_id = '4242'"
        )
        assert (await cursor.fetchone())["total_lap_ms"] is None


# ---------------------------------------------------------------------------
# The team → role mapping
# ---------------------------------------------------------------------------


async def test_a_team_with_no_role_reads_as_none(tmp_path):
    service = PlacementService(await _make_db(tmp_path))

    assert await service.get_team_role_config("Alpha") is None


async def test_a_role_is_mapped_to_a_team(tmp_path):
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)

    await service.set_team_role_config("Alpha", ROLE_ID, ACTOR_ID, "Manager")

    config = await service.get_team_role_config("Alpha")
    assert config is not None
    assert config.role_id == ROLE_ID
    assert config.team_name == "Alpha"


async def test_remapping_a_team_replaces_rather_than_duplicates(tmp_path):
    """Two rows for one team would make which role a driver is given depend on row order."""
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)

    await service.set_team_role_config("Alpha", ROLE_ID, ACTOR_ID, "Manager")
    await service.set_team_role_config(
        "Alpha", OTHER_ROLE_ID, ACTOR_ID, "Manager"
    )

    assert (await service.get_team_role_config("Alpha")).role_id == OTHER_ROLE_ID
    assert len(await service.get_all_team_role_configs()) == 1


async def test_setting_a_role_records_what_it_replaced(tmp_path):
    """The league's only record of a role change. `None` as the old value is what
    distinguishes a first mapping from a remap."""
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)

    await service.set_team_role_config("Alpha", ROLE_ID, ACTOR_ID, "Manager")
    await service.set_team_role_config(
        "Alpha", OTHER_ROLE_ID, ACTOR_ID, "Manager"
    )

    entries = await _audit(db_path)
    assert json.loads(entries[0]["old_value"])["role_id"] is None
    assert json.loads(entries[1]["old_value"])["role_id"] == ROLE_ID
    assert json.loads(entries[1]["new_value"])["role_id"] == OTHER_ROLE_ID


async def test_every_mapping_is_listed(tmp_path):
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await service.set_team_role_config("Alpha", ROLE_ID)
    await service.set_team_role_config("Beta", OTHER_ROLE_ID)

    configs = await service.get_all_team_role_configs()

    assert {c.team_name for c in configs} == {"Alpha", "Beta"}


async def test_a_mapping_is_deleted(tmp_path):
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await service.set_team_role_config("Alpha", ROLE_ID)

    await service.delete_team_role_config("Alpha", ACTOR_ID, "Manager")

    assert await service.get_team_role_config("Alpha") is None


async def test_deleting_records_the_role_that_was_removed(tmp_path):
    """Recorded as a change *to* `None`, so the audit shows a role was taken away rather
    than merely that something happened."""
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await service.set_team_role_config("Alpha", ROLE_ID)

    await service.delete_team_role_config("Alpha", ACTOR_ID, "Manager")

    entry = (await _audit(db_path))[-1]
    assert json.loads(entry["old_value"])["role_id"] == ROLE_ID
    assert json.loads(entry["new_value"])["role_id"] is None


async def test_deleting_a_mapping_that_does_not_exist_is_a_no_op(tmp_path):
    """`/team remove` calls this for every team whether or not it had a role, so raising
    would make the common case an error."""
    db_path = await _make_db(tmp_path)

    await PlacementService(db_path).delete_team_role_config("Ghost")

    assert await _audit(db_path) == []


async def test_a_mapping_follows_its_team_through_a_rename(tmp_path):
    """Otherwise renaming a team would silently strip its drivers' role."""
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await service.set_team_role_config("Alpha", ROLE_ID)

    await service.rename_team_role_config("Alpha", "Beta", ACTOR_ID, "Manager")

    assert await service.get_team_role_config("Alpha") is None
    renamed = await service.get_team_role_config("Beta")
    assert renamed is not None
    assert renamed.role_id == ROLE_ID


async def test_a_rename_records_the_same_role_under_both_names(tmp_path):
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await service.set_team_role_config("Alpha", ROLE_ID)

    await service.rename_team_role_config("Alpha", "Beta", ACTOR_ID, "Manager")

    entry = (await _audit(db_path))[-1]
    old, new = json.loads(entry["old_value"]), json.loads(entry["new_value"])
    assert (old["team"], new["team"]) == ("Alpha", "Beta")
    assert old["role_id"] == new["role_id"] == ROLE_ID


async def test_renaming_a_team_with_no_mapping_is_a_no_op(tmp_path):
    db_path = await _make_db(tmp_path)

    await PlacementService(db_path).rename_team_role_config("Ghost", "Beta")

    assert await _audit(db_path) == []


async def test_a_change_made_by_the_bot_itself_is_attributed_to_it(tmp_path):
    """The defaults exist because autoreserve and season setup reach these without an
    actor. "system" in the audit is better than a blank or a zero."""
    db_path = await _make_db(tmp_path)

    await PlacementService(db_path).set_team_role_config("Alpha", ROLE_ID)

    assert (await _audit(db_path))[0]["actor_name"] == "system"


# ---------------------------------------------------------------------------
# A role belongs to one team only (decided 2026-09-22, with #375)
# ---------------------------------------------------------------------------


async def test_a_role_another_team_holds_is_refused_and_nothing_written(tmp_path):
    """A submission names a team by its role, so a role two teams held would name either."""
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await service.set_team_role_config("Alpha", ROLE_ID)
    await service.set_team_role_config("Beta", OTHER_ROLE_ID)

    with pytest.raises(ValueError, match='already the role of "Alpha"'):
        await service.set_team_role_config("Beta", ROLE_ID)

    assert (await service.get_team_role_config("Beta")).role_id == OTHER_ROLE_ID
    assert len(await _audit(db_path)) == 2


async def test_the_reserve_s_role_is_one_no_other_team_may_hold(tmp_path):
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await service.set_team_role_config("Reserve", ROLE_ID)

    with pytest.raises(ValueError, match='"Reserve"'):
        await service.set_team_role_config("Alpha", ROLE_ID)


async def test_a_team_may_be_given_the_role_it_already_holds(tmp_path):
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await service.set_team_role_config("Alpha", ROLE_ID)

    await service.set_team_role_config("Alpha", ROLE_ID)

    assert (await service.get_team_role_config("Alpha")).role_id == ROLE_ID


async def test_the_team_holding_a_role_is_named_but_never_the_team_asking(tmp_path):
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await service.set_team_role_config("Alpha", ROLE_ID)

    assert await service.team_holding_role(ROLE_ID) == "Alpha"
    assert await service.team_holding_role(ROLE_ID, other_than="Alpha") is None
    assert await service.team_holding_role(OTHER_ROLE_ID) is None


async def test_the_schema_refuses_two_teams_one_role(tmp_path):
    """The backstop behind the refusal, for any writer that goes around the service."""
    import sqlite3

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO team_role_configs (team_name, role_id) VALUES ('Alpha', ?)", (ROLE_ID,)
        )
        with pytest.raises(sqlite3.IntegrityError):
            await db.execute(
                "INSERT INTO team_role_configs (team_name, role_id) VALUES ('Beta', ?)",
                (ROLE_ID,),
            )
