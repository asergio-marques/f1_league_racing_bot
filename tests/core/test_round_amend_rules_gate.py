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

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.season_service import SeasonService

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
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
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
    # ``MagicMock`` with each awaited member named (issue #240). A whole-bot ``AsyncMock``
    # answers an unpinned reader with a truthy mock, which for a file about *rules* would
    # mean a rule passing on the stub rather than the round in front of it.
    cog.bot = MagicMock()
    cog.bot.db_path = db_path
    cog.bot.output_router.post_log = AsyncMock()
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
    cog._get_pending = MagicMock(return_value=None)
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
    from leaguebot.core.cogs.season_cog import _ConfirmView

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
    return [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]


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
    """A fault while the amendment is applied is the bot's: the standard failure reply naming
    `/round amend` and the round, one line in the log channel, and the buttons stopped."""
    import sqlite3

    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    cog = _cog(path)
    cog.bot.amendment_service.amend_round = AsyncMock(
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
    assert "OperationalError" in line
    assert view.is_finished()


def _pending(track: str | None = "Bahrain International Circuit"):
    from leaguebot.core.models.round import RoundFormat

    return SimpleNamespace(
        divisions=[
            SimpleNamespace(
                name=DIVISION,
                rounds=[
                    {
                        "round_number": 1,
                        "format": RoundFormat.NORMAL,
                        "track_name": track,
                        "scheduled_at": datetime(2026, 12, 1, 18, 0),
                    }
                ],
            )
        ]
    )


#: Every refusal `/round amend` makes, on either path, and each of its Confirm button's: the
#: case, the arguments, and a fragment of the reply.
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
    pytest.param("pending", {"division": "Nowhere", "track": NEW_TRACK}, "pending setup",
                 id="pending-unknown-division"),
    pytest.param("pending", {"round_number": 9, "track": NEW_TRACK}, "pending setup",
                 id="pending-unknown-round"),
    pytest.param("pending", {"track": "Nowhere Ring"}, "Unknown track",
                 id="pending-unknown-track"),
    pytest.param("pending", {"scheduled_at": "soon"}, "Invalid datetime",
                 id="pending-bad-datetime"),
    pytest.param("pending", {"format": "DRAG"}, "Invalid format", id="pending-bad-format"),
    pytest.param("pending no track", {"format": "NORMAL"}, "requires a track",
                 id="pending-format-needs-a-track"),
    pytest.param("confirm by another", {}, "Not your action", id="confirm-not-your-action"),
    pytest.param("confirm round gone", {}, "no longer exists", id="confirm-round-gone"),
    pytest.param("confirm window passed", {}, "can no longer be amended",
                 id="confirm-no-longer-allowed"),
]


@pytest.mark.parametrize("case, kwargs, said", ROUND_AMEND_REFUSALS)
async def test_every_round_amend_refusal_reaches_the_log_channel(tmp_path, case, kwargs, said):
    """Each refusal answers the manager as before, changes nothing, and writes one line in the
    standard refusal form."""
    started = case == "started"
    soon = case == "confirm window passed"
    when = datetime.now(timezone.utc) + (
        timedelta(hours=-1) if started else timedelta(hours=1) if soon else timedelta(days=30)
    )
    path = await _db(tmp_path, scheduled_at=when)
    cog = _cog(path)
    cog.bot.amendment_service.amend_round = AsyncMock()
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
        if case.startswith("pending"):
            cog._get_pending = MagicMock(
                return_value=_pending(track=None if case == "pending no track" else "Bahrain")
            )
            cog._snapshot_pending = AsyncMock()
        kwargs = dict(kwargs)
        division = kwargs.pop("division", DIVISION)
        round_number = kwargs.pop("round_number", 1)
        await SeasonCog.round_amend.callback.__wrapped__(
            cog, interaction, division, round_number, **kwargs
        )

    assert said in _all_replies(interaction)
    cog.bot.amendment_service.amend_round.assert_not_awaited()
    if case.startswith("pending"):
        # The season being set up is written through its snapshot, not the amendment service.
        cog._snapshot_pending.assert_not_awaited()
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
    cog = _cog(path)
    cog.bot.amendment_service.amend_round = AsyncMock()
    interaction = _answered(_interaction())
    _recording(cog, interaction)
    view = _view(cog, [("track_name", NEW_TRACK)])

    if how == "cancelled":
        await view.cancel.callback(interaction)
    else:
        await view.on_timeout()

    cog.bot.amendment_service.amend_round.assert_not_awaited()
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


