"""`PlacementService` — formatting a seeding total, and the team → Discord role mapping.

Issue #208. `placement_service.py` sat at 42.5%. This file takes its two self-contained
halves: how a driver's seeding total is shown, and the `team_role_configs` layer that decides
which Discord role a placed driver is given. The total itself is worked out by signup, and
`tests/signup/test_lap_sum.py` pins how.

**The role mapping's three writers write the mapping alone,** in their `_on` forms, on the
connection of a `/team` change's save, committing nothing. The audit entry of a role change is
the change's own, written in the same save with the queue's clock, and `test_team_changes.py`
pins what each records; the writers record nothing themselves.

**Deleting and renaming an absent mapping are deliberate no-ops**, not errors: `/team remove`
and `/team modify` call them for every team whether or not it had a role, so raising would make
the common case an error.
"""
from __future__ import annotations

from datetime import datetime, timezone

import os

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.placement_service import (
    PlacementService,
    _fmt_ms,
)

#: Why a placement refusal test fails until #283's build raises the member's refusal by name.
PLACEMENT_REFUSAL = "#283: placement still refuses with a bare ValueError, not Refused"

SERVER_ID = 9408
ROLE_ID = 4242
OTHER_ROLE_ID = 5353
NOW = datetime(2031, 3, 4, 5, 6, 7, tzinfo=timezone.utc)


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


async def _map(db_path: str, team_name: str, role_id: int) -> None:
    """*team_name* mapped to *role_id*, written straight into the mapping. The tests that are not
    of the mapping's writers map their teams here."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO team_role_configs (team_name, role_id, updated_at) VALUES (?, ?, '2031-03-04T05:06:07+00:00')",
            (team_name, role_id),
        )
        await db.commit()


async def _write(db_path: str, write) -> None:
    """Run one of the mapping's writers in its `_on` form on a connection of its own and commit
    it, as a `/team` change's save runs it: *write* takes the service and the connection."""
    async with get_connection(db_path) as db:
        await write(PlacementService(db_path), db)
        await db.commit()


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
        (3_600_000, "60:00.000"),
    ],
)
def test_a_total_is_formatted_as_minutes_seconds_milliseconds(total_ms, expected):
    """Seconds and milliseconds are zero-padded; a league reading `1:3.45` could not tell
    three seconds from thirty. Several tracks summed can exceed an hour, and the total keeps
    counting in minutes: rolling over into hours would make a four-track total read as shorter
    than a three-track one."""
    assert _fmt_ms(total_ms) == expected


# ---------------------------------------------------------------------------
# The team → role mapping
# ---------------------------------------------------------------------------


async def test_a_team_with_no_role_reads_as_none(tmp_path):
    service = PlacementService(await _make_db(tmp_path))

    assert await service.get_team_role_config("Alpha") is None


async def test_a_role_is_mapped_to_a_team(tmp_path):
    """Alpha, with no role, is given 4242 as a `/team` change's save gives it. The mapping
    holds Alpha with 4242, stamped with the save's time as handed in, and the writer leaves
    the audit entry to the change, writing none."""
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)

    await _write(db_path, lambda s, db: s.set_team_role_config_on(db, "Alpha", ROLE_ID, now=NOW))

    config = await service.get_team_role_config("Alpha")
    assert config is not None
    assert config.role_id == ROLE_ID
    assert config.team_name == "Alpha"
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT updated_at FROM team_role_configs WHERE team_name = 'Alpha'"
        )
        assert (await cursor.fetchone())["updated_at"] == NOW.isoformat()
    assert await _audit(db_path) == []


async def test_remapping_a_team_replaces_rather_than_duplicates(tmp_path):
    """Two rows for one team would make which role a driver is given depend on row order."""
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await _map(db_path, "Alpha", ROLE_ID)

    await _write(db_path, lambda s, db: s.set_team_role_config_on(db, "Alpha", OTHER_ROLE_ID, now=NOW))

    assert (await service.get_team_role_config("Alpha")).role_id == OTHER_ROLE_ID
    assert len(await service.get_all_team_role_configs()) == 1


async def test_every_mapping_is_listed(tmp_path):
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await _map(db_path, "Alpha", ROLE_ID)
    await _map(db_path, "Beta", OTHER_ROLE_ID)

    configs = await service.get_all_team_role_configs()

    assert {c.team_name for c in configs} == {"Alpha", "Beta"}


