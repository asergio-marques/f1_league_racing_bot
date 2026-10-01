"""`handle_rsvp_button` — what happens when a driver presses a check-in button.

Issue #208. `tests/attendance/test_attendance_module_gate.py` covers exactly one branch of this
handler, the module gate at its head (issue #114). Everything after that gate — who is allowed
to answer, when an answer locks, and what an answer does — was unexecuted.

That is the busiest surface in the attendance module. Every driver in the league presses one
of these three buttons every round, and the rules underneath decide whether a driver can still
change their mind.

**The locking rules are the substance of this file**, and they are not one rule with different
subjects. `attendance_module_specification.md`:

    Full-time drivers will be allowed to change their chosen option until the RSVP deadline is
    met. After that point, the choices are locked.

    Reserve drivers will be allowed to change their chosen option until the time of the round,
    provided they have NOT accepted the check-in. After that point, the choices are locked.

So three different instants: a full-time driver locks at the deadline; a reserve who has
accepted locks at the deadline too, having been counted on for a seat; and a reserve who has
not accepted locks only at the round start, because stepping in late is the entire point of a
reserve. The configured deadline of zero is a fourth case, and the spec is explicit that it is
not "no lock" —

    A value of 0 means that the deadline lasts up until the scheduled time of the round, which
    no alterations permitted beyond that point.

The handler's own comments cite `FR-014`–`FR-017` for these. Those numbers belong to the
increment spec that built them, which `CLAUDE.md` says is a historical record and not to be
read as current behaviour, so the wip-spec's words are quoted above instead.

Each is pinned by a pair of tests sitting either side of its moment, because the three differ
only in *which* instant they compare against and a later reader collapsing them into one check
would pass any test that only exercised one kind of driver. `test_a_reserve_who_has_not_
accepted_may_still_step_in_after_the_deadline` is the one that would catch it.

**Times are taken from the clock, never pinned, where the handler is driven.** The handler
reads `datetime.now` itself and takes no `now` parameter, so a fixture that seeded a fixed date
would pass today and fail silently once it went by. Every round it is driven against is
scheduled relative to the real present. `AttendanceService.answer_rsvp`, which applies the lock
rules and writes the answer in one transaction (#482), takes `now`, and its tests pin it.

**Two presses at once are run against a real database** (#482). `_HeldWrite` holds the first
press's write open, uncommitted, until the second press reaches its own, so the order is fixed
rather than left to the scheduler: the second press must see the first's answer, not the one
the first is replacing.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.attendance.cogs.attendance_cog import handle_rsvp_button
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.attendance.services.attendance_service import AttendanceService

SERVER_ID = 8508
SEASON_ID = 1
DIVISION_ID = 1
ROUND_ID = 42
RSVP_CHANNEL_ID = 770177

FULL_TIME_PROFILE = 301
RESERVE_PROFILE = 302
STRANGER_PROFILE = 303

#: The configured deadline, in hours before the round.
DEADLINE_HOURS = 6

#: Why a test fails until the answer is decided and written in one transaction.
ANSWER_RSVP = (
    "#482: AttendanceService.answer_rsvp applies the lock rules and writes the answer in one "
    "transaction (F1/F2)"
)

#: The moment `answer_rsvp` is told it is, in the tests that call it directly.
NOW = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    starts_in: timedelta,
    deadline_hours: int = DEADLINE_HOURS,
    call_posted: bool = True,
) -> str:
    """A division with a full-time seat, a reserve seat and a round *starts_in* from now.

    A negative *starts_in* puts the round in the past. Everything is relative to the real
    clock because the handler reads the clock itself.

    *call_posted* seeds the `NO_RSVP` rows that posting the check-in call creates —
    `run_rsvp_notice` calls `bulk_insert_attendance_rows` for the whole roster before any
    button exists to press. Passing False is the driver who has **no** such row: placed into
    the division after the call went out, since nothing outside `run_rsvp_notice` opens one.
    That is issue #209, and the tests below exercise both, because the two answer paths part
    company at the row's existence and nowhere else.
    """
    db_path = os.path.join(str(tmp_path), "rsvp_button.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO attendance_config "
            "(id, module_enabled, rsvp_deadline_hours) VALUES (?, 1, ?)",
            (1, deadline_hours),
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
            "INSERT INTO attendance_division_config "
            "(division_id, rsvp_channel_id) VALUES (?, ?)",
            (DIVISION_ID, str(RSVP_CHANNEL_ID)),
        )
        await db.execute(
            "INSERT INTO rounds "
            "(id, division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (?, ?, 1, 'NORMAL', 'Silverstone Circuit', ?)",
            (ROUND_ID, DIVISION_ID, (datetime.now(timezone.utc) + starts_in).isoformat()),
        )

        # The row `run_rsvp_notice` writes when it posts the call. The handler reads it to
        # know a call is still standing (#175), so a press with no row is one on a call
        # already taken down.
        await db.execute(
            "INSERT INTO rsvp_embed_messages "
            "(round_id, division_id, message_id, channel_id, posted_at) "
            "VALUES (?, ?, '900500', ?, ?)",
            (
                ROUND_ID,
                DIVISION_ID,
                str(RSVP_CHANNEL_ID),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (10, ?, 'Alpha', 'Alpha', 2, 0)",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (11, ?, 'Reserve', 'Reserve', 6, 1)",
            (DIVISION_ID,),
        )
        for profile_id, name in (
            (FULL_TIME_PROFILE, "Full Timer"),
            (RESERVE_PROFILE, "Stand In"),
            (STRANGER_PROFILE, "Outsider"),
        ):
            await db.execute(
                "INSERT INTO driver_profiles "
                "(id, discord_user_id, current_state, is_test_driver, "
                "test_display_name) VALUES (?, ?, 'ACTIVE', 1, ?)",
                (profile_id, str(profile_id), name),
            )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (20, 10, 1, ?)",
            (FULL_TIME_PROFILE,),
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (21, 11, 1, ?)",
            (RESERVE_PROFILE,),
        )
        for profile_id, seat_id in ((FULL_TIME_PROFILE, 20), (RESERVE_PROFILE, 21)):
            await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id, team_seat_id) "
                "VALUES (?, ?, ?, ?)",
                (profile_id, SEASON_ID, DIVISION_ID, seat_id),
            )

        if call_posted:
            for profile_id in (FULL_TIME_PROFILE, RESERVE_PROFILE):
                await db.execute(
                    "INSERT INTO driver_round_attendance "
                    "(round_id, division_id, driver_profile_id, rsvp_status) "
                    "VALUES (?, ?, ?, 'NO_RSVP')",
                    (ROUND_ID, DIVISION_ID, profile_id),
                )
        await db.commit()
    return db_path


async def _set_status(db_path: str, profile_id: int, status: str) -> None:
    """Set an answer on the row the posted call already created."""
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_round_attendance SET rsvp_status = ? "
            "WHERE round_id = ? AND division_id = ? AND driver_profile_id = ?",
            (status, ROUND_ID, DIVISION_ID, profile_id),
        )
        await db.commit()


async def _uncommit_placement(db_path: str, profile_id: int) -> None:
    """Put the driver's placement back to unconfirmed, as a mid-season signup's stands.

    `_make_db` seeds an ACTIVE season, and migration 057's trigger defaults a placement
    written into one to `committed = 1` — which is what keeps every fixture seating a driver
    in a running season meaning what it always meant. Saying otherwise has to be deliberate.
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_season_assignments SET committed = 0 WHERE driver_profile_id = ?",
            (profile_id,),
        )
        await db.commit()


