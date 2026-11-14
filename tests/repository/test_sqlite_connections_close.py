"""No `with sqlite3.connect(...)`: a sqlite3 connection's own `with` commits and does not close.

The connection it leaves open is freed only when the garbage collector reaches it, for it sits in
a reference cycle with its statement cache. Until then it holds the database file, and its `-wal`
and `-shm` beside it, open. Linux lets a file held open be deleted; Windows does not. A test
support helper that read the hub's channel this way left the change queue's database open into
`test_a_job_after_a_restored_state_is_numbered_above_every_job_before_it`, whose restore deletes
the `-wal`, and the Windows job failed whenever the collector had not yet run.

A connection opened with `sqlite3.connect` is closed by name instead: in `contextlib.closing(...)`
(adding the connection's own `with` beside it where it should commit on the way out), or with
`close()` in a `finally`, as `backup_service` does.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _opens_in_its_own_with(path: Path) -> list[int]:
    """The lines in *path* where `sqlite3.connect(...)` is the context manager of a `with`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            call = item.context_expr
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "connect"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "sqlite3"
            ):
                lines.append(node.lineno)
    return lines


def test_no_sqlite3_connection_is_left_to_its_own_with():
    """Every file under `src/` and `tests/` is read; none opens a sqlite3 connection as the
    context manager of a `with`, which would commit and leave it open."""
    found = [
        f"{path.relative_to(ROOT)}:{line}"
        for folder in ("src", "tests")
        for path in sorted((ROOT / folder).rglob("*.py"))
        for line in _opens_in_its_own_with(path)
    ]
    assert found == [], "close these with contextlib.closing(...): " + ", ".join(found)
