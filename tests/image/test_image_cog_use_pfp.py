"""The `/images use-pfp` commands.

Three toggles governing whether the bot obtains driver portraits from Discord, and how it
keeps them up to date. The rule they all serve: with portraits enabled, at least one of the
two update triggers must be enabled, and a command that would leave neither is refused with
the configuration left as it stood.

Every outcome is recorded in the log channel (#482): a refusal as one "⛔" line, a success as
one line naming the command, and the confirmation that enables daily updates as cancelled,
lapsed, refused, succeeded or failed — once, however often it is pressed.

The toggles and the confirmation run on the change queue (#439, slice 7): each is driven past
its permission guard on a league built by the migrations, with the real image services, a
scheduler double and the real queue, which the test runs. A refusal at the ask is answered at
once and queues nothing; a change asked is acknowledged at once and answered once it has run,
the daily job armed or removed by a job after the save, the success line written only once the
job is armed.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.image.cogs.image_cog import ImageCog, PortraitTimeConfirm, PortraitTimeModal
from tests.support.change_queue import run_queue
from tests.support.image_cog_doubles import (
    QUEUED_MEMBER as MEMBER,
    assert_one_line,
    assert_one_refusal,
    interaction as _image_interaction,
    log_bot,
    logged,
    said,
)
from tests.support.settings_league import audit_rows, settings_league, switch
from tests.support.undecorate import undecorate

#: The command the confirmation belongs to, as its log lines name it.
DAILY = "`/images use-pfp daily-toggle`"
#: The portrait-time form, as its refusals name it.
FORM = "the “Daily portrait updates” form"
#: What a failure to schedule the daily job says became of the change.
STORED_AT_RESTART = (
    "The setting is stored, and the daily job will be scheduled when the bot next starts."
)
#: The two lines beneath a cancel or a lapse of the confirmation.
ENDED = (
    "\n  Daily driver-portrait updates are unchanged."
    "\n  Run `/images use-pfp daily-toggle` again to enable them."
)
#: Each toggle's command body, and the command as a league types it.
COMMANDS = {
    "use_pfp_toggle": "images use-pfp toggle",
    "use_pfp_prerender_toggle": "images use-pfp prerender-toggle",
    "use_pfp_daily_toggle": "images use-pfp daily-toggle",
}
#: The portrait settings, as the league starts with them.
PACKAGED = {"use_pfp": 0, "pfp_prerender": 1, "pfp_daily": 0, "pfp_daily_time": "03:00"}


def _interaction(league, command=None):
    """An interaction from "Race Control" for `/<command>` (None: a form's or a button's), on
    *league*'s bot.

    It tracks whether it has been answered, as discord.py's does, so a refusal and a reply after
    a button's message is edited both land where a member would see them.
    """
    return _image_interaction(command, bot=league.bot)


async def _league(tmp_path, *, modules=("images",), **config) -> tuple[ImageCog, Any]:
    """A league with *modules* on and its portrait settings as `PACKAGED` with *config* written
    over them; its bot is `log_bot`'s real-queue form. Returns the cog and the league."""
    league = await settings_league(tmp_path, modules=modules)
    if "images" in modules:
        values = {**PACKAGED, **config}
        async with get_connection(league.db_path) as db:
            for column, value in values.items():
                stored = int(value) if isinstance(value, bool) else value
                await db.execute(f"UPDATE image_config SET {column} = ?", (stored,))
            await db.commit()
    log_bot(league)
    return ImageCog(league.bot), league


async def _portraits(league) -> dict[str, Any]:
    """The four portrait settings as stored."""
    async with get_connection(league.db_path) as db:
        row = await (await db.execute(
            "SELECT use_pfp, pfp_prerender, pfp_daily, pfp_daily_time FROM image_config"
        )).fetchone()
    return dict(row)


async def _write(league, sql: str) -> None:
    async with get_connection(league.db_path) as db:
        await db.execute(sql)
        await db.commit()


