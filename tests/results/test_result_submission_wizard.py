"""The results submission wizard: from a round's start time to penalty review.

Issue #208. `run_result_submission_job` was three-quarters untested. Every raced round of every
season passes through it, and until now only its early exits ran. This file drives the whole
flow — the channel, the paste for each session, the points configuration and the handover —
with the real validator and the real session write, so a paste that would be refused in
production is refused here, and one that would be saved is saved.

**The round's start is what moves it off NOT_RUN, and where results are off it ends there.**
The job fires for every round at its scheduled time, whatever the modules. With results on, the
round begins waiting for them; with results off nobody will ever enter one, so the round is made
FINAL on the spot and its division reconsidered — otherwise it would stay outstanding for ever
and the season could never be completed (#154).

**Sessions are asked for in racing order, one at a time, and each must be accepted before the
next is asked.** A rejected paste is answered with every error and the same session is asked
again: the manager fixes the paste rather than losing the session. The rejected input is logged
with the reason, and so is the accepted one — the log is the only record of what was actually
typed.

**A session can be cancelled.** `CANCELLED` records the session as cancelled, says so in the
division's results channel, and moves on. If every session is cancelled there is nothing for
stewards to review, so the channel is closed instead of opening a penalty review.

**Only the league's people are listened to, and only in the submission channel.** The wait
ignores the bot's own messages and every other channel; otherwise the bot's prompt would be
read as a paste.

**A fastest-lap override must name somebody in the paste.** It decides who gets the bonus when
two laps tie, and naming a driver who did not race would award it to them.

**The points configuration is picked without asking where it can be.** One attached
configuration is used and the manager told; several are offered to choose from; none is saved
without one and warned about, rather than refusing a round whose drivers have just raced.

**Anything missing before the channel exists stops the job quietly and logs why.** A guild the
bot is not in, a division without a results channel, a channel that has since been deleted —
none of these can open a submission, and none should crash the scheduler thread.

**The last paste asks the change queue to open the penalty review** (#439, slice 2): the wizard
asks `results.review.open` rather than opening the review itself, and, since a paste answers no
interaction, says in the submission channel that the review is being opened, with its job number
and, where the queue is stopped, the job it is stopped at. The bot's queue is a real one here,
`results.review.open` standing in as a change type of this file's own, so what the wizard asks is
read from the queue's table and the review itself is left to `test_review_open_change.py`.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.round import RoundFormat
from leaguebot.results.services import result_submission_service
from leaguebot.results.services.result_submission_service import (
    get_sessions_for_format,
    run_result_submission_job,
)
from tests.support.change_queue import attach_queue, change_rows, register, run_queue, step_rows
from tests.support.teams import seed_team_instances

SERVER_ID = 14008
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
RESULTS_CHANNEL = 700
SUB_CHANNEL = 7100
MANAGER = 77
TEAM_ROLE = 3001

NOW = datetime(2026, 2, 1, 20, 0, tzinfo=timezone.utc)
REVIEW_OPEN = "results.review.open"

QUALI_PASTE = "1, <@101>, T3001, Soft, 1:19.000, N/A\n2, <@102>, T3001, Soft, 1:19.500, +0.500"
RACE_PASTE = "1, <@101>, T3001, 1:30:00.000, 1:20.000, N/A\n2, <@102>, T3001, +5.000, 1:21.000, N/A"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    name="submission_wizard",
    status="NOT_RUN",
    fmt="NORMAL",
    results_channel: int | None = RESULTS_CHANNEL,
):
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 4, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await seed_team_instances(db, DIVISION_ID, TEAM_ROLE)
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
            "VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', ?, ?)",
            (ROUND_ID, DIVISION_ID, fmt, status),
        )
        if results_channel is not None:
            await db.execute(
                "INSERT INTO division_results_config (division_id, results_channel_id) "
                "VALUES (?, ?)",
                (DIVISION_ID, results_channel),
            )
        await db.commit()
    return db_path


def _message(content, *, author_id=MANAGER, bot_author=False, channel_id=SUB_CHANNEL):
    msg = MagicMock()
    msg.content = content
    msg.author = MagicMock()
    msg.author.id = author_id
    msg.author.bot = bot_author
    msg.author.display_name = "Manager"
    msg.channel = SimpleNamespace(id=channel_id)
    return msg


def _channel(channel_id):
    channel = MagicMock()
    channel.id = channel_id
    sent = MagicMock()
    sent.edit = AsyncMock()
    channel.send = AsyncMock(return_value=sent)
    return channel


def _bot(
    db_path,
    pastes,
    *,
    results_enabled=True,
    guild=True,
    results_channel_present=True,
):
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = db_path
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=results_enabled)
    bot.config_service = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(
            interaction_channel_id=100, interaction_role_id=900, league_admin_role_id=901
        )
    )
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    messages = [_message(p) if isinstance(p, str) else p for p in pastes]
    bot.wait_for = AsyncMock(side_effect=messages)

    results = _channel(RESULTS_CHANNEL)
    g = MagicMock()
    g.get_channel = MagicMock(return_value=results if results_channel_present else None)
    g.get_role = MagicMock(side_effect=lambda rid: SimpleNamespace(id=rid))
    bot.get_guild = MagicMock(return_value=g if guild else None)
    bot._results = results
    bot._guild = g
    attach_queue(bot, db_path, now=NOW, types=[_change_type(REVIEW_OPEN)])
    return bot


def _change_type(kind: str, *, fails: bool = False) -> Any:
    """A change type of *kind* with one job, `open`, which does nothing, or, with *fails*, is
    refused by Discord and so stops the queue. It stands in for `results.review.open`, whose own
    tests are elsewhere, and as a job that stops the queue ahead of the paste."""
    from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
    from leaguebot.core.services.change_queue import ChangeType, Step, StepFailedOnDiscord

    async def act(_ctx: Any) -> Any:
        if fails:
            raise StepFailedOnDiscord("Missing Access")
        return StepResult()

    async def check(_ctx: Any) -> Any:
        return Verdict.go()

    return ChangeType(
        kind=kind,
        opening=(PlannedStep("open"),),
        steps={"open": Step("open", StepKind.ACT, act)},
        check=check,
        key=lambda payload: f"{kind}:{payload.get('round_id')}",
        doing=lambda _payload: f"Doing {kind}",
        outcome=lambda _ctx: "Done.",
    )


async def _opened(db_path) -> list[dict]:
    """The payload of every `results.review.open` asked for, in the order asked."""
    return [json.loads(row["payload"]) for row in await change_rows(db_path)
            if row["kind"] == REVIEW_OPEN]


async def _run(
    bot, *, configs=("Standard",), selected="Half", create_error=None, on_select=None
):
    """*on_select* is awaited while the points configuration is being chosen."""
    sub = _channel(SUB_CHANNEL)

    class _FakeSelect:
        def __init__(self, names, server_cfg, **_context):
            self.names = names
            self.selected = selected

        async def wait(self):
            if on_select is not None:
                await on_select()
            return None

    patches = {
        "validation": patch(
            "leaguebot.results.services.result_submission_service._build_division_validation_data",
            new=AsyncMock(return_value=({101, 102}, {TEAM_ROLE: TEAM_ROLE}, None, {101: TEAM_ROLE, 102: TEAM_ROLE}, set(), {}, {"t3001": TEAM_ROLE})),
        ),
        "create": patch(
            "leaguebot.results.services.result_submission_service.create_submission_channel",
            new=AsyncMock(return_value=sub, side_effect=create_error),
        ),
        "configs": patch(
            "leaguebot.results.services.season_points_service.get_attached_config_names",
            new=AsyncMock(return_value=list(configs)),
        ),
        "select": patch("leaguebot.results.services.result_submission_service._ConfigSelectView", new=_FakeSelect),
        "points": patch(
            "leaguebot.results.services.result_submission_service._apply_points_from_config", new=AsyncMock()
        ),
        "close": patch(
            "leaguebot.results.services.result_submission_service.close_submission_channel", new=AsyncMock()
        ),
        "refresh": patch(
            "leaguebot.core.services.season_service.SeasonService.refresh_division_status", new=AsyncMock()
        ),
    }
    if hasattr(result_submission_service, "enter_penalty_state"):
        # The handover before the review moved onto the queue (#439), kept inert while it
        # stands: the review it would open is not this file's subject.
        patches["handover"] = patch.object(
            result_submission_service, "enter_penalty_state", new=AsyncMock()
        )
    started = {k: p.start() for k, p in patches.items()}
    try:
        await run_result_submission_job(ROUND_ID, bot)
    finally:
        for p in patches.values():
            p.stop()
    started["sub"] = sub
    started["opened"] = await _opened(bot.db_path)
    return started


def _said(channel) -> str:
    return "\n".join(str(c.args[0]) for c in channel.send.await_args_list if c.args)


def _logged(bot) -> str:
    return "\n".join(str(c.args[0]) for c in bot.output_router.post_log.await_args_list)


async def _round_status(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT status FROM rounds WHERE id = ?", (ROUND_ID,))
        return (await cursor.fetchone())[0]


async def _sessions(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT session_type, status, config_name, submitted_by FROM session_results "
            "ORDER BY id"
        )
        return [tuple(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# The round's start
# ---------------------------------------------------------------------------


async def test_the_round_starts_waiting_for_results(tmp_path):
    db_path = await _make_db(tmp_path)

    await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    assert await _round_status(db_path) == "AWAITING_RESULTS"


async def test_with_results_off_the_round_ends_at_its_start(tmp_path):
    """#154: nobody will ever enter a result, so the round would otherwise stay outstanding
    for ever and the season could never be completed."""
    db_path = await _make_db(tmp_path, name="wizard_off")
    bot = _bot(db_path, [], results_enabled=False)

    stubs = await _run(bot)

    assert await _round_status(db_path) == "FINAL"
    stubs["refresh"].assert_awaited_once_with(DIVISION_ID)
    stubs["create"].assert_not_awaited()


async def test_a_round_already_moved_on_is_not_moved_back(tmp_path):
    """The write is guarded to NOT_RUN, so a job firing late for a round already in review
    cannot reset it."""
    db_path = await _make_db(tmp_path, name="wizard_moved", status="AWAITING_REPORT_VERDICTS")

    await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    assert await _round_status(db_path) == "AWAITING_REPORT_VERDICTS"


async def test_a_cancelled_round_opens_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_cancelled", status="CANCELLED")

    stubs = await _run(_bot(db_path, []))

    stubs["create"].assert_not_awaited()
    assert await _round_status(db_path) == "CANCELLED"


async def test_a_round_that_does_not_exist_opens_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_noround")
    bot = _bot(db_path, [])

    sub_patch = patch("leaguebot.results.services.result_submission_service.create_submission_channel", new=AsyncMock())
    with sub_patch as create:
        await run_result_submission_job(9999, bot)

    create.assert_not_awaited()


# ---------------------------------------------------------------------------
# What stops it before a channel exists
# ---------------------------------------------------------------------------


async def test_a_guild_the_bot_is_not_in_opens_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_noguild")

    stubs = await _run(_bot(db_path, [], guild=False))

    stubs["create"].assert_not_awaited()


async def test_a_division_without_a_results_channel_opens_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_nochan", results_channel=None)

    stubs = await _run(_bot(db_path, []))

    stubs["create"].assert_not_awaited()


async def test_a_results_channel_since_deleted_opens_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_chgone")

    stubs = await _run(_bot(db_path, [], results_channel_present=False))

    stubs["create"].assert_not_awaited()


async def test_failing_to_build_validation_data_opens_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_novalid")
    bot = _bot(db_path, [])

    with patch(
        "leaguebot.results.services.result_submission_service._build_division_validation_data",
        new=AsyncMock(side_effect=RuntimeError("team service down")),
    ), patch(
        "leaguebot.results.services.result_submission_service.create_submission_channel", new=AsyncMock()
    ) as create:
        await run_result_submission_job(ROUND_ID, bot)  # must not raise

    create.assert_not_awaited()


async def test_a_channel_discord_refuses_to_create_stops_the_job(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_createfail")
    bot = _bot(db_path, [QUALI_PASTE])

    stubs = await _run(
        bot, create_error=discord.HTTPException(MagicMock(status=403), "missing permissions")
    )

    bot.wait_for.assert_not_awaited()
    assert stubs["opened"] == []


async def test_the_channel_is_opened_to_both_league_roles(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_roles")

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    kwargs = stubs["create"].await_args.kwargs
    assert kwargs["interaction_role"].id == 900
    assert kwargs["league_admin_role"].id == 901
    assert kwargs["bot_cmd_channel_id"] == 100


# ---------------------------------------------------------------------------
# Collecting the sessions
# ---------------------------------------------------------------------------


async def test_the_opening_message_names_the_round_and_its_sessions(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_opening")

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    opening = str(stubs["sub"].send.await_args_list[0].args[0])
    assert "Round 3" in opening
    assert "Pro" in opening
    assert "Qualifying" in opening and "Race" in opening


@pytest.mark.parametrize(
    ("fmt", "label"),
    [("NORMAL", "Normal"), ("SPRINT", "Sprint"), ("MYSTERY", "Mystery"), ("ENDURANCE", "Endurance")],
)
async def test_the_opening_message_names_the_format_as_a_league_reads_it(tmp_path, fmt, label):
    """Issue #360. The format was interpolated as the enum member, which Python 3.12 prints as
    ``RoundFormat.NORMAL`` — the code name, not a word a league manager uses."""
    db_path = await _make_db(tmp_path, name=f"wizard_format_{fmt}", fmt=fmt)
    sessions = get_sessions_for_format(RoundFormat(fmt))

    stubs = await _run(_bot(db_path, ["CANCELLED"] * len(sessions)))

    opening = str(stubs["sub"].send.await_args_list[0].args[0])
    assert f"(Pro) - {label}. Sessions:" in opening
    assert "RoundFormat" not in opening
    assert fmt not in opening


async def test_the_opening_message_pings_the_interaction_role_and_not_the_division(tmp_path):
    """Issue #136. The channel is closed to the division's role, so a ping for it reached
    nobody and the league managers who enter the results were never told."""
    db_path = await _make_db(tmp_path, name="wizard_ping")

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    opening = str(stubs["sub"].send.await_args_list[0].args[0])
    assert "<@&900>" in opening
    assert "<@&555>" not in opening
    assert "<@&901>" not in opening


async def test_the_opening_message_pings_nobody_when_the_interaction_role_is_gone(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_ping_gone")
    bot = _bot(db_path, [QUALI_PASTE, RACE_PASTE])
    bot._guild.get_role = MagicMock(
        side_effect=lambda rid: None if rid == 900 else SimpleNamespace(id=rid)
    )

    stubs = await _run(bot)

    opening = str(stubs["sub"].send.await_args_list[0].args[0])
    assert "<@&" not in opening


async def test_a_normal_round_is_saved_session_by_session(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_saved")

    await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    assert await _sessions(db_path) == [
        ("FEATURE_QUALIFYING", "ACTIVE", "Standard", MANAGER),
        ("FEATURE_RACE", "ACTIVE", "Standard", MANAGER),
    ]


async def test_the_drivers_rows_are_written(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_rows")

    await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position FROM race_session_results "
            "ORDER BY finishing_position"
        )
        assert [tuple(r) for r in await cursor.fetchall()] == [(101, 1), (102, 2)]
        cursor = await db.execute("SELECT COUNT(*) FROM qualifying_session_results")
        assert (await cursor.fetchone())[0] == 2


async def test_a_sprint_round_asks_for_all_four_sessions_in_order(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_sprint", fmt="SPRINT")

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE, QUALI_PASTE, RACE_PASTE]))

    assert [s[0] for s in await _sessions(db_path)] == [
        "SPRINT_QUALIFYING",
        "SPRINT_RACE",
        "FEATURE_QUALIFYING",
        "FEATURE_RACE",
    ]
    prompts = [str(c.args[0]) for c in stubs["sub"].send.await_args_list if "Submit **" in str(c.args[0])]
    assert len(prompts) == 4


async def test_points_are_applied_for_each_saved_session(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_points")

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    assert stubs["points"].await_count == 2


async def test_the_round_is_handed_to_penalty_review(tmp_path):
    """The last paste asks for round 3's review to open, its first results published under
    "Provisional Results"; the channel stays open for the review."""
    db_path = await _make_db(tmp_path, name="wizard_handover")

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    assert len(stubs["opened"]) == 1
    payload = stubs["opened"][0]
    assert payload["round_id"] == ROUND_ID
    assert payload["label"] == "Provisional Results"
    assert payload["publish"] is True
    assert not payload.get("returning")
    stubs["close"].assert_not_awaited()


@pytest.mark.parametrize(
    "stopped",
    [pytest.param(False, id="queue_clear"), pytest.param(True, id="queue_stopped")],
)
async def test_the_last_paste_says_in_the_channel_that_the_review_is_being_opened(tmp_path, stopped):
    """A paste answers no interaction, so the channel is told: the penalty review is being
    opened, with its job number, and, where the queue is stopped, the job it is stopped at."""
    db_path = await _make_db(tmp_path, name=f"wizard_said_{stopped}")
    bot = _bot(db_path, [QUALI_PASTE, RACE_PASTE])
    blocker = None
    if stopped:
        # The stop's log line is queued and delivered through the router; its notice is not
        # this test's subject.
        bot.output_router.queue_log_on = AsyncMock(return_value=None)
        bot.output_router.deliver_queued = AsyncMock()
        register(bot, _change_type("test.blocker", fails=True))
        await bot.change_queue.ask("test.blocker", {}, what="the blocker")
        await run_queue(bot)
        blocker = (await step_rows(db_path))[0]
        assert blocker["failing_since"] is not None

    stubs = await _run(bot)

    changes = [row for row in await change_rows(db_path) if row["kind"] == REVIEW_OPEN]
    assert len(changes) == 1
    first_job = (await step_rows(db_path, changes[0]["id"]))[0]["id"]
    lines = [line for line in _said(stubs["sub"]).splitlines()
             if "penalty review" in line.lower() and "being opened" in line.lower()]
    assert len(lines) == 1
    assert f"job #{first_job}" in lines[0]
    if stopped:
        assert f"job #{blocker['id']}" in lines[0]
        assert "stopped" in lines[0]
    else:
        assert "stopped" not in lines[0]


async def test_the_wait_ignores_the_bot_and_other_channels(tmp_path):
    """Otherwise the bot's own prompt would be read as a paste."""
    db_path = await _make_db(tmp_path, name="wizard_check")
    bot = _bot(db_path, [QUALI_PASTE, RACE_PASTE])

    await _run(bot)

    check = bot.wait_for.await_args_list[0].kwargs["check"]
    assert check(_message("x")) is True
    assert check(_message("x", bot_author=True)) is False
    assert check(_message("x", channel_id=999)) is False


