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
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

import leaguebot.__main__ as bot_module
from leaguebot.core.db.database import get_connection, run_migrations

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
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', ?)",
            (SEASON_ID, season_status),
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
    stub.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    stub.db_path = db_path
    stub.module_service = MagicMock()
    stub.module_service.is_weather_enabled = AsyncMock(return_value=weather_enabled)
    stub.output_router = MagicMock()
    stub.output_router.post_log = AsyncMock(return_value=None)
    stub.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
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
        "leaguebot.weather.services.weather_config_service.get_weather_pipeline_config", new=get_config
    ), patch("leaguebot.weather.services.phase1_service.run_phase1", new=AsyncMock()) as p1, patch(
        "leaguebot.weather.services.phase2_service.run_phase2", new=AsyncMock()
    ) as p2, patch(
        "leaguebot.weather.services.phase3_service.run_phase3", new=AsyncMock()
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


async def test_the_horizons_are_read_once(tmp_path):
    """The league has one configuration, and a query per round would be a database read for
    every round of every division on every start-up."""
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
    naive = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
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
            "INSERT INTO season_review_prompts (id, season_id, channel_id, "
            "message_id, reviewer_id, posted_at) "
            "VALUES (?, ?, ?, ?, ?, '2026-02-01T00:00:00+00:00')",
            (1, SEASON_ID, CHANNEL_ID, MESSAGE_ID, REVIEWER_ID),
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
    assert "/season placements-review" in posted


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
            "INSERT INTO round_amend_channels (round_id, channel_id, "
            "session_types, created_at) VALUES (?, ?, '[\"FEATURE_RACE\"]', "
            "'2026-02-01T00:00:00+00:00')",
            (ROUND_ID, CHANNEL_ID),
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


def test_amendments_are_reverted_before_submission_channels_resume():
    """**The amend sweep runs before the submission-channel one** (#345, decided 2026-09-21).

    An amendment open at the restart has its unapproved corrections in the database, and a
    submission channel whose results were saved but never posted posts its standings from it on
    recovery. Swept the other way round, those standings would publish the corrections the
    amendment's revert then takes back. Read from the source, because the two run inside
    `on_ready` among a dozen start-up steps no test drives whole.
    """
    import inspect

    source = inspect.getsource(bot_module.main)
    amend = source.index("await _recover_orphaned_amend_channels(bot)")
    submission = source.index("await _recover_orphaned_submission_channels(bot)")
    assert amend < submission


async def test_recovery_hands_the_bot_to_the_revert(tmp_path):
    """So the standings put back settle a full tie by name (#345)."""
    db_path = await _base_db(tmp_path, "amend_hands_bot")
    await _seed_amend(db_path)
    stub = _stub_bot(db_path, guild=_amend_guild())

    with patch(
        "leaguebot.results.services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(return_value=False),
    ) as revert:
        await bot_module._recover_orphaned_amend_channels(stub)

    revert.assert_awaited_once_with(db_path, ROUND_ID, stub)


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

    logged = str(stub.output_router.post_log.await_args.args[0])
    assert "restarted mid-amendment" in logged
    assert "/round results amend" in logged


@pytest.mark.xfail(
    strict=True, reason="#462: the notice still says to re-run /round results amend"
)
async def test_an_amendment_with_nothing_to_put_back_says_to_re_run_results_rounds_amend(tmp_path):
    """Nothing to put back: the corrections were never entered, or the amendment had been
    approved. For the first, the notice sends the manager to the command they now type."""
    db_path = await _base_db(tmp_path, "amend_notice_rerun")
    await _seed_amend(db_path)
    stub = _stub_bot(db_path, guild=_amend_guild())

    with patch(
        "leaguebot.results.services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(return_value=False),
    ):
        await bot_module._recover_orphaned_amend_channels(stub)

    logged = str(stub.output_router.post_log.await_args.args[0])
    assert (
        "  Amendment channel deleted; nothing needed putting back. If the amendment had not "
        "been approved, re-run /results rounds amend. If it had, its channels may be "
        "part-rebuilt: run /results rounds sync and /results standings sync."
    ) in logged.splitlines()


async def test_the_notice_names_the_round_and_the_session(tmp_path):
    """A manager with four sessions amended over an evening needs to know which one went."""
    db_path = await _base_db(tmp_path, "amend_notice_names")
    await _seed_amend(db_path)
    stub = _stub_bot(db_path, guild=_amend_guild())

    await bot_module._recover_orphaned_amend_channels(stub)

    logged = str(stub.output_router.post_log.await_args.args[0])
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


async def test_an_abandoned_amendment_is_put_back_as_it_was(tmp_path):
    """Stage one commits, so abandoning an amendment is not a no-op (#345).

    The corrected classification is written and the round scored from it before the reports and
    appeals are reviewed. A restart between stages therefore has to *undo* it, or the round is
    left scored one way and posted another with nothing that would later notice.
    """
    db_path = await _base_db(tmp_path, "amend_revert")
    await _seed_amend(db_path)
    bot = _stub_bot(db_path, guild=_amend_guild(channel=None))

    with patch(
        "leaguebot.results.services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(return_value=True),
    ) as revert:
        await bot_module._recover_orphaned_amend_channels(bot)

    revert.assert_awaited_once()
    logged = "\n".join(
        str(call.args[0]) for call in bot.output_router.post_log.await_args_list
    )
    assert "put back as it was" in logged
    assert "re-run /round results amend" in logged


async def test_a_revert_that_fails_hands_the_round_to_the_sweep(tmp_path):
    """**The snapshot is the only way back, so a failed revert keeps it** (#345).

    Deleting the row with the channel threw the snapshot away, leaving the round half-amended
    with nothing that could ever restore it. The row is kept instead, with a deadline of now, so
    the sweep retries within minutes — and deletes the channel once it has put the round back.
    """
    db_path = await _base_db(tmp_path, "amend_revert_fails")
    await _seed_amend(db_path)
    bot = _stub_bot(db_path, guild=_amend_guild(channel=None))

    with patch(
        "leaguebot.results.services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(side_effect=RuntimeError("locked")),
    ):
        await bot_module._recover_orphaned_amend_channels(bot)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT expires_at FROM round_amend_channels")
        row = await cursor.fetchone()
    assert row is not None, "the row, and the snapshot with it, was thrown away"
    assert row["expires_at"] is not None


async def test_nothing_to_put_back_is_not_reported_as_a_revert(tmp_path):
    """The log must not claim a round was put back when nothing was reverted.

    Nothing to revert means the corrected results were never entered, or the amendment had been
    approved and was rebuilding the channels when the bot stopped — in which case the manager
    needs the sync commands, not a reassurance.
    """
    db_path = await _base_db(tmp_path, "amend_nothing_to_revert")
    await _seed_amend(db_path)
    bot = _stub_bot(db_path, guild=_amend_guild(channel=None))

    with patch(
        "leaguebot.results.services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(return_value=False),
    ):
        await bot_module._recover_orphaned_amend_channels(bot)

    logged = "\n".join(
        str(call.args[0]) for call in bot.output_router.post_log.await_args_list
    )
    assert "put back as it was" not in logged
    assert "/results rounds sync" in logged
    assert await _amend_rows(db_path) == 0


async def test_an_amendment_row_outlives_a_channel_that_could_not_be_deleted(tmp_path):
    """**The row is the one thing that names the channel** (#345).

    Restart recovery finds an orphaned amend channel by that table and no other route, so
    forgetting the row while the guild is out of cache leaks a private channel with the
    amendment's stage prompts still live in it. The snapshot was released before the rebuild
    began, so the row left standing carries nothing a sweep could act on.
    """
    from leaguebot.results.services.result_submission_service import close_submission_channel

    db_path = await _base_db(tmp_path, "close_unreachable")
    await _seed_amend(db_path)

    await close_submission_channel(CHANNEL_ID, ROUND_ID, None, db_path)

    assert await _amend_rows(db_path) == 1


async def _closed_rows(db_path) -> list[tuple[int, bool]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT channel_id, closed_at IS NOT NULL FROM round_amend_channels ORDER BY id"
        )
        return [(row[0], bool(row[1])) for row in await cursor.fetchall()]


async def test_a_channel_the_bot_may_not_delete_keeps_its_row_closed(tmp_path):
    """The approval of an amendment's last stage reaches here, the commonest way one ends. The
    row went before the delete was tried, so a channel the bot had lost the right to delete
    stood with nothing naming it; kept, closed, it holds nothing and recovery finds it."""
    from leaguebot.results.services.result_submission_service import close_submission_channel

    db_path = await _base_db(tmp_path, "close_forbidden")
    await _seed_amend(db_path)
    channel = MagicMock()
    channel.delete = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Missing Access")
    )

    await close_submission_channel(
        CHANNEL_ID, ROUND_ID, _amend_guild(channel=channel), db_path
    )

    assert await _closed_rows(db_path) == [(CHANNEL_ID, True)]


