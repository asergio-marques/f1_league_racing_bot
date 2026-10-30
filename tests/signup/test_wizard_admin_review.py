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
    two times unless given); manager 77 holding the interaction role. Today's handlers off the
    queue are stand-ins that do nothing, so that only what is asked of the queue shows."""
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
        "INSERT INTO signup_records (season_id, discord_user_id, server_display_name, "
        "nationality, platform, platform_id, driver_type, lap_times_json, signup_channel_id) "
        "VALUES (?, ?, ?, 'British', 'Steam', 'kimi7', 'Full-Time Driver', ?, ?)",
        SEASON_ID, str(SIGNING_UP), str(SIGNING_UP),
        json.dumps(LAP_TIMES if lap_times is None else lap_times), KIMI_CHANNEL,
    )
    made = league._recording(channel(KIMI_CHANNEL, league.events))
    made.guild = league.guild
    made.posted = []
    made.set_permissions = AsyncMock()
    league.channels[KIMI_CHANNEL] = made
    wizards = league.bot.wizard_service
    wizards.approve_signup = AsyncMock(return_value=None)
    wizards.reject_signup = AsyncMock(return_value=None)
    wizards.request_changes = AsyncMock(return_value=None)
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
    """Manager 77 presses Approve on Kimi's review panel, handed the change queue when built
    (A23), and the queue runs unless *run* is false."""
    from leaguebot.signup.services.signup_views import AdminReviewView

    press = _press(league)
    view = AdminReviewView(str(SIGNING_UP), change_queue=league.bot.change_queue)
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
    from leaguebot.signup.cogs.admin_review_cog import _PENDING_REASONS, AdminReviewCog

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
    from leaguebot.core.services.driver_service import DriverService

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
