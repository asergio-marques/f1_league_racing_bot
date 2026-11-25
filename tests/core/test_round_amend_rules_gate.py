"""`/round amend` refuses what the rules refuse, and says what an amendment costs.

The rules themselves are tested in `test_amendment_rules_service.py`, which drives them pure.
These drive the *command*: that it asks, that a refusal reaches the manager instead of a
confirmation, and that nothing is offered for an amendment the rules will not allow. A gate that
decides correctly and is never consulted is the failure this file is here to catch.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.round_cog import RoundCog
from leaguebot.framework.db.database import get_connection, run_migrations
from tests.support.change_queue import change_rows, league_bot, league_queue, run_queue
from tests.support.setup_reads import hold_setup
from tests.support.switches import answer_switch

SERVER_ID = 4400
USER_ID = 88
DIVISION = "Div A"
#: Seeded by migration 029, so `resolve_track_name` finds it in earnest.
NEW_TRACK = "Silverstone Circuit"
#: The kind of change Confirm asks the queue for.
ROUND_AMEND_KIND = "season.round.amend"


async def _db(
    tmp_path,
    *,
    scheduled_at: datetime,
    phase1_done: int = 0,
    phase2_done: int = 0,
    round_format: str = "NORMAL",
    track_name: str | None = "Bahrain International Circuit",
    status: str = "NOT_RUN",
) -> str:
    path = str(tmp_path / "round_amend.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, forecast_channel_id, mention_role_id) "
            "VALUES (1, 1, ?, 1, 999, 555)",
            (DIVISION,),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at, phase1_done, phase2_done, status) VALUES (1, 1, 1, ?, ?, ?, ?, ?, ?)",
            (
                round_format, track_name, scheduled_at.isoformat(), phase1_done, phase2_done,
                status,
            ),
        )
        await db.commit()
    return path


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = USER_ID
    interaction.user.display_name = "Manager"
    interaction.response.defer = AsyncMock()
    # The message a follow-up sends, which the queue edits with the outcome.
    interaction.followup.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    interaction.edit_original_response = AsyncMock()
    return interaction


async def _cog(
    db_path, *, attendance: bool = True, weather: bool = False, now: datetime | None = None,
):
    cog = RoundCog.__new__(RoundCog)
    # The league's bot (`league_bot`), with each awaited member it reads named (issue #240). A
    # whole-bot ``AsyncMock`` answers an unpinned reader with a truthy mock, which for a file
    # about *rules* would mean a rule passing on the stub rather than the round in front of it.
    cog.bot = await league_bot(db_path)
    cog.bot.output_router.post_log = AsyncMock()
    answer_switch(cog.bot.module_service, "attendance", attendance)
    for module in ("signup", "results", "images"):
        answer_switch(cog.bot.module_service, module, False)
    answer_switch(cog.bot.module_service, "weather", weather)
    cog.bot.attendance_service.get_config_or_defaults = AsyncMock(
        return_value=SimpleNamespace(
            rsvp_notice_days=5, rsvp_last_notice_hours=24, rsvp_deadline_hours=2
        )
    )
    # The windows an amendment is judged against are read through the builder's own hook, off
    # these doubles: attendance's where it is on, weather's always.
    # The queue writes the lines a change saves through `queue_log_on`, which `_lines` reads
    # beside the refusals posted at once.
    cog.bot.output_router.queue_log_on = AsyncMock(return_value=None)
    cog.bot.output_router.deliver_queued = AsyncMock()
    cog.bot.output_router.strip_view = AsyncMock()
    # A real change queue with the bot's own change types, so that Confirm asks it for the
    # amendment and its check judges it as it would in the league. Its clock reads *now*, the
    # host's where none is given.
    league_queue(cog.bot, db_path, now=now or datetime.now(timezone.utc))
    # No season being set up, so the command takes its active-season path.
    hold_setup(cog.bot.season_service, None, divisions=False)
    if hasattr(RoundCog, "clear_pending"):
        # While the cog still keeps a setup in memory, it keeps none here.
        cog._pending = {}
    return cog


async def _amendments(path: str) -> list[dict]:
    """Every round amendment asked of the queue, in the order asked."""
    return [row for row in await change_rows(path) if row["kind"] == ROUND_AMEND_KIND]


async def _amend(cog, interaction, **kwargs):
    """Drive the command past its permission guard.

    `league_manager_only` wraps the callback in the channel-and-role guard, which has its own
    tests and is not what these are about — satisfying it here would mean building a member,
    a role and a server config in every one of them to reach the line under test. `functools
    .wraps` leaves the undecorated function on ``__wrapped__``, so the guard is stepped over
    rather than stubbed out.
    """
    await RoundCog.round_amend.callback.__wrapped__(cog, interaction, DIVISION, 1, **kwargs)


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


async def test_bringing_a_round_inside_its_check_in_deadline_is_refused(tmp_path):
    """The round would have a check-in that opens and closes in the same instant."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    interaction = _interaction()
    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)

    await _amend(await _cog(path), interaction, scheduled_at=soon.isoformat())

    assert "check-in deadline" in _reply(interaction)
    assert not _offered_a_confirmation(interaction)


async def test_moving_a_round_backwards_is_refused_with_attendance_off(tmp_path):
    """The command consults the rule, and with no module standing in for it, at the change
    queue's time: the queue's clock reads 4 March 2031, years past the host's, so an hour before
    it is still ahead of the host's and only the queue's clock finds it in the past.

    Attendance off deliberately: its deadline rule refuses a backwards move as well, so a test
    that left the module on would pass whether or not the command reached the rule at all.
    """
    now = datetime(2031, 3, 4, 5, 6, tzinfo=timezone.utc)
    path = await _db(tmp_path, scheduled_at=now + timedelta(days=30))
    interaction = _interaction()
    past = (now - timedelta(hours=1)).replace(tzinfo=None)

    await _amend(
        await _cog(path, attendance=False, now=now), interaction, scheduled_at=past.isoformat()
    )

    assert "cannot be moved into the past" in _reply(interaction)
    assert not _offered_a_confirmation(interaction), "a refused amendment must not be offered"


