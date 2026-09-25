"""The bot is imported from one place, the installed package `leaguebot`, and never through `src`.

Issue #398. The bot then ran as `python src/bot.py`, which put `src/` itself on `sys.path` and never
the repository root, so no package named `src` existed. `models/amendment_state.py` imported
`from src.models.points_config import SessionType` all the same, and every `/results amend`
command that read the amendment state raised `ModuleNotFoundError` under the virtualenv.

The suite could not see it: `tests/__init__.py` makes the test tree a package, so pytest puts
the repository root on `sys.path`, and `src.…` resolves under test. It still would: the bot is now
the package `leaguebot`, installed from `src/` (docs/design/architecture.md, "How the code is laid
out"), and `src` imports as a namespace package wherever the repository root is on the path. An
import here therefore proves nothing about the bot, which is why the first test reads the source
instead of importing it, and reads all of it.

The last two hold the arrangement that replaced the path: the tests import the package this
checkout installed, and pytest puts nothing on the path of its own, so the tests see the bot from
the one place it runs from.
"""
from __future__ import annotations

import ast
import configparser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def _names_the_src_package(node: ast.AST) -> bool:
    if isinstance(node, ast.ImportFrom):
        return node.level == 0 and (node.module or "").split(".")[0] == "src"
    if isinstance(node, ast.Import):
        return any(alias.name.split(".")[0] == "src" for alias in node.names)
    return False


def test_no_module_in_src_imports_it_as_the_src_package():
    offenders = sorted(
        f"{path.relative_to(SRC).as_posix()}:{node.lineno}"
        for path in SRC.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if _names_the_src_package(node)
    )
    assert offenders == [], (
        "The bot is the installed package `leaguebot`; through `src` a module is a second copy "
        f"of itself, or no module at all. Import these through `leaguebot` instead: {offenders}"
    )


def test_the_scan_sees_an_import_through_src():
    """The scan itself: both spellings of the import are caught, and a relative one is not."""
    caught = [
        _names_the_src_package(node)
        for source in (
            "from src.leaguebot.results.models.points_config import SessionType",
            "import src.leaguebot.results.models.points_config",
            "from .src import thing",
            "from leaguebot.results.models.points_config import SessionType",
        )
        for node in ast.parse(source).body
    ]
    assert caught == [True, True, False, False]


def test_leaguebot_is_imported_from_this_checkout():
    """The tests import the bot as the package `pip install -e .` installed, and it is this
    checkout's. A copy installed from elsewhere would be tested in its place, and would pass or
    fail on code that is not this code."""
    import leaguebot

    assert Path(leaguebot.__file__).resolve().parent == SRC / "leaguebot"


def test_pytest_puts_nothing_on_the_path():
    """The package is installed rather than reached through a path set for the tests
    (architecture.md, "How the code is laid out"). A path of the tests' own is how an import that
    works only under test hides one that fails in the bot."""
    parser = configparser.ConfigParser()
    parser.read(ROOT / "pytest.ini", encoding="utf-8")

    assert parser.has_section("pytest")
    assert not parser.has_option("pytest", "pythonpath")


def test_python_m_leaguebot_starts_the_bot():
    """`python -m leaguebot` runs the package's `__main__.py`, which starts the bot only when it is
    run that way: the tests import the same file, and must not start anything by doing so."""
    import importlib.util

    spec = importlib.util.find_spec("leaguebot.__main__")
    assert spec is not None and spec.origin is not None
    assert Path(spec.origin).resolve() == SRC / "leaguebot" / "__main__.py"

    last = ast.parse(Path(spec.origin).read_text(encoding="utf-8")).body[-1]
    assert isinstance(last, ast.If)
    assert ast.unparse(last.test) == "__name__ == '__main__'"
    assert [ast.unparse(statement) for statement in last.body] == ["asyncio.run(main())"]
