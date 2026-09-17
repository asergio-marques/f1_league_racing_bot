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
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.result_submission_service import _resubmit_collection_task  # noqa: E402

SERVER_ID = 14108
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
SUB_CHANNEL = 7100
MANAGER = 77
TEAM_ROLE = 3001

QUALI_PASTE = "1, <@101>, <@&3001>, Soft, 1:19.000, N/A\n2, <@102>, <@&3001>, Soft, 1:19.500, +0.500"
RACE_PASTE = "1, <@101>, <@&3001>, 1:30:00.000, 1:20.000, N/A\n2, <@102>, <@&3001>, +5.000, 1:21.000, N/A"


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
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 4, '2026-01-01', 'ACTIVE')",
            (SEASON_ID, SERVER_ID),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
            "VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', ?, 'AWAITING_REPORT_VERDICTS')",
            (ROUND_ID, DIVISION_ID, fmt),
        )
        await db.commit()
    return db_path


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
    bot.db_path = db_path
    bot.config_service = MagicMock()
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
):
    channel = channel if channel is not None else _channel()

    class _FakeSelect:
        def __init__(self, names, server_cfg):
            self.selected = selected

        async def wait(self):
            return None

    patches = {
        "validation": patch(
            "services.result_submission_service._build_division_validation_data",
            new=AsyncMock(
                return_value=({101, 102}, {TEAM_ROLE}, None, {101: TEAM_ROLE, 102: TEAM_ROLE}, set()),
                side_effect=validation_error,
            ),
        ),
        "configs": patch(
            "services.season_points_service.get_attached_config_names",
            new=AsyncMock(return_value=list(configs)),
        ),
        "select": patch("services.result_submission_service._ConfigSelectView", new=_FakeSelect),
        "points": patch(
            "services.result_submission_service._apply_points_from_config", new=AsyncMock()
        ),
        "penalty": patch(
            "services.result_submission_service.enter_penalty_state", new=AsyncMock()
        ),
        "close": patch(
            "services.result_submission_service.close_submission_channel", new=AsyncMock()
        ),
    }
    started = {k: p.start() for k, p in patches.items()}
    try:
        await _resubmit_collection_task(ROUND_ID, DIVISION_ID, bot, channel)
    finally:
        for p in patches.values():
            p.stop()
    started["channel"] = channel
    return started


def _said(channel) -> str:
    return "\n".join(str(c.args[0]) for c in channel.send.await_args_list if c.args)


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

    stubs = await _run(bot, validation_error=RuntimeError("team service down"))

    assert "Resubmission failed: could not load division data" in _said(stubs["channel"])
    bot.wait_for.assert_not_awaited()


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