async def test_a_refusal_says_nothing_was_changed(tmp_path):
    """The manager must not be left wondering whether half of it went through."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) - timedelta(hours=1))
    interaction = _interaction()

    await _amend(await _cog(path), interaction, track=NEW_TRACK)

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

    await _amend(await _cog(path, attendance=False), interaction, scheduled_at=soon.isoformat())

    assert _offered_a_confirmation(interaction)


# ---------------------------------------------------------------------------
# What the confirmation says it will cost
# ---------------------------------------------------------------------------


async def test_an_allowed_amendment_is_offered_for_confirmation(tmp_path):
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    interaction = _interaction()

    await _amend(await _cog(path), interaction, track=NEW_TRACK)

    assert _offered_a_confirmation(interaction)
    assert "Amend Round 1" in _reply(interaction)


@pytest.mark.parametrize(("kwargs", "line"), [
    pytest.param(
        {"scheduled_at": "2099-06-21T18:00:00"},
        "  • `scheduled_at` → `2099-06-21 18:00:00`",
        id="a-moment-with-no-zone",
    ),
    pytest.param(
        {"scheduled_at": "2099-06-21T20:00:00+02:00"},
        "  • `scheduled_at` → `2099-06-21 18:00:00`",
        id="a-moment-with-a-zone",
    ),
])
async def test_the_confirmation_shows_each_new_value_as_a_league_reads_it(tmp_path, kwargs, line):
    """The season is active, with attendance off, and round 1 a month out with nothing drawn. A
    manager amends it. The confirmation offered names each new value as a league reads it: a new
    moment, typed with no zone or as 20:00 at +02:00, as UTC with no offset, "• `scheduled_at` →
    `2099-06-21 18:00:00`"."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    interaction = _interaction()

    await _amend(await _cog(path, attendance=False), interaction, **kwargs)

    assert _offered_a_confirmation(interaction)
    assert line in _reply(interaction)


async def test_the_summary_names_the_forecasts_it_will_withdraw(tmp_path):
    """Weather is on and attendance off. Round 1 of Div A races in three hours, its Phase 1 and
    Phase 2 forecasts drawn and Phase 3's not yet. A manager moves it a month out. The
    confirmation names the field changed, then the drawn phases the move puts back in question,
    Phase 1 and Phase 2, as withdrawn and redrawn; Phase 3, never drawn, is not named (core spec,
    "Amending and cancelling a round"; criterion 5)."""
    now = datetime(2031, 3, 4, 5, 6, tzinfo=timezone.utc)
    path = await _db(tmp_path, scheduled_at=now + timedelta(hours=3), phase1_done=1, phase2_done=1)
    interaction = _interaction()

    await _amend(await _cog(path, attendance=False, weather=True, now=now), interaction,
                 scheduled_at="2031-04-04T05:06:00")

    assert _reply(interaction) == (
        "**Amend Round 1** in division **Div A**:\n"
        "  \u2022 `scheduled_at` \u2192 `2031-04-04 05:06:00`\n"
        "\n\u26a0\ufe0f This will withdraw and redraw Phase 1, Phase 2 for this round."
    )


async def test_the_summary_says_nothing_of_forecasts_where_none_was_drawn(tmp_path):
    """Weather is on. Round 1 of Div A is a month out and has drawn nothing yet. A manager changes
    its circuit. The confirmation names the field changed and says nothing about forecasts: none
    is withdrawn and none stands (owner, 2026-11-14, "Nothing about forecasts"; criterion 5)."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    interaction = _interaction()

    await _amend(await _cog(path, weather=True), interaction, track=NEW_TRACK)

    assert _reply(interaction) == (
        "**Amend Round 1** in division **Div A**:\n"
        "  \u2022 `track_name` \u2192 `Silverstone Circuit`"
    )
    assert _offered_a_confirmation(interaction)


async def test_the_summary_promises_no_redraw_while_weather_is_off(tmp_path):
    """Weather and attendance are off. Round 1 of Div A races in three hours, its Phase 1 and
    Phase 2 forecasts drawn. A manager moves it a month out. The confirmation names Phase 1 and
    Phase 2 as withdrawn and promises no redraw, since no phase is drawn while weather is off
    (weather spec, "Changes to a round after generation"; criterion 5)."""
    now = datetime(2031, 3, 4, 5, 6, tzinfo=timezone.utc)
    path = await _db(tmp_path, scheduled_at=now + timedelta(hours=3), phase1_done=1, phase2_done=1)
    interaction = _interaction()

    await _amend(await _cog(path, attendance=False, now=now), interaction,
                 scheduled_at="2031-04-04T05:06:00")

    assert _reply(interaction) == (
        "**Amend Round 1** in division **Div A**:\n"
        "  \u2022 `scheduled_at` \u2192 `2031-04-04 05:06:00`\n"
        "\n\u26a0\ufe0f This will withdraw Phase 1, Phase 2 for this round."
    )


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

    await _amend(await _cog(path, attendance=False), interaction, scheduled_at=nearer.isoformat())

    # Word for word, as it always has read.
    assert _reply(interaction) == (
        "**Amend Round 1** in division **Div A**:\n"
        f"  \u2022 `scheduled_at` \u2192 `{nearer}`\n"
        "\n\u26a0\ufe0f The forecasts already posted for this round will stand."
    )


async def _called(path: str, *, notified: bool = False) -> None:
    """Round 1's check-in call posted (message 7001), its last notice too (7002) where
    *notified*."""
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, channel_id, "
            "posted_at, last_notice_msg_id) VALUES (1, 1, '7001', '77', ?, ?)",
            (datetime.now(timezone.utc).isoformat(), "7002" if notified else None),
        )
        await db.commit()


@pytest.mark.parametrize("called, warning", [
    pytest.param(
        False,
        "\u26a0\ufe0f The check-in call for that moment is already due, so it will be posted at "
        "once.",
        id="no-call-posted",
    ),
    pytest.param(
        True,
        "\u26a0\ufe0f The check-in call for that moment is already due, so the call already "
        "posted will be posted again at once, carrying what changed and keeping every answer "
        "given to it.",
        id="a-call-standing",
    ),
])
async def test_the_check_in_warnings_follow_the_forecasts_line(tmp_path, called, warning):
    """The queue's clock reads 4 March 2031, 05:06 UTC. Round 1 of Div A races in three hours,
    its Phase 1 and Phase 2 forecasts drawn, with weather on, attendance on (check-in call 5 days
    before, last notice 24 hours, deadline 2 hours) and the forecast horizons at their defaults
    (5 days, 2 days, 2 hours). Either no check-in call has gone out for it, or one has and its
    last notice has not. A manager moves it to three days out, its call's moment passed and its
    last notice's still ahead. The confirmation names the moment, then the drawn phase the move
    withdraws and redraws (Phase 2, Phase 1's horizon being behind the clock and Phase 3 never
    drawn), then the check-in warning saying what will happen: the call posted at once where none
    stands, or posted again keeping every answer where one does. The forecasts' line comes first
    and the check-in's after."""
    now = datetime(2031, 3, 4, 5, 6, tzinfo=timezone.utc)
    path = await _db(tmp_path, scheduled_at=now + timedelta(hours=3), phase1_done=1, phase2_done=1)
    if called:
        await _called(path)
    interaction = _interaction()

    await _amend(await _cog(path, weather=True, now=now), interaction,
                 scheduled_at="2031-03-07T05:06:00")

    assert _reply(interaction) == (
        "**Amend Round 1** in division **Div A**:\n"
        "  \u2022 `scheduled_at` \u2192 `2031-03-07 05:06:00`\n"
        "\n\u26a0\ufe0f This will withdraw and redraw Phase 2 for this round.\n"
        + warning
    )
    assert _offered_a_confirmation(interaction)


