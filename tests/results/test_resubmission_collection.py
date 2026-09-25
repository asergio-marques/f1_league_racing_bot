"""Collecting a round's results again after a league manager presses Resubmit.

Issue #208 found `_resubmit_collection_task` 99% untested, and issue #210 found why nothing had
noticed it never worked. `test_result_resubmission.py` covers the button and stubs this task
out, and the collection tests here patched in a round context carrying `season_id` and
`round_format` — the two fields the real `_get_round_context` did not select. The task raised
`KeyError` on its first read, inside a background task nobody awaited, and the league was left
told to paste results into a channel nothing was reading.

**Every test here reads the round from the database.** Nothing patches `_get_round_context`,
so a field the task needs and the query does not select fails the whole file rather than
hiding behind a fixture. `test_resubmitting_reads_its_round_from_the_database` is the one
that names the issue.

The loop mirrors the first submission: sessions in racing order, a refused paste is answered
and the same session asked again, `CANCELLED` skips one, a fastest-lap override must name a
finisher, and the points configuration is chosen as before. It differs from the first
submission in two ways, and both are deliberate:

- **It reuses the existing channel.** The league manager pressed Resubmit inside it, and it
  already holds the review's history.
- **It hands the round back to penalty review marked as a resubmission**, so the provisional
  tables it reposts say they replace the earlier ones.

**Nothing is written until the last session is in.** A resubmission supersedes the round's
results rather than deleting them first, so each session is held in memory and
`replace_round_results` swaps them in at the end. Team agreement across sessions is checked
against those held sessions, not the stored results being replaced.

**Cancel, or a failure before the swap, returns the round to penalty review** with the results
it had, which are already posted. The announcement's Cancel button is raced against every paste
through an `asyncio.Event` rather than `View.wait()`, whose stopped future is destroyed by the
first wait cancelled on it; `test_a_paste_is_still_collected_when_nobody_presses_cancel` holds
that.

`test_pressing_resubmit_collects_and_replaces_the_results` runs the whole path from the button,
because every piece of it was tested with the next piece stubbed and the whole never worked.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
import leaguebot.results.services.penalty_wizard as pw
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.penalty_service import StagedPenalty
from leaguebot.results.services.result_submission_service import (
    ResubmissionCancelView,
    _resubmit_collection_task,
    enter_resubmit_flow,
)
from tests.support.teams import seed_team_instances

SERVER_ID = 14108
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
SUB_CHANNEL = 7100
MANAGER = 77
TEAM_ROLE = 3001

QUALI_PASTE = "1, <@101>, T3001, Soft, 1:19.000, N/A\n2, <@102>, T3001, Soft, 1:19.500, +0.500"
RACE_PASTE = "1, <@101>, T3001, 1:30:00.000, 1:20.000, N/A\n2, <@102>, T3001, +5.000, 1:21.000, N/A"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name="resubmit", fmt="NORMAL"):
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
        await seed_team_instances(db, DIVISION_ID, TEAM_ROLE, 3002, 9999)
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
            "VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', ?, 'AWAITING_REPORT_VERDICTS')",
            (ROUND_ID, DIVISION_ID, fmt),
        )
        await db.commit()
    return db_path


async def _seed_old_results(db_path, *, team_role=TEAM_ROLE):
    """The round's results as first submitted: a race won by driver 101."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO session_results (id, round_id, division_id, session_type, status) "
            "VALUES (500, ?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, team_instance_id, "
            "finishing_position) VALUES (500, 101, ?, 1)",
            (team_role,),
        )
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at, "
            "in_penalty_review, results_posted, resubmitting) "
            "VALUES (?, ?, '2026-02-01T00:00:00+00:00', 1, 1, 1)",
            (ROUND_ID, SUB_CHANNEL),
        )
        await db.commit()


def _message(content, *, bot_author=False, channel_id=SUB_CHANNEL):
    msg = MagicMock()
    msg.content = content
    msg.author = MagicMock()
    msg.author.id = MANAGER
    msg.author.bot = bot_author
    msg.channel = SimpleNamespace(id=channel_id)
    return msg


def _channel():
    channel = MagicMock()
    channel.id = SUB_CHANNEL
    sent = MagicMock()
    sent.edit = AsyncMock()
    channel.send = AsyncMock(return_value=sent)
    return channel


