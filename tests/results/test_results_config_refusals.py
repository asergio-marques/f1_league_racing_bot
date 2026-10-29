"""Every refusal of the `/results config` group is recorded in the log channel (#482; #439, slice 7).

The rule is the core specification's "The record of what changed": a refusal is recorded as one
line naming the member, what was refused and why. Each case runs a `/results config` command, or
presses a button of `/results config remove`'s confirmation, in a way the bot turns away, and
expects today's reply, seen by the member alone, and exactly one standard refusal line.

The commands ask a change on the queue (core :125): a refusal is given at once, nothing queued,
where the state when asked already refuses it; where the state moves between the ask and the run
(the configuration removed meanwhile), the change is acknowledged and refused when it runs, the
acknowledgement updated with the same refusal.

The league is built from the production migrations with the real queue, so a case holds whatever
the command reads before it refuses: the points configuration CONFIG, and the season, where one
is named, in the status the case gives.
"""
from __future__ import annotations

from typing import Any

import pytest
from discord import app_commands

from leaguebot.core.db.database import get_connection
from leaguebot.results.cogs.results_cog import ResultsCog, _ConfirmRemoveConfigView
from leaguebot.results.models.points_config import SessionType
from tests.support import saves
from tests.support.change_queue import MEMBER_ID, change_rows, member, run_queue
from tests.support.settings_league import (
    interaction,
    log_lines,
    said,
    settings_league,
)
from tests.support.undecorate import undecorate

OTHER_ADMIN = 5151
CONFIG = "100%"
MISSING = "75%"


async def _league(tmp_path, *, season_status: str | None = "SETUP") -> Any:
    """A league, results alone on, holding the one points configuration CONFIG with no season
    built on it, and a season in *season_status* (None: none)."""
    league = await settings_league(tmp_path, modules=("results",))
    await saves.create_config(league.db_path, CONFIG)
    if season_status is not None:
        stage = "CONFIGURATION" if season_status == "SETUP" else "ONGOING"
        async with get_connection(league.db_path) as db:
            await db.execute(
                "INSERT INTO seasons (id, season_number, start_date, status, stage) "
                "VALUES (1, 1, '2026-01-01', ?, ?)",
                (season_status, stage),
            )
            await db.commit()
    return league


def _choice(session: SessionType, label: str) -> Any:
    return app_commands.Choice(name=label, value=session.value)


RACE = _choice(SessionType.FEATURE_RACE, "Feature Race")
QUALIFYING = _choice(SessionType.FEATURE_QUALIFYING, "Feature Qualifying")


def _run(method: str, *args: Any):
    async def run(league: Any, asked: Any) -> None:
        await undecorate(getattr(ResultsCog, method))(ResultsCog(league.bot), asked, *args)
    return run


async def _remove_gone_meanwhile(league: Any, asked: Any) -> None:
    """Ask the removal of CONFIG, which no season stands on; before it runs, CONFIG is deleted;
    then run the queue."""
    await undecorate(ResultsCog.config_remove)(ResultsCog(league.bot), asked, CONFIG)
    async with get_connection(league.db_path) as db:
        await db.execute("DELETE FROM points_config_store WHERE config_name = ?", (CONFIG,))
        await db.commit()
    await run_queue(league.bot)


_MENTION_REFUSAL = (
    "A mention of a member in the configuration name would notify them wherever it is "
    "posted. Remove it, then try again."
)


def _case(case_id, command, run, reply, *, season_status="SETUP", at_once=True, marks=()):
    return pytest.param(command, run, reply, season_status, at_once, id=case_id, marks=marks)


