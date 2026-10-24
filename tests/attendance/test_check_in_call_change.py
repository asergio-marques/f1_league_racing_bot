"""A round's check-in call on the change queue (#439, slice 6b, C26): asked by its timer, a restart
or `/attendance post-check-in`, posted and recorded by the call's own jobs.

The league is `ongoing_league`'s with attendance on, its queue rebuilt on a real
`AttendanceService`: Pro (role 801) has Lewis and Max seated at Ferrari, its check-in channel is
603, and round 3's call already stands there as messages 7001 to 7003, Lewis having accepted.
Pro's round 4 is moved to three days off, past the moment its call falls due (five days before)
and before its check-in deadline (two hours before), with no call posted for it yet. A test asks
the call of the queue as its timer does, as the bot (`ask_check_in_call`), and runs the queue.

A call refused, or whose check-in channel is unset, stops the queue; the stop notice says what is
left for the round, as an amendment's call already does. A call asked past its deadline is given up
by a job of its own; a call for a cancelled round, or with attendance off, is dropped. The restart
asks the queue for each call that fell due while the bot was down, before the queue starts, and
leaves alone a call already in hand; under test mode it asks none (the #429 rule, attendance spec
:222, kept: decision 15 of the slice).
"""
from __future__ import annotations

import inspect
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

from leaguebot.attendance.services.attendance_service import AttendanceService
from tests.support.change_queue import (
    attach_queue,
    change_rows,
    discard_job,
    http_error,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.season_league import (
    CALL_MESSAGES,
    DIVISIONS,
    LEWIS,
    MAX,
    PRO,
    SeasonLeague,
    ongoing_league,
    profile_id,
    round_id,
)

KIND = "attendance.check_in.call"
CHECK_IN = DIVISIONS[PRO][3].checkin
ROUND_3 = round_id(PRO, 3)
ROUND_4 = round_id(PRO, 4)

#: What the stop notice says is left where no call stands and no answers are kept.
NOTHING_LEFT = (
    "no call stands, and no attendance rows are open for this round until this call is posted"
)
#: What the stop notice says the round comes to meanwhile (attendance spec :120).
COUNTS_NOTHING = "will count nothing against anyone"
#: What the log channel is told of a round whose call is given up (attendance spec :121).
COUNTS_NOTHING_GIVEN_UP = "the round counts nothing against anyone"



async def _call_league(tmp_path: Any, *, images: bool = False) -> SeasonLeague:
    """The league of the module docstring: round 4 three days off, no call posted for it."""
    league = await ongoing_league(tmp_path, attendance=True, images=images)
    league.bot.attendance_service = AttendanceService(league.db_path)
    attach_queue(league.bot, league.db_path, now=league.clock)
    await _round_4_in(league, timedelta(days=3))
    return league


async def _round_4_in(league: SeasonLeague, delta: timedelta) -> None:
    when = league.clock.now + delta
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.replace(tzinfo=None).isoformat(), ROUND_4)


async def _ask(league: SeasonLeague, round_: int = ROUND_4, **kwargs: Any) -> None:
    """Ask the queue for *round_*'s call as its timer does; the queue is not run."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_call
    from leaguebot.core.models.change import ChangeOrigin

    await ask_check_in_call(league.bot.change_queue, league.db_path, round_,
                            origin=ChangeOrigin.BOT, **kwargs)


def _sent(league: SeasonLeague) -> list[int]:
    """The ids of the messages posted in Pro's check-in channel, in order."""
    return [mid for kind, cid, mid in league.events if kind == "send" and cid == CHECK_IN]


async def _call_record(league: SeasonLeague, round_: int = ROUND_4) -> list[dict[str, Any]]:
    return await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", round_)


async def _answers(league: SeasonLeague, round_: int = ROUND_4) -> dict[int, str]:
    rows = await league.rows(
        "SELECT driver_profile_id, rsvp_status FROM driver_round_attendance WHERE round_id = ?",
        round_,
    )
    return {row["driver_profile_id"]: row["rsvp_status"] for row in rows}


async def _call_changes(league: SeasonLeague) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == KIND]


