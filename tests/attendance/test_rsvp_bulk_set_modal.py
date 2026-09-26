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
and send a maintainer looking for it in the database.

**Nothing applied means nothing logged and nothing rebuilt.** A paste that was entirely wrong
did not change the round, and a log line saying otherwise would be a false record of a
rehearsal's state.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.cogs.test_mode_cog import _RsvpBulkSetModal
from leaguebot.core.db.database import get_connection, run_migrations

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
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        for discord_uid, profile_id in DRIVERS.items():
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, "
                "current_state, is_test_driver) VALUES (?, ?, 'ASSIGNED', 1)",
                (profile_id, str(discord_uid)),
            )
        await db.commit()
    return db_path


def _bot(db_path: str, *, attendance_rows=None, channel=None):
    """*attendance_rows* is the set of profile ids that have a row for this round."""
    attendance_rows = DRIVERS.values() if attendance_rows is None else attendance_rows

    bot = MagicMock()
    bot.db_path = db_path
    bot.attendance_service = MagicMock()
    bot.attendance_service.get_attendance_row_for_driver = AsyncMock(
        side_effect=lambda **kw: (
            MagicMock() if kw["driver_profile_id"] in attendance_rows else None
        )
    )
    bot.attendance_service.upsert_rsvp_status = AsyncMock(return_value=None)
    bot.get_channel = MagicMock(return_value=channel)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    return bot


def _channel(*, fetch_fails: bool = False):
    channel = MagicMock()
    message = MagicMock()
    message.edit = AsyncMock()
    if fetch_fails:
        channel.fetch_message = AsyncMock(side_effect=RuntimeError("message gone"))
    else:
        channel.fetch_message = AsyncMock(return_value=message)
    channel._message = message
    return channel


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Maintainer"
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _submit(bot, entries: str, interaction=None):
    interaction = interaction or _interaction()
    modal = _RsvpBulkSetModal(
        division_name="Pro",
        division_id=DIVISION_ID,
        round_id=ROUND_ID,
        embed_channel_id=EMBED_CHANNEL,
        embed_message_id=EMBED_MESSAGE,
        bot=bot,
    )
    modal.entries._value = entries  # type: ignore[attr-defined]
    with patch(
        "leaguebot.attendance.services.rsvp_service._rebuild_embed_for_round",
        new=AsyncMock(return_value=MagicMock()),
    ) as rebuild, patch("leaguebot.attendance.services.rsvp_service.RsvpView", new=MagicMock()):
        await modal.on_submit(interaction)
    return interaction, rebuild


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0]) for call in interaction.followup.send.await_args_list if call.args
    )


def _applied(bot) -> list[tuple[int, str]]:
    return [
        (call.kwargs["driver_profile_id"], call.kwargs["status"])
        for call in bot.attendance_service.upsert_rsvp_status.await_args_list
    ]


# ---------------------------------------------------------------------------
# What a good paste does
# ---------------------------------------------------------------------------


async def test_a_single_entry_is_applied(tmp_path):
    bot = _bot(await _make_db(tmp_path))

    interaction, _ = await _submit(bot, "900000001, accept")

    assert _applied(bot) == [(31, "ACCEPTED")]
    assert "Applied 1 update(s)" in _replied(interaction)


async def test_every_entry_of_a_paste_is_applied(tmp_path):
    """Twenty test drivers into a particular state without twenty button presses is the
    whole reason the modal exists."""
    bot = _bot(await _make_db(tmp_path, name="bulk_many"))

    await _submit(bot, "900000001, accept\n900000002, decline")

    assert _applied(bot) == [(31, "ACCEPTED"), (32, "DECLINED")]


@pytest.mark.parametrize(
    "typed,stored",
    [
        ("accept", "ACCEPTED"),
        ("accepted", "ACCEPTED"),
        ("tentative", "TENTATIVE"),
        ("decline", "DECLINED"),
        ("declined", "DECLINED"),
    ],
)
async def test_the_vocabulary_is_the_maintainers(tmp_path, typed, stored):
    """Typed by hand under time pressure during a rehearsal, so both the verb and the
    participle are accepted."""
    bot = _bot(await _make_db(tmp_path, name=f"bulk_{typed}"))

    await _submit(bot, f"900000001, {typed}")

    assert _applied(bot) == [(31, stored)]


async def test_the_status_is_read_regardless_of_case(tmp_path):
    bot = _bot(await _make_db(tmp_path, name="bulk_case"))

    await _submit(bot, "900000001, ACCEPT")

    assert _applied(bot) == [(31, "ACCEPTED")]