async def test_the_accepted_input_is_logged_verbatim(tmp_path):
    """The log is the only record of what was actually typed."""
    db_path = await _make_db(tmp_path, name="wizard_log")
    bot = _bot(db_path, [QUALI_PASTE, RACE_PASTE])

    await _run(bot)

    logged = _logged(bot)
    assert logged.count("RESULT_SUBMISSION_ACCEPTED") == 2
    assert "1:30:00.000" in logged


# ---------------------------------------------------------------------------
# A paste refused
# ---------------------------------------------------------------------------


async def test_a_rejected_paste_is_answered_and_the_session_asked_again(tmp_path):
    """The manager fixes the paste rather than losing the session."""
    db_path = await _make_db(tmp_path, name="wizard_reject")
    bot = _bot(db_path, ["1, not-a-mention, T3001, Soft, 1:19.000, N/A", QUALI_PASTE, RACE_PASTE])

    stubs = await _run(bot)

    said = _said(stubs["sub"])
    assert "Validation failed" in said
    assert "Driver must be a Discord member mention" in said
    assert bot.wait_for.await_count == 3
    assert [s[0] for s in await _sessions(db_path)] == ["FEATURE_QUALIFYING", "FEATURE_RACE"]


async def test_a_rejected_paste_is_logged_with_its_input(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_reject_log")
    bot = _bot(db_path, ["garbage", QUALI_PASTE, RACE_PASTE])

    await _run(bot)

    logged = _logged(bot)
    assert "RESULT_SUBMISSION_REJECTED" in logged
    assert "garbage" in logged


async def test_a_driver_outside_the_division_is_refused(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_outsider")
    outsider = "1, <@999>, T3001, Soft, 1:19.000, N/A\n2, <@102>, T3001, Soft, 1:19.500, +0.500"
    bot = _bot(db_path, [outsider, QUALI_PASTE, RACE_PASTE])

    stubs = await _run(bot)

    assert "Validation failed" in _said(stubs["sub"])
    assert bot.wait_for.await_count == 3


async def test_a_fastest_lap_override_naming_a_non_finisher_is_refused(tmp_path):
    """It would award the bonus to somebody who did not race."""
    db_path = await _make_db(tmp_path, name="wizard_fl_bad")
    bot = _bot(db_path, [QUALI_PASTE, "FL: <@999>\n" + RACE_PASTE, RACE_PASTE])

    stubs = await _run(bot)

    assert "FL override <@999> is not in the submitted results" in _said(stubs["sub"])
    assert bot.wait_for.await_count == 3


async def test_a_valid_fastest_lap_override_is_saved(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_fl_ok")

    await _run(_bot(db_path, [QUALI_PASTE, "FL: <@102>\n" + RACE_PASTE]))

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT fl_driver_override FROM session_results WHERE session_type = 'FEATURE_RACE'"
        )
        assert (await cursor.fetchone())[0] == 102


# ---------------------------------------------------------------------------
# Cancelled sessions
# ---------------------------------------------------------------------------


async def test_a_session_can_be_cancelled(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_cancel_one")
    bot = _bot(db_path, ["cancelled", RACE_PASTE])

    stubs = await _run(bot)

    assert (await _sessions(db_path))[0][:2] == ("FEATURE_QUALIFYING", "CANCELLED")
    assert "was cancelled" in _said(bot._results)
    assert len(stubs["opened"]) == 1


async def test_every_session_cancelled_closes_the_channel_instead_of_reviewing(tmp_path):
    """There is nothing for stewards to review."""
    db_path = await _make_db(tmp_path, name="wizard_cancel_all")

    stubs = await _run(_bot(db_path, ["CANCELLED", "CANCELLED"]))

    stubs["close"].assert_awaited_once()
    assert stubs["opened"] == []
    assert "no penalty review required" in _said(stubs["sub"])


# ---------------------------------------------------------------------------
# Choosing the points configuration
# ---------------------------------------------------------------------------


async def test_a_single_configuration_is_chosen_without_asking(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_cfg_one")

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]), configs=("Standard",))

    assert "Auto-selected config **Standard**" in _said(stubs["sub"])


async def test_several_configurations_are_offered_to_choose_from(tmp_path):
    db_path = await _make_db(tmp_path, name="wizard_cfg_many")

    stubs = await _run(
        _bot(db_path, [QUALI_PASTE, RACE_PASTE]), configs=("Standard", "Half"), selected="Half"
    )

    assert "Select the points configuration" in _said(stubs["sub"])
    assert {s[2] for s in await _sessions(db_path)} == {"Half"}


async def test_no_configuration_saves_without_one_and_warns(tmp_path):
    """Refusing a round whose drivers have just raced would be the worse outcome."""
    db_path = await _make_db(tmp_path, name="wizard_cfg_none")

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]), configs=())

    assert "No points configuration attached" in _said(stubs["sub"])
    assert {s[2] for s in await _sessions(db_path)} == {None}
    stubs["points"].assert_not_awaited()


