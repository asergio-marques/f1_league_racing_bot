"""`/backup` and `/test-mode roster` — the commands that only exist while testing.

Issue #208. `test_mode_cog.py` was at 50.3%. These two groups are the ones a maintainer drives
while rehearsing a season, and neither the backup guard nor the roster commands were executed.

**The backup commands copy and replace the whole database, and the test-mode guard is the only
thing standing between that and a running league.** It is deliberately re-read at the moment of
the command rather than trusted from earlier — the flag can be turned off between one command
and the next, and a restore is not something to run on the strength of a stale reading. Every
one of the four is tested for the refusal, individually, because the guard is a call at the top
of each rather than a decorator shared between them: one missing it would let a restore run on
a live league.

**The refusal has to say why, not just no.** It names the risk and the command that enables
test mode, because a maintainer meeting it has usually just forgotten which mode they are in.

**The roster commands report what they did in the league's own terms.** A fake driver is
identified by a numeric Discord id, which is unreadable, so every success message names the
driver and the team instead — `test_removing_a_driver_names_them_and_their_team` holds that.
A non-numeric id is refused with its own message rather than being allowed to fail somewhere
deeper, where the error would name a column instead of the thing the maintainer typed.

`backup_service.state` is stubbed throughout: what is under test is which message each state of
the backup produces, not the file handling underneath, which has its own cover in
`tests/unit/test_backup_service.py`.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# Aliased on import: pytest tries to collect any module-level name starting with `Test`
# as a test class, and warns that it cannot because the cog has an `__init__`.
from cogs.test_mode_cog import TestModeCog as _Cog  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 10008
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_cog(*, test_mode: bool = True, config_missing: bool = False) -> _Cog:
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"
    bot.config_service = MagicMock()
    bot.config_service.get_server_config = AsyncMock(
        return_value=None if config_missing else SimpleNamespace(test_mode_active=test_mode)
    )
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.scheduler_service = MagicMock()

    cog = _Cog.__new__(_Cog)
    cog.bot = bot
    # The stage the roster may change in has tests of its own (test_test_mode_roster_stage).
    cog._refuse_roster_change_outside_placements = AsyncMock(return_value=False)
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Maintainer"
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


def _backup_state(
    *,
    exists: bool = True,
    readable: bool = True,
    locked: bool = False,
    locked_by: str = "someone",
    size_bytes: int = 4096,
):
    return SimpleNamespace(
        exists=exists,
        readable=readable,
        locked=locked,
        locked_by=locked_by,
        size_bytes=size_bytes,
        taken_at=datetime.now(timezone.utc),
    )


#: Every command in the `/backup` group, by the name a maintainer types.
BACKUP_COMMANDS = [
    ("save", _Cog.backup_save),
    ("lock", _Cog.backup_lock),
    ("status", _Cog.backup_status),
    ("restore", _Cog.backup_restore),
]


# ---------------------------------------------------------------------------
# The test-mode guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,command", BACKUP_COMMANDS, ids=[n for n, _ in BACKUP_COMMANDS]
)
async def test_every_backup_command_is_refused_outside_test_mode(name, command):
    """The guard is a call at the top of each command rather than a shared decorator, so
    one missing it would let a restore run on a live league."""
    cog = _make_cog(test_mode=False)
    interaction = _interaction()

    await undecorate(command)(cog, interaction)

    assert "only while the server is in **test mode**" in _replied(interaction)


@pytest.mark.parametrize(
    "name,command", BACKUP_COMMANDS, ids=[n for n, _ in BACKUP_COMMANDS]
)
async def test_a_server_with_no_configuration_is_refused_too(name, command):
    """No configuration means the flag cannot be read, and assuming test mode would be
    assuming the most dangerous of the two states."""
    cog = _make_cog(config_missing=True)
    interaction = _interaction()

    await undecorate(command)(cog, interaction)

    assert "test mode" in _replied(interaction)


async def test_the_refusal_says_why_and_how_to_proceed(tmp_path):
    """A maintainer meeting this has usually just forgotten which mode they are in."""
    cog = _make_cog(test_mode=False)
    interaction = _interaction()

    refused = await cog._refuse_outside_test_mode(interaction)

    assert refused is True
    replied = _replied(interaction)
    assert "whole database" in replied
    assert "/test-mode toggle" in replied


async def test_the_guard_passes_in_test_mode(tmp_path):
    cog = _make_cog(test_mode=True)
    interaction = _interaction()

    assert await cog._refuse_outside_test_mode(interaction) is False
    assert _replied(interaction) == ""


# ---------------------------------------------------------------------------
# /backup status
# ---------------------------------------------------------------------------


async def test_no_backup_says_so_and_names_the_command_that_takes_one():
    cog = _make_cog()
    interaction = _interaction()

    with patch(
        "services.backup_service.state", return_value=_backup_state(exists=False)
    ):
        await undecorate(_Cog.backup_status)(cog, interaction)

    replied = _replied(interaction)
    assert "no saved backup" in replied
    assert "/backup save" in replied


async def test_a_saved_backup_reports_when_it_was_taken_and_how_big():
    cog = _make_cog()
    interaction = _interaction()

    with patch(
        "services.backup_service.state", return_value=_backup_state(size_bytes=8192)
    ):
        await undecorate(_Cog.backup_status)(cog, interaction)

    replied = _replied(interaction)
    assert "Saved backup" in replied
    assert "8 KB" in replied


async def test_an_unreadable_backup_is_called_out_emphatically():
    """A backup that exists but cannot be read is worse than none, because a maintainer
    would otherwise rehearse a season believing they could get back."""
    cog = _make_cog()
    interaction = _interaction()

    with patch(
        "services.backup_service.state", return_value=_backup_state(readable=False)
    ):
        await undecorate(_Cog.backup_status)(cog, interaction)

    assert "it cannot be restored" in _replied(interaction)


async def test_a_readable_backup_says_so_plainly():
    cog = _make_cog()
    interaction = _interaction()

    with patch("services.backup_service.state", return_value=_backup_state(readable=True)):
        await undecorate(_Cog.backup_status)(cog, interaction)

    assert "Readable: yes" in _replied(interaction)


async def test_a_locked_backup_names_who_locked_it():
    """The lock is what stops one maintainer's restore overwriting another's save."""
    cog = _make_cog()
    interaction = _interaction()

    with patch(
        "services.backup_service.state",
        return_value=_backup_state(locked=True, locked_by="Maintainer"),
    ):
        await undecorate(_Cog.backup_status)(cog, interaction)

    assert "Locked: Maintainer" in _replied(interaction)


async def test_an_unlocked_backup_reports_no_lock():
    cog = _make_cog()
    interaction = _interaction()

    with patch("services.backup_service.state", return_value=_backup_state(locked=False)):
        await undecorate(_Cog.backup_status)(cog, interaction)

    assert "Locked: no" in _replied(interaction)


# ---------------------------------------------------------------------------
# /backup restore
# ---------------------------------------------------------------------------


async def test_restoring_with_no_backup_is_refused():
    cog = _make_cog()
    interaction = _interaction()

    with patch(
        "services.backup_service.state", return_value=_backup_state(exists=False)
    ):
        await undecorate(_Cog.backup_restore)(cog, interaction)

    replied = _replied(interaction)
    assert "no saved backup to restore" in replied
    assert "/backup save" in replied


# ---------------------------------------------------------------------------
# /test-mode roster remove
# ---------------------------------------------------------------------------


async def _remove(cog, interaction, user_id: str = "4242"):
    await undecorate(_Cog.roster_remove)(cog, interaction, user_id)


async def test_the_roster_commands_are_refused_outside_test_mode():
    cog = _make_cog(test_mode=False)
    interaction = _interaction()

    await _remove(cog, interaction)

    assert "only available when test mode is enabled" in _replied(interaction)


async def test_a_non_numeric_user_id_is_refused_in_its_own_words():
    """Refused here rather than deeper, where the error would name a column instead of the
    thing the maintainer typed."""
    cog = _make_cog()
    interaction = _interaction()

    await _remove(cog, interaction, user_id="not-a-number")

    assert "must be a numeric Discord user ID" in _replied(interaction)


async def test_removing_a_driver_names_them_and_their_team():
    """A fake driver is identified by a numeric id, which is unreadable — the message has
    to name the driver and the team or the maintainer cannot tell what went."""
    cog = _make_cog()
    interaction = _interaction()

    with patch(
        "services.test_roster_service.remove_test_driver",
        new=AsyncMock(return_value={"display_name": "Test Lewis", "team_name": "Alpha"}),
    ):
        await _remove(cog, interaction)

    replied = _replied(interaction)
    assert "Test Lewis" in replied
    assert "Alpha" in replied


async def test_a_refusal_from_the_roster_service_reaches_the_maintainer():
    """The service returns a string to refuse, which the command has to recognise as a
    refusal rather than treat as a result."""
    cog = _make_cog()
    interaction = _interaction()

    with patch(
        "services.test_roster_service.remove_test_driver",
        new=AsyncMock(return_value="No test driver with that id."),
    ):
        await _remove(cog, interaction)

    assert "No test driver with that id." in _replied(interaction)
    cog.bot.output_router.post_log.assert_not_awaited()


async def test_a_successful_removal_is_logged_with_the_id_as_well_as_the_name():
    """The id is what a maintainer would need to add the driver back, and it is the one
    thing the readable message deliberately leaves out."""
    cog = _make_cog()
    interaction = _interaction()

    with patch(
        "services.test_roster_service.remove_test_driver",
        new=AsyncMock(return_value={"display_name": "Test Lewis", "team_name": "Alpha"}),
    ):
        await _remove(cog, interaction, user_id="4242")

    logged = cog.bot.output_router.post_log.await_args.args[1]
    assert "roster remove" in logged
    assert "4242" in logged
    assert "Test Lewis" in logged
