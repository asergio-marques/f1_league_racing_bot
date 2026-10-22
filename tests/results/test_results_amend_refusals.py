"""Every refusal of `/results amend` toggle, revert, session, fl and fl-plimit is recorded (#482; #439, slice 7).

The rule is the core specification's "The record of what changed": a refusal is recorded as one
line naming the member, what was refused and why. Each case runs one of the commands against a
running season in a state the bot turns it away in — amendment mode off, or on with changes
staged when the manager asks to switch it off — and expects today's reply, seen by the manager
alone, and exactly one standard refusal line.

The commands ask a change on the queue, checked at the ask (core :125): where the state when
asked refuses it, the refusal is given at once and nothing is queued.

The league is built from the production migrations with the real queue, so a case holds whatever
the command reads before it refuses.
"""
from __future__ import annotations

from typing import Any

import pytest
from discord import app_commands

from leaguebot.core.db.database import get_connection
from leaguebot.results.cogs.results_cog import ResultsCog
from tests.support import saves
from tests.support.change_queue import MEMBER_ID, change_rows
from tests.support.settings_league import interaction, log_lines, said, settings_league
from tests.support.undecorate import undecorate

CONFIG = "100%"
SEASON_ID = 1


async def _league(tmp_path, amendment: str) -> Any:
    """A league, results alone on, with a running season scored by CONFIG (Feature Race P1
    25 pts, fastest lap 1 pt in the top 10), its amendment mode *amendment*: "none" never
    switched on, "off" switched off again, "staged" on with a change staged."""
    league = await settings_league(tmp_path, modules=("results",))
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE', 'ONGOING')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (SEASON_ID, CONFIG),
        )
        await db.execute(
            "INSERT INTO season_points_entries (season_id, config_name, session_type, "
            "position, points) VALUES (?, ?, 'FEATURE_RACE', 1, 25)",
            (SEASON_ID, CONFIG),
        )
        await db.execute(
            "INSERT INTO season_points_fl (season_id, config_name, session_type, fl_points, "
            "fl_position_limit) VALUES (?, ?, 'FEATURE_RACE', 1, 10)",
            (SEASON_ID, CONFIG),
        )
        await db.commit()
    if amendment != "none":
        await saves.enable_amendment_mode(league.db_path, SEASON_ID)
        async with get_connection(league.db_path) as db:
            await db.execute(
                "UPDATE season_amendment_state SET amendment_active = ?, modified_flag = ? "
                "WHERE season_id = ?",
                (int(amendment == "staged"), int(amendment == "staged"), SEASON_ID),
            )
            await db.commit()
    return league


_RACE = app_commands.Choice(name="Feature Race", value="FEATURE_RACE")


def _run(method: str, *args: Any):
    async def run(league: Any, asked: Any) -> None:
        await undecorate(getattr(ResultsCog, method))(ResultsCog(league.bot), asked, *args)
    return run


_toggle = _run("amend_toggle")
_revert = _run("amend_revert")
_session = _run("amend_session", CONFIG, _RACE, 1, 30)
_fl = _run("amend_fl", CONFIG, _RACE, 2)
_plimit = _run("amend_fl_plimit", CONFIG, _RACE, 8)
# The same three, asking for the values the store already holds: the refusal comes first
# (slice 3, decision 23), never "nothing changed".
_session_same = _run("amend_session", CONFIG, _RACE, 1, 25)
_fl_same = _run("amend_fl", CONFIG, _RACE, 1)
_plimit_same = _run("amend_fl_plimit", CONFIG, _RACE, 10)


_NOT_ACTIVE = "❌ Amendment mode is not active."
_UNCOMMITTED = (
    "❌ Cannot disable amendment mode — uncommitted changes exist. "
    "Use `/results amend revert` to discard or `/results amend review` to apply."
)


def _case(case_id, command, run, amendment, reply):
    return pytest.param(
        command, run, amendment, reply,
        id=case_id,
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
        _case("session-same-value-mode-off", "results amend session", _session_same, "off", _NOT_ACTIVE),
        _case("fl-same-value-mode-off", "results amend fl", _fl_same, "off", _NOT_ACTIVE),
        _case(
            "fl-plimit-same-value-mode-off", "results amend fl-plimit", _plimit_same, "off",
            _NOT_ACTIVE,
        ),
    ],
)
async def test_every_amend_refusal_is_recorded(tmp_path, command, run, amendment, reply):
    """The member Admin is refused at once as today, nothing is queued, staged or switched, and
    one line records the refusal and its reason."""
    league = await _league(tmp_path, amendment)
    asked = interaction(league.bot, command)

    await run(league, asked)

    assert said(asked) == [reply]
    assert await change_rows(league.db_path) == []
    reason = reply.removeprefix("❌ ")
    assert await log_lines(league) == [
        f"⛔ `/{command}` refused for Admin (`<@{MEMBER_ID}>`) — {reason}"
    ]
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT amendment_active, modified_flag FROM season_amendment_state "
            "WHERE season_id = ?",
            (SEASON_ID,),
        )
        row = await cursor.fetchone()
    expected = {"none": None, "off": (0, 0), "staged": (1, 1)}[amendment]
    assert (None if row is None else tuple(row)) == expected
