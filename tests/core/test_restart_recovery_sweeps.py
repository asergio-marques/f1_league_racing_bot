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

**A missed phase is asked of the change queue** (#439), through weather's `ask_forecast`, by
number, a mystery round's alike: the mystery rule is weather's, which asks a mystery round's
phase 1 as its notice and nothing for its phases 2 and 3, so the sweep names no format.

**Every standing review prompt is expired by definition.** The approve button's five minutes are
a `discord.ui.View` timeout held in memory; the bot was down, so they cannot have been served,
and no view exists to serve them now. Each row is therefore cleared unconditionally — there is
no "still valid" case to preserve, and a reader who added one would leave a button nothing is
listening to standing for ever.

**The review channel is *fetched*, not only read from the cache.** A cache miss and a deleted
channel are indistinguishable to `get_channel`, and the row is cleared either way — so a miss
would drop the only record of a message still standing, which is exactly what the sweep exists
to prevent.

**An abandoned amendment's lapse is asked of the change queue** (#439, slice 6b, C64). The sweep
puts nothing back, deletes nothing and writes nothing itself: it asks `results.amend.lapse` as the
bot, with the restart's wording, before the queue starts, and leaves alone an amendment whose
lapse is already in hand (core spec :133). The queue's jobs put the round back, delete the channel
and close its row with the deletion's mark, so a crash between them is resumed rather than run
again.

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
from tests.support.review_prompts import store_review_prompt

SERVER_ID = 11808
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
CHANNEL_ID = 700
MESSAGE_ID = 8800
REVIEWER_ID = 77
STARTER_ID = 4242

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
    """Run the phase sweep; one recorder per phase holds the rounds it asked the queue for."""
    get_config = AsyncMock(return_value=config or _config())
    p1, p2, p3 = AsyncMock(), AsyncMock(), AsyncMock()

    async def _ask(_queue, _db_path, round_id, phase):
        await {1: p1, 2: p2, 3: p3}[phase](round_id)

    with patch(
        "leaguebot.weather.services.weather_config_service.get_weather_pipeline_config", new=get_config
    ), patch(
        "leaguebot.weather.services.forecast_changes.ask_forecast", new=AsyncMock(side_effect=_ask)
    ):
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


async def test_a_mystery_round_is_asked_like_any_other(tmp_path):
    """A mystery round past every horizon has each phase asked by number; weather, not the
    sweep, turns phase 1 into its notice and asks nothing for phases 2 and 3."""
    db_path = await _base_db(tmp_path, "phases_mystery")
    await _seed_round(db_path, days_away=0.1, fmt="MYSTERY")

    p1, p2, p3, _ = await _phases(_stub_bot(db_path))

    assert _fired(p1) == _fired(p2) == _fired(p3) == [ROUND_ID]


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
        await store_review_prompt(
            db,
            season_id=SEASON_ID,
            channel_id=CHANNEL_ID,
            message_id=MESSAGE_ID,
            reviewer_id=REVIEWER_ID,
            posted_at="2026-02-01T00:00:00+00:00",
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
#
# The league is `review_league`'s: round 3 of Pro (round 21 here as there), FINAL, its Feature
# Race won by Lewis ahead of Max, its submission channel closed. Alex (4242) opened
# `/results rounds amend` of its Feature Race, channel 705, and the bot stopped. The restart asks
# the queue for the amendment's lapse (`results.amend.lapse`, C64), as the bot, before the queue
# starts; the queue's jobs put the round back, delete the channel, close the row and write the
# restart's line.

NO_AMEND_LAPSE_AT_RESTART = (
    "#439: the restart deletes an abandoned amendment's channel, puts its round back and writes "
    "its line itself, off the change queue, rather than asking the queue for "
    "`results.amend.lapse` as the bot"
)


async def _amend_league(tmp_path, *, amended: bool = False):
    """The league above with Alex's amendment open. *amended*: stage one was recorded, the round
    snapshotted and its Feature Race rewritten with Max ahead of Lewis."""
    from leaguebot.results.models.points_config import SessionType
    from leaguebot.results.services.result_submission_service import snapshot_before_amendment
    from tests.support.review_league import AMEND_CHANNEL, NOW, review_league

    league = await review_league(tmp_path, round_status="FINAL")
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET closed = 1, in_penalty_review = 0"
        )
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "started_by) VALUES (?, ?, '[\"FEATURE_RACE\"]', ?, ?)",
            (ROUND_ID, AMEND_CHANNEL, NOW.isoformat(), STARTER_ID),
        )
        await db.commit()
    if amended:
        await snapshot_before_amendment(league.db_path, ROUND_ID, [SessionType.FEATURE_RACE])
        async with get_connection(league.db_path) as db:
            await db.execute(
                "UPDATE race_session_results SET finishing_position = finishing_position + 10"
            )
            await db.execute(
                "UPDATE race_session_results SET finishing_position = 13 - finishing_position"
            )
            await db.commit()
    known = league.guild.get_member.side_effect

    def _member(user_id):
        if user_id != STARTER_ID:
            return known(user_id)
        alex = MagicMock(spec=discord.Member)
        alex.id = STARTER_ID
        alex.display_name = "Alex"
        alex.mention = f"<@{STARTER_ID}>"
        return alex

    league.guild.get_member.side_effect = _member
    league.guild.fetch_member = AsyncMock(side_effect=_member)
    return league


