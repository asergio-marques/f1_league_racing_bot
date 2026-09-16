"""The three sweeps a restart runs over work the previous process left behind.

Issue #208. `_recover_missed_phases`, `_recover_expired_review_prompts` and
`_recover_orphaned_amend_channels` were uncovered. Each cleans up after something that lived
only in memory and died with the process — a scheduled phase, a view's timeout, a `wait_for`
loop — and between them they decide what a league finds when the bot comes back.

**A missed phase is fired against the league's own horizons, not the packaged defaults**
(issue #111). Every other path that judges whether a phase is overdue reads
`weather_pipeline_config`; a restart judging by 5/2/2 made the same league see one set of
timings on an enable and another on a restart — a longer phase 1 never published at all, a
shorter one published days early. The config is resolved once per server rather than once per
round, and only *after* the module gate, so a server with weather switched off is never queried
for it.

**A mystery round is never caught up.** Its track is not known, so there is nothing to forecast,
and the query excludes it rather than the loop skipping it — worth a test either way, because
the exclusion is invisible in the loop where a reader would look for it.

**Every standing review prompt is expired by definition.** The approve button's five minutes are
a `discord.ui.View` timeout held in memory; the bot was down, so they cannot have been served,
and no view exists to serve them now. Each row is therefore cleared unconditionally — there is
no "still valid" case to preserve, and a reader who added one would leave a button nothing is
listening to standing for ever.

**The review channel is *fetched*, not only read from the cache.** A cache miss and a deleted
channel are indistinguishable to `get_channel`, and the row is cleared either way — so a miss
would drop the only record of a message still standing, which is exactly what the sweep exists
to prevent.

**The amend row goes before the channel does.** A further crash between the two must not leave
the row to be processed again; the channel is recoverable by hand and a loop that deletes the
same channel on every restart is not. That ordering is the reverse of the submission-channel
sweep's and is deliberate.

**Every sweep announces itself.** A league manager whose amendment vanished needs to know to
re-run the command, and a reviewer whose approve button disappeared needs to know to review
again. Neither is discoverable from the absence of a message.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import bot as bot_module  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 11808
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
CHANNEL_ID = 700
MESSAGE_ID = 8800
REVIEWER_ID = 77

PHASE_1_DAYS = 7
PHASE_2_DAYS = 3
PHASE_3_HOURS = 6


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _base_db(tmp_path, name: str, *, season_status: str = "ACTIVE") -> str:
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
            "VALUES (?, ?, 1, '2026-01-01', ?)",
            (SEASON_ID, SERVER_ID, season_status),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.commit()
    return db_path


def _stub_bot(db_path: str, *, weather_enabled: bool = True, guild=None, channel=None):
    stub = MagicMock()
    stub.db_path = db_path
    stub.module_service = MagicMock()
    stub.module_service.is_weather_enabled = AsyncMock(return_value=weather_enabled)
    stub.output_router = MagicMock()
    stub.output_router.post_log = AsyncMock(return_value=None)
    stub.get_guild = MagicMock(return_value=guild)
    stub.get_channel = MagicMock(return_value=channel)
    stub.fetch_channel = AsyncMock(return_value=channel)
    return stub


# ---------------------------------------------------------------------------
# Missed weather phases
# ---------------------------------------------------------------------------


async def _seed_round(
    db_path,
    *,
    days_away: float,
    fmt: str = "NORMAL",
    p1: int = 0,
    p2: int = 0,
    p3: int = 0,
    round_id: int = ROUND_ID,
):
    at = datetime.now(timezone.utc) + timedelta(days=days_away)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "phase1_done, phase2_done, phase3_done) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (round_id, DIVISION_ID, round_id, at.isoformat(), fmt, p1, p2, p3),
        )
        await db.commit()


def _config(phase_1_days=PHASE_1_DAYS, phase_2_days=PHASE_2_DAYS, phase_3_hours=PHASE_3_HOURS):
    return SimpleNamespace(
        server_id=SERVER_ID,
        phase_1_days=phase_1_days,
        phase_2_days=phase_2_days,
        phase_3_hours=phase_3_hours,
    )


async def _phases(stub, *, config=None):
    get_config = AsyncMock(return_value=config or _config())
    with patch(
        "services.weather_config_service.get_weather_pipeline_config", new=get_config
    ), patch("services.phase1_service.run_phase1", new=AsyncMock()) as p1, patch(
        "services.phase2_service.run_phase2", new=AsyncMock()
    ) as p2, patch(
        "services.phase3_service.run_phase3", new=AsyncMock()
    ) as p3:
        await bot_module._recover_missed_phases(stub)
    return p1, p2, p3, get_config


def _fired(stub_call) -> list[int]:
    return [call.args[0] for call in stub_call.await_args_list]


async def test_a_phase_whose_horizon_has_passed_is_fired(tmp_path):
    """The job that would have run it died with the process; without this the round simply
    never gets its forecast."""
    db_path = await _base_db(tmp_path, "phases_due")
    await _seed_round(db_path, days_away=PHASE_1_DAYS - 1)

    p1, p2, p3, _ = await _phases(_stub_bot(db_path))

    assert _fired(p1) == [ROUND_ID]
    assert _fired(p2) == []


async def test_a_phase_still_ahead_of_its_horizon_is_left_to_the_scheduler(tmp_path):
    """Firing it now would publish a forecast days early and mark the phase done."""
    db_path = await _base_db(tmp_path, "phases_early")
    await _seed_round(db_path, days_away=30)

    p1, p2, p3, _ = await _phases(_stub_bot(db_path))

    assert _fired(p1) == []
    assert _fired(p2) == []
    assert _fired(p3) == []


async def test_a_round_past_every_horizon_fires_all_three(tmp_path):
    db_path = await _base_db(tmp_path, "phases_all")
    await _seed_round(db_path, days_away=0.1)

    p1, p2, p3, _ = await _phases(_stub_bot(db_path))

    assert _fired(p1) == _fired(p2) == _fired(p3) == [ROUND_ID]


@pytest.mark.parametrize(
    "done,expected",
    [
        ({"p1": 1}, ([], [ROUND_ID], [ROUND_ID])),
        ({"p2": 1}, ([ROUND_ID], [], [ROUND_ID])),
        ({"p3": 1}, ([ROUND_ID], [ROUND_ID], [])),
    ],
)
async def test_a_phase_already_done_is_not_fired_again(tmp_path, done, expected):
    """These flags are the only thing between a restart and a second forecast for every
    round of the season."""
    db_path = await _base_db(tmp_path, f"phases_done_{list(done)[0]}")
    await _seed_round(db_path, days_away=0.1, **done)

    p1, p2, p3, _ = await _phases(_stub_bot(db_path))

    assert (_fired(p1), _fired(p2), _fired(p3)) == expected


async def test_the_horizons_are_the_leagues_own(tmp_path):
    """Issue #111: a restart judging by the packaged 5/2/2 made the same league see one set
    of timings on an enable and another on a restart."""
    db_path = await _base_db(tmp_path, "phases_horizons")
    await _seed_round(db_path, days_away=10)

    p1, _, _, _ = await _phases(_stub_bot(db_path), config=_config(phase_1_days=14))

    assert _fired(p1) == [ROUND_ID]


async def test_a_shorter_horizon_leaves_the_same_round_alone(tmp_path):
    """The other side of the comparison — and the failure mode is silence, not an error."""
    db_path = await _base_db(tmp_path, "phases_horizons_short")
    await _seed_round(db_path, days_away=10)

    p1, _, _, _ = await _phases(_stub_bot(db_path), config=_config(phase_1_days=3))

    assert _fired(p1) == []


async def test_a_server_with_weather_off_is_never_asked_for_its_horizons(tmp_path):
    """The module gate comes first deliberately: a league that has never enabled weather
    should not have its pipeline configuration read on every start-up."""
    db_path = await _base_db(tmp_path, "phases_module_off")
    await _seed_round(db_path, days_away=0.1)

    p1, _, _, get_config = await _phases(_stub_bot(db_path, weather_enabled=False))

    assert _fired(p1) == []
    get_config.assert_not_awaited()


async def test_the_horizons_are_read_once_per_server(tmp_path):
    """A season's rounds all share one configuration, and a query per round would be a
    database read for every round of every division on every start-up."""
    db_path = await _base_db(tmp_path, "phases_onceper")
    for round_id in (21, 22, 23):
        await _seed_round(db_path, days_away=0.1, round_id=round_id)

    _, _, _, get_config = await _phases(_stub_bot(db_path))

    assert get_config.await_count == 1


async def test_a_mystery_round_is_never_caught_up(tmp_path):
    """There is nothing to forecast for a track nobody knows. Excluded by the query rather
    than skipped in the loop, which is why it is worth a test of its own."""
    db_path = await _base_db(tmp_path, "phases_mystery")
    await _seed_round(db_path, days_away=0.1, fmt="MYSTERY")

    p1, p2, p3, _ = await _phases(_stub_bot(db_path))

    assert _fired(p1) == _fired(p2) == _fired(p3) == []


async def test_a_season_that_is_not_active_is_not_caught_up(tmp_path):
    """A season in setup has not started and an archived one is finished; forecasting for
    either would post into a division that is not racing."""
    db_path = await _base_db(tmp_path, "phases_setup", season_status="SETUP")
    await _seed_round(db_path, days_away=0.1)

    p1, _, _, _ = await _phases(_stub_bot(db_path))

    assert _fired(p1) == []


async def test_a_naive_scheduled_time_is_read_as_utc(tmp_path):
    """SQLite hands the column back as text with no offset, and comparing the parsed value
    to an aware "now" raises — which would abort the sweep partway through a season."""
    db_path = await _base_db(tmp_path, "phases_naive")
    naive = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format) "
            "VALUES (?, ?, 1, ?, 'NORMAL')",
            (ROUND_ID, DIVISION_ID, naive.isoformat()),
        )
        await db.commit()

    p1, _, _, _ = await _phases(_stub_bot(db_path))

    assert _fired(p1) == [ROUND_ID]


# ---------------------------------------------------------------------------
# Standing season-review prompts
# ---------------------------------------------------------------------------


async def _seed_prompt(db_path):
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_review_prompts (server_id, season_id, channel_id, "
            "message_id, reviewer_id, posted_at) "
            "VALUES (?, ?, ?, ?, ?, '2026-02-01T00:00:00+00:00')",
            (SERVER_ID, SEASON_ID, CHANNEL_ID, MESSAGE_ID, REVIEWER_ID),
        )
        await db.commit()


def _review_channel(*, fetch_fails: bool = False, send_fails: bool = False):
    channel = MagicMock()
    message = MagicMock()
    message.delete = AsyncMock()
    if fetch_fails:
        channel.fetch_message = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "gone")
        )
    else:
        channel.fetch_message = AsyncMock(return_value=message)
    channel.send = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "no") if send_fails else None
    )
    channel._message = message
    return channel


async def _prompt_rows(db_path) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) AS n FROM season_review_prompts")
        return (await cursor.fetchone())["n"]


async def test_a_standing_review_prompt_is_deleted(tmp_path):
    """Its five minutes are a view timeout held in memory: the bot was down, so they cannot
    have been served, and no view exists to serve them now."""
    db_path = await _base_db(tmp_path, "review_delete")
    await _seed_prompt(db_path)
    channel = _review_channel()

    await bot_module._recover_expired_review_prompts(_stub_bot(db_path, channel=channel))

    channel._message.delete.assert_awaited_once()


async def test_the_reviewer_is_told_the_review_expired(tmp_path):
    """They would otherwise sit watching a channel for a button that is never coming back."""
    db_path = await _base_db(tmp_path, "review_notice")
    await _seed_prompt(db_path)
    channel = _review_channel()

    await bot_module._recover_expired_review_prompts(_stub_bot(db_path, channel=channel))

    posted = str(channel.send.await_args.args[0])
    assert f"<@{REVIEWER_ID}>" in posted
    assert "/season review" in posted


async def test_the_row_is_cleared(tmp_path):
    """Otherwise the same prompt is swept on every later restart, posting the notice again
    each time."""
    db_path = await _base_db(tmp_path, "review_clear")
    await _seed_prompt(db_path)

    await bot_module._recover_expired_review_prompts(
        _stub_bot(db_path, channel=_review_channel())
    )

    assert await _prompt_rows(db_path) == 0


async def test_a_channel_missing_from_the_cache_is_fetched(tmp_path):
    """A cache miss and a deleted channel are indistinguishable to `get_channel`, and the
    row is cleared either way — so a miss would drop the only record of a message still
    standing."""
    db_path = await _base_db(tmp_path, "review_fetch")
    await _seed_prompt(db_path)
    channel = _review_channel()
    stub = _stub_bot(db_path, channel=None)
    stub.fetch_channel = AsyncMock(return_value=channel)

    await bot_module._recover_expired_review_prompts(stub)

    stub.fetch_channel.assert_awaited_once()
    channel._message.delete.assert_awaited_once()


@pytest.mark.parametrize(
    "error",
    [
        discord.NotFound(MagicMock(status=404), "gone"),
        discord.Forbidden(MagicMock(status=403), "no"),
    ],
)
async def test_a_channel_that_cannot_be_fetched_still_clears_the_row(tmp_path, error):
    """Deleted, or the bot removed from it. Either way the row has nothing left to describe."""
    db_path = await _base_db(tmp_path, f"review_unfetchable_{type(error).__name__}")
    await _seed_prompt(db_path)
    stub = _stub_bot(db_path, channel=None)
    stub.fetch_channel = AsyncMock(side_effect=error)

    await bot_module._recover_expired_review_prompts(stub)

    assert await _prompt_rows(db_path) == 0


async def test_a_message_already_gone_still_posts_the_notice(tmp_path):
    """Deleted by hand, or with something else. The reviewer still needs telling."""
    db_path = await _base_db(tmp_path, "review_msggone")
    await _seed_prompt(db_path)
    channel = _review_channel(fetch_fails=True)

    await bot_module._recover_expired_review_prompts(_stub_bot(db_path, channel=channel))

    channel.send.assert_awaited_once()
    assert await _prompt_rows(db_path) == 0


async def test_a_notice_that_cannot_be_posted_still_clears_the_row(tmp_path):
    """The message is deleted by then; leaving the row would sweep a prompt that no longer
    exists on every restart from here on."""
    db_path = await _base_db(tmp_path, "review_sendfail")
    await _seed_prompt(db_path)

    await bot_module._recover_expired_review_prompts(
        _stub_bot(db_path, channel=_review_channel(send_fails=True))
    )

    assert await _prompt_rows(db_path) == 0


async def test_a_restart_with_no_standing_prompts_does_nothing(tmp_path):
    db_path = await _base_db(tmp_path, "review_empty")
    channel = _review_channel()

    await bot_module._recover_expired_review_prompts(_stub_bot(db_path, channel=channel))

    channel.send.assert_not_awaited()


async def test_an_unreadable_prompt_table_does_not_stop_the_start_up(tmp_path):
    """This runs inside start-up; a failure to read one table must not stop the bot
    starting at all."""
    db_path = await _base_db(tmp_path, "review_unreadable")
    stub = _stub_bot(db_path)
    stub.db_path = os.path.join(str(tmp_path), "does-not-exist-at-all.db")

    await bot_module._recover_expired_review_prompts(stub)  # must not raise


# ---------------------------------------------------------------------------
# Orphaned amendment channels
# ---------------------------------------------------------------------------


async def _seed_amend(db_path):
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format) "
            "VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, server_id, channel_id, "
            "session_type, created_at) VALUES (?, ?, ?, 'FEATURE_RACE', "
            "'2026-02-01T00:00:00+00:00')",
            (ROUND_ID, SERVER_ID, CHANNEL_ID),
        )
        await db.commit()


async def _amend_rows(db_path) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) AS n FROM round_amend_channels")
        return (await cursor.fetchone())["n"]


def _amend_guild(*, channel=None):
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=channel)
    return guild


async def test_an_orphaned_amend_channel_is_deleted(tmp_path):
    """Its `wait_for` loop died with the process, so it is a private channel nothing is
    listening to and a manager could paste results into for ever."""
    db_path = await _base_db(tmp_path, "amend_delete")
    await _seed_amend(db_path)
    channel = MagicMock()
    channel.delete = AsyncMock()

    await bot_module._recover_orphaned_amend_channels(
        _stub_bot(db_path, guild=_amend_guild(channel=channel))
    )

    channel.delete.assert_awaited_once()


async def test_the_amend_row_goes_before_the_channel_does(tmp_path):
    """A further crash between the two must not leave the row to be processed again. The
    channel is recoverable by hand; a loop deleting the same channel on every restart is
    not — which is why this is the reverse of the submission sweep's order."""
    db_path = await _base_db(tmp_path, "amend_order")
    await _seed_amend(db_path)
    seen: dict[str, int] = {}

    async def _record(*_args, **_kwargs):
        seen["rows"] = await _amend_rows(db_path)

    channel = MagicMock()
    channel.delete = AsyncMock(side_effect=_record)

    await bot_module._recover_orphaned_amend_channels(
        _stub_bot(db_path, guild=_amend_guild(channel=channel))
    )

    assert seen["rows"] == 0