def _bot(db_path, pastes, *, guild=True):
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = db_path
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.config_service = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.config_service.get_server_config = AsyncMock(return_value=SimpleNamespace())
    bot.wait_for = AsyncMock(side_effect=[_message(p) for p in pastes])
    bot.get_guild = MagicMock(return_value=MagicMock() if guild else None)
    return bot


async def _run(
    bot,
    channel=None,
    *,
    configs=("Standard",),
    selected="Half",
    validation_error=None,
    validation=None,
    cancel_view=None,
    on_select=None,
):
    channel = channel if channel is not None else _channel()

    class _FakeSelect:
        def __init__(self, names, server_cfg):
            self.selected = selected

        async def wait(self):
            if on_select is not None:
                on_select()
            return None

    patches = {
        "validation": patch(
            "leaguebot.results.services.result_submission_service._build_division_validation_data",
            new=AsyncMock(
                return_value=validation
                or ({101, 102}, {TEAM_ROLE: TEAM_ROLE}, None, {101: TEAM_ROLE, 102: TEAM_ROLE}, set(), {}, {"t3001": TEAM_ROLE}),
                side_effect=validation_error,
            ),
        ),
        "configs": patch(
            "leaguebot.results.services.season_points_service.get_attached_config_names",
            new=AsyncMock(return_value=list(configs)),
        ),
        "select": patch("leaguebot.results.services.result_submission_service._ConfigSelectView", new=_FakeSelect),
        "points": patch(
            "leaguebot.results.services.result_submission_service._apply_points_in_tx",
            new=AsyncMock(return_value=True),
        ),
        "penalty": patch(
            "leaguebot.results.services.result_submission_service.enter_penalty_state", new=AsyncMock()
        ),
        "close": patch(
            "leaguebot.results.services.result_submission_service.close_submission_channel", new=AsyncMock()
        ),
    }
    started = {k: p.start() for k, p in patches.items()}
    try:
        await _resubmit_collection_task(ROUND_ID, DIVISION_ID, bot, channel, cancel_view)
    finally:
        for p in patches.values():
            p.stop()
    started["channel"] = channel
    return started


def _said(channel) -> str:
    return "\n".join(str(c.args[0]) for c in channel.send.await_args_list if c.args)


async def _resubmitting(db_path) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT resubmitting FROM round_submission_channels WHERE round_id = ?", (ROUND_ID,)
        )
        return (await cursor.fetchone())[0]


