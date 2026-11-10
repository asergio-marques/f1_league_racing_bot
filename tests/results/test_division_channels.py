"""The three results channel setters.

They share one `division_results_config` row and must not blank each other. They are separate
`ON CONFLICT` upserts naming one column each, which is the shape where a copy-paste error
silently clears the neighbouring channel.

Each is called by its setting's save on the save's own connection (#283), and never commits: the
save owns the transaction, so the channel and its audit entry stand or fall together. The tests
call each on a connection of their own and commit, as the save's transaction does once it ends.
"""
from __future__ import annotations

import inspect
import os

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.services.division_channels import (
    set_division_penalty_channel,
    set_division_results_channel,
    set_division_standings_channel,
)

SERVER_ID = 9808
DIVISION_ID = 11

CHANNEL_SETTERS = [
    (set_division_results_channel, "results_channel_id"),
    (set_division_standings_channel, "standings_channel_id"),
    (set_division_penalty_channel, "penalty_channel_id"),
]


async def _make_db(tmp_path) -> str:
    """A database holding one division of a season in setup."""
    db_path = os.path.join(str(tmp_path), "seasons.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 1, '2026-01-01', 'SETUP')",
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, 1, 'Division 11', 11, 555)",
            (DIVISION_ID,),
        )
        await db.commit()
    return db_path


def _on_a_connection(setter) -> None:
    """Fail at once where *setter* still opens a database by the path it is given: handed a
    connection, it would open one it never closes, at a file named for that connection in the
    working directory, and the session could not end."""
    if "db_path" in inspect.signature(setter).parameters:
        pytest.fail(f"{setter.__name__} still opens its own connection")


async def _set(db_path: str, setter, *values) -> list:
    """Call *setter* with each of *values* in turn for Division 11, on one connection, then
    commit; the values each call gave back."""
    _on_a_connection(setter)
    async with get_connection(db_path) as db:
        replaced = [await setter(db, DIVISION_ID, value) for value in values]
        await db.commit()
    return replaced


#: `penalty_channel_id` is a TEXT column while the other two are INTEGER, so the value
#: comes back as a string. Compared as text throughout rather than papered over with a cast:
#: the divergence is real and a test that hid it would hide the next one too.
def _as_text(value) -> str | None:
    return None if value is None else str(value)


@pytest.mark.parametrize("setter,column", CHANNEL_SETTERS)
async def test_each_results_channel_setter_writes_its_own_column(tmp_path, setter, column):
    db_path = await _make_db(tmp_path)

    await _set(db_path, setter, 700001)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT {column} FROM division_results_config WHERE division_id = 11"  # noqa: S608
        )
        assert _as_text((await cursor.fetchone())[column]) == "700001"


async def test_the_three_results_channels_share_a_row_without_blanking_each_other(tmp_path):
    """Three separate upserts naming one column each — the shape where a copy-paste error
    silently clears the neighbouring channel."""
    db_path = await _make_db(tmp_path)

    await _set(db_path, set_division_results_channel, 700001)
    await _set(db_path, set_division_standings_channel, 700002)
    await _set(db_path, set_division_penalty_channel, 700003)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT results_channel_id, standings_channel_id, penalty_channel_id "
            "FROM division_results_config WHERE division_id = 11"
        )
        row = await cursor.fetchone()
    assert row["results_channel_id"] == 700001
    assert row["standings_channel_id"] == 700002
    assert _as_text(row["penalty_channel_id"]) == "700003"


@pytest.mark.parametrize("setter,column", CHANNEL_SETTERS)
async def test_a_results_channel_setter_reports_what_it_replaced(tmp_path, setter, column):
    db_path = await _make_db(tmp_path)

    [first] = await _set(db_path, setter, 700001)
    [second] = await _set(db_path, setter, 700002)

    assert first is None
    assert _as_text(second) == "700001"


@pytest.mark.parametrize("setter,column", CHANNEL_SETTERS)
async def test_a_results_channel_can_be_cleared(tmp_path, setter, column):
    """A league that stops posting one of the three has no other way to unset it."""
    db_path = await _make_db(tmp_path)
    await _set(db_path, setter, 700001)

    await _set(db_path, setter, None)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT {column} FROM division_results_config WHERE division_id = 11"  # noqa: S608
        )
        assert (await cursor.fetchone())[column] is None


async def test_setting_a_channel_twice_does_not_make_a_second_row(tmp_path):
    db_path = await _make_db(tmp_path)

    await _set(db_path, set_division_results_channel, 700001)
    await _set(db_path, set_division_results_channel, 700002)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM division_results_config WHERE division_id = ?", (DIVISION_ID,)
        )
        assert (await cursor.fetchone())[0] == 1


