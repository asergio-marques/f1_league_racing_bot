"""Setting a division's RSVP answers in bulk, from a test-mode maintainer's paste.

Issue #208. `_RsvpBulkSetModal.on_submit` is fifty statements of parsing and was uncovered. It
exists so a rehearsal can put twenty test drivers into a particular attendance state without
twenty button presses — which is how the attendance module's interesting cases (a division one
short, a reserve called up, a deadline with three tentatives outstanding) get set up at all.

**A bad line is reported and the rest go on.** A maintainer pasting twenty lines with a typo in
the fourth wants the other nineteen applied; refusing the lot would make them edit and retype
the whole paste to fix one character. So the contract is partial success, and every line is
reported by its own number — a list of errors that did not say *which* line would be no better
than refusing.

**Five ways a line can be wrong, and each says which.** No comma, a non-numeric id, a status
that is not one of the three, an id nobody in this server has a profile for, and a driver with
no attendance row for this round. They are different mistakes with different fixes, so they are
tested separately rather than as "an error is reported": a maintainer told only "line 4 is
invalid" has to guess between an id they mistyped and a driver who is not in this division.

**The status vocabulary is the maintainer's, not the database's.** `accept` and `accepted` both
mean `ACCEPTED`, and case does not matter, because this is typed by hand under time pressure
during a rehearsal.

**The embed is rebuilt once, after every line.** It is a Discord message edit per rebuild, and
doing it per line would be twenty edits for one paste — straight into a rate limit, with the
first nineteen showing states that were already stale when they were drawn.

**A failed rebuild does not fail the updates.** The statuses are already written by then; the
embed is a view of them. Refusing at that point would report an error for work that succeeded
and send a maintainer looking for it in the database. A redraw Discord refuses stops the change
queue at the redraw, the answers saved; a call or channel deleted by hand completes it.

**The form asks the change queue** (#439, slice 6b, C36). Each test submits the form and runs
the queue (`_submit`) against a real `AttendanceService` on a seeded round, reads the answers
saved from the database (`_applied`), and reads what the maintainer was told from the
acknowledgement and its update together (`_replied`).

**Nothing applied means nothing rebuilt and no success line.** A paste that was entirely wrong
did not change the round, so a success line would be a false record of a rehearsal's state.
It is recorded as a refusal instead, naming each entry turned away (#482).
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.attendance.cogs.attendance_cog import _RsvpBulkSetModal
from leaguebot.attendance.services.attendance_service import AttendanceService
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    acknowledgement,
    attach_queue,
    http_error,
    league_double,
    member,
    member_interaction,
    run_queue,
    step_rows,
    stopped_job,
    updated_reply,
)

SERVER_ID = 10808
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
EMBED_CHANNEL = 700
EMBED_MESSAGE = 800

#: discord_user_id -> driver_profile_id, for the two drivers the fixture seeds.
DRIVERS = {900000001: 31, 900000002: 32}

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name: str = "rsvp_bulk") -> str:
    """Pro's round 21, three days off, with its check-in call (800) standing in channel 700 and
    both test drivers seated in the division with an unanswered row each, as posting the call
    leaves them. Test mode and the Attendance module are on."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    now = datetime.now(timezone.utc)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) "
            "VALUES (?, 900, 100, 101, 1)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO attendance_config (id, module_enabled, rsvp_deadline_hours) "
            "VALUES (1, 1, 2)"
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, rsvp_channel_id) "
            "VALUES (?, ?)",
            (DIVISION_ID, str(EMBED_CHANNEL)),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (?, ?, 1, 'NORMAL', 'Silverstone Circuit', ?)",
            (ROUND_ID, DIVISION_ID, (now + timedelta(days=3)).isoformat()),
        )
        await db.execute(
            "INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, channel_id, "
            "posted_at) VALUES (?, ?, ?, ?, ?)",
            (ROUND_ID, DIVISION_ID, str(EMBED_MESSAGE), str(EMBED_CHANNEL), now.isoformat()),
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, "
            "is_reserve) VALUES (10, ?, 'Alpha', 'Alpha', 2, 0)",
            (DIVISION_ID,),
        )
        for seat, (discord_uid, profile_id) in enumerate(DRIVERS.items(), start=1):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state, "
                "is_test_driver) VALUES (?, ?, 'ASSIGNED', 1)",
                (profile_id, str(discord_uid)),
            )
            await db.execute(
                "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, 10, ?, ?)",
                (19 + seat, seat, profile_id),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, team_seat_id) VALUES (?, ?, ?, ?)",
                (profile_id, SEASON_ID, DIVISION_ID, 19 + seat),
            )
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, "
                "driver_profile_id, rsvp_status) VALUES (?, ?, ?, 'NO_RSVP')",
                (ROUND_ID, DIVISION_ID, profile_id),
            )
        await db.commit()
    return db_path


