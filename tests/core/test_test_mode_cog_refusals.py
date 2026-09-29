"""Every refusal of the `/test-mode` commands, and of the roster import form, is recorded (#482).

The rule is the core specification's "The record of what changed": a refusal is recorded as one
line naming the member, what was refused and why, and only a view, a list, a preview and the hub's
About record nothing. Each case below turns a league admin away at one of the test-mode cog's own
checks, and expects today's reply, seen by them alone, and exactly one standard refusal line; a
refusal on the roster import form names the form. The refusal to change the roster outside
placements is recorded already and is not a case; the restore confirmation is tested in
`test_test_mode_backup.py`.

`/test-mode backup status` is a view, so its refusal outside test mode records nothing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs import test_mode_cog as cog_module
from leaguebot.core.cogs.test_mode_cog import TestModeCog as _Cog
from leaguebot.core.cogs.test_mode_cog import _RosterImportModal
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services import backup_service
from leaguebot.core.services import season_lifecycle_service
from leaguebot.core.services import test_mode_service
from leaguebot.core.services import test_roster_service
from leaguebot.core.utils import roster_import
from leaguebot.results.services import result_submission_service
from tests.support.undecorate import undecorate

ADMIN_ID = 4242
DRIVER_ID = 5151

#: The marks a reply may open with, which a refusal's log line leaves out of its reason.
_REPLY_MARKS = ("❌", "⛔", "⚠️", "⚠", "ℹ️", "ℹ", "⏳", "⛓", "⏸️", "⏸")

#: How the log names the roster import form.
_IMPORT_FORM = "the “Import a test roster” form"


def _reason(reply: str) -> str:
    """The reason a refusal line gives: the reply's first line, without its opening mark."""
    first = reply.strip().splitlines()[0].strip()
    for mark in _REPLY_MARKS:
        if first.startswith(mark):
            return first[len(mark):].lstrip("️").strip()
    return first


def _cog() -> _Cog:
    """A test-mode cog on a server in test mode, whose every check passes until a case makes
    one refuse."""
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"
    bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(test_mode_active=True)
    )
    bot.output_router.post_log = AsyncMock(return_value=None)
    cog = _Cog.__new__(_Cog)
    cog.bot = bot
    # The stage the roster may change in is recorded already, and tested on its own.
    cog._refuse_roster_change_outside_placements = AsyncMock(return_value=False)
    return cog


def _interaction(cog: _Cog, command: str | None) -> MagicMock:
    """A `/test-mode` command, or the import form's submission, by the league admin Alex,
    answering as Discord's does."""
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction = MagicMock()
    interaction.client = cog.bot
    if command is None:
        interaction.command = None
    else:
        interaction.command.qualified_name = command
    interaction.user.id = ADMIN_ID
    interaction.user.display_name = "Alex"
    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.response.send_modal = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


def _member() -> MagicMock:
    member = MagicMock()
    member.id = DRIVER_ID
    member.display_name = "Racer"
    return member


# ── How a case makes the cog refuse ───────────────────────────────────────────


def _not_in_test_mode(cog, monkeypatch):
    cog.bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(test_mode_active=False)
    )


def _no_season_in_configuration(cog, monkeypatch):
    monkeypatch.setattr(season_lifecycle_service, "live_season_stage", AsyncMock(return_value=None))


def _real_drivers(cog, monkeypatch):
    monkeypatch.setattr(
        season_lifecycle_service,
        "live_season_stage",
        AsyncMock(return_value=(3, SeasonStage.CONFIGURATION)),
    )
    _not_in_test_mode(cog, monkeypatch)
    monkeypatch.setattr(cog_module, "count_live_real_drivers", AsyncMock(return_value=2))


def _nothing_left(cog, monkeypatch):
    monkeypatch.setattr(cog_module, "get_next_pending_phase", AsyncMock(return_value=None))


def _submission_open(status: str):
    """Round 2 of Pro waiting on its results, its submission channel open, in *status*."""
    def setup(cog, monkeypatch):
        entry = {
            "phase_number": 4,
            "round_number": 2,
            "round_id": 12,
            "division_name": "Pro",
            "track_name": "Monza",
        }
        monkeypatch.setattr(cog_module, "get_next_pending_phase", AsyncMock(return_value=entry))
        monkeypatch.setattr(
            result_submission_service, "is_submission_open", AsyncMock(return_value=True)
        )
        monkeypatch.setattr(
            test_mode_service, "round_result_status", AsyncMock(return_value=status)
        )
    return setup