async def _run(cog, league, name: str):
    """Run the toggle *name* past its tier guard, then the queue; returns the interaction."""
    interaction = _interaction(league, COMMANDS[name])
    await undecorate(getattr(ImageCog, name))(cog, interaction)
    await run_queue(league.bot)
    return interaction


def _acknowledged(interaction) -> None:
    """Acknowledged at once as under way."""
    assert any(text and text.startswith("⏳ ") for text in interaction.said), interaction.said


def _not_asked(interaction) -> None:
    """Answered at once, nothing queued."""
    assert not any(text and text.startswith("⏳ ") for text in interaction.said), interaction.said


def _fail_to_arm(league) -> None:
    """The scheduler refusing the daily job, as a job store refusing it would."""
    league.scheduler.schedule_portrait_refresh.side_effect = RuntimeError(
        "the job store refused the job"
    )


async def _form(cog, league) -> PortraitTimeModal:
    """The portrait-time form, as `/images use-pfp daily-toggle` opens it."""
    opener = _interaction(league, "images use-pfp daily-toggle")
    await undecorate(ImageCog.use_pfp_daily_toggle)(cog, opener)
    return opener.response.send_modal.await_args.args[0]


async def _confirmation(cog, league, time="19:00"):
    """The confirmation the form sends for *time*, and the form's own interaction."""
    modal = await _form(cog, league)
    modal.time_of_day._value = time
    submitted = _interaction(league)
    await modal.on_submit(submitted)
    return submitted.response.send_message.await_args.kwargs["view"], submitted


async def _confirm(view, league):
    """Press the confirmation's ✅ Confirm, then run the queue; returns the press."""
    press = _interaction(league)
    await view.confirm.callback(press)
    await run_queue(league.bot)
    return press


# ── The module gate ───────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(COMMANDS))
async def test_every_command_refuses_while_the_module_is_disabled(name, tmp_path):
    cog, league = await _league(tmp_path, modules=())

    interaction = await _run(cog, league, name)

    assert "Image module is not enabled" in said(interaction)
    _not_asked(interaction)
    interaction.response.send_modal.assert_not_awaited()
    assert await audit_rows(league.db_path) == []


# ── The master toggle ─────────────────────────────────────────────────────


async def test_the_master_toggle_enables_portraits(tmp_path):
    cog, league = await _league(tmp_path, use_pfp=False)

    interaction = await _run(cog, league, "use_pfp_toggle")

    _acknowledged(interaction)
    assert (await _portraits(league))["use_pfp"] == 1
    assert "enabled" in interaction.said[-1]


async def test_the_master_toggle_disables_portraits_and_stops_the_daily_job(tmp_path):
    cog, league = await _league(tmp_path, use_pfp=True, pfp_daily=True)

    interaction = await _run(cog, league, "use_pfp_toggle")

    _acknowledged(interaction)
    assert (await _portraits(league))["use_pfp"] == 0
    # A job left running against a setting that says no would keep fetching.
    league.scheduler.cancel_portrait_refresh.assert_called_once_with()


# ── The two sub-toggles need the master on ────────────────────────────────


@pytest.mark.parametrize("name", ["use_pfp_prerender_toggle", "use_pfp_daily_toggle"])
async def test_a_sub_toggle_refuses_while_portraits_are_disabled(name, tmp_path):
    cog, league = await _league(tmp_path, use_pfp=False)

    interaction = await _run(cog, league, name)

    assert "not being obtained from Discord" in said(interaction)
    _not_asked(interaction)
    assert await _portraits(league) == PACKAGED


async def test_prerender_toggles_off_while_the_daily_job_stands(tmp_path):
    cog, league = await _league(tmp_path, use_pfp=True, pfp_prerender=True, pfp_daily=True)

    interaction = await _run(cog, league, "use_pfp_prerender_toggle")

    _acknowledged(interaction)
    assert (await _portraits(league))["pfp_prerender"] == 0