#: The check-in channel `_bot` gives where a test names none: one holding the call.
_STANDING = object()


async def _bot(db_path: str, *, attendance_rows=None, channel=_STANDING, saved=None):
    """A bot on *db_path* with a real `AttendanceService` and a change queue whose clock reads
    the real present; its log lines are recorded by the router's `post_log`.

    *attendance_rows* is the set of profile ids that keep a row for this round, and *saved*
    those whose answer is saved (any other's write changes nothing); each is every driver by
    default. *channel* is the check-in channel the bot finds, None for one deleted; by default
    one holding the call (`_channel`)."""
    channel = _channel() if channel is _STANDING else channel
    attendance_rows = DRIVERS.values() if attendance_rows is None else attendance_rows
    saved = DRIVERS.values() if saved is None else saved
    async with get_connection(db_path) as db:
        for profile_id in DRIVERS.values():
            if profile_id not in attendance_rows:
                await db.execute(
                    "DELETE FROM driver_round_attendance WHERE driver_profile_id = ?",
                    (profile_id,),
                )
        for profile_id in DRIVERS.values():
            if profile_id not in saved:
                for event in ("INSERT", "UPDATE"):
                    await db.execute(
                        f"CREATE TRIGGER ignore_{profile_id}_{event.lower()} BEFORE {event} "
                        f"ON driver_round_attendance WHEN NEW.driver_profile_id = {profile_id} "
                        "BEGIN SELECT RAISE(IGNORE); END"
                    )
        await db.commit()

    bot = league_double(db_path)
    bot.attendance_service = AttendanceService(db_path)
    bot.config_service.get_server_config.return_value.test_mode_active = True
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    # The lines the queue writes reach the log channel the database names (101), whose sends are
    # the router's `post_log` record, where the tests read them with the refusals' (#439), as the
    # direct posts wrote them.
    log_channel = MagicMock(spec=discord.TextChannel)

    async def _send(content=None, **_kwargs):
        # The queue's delivery names the member in code format and ends a line with a rule.
        line = re.sub(r"`(<@!?&?\d+>)`", r"\1", content).split("\n\u2015")[0]
        await bot.output_router.post_log(line)
        return MagicMock(id=1)

    log_channel.send = _send

    known = bot.get_channel.side_effect
    bot.get_channel = MagicMock(
        side_effect=lambda cid: (
            channel if cid == EMBED_CHANNEL else log_channel if cid == 101 else known(cid)
        )
    )
    found = bot.fetch_channel.side_effect

    async def _fetch_channel(cid: int):
        if cid == 101:
            return log_channel
        if cid != EMBED_CHANNEL:
            return found(cid)
        if channel is None:
            raise http_error(discord.NotFound, status=404, text="Unknown Channel")
        return channel

    bot.fetch_channel = AsyncMock(side_effect=_fetch_channel)
    bot.output_router.post_log = AsyncMock(return_value=None)
    attach_queue(bot, db_path, now=datetime.now(timezone.utc))
    return bot


