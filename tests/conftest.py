"""Shared collection rules.

The `rasteriser` marker is the one mechanism for "this test needs Inkscape". It carries
two behaviours, so no test has to re-implement either:

- CI deselects the marker outright (`-m "not rasteriser"`), because installing Inkscape
  on a hosted runner costs more than the tests return there.
- A local run keeps them, and skips them with a clear reason when Inkscape is absent
  rather than failing on a missing program.

The schema is also built once here rather than once per test — see
`_install_template_migrations` below for why that is worth the indirection.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest


def _install_template_migrations() -> None:
    """Build the schema once and copy it, instead of migrating for every test.

    `run_migrations` applies each of the migration files in turn and commits after every
    one, so raising a schema costs upwards of forty flushes to disk. Nearly every fixture
    in this suite raises one per test. Linux absorbs that — its flushes are nearly free —
    and a Windows runner does not: the same suite took three minutes on `ubuntu-latest`
    and was still at a third of the way after twenty on `windows-latest`, because there
    every flush is a durable write past a virus scanner.

    So the migrations run once and the finished file is copied thereafter. Two cases must
    still migrate in earnest, and both are recognised rather than listed by name:

    - **A target that already holds something.** `test_migration_043` upgrades a populated
      042-era database, and `test_database` proves that a second run changes nothing. Both
      would be destroyed by a copy.
    - **A different set of migration files.** `test_migration_043` builds its 042 database
      by hiding the newest migration from the directory and migrating without it, so a
      template raised from the full set would be the wrong schema entirely. The templates
      are therefore keyed on the set of files each was built from, and a set never seen
      before gets its own.

    This patches the module attribute at import time, before pytest imports any test
    module, because the tests bind `run_migrations` by name at *their* import time and a
    later patch would not reach them.
    """
    from db import database

    real = database.run_migrations
    templates: dict[tuple[str, ...], Path] = {}
    scratch = Path(tempfile.mkdtemp(prefix="f1-schema-"))
    atexit.register(shutil.rmtree, scratch, ignore_errors=True)

    def migration_set() -> tuple[str, ...]:
        return tuple(sorted(
            name for name in os.listdir(database._MIGRATIONS_DIR)
            if name.endswith(".sql") and not name.startswith("__")
        ))

    async def run_migrations(db_path: str) -> None:
        target = Path(db_path)
        if db_path == ":memory:" or (target.exists() and target.stat().st_size):
            await real(db_path)
            return

        key = migration_set()
        template = templates.get(key)
        if template is None:
            template = scratch / f"{len(templates)}.db"
            await real(str(template))
            templates[key] = template

        shutil.copyfile(template, target)

    database.run_migrations = run_migrations


_install_template_migrations()


def pytest_collection_modifyitems(config, items):
    from services.image_render_service import converter_available

    if converter_available(use_cache=False):
        return

    skip = pytest.mark.skip(reason="Inkscape is not installed on this host")
    for item in items:
        if "rasteriser" in item.keywords:
            item.add_marker(skip)
