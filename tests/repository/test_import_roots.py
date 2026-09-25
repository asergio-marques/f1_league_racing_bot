"""No module in `src/` imports another through a package named `src`.

Issue #398. The bot runs as `python src/leaguebot/__main__.py`, which puts `src/` itself on `sys.path` — never
the repository root — so `cogs`, `services`, `models` and the rest are top-level packages, and
no package named `src` exists. `results/models/amendment_state.py` imported
`from src.models.points_config import SessionType` all the same, and every `/results amend`
command that read the amendment state raised `ModuleNotFoundError` under the virtualenv.

The suite could not see it: `tests/__init__.py` makes the test tree a package, so pytest puts
the repository root on `sys.path` and `src.…` resolves under test. An import here therefore
proves nothing about the bot, which is why this reads the source instead of importing it —
and reads all of it, because the next such import would pass every test just as this one did.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"


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
        "The bot runs with src/ on sys.path, so no package named `src` exists there; "
        f"import these from the top-level package instead: {offenders}"
    )


def test_the_scan_sees_an_import_through_src():
    """The scan itself: both spellings of the import are caught, and a relative one is not."""
    caught = [
        _names_the_src_package(node)
        for source in (
            "from src.models.points_config import SessionType",
            "import src.models.points_config",
            "from .src import thing",
            "from leaguebot.results.models.points_config import SessionType",
        )
        for node in ast.parse(source).body
    ]
    assert caught == [True, True, False, False]