async def _status(db_path: str, profile_id: int) -> str | None:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT rsvp_status FROM driver_round_attendance "
            "WHERE round_id = ? AND driver_profile_id = ?",
            (ROUND_ID, profile_id),
        )
        row = await cursor.fetchone()
    return row["rsvp_status"] if row else None


async def _accepted_at(db_path: str, profile_id: int) -> str | None:
    """The driver's `accepted_at`, which is also None where they hold no row at all."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT accepted_at FROM driver_round_attendance "
            "WHERE round_id = ? AND driver_profile_id = ?",
            (ROUND_ID, profile_id),
        )
        row = await cursor.fetchone()
    return row["accepted_at"] if row else None


def _make_interaction(db_path: str, profile_id: int) -> MagicMock:
    """A pressed button, from *profile_id*, on a bot wired to *db_path*.

    The driver's Discord id and their profile id are the same number in this fixture, which
    is what `_make_db` seeds — it keeps the lookup by Discord id honest without a second
    mapping to keep in step.
    """
    bot = MagicMock()
    bot.db_path = db_path
    bot.attendance_service = AttendanceService(db_path)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.get_channel = MagicMock(return_value=None)
    bot.output_router.post_log = AsyncMock(return_value=None)

    interaction = MagicMock()
    interaction.client = bot
    interaction.guild_id = SERVER_ID
    interaction.user.id = profile_id
    interaction.user.display_name = "Driver"
    interaction.response.send_message = AsyncMock(return_value=None)
    interaction.response.is_done = MagicMock(
        side_effect=lambda: bool(interaction.response.send_message.await_count)
    )
    return interaction


def _reply(interaction: MagicMock) -> str:
    return str(interaction.response.send_message.await_args.args[0])


async def _ignore_answers(db_path: str) -> None:
    """Make every write of an answer change nothing, without raising.

    The way an answer goes unrecorded with no error: the statement runs and writes no row.
    Triggers on the real table, rather than a stubbed service, so the press runs its real path.
    """
    async with get_connection(db_path) as db:
        for event in ("INSERT", "UPDATE"):
            await db.execute(
                f"CREATE TRIGGER ignore_answer_{event.lower()} BEFORE {event} "
                "ON driver_round_attendance BEGIN SELECT RAISE(IGNORE); END"
            )
        await db.commit()


def _writes_an_answer(sql: str) -> bool:
    statement = " ".join(sql.split()).upper()
    return statement.startswith("BEGIN IMMEDIATE") or (
        statement.startswith(("INSERT", "UPDATE")) and "DRIVER_ROUND_ATTENDANCE" in statement
    )


class _HeldWrite:
    """Holds the first answer's write open, uncommitted, until a second press reaches its own.

    The first connection to begin writing an answer is the first press's. Its commit waits
    until another connection begins writing an answer, so the second press runs while the
    first holds the write lock and has not saved. `after_the_first` starts the second press
    only once the first holds it.
    """

    def __init__(self, monkeypatch) -> None:
        import aiosqlite

        self.first = None
        self.holding = asyncio.Event()
        self.second_reached = asyncio.Event()
        real_execute = aiosqlite.Connection.execute
        real_commit = aiosqlite.Connection.commit
        held = self

        def execute(connection, sql, *args, **kwargs):
            if _writes_an_answer(sql):
                if held.first is None:
                    held.first = connection
                elif connection is not held.first:
                    held.second_reached.set()
            return real_execute(connection, sql, *args, **kwargs)

        async def commit(connection):
            if connection is held.first and not held.holding.is_set():
                held.holding.set()
                await asyncio.wait_for(held.second_reached.wait(), timeout=10)
            return await real_commit(connection)

        monkeypatch.setattr(aiosqlite.Connection, "execute", execute)
        monkeypatch.setattr(aiosqlite.Connection, "commit", commit)

    async def after_the_first(self, press):
        await asyncio.wait_for(self.holding.wait(), timeout=10)
        return await press()


# ---------------------------------------------------------------------------
# The custom id
# ---------------------------------------------------------------------------


async def test_an_unparseable_button_id_says_so_rather_than_raising(tmp_path):
    """The id is round-tripped through Discord and comes back as whatever is on the button.
    A malformed one must not take the interaction down with an unhandled exception."""
    interaction = _make_interaction(await _make_db(tmp_path, starts_in=timedelta(days=3)), 1)

    await handle_rsvp_button(interaction, "nonsense")

    assert "invalid button ID" in _reply(interaction)


async def test_an_unknown_action_says_so(tmp_path):
    """Parseable, but not one of the three buttons — a button from an older version of the
    bot still sitting on a message in the channel."""
    interaction = _make_interaction(await _make_db(tmp_path, starts_in=timedelta(days=3)), 1)

    await handle_rsvp_button(interaction, f"rsvp_maybe_r{ROUND_ID}")

    assert "unknown action" in _reply(interaction)


# ---------------------------------------------------------------------------
# FR-011 — who may answer
# ---------------------------------------------------------------------------


async def test_someone_with_no_driver_profile_is_turned_away(tmp_path):
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    interaction = _make_interaction(db_path, 999999)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert "not registered as a driver" in _reply(interaction)


async def test_a_call_no_longer_standing_records_no_answer(tmp_path):
    """A cancelled season takes its calls down several steps before its rounds are recorded
    cancelled; a failure in between must not leave a withdrawn call still answerable."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    before = await _status(db_path, FULL_TIME_PROFILE)
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM rsvp_embed_messages WHERE round_id = ?", (ROUND_ID,))
        await db.commit()
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert "no longer open" in _reply(interaction)
    assert await _status(db_path, FULL_TIME_PROFILE) == before


