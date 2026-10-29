"""`/test-mode advance` — firing a season's next scheduled event by hand.

Issue #208. Advance is how a maintainer rehearses a whole season in an afternoon: it asks the
scheduler what would fire next and fires it now, one event at a time.

**It fires what a live season would fire, and nothing else.** The candidate comes from the
scheduler's own queued jobs, so a module that is switched off contributes nothing and advance
cannot post output a real season would not have posted. That is the module-output rule reached
from the test-mode side.

**Each phase cancels the job it just fired by hand.** Otherwise the real job stays queued and
fires again later — posting the same forecast, or opening a second submission channel for a
round already submitted. `test_a_fired_phase_cancels_its_own_job` holds it.

**An unhandled error in a phase is reported, not raised.** The maintainer is stepping through a
season and needs to know which round failed; an exception escaping into the interaction handler
would tell them only that the command failed, with the season half-advanced and no indication
where. The report is the standard failure reply and one failure line in the log channel, each
naming the phase, the division and the round (#482).

**Result submission refuses while one is already open**, and names *which* review is standing.
A round awaiting appeal verdicts has had its report verdicts settled already, so always saying
"penalty review" would send a maintainer to a review that is finished — the message reads the
round's actual status instead.

**Nothing left to advance is an answer, not a failure**, and it names the command that ends the
season — which is the thing a maintainer reaching the end of the queue actually wants next.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Aliased on import: pytest tries to collect any module-level name starting with `Test`
# as a test class, and warns that it cannot because the cog has an `__init__`.
from leaguebot.core.cogs.test_mode_cog import TestModeCog as _Cog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.round import Round, RoundFormat
from tests.support.undecorate import undecorate

SERVER_ID = 12308
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 5

#: The kinds of a forecast's change and of the clean-up a day after the round.
FORECAST = "weather.forecast"
FORECAST_CLEAN_UP = "weather.forecast.clean_up"

#: The kinds of the four check-in steps, asked of the change queue by name as a forecast's is.
CHECK_IN_CALL = "attendance.check_in.call"
CHECK_IN_LAST_NOTICE = "attendance.check_in.last_notice"
CHECK_IN_DEADLINE = "attendance.check_in.deadline"
CHECK_IN_CLEAN_UP = "attendance.check_in.clean_up"

#: The check-in runners advance called before #439, stubbed while they exist.
_CHECK_IN_RUNNERS = ("run_rsvp_notice", "run_rsvp_last_notice", "run_rsvp_deadline", "run_rsvp_cleanup")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "advance.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) "
            "VALUES (?, 900, 100, 101, 1)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (?, ?, 3, 'MYSTERY', 'Silverstone Circuit', '2026-06-01')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.commit()
    return db_path


def _entry(phase_number: int, *, job_id: str | None = "weather_p1_r5"):
    return {
        "phase_number": phase_number,
        "round_id": ROUND_ID,
        "round_number": 3,
        "division_name": "Division 1",
        "track_name": "Silverstone Circuit",
        "job_id": job_id,
    }


def _make_cog(db_path: str, *, test_mode: bool = True) -> _Cog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.config_service = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(test_mode_active=test_mode)
    )
    bot.scheduler_service = MagicMock()
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.change_queue = MagicMock()
    bot.change_queue.ask = AsyncMock(return_value=1)

    cog = _Cog.__new__(_Cog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.command.qualified_name = "test-mode advance"
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Maintainer"
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _failure_lines(cog) -> list[str]:
    """Every failure line written to the log channel."""
    return [
        str(call.args[0])
        for call in cog.bot.output_router.post_log.await_args_list
        if str(call.args[0]).startswith("❌")
    ]


def _assert_failure_recorded(cog, interaction, phase: str) -> None:
    """The maintainer got the standard failure reply and the log channel one failure line,
    each naming *phase*, the division and the round, the line the kind of fault too (#482)."""
    replied = _replied(interaction)
    assert "stopped on a fault in the bot" in replied
    assert "internal error" not in replied
    assert "Check the bot logs" not in replied
    for text in (phase, "Division 1", "Round 3"):
        assert text.lower() in replied.lower()
    lines = _failure_lines(cog)
    assert len(lines) == 1
    line = lines[0]
    assert "`/test-mode advance`" in line
    assert "failed for Maintainer (<@77>) — RuntimeError." in line
    for text in (phase, "Division 1", "Round 3"):
        assert text.lower() in line.lower()


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


def _asked(cog) -> list[tuple[str, dict]]:
    """Each change advance asked of the change queue: its kind and its payload."""
    return [
        (call.args[0], call.args[1] if len(call.args) > 1 else call.kwargs["payload"])
        for call in cog.bot.change_queue.ask.await_args_list
    ]


async def _advance(cog, interaction, entry, **patches):
    """Run advance with the queue answering *entry* and every phase service stubbed. A weather
    step has no runner to stub: advance asks the change queue for it (#439). Nor, once the
    check-in steps are asked of the queue too, has a check-in step: the check-in runners are
    stubbed only while the attendance module still has them, so that a test asserting an ask
    fails on the runner's call rather than on a post. The submission job is stubbed likewise, only
    while the results module still has it."""
    from leaguebot.attendance.services import rsvp_service

    defaults = {
        f"leaguebot.attendance.services.rsvp_service.{name}": AsyncMock(return_value=None)
        for name in _CHECK_IN_RUNNERS
        if hasattr(rsvp_service, name)
    }
    defaults |= {
        "leaguebot.results.services.result_submission_service.is_submission_open": AsyncMock(
            return_value=False
        ),
        "leaguebot.core.services.test_mode_service.round_result_status": AsyncMock(
            return_value="AWAITING_RESULTS"
        ),
    }
    from leaguebot.results.services import result_submission_service

    if hasattr(result_submission_service, "run_result_submission_job"):
        defaults["leaguebot.results.services.result_submission_service.run_result_submission_job"] = (
            AsyncMock(return_value=None)
        )
    defaults.update(patches)

    from contextlib import ExitStack

    with ExitStack() as stack:
        mocks = {}
        for target, mock in defaults.items():
            stack.enter_context(patch(target, new=mock))
            mocks[target.rsplit(".", 1)[1]] = mock
        stack.enter_context(
            patch(
                "leaguebot.core.cogs.test_mode_cog.get_next_pending_phase",
                new=AsyncMock(return_value=entry),
            )
        )
        await undecorate(_Cog.advance)(cog, interaction)
    return mocks


# ---------------------------------------------------------------------------
# The gate and the empty queue
# ---------------------------------------------------------------------------


async def test_advance_is_refused_outside_test_mode(tmp_path):
    """It fires a season's events by hand, which is not something to do to a live league."""
    cog = _make_cog(await _make_db(tmp_path), test_mode=False)
    interaction = _interaction()

    await _advance(cog, interaction, _entry(1))

    assert "Test mode is not active" in _replied(interaction)
    assert "/test-mode toggle" in _replied(interaction)


async def test_a_refused_advance_does_no_heavy_work(tmp_path):
    """The check runs before the defer, so it costs nothing at all."""
    cog = _make_cog(await _make_db(tmp_path), test_mode=False)
    interaction = _interaction()

    await _advance(cog, interaction, _entry(1))

    interaction.response.defer.assert_not_awaited()


async def test_an_empty_queue_says_so_and_names_what_comes_next(tmp_path):
    """Reaching the end of the queue is the point of the exercise, and the maintainer's
    next step is ending the season."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    await _advance(cog, interaction, None)

    replied = _replied(interaction)
    assert "nothing left to advance" in replied
    assert "/season complete" in replied


# ---------------------------------------------------------------------------
# The mystery notice
# ---------------------------------------------------------------------------


async def test_a_mystery_notice_is_posted(tmp_path):
    """Round 3 of Division 1 is a mystery round whose notice is next. Advance asks the change
    queue for its notice (a `weather.forecast` of phase 0 for the round) and says it is on the
    queue; the cog itself posts nothing."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    await _advance(cog, interaction, _entry(0))

    [(kind, payload)] = _asked(cog)
    assert kind == FORECAST
    assert (payload["round_id"], payload["phase"]) == (ROUND_ID, 0)
    assert ("🔮 The **Mystery Round notice** for **Division 1** — **Round 3** is on the change "
            "queue") in _replied(interaction)


@pytest.mark.parametrize("job_id", ["weather_p1_s1_d1_r3_id5", None], ids=["queued", "found-in-db"])
async def test_a_posted_mystery_notice_takes_its_notice_job_down(tmp_path, job_id):
    """By the round and the `weather_p1` it is armed under, whatever the entry carries. A notice
    found from database state carries no job, yet its job may still be queued — and left there it
    would post the notice a second time at its own moment, `run_mystery_notice` not asking
    whether one is up (#426)."""
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(0, job_id=job_id))

    cog.bot.scheduler_service.cancel_round.assert_called_once_with(
        ROUND_ID, only=frozenset({"weather_p1"})
    )
    cog.bot.scheduler_service.cancel_job.assert_not_called()