async def _restart(league) -> list[dict]:
    """The restart's sweep, then the lapses it asked of the queue."""
    from tests.support.change_queue import change_rows

    await bot_module._recover_orphaned_amend_channels(league.bot)
    return [row for row in await change_rows(league.db_path)
            if row["kind"] == "results.amend.lapse"]


async def _restart_and_run(league) -> None:
    from tests.support.change_queue import run_queue

    await _restart(league)
    await run_queue(league.bot)


def _amend_lines(league) -> list[str]:
    """The log channel's lines about the amendment, as written: the mentions the router wraps in
    backticks unwrapped, and the rule closing each entry dropped."""
    lines = []
    for line in league.bot.log_channel.sent:
        if "/results rounds amend` of round 3" not in line:
            continue
        kept = [part for part in line.splitlines() if part.strip("―")]
        lines.append("\n".join(kept).replace("`<@", "<@").replace(">`", ">"))
    return lines


async def _amend_row(league) -> dict | None:
    async with get_connection(league.db_path) as db:
        cursor = await db.execute("SELECT * FROM round_amend_channels WHERE round_id = ?",
                                  (ROUND_ID,))
        row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def _winner(league) -> int:
    from tests.support.review_league import race_rows

    rows = await race_rows(league.db_path)
    return next(driver for driver, row in rows.items() if row["finishing_position"] == 1)


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


@pytest.mark.xfail(strict=True, reason=NO_AMEND_LAPSE_AT_RESTART)
async def test_a_second_restart_leaves_a_lapse_in_hand_alone(tmp_path):
    """The bot stops again before the queue has run the lapse the first restart asked for. The
    second restart finds that lapse in hand and asks no other (core :133): one lapse, one line."""
    from tests.support.change_queue import run_queue

    league = await _amend_league(tmp_path)
    await _restart(league)

    lapses = await _restart(league)
    await run_queue(league.bot)

    assert len(lapses) == 1
    assert len(_amend_lines(league)) == 1


async def test_the_league_manager_is_told_to_re_run_the_command(tmp_path):
    """Their amendment vanished with the restart, and an empty channel list is not an
    explanation. Round 3 had nothing to put back, so once the queue has run the lapse its line
    says a restart ended it, and to re-run the command (#482)."""
    league = await _amend_league(tmp_path)

    await _restart_and_run(league)

    [line] = _amend_lines(league)
    assert "was ended by a restart" in line
    assert "re-run /results rounds amend" in line


