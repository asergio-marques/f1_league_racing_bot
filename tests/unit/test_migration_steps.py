"""No test builds the schema by applying the migration files itself — issues #252 and #254.

Four test files once looped over the migrations directory, raising the schema afresh in every
test and skipping `tests/conftest.py`'s template, which is how the Windows job came to time
out (#252). Since the chain was squashed into one baseline (#254) there is no older schema to
build: every test takes the schema from `run_migrations`, which the template makes cheap.
"""
from __future__ import annotations

from pathlib import Path


def test_no_test_builds_the_schema_from_the_migration_files():
    """A file that lists the migrations directory and runs `executescript` is building the
    schema by hand; `run_migrations` is the one way to raise it."""
    tests_root = Path(__file__).resolve().parents[1]
    offenders = sorted(
        str(path.relative_to(tests_root))
        for path in tests_root.rglob("*.py")
        if path != Path(__file__).resolve()
        and "executescript" in (text := path.read_text(encoding="utf-8"))
        and "listdir(" in text
        and "migrations" in text.lower()
    )

    assert offenders == [], (
        "these build the schema from the migration files; use run_migrations: "
        + ", ".join(offenders)
    )