async def test_surrounding_whitespace_is_ignored(tmp_path):
    """A paste out of a spreadsheet or a chat message carries it, and refusing over a space
    would be a puzzle rather than a message."""
    bot = _bot(await _make_db(tmp_path, name="bulk_spaces"))

    await _submit(bot, "   900000001 ,   accept   ")

    assert _applied(bot) == [(31, "ACCEPTED")]


async def test_blank_lines_are_skipped_silently(tmp_path):
    """They are how a paste is spaced, not a mistake to report."""
    bot = _bot(await _make_db(tmp_path, name="bulk_blanks"))

    interaction, _ = await _submit(bot, "900000001, accept\n\n   \n900000002, accept")

    assert len(_applied(bot)) == 2
    assert "Errors" not in _replied(interaction)


async def test_a_status_containing_a_comma_is_kept_whole(tmp_path):
    """The line is split once, so only the first comma separates — which is what lets the
    error name the whole status a maintainer typed rather than its first word."""
    bot = _bot(await _make_db(tmp_path, name="bulk_comma"))

    interaction, _ = await _submit(bot, "900000001, accept, maybe")

    assert "accept, maybe" in _replied(interaction)


async def test_the_reply_lists_what_was_applied(tmp_path):
    """A maintainer setting up a scenario needs to see the state they now have, not a
    count — the point of the paste is the arrangement it produces."""
    bot = _bot(await _make_db(tmp_path, name="bulk_reply"))

    interaction, _ = await _submit(bot, "900000001, accept\n900000002, tentative")

    replied = _replied(interaction)
    assert "900000001` → accepted" in replied
    assert "900000002` → tentative" in replied
    assert "Pro" in replied


# ---------------------------------------------------------------------------
# The five ways a line can be wrong
# ---------------------------------------------------------------------------


async def test_a_line_without_a_comma_is_refused(tmp_path):
    bot = _bot(await _make_db(tmp_path, name="bulk_nocomma"))

    interaction, _ = await _submit(bot, "900000001 accept")

    assert "Line 1: expected `ID, status`" in _replied(interaction)
    assert _applied(bot) == []


async def test_a_non_numeric_id_is_refused(tmp_path):
    bot = _bot(await _make_db(tmp_path, name="bulk_badid"))

    interaction, _ = await _submit(bot, "driver-one, accept")

    assert "not a valid numeric ID" in _replied(interaction)
    assert _applied(bot) == []


async def test_an_unknown_status_is_refused_and_the_options_given(tmp_path):
    """The three words are the fix, and a maintainer who guessed "yes" needs them rather
    than to be told their guess was wrong."""
    bot = _bot(await _make_db(tmp_path, name="bulk_badstatus"))

    interaction, _ = await _submit(bot, "900000001, yes")

    replied = _replied(interaction)
    assert "unknown status `yes`" in replied
    assert "accept, tentative, or decline" in replied


async def test_an_id_with_no_driver_profile_is_refused(tmp_path):
    """Distinct from a driver with no attendance row: one is a wrong id, the other is a
    driver in the wrong division, and they are fixed differently."""
    bot = _bot(await _make_db(tmp_path, name="bulk_noprofile"))

    interaction, _ = await _submit(bot, "900000009, accept")

    assert "no driver profile for ID `900000009`" in _replied(interaction)
    assert _applied(bot) == []


async def test_a_driver_with_no_attendance_row_is_refused(tmp_path):
    """They are in the league but not in this round's division, so there is nothing to set
    a status on."""
    bot = _bot(await _make_db(tmp_path, name="bulk_norow"), attendance_rows={31})

    interaction, _ = await _submit(bot, "900000002, accept")

    assert "no attendance row for this round" in _replied(interaction)
    assert _applied(bot) == []


# ---------------------------------------------------------------------------
# Partial success
# ---------------------------------------------------------------------------


async def test_a_bad_line_does_not_stop_the_good_ones(tmp_path):
    """A maintainer pasting twenty lines with a typo in the fourth wants the other
    nineteen applied."""
    bot = _bot(await _make_db(tmp_path, name="bulk_partial"))

    interaction, _ = await _submit(
        bot, "900000001, accept\nrubbish\n900000002, decline"
    )

    assert _applied(bot) == [(31, "ACCEPTED"), (32, "DECLINED")]
    assert "Applied 2 update(s)" in _replied(interaction)
    assert "Errors" in _replied(interaction)


async def test_an_error_names_the_line_it_was_on(tmp_path):
    """A list of errors that did not say which line would be no better than refusing the
    whole paste."""
    bot = _bot(await _make_db(tmp_path, name="bulk_lineno"))

    interaction, _ = await _submit(bot, "900000001, accept\n900000002, maybe")

    assert "Line 2:" in _replied(interaction)


