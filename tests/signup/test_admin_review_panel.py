"""The admin review panel — approving, rejecting and sending back a signup by button.

Issue #208. `admin_review_cog.py` was at 40.0%. The panel is posted into the driver's **own**
signup channel, which the driver can read, so the permission check on these three buttons is
the only thing standing between a driver and approving their own signup. The cog's own docstring
says as much: "It is not a formality."

**The permission check is tested first and hardest.** It used to admit Discord's Manage Guild
permission — a level the two-tier model has no room for — and read the interaction role by
hand; both are now one question asked of `is_league_manager`, so the buttons and the commands
agree by construction rather than by two implementations happening to match.

**The race guard is the second half of the same job.** Three managers can be looking at one
panel, and the buttons never disappear — so each press re-reads the driver's state and refuses
unless they are still `PENDING_ADMIN_APPROVAL`. Without it, two managers pressing Approve and
Reject a second apart would both succeed, and which one the driver ends up in would depend on
the order the callbacks happened to finish.

**Reject and Request Changes collect a reason out of band.** The button cannot open a modal
because the panel is persistent and may outlive a restart, so it parks an entry in
`_PENDING_REASONS` and the cog's `on_message` listener picks up the manager's next message in
that channel. That message is deleted afterwards: it is a private judgement about a driver,
typed into a channel the driver can read.

**An empty reason becomes a stated one.** `"No specific reason given."` is deliberate — the
driver is told something rather than being shown an empty **Reason:** that reads as a bug.

**On the change queue (#439, slice 6b, C78, C84, C85).** The panel is handed the change queue when
it is built (A23). Approve asks `signup.approve` of it; the reason typed after Reject asks
`signup.end`, and after Request Changes `signup.request_changes`, each through the press's stored
interaction; the driver's status, the notice in their channel and the reason's deletion are done
when the queue reaches them. These tests drive the panel through a real queue: the league is
`tests/signup/test_review_changes.py`'s, Kimi (105) awaiting approval in his signup channel
(6405), manager 77 holding the interaction role.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import leaguebot.signup.services.signup_views as arc
from leaguebot.signup.cogs.admin_review_cog import AdminReviewCog
from leaguebot.signup.services.signup_views import (
    AdminReviewView,
    _PENDING_REASONS,
)
from tests.signup.test_review_changes import (
    APPROVE_KIND,
    CLOSING_KIND,
    KIMI_CHANNEL,
    KIMI_LANE,
    REQUEST_KIND,
    REVIEWER,
    _answered,
    _changes_of,
    _driver_state,
    _league,
    _manager,
    _texts,
)
from tests.signup.test_wizard_changes import END_KIND
from tests.support.change_queue import (
    MANAGER_ROLE_ID,
    http_error,
    member_interaction,
    run_queue,
    stopped_job,
    typed_message,
    updated_reply,
)
from tests.support.season_league import SIGNING_UP, SeasonLeague

#: A second league manager, beside 77.
SECOND_MANAGER = REVIEWER + 1
#: A channel holding no signup wizard.
NO_WIZARD_CHANNEL = 6499
#: An account with no driver profile at all.
NO_PROFILE = "9999"
#: The reason manager 77 types after Reject or Request Changes.
REASON = "Lap time could not be verified."


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _clear_pending():
    """`_PENDING_REASONS` is module-level state; a leak between tests would make one
    test's parked reason answer another's message. Each entry's five-minute lapse is a task
    (#482), cancelled here so that none outlives its test."""
    _PENDING_REASONS.clear()
    yield
    for entry in list(_PENDING_REASONS.values()):
        lapse = entry.get("lapse")
        if lapse is not None:
            lapse.cancel()
    _PENDING_REASONS.clear()


BUTTONS = ["approve_button", "request_changes_button", "reject_button"]

#: Each button's label, as a refusal's line names it.
LABELS = {
    "approve_button": "Approve",
    "request_changes_button": "Request Changes",
    "reject_button": "Reject",
}


def _pressed_by(league: SeasonLeague, user_id: int, *, channel_id: int = KIMI_CHANNEL) -> Any:
    """A press by *user_id* in *channel_id*, now."""
    who = _manager(league) if user_id == REVIEWER else league._member(user_id)
    who.bot = False
    press = member_interaction(league.bot, user=who, created_at=league.clock.now)
    press.guild = league.guild
    press.channel_id = channel_id
    press.channel = league.channels.get(channel_id) or MagicMock()
    return press


async def _press(
    league: SeasonLeague,
    button: str,
    *,
    by: int = REVIEWER,
    driver: str | None = str(SIGNING_UP),
    channel_id: int = KIMI_CHANNEL,
) -> Any:
    """*by* presses *button* on a review panel built for *driver* (none: a panel rebuilt at a
    restart), handed the change queue when it is built (A23)."""
    view = AdminReviewView(driver, change_queue=league.bot.change_queue)
    press = _pressed_by(league, by, channel_id=channel_id)
    await getattr(view, button).callback(press)
    return press


#: The rule the log channel's router appends beneath every line it posts.
LOG_SEPARATOR = "\n" + "\u2015" * 36


def _lines(league: SeasonLeague) -> list[str]:
    """The log channel's lines as they were written, without the rule the router appends."""
    return [str(line).removesuffix(LOG_SEPARATOR) for line in league.bot.log_channel.sent]


def _refusals(league: SeasonLeague) -> list[str]:
    return [line for line in _lines(league) if line.startswith("⛔")]


def _sent(press: Any) -> list[str]:
    """What the press's follow-ups said, in order."""
    return [str(call.args[0]) for call in press.followup.send.await_args_list if call.args]


def _assert_refusal_recorded(league: SeasonLeague, button: str, by: int, reason: str) -> None:
    """One line in the log channel, naming the button, the signup review it sits on, the member
    who pressed it, and why (core specification, "The record of what changed")."""
    lines = _refusals(league)
    assert len(lines) == 1, _lines(league)
    line = lines[0]
    assert line.startswith(f"⛔ the “{LABELS[button]}” button"), line
    assert "signup review" in line
    assert f"<@{by}>" in line and "refused for" in line, line
    assert line.endswith(f"— {reason}"), line


async def _nothing_asked(league: SeasonLeague) -> None:
    for kind in (APPROVE_KIND, END_KIND, REQUEST_KIND):
        assert await _changes_of(league, kind) == [], kind


def _payload(change: dict[str, Any]) -> dict[str, Any]:
    payload = change["payload"]
    return json.loads(payload) if isinstance(payload, str) else dict(payload)


# ---------------------------------------------------------------------------
# Who may press these
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("button", BUTTONS, ids=["approve", "request-changes", "reject"])
async def test_a_driver_cannot_action_their_own_signup(tmp_path, button):
    """The panel sits in the driver's own channel, which they can read. This check is the
    only thing between them and approving themselves. Kimi presses *button* on his own panel:
    he is told he has insufficient permissions, one refusal line records it, nothing is asked
    of the queue, no reason is waited for, and his signup still awaits approval."""
    league = await _league(tmp_path)

    press = await _press(league, button, by=SIGNING_UP)
    await run_queue(league.bot)

    assert "Insufficient permissions" in _answered(press)
    _assert_refusal_recorded(league, button, SIGNING_UP, "Insufficient permissions.")
    await _nothing_asked(league)
    assert _PENDING_REASONS == {}
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"


@pytest.mark.parametrize("button", BUTTONS, ids=["approve", "request-changes", "reject"])
async def test_a_signup_already_actioned_is_refused(tmp_path, button):
    """Three managers can be looking at one panel and the buttons never disappear. Without
    this, Approve and Reject a second apart would both succeed and the outcome would turn
    on which callback finished first. Kimi is already Unassigned; manager 77 presses *button*:
    he is told the signup has already been actioned, one refusal line records it, nothing is
    asked of the queue, and Kimi stays Unassigned."""
    league = await _league(tmp_path, state="UNASSIGNED")

    press = await _press(league, button)
    await run_queue(league.bot)

    assert "already been actioned" in _answered(press)
    _assert_refusal_recorded(league, button, REVIEWER, "This signup has already been actioned.")
    await _nothing_asked(league)
    assert _PENDING_REASONS == {}
    assert await _driver_state(league) == "UNASSIGNED"


@pytest.mark.parametrize("button", BUTTONS, ids=["approve", "request-changes", "reject"])
async def test_a_driver_whose_profile_has_gone_is_refused(tmp_path, button):
    """They withdrew, or left the server, between the panel being posted and pressed: a panel
    built for an account with no driver profile is pressed by manager 77, who is told the signup
    has already been actioned; the refusal is recorded and nothing is asked of the queue."""
    league = await _league(tmp_path)

    press = await _press(league, button, driver=NO_PROFILE)
    await run_queue(league.bot)

    assert "already been actioned" in _answered(press)
    _assert_refusal_recorded(league, button, REVIEWER, "This signup has already been actioned.")
    await _nothing_asked(league)


async def test_a_panel_whose_channel_has_no_wizard_cannot_identify_the_driver(tmp_path):
    """A panel rebuilt after a restart carries no stored driver and finds one by channel.
    Finding none must refuse rather than act on `None`: Approve pressed on a rebuilt panel in a
    channel holding no signup wizard tells manager 77 the driver could not be identified, records
    the refusal, and asks nothing of the queue."""
    league = await _league(tmp_path)

    press = await _press(league, "approve_button", driver=None, channel_id=NO_WIZARD_CHANNEL)
    await run_queue(league.bot)

    assert "Could not identify driver" in _answered(press)
    _assert_refusal_recorded(
        league, "approve_button", REVIEWER, "Could not identify driver for this signup."
    )
    await _nothing_asked(league)


async def test_a_panel_rebuilt_after_a_restart_finds_its_driver(tmp_path):
    """A panel rebuilt with no driver, pressed in Kimi's channel, finds Kimi by the channel's
    wizard: the approval asked of the queue names him, and once it runs he is Unassigned."""
    league = await _league(tmp_path)

    await _press(league, "approve_button", driver=None)
    await run_queue(league.bot)

    [approve] = await _changes_of(league, APPROVE_KIND)
    assert str(SIGNING_UP) in json.dumps(_payload(approve))
    assert await _driver_state(league) == "UNASSIGNED"


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


async def test_approving_needs_no_reason(tmp_path):
    """Only the two negative outcomes collect one — a driver told why they were approved
    would be an odd thing to read. Approve waits for no reason: the approval is asked of the
    queue at once."""
    league = await _league(tmp_path)

    await _press(league, "approve_button")

    assert _PENDING_REASONS == {}
    assert len(await _changes_of(league, APPROVE_KIND)) == 1


# ---------------------------------------------------------------------------
# Reject and Request Changes — the parked reason
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "button,action",
    [("request_changes_button", "request_changes"), ("reject_button", "reject")],
    ids=["request-changes", "reject"],
)
async def test_the_negative_buttons_ask_for_a_reason_first(tmp_path, button, action):
    """The panel is persistent and may outlive a restart, so it cannot open a modal — it
    parks an entry and the listener picks up the manager's next message. Manager 77 presses
    *button*: he is asked to type the reason, an entry is parked for him, and nothing is asked
    of the queue yet."""
    league = await _league(tmp_path)

    press = await _press(league, button)

    assert _PENDING_REASONS[(KIMI_CHANNEL, REVIEWER)]["action"] == action
    assert "type the reason" in _answered(press)
    await _nothing_asked(league)


