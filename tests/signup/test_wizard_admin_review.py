"""Approving, rejecting and sending back a signup, through the change queue.

Issue #208, then #439 slice 6b (C78, C84, C85). These three are what a league manager does with
a completed signup. Each is asked of the queue as the manager's change on the league lane:
Approve by its press (`signup.approve`), Reject and Request Changes by the reason the manager
types after pressing (`signup.end` with ``reason="rejected"``, `signup.request_changes`). The
driver's status, record and role change on the league lane; what is posted back in the driver's
channel comes back on the driver's own lane (`signup.closing`, `signup.choices`). The tests here
press or type as a manager does and read what the queue saved and posted once it has run.

**Every failure of the role is survivable, deliberately.** A missing role, a role the bot cannot
grant, a driver who has left the server: none of them may stop an approval part-way (signup
:122). `tests/signup/test_review_changes.py` pins the queue's shape of each change; this file
pins the rules each outcome holds.

**All three hold the channel for 24 hours rather than deleting it.** The driver is told the
outcome in the channel, so deleting it at once would take the message with it. Approval's hold,
with the role granted, Unassigned and the approved mark, is pinned by
`tests/signup/test_review_changes.py::test_approve_saves_grants_the_role_and_tells_the_driver_as_jobs`.

Request Changes writes down **who** asked and **why**, so that the lapse can mention the right
manager when the five minutes run out.
"""
from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

from tests.support.change_queue import (
    MANAGER_ROLE_ID,
    acknowledgement,
    change_rows,
    discard_job,
    http_error,
    member_interaction,
    run_queue,
    stopped_job,
    typed_message,
    updated_reply,
)
from tests.support.review_league import channel
from tests.support.season_league import (
    DRIVER_ROLE,
    SEASON_ID,
    SIGNING_UP,
    SIGNUP_CHANNEL_BASE,
    SeasonLeague,
    review_services,
    setup_league,
    signing_up,
)


KIMI_CHANNEL = SIGNUP_CHANNEL_BASE + SIGNING_UP
REVIEWER = 77
LAP_TIMES = {"1": "1:30.000", "2": "1:31.500"}
INACTIVITY_TIMER = "signup_inactivity"
CLOSING_KIND = "signup.closing"
LABELS = {"reject": "Reject", "request_changes": "Request Changes"}


async def _league(tmp_path: Any, *, state: str = "PENDING_ADMIN_APPROVAL",
                  lap_times: dict[str, str] | None = None) -> SeasonLeague:
    """`setup_league` at Signups, the window open, the driver role (820) set; Kimi at *state*,
    his wizard unengaged in his signup channel, his signup record saved with *lap_times* (Kimi's
    two times unless given); manager 77 holding the interaction role."""
    os.makedirs(str(tmp_path), exist_ok=True)
    league = await setup_league(tmp_path, stage="SIGNUPS", signups_open=True)
    await league.write("UPDATE server_configs SET driver_role_id = ?", DRIVER_ROLE)
    league.roles_held[REVIEWER] = {MANAGER_ROLE_ID}
    await signing_up(league, SIGNING_UP, state=state)
    await league.write(
        "UPDATE signup_wizard_records SET wizard_state = 'UNENGAGED', draft_answers_json = '{}' "
        "WHERE discord_user_id = ?", str(SIGNING_UP),
    )
    await league.write(
        "INSERT INTO signup_records (created_at, season_id, discord_user_id, server_display_name, "
        "nationality, platform, platform_id, driver_type, lap_times_json) "
        "VALUES ('2026-01-01T00:00:00+00:00', ?, ?, ?, 'British', 'Steam', 'kimi7', 'Full-Time Driver', ?)",
        SEASON_ID, str(SIGNING_UP), str(SIGNING_UP),
        json.dumps(LAP_TIMES if lap_times is None else lap_times),
    )
    made = league._recording(channel(KIMI_CHANNEL, league.events))
    made.guild = league.guild
    made.posted = []
    made.set_permissions = AsyncMock()
    league.channels[KIMI_CHANNEL] = made
    return league


def _manager(league: SeasonLeague) -> Any:
    manager = league._member(REVIEWER)
    manager.bot = False
    return manager


def _press(league: SeasonLeague) -> Any:
    """Manager 77's press in Kimi's signup channel, now."""
    press = member_interaction(league.bot, user=_manager(league), created_at=league.clock.now)
    press.guild = league.guild
    press.channel_id = KIMI_CHANNEL
    press.channel = league.channel(KIMI_CHANNEL)
    return press


async def _approve(league: SeasonLeague, *, run: bool = True) -> Any:
    """Manager 77 presses Approve on Kimi's review panel, handed the change queue (A23) and the
    services it reads (#286) when built, and the queue runs unless *run* is false."""
    from leaguebot.signup.services.signup_views import AdminReviewView

    press = _press(league)
    view = AdminReviewView(str(SIGNING_UP), change_queue=league.bot.change_queue,
                           **review_services(league.bot))
    await view.approve_button.callback(press)
    if run:
        await run_queue(league.bot)
    return press


async def _type_reason(league: SeasonLeague, action: str, reason: str,
                       *, moved_to: str | None = None,
                       moved_after_ask: str | None = None) -> Any:
    """Manager 77 has pressed *action* ("reject" or "request_changes") on Kimi's review panel,
    the press deferred and parked as the button parks it; where *moved_to* is given Kimi's
    signup moves on to that state meanwhile, and where *moved_after_ask* is given it moves on to
    that state only once the queue has accepted the press, before the queue runs. 77 types *reason* in Kimi's channel, the review cog
    hears it, and the queue runs. Gives the stored press."""
    from leaguebot.signup.cogs.admin_review_cog import AdminReviewCog
    from leaguebot.signup.services.signup_views import _PENDING_REASONS

    manager = _manager(league)
    press = _press(league)
    await press.response.defer(ephemeral=True)
    _PENDING_REASONS[(KIMI_CHANNEL, REVIEWER)] = {
        "action": action,
        "discord_user_id": str(SIGNING_UP),
        "actor": manager,
        "guild": league.guild,
        "interaction": press,
        "followup": press.followup,
    }
    if moved_to is not None:
        await league.write(
            "UPDATE driver_profiles SET current_state = ? WHERE discord_user_id = ?",
            moved_to, str(SIGNING_UP),
        )
    message = typed_message(league.channel(KIMI_CHANNEL), manager, reason,
                            created_at=league.clock.now)
    message.guild = league.guild
    try:
        await AdminReviewCog(league.bot).on_message(message)
    finally:
        _PENDING_REASONS.clear()
    if moved_after_ask is not None:
        await league.write(
            "UPDATE driver_profiles SET current_state = ? WHERE discord_user_id = ?",
            moved_after_ask, str(SIGNING_UP),
        )
    await run_queue(league.bot)
    return press