def _log_lines(cog) -> list[str]:
    """Every line written to the log channel."""
    return [str(call.args[0]) for call in cog.bot.output_router.post_log.await_args_list]


async def test_a_posted_mystery_notice_is_recorded(tmp_path):
    """The notice is posted to the league, so the rehearsal records it as every other phase is
    recorded: one success line naming the maintainer and what was posted, for which round (#482)."""
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(0))

    [line] = _log_lines(cog)
    assert line.startswith("Maintainer (<@77>) | /test-mode advance | Success")
    assert "Mystery Round notice" in line
    assert "Division 1" in line and "Round 3" in line


async def test_a_fired_phase_cancels_its_own_job(tmp_path):
    """Otherwise the real job stays queued and fires again later, posting the same forecast
    a second time."""
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(1, job_id="weather_p1_s1_d1_r3_id5"))

    cog.bot.scheduler_service.cancel_job.assert_called_once_with("weather_p1_s1_d1_r3_id5")


async def test_a_phase_with_no_job_cancels_nothing(tmp_path):
    """An event whose job already fired, or was never armed — cancelling `None` would
    raise inside the scheduler."""
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(1, job_id=None))

    cog.bot.scheduler_service.cancel_job.assert_not_called()


async def test_a_failing_mystery_notice_is_reported_not_raised(tmp_path):
    """The change queue cannot save the notice's ask. The maintainer is stepping through a season and needs to know which round failed: the
    standard failure reply and one failure line name the notice, the division and the round
    (#482)."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()
    interaction.client = cog.bot

    cog.bot.change_queue.ask = AsyncMock(side_effect=RuntimeError("API down"))

    await _advance(cog, interaction, _entry(0))

    _assert_failure_recorded(cog, interaction, "Mystery Round notice")


async def test_a_failing_notice_is_not_marked_done(tmp_path):
    """The change queue cannot save the notice's ask: it did not happen, and marking it would
    skip the round on the next advance. The cog marks nothing; the notice's draw does."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    cog.bot.change_queue.ask = AsyncMock(side_effect=RuntimeError("API down"))

    await _advance(cog, _interaction(), _entry(0))

    cog.bot.change_queue.ask.assert_awaited_once()
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT phase1_done FROM rounds WHERE id = ?", (ROUND_ID,)
        )
        assert (await cursor.fetchone())["phase1_done"] == 0


# ---------------------------------------------------------------------------
# Result submission
# ---------------------------------------------------------------------------