@pytest.mark.parametrize("setter,column", CHANNEL_SETTERS)
async def test_a_results_channel_setter_leaves_the_commit_to_its_save(tmp_path, setter, column):
    """Division 11 has none of its results channels. Its results (standings, verdicts) channel
    is set to 700001 on a connection whose transaction is then rolled back, as a save's is when
    the audit entry beside it fails: the setter committed nothing, and Division 11 still has no
    such channel."""
    _on_a_connection(setter)
    db_path = await _make_db(tmp_path)

    async with get_connection(db_path) as db:
        await setter(db, DIVISION_ID, 700001)
        await db.rollback()

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT {column} FROM division_results_config WHERE division_id = 11"  # noqa: S608
        )
        row = await cursor.fetchone()
    assert row is None or row[column] is None


@pytest.mark.parametrize("setter,column", CHANNEL_SETTERS)
async def test_a_results_channel_already_held_is_left_unwritten(tmp_path, setter, column):
    """Division 11's results (standings, verdicts) channel is already 700001, and the setter is
    asked for 700001 again: it writes nothing, no row being changed on the connection, and gives
    back 700001 as the channel it would have replaced, so its save can tell nothing changed."""
    db_path = await _make_db(tmp_path)
    await _set(db_path, setter, 700001)

    async with get_connection(db_path) as db:
        before = db.total_changes
        replaced = await setter(db, DIVISION_ID, 700001)
        changed = db.total_changes - before
        await db.commit()

    assert changed == 0
    assert _as_text(replaced) == "700001"


async def test_results_names_each_division_s_missing_or_deleted_channels(tmp_path):
    """Division 11's results channel 120 is set, its standings channel 121 gone from the server and
    the server cannot say whether its verdicts channel 122 is there; Am has none of the three.
    Results on names Division 11's standings channel as gone, taking the verdicts channel as there, and each of
    Am's three as missing, in the order results, standings, verdicts, each with the command that
    sets it. With no server to check, Am's three alone are named, Division 11's set channels
    being taken as there. Results off names nothing."""
    from unittest.mock import AsyncMock, MagicMock

    from leaguebot.results.services.division_channels import division_channel_faults
    from tests.support.channel_faults import server_holding

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier) "
            "VALUES (12, 1, 'Am', 1, 12)"
        )
        await db.execute(
            "INSERT INTO division_results_config (division_id, results_channel_id, "
            "standings_channel_id, penalty_channel_id) VALUES (?, 120, 121, '122')",
            (DIVISION_ID,),
        )
        await db.commit()
    guild = server_holding(gone=(121,), unreadable=(122,))

    async def faults(on: bool, server=guild) -> dict[int, list[str]]:
        modules = MagicMock()
        modules.is_results_enabled = AsyncMock(return_value=on)
        found = await division_channel_faults(
            modules, db_path, divisions={DIVISION_ID: "Division 11", 12: "Am"}, guild=server
        )
        return {division_id: lines for division_id, lines in found.items() if lines}

    am = {
        12: [
            "**Am** has no results channel — `/results channel results`.",
            "**Am** has no standings channel — `/results channel standings`.",
            "**Am** has no verdicts channel — `/results channel verdicts`.",
        ],
    }
    assert await faults(True) == {
        DIVISION_ID: [
            "**Division 11**'s standings channel is no longer on the server — "
            "`/results channel standings`.",
        ],
        **am,
    }
    assert await faults(True, None) == am
    assert await faults(False) == {}


@pytest.mark.xfail(
    strict=True, reason="#283: results does not yet hand the review its division channel lines"
)
async def test_results_hands_the_review_each_division_s_channel_lines(tmp_path):
    """Division 11 has its results channel 120 and no standings or verdicts channel; Am has its
    standings channel 221 and verdicts channel 222 and no results channel; Club has no results
    configuration at all. Results hands the placements review three lines for each, results,
    standings then verdicts: a channel set is named, a results or standings channel not set reads
    "*(none)*", and a verdicts channel not set "*(not configured)*". It asks no switch, the
    review showing them whether results is on or off."""
    from leaguebot.results.services.division_channels import division_lines

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier) "
            "VALUES (12, 1, 'Am', 1, 12), (13, 1, 'Club', 1, 13)"
        )
        await db.execute(
            "INSERT INTO division_results_config (division_id, results_channel_id, "
            "standings_channel_id, penalty_channel_id) VALUES (?, 120, NULL, NULL), "
            "(12, NULL, 221, '222')",
            (DIVISION_ID,),
        )
        await db.commit()

    lines = await division_lines(db_path, season_id=1, division_ids=[DIVISION_ID, 12, 13])

    assert lines == {
        DIVISION_ID: [
            "  Results channel: <#120>",
            "  Standings channel: *(none)*",
            "  Verdicts channel: *(not configured)*",
        ],
        12: [
            "  Results channel: *(none)*",
            "  Standings channel: <#221>",
            "  Verdicts channel: <#222>",
        ],
        13: [
            "  Results channel: *(none)*",
            "  Standings channel: *(none)*",
            "  Verdicts channel: *(not configured)*",
        ],
    }