async def test_a_cancelled_round_records_no_answer(tmp_path):
    """Its call is taken down with the cancellation (#175); a press that raced it, or lands
    on a call the bot could not delete, must not record an answer to a round that is off."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    before = await _status(db_path, FULL_TIME_PROFILE)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", (ROUND_ID,))
        await db.commit()
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert "no longer open" in _reply(interaction)
    assert await _status(db_path, FULL_TIME_PROFILE) == before


async def test_a_driver_of_another_division_is_turned_away(tmp_path):
    """A driver of the server, but with no seat in this division — the call is posted in a
    channel they may well be able to see."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    interaction = _make_interaction(db_path, STRANGER_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert "not a member of this division" in _reply(interaction)


async def test_a_driver_whose_placement_is_not_confirmed_is_turned_away(tmp_path):
    """Only a driver with a *confirmed* placement in the division may answer its call.

    An unconfirmed placement stands outside the championship until `/season
    placements-review` confirms it (issue #220) — no roles, no lineup, no check-in, no
    attendance points — and `handle_rsvp_button` was the one reader of the championship that
    walked the seats without `uncommitted_seat_excluded`. It did not show while
    `upsert_rsvp_status` silently discarded every answer it held no row for; the moment that
    began opening rows, the predicate became what stops it opening one here (issue #209).

    **The same refusal as every other way of not being a driver of this division**, together
    with the two tests above: an unconfirmed placement, a seat in another division, and no
    profile at all are one rule with one message. Telling them apart would serve nobody — a
    league manager who does not drive and presses a button out of curiosity is in exactly the
    same position — and who can see a check-in channel in the first place is the league's own
    permissions to set, which this bot does not touch."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3), call_posted=False)
    await _uncommit_placement(db_path, FULL_TIME_PROFILE)
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert "not a member of this division" in _reply(interaction)
    assert await _status(db_path, FULL_TIME_PROFILE) is None


async def test_a_button_for_a_deleted_round_says_so(tmp_path):
    """A call outlives its round if the round is removed by an amendment."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, "rsvp_accept_r9999")

    assert "no longer exists" in _reply(interaction)


# ---------------------------------------------------------------------------
# Recording an answer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action,expected",
    [("accept", "ACCEPTED"), ("tentative", "TENTATIVE"), ("decline", "DECLINED")],
)
async def test_each_button_records_its_own_status(tmp_path, action, expected):
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_{action}_r{ROUND_ID}")

    assert await _status(db_path, FULL_TIME_PROFILE) == expected
    assert "has been updated" in _reply(interaction)


async def test_a_driver_may_change_their_answer_before_the_deadline(tmp_path):
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    await _set_status(db_path, FULL_TIME_PROFILE, "ACCEPTED")
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_decline_r{ROUND_ID}")

    assert await _status(db_path, FULL_TIME_PROFILE) == "DECLINED"