# ---------------------------------------------------------------------------
# Nothing is entered while another round of the division is being amended (#345)
# ---------------------------------------------------------------------------
#
# Decided 2026-09-21. An amendment's first stage writes its corrections and recalculates the
# championship, publishing nothing; a first pass posts standings from the same database, so it
# would publish them. While one is open, a paste — `CANCELLED` included — is refused and the
# session asked again, the channel staying open.

AMENDED_ROUND = 20
AMEND_CHANNEL = 8200


async def _open_amendment(db_path, *, division_id=DIVISION_ID):
    async with get_connection(db_path) as db:
        if division_id != DIVISION_ID:
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, ?, 'Am', 2, 556)",
                (division_id, SEASON_ID),
            )
        await db.execute(
            "INSERT OR IGNORE INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "status) VALUES (?, ?, 2, '2026-01-25T18:00:00+00:00', 'NORMAL', 'FINAL')",
            (AMENDED_ROUND, division_id),
        )
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
            "VALUES (?, ?, '[\"FEATURE_RACE\"]', '2026-02-01T00:00:00+00:00')",
            (AMENDED_ROUND, AMEND_CHANNEL),
        )
        await db.commit()


async def _end_amendment(db_path):
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM round_amend_channels")
        await db.commit()


def _pastes_ending_the_amendment(db_path, pastes, *, before: int, seen: list):
    """Each paste in turn, the amendment ending just before paste number *before* arrives.

    What the round held at that moment is put in *seen*: nothing entered while the amendment
    was open.
    """
    messages = iter([_message(p) for p in pastes])
    calls = 0

    async def wait_for(*_a, **_k):
        nonlocal calls
        calls += 1
        if calls == before:
            seen.extend(await _sessions(db_path))
            await _end_amendment(db_path)
        return next(messages)

    return AsyncMock(side_effect=wait_for)