async def test_closing_one_channel_leaves_a_later_amendment_of_the_round_alone(tmp_path):
    """Closed, the old row no longer holds the round, so a fresh amendment may replace it while
    the old channel's delete is still awaited. Matched on the round alone, finishing that close
    forgot the fresh amendment — snapshot, deadline and all."""
    from leaguebot.results.services.result_submission_service import _close_amend_channel_record

    db_path = await _base_db(tmp_path, "close_scoped")
    await _seed_amend(db_path)
    fresh_channel = CHANNEL_ID + 1
    old_channel = MagicMock()

    async def _slow_delete(**_kwargs):
        # While the delete is awaited, a fresh amendment takes the round's place.
        async with get_connection(db_path) as db:
            await db.execute("DELETE FROM round_amend_channels WHERE closed_at IS NOT NULL")
            await db.execute(
                "INSERT INTO round_amend_channels (round_id, channel_id, session_types, "
                "created_at) VALUES (?, ?, '[\"FEATURE_RACE\"]', '2026-02-01T01:00:00+00:00')",
                (ROUND_ID, fresh_channel),
            )
            await db.commit()

    old_channel.delete = AsyncMock(side_effect=_slow_delete)

    await _close_amend_channel_record(db_path, ROUND_ID, CHANNEL_ID, old_channel, reason="done")

    assert await _closed_rows(db_path) == [(fresh_channel, False)]


async def test_the_amendment_row_goes_with_the_channel_it_names(tmp_path):
    from leaguebot.results.services.result_submission_service import close_submission_channel

    db_path = await _base_db(tmp_path, "close_reachable")
    await _seed_amend(db_path)
    channel = MagicMock()
    channel.delete = AsyncMock()

    await close_submission_channel(
        CHANNEL_ID, ROUND_ID, _amend_guild(channel=channel), db_path
    )

    channel.delete.assert_awaited_once()
    assert await _amend_rows(db_path) == 0