async def _sessions(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT session_type, status, config_name FROM session_results ORDER BY id"
        )
        return [tuple(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# Issue #210
# ---------------------------------------------------------------------------


async def test_resubmitting_reads_its_round_from_the_database(tmp_path):
    """**Issue #210.** The real `_get_round_context` selected neither `season_id` nor
    `round_format`, so the task raised on its first read — inside a background task nobody
    awaits — and never asked for the results the button had just deleted."""
    db_path = await _make_db(tmp_path, name="resubmit_real_context")

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    assert "Resubmitting results for **Round 3** (Pro)" in _said(stubs["channel"])
    assert [s[0] for s in await _sessions(db_path)] == ["FEATURE_QUALIFYING", "FEATURE_RACE"]
    assert stubs["penalty"].await_args.kwargs["season_id"] == SEASON_ID


# ---------------------------------------------------------------------------
# Before collection starts
# ---------------------------------------------------------------------------


async def test_no_channel_means_nothing_to_collect_in(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_nochannel")
    bot = _bot(db_path, [])

    await _resubmit_collection_task(ROUND_ID, DIVISION_ID, bot, None)  # must not raise

    bot.wait_for.assert_not_awaited()


async def test_an_unknown_round_tells_the_channel_resubmission_failed(tmp_path):
    """The round was deleted between the press and the task starting. Raising here would be
    swallowed by the background task, leaving the manager pasting into a channel nothing
    reads."""
    db_path = await _make_db(tmp_path, name="resubmit_noround")
    bot = _bot(db_path, [])
    channel = _channel()

    await _resubmit_collection_task(ROUND_ID + 1, DIVISION_ID, bot, channel)  # must not raise

    assert "Resubmission failed: this round could not be found" in _said(channel)
    bot.wait_for.assert_not_awaited()


async def test_a_guild_the_bot_is_not_in_collects_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_noguild")
    bot = _bot(db_path, [], guild=False)

    stubs = await _run(bot)

    bot.wait_for.assert_not_awaited()
    stubs["penalty"].assert_not_awaited()


async def test_failing_to_load_the_division_says_so_in_the_channel(tmp_path):
    """The manager has just been told to paste the results again; silence would leave them
    pasting into a channel nothing reads."""
    db_path = await _make_db(tmp_path, name="resubmit_novalid")
    bot = _bot(db_path, [])

    await _seed_old_results(db_path)

    stubs = await _run(bot, validation_error=RuntimeError("team service down"))

    assert "Resubmission failed: could not load division data" in _said(stubs["channel"])
    bot.wait_for.assert_not_awaited()
    # Nothing to collect with, so the round goes back to the review it came from.
    assert stubs["penalty"].await_args.kwargs["skip_results_post"] is True
    assert await _resubmitting(db_path) == 0


async def test_the_existing_channel_is_reused_and_the_resubmission_announced(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_announce")

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    opening = str(stubs["channel"].send.await_args_list[0].args[0])
    assert "Resubmitting results for **Round 3** (Pro)" in opening


# ---------------------------------------------------------------------------
# Collecting
# ---------------------------------------------------------------------------


async def test_each_session_is_saved_again(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_saved")

    await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    assert await _sessions(db_path) == [
        ("FEATURE_QUALIFYING", "ACTIVE", "Standard"),
        ("FEATURE_RACE", "ACTIVE", "Standard"),
    ]


async def test_a_sprint_round_asks_for_all_four_sessions(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_sprint", fmt="SPRINT")

    await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE, QUALI_PASTE, RACE_PASTE]))

    assert [s[0] for s in await _sessions(db_path)] == [
        "SPRINT_QUALIFYING",
        "SPRINT_RACE",
        "FEATURE_QUALIFYING",
        "FEATURE_RACE",
    ]


async def test_the_round_goes_back_to_penalty_review_as_a_resubmission(tmp_path):
    """So the provisional tables it reposts say they replace the earlier ones."""
    db_path = await _make_db(tmp_path, name="resubmit_handover")

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    stubs["penalty"].assert_awaited_once()
    assert stubs["penalty"].await_args.kwargs["is_resubmission"] is True
    assert stubs["penalty"].await_args.kwargs["season_id"] == SEASON_ID


async def test_points_are_applied_for_each_saved_session(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_points")

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    assert stubs["points"].await_count == 2


async def test_the_wait_ignores_the_bot_and_other_channels(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_check")
    bot = _bot(db_path, [QUALI_PASTE, RACE_PASTE])

    await _run(bot)

    check = bot.wait_for.await_args_list[0].kwargs["check"]
    assert check(_message("x")) is True
    assert check(_message("x", bot_author=True)) is False
    assert check(_message("x", channel_id=999)) is False


async def test_a_refused_paste_is_answered_and_the_session_asked_again(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_reject")
    bot = _bot(db_path, ["garbage", QUALI_PASTE, RACE_PASTE])

    stubs = await _run(bot)

    assert "Validation failed" in _said(stubs["channel"])
    assert bot.wait_for.await_count == 3
    assert len(await _sessions(db_path)) == 2


async def test_a_fastest_lap_override_naming_a_non_finisher_is_refused(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_fl")
    bot = _bot(db_path, [QUALI_PASTE, "FL: <@999>\n" + RACE_PASTE, RACE_PASTE])

    stubs = await _run(bot)

    assert "FL override <@999> is not in the submitted results" in _said(stubs["channel"])
    assert bot.wait_for.await_count == 3


async def test_a_session_can_be_cancelled(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_cancel_one")

    stubs = await _run(_bot(db_path, ["CANCELLED", RACE_PASTE]))

    assert (await _sessions(db_path))[0][:2] == ("FEATURE_QUALIFYING", "CANCELLED")
    stubs["penalty"].assert_awaited_once()


async def test_every_session_cancelled_closes_the_channel(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_cancel_all")

    stubs = await _run(_bot(db_path, ["cancelled", "CANCELLED"]))

    stubs["close"].assert_awaited_once()
    stubs["penalty"].assert_not_awaited()


# ---------------------------------------------------------------------------
# The points configuration
# ---------------------------------------------------------------------------


async def test_a_single_configuration_is_chosen_without_asking(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_cfg_one")

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]), configs=("Standard",))

    assert "Auto-selected config **Standard**" in _said(stubs["channel"])


async def test_several_configurations_are_offered_to_choose_from(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_cfg_many")

    stubs = await _run(
        _bot(db_path, [QUALI_PASTE, RACE_PASTE]), configs=("Standard", "Half"), selected="Half"
    )

    assert "Select the points configuration" in _said(stubs["channel"])
    assert {s[2] for s in await _sessions(db_path)} == {"Half"}


async def test_no_configuration_saves_without_one(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_cfg_none")

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]), configs=())

    assert {s[2] for s in await _sessions(db_path)} == {None}
    stubs["points"].assert_not_awaited()


# ---------------------------------------------------------------------------
# The earlier results stand until the new ones replace them
# ---------------------------------------------------------------------------


async def test_the_earlier_results_stand_until_the_last_session_is_in(tmp_path):
    """Issue #210: a resubmission supersedes the round's results rather than deleting them
    first. Midway through, the round still holds exactly what it held before."""
    db_path = await _make_db(tmp_path, name="resubmit_stand")
    await _seed_old_results(db_path)
    seen_midway: list = []
    pastes = iter([QUALI_PASTE, RACE_PASTE])

    async def _wait_for(event, check):
        if not seen_midway and bot.wait_for.await_count == 2:
            seen_midway.append(await _sessions(db_path))
        return _message(next(pastes))

    bot = _bot(db_path, [])
    bot.wait_for = AsyncMock(side_effect=_wait_for)

    await _run(bot)

    assert seen_midway == [[("FEATURE_RACE", "ACTIVE", None)]]
    assert await _sessions(db_path) == [
        ("FEATURE_QUALIFYING", "ACTIVE", "Standard"),
        ("FEATURE_RACE", "ACTIVE", "Standard"),
    ]


async def test_team_agreement_is_checked_against_the_resubmission_not_the_old_results(tmp_path):
    """The stored results are the ones being replaced, and may carry exactly the wrong team
    that made the manager resubmit. Checking against them would refuse the correction."""
    db_path = await _make_db(tmp_path, name="resubmit_team_old")
    await _seed_old_results(db_path, team_role=9999)

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    assert "Validation failed" not in _said(stubs["channel"])
    stubs["penalty"].assert_awaited_once()


async def test_a_team_disagreement_within_the_resubmission_is_refused(tmp_path):
    """Driver 101 is a reserve, so no seat pins their team — only the qualifying session just
    entered does. Entering them under another team in the race is refused, and the race asked
    for again."""
    db_path = await _make_db(tmp_path, name="resubmit_team_new")
    other_team = RACE_PASTE.replace("<@101>, T3001", "<@101>, T3002")
    bot = _bot(db_path, [QUALI_PASTE, other_team, RACE_PASTE])

    stubs = await _run(
        bot, validation=({101, 102}, {TEAM_ROLE: TEAM_ROLE, 3002: 3002}, None, {102: TEAM_ROLE}, {101}, {}, {"t3001": TEAM_ROLE, "t3002": 3002})
    )

    assert "was recorded under <@&3001> in Feature Qualifying" in _said(stubs["channel"])
    assert bot.wait_for.await_count == 3


async def test_a_failed_swap_says_the_earlier_results_still_stand(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_swap_fails")
    await _seed_old_results(db_path)

    with patch(
        "leaguebot.results.services.result_submission_service.replace_round_results",
        new=AsyncMock(side_effect=RuntimeError("disk")),
    ):
        stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]))

    assert "The earlier results still stand" in _said(stubs["channel"])
    assert await _sessions(db_path) == [("FEATURE_RACE", "ACTIVE", None)]
    stubs["penalty"].assert_awaited_once()
    assert stubs["penalty"].await_args.kwargs["skip_results_post"] is True
    assert await _resubmitting(db_path) == 0


# ---------------------------------------------------------------------------
# Nothing is entered while another round of the division is being amended (#345)
# ---------------------------------------------------------------------------
#
# Decided 2026-09-21, as for the first submission: a resubmission posts its provisional standings
# from a database holding the amendment's unapproved corrections. Its pastes are refused while
# one is open — the last of them being what swaps the results in — and each session asked again.

AMENDED_ROUND = 20
AMEND_CHANNEL = 8200


async def _open_amendment(db_path):
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "status) VALUES (?, ?, 2, '2026-01-25T18:00:00+00:00', 'NORMAL', 'FINAL')",
            (AMENDED_ROUND, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
            "VALUES (?, ?, '[\"FEATURE_RACE\"]', '2026-02-01T00:00:00+00:00')",
            (AMENDED_ROUND, AMEND_CHANNEL),
        )
        await db.commit()


def _pastes_around_an_amendment(db_path, pastes, *, opens=None, ends: int, seen: list):
    """Each paste in turn; the amendment opens before paste *opens* and ends before *ends*.

    What the round held when it ended is put in *seen*.
    """
    messages = iter([_message(p) for p in pastes])
    calls = 0

    async def wait_for(*_a, **_k):
        nonlocal calls
        calls += 1
        if calls == opens:
            await _open_amendment(db_path)
        if calls == ends:
            seen.extend(await _sessions(db_path))
            async with get_connection(db_path) as db:
                await db.execute("DELETE FROM round_amend_channels")
                await db.commit()
        return next(messages)

    return AsyncMock(side_effect=wait_for)


async def test_a_resubmitted_paste_is_refused_while_another_round_is_amended(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_held")
    await _seed_old_results(db_path)
    await _open_amendment(db_path)
    bot = _bot(db_path, [])
    bot.wait_for = _pastes_around_an_amendment(
        db_path, [QUALI_PASTE, QUALI_PASTE, RACE_PASTE], ends=2, seen=[]
    )

    stubs = await _run(bot)

    said = _said(stubs["channel"])
    assert f"Round 2 of this division is being amended in <#{AMEND_CHANNEL}>" in said
    assert "Paste **" in said and "again then." in said
    assert bot.wait_for.await_count == 3
    assert [s[0] for s in await _sessions(db_path)] == ["FEATURE_QUALIFYING", "FEATURE_RACE"]
    stubs["penalty"].assert_awaited_once()


async def test_the_last_paste_refused_leaves_the_earlier_results_standing(tmp_path):
    """The last session's paste is the one that swaps the results in, so it is the commit held.

    Opened after the first session was taken, the amendment refuses the second; the round still
    holds the results it had until the race is pasted again after the amendment has ended.
    """
    db_path = await _make_db(tmp_path, name="resubmit_held_swap")
    await _seed_old_results(db_path)
    bot = _bot(db_path, [])
    seen: list = []
    bot.wait_for = _pastes_around_an_amendment(
        db_path, [QUALI_PASTE, RACE_PASTE, RACE_PASTE], opens=2, ends=3, seen=seen
    )

    stubs = await _run(bot)

    assert seen == [("FEATURE_RACE", "ACTIVE", None)]
    assert "is being amended" in _said(stubs["channel"])
    assert [s[0] for s in await _sessions(db_path)] == ["FEATURE_QUALIFYING", "FEATURE_RACE"]


async def test_cancelling_a_resubmitted_session_is_refused_while_amended(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_held_cancel")
    await _seed_old_results(db_path)
    await _open_amendment(db_path)
    bot = _bot(db_path, [])
    bot.wait_for = _pastes_around_an_amendment(
        db_path, ["CANCELLED", "CANCELLED", RACE_PASTE], ends=2, seen=[]
    )

    stubs = await _run(bot)

    assert "Type `CANCELLED` for **" in _said(stubs["channel"])
    assert (await _sessions(db_path))[0][:2] == ("FEATURE_QUALIFYING", "CANCELLED")


# ---------------------------------------------------------------------------
# Cancelling the resubmission
# ---------------------------------------------------------------------------


def _cancel_view():
    view = ResubmissionCancelView(SimpleNamespace(db_path="", bot=MagicMock()))
    view.message = MagicMock()
    view.message.edit = AsyncMock()
    return view


def _press_cancel(view, actor=MANAGER):
    view.cancelled_by = actor
    view.pressed.set()


def _cancelled_on_second_wait(db_path, view):
    """A bot whose manager pastes the qualifying session, then presses Cancel."""
    bot = _bot(db_path, [])
    calls = []

    async def _wait_for(event, check):
        calls.append(event)
        if len(calls) == 1:
            return _message(QUALI_PASTE)
        _press_cancel(view)
        await asyncio.Event().wait()  # no paste ever comes

    bot.wait_for = AsyncMock(side_effect=_wait_for)
    return bot


async def test_the_button_is_labelled_cancel():
    view = _cancel_view()

    assert [child.label for child in view.children] == ["Cancel"]


async def test_cancelling_the_resubmission_keeps_the_earlier_results(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_cancel_keep")
    await _seed_old_results(db_path)
    view = _cancel_view()

    await _run(_cancelled_on_second_wait(db_path, view), cancel_view=view)

    assert await _sessions(db_path) == [("FEATURE_RACE", "ACTIVE", None)]
    assert await _resubmitting(db_path) == 0


async def test_cancelling_the_resubmission_returns_the_round_to_penalty_review(tmp_path):
    """The results were never touched and are already posted, so the prompt comes back
    without them being posted a second time."""
    db_path = await _make_db(tmp_path, name="resubmit_cancel_review")
    await _seed_old_results(db_path)
    view = _cancel_view()

    stubs = await _run(_cancelled_on_second_wait(db_path, view), cancel_view=view)

    stubs["penalty"].assert_awaited_once()
    assert stubs["penalty"].await_args.kwargs["skip_results_post"] is True
    assert "is_resubmission" not in stubs["penalty"].await_args.kwargs
    assert "Resubmission cancelled" in _said(stubs["channel"])
    view.message.edit.assert_awaited_once_with(view=None)


async def test_cancelling_the_resubmission_is_logged(tmp_path):
    db_path = await _make_db(tmp_path, name="resubmit_cancel_log")
    await _seed_old_results(db_path)
    view = _cancel_view()
    bot = _cancelled_on_second_wait(db_path, view)

    await _run(bot, cancel_view=view)

    logged = "\n".join(str(c.args[0]) for c in bot.output_router.post_log.await_args_list)
    assert f"<@{MANAGER}> | RESULTS_RESUBMISSION | Cancelled" in logged


async def test_cancel_pressed_while_choosing_the_configuration_replaces_nothing(tmp_path):
    """The last session's configuration menu is its own wait. A cancel landing there must
    still stop the swap that would otherwise follow it."""
    db_path = await _make_db(tmp_path, name="resubmit_cancel_select")
    await _seed_old_results(db_path)
    view = _cancel_view()
    presses = []

    def _on_select():
        presses.append(1)
        if len(presses) == 2:
            _press_cancel(view)

    stubs = await _run(
        _bot(db_path, [QUALI_PASTE, RACE_PASTE]),
        configs=("Standard", "Half"),
        cancel_view=view,
        on_select=_on_select,
    )

    assert await _sessions(db_path) == [("FEATURE_RACE", "ACTIVE", None)]
    assert stubs["penalty"].await_args.kwargs["skip_results_post"] is True


async def test_a_completed_resubmission_takes_down_the_cancel_button(tmp_path):
    """Once the swap has landed there is nothing left to cancel."""
    db_path = await _make_db(tmp_path, name="resubmit_cancel_done")
    await _seed_old_results(db_path)
    view = _cancel_view()

    stubs = await _run(_bot(db_path, [QUALI_PASTE, RACE_PASTE]), cancel_view=view)

    view.message.edit.assert_awaited_once_with(view=None)
    assert stubs["penalty"].await_args.kwargs["is_resubmission"] is True


async def test_a_paste_is_still_collected_when_nobody_presses_cancel(tmp_path):
    """Each paste wins its race against the button; losing that race must not spoil the
    next one."""
    db_path = await _make_db(tmp_path, name="resubmit_cancel_idle")
    await _seed_old_results(db_path)
    view = _cancel_view()
    pastes = iter(["garbage", QUALI_PASTE, RACE_PASTE])

    async def _wait_for(event, check):
        # A paste arrives some ticks after the wait begins, as a real one does. Answered at
        # once, it would win every race before the button's side had run at all.
        for _ in range(5):
            await asyncio.sleep(0)
        return _message(next(pastes))

    bot = _bot(db_path, [])
    bot.wait_for = AsyncMock(side_effect=_wait_for)

    await _run(bot, cancel_view=view)

    assert [s[0] for s in await _sessions(db_path)] == ["FEATURE_QUALIFYING", "FEATURE_RACE"]


async def test_cancel_refuses_somebody_without_the_tier(monkeypatch):
    monkeypatch.setattr(pw, "is_league_manager", lambda config, member: False)
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.config_service = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.config_service.get_server_config = AsyncMock(return_value=MagicMock())
    view = ResubmissionCancelView(SimpleNamespace(db_path="", bot=bot))
    interaction = MagicMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = 5
    interaction.response.send_message = AsyncMock()

    await type(view).cancel_btn(view, interaction, MagicMock())

    assert view.cancelled is False
    assert not view.pressed.is_set()
    assert "Only league managers" in interaction.response.send_message.await_args.args[0]


async def test_cancel_pressed_by_a_league_manager_stops_the_resubmission(monkeypatch):
    monkeypatch.setattr(pw, "is_league_manager", lambda config, member: True)
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.config_service = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.config_service.get_server_config = AsyncMock(return_value=MagicMock())
    view = ResubmissionCancelView(SimpleNamespace(db_path="", bot=bot))
    interaction = MagicMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = MANAGER
    interaction.response.send_message = AsyncMock()

    await type(view).cancel_btn(view, interaction, MagicMock())

    assert view.cancelled_by == MANAGER
    assert view.pressed.is_set()


# ---------------------------------------------------------------------------
# From the button to the swap, with nothing between them stubbed
# ---------------------------------------------------------------------------


def _review_state(bot, *, staged=()):
    return SimpleNamespace(
        bot=bot,
        db_path=bot.db_path,
        round_id=ROUND_ID,
        division_id=DIVISION_ID,
        division_name="Pro",
        submission_channel_id=SUB_CHANNEL,
        staged=list(staged),
        staged_pardons=[],
        prompt_message_id=None,
        approval_message_id=None,
    )


def _pressed_resubmit():
    interaction = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = MANAGER
    interaction.followup.send = AsyncMock()
    return interaction


async def _press_resubmit_and_collect(bot, state):
    """Press Resubmit and wait for the collection it starts, stubbing only Discord and the
    division lookups the collection's own tests already cover."""
    with patch(
        "leaguebot.results.services.result_submission_service._build_division_validation_data",
        new=AsyncMock(
            return_value=({101, 102}, {TEAM_ROLE: TEAM_ROLE}, None, {101: TEAM_ROLE, 102: TEAM_ROLE}, set(), {}, {"t3001": TEAM_ROLE})
        ),
    ), patch(
        "leaguebot.results.services.season_points_service.get_attached_config_names",
        new=AsyncMock(return_value=["Standard"]),
    ), patch(
        "leaguebot.results.services.result_submission_service.enter_penalty_state", new=AsyncMock()
    ) as penalty:
        await enter_resubmit_flow(_pressed_resubmit(), state)
        task = next(t for t in asyncio.all_tasks() if t.get_name() == f"resubmit_r{ROUND_ID}")
        await asyncio.wait_for(task, timeout=5)
    return penalty


async def test_pressing_resubmit_collects_and_replaces_the_results(tmp_path):
    """Issue #210, end to end. The button used to delete the results and start a collection
    that raised on its first line; every part of it was tested with the next part stubbed."""
    db_path = await _make_db(tmp_path, name="resubmit_end_to_end")
    await _seed_old_results(db_path)
    channel = _channel()
    bot = _bot(db_path, [QUALI_PASTE, RACE_PASTE])
    bot.get_channel = MagicMock(return_value=channel)

    penalty = await _press_resubmit_and_collect(bot, _review_state(bot))

    assert [s[:2] for s in await _sessions(db_path)] == [
        ("FEATURE_QUALIFYING", "ACTIVE"),
        ("FEATURE_RACE", "ACTIVE"),
    ]
    assert await _resubmitting(db_path) == 0
    assert penalty.await_args.kwargs["is_resubmission"] is True


async def test_staged_penalties_stay_discarded_after_cancelling(tmp_path):
    """Resubmit logged and cleared them. Cancelling keeps the results, not the staged list —
    the review comes back empty, as the announcement's log already recorded."""
    db_path = await _make_db(tmp_path, name="resubmit_end_to_end_cancel")
    await _seed_old_results(db_path)
    channel = _channel()
    bot = _bot(db_path, [])
    bot.get_channel = MagicMock(return_value=channel)
    staged = StagedPenalty(
        driver_user_id=101, session_type=SessionType.FEATURE_RACE,
        penalty_type="TIME", penalty_seconds=5,
    )
    state = _review_state(bot, staged=[staged])

    async def _wait_for(event, check):
        view = next(
            c.kwargs["view"] for c in channel.send.await_args_list if "view" in c.kwargs
        )
        view.cancelled_by = MANAGER
        view.pressed.set()
        await asyncio.Event().wait()

    bot.wait_for = AsyncMock(side_effect=_wait_for)

    penalty = await _press_resubmit_and_collect(bot, state)

    assert state.staged == []
    assert await _sessions(db_path) == [("FEATURE_RACE", "ACTIVE", None)]
    assert penalty.await_args.kwargs["skip_results_post"] is True