async def _state(league: SeasonLeague) -> str | None:
    rows = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    return str(rows[0]["current_state"]) if rows else None


async def _record(league: SeasonLeague) -> dict[str, Any]:
    [row] = await league.rows(
        "SELECT * FROM signup_records WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    return row


async def _draft(league: SeasonLeague) -> dict[str, Any]:
    [row] = await league.rows(
        "SELECT draft_answers_json FROM signup_wizard_records WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    return dict(json.loads(row["draft_answers_json"] or "{}"))


def _texts(league: SeasonLeague) -> list[str]:
    return list(league.channel(KIMI_CHANNEL).texts)


def _answered(press: Any) -> str:
    """All the manager was told: the acknowledgement, then the reply as updated, then any
    follow-up."""
    told = [acknowledgement(press), updated_reply(press)]
    told += [str(call.args[0]) for call in press.followup.send.await_args_list if call.args]
    return "\n".join(part for part in told if part)


def _lines(league: SeasonLeague) -> list[str]:
    """The log channel's lines as a reader sees them: the router wraps each mention in backticks
    so that it names without notifying, and the backticks are not part of what a line says."""
    return league.log().replace("`", "").splitlines()


async def _closings(league: SeasonLeague) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == CLOSING_KIND]


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


async def test_a_driver_with_no_lap_times_stores_no_seeding_value(tmp_path):
    """A league that asked for no times has nothing to seed from, and storing a zero would seed
    every driver identically: Kimi's record, holding no lap times, is approved with no total."""
    league = await _league(tmp_path, lap_times={})

    await _approve(league)

    assert await _state(league) == "UNASSIGNED"
    assert (await _record(league))["total_lap_ms"] is None


async def test_approval_without_a_signup_configuration_does_nothing(tmp_path):
    """The signup module's configuration is gone when manager 77 presses Approve: Kimi stays
    awaiting approval, holds no role, and nothing is posted in his channel; the manager is told
    it was refused."""
    league = await _league(tmp_path)
    await league.write("DELETE FROM signup_module_config")

    press = await _approve(league)

    assert await _state(league) == "PENDING_ADMIN_APPROVAL"
    assert league.granted.get(SIGNING_UP) is None
    assert _texts(league) == []
    told = _answered(press)
    assert "⛔" in told and "✅" not in told, told


@pytest.mark.parametrize("why", ["role missing", "member left", "Discord refuses"])
async def test_a_driver_role_not_granted_is_reported_and_the_approval_stands(tmp_path, why):
    """The driver role cannot be given: the role has been deleted, Kimi has left the server, or
    Discord refuses it. The approval stands, as the core specification's role rules have it:
    Kimi is Unassigned, his signup record is marked approved, the league lane is not stopped,
    and both the manager's updated reply and the Approved line in the log channel say the
    driver role could not be granted. A driver who has left the server may come back, and the
    record is the league's either way, so he is approved all the same."""
    from tests.support.change_queue import stopped_job

    league = await _league(tmp_path)
    if why == "role missing":
        league.roles_gone.add(DRIVER_ROLE)
    elif why == "member left":
        league.absent.add(SIGNING_UP)
    else:
        league.grant_fails[SIGNING_UP] = http_error(discord.Forbidden, status=403)

    press = await _approve(league)

    assert await _state(league) == "UNASSIGNED"
    assert (await _record(league))["approved"] == 1
    assert await stopped_job(league.db_path, "league") is None
    assert "role" in updated_reply(press).lower()
    assert any("| Signup | Approved" in line for line in _lines(league)), league.log()
    assert "driver role could not be granted" in league.log().lower()


async def test_a_driver_role_granted_adds_nothing_to_the_approval(tmp_path):
    """Where the role is given there is nothing to report: neither the manager's reply nor the
    log says a role could not be granted, and the Approved line names the manager and Kimi."""
    league = await _league(tmp_path)

    press = await _approve(league)

    assert "could not be granted" not in updated_reply(press).lower()
    assert "could not be granted" not in league.log().lower()
    [approved] = [line for line in _lines(league) if "| Signup | Approved" in line]
    assert f"<@{REVIEWER}>" in approved
    assert any(line.startswith("  driver:") and f"<@{SIGNING_UP}>" in line
               for line in _lines(league)), league.log()



async def test_a_fake_driver_is_approved_with_no_role_and_then_seated_by_driver_assign(tmp_path):
    """The server is in test mode, at Signups with the window open, the driver role (820) set.
    Mock Alpha, a fake driver with no member of the server behind it, has submitted its signup
    and awaits approval in its own signup channel. Manager 77 presses Approve on its review
    panel and the queue runs: Mock Alpha is Unassigned, its signup marked approved, the Approved
    line written naming Mock Alpha by its test name, and no role granted to anyone, while neither the manager's reply nor the log
    channel says a role could not be granted. The window then closes and the season moves to
    Placements, and McLaren in Am is given a second, free seat; the league admin Admin (77) runs the test-mode `/driver assign` of Mock Alpha, by
    ID, to McLaren in Am, and the queue runs it: Mock Alpha holds a placement at McLaren in Am,
    sits in a McLaren seat, and is Assigned."""
    from leaguebot.signup.services.signup_views import AdminReviewView
    from tests.core.test_driver_test_cog import _run_variant
    from tests.signup.test_wizard_changes import MOCK, MOCK_NAME
    from tests.support.season_league import MCLAREN

    mock_channel = SIGNUP_CHANNEL_BASE + 99
    league = await _league(tmp_path)
    await league.set_test_mode(True)
    await league.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state, is_test_driver, "
        "test_display_name) VALUES (?, 'PENDING_ADMIN_APPROVAL', 1, ?)",
        str(MOCK), MOCK_NAME,
    )
    league.absent.add(MOCK)
    await league.write(
        "INSERT INTO signup_wizard_records (discord_user_id, wizard_state, signup_channel_id, "
        "draft_answers_json, created_at, last_activity_at) VALUES (?, 'UNENGAGED', ?, '{}', ?, ?)",
        str(MOCK), mock_channel, league.clock.now.isoformat(), league.clock.now.isoformat(),
    )
    await league.write(
        "INSERT INTO signup_records (created_at, season_id, discord_user_id, discord_username, "
        "server_display_name, nationality, platform, platform_id, driver_type, lap_times_json) "
        "VALUES ('2026-01-01T00:00:00+00:00', ?, ?, ?, ?, 'British', 'Steam', 'alpha1', "
        "'Full-Time Driver', ?)",
        SEASON_ID, str(MOCK), MOCK_NAME, MOCK_NAME, json.dumps(LAP_TIMES),
    )
    made = league._recording(channel(mock_channel, league.events))
    made.guild = league.guild
    made.posted = []
    made.set_permissions = AsyncMock()
    league.channels[mock_channel] = made

    press = member_interaction(league.bot, user=_manager(league), created_at=league.clock.now)
    press.guild = league.guild
    press.channel_id = mock_channel
    press.channel = made
    view = AdminReviewView(str(MOCK), change_queue=league.bot.change_queue,
                           **review_services(league.bot))
    await view.approve_button.callback(press)
    await run_queue(league.bot)

    [profile] = await league.rows(
        "SELECT id, current_state FROM driver_profiles WHERE discord_user_id = ?", str(MOCK)
    )
    assert profile["current_state"] == "UNASSIGNED"
    [record] = await league.rows(
        "SELECT approved FROM signup_records WHERE discord_user_id = ?", str(MOCK)
    )
    assert record["approved"] == 1
    assert any("| Signup | Approved" in line for line in _lines(league)), league.log()
    assert any(line.startswith("  driver:") and MOCK_NAME in line
               for line in _lines(league)), league.log()
    assert league.granted == {}
    assert "could not be granted" not in updated_reply(press).lower()
    assert "could not be granted" not in league.log().lower()

    await league.write("UPDATE signup_module_config SET signups_open = 0")
    await league.write("UPDATE seasons SET stage = 'PLACEMENTS' WHERE id = ?", SEASON_ID)
    await league.write(
        "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, 2)", MCLAREN
    )
    await _run_variant(league, "assign", user=str(MOCK), division="Am", team="McLaren")
    await run_queue(league.bot)

    placed = await league.rows(
        "SELECT d.name AS division, ti.name AS team FROM driver_season_assignments dsa "
        "JOIN divisions d ON d.id = dsa.division_id "
        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
        "WHERE dsa.driver_profile_id = ?", profile["id"],
    )
    assert [(row["division"], row["team"]) for row in placed] == [("Am", "McLaren")]
    [state] = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", str(MOCK)
    )
    assert state["current_state"] == "ASSIGNED"
    assert league.granted == {}


