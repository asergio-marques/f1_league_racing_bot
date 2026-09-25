"""Which code may import which, checked by import-linter (#282).

The import rules of `docs/design/architecture.md` are written as contracts in `.importlinter` at the
repository root: nothing below the cogs imports a cog, models use no Discord and no database, the
database code imports nothing else of the bot's, and utilities use no services; core uses no module,
a module uses another only where the dependency table allows, and nothing imports the entry point.
Running them here, rather than as a CI step of their own, means the ordinary test run checks them on
every host (decided with the plan for #282).

import-linter reads the import statements of the package, `leaguebot`, without running them, and
counts an import made inside a function or under `TYPE_CHECKING` like any other: both are still a
dependency. Where today's code breaks a rule, the import is listed in `.importlinter` with the issue
that will remove it, and a listed import that no longer exists fails this test, so the lists only
shrink. The rules an import graph cannot see are in `test_architecture_rules.py`.

The tests after the first hold what the contracts can see: a module's folder they do not name,
a file outside the kind folders they name by wildcard, and a run-time import behind their one
permanent exception would each pass them unnoticed.
"""
from __future__ import annotations

import ast
import configparser
import re
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


# ── What the contracts can see ──────────────────────────────────────────────────────────────

PACKAGE = CONTRACTS.parent / "src" / "leaguebot"

#: The kinds of code a module's folder holds, each in a folder of its own (architecture.md, "How
#: the code is laid out"). The contracts name code by these folders.
KINDS = frozenset({"cogs", "services", "models", "utils"})


def _contracts() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(CONTRACTS, encoding="utf-8")
    return parser


def _named(section: str, option: str) -> set[str]:
    """The modules a contract lists under *option*, without the package's name."""
    return {
        line.strip().removeprefix("leaguebot.")
        for line in _contracts()[f"importlinter:contract:{section}"][option].splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def _modules() -> set[str]:
    """Every module's folder under `src/leaguebot/`, core's aside."""
    return {path.name for path in PACKAGE.iterdir() if (path / "__init__.py").is_file()} - {"core"}


#: The name of a module's own contract, which states the modules it may use: its row of the
#: dependency table (architecture.md, "How modules and core fit together").
_MODULE_CONTRACT = re.compile(
    r"(?P<module>[a-z]+)-uses-no-other-module(?:-but-(?P<allowed>[a-z-]+))?"
)


def test_every_module_is_under_the_rules_between_modules():
    """The rules between modules name each module by hand, so a module's folder the contracts do
    not name would be under none of them: core could use it, and it could use anything. Adding a
    module adds it to `.importlinter`, and to the dependency table there. Each module's contract
    says in its name which modules it may use, and forbids exactly the rest, so a module left
    out of another's forbidden list is found here."""
    modules = _modules()

    assert _named("core-uses-no-module", "forbidden_modules") == modules
    assert _named("nothing-imports-the-entry-point", "source_modules") == modules | {"core"}
    rows = {}
    for section in _contracts().sections():
        match = _MODULE_CONTRACT.fullmatch(section.removeprefix("importlinter:contract:"))
        if match is None:
            continue
        module, allowed = match["module"], set((match["allowed"] or "").split("-and-")) - {""}
        assert _named(match[0], "source_modules") == {module}
        assert _named(match[0], "forbidden_modules") == modules - {module} - allowed, match[0]
        rows[module] = allowed
    assert set(rows) == modules, "Every module has a contract saying which modules it may use."


def test_every_folder_of_the_package_is_a_package():
    """import-linter reads only regular packages: a folder with no `__init__.py`, and every file
    beneath it, is left out of the import graph, and every contract passes without reading it."""
    missing = sorted(
        {
            folder.as_posix()
            for path in PACKAGE.rglob("*.py")
            for folder in path.relative_to(PACKAGE).parents
            if not (PACKAGE / folder / "__init__.py").is_file()
        }
    )
    assert missing == []


def _placed(parts: tuple[str, ...]) -> bool:
    if len(parts) == 1:
        return parts[0] in ("__init__.py", "__main__.py")
    if len(parts) == 2:
        return parts[1] == "__init__.py"
    return parts[1] in KINDS or parts[:2] == ("core", "db")


def test_every_file_sits_in_a_folder_the_contracts_name():
    """A file outside its module's folders for each kind would escape every contract naming a
    kind (`leaguebot.*.services` and the rest), and a kind folder of a new name likewise."""
    misplaced = sorted(
        path.relative_to(PACKAGE).as_posix()
        for path in PACKAGE.rglob("*.py")
        if not _placed(path.relative_to(PACKAGE).parts)
    )
    assert misplaced == []


def test_the_bot_s_type_names_the_services_for_the_type_checker_only():
    """The one permanent exception in `.importlinter` lets the bot's type import every service,
    the modules' among them, because it does so under `TYPE_CHECKING` and nothing runs because of
    it (architecture.md, "How the code is laid out"). Imported at run time, core would load the
    modules' code the moment anything imports the bot's type."""
    tree = ast.parse((PACKAGE / "core" / "utils" / "league_bot.py").read_text(encoding="utf-8"))
    guarded = {
        id(inner)
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) in ("TYPE_CHECKING", "typing.TYPE_CHECKING")
        for inner in ast.walk(node)
    }
    unguarded = sorted(
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and ".services" in (node.module or "")
        and id(node) not in guarded
    )
    assert unguarded == []