def _former_driver_refused(cog, monkeypatch):
    cog.bot.driver_service.set_former_driver = AsyncMock(
        side_effect=ValueError("No driver profile found for that user.")
    )


def _locked(cog, monkeypatch):
    monkeypatch.setattr(cog_module, "_jobstore_path", lambda bot: "/tmp/does-not-matter.jobs.db")
    monkeypatch.setattr(
        backup_service,
        "save",
        MagicMock(
            side_effect=backup_service.BackupError(
                "the saved backup is locked, so it will not be overwritten. "
                "Unlock it with `/test-mode backup lock` first."
            )
        ),
    )


def _backup(*, exists: bool, readable: bool = True):
    """Makes the saved backup stand as given."""
    def setup(cog, monkeypatch):
        state = SimpleNamespace(
            exists=exists,
            taken_at=datetime(2026, 9, 1, tzinfo=timezone.utc) if exists else None,
            size_bytes=4096 if exists else 0,
            readable=readable,
            locked=False,
            locked_by=None,
        )
        monkeypatch.setattr(backup_service, "state", MagicMock(return_value=state))
    return setup


def _roster_service_refuses(name: str, reason: str):
    def setup(cog, monkeypatch):
        monkeypatch.setattr(test_roster_service, name, AsyncMock(return_value=reason))
    return setup


def _paste_unreadable(cog, monkeypatch):
    monkeypatch.setattr(
        roster_import,
        "parse_roster_csv",
        MagicMock(return_value=([], ["Row 2: no team is named `XYZ`."])),
    )


def _paste_refused_by_the_service(cog, monkeypatch):
    monkeypatch.setattr(
        roster_import, "parse_roster_csv", MagicMock(return_value=([MagicMock()], []))
    )
    monkeypatch.setattr(
        test_roster_service,
        "add_test_drivers_in_bulk",
        AsyncMock(return_value=(0, ["Pro already holds drivers."])),
    )


# ── How a case runs ────────────────────────────────────────────────────────────


def _command(method, *args):
    return lambda cog, i: undecorate(getattr(_Cog, method))(cog, i, *args)


async def _submit_import_form(cog, interaction):
    form = _RosterImportModal(cog)
    form.csv_text = SimpleNamespace(value="ID,Driver name,Team,Division,Nationality")
    await form.on_submit(interaction)