async def test_a_league_with_no_driver_role_configured_still_approves(tmp_path):
    """No driver role is set for the league: Kimi is approved and given no role."""
    league = await _league(tmp_path)
    await league.write("UPDATE server_configs SET driver_role_id = NULL")

    await _approve(league)

    assert await _state(league) == "UNASSIGNED"
    assert league.granted.get(SIGNING_UP) is None


async def test_approval_cancels_the_inactivity_timeout(tmp_path):
    """The wizard is finished. Left armed, the 24-hour timeout would later close a signup that
    had already been approved, so the approval disarms Kimi's inactivity timer."""
    league = await _league(tmp_path)

    await _approve(league)

    league.bot.scheduler_service.disarm.assert_any_call(INACTIVITY_TIMER, str(SIGNING_UP))


async def test_an_approval_whose_role_job_is_discarded_is_still_recorded(tmp_path, monkeypatch):
    """Manager 77 approves Kimi and a fault of the bot's own stops the queue at the job that
    gives the driver role; an admin discards it. Kimi is Unassigned, and the log channel carries
    the Approved line naming 77 and Kimi all the same, saying the role was not granted."""
    league = await _league(tmp_path)

    await _approve(league, run=False)
    monkeypatch.setattr(
        "leaguebot.signup.services.review_changes.league_guild",
        AsyncMock(side_effect=RuntimeError("fault")),
    )
    await run_queue(league.bot)
    await discard_job(league.bot)

    assert await _state(league) == "UNASSIGNED"
    [approved] = [line for line in _lines(league) if "| Signup | Approved" in line]
    assert f"<@{REVIEWER}>" in approved
    assert "driver role could not be granted" in league.log().lower(), league.log()


@pytest.mark.parametrize(
    ("outcome", "action"),
    [
        pytest.param("approve", "Approved"),
        pytest.param("reject", "Rejected"),
        pytest.param("request_changes", "Correction requested"),
    ],
)
async def test_a_driver_who_has_left_is_named_by_mention_alone(tmp_path, outcome, action):
    """Kimi has left the server. Manager 77's Approve, or the reason 77 types after Reject or
    Request Changes, is carried out by the queue, and its line names Kimi by mention alone
    beneath 77's line, never by the raw id beside it (core specification, "The record of what
    changed")."""
    league = await _league(tmp_path)
    league.absent.add(SIGNING_UP)

    if outcome == "approve":
        await _approve(league)
    else:
        await _type_reason(league, outcome, "Lap times look mistyped")

    lines = _lines(league)
    [first] = [line for line in lines if f"| Signup | {action}" in line]
    assert f"<@{REVIEWER}>" in first
    assert f"  driver: <@{SIGNING_UP}>" in lines, lines


# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------


async def test_a_rejected_driver_returns_to_not_signed_up(tmp_path):
    """Manager 77 pressed Reject and types the reason: once the queue runs, Kimi is Not Signed
    Up."""
    league = await _league(tmp_path)

    await _type_reason(league, "reject", "Lap time unverified")

    assert await _state(league) == "NOT_SIGNED_UP"


