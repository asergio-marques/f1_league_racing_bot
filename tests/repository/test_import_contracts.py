"""Which code may import which, checked by import-linter (#282).

The import rules of `docs/design/architecture.md` are written as contracts in `.importlinter`
at the repository root: nothing below the cogs imports a cog, models use no Discord and no
database, the database code imports nothing else of the bot's, and utilities use no services;
core uses no module, a module uses another only where the dependency table allows, and nothing
imports the entry point. Running them here, rather than as a CI step of their own, means the ordinary test run checks
them on every host (decided with the plan for #282).

import-linter reads the import statements of the package, `leaguebot`, without running them, and counts an import
made inside a function or under `TYPE_CHECKING` like any other: both are still a dependency.
Where today's code breaks a rule, the import is listed in `.importlinter` with the issue that
will remove it, and a listed import that no longer exists fails this test, so the lists only
shrink. The rules an import graph cannot see are in `test_architecture_rules.py`.
"""
from __future__ import annotations

from pathlib import Path

from importlinter import configuration
from importlinter.application.use_cases import lint_imports

CONTRACTS = Path(__file__).resolve().parents[2] / ".importlinter"


def test_the_import_contracts_hold(capsys):
    """No cache is written: the check reads `src/` afresh on each run and leaves nothing
    behind in the checkout.

    `configure()` is what import-linter's own command line does before linting; called
    directly, `lint_imports` has no way to read the file without it.
    """
    configuration.configure()
    kept = lint_imports(config_filename=str(CONTRACTS), cache_dir=None, no_logo=True)
    report = capsys.readouterr().out
    assert kept, report