async def test_the_parked_entry_is_keyed_to_the_manager_who_pressed(tmp_path):
    """Two managers in one channel must not answer each other's prompt: managers 77 and 78
    each press Reject on Kimi's panel, and an entry is parked for each."""
    league = await _league(tmp_path)
    league.roles_held[SECOND_MANAGER] = {MANAGER_ROLE_ID}

    await _press(league, "reject_button")
    await _press(league, "reject_button", by=SECOND_MANAGER)

    assert (KIMI_CHANNEL, REVIEWER) in _PENDING_REASONS
    assert (KIMI_CHANNEL, SECOND_MANAGER) in _PENDING_REASONS


# ---------------------------------------------------------------------------
# The listener that collects it
# ---------------------------------------------------------------------------


async def _park(league: SeasonLeague, action: str) -> Any:
    """Manager 77 has pressed *action*'s button on Kimi's panel: the press deferred and its entry
    parked as the button parks it, with the press's own interaction, and the lapse task a reason
    that arrives cancels (#482). Today's rejection off the queue is a stand-in that does nothing,
    as `_league`'s other handlers are, so that only what the reason asks of the queue shows.
    Gives the press."""
    league.bot.wizard_service.reject_signup = AsyncMock(return_value=None)
    press = _pressed_by(league, REVIEWER)
    await press.response.defer(ephemeral=True)
    # The acknowledgement of a deferred press is its first follow-up, and editing that message is
    # editing the press's original response, as Discord has it.
    press.followup.send.return_value = MagicMock(edit=press.edit_original_response)
    _PENDING_REASONS[(KIMI_CHANNEL, REVIEWER)] = {
        "action": action,
        "discord_user_id": str(SIGNING_UP),
        "actor": press.user,
        "guild": league.guild,
        "interaction": press,
        "followup": press.followup,
        "lapse": asyncio.get_running_loop().create_future(),
    }
    return press


