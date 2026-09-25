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
where.

**Result submission refuses while one is already open**, and names *which* review is standing.
A round awaiting appeal verdicts has had its report verdicts settled already, so always saying
"penalty review" would send a maintainer to a review that is finished — the message reads the
round's actual status instead.

**Nothing left to advance is an answer, not a failure**, and it names the command that ends the
season — which is the thing a maintainer reaching the end of the queue actually wants next.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# Aliased on import: pytest tries to collect any module-level name starting with `Test`
# as a test class, and warns that it cannot because the cog has an `__init__`.
from cogs.test_mode_cog import TestModeCog as _Cog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from models.round import Round, RoundFormat  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 12308
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 5


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

    cog = _Cog.__new__(_Cog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
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


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


async def _advance(cog, interaction, entry, **patches):
    """Run advance with the queue answering *entry* and every phase service stubbed."""
    defaults = {
        "services.mystery_notice_service.run_mystery_notice": AsyncMock(return_value=None),
        "services.phase1_service.run_phase1": AsyncMock(return_value=None),
        "services.phase2_service.run_phase2": AsyncMock(return_value=None),
        "services.phase3_service.run_phase3": AsyncMock(return_value=None),
        "services.rsvp_service.run_rsvp_notice": AsyncMock(return_value=None),
        "services.rsvp_service.run_rsvp_last_notice": AsyncMock(return_value=None),
        "services.rsvp_service.run_rsvp_deadline": AsyncMock(return_value=None),
        "services.rsvp_service.run_rsvp_cleanup": AsyncMock(return_value=None),
        "services.forecast_cleanup_service.run_post_race_cleanup": AsyncMock(return_value=None),
        "services.result_submission_service.is_submission_open": AsyncMock(
            return_value=False
        ),
        "services.result_submission_service.run_result_submission_job": AsyncMock(
            return_value=None
        ),
        "services.test_mode_service.round_result_status": AsyncMock(
            return_value="AWAITING_RESULTS"
        ),
    }
    defaults.update(patches)

    from contextlib import ExitStack

    with ExitStack() as stack:
        mocks = {}
        for target, mock in defaults.items():
            stack.enter_context(patch(target, new=mock))
            mocks[target.rsplit(".", 1)[1]] = mock
        stack.enter_context(
            patch(
                "cogs.test_mode_cog.get_next_pending_phase",
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
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    mocks = await _advance(cog, interaction, _entry(0))

    mocks["run_mystery_notice"].assert_awaited_once()
    assert "Mystery Round notice" in _replied(interaction)


async def test_a_posted_mystery_notice_is_marked_done(tmp_path):
    """So the round is excluded from every later advance — without it the same notice
    would be posted on every press."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _advance(cog, _interaction(), _entry(0))

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT phase1_done FROM rounds WHERE id = ?", (ROUND_ID,)
        )
        assert (await cursor.fetchone())["phase1_done"] == 1


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
    """The maintainer is stepping through a season and needs to know which round failed."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _advance(
        cog,
        interaction,
        _entry(0),
        **{
            "services.mystery_notice_service.run_mystery_notice": AsyncMock(
                side_effect=RuntimeError("API down")
            )
        },
    )

    replied = _replied(interaction)
    assert "internal error" in replied
    assert "Division 1" in replied
    assert "Round 3" in replied


async def test_a_failing_notice_is_not_marked_done(tmp_path):
    """It did not happen, and marking it would skip the round on the next advance."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _advance(
        cog,
        _interaction(),
        _entry(0),
        **{
            "services.mystery_notice_service.run_mystery_notice": AsyncMock(
                side_effect=RuntimeError("API down")
            )
        },
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT phase1_done FROM rounds WHERE id = ?", (ROUND_ID,)
        )
        assert (await cursor.fetchone())["phase1_done"] == 0


# ---------------------------------------------------------------------------
# Result submission
# ---------------------------------------------------------------------------


async def test_result_submission_opens_a_wizard(tmp_path):
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    await _advance(cog, interaction, _entry(4))

    assert "Opening result submission wizard" in _replied(interaction)


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
    from services.scheduler_service import SchedulerService

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
            "services.result_submission_service.is_submission_open": AsyncMock(
                return_value=True
            )
        },
    )

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
            "services.result_submission_service.is_submission_open": AsyncMock(
                return_value=True
            ),
            "services.test_mode_service.round_result_status": AsyncMock(
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
            "services.result_submission_service.is_submission_open": AsyncMock(
                return_value=True
            ),
            "services.test_mode_service.round_result_status": AsyncMock(
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
            "services.result_submission_service.is_submission_open": AsyncMock(
                return_value=True
            ),
            "services.test_mode_service.round_result_status": AsyncMock(
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


async def test_the_rsvp_notice_is_fired(tmp_path):
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    mocks = await _advance(cog, interaction, _entry(5))

    mocks["run_rsvp_notice"].assert_awaited_once()
    assert "RSVP notice" in _replied(interaction)


async def test_a_failing_rsvp_notice_is_reported_not_raised(tmp_path):
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    await _advance(
        cog,
        interaction,
        _entry(5),
        **{
            "services.rsvp_service.run_rsvp_notice": AsyncMock(
                side_effect=RuntimeError("no channel")
            )
        },
    )

    assert "internal error" in _replied(interaction)


async def test_a_fired_rsvp_notice_is_logged_to_the_league(tmp_path):
    """Test mode still writes to the league's log, so a maintainer can reconstruct what
    the rehearsal did."""
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(5))

    cog.bot.output_router.post_log.assert_awaited()


@pytest.mark.parametrize(
    "phase,runner,label",
    [
        (6, "run_rsvp_last_notice", "RSVP last-notice"),
        (7, "run_rsvp_deadline", "RSVP deadline"),
    ],
)
async def test_the_later_check_in_phases_are_fired(tmp_path, phase, runner, label):
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    mocks = await _advance(cog, interaction, _entry(phase))

    mocks[runner].assert_awaited_once_with(ROUND_ID, cog.bot)
    assert label in _replied(interaction)


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
    "phase,runner,phrase",
    [
        (6, "services.rsvp_service.run_rsvp_last_notice", "RSVP last-notice"),
        (7, "services.rsvp_service.run_rsvp_deadline", "RSVP deadline"),
    ],
)
async def test_a_failing_check_in_phase_is_reported_not_raised(tmp_path, phase, runner, phrase):
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    await _advance(
        cog, interaction, _entry(phase), **{runner: AsyncMock(side_effect=RuntimeError("x"))}
    )

    replied = _replied(interaction)
    assert "internal error" in replied
    assert phrase in replied
    cog.bot.output_router.post_log.assert_not_awaited()


@pytest.mark.parametrize("phase,name", [(6, "rsvp_last_notice"), (7, "rsvp_deadline")])
async def test_a_fired_check_in_phase_is_logged_by_name(tmp_path, phase, name):
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(phase))

    assert f"phase: {name}" in str(cog.bot.output_router.post_log.await_args.args[0])


async def test_the_deadline_reply_says_reserves_were_distributed(tmp_path):
    """The deadline is where reserves are called up, which is what a maintainer rehearsing
    it is looking to see happen."""
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    await _advance(cog, interaction, _entry(7))

    assert "Reserve distribution complete" in _replied(interaction)


# ---------------------------------------------------------------------------
# The cleanups a day after the round (#425)
# ---------------------------------------------------------------------------

CLEANUPS = [
    (8, "run_post_race_cleanup", "forecast cleanup", "cleanup"),
    (9, "run_rsvp_cleanup", "check-in cleanup", "rsvp_cleanup"),
]


@pytest.mark.parametrize("phase,runner,label,prefix", CLEANUPS)
async def test_each_cleanup_reaches_its_own_runner(tmp_path, phase, runner, label, prefix):
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    mocks = await _advance(cog, interaction, _entry(phase))

    mocks[runner].assert_awaited_once_with(ROUND_ID, cog.bot)
    other = {"run_post_race_cleanup", "run_rsvp_cleanup"} - {runner}
    mocks[other.pop()].assert_not_awaited()
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


@pytest.mark.parametrize("phase,runner,label,prefix", CLEANUPS)
async def test_a_failing_cleanup_is_reported_not_raised(tmp_path, phase, runner, label, prefix):
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()
    target = {
        "run_post_race_cleanup": "services.forecast_cleanup_service.run_post_race_cleanup",
        "run_rsvp_cleanup": "services.rsvp_service.run_rsvp_cleanup",
    }[runner]

    await _advance(
        cog, interaction, _entry(phase), **{target: AsyncMock(side_effect=RuntimeError("x"))}
    )

    replied = _replied(interaction)
    assert "internal error" in replied
    assert label in replied
    cog.bot.output_router.post_log.assert_not_awaited()


@pytest.mark.parametrize("phase,runner,label,prefix", CLEANUPS)
async def test_a_fired_cleanup_is_logged_by_name(tmp_path, phase, runner, label, prefix):
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(phase))

    assert f"phase: {prefix}" in str(cog.bot.output_router.post_log.await_args.args[0])


# ---------------------------------------------------------------------------
# The weather phases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase,runner", [(1, "run_phase1"), (2, "run_phase2"), (3, "run_phase3")])
async def test_each_weather_phase_reaches_its_own_runner(tmp_path, phase, runner):
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    mocks = await _advance(cog, interaction, _entry(phase))

    mocks[runner].assert_awaited_once_with(ROUND_ID, cog.bot)
    for other in {"run_phase1", "run_phase2", "run_phase3"} - {runner}:
        mocks[other].assert_not_awaited()
    assert f"Advanced **Phase {phase}**" in _replied(interaction)


async def test_a_weather_phase_cancels_its_job_before_running(tmp_path):
    """Before, not after: a runner that raised would otherwise leave the real job queued to
    fire the same phase again."""
    cog = _make_cog(await _make_db(tmp_path))
    order: list[str] = []
    cog.bot.scheduler_service.cancel_job = MagicMock(side_effect=lambda _j: order.append("cancel"))

    await _advance(
        cog,
        _interaction(),
        _entry(2),
        **{"services.phase2_service.run_phase2": AsyncMock(side_effect=lambda *_: order.append("run"))},
    )

    assert order == ["cancel", "run"]


async def test_a_failing_weather_phase_names_the_round_and_track(tmp_path):
    cog = _make_cog(await _make_db(tmp_path))
    interaction = _interaction()

    await _advance(
        cog,
        interaction,
        _entry(1),
        **{"services.phase1_service.run_phase1": AsyncMock(side_effect=RuntimeError("boom"))},
    )

    replied = _replied(interaction)
    assert "internal error occurred while advancing Phase 1" in replied
    assert "Silverstone Circuit" in replied
    cog.bot.output_router.post_log.assert_not_awaited()


async def test_a_fired_weather_phase_is_logged_with_its_track(tmp_path):
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(3))

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert "phase: 3" in logged
    assert "track: Silverstone Circuit" in logged