async def test_advancing_a_round_start_replies_on_the_change_queue(tmp_path):
    """Round 3 of Division 1's result submission (4) is next, none open. Advance asks the change
    queue for `results.submission.open` of the round and replies that it is on the change queue,
    naming the division and the round, as a weather step's reply does; it starts no submission
    job of its own."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    mocks = await _advance(cog, interaction, _entry(4))

    [(kind, payload)] = _asked(cog)
    assert (kind, payload["round_id"]) == ("results.submission.open", ROUND_ID)
    replied = _replied(interaction)
    assert "is on the change queue" in replied
    assert "**Division 1**" in replied and "Round 3" in replied
    if "run_result_submission_job" in mocks:
        mocks["run_result_submission_job"].assert_not_awaited()


async def test_an_opened_submission_is_recorded_as_asked_of_the_queue(tmp_path):
    """Advance's line names the division and the round, and never says the results were
    submitted: the submission is opened, not done (#482). Nor does it say the submission has
    started: it is asked of the change queue, as the reply says, and names its phase so."""
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(4))

    [line] = _log_lines(cog)
    assert line.startswith("Maintainer (<@77>) | /test-mode advance")
    assert "Division 1" in line and "Round 3" in line
    assert "submitted" not in line.lower()
    assert "phase: result submission (on the change queue)" in line
    assert "started" not in line and "runs on its own" not in line


async def _noop(round_id: int) -> None:
    return None


async def test_the_round_s_real_results_job_is_cancelled_before_the_wizard_opens(tmp_path):
    """A weather-enabled season carries a real future-dated results job; left armed it
    would open a second submission channel after advance had already opened one.

    Against a real scheduler, with the job armed by the production path: advance once
    cancelled `results_r{round_id}`, an ID no job has ever had, and a double on
    `cancel_job` held that as correct (issue #139). Only this round's results job goes —
    another round's, and this round's forecasts, stay queued.
    """
    from leaguebot.core.services.scheduler_service import SchedulerService

    cog = _make_cog(await _make_db(tmp_path))
    service = SchedulerService(str(tmp_path / "jobs.db"))
    try:
        service.start()
        later = datetime.now(timezone.utc) + timedelta(days=3)
        rounds = [
            Round(
                id=round_id, division_id=DIVISION_ID, round_number=number,
                format=RoundFormat.NORMAL, track_name="Silverstone Circuit",
                scheduled_at=later,
            )
            for round_id, number in ((ROUND_ID, 3), (ROUND_ID + 1, 4))
        ]
        service.schedule_result_submission_jobs(rounds, division_meta={DIVISION_ID: (1, 1)})
        weather_job = f"weather_p3_s1_d1_r3_id{ROUND_ID}"
        service._scheduler.add_job(
            _noop, "date", run_date=later, id=weather_job, kwargs={"round_id": ROUND_ID}
        )
        cog.bot.scheduler_service = service

        await _advance(cog, _interaction(), _entry(4, job_id=None))

        assert sorted(job.id for job in service._scheduler.get_jobs()) == sorted(
            [f"results_s1_d1_r4_id{ROUND_ID + 1}", weather_job]
        )
    finally:
        try:
            service._scheduler.shutdown(wait=False)
        except Exception:
            pass
        service._scheduler._jobstores["default"].engine.dispose()


async def test_an_open_submission_blocks_the_next_advance(tmp_path):
    """The maintainer has to finish the round in front of them before stepping past it."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    mocks = await _advance(
        cog,
        interaction,
        _entry(4),
        **{
            "leaguebot.results.services.result_submission_service.is_submission_open": AsyncMock(
                return_value=True
            )
        },
    )

    assert _asked(cog) == []
    if "run_result_submission_job" in mocks:
        mocks["run_result_submission_job"].assert_not_awaited()
    assert "awaiting" in _replied(interaction)


async def test_a_round_awaiting_penalty_review_names_that_review(tmp_path):
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    await _advance(
        cog,
        interaction,
        _entry(4),
        **{
            "leaguebot.results.services.result_submission_service.is_submission_open": AsyncMock(
                return_value=True
            ),
            "leaguebot.core.services.test_mode_service.round_result_status": AsyncMock(
                return_value="AWAITING_REPORT_VERDICTS"
            ),
        },
    )

    assert "penalty review" in _replied(interaction)


async def test_a_round_awaiting_appeals_names_the_appeals_review(tmp_path):
    """Its report verdicts are settled already, so always saying "penalty review" would
    send a maintainer to a review that is finished."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    await _advance(
        cog,
        interaction,
        _entry(4),
        **{
            "leaguebot.results.services.result_submission_service.is_submission_open": AsyncMock(
                return_value=True
            ),
            "leaguebot.core.services.test_mode_service.round_result_status": AsyncMock(
                return_value="AWAITING_APPEAL_VERDICTS"
            ),
        },
    )

    replied = _replied(interaction)
    assert "appeals review" in replied
    assert "penalty review" not in replied


async def test_a_finalised_round_with_an_open_channel_says_so_differently(tmp_path):
    """The defensive case: nothing is awaiting review, so the message is about the
    submission being in progress rather than about a review to complete."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    await _advance(
        cog,
        interaction,
        _entry(4),
        **{
            "leaguebot.results.services.result_submission_service.is_submission_open": AsyncMock(
                return_value=True
            ),
            "leaguebot.core.services.test_mode_service.round_result_status": AsyncMock(
                return_value="FINAL"
            ),
        },
    )

    replied = _replied(interaction)
    assert "already in progress" in replied
    assert "awaiting" not in replied


# ---------------------------------------------------------------------------
# The check-in phases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", [6, 7])
async def test_a_fired_check_in_phase_cancels_its_own_job(tmp_path, phase):
    """Otherwise the real job fires later and the division is asked a second time."""
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(phase, job_id="rsvp_job"))

    cog.bot.scheduler_service.cancel_job.assert_called_once_with("rsvp_job")


@pytest.mark.parametrize("phase", [6, 7])
async def test_a_check_in_phase_with_no_job_cancels_nothing(tmp_path, phase):
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(phase, job_id=None))

    cog.bot.scheduler_service.cancel_job.assert_not_called()


@pytest.mark.parametrize(
    "phase,phrase", [(5, "RSVP notice"), (6, "RSVP last-notice"), (7, "RSVP deadline")]
)
async def test_a_failing_check_in_phase_is_reported_not_raised(tmp_path, phase, phrase):
    """The change queue cannot save the step's ask. The standard failure reply and one failure
    line name the phase, the division and the round (#482)."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()
    interaction.client = cog.bot
    cog.bot.change_queue.ask = AsyncMock(side_effect=RuntimeError("x"))

    await _advance(cog, interaction, _entry(phase))

    _assert_failure_recorded(cog, interaction, phrase)


@pytest.mark.parametrize(
    "phase,name", [(5, "rsvp_notice"), (6, "rsvp_last_notice"), (7, "rsvp_deadline")]
)
async def test_a_fired_check_in_phase_is_logged_by_name(tmp_path, phase, name):
    """Test mode still writes to the league's log, so a maintainer can reconstruct what
    the rehearsal did."""
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(phase))

    assert f"phase: {name}" in str(cog.bot.output_router.post_log.await_args.args[0])


async def test_the_deadline_reply_does_not_say_reserves_were_distributed(tmp_path):
    """Round 3's deadline is advanced. Its reserves are distributed only once the change queue
    reaches it, so the reply says it is on the change queue and does not say the distribution
    is complete."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    await _advance(cog, interaction, _entry(7))

    replied = _replied(interaction)
    assert "is on the change queue" in replied
    assert "Reserve distribution complete" not in replied