async def test_the_league_manager_is_told_to_re_run_the_command(tmp_path):
    """Their amendment vanished with the restart, and an empty channel list is not an
    explanation."""
    db_path = await _base_db(tmp_path, "amend_notice")
    await _seed_amend(db_path)
    stub = _stub_bot(db_path, guild=_amend_guild())

    await bot_module._recover_orphaned_amend_channels(stub)

    logged = str(stub.output_router.post_log.await_args.args[1])
    assert "restarted mid-amendment" in logged
    assert "/round results amend" in logged


async def test_the_notice_names_the_round_and_the_session(tmp_path):
    """A manager with four sessions amended over an evening needs to know which one went."""
    db_path = await _base_db(tmp_path, "amend_notice_names")
    await _seed_amend(db_path)
    stub = _stub_bot(db_path, guild=_amend_guild())

    await bot_module._recover_orphaned_amend_channels(stub)

    logged = str(stub.output_router.post_log.await_args.args[1])
    assert "R3" in logged
    assert "Feature Race" in logged


async def test_an_amendment_cannot_outlive_its_round(tmp_path):
    """`round_amend_channels.round_id` carries `ON DELETE CASCADE`, so a round deleted
    while an amendment was open takes the amend row with it and there is nothing left for
    the sweep to find. The `id={round_id}` fallback in the notice is therefore unreachable
    as the schema stands — pinned here so a reader does not take its presence as evidence
    that an orphaned amendment can survive its round."""
    db_path = await _base_db(tmp_path, "amend_cascade")
    await _seed_amend(db_path)
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM rounds WHERE id = ?", (ROUND_ID,))
        await db.commit()

    assert await _amend_rows(db_path) == 0

    stub = _stub_bot(db_path, guild=_amend_guild())
    await bot_module._recover_orphaned_amend_channels(stub)

    stub.output_router.post_log.assert_not_awaited()


