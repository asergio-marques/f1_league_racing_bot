"""Which code may import which, checked by import-linter (#282).

The import rules of `docs/design/architecture.md` are written as contracts in `.importlinter` at the
repository root: nothing below the cogs imports a cog, models use no Discord and no database, the
database code imports nothing else of the bot's, and utilities use no services; `utils` imports
nothing of the bot, the framework imports nothing of core or of any module, core uses no module, a
module uses another only where the dependency table allows, and nothing imports the entry point.
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

import pytest
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
#: The layers below core and the modules: `utils`, which imports nothing of the bot, and the
#: framework, which imports nothing of core or of any module (architecture.md, "How the code is laid
#: out"). Neither is a module: neither has a switch or a wiring.
LAYERS = frozenset({"utils", "framework"})
#: A module's part of the builder, one file beside its kind folders and under the entry point's
#: rules, imported by the entry point alone (architecture.md, "How the code is laid out"). Core has
#: none: its part is the builder's own.
WIRING = "wiring.py"
#: The contract that keeps every module's wiring to the entry point.
WIRING_CONTRACT = "nothing-imports-a-module-s-wiring-but-the-entry-point"


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
    """Every module's folder under `src/leaguebot/`, core's and the layers' aside."""
    return {
        path.name for path in PACKAGE.iterdir() if (path / "__init__.py").is_file()
    } - {"core"} - LAYERS


#: The name of a module's own contract, which states the modules it may use: its row of the
#: dependency table (architecture.md, "How modules and core fit together").
_MODULE_CONTRACT = re.compile(
    r"(?P<module>[a-z]+)-uses-no-other-module(?:-but-(?P<allowed>[a-z-]+))?"
)


def test_every_module_is_under_the_rules_between_modules():
    """The rules between modules name each module by hand, so a module's folder the contracts do
    not name would be under none of them: core could use it, and it could use anything. Adding a
    module adds it to `.importlinter`. Each module's contract says in its name which modules it
    may use, and forbids exactly the rest, so a module left out of another's forbidden list is
    found here.

    The modules a contract allows are the module's row of the dependency table in
    `core/models/module_dependencies.py`, with the image module besides, which every module may
    call without an entry, so the contracts and the table the switches read cannot drift apart."""
    from leaguebot.core.models.module_dependencies import needs

    modules = _modules()

    assert _named("core-uses-no-module", "forbidden_modules") == modules
    assert _named("nothing-imports-the-entry-point", "source_modules") == modules | {"core"} | LAYERS
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
    assert rows["image"] == set()
    assert {module: allowed for module, allowed in rows.items() if module != "image"} == {
        module: set(needs(module)) | {"image"} for module in rows if module != "image"
    }, "Each module's contract allows the modules the dependency table says it needs, and image."


@pytest.mark.parametrize(
    ("contract", "layer", "also_forbidden"),
    [
        pytest.param("utils-imports-nothing-of-the-bot", "utils", {"framework"}, id="utils"),
        pytest.param("the-framework-uses-no-league-rule", "framework", set(), id="framework"),
    ],
)
def test_the_layers_below_core_use_no_league_rule(contract, layer, also_forbidden):
    """Each layer's contract forbids exactly core, every module and the entry point, and for `utils`
    the framework too (architecture.md, "How the code is laid out"). The modules are named by hand,
    so a module added to the package and left out of a layer's contract would be one the layer could
    import: it is found here."""
    assert _named(contract, "source_modules") == {layer}
    assert _named(contract, "forbidden_modules") == (
        _modules() | {"core", "__main__"} | also_forbidden
    )


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
    if parts[0] == "utils":
        return len(parts) == 2 and parts[1].removesuffix(".py") not in KINDS | {"db", "wiring"}
    if len(parts) == 2:
        return parts[1] == "__init__.py" or (
            parts[1] == WIRING and parts[0] not in {"core"} | LAYERS
        )
    return parts[1] in KINDS or parts[:2] == ("framework", "db")


@pytest.mark.parametrize(
    ("parts", "placed"),
    [
        pytest.param(("results", "wiring.py"), True, id="a module's wiring"),
        pytest.param(("core", "wiring.py"), False, id="core's"),
        pytest.param(("results", "helpers.py"), False, id="another file beside the kinds"),
        pytest.param(("utils", "time_parsing.py"), True, id="a helper in utils"),
        pytest.param(("utils", "text", "messages.py"), False, id="a folder in utils"),
        pytest.param(("utils", "services.py"), False, id="a helper in utils named for a kind"),
        pytest.param(("utils", "wiring.py"), False, id="a helper in utils named for the wiring"),
        pytest.param(
            ("framework", "services", "queue", "change_queue.py"), True, id="a framework service"
        ),
        pytest.param(("framework", "db", "database.py"), True, id="the framework's database code"),
        pytest.param(("framework", "wiring.py"), False, id="the framework's wiring"),
    ],
)
def test_files_sit_where_their_part_or_layer_places_them(parts, placed):
    """A module's wiring is the one file a module keeps outside its kind folders; core and the
    framework keep none, and any other file there is out of place. The framework holds its kind
    folders as a part does, and its database code in `db/`; `utils` holds its helpers directly, one
    file to a topic, with no folders, and none named for a kind or the wiring, whose dotted name the
    contracts' `leaguebot.*.<kind>` would take for a kind folder."""
    assert _placed(parts) is placed


