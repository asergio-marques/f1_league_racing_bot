"""No test file defines a name twice in one scope, since Python keeps the last and pytest runs it alone.

`tests/results/test_repost_for_division.py` once defined `test_each_round_is_labelled_by_its_own_status`
twice: the results repost's test, then the standings repost's. The second bound the name, so pytest
never collected the first, and the rule it pinned — a round awaiting verdicts is labelled as such,
never as final — went unchecked while the suite stayed green. Nothing in pytest reports it. A helper
or fixture defined twice fails the same way, silently running the later copy for both.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]

_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _defined_twice(source: str) -> list[str]:
    """Each name a module, or a class at its top, defines more than once."""
    tree = ast.parse(source)
    scopes = [("", tree.body)] + [
        (f"{node.name}::", node.body) for node in tree.body if isinstance(node, ast.ClassDef)
    ]
    found = []
    for prefix, body in scopes:
        names = Counter(node.name for node in body if isinstance(node, _DEFINITIONS))
        found.extend(f"{prefix}{name}" for name, count in sorted(names.items()) if count > 1)
    return found


def test_the_check_sees_a_test_defined_twice():
    twice = "def test_a():\n    pass\n\nasync def test_a():\n    pass\n"
    assert _defined_twice(twice) == ["test_a"]


def test_the_check_sees_a_method_defined_twice_in_a_test_class():
    twice = "class TestSeat:\n    def test_a(self):\n        pass\n\n    def test_a(self):\n        pass\n"
    assert _defined_twice(twice) == ["TestSeat::test_a"]


def test_no_test_file_defines_a_name_twice():
    shadowed = [
        f"{path.relative_to(TESTS.parent).as_posix()}::{name}"
        for path in sorted(TESTS.rglob("*.py"))
        for name in _defined_twice(path.read_text(encoding="utf-8"))
    ]
    assert shadowed == [], f"defined twice, so only the last runs: {shadowed}"