_STATUS = (
    "This round's results have been entered, so it can no longer be amended. "
    "Drivers have reports and appeals to lodge against them."
)
_PAST = (
    "A round cannot be moved into the past. Its result submission would open late, at once, "
    "and not at the round's moment. Give it a moment still to come."
)
_DEADLINE = (
    "The check-in deadline for that moment has already passed, so the round would have no "
    "check-in at all. Move it further out, or shorten the deadline."
)
_TRACK_DRAWN = (
    "The first forecast for this round has already been posted, and it was drawn for its "
    "present circuit. Amend the round's moment as well to put the forecasts back in question."
)
_FORMAT_DRAWN = (
    "The second forecast for this round has already been posted, and it was drawn for its "
    "present sessions. Amend the round's moment as well to put the forecasts back in question."
)
_STARTED = "This round has already started, so its track and format can no longer be amended."
_MYSTERY = "A mystery round names no circuit. Change its format as well to give it one."


@pytest.mark.parametrize("round_format, track_name, asked, refusals", [
    pytest.param(
        "MYSTERY", None, {"track": NEW_TRACK},
        [_STATUS, _PAST, _DEADLINE, _TRACK_DRAWN, _STARTED, _MYSTERY],
        id="a-mystery-round-named-a-circuit",
    ),
    pytest.param(
        "NORMAL", "Bahrain International Circuit", {"track": NEW_TRACK, "format": "SPRINT"},
        [_STATUS, _PAST, _DEADLINE, _TRACK_DRAWN, _FORMAT_DRAWN, _STARTED],
        id="a-round-given-a-new-circuit-and-format",
    ),
])
async def test_every_refusal_is_given_in_today_s_order(
    tmp_path, round_format, track_name, asked, refusals
):
    """The queue's clock reads 4 March 2031, 05:06 UTC, with attendance on. Round 1 of Div A is
    a day out, its results already entered (FINAL), and its first two forecasts posted. A manager
    asks to move it an hour into the past and, in the same breath, either names a circuit for it
    as a mystery round or gives it a new circuit and format. The refusal names every rule broken,
    one bullet each, in today's order and words: the round's status, the move into the past, the
    check-in deadline, the forecasts already posted, the round already started, the mystery round
    named no circuit; then says nothing has been changed, and offers no confirmation."""
    now = datetime(2031, 3, 4, 5, 6, tzinfo=timezone.utc)
    path = await _db(
        tmp_path, scheduled_at=now + timedelta(days=1), phase1_done=1, phase2_done=1,
        round_format=round_format, track_name=track_name, status="FINAL",
    )
    interaction = _interaction()

    await _amend(
        await _cog(path, now=now), interaction, scheduled_at="2031-03-04T04:06:00", **asked
    )

    assert _reply(interaction) == (
        "\u26d4 **Round 1** in **Div A** cannot be amended:\n"
        + "\n".join(f"\u2022 {reason}" for reason in refusals)
        + "\n\n**Nothing has been changed.**"
    )
    assert not _offered_a_confirmation(interaction)


# ---------------------------------------------------------------------------
# The rules are judged again when the amendment is confirmed
# ---------------------------------------------------------------------------
#
# `_ConfirmView` stands for two minutes. The world can move inside them, and an amendment
# allowed on the strength of a window that has since closed is the silent loss these rules
# exist to prevent. Every test below constructs a View, so every one is `async def`: apt's
# discord.py 2.5.0 calls `asyncio.get_running_loop()` in `View.__init__`.


