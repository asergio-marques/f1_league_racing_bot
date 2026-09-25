"""Every command the bot names in a message is a command the bot actually has.

Issue #195, and the third of its class: #125 sent a manager to `/signup cancel-timer`, and
the backup commands sent a maintainer to `/backup save` — a group that has never existed,
`backup` being a subgroup of `/test-mode`. Both shipped because a refusal's wording was only
ever asserted as a literal, in the test file for its own cog, and a literal asserts that the
message has not changed rather than that what it says is true. Worse, both of the tests
covering the backup replies asserted the *wrong* name, so the suite held the defect in place.

The constitution's Bot Behavior Standards already require an error to "suggest a corrective
action". What was missing was enforcement, so this reads the whole of `src/` at once.

**The rule.** A name in backticks passes if it is a registered command or group, or if it
extends a registered **leaf** command with its arguments — `/module enable weather` names the
leaf `/module enable` and the module it acts on. Extending a *group* is not enough: that is
exactly the shape of `/signup cancel-timer`, where `/signup` exists and the subcommand never
did, and accepting it would let the whole class back in.

**Why string literals and not the file's text.** A comment may name a command deliberately
and correctly say it does not exist — `signup_cog.py` records that the close refusal "used to
name `/signup cancel-timer`" — and `season_cog.py` mentions the path `/tmp`. Both would be
false positives on a raw scan. Docstrings are string literals and are checked, which is
intended: #195 named two of them.

**The whole set is read off the cog classes, with no bot and no gateway**, as
`test_command_tiers.py` does. Importing the cogs builds the groups; a test that needed a live
Discord connection would belong to full system testing and not here.

One limit worth knowing when editing a message: a command name split across an f-string's
interpolation is not seen. Keep a name whole within one literal and it is checked.
"""
from __future__ import annotations

import ast
import importlib
import pkgutil
import re
import sys
from pathlib import Path

import pytest
from discord import app_commands
from discord.ext import commands as discord_commands

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

#: A command as the bot writes it in a message: backticked, slash-led, lowercase words.
#: Trailing words are kept so an argument spelled out in the message can be recognised.
COMMAND_IN_TEXT = re.compile(r"`(/[a-z][a-z0-9-]*(?: [a-z0-9-]+)*)`")

#: Stands in for an f-string's interpolation, so a name cannot be joined across one.
INTERPOLATION = "\x00"


def _walk(command, registered: set[str], leaves: set[str]) -> None:
    registered.add(f"/{command.qualified_name}")
    if isinstance(command, app_commands.Group):
        for child in command.commands:
            _walk(child, registered, leaves)
    else:
        leaves.add(f"/{command.qualified_name}")


def _registered_commands() -> tuple[set[str], set[str]]:
    """Every name the bot answers to, and the subset that takes arguments rather than
    subcommands."""
    import leaguebot

    registered: set[str] = set()
    leaves: set[str] = set()
    for module_info in pkgutil.walk_packages(leaguebot.__path__, "leaguebot."):
        if ".cogs." not in module_info.name:
            continue
        module = importlib.import_module(module_info.name)
        for attribute in dir(module):
            candidate = getattr(module, attribute)
            if (
                isinstance(candidate, type)
                and issubclass(candidate, discord_commands.Cog)
                and candidate is not discord_commands.Cog
            ):
                for command in getattr(candidate, "__cog_app_commands__", []) or []:
                    _walk(command, registered, leaves)
    return registered, leaves


REGISTERED, LEAVES = _registered_commands()


def _strings(tree: ast.AST):
    """Every string literal of a module, f-strings reassembled from their literal parts."""
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            yield node.lineno, "".join(
                part.value if isinstance(part, ast.Constant) else INTERPOLATION
                for part in node.values
            )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def _names_in(path: Path) -> list[tuple[int, str]]:
    """Every command named in a string literal of one source file, with its line."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    found = {
        (line, name)
        for line, text in _strings(tree)
        for name in COMMAND_IN_TEXT.findall(text)
    }
    return sorted(found)


def _exists(name: str) -> bool:
    """Whether the bot has this command, allowing a leaf to be named with its arguments."""
    if name in REGISTERED:
        return True
    words = name.split(" ")
    return any(" ".join(words[:index]) in LEAVES for index in range(1, len(words)))


#: Only the files that name a command at all, so a failure points at one of them.
SOURCES_NAMING_COMMANDS = sorted(
    path for path in SRC.rglob("*.py") if _names_in(path)
)


def test_the_cogs_declare_commands_at_all():
    """A guard on the guard: an import that quietly found nothing would pass everything."""
    assert len(REGISTERED) > 100
    assert "/test-mode backup save" in REGISTERED
    assert "/test-mode backup" in REGISTERED and "/test-mode backup" not in LEAVES


def test_the_source_names_commands_at_all():
    """The other half of that guard: a regex that matched nothing would pass everything."""
    assert len(SOURCES_NAMING_COMMANDS) > 5


@pytest.mark.parametrize(
    "path",
    SOURCES_NAMING_COMMANDS,
    ids=[str(path.relative_to(SRC)) for path in SOURCES_NAMING_COMMANDS],
)
def test_every_command_named_in_a_message_exists(path):
    """The regression test for #195, and for the class #125 belongs to."""
    unknown = [
        f"{path.relative_to(SRC)}:{line} names `{name}`"
        for line, name in _names_in(path)
        if not _exists(name)
    ]
    assert unknown == [], (
        "these name a command the bot does not have, so a manager told to run one finds "
        "nothing:\n  " + "\n  ".join(unknown)
    )


def test_a_subcommand_of_a_real_group_is_not_accepted_on_the_group_alone():
    """What #125 was: `/signup` exists, `/signup cancel-timer` never did.

    Pinned because the natural relaxation — accept anything whose first word is a real
    command — would pass that and let the whole class back in.
    """
    assert _exists("/signup")
    assert not _exists("/signup cancel-timer")


def test_a_leaf_named_with_its_arguments_is_accepted():
    """`/module enable` takes the module as a parameter, and the messages spell it out."""
    assert _exists("/module enable weather")
    assert _exists("/attendance config autosack 0")


def test_the_backup_group_is_only_reachable_through_test_mode():
    """The defect itself: `/backup …` is not a command, however often it was written."""
    assert not _exists("/backup save")
    assert not _exists("/backup lock")
    assert not _exists("/backup status")
    assert not _exists("/backup restore")
