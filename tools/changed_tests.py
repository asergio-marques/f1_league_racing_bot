"""List every test a branch adds, modifies, deletes or moves, so that no test change goes unseen.

The `work-issue` workflow (`.claude/workflows/work-issue.js`) runs this at two points. In the
tests stage, from the branch's base: the builder's own list of the tests it changed, each with
the scenario it tests, must match this one entry for entry, because the owner approves the tests
at Gate 2 from that list and a test missing from it is a test nobody approved. In the build, from
the commit the owner approved: there, anything but the removal of the issue's expected-failure
markers is a test change the build was not allowed to make. A builder's word is not enough for
either, which is why this is a program rather than an agent reading the diff.

**A test is what pytest collects by default:** a `test*` function at the top of a `test_*.py` or
`*_test.py` file, or a `test*` method of a top-level `Test*` class there. Everything else under
`tests/` that a test's scenario can depend on is **support**: a fixture or helper, by name; a
module-level assignment, by the name it binds (a ratchet list such as `KNOWN_DIRECT_POSTS` is one);
any other module-level code, as `<module level>`; and a file that is not Python, as `<file>`.

**A change is a change to the syntax tree, not to the text.** Reformatting and comments change
nothing; a test's docstring does, since it says what the test is for. A file's or a class's own
docstring is not read. A name bound twice in one file, as a test shadowed by a second of the same
name, counts every binding, so that a change to the one pytest never runs is seen too.

**A move changes nothing, but it changes nothing else either.** Moving a module rewrites every
import of it and every dotted path a test patches in it, and changes no scenario, so the build
must be free to make it. An import is therefore read as the module's own name and the names it
binds, less the package it sits in, and a string that is a dotted path of three parts or more, as
`patch()` takes, as its last two. An import that binds another name, or takes it from another
module, is a change. The imports at the top of a file are support, as `<imports>`.

**A moved test, or moved support,** is one deleted in one place and added, unchanged and under the
same name, in another. It is reported once, as moved, with where it came from. One moved and
changed at once is reported as deleted and added.

**With `--issue N`,** a test whose only change is the loss of its
`xfail(..., reason="#N: ...")` markers, on the test or on a case of it through
`pytest.param(..., marks=...)`, is reported under `markersRemoved` rather than as modified: that is
the one test change the build makes by design.

Run it as:

    python tools/changed_tests.py --repo <checkout> --base <commit> [--head HEAD] [--issue N]

It prints JSON, and exits 2 with the reason on stderr where git fails or a file cannot be parsed.
"""
from __future__ import annotations

import argparse
import ast
import copy
import fnmatch
import json
import posixpath
import re
import subprocess
import sys
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import TypeVar

MODULE_LEVEL = "<module level>"
CLASS_LEVEL = "<class level>"
IMPORTS = "<imports>"
WHOLE_FILE = "<file>"

_TEST_FILES = ("test_*.py", "*_test.py")
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)

_Key = TypeVar("_Key", bound=Hashable)

# A package, a module and a name in it, as `patch()` and `monkeypatch.setattr()` take a target.
_DOTTED_PATH = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*){2,}")


class ToolError(Exception):
    """The change cannot be listed: the message says why."""


@dataclass(frozen=True)
class _Unit:
    """One test or one piece of support, as it stands on one side of the change."""

    node: ast.AST | None
    shape: str
    bound_twice: bool = False


def _tail(dotted: str | None) -> str | None:
    return dotted.rsplit(".", 1)[-1] if dotted else dotted


class _AsMoved(ast.NodeTransformer):
    """The tree as any move would leave it: each module named by itself, less its package."""

    def visit_Import(self, node: ast.Import) -> ast.Import:
        return ast.Import(names=[ast.alias(name=_tail(a.name) or "", asname=a.asname) for a in node.names])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom:
        names = [ast.alias(name=a.name, asname=a.asname) for a in node.names]
        return ast.ImportFrom(module=_tail(node.module), names=names, level=node.level)

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        if isinstance(node.value, str) and _DOTTED_PATH.fullmatch(node.value):
            return ast.Constant(value=".".join(node.value.rsplit(".", 2)[-2:]))
        return node


def _shape(node: ast.AST) -> str:
    """What a change is measured on: the syntax tree, as any move would leave it."""
    return ast.dump(_AsMoved().visit(copy.deepcopy(node)))


def _imports_shape(imports: list[ast.stmt]) -> str:
    """The names a file imports, each once, in no order: sorting or splitting them is no change."""
    bound = set()
    for statement in imports:
        moved = _AsMoved().visit(copy.deepcopy(statement))
        module = getattr(moved, "module", None)
        level = getattr(moved, "level", 0)
        for alias in getattr(moved, "names", []):
            bound.add((module or "", level, alias.name, alias.asname or ""))
    return repr(sorted(bound))