async def _hear(league: SeasonLeague, content: str, *, by: int = REVIEWER,
                is_bot: bool = False) -> Any:
    """*by* types *content* in Kimi's channel, which the review cog hears. Gives the message."""
    author = _manager(league) if by == REVIEWER else league._member(by)
    author.bot = is_bot
    message = typed_message(league.channel(KIMI_CHANNEL), author, content,
                            created_at=league.clock.now)
    message.guild = league.guild
    await AdminReviewCog(league.bot).on_message(message)
    return message


def _rejected(league: SeasonLeague) -> list[str]:
    return [text for text in _texts(league) if "rejected" in text.lower()]


async def test_a_rejection_reason_reaches_the_driver(tmp_path):
    """Manager 77 types the reason after Reject. A rejection is asked of the queue; once it
    runs Kimi's channel is told his signup was rejected, giving the reason, and the manager is
    told through his press."""
    league = await _league(tmp_path)
    press = await _park(league, "reject")

    await _hear(league, REASON)
    assert len(await _changes_of(league, END_KIND)) == 1
    await run_queue(league.bot)

    told = _rejected(league)
    assert len(told) == 1 and REASON in told[0], _texts(league)
    assert "rejected" in " ".join(_sent(press) + [updated_reply(press)]).lower()


async def test_a_correction_reason_reaches_the_driver(tmp_path):
    """Manager 77 types the reason after Request Changes: the request asked of the queue
    carries it, to reach Kimi with the correction."""
    league = await _league(tmp_path)
    await _park(league, "request_changes")

    await _hear(league, "Your platform ID looks wrong.")

    [request] = await _changes_of(league, REQUEST_KIND)
    assert "Your platform ID looks wrong." in json.dumps(_payload(request))