async def test_an_amendment_with_nothing_to_put_back_says_to_re_run_results_rounds_amend(tmp_path):
    """Nothing to put back: the corrections were never entered, or the amendment had been
    approved. For the first, the line sends the manager to the command they now type; for the
    second, to the two syncs."""
    league = await _amend_league(tmp_path)

    await _restart_and_run(league)

    [line] = _amend_lines(league)
    assert (
        "  Amendment channel deleted; nothing needed putting back. If the amendment had not "
        "been approved, re-run /results rounds amend. If it had, its channels may be "
        "part-rebuilt: run /results rounds sync and /results standings sync."
    ) in line.splitlines()


async def test_the_notice_names_the_round_and_the_session(tmp_path):
    """A manager with four sessions amended over an evening needs to know which one went: the
    lapse's line names the round and its division, as every record names them (#482), and the
    session beneath."""
    league = await _amend_league(tmp_path)

    await _restart_and_run(league)

    [line] = _amend_lines(league)
    assert "round 3 (Pro)" in line
    assert "Feature Race" in line


async def test_an_amendment_cannot_outlive_its_round(tmp_path):
    """`round_amend_channels.round_id` carries `ON DELETE CASCADE`, so a round deleted while an
    amendment was open takes the amendment's row with it: the restart finds nothing, asks no
    lapse, and the log channel is told nothing."""
    from tests.support.change_queue import run_queue

    league = await _amend_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute("DELETE FROM rounds WHERE id = ?", (ROUND_ID,))
        await db.commit()
    assert await _amend_row(league) is None

    lapses = await _restart(league)
    await run_queue(league.bot)

    assert lapses == []
    assert _amend_lines(league) == []


@pytest.mark.xfail(strict=True, reason=NO_AMEND_LAPSE_AT_RESTART)
async def test_a_channel_already_deleted_still_clears_the_row(tmp_path):
    """Someone deleted the amendment channel by hand while the bot was down. The lapse's
    deletion finds it gone and completes: the queue does not stop, the row is closed and the
    line written."""
    from tests.support.change_queue import stopped_job
    from tests.support.review_league import AMEND_CHANNEL

    league = await _amend_league(tmp_path)
    del league.channels[AMEND_CHANNEL]

    await _restart_and_run(league)

    assert await stopped_job(league.db_path) is None
    row = await _amend_row(league)
    assert row is not None and row["closed_at"] is not None
    assert len(_amend_lines(league)) == 1


async def test_a_restart_with_no_amendments_open_does_nothing(tmp_path):
    """No amendment is open: the restart asks no lapse and nothing is written."""
    from tests.support.change_queue import run_queue

    league = await _amend_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute("DELETE FROM round_amend_channels")
        await db.commit()

    lapses = await _restart(league)
    await run_queue(league.bot)

    assert lapses == []
    assert _amend_lines(league) == []


@pytest.mark.xfail(strict=True, reason=NO_AMEND_LAPSE_AT_RESTART)
async def test_an_abandoned_amendment_is_put_back_as_it_was(tmp_path):
    """Stage one commits, so abandoning an amendment is not a no-op (#345).

    Stage one of Alex's amendment wrote Max ahead of Lewis, and the bot stopped before the
    stages were approved. The restart asks the lapse and changes nothing itself; when the queue
    runs it, the round is put back with Lewis ahead, the channel deleted, and the line says the
    round was put back and to re-run the command.
    """
    from tests.support.change_queue import run_queue
    from tests.support.review_league import LEWIS, MAX

    league = await _amend_league(tmp_path, amended=True)

    await _restart(league)
    assert await _winner(league) == MAX

    await run_queue(league.bot)

    assert await _winner(league) == LEWIS
    [line] = _amend_lines(league)
    assert "put back as it was" in line
    assert "re-run /results rounds amend" in line


async def test_an_amendment_put_back_says_to_re_run_results_rounds_amend(tmp_path):
    """The round was put back, so the amendment may be run again: the line sends the manager to
    the command they now type."""
    league = await _amend_league(tmp_path, amended=True)

    await _restart_and_run(league)

    [line] = _amend_lines(league)
    assert (
        "  Amendment channel deleted, and the round put back as it was. Please re-run "
        "/results rounds amend."
    ) in line.splitlines()