def _view(cog, amendments):
    from leaguebot.core.cogs.round_cog import _ConfirmView

    return _ConfirmView(
        cog=cog, interaction_user_id=USER_ID, round_id=1, amendments=amendments
    )


async def test_a_window_passing_while_the_confirmation_stands_refuses_it(tmp_path):
    """Offered while the check-in deadline was ahead, pressed once it is behind.

    The round sits an hour out, so its deadline — two hours before it — has already gone by.
    Confirming would apply an amendment the command itself would no longer offer.
    """
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1))
    cog = await _cog(path)
    interaction = _interaction()

    await _view(cog, [("track_name", NEW_TRACK)]).confirm.callback(interaction)

    assert "can no longer be amended" in _reply(interaction)
    assert await _amendments(path) == []


async def test_a_last_notice_posted_while_the_confirmation_stands_refuses_the_move(tmp_path):
    """Round 1 of Div A races in twenty hours, attendance on (call 5 days, last notice 24 hours,
    deadline 2 hours), its check-in call posted. A manager is offered a move to two days out,
    its call's moment passed and its last notice's ahead; before Confirm is pressed the round's
    last notice is posted. Confirm refuses, naming the last notice already posted, and nothing is
    asked of the queue."""
    now = datetime.now(timezone.utc)
    path = await _db(tmp_path, scheduled_at=now + timedelta(hours=20))
    await _called(path, notified=True)
    cog = await _cog(path)
    interaction = _interaction()

    await _view(cog, [("scheduled_at", now + timedelta(days=2))]).confirm.callback(interaction)

    assert "can no longer be amended" in _reply(interaction)
    assert "last notice for this round has already been posted" in _reply(interaction)
    assert await _amendments(path) == []