async def test_pressing_the_button_already_pressed_is_a_no_op(tmp_path):
    """Says so rather than rewriting the row. `attendance_module_specification.md`: "Every
    time a reserve changes RSVP status to accepted, the time will be updated. So flip-flopping
    on attendance is bad." Rewriting on a no-op would move `accepted_at` without the driver
    having changed anything, and with it their place in the distribution order."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    await _set_status(db_path, FULL_TIME_PROFILE, "ACCEPTED")
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert "already marked as" in _reply(interaction)


# ---------------------------------------------------------------------------
# FR-014 — a full-time driver locks at the deadline
# ---------------------------------------------------------------------------


async def test_a_full_time_driver_may_answer_before_the_deadline(tmp_path):
    db_path = await _make_db(tmp_path, starts_in=timedelta(hours=DEADLINE_HOURS + 2))
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert await _status(db_path, FULL_TIME_PROFILE) == "ACCEPTED"


async def test_a_full_time_driver_is_locked_out_after_the_deadline(tmp_path):
    """Sits an hour the other side of the deadline from the test above. The round has not
    started — only the deadline has gone by — which is exactly the case the rule governs."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(hours=DEADLINE_HOURS - 1))
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert "deadline has passed" in _reply(interaction)
    assert await _status(db_path, FULL_TIME_PROFILE) == "NO_RSVP"