# case id → (the command, or None for the form; how it runs; how the cog refuses; a phrase of
# today's reply)
_CASES = {
    "toggle outside configuration": (
        "test-mode toggle",
        _command("toggle"),
        _no_season_in_configuration,
        "can only be switched while a season is in configuration",
    ),
    "toggle with real drivers": (
        "test-mode toggle",
        _command("toggle"),
        _real_drivers,
        "cannot be enabled while this server has **2** real driver(s)",
    ),
    "nationality outside test mode": (
        "test-mode nationality",
        _command("nationality"),
        _not_in_test_mode,
        "only available when test mode is enabled",
    ),
    "advance outside test mode": (
        "test-mode advance",
        _command("advance"),
        _not_in_test_mode,
        "Test mode is not active",
    ),
    "advance with nothing left": (
        "test-mode advance",
        _command("advance"),
        _nothing_left,
        "There is nothing left to advance",
    ),
    "advance awaiting review": (
        "test-mode advance",
        _command("advance"),
        _submission_open("AWAITING_PENALTY_REVIEW"),
        "is awaiting penalty review approval",
    ),
    "advance with a submission in progress": (
        "test-mode advance",
        _command("advance"),
        _submission_open("FINAL"),
        "is already in progress",
    ),
    "set-former-driver outside test mode": (
        "test-mode set-former-driver",
        _command("set_former_driver", _member(), True),
        _not_in_test_mode,
        "only available when test mode is enabled",
    ),
    "set-former-driver refused by the service": (
        "test-mode set-former-driver",
        _command("set_former_driver", _member(), True),
        _former_driver_refused,
        "No driver profile found for that user.",
    ),
    "backup save outside test mode": (
        "test-mode backup save",
        _command("backup_save"),
        _not_in_test_mode,
        "run only while the server is in **test mode**",
    ),
    "backup save while locked": (
        "test-mode backup save",
        _command("backup_save"),
        _locked,
        "the saved backup is locked",
    ),
    "backup lock outside test mode": (
        "test-mode backup lock",
        _command("backup_lock"),
        _not_in_test_mode,
        "run only while the server is in **test mode**",
    ),
    "backup lock with none saved": (
        "test-mode backup lock",
        _command("backup_lock"),
        _backup(exists=False),
        "There is no saved backup to lock",
    ),
    "backup restore outside test mode": (
        "test-mode backup restore",
        _command("backup_restore"),
        _not_in_test_mode,
        "run only while the server is in **test mode**",
    ),
    "backup restore with none saved": (
        "test-mode backup restore",
        _command("backup_restore"),
        _backup(exists=False),
        "There is no saved backup to restore",
    ),
    "backup restore of an unreadable backup": (
        "test-mode backup restore",
        _command("backup_restore"),
        _backup(exists=True, readable=False),
        "is not a readable database",
    ),
    "roster add-bulk outside test mode": (
        "test-mode roster add-bulk",
        _command("roster_add_bulk"),
        _not_in_test_mode,
        "only available when test mode is enabled",
    ),
    "roster remove outside test mode": (
        "test-mode roster remove",
        _command("roster_remove", "900001"),
        _not_in_test_mode,
        "only available when test mode is enabled",
    ),
    "roster remove of an id that is not a number": (
        "test-mode roster remove",
        _command("roster_remove", "not-a-number"),
        None,
        "`user_id` must be a numeric Discord user ID",
    ),
    "roster remove refused by the service": (
        "test-mode roster remove",
        _command("roster_remove", "900001"),
        _roster_service_refuses("remove_test_driver", "No fake driver has that ID."),
        "No fake driver has that ID.",
    ),
    "roster clear outside test mode": (
        "test-mode roster clear",
        _command("roster_clear", "Pro"),
        _not_in_test_mode,
        "only available when test mode is enabled",
    ),
    "roster clear refused by the service": (
        "test-mode roster clear",
        _command("roster_clear", "Nowhere"),
        _roster_service_refuses("clear_test_drivers", "No division named **Nowhere**."),
        "No division named **Nowhere**.",
    ),
    "import form with an unreadable paste": (
        None,
        _submit_import_form,
        _paste_unreadable,
        "The roster was not imported — 1 problem(s)",
    ),
    "import form refused by the service": (
        None,
        _submit_import_form,
        _paste_refused_by_the_service,
        "The roster was not imported — 1 problem(s)",
    ),
}


@pytest.mark.parametrize("case", sorted(_CASES))
async def test_a_refusal_of_the_test_mode_cog_is_recorded_in_the_log_channel(case, monkeypatch):
    """A league admin the test-mode cog's own checks turn away is answered as today, seen by
    them alone, and one standard line records the refusal; on the import form it names the
    form (#482)."""
    command, run, setup, phrase = _CASES[case]
    cog = _cog()
    if setup is not None:
        setup(cog, monkeypatch)
    interaction = _interaction(cog, command)

    await run(cog, interaction)

    interaction.response.send_modal.assert_not_awaited()
    sent = (
        interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    )
    assert len(sent) == 1 and sent[0].kwargs.get("ephemeral") is True
    reply = sent[0].args[0]
    assert phrase in reply
    what = _IMPORT_FORM if command is None else f"`/{command}`"
    lines = [call.args[0] for call in cog.bot.output_router.post_log.await_args_list]
    assert lines == [f"⛔ {what} refused for Alex (<@{ADMIN_ID}>) — {_reason(reply)}"]


async def test_backup_status_outside_test_mode_records_nothing():
    """`/test-mode backup status` is a view: refused outside test mode, it answers as today
    and writes nothing to the log channel (#482)."""
    cog = _cog()
    _not_in_test_mode(cog, None)
    interaction = _interaction(cog, "test-mode backup status")

    await undecorate(_Cog.backup_status)(cog, interaction)

    interaction.followup.send.assert_awaited_once()
    reply = interaction.followup.send.await_args.args[0]
    assert "run only while the server is in **test mode**" in reply
    assert interaction.followup.send.await_args.kwargs.get("ephemeral") is True
    cog.bot.output_router.post_log.assert_not_awaited()