async def test_results_entered_while_the_confirmation_stands_refuses_it(tmp_path):
    """The other way the world moves: the round's results come in before the button is pressed."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    async with get_connection(path) as db:
        await db.execute(
            "UPDATE rounds SET status = 'AWAITING_REPORT_VERDICTS' WHERE id = 1"
        )
        await db.commit()
    cog = await _cog(path)
    interaction = _interaction()

    await _view(cog, [("track_name", NEW_TRACK)]).confirm.callback(interaction)

    assert "results have been entered" in _reply(interaction)
    assert await _amendments(path) == []


async def test_a_confirmation_the_rules_still_allow_goes_through(tmp_path):
    """The gate must not cost a manager an amendment that is still perfectly good: Confirm asks
    the change queue for one amendment of round 1 of Div A, carrying the whole change set."""
    import json

    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    cog = await _cog(path)
    interaction = _interaction()

    await _view(cog, [("track_name", NEW_TRACK)]).confirm.callback(interaction)

    [asked] = await _amendments(path)
    payload = json.loads(asked["payload"])
    assert payload["round_id"] == 1
    assert payload["division_id"] == 1
    assert payload["division_name"] == DIVISION
    # And the whole change set went in one change, not one change per field.
    assert payload["changes"] == [["track_name", NEW_TRACK]]


# ---------------------------------------------------------------------------
# A failed confirmation, and every outcome, reach the log channel (#442)
# ---------------------------------------------------------------------------


def _recording(cog, interaction):
    """Let the refusal, the cancel and the failure reach a log channel, naming the member."""
    interaction.client = cog.bot
    interaction.command.qualified_name = "round amend"
    cog.bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    member = MagicMock()
    member.display_name = "Manager"
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=member)
    cog.bot.get_guild = MagicMock(return_value=guild)


def _lines(cog) -> list[str]:
    """The lines written to the log channel: those posted at once, then those a change saved."""
    router = cog.bot.output_router
    return [str(c.args[0]) for c in router.post_log.await_args_list] + [
        str(c.args[1]) for c in router.queue_log_on.await_args_list
    ]


def _answered(interaction) -> MagicMock:
    """A button press nothing has answered yet."""
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    return interaction


def _all_replies(interaction) -> str:
    calls = interaction.followup.send.await_args_list
    if isinstance(interaction.response.send_message, AsyncMock):
        calls = interaction.response.send_message.await_args_list + calls
    return "\n".join(str(call.args[0]) for call in calls if call.args)


async def test_a_failed_round_amend_confirmation_goes_to_report_failure(tmp_path):
    """A fault while the amendment is asked of the change queue is the bot's: the standard
    failure reply naming `/round amend` and the round, one line in the log channel, and the
    buttons stopped."""
    import sqlite3

    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    cog = await _cog(path)
    cog.bot.change_queue.ask = AsyncMock(
        side_effect=sqlite3.OperationalError("database is locked")
    )
    interaction = _interaction()
    _recording(cog, interaction)
    view = _view(cog, [("track_name", NEW_TRACK)])

    await view.confirm.callback(interaction)

    reply = _reply(interaction)
    assert "❌ `/round amend` of round 1 stopped on a fault in the bot" in reply
    assert "partly done" in reply
    assert "database is locked" not in reply
    assert "The amendment failed" not in reply
    [line] = _lines(cog)
    assert "`/round amend` of round 1 failed for Manager (<@88>)" in line
    assert "— the bot could not read or write its database." in line
    assert "OperationalError" not in line
    assert view.is_finished()


#: Every refusal `/round amend` makes of a round of a season being raced, and each of its
#: Confirm button's: the case, the arguments, and a fragment of the reply. A round of the season
#: being set up is refused through the change queue, in `test_amendment_cog_pending.py`.
ROUND_AMEND_REFUSALS = [
    pytest.param("command", {}, "Provide at least one field", id="no-field-given"),
    pytest.param("no season", {"track": NEW_TRACK}, "No season is being raced", id="no-season"),
    pytest.param("command", {"division": "Nowhere", "track": NEW_TRACK}, "not found",
                 id="unknown-division"),
    pytest.param("command", {"round_number": 9, "track": NEW_TRACK}, "not found",
                 id="unknown-round"),
    pytest.param("command", {"track": "Nowhere Ring"}, "Unknown track", id="unknown-track"),
    pytest.param("command", {"scheduled_at": "soon"}, "Invalid datetime", id="bad-datetime"),
    pytest.param("command", {"format": "DRAG"}, "Invalid format", id="bad-format"),
    pytest.param("started", {"track": NEW_TRACK}, "already started", id="the-rules-refuse"),
    pytest.param("first forecast posted", {"track": NEW_TRACK},
                 "first forecast for this round has already been posted",
                 id="the-first-forecast-stands"),
    pytest.param("moment held", {}, "already holds round 2", id="the-moment-is-held"),
    pytest.param("last notice passed", {},
                 "last notice for that moment has already passed", id="the-last-notice-passed"),
    pytest.param("last notice posted", {},
                 "last notice for this round has already been posted",
                 id="the-last-notice-is-posted"),
    pytest.param("confirm by another", {}, "Not your action", id="confirm-not-your-action"),
    pytest.param("confirm round gone", {}, "no longer exists", id="confirm-round-gone"),
    pytest.param("confirm window passed", {}, "can no longer be amended",
                 id="confirm-no-longer-allowed"),
]


@pytest.mark.parametrize("case, kwargs, said", ROUND_AMEND_REFUSALS)
async def test_every_round_amend_refusal_reaches_the_log_channel(tmp_path, case, kwargs, said):
    """Each refusal answers the manager as before, changes nothing, and writes one line in the
    standard refusal form. On the command's path nothing is offered for it: a round that has
    started, say, refuses a track amendment rather than offering a confirmation of one."""
    started = case == "started"
    soon = case == "confirm window passed"
    # Three days out, inside its first forecast's horizon, with that forecast posted.
    forecast = case == "first forecast posted"
    # The last notice posted: the round twenty hours out, its call and last notice out.
    notified = case == "last notice posted"
    when = datetime.now(timezone.utc) + (
        timedelta(hours=-1) if started else timedelta(hours=1) if soon
        else timedelta(days=3) if forecast else timedelta(hours=20) if notified
        else timedelta(days=30)
    )
    path = await _db(tmp_path, scheduled_at=when, phase1_done=int(forecast))
    if notified:
        # Moved to two days out: its call's moment passed, its last notice's still ahead.
        await _called(path, notified=True)
        kwargs = {"scheduled_at": _as_typed(datetime.now(timezone.utc) + timedelta(days=2))}
    if case == "last notice passed":
        # Moved to three hours out: its last notice's moment passed, its deadline's ahead.
        kwargs = {"scheduled_at": _as_typed(datetime.now(timezone.utc) + timedelta(hours=3))}
    if case == "moment held":
        # Round 2 of the division, not run, a day after round 1: the moment round 1 is asked to.
        held = (when + timedelta(days=1)).replace(microsecond=0)
        async with get_connection(path) as db:
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
                "scheduled_at, status) VALUES (2, 1, 2, 'NORMAL', 'Silverstone Circuit', ?, "
                "'NOT_RUN')",
                (held.isoformat(),),
            )
            await db.commit()
        kwargs = {"scheduled_at": held.strftime("%Y-%m-%dT%H:%M:%S")}
    cog = await _cog(path)
    interaction = _interaction()
    _recording(cog, interaction)

    if case.startswith("confirm"):
        _answered(interaction)
        if case == "confirm by another":
            interaction.user.id = USER_ID + 1
        if case == "confirm round gone":
            cog.bot.season_service.get_round = AsyncMock(return_value=None)
        await _view(cog, [("track_name", NEW_TRACK)]).confirm.callback(interaction)
    else:
        if case == "no season":
            cog.bot.season_service.get_confirmed_season = AsyncMock(return_value=None)
        kwargs = dict(kwargs)
        division = kwargs.pop("division", DIVISION)
        round_number = kwargs.pop("round_number", 1)
        await RoundCog.round_amend.callback.__wrapped__(
            cog, interaction, division, round_number, **kwargs
        )

    assert said in _all_replies(interaction)
    if not case.startswith("confirm"):
        assert not _offered_a_confirmation(interaction), "a refused amendment must not be offered"
    assert await _amendments(path) == []
    # The member refused is the one who pressed: on another's Confirm, member 89, not 88.
    refused = USER_ID + 1 if case == "confirm by another" else USER_ID
    [line] = _lines(cog)
    assert line.startswith("⛔ ")
    assert "/round amend" in line
    assert f"refused for Manager (<@{refused}>)" in line


@pytest.mark.parametrize("how", ["cancelled", "lapsed"])
async def test_every_group_e_cancel_and_lapse_reaches_the_log_channel(tmp_path, how):
    """`/round amend`'s confirmation, cancelled with its button or left for its two minutes:
    one line in the standard form, naming the member who ran it, and nothing amended. Beneath
    it the line says what became of the round, and what to do next."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    cog = await _cog(path)
    interaction = _answered(_interaction())
    _recording(cog, interaction)
    view = _view(cog, [("track_name", NEW_TRACK)])

    if how == "cancelled":
        await view.cancel.callback(interaction)
    else:
        await view.on_timeout()

    assert await _amendments(path) == []
    [line] = _lines(cog)
    head, *beneath = line.splitlines()
    assert beneath == ["  Nothing was changed. Run `/round amend` again to start over."]
    if how == "cancelled":
        assert head.startswith("↩️ ")
        assert "/round amend" in head
        assert f"cancelled by Manager (<@{USER_ID}>)" in head
    else:
        assert head.startswith("⌛ ")
        assert "/round amend" in head
        assert f"lapsed unconfirmed (started by Manager (<@{USER_ID}>))" in head


