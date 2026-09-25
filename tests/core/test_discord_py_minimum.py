"""The bot refuses to start on a discord.py older than it needs (#381).

`/team add` and `/team modify` take their input in a modal carrying a role picker, which needs
`discord.ui.Label` — new in discord.py 2.6. A host that installed discord.py from apt rather than
into a virtualenv has 2.5, and without this would fail inside the team cog with an AttributeError
naming no cause.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import leaguebot.__main__ as bot_module  # noqa: E402


def test_the_installed_discord_py_meets_the_minimum():
    """Which is what lets every other test import the team cog at all."""
    bot_module.require_discord_py()


def test_an_older_discord_py_is_refused_saying_what_to_do():
    with pytest.raises(RuntimeError) as refusal:
        bot_module.require_discord_py((2, 5, 0, "final", 0))

    assert "2.6 or later" in str(refusal.value)
    assert "2.5" in str(refusal.value)
    assert "requirements.txt" in str(refusal.value)


@pytest.mark.parametrize("version", [(2, 6, 0, "final", 0), (2, 7, 1, "final", 0), (3, 0)])
def test_the_minimum_and_anything_newer_stand(version):
    bot_module.require_discord_py(version)