def _amending_in_earnest(cog, path):
    """Let the confirmation amend the round through the real amendment service, with weather
    off, so that every line a confirmed amendment writes is the one a league would read."""
    from leaguebot.core.services.amendment_service import AmendmentService

    cog.bot.amendment_service = AmendmentService(path)
    cog.bot.module_service.is_weather_enabled = AsyncMock(return_value=False)


async def test_a_round_amend_logs_the_values_it_set(tmp_path):
    """A confirmed amendment writes one line: it names the member, `/round amend` and the round,
    and states beneath it each field changed, from its old value to its new one, named as the
    command's parameter (`track`) rather than by its column. The amendment's own
    "/round amend (field)" line is gone."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    cog = _cog(path, attendance=False)
    _amending_in_earnest(cog, path)
    interaction = _interaction()
    _recording(cog, interaction)

    await _view(cog, [("track_name", NEW_TRACK)]).confirm.callback(interaction)

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
    Silverstone Circuit, and the amendment is saved, but the reply saying so cannot be sent. The
    log holds the amendment's one success line, with the track from what to what, beside the
    failure line for the press."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    cog = _cog(path, attendance=False)
    _amending_in_earnest(cog, path)
    interaction = _interaction()
    interaction.followup.send = AsyncMock(side_effect=RuntimeError("gateway closed"))
    _recording(cog, interaction)

    await _view(cog, [("track_name", NEW_TRACK)]).confirm.callback(interaction)

    async with get_connection(path) as db:
        cursor = await db.execute("SELECT track_name FROM rounds WHERE id = 1")
        assert (await cursor.fetchone())["track_name"] == NEW_TRACK
    success, failure = _lines(cog)
    assert success.startswith(f"Manager (<@{USER_ID}>) | /round amend | Success\n")
    assert f"  track: Bahrain International Circuit \u2192 {NEW_TRACK}" in success.splitlines()
    assert failure.startswith("\u274c ")
    assert f"failed for Manager (<@{USER_ID}>)" in failure


async def test_a_pending_round_amend_logs_the_values_it_set(tmp_path):
    """On a season still being set up, the success line likewise names the member and
    `/round amend`, and states beneath it each field changed, from its old value to its new
    one."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    cog = _cog(path)
    cog._get_pending = MagicMock(return_value=_pending())
    cog._snapshot_pending = AsyncMock()
    interaction = _interaction()
    _recording(cog, interaction)

    await _amend(cog, interaction, track=NEW_TRACK)

    cog._snapshot_pending.assert_awaited_once()
    [line] = _lines(cog)
    assert "/round amend" in line
    assert f"<@{USER_ID}>" in line
    values = line.split("\n", 1)[1] if "\n" in line else ""
    assert "Bahrain International Circuit" in values, "the old value is not stated"
    assert NEW_TRACK in values, "the new value is not stated"


@pytest.mark.parametrize("where", ["after amending", "before amending"])
async def test_a_round_amend_confirmation_that_fails_elsewhere_stops_its_view(tmp_path, where):
    """A fault in the Confirm press anywhere but the amendment itself — putting the rounds back
    in order after the round was moved, or reading the round before it — still stops the
    buttons. Left running, the view would lapse two minutes later and record that nothing was
    changed, beside the failure it already recorded, even where the round was changed."""
    import sqlite3

    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    cog = _cog(path, attendance=False)
    cog.bot.amendment_service.amend_round = AsyncMock()
    later = (datetime.now(timezone.utc) + timedelta(days=31)).replace(tzinfo=None)
    fault = sqlite3.OperationalError("database is locked")
    if where == "after amending":
        cog.bot.season_service.renumber_rounds = AsyncMock(side_effect=fault)
    else:
        cog.bot.season_service.get_round = AsyncMock(side_effect=fault)
    interaction = _interaction()
    _recording(cog, interaction)
    view = _view(cog, [("scheduled_at", later)])

    # As discord.py runs a press: a callback that raises goes to the view's `on_error`.
    try:
        await view.confirm.callback(interaction)
    except Exception as exc:  # noqa: BLE001 — handed on as the library hands it on
        await view.on_error(interaction, exc, view.confirm)

    assert view.is_finished(), "the buttons are still live, so the view will lapse as well"
    [line] = _lines(cog)
    assert line.startswith("❌ ")
    assert f"failed for Manager (<@{USER_ID}>)" in line
    assert "OperationalError" in line
    assert "database is locked" not in line


