"""Shared collection rules.

The `rasteriser` marker is the one mechanism for "this test needs Inkscape". It carries
two behaviours, so no test has to re-implement either:

- CI deselects the marker outright (`-m "not rasteriser"`), because installing Inkscape
  on a hosted runner costs more than the tests return there.
- A local run keeps them, and skips them with a clear reason when Inkscape is absent
  rather than failing on a missing program.

The schema is also built once here rather than once per test — see
`_install_template_migrations` below for why that is worth the indirection.

Scratch is not kept. `pytest.ini` sets `tmp_path_retention_count = 0`, so pytest clears
every earlier run's `tmp_path` tree at session start and its own at session end; three
retained runs overran the 923 MB tmpfs `/tmp` sits on the Raspberry Pi, which failed the
rasteriser tests with 0-byte PNGs. It also sets `tmp_path_retention_policy = failed`, which
drops each *passing* test's directory as it finishes — the count alone acts only at session
end, and one run's accumulated scratch reached 817 MB and filled the tmpfs before it could.
The template scratch below is the one thing pytest does not own, so `pytest_sessionstart`
sweeps it to the same schedule.

**`BOT_TOKEN` is given a placeholder before any test module is collected.** `src/bot.py` reads
it with `os.environ["BOT_TOKEN"]` at import time, after `load_dotenv()`, so importing the module
to reach one of its recovery sweeps raises without it. A development host has a gitignored `.env`
that supplies a real one and hides the problem; a CI runner has neither, and every test file
importing `bot` at module level then fails at collection, which aborts the whole run. It is set
here, once, rather than in the test files, because a per-file default only helps files collected
after it — which is how the suite passed locally and failed on both runners. `setdefault`, so a
real token in the environment is left alone; nothing in the suite connects to Discord with it.
Pinned by `tests/unit/test_suite_needs_no_dotenv.py`.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# Before any test module is imported — see the module docstring.
os.environ.setdefault("BOT_TOKEN", "not-a-real-token")


_TEMPLATE_PREFIX = "f1-schema-"
_TEMPLATE_SCRATCH: Path | None = None


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

    - **A target that already holds something.** `test_database` proves that a second run
      changes nothing, which a copy over the first would destroy.
    - **A different set of migration files.** A test that hides a migration from the
      directory, as the historic-migration tests did before the chain was squashed into one
      baseline (#254), needs the schema those files make, not the full set's. The templates
      are therefore keyed on the set of files each was built from, and a set never seen
      before gets its own.

    This patches the module attribute at import time, before pytest imports any test
    module, because the tests bind `run_migrations` by name at *their* import time and a
    later patch would not reach them.
    """
    from db import database

    global _TEMPLATE_SCRATCH

    real = database.run_migrations
    templates: dict[tuple[str, ...], Path] = {}
    scratch = Path(tempfile.mkdtemp(prefix=_TEMPLATE_PREFIX))
    _TEMPLATE_SCRATCH = scratch
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


def pytest_sessionstart(session):
    """Clear template scratch that an earlier run was killed before cleaning up.

    `_install_template_migrations` puts its templates in a `tempfile.mkdtemp` outside the
    directories pytest owns, so `tmp_path_retention_count = 0` never reaches them and the
    `atexit` registered beside them is the only thing that does — which a killed run never
    gets to. Each leak is small, but it lands on the very tmpfs that retention setting
    exists to protect. Only directories carrying this module's own prefix are removed, and
    never the one this run is using.
    """
    root = Path(tempfile.gettempdir())
    for stale in root.glob(f"{_TEMPLATE_PREFIX}*"):
        if stale != _TEMPLATE_SCRATCH and stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)


def pytest_collection_modifyitems(config, items):
    from services.image_render_service import converter_available

    if converter_available(use_cache=False):
        return

    skip = pytest.mark.skip(reason="Inkscape is not installed on this host")
    for item in items:
        if "rasteriser" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_posting_throttle(monkeypatch):
    """Stand down the division-replay posting throttle for the whole suite.

    `results_post_service.POSTING_THROTTLE_SECONDS` paces a whole-division rebuild so it does
    not spend its time in Discord's rate-limit backoff (#345). Against a stubbed channel there
    is no rate limit to respect and the wait buys nothing — it is simply a second of wall clock
    per posting, and a division-wide repost posts tens of them. Left in, `test_repost_for_division`
    alone took eighteen seconds where it takes well under one.

    Autouse rather than opt-in: a test that reposts a division does not otherwise have to know
    the throttle exists, and the one that does know — `test_replay_throttle.py` — patches the
    constant itself and is unaffected by this.

    **Imported and patched without a fallback, deliberately.** An earlier version swallowed any
    import error and returned, and set the attribute with ``raising=False``. Between them those
    two turned a broken import or a renamed constant into a suite that silently paid a real
    second per posting again — a slow CI run nobody attributes to the commit that caused it,
    rather than a failure anyone can see. `results_post_service` imports cleanly wherever the
    suite runs; if it ever does not, that is worth a loud error.
    """
    from services import results_post_service

    monkeypatch.setattr(results_post_service, "POSTING_THROTTLE_SECONDS", 0)