async def test_rejection_grants_no_role(tmp_path):
    """Kimi's rejection, once run, grants him no role, though the league has a driver role and
    his channel is told he was rejected."""
    league = await _league(tmp_path)

    await _type_reason(league, "reject", "Lap time unverified")

    assert league.granted.get(SIGNING_UP) is None
    assert any("rejected" in text.lower() for text in _texts(league)), _texts(league)


async def test_a_rejection_reason_reaches_the_driver(tmp_path):
    """The driver has to know what to fix before signing up again: the closing notice posted in
    Kimi's channel gives "Lap time unverified"."""
    league = await _league(tmp_path)

    await _type_reason(league, "reject", "Lap time unverified")

    assert any("Lap time unverified" in text for text in _texts(league)), _texts(league)


async def test_a_rejection_reason_is_logged(tmp_path):
    """Manager 77 rejects Kimi as a duplicate entry: the log channel carries a Rejected line
    naming 77, with the reason beneath it."""
    league = await _league(tmp_path)

    await _type_reason(league, "reject", "Duplicate entry")

    [rejected] = [line for line in _lines(league) if "| Signup | Rejected" in line]
    assert f"<@{REVIEWER}>" in rejected
    assert "Duplicate entry" in league.log()


@pytest.mark.parametrize("outcome", ["reject", "request_changes"])
async def test_a_signup_still_awaiting_review_is_acted_on_and_nothing_is_refused(
    tmp_path, outcome
):
    """Kimi's signup still awaits review when manager 77's reason arrives: the queue acts on it
    (Kimi Not Signed Up after Reject, awaiting a correction parameter after Request Changes),
    and the manager is told of no refusal."""
    league = await _league(tmp_path)

    press = await _type_reason(league, outcome, "Lap times look mistyped")

    wanted = "NOT_SIGNED_UP" if outcome == "reject" else "AWAITING_CORRECTION_PARAMETER"
    assert await _state(league) == wanted
    assert "⛔" not in _answered(press)


# ---------------------------------------------------------------------------
# Requesting changes
# ---------------------------------------------------------------------------


async def test_requesting_changes_awaits_a_correction_parameter(tmp_path):
    """Manager 77 pressed Request Changes and types the reason: once the queue runs, Kimi awaits
    a correction parameter."""
    league = await _league(tmp_path)

    await _type_reason(league, "request_changes", "Wrong platform")

    assert await _state(league) == "AWAITING_CORRECTION_PARAMETER"


async def test_the_manager_who_asked_is_written_down(tmp_path):
    """The window outlives the process, so the manager's id is saved on Kimi's wizard: it is
    what lets the lapse mention the right manager."""
    league = await _league(tmp_path)

    await _type_reason(league, "request_changes", "Wrong platform")

    assert (await _draft(league))["_correction_requested_by"] == str(REVIEWER)


async def test_the_reason_is_saved_alongside_it(tmp_path):
    """The reason 77 typed, "Wrong platform", is saved on Kimi's wizard beside the manager's
    id, for the re-collection prompt to give."""
    league = await _league(tmp_path)

    await _type_reason(league, "request_changes", "Wrong platform")

    assert (await _draft(league))["_correction_reason"] == "Wrong platform"


async def test_the_driver_is_prompted_in_their_own_channel(tmp_path):
    """Once the queue runs, Kimi's own signup channel is told a correction was requested, with
    the choices of what to correct."""
    league = await _league(tmp_path)

    await _type_reason(league, "request_changes", "Wrong platform")

    assert any("requested a correction" in text for text in _texts(league)), _texts(league)


async def test_a_driver_with_no_wizard_is_left_alone(tmp_path):
    """Kimi's wizard is gone when manager 77's reason arrives: nothing is asked of him, he stays
    awaiting approval, nothing is posted in his channel, and the manager is told it was refused
    rather than that a correction was requested."""
    league = await _league(tmp_path)
    await league.write("DELETE FROM signup_wizard_records WHERE discord_user_id = ?",
                       str(SIGNING_UP))

    press = await _type_reason(league, "request_changes", "Wrong platform")

    assert await _state(league) == "PENDING_ADMIN_APPROVAL"
    assert _texts(league) == []
    told = _answered(press)
    assert "⛔" in told and "✅" not in told, told


# ---------------------------------------------------------------------------
# The reason typed after the signup has moved on (#492)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    ["reject", "request_changes"],
    ids=["reject", "request-changes"],
)
@pytest.mark.parametrize(
    "state", ["UNASSIGNED", "NOT_SIGNED_UP", "AWAITING_CORRECTION_PARAMETER"],
    ids=["after-approve", "after-withdrawal", "changes-already-requested"],
)
async def test_a_reason_typed_after_the_signup_moved_on_is_refused_and_recorded(
    tmp_path, action, state
):
    """Manager 77 presses Reject or Request Changes on Kimi's panel; before the reason arrives,
    another manager approves Kimi, Kimi withdraws, or a correction was already asked for. 77
    types the reason and the queue runs. 77 is told the signup has moved on since they pressed
    the button, one refusal line names the button and 77, and Kimi is left as he is: his state
    unchanged, nothing posted in his channel, no closing notice asked."""
    label = LABELS[action]
    league = await _league(tmp_path)

    press = await _type_reason(league, action, "Lap time could not be verified.",
                               moved_to=state)

    told = _answered(press)
    assert f"⛔ This signup has moved on since you pressed {label}." in told, told
    assert "✅" not in told
    assert await _state(league) == state
    assert _texts(league) == []
    assert await _closings(league) == []
    refusals = [line for line in _lines(league) if line.startswith("⛔")]
    assert len(refusals) == 1, league.log()
    assert refusals[0].startswith(f"⛔ the “{label}” button of"), refusals[0]
    assert f"refused for {_manager(league).display_name} (<@{REVIEWER}>)" in refusals[0]
    assert "has moved on" in refusals[0]


