"""A points edit that changes nothing is answered and recorded as such (#482).

The rule is the core specification's "The record of what changed": a request that changes
nothing replies that nothing changed, writes no audit entry, and records one line in the success
form saying so. `/results config session`, `fl` and `fl-plimit`, and `/results amend session`,
`fl` and `fl-plimit`, set to the value the table (or the modification store) already holds are
such requests, as is `/results amend revert` with nothing staged.

A same-value amend stages nothing, so it leaves the modified flag as it stood and does not block
`/results amend toggle` off (owner, 2026-09-30). Where the table is out of order, the
nothing-changed reply still carries the warning naming the positions at fault. The refusal comes
first (slice 3, decision 23): a request that cannot be carried out is refused as today, which
`test_results_amend_refusals.py` and `test_results_config_refusals.py` pin.

The configuration, the season's points and the modification store live in a real database built
from the production migrations, so each case holds the values the command reads.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.amendment_service import enable_amendment_mode
from leaguebot.results.cogs.results_cog import ResultsCog
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services import points_config_service
from tests.support.undecorate import undecorate

SERVER_ID = 10483
ACTOR_ID = 4242
CONFIG = "100%"

_RACE = SimpleNamespace(value="FEATURE_RACE", name="Feature Race")


async def _db(tmp_path, table: list[tuple[int, int]]) -> tuple[str, int]:
    """A server holding CONFIG, whose Feature Race table is *table* with a fastest-lap bonus of
    1 pt for the top 10, and a running season scored by the same table, in amendment mode with
    nothing staged."""
    db_path = os.path.join(str(tmp_path), "same_value.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        for position, points in table:
            await db.execute(
                "INSERT INTO season_points_entries (season_id, config_name, session_type, "
                "position, points) VALUES (?, ?, 'FEATURE_RACE', ?, ?)",
                (season_id, CONFIG, position, points),
            )
        await db.execute(
            "INSERT INTO season_points_fl (season_id, config_name, session_type, fl_points, "
            "fl_position_limit) VALUES (?, ?, 'FEATURE_RACE', 1, 10)",
            (season_id, CONFIG),
        )
        await db.commit()
    assert season_id is not None
    await points_config_service.create_config(db_path, CONFIG)
    for position, points in table:
        await points_config_service.set_session_points(
            db_path, CONFIG, SessionType.FEATURE_RACE, position, points
        )
    await points_config_service.set_fl_bonus(db_path, CONFIG, SessionType.FEATURE_RACE, 1)
    await points_config_service.set_fl_position_limit(
        db_path, CONFIG, SessionType.FEATURE_RACE, 10
    )
    await enable_amendment_mode(db_path, season_id)
    return db_path, season_id


def _cog(db_path: str, season_id: int) -> ResultsCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_results_enabled = AsyncMock(return_value=True)
    bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=SimpleNamespace(
            id=season_id, season_number=1, status="ACTIVE", stage=SeasonStage.ONGOING
        )
    )
    bot.output_router.post_log = AsyncMock(return_value=None)
    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = bot
    return cog


def _interaction(cog: ResultsCog, command: str):
    """`/<command>` run by the league manager Alex, answering as Discord's does."""
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction = MagicMock()
    interaction.client = cog.bot
    interaction.guild_id = SERVER_ID
    interaction.command.qualified_name = command
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Alex"
    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(c.args[0])
        for c in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if c.args
    )


def _lines(cog) -> list[str]:
    return [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]


async def _audit_count(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM audit_entries")
        return (await cursor.fetchone())[0]


async def _modified_flag(db_path: str, season_id: int) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT modified_flag FROM season_amendment_state WHERE season_id = ?",
            (season_id,),
        )
        return (await cursor.fetchone())[0]


# What each command is given: the value the table already holds.
_CONFIG_SETS = {
    "results config session": lambda cog, i: undecorate(ResultsCog.config_session)(
        cog, i, CONFIG, _RACE, 1, 25
    ),
    "results config fl": lambda cog, i: undecorate(ResultsCog.config_fl)(cog, i, CONFIG, _RACE, 1),
    "results config fl-plimit": lambda cog, i: undecorate(ResultsCog.config_fl_plimit)(
        cog, i, CONFIG, _RACE, 10
    ),
}
_AMEND_SETS = {
    "results amend session": lambda cog, i: undecorate(ResultsCog.amend_session)(
        cog, i, CONFIG, _RACE, 1, 25
    ),
    "results amend fl": lambda cog, i: undecorate(ResultsCog.amend_fl)(cog, i, CONFIG, _RACE, 1),
    "results amend fl-plimit": lambda cog, i: undecorate(ResultsCog.amend_fl_plimit)(
        cog, i, CONFIG, _RACE, 10
    ),
}


def _assert_nothing_changed(cog, interaction, command: str) -> None:
    """Alex is told nothing changed, never that a value was set, and one line in the success
    form records it, naming the configuration."""
    replied = _replied(interaction)
    assert "nothing changed" in replied.lower()
    assert "✅ Set" not in replied
    assert "Updated in modification store" not in replied
    [line] = _lines(cog)
    assert line.splitlines()[0] == f"Alex (<@{ACTOR_ID}>) | /{command} | Nothing changed"
    assert CONFIG in line