async def test_a_paste_is_refused_while_another_round_is_amended(tmp_path):
    db_path = await _make_db(tmp_path, name="held_paste")
    await _open_amendment(db_path)
    bot = _bot(db_path, [])
    seen: list = []
    bot.wait_for = _pastes_ending_the_amendment(
        db_path, [QUALI_PASTE, QUALI_PASTE, RACE_PASTE], before=2, seen=seen
    )

    stubs = await _run(bot)

    assert seen == []
    said = _said(stubs["sub"])
    assert f"Round 2 of this division is being amended in <#{AMEND_CHANNEL}>" in said
    assert "Paste **" in said and "again then." in said
    # Asked again, and entered once the amendment had ended.
    assert [s[0] for s in await _sessions(db_path)] == ["FEATURE_QUALIFYING", "FEATURE_RACE"]
    assert len(stubs["opened"]) == 1
    # A refused paste was never accepted, and the log does not say it was.
    assert _logged(bot).count("RESULT_SUBMISSION_ACCEPTED") == 2


async def test_cancelling_a_session_is_refused_while_another_round_is_amended(tmp_path):
    """`CANCELLED` is entered too: it records the session, and tells the results channel."""
    db_path = await _make_db(tmp_path, name="held_cancel")
    await _open_amendment(db_path)
    bot = _bot(db_path, [])
    seen: list = []
    bot.wait_for = _pastes_ending_the_amendment(
        db_path, ["CANCELLED", "CANCELLED", RACE_PASTE], before=2, seen=seen
    )

    stubs = await _run(bot)

    assert seen == []
    assert "Type `CANCELLED` for **" in _said(stubs["sub"])
    assert _said(bot._results).count("was cancelled") == 1
    assert (await _sessions(db_path))[0][:2] == ("FEATURE_QUALIFYING", "CANCELLED")