async def test_with_no_deadline_configured_a_full_time_driver_locks_at_the_start(tmp_path):
    """A deadline of zero is not "no lock" — the spec makes the round start the lock."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(hours=-1), deadline_hours=0)
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert "deadline has passed" in _reply(interaction)


async def test_with_no_deadline_configured_answers_stay_open_until_the_start(tmp_path):
    db_path = await _make_db(tmp_path, starts_in=timedelta(hours=1), deadline_hours=0)
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert await _status(db_path, FULL_TIME_PROFILE) == "ACCEPTED"


# ---------------------------------------------------------------------------
# FR-015 / FR-016 — a reserve locks differently, and on what they already said
# ---------------------------------------------------------------------------


async def test_a_reserve_who_accepted_is_locked_out_after_the_deadline(tmp_path):
    """They have been counted on for a seat; withdrawing after the deadline is the case the
    "provided they have NOT accepted the check-in" clause exists to prevent."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(hours=DEADLINE_HOURS - 1))
    await _set_status(db_path, RESERVE_PROFILE, "ACCEPTED")
    interaction = _make_interaction(db_path, RESERVE_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_decline_r{ROUND_ID}")

    assert "already accepted" in _reply(interaction)
    assert await _status(db_path, RESERVE_PROFILE) == "ACCEPTED"


async def test_a_reserve_who_has_not_accepted_may_still_step_in_after_the_deadline(tmp_path):
    """The test that would catch the three locking rules being collapsed into one. The deadline has gone by and the round has not started; a full-time driver would be
    refused here and a reserve must not be, because stepping in late is what a reserve is
    for."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(hours=DEADLINE_HOURS - 1))
    await _set_status(db_path, RESERVE_PROFILE, "NO_RSVP")
    interaction = _make_interaction(db_path, RESERVE_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert await _status(db_path, RESERVE_PROFILE) == "ACCEPTED"


async def test_a_reserve_with_no_answer_at_all_may_step_in_after_the_deadline(tmp_path):
    """The same rule reached with no `driver_round_attendance` row, where the status falls
    back to `NO_RSVP` rather than being read.

    Issue #209: this said so and seeded a row anyway, which made it a duplicate of the test
    above and left the no-row path — the reserve moved into the division after the call went
    out, then called upon when somebody dropped — unexecuted."""
    db_path = await _make_db(
        tmp_path, starts_in=timedelta(hours=DEADLINE_HOURS - 1), call_posted=False
    )
    interaction = _make_interaction(db_path, RESERVE_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert await _status(db_path, RESERVE_PROFILE) == "ACCEPTED"


async def test_a_reserve_is_locked_out_once_the_round_has_started(tmp_path):
    """The other side of the reserve's rule. Late is allowed; after the lights have gone out
    is not."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(hours=-1))
    interaction = _make_interaction(db_path, RESERVE_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert "round has started" in _reply(interaction)
    assert await _status(db_path, RESERVE_PROFILE) == "NO_RSVP"


# ---------------------------------------------------------------------------
# A driver placed into the division after the call went out — issue #209
#
# `run_rsvp_notice` opens a `driver_round_attendance` row per driver of the division *at that
# moment*, and nothing else in the bot opens one: not `assign_driver`, not `move_driver`, not
# `commit_mid_season_placements`. So a driver who arrives while the call is standing has none
# — and they are asked anyway, because the embed is rebuilt from the current roster on every
# press and lists them with an empty bracket.
#
# The lock rules above are unaffected and must stay so: all three checks run before the write,
# so a row is never created for an answer that was refused.
# ---------------------------------------------------------------------------


async def test_a_driver_placed_after_the_call_has_their_answer_recorded(tmp_path):
    """The defect itself. The answer was discarded in silence and the driver thanked for it,
    leaving the call showing them as never having replied."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3), call_posted=False)
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert await _status(db_path, FULL_TIME_PROFILE) == "ACCEPTED"
    assert await _accepted_at(db_path, FULL_TIME_PROFILE) is not None
    assert "has been updated" in _reply(interaction)


async def test_an_answer_recorded_for_a_late_joiner_leaves_accepted_at_null_when_declined(
    tmp_path,
):
    """A row created at answer time takes the ordinary `accepted_at` rule and not a special
    case of it. The reserve distribution orders by that column, so a row born carrying a
    timestamp it never earned would jump a queue it should be at the back of."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3), call_posted=False)
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_decline_r{ROUND_ID}")

    assert await _status(db_path, FULL_TIME_PROFILE) == "DECLINED"
    assert await _accepted_at(db_path, FULL_TIME_PROFILE) is None


async def test_a_driver_placed_after_the_deadline_is_locked_out_and_gets_no_row(tmp_path):
    """The insert must not move in front of the locks. A full-time driver placed once the
    deadline has gone by is refused as any other would be, and refusing has to leave the
    round's attendance record alone — a row saying ACCEPTED for an answer the bot would not
    take is worse than the silence it replaced."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(hours=1), call_posted=False)
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert "deadline has passed" in _reply(interaction)
    assert await _status(db_path, FULL_TIME_PROFILE) is None


async def test_an_answer_that_writes_nothing_is_not_reported_as_recorded(tmp_path):
    """The other half of issue #209, and the half no upsert can settle on its own.

    A write that changes no rows and a write that succeeded were indistinguishable here: the
    thanks went out either way. The service is stubbed rather than driven to failure because
    there is no longer a way to make it fail honestly — which is the point. The branch has to
    hold for whatever makes the write a no-op next, or the silence comes back.

    The table's triggers make the write a no-op, rather than a stubbed service, so the test
    holds whichever service method writes the answer (#482)."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    await _ignore_answers(db_path)
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert "could not be recorded" in _reply(interaction)
    assert "has been updated" not in _reply(interaction)


# ---------------------------------------------------------------------------
# The embed refresh
# ---------------------------------------------------------------------------


async def test_the_call_is_edited_in_place_when_an_answer_changes(tmp_path):
    """The division reads who is racing off the call itself, so an answer that does not reach
    the embed is an answer nobody can see."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO rsvp_embed_messages "
            "(round_id, division_id, message_id, channel_id, posted_at) "
            "VALUES (?, ?, '900500', ?, ?)",
            (
                ROUND_ID,
                DIVISION_ID,
                str(RSVP_CHANNEL_ID),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()

    message = MagicMock()
    message.edit = AsyncMock(return_value=None)
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)

    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)
    interaction.client.get_channel = MagicMock(return_value=channel)

    with patch(
        "leaguebot.attendance.services.rsvp_service._rebuild_embed_for_round",
        new=AsyncMock(return_value=MagicMock()),
    ):
        await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    message.edit.assert_awaited_once()
    assert await _status(db_path, FULL_TIME_PROFILE) == "ACCEPTED"


async def test_an_answer_is_still_recorded_when_the_call_cannot_be_found(tmp_path):
    """The answer is the thing that matters. A call deleted by hand must not cost a driver
    their check-in."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert await _status(db_path, FULL_TIME_PROFILE) == "ACCEPTED"
    assert "has been updated" in _reply(interaction)


# ---------------------------------------------------------------------------
# Any account names the driver (issue #243)
# ---------------------------------------------------------------------------


async def test_a_press_from_a_drivers_past_account_answers_for_the_driver(tmp_path):
    """The full-time driver has moved to a new account, and presses from the old one."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    new_account = 9301
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET discord_user_id = ? WHERE id = ?",
            (str(new_account), FULL_TIME_PROFILE),
        )
        await db.commit()
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert await _status(db_path, FULL_TIME_PROFILE) == "ACCEPTED"
    assert "has been updated" in _reply(interaction)


# ---------------------------------------------------------------------------
# Every press is recorded in the log channel (#482; decided 2026-09-29: every driver press is
# logged, each check-in answer included)
# ---------------------------------------------------------------------------

CALL = "of the check-in call for round 1 of Division 1"


def _logged(interaction) -> list[str]:
    return [str(c.args[0]) for c in interaction.client.output_router.post_log.await_args_list]


async def _module_off(db_path, interaction):
    interaction.client.module_service.is_attendance_enabled = AsyncMock(return_value=False)


async def _cancel_round(db_path, interaction):
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", (ROUND_ID,))
        await db.commit()


async def _reserve_accepted(db_path, interaction):
    await _set_status(db_path, RESERVE_PROFILE, "ACCEPTED")


#: Each refusal: its id, when the round starts, who presses, the button's id, what to arrange
#: first, how the line names the press, and the reason it carries.
REFUSALS = [
    ("unparseable", timedelta(days=3), FULL_TIME_PROFILE, "nonsense", None,
     "a check-in call button", "Internal error: invalid button ID."),
    ("unknown-action", timedelta(days=3), FULL_TIME_PROFILE, f"rsvp_maybe_r{ROUND_ID}", None,
     "a check-in call button", "Internal error: unknown action."),
    ("module-off", timedelta(days=3), FULL_TIME_PROFILE, f"rsvp_accept_r{ROUND_ID}", _module_off,
     "the \u201cAccept\u201d button of a check-in call", "switched off for this server"),
    ("no-profile", timedelta(days=3), 999999, f"rsvp_accept_r{ROUND_ID}", None,
     "the \u201cAccept\u201d button of a check-in call", "not registered as a driver"),
    ("round-gone", timedelta(days=3), FULL_TIME_PROFILE, "rsvp_accept_r9999", None,
     "the \u201cAccept\u201d button of a check-in call", "This round no longer exists."),
    ("cancelled", timedelta(days=3), FULL_TIME_PROFILE, f"rsvp_accept_r{ROUND_ID}", _cancel_round,
     f"the \u201cAccept\u201d button {CALL}", "This check-in is no longer open"),
    ("not-a-member", timedelta(days=3), STRANGER_PROFILE, f"rsvp_accept_r{ROUND_ID}", None,
     f"the \u201cAccept\u201d button {CALL}", "You are not a member of this division."),
    ("deadline", timedelta(hours=DEADLINE_HOURS - 1), FULL_TIME_PROFILE,
     f"rsvp_tentative_r{ROUND_ID}", None,
     f"the \u201cTentative\u201d button {CALL}", "The RSVP deadline has passed."),
    ("reserve-accepted", timedelta(hours=DEADLINE_HOURS - 1), RESERVE_PROFILE,
     f"rsvp_decline_r{ROUND_ID}", _reserve_accepted,
     f"the \u201cDecline\u201d button {CALL}", "You have already accepted"),
    ("round-started", timedelta(hours=-1), RESERVE_PROFILE, f"rsvp_accept_r{ROUND_ID}", None,
     f"the \u201cAccept\u201d button {CALL}", "The round has started."),
]


@pytest.mark.parametrize(
    "starts_in,profile,custom_id,arrange,what,reason",
    [case[1:] for case in REFUSALS],
    ids=[case[0] for case in REFUSALS],
)
async def test_every_refused_press_is_recorded(
    tmp_path, starts_in, profile, custom_id, arrange, what, reason
):
    """A14: each refusal keeps its reply and writes one line naming the button and, once the
    round is read, the call it sits on."""
    db_path = await _make_db(tmp_path, starts_in=starts_in)
    interaction = _make_interaction(db_path, profile)
    if arrange is not None:
        await arrange(db_path, interaction)

    await handle_rsvp_button(interaction, custom_id)

    interaction.response.send_message.assert_awaited_once()
    assert reason in _reply(interaction)
    logged = _logged(interaction)
    assert len(logged) == 1
    assert logged[0].startswith(f"\u26d4 {what} refused for Driver (<@{profile}>) \u2014 ")
    assert reason in logged[0]


async def test_an_answer_given_is_recorded_with_the_one_it_replaced(tmp_path):
    """A15: one success line, giving the answer and the one before it."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    await _set_status(db_path, FULL_TIME_PROFILE, "TENTATIVE")
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)
    interaction.client.get_channel = MagicMock(return_value=_redrawable_channel())

    with patch(
        "leaguebot.attendance.services.rsvp_service._rebuild_embed_for_round",
        new=AsyncMock(return_value=MagicMock()),
    ):
        await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert _logged(interaction) == [
        f"Driver (<@{FULL_TIME_PROFILE}>) | the \u201cAccept\u201d button {CALL} | Success\n"
        "  answer: accepted (was: tentative)"
    ]


async def test_a_first_answer_was_no_answer(tmp_path):
    """A15: a driver answering for the first time replaced no answer."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_decline_r{ROUND_ID}")

    logged = _logged(interaction)
    assert len(logged) == 1
    assert "| Success\n  answer: declined (was: no answer)" in logged[0]


async def test_the_answer_already_held_records_that_nothing_changed(tmp_path):
    """A16: a press for the answer already held changes nothing, and records so."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    await _set_status(db_path, FULL_TIME_PROFILE, "ACCEPTED")
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert _logged(interaction) == [
        f"Driver (<@{FULL_TIME_PROFILE}>) | the \u201cAccept\u201d button {CALL} | "
        "Nothing changed\n  answer: accepted"
    ]


async def test_an_answer_not_recorded_is_recorded_as_failed(tmp_path):
    """A17: the driver is told it was not recorded, and the log says the same."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    await _ignore_answers(db_path)
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert _logged(interaction) == [
        f"Driver (<@{FULL_TIME_PROFILE}>) | the \u201cAccept\u201d button {CALL} | Failed\n"
        "  answer: accepted\n  not recorded"
    ]


def _redrawable_channel(*, fails: bool = False) -> MagicMock:
    import discord

    message = MagicMock()
    message.edit = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=500), "down") if fails else None
    )
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    return channel


@pytest.mark.parametrize(
    "channel", [None, _redrawable_channel(fails=True)], ids=["no-channel", "edit-fails"]
)
async def test_a_call_not_redrawn_is_named_in_the_line(tmp_path, channel):
    """A18: the answer stands, and the line says the call still shows the old one."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)
    interaction.client.get_channel = MagicMock(return_value=channel)

    with patch(
        "leaguebot.attendance.services.rsvp_service._rebuild_embed_for_round",
        new=AsyncMock(return_value=MagicMock()),
    ):
        await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert await _status(db_path, FULL_TIME_PROFILE) == "ACCEPTED"
    logged = _logged(interaction)
    assert len(logged) == 1
    assert logged[0].endswith("\n  call not redrawn")


async def test_a_failed_line_that_cannot_be_posted_does_not_raise(tmp_path):
    """P2 leaves the Failed line its catch-all: it reports a failure, as `report_failure` does,
    and the driver has already been told their answer was not recorded."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    await _ignore_answers(db_path)
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)
    interaction.client.output_router.post_log = AsyncMock(
        side_effect=RuntimeError("log channel down")
    )

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    assert "could not be recorded" in _reply(interaction)


# ---------------------------------------------------------------------------
# Two presses at once (#482, F1 and F2)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=ANSWER_RSVP)
async def test_two_quick_presses_of_the_same_button_record_one_answer(tmp_path, monkeypatch):
    """F1: the second press sees the first's answer, so it is answered and logged as nothing
    changed, not as a second success."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    first = _make_interaction(db_path, FULL_TIME_PROFILE)
    second = _make_interaction(db_path, FULL_TIME_PROFILE)
    held = _HeldWrite(monkeypatch)
    press = f"rsvp_accept_r{ROUND_ID}"

    await asyncio.gather(
        handle_rsvp_button(first, press),
        held.after_the_first(lambda: handle_rsvp_button(second, press)),
    )

    assert "has been updated" in _reply(first)
    assert "already marked as" in _reply(second)
    outcomes = [
        line.split("\n")[0].rsplit(" | ", 1)[1] for line in _logged(first) + _logged(second)
    ]
    assert outcomes == ["Success", "Nothing changed"]
    assert await _status(db_path, FULL_TIME_PROFILE) == "ACCEPTED"


# ---------------------------------------------------------------------------
# AttendanceService.answer_rsvp — the lock rules and the answer in one transaction (#482)
# ---------------------------------------------------------------------------


async def _seed_answer(db_path: str, profile_id: int, status: str, accepted_at=None) -> None:
    """Set an answer, and its accept time, on the row the posted call created."""
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_round_attendance SET rsvp_status = ?, accepted_at = ? "
            "WHERE round_id = ? AND division_id = ? AND driver_profile_id = ?",
            (status, accepted_at, ROUND_ID, DIVISION_ID, profile_id),
        )
        await db.commit()


async def _answer(db_path, profile_id, status, *, starts_in, deadline_hours=DEADLINE_HOURS):
    """`answer_rsvp` as the handler calls it, at `NOW`, for a round *starts_in* from then."""
    return await AttendanceService(db_path).answer_rsvp(
        ROUND_ID,
        DIVISION_ID,
        profile_id,
        status,
        now=NOW,
        scheduled_at=NOW + starts_in,
        deadline_hours=deadline_hours,
        is_reserve=profile_id == RESERVE_PROFILE,
    )


#: Each case: its id, who answers, the answer held, the answer given, when the round starts
#: from `NOW`, the deadline in hours, and the outcome by name.
ANSWER_CASES = [
    ("full-time-before-deadline", FULL_TIME_PROFILE, "NO_RSVP", "ACCEPTED",
     timedelta(hours=DEADLINE_HOURS, seconds=1), DEADLINE_HOURS, "RECORDED"),
    ("full-time-at-deadline", FULL_TIME_PROFILE, "NO_RSVP", "ACCEPTED",
     timedelta(hours=DEADLINE_HOURS), DEADLINE_HOURS, "LOCKED_AT_DEADLINE"),
    ("reserve-accepted-before-deadline", RESERVE_PROFILE, "ACCEPTED", "DECLINED",
     timedelta(hours=DEADLINE_HOURS, seconds=1), DEADLINE_HOURS, "RECORDED"),
    ("reserve-accepted-at-deadline", RESERVE_PROFILE, "ACCEPTED", "DECLINED",
     timedelta(hours=DEADLINE_HOURS), DEADLINE_HOURS, "LOCKED_ACCEPTED"),
    ("reserve-not-accepted-after-deadline", RESERVE_PROFILE, "TENTATIVE", "ACCEPTED",
     timedelta(seconds=1), DEADLINE_HOURS, "RECORDED"),
    ("reserve-not-accepted-at-start", RESERVE_PROFILE, "TENTATIVE", "ACCEPTED",
     timedelta(0), DEADLINE_HOURS, "LOCKED_AT_START"),
    ("no-deadline-before-start", FULL_TIME_PROFILE, "NO_RSVP", "ACCEPTED",
     timedelta(seconds=1), 0, "RECORDED"),
    ("no-deadline-at-start", FULL_TIME_PROFILE, "NO_RSVP", "ACCEPTED",
     timedelta(0), 0, "LOCKED_AT_DEADLINE"),
    ("unchanged", FULL_TIME_PROFILE, "TENTATIVE", "TENTATIVE",
     timedelta(days=3), DEADLINE_HOURS, "UNCHANGED"),
    ("locked-on-the-answer-held", FULL_TIME_PROFILE, "TENTATIVE", "TENTATIVE",
     timedelta(hours=1), DEADLINE_HOURS, "LOCKED_AT_DEADLINE"),
]


@pytest.mark.xfail(strict=True, reason=ANSWER_RSVP)
@pytest.mark.parametrize(
    "profile,held,given,starts_in,deadline_hours,outcome",
    [case[1:] for case in ANSWER_CASES],
    ids=[case[0] for case in ANSWER_CASES],
)
async def test_answer_rsvp_applies_the_lock_rules(
    tmp_path, profile, held, given, starts_in, deadline_hours, outcome
):
    """Each lock rule either side of its moment: a full-time driver locks at the deadline, a
    reserve who has accepted at the deadline, any other reserve at the start, and a deadline
    of 0 is the start. A lock is checked before the answer held, as the handler always has."""
    from leaguebot.attendance.services.attendance_service import RsvpOutcome

    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    await _seed_answer(db_path, profile, held)

    result = await _answer(
        db_path, profile, given, starts_in=starts_in, deadline_hours=deadline_hours
    )

    assert result.outcome is RsvpOutcome[outcome]
    assert result.found == held
    assert await _status(db_path, profile) == (given if outcome == "RECORDED" else held)


@pytest.mark.xfail(strict=True, reason=ANSWER_RSVP)
async def test_answer_rsvp_sets_the_accept_time_from_now(tmp_path):
    """An accept is stamped with the `now` it is given, which orders the reserves."""
    from leaguebot.attendance.services.attendance_service import RsvpOutcome

    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))

    result = await _answer(db_path, RESERVE_PROFILE, "ACCEPTED", starts_in=timedelta(days=3))

    assert result.outcome is RsvpOutcome.RECORDED
    assert result.found == "NO_RSVP"
    assert await _accepted_at(db_path, RESERVE_PROFILE) == NOW.isoformat()