@pytest.mark.parametrize(
    "action",
    ["reject", "request_changes"],
    ids=["reject", "request-changes"],
)
async def test_a_reason_typed_after_the_signup_moved_on_is_taken_down(tmp_path, action):
    """Manager 77 presses Reject or Request Changes on Kimi's panel; Kimi withdraws before the
    reason arrives. 77 types the reason, is told the signup has moved on, and the queue runs:
    the reason is deleted from Kimi's channel, where Kimi could otherwise read it."""
    league = await _league(tmp_path)

    await _type_reason(league, action, "Lap time could not be verified.",
                       moved_to="NOT_SIGNED_UP")

    standing = [m.content for m in league.channel(KIMI_CHANNEL).posted
                if getattr(m, "content", None) == "Lap time could not be verified."]
    assert standing == [], standing


def _reason_standing(league: SeasonLeague, reason: str) -> list[str]:
    return [m.content for m in league.channel(KIMI_CHANNEL).posted
            if getattr(m, "content", None) == reason]


@pytest.mark.parametrize(
    "action",
    ["reject", "request_changes"],
    ids=["reject", "request-changes"],
)
async def test_a_reason_whose_press_is_refused_as_it_comes_up_to_run_is_taken_down(
    tmp_path, action
):
    """Manager 77 presses Reject or Request Changes on Kimi's panel and types the reason while
    Kimi is still awaiting review, so the queue accepts the press. Kimi withdraws before the
    queue reaches it; the press is refused as it comes up to run, 77 is told the signup has moved
    on, and the reason is deleted from Kimi's channel, where Kimi could otherwise read it."""
    league = await _league(tmp_path)

    await _type_reason(league, action, "Lap time could not be verified.",
                       moved_after_ask="NOT_SIGNED_UP")

    refusals = [line for line in _lines(league) if line.startswith("⛔")]
    assert len(refusals) == 1 and "has moved on" in refusals[0], league.log()
    assert _reason_standing(league, "Lap time could not be verified.") == []
    assert await _state(league) == "NOT_SIGNED_UP"


@pytest.mark.parametrize(
    "action",
    ["reject", "request_changes"],
    ids=["reject", "request-changes"],
)
async def test_a_reason_whose_press_is_discarded_at_its_check_is_taken_down(
    tmp_path, monkeypatch, action
):
    """Manager 77 presses Reject or Request Changes on Kimi's panel and types the reason; the
    queue accepts the press, but reading Kimi's profile raises as the check runs again when the
    change starts, so the queue
    stops at it. A league admin discards the job before any of it began, and the reason is
    deleted from Kimi's channel, the change that would have taken it down having never run."""
    from leaguebot.core.services.drivers.driver_service import DriverService

    league = await _league(tmp_path)
    original = DriverService.get_profile

    calls: list[int] = []

    async def failing(self: Any, *args: Any, **kwargs: Any) -> Any:
        # The ask's own check passes; the check as the queue starts the change raises.
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("disk I/O error")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(DriverService, "get_profile", failing)
    await _type_reason(league, action, "Lap time could not be verified.")
    assert await stopped_job(league.db_path) is not None
    assert _reason_standing(league, "Lap time could not be verified.") != []

    monkeypatch.setattr(DriverService, "get_profile", original)
    await discard_job(league.bot)
    await run_queue(league.bot)

    assert _reason_standing(league, "Lap time could not be verified.") == []


# ---------------------------------------------------------------------------
# Every signup decision a person makes is audited (#502)
# ---------------------------------------------------------------------------

async def _approved(tmp_path: Any) -> SeasonLeague:
    league = await _league(tmp_path)
    await _approve(league)
    return league


async def _rejected(tmp_path: Any) -> SeasonLeague:
    league = await _league(tmp_path)
    await _type_reason(league, "reject", "Lap time unverified")
    return league


async def _changes_requested(tmp_path: Any) -> SeasonLeague:
    league = await _league(tmp_path)
    await _type_reason(league, "request_changes", "Lap times look mistyped")
    return league


async def _field_chosen(tmp_path: Any) -> SeasonLeague:
    from datetime import timedelta

    from tests.signup.test_review_changes import _league as offered_league
    from tests.signup.test_review_changes import _press_choice

    league = await offered_league(tmp_path, state="AWAITING_CORRECTION_PARAMETER",
                                  offered_ago=timedelta(minutes=1))
    await _press_choice(league)
    await run_queue(league.bot)
    return league


async def _withdrew(tmp_path: Any) -> SeasonLeague:
    from tests.signup.test_wizard_changes import _mid_signup, _press_cancel

    league = await _mid_signup(tmp_path)
    await _press_cancel(league)
    await run_queue(league.bot)
    return league


async def _expired(tmp_path: Any) -> SeasonLeague:
    from datetime import timedelta

    from tests.signup.test_wizard_changes import (
        INACTIVITY_TIMER as INACTIVITY,
        _idle,
        _league as wizard_league,
        _timer_callback,
    )

    league = await wizard_league(tmp_path)
    await _idle(league, SIGNING_UP, league.clock.now - timedelta(hours=24, minutes=1))
    await _timer_callback(league, INACTIVITY)(str(SIGNING_UP))
    await run_queue(league.bot)
    return league