# ── The at-least-one rule, at the command surface ─────────────────────────


async def test_disabling_the_last_trigger_is_refused_and_changes_nothing(tmp_path):
    cog, league = await _league(tmp_path, use_pfp=True, pfp_prerender=True, pfp_daily=False)

    interaction = await _run(cog, league, "use_pfp_prerender_toggle")

    reply = said(interaction)
    assert "Cannot disable pre-render updates" in reply
    assert "Enable daily updates first" in reply
    _not_asked(interaction)
    assert (await _portraits(league))["pfp_prerender"] == 1


async def test_disabling_the_last_trigger_is_refused_the_other_way_round(tmp_path):
    cog, league = await _league(tmp_path, use_pfp=True, pfp_prerender=False, pfp_daily=True)

    interaction = await _run(cog, league, "use_pfp_daily_toggle")

    assert "Cannot disable daily updates" in said(interaction)
    _not_asked(interaction)
    assert (await _portraits(league))["pfp_daily"] == 1
    league.scheduler.cancel_portrait_refresh.assert_not_called()


async def test_the_master_toggle_is_never_refused_by_the_rule(tmp_path):
    # Turning the feature off wholesale is what an invalid configuration was an awkward
    # spelling of, so it cannot itself be invalid.
    cog, league = await _league(tmp_path, use_pfp=True, pfp_prerender=False, pfp_daily=False)

    interaction = await _run(cog, league, "use_pfp_toggle")

    _acknowledged(interaction)
    assert (await _portraits(league))["use_pfp"] == 0


# ── The daily toggle's modal ──────────────────────────────────────────────


async def test_enabling_the_daily_job_opens_the_modal_and_commits_nothing_yet(tmp_path):
    cog, league = await _league(tmp_path, use_pfp=True, pfp_daily=False, pfp_daily_time="03:00")

    interaction = await _run(cog, league, "use_pfp_daily_toggle")

    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(modal, PortraitTimeModal)
    # Prefilled with what is stored, so a manager need not remember it.
    assert modal.time_of_day.default == "03:00"
    _not_asked(interaction)
    assert (await _portraits(league))["pfp_daily"] == 0


async def test_the_modal_says_the_time_is_utc(tmp_path):
    # The manager is naming a zone they did not choose; the scheduler is UTC throughout.
    cog, _league_ = await _league(tmp_path, use_pfp=True)
    modal = PortraitTimeModal(cog, "03:00")

    assert "UTC" in modal.time_of_day.label


async def test_disabling_the_daily_job_needs_no_modal(tmp_path):
    cog, league = await _league(tmp_path, use_pfp=True, pfp_prerender=True, pfp_daily=True)

    interaction = await _run(cog, league, "use_pfp_daily_toggle")

    interaction.response.send_modal.assert_not_awaited()
    _acknowledged(interaction)
    assert (await _portraits(league))["pfp_daily"] == 0
    league.scheduler.cancel_portrait_refresh.assert_called_once_with()


async def test_an_unreadable_time_changes_nothing(tmp_path):
    cog, league = await _league(tmp_path, use_pfp=True)
    modal = PortraitTimeModal(cog, "03:00")
    modal.time_of_day._value = "half past three"
    interaction = _interaction(league)

    await modal.on_submit(interaction)
    await run_queue(league.bot)

    assert "Could not read" in said(interaction)
    assert (await _portraits(league))["pfp_daily"] == 0


async def test_a_readable_time_asks_for_confirmation_before_committing(tmp_path):
    cog, league = await _league(tmp_path, use_pfp=True)
    modal = PortraitTimeModal(cog, "03:00")
    modal.time_of_day._value = "7pm"
    interaction = _interaction(league)

    await modal.on_submit(interaction)
    await run_queue(league.bot)

    reply = said(interaction)
    assert "19:00 UTC" in reply and "Confirm" in reply
    view = interaction.response.send_message.await_args.kwargs["view"]
    assert isinstance(view, PortraitTimeConfirm)
    # Nothing is committed by the modal itself.
    assert await _portraits(league) == {**PACKAGED, "use_pfp": 1}