def _put(units: dict[str, _Unit], key: str, node: ast.AST | None, shape: str) -> None:
    """Bind *key* to *node*, keeping any earlier binding of it in the shape."""
    earlier = units.get(key)
    if earlier is None:
        units[key] = _Unit(node, shape)
    else:
        units[key] = _Unit(node, f"{earlier.shape}\n{shape}", bound_twice=True)


def _git(repo: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", check=False
    )
    if result.returncode != 0:
        raise ToolError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _is_test_module(path: str) -> bool:
    name = posixpath.basename(path)
    return any(fnmatch.fnmatch(name, pattern) for pattern in _TEST_FILES)


def _is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _bound_name(statement: ast.stmt) -> str | None:
    """The name a module-level assignment binds, or None for any other statement."""
    if isinstance(statement, ast.Assign):
        return ", ".join(ast.unparse(target) for target in statement.targets)
    if isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
        return ast.unparse(statement.target)
    return None


def _units(source: str, path: str) -> tuple[dict[str, _Unit], dict[str, _Unit]]:
    """The tests and the support in one file, keyed by node id and by name."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        raise ToolError(f"{path} cannot be parsed: {error}") from error
    in_test_module = _is_test_module(path)
    tests: dict[str, _Unit] = {}
    support: dict[str, _Unit] = {}
    imports: list[ast.stmt] = []
    loose: list[ast.stmt] = []
    for index, statement in enumerate(tree.body):
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            imports.append(statement)
        elif index == 0 and _is_docstring(statement):
            continue
        elif isinstance(statement, _DEFS):
            if in_test_module and statement.name.startswith("test"):
                _put(tests, f"{path}::{statement.name}", statement, _shape(statement))
            else:
                _put(support, statement.name, statement, _shape(statement))
        elif isinstance(statement, ast.ClassDef):
            if in_test_module and statement.name.startswith("Test"):
                _test_class(statement, path, tests, support)
            else:
                _put(support, statement.name, statement, _shape(statement))
        elif (name := _bound_name(statement)) is not None:
            _put(support, name, statement, _shape(statement))
        else:
            loose.append(statement)
    if imports:
        support[IMPORTS] = _Unit(None, _imports_shape(imports))
    if loose:
        support[MODULE_LEVEL] = _Unit(None, _shape(ast.Module(body=loose, type_ignores=[])))
    return tests, support


def _test_class(
    cls: ast.ClassDef, path: str, tests: dict[str, _Unit], support: dict[str, _Unit]
) -> None:
    """A `Test*` class's tests, its other methods, and whatever else it declares."""
    rest: list[ast.stmt] = []
    for index, statement in enumerate(cls.body):
        if index == 0 and _is_docstring(statement):
            continue
        if isinstance(statement, _DEFS):
            if statement.name.startswith("test"):
                _put(tests, f"{path}::{cls.name}::{statement.name}", statement, _shape(statement))
            else:
                _put(support, f"{cls.name}::{statement.name}", statement, _shape(statement))
        else:
            rest.append(statement)
    if rest or cls.decorator_list or cls.bases or cls.keywords:
        shell = ast.ClassDef(
            name=cls.name,
            bases=cls.bases,
            keywords=cls.keywords,
            body=rest,
            decorator_list=cls.decorator_list,
            type_params=getattr(cls, "type_params", []),
        )
        _put(support, f"{cls.name}::{CLASS_LEVEL}", None, _shape(shell))


def _is_issue_marker(decorator: ast.expr, issue: str) -> bool:
    """`@pytest.mark.xfail(..., reason="#<issue>: ...")`, the tests stage's marker."""
    if not isinstance(decorator, ast.Call) or not ast.unparse(decorator.func).endswith("xfail"):
        return False
    for keyword in decorator.keywords:
        if (
            keyword.arg == "reason"
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        ):
            return keyword.value.value.startswith(f"#{issue}:")
    return False


def _without_markers(node: ast.AST, issue: str) -> tuple[ast.AST, int]:
    """A copy of a test less the issue's markers, on it or on its cases, and how many it had."""
    copied = copy.deepcopy(node)
    if not isinstance(copied, _DEFS):
        return copied, 0
    kept = [d for d in copied.decorator_list if not _is_issue_marker(d, issue)]
    removed = len(copied.decorator_list) - len(kept)
    copied.decorator_list = kept
    for decorator in kept:
        for call in [n for n in ast.walk(decorator) if isinstance(n, ast.Call)]:
            for keyword in [k for k in call.keywords if k.arg == "marks"]:
                value = keyword.value
                if _is_issue_marker(value, issue):
                    call.keywords.remove(keyword)
                    removed += 1
                elif isinstance(value, (ast.List, ast.Tuple)):
                    left = [e for e in value.elts if not _is_issue_marker(e, issue)]
                    removed += len(value.elts) - len(left)
                    if left:
                        value.elts = left
                    else:
                        call.keywords.remove(keyword)
    return copied, removed