async def test_a_deadline_whose_announcement_was_refused_is_not_offered_again(tmp_path):
    """Test mode on, attendance and results on. Round 3's check-in deadline has run, its
    distribution saved and marked (`deadline_run_at`), but Discord refused its announcement, so
    none is recorded (`distribution_msg_id` NULL). The scheduler holds nothing more. The next
    advance moves on to the round's results phase (4), not the deadline (7) again, which would
    do nothing and hold every later step behind it (#439)."""
    from leaguebot.core.services.test_mode_service import get_next_pending_phase

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute("INSERT INTO attendance_config (id, module_enabled) VALUES (1, 1)")
        await db.execute("INSERT INTO results_module_config (id, module_enabled) VALUES (1, 1)")
        await db.execute(
            "INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, channel_id, "
            "posted_at, last_notice_msg_id, distribution_msg_id, deadline_run_at) "
            "VALUES (?, ?, 'call', 'ch1', '2026-05-27T00:00:00', 'last', NULL, "
            "'2026-05-31T22:00:00')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.commit()
    scheduler = MagicMock()
    scheduler.get_pending_advance_jobs = MagicMock(return_value=[])

    entry = await get_next_pending_phase(
        db_path, scheduler, now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    assert entry is not None
    assert (entry["round_id"], entry["phase_number"]) == (ROUND_ID, 4)


# ---------------------------------------------------------------------------
# The cleanups a day after the round (#425)
# ---------------------------------------------------------------------------

CLEANUPS = [
    (8, "run_post_race_cleanup", "forecast cleanup", "cleanup"),
    (9, "run_rsvp_cleanup", "check-in cleanup", "rsvp_cleanup"),
]


#: The cleanups, each asked of the change queue: the check-in cleanup's once advance asks it.
CLEANUPS_ASKED = [
    pytest.param(*CLEANUPS[0]),
    pytest.param(*CLEANUPS[1]),
]


@pytest.mark.parametrize("phase,runner,label,prefix", CLEANUPS_ASKED)
async def test_each_cleanup_reaches_its_own_runner(tmp_path, phase, runner, label, prefix):
    """The forecast cleanup (8) is asked of the change queue as a `weather.forecast.clean_up`
    of the round, replying '🧹 The **forecast cleanup** for **Division 1** — **Round 3** is on
    the change queue: …'; the check-in cleanup (9) is asked of it as an
    `attendance.check_in.clean_up` of the round, its reply saying it is on the change queue."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    await _advance(cog, interaction, _entry(phase))

    [(kind, payload)] = _asked(cog)
    if phase == 8:
        assert (kind, payload["round_id"]) == (FORECAST_CLEAN_UP, ROUND_ID)
        assert ("🧹 The **forecast cleanup** for **Division 1** — **Round 3** is on the change "
                "queue") in _replied(interaction)
    else:
        assert (kind, payload["round_id"]) == (CHECK_IN_CLEAN_UP, ROUND_ID)
        assert "is on the change queue" in _replied(interaction)
    assert label in _replied(interaction)


@pytest.mark.parametrize("job_id", ["cleanup_job", None], ids=["from-the-job-store", "from-state"])
@pytest.mark.parametrize("phase,runner,label,prefix", CLEANUPS)
async def test_a_fired_cleanup_cancels_its_job_by_kind(
    tmp_path, phase, runner, label, prefix, job_id
):
    """By kind rather than by the entry's job, which is None where the step was found from
    database state; a job left queued would fire again at its own moment."""
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(phase, job_id=job_id))

    cog.bot.scheduler_service.cancel_round.assert_called_once_with(
        ROUND_ID, only=frozenset({prefix})
    )


@pytest.mark.parametrize("phase,runner,label,prefix", CLEANUPS_ASKED)
async def test_a_failing_cleanup_is_reported_not_raised(tmp_path, phase, runner, label, prefix):
    """The change queue cannot save the cleanup's ask. The standard failure reply and one
    failure line name the cleanup, the division and the round (#482)."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()
    interaction.client = cog.bot
    cog.bot.change_queue.ask = AsyncMock(side_effect=RuntimeError("x"))

    await _advance(cog, interaction, _entry(phase))

    _assert_failure_recorded(cog, interaction, label)


@pytest.mark.parametrize("phase,runner,label,prefix", CLEANUPS)
async def test_a_fired_cleanup_is_logged_by_name(tmp_path, phase, runner, label, prefix):
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(phase))

    assert f"phase: {prefix}" in str(cog.bot.output_router.post_log.await_args.args[0])


#: Each check-in step advance asks of the change queue: its phase, its kind, and the name its
#: reply gives it.
CHECK_IN_STEPS = [
    pytest.param(5, CHECK_IN_CALL, "RSVP notice", id="call"),
    pytest.param(6, CHECK_IN_LAST_NOTICE, "RSVP last-notice", id="last notice"),
    pytest.param(7, CHECK_IN_DEADLINE, "RSVP deadline", id="deadline"),
    pytest.param(9, CHECK_IN_CLEAN_UP, "check-in cleanup", id="clean-up"),
]


@pytest.mark.parametrize("phase,kind,label", CHECK_IN_STEPS)
async def test_advancing_a_check_in_step_replies_on_the_change_queue(
    tmp_path, phase, kind, label
):
    """Round 3 of Division 1's check-in call (5), last notice (6), deadline (7) or clean-up (9)
    is next. Advance asks the change queue for that step by its kind, for the round, and replies
    that it is on the change queue, naming the step, the division and the round, as a weather
    step's reply does; it runs no check-in runner of its own."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    mocks = await _advance(cog, interaction, _entry(phase))

    [(asked_kind, payload)] = _asked(cog)
    assert (asked_kind, payload["round_id"]) == (kind, ROUND_ID)
    replied = _replied(interaction)
    assert "is on the change queue" in replied
    assert label in replied
    assert "**Division 1**" in replied and "Round 3" in replied
    for name in _CHECK_IN_RUNNERS:
        if name in mocks:
            mocks[name].assert_not_awaited()


# ---------------------------------------------------------------------------
# The weather phases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", [1, 2, 3])
async def test_each_weather_phase_asks_the_queue_for_its_phase(tmp_path, phase):
    """Phase *phase* of round 3 of Division 1 (Silverstone Circuit) is next. Advance asks the
    change queue for that phase (a `weather.forecast` of the round and phase) and replies
    '⏩ **Phase n** for **Division 1** — **Silverstone Circuit** is on the change queue: …'."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    await _advance(cog, interaction, _entry(phase))

    [(kind, payload)] = _asked(cog)
    assert kind == FORECAST
    assert (payload["round_id"], payload["phase"]) == (ROUND_ID, phase)
    assert (f"⏩ **Phase {phase}** for **Division 1** — **Silverstone Circuit** is on the "
            "change queue") in _replied(interaction)


async def test_a_weather_phase_cancels_its_job_before_asking(tmp_path):
    """Before, not after: an ask that raised would otherwise leave the real job queued to fire
    the same phase again."""
    cog = _make_cog(await _make_db(tmp_path))
    order: list[str] = []
    cog.bot.scheduler_service.cancel_job = MagicMock(side_effect=lambda _j: order.append("cancel"))
    cog.bot.change_queue.ask = AsyncMock(side_effect=lambda *_a, **_k: order.append("ask"))

    await _advance(cog, _interaction(), _entry(2))

    assert order == ["cancel", "ask"]


async def test_a_failing_weather_phase_names_the_round_and_track(tmp_path):
    """The change queue cannot save Phase 1's ask. The standard failure reply names the phase,
    the division, the round and the track, and one failure line the phase, the division and
    the round (#482)."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()
    interaction.client = cog.bot
    cog.bot.change_queue.ask = AsyncMock(side_effect=RuntimeError("boom"))

    await _advance(cog, interaction, _entry(1))

    _assert_failure_recorded(cog, interaction, "Phase 1")
    assert "Silverstone Circuit" in _replied(interaction)


async def test_a_fired_weather_phase_is_logged_with_its_track(tmp_path):
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(3))

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert "phase: 3" in logged
    assert "track: Silverstone Circuit" in logged


# ---------------------------------------------------------------------------
# A weather step advanced, then run by the change queue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", [
    pytest.param(0, id="mystery notice"),
    pytest.param(1, id="phase 1"),
])
async def test_an_advanced_phase_is_drawn_and_posted_by_the_queue(tmp_path, phase):
    """Season 3 is being raced in test mode, with weather on; Pro's round 3 (Silverstone
    Circuit, a mystery round for the notice) has its notice, or Phase 1, next. The maintainer
    advances: nothing is posted and the round is not marked done until the change queue runs.
    Once it has, the notice, or the Phase 1 forecast, stands in Pro's forecast channel and the
    round's Phase 1 is marked done by the draw's save (for Phase 1, one draw recorded)."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.season_league import DIVISIONS, PRO, ongoing_league, round_id

    league = await ongoing_league(tmp_path, weather=True)
    await league.set_test_mode(True)
    round_ = round_id(PRO, 3)
    forecast = DIVISIONS[PRO][3].forecast
    await league.write("UPDATE rounds SET track_name = 'Silverstone Circuit' WHERE id = ?",
                       round_)
    if phase == 0:
        await league.write("UPDATE rounds SET format = 'MYSTERY' WHERE id = ?", round_)
    for session in ("SHORT_QUALIFYING", "LONG_RACE"):
        await league.write("INSERT INTO sessions (round_id, session_type) VALUES (?, ?)",
                           round_, session)
    # The notice takes its timer's job down by round and kind; the league's double records a
    # round's jobs taken down by round alone.
    league.bot.scheduler_service.cancel_round = MagicMock()
    cog = _Cog.__new__(_Cog)
    cog.bot = league.bot
    entry = {"phase_number": phase, "round_id": round_, "round_number": 3,
             "division_name": "Pro", "track_name": "Silverstone Circuit", "job_id": None}

    async def _phase1_done() -> int:
        [row] = await league.rows("SELECT phase1_done FROM rounds WHERE id = ?", round_)
        return row["phase1_done"]

    def _sent() -> list[int]:
        return [mid for kind, cid, mid in league.events if cid == forecast and kind == "send"]

    with patch("leaguebot.core.cogs.test_mode_cog.get_next_pending_phase",
               new=AsyncMock(return_value=entry)):
        await undecorate(_Cog.advance)(cog, _interaction())

    assert _sent() == []
    assert await _phase1_done() == 0

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(_sent()) == 1
    assert await _phase1_done() == 1
    if phase == 1:
        [row] = await league.rows(
            "SELECT COUNT(*) AS n FROM phase_results WHERE round_id = ? AND phase_number = 1 "
            "AND status = 'ACTIVE'", round_)
        assert row["n"] == 1


async def test_an_advanced_phase_of_a_raced_round_says_it_was_not_due(tmp_path):
    """Season 3 is being raced in test mode, with weather on; Pro's round 3 (Silverstone
    Circuit) was raced an hour ago with its Phase 1 not done (weather switched on after it).
    The maintainer advances its Phase 1: the change queue drops it as not due (A25), so the reply
    says it was not due and why, and not that it is on the change queue; nothing is asked of the
    queue, nothing posted, and the phase stays undone."""
    from tests.support.change_queue import change_rows, run_queue, stopped_job
    from tests.support.season_league import DIVISIONS, PRO, ongoing_league, round_id

    league = await ongoing_league(tmp_path, weather=True)
    await league.set_test_mode(True)
    round_ = round_id(PRO, 3)
    forecast = DIVISIONS[PRO][3].forecast
    raced = (league.clock.now - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    await league.write("UPDATE rounds SET track_name = 'Silverstone Circuit', scheduled_at = ? "
                       "WHERE id = ?", raced, round_)
    for session in ("SHORT_QUALIFYING", "LONG_RACE"):
        await league.write("INSERT INTO sessions (round_id, session_type) VALUES (?, ?)",
                           round_, session)
    cog = _Cog.__new__(_Cog)
    cog.bot = league.bot
    interaction = _interaction()
    entry = {"phase_number": 1, "round_id": round_, "round_number": 3,
             "division_name": "Pro", "track_name": "Silverstone Circuit", "job_id": None}

    with patch("leaguebot.core.cogs.test_mode_cog.get_next_pending_phase",
               new=AsyncMock(return_value=entry)):
        await undecorate(_Cog.advance)(cog, interaction)
    await run_queue(league.bot)

    replied = _replied(interaction)
    assert "is on the change queue" not in replied
    assert ("⏩ **Phase 1** for **Pro** — **Round 3** was not due: its race has been run."
            in replied)
    assert [row for row in await change_rows(league.db_path) if row["kind"] == FORECAST] == []
    assert await stopped_job(league.db_path) is None
    assert [mid for kind, cid, mid in league.events if cid == forecast] == []
    [row] = await league.rows("SELECT phase1_done FROM rounds WHERE id = ?", round_)
    assert row["phase1_done"] == 0


async def test_an_advanced_forecast_cleanup_deletes_an_earlier_phase_still_standing(tmp_path):
    """Season 3 is being raced in test mode, with weather on; Pro's round 3 was raced a day and
    an hour ago, and its Phase 3 never posted, so its Phase 2 forecast (message 8002) stands.
    The maintainer advances its forecast cleanup (8). The clean-up deletes whichever of Phases 1
    to 3 is still recorded (owner, 2026-10-12): once the change queue has run, the Phase 2
    forecast is gone from Pro's forecast channel, its record with it."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.season_league import (
        DIVISIONS, PRO, ongoing_league, posted_forecast, round_id,
    )

    league = await ongoing_league(tmp_path, weather=True)
    await league.set_test_mode(True)
    round_ = round_id(PRO, 3)
    forecast = league.channel(DIVISIONS[PRO][3].forecast)
    raced = (league.clock.now - timedelta(days=1, hours=1)).replace(tzinfo=None).isoformat()
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?", raced, round_)
    await posted_forecast(league, round_, 2, 8002)
    league.bot.scheduler_service.cancel_round = MagicMock()
    cog = _Cog.__new__(_Cog)
    cog.bot = league.bot
    entry = {"phase_number": 8, "round_id": round_, "round_number": 3,
             "division_name": "Pro", "track_name": "Silverstone", "job_id": None}

    with patch("leaguebot.core.cogs.test_mode_cog.get_next_pending_phase",
               new=AsyncMock(return_value=entry)):
        await undecorate(_Cog.advance)(cog, _interaction())
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert 8002 not in forecast.messages
    assert await league.rows("SELECT * FROM forecast_messages WHERE round_id = ?", round_) == []


async def test_an_advanced_forecast_cleanup_names_the_phase_it_deletes(tmp_path):
    """Season 3 is being raced in test mode, with weather on; Pro's round 3 was raced a day and
    an hour ago, and its Phase 3 never posted, so its Phase 2 forecast (message 8002) stands.
    The maintainer advances its forecast cleanup (8): the reply names the forecast it deletes,
    "🧹 The **forecast cleanup** for **Pro** — **Round 3** is on the change queue: it deletes the
    Phase 2 forecast as soon as the queue reaches it.", and so does the change asked of the
    queue, "deleting the Phase 2 forecast of round 3 in **Pro** a day after it"."""
    from tests.support.change_queue import change_rows
    from tests.support.season_league import PRO, ongoing_league, posted_forecast, round_id

    league = await ongoing_league(tmp_path, weather=True)
    await league.set_test_mode(True)
    round_ = round_id(PRO, 3)
    raced = (league.clock.now - timedelta(days=1, hours=1)).replace(tzinfo=None).isoformat()
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?", raced, round_)
    await posted_forecast(league, round_, 2, 8002)
    league.bot.scheduler_service.cancel_round = MagicMock()
    cog = _Cog.__new__(_Cog)
    cog.bot = league.bot
    interaction = _interaction()
    entry = {"phase_number": 8, "round_id": round_, "round_number": 3,
             "division_name": "Pro", "track_name": "Silverstone", "job_id": None}

    with patch("leaguebot.core.cogs.test_mode_cog.get_next_pending_phase",
               new=AsyncMock(return_value=entry)):
        await undecorate(_Cog.advance)(cog, interaction)

    assert ("🧹 The **forecast cleanup** for **Pro** — **Round 3** is on the change queue: it "
            "deletes the Phase 2 forecast as soon as the queue reaches it.") in _replied(interaction)
    [row] = [row for row in await change_rows(league.db_path) if row["kind"] == FORECAST_CLEAN_UP]
    assert row["what"] == "deleting the Phase 2 forecast of round 3 in **Pro** a day after it"


async def test_an_advanced_mystery_notice_of_a_raced_round_says_it_was_not_due(tmp_path):
    """Season 3 is being raced in test mode, with weather on; Pro's round 3 is a mystery round
    raced an hour ago whose notice never went out. The maintainer advances its notice (0): the
    change queue drops it as not due (A25), so the reply says it was not due and why, and not
    that it is on the change queue; nothing is asked of the queue, and nothing posted."""
    from tests.support.change_queue import change_rows, run_queue, stopped_job
    from tests.support.season_league import DIVISIONS, PRO, ongoing_league, round_id

    league = await ongoing_league(tmp_path, weather=True)
    await league.set_test_mode(True)
    round_ = round_id(PRO, 3)
    forecast = DIVISIONS[PRO][3].forecast
    raced = (league.clock.now - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    await league.write("UPDATE rounds SET format = 'MYSTERY', scheduled_at = ? WHERE id = ?",
                       raced, round_)
    league.bot.scheduler_service.cancel_round = MagicMock()
    cog = _Cog.__new__(_Cog)
    cog.bot = league.bot
    interaction = _interaction()
    entry = {"phase_number": 0, "round_id": round_, "round_number": 3,
             "division_name": "Pro", "track_name": "Mystery", "job_id": None}

    with patch("leaguebot.core.cogs.test_mode_cog.get_next_pending_phase",
               new=AsyncMock(return_value=entry)):
        await undecorate(_Cog.advance)(cog, interaction)
    await run_queue(league.bot)

    replied = _replied(interaction)
    assert "is on the change queue" not in replied
    assert ("🔮 The **Mystery Round notice** for **Pro** — **Round 3** was not due: its race "
            "has been run.") in replied
    assert [row for row in await change_rows(league.db_path) if row["kind"] == FORECAST] == []
    assert await stopped_job(league.db_path) is None
    assert [mid for kind, cid, mid in league.events if cid == forecast] == []


async def test_an_advanced_forecast_cleanup_with_no_forecast_standing_says_it_was_not_due(
    tmp_path,
):
    """Season 3 is being raced in test mode, with weather on; Pro's round 3 was raced a day and
    an hour ago and no forecast of it is recorded. The maintainer advances its forecast cleanup
    (8): the change queue drops it as not due, so the reply says it was not due as no forecast
    stands, and not that it is on the change queue; nothing is asked of the queue."""
    from tests.support.change_queue import change_rows, stopped_job
    from tests.support.season_league import PRO, ongoing_league, round_id

    league = await ongoing_league(tmp_path, weather=True)
    await league.set_test_mode(True)
    round_ = round_id(PRO, 3)
    raced = (league.clock.now - timedelta(days=1, hours=1)).replace(tzinfo=None).isoformat()
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?", raced, round_)
    await league.write("DELETE FROM forecast_messages WHERE round_id = ?", round_)
    league.bot.scheduler_service.cancel_round = MagicMock()
    cog = _Cog.__new__(_Cog)
    cog.bot = league.bot
    interaction = _interaction()
    entry = {"phase_number": 8, "round_id": round_, "round_number": 3,
             "division_name": "Pro", "track_name": "Silverstone", "job_id": None}

    with patch("leaguebot.core.cogs.test_mode_cog.get_next_pending_phase",
               new=AsyncMock(return_value=entry)):
        await undecorate(_Cog.advance)(cog, interaction)

    replied = _replied(interaction)
    assert "is on the change queue" not in replied
    assert ("🧹 The **forecast cleanup** for **Pro** — **Round 3** was not due: no forecast "
            "stands.") in replied
    assert [row for row in await change_rows(league.db_path)
            if row["kind"] == FORECAST_CLEAN_UP] == []
    assert await stopped_job(league.db_path) is None


# ---------------------------------------------------------------------------
# The weather steps advance passed over, named in its reply (#439)
# ---------------------------------------------------------------------------


def _passed_over(phase_number: int, round_number: int) -> dict:
    """A raced round's weather step advance passes over, as `get_passed_over_steps` gives it."""
    return {"phase_number": phase_number, "round_id": 100 + round_number,
            "round_number": round_number, "division_name": "Division 1",
            "track_name": "Bahrain", "job_id": None}


PHASE_2_PASSED = ("⏭️ **Phase 2** for **Division 1** — **Round 2** was not due: its race has "
                  "been run.")
NOTICE_PASSED = ("⏭️ The **Mystery Round notice** for **Division 1** — **Round 1** was not due: "
                 "its race has been run.")
ADVANCED = "⏩ **Phase 1** for **Division 1** — **Silverstone Circuit** is on the change queue"


@pytest.mark.parametrize("passed, named", [
    pytest.param([], [], id="none passed over"),
    pytest.param([(2, 2)], [PHASE_2_PASSED], id="one passed over"),
    pytest.param([(0, 1), (2, 2)], [NOTICE_PASSED, PHASE_2_PASSED], id="two passed over"),
])
async def test_advance_names_the_raced_steps_it_passed_over_before_the_step_it_advanced(
    tmp_path, passed, named,
):
    """Advance passes over the weather steps of a round whose race has been run (owner,
    2026-10-12, "Advance names what it skipped"). Advancing round 3's Phase 1, the reply first
    names each step passed over, "⏭️ **Phase 2** for **Division 1** — **Round 2** was not due:
    its race has been run.", a mystery round's notice as advance names it, in order, then the
    step advanced. With nothing passed over the reply is as before, naming nothing."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()
    steps = [_passed_over(phase, number) for phase, number in passed]

    with patch("leaguebot.core.cogs.test_mode_cog.get_passed_over_steps",
               new=AsyncMock(return_value=steps)):
        await _advance(cog, interaction, _entry(1, job_id=None))

    replied = _replied(interaction)
    assert ADVANCED in replied
    places = [replied.index(line) for line in named]
    assert places == sorted(places)
    assert all(place < replied.index(ADVANCED) for place in places)
    assert replied.count("⏭️") == len(named)
    [(kind, payload)] = _asked(cog)
    assert (kind, payload["phase"]) == (FORECAST, 1)


async def test_advance_with_only_raced_steps_left_names_them_and_says_nothing_else_is_due(
    tmp_path,
):
    """Nothing is pending but round 2's Phase 2, whose race has been run: advance names it as
    passed over and says nothing else is due, as it says when nothing is pending; nothing is
    asked of the change queue. The log channel's refusal line stands on its own, naming no step
    "above", since nothing sits above it there."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()
    interaction.client = cog.bot

    with patch("leaguebot.core.cogs.test_mode_cog.get_passed_over_steps",
               new=AsyncMock(return_value=[_passed_over(2, 2)])):
        await _advance(cog, interaction, None)

    replied = _replied(interaction)
    assert PHASE_2_PASSED in replied
    assert "There is nothing left to advance." in replied
    assert replied.index(PHASE_2_PASSED) < replied.index("There is nothing left to advance.")
    assert _asked(cog) == []
    logged = [str(call.args[0]) for call in cog.bot.output_router.post_log.await_args_list]
    refusals = [line for line in logged if "refused" in line]
    assert len(refusals) == 1, logged
    assert ("Nothing is due but the weather steps of rounds whose races have been run, which "
            "were passed over. There is nothing left to advance.") in refusals[0]
    assert "above" not in refusals[0]


async def test_advance_reads_the_clock_once_for_the_step_it_offers_and_those_it_passes_over(
    tmp_path,
):
    """The step advance offers and the steps it names as passed over are judged at one moment,
    so that a race time falling between two readings of the clock cannot leave a step both
    offered and named as passed over, or neither."""
    cog = _make_cog(await _make_db(tmp_path))
    offered = AsyncMock(return_value=None)
    passed = AsyncMock(return_value=[])

    with patch("leaguebot.core.cogs.test_mode_cog.get_next_pending_phase", new=offered), \
            patch("leaguebot.core.cogs.test_mode_cog.get_passed_over_steps", new=passed):
        await undecorate(_Cog.advance)(cog, _interaction())

    moment = offered.await_args.kwargs["now"]
    assert moment is not None
    assert passed.await_args.kwargs["now"] == moment



# ---------------------------------------------------------------------------
# A past-dated round's check-in, advanced by hand on a real queue (attendance spec :221-222)
# ---------------------------------------------------------------------------
# Under test mode a restored save's rounds are dated in the past, and a restart posts no late
# call for them. Advance posts each step in its turn instead, however late the round is: the
# step carries `advanced` and is judged by no clock (test-mode.md :100).


async def _ask_as_advance(league, kind: str, round_id: int, round_number: int) -> None:
    """Ask the change queue for check-in step *kind* of *round_id* as advance does, through the
    cog's own ask, with the league's real queue; the queue is not run."""
    cog = _Cog.__new__(_Cog)
    cog.bot = league.bot
    entry = {"round_id": round_id, "round_number": round_number, "division_name": "Pro"}
    await cog._ask_check_in(entry, kind, "advancing the check-in")


async def test_advance_posts_a_past_dated_rounds_call_instead_of_giving_it_up(tmp_path):
    """Round 4 was raced an hour ago, so its check-in deadline passed three hours ago and no call
    was ever posted for it. Advance asks its call and the queue runs: the call is posted in
    Pro's check-in channel and recorded, the round's check-in is left open, and no line says the
    call was given up."""
    from datetime import timedelta as delta

    from tests.attendance.test_check_in_call_change import (
        ROUND_4,
        _answers,
        _call_league,
        _call_record,
        _round_4_in,
        _sent,
    )
    from tests.support.change_queue import run_queue, stopped_job

    league = await _call_league(tmp_path)
    await _round_4_in(league, delta(hours=-1))

    await _ask_as_advance(league, CHECK_IN_CALL, ROUND_4, 4)
    await run_queue(league.bot)

    assert len(_sent(league)) == 1
    assert len(await _call_record(league)) == 1
    [round_] = await league.rows("SELECT checkin_cleared FROM rounds WHERE id = ?", ROUND_4)
    assert round_["checkin_cleared"] == 0
    assert "could not be posted" not in league.log()
    assert "counts nothing against anyone" not in league.log()
    assert await stopped_job(league.db_path) is None
    assert set((await _answers(league)).values()) == {"NO_RSVP"}


async def test_an_advanced_last_notice_is_posted_after_the_real_deadline(tmp_path):
    """Round 3 is an hour off, so its two-hour deadline passed an hour ago, and its call stands
    with no last notice. Advance asks the last notice and the queue runs: it is posted in Pro's
    check-in channel, not dropped for the real clock."""
    from datetime import timedelta as delta

    from tests.attendance.test_last_notice_change import ROUND_3, _call_row, _league, _sent
    from tests.support.change_queue import run_queue, stopped_job

    league = await _league(tmp_path)
    when = league.clock.now + delta(hours=1)
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.replace(tzinfo=None).isoformat(), ROUND_3)

    await _ask_as_advance(league, CHECK_IN_LAST_NOTICE, ROUND_3, 3)
    await run_queue(league.bot)

    [posted] = _sent(league)
    assert (await _call_row(league))["last_notice_msg_id"] == str(posted)
    assert await stopped_job(league.db_path) is None


async def test_an_advanced_deadline_places_the_reserves_on_every_answer_held(tmp_path):
    """Round 3's deadline passed an hour ago. Rhea accepted half an hour before it and Sam ten
    minutes after it. A timer's deadline places Rhea alone; advance's places both, Sam at least
    on standby, whatever the real clock says."""
    from datetime import timedelta as delta

    from tests.attendance.test_deadline_change import (
        FERRARI,
        RHEA,
        ROUND_3,
        SAM,
        _deadline,
        _league,
        _placement,
        _reserves,
    )
    from tests.support.change_queue import run_queue

    league = await _league(tmp_path)
    deadline = _deadline(league)
    await _reserves(league, rhea=deadline - delta(minutes=30), sam=deadline + delta(minutes=10))

    await _ask_as_advance(league, CHECK_IN_DEADLINE, ROUND_3, 3)
    await run_queue(league.bot)

    assert await _placement(league, RHEA) == (FERRARI, 0)
    assert await _placement(league, SAM) != (None, 0)


# ---------------------------------------------------------------------------
# A round whose every session was entered as not held (#439, slice 8, Part E)
# ---------------------------------------------------------------------------

async def test_advance_offers_only_the_clean_ups_of_a_round_whose_every_session_was_not_held(
    tmp_path,
):
    """Test mode on, weather, attendance and results on. Round 3 of Division 1 raced on 1 June and
    ended cancelled because every session of it was entered as not held. Its Phase 3 forecast and
    its check-in call still stand, the call's deadline never run, and the scheduler still holds
    its deadline and both clean-ups. Advanced on 3 June, advance offers the forecast clean-up (8);
    once the forecast is down, the check-in clean-up (9); once the call is down, nothing. Never
    the deadline (7) nor the round's results submission (4): the round has ended (core :472,
    :475)."""
    from leaguebot.core.services.test_mode_service import get_next_pending_phase

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET weather_module_enabled = 1")
        await db.execute("INSERT INTO attendance_config (id, module_enabled) VALUES (1, 1)")
        await db.execute("INSERT INTO results_module_config (id, module_enabled) VALUES (1, 1)")
        await db.execute(
            "UPDATE rounds SET format = 'NORMAL', status = 'CANCELLED', not_held = 1 WHERE id = ?",
            (ROUND_ID,),
        )
        await db.execute(
            "INSERT INTO forecast_messages "
            "(round_id, division_id, phase_number, message_id, posted_at) "
            "VALUES (?, ?, 3, 800, '2026-05-31T12:00:00')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, channel_id, "
            "posted_at) VALUES (?, ?, 'call', 'ch1', '2026-05-27T00:00:00')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.commit()
    scheduler = MagicMock()
    scheduler.get_pending_advance_jobs = MagicMock(return_value=[
        {"job_id": f"{prefix}_r{ROUND_ID}", "round_id": ROUND_ID, "phase_number": phase,
         "next_run_time": datetime(2026, 6, day, tzinfo=timezone.utc)}
        for prefix, phase, day in (("rsvp_deadline", 7, 1), ("cleanup", 8, 2),
                                   ("rsvp_cleanup", 9, 2))
    ])
    now = datetime(2026, 6, 3, tzinfo=timezone.utc)

    entry = await get_next_pending_phase(db_path, scheduler, now=now)
    assert entry is not None
    assert (entry["round_id"], entry["phase_number"]) == (ROUND_ID, 8)

    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM forecast_messages WHERE round_id = ?", (ROUND_ID,))
        await db.commit()
    entry = await get_next_pending_phase(db_path, scheduler, now=now)
    assert entry is not None
    assert (entry["round_id"], entry["phase_number"]) == (ROUND_ID, 9)

    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM rsvp_embed_messages WHERE round_id = ?", (ROUND_ID,))
        await db.commit()
    assert await get_next_pending_phase(db_path, scheduler, now=now) is None