async def test_confirming_stores_the_time_enables_the_job_and_arms_it(tmp_path):
    """Confirm takes its buttons down, so the same confirmation cannot be pressed again, and is
    acknowledged; once run, the time and the flag are stored, the job armed, and the reply names
    the time."""
    cog, league = await _league(tmp_path, use_pfp=True)
    view, _submitted = await _confirmation(cog, league)

    interaction = await _confirm(view, league)

    stored = await _portraits(league)
    assert (stored["pfp_daily"], stored["pfp_daily_time"]) == (1, "19:00")
    league.scheduler.schedule_portrait_refresh.assert_called_once_with("19:00")
    _acknowledged(interaction)
    assert "19:00 UTC" in interaction.said[-1]
    interaction.response.edit_message.assert_awaited()
    assert interaction.response.edit_message.await_args.kwargs.get("view", "kept") is None


async def test_cancelling_the_confirmation_changes_nothing(tmp_path):
    cog, league = await _league(tmp_path, use_pfp=True)
    view, _submitted = await _confirmation(cog, league)
    interaction = _interaction(league)

    await view.cancel.callback(interaction)
    await run_queue(league.bot)

    assert "Cancelled" in said(interaction)
    assert await _portraits(league) == {**PACKAGED, "use_pfp": 1}
    league.scheduler.schedule_portrait_refresh.assert_not_called()


async def test_a_scheduler_failure_does_not_lose_the_setting(tmp_path):
    """The setting is stored first, so startup recovery arms the job on the next restart; the
    member is told so as a failure, never as a success (F1), and the one line is the failure."""
    cog, league = await _league(tmp_path, use_pfp=True)
    _fail_to_arm(league)
    view, _submitted = await _confirmation(cog, league)

    interaction = await _confirm(view, league)

    _acknowledged(interaction)
    assert (await _portraits(league))["pfp_daily"] == 1
    reply = interaction.said[-1]
    assert STORED_AT_RESTART in reply and "✅" not in reply
    lines = logged(league.bot)
    assert len(lines) == 1, lines
    assert lines[0].startswith("❌ ") and f"failed for {MEMBER}" in lines[0], lines[0]


# ── The confirmation's every ending is recorded ───────────────────────────


def _http_error() -> discord.HTTPException:
    """Discord refusing to edit the message the buttons sit on."""
    return discord.HTTPException(MagicMock(status=500, reason="Server Error"), "gone")


async def test_cancelling_is_recorded_and_takes_the_buttons_down(tmp_path):
    cog, league = await _league(tmp_path, use_pfp=True)
    view, _submitted = await _confirmation(cog, league)
    press = _interaction(league)

    await view.cancel.callback(press)

    assert logged(league.bot) == [f"↩️ {DAILY} cancelled by {MEMBER}{ENDED}"]
    press.response.edit_message.assert_awaited()
    assert press.response.edit_message.await_args.kwargs.get("view", "kept") is None


async def test_a_lapse_is_recorded_naming_who_opened_it_and_takes_the_buttons_down(tmp_path):
    cog, league = await _league(tmp_path, use_pfp=True)
    view, submitted = await _confirmation(cog, league)

    await view.on_timeout()

    assert logged(league.bot) == [
        f"⌛ {DAILY} lapsed unconfirmed (started by {MEMBER}){ENDED}"
    ]
    submitted.edit_original_response.assert_awaited()
    assert submitted.edit_original_response.await_args.kwargs.get("view", "kept") is None