def _only_markers_removed(before: _Unit, after: _Unit, issue: str) -> bool:
    """The test lost some of the issue's markers, and nothing else changed.

    A name bound twice is never read so: its shadowed binding could have changed unseen.
    """
    if before.node is None or after.node is None or before.bound_twice or after.bound_twice:
        return False
    was, had = _without_markers(before.node, issue)
    now, has = _without_markers(after.node, issue)
    return had > has and _shape(was) == _shape(now)


def _side(repo: str, rev: str, path: str, present: bool) -> str:
    return _git(repo, "show", f"{rev}:{path}") if present else ""


def changed_tests(repo: str, base: str, head: str = "HEAD", issue: str | None = None) -> dict:
    """Every test and piece of support under `tests/` that differs between *base* and *head*."""
    base_sha = _git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
    head_sha = _git(repo, "rev-parse", "--verify", f"{head}^{{commit}}").strip()
    listing = _git(repo, "diff", "--name-status", "--no-renames", base_sha, head_sha, "--", "tests/")
    tests: list[dict] = []
    support: list[dict] = []
    markers: list[str] = []
    added_tests: dict[str, _Unit] = {}
    deleted_tests: dict[str, _Unit] = {}
    added_support: dict[tuple[str, str], _Unit] = {}
    deleted_support: dict[tuple[str, str], _Unit] = {}
    for line in sorted(listing.splitlines()):
        status, path = line.split("\t", 1)
        status = status[0]
        if not path.endswith(".py"):
            change = {"A": "added", "D": "deleted"}.get(status, "modified")
            support.append({"file": path, "name": WHOLE_FILE, "change": change})
            continue
        before_tests, before_support = _units(_side(repo, base_sha, path, status != "A"), path)
        after_tests, after_support = _units(_side(repo, head_sha, path, status != "D"), path)
        for nodeid in sorted(before_tests.keys() | after_tests.keys()):
            before, after = before_tests.get(nodeid), after_tests.get(nodeid)
            if before is None and after is not None:
                added_tests[nodeid] = after
            elif after is None and before is not None:
                deleted_tests[nodeid] = before
            elif before is not None and after is not None and before.shape != after.shape:
                if issue and _only_markers_removed(before, after, issue):
                    markers.append(nodeid)
                else:
                    tests.append({"nodeid": nodeid, "change": "modified"})
        for name in sorted(before_support.keys() | after_support.keys()):
            before, after = before_support.get(name), after_support.get(name)
            if before is None and after is not None:
                added_support[(path, name)] = after
            elif after is None and before is not None:
                deleted_support[(path, name)] = before
            elif before is not None and after is not None and before.shape != after.shape:
                support.append({"file": path, "name": name, "change": "modified"})
    for nodeid, change, origin in _pair_moves(added_tests, deleted_tests, lambda k: k.split("::")[-1]):
        tests.append({"nodeid": nodeid, "change": change, **({"from": origin} if origin else {})})
    for (path, name), change, was in _pair_moves(added_support, deleted_support, lambda k: k[1]):
        support.append({"file": path, "name": name, "change": change, **({"from": was[0]} if was else {})})
    return {
        "base": base_sha,
        "head": head_sha,
        "tests": sorted(tests, key=lambda t: t["nodeid"]),
        "support": sorted(support, key=lambda s: (s["file"], s["name"])),
        "markersRemoved": sorted(markers),
    }


def _pair_moves(
    added: dict[_Key, _Unit], deleted: dict[_Key, _Unit], name_of: Callable[[_Key], str]
) -> list[tuple[_Key, str, _Key | None]]:
    """Each unit added or deleted, with every identical pair under one name matched as a move.

    Returns `(where it is now, the change, where it came from)`, the last for a move alone.
    """
    waiting: dict[tuple[str, str], list[_Key]] = {}
    for key, unit in sorted(deleted.items()):
        waiting.setdefault((name_of(key), unit.shape), []).append(key)
    entries: list[tuple[_Key, str, _Key | None]] = []
    for key, unit in sorted(added.items()):
        origins = waiting.get((name_of(key), unit.shape))
        entries.append((key, "moved", origins.pop(0)) if origins else (key, "added", None))
    entries.extend((key, "deleted", None) for keys in waiting.values() for key in keys)
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=".", help="the checkout to read (default: here)")
    parser.add_argument("--base", required=True, help="the commit to compare from")
    parser.add_argument("--head", default="HEAD", help="the commit to compare to (default: HEAD)")
    parser.add_argument("--issue", help="report the loss of this issue's xfail markers apart")
    options = parser.parse_args(argv)
    issue = options.issue.lstrip("#") if options.issue else None
    try:
        result = changed_tests(options.repo, options.base, options.head, issue)
    except ToolError as error:
        print(error, file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