def _channel(*, refuses: bool = False, call_gone: bool = False):
    """Pro's check-in channel holding the call (800); *refuses* makes Discord refuse every edit
    of it, and *call_gone* has it deleted by hand."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = EMBED_CHANNEL
    message = MagicMock(id=EMBED_MESSAGE)
    message.channel = channel
    message.edit = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
        if refuses else None
    )
    gone = http_error(discord.NotFound, status=404, text="Unknown Message")
    if call_gone:
        channel.fetch_message = AsyncMock(side_effect=gone)
        partial = MagicMock(id=EMBED_MESSAGE)
        partial.edit = AsyncMock(side_effect=gone)
        channel.get_partial_message = MagicMock(return_value=partial)
    else:
        channel.fetch_message = AsyncMock(return_value=message)
        channel.get_partial_message = MagicMock(return_value=message)
    channel._message = message
    return channel


async def _submit(bot, entries: str, interaction=None):
    """A maintainer (77) submits the form with *entries*, and the queue runs what it asked."""
    interaction = interaction or member_interaction(
        bot, user=member(77, "Maintainer"), created_at=datetime.now(timezone.utc)
    )
    interaction.client = bot
    modal = _RsvpBulkSetModal(
        division_name="Pro",
        division_id=DIVISION_ID,
        round_id=ROUND_ID,
        embed_message_id=EMBED_MESSAGE,
        bot=bot,
    )
    modal.entries._value = entries  # type: ignore[attr-defined]
    with patch(
        "leaguebot.attendance.services.rsvp_service._rebuild_embed_for_round",
        new=AsyncMock(return_value=MagicMock()),
    ) as rebuild, patch("leaguebot.attendance.services.rsvp_service.RsvpView", new=MagicMock()):
        await modal.on_submit(interaction)
        await run_queue(bot)
    return interaction, rebuild


def _replied(interaction) -> str:
    """What the maintainer was told: the acknowledgement, then its update."""
    return acknowledgement(interaction) + "\n" + updated_reply(interaction)


async def _applied(bot) -> list[tuple[int, str]]:
    """Each answer saved for the round, in profile id order: (profile id, status)."""
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT driver_profile_id, rsvp_status FROM driver_round_attendance "
            "WHERE round_id = ? AND rsvp_status != 'NO_RSVP' ORDER BY driver_profile_id",
            (ROUND_ID,),
        )
        return [(row["driver_profile_id"], row["rsvp_status"]) for row in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# What a good paste does
# ---------------------------------------------------------------------------


async def test_a_single_entry_is_applied(tmp_path):
    bot = await _bot(await _make_db(tmp_path))

    interaction, _ = await _submit(bot, "900000001, accept")

    assert (await _applied(bot)) == [(31, "ACCEPTED")]
    assert "Applied 1 update(s)" in _replied(interaction)


async def test_every_entry_of_a_paste_is_applied(tmp_path):
    """Twenty test drivers into a particular state without twenty button presses is the
    whole reason the modal exists."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_many"))

    await _submit(bot, "900000001, accept\n900000002, decline")

    assert (await _applied(bot)) == [(31, "ACCEPTED"), (32, "DECLINED")]


@pytest.mark.parametrize(
    "typed,stored",
    [
        ("accept", "ACCEPTED"),
        ("accepted", "ACCEPTED"),
        ("tentative", "TENTATIVE"),
        ("decline", "DECLINED"),
        ("declined", "DECLINED"),
        ("ACCEPT", "ACCEPTED"),
    ],
    ids=["accept", "accepted", "tentative", "decline", "declined", "upper-case-accept"],
)
async def test_the_vocabulary_is_the_maintainers(tmp_path, typed, stored):
    """Typed by hand under time pressure during a rehearsal, so both the verb and the
    participle are accepted, and the status is read regardless of case."""
    bot = await _bot(await _make_db(tmp_path, name=f"bulk_{typed}"))

    await _submit(bot, f"900000001, {typed}")

    assert (await _applied(bot)) == [(31, stored)]


async def test_surrounding_whitespace_is_ignored(tmp_path):
    """A paste out of a spreadsheet or a chat message carries it, and refusing over a space
    would be a puzzle rather than a message."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_spaces"))

    await _submit(bot, "   900000001 ,   accept   ")

    assert (await _applied(bot)) == [(31, "ACCEPTED")]


async def test_blank_lines_are_skipped_silently(tmp_path):
    """They are how a paste is spaced, not a mistake to report."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_blanks"))

    interaction, _ = await _submit(bot, "900000001, accept\n\n   \n900000002, accept")

    assert len((await _applied(bot))) == 2
    assert "Errors" not in _replied(interaction)