@pytest.mark.parametrize("ending", ["cancel", "lapse"])
async def test_an_ending_is_recorded_though_the_buttons_cannot_be_taken_down(ending, tmp_path):
    cog, league = await _league(tmp_path, use_pfp=True)
    view, submitted = await _confirmation(cog, league)

    if ending == "cancel":
        press = _interaction(league)
        press.response.edit_message.side_effect = _http_error()
        await view.cancel.callback(press)
        mark = "↩️ "
    else:
        submitted.edit_original_response.side_effect = _http_error()
        await view.on_timeout()
        mark = "⌛ "

    lines = logged(league.bot)
    assert len(lines) == 1 and lines[0].startswith(f"{mark}{DAILY} "), lines


# ── The success is recorded only once the daily job is armed (F2) ────────


async def test_the_master_toggle_arms_the_daily_job_before_the_success_is_recorded(tmp_path):
    """Portraits switched on with daily updates standing at 07:45: acknowledged at once; when
    it runs, the daily job is re-armed at the standing time, no line has been written by the
    time it is armed, and one success line follows it."""
    cog, league = await _league(tmp_path, use_pfp=False, pfp_daily=True, pfp_daily_time="07:45")
    recorded_by_then = []
    league.scheduler.schedule_portrait_refresh.side_effect = (
        lambda *_args: recorded_by_then.append(logged(league.bot))
    )

    interaction = await _run(cog, league, "use_pfp_toggle")

    _acknowledged(interaction)
    assert recorded_by_then == [[]]
    league.scheduler.schedule_portrait_refresh.assert_called_once_with("07:45")
    assert_one_line(league.bot, "images use-pfp toggle")


async def test_a_scheduler_failure_on_the_master_toggle_is_one_failure_not_a_success(tmp_path):
    cog, league = await _league(tmp_path, use_pfp=False, pfp_daily=True)
    _fail_to_arm(league)

    interaction = await _run(cog, league, "use_pfp_toggle")

    _acknowledged(interaction)
    assert (await _portraits(league))["use_pfp"] == 1
    reply = interaction.said[-1]
    assert STORED_AT_RESTART in reply and "✅" not in reply
    lines = logged(league.bot)
    assert len(lines) == 1, lines
    assert lines[0].startswith(f"❌ `/images use-pfp toggle` failed for {MEMBER}"), lines[0]


# ── One save per confirmation (F10) ───────────────────────────────────────


async def test_a_second_press_of_confirm_saves_nothing_and_is_refused(tmp_path):
    """The first press is acknowledged and, once run, saves the daily setting with one audit
    entry and one success line; a second press of the same confirmation is refused at once,
    saving nothing and arming nothing more."""
    cog, league = await _league(tmp_path, use_pfp=True)
    view, _submitted = await _confirmation(cog, league)

    first = await _confirm(view, league)
    second = await _confirm(view, league)

    _acknowledged(first)
    league.scheduler.schedule_portrait_refresh.assert_called_once()
    assert len(await audit_rows(league.db_path)) == 1
    assert "ℹ️ This confirmation has already been answered." in said(second)
    _not_asked(second)
    lines = logged(league.bot)
    assert len(lines) == 2, lines
    assert sum(
        line.startswith(f"{MEMBER} | /images use-pfp daily-toggle | Success") for line in lines
    ) == 1, lines
    assert sum(
        line.startswith("⛔ ") and f"refused for {MEMBER} — " in line for line in lines
    ) == 1, lines


async def test_the_confirmation_is_not_stopped_until_cancel_and_confirm_have_finished(
    tmp_path, monkeypatch
):
    """A stopped view answers no press at all, so stopping one before its press has finished
    would leave a second press unanswered and unrecorded rather than refused: a cancel is not
    stopped while its ending is recorded, nor a confirm while its change is asked."""
    cog, league = await _league(tmp_path, use_pfp=True)
    cancelled, _submitted = await _confirmation(cog, league)
    confirmed, _submitted = await _confirmation(cog, league)
    while_posting, while_asking = [], []
    league.bot.output_router.post_log = AsyncMock(
        side_effect=lambda *_args, **_kwargs: while_posting.append(cancelled.is_finished())
    )
    ask = league.bot.change_queue.ask

    async def _asking(*args, **kwargs):
        while_asking.append(confirmed.is_finished())
        return await ask(*args, **kwargs)

    monkeypatch.setattr(league.bot.change_queue, "ask", _asking)

    await cancelled.cancel.callback(_interaction(league))
    await confirmed.confirm.callback(_interaction(league))

    assert while_posting[0] is False
    assert while_asking == [False]
    assert cancelled.is_finished() and confirmed.is_finished()


