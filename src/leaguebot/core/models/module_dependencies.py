"""The dependency table between modules: plain data, read wherever a module is switched on or
off (`docs/design/architecture.md`, "How modules and core fit together").

`NEEDS` names modules by their folder under `src/leaguebot/` ("image", not "images"), the same
names `.importlinter`'s contracts use, so this table and the import rules cannot drift apart
(`tests/repository/test_import_contracts.py::test_every_module_is_under_the_rules_between_modules`).
A name not in `NEEDS` answers "nothing" rather than raising: `/module` offers "images" where the
table holds "image", and the two are not the same string.

The image module is never an entry: every module may call it without the table saying so.
Stewarding adds a second entry when its module is built [STW-MOD-007].
"""
from __future__ import annotations

from collections.abc import Iterable

#: What each module needs switched on before it may be enabled. A module absent from this table,
#: or any name it does not hold, needs nothing.
NEEDS: dict[str, frozenset[str]] = {
    "attendance": frozenset({"results"}),
}


def needs(module: str) -> frozenset[str]:
    """The modules *module* must have switched on before it may be enabled. Empty for a module
    with no entry, images included."""
    return NEEDS.get(module, frozenset())


def taken_down_with(module: str, enabled: Iterable[str]) -> frozenset[str]:
    """The switched-on modules that *module* being switched off takes down with it.

    Only a module that both needs *module* and is itself among *enabled* is taken down: the
    dependency only bites while the dependant is on."""
    enabled = frozenset(enabled)
    return frozenset(
        dependant for dependant, required in NEEDS.items() if module in required and dependant in enabled
    )