async def test_a_status_containing_a_comma_is_kept_whole(tmp_path):
    """The line is split once, so only the first comma separates — which is what lets the
    error name the whole status a maintainer typed rather than its first word."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_comma"))

    interaction, _ = await _submit(bot, "900000001, accept, maybe")

    assert "accept, maybe" in _replied(interaction)


async def test_the_reply_lists_what_was_applied(tmp_path):
    """A maintainer setting up a scenario needs to see the state they now have, not a
    count — the point of the paste is the arrangement it produces."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_reply"))

    interaction, _ = await _submit(bot, "900000001, accept\n900000002, tentative")

    replied = _replied(interaction)
    assert "900000001` → accepted" in replied
    assert "900000002` → tentative" in replied
    assert "Pro" in replied


# ---------------------------------------------------------------------------
# The five ways a line can be wrong
# ---------------------------------------------------------------------------


async def test_a_line_without_a_comma_is_refused(tmp_path):
    bot = await _bot(await _make_db(tmp_path, name="bulk_nocomma"))

    interaction, _ = await _submit(bot, "900000001 accept")

    assert "Line 1: expected `ID, status`" in _replied(interaction)
    assert (await _applied(bot)) == []


async def test_a_non_numeric_id_is_refused(tmp_path):
    bot = await _bot(await _make_db(tmp_path, name="bulk_badid"))

    interaction, _ = await _submit(bot, "driver-one, accept")

    assert "not a valid numeric ID" in _replied(interaction)
    assert (await _applied(bot)) == []


async def test_an_unknown_status_is_refused_and_the_options_given(tmp_path):
    """The three words are the fix, and a maintainer who guessed "yes" needs them rather
    than to be told their guess was wrong."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_badstatus"))

    interaction, _ = await _submit(bot, "900000001, yes")

    replied = _replied(interaction)
    assert "unknown status `yes`" in replied
    assert "accept, tentative, or decline" in replied


async def test_an_id_with_no_driver_profile_is_refused(tmp_path):
    """Distinct from a driver with no attendance row: one is a wrong id, the other is a
    driver in the wrong division, and they are fixed differently."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_noprofile"))

    interaction, _ = await _submit(bot, "900000009, accept")

    assert "no driver profile for ID `900000009`" in _replied(interaction)
    assert (await _applied(bot)) == []


async def test_a_driver_with_no_attendance_row_is_refused(tmp_path):
    """They are in the league but not in this round's division, so there is nothing to set
    a status on."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_norow"), attendance_rows={31})

    interaction, _ = await _submit(bot, "900000002, accept")

    assert "no attendance row for this round" in _replied(interaction)
    assert (await _applied(bot)) == []


# ---------------------------------------------------------------------------
# Partial success
# ---------------------------------------------------------------------------


async def test_a_bad_line_does_not_stop_the_good_ones(tmp_path):
    """A maintainer pasting twenty lines with a typo in the fourth wants the other
    nineteen applied."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_partial"))

    interaction, _ = await _submit(
        bot, "900000001, accept\nrubbish\n900000002, decline"
    )

    assert (await _applied(bot)) == [(31, "ACCEPTED"), (32, "DECLINED")]
    assert "Applied 2 update(s)" in _replied(interaction)
    assert "Errors" in _replied(interaction)


async def test_an_error_names_the_line_it_was_on(tmp_path):
    """A list of errors that did not say which line would be no better than refusing the
    whole paste."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_lineno"))

    interaction, _ = await _submit(bot, "900000001, accept\n900000002, maybe")

    assert "Line 2:" in _replied(interaction)


async def test_line_numbers_count_blank_lines_too(tmp_path):
    """They are what a maintainer sees in the box they pasted into; renumbering around the
    blanks would point at the wrong line of their own text."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_lineno_blank"))

    interaction, _ = await _submit(bot, "\n\n900000001, maybe")

    assert "Line 3:" in _replied(interaction)