async def test_an_amendment_opened_while_the_configuration_is_chosen_still_holds(tmp_path):
    """The hold is read when the session is written, not when the paste arrives.

    Choosing a configuration waits on the manager, and an amendment can open meanwhile; read
    only on arrival, that paste would be entered — and, the round's last, post its standings
    from the amendment's corrections.
    """
    db_path = await _make_db(tmp_path, name="held_at_commit")
    bot = _bot(db_path, [])
    seen: list = []
    bot.wait_for = _pastes_ending_the_amendment(
        db_path, [QUALI_PASTE, QUALI_PASTE, RACE_PASTE], before=2, seen=seen
    )
    opened: list = []

    async def _open_once():
        if not opened:
            opened.append(True)
            await _open_amendment(db_path)

    stubs = await _run(bot, configs=("Standard", "Half"), on_select=_open_once)

    assert seen == []
    assert "is being amended" in _said(stubs["sub"])
    assert len(await _sessions(db_path)) == 2


async def test_an_amendment_in_another_division_holds_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="held_elsewhere")
    await _open_amendment(db_path, division_id=12)

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    assert len(await _sessions(db_path)) == 2
    assert "is being amended" not in _said(stubs["sub"])


# ---------------------------------------------------------------------------
# A submission closed by a cancellation (#439 slice 4b, amendment A, 2.9)
# ---------------------------------------------------------------------------

