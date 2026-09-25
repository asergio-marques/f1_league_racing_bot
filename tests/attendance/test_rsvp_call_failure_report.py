"""A check-in call that does not post must be visible to the league's staff (FR-062).

The hazard this closes predates the image module. ``run_rsvp_notice`` returns on a failed
``channel.send`` **before** ``bulk_insert_attendance_rows``, so the round holds no attendance
rows; the penalty pass iterates those rows and finds none; and the attendance sheet then draws
every cell of that round empty — which means **zero points**. A round nobody was asked to check
in for is recorded as flawless attendance for everyone, and nothing anywhere says otherwise.

Reported to the log channel, it becomes a thing staff can act on.
"""
from __future__ import annotations

import inspect
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.attendance.services import rsvp_service  # noqa: E402
from leaguebot.attendance.services.rsvp_service import _report_call_failure  # noqa: E402


def _bot():
    bot = MagicMock()
    bot.output_router.post_log = AsyncMock()
    return bot


@pytest.mark.asyncio
async def test_the_report_names_the_season_the_division_and_the_round():
    bot = _bot()
    await _report_call_failure(
        bot,
        division_id=7,
        division_name="Division 1",
        season_number=4,
        round_number=11,
        reason="the call could not be posted: 503 Service Unavailable",
    )

    bot.output_router.post_log.assert_awaited_once()
    (content,) = bot.output_router.post_log.await_args.args
    assert "season: 4" in content
    assert "Division 1" in content
    assert "id=7" in content
    assert "round: 11" in content
    assert "503 Service Unavailable" in content


@pytest.mark.asyncio
async def test_the_report_says_the_round_opened_no_attendance_rows():
    """The consequence is what staff need to know, not merely that a send failed."""
    bot = _bot()
    await _report_call_failure(
        bot,
        division_id=7,
        division_name="Division 1",
        season_number=4,
        round_number=11,
        reason="whatever",
    )

    (content,) = bot.output_router.post_log.await_args.args
    assert "no attendance rows were opened" in content


@pytest.mark.asyncio
async def test_a_failure_to_report_never_masks_the_original_failure():
    bot = _bot()
    bot.output_router.post_log = AsyncMock(side_effect=RuntimeError("log channel gone"))

    # Must not raise.
    await _report_call_failure(
        bot,
        division_id=7,
        division_name="Division 1",
        season_number=4,
        round_number=11,
        reason="whatever",
    )


# ── Wiring, and its independence from the images module ───────────────────


def test_both_failure_paths_of_the_notice_report_the_call():
    """A failed send and an unreachable channel are both failures to post."""
    source = inspect.getsource(rsvp_service.run_rsvp_notice)
    assert source.count("_report_call_failure(") == 2


def test_the_report_is_reached_before_the_notice_returns_on_a_failed_send():
    source = inspect.getsource(rsvp_service.run_rsvp_notice)
    after_send_failure = source.split("failed to post embed for division")[1]
    report_at = after_send_failure.index("_report_call_failure(")
    return_at = after_send_failure.index("return")
    assert report_at < return_at


def test_the_report_consults_no_image_module_and_no_toggle():
    """FR-062 holds with the ``rsvp`` toggle off, so it must not sit behind an image check.

    Read from the compiled body rather than the source text — the docstring discusses the
    toggle precisely because the report must ignore it, and a text match would catch that.
    """
    referenced = set(_report_call_failure.__code__.co_names)
    for forbidden in ("image", "toggle", "aspect", "template", "png", "svg", "enqueue"):
        assert not any(forbidden in name.lower() for name in referenced), forbidden


def test_the_report_is_not_guarded_by_any_conditional_in_its_call_sites():
    """Both call sites are unconditional within the failure branch they sit in."""
    source = inspect.getsource(rsvp_service.run_rsvp_notice)
    for fragment in source.split("_report_call_failure(")[:-1]:
        tail = fragment.rsplit("\n", 2)[-2:]
        assert not any(line.strip().startswith("if ") for line in tail), tail


def test_the_note_names_the_command_that_posts_the_call_again():
    """The advice must name a command that exists (#123).

    The note told staff to "post the call again" for as long as there was no command to do it
    with — sound advice nobody could follow, which is the whole of issue #123. It now names
    `/attendance post-check-in` and fills in the division and round, so a manager can copy the
    line out of the log channel. Renaming that command without fixing this note would put the
    bot back to advising the impossible, which is what this test is here to stop.
    """
    source = inspect.getsource(_report_call_failure)
    assert "/attendance post-check-in" in source
    assert "{division_name}" in source and "{round_number}" in source


def test_the_command_the_note_names_is_a_real_one():
    """Pinned against the cog itself, so a rename breaks this rather than a league's recovery."""
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
    from leaguebot.attendance.cogs.attendance_cog import AttendanceCog

    assert "post-check-in" in {c.name for c in AttendanceCog.attendance.commands}


@pytest.mark.asyncio
async def test_a_report_can_carry_a_note_of_its_own():
    """A call given up at a restart, its deadline passed (#429), cannot be posted by hand
    either — the command refuses once the deadline has gone — so its report must not advise it.
    The reporter takes the note it is given in place of the usual advice."""
    bot = _bot()
    await _report_call_failure(
        bot,
        division_id=7,
        division_name="Division 1",
        season_number=4,
        round_number=11,
        reason="whatever",
        note="nothing can be done about it",
    )

    (content,) = bot.output_router.post_log.await_args.args
    assert "  note: nothing can be done about it" in content
    assert "/attendance post-check-in" not in content