async def test_a_round_amend_logs_the_values_it_set(tmp_path):
    """A confirmed amendment, run on the change queue with weather and attendance off, writes one
    line, saved with the amendment: it names the member, `/round amend` and the round, and states
    beneath it each field changed, from its old value to its new one, named as the command's
    parameter (`track`) rather than by its column. The amendment's own "/round amend (field)"
    line is gone."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    cog = await _cog(path, attendance=False)
    interaction = _interaction()
    _recording(cog, interaction)

    await _view(cog, [("track_name", NEW_TRACK)]).confirm.callback(interaction)
    await run_queue(cog.bot)

    [line] = _lines(cog)
    assert "(field)" not in line
    assert "/round amend" in line
    assert f"<@{USER_ID}>" in line
    assert "round 1" in line.lower()
    values = line.split("\n", 1)[1] if "\n" in line else ""
    assert "Bahrain International Circuit" in values, "the old value is not stated"
    assert NEW_TRACK in values, "the new value is not stated"
    assert f"  track: Bahrain International Circuit \u2192 {NEW_TRACK}" in values.splitlines()
    assert "track_name" not in values


async def test_a_confirmed_round_amend_whose_reply_fails_still_records_what_changed(tmp_path):
    """The manager confirms moving round 1 of Div A from Bahrain International Circuit to
    Silverstone Circuit, but the acknowledgement cannot be sent. The amendment is already asked
    of the change queue, so the failed acknowledgement is the queue's, logged on the host: the
    queue runs it, the round is amended, and the log holds the amendment's one success line, with
    the track from what to what, and no failure line."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    cog = await _cog(path, attendance=False)
    interaction = _interaction()
    interaction.followup.send = AsyncMock(side_effect=RuntimeError("gateway closed"))
    _recording(cog, interaction)

    await _view(cog, [("track_name", NEW_TRACK)]).confirm.callback(interaction)
    await run_queue(cog.bot)

    async with get_connection(path) as db:
        cursor = await db.execute("SELECT track_name FROM rounds WHERE id = 1")
        assert (await cursor.fetchone())["track_name"] == NEW_TRACK
    [success] = _lines(cog)
    assert success.startswith(f"Manager (<@{USER_ID}>) | /round amend | Success\n")
    assert f"  track: Bahrain International Circuit \u2192 {NEW_TRACK}" in success.splitlines()


@pytest.mark.parametrize(
    "where, kind, error, message",
    [
        pytest.param(
            "the ask fails", "the bot could not read or write its database",
            "OperationalError", "database is locked", id="the ask fails",
        ),
        pytest.param(
            "before amending", "the bot could not read or write its database",
            "OperationalError", "database is locked", id="before amending",
        ),
        pytest.param(
            "the first answer fails", "the bot hit an internal fault",
            "RuntimeError", "gateway closed", id="the first answer fails",
        ),
    ],
)
async def test_a_round_amend_confirmation_that_fails_elsewhere_stops_its_view(
    tmp_path, where, kind, error, message,
):
    """A fault in the Confirm press — asking the change queue for the amendment of round 1 of
    Div A, reading the round before it, or the press not even acknowledged, its defer failing —
    still stops the buttons, and is reported as any other fault in the press. Left running, the
    view would lapse two minutes later and record that nothing was changed, beside the failure
    it already recorded."""
    import sqlite3

    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    cog = await _cog(path, attendance=False)
    later = datetime.now(timezone.utc) + timedelta(days=31)
    fault = sqlite3.OperationalError("database is locked")
    interaction = _interaction()
    if where == "the ask fails":
        cog.bot.change_queue.ask = AsyncMock(side_effect=fault)
    elif where == "before amending":
        cog.bot.season_service.get_round = AsyncMock(side_effect=fault)
    else:
        interaction.response.defer = AsyncMock(side_effect=RuntimeError("gateway closed"))
    _recording(cog, interaction)
    view = _view(cog, [("scheduled_at", later)])

    # As discord.py runs a press: a callback that raises goes to the view's `on_error`.
    try:
        await view.confirm.callback(interaction)
    except Exception as exc:  # noqa: BLE001 — handed on as the library hands it on
        await view.on_error(interaction, exc, view.confirm)

    assert view.is_finished(), "the buttons are still live, so the view will lapse as well"
    assert await _amendments(path) == []
    [line] = _lines(cog)
    assert line.startswith("❌ ")
    assert f"failed for Manager (<@{USER_ID}>)" in line
    assert f"— {kind}." in line
    assert error not in line
    assert message not in line


# ---------------------------------------------------------------------------
# An amendment to the values that stand changes nothing, and says so (#482)
# ---------------------------------------------------------------------------
#
# The core specification's "The record of what changed": a command that changes nothing because
# nothing was asked of it records that nothing was changed. The reply says nothing changed, no
# audit entry is written and the log holds one line, in the success form, saying so, and no
# confirmation is offered. A round of the season being set up is tested in
# `test_amendment_cog_pending.py`. Whether the values stand is judged purely
# (`amendment_rules_service`), and tested there.



def _says_nothing_changed(text: str) -> bool:
    return "nothing" in text.lower() and "chang" in text.lower()


async def _audit_rows(path: str) -> list:
    async with get_connection(path) as db:
        cursor = await db.execute("SELECT change_type FROM audit_entries")
        return [tuple(row) for row in await cursor.fetchall()]