@pytest.mark.xfail(strict=True, reason=ANSWER_RSVP)
async def test_answer_rsvp_keeps_the_accept_time_of_an_answer_already_held(tmp_path):
    """Pressing Accept again changes nothing, and leaves a reserve's place in the queue."""
    from leaguebot.attendance.services.attendance_service import RsvpOutcome

    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    earlier = (NOW - timedelta(days=1)).isoformat()
    await _seed_answer(db_path, RESERVE_PROFILE, "ACCEPTED", accepted_at=earlier)

    result = await _answer(db_path, RESERVE_PROFILE, "ACCEPTED", starts_in=timedelta(days=3))

    assert result.outcome is RsvpOutcome.UNCHANGED
    assert result.found == "ACCEPTED"
    assert await _accepted_at(db_path, RESERVE_PROFILE) == earlier


@pytest.mark.xfail(strict=True, reason=ANSWER_RSVP)
async def test_answer_rsvp_clears_the_accept_time_of_an_accept_withdrawn(tmp_path):
    from leaguebot.attendance.services.attendance_service import RsvpOutcome

    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    await _seed_answer(
        db_path, RESERVE_PROFILE, "ACCEPTED", accepted_at=(NOW - timedelta(days=1)).isoformat()
    )

    result = await _answer(db_path, RESERVE_PROFILE, "DECLINED", starts_in=timedelta(days=3))

    assert result.outcome is RsvpOutcome.RECORDED
    assert await _accepted_at(db_path, RESERVE_PROFILE) is None