async def test_an_empty_reason_becomes_a_stated_one(tmp_path):
    """The driver is told something rather than shown an empty **Reason:** that reads as
    a bug. Manager 77 types only spaces after Reject; once the queue runs Kimi's channel is
    told the signup was rejected for "No specific reason given."."""
    league = await _league(tmp_path)
    await _park(league, "reject")

    await _hear(league, "   ")
    await run_queue(league.bot)

    told = _rejected(league)
    assert len(told) == 1 and "No specific reason given." in told[0], _texts(league)


async def test_the_reason_message_is_deleted(tmp_path):
    """It is a private judgement about a driver, typed into a channel the driver reads. The
    reason stands until the queue reaches it, and is deleted by a job of the rejection."""
    league = await _league(tmp_path)
    await _park(league, "reject")

    message = await _hear(league, "Not fast enough.")
    assert not message.gone
    await run_queue(league.bot)

    assert message.gone


async def test_a_reason_that_cannot_be_deleted_still_counts(tmp_path):
    """The action matters more than the tidying: Discord refuses to delete the manager's
    reason. Once the queue runs Kimi is Not Signed Up all the same; only the turns in Kimi's
    channel stop, at the reason's deletion, and the league lane runs on."""
    league = await _league(tmp_path)
    await _park(league, "reject")
    author = _manager(league)
    message = typed_message(league.channel(KIMI_CHANNEL), author, "Not fast enough.",
                            created_at=league.clock.now)
    message.guild = league.guild
    message.fails_delete = http_error(discord.Forbidden, status=403)

    await AdminReviewCog(league.bot).on_message(message)
    await run_queue(league.bot)

    assert await _driver_state(league) == "NOT_SIGNED_UP"
    stopped = await stopped_job(league.db_path, KIMI_LANE)
    assert stopped is not None and stopped["name"] == "delete_reason", stopped
    assert await stopped_job(league.db_path, "league") is None
    assert len(await _changes_of(league, CLOSING_KIND)) == 1


async def test_the_parked_entry_is_consumed(tmp_path):
    """Popped, not read. Left behind, the manager's next message in that channel would
    reject the driver a second time."""
    league = await _league(tmp_path)
    await _park(league, "reject")

    await _hear(league, "Reason.")

    assert _PENDING_REASONS == {}