def _as_typed(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(("track",), id="its_own_track"),
        pytest.param(("format",), id="its_own_format"),
        pytest.param(("scheduled_at",), id="its_own_moment"),
        pytest.param(("track", "format", "scheduled_at"), id="every_value_as_it_stands"),
    ],
)
async def test_an_active_round_amended_to_what_stands_changes_nothing(tmp_path, fields):
    """Round 1 of Div A, in a season being raced, stands at Bahrain International Circuit, in
    the NORMAL format, thirty days from now; the manager amends it to values it already holds."""
    at = (datetime.now(timezone.utc) + timedelta(days=30)).replace(second=0, microsecond=0)
    path = await _db(tmp_path, scheduled_at=at)
    cog = await _cog(path)
    interaction = _interaction()
    _recording(cog, interaction)
    standing = {
        "track": "Bahrain International Circuit",
        "format": "NORMAL",
        "scheduled_at": _as_typed(at),
    }

    await _amend(cog, interaction, **{field: standing[field] for field in fields})

    assert not _offered_a_confirmation(interaction)
    # Nothing is asked of the change queue, which makes every amendment.
    assert await _amendments(path) == []
    assert _says_nothing_changed(_reply(interaction))
    assert await _audit_rows(path) == []
    [line] = _lines(cog)
    assert line.startswith(f"Manager (<@{USER_ID}>) | /round amend")
    assert not line.startswith("⛔")
    assert _says_nothing_changed(line)


@pytest.mark.parametrize(
    "status, refusal",
    [
        pytest.param(
            "CANCELLED",
            "This round has been cancelled and can no longer be amended.",
            id="cancelled",
        ),
        pytest.param(
            "AWAITING_REPORT_VERDICTS",
            "This round's results have been entered",
            id="results_entered",
        ),
    ],
)
async def test_a_round_that_cannot_be_amended_is_refused_even_given_what_stands(
    tmp_path, status, refusal
):
    """Round 1 of Div A stands at Bahrain International Circuit, thirty days from now, but can
    no longer be amended. The manager gives it the track it already has. The owner decided
    (2026-09-30) that the refusal comes first: the manager is told the round cannot be amended,
    as before, rather than that nothing changed."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    async with get_connection(path) as db:
        await db.execute("UPDATE rounds SET status = ? WHERE id = 1", (status,))
        await db.commit()
    cog = await _cog(path)
    interaction = _interaction()
    _recording(cog, interaction)

    await _amend(cog, interaction, track="Bahrain International Circuit")

    assert refusal in _reply(interaction)
    assert "already holds those values" not in _reply(interaction)
    [line] = _lines(cog)
    assert line.startswith("⛔ "), line


# ---------------------------------------------------------------------------
# A cancellation in hand holds the amendment (owner, 2026-10-08, answer 1; #439 slice 4b)
#
# A cancellation waiting or stopped on the queue has removed, or is about to remove, the round's
# timed work. An amendment confirmed meanwhile would arm it again, and the round, once cancelled,
# would still have its forecasts posted. So `/round amend` is refused, naming the job, while a
# cancellation of the round, of its division or of another round of its division is in hand: at
# the offer and again at confirm.
# ---------------------------------------------------------------------------

_CANCELLATIONS_IN_HAND = [
    pytest.param(
        "season.round.cancel",
        {"round_id": 1, "round_number": 1, "track_name": "Bahrain International Circuit",
         "division_id": 1, "division_name": DIVISION, "season_number": 1},
        id="round-cancel",
    ),
    pytest.param(
        "season.division.cancel",
        {"division_id": 1, "division_name": DIVISION, "season_number": 1},
        id="division-cancel",
    ),
]


async def _seed_cancellation(
    path: str, kind: str, payload: dict, *, state: str = "QUEUED"
) -> int:
    """A cancellation of *kind* waiting on the queue, its first job (removing the timed work) not
    yet done, behind an earlier change of three jobs long done, so that the job's number and its
    change's id differ. Gives the job's number. With *state* 'DONE', the cancellation is over
    instead, its job done."""
    import json

    async with get_connection(path) as db:
        cursor = await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
            "VALUES ('hub.refresh', 'hub.refresh', '{}', 'BOT', 'DONE', 'refreshing the hub')"
        )
        for position in range(3):
            await db.execute(
                "INSERT INTO queued_change_steps (change_id, position, name, done_at) "
                "VALUES (?, ?, 'refresh', '2026-10-05T11:00:00+00:00')",
                (cursor.lastrowid, position),
            )
        cursor = await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
            "VALUES (?, ?, ?, 'MEMBER', ?, 'a cancellation of the test')",
            (kind, f"{kind}:{json.dumps(payload, sort_keys=True)}", json.dumps(payload), state),
        )
        change_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO queued_change_steps (change_id, position, name, payload, done_at) "
            "VALUES (?, 0, 'unarm', '{}', ?)",
            (change_id, "2026-10-05T12:00:00+00:00" if state == "DONE" else None),
        )
        job = cursor.lastrowid
        await db.commit()
    assert job is not None and job != change_id
    return job


@pytest.mark.parametrize("at", ["offer", "confirm"])
@pytest.mark.parametrize(("kind", "payload"), _CANCELLATIONS_IN_HAND)
async def test_a_round_being_cancelled_is_not_amended_naming_the_job(tmp_path, kind, payload, at):
    """Round 1 of Div A is a month out, and a cancellation of it, or of Div A, waits on the queue.
    Asking to amend its track is refused before any confirmation is offered; pressing Confirm on
    an amendment offered earlier is refused too. Either way the reply names the job and how to
    clear it, nothing is amended, and one refusal line is written in the log channel."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    job = await _seed_cancellation(path, kind, payload)
    cog = await _cog(path)
    interaction = _interaction()
    _recording(cog, interaction)

    if at == "confirm":
        _answered(interaction)
        await _view(cog, [("track_name", NEW_TRACK)]).confirm.callback(interaction)
    else:
        await _amend(cog, interaction, track=NEW_TRACK)
        assert not _offered_a_confirmation(interaction)

    assert (
        f"⏸️ Round 1 in **{DIVISION}** is being cancelled (job #{job}), so it cannot be "
        "amended. Let that finish, or press **Retry** or **Discard** on its notice if it has "
        "stopped."
    ) in _all_replies(interaction)
    assert await _amendments(path) == []
    [line] = _lines(cog)
    assert line.startswith("⛔ ")
    assert "/round amend" in line and f"job #{job}" in line