@pytest.mark.xfail(strict=True, reason=ANSWER_RSVP)
async def test_answer_rsvp_opens_a_row_for_a_driver_placed_after_the_call(tmp_path):
    from leaguebot.attendance.services.attendance_service import RsvpOutcome

    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3), call_posted=False)

    result = await _answer(db_path, RESERVE_PROFILE, "ACCEPTED", starts_in=timedelta(hours=1))

    assert result.outcome is RsvpOutcome.RECORDED
    assert result.found == "NO_RSVP"
    assert await _status(db_path, RESERVE_PROFILE) == "ACCEPTED"


@pytest.mark.xfail(strict=True, reason=ANSWER_RSVP)
async def test_answer_rsvp_reports_an_answer_it_could_not_write(tmp_path):
    """A write that changes no row is reported as not recorded, never as recorded."""
    from leaguebot.attendance.services.attendance_service import RsvpOutcome

    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    await _ignore_answers(db_path)

    result = await _answer(db_path, FULL_TIME_PROFILE, "ACCEPTED", starts_in=timedelta(days=3))

    assert result.outcome is RsvpOutcome.NOT_RECORDED
    assert result.found == "NO_RSVP"
    assert await _status(db_path, FULL_TIME_PROFILE) == "NO_RSVP"


@pytest.mark.xfail(strict=True, reason=ANSWER_RSVP)
async def test_a_reserve_s_accept_then_decline_after_the_deadline_leaves_the_accept(
    tmp_path, monkeypatch
):
    """F2: after the deadline a reserve presses Accept, then Decline before Accept has saved.
    Decline waits for Accept, sees it, and is refused as locked: an accepted reserve is locked
    at the deadline whatever presses follow."""
    from leaguebot.attendance.services.attendance_service import RsvpOutcome

    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    held = _HeldWrite(monkeypatch)
    after_the_deadline = timedelta(hours=DEADLINE_HOURS - 1)

    accept, decline = await asyncio.gather(
        _answer(db_path, RESERVE_PROFILE, "ACCEPTED", starts_in=after_the_deadline),
        held.after_the_first(
            lambda: _answer(db_path, RESERVE_PROFILE, "DECLINED", starts_in=after_the_deadline)
        ),
    )

    assert accept.outcome is RsvpOutcome.RECORDED
    assert decline.outcome is RsvpOutcome.LOCKED_ACCEPTED
    assert decline.found == "ACCEPTED"
    assert await _status(db_path, RESERVE_PROFILE) == "ACCEPTED"
    assert await _accepted_at(db_path, RESERVE_PROFILE) == NOW.isoformat()