async def test_a_round_amend_confirmation_whose_first_answer_fails_stops_its_view(tmp_path):
    """The Confirm press cannot even be acknowledged — the defer fails. It is reported as any
    other fault in the press, and the buttons stop, so no lapse line follows the failure."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    cog = _cog(path, attendance=False)
    cog.bot.amendment_service.amend_round = AsyncMock()
    later = (datetime.now(timezone.utc) + timedelta(days=31)).replace(tzinfo=None)
    interaction = _interaction()
    interaction.response.defer = AsyncMock(side_effect=RuntimeError("gateway closed"))
    _recording(cog, interaction)
    view = _view(cog, [("scheduled_at", later)])

    try:
        await view.confirm.callback(interaction)
    except Exception as exc:  # noqa: BLE001 — handed on as the library hands it on
        await view.on_error(interaction, exc, view.confirm)

    assert view.is_finished(), "the buttons are still live, so the view will lapse as well"
    cog.bot.amendment_service.amend_round.assert_not_awaited()
    [line] = _lines(cog)
    assert line.startswith("❌ ")
    assert f"failed for Manager (<@{USER_ID}>)" in line
    assert "RuntimeError" in line
    assert "gateway closed" not in line


# ---------------------------------------------------------------------------
# An amendment to the values that stand changes nothing, and says so (#482)
# ---------------------------------------------------------------------------
#
# The core specification's "The record of what changed": a command that changes nothing because
# nothing was asked of it records that nothing was changed. On either path the reply says nothing
# changed, no audit entry is written and the log holds one line, in the success form, saying so;
# the active path offers no confirmation for it. Whether the values stand is judged purely
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
    cog = _cog(path)
    cog.bot.amendment_service.amend_round = AsyncMock()
    interaction = _interaction()
    _recording(cog, interaction)
    standing = {
        "track": "Bahrain International Circuit",
        "format": "NORMAL",
        "scheduled_at": _as_typed(at),
    }

    await _amend(cog, interaction, **{field: standing[field] for field in fields})

    assert not _offered_a_confirmation(interaction)
    cog.bot.amendment_service.amend_round.assert_not_awaited()
    assert _says_nothing_changed(_reply(interaction))
    assert await _audit_rows(path) == []
    [line] = _lines(cog)
    assert line.startswith(f"Manager (<@{USER_ID}>) | /round amend")
    assert not line.startswith("⛔")
    assert _says_nothing_changed(line)


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(("track",), id="its_own_track"),
        pytest.param(("format",), id="its_own_format"),
        pytest.param(("scheduled_at",), id="its_own_moment"),
        pytest.param(("track", "format", "scheduled_at"), id="every_value_as_it_stands"),
    ],
)
async def test_a_pending_round_amended_to_what_stands_changes_nothing(tmp_path, fields):
    """Round 1 of Div A, in the season being set up, stands at Bahrain International Circuit, in
    the NORMAL format, on 1 December 2026 at 18:00 UTC; the manager amends it to values it
    already holds. Nothing is saved to the season being set up."""
    path = await _db(tmp_path, scheduled_at=datetime.now(timezone.utc) + timedelta(days=30))
    cog = _cog(path)
    pending = _pending()
    cog._get_pending = MagicMock(return_value=pending)
    cog._snapshot_pending = AsyncMock()
    interaction = _interaction()
    _recording(cog, interaction)
    standing = {
        "track": "Bahrain International Circuit",
        "format": "NORMAL",
        "scheduled_at": "2026-12-01T18:00:00",
    }

    await _amend(cog, interaction, **{field: standing[field] for field in fields})

    cog._snapshot_pending.assert_not_awaited()
    assert _says_nothing_changed(_reply(interaction))
    assert "updated and saved" not in _reply(interaction)
    assert await _audit_rows(path) == []
    [line] = _lines(cog)
    assert line.startswith(f"Manager (<@{USER_ID}>) | /round amend")
    assert not line.startswith("⛔")
    assert _says_nothing_changed(line)