async def test_an_entirely_wrong_paste_says_so(tmp_path):
    bot = await _bot(await _make_db(tmp_path, name="bulk_allbad"))

    interaction, _ = await _submit(bot, "rubbish\nmore rubbish")

    replied = _replied(interaction)
    assert "Applied" not in replied
    assert "Errors" in replied


# ---------------------------------------------------------------------------
# The embed, and the log
# ---------------------------------------------------------------------------


async def test_the_embed_is_rebuilt_once_for_the_whole_paste(tmp_path):
    """A Discord message edit per rebuild; per line it would be twenty edits for one paste,
    straight into a rate limit and showing states that were stale when they were drawn."""
    channel = _channel()
    bot = await _bot(await _make_db(tmp_path, name="bulk_rebuild"), channel=channel)

    _, rebuild = await _submit(bot, "900000001, accept\n900000002, decline")

    rebuild.assert_awaited_once()
    channel._message.edit.assert_awaited_once()


async def test_nothing_applied_rebuilds_nothing(tmp_path):
    """The round did not change, and an edit would redraw the same embed for no reason."""
    channel = _channel()
    bot = await _bot(await _make_db(tmp_path, name="bulk_norebuild"), channel=channel)

    _, rebuild = await _submit(bot, "rubbish")

    rebuild.assert_not_awaited()
    channel._message.edit.assert_not_awaited()


async def test_a_failed_rebuild_does_not_fail_the_updates(tmp_path):
    """The statuses are already written by then; the embed is a view of them. Discord refuses
    every edit of the call (800): the answer is saved all the same, and the queue stops at the
    redraw, to be retried, rather than the answer being refused."""
    bot = await _bot(
        await _make_db(tmp_path, name="bulk_rebuild_fails"),
        channel=_channel(refuses=True),
    )

    await _submit(bot, "900000001, accept")

    assert (await _applied(bot)) == [(31, "ACCEPTED")]
    stopped = await stopped_job(bot.db_path)
    assert stopped is not None and stopped["name"] == "redraw"


async def test_a_missing_embed_channel_does_not_fail_the_updates(tmp_path):
    """A rehearsal's channels get deleted between runs, and that must not stop the next
    rehearsal being set up: the redraw finds the call gone and completes, the queue is not
    stopped, and the answer is applied."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_nochannel"), channel=None)

    interaction, _ = await _submit(bot, "900000001, accept")

    assert (await _applied(bot)) == [(31, "ACCEPTED")]
    assert "Applied 1 update(s)" in _replied(interaction)
    assert await stopped_job(bot.db_path) is None


async def test_the_changes_are_logged(tmp_path):
    """Test mode changes what a league's data says, so the log is what distinguishes a
    rehearsal's state from a real one afterwards."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_log"))

    await _submit(bot, "900000001, accept")

    logged = str(bot.output_router.post_log.await_args.args[0])
    assert "/attendance test rsvp" in logged
    assert "Pro" in logged
    assert str(ROUND_ID) in logged
    assert "900000001" in logged


