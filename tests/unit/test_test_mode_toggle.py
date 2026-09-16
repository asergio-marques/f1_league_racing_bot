"""`/test-mode toggle` — the four guards around switching a server into and out of test mode.

Issue #208. The command itself is one flag flip; almost all of it is the guards, and each one
records a way the bot went wrong before it existed.

**Test mode may not be switched on over a real league.** Disabling it again deletes every fake
driver without confirmation, and while it is on the signup and placement paths refuse real
drivers outright — so a league that enabled it mid-season would find itself unable to run and
one flag flip away from losing its roster.

**Nor while a signup window is open.** `/signup open` will not run under test mode, and it would
be inconsistent to leave a window already open — one whose button is posted and pinging the base
role — silently rejecting every driver who pressed it. The window is deliberately *not* closed
here: closing posts a public notice in a channel a league reads, which is not something a flag
flip should do. `test_an_open_window_is_refused_rather_than_closed` holds both halves of that.

**Leaving is refused in exactly one case**, and it is the one that used to break. A season that
has started while holding fake drivers keeps test mode on until it is completed: disabling
deletes every fake driver, and one that has raced cannot be deleted — the check-in, the
standings and the season's end each write a row carrying a foreign key to the profile. The
delete therefore raised *after* the flag had already been flipped, stranding the server out of
test mode with its roster still seated and no reply sent. The guard runs before the flip for
exactly that reason.

**Every refusal names a number and a way out.** A maintainer told only "cannot" has to go
looking for which driver or which season is in the way.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# Aliased on import: pytest tries to collect any module-level name starting with `Test`
# as a test class, and warns that it cannot because the cog has an `__init__`.
from cogs.test_mode_cog import TestModeCog as _Cog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 11508
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    """A migrated database with one server.

    The success path reads `seasons` to seed the default point configurations, so the
    guards alone cannot be tested against a path that is not a database.
    """
    db_path = os.path.join(str(tmp_path), "test_mode.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.commit()
    return db_path


def _make_cog(db_path: str, *, test_mode: bool = False, config_missing: bool = False) -> _Cog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.config_service = MagicMock()
    bot.config_service.get_server_config = AsyncMock(
        return_value=None if config_missing else SimpleNamespace(test_mode_active=test_mode)
    )
    bot.signup_module_service = MagicMock()
    bot.signup_module_service.get_window_state = AsyncMock(return_value=False)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = _Cog.__new__(_Cog)
    cog.bot = bot
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


def _guards(*, real_drivers: int = 0, seated: int = 0, new_state: bool = True):
    """Patch the three counters and the flip, returning the flip mock."""
    return (
        patch(
            "cogs.test_mode_cog.count_live_real_drivers",
            new=AsyncMock(return_value=real_drivers),
        ),
        patch(
            "cogs.test_mode_cog.count_test_drivers_in_a_started_season",
            new=AsyncMock(return_value=seated),
        ),
        patch(
            "cogs.test_mode_cog.toggle_test_mode",
            new=AsyncMock(return_value=new_state),
        ),
    )


async def _toggle(cog, interaction, **kwargs):
    real, seated, flip = _guards(**kwargs)
    with real, seated, flip as flip_mock, patch(
        "services.test_roster_service.ensure_test_configs", new=AsyncMock(return_value=None)
    ):
        await undecorate(_Cog.toggle)(cog, interaction)
    return flip_mock


# ---------------------------------------------------------------------------
# Entering test mode
# ---------------------------------------------------------------------------


async def test_a_server_with_no_real_drivers_may_enter_test_mode(tmp_path):
    cog = _make_cog(await _make_db(tmp_path), test_mode=False)
    interaction = _interaction()

    flip = await _toggle(cog, interaction, real_drivers=0)

    flip.assert_awaited_once()


async def test_a_league_with_real_drivers_may_not_enter_test_mode(tmp_path):
    """Disabling again deletes every fake driver without confirmation, and while it is on
    no real driver may sign up or be placed — a league that enabled it mid-season would
    find itself unable to run."""
    cog = _make_cog(await _make_db(tmp_path), test_mode=False)
    interaction = _interaction()

    flip = await _toggle(cog, interaction, real_drivers=12)

    flip.assert_not_awaited()
    assert "12" in _replied(interaction)


async def test_the_refusal_says_why_test_mode_is_for_an_empty_league(tmp_path):
    """A maintainer told only "cannot" would try again tomorrow."""
    cog = _make_cog(await _make_db(tmp_path), test_mode=False)
    interaction = _interaction()

    await _toggle(cog, interaction, real_drivers=1)

    replied = _replied(interaction)
    assert "empty league" in replied
    assert "deletes every fake" in replied


async def test_an_open_window_is_refused_rather_than_closed(tmp_path):
    """Closing posts a public notice in a channel a league reads, which is not something
    a flag flip should do — so the command refuses and names the command that closes it."""
    cog = _make_cog(await _make_db(tmp_path), test_mode=False)
    cog.bot.signup_module_service.get_window_state = AsyncMock(return_value=True)
    interaction = _interaction()

    flip = await _toggle(cog, interaction)

    flip.assert_not_awaited()
    replied = _replied(interaction)
    assert "signups are open" in replied
    assert "/signup close" in replied


async def test_the_open_window_refusal_says_what_would_have_happened(tmp_path):
    """The button is posted and pinging the base role; under test mode it would refuse
    everyone who pressed it."""
    cog = _make_cog(await _make_db(tmp_path), test_mode=False)
    cog.bot.signup_module_service.get_window_state = AsyncMock(return_value=True)
    interaction = _interaction()

    await _toggle(cog, interaction)

    assert "refuse everyone who pressed it" in _replied(interaction)


async def test_real_drivers_are_checked_before_the_signup_window(tmp_path):
    """A server with both is told about the drivers, which is the harder of the two to
    put right — closing a window is one command, emptying a league is not."""
    cog = _make_cog(await _make_db(tmp_path), test_mode=False)
    cog.bot.signup_module_service.get_window_state = AsyncMock(return_value=True)
    interaction = _interaction()

    await _toggle(cog, interaction, real_drivers=3)

    assert "real driver" in _replied(interaction)
    assert "signups are open" not in _replied(interaction)


# ---------------------------------------------------------------------------
# Leaving test mode
# ---------------------------------------------------------------------------


async def test_test_mode_may_be_left_when_no_season_has_raced(tmp_path):
    cog = _make_cog(await _make_db(tmp_path), test_mode=True)
    interaction = _interaction()

    flip = await _toggle(cog, interaction, seated=0, new_state=False)

    flip.assert_awaited_once()


async def test_a_running_season_holding_fake_drivers_keeps_test_mode_on(tmp_path):
    """The case that used to strand a server: the delete raised *after* the flag had been
    flipped, leaving the server out of test mode with its roster still seated and no reply
    sent. The guard runs before the flip for exactly that reason."""
    cog = _make_cog(await _make_db(tmp_path), test_mode=True)
    interaction = _interaction()

    flip = await _toggle(cog, interaction, seated=8, new_state=False)

    flip.assert_not_awaited()
    assert "8" in _replied(interaction)


async def test_the_refusal_explains_why_a_raced_driver_cannot_be_deleted(tmp_path):
    """The check-in, the standings and the season's end each carry a foreign key to the
    profile — which a maintainer cannot be expected to infer."""
    cog = _make_cog(await _make_db(tmp_path), test_mode=True)
    interaction = _interaction()

    await _toggle(cog, interaction, seated=1, new_state=False)

    replied = _replied(interaction)
    assert "cannot be deleted" in replied
    assert "/season complete" in replied


async def test_the_seated_check_does_not_run_on_the_way_in(tmp_path):
    """It asks about fake drivers in a started season, which is meaningless for a server
    that is not in test mode yet — and running it would refuse an entry for a reason
    belonging to the exit."""
    cog = _make_cog(await _make_db(tmp_path), test_mode=False)
    interaction = _interaction()

    flip = await _toggle(cog, interaction, seated=8)

    flip.assert_awaited_once()


async def test_the_real_driver_check_does_not_run_on_the_way_out(tmp_path):
    """A server in test mode holds fake drivers by definition; counting real ones would
    refuse the exit for having done exactly what test mode is for."""
    cog = _make_cog(await _make_db(tmp_path), test_mode=True)
    interaction = _interaction()

    flip = await _toggle(cog, interaction, real_drivers=12, seated=0, new_state=False)

    flip.assert_awaited_once()


# ---------------------------------------------------------------------------
# A server that has never been configured
# ---------------------------------------------------------------------------


async def test_a_server_with_no_configuration_still_toggles(tmp_path):
    """`/bot-init` writes the configuration, and a maintainer may reasonably turn test
    mode on before running it — there is no league to protect yet."""
    cog = _make_cog(await _make_db(tmp_path), config_missing=True)
    interaction = _interaction()

    flip = await _toggle(cog, interaction)

    flip.assert_awaited_once()