async def test_a_mapping_is_deleted(tmp_path):
    """Alpha, mapped to 4242, has its mapping deleted as `/team remove`'s save deletes it; the
    writer leaves the audit entry to the change, writing none."""
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await _map(db_path, "Alpha", ROLE_ID)

    await _write(db_path, lambda s, db: s.delete_team_role_config_on(db, "Alpha"))

    assert await service.get_team_role_config("Alpha") is None
    assert await _audit(db_path) == []


async def test_deleting_a_mapping_that_does_not_exist_is_a_no_op(tmp_path):
    """`/team remove` calls this for every team whether or not it had a role, so raising
    would make the common case an error. Ghost has no mapping: deleting it raises nothing and
    writes nothing."""
    db_path = await _make_db(tmp_path)

    await _write(db_path, lambda s, db: s.delete_team_role_config_on(db, "Ghost"))

    assert await PlacementService(db_path).get_all_team_role_configs() == []
    assert await _audit(db_path) == []


async def test_a_mapping_follows_its_team_through_a_rename(tmp_path):
    """Otherwise renaming a team would silently strip its drivers' role."""
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await _map(db_path, "Alpha", ROLE_ID)

    await _write(db_path, lambda s, db: s.rename_team_role_config_on(db, "Alpha", "Beta", now=NOW))

    assert await service.get_team_role_config("Alpha") is None
    renamed = await service.get_team_role_config("Beta")
    assert renamed is not None
    assert renamed.role_id == ROLE_ID


async def test_renaming_a_team_with_no_mapping_is_a_no_op(tmp_path):
    """Ghost has no mapping: renaming it to Beta raises nothing and writes nothing."""
    db_path = await _make_db(tmp_path)

    await _write(db_path, lambda s, db: s.rename_team_role_config_on(db, "Ghost", "Beta", now=NOW))

    assert await PlacementService(db_path).get_all_team_role_configs() == []
    assert await _audit(db_path) == []


# ---------------------------------------------------------------------------
# A role belongs to one team only (decided 2026-09-22, with #375)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=PLACEMENT_REFUSAL)
async def test_a_role_another_team_holds_is_refused_and_nothing_written(tmp_path):
    """A submission names a team by its role, so a role two teams held would name either.
    Alpha holds 4242 and Beta 5353; giving Beta 4242 is refused naming Alpha, and Beta keeps
    5353."""
    from leaguebot.core.models.refusal import Refused
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await _map(db_path, "Alpha", ROLE_ID)
    await _map(db_path, "Beta", OTHER_ROLE_ID)

    with pytest.raises(Refused, match='already the role of "Alpha"'):
        await _write(db_path, lambda s, db: s.set_team_role_config_on(db, "Beta", ROLE_ID, now=NOW))

    assert (await service.get_team_role_config("Beta")).role_id == OTHER_ROLE_ID
    assert await _audit(db_path) == []


@pytest.mark.xfail(strict=True, reason=PLACEMENT_REFUSAL)
async def test_the_reserve_s_role_is_one_no_other_team_may_hold(tmp_path):
    from leaguebot.core.models.refusal import Refused
    db_path = await _make_db(tmp_path)
    await _map(db_path, "Reserve", ROLE_ID)

    with pytest.raises(Refused, match='"Reserve"'):
        await _write(db_path, lambda s, db: s.set_team_role_config_on(db, "Alpha", ROLE_ID, now=NOW))


async def test_a_team_may_be_given_the_role_it_already_holds(tmp_path):
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await _map(db_path, "Alpha", ROLE_ID)

    await _write(db_path, lambda s, db: s.set_team_role_config_on(db, "Alpha", ROLE_ID, now=NOW))

    assert (await service.get_team_role_config("Alpha")).role_id == ROLE_ID


async def test_the_team_holding_a_role_is_named_but_never_the_team_asking(tmp_path):
    db_path = await _make_db(tmp_path)
    service = PlacementService(db_path)
    await _map(db_path, "Alpha", ROLE_ID)

    assert await service.team_holding_role(ROLE_ID) == "Alpha"
    assert await service.team_holding_role(ROLE_ID, other_than="Alpha") is None
    assert await service.team_holding_role(OTHER_ROLE_ID) is None


async def test_the_schema_refuses_two_teams_one_role(tmp_path):
    """The backstop behind the refusal, for any writer that goes around the service."""
    import sqlite3

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO team_role_configs (team_name, role_id, updated_at) VALUES ('Alpha', ?, '2031-03-04T05:06:07+00:00')", (ROLE_ID,)
        )
        with pytest.raises(sqlite3.IntegrityError):
            await db.execute(
                "INSERT INTO team_role_configs (team_name, role_id, updated_at) VALUES ('Beta', ?, '2031-03-04T05:06:07+00:00')",
                (ROLE_ID,),
            )
