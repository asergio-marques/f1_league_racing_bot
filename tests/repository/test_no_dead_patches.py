"""No test patches a name its target module does not have, creating it as it goes.

`patch("leaguebot.a.b.name", ..., create=True)`, `patch.object(module, "name", ..., create=True)`
and `monkeypatch.setattr(module_or_path, "name", ..., raising=False)` each *make* the attribute
when the module lacks it, instead of failing. That is the flag's one purpose, and it is how a
patch outlives the code it stubbed: when `post_autosanction_announcement` was deleted, the tests
that stubbed it kept passing, each still reading as though it kept the announcer out of the run,
while the patch stubbed nothing and an assertion on its mock (`assert_not_awaited`) could never
fail. A test that reads as isolating a call it never isolated hides what it covers.

So this walks every test file, finds those three forms, and resolves each *statically named*
target: a string path starting `leaguebot.`, or a name bound by an import in the test file to a
`leaguebot` module. Where the target module lacks the name, it fails, naming file and line. A
patch that really creates something the module reads (a `getattr` with a default) is not what
`create=True` is for in this suite: say what is stubbed with a name the module has.

A target it cannot resolve statically (an object built at run time, such as a fake bot or a cog,
or a class rather than a module) is passed over: only the module's own names are checked. The
source is read, and only the modules the targets name are imported.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path
from types import ModuleType

TESTS = Path(__file__).resolve().parents[1]


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)


def _is(node: ast.expr | None, value: bool) -> bool:
    return isinstance(node, ast.Constant) and node.value is value


def _text(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _module_aliases(tree: ast.Module) -> dict[str, str]:
    """Each name an import binds to a `leaguebot` module or object, but one bound to two
    different things (an import local to a function) is left out: it cannot be told apart."""
    bound: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bound.setdefault(alias.asname, set()).add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                bound.setdefault(alias.asname or alias.name, set()).add(f"{node.module}.{alias.name}")
    return {
        name: next(iter(targets))
        for name, targets in bound.items()
        if len(targets) == 1 and next(iter(targets)).startswith("leaguebot.")
    }


def _patched(source: str) -> list[tuple[int, str, str]]:
    """Each (line, target, name) the source patches into being: *target* the dotted path of the
    module, *name* the attribute the patch creates. Only a target named statically is given."""
    tree = ast.parse(source)
    aliases = _module_aliases(tree)

    def base(node: ast.expr) -> str | None:
        text = ast.unparse(node)
        target = aliases.get(text, text)
        return target if target.startswith("leaguebot.") else None

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        callee = ast.unparse(func)
        target: str | None = None
        name: str | None = None
        if callee == "patch" or callee.endswith(".patch"):
            path = _text(node.args[0]) if node.args else None
            if _is(_keyword(node, "create"), True) and path and "." in path:
                target, name = path.rsplit(".", 1)
        elif callee == "patch.object" or callee.endswith(".patch.object"):
            if _is(_keyword(node, "create"), True) and len(node.args) >= 2:
                target, name = base(node.args[0]), _text(node.args[1])
        elif isinstance(func, ast.Attribute) and func.attr == "setattr":
            if _is(_keyword(node, "raising"), False) and node.args:
                path = _text(node.args[0])
                if path is not None and "." in path:
                    target, name = path.rsplit(".", 1)
                elif len(node.args) >= 2:
                    target, name = base(node.args[0]), _text(node.args[1])
        if target and name and target.startswith("leaguebot."):
            found.append((node.lineno, target, name))
    return found


def _module(dotted: str) -> ModuleType | None:
    """The module *dotted* names, or None where it names something else or nothing."""
    try:
        return importlib.import_module(dotted)
    except ModuleNotFoundError:
        parent, _, last = dotted.rpartition(".")
        try:
            owner = importlib.import_module(parent)
        except ModuleNotFoundError:
            return None
        found = getattr(owner, last, None)
        return found if isinstance(found, ModuleType) else None


def _dead_patches(source: str, path: str) -> list[str]:
    """Each patch in *source* that creates a name its module lacks, as ``path:line: module.name``."""
    dead = []
    for line, target, name in _patched(source):
        module = _module(target)
        if module is not None and not hasattr(module, name):
            dead.append(f"{path}:{line}: {target}.{name}")
    return dead


def test_no_test_patches_a_name_its_module_does_not_have():
    dead = []
    for path in sorted(TESTS.rglob("*.py")):
        dead.extend(_dead_patches(path.read_text(encoding="utf-8"), str(path.relative_to(TESTS.parent))))

    assert not dead, "\n".join(
        ["a patch creates a name its module does not have, so it stubs nothing:", *dead]
    )


def test_the_check_sees_each_form_of_a_patch_that_creates_a_missing_name():
    source = (
        "from unittest.mock import patch\n"
        "import leaguebot.core.utils.league_bot as lb\n"
        "from leaguebot.core.utils import league_bot as other\n"
        "def test_a(monkeypatch):\n"
        "    patch('leaguebot.core.utils.league_bot.gone', new=1, create=True)\n"
        "    patch.object(lb, 'also_gone', new=1, create=True)\n"
        "    monkeypatch.setattr(other, 'again_gone', 1, raising=False)\n"
        "    monkeypatch.setattr('leaguebot.core.utils.league_bot.path_gone', 1, raising=False)\n"
    )

    assert _dead_patches(source, "t.py") == [
        "t.py:5: leaguebot.core.utils.league_bot.gone",
        "t.py:6: leaguebot.core.utils.league_bot.also_gone",
        "t.py:7: leaguebot.core.utils.league_bot.again_gone",
        "t.py:8: leaguebot.core.utils.league_bot.path_gone",
    ]


def test_the_check_passes_over_a_name_the_module_has_and_a_target_it_cannot_resolve():
    source = (
        "from unittest.mock import patch\n"
        "import leaguebot.core.utils.league_bot as lb\n"
        "def test_a(monkeypatch, cog):\n"
        "    patch('leaguebot.core.utils.league_bot.LeagueBot', new=1, create=True)\n"
        "    patch('leaguebot.core.utils.league_bot.LeagueBot.gone', new=1, create=True)\n"
        "    patch.object(lb, 'gone', new=1)\n"
        "    monkeypatch.setattr(lb, 'gone', 1)\n"
        "    monkeypatch.setattr(cog.bot, 'gone', 1, raising=False)\n"
        "    patch.object(cog.bot, 'gone', create=True)\n"
    )

    assert _dead_patches(source, "t.py") == []
