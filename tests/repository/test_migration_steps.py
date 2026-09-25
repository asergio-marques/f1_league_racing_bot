"""No test builds the schema by applying the migration files itself — issues #252 and #254 —
nor by writing its own copy of it (#233).

Four test files once looped over the migrations directory, raising the schema afresh in every
test and skipping `tests/conftest.py`'s template, which is how the Windows job came to time
out (#252). Since the chain was squashed into one baseline (#254) there is no older schema to
build: every test takes the schema from `run_migrations`, which the template makes cheap.

Fifteen more wrote the tables they needed by hand. Each copy carried the columns its author
wanted and none of the constraints, defaults or triggers beside them, so a test on it passed on
data the bot refuses — two race sessions for one round, a driver profile with no state, every
driver sharing one Discord account — and penalty defaults of 2/1/3 where the schema's are
1/1/1 (#233).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_DECLARES_A_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`\[]?(\w+)", re.IGNORECASE
)

#: The files that may declare a table the migrations declare, and why. A database whose schema
#: is itself the subject, and a file nothing reads inside, are the only two reasons there are.
_MAY_DECLARE_A_PRODUCTION_TABLE = {
    "tests/core/test_database.py": (
        "stands up a database from before the baseline, which the runner must refuse"
    ),
    "tests/core/test_backup_service.py": (
        "the backup copies the database whole and reads nothing inside it"
    ),
    "tests/core/test_backup_before_approval.py": (
        "the backup copies the database whole and reads nothing inside it"
    ),
    "tests/core/test_test_mode_backup.py": (
        "the backup copies the database whole and reads nothing inside it"
    ),
}


def _production_tables() -> set[str]:
    """Every table the migrations declare, and the one `run_migrations` creates for itself."""
    tables = {"schema_migrations"}
    for sql in sorted((REPO_ROOT / "src" / "leaguebot" / "core" / "db" / "migrations").glob("*.sql")):
        tables |= {
            match.group(1).lower()
            for match in _DECLARES_A_TABLE.finditer(sql.read_text(encoding="utf-8"))
        }
    return tables


def test_no_test_builds_the_schema_from_the_migration_files():
    """A file that lists the migrations directory and runs `executescript` on what it reads
    from a file is building the schema by hand; `run_migrations` is the one way to raise it.
    An `executescript` of SQL written in the test itself — seeding rows, standing up an old
    database to be refused — is not."""
    import re

    reads_a_file = re.compile(r"executescript\(\s*\w+\.read(?:_text)?\(")
    tests_root = Path(__file__).resolve().parents[1]
    offenders = sorted(
        str(path.relative_to(tests_root))
        for path in tests_root.rglob("*.py")
        if path != Path(__file__).resolve()
        and reads_a_file.search(text := path.read_text(encoding="utf-8"))
        and "listdir(" in text
        and "migrations" in text.lower()
    )

    assert offenders == [], (
        "these build the schema from the migration files; use run_migrations: "
        + ", ".join(offenders)
    )


def test_no_test_declares_a_table_the_migrations_declare():
    """A test builds its schema with `run_migrations` and seeds the rows it needs; it never
    declares a table the migrations already declare. A table of its own — a spy the test
    reads back, another program's jobstore — is not a copy of anything, and passes.

    An exception that no longer declares such a table fails too, so the list cannot go on
    excusing a file that has since been converted."""
    production = _production_tables()
    offenders: list[str] = []
    declaring: set[str] = set()
    for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
        name = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for match in _DECLARES_A_TABLE.finditer(text):
            if match.group(1).lower() not in production:
                continue
            declaring.add(name)
            if name not in _MAY_DECLARE_A_PRODUCTION_TABLE:
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{name}:{line} ({match.group(1)})")

    assert offenders == [], (
        "these declare a table the migrations declare; build the schema with run_migrations "
        "and seed it instead:\n" + "\n".join(offenders)
    )
    assert sorted(set(_MAY_DECLARE_A_PRODUCTION_TABLE) - declaring) == [], (
        "these are excused from the rule but no longer need to be; remove them from the list"
    )