async def _recover(league: SeasonLeague) -> None:
    """The restart's step for the calls that fell due while the bot was down; the queue is not
    started."""
    from leaguebot.__main__ import _recover_missed_check_in_calls

    await _recover_missed_check_in_calls(league.bot, now=league.clock.now)


# ---------------------------------------------------------------------------
# The call posted and recorded by its own jobs
# ---------------------------------------------------------------------------


async def test_the_timer_asks_for_the_call_and_the_queue_posts_it(tmp_path):
    """Nothing is posted as the call is asked; the queue posts it in Pro's check-in channel,
    pinging Pro's role. The check-in timer's callback asks the queue rather than posting."""
    import leaguebot.__main__ as bot_module

    league = await _call_league(tmp_path)

    await _ask(league)
    assert _sent(league) == []
    await run_queue(league.bot)

    [posted] = _sent(league)
    assert "<@&801>" in league.texts(CHECK_IN)[-1]
    assert [row["kind"] for row in await _call_changes(league)] == [KIND]
    assert await stopped_job(league.db_path) is None
    assert posted in league.channel(CHECK_IN).messages
    assert "ask_check_in_call(" in inspect.getsource(bot_module.main)


async def test_the_call_s_record_saves_its_message_and_the_no_rsvp_rows_with_its_mark(tmp_path):
    """The call's message and channel, and a NO_RSVP row for each of Pro's seated drivers, are
    saved with the posting job's mark."""
    league = await _call_league(tmp_path)

    await _ask(league)
    await run_queue(league.bot)

    [posted] = _sent(league)
    [row] = await _call_record(league)
    assert (row["message_id"], row["channel_id"]) == (str(posted), str(CHECK_IN))
    assert await _answers(league) == {profile_id(LEWIS): "NO_RSVP", profile_id(MAX): "NO_RSVP"}
    [change] = await _call_changes(league)
    [post] = [step for step in await step_rows(league.db_path, change["id"])
              if step["name"] == "post_call"]
    assert post["done_at"] is not None


async def test_a_refused_call_stops_the_queue_saying_what_is_left(tmp_path):
    """Discord refuses the call: the queue stops at it, the stop notice saying that no call
    stands and no attendance rows are open, and nothing is recorded. Once Discord takes it, the
    queue's next try posts it."""
    league = await _call_league(tmp_path)
    league.channel(CHECK_IN).send_fails = http_error(discord.Forbidden, status=403,
                                                     text="Missing Permissions")

    await _ask(league)
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "post_call"
    assert NOTHING_LEFT in league.log()
    assert COUNTS_NOTHING in league.log()
    assert await _call_record(league) == []
    assert await _answers(league) == {}

    league.channel(CHECK_IN).send_fails = None
    league.clock.advance(minutes=1)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(_sent(league)) == 1
    assert len(await _call_record(league)) == 1


async def test_a_retried_call_is_posted_as_text(tmp_path, monkeypatch):
    """Images on, the check-in graphic drawn. The first try is refused; the try that posts it
    carries no picture (Constitution XIV r8)."""
    from leaguebot.image.services import image_rsvp_post

    league = await _call_league(tmp_path, images=True)
    picture = tmp_path / "checkin.png"

    async def _attach(*_args: Any, **_kwargs: Any) -> discord.File:
        picture.write_bytes(b"png")
        return discord.File(str(picture), filename="checkin.png")

    drawing = AsyncMock(side_effect=_attach)
    monkeypatch.setattr(image_rsvp_post, "try_attach", drawing)
    league.channel(CHECK_IN).send_fails = http_error(status=500, text="Internal Server Error")

    await _ask(league)
    await run_queue(league.bot)
    assert await stopped_job(league.db_path) is not None
    assert drawing.await_count >= 1

    league.channel(CHECK_IN).send_fails = None
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(_sent(league)) == 1
    assert league.channel(CHECK_IN).files[-1] is None