async def test_a_message_with_nothing_parked_is_ignored(tmp_path):
    """Ordinary conversation in a signup channel must not be swallowed: manager 77 types in
    Kimi's channel with no reason waited for, and nothing is asked of the queue."""
    league = await _league(tmp_path)

    message = await _hear(league, "just chatting")
    await run_queue(league.bot)

    await _nothing_asked(league)
    assert not message.gone
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"


async def test_the_bot_s_own_messages_are_ignored(tmp_path):
    """The bot posts into this channel constantly, and one of its own messages answering
    a prompt would reject a driver with the bot's text as the reason."""
    league = await _league(tmp_path)
    await _park(league, "reject")

    await _hear(league, "Welcome to the signup wizard!", by=REVIEWER, is_bot=True)

    await _nothing_asked(league)
    assert _PENDING_REASONS != {}


async def test_another_member_s_message_does_not_answer_the_prompt(tmp_path):
    """The entry is keyed to the manager, so Kimi typing in his own channel while a manager
    is composing a reason must not supply it."""
    league = await _league(tmp_path)
    await _park(league, "reject")

    await _hear(league, "wait, why?", by=SIGNING_UP)

    await _nothing_asked(league)
    assert _PENDING_REASONS != {}


# ---------------------------------------------------------------------------
# The press recorded, and the reason's five minutes (#482)
# ---------------------------------------------------------------------------

NEGATIVE = [("request_changes_button", "Request Changes"), ("reject_button", "Reject")]
NEGATIVE_IDS = ["request-changes", "reject"]


#: The manager's reply where no reason arrived after pressing Reject.
REJECT_LAPSED = (
    "⌛ No reason arrived within five minutes. Nothing was rejected; the signup still awaits review."
)


@pytest.mark.parametrize("button,label", NEGATIVE, ids=NEGATIVE_IDS)
async def test_the_negative_buttons_write_their_press_line_and_park_the_press(
    tmp_path, button, label
):
    """The press is logged (owner, 2026-09-30, "Five minutes, logged"), and the entry keeps the
    press's own interaction, through which the reason step later answers and records."""
    league = await _league(tmp_path)

    press = await _press(league, button)

    lines = [line for line in _lines(league) if "Asked for a reason" in line]
    assert len(lines) == 1, _lines(league)
    assert f"<@{REVIEWER}>" in lines[0], lines[0]
    assert f"| the “{label}” button of" in lines[0], lines[0]
    assert "signup review" in lines[0]
    assert lines[0].endswith("| Asked for a reason"), lines[0]
    assert _PENDING_REASONS[(KIMI_CHANNEL, REVIEWER)]["interaction"] is press


@pytest.mark.parametrize("button,label", NEGATIVE, ids=NEGATIVE_IDS)
async def test_a_reason_not_given_in_five_minutes_lapses_and_the_driver_is_untouched(
    tmp_path, monkeypatch, button, label
):
    """Five minutes after the press with no reason typed, the manager is told nothing was
    done, one lapse line names them, nothing is asked of the queue, and Kimi still awaits
    review."""
    assert arc._REASON_LAPSE_SECONDS == 5 * 60
    monkeypatch.setattr(arc, "_REASON_LAPSE_SECONDS", 0)
    league = await _league(tmp_path)

    press = await _press(league, button)
    await _PENDING_REASONS[(KIMI_CHANNEL, REVIEWER)]["lapse"]

    assert _PENDING_REASONS == {}
    told = _sent(press)[-1]
    assert told.startswith("⌛ No reason arrived within five minutes."), told
    assert told.endswith("the signup still awaits review."), told
    if label == "Reject":
        assert told == REJECT_LAPSED
    lapsed = [line for line in _lines(league) if line.startswith("⌛")]
    assert len(lapsed) == 1, _lines(league)
    head, *detail = lapsed[0].split("\n")
    assert head.startswith(f"⌛ the “{label}” button of"), head
    assert "lapsed unconfirmed (started by" in head and f"<@{REVIEWER}>" in head, head
    nothing_done = "Nothing was rejected" if label == "Reject" else "No changes were requested"
    assert [text.strip() for text in detail] == [
        f"{nothing_done}; the signup still awaits review. Press {label} again to give a reason."
    ], detail
    await _nothing_asked(league)
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"