_CLOSED_BY = {
    "the round cancelled": (
        "CANCELLED",
        "⛔ Round 3 has been cancelled, so its results can no longer be entered. Nothing was saved.",
    ),
    "the submission closed, the round not cancelled": (
        "AWAITING_RESULTS",
        "⛔ Round 3's results submission has been closed, so its results can no longer be "
        "entered. Nothing was saved.",
    ),
}


def _closing_before_the_first_paste(db_path, pastes, round_status: str):
    """Each paste in turn; before the first arrives, round 3's submission is recorded closed and
    the round put in *round_status*, as a cancellation's save (or `/season cancel`'s closing
    before its cascade) leaves them while the wizard waits."""
    messages = iter([_message(p) for p in pastes])
    calls = 0

    async def wait_for(*_a, **_k):
        nonlocal calls
        calls += 1
        if calls == 1:
            async with get_connection(db_path) as db:
                await db.execute(
                    "INSERT INTO round_submission_channels (round_id, channel_id, created_at, "
                    "closed) VALUES (?, ?, '2026-02-01T18:00:00+00:00', 1)",
                    (ROUND_ID, SUB_CHANNEL),
                )
                await db.execute(
                    "UPDATE rounds SET status = ? WHERE id = ?", (round_status, ROUND_ID)
                )
                await db.commit()
        return next(messages)

    return AsyncMock(side_effect=wait_for)