@pytest.mark.parametrize(
    "decide, after, audited",
    [
        pytest.param(_approved, "UNASSIGNED",
                     ("SIGNUP_APPROVE", REVIEWER, "PENDING_ADMIN_APPROVAL"),
                     id="approve"),
        pytest.param(_rejected, "NOT_SIGNED_UP",
                     ("SIGNUP_REJECT", REVIEWER, "PENDING_ADMIN_APPROVAL"),
                     id="reject"),
        pytest.param(_changes_requested, "AWAITING_CORRECTION_PARAMETER",
                     ("SIGNUP_REQUEST_CHANGES", REVIEWER, "PENDING_ADMIN_APPROVAL"),
                     id="request-changes"),
        pytest.param(_field_chosen, "PENDING_DRIVER_CORRECTION",
                     ("SIGNUP_CORRECTION_CHOICE", REVIEWER, "AWAITING_CORRECTION_PARAMETER"),
                     id="field-choice"),
        pytest.param(_withdrew, "NOT_SIGNED_UP",
                     ("SIGNUP_WITHDRAW", SIGNING_UP, "PENDING_SIGNUP_COMPLETION"),
                     id="withdrawal"),
        pytest.param(_expired, "NOT_SIGNED_UP", None, id="expiry"),
    ],
)
async def test_each_signup_decision_writes_an_audit_entry(tmp_path, decide, after, audited):
    """Kimi's signup is decided by a person: manager 77 approves it, rejects it or asks for
    changes on his review panel (typing the reason), or picks the field Kimi is to correct; or
    Kimi withdraws it himself part-way through. Once the queue has run, Kimi is in his new status
    and exactly one signup audit entry records it: by the person who decided, naming Kimi, his
    status before and his status after. Kimi's signup expiring after 24 hours idle is the bot's
    doing, not a person's, and writes no signup audit entry."""
    from tests.support.settings_league import audit_rows

    league = await decide(tmp_path)

    assert await _state(league) == after
    entries = [row for row in await audit_rows(league.db_path)
               if row["change_type"].startswith("SIGNUP_")]
    if audited is None:
        assert entries == []
        return
    change_type, actor, before = audited
    [entry] = entries
    assert entry["change_type"] == change_type
    assert entry["actor_id"] == actor
    assert entry["old_value"]["state"] == before
    assert entry["old_value"]["discord_user_id"] == str(SIGNING_UP)
    assert entry["new_value"]["new_state"] == after


# ---------------------------------------------------------------------------
# A fake driver's signup is named by its test name (#600)
# ---------------------------------------------------------------------------

#: Kimi's test name, where a case makes him a fake driver.
MOCK_KIMI = "Mock Alpha"


async def _as_mock(league: SeasonLeague) -> None:
    """Kimi made a fake driver called Mock Alpha, no member of the server holding his ID, and the
    server put in test mode."""
    await league.write(
        "UPDATE driver_profiles SET is_test_driver = 1, test_display_name = ? "
        "WHERE discord_user_id = ?", MOCK_KIMI, str(SIGNING_UP),
    )
    league.absent.add(SIGNING_UP)
    await league.set_test_mode(True)


async def _mock_rejected(tmp_path: Any) -> SeasonLeague:
    league = await _league(tmp_path)
    await _as_mock(league)
    await _type_reason(league, "reject", "Lap time unverified")
    return league


async def _mock_changes_requested(tmp_path: Any) -> SeasonLeague:
    league = await _league(tmp_path)
    await _as_mock(league)
    await _type_reason(league, "request_changes", "Lap times look mistyped")
    return league


async def _mock_field_chosen(tmp_path: Any) -> SeasonLeague:
    from datetime import timedelta

    from tests.signup.test_review_changes import _league as offered_league
    from tests.signup.test_review_changes import _press_choice

    league = await offered_league(tmp_path, state="AWAITING_CORRECTION_PARAMETER",
                                  offered_ago=timedelta(minutes=1))
    await _as_mock(league)
    await _press_choice(league)
    await run_queue(league.bot)
    return league


async def _mock_withdrew(tmp_path: Any) -> SeasonLeague:
    """Kimi, made Mock Alpha part-way through signing up; the league admin Admin (77) presses
    Cancel Signup on his wizard, the view handed the services that judge who may answer it."""
    from leaguebot.signup.services.signup_views import WithdrawButtonView
    from tests.signup.test_wizard_changes import _mid_signup
    from tests.support.change_queue import tier_member
    from tests.support.season_league import ADMIN_ID

    league = await _mid_signup(tmp_path)
    await _as_mock(league)
    press = member_interaction(league.bot, user=tier_member("admin", member_id=ADMIN_ID))
    press.guild = league.guild
    press.channel_id = KIMI_CHANNEL
    press.channel = league.channel(KIMI_CHANNEL)
    view = WithdrawButtonView(
        str(SIGNING_UP), league.bot.change_queue, league.bot.signup_module_service,
        config=league.bot.config_service, drivers=league.bot.driver_service,
    )
    await view.withdraw_button.callback(press)
    await run_queue(league.bot)
    return league


async def _mock_cancelled(tmp_path: Any) -> SeasonLeague:
    """Kimi, made Mock Alpha awaiting review; the league admin Admin (77) runs the test-mode
    `/signup cancel` naming his ID and gives the reason "Signed up twice"."""
    from tests.signup.test_signup_test_cog import _run_test_command
    from tests.signup.test_wizard_changes import _submit_cancel

    league = await _league(tmp_path)
    await _as_mock(league)
    asked = await _run_test_command(league, "signup cancel", str(SIGNING_UP))
    await _submit_cancel(league, asked, "Signed up twice")
    await run_queue(league.bot)
    return league


async def _mock_expired(tmp_path: Any) -> SeasonLeague:
    from datetime import timedelta

    from tests.signup.test_wizard_changes import (
        INACTIVITY_TIMER as INACTIVITY,
        _idle,
        _league as wizard_league,
        _timer_callback,
    )

    league = await wizard_league(tmp_path)
    await _idle(league, SIGNING_UP, league.clock.now - timedelta(hours=24, minutes=1))
    await _as_mock(league)
    await _timer_callback(league, INACTIVITY)(str(SIGNING_UP))
    await run_queue(league.bot)
    return league