async def test_the_changes_are_logged_as_attendance_test_rsvp(tmp_path):
    """The log names the command a maintainer now types, the attendance module's test tool."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_log_name"))

    await _submit(bot, "900000001, accept")

    logged = str(bot.output_router.post_log.await_args.args[0])
    assert logged.splitlines()[0] == "Maintainer (<@77>) | /attendance test rsvp | Success"
    assert "  updates: 1" in logged.splitlines()


async def test_a_paste_with_nothing_applied_records_its_refusal(tmp_path):
    """A paste that changed nothing is a refusal, recorded as one (#482): one ⛔ line naming
    the form and each entry turned away, and no success line, which would be a false record
    of a rehearsal's state."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_nolog"))

    await _submit(bot, "rubbish")

    logged = [str(c.args[0]) for c in bot.output_router.post_log.await_args_list]
    assert len(logged) == 1
    assert logged[0].startswith(
        "\u26d4 the \u201cBulk Set RSVP Statuses\u201d form for Pro refused for "
        "Maintainer (<@77>) \u2014 "
    )
    assert "Line 1: expected `ID, status`" in logged[0]


# ---------------------------------------------------------------------------
# A form submitted on a state that changed while it stood open (#482, decided 2026-10-01)
# ---------------------------------------------------------------------------


async def _write(bot, sql: str, *args) -> None:
    async with get_connection(bot.db_path) as db:
        await db.execute(sql, args)
        await db.commit()


async def _test_mode_off(bot):
    bot.config_service.get_server_config.return_value.test_mode_active = False
    await _write(bot, "UPDATE server_configs SET test_mode_active = 0")


async def _module_off(bot):
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    await _write(bot, "UPDATE attendance_config SET module_enabled = 0")


async def _call_taken_down(bot):
    await _write(bot, "DELETE FROM rsvp_embed_messages WHERE round_id = ?", ROUND_ID)


async def _call_replaced(bot):
    await _write(bot, "UPDATE rsvp_embed_messages SET message_id = ? WHERE round_id = ?",
                 str(EMBED_MESSAGE + 1), ROUND_ID)


@pytest.mark.parametrize(
    "change,reply",
    [
        (_test_mode_off, "Test mode was switched off while this form was open. Nothing was set."),
        (_module_off, "The Attendance module was switched off while this form was open."),
        (_call_taken_down, "The check-in call this form was opened on is no longer the division's open call."),
        (_call_replaced, "The check-in call this form was opened on is no longer the division's open call."),
    ],
    ids=["test-mode-off", "module-off", "call-taken-down", "call-replaced"],
)
async def test_a_form_whose_state_changed_while_open_is_refused(tmp_path, change, reply):
    """A9 (D1): test mode, the module and the call are checked again on submit. Each change
    is refused with nothing written and nothing redrawn, and one refusal line recorded."""
    channel = _channel()
    bot = await _bot(await _make_db(tmp_path, name="bulk_stale"), channel=channel)
    await change(bot)

    interaction, rebuild = await _submit(bot, "900000001, accept")

    assert reply in _replied(interaction)
    assert (await _applied(bot)) == []
    rebuild.assert_not_awaited()
    channel._message.edit.assert_not_awaited()
    logged = [str(c.args[0]) for c in bot.output_router.post_log.await_args_list]
    assert len(logged) == 1
    assert logged[0].startswith(
        "\u26d4 the \u201cBulk Set RSVP Statuses\u201d form for Pro refused for Maintainer (<@77>)"
    )


# ---------------------------------------------------------------------------
# Every outcome of the form is recorded in the log channel (#482)
# ---------------------------------------------------------------------------


async def test_a_paste_with_every_entry_rejected_records_each_one(tmp_path):
    """A10: the reply is unchanged, and the one refusal line lists every entry turned away."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_allbad_log"))

    interaction, _ = await _submit(bot, "rubbish\n123, maybe")

    assert "Errors" in _replied(interaction)
    logged = [str(c.args[0]) for c in bot.output_router.post_log.await_args_list]
    assert len(logged) == 1
    assert "Line 1:" in logged[0]
    assert "Line 2:" in logged[0]


async def test_an_empty_paste_records_that_nothing_changed(tmp_path):
    """A11: a paste of blank lines asks nothing, and records that nothing was changed. It is
    still answered, since an empty reply to a modal submission looks to Discord like a
    command that hung."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_empty_log"))

    interaction, _ = await _submit(bot, "\n  \n")

    assert "No valid entries." in _replied(interaction)
    logged = [str(c.args[0]) for c in bot.output_router.post_log.await_args_list]
    assert len(logged) == 1
    assert logged[0].startswith(
        "Maintainer (<@77>) | /attendance test rsvp | Nothing changed"
    )


