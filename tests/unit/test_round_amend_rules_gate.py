"""`/round amend` refuses what the rules refuse, and says what an amendment costs.

The rules themselves are tested in `test_amendment_rules_service.py`, which drives them pure.
These drive the *command*: that it asks, that a refusal reaches the manager instead of a
confirmation, and that nothing is offered for an amendment the rules will not allow. A gate that
decides correctly and is never consulted is the failure this file is here to catch.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from services.season_service import SeasonService  # noqa: E402

SERVER_ID = 4400
USER_ID = 88
DIVISION = "Div A"
#: Seeded by migration 029, so `resolve_track_name` finds it in earnest.
NEW_TRACK = "Silverstone Circuit"


async def _db(tmp_path, *, scheduled_at: datetime, phase1_done: int = 0) -> str:
    path = str(tmp_path / "round_amend.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number) "
            "VALUES (1, ?, '2026-01-01', 'ACTIVE', 1)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, forecast_channel_id, mention_role_id) "
            "VALUES (1, 1, ?, 1, 999, 555)",
            (DIVISION,),
        )
        await db.execute(
            "INSERT INTO rounds "
            "(id, division_id, round_number, format, track_name, scheduled_at, phase1_done) "
            "VALUES (1, 1, 1, 'NORMAL', 'Bahrain International Circuit', ?, ?)",
            (scheduled_at.isoformat(), phase1_done),
        )
        await db.commit()
    return path


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = USER_ID
    interaction.user.display_name = "Manager"
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _cog(db_path, *, attendance: bool = True):
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = AsyncMock()
    cog.bot.db_path = db_path
    # A real season service, so the round comes back carrying the status and phase flags the
    # rules read. Stubbing it would leave this testing the stub.
    cog.bot.season_service = SeasonService(db_path)
    cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance)
    cog.bot.attendance_service.get_or_create_config = AsyncMock(
        return_value=SimpleNamespace(
            rsvp_notice_days=5, rsvp_last_notice_hours=24, rsvp_deadline_hours=2
        )
    )
    # No pending setup, so the command takes its active-season path.
    cog._get_pending_for_server = MagicMock(return_value=None)
    return cog


async def _amend(cog, interaction, **kwargs):
    """Drive the command past its permission guard.

    `league_manager_only` wraps the callback in the channel-and-role guard, which has its own
    tests and is not what these are about — satisfying it here would mean building a member,
    a role and a server config in every one of them to reach the line under test. `functools
    .wraps` leaves the undecorated function on ``__wrapped__``, so the guard is stepped over
    rather than stubbed out.
    """
    await SeasonCog.round_amend.callback.__wrapped__(cog, interaction, DIVISION, 1, **kwargs)


def _reply(interaction) -> str:
    return "\n".join(
        str(call.args[0]) for call in interaction.followup.send.await_args_list if call.args
    )


def _offered_a_confirmation(interaction) -> bool:
    return any(
        call.kwargs.get("view") is not None
        for call in interaction.followup.send.await_args_list
    )


# ---------------------------------------------------------------------------
# What the command refuses
# ---------------------------------------------------------------------------


async def test_a_round_that_has_started_refuses_a_track_amendment(tmp_path):
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) - timedelta(hours=1))
    interaction = _interaction()

    await _amend(_cog(path), interaction, track=NEW_TRACK)

    assert "already started" in _reply(interaction)
    assert not _offered_a_confirmation(interaction), "a refused amendment must not be offered"


async def test_bringing_a_round_inside_its_check_in_deadline_is_refused(tmp_path):
    """The round would have a check-in that opens and closes in the same instant."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    interaction = _interaction()
    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)

    await _amend(_cog(path), interaction, scheduled_at=soon.isoformat())

    assert "check-in deadline" in _reply(interaction)
    assert not _offered_a_confirmation(interaction)


async def test_moving_a_round_backwards_is_refused_with_attendance_off(tmp_path):
    """The command consults the rule, and with no module standing in for it.

    Attendance off deliberately: its deadline rule refuses a backwards move as well, so a test
    that left the module on would pass whether or not the command reached the rule at all.
    """
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    interaction = _interaction()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)

    await _amend(_cog(path, attendance=False), interaction, scheduled_at=past.isoformat())

    assert "cannot be moved into the past" in _reply(interaction)
    assert not _offered_a_confirmation(interaction), "a refused amendment must not be offered"


async def test_a_refusal_says_nothing_was_changed(tmp_path):
    """The manager must not be left wondering whether half of it went through."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) - timedelta(hours=1))
    interaction = _interaction()

    await _amend(_cog(path), interaction, track=NEW_TRACK)

    assert "Nothing has been changed" in _reply(interaction)
    # And the round really is untouched.
    async with get_connection(path) as db:
        cursor = await db.execute("SELECT track_name FROM rounds WHERE id = 1")
        assert (await cursor.fetchone())["track_name"] == "Bahrain International Circuit"


async def test_the_deadline_is_not_judged_while_attendance_is_disabled(tmp_path):
    """With no check-in to lose, the amendment the deadline would have refused goes ahead."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    interaction = _interaction()
    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)

    await _amend(_cog(path, attendance=False), interaction, scheduled_at=soon.isoformat())

    assert _offered_a_confirmation(interaction)