@pytest.mark.parametrize("closed_by", sorted(_CLOSED_BY))
@pytest.mark.parametrize("pastes", [
    pytest.param((QUALI_PASTE, RACE_PASTE), id="results"),
    pytest.param(("CANCELLED", "CANCELLED"), id="CANCELLED"),
])
async def test_a_paste_into_a_submission_closed_by_a_cancellation_is_refused_and_nothing_is_saved(
    tmp_path, closed_by, pastes,
):
    """Round 3's submission channel is open and asks for the qualifying results. While it waits,
    a cancellation closes the submission: the round is cancelled, or (closed by `/season cancel`
    before its cascade, or by results being turned off) the round is not yet cancelled. The
    manager then pastes the qualifying results, or types `CANCELLED`. The wizard answers once in
    the channel, saying the round is cancelled or its submission closed and that nothing was
    saved, and stops collecting: no session is saved, nothing is said in the results channel,
    no review is asked, and it waits for nothing more."""
    status, refusal = _CLOSED_BY[closed_by]
    db_path = await _make_db(tmp_path, name="closed_by_cancellation")
    bot = _bot(db_path, [])
    bot.wait_for = _closing_before_the_first_paste(db_path, list(pastes), status)

    stubs = await _run(bot)

    assert _said(stubs["sub"]).count(refusal) == 1
    assert bot.wait_for.await_count == 1
    assert await _sessions(db_path) == []
    assert "was cancelled" not in _said(bot._results)
    assert stubs["opened"] == []


