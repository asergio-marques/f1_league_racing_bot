"""Every command of the bot sits in one tier, and this is the register of which.

Issue #116 found 147 of the bot's commands at the wrong level of authority, and nothing in
the suite noticed because a command's permission was only ever asserted one command at a
time, in the test file for its own cog, when somebody remembered. This asserts all of them
at once.

**Why the league admin commands are named and the league managers are not.** The core
specification says "Where this specification does not state a tier, the command is a league
manager's", and this table is shaped the same way. A new command therefore passes by being a
league manager's, which is the right default, and fails the moment it claims the higher tier
without that being written down here.

A command carrying no tier guard at all fails too — `TIER_ATTRIBUTE` is absent — so nothing
can ship unguarded either.

**The whole set is read off the classes, with no bot and no gateway.** `__cog_app_commands__`
is built by `CogMeta` when the class body is read, so importing the cogs is enough; a test
that needed a live Discord connection would belong to full system testing and not here.
"""
from __future__ import annotations

import importlib
import os
import pkgutil
import sys

import pytest
from discord import app_commands
from discord.ext import commands as discord_commands

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.channel_guard import (  # noqa: E402
    CHANNEL_EXEMPT_ATTRIBUTE,
    LEAGUE_ADMIN,
    LEAGUE_MANAGER,
    TIER_ATTRIBUTE,
)

#: A league admin's command, given in the interaction channel.
ADMIN = "league admin"

#: A league admin's command, exempt from the interaction channel because it is one of the
#: five that set the bot up — and reachable by Discord's Administrator permission as well as
#: by the role, for the same reason.
SETUP = "league admin, any channel"

#: Every command that is **not** a league manager's, and why. Anything absent is a league
#: manager's, which is the default the core specification states.
LEAGUE_ADMIN_COMMANDS: dict[str, str] = {
    # The bot upon the server. These five repair the settings every other guard reads.
    "bot-init": SETUP,
    "bot-admin-role": SETUP,
    "bot-interaction-role": SETUP,
    "bot-interaction-channel": SETUP,
    "bot-log-channel": SETUP,
    # Starting over, and deleting the bot's own messages.
    "bot-reset": ADMIN,
    "clean-bot": ADMIN,
    # Arming and disarming a module server-wide.
    "module enable": ADMIN,
    "module disable": ADMIN,
    # Destroying what a league is built from, where nothing puts it back.
    "season cancel": ADMIN,
    "season complete": ADMIN,
    "division cancel": ADMIN,
    "division delete": ADMIN,
    "round cancel": ADMIN,
    "round delete": ADMIN,
    "team remove": ADMIN,
    "driver sack": ADMIN,
    # The same rule, on three commands the issue itself had at the lower tier.
    # `/round results amend` overwrites a FINAL round's classification in place — it does
    # not supersede, whatever the comment in `amend_session_results` used to claim.
    "round results amend": ADMIN,
    # Approval overwrites the season's points entire; the rest of `/results amend` writes
    # only the modification store, which `revert` discards.
    "results amend review": ADMIN,
    # Deletes a points configuration outright, attached to the standing season or not.
    "results config remove": ADMIN,
    # The whole of test mode.
    "test-mode toggle": ADMIN,
    "test-mode advance": ADMIN,
    "test-mode review": ADMIN,
    "test-mode nationality": ADMIN,
    "test-mode set-former-driver": ADMIN,
    "test-mode roster add": ADMIN,
    "test-mode roster add-bulk": ADMIN,
    "test-mode roster clear": ADMIN,
    "test-mode roster list": ADMIN,
    "test-mode roster remove": ADMIN,
    "test-mode rsvp set-status": ADMIN,
    "test-mode backup save": ADMIN,
    "test-mode backup lock": ADMIN,
    "test-mode backup restore": ADMIN,
    "test-mode backup status": ADMIN,
}