@pytest.mark.parametrize("at", ["offer", "confirm"])
async def test_a_round_is_not_amended_while_another_round_of_its_division_is_being_cancelled(
    tmp_path, at
):
    """Round 1 of Div A is a month out, and a cancellation of round 2 of Div A waits on the queue.
    An amended date could renumber the division while it waits, and the cancelled round would be
    announced by a number it no longer bears (owner, 2026-10-08: hold amends in the division).
    Asking to amend round 1's track is refused before any confirmation is offered; pressing
    Confirm on an amendment offered earlier is refused too. The reply names the job, nothing is
    amended, and one refusal line is written in the log channel."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    job = await _seed_cancellation(path, "season.round.cancel", {
        "round_id": 2, "round_number": 2, "track_name": "Bahrain International Circuit",
        "division_id": 1, "division_name": DIVISION, "season_number": 1,
    })
    cog = await _cog(path)
    interaction = _interaction()
    _recording(cog, interaction)

    if at == "confirm":
        _answered(interaction)
        await _view(cog, [("track_name", NEW_TRACK)]).confirm.callback(interaction)
    else:
        await _amend(cog, interaction, track=NEW_TRACK)
        assert not _offered_a_confirmation(interaction)

    assert (
        f"⏸️ A round of **{DIVISION}** is being cancelled (job #{job}), so its rounds cannot be "
        "amended until that is done. Let that finish, or press **Retry** or **Discard** on its "
        "notice if it has stopped."
    ) in _all_replies(interaction)
    assert await _amendments(path) == []
    [line] = _lines(cog)
    assert line.startswith("⛔ ")
    assert "/round amend" in line and f"job #{job}" in line


@pytest.mark.parametrize(
    "at",
    ["offer", "confirm"],
)
@pytest.mark.parametrize(
    ("division_id", "state"),
    [
        pytest.param(2, "QUEUED", id="another-division-waiting"),
        pytest.param(1, "DONE", id="same-division-done"),
    ],
)
async def test_a_cancellation_elsewhere_or_done_lets_the_amendment_through(
    tmp_path, at, division_id, state
):
    """Round 1 of Div A is a month out. A cancellation of a round of another division waits on
    the queue, or one of round 2 of Div A has already been carried out. Neither holds the
    amendment: the hold stays inside the division, and only while the cancellation is in hand.
    Asking to amend round 1's track is offered for confirmation; pressing Confirm asks the
    change queue for the amendment."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    await _seed_cancellation(path, "season.round.cancel", {
        "round_id": 2, "round_number": 2, "track_name": "Bahrain International Circuit",
        "division_id": division_id, "division_name": DIVISION if division_id == 1 else "Div B",
        "season_number": 1,
    }, state=state)
    cog = await _cog(path)
    interaction = _interaction()
    _recording(cog, interaction)

    if at == "confirm":
        _answered(interaction)
        await _view(cog, [("track_name", NEW_TRACK)]).confirm.callback(interaction)
        assert len(await _amendments(path)) == 1
    else:
        await _amend(cog, interaction, track=NEW_TRACK)
        assert _offered_a_confirmation(interaction)

    assert "⏸️" not in _all_replies(interaction)
    assert not any(line.startswith("⛔ ") for line in _lines(cog))


# ---------------------------------------------------------------------------
# A season's end in hand holds the amendment (#439 slice 5, answer B)
#
# While the season's completion, cancellation or abort is waiting, being carried out or stopped,
# every request about that season is refused at once, naming the job: `/round amend` among them,
# at the offer and again at confirm.
# ---------------------------------------------------------------------------

_SEASON_ENDS_IN_HAND = [
    pytest.param(
        "season.complete", {"season_id": 1, "season_number": 1},
        "⏳ Season 1 is being completed (job #{job}), so this cannot be done until that is "
        "finished. If it has stopped, press Retry or Discard on its notice in the log channel.",
        id="complete",
    ),
    pytest.param(
        "season.cancel", {"season_id": 1, "season_number": 1},
        "⏳ Season 1 is being cancelled (job #{job}), so this cannot be done until that is "
        "finished. If it has stopped, press Retry or Discard on its notice in the log channel.",
        id="cancel",
    ),
    pytest.param(
        "season.abort", {"season_id": 1},
        "⏳ The season being set up is being aborted (job #{job}), so this cannot be done until "
        "that is finished. If it has stopped, press Retry or Discard on its notice in the log "
        "channel.",
        id="abort",
    ),
]


@pytest.mark.parametrize("at", ["offer", "confirm"])
@pytest.mark.parametrize(("kind", "payload", "said"), _SEASON_ENDS_IN_HAND)
async def test_a_round_of_a_season_whose_end_is_in_hand_is_not_amended_naming_the_job(
    tmp_path, kind, payload, said, at
):
    """Round 1 of Div A is a month out, and season 1's completion, its cancellation, or the
    season's abort waits on the queue. Asking to amend round 1's track is refused before any
    confirmation is offered; pressing Confirm on an amendment offered earlier is refused too.
    Either way the reply names the season's end and its job, nothing is amended or armed, and
    one refusal line naming the job is written in the log channel."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    job = await _seed_cancellation(path, kind, payload)
    cog = await _cog(path)
    interaction = _interaction()
    _recording(cog, interaction)

    if at == "confirm":
        _answered(interaction)
        await _view(cog, [("track_name", NEW_TRACK)]).confirm.callback(interaction)
    else:
        await _amend(cog, interaction, track=NEW_TRACK)
        assert not _offered_a_confirmation(interaction)

    assert said.format(job=job) in _all_replies(interaction)
    assert await _amendments(path) == []
    [line] = _lines(cog)
    assert line.startswith("⛔ ")
    assert "/round amend" in line and f"job #{job}" in line