# ---------------------------------------------------------------------------
# What the confirmation says it will cost
# ---------------------------------------------------------------------------


async def test_an_allowed_amendment_is_offered_for_confirmation(tmp_path):
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    interaction = _interaction()

    await _amend(_cog(path), interaction, track=NEW_TRACK)

    assert _offered_a_confirmation(interaction)
    assert "Amend Round 1" in _reply(interaction)


async def test_the_summary_names_the_forecasts_it_will_withdraw(tmp_path):
    """A round a month out has drawn nothing yet, so all three phases are still to come."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    interaction = _interaction()

    await _amend(_cog(path), interaction, track=NEW_TRACK)

    reply = _reply(interaction)
    assert "Phase 1" in reply and "Phase 2" in reply and "Phase 3" in reply


async def test_the_summary_does_not_claim_to_withdraw_a_forecast_that_stands(tmp_path):
    """The old summary told every league its forecasts would be invalidated, always.

    A round two hours out has had all three phases drawn and would have had them drawn under its
    new moment too, so nothing is withdrawn — and saying otherwise would be a promise to redraw
    a forecast that is about to be left exactly as it is.
    """
    now = datetime.now(timezone.utc)
    path = await _db(tmp_path, scheduled_at=now + timedelta(hours=4), phase1_done=1)
    interaction = _interaction()
    # Brought an hour nearer. Phase 3 falls two hours before the round, so at three hours out
    # its horizon is still behind us and every earlier phase's is too — nothing is withdrawn.
    # Pushing the round *later* would move Phase 3's horizon back into the future and withdraw
    # it, which is the opposite case and is covered above.
    nearer = (now + timedelta(hours=1)).replace(tzinfo=None)

    await _amend(_cog(path, attendance=False), interaction, scheduled_at=nearer.isoformat())

    reply = _reply(interaction)
    assert "will stand" in reply
    assert "withdraw" not in reply


# ---------------------------------------------------------------------------
# The rules are judged again when the amendment is confirmed
# ---------------------------------------------------------------------------
#
# `_ConfirmView` stands for two minutes. The world can move inside them, and an amendment
# allowed on the strength of a window that has since closed is the silent loss these rules
# exist to prevent. Every test below constructs a View, so every one is `async def`: apt's
# discord.py 2.5.0 calls `asyncio.get_running_loop()` in `View.__init__`.


def _view(cog, amendments):
    from cogs.season_cog import _ConfirmView

    return _ConfirmView(
        cog=cog, interaction_user_id=USER_ID, round_id=1, amendments=amendments
    )


async def test_a_window_passing_while_the_confirmation_stands_refuses_it(tmp_path):
    """Offered while the check-in deadline was ahead, pressed once it is behind.

    The round sits an hour out, so its deadline — two hours before it — has already gone by.
    Confirming would apply an amendment the command itself would no longer offer.
    """
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1))
    cog = _cog(path)
    cog.bot.amendment_service.amend_round = AsyncMock()
    interaction = _interaction()

    await _view(cog, [("track_name", NEW_TRACK)]).confirm.callback(interaction)

    assert "can no longer be amended" in _reply(interaction)
    cog.bot.amendment_service.amend_round.assert_not_awaited()


async def test_results_entered_while_the_confirmation_stands_refuses_it(tmp_path):
    """The other way the world moves: the round's results come in before the button is pressed."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    async with get_connection(path) as db:
        await db.execute(
            "UPDATE rounds SET status = 'AWAITING_REPORT_VERDICTS' WHERE id = 1"
        )
        await db.commit()
    cog = _cog(path)
    cog.bot.amendment_service.amend_round = AsyncMock()
    interaction = _interaction()

    await _view(cog, [("track_name", NEW_TRACK)]).confirm.callback(interaction)

    assert "results have been entered" in _reply(interaction)
    cog.bot.amendment_service.amend_round.assert_not_awaited()


async def test_a_confirmation_the_rules_still_allow_goes_through(tmp_path):
    """The gate must not cost a manager an amendment that is still perfectly good."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    cog = _cog(path)
    cog.bot.amendment_service.amend_round = AsyncMock()
    interaction = _interaction()

    await _view(cog, [("track_name", NEW_TRACK)]).confirm.callback(interaction)

    cog.bot.amendment_service.amend_round.assert_awaited_once()
    # And the whole change set went in one call, not one call per field.
    assert cog.bot.amendment_service.amend_round.await_args.args[2] == [("track_name", NEW_TRACK)]