async def test_an_amendment_put_back_at_a_restart_is_recorded_as_a_lapse(tmp_path):
    """Alex started `/results rounds amend` of round 3 (Pro) and entered the corrections; the
    bot restarted before the amendment was approved, and the queue's lapse puts the round back.
    The README promises this is recorded as a lapse: one line, naming Alex as its starter, with
    the session being amended, what became of it and what to do next beneath it."""
    league = await _amend_league(tmp_path, amended=True)

    await _restart_and_run(league)

    [line] = _amend_lines(league)
    head, *detail = line.splitlines()
    assert head == (
        f"⌛ `/results rounds amend` of round 3 (Pro) lapsed unconfirmed "
        f"(started by Alex (<@{STARTER_ID}>))"
    )
    assert (
        "  Amendment channel deleted, and the round put back as it was. Please re-run "
        "/results rounds amend."
    ) in detail
    assert "Feature Race" in "\n".join(detail), detail


async def test_an_amendment_with_nothing_to_put_back_is_recorded_neutrally(tmp_path):
    """Alex started `/results rounds amend` of round 3 (Pro); at the restart there is nothing to
    put back, so the bot cannot tell whether the corrections were never entered or the
    amendment had been approved. One line names Alex and says a restart ended it — never
    "lapsed" — with both cases, and what to run in each, beneath it."""
    league = await _amend_league(tmp_path)

    await _restart_and_run(league)

    [line] = _amend_lines(league)
    head = line.splitlines()[0]
    assert (
        f"`/results rounds amend` of round 3 (Pro), started by Alex (<@{STARTER_ID}>), "
        "was ended by a restart"
    ) in head
    assert "lapsed" not in line
    assert "If the amendment had not been approved, re-run /results rounds amend." in line
    assert "run /results rounds sync and /results standings sync" in line


@pytest.mark.xfail(strict=True, reason=NO_AMEND_LAPSE_AT_RESTART)
async def test_a_revert_that_fails_stops_the_queue_keeping_the_snapshot(tmp_path):
    """**The snapshot is the only way back, so a failed revert keeps it** (#345).

    Stage one of Alex's amendment is recorded, but its snapshot cannot be read back. The lapse
    the restart asked stops the league lane at its `revert` job: the row stands open with its
    snapshot, the channel is not deleted and the round is left as stage one wrote it, for Retry
    once the cause is put right.
    """
    from tests.support.change_queue import stopped_job
    from tests.support.review_league import AMEND_CHANNEL, MAX

    league = await _amend_league(tmp_path, amended=True)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE round_amend_channels SET pre_amendment_state = '{not json'")
        await db.commit()

    await _restart_and_run(league)

    stopped = await stopped_job(league.db_path, "league")
    assert stopped is not None and stopped["name"] == "revert"
    row = await _amend_row(league)
    assert row is not None and row["closed_at"] is None
    assert row["pre_amendment_state"] == "{not json"
    assert ("delete_channel", AMEND_CHANNEL, AMEND_CHANNEL) not in league.events
    assert await _winner(league) == MAX


@pytest.mark.xfail(strict=True, reason=NO_AMEND_LAPSE_AT_RESTART)
async def test_nothing_to_put_back_is_not_reported_as_a_revert(tmp_path):
    """The log must not claim a round was put back when nothing was reverted.

    Nothing to revert means the corrected results were never entered, or the amendment had been
    approved and was rebuilding the channels when the bot stopped — in which case the manager
    needs the sync commands, not a reassurance. The row is closed with its channel's deletion.
    """
    league = await _amend_league(tmp_path)

    await _restart_and_run(league)

    [line] = _amend_lines(league)
    assert "put back as it was" not in line
    assert "/results rounds sync" in line
    row = await _amend_row(league)
    assert row is not None and row["closed_at"] is not None
