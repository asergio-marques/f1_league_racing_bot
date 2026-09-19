"""`handle_rsvp_button` — what happens when a driver presses a check-in button.

Issue #208. `tests/unit/test_attendance_module_gate.py` covers exactly one branch of this
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

**Times are taken from the clock, never pinned.** The handler reads `datetime.now` itself and
takes no `now` parameter, so a fixture that seeded a fixed date would pass today and fail
silently once it went by. Every round here is scheduled relative to the real present.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.attendance_cog import handle_rsvp_button  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from services.attendance_service import AttendanceService  # noqa: E402

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

        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, max_seats, is_reserve) "
            "VALUES (10, ?, 'Alpha', 2, 0)",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, max_seats, is_reserve) "
            "VALUES (11, ?, 'Reserve', 6, 1)",
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

    interaction = MagicMock()
    interaction.client = bot
    interaction.guild_id = SERVER_ID
    interaction.user.id = profile_id
    interaction.response.send_message = AsyncMock(return_value=None)
    return interaction


def _reply(interaction: MagicMock) -> str:
    return str(interaction.response.send_message.await_args.args[0])


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

    assert "has been cancelled" in _reply(interaction)
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
    hold for whatever makes the write a no-op next, or the silence comes back."""
    db_path = await _make_db(tmp_path, starts_in=timedelta(days=3))
    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)
    interaction.client.attendance_service.upsert_rsvp_status = AsyncMock(return_value=False)

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
        await db.commit()

    message = MagicMock()
    message.edit = AsyncMock(return_value=None)
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)

    interaction = _make_interaction(db_path, FULL_TIME_PROFILE)
    interaction.client.get_channel = MagicMock(return_value=channel)

    with patch(
        "services.rsvp_service._rebuild_embed_for_round",
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