def _walk(objects):
    for obj in objects:
        if isinstance(obj, app_commands.Group):
            yield from _walk(obj.commands)
        else:
            yield obj


def _every_command() -> dict[str, app_commands.Command]:
    """Every app command the bot declares, by its full name, off the cog classes."""
    import cogs

    found: dict[str, app_commands.Command] = {}
    for module_info in pkgutil.iter_modules(cogs.__path__):
        module = importlib.import_module(f"cogs.{module_info.name}")
        for attribute in dir(module):
            candidate = getattr(module, attribute)
            if (
                isinstance(candidate, type)
                and issubclass(candidate, discord_commands.Cog)
                and candidate is not discord_commands.Cog
            ):
                for command in _walk(getattr(candidate, "__cog_app_commands__", []) or []):
                    found[command.qualified_name] = command
    return found


COMMANDS = _every_command()


def test_the_cogs_declare_commands_at_all():
    """A guard on the guard: an import that quietly found nothing would pass everything."""
    assert len(COMMANDS) > 100


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_every_command_declares_a_tier(name):
    """No command ships unguarded, whatever else is true of it."""
    tier = getattr(COMMANDS[name].callback, TIER_ATTRIBUTE, None)
    assert tier in (LEAGUE_ADMIN, LEAGUE_MANAGER), (
        f"/{name} carries no tier guard — it is reachable by anybody, in any channel"
    )


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_every_command_sits_at_the_tier_the_register_gives_it(name):
    callback = COMMANDS[name].callback
    expected = LEAGUE_ADMIN_COMMANDS.get(name)
    tier = getattr(callback, TIER_ATTRIBUTE, None)
    exempt = getattr(callback, CHANNEL_EXEMPT_ATTRIBUTE, None)

    if expected is None:
        assert tier == LEAGUE_MANAGER, (
            f"/{name} is a league admin's in the code and a league manager's here. Either "
            f"the guard is wrong, or the register needs the command adding to it — the "
            f"second is a decision about who may run it, not a test to update."
        )
        assert exempt is False, f"/{name} is exempt from the interaction channel"
    else:
        assert tier == LEAGUE_ADMIN, f"/{name} should be a league admin's"
        assert exempt is (expected is SETUP), (
            f"/{name} is {'not ' if expected is SETUP else ''}exempt from the interaction "
            f"channel and should be the other way about"
        )


def test_the_register_names_no_command_that_does_not_exist():
    """A renamed or withdrawn command leaves its entry behind, and the entry says nothing."""
    missing = sorted(set(LEAGUE_ADMIN_COMMANDS) - set(COMMANDS))
    assert missing == [], f"the register names commands the bot does not have: {missing}"


def test_only_the_five_setup_commands_run_outside_the_interaction_channel():
    """Every other command of either tier is given in the interaction channel."""
    exempt = sorted(
        name
        for name, command in COMMANDS.items()
        if getattr(command.callback, CHANNEL_EXEMPT_ATTRIBUTE, False)
    )
    assert exempt == [
        "bot-admin-role",
        "bot-init",
        "bot-interaction-channel",
        "bot-interaction-role",
        "bot-log-channel",
    ]


def test_the_tiers_divide_as_the_register_says():
    """The headline split, so a wholesale drift shows up as one failure rather than many.

    The totals are not asserted: a new command is expected to arrive as a league manager's
    and should not have to edit this file to do it. What is asserted is that the two tiers
    account for every command between them, and that the higher one holds exactly what the
    register above names.
    """
    admin = {
        name
        for name, command in COMMANDS.items()
        if getattr(command.callback, TIER_ATTRIBUTE, None) == LEAGUE_ADMIN
    }
    manager = {
        name
        for name, command in COMMANDS.items()
        if getattr(command.callback, TIER_ATTRIBUTE, None) == LEAGUE_MANAGER
    }

    assert admin == set(LEAGUE_ADMIN_COMMANDS)
    assert admin | manager == set(COMMANDS)
    assert admin & manager == set()