@pytest.mark.parametrize(
    ("command", "run", "reply", "season_status", "at_once"),
    [
        _case("add-name", "results config add", _run("config_add", "<@1>"),
              f"❌ {_MENTION_REFUSAL}"),
        _case(
            "add-duplicate", "results config add", _run("config_add", CONFIG),
            f"❌ A config named **{CONFIG}** already exists on this server.",
        ),
        _case(
            "add-duplicate-no-season", "results config add", _run("config_add", CONFIG),
            f"❌ A config named **{CONFIG}** already exists on this server.", season_status=None,
        ),
        _case(
            "remove-not-found", "results config remove", _run("config_remove", MISSING),
            f"❌ Config **{MISSING}** not found.",
        ),
        _case(
            "remove-gone-meanwhile", "results config remove", _remove_gone_meanwhile,
            f"❌ Config **{CONFIG}** not found.", at_once=False,
        ),
        _case(
            "session-not-found", "results config session",
            _run("config_session", MISSING, RACE, 1, 25),
            f"❌ Config **{MISSING}** not found.",
        ),
        _case(
            "fl-not-found", "results config fl", _run("config_fl", MISSING, RACE, 1),
            f"❌ Config **{MISSING}** not found.",
        ),
        _case(
            "fl-not-found-bonus-2", "results config fl", _run("config_fl", MISSING, RACE, 2),
            f"❌ Config **{MISSING}** not found.",
        ),
        _case(
            "fl-qualifying", "results config fl", _run("config_fl", CONFIG, QUALIFYING, 1),
            "❌ Fastest-lap bonus cannot be set for qualifying sessions.",
        ),
        _case(
            "fl-qualifying-bonus-2", "results config fl",
            _run("config_fl", CONFIG, QUALIFYING, 2),
            "❌ Fastest-lap bonus cannot be set for qualifying sessions.",
        ),
        _case(
            "fl-plimit-not-found", "results config fl-plimit",
            _run("config_fl_plimit", MISSING, RACE, 10),
            f"❌ Config **{MISSING}** not found.",
        ),
        _case(
            "fl-plimit-not-found-limit-8", "results config fl-plimit",
            _run("config_fl_plimit", MISSING, RACE, 8),
            f"❌ Config **{MISSING}** not found.",
        ),
        _case(
            "fl-plimit-qualifying", "results config fl-plimit",
            _run("config_fl_plimit", CONFIG, QUALIFYING, 10),
            "❌ Position limit cannot be set for qualifying sessions.",
        ),
        _case(
            "fl-plimit-qualifying-limit-8", "results config fl-plimit",
            _run("config_fl_plimit", CONFIG, QUALIFYING, 8),
            "❌ Position limit cannot be set for qualifying sessions.",
        ),
        _case(
            "append-no-season", "results config append", _run("config_append", CONFIG),
            "❌ No season found for this server.", season_status=None,
        ),
        _case(
            "append-not-in-setup", "results config append", _run("config_append", CONFIG),
            "❌ Config attachment is only allowed for seasons in SETUP.", season_status="ACTIVE",
        ),
        _case(
            "append-not-found", "results config append", _run("config_append", MISSING),
            f"❌ Config **{MISSING}** does not exist on this server, so nothing was attached. "
            "Check the spelling, or create it with `/results config add`.",
        ),
        _case(
            "detach-no-season", "results config detach", _run("config_detach", CONFIG),
            "❌ No season found for this server.", season_status=None,
        ),
        _case(
            "detach-not-in-setup", "results config detach", _run("config_detach", CONFIG),
            "❌ Config detachment is only allowed for seasons in SETUP.", season_status="ACTIVE",
        ),
    ],
)
async def test_every_config_refusal_is_recorded(tmp_path, command, run, reply, season_status,
                                                at_once):
    """The member Admin is refused as today, and one line records the refusal and its reason:
    at once, nothing queued, where the state when asked refuses it; else acknowledged, then
    refused when the change runs. A fastest-lap command on a qualifying session is refused
    apart from "config not found", so a manager who typed the wrong session is told which of
    the two went wrong."""
    league = await _league(tmp_path, season_status=season_status)
    asked = interaction(league.bot, command)

    await run(league, asked)

    if at_once:
        assert said(asked) == [reply]
        assert await change_rows(league.db_path) == []
    else:
        assert said(asked)[0].startswith("⏳ ")
        assert said(asked)[-1] == reply
    reason = reply.removeprefix("❌ ")
    assert await log_lines(league) == [
        f"⛔ `/{command}` refused for Admin (`<@{MEMBER_ID}>`) — {reason}"
    ]


@pytest.mark.parametrize("button", ["confirm", "cancel"])
async def test_a_confirmation_pressed_by_another_admin_is_recorded(tmp_path, button):
    """Admin asked to remove 100% from a season in setup; another admin, Sam, presses one of
    the confirmation's buttons. Sam is told it is not their action, nothing is removed or
    queued, and one line records the refusal naming the button, the removal it belongs to, and
    Sam."""
    league = await _league(tmp_path)
    view = _ConfirmRemoveConfigView(ResultsCog(league.bot), MEMBER_ID, CONFIG)
    pressed = interaction(league.bot, user=member(OTHER_ADMIN, "Sam", "Sam#0001"))
    label = {"confirm": "Remove it anyway", "cancel": "Cancel"}[button]

    await getattr(type(view), button)(view, pressed, type("Button", (), {"label": label})())

    assert said(pressed) == ["⛔ Not your action."]
    [line] = await log_lines(league)
    assert line.startswith("⛔ ")
    assert label in line
    assert "`/results config remove`" in line
    assert CONFIG in line
    assert line.endswith(f"refused for Sam (`<@{OTHER_ADMIN}>`) — Not your action.")
    assert await change_rows(league.db_path) == []
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM points_config_store WHERE config_name = ?", (CONFIG,)
        )
        assert (await cursor.fetchone())[0] == 1