async def test_a_call_whose_rsvp_channel_is_unset_stops_the_queue_naming_the_command(tmp_path):
    """Pro's check-in channel is unset: the queue stops at the call, naming
    `/attendance channel rsvp`, rather than the log channel being told and the call skipped."""
    league = await _call_league(tmp_path)
    await league.write(
        "UPDATE attendance_division_config SET rsvp_channel_id = NULL WHERE division_id = ?", PRO,
    )

    await _ask(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is not None
    assert "/attendance channel rsvp" in league.log()
    assert _sent(league) == []
    assert await _call_record(league) == []


async def test_a_call_whose_record_fails_removes_its_copy_before_the_next_try(tmp_path):
    """The call is posted but its record cannot be saved (the database refuses the row), so the
    queue stops with the message standing. On Retry, the copy is deleted before the call is
    posted again, and the second is the one recorded."""
    league = await _call_league(tmp_path)
    await league.write(
        "CREATE TRIGGER refuse_round_4_call BEFORE INSERT ON rsvp_embed_messages "
        f"WHEN NEW.round_id = {ROUND_4} BEGIN SELECT RAISE(ABORT, 'record refused'); END"
    )

    await _ask(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is not None
    [copy] = _sent(league)
    assert copy in league.channel(CHECK_IN).messages

    await league.write("DROP TRIGGER refuse_round_4_call")
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    [_, posted] = _sent(league)
    assert ("delete", CHECK_IN, copy) in league.events
    assert league.events.index(("delete", CHECK_IN, copy)) < league.events.index(
        ("send", CHECK_IN, posted))
    [row] = await _call_record(league)
    assert row["message_id"] == str(posted)


async def test_a_call_replacing_an_earlier_one_deletes_its_messages_as_jobs_after_it(tmp_path):
    """Round 3's call stands (7001 to 7003, Lewis accepted). A call asked to replace it is posted
    first; then each of the earlier call's three messages is deleted by a job of its own. Lewis's
    answer carries over, and the new call is the one recorded."""
    league = await _call_league(tmp_path)

    await _ask(league, ROUND_3, replacing=True)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    [posted] = _sent(league)
    sent_at = league.events.index(("send", CHECK_IN, posted))
    for message_id in CALL_MESSAGES:
        assert league.events.index(("delete", CHECK_IN, message_id)) > sent_at
    [change] = await _call_changes(league)
    names = [step["name"] for step in await step_rows(league.db_path, change["id"])]
    assert names.count("delete_earlier") == len(CALL_MESSAGES)
    assert names.index("delete_earlier") > names.index("post_call")
    [row] = await _call_record(league, ROUND_3)
    assert row["message_id"] == str(posted)
    assert (await _answers(league, ROUND_3))[profile_id(LEWIS)] == "ACCEPTED"


async def test_a_call_asked_past_its_deadline_gives_up_and_clears_the_answers(tmp_path):
    """Round 4 is an hour off, inside its two-hour deadline, and Lewis's answer to an earlier call
    is kept. The call is not posted: a job gives it up, closing the round's check-in, clearing
    the answers and telling the log channel that the round counts nothing against anyone."""
    league = await _call_league(tmp_path)
    await _round_4_in(league, timedelta(hours=1))
    await league.write(
        "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
        "rsvp_status) VALUES (?, ?, ?, 'ACCEPTED')", ROUND_4, PRO, profile_id(LEWIS),
    )

    await _ask(league)
    await run_queue(league.bot)

    assert _sent(league) == []
    assert await stopped_job(league.db_path) is None
    [round_] = await league.rows("SELECT checkin_cleared FROM rounds WHERE id = ?", ROUND_4)
    assert round_["checkin_cleared"] == 1
    assert await _answers(league) == {}
    assert ("the round's check-in deadline passed before its call could be posted"
            in league.log())
    assert COUNTS_NOTHING_GIVEN_UP in league.log()
    [change] = await _call_changes(league)
    assert [step["name"] for step in await step_rows(league.db_path, change["id"])] == ["give_up"]


@pytest.mark.parametrize("why", ["cancelled", "attendance off"])
async def test_a_call_for_a_cancelled_round_or_with_attendance_off_is_dropped(tmp_path, why):
    """Round 4 is cancelled, or attendance switched off, once the call is asked: nothing is
    posted or recorded, and the queue does not stop."""
    league = await _call_league(tmp_path)
    await _ask(league)
    if why == "cancelled":
        await league.write("UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", ROUND_4)
    else:
        await league.switch("attendance", False)

    await run_queue(league.bot)

    assert _sent(league) == []
    assert await _call_record(league) == []
    assert await _answers(league) == {}
    assert await stopped_job(league.db_path) is None


async def test_a_discarded_call_says_post_check_in_is_the_way_to_post_it(tmp_path):
    """Discord refuses the call and a league admin discards it: no call stands, and the log
    channel's line says to post it with `/attendance post-check-in`."""
    league = await _call_league(tmp_path)
    league.channel(CHECK_IN).send_fails = http_error(discord.Forbidden, status=403,
                                                     text="Missing Permissions")
    await _ask(league)
    await run_queue(league.bot)
    told_before = league.log()

    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _call_record(league) == []
    assert "/attendance post-check-in" in league.log()[len(told_before):]


async def test_a_hand_posted_call_an_admin_discards_tells_the_manager_it_was_discarded(tmp_path):
    """A manager runs `/attendance post-check-in` for round 4, Discord refuses the call and a
    league admin discards it: the manager's acknowledgement is updated to say the call was
    discarded by an admin and not posted, naming `/attendance post-check-in` as the way to post
    it, never that it was no longer owed (core spec, how a change is carried out)."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from leaguebot.attendance.cogs.attendance_cog import AttendanceCog
    from tests.support.change_queue import member_interaction
    from tests.support.undecorate import undecorate

    league = await _call_league(tmp_path)
    league.channel(CHECK_IN).send_fails = http_error(discord.Forbidden, status=403,
                                                     text="Missing Permissions")
    cog = AttendanceCog.__new__(AttendanceCog)
    cog.bot = league.bot
    interaction = member_interaction(league.bot)
    interaction.guild = league.guild
    interaction.command = SimpleNamespace(qualified_name="attendance post-check-in")
    interaction.followup.send.return_value = MagicMock(edit=AsyncMock())

    await undecorate(AttendanceCog.post_check_in)(cog, interaction, "Pro", 4)
    await run_queue(league.bot)
    assert await stopped_job(league.db_path) is not None
    await discard_job(league.bot)

    edits = interaction.followup.send.return_value.edit.await_args_list
    outcome = str(edits[-1].args[0] if edits[-1].args else edits[-1].kwargs.get("content", ""))
    assert outcome.startswith("⛔ The check-in call for round 4 of **Pro** was not posted")
    assert "a league admin discarded it" in outcome
    assert "/attendance post-check-in" in outcome
    assert "no longer owed" not in outcome
    assert _sent(league) == []
    assert await _call_record(league) == []


# ---------------------------------------------------------------------------
# The restart
# ---------------------------------------------------------------------------


async def test_the_start_up_asks_for_each_call_due_before_the_queue_starts(tmp_path):
    """Round 4's call fell due while the bot was down. The restart asks the queue for it and
    posts nothing itself; the queue then posts it."""
    league = await _call_league(tmp_path)

    await _recover(league)

    assert _sent(league) == []
    assert len(await _call_changes(league)) == 1
    await run_queue(league.bot)
    [posted] = _sent(league)
    [row] = await _call_record(league)
    assert row["message_id"] == str(posted)


async def test_the_start_up_leaves_a_call_in_hand_on_the_queue_alone(tmp_path):
    """Round 4's call was asked of the queue before the bot stopped, and has not run. The restart
    asks nothing more, and one call is posted."""
    league = await _call_league(tmp_path)
    await _ask(league)

    await _recover(league)

    assert len(await _call_changes(league)) == 1
    await run_queue(league.bot)
    assert len(_sent(league)) == 1


async def test_the_start_up_asks_no_late_call_under_test_mode(tmp_path):
    """Test mode is on: the restart asks for no late call, and posts none (attendance spec :222,
    the #429 rule); `/test-mode advance` posts it in its turn."""
    league = await _call_league(tmp_path)
    await league.set_test_mode(True)

    await _recover(league)
    await run_queue(league.bot)

    assert await _call_changes(league) == []
    assert _sent(league) == []