async def _mock_cancel_refused_on_the_queue(tmp_path: Any) -> SeasonLeague:
    """Kimi, made Mock Alpha awaiting review; the league admin Admin (77) runs the test-mode
    `/signup cancel` naming his ID and submits the form, and before the queue runs it his signup
    is approved, so that the queued cancellation is refused as it runs."""
    from tests.signup.test_signup_test_cog import _run_test_command
    from tests.signup.test_wizard_changes import _submit_cancel

    league = await _league(tmp_path)
    await _as_mock(league)
    asked = await _run_test_command(league, "signup cancel", str(SIGNING_UP))
    await _submit_cancel(league, asked, "Signed up twice")
    await league.write(
        "UPDATE driver_profiles SET current_state = 'UNASSIGNED' WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    await run_queue(league.bot)
    return league


async def _mock_rejection_stops_the_lane(tmp_path: Any) -> SeasonLeague:
    """Kimi, made Mock Alpha awaiting review; manager 77 rejects it, typing the reason, and the
    save of the job that ends the signup fails, so that the queue stops at it."""
    from leaguebot.signup.services.closing_steps import END
    from leaguebot.signup.services.wizard_changes import END_JOB
    from tests.support.change_queue import refuse_the_job

    league = await _league(tmp_path)
    await _as_mock(league)
    await refuse_the_job(league.db_path, END, END_JOB, True)
    await _type_reason(league, "reject", "Lap time unverified")
    return league


async def _mock_channel_gone_stops_the_lane(tmp_path: Any) -> SeasonLeague:
    """Kimi, made Mock Alpha awaiting review; his signup channel is deleted, the bot sees it go
    and asks for his signup's end, and the save of the job that ends it fails, so that the queue
    stops at it. The fault is lifted and a league manager presses Retry."""
    from leaguebot.signup.cogs.signup_cog import SignupCog
    from leaguebot.signup.services.closing_steps import END
    from leaguebot.signup.services.wizard_changes import END_JOB
    from tests.support.change_queue import refuse_the_job, retry_job

    league = await _league(tmp_path)
    await _as_mock(league)
    await refuse_the_job(league.db_path, END, END_JOB, True)
    gone = league.channel(KIMI_CHANNEL)
    league.remove_channel(KIMI_CHANNEL)
    cog = SignupCog.__new__(SignupCog)
    cog.bot = league.bot
    await cog.on_guild_channel_delete(gone)
    await run_queue(league.bot)
    await refuse_the_job(league.db_path, END, END_JOB, False)
    await retry_job(league.bot, lane="league")
    # What the channel was sent before it went stays readable, for the test to read.
    league.channels[KIMI_CHANNEL] = gone
    return league


async def _mock_lapse_stops_the_lane(tmp_path: Any) -> SeasonLeague:
    """Kimi, made Mock Alpha awaiting the choice of a field to correct; its five minutes run out
    and the timer asks for the lapse, whose job telling his channel the correction lapsed fails
    to be saved as done, so that the queue stops at it. A league admin presses Discard."""
    from leaguebot.signup.services.review_changes import LAPSE, POST_LAPSE
    from tests.signup.test_correction_parameter_timeout import _fire_the_timer
    from tests.signup.test_correction_parameter_timeout import _league as lapsing_league
    from tests.support.change_queue import refuse_the_job

    league = await lapsing_league(tmp_path)
    await _as_mock(league)
    await refuse_the_job(league.db_path, LAPSE, POST_LAPSE, True)
    await _fire_the_timer(league)
    await run_queue(league.bot)
    await discard_job(league.bot, lane="league")
    return league


async def _mock_stale_press(tmp_path: Any) -> SeasonLeague:
    """Kimi, made Mock Alpha awaiting review, his wizard past its questions; the league admin
    Admin (77) scrolls up his channel and presses Steam on the platform question, the view handed
    the services that judge who may answer it, and the queue runs."""
    from leaguebot.signup.services.signup_views import PlatformButtonView
    from tests.support.change_queue import tier_member
    from tests.support.season_league import ADMIN_ID

    league = await _league(tmp_path)
    await _as_mock(league)
    press = member_interaction(league.bot, user=tier_member("admin", member_id=ADMIN_ID))
    press.guild = league.guild
    press.channel_id = KIMI_CHANNEL
    press.channel = league.channel(KIMI_CHANNEL)
    view = PlatformButtonView(
        str(SIGNING_UP), league.bot.change_queue, signup=league.bot.signup_module_service,
        config=league.bot.config_service, drivers=league.bot.driver_service,
    )
    [button] = [child for child in view.children if getattr(child, "label", None) == "Steam"]
    await button.callback(press)
    await run_queue(league.bot)
    return league


async def _mock_close_discarding(tmp_path: Any, job: str) -> SeasonLeague:
    """Kimi, made Mock Alpha part-way through signing up, his signup channel standing; manager 77
    runs `/signup close` and presses Confirm Close, and the save of Mock Alpha's *job* (telling
    his channel signups have closed, locking it, or arming its deletion a day on) fails, so that
    the queue stops at it. A league admin presses Discard."""
    from tests.signup.test_window_changes import CLOSE_KIND, _close_league, _confirm_close
    from tests.support.change_queue import refuse_the_job

    league = await _close_league(tmp_path, SIGNING_UP)
    await _as_mock(league)
    await refuse_the_job(league.db_path, CLOSE_KIND, job, True)
    await _confirm_close(league)
    await run_queue(league.bot)
    await discard_job(league.bot)
    return league


async def _mock_switch_off_discarding(tmp_path: Any) -> SeasonLeague:
    """Kimi, made Mock Alpha part-way through signing up, his signup channel standing, the window
    open and the season in Configuration; manager 77 runs `/module disable signup`, and the save
    of the job telling Mock Alpha's channel signups have closed fails, so that the queue stops at
    it. A league admin presses Discard."""
    from unittest.mock import MagicMock

    from leaguebot.core.cogs.module_cog import ModuleCog
    from leaguebot.signup.services.closing_steps import SIGNUP_NOTICE
    from tests.signup.test_window_changes import MODULE_OFF_KIND, _close_league
    from tests.signup.test_window_changes import _manager as closing_manager
    from tests.support.change_queue import refuse_the_job

    league = await _close_league(tmp_path, SIGNING_UP, stage="CONFIGURATION")
    await _as_mock(league)
    await refuse_the_job(league.db_path, MODULE_OFF_KIND, SIGNUP_NOTICE, True)
    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = league.bot
    interaction = closing_manager(league)
    interaction.command = MagicMock(qualified_name="module disable")
    await cog._disable_signup(interaction)
    await run_queue(league.bot)
    await discard_job(league.bot)
    return league


#: Why a case still fails: the in-progress text of a fake driver's closing, correction choices
#: or re-collect mentions it by its ID.
_DOING_BY_ID = (
    "#600: the in-progress text of a fake driver's closing, correction choices or re-collect "
    "mentions it by <@id>, not its test name"
)


#: How a fake driver's signup is ended, sent back or refused; the words of each log entry it
#: writes that must name it, one or several; and the words of the post its channel is sent naming
#: it (None where the post names nobody).
_FAKE_DRIVER_NAMED = [
    pytest.param(_mock_rejected, "| Signup | Rejected", "rejected by", id="reject",
                 marks=pytest.mark.xfail(strict=True, reason=_DOING_BY_ID)),
    pytest.param(_mock_changes_requested, "| Signup | Correction requested",
                 "requested a correction", id="request-changes",
                 marks=pytest.mark.xfail(strict=True, reason=_DOING_BY_ID)),
    pytest.param(_mock_field_chosen, "Correction requested: ", "re-submit", id="field-choice",
                 marks=pytest.mark.xfail(strict=True, reason=_DOING_BY_ID)),
    pytest.param(_mock_withdrew, "| Signup | Withdrawn", None, id="withdrawal",
                 marks=pytest.mark.xfail(strict=True, reason=_DOING_BY_ID)),
    pytest.param(_mock_cancelled, "| Signup | Cancelled", "cancelled by", id="cancel",
                 marks=pytest.mark.xfail(strict=True, reason=_DOING_BY_ID)),
    pytest.param(_mock_expired, "lapsed unconfirmed", None, id="24-hour lapse"),
    pytest.param(_mock_cancel_refused_on_the_queue, "signup has already been approved", None,
                 id="cancel refused on the queue"),
    pytest.param(_mock_rejection_stops_the_lane, "The queue is stopped at job #", None,
                 id="rejection stopping the lane"),
    pytest.param(_mock_channel_gone_stops_the_lane,
                 ("The queue is stopped at job #", "| Retried"), None,
                 id="deleted channel's ending stopping the lane, retried"),
    pytest.param(_mock_lapse_stops_the_lane,
                 ("The queue is stopped at job #", "| Discarded"), None,
                 id="correction lapse stopping the lane, discarded"),
    pytest.param(_mock_stale_press, "button of Mock Alpha's signup wizard refused", None,
                 id="stale button pressed by a league admin"),
    pytest.param(lambda tmp_path: _mock_close_discarding(tmp_path, "signup_notice"),
                 "failed_step: Mock Alpha was not told signups had closed.", None,
                 id="close, telling the channel discarded"),
    pytest.param(lambda tmp_path: _mock_close_discarding(tmp_path, "close_signup"),
                 "failed_step: Mock Alpha's signup channel could not be locked", None,
                 id="close, locking the channel discarded"),
    pytest.param(lambda tmp_path: _mock_close_discarding(tmp_path, "arm_deletion"),
                 "failed_step: Mock Alpha's signup channel will not delete itself", None,
                 id="close, arming the channel's deletion discarded"),
    pytest.param(_mock_switch_off_discarding,
                 "failed_step: Mock Alpha was not told signups had closed.", None,
                 id="signup switched off, telling the channel discarded"),
]

#: The signup changes whose in-progress text names the driver they are for: the closing of a
#: signup on its channel, the offer of the correction choices, and the prompt to re-submit an
#: answer. The queue's "under way" reply and its "has already been asked for" refusal each print
#: a change's in-progress text, `ChangeType.doing` of its payload, word for word.
_DOING_NAMES_THE_DRIVER = ("signup.closing", "signup.choices", "signup.recollect")


@pytest.mark.parametrize(("decide", "logged", "posted"), _FAKE_DRIVER_NAMED)
async def test_a_fake_driver_s_signup_is_named_by_its_test_name_and_never_by_its_id(
    tmp_path, decide, logged, posted
):
    """The server is in test mode and Kimi is a fake driver called Mock Alpha, no member of the
    server holding his ID. His signup is rejected or sent back for changes by manager 77 (typing
    the reason), the field to correct is picked by 77, a league admin presses Cancel Signup on
    his wizard, the league admin Admin (77) cancels it with the test-mode `/signup cancel` giving
    a reason, or it lapses after 24 hours idle; or Admin's cancellation is refused as the queue
    runs it, his signup approved meanwhile; or 77's rejection stops the queue, the save of the job
    ending his signup failing; or his signup channel is deleted and the save of the ending fails,
    a league manager pressing Retry once the fault is lifted; or his correction lapses and its job
    telling his channel fails to be saved, a league admin pressing Discard; or Admin presses Steam
    on his wizard's platform question after it has been answered; or, Kimi part-way through
    signing up, manager 77 closes signups or switches the signup module off, and the job telling
    his channel signups have closed, locking it or arming its deletion fails to be saved, a league
    admin pressing Discard; the queue runs. Each log entry the case names (the refusal, the notice
    of the stopped queue naming the job, the Retry and Discard lines, and the close's report of
    each discarded job included) names Mock Alpha, and nothing in the log channel or in his signup
    channel mentions his ID;
    the rejection notice, the correction choices, the re-submit prompt and the cancellation
    notice posted in his channel name Mock Alpha;
    and the in-progress text of every change the case asked, which the queue's "under way" reply
    and its "has already been asked for" refusal print, mentions no account by his ID, the
    closing of his signup, the offer of his correction choices and the prompt to re-submit an
    answer naming Mock Alpha."""
    league = await decide(tmp_path)

    log = league.log().replace("`", "")
    entries = [entry.replace("`", "") for entry in league.bot.log_channel.sent]
    for words in (logged,) if isinstance(logged, str) else logged:
        assert [entry for entry in entries if words in entry and MOCK_KIMI in entry], log
    assert f"<@{SIGNING_UP}>" not in log, log
    texts = _texts(league)
    assert not [text for text in texts if f"<@{SIGNING_UP}>" in text], texts
    if posted is not None:
        [post] = [text for text in texts if posted in text]
        assert MOCK_KIMI in post, post
    types = {change_type.kind: change_type for change_type in league.bot.change_queue.types}
    for change in await change_rows(league.db_path):
        doing = types[change["kind"]].doing(json.loads(change["payload"]))
        assert f"<@{SIGNING_UP}>" not in doing, (change["kind"], doing)
        if change["kind"] in _DOING_NAMES_THE_DRIVER:
            assert MOCK_KIMI in doing, (change["kind"], doing)