async def test_a_guild_missing_from_the_cache_still_clears_the_row(tmp_path):
    db_path = await _base_db(tmp_path, "amend_noguild")
    await _seed_amend(db_path)

    await bot_module._recover_orphaned_amend_channels(_stub_bot(db_path, guild=None))

    assert await _amend_rows(db_path) == 0


async def test_a_channel_already_deleted_still_clears_the_row(tmp_path):
    db_path = await _base_db(tmp_path, "amend_nochannel")
    await _seed_amend(db_path)

    await bot_module._recover_orphaned_amend_channels(
        _stub_bot(db_path, guild=_amend_guild(channel=None))
    )

    assert await _amend_rows(db_path) == 0


async def test_a_channel_that_refuses_deletion_does_not_stop_the_sweep(tmp_path):
    db_path = await _base_db(tmp_path, "amend_delfail")
    await _seed_amend(db_path)
    channel = MagicMock()
    channel.delete = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=403), "no")
    )
    stub = _stub_bot(db_path, guild=_amend_guild(channel=channel))

    await bot_module._recover_orphaned_amend_channels(stub)

    stub.output_router.post_log.assert_awaited_once()


async def test_a_failing_notice_does_not_stop_the_start_up(tmp_path):
    """The channel is already gone by then, and the sweep has done its work."""
    db_path = await _base_db(tmp_path, "amend_logfail")
    await _seed_amend(db_path)
    stub = _stub_bot(db_path, guild=_amend_guild())
    stub.output_router.post_log = AsyncMock(side_effect=RuntimeError("no log channel"))

    await bot_module._recover_orphaned_amend_channels(stub)  # must not raise

    assert await _amend_rows(db_path) == 0


async def test_a_restart_with_no_amendments_open_does_nothing(tmp_path):
    db_path = await _base_db(tmp_path, "amend_empty")
    stub = _stub_bot(db_path, guild=_amend_guild())

    await bot_module._recover_orphaned_amend_channels(stub)

    stub.output_router.post_log.assert_not_awaited()