class _Gateway:
    """`bot.wait_for` as discord.py's own: each wait is a future that only an event dispatched to
    it resolves, and no message ever arrives. `close` does what a cancellation does to a
    submission it meets open and empty: it records round 3's submission closed, with the round in
    *round_status*, then deletes its channel, so the `guild_channel_delete` event reaches every wait
    whose check takes the channel, and the time any wait was given runs out. It does not say how
    the wizard learns of the closing, only that it has every means to."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.waits: list[tuple[str, Any, float | None, asyncio.Future]] = []
        self.waiting = asyncio.Event()

    async def wait_for(self, event, *, check=None, timeout=None):
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.waits.append((event, check, timeout, future))
        self.waiting.set()
        return await future

    async def close(self, round_status: str) -> None:
        async with get_connection(self.db_path) as db:
            await db.execute(
                "INSERT INTO round_submission_channels (round_id, channel_id, created_at, "
                "closed) VALUES (?, ?, '2026-02-01T18:00:00+00:00', 1)",
                (ROUND_ID, SUB_CHANNEL),
            )
            await db.execute("UPDATE rounds SET status = ? WHERE id = ?", (round_status, ROUND_ID))
            await db.commit()
        deleted = SimpleNamespace(id=SUB_CHANNEL)
        for event, check, timeout, future in list(self.waits):
            if future.done():
                continue
            if event == "guild_channel_delete" and (check is None or check(deleted)):
                future.set_result(deleted)
            elif timeout is not None:
                future.set_exception(asyncio.TimeoutError())

    def pending(self) -> list[str]:
        return [event for event, _c, _t, future in self.waits if not future.done()]

    def expire_first(self) -> None:
        """Run out the time of the first wait for a message still pending."""
        for event, _check, _timeout, future in self.waits:
            if event == "message" and not future.done():
                future.set_exception(asyncio.TimeoutError())
                return

    def dispatch(self, message: Any) -> int:
        """Send *message* to every wait for a message still pending whose check takes it, as
        discord.py does; gives how many heard it."""
        heard = 0
        for event, check, _timeout, future in list(self.waits):
            if event == "message" and not future.done() and (check is None or check(message)):
                future.set_result(message)
                heard += 1
        return heard


@pytest.mark.parametrize("round_status", [
    pytest.param("CANCELLED", id="the round cancelled"),
    pytest.param("AWAITING_RESULTS", id="closed by /season cancel before its cascade"),
])
async def test_a_wizard_waiting_on_a_submission_a_cancellation_closes_stops_waiting(
    tmp_path, round_status,
):
    """Round 3's submission channel is open, has accepted nothing, and is waiting for the
    qualifying results. A cancellation then closes the submission and deletes its channel: the
    round's own or its division's (the round recorded cancelled), or `/season cancel`'s, which
    closes it before its cascade cancels the round. No paste ever comes. The wizard stops waiting
    and returns, without a restart: no wait of its own is left pending, no session is saved,
    nothing is said in the results channel and no review is asked."""
    db_path = await _make_db(tmp_path, name="wait_ends_on_closing")
    bot = _bot(db_path, [])
    gateway = _Gateway(db_path)
    bot.wait_for = gateway.wait_for

    wizard = asyncio.create_task(_run(bot))
    try:
        await asyncio.wait_for(gateway.waiting.wait(), timeout=5)
        await gateway.close(round_status)
        done, _ = await asyncio.wait({wizard}, timeout=3)
        assert wizard in done, "the wizard is still waiting on a closed submission"
        stubs = wizard.result()
    finally:
        if not wizard.done():
            wizard.cancel()
            await asyncio.gather(wizard, return_exceptions=True)

    assert gateway.pending() == []
    assert await _sessions(db_path) == []
    assert "was cancelled" not in _said(bot._results)
    assert stubs["opened"] == []


async def test_a_paste_sent_while_the_wizard_rereads_its_submission_is_still_heard(
    tmp_path, monkeypatch,
):
    """Round 3's submission channel is open, has accepted nothing, and is waiting for the
    qualifying results. The first listener's time runs out, and the wizard reads whether its
    submission was closed, a read made slow. While the read is going, the manager pastes the
    qualifying results, which reach every listener then registered; the read ends, finding the
    submission open. The wizard takes the paste: the qualifying results are saved, and the race
    results after them. It says nothing of a closed submission, and while the read was held, as
    the paste was sent, at least one listener stood registered."""
    db_path = await _make_db(tmp_path, name="paste_during_reread")
    bot = _bot(db_path, [])
    gateway = _Gateway(db_path)
    bot.wait_for = gateway.wait_for
    real_read = result_submission_service.closed_by_cancellation
    reading, release = asyncio.Event(), asyncio.Event()

    def _listeners() -> int:
        return gateway.pending().count("message")

    async def _slow_read(*args: Any) -> Any:
        """The first read is held until the test releases it; any later one, as made."""
        if not reading.is_set():
            reading.set()
            await release.wait()
        return await real_read(*args)

    monkeypatch.setattr(result_submission_service, "closed_by_cancellation", _slow_read)

    async def _until(condition: Any) -> bool:
        for _ in range(60):
            if await condition():
                return True
            await asyncio.sleep(0.05)
        return False

    async def _race_asked() -> bool:
        return bool(await _sessions(db_path)) and _listeners() > 0

    wizard = asyncio.create_task(_run(bot))
    try:
        await asyncio.wait_for(gateway.waiting.wait(), timeout=5)
        gateway.expire_first()
        await asyncio.wait_for(reading.wait(), timeout=5)
        # A paste can arrive only while the read is suspended, as it is here.
        listening = _listeners()
        heard = gateway.dispatch(_message(QUALI_PASTE))
        release.set()

        assert await _until(_race_asked), "the paste sent during the read was never heard"
        gateway.dispatch(_message(RACE_PASTE))
        done, _ = await asyncio.wait({wizard}, timeout=5)
        assert wizard in done, "the wizard never finished"
        stubs = wizard.result()
    finally:
        if not wizard.done():
            wizard.cancel()
            await asyncio.gather(wizard, return_exceptions=True)

    assert heard >= 1
    assert [row[0] for row in await _sessions(db_path)] == ["FEATURE_QUALIFYING", "FEATURE_RACE"]
    assert "can no longer be entered" not in _said(stubs["sub"])
    assert listening >= 1