async def test_line_numbers_count_blank_lines_too(tmp_path):
    """They are what a maintainer sees in the box they pasted into; renumbering around the
    blanks would point at the wrong line of their own text."""
    bot = _bot(await _make_db(tmp_path, name="bulk_lineno_blank"))

    interaction, _ = await _submit(bot, "\n\n900000001, maybe")

    assert "Line 3:" in _replied(interaction)


async def test_an_entirely_wrong_paste_says_so(tmp_path):
    bot = _bot(await _make_db(tmp_path, name="bulk_allbad"))

    interaction, _ = await _submit(bot, "rubbish\nmore rubbish")

    replied = _replied(interaction)
    assert "Applied" not in replied
    assert "Errors" in replied


async def test_an_empty_paste_is_answered_rather_than_ignored(tmp_path):
    """An empty reply to a modal submission looks to Discord like a command that hung."""
    bot = _bot(await _make_db(tmp_path, name="bulk_empty"))

    interaction, _ = await _submit(bot, "\n  \n")

    assert "No valid entries." in _replied(interaction)


# ---------------------------------------------------------------------------
# The embed, and the log
# ---------------------------------------------------------------------------


async def test_the_embed_is_rebuilt_once_for_the_whole_paste(tmp_path):
    """A Discord message edit per rebuild; per line it would be twenty edits for one paste,
    straight into a rate limit and showing states that were stale when they were drawn."""
    channel = _channel()
    bot = _bot(await _make_db(tmp_path, name="bulk_rebuild"), channel=channel)

    _, rebuild = await _submit(bot, "900000001, accept\n900000002, decline")

    rebuild.assert_awaited_once()
    channel._message.edit.assert_awaited_once()


async def test_nothing_applied_rebuilds_nothing(tmp_path):
    """The round did not change, and an edit would redraw the same embed for no reason."""
    channel = _channel()
    bot = _bot(await _make_db(tmp_path, name="bulk_norebuild"), channel=channel)

    _, rebuild = await _submit(bot, "rubbish")

    rebuild.assert_not_awaited()
    channel._message.edit.assert_not_awaited()


async def test_a_failed_rebuild_does_not_fail_the_updates(tmp_path):
    """The statuses are already written by then; the embed is a view of them. Reporting an
    error would send a maintainer looking for work that succeeded."""
    bot = _bot(
        await _make_db(tmp_path, name="bulk_rebuild_fails"),
        channel=_channel(fetch_fails=True),
    )

    interaction, _ = await _submit(bot, "900000001, accept")

    assert _applied(bot) == [(31, "ACCEPTED")]
    assert "Applied 1 update(s)" in _replied(interaction)


async def test_a_missing_embed_channel_does_not_fail_the_updates(tmp_path):
    """A rehearsal's channels get deleted between runs, and that must not stop the next
    rehearsal being set up."""
    bot = _bot(await _make_db(tmp_path, name="bulk_nochannel"), channel=None)

    interaction, _ = await _submit(bot, "900000001, accept")

    assert _applied(bot) == [(31, "ACCEPTED")]
    assert "Applied 1 update(s)" in _replied(interaction)


async def test_the_changes_are_logged(tmp_path):
    """Test mode changes what a league's data says, so the log is what distinguishes a
    rehearsal's state from a real one afterwards."""
    bot = _bot(await _make_db(tmp_path, name="bulk_log"))

    await _submit(bot, "900000001, accept")

    logged = str(bot.output_router.post_log.await_args.args[0])
    assert "/test-mode rsvp set-status" in logged
    assert "Pro" in logged
    assert str(ROUND_ID) in logged
    assert "900000001" in logged


@pytest.mark.xfail(
    strict=True, reason="#462: the modal still logs its changes as /test-mode rsvp set-status"
)
async def test_the_changes_are_logged_as_attendance_test_rsvp(tmp_path):
    """The log names the command a maintainer now types, the attendance module's test tool."""
    bot = _bot(await _make_db(tmp_path, name="bulk_log_name"))

    await _submit(bot, "900000001, accept")

    logged = str(bot.output_router.post_log.await_args.args[0])
    assert logged.splitlines()[0] == "Maintainer (<@77>) | /attendance test rsvp | 1 update(s)"


async def test_nothing_applied_is_not_logged(tmp_path):
    """A log line for a paste that changed nothing would be a false record of a rehearsal's
    state."""
    bot = _bot(await _make_db(tmp_path, name="bulk_nolog"))

    await _submit(bot, "rubbish")

    bot.output_router.post_log.assert_not_awaited()