# ── Checked again on submit (F5) ──────────────────────────────────────────

_CHANGED_SINCE_OPENING = [
    pytest.param(
        "module_enabled",
        False,
        "❌ The Image module was switched off while this form was open. Nothing was stored.",
        id="module-switched-off",
    ),
    pytest.param(
        "use_pfp",
        False,
        "❌ Driver portraits stopped being obtained from Discord while this was open. "
        "Nothing was changed.",
        id="portraits-switched-off",
    ),
    pytest.param(
        "pfp_daily",
        True,
        "ℹ️ Daily driver-portrait updates were already enabled while this was open. "
        "Nothing was changed.",
        id="daily-already-on",
    ),
]

#: The same, as the Confirm button answers them: a confirmation is not a form, so its
#: module refusal says "while this was open", as the other two do.
_CONFIRM_CHANGED_SINCE_OPENING = [
    pytest.param(
        "module_enabled",
        False,
        "❌ The Image module was switched off while this was open. Nothing was stored.",
        id="module-switched-off",
    ),
    *_CHANGED_SINCE_OPENING[1:],
]


async def _change_since_opening(league, setting, value) -> None:
    """Another manager changed *setting* to *value* while the form or confirmation was open."""
    if setting == "module_enabled":
        await switch(league.bot, ("images",), on=value)
    else:
        await _write(league, f"UPDATE image_config SET {setting} = {int(value)}")


@pytest.mark.parametrize(("setting", "value", "reply"), _CONFIRM_CHANGED_SINCE_OPENING)
async def test_confirm_writes_nothing_once_things_changed_since_opening(
    setting, value, reply, tmp_path
):
    """Refused at once by the daily setting's check, nothing queued: the time stays 03:00, no
    audit entry, the job not armed, and one "⛔" line."""
    cog, league = await _league(tmp_path, use_pfp=True)
    view, _submitted = await _confirmation(cog, league)
    await _change_since_opening(league, setting, value)

    press = await _confirm(view, league)

    assert (await _portraits(league))["pfp_daily_time"] == "03:00"
    assert await audit_rows(league.db_path) == []
    league.scheduler.schedule_portrait_refresh.assert_not_called()
    assert reply in said(press)
    _not_asked(press)
    lines = logged(league.bot)
    assert len(lines) == 1, lines
    assert lines[0].startswith("⛔ ") and f"refused for {MEMBER} — " in lines[0], lines[0]


@pytest.mark.parametrize(("setting", "value", "reply"), _CHANGED_SINCE_OPENING)
async def test_the_form_asks_for_no_confirmation_once_things_changed_since_opening(
    setting, value, reply, tmp_path
):
    cog, league = await _league(tmp_path, use_pfp=True)
    modal = await _form(cog, league)
    await _change_since_opening(league, setting, value)
    modal.time_of_day._value = "19:00"
    submitted = _interaction(league)

    await modal.on_submit(submitted)

    assert reply in said(submitted)
    assert all("view" not in call.kwargs for call in submitted.response.send_message.await_args_list)
    assert_one_refusal(league.bot, FORM)


async def test_an_unreadable_time_is_recorded_as_a_refusal_of_the_form(tmp_path):
    cog, league = await _league(tmp_path, use_pfp=True)
    modal = await _form(cog, league)
    modal.time_of_day._value = "half past three"
    submitted = _interaction(league)

    await modal.on_submit(submitted)

    assert "Could not read" in said(submitted)
    assert_one_refusal(league.bot, FORM)
