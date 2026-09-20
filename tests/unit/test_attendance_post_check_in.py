"""`/attendance post-check-in` — the check-in call a league can post by hand (issue #123).

A check-in call used to reach a division from its scheduled job, from the recovery that re-arms
it after a restart, and from `/test-mode advance` — and from nowhere a league could reach. So a
call that failed to post stayed missed, and a round with no call records **perfect attendance
for the whole division**: no attendance rows are opened, the penalty pass iterates none, and
the sheet draws every cell empty. `_report_call_failure` told the manager to post the call
again and no command existed to do it.

Three rules decided with the user on 2026-09-20 shape what this command will and will not do,
and each has a test here that fails the moment somebody relaxes it:

**A call already standing is never replaced** — `test_a_call_already_standing_is_refused`. A
second call beside the first would split a division's answers across two messages, and the path
that legitimately replaces one is an amendment, which carries every answer across.

**It cannot post a call early** — `test_a_call_not_yet_due_is_refused`. Before the call is due
the scheduled one is still coming, and posting now would quietly override the lead time the
league configured with `/attendance config rsvp-notice`.

**It cannot post a call nobody could answer** — `test_a_round_past_its_deadline_is_refused`.
The buttons lock at the deadline, so a call posted after it arrives dead.

Together the last two confine the command to exactly the window in which a call *should* be
standing and is not, which is the whole of its purpose.

Every test that turns on a moment builds the round's `scheduled_at` from a fixed `now` it also
controls, so none of them can pass today and fail next month.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.attendance_cog import AttendanceCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from models.season import SeasonStage  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402
from utils.channel_guard import LEAGUE_MANAGER, TIER_ATTRIBUTE  # noqa: E402

SERVER_ID = 4242
SEASON_ID = 1
DIVISION_ID = 1
ROUND_ID = 1

#: The round sits five days and a little ahead of this, so the call is due and the deadline is
#: not. Fixed rather than taken from the clock: these tests are *about* the moment.
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

NOTICE_DAYS = 5
DEADLINE_HOURS = 2


async def _make_db(tmp_path, *, scheduled_at: datetime, status: str = "NOT_RUN") -> str:
    """A migrated database with one division and one round at *scheduled_at*."""
    db_path = os.path.join(str(tmp_path), "post_check_in.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO attendance_config (id, rsvp_notice_days, rsvp_deadline_hours) "
            "VALUES (1, ?, ?)",
            (NOTICE_DAYS, DEADLINE_HOURS),
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
            "INSERT INTO rounds "
            "(id, division_id, round_number, format, track_name, scheduled_at, status) "
            "VALUES (?, ?, 1, 'NORMAL', 'Silverstone Circuit', ?, ?)",
            (ROUND_ID, DIVISION_ID, scheduled_at.isoformat(), status),
        )
        await db.commit()
    return db_path


def _cog(db_path: str, *, stage=SeasonStage.ONGOING) -> AttendanceCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    season = None if stage is None else SimpleNamespace(id=SEASON_ID, stage=stage)
    bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    bot.season_service.get_divisions = AsyncMock(
        return_value=[SimpleNamespace(id=DIVISION_ID, name="Division 1")]
    )
    bot.attendance_service.get_config = AsyncMock(
        return_value=SimpleNamespace(
            rsvp_notice_days=NOTICE_DAYS, rsvp_deadline_hours=DEADLINE_HOURS
        )
    )
    bot.output_router.post_log = AsyncMock()
    return AttendanceCog(bot)


def _interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Manager"
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _record_call(db_path: str) -> None:
    """Record a check-in call as standing for the round."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rsvp_embed_messages "
            "(round_id, division_id, message_id, channel_id, posted_at) "
            "VALUES (?, ?, '9001', '7001', ?)",
            (ROUND_ID, DIVISION_ID, NOW.isoformat()),
        )
        await db.commit()


async def _invoke(cog, interaction, *, division="division 1", round=1, now=NOW, posts=False):
    """Run the command with the clock pinned, standing in for `run_rsvp_notice`.

    *posts* says whether the stand-in should record a call, which is how the command decides
    between reporting a success and a failure.
    """
    db_path = cog.bot.db_path

    async def _fake_notice(round_id, bot):
        if posts:
            await _record_call(db_path)

    clock = MagicMock(wraps=datetime)
    clock.now = MagicMock(return_value=now)
    with patch("cogs.attendance_cog.datetime", clock), patch(
        "services.rsvp_service.run_rsvp_notice", new=AsyncMock(side_effect=_fake_notice)
    ) as notice:
        await undecorate(AttendanceCog.post_check_in)(cog, interaction, division, round)
    return notice


def _replied(interaction) -> str:
    return "\n".join(str(c.args[0]) for c in interaction.followup.send.await_args_list)


def _logged(cog) -> str:
    return "\n".join(str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list)


async def _rows(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM rsvp_embed_messages WHERE round_id = ?", (ROUND_ID,)
        )
        return (await cur.fetchone())["n"]


# ---------------------------------------------------------------------------
# The tier
# ---------------------------------------------------------------------------


async def test_the_command_is_a_league_manager_s():
    """A manager's, like `/attendance sync` — the other repair command in the group."""
    assert getattr(AttendanceCog.post_check_in.callback, TIER_ATTRIBUTE) == LEAGUE_MANAGER


# ---------------------------------------------------------------------------
# The refusals
# ---------------------------------------------------------------------------


async def test_it_is_refused_while_the_module_is_off(tmp_path):
    db_path = await _make_db(tmp_path, scheduled_at=NOW + timedelta(days=1))
    cog = _cog(db_path)
    cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    interaction = _interaction()

    notice = await _invoke(cog, interaction)

    notice.assert_not_awaited()
    assert "not enabled" in str(interaction.response.send_message.await_args.args[0])


