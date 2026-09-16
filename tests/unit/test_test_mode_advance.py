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
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# Aliased on import: pytest tries to collect any module-level name starting with `Test`
# as a test class, and warns that it cannot because the cog has an `__init__`.
from cogs.test_mode_cog import TestModeCog as _Cog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
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
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID, SERVER_ID),
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


async def test_a_fired_phase_cancels_its_own_job(tmp_path):
    """Otherwise the real job stays queued and fires again later, posting the same notice
    a second time."""
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(0, job_id="mystery_r5"))

    cog.bot.scheduler_service.cancel_job.assert_called_once_with("mystery_r5")


async def test_a_phase_with_no_job_cancels_nothing(tmp_path):
    """An event whose job already fired, or was never armed — cancelling `None` would
    raise inside the scheduler."""
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(0, job_id=None))

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


async def test_the_results_job_is_cancelled_before_the_wizard_opens(tmp_path):
    """A weather-enabled season carries a real future-dated results job; left armed it
    would open a second submission channel after advance had already opened one."""
    cog = _make_cog(await _make_db(tmp_path))

    await _advance(cog, _interaction(), _entry(4))

    cog.bot.scheduler_service.cancel_job.assert_called_once_with(f"results_r{ROUND_ID}")


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
