"""Winding a season down on the change queue (#439, slice 5).

A season in an ongoing stage whose every division is done is wound down by a change of its own:
`wind_down` closes the signup window first; `turn_down` then turns the season's pending
placements down, moves the season on to Pending completion and writes its line, all in one save;
then each driver turned down has a job of their own: a signup in review is told (`signup_notice`)
before their channel is locked (`close_signup`), and an approved driver loses the driver role
(`take_driver_role`). A driver the window's close returns to Not Signed Up is told and locked
the same way. Every failure stops the queue.

Every test drives the real queue through `tests/support/season_league.py`, the season built by
the migrations with "now" pinned: every round final and both divisions finished, the members
holding their roles, Lewis's placement committed and Max's and Charles's not. Today the wind-down
is one job running `wind_down_ongoing`, which catches what fails, so each test is marked to fail
until the build.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import discord
import pytest

from leaguebot.core.models.change import ChangeOrigin
from tests.support.change_queue import (
    change_rows,
    discard_job,
    http_error,
    queued_log_lines,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.season_league import (
    CHARLES,
    DRIVER_ROLE,
    LEWIS,
    MAX,
    SEASON_ID,
    SIGNING_UP,
    WIND_DOWN_KIND,
    driver_state,
    pending_completion_league,
    signing_up,
    window_open,
)



# ── Helpers ─────────────────────────────────────────────────────────────────────────


async def _league(tmp_path: Any, *, stage: str = "ONGOING_PLACEMENTS",
                  signups_open: bool = False) -> Any:
    """The season at *stage*, every division done, Max's and Charles's placements pending and a
    driver (105) in review."""
    league = await pending_completion_league(tmp_path, stage=stage, signups_open=signups_open)
    await league.write(
        "UPDATE driver_season_assignments SET committed = 0 WHERE driver_profile_id IN "
        "(SELECT id FROM driver_profiles WHERE discord_user_id IN (?, ?))",
        str(MAX), str(CHARLES),
    )
    return league


async def _ask(league: Any) -> int:
    change_id = await league.bot.change_queue.ask(
        WIND_DOWN_KIND, {}, origin=ChangeOrigin.BOT, what="Winding the season down",
    )
    assert change_id is not None
    return change_id


async def _jobs(league: Any) -> list[tuple[str, int | None]]:
    """The wind-down's jobs: each one's name, and the driver it is for where it is for one."""
    rows = [row for row in await change_rows(league.db_path) if row["kind"] == WIND_DOWN_KIND]
    assert len(rows) == 1, rows
    jobs = []
    for step in await step_rows(league.db_path, rows[0]["id"]):
        payload = json.loads(step["payload"] or "{}")
        user = payload.get("user_id")
        jobs.append((step["name"], int(user) if user is not None else None))
    return jobs


async def _stage(league: Any) -> str:
    return (await league.season())["stage"]


async def _pending(league: Any) -> int:
    rows = await league.rows(
        "SELECT COUNT(*) AS n FROM driver_season_assignments WHERE season_id = ? AND committed = 0",
        SEASON_ID,
    )
    return rows[0]["n"]


async def _stopped_at(league: Any) -> tuple[str, int | None] | None:
    job = await stopped_job(league.db_path)
    if job is None:
        return None
    user = json.loads(job["payload"] or "{}").get("user_id")
    return job["name"], int(user) if user is not None else None


# ── Defects ─────────────────────────────────────────────────────────────────────────


async def test_the_turn_down_and_the_move_to_pending_completion_are_one_save(tmp_path):
    league = await _league(tmp_path)
    await _ask(league)
    from leaguebot.core.services import season_lifecycle_service

    with patch.object(season_lifecycle_service, "advance_to_pending_completion_on",
                      side_effect=RuntimeError("the move failed")):
        await run_queue(league.bot)
    assert await _pending(league) == 2
    assert await _stage(league) == "ONGOING_PLACEMENTS"
    assert await stopped_job(league.db_path) is not None

    await retry_job(league.bot)
    assert await _pending(league) == 0
    assert await _stage(league) == "PENDING_COMPLETION"


async def test_a_window_that_cannot_be_closed_stops_the_queue(tmp_path):
    league = await _league(tmp_path, stage="ONGOING_SIGNUPS", signups_open=True)
    league.close_fails = RuntimeError("the window could not be recorded closed")
    await _ask(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("wind_down", None)
    assert await _stage(league) == "ONGOING_SIGNUPS"
    assert await _pending(league) == 2

    league.close_fails = None
    await retry_job(league.bot)
    assert not await window_open(league)
    assert await _stage(league) == "PENDING_COMPLETION"


async def test_a_signup_channel_that_cannot_be_held_stops_the_queue_at_its_driver(tmp_path):
    league = await _league(tmp_path)
    await signing_up(league)
    league.hold_fails[SIGNING_UP] = http_error(discord.Forbidden, status=403,
                                               text="Missing Permissions")
    await _ask(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("signup_notice", SIGNING_UP)
    assert SIGNING_UP not in league.locked
    assert await _stage(league) == "PENDING_COMPLETION"


async def test_a_driver_role_discord_will_not_take_back_stops_the_queue(tmp_path):
    league = await _league(tmp_path)
    league.revoke_fails[MAX] = http_error(discord.Forbidden, status=403,
                                          text="Missing Permissions")
    await _ask(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("take_driver_role", MAX)

    del league.revoke_fails[MAX]
    await retry_job(league.bot)
    assert DRIVER_ROLE in league.revoked[MAX]


async def test_the_turned_down_line_is_written_with_the_save(tmp_path):
    league = await _league(tmp_path)
    league.bot.log_channel.send.side_effect = http_error(discord.Forbidden, status=403,
                                                         text="Missing Access")
    await _ask(league)
    await run_queue(league.bot)
    assert await _pending(league) == 0
    assert any("System | Every division is done | Pending placements turned down: 2" in line
               for line in await queued_log_lines(league.db_path))


# ── What it does ────────────────────────────────────────────────────────────────────


async def test_each_turned_down_driver_has_a_job_of_their_own(tmp_path):
    league = await _league(tmp_path)
    await signing_up(league)
    await _ask(league)
    await run_queue(league.bot)
    jobs = await _jobs(league)
    assert jobs[:2] == [("wind_down", None), ("turn_down", None)]
    assert sorted(jobs[2:]) == sorted([
        ("signup_notice", SIGNING_UP), ("close_signup", SIGNING_UP),
        ("take_driver_role", MAX), ("take_driver_role", CHARLES),
    ])
    assert jobs.index(("signup_notice", SIGNING_UP)) < jobs.index(("close_signup", SIGNING_UP))
    assert [uid for uid, _notice in league.notices] == [SIGNING_UP]
    assert league.locked == [SIGNING_UP]
    assert league.revoked == {MAX: [DRIVER_ROLE], CHARLES: [DRIVER_ROLE]}
    assert LEWIS not in league.revoked
    for user_id in (SIGNING_UP, MAX, CHARLES):
        assert await driver_state(league, user_id) == "NOT_SIGNED_UP"
    assert await _stage(league) == "PENDING_COMPLETION"


async def test_a_plain_ongoing_season_is_moved_on_with_no_driver_job(tmp_path):
    league = await pending_completion_league(tmp_path, stage="ONGOING")
    await _ask(league)
    await run_queue(league.bot)
    assert [name for name, _user in await _jobs(league)] == ["wind_down", "turn_down"]
    assert await _stage(league) == "PENDING_COMPLETION"
    assert league.revoked == {}


# ── A signup channel's notice, before its lock (F2) ─────────────────────────────────


async def test_a_driver_returned_by_the_wind_down_s_close_is_told_before_their_channel_is_locked(
    tmp_path,
):
    league = await _league(tmp_path, stage="ONGOING_SIGNUPS", signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    await _ask(league)
    await run_queue(league.bot)
    jobs = await _jobs(league)
    assert jobs.index(("signup_notice", SIGNING_UP)) < jobs.index(("close_signup", SIGNING_UP))
    assert [(kind, uid) for kind, uid, _n in league.events if kind in ("notice", "lock")] == [
        ("notice", SIGNING_UP), ("lock", SIGNING_UP),
    ]
    assert league.notices == [(SIGNING_UP, "🔒 Signups have closed. This channel will be "
                                           "automatically deleted in 24 hours.")]
    assert await driver_state(league, SIGNING_UP) == "NOT_SIGNED_UP"


async def test_a_discarded_signup_notice_still_closes_the_channel(tmp_path):
    league = await _league(tmp_path, stage="ONGOING_SIGNUPS", signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    league.hold_fails[SIGNING_UP] = http_error(discord.Forbidden, status=403,
                                               text="Missing Permissions")
    await _ask(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("signup_notice", SIGNING_UP)
    assert league.locked == []

    await discard_job(league.bot)
    assert league.locked == [SIGNING_UP]
    assert league.notices == []
    discard_lines = [line for line in league.bot.log_channel.sent if "Discard" in line]
    assert discard_lines and f"<@{SIGNING_UP}>" in discard_lines[-1]
