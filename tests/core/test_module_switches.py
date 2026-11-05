"""The modules' switches as plain data: the dependency table, and the kinds of the changes that
turn a module on and off.

`docs/design/architecture.md`, "How modules and core fit together", writes the dependency table
once, in `core/models/`, to be read wherever it matters: by `/module enable`'s refusal of a module
whose need is off, and by `/module disable`'s cascade and its warning. It starts with one entry,
attendance needing results. The image module is never an entry, since every module may call it.
A switch is the module's own change, `module.on:<name>` or `module.off:<name>`.

The command's reading of the table is pinned beside the command's own tests:
`test_module_enable_disable.py` for the refusal, `test_results_disable_cascade.py` for the
cascade and its warning.
"""
from __future__ import annotations

import pytest

from leaguebot.core.cogs.module_cog import _MODULE_CHOICES
from leaguebot.core.models.change import module_off

#: Every module `/module enable` and `/module disable` offer, by the name the command gives it.
OFFERED = tuple(choice.value for choice in _MODULE_CHOICES)


def test_the_dependency_table_answers_what_a_switch_needs_and_takes_down():
    """Attendance needs results and no other module needs another, images included. A switch-off
    of results takes attendance down with it where attendance is on and not where it is off, and
    a switch-off of any other module takes nothing with it."""
    from leaguebot.core.models.module_dependencies import needs, taken_down_with

    assert {module: set(needs(module)) for module in OFFERED} == {
        "weather": set(),
        "signup": set(),
        "results": set(),
        "attendance": {"results"},
        "images": set(),
    }
    everything_on = frozenset(OFFERED)
    assert {module: set(taken_down_with(module, everything_on)) for module in OFFERED} == {
        "weather": set(),
        "signup": set(),
        "results": {"attendance"},
        "attendance": set(),
        "images": set(),
    }
    assert set(taken_down_with("results", everything_on - {"attendance"})) == set()


def test_a_module_is_switched_on_and_off_by_kinds_naming_it():
    """Every module /module offers is switched on by `module.on:<name>` and off by
    `module.off:<name>`."""
    from leaguebot.core.models.change import module_on

    assert {module: (module_on(module), module_off(module)) for module in OFFERED} == {
        module: (f"module.on:{module}", f"module.off:{module}") for module in OFFERED
    }