def test_every_module_has_its_wiring_and_only_the_entry_point_imports_it():
    """Each module's wiring signs it up as the builder asks, and nothing but the entry point
    imports one: not a module's own services, cogs, models or utilities, not core, and not another
    module's wiring. A source module the contract names is a kind folder, not the module, as
    import-linter passes over an import between a module and what lies within it."""
    modules = _modules()

    assert sorted(module for module in modules if not (PACKAGE / module / WIRING).is_file()) == []
    assert _named(WIRING_CONTRACT, "forbidden_modules") == {"*.wiring"}
    assert _named(WIRING_CONTRACT, "source_modules") == {f"*.{kind}" for kind in KINDS} | {
        "*.wiring",
        "*.db",
    }


def test_every_file_sits_in_a_folder_the_contracts_name():
    """A file outside its module's folders for each kind would escape every contract naming a
    kind (`leaguebot.*.services` and the rest), and a kind folder of a new name likewise."""
    misplaced = sorted(
        path.relative_to(PACKAGE).as_posix()
        for path in PACKAGE.rglob("*.py")
        if not _placed(path.relative_to(PACKAGE).parts)
    )
    assert misplaced == []


#: Core's services that are plumbing every module uses: the change queue, posting to Discord, and
#: what core asks of the modules through its hooks and protocols.
CORE_PLUMBING_FOLDERS = frozenset({"queue", "posting", "modules"})
#: Core's services that hold a league rule of core's own.
CORE_LEAGUE_RULE_FOLDERS = frozenset(
    {"seasons", "cancellations", "rounds", "drivers", "settings", "test_mode", "upkeep"}
)
#: The files that sit directly in `core/services/`: the package's own, and the scheduler, whose
#: path every saved job names (`test_every_stored_job_names_the_runner_by_its_pinned_module_path`).
CORE_SERVICES_LOOSE = frozenset({"__init__.py", "scheduler_service.py"})


def test_core_s_services_sit_in_topic_folders_plumbing_apart_from_league_rules():
    """Core's services are grouped by topic into packages, and no folder holds both plumbing and a
    league rule, so that a later review of what core holds can move a whole folder rather than
    sort its files again (architecture.md, "How the code is laid out"). Every file of core's
    services is in one of the named folders but the two that stay loose, and every named folder
    is there."""
    services = PACKAGE / "core" / "services"
    loose = sorted(path.name for path in services.glob("*.py") if path.name not in CORE_SERVICES_LOOSE)
    folders = {path.name for path in services.iterdir() if (path / "__init__.py").is_file()}

    assert loose == []
    assert folders == CORE_PLUMBING_FOLDERS | CORE_LEAGUE_RULE_FOLDERS
    assert not CORE_PLUMBING_FOLDERS & CORE_LEAGUE_RULE_FOLDERS


#: The framework's services, by topic (architecture.md, "How the code is laid out"): the change
#: queue, posting to Discord, the modules' switches, and the scheduler's timers with the start-up
#: sweep.
FRAMEWORK_SERVICE_FOLDERS = frozenset({"queue", "posting", "switches", "jobs"})


def test_the_framework_s_services_sit_in_its_topic_folders():
    """The framework's services are grouped by topic into packages as core's are, with none loose
    (architecture.md, "How the code is laid out"). The folders fill as the machinery moves out of
    core, so each that is there is one of the named, and no file sits outside them."""
    services = PACKAGE / "framework" / "services"
    if not services.is_dir():
        return
    loose = sorted(path.name for path in services.glob("*.py") if path.name != "__init__.py")
    folders = {path.name for path in services.iterdir() if (path / "__init__.py").is_file()}

    assert loose == []
    assert folders <= FRAMEWORK_SERVICE_FOLDERS


def test_the_bot_s_type_names_the_services_for_the_type_checker_only():
    """The one permanent exception in `.importlinter` lets the bot's type import every service,
    the modules' among them, because it does so under `TYPE_CHECKING` and nothing runs because of
    it (architecture.md, "How the code is laid out"). Imported at run time, core would load the
    modules' code the moment anything imports the bot's type."""
    tree = ast.parse((PACKAGE / "framework" / "utils" / "league_bot.py").read_text(encoding="utf-8"))
    guarded = {
        id(inner)
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) in ("TYPE_CHECKING", "typing.TYPE_CHECKING")
        for statement in node.body
        for inner in ast.walk(statement)
    }
    unguarded = sorted(
        ast.unparse(node)
        for node in ast.walk(tree)
        if id(node) not in guarded
        and (
            (isinstance(node, ast.ImportFrom) and ".services" in (node.module or ""))
            or (isinstance(node, ast.Import)
                and any(".services" in alias.name for alias in node.names))
        )
    )
    assert unguarded == []