async def test_a_part_applied_paste_names_what_it_passed_over(tmp_path):
    """A12: the success line lists every entry passed over beside those applied."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_part_log"))

    await _submit(bot, "900000001, accept\nrubbish")

    logged = str(bot.output_router.post_log.await_args.args[0])
    assert logged.splitlines()[0] == "Maintainer (<@77>) | /attendance test rsvp | Success"
    assert "passed over: Line 2: expected `ID, status`" in logged


@pytest.mark.parametrize(
    "channel", [_channel(call_gone=True), None], ids=["call-gone", "no-channel"]
)
async def test_a_call_not_redrawn_is_named_in_the_line(tmp_path, channel):
    """A13: the call (800) deleted by hand, or its channel deleted: the answers stand, and the
    line says the call was not redrawn. A redraw Discord refuses stops the queue instead
    (`test_a_failed_rebuild_does_not_fail_the_updates`)."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_noredraw_log"), channel=channel)

    await _submit(bot, "900000001, accept")

    logged = str(bot.output_router.post_log.await_args.args[0])
    assert logged.splitlines()[0] == "Maintainer (<@77>) | /attendance test rsvp | Success"
    assert "  call not redrawn" in logged.splitlines()


async def test_a_call_redrawn_is_not_named_as_failed(tmp_path):
    """A13's counterpart: a call redrawn says nothing of a redraw."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_redraw_log"), channel=_channel())

    await _submit(bot, "900000001, accept")

    assert "call not redrawn" not in str(bot.output_router.post_log.await_args.args[0])


# ---------------------------------------------------------------------------
# An entry the service did not save is passed over, not applied (#482)
# ---------------------------------------------------------------------------


async def test_an_entry_not_saved_is_passed_over(tmp_path):
    """An answer the bot did not save is reported and passed over like any other entry that
    cannot be applied: it is neither counted, listed nor logged as applied."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_unsaved"), saved={31})

    interaction, _ = await _submit(bot, "900000001, accept\n900000002, decline")

    replied = _replied(interaction)
    applied, _, errors = replied.partition("Errors")
    assert "Applied 1 update(s)" in applied
    assert "900000001" in applied
    assert "900000002" not in applied
    assert "Line 2:" in errors and "900000002" in errors
    logged = str(bot.output_router.post_log.await_args.args[0])
    assert logged.splitlines()[0] == "Maintainer (<@77>) | /attendance test rsvp | Success"
    assert "  updates: 1" in logged.splitlines()
    changes = next(line for line in logged.splitlines() if line.startswith("  changes: "))
    assert "900000002" not in changes
    passed_over = next(line for line in logged.splitlines() if line.startswith("  passed over: "))
    assert "Line 2:" in passed_over and "900000002" in passed_over


async def test_a_paste_with_nothing_saved_is_refused(tmp_path):
    """A paste none of whose answers was saved changed nothing: it is answered as a paste with
    nothing applied, the call is not redrawn, and one refusal line lists every entry."""
    channel = _channel()
    bot = await _bot(
        await _make_db(tmp_path, name="bulk_none_saved"), channel=channel, saved=set()
    )

    interaction, rebuild = await _submit(bot, "900000001, accept\n900000002, decline")

    replied = _replied(interaction)
    assert "Applied" not in replied
    assert "Errors" in replied
    rebuild.assert_not_awaited()
    channel._message.edit.assert_not_awaited()
    logged = [str(c.args[0]) for c in bot.output_router.post_log.await_args_list]
    assert len(logged) == 1
    assert logged[0].startswith(
        "\u26d4 the \u201cBulk Set RSVP Statuses\u201d form for Pro refused for "
        "Maintainer (<@77>) \u2014 "
    )
    assert "900000001" in logged[0] and "900000002" in logged[0]


async def test_every_answer_of_a_paste_is_saved_at_the_time_the_form_read(tmp_path):
    """The service takes the time rather than reading the clock: the form reads it once and
    hands the same moment to every answer it saves, so both drivers' accepts carry one time,
    with its zone."""
    bot = await _bot(await _make_db(tmp_path, name="bulk_now"))

    await _submit(bot, "900000001, accept\n900000002, accept")

    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT accepted_at FROM driver_round_attendance WHERE round_id = ? "
            "ORDER BY driver_profile_id", (ROUND_ID,),
        )
        stamps = [row["accepted_at"] for row in await cursor.fetchall()]
    assert len(stamps) == 2
    assert stamps[0] is not None
    assert datetime.fromisoformat(stamps[0]).tzinfo is not None
    assert stamps[0] == stamps[1]
