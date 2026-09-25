"""The bot writes no naive UTC timestamp.

Issue #160. ``datetime.utcnow()`` and ``datetime.utcfromtimestamp()`` return a naive datetime
that merely happens to hold UTC, are deprecated since Python 3.12, and are scheduled for
removal. Two verdict writers used the first while every other timestamp in the bot was written
with ``datetime.now(timezone.utc)``, so the same kind of value was stored in two formats.

Refused by name wherever it is called in ``src/``, as an attribute of anything — ``datetime``,
a module alias, an imported class — since the replacement is always the same.
"""
from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
NAIVE_UTC = {"utcnow", "utcfromtimestamp"}


def _naive_utc_calls(path: pathlib.Path, root: pathlib.Path = REPO_ROOT) -> list[str]:
    """Every naive-UTC call in *path*, in source order, as ``file:line name()``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = sorted(
        (node.lineno, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in NAIVE_UTC
    )
    where = path.relative_to(root).as_posix()
    return [f"{where}:{line} {name}()" for line, name in calls]


def test_src_never_calls_utcnow():
    found = [hit for path in sorted(SRC.rglob("*.py")) for hit in _naive_utc_calls(path)]
    assert not found, (
        "Use datetime.now(timezone.utc) or datetime.fromtimestamp(ts, timezone.utc):\n  "
        + "\n  ".join(found)
    )


def test_the_detector_sees_both_spellings(tmp_path):
    """The guard is only worth having if it fires, for a module alias as for the class."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import datetime as _dt\n"
        "from datetime import datetime\n"
        "a = _dt.datetime.utcnow()\n"
        "b = datetime.utcfromtimestamp(0)\n"
        "c = datetime.now()\n",
        encoding="utf-8",
    )
    assert _naive_utc_calls(sample, tmp_path) == [
        "sample.py:3 utcnow()",
        "sample.py:4 utcfromtimestamp()",
    ]