@pytest.mark.parametrize("command", sorted(_CONFIG_SETS))
async def test_a_config_set_to_the_value_it_holds_changes_nothing(tmp_path, command):
    """100% gives 25 pts for a Feature Race win and 1 pt for its fastest lap in the top 10.
    Alex sets one of those values to what it already is."""
    db_path, season_id = await _db(tmp_path, [(1, 25), (2, 18)])
    cog = _cog(db_path, season_id)
    interaction = _interaction(cog, command)
    before = await _audit_count(db_path)

    await _CONFIG_SETS[command](cog, interaction)

    _assert_nothing_changed(cog, interaction, command)
    assert await _audit_count(db_path) == before


@pytest.mark.parametrize("command", sorted(_AMEND_SETS))
async def test_an_amend_set_to_the_value_staged_changes_nothing(tmp_path, command):
    """The season is in amendment mode with nothing staged; its store gives 25 pts for a win
    and 1 pt for the fastest lap in the top 10. Alex amends one of those to what it already
    is: no audit entry, and the modified flag stays clear."""
    db_path, season_id = await _db(tmp_path, [(1, 25), (2, 18)])
    cog = _cog(db_path, season_id)
    interaction = _interaction(cog, command)
    before = await _audit_count(db_path)

    await _AMEND_SETS[command](cog, interaction)

    _assert_nothing_changed(cog, interaction, command)
    assert await _audit_count(db_path) == before
    assert await _modified_flag(db_path, season_id) == 0


@pytest.mark.parametrize("command", sorted(_AMEND_SETS))
async def test_a_same_value_amend_leaves_amendment_mode_free_to_switch_off(tmp_path, command):
    """After an amend that staged nothing, `/results amend toggle` switches amendment mode off,
    never refusing for uncommitted changes."""
    db_path, season_id = await _db(tmp_path, [(1, 25), (2, 18)])
    cog = _cog(db_path, season_id)
    await _AMEND_SETS[command](cog, _interaction(cog, command))
    toggle = _interaction(cog, "results amend toggle")

    await undecorate(ResultsCog.amend_toggle)(cog, toggle)

    toggle.followup.send.assert_awaited_once_with("✅ Amendment mode disabled.", ephemeral=True)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT amendment_active FROM season_amendment_state WHERE season_id = ?",
            (season_id,),
        )
        assert (await cursor.fetchone())[0] == 0


async def test_a_revert_with_nothing_staged_records_that_nothing_changed(tmp_path):
    """Amendment mode is on and nothing is staged. Alex runs `/results amend revert`: told
    nothing changed, and one nothing-changed line, never a revert."""
    db_path, season_id = await _db(tmp_path, [(1, 25), (2, 18)])
    cog = _cog(db_path, season_id)
    interaction = _interaction(cog, "results amend revert")

    await undecorate(ResultsCog.amend_revert)(cog, interaction)

    replied = _replied(interaction)
    assert "nothing changed" in replied.lower()
    assert "reverted" not in replied
    [line] = _lines(cog)
    assert line.splitlines()[0] == (
        f"Alex (<@{ACTOR_ID}>) | /results amend revert | Nothing changed"
    )
    assert await _modified_flag(db_path, season_id) == 0


@pytest.mark.parametrize(
    "command", ["results config session", "results amend session"]
)
async def test_a_same_value_set_on_an_out_of_order_table_keeps_the_warning(tmp_path, command):
    """The Feature Race table gives 18 pts for a win and 25 for second, out of order. Alex
    sets second place to the 25 it already holds: told nothing changed, and still warned which
    positions are at fault."""
    db_path, season_id = await _db(tmp_path, [(1, 18), (2, 25)])
    cog = _cog(db_path, season_id)
    interaction = _interaction(cog, command)
    run = {
        "results config session": ResultsCog.config_session,
        "results amend session": ResultsCog.amend_session,
    }[command]

    await undecorate(run)(cog, interaction, CONFIG, _RACE, 2, 25)

    replied = _replied(interaction)
    assert "nothing changed" in replied.lower()
    assert "out of order" in replied
    assert "position 1 (18 pts) < position 2 (25 pts)" in replied
    [line] = _lines(cog)
    assert line.splitlines()[0] == f"Alex (<@{ACTOR_ID}>) | /{command} | Nothing changed"


@pytest.mark.parametrize(
    ("current", "requested", "stands"),
    [
        pytest.param({"points": 25}, {"points": 25}, True, id="same-value"),
        pytest.param({"points": 25}, {"points": 30}, False, id="another-value"),
        pytest.param({"points": None}, {"points": 0}, False, id="absent-is-not-zero"),
        pytest.param({}, {"points": 25}, False, id="nothing-read"),
        pytest.param(
            {"fl_points": 1, "fl_position_limit": 10}, {"fl_position_limit": 10}, True,
            id="only-what-is-asked",
        ),
        pytest.param(
            {"fl_points": 1, "fl_position_limit": None}, {"fl_position_limit": 10}, False,
            id="limit-not-set",
        ),
    ],
)
def test_the_judgement_of_values_that_stand(current, requested, stands):
    """Given the values the cog has read and the values asked for, the request changes nothing
    only where every value asked for is already held; a value not held at all is never the same
    as one worth nothing."""
    from leaguebot.results.services.points_config_service import values_stand

    assert values_stand(current, requested) is stands
