"""Every refusal of `/results amend` toggle, revert, session, fl and fl-plimit is recorded (#482).

The rule is the core specification's "The record of what changed": a refusal is recorded as one
line naming the member, what was refused and why. Each case runs one of the commands against a
running season in a state the bot turns it away in — amendment mode off, or on with changes
staged when the manager asks to switch it off — and expects today's reply, seen by the manager
alone, and exactly one standard refusal line.

The season's points and its amendment state live in a real database built from the production
migrations, so a case holds whatever the command reads before it refuses.
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
from tests.support.undecorate import undecorate

ACTOR_ID = 4242
CONFIG = "100%"

_NOT_YET = "#482: the refusal is answered but not recorded in the log channel"


async def _db(tmp_path, amendment: str) -> tuple[str, int]:
    """A running season scored by CONFIG (Feature Race P1 25 pts, fastest lap 1 pt in the top
    10), its amendment mode *amendment*: "none" never switched on, "off" switched off again,
    "staged" on with a change staged."""
    db_path = os.path.join(str(tmp_path), "amend_refusals.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO season_points_entries (season_id, config_name, session_type, "
            "position, points) VALUES (?, ?, 'FEATURE_RACE', 1, 25)",
            (season_id, CONFIG),
        )
        await db.execute(
            "INSERT INTO season_points_fl (season_id, config_name, session_type, fl_points, "
            "fl_position_limit) VALUES (?, ?, 'FEATURE_RACE', 1, 10)",
            (season_id, CONFIG),
        )
        await db.commit()
    assert season_id is not None
    if amendment != "none":
        await enable_amendment_mode(db_path, season_id)
        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE season_amendment_state SET amendment_active = ?, modified_flag = ? "
                "WHERE season_id = ?",
                (int(amendment == "staged"), int(amendment == "staged"), season_id),
            )
            await db.commit()
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
    interaction.command.qualified_name = command
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Alex"
    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


_RACE = SimpleNamespace(value="FEATURE_RACE", name="Feature Race")


async def _toggle(cog, interaction):
    await undecorate(ResultsCog.amend_toggle)(cog, interaction)


async def _revert(cog, interaction):
    await undecorate(ResultsCog.amend_revert)(cog, interaction)


async def _session(cog, interaction):
    await undecorate(ResultsCog.amend_session)(cog, interaction, CONFIG, _RACE, 1, 30)


async def _fl(cog, interaction):
    await undecorate(ResultsCog.amend_fl)(cog, interaction, CONFIG, _RACE, 2)


async def _plimit(cog, interaction):
    await undecorate(ResultsCog.amend_fl_plimit)(cog, interaction, CONFIG, _RACE, 8)


_NOT_ACTIVE = "❌ Amendment mode is not active."
_UNCOMMITTED = (
    "❌ Cannot disable amendment mode — uncommitted changes exist. "
    "Use `/results amend revert` to discard or `/results amend review` to apply."
)


def _case(case_id, command, run, amendment, reply):
    return pytest.param(
        command, run, amendment, reply,
        id=case_id,
        marks=pytest.mark.xfail(strict=True, reason=_NOT_YET),
    )


@pytest.mark.parametrize(
    ("command", "run", "amendment", "reply"),
    [
        _case("toggle-off-with-changes-staged", "results amend toggle", _toggle, "staged", _UNCOMMITTED),
        _case("revert-never-on", "results amend revert", _revert, "none", _NOT_ACTIVE),
        _case("revert-mode-off", "results amend revert", _revert, "off", _NOT_ACTIVE),
        _case("session-mode-off", "results amend session", _session, "off", _NOT_ACTIVE),
        _case("fl-mode-off", "results amend fl", _fl, "off", _NOT_ACTIVE),
        _case("fl-plimit-mode-off", "results amend fl-plimit", _plimit, "off", _NOT_ACTIVE),
    ],
)
async def test_every_amend_refusal_is_recorded(tmp_path, command, run, amendment, reply):
    """The manager Alex is refused as today, nothing is staged or switched, and one line
    records the refusal and its reason."""
    db_path, season_id = await _db(tmp_path, amendment)
    cog = _cog(db_path, season_id)
    interaction = _interaction(cog, command)

    await run(cog, interaction)

    interaction.followup.send.assert_awaited_once_with(reply, ephemeral=True)
    reason = reply.removeprefix("❌ ")
    cog.bot.output_router.post_log.assert_awaited_once_with(
        f"⛔ `/{command}` refused for Alex (<@{ACTOR_ID}>) — {reason}"
    )
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT amendment_active, modified_flag FROM season_amendment_state "
            "WHERE season_id = ?",
            (season_id,),
        )
        row = await cursor.fetchone()
    expected = {"none": None, "off": (0, 0), "staged": (1, 1)}[amendment]
    assert (None if row is None else tuple(row)) == expected