async def test_a_reason_given_in_time_cancels_the_lapse(tmp_path):
    """The reason takes the entry and cancels its lapse, so no lapse follows the rejection:
    the rejection is asked of the queue, and once it runs no lapse line or lapse reply is
    written."""
    league = await _league(tmp_path)
    press = await _press(league, "reject_button")
    lapse = _PENDING_REASONS[(KIMI_CHANNEL, REVIEWER)]["lapse"]

    await _hear(league, REASON)
    await asyncio.gather(lapse, return_exceptions=True)
    await run_queue(league.bot)

    assert lapse.cancelled()
    assert len(await _changes_of(league, END_KIND)) == 1
    assert not any("lapsed" in line for line in _lines(league)), _lines(league)
    assert not any(text.startswith("⌛") for text in _sent(press))


async def test_a_lapse_whose_entry_was_replaced_does_nothing(tmp_path, monkeypatch):
    """A lapse acts only while its own entry is the one pending: a later press has put
    another in its place, and that one is left for its own five minutes."""
    monkeypatch.setattr(arc, "_REASON_LAPSE_SECONDS", 0)
    league = await _league(tmp_path)
    press = await _press(league, "reject_button")
    first = _PENDING_REASONS[(KIMI_CHANNEL, REVIEWER)]
    later = dict(first, lapse=None)
    _PENDING_REASONS[(KIMI_CHANNEL, REVIEWER)] = later

    await asyncio.gather(first["lapse"], return_exceptions=True)

    assert _PENDING_REASONS[(KIMI_CHANNEL, REVIEWER)] is later
    assert not any("lapsed" in line for line in _lines(league)), _lines(league)
    assert not any(text.startswith("⌛") for text in _sent(press))


async def test_a_reset_cancels_every_pending_lapse_and_none_fires_after(tmp_path):
    """`/bot pack` and `/bot factory-reset` clear the league's state in memory: every pending
    reason goes, and its lapse with it, so no lapse fires into a league that has changed.
    Managers 77 and 78 press Reject and Request Changes on Kimi's panel; the league's memory is
    cleared, handed the change queue: both lapses are cancelled, no reason is left waiting, and
    no lapse is recorded or told. The lapses keep their five minutes: a lapse of no length would
    fire while the second press reads the database, and leave one pending."""
    from leaguebot.core.services.in_memory_state import clear_in_memory_state

    league = await _league(tmp_path)
    league.roles_held[SECOND_MANAGER] = {MANAGER_ROLE_ID}
    rejecting = await _press(league, "reject_button")
    requesting = await _press(league, "request_changes_button", by=SECOND_MANAGER)
    lapses = [entry["lapse"] for entry in _PENDING_REASONS.values()]

    clear_in_memory_state(league.bot, queue=league.bot.change_queue)
    await asyncio.gather(*lapses, return_exceptions=True)

    assert len(lapses) == 2 and all(lapse.cancelled() for lapse in lapses)
    assert _PENDING_REASONS == {}
    assert not any("lapsed" in line for line in _lines(league)), _lines(league)
    assert not any(text.startswith("⌛") for text in _sent(rejecting) + _sent(requesting))


@pytest.mark.parametrize(
    "action,label",
    [
        pytest.param("request_changes", "Request Changes"),
        pytest.param("reject", "Reject"),
    ],
    ids=NEGATIVE_IDS,
)
async def test_a_fault_in_the_reason_step_is_reported_through_the_press(tmp_path, action, label):
    """The reason completes the change the press asked for, so a fault in asking it is the
    button's own: the queue cannot take the request (the database is locked), and the manager is
    told once through his press, one failure line names the button, and nothing escapes to
    discord.py's event handler (core specification, "When a command fails")."""
    league = await _league(tmp_path)
    press = await _park(league, action)
    league.bot.change_queue.ask = AsyncMock(side_effect=RuntimeError("database is locked"))

    await _hear(league, REASON)

    failures = [text for text in _sent(press) if text.startswith("❌")]
    assert len(failures) == 1, _sent(press)
    assert f"“{label}” button" in failures[0]
    assert not any(text.startswith("✅") for text in _sent(press))
    failed = [line for line in _lines(league) if line.startswith("❌")]
    assert len(failed) == 1, _lines(league)
    assert failed[0].startswith(f"❌ the “{label}” button of"), failed[0]
    assert f"<@{REVIEWER}>" in failed[0] and "failed for" in failed[0]