@pytest.mark.parametrize(
    "stage", [None, SeasonStage.PLACEMENTS, SeasonStage.PENDING_COMPLETION]
)
async def test_it_is_refused_outside_the_ongoing_stages(tmp_path, stage):
    db_path = await _make_db(tmp_path, scheduled_at=NOW + timedelta(days=1))
    cog = _cog(db_path, stage=stage)
    interaction = _interaction()

    notice = await _invoke(cog, interaction)

    notice.assert_not_awaited()
    assert "only while the season is ongoing" in _replied(interaction)


async def test_an_unknown_division_is_refused(tmp_path):
    db_path = await _make_db(tmp_path, scheduled_at=NOW + timedelta(days=1))
    cog = _cog(db_path)
    interaction = _interaction()

    notice = await _invoke(cog, interaction, division="Division 9")

    notice.assert_not_awaited()
    assert "not found" in _replied(interaction)


async def test_an_unknown_round_is_refused(tmp_path):
    db_path = await _make_db(tmp_path, scheduled_at=NOW + timedelta(days=1))
    cog = _cog(db_path)
    interaction = _interaction()

    notice = await _invoke(cog, interaction, round=7)

    notice.assert_not_awaited()
    assert "no round 7" in _replied(interaction)


async def test_a_cancelled_round_is_refused(tmp_path):
    """A round that is off has no check-in to answer, so there is nothing to post."""
    db_path = await _make_db(
        tmp_path, scheduled_at=NOW + timedelta(days=1), status="CANCELLED"
    )
    cog = _cog(db_path)
    interaction = _interaction()

    notice = await _invoke(cog, interaction)

    notice.assert_not_awaited()
    assert "cancelled" in _replied(interaction)


async def test_a_call_already_standing_is_refused(tmp_path):
    """Decision 1: a standing call is never replaced, and the refusal says what to do instead.

    A second call beside the first splits a division's answers across two messages. An
    amendment is the path that takes one down and carries the answers over.
    """
    db_path = await _make_db(tmp_path, scheduled_at=NOW + timedelta(days=1))
    await _record_call(db_path)
    cog = _cog(db_path)
    interaction = _interaction()

    notice = await _invoke(cog, interaction)

    notice.assert_not_awaited()
    reply = _replied(interaction)
    assert "already standing" in reply
    assert "Amend the round" in reply


async def test_a_call_not_yet_due_is_refused(tmp_path):
    """Decision 3: the command cannot post a call ahead of its configured lead time.

    The round is six days out and the notice is five, so the scheduled call is still coming.
    Posting now would silently override `/attendance config rsvp-notice`.
    """
    db_path = await _make_db(tmp_path, scheduled_at=NOW + timedelta(days=NOTICE_DAYS + 1))
    cog = _cog(db_path)
    interaction = _interaction()

    notice = await _invoke(cog, interaction)

    notice.assert_not_awaited()
    assert "not due" in _replied(interaction)
    assert await _rows(db_path) == 0


async def test_a_call_exactly_due_is_posted(tmp_path):
    """The boundary belongs to the window: at the due moment the call is late, not early."""
    db_path = await _make_db(tmp_path, scheduled_at=NOW + timedelta(days=NOTICE_DAYS))
    cog = _cog(db_path)
    interaction = _interaction()

    notice = await _invoke(cog, interaction, posts=True)

    notice.assert_awaited_once()


async def test_a_round_past_its_deadline_is_refused(tmp_path):
    """A call posted after the deadline arrives with its buttons already locked."""
    db_path = await _make_db(
        tmp_path, scheduled_at=NOW + timedelta(hours=DEADLINE_HOURS - 1)
    )
    cog = _cog(db_path)
    interaction = _interaction()

    notice = await _invoke(cog, interaction)

    notice.assert_not_awaited()
    assert "closed at" in _replied(interaction)
    assert await _rows(db_path) == 0


# ---------------------------------------------------------------------------
# Posting it
# ---------------------------------------------------------------------------


async def test_it_posts_the_call_for_the_named_round(tmp_path):
    """The defect itself: a league can now reach `run_rsvp_notice`, which opens the rows."""
    db_path = await _make_db(tmp_path, scheduled_at=NOW + timedelta(days=1))
    cog = _cog(db_path)
    interaction = _interaction()

    notice = await _invoke(cog, interaction, posts=True)

    notice.assert_awaited_once()
    assert notice.await_args.args[0] == ROUND_ID
    assert "posted for round 1" in _replied(interaction)


async def test_a_call_that_fails_to_post_is_reported_as_failed(tmp_path):
    """`run_rsvp_notice` swallows its own faults, so success is judged by what stands after.

    Without this the command would answer a failed post with a tick, which is exactly the kind
    of silent wrong record the whole issue is about.
    """
    db_path = await _make_db(tmp_path, scheduled_at=NOW + timedelta(days=1))
    cog = _cog(db_path)
    interaction = _interaction()

    await _invoke(cog, interaction, posts=False)

    reply = _replied(interaction)
    assert "could not be posted" in reply
    assert "✅" not in reply
    assert "| Failed" in _logged(cog)


async def test_the_run_is_written_to_the_log_channel(tmp_path):
    db_path = await _make_db(tmp_path, scheduled_at=NOW + timedelta(days=1))
    cog = _cog(db_path)
    interaction = _interaction()

    await _invoke(cog, interaction, posts=True)

    logged = _logged(cog)
    assert "/attendance post-check-in | Success" in logged
    assert "Division 1" in logged
    assert "round: 1" in logged
