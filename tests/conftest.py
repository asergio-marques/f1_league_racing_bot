"""Shared collection rules.

The `rasteriser` marker is the one mechanism for "this test needs Inkscape". It carries
two behaviours, so no test has to re-implement either:

- CI deselects the marker outright (`-m "not rasteriser"`), because installing Inkscape
  on a hosted runner costs more than the tests return there.
- A local run keeps them, and skips them with a clear reason when Inkscape is absent
  rather than failing on a missing program.

The schema is also built once here rather than once per test — see
`_install_template_migrations` below for why that is worth the indirection — and every
aiosqlite connection the suite opens skips the flush to disk, for the reasons
`_install_unsynced_connections` gives.

Scratch is not kept. `pytest.ini` sets `tmp_path_retention_count = 0`, so pytest clears
every earlier run's `tmp_path` tree at session start and its own at session end; three
retained runs overran the 923 MB tmpfs `/tmp` sits on the Raspberry Pi, which failed the
rasteriser tests with 0-byte PNGs. It also sets `tmp_path_retention_policy = failed`, which
drops each *passing* test's directory as it finishes — the count alone acts only at session
end, and one run's accumulated scratch reached 817 MB and filled the tmpfs before it could.
The template scratch below is the one thing pytest does not own, so `_sweep_stale_templates`
sweeps it to the same schedule.

**`BOT_TOKEN` is given a placeholder before any test module is collected.** The entry point,
`src/leaguebot/__main__.py`, reads it with `os.environ["BOT_TOKEN"]` at import time, after
`load_dotenv()`, so importing the module to reach one of its recovery sweeps raises without it. A
development host has a gitignored `.env` that supplies a real one and hides the problem; a CI runner
has neither, and every test file importing the entry point at module level then fails at
collection, which aborts the whole run. It is set
here, once, rather than in the test files, because a per-file default only helps files collected
after it — which is how the suite passed locally and failed on both runners. `setdefault`, so a
real token in the environment is left alone; nothing in the suite connects to Discord with it.
Pinned by `tests/repository/test_suite_needs_no_dotenv.py`.
"""
from __future__ import annotations

import atexit
import functools
import gc
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

import pytest
import pytest_asyncio

from tests.support.connections import close_lent_connections

# Before any test module is imported — see the module docstring.
os.environ.setdefault("BOT_TOKEN", "not-a-real-token")


def _refuse_another_checkouts_bot() -> None:
    """Stop the run before it starts when the bot it would test is another checkout's.

    The tests import the bot as the package installed into the virtualenv (`pip install -e .`),
    and an editable install points the virtualenv at the `src/` of the checkout it was made from.
    A worktree sharing that virtualenv would run every test against the other checkout's code,
    and pass on code that is not its own. The remedy is to put this checkout's `src/` first, as
    `PYTHONPATH=src`, which Python reads before the install. `test_import_roots.py` holds the same
    thing as a test; this stops the thousands of others from running first. It runs from
    `pytest_configure`, where stopping the run prints its reason alone.
    """
    import leaguebot

    here = Path(__file__).resolve().parents[1] / "src" / "leaguebot"
    found = Path(leaguebot.__file__).resolve().parent
    if found != here:
        pytest.exit(
            f"The bot these tests would import is {found}, not this checkout's {here}. The "
            "virtualenv was installed from another checkout: run with PYTHONPATH=src.",
            returncode=pytest.ExitCode.USAGE_ERROR,
        )


def pytest_configure(config):
    _refuse_another_checkouts_bot()


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
    from leaguebot.core.db import database

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


def _install_unsynced_connections() -> None:
    """Open every aiosqlite connection in the suite at `synchronous = OFF` (#256).

    The bot runs its database at FULL, deliberately (see `_enable_wal` in `leaguebot.core.db.database`).
    Under WAL that flushes the log on every commit, and the log and the database again when
    the last connection closes and checkpoints — five flushes for a connection that writes.
    Across the suite that came to some seven flushes a test (counted 2026-09-24). Linux
    absorbs them; on Windows each is a `FlushFileBuffers`, and a test that raised a database
    cost about 80 ms more there than on Linux, which was nearly all of the Windows job's
    extra time. Measured on the Pi's SD card, where a flush is dear as it is on Windows, a
    stand-in test took 98 ms with the flushes and 38 ms without.

    **OFF is safe here, and only here.** It changes what survives a power cut, which no test
    can observe, on databases thrown away when the test ends. The bot's own FULL is still
    pinned, by `test_connections_keep_full_durability`, which reaches aiosqlite's own
    `connect` to see past this.

    **The journal mode is not touched.** WAL decides which reads wait on which writes, and
    tests do observe that; `test_the_schema_template_is_copied_in_wal_mode` pins it. Two other
    remedies were weighed and declined: a template left out of WAL, for that reason, and a
    connection held open to spare the `-wal` and `-shm` files being made and deleted, which
    measured slower and would hold open on Windows a file some tests delete.

    **Through `aiosqlite.connect`, not `get_connection`.** It hands its keyword arguments
    straight to `sqlite3.connect`, so a `factory` makes the setting as the connection opens,
    on its own thread and with no round trip of its own, and it reaches the fixtures that
    seed through aiosqlite directly as well as everything that goes through
    `get_connection`. **The bot's own factory is kept, made unsynced:** `get_connection` hands
    aiosqlite a `sqlite3.Connection` subclass of `leaguebot.core.db.database`'s, which records
    the cursors it must close before lending the connection again, so it is swapped for a
    subclass of itself that also turns the flush off. Swapping it for `UnsyncedConnection`
    would lose that, and keeping it as it is would keep FULL. Any other factory a caller
    passes wins as it is. Plain `sqlite3.connect` is left alone,
    and so is the scheduler's job store, which goes through SQLAlchemy and keeps a FULL pin
    of its own. Nothing in `src/` changes: a seam there would put test machinery in the bot.

    Patched at import time, like `run_migrations` above. `get_connection` and the tests call
    `aiosqlite.connect` through the module when they connect, so the order of imports does
    not matter — only a `from aiosqlite import connect` would escape it, and nothing does.
    Pinned by the tests under "The suite's own databases" in `test_database.py`.
    """
    import sqlite3

    import aiosqlite

    real = aiosqlite.connect

    class UnsyncedConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.execute("PRAGMA synchronous = OFF")

    unsynced: dict[type, type] = {}

    def unsynced_bot_factory(factory: type) -> type:
        if factory not in unsynced:
            def __init__(self, *args, **kwargs) -> None:
                factory.__init__(self, *args, **kwargs)
                self.execute("PRAGMA synchronous = OFF")

            name = f"Unsynced{factory.__name__}"
            unsynced[factory] = type(name, (factory,), {"__init__": __init__})
        return unsynced[factory]

    @functools.wraps(real)
    def connect(database, **kwargs):
        factory = kwargs.get("factory")
        if factory is None:
            kwargs["factory"] = UnsyncedConnection
        elif factory.__module__ == "leaguebot.core.db.database":
            kwargs["factory"] = unsynced_bot_factory(factory)
        return real(database, **kwargs)

    aiosqlite.connect = connect


_install_unsynced_connections()


def _sweep_stale_templates() -> None:
    """Clear template scratch that an earlier run was killed before cleaning up.

    `_install_template_migrations` puts its templates in a `tempfile.mkdtemp` outside the
    directories pytest owns, so `tmp_path_retention_count = 0` never reaches them and the
    `atexit` registered beside them is the only thing that does — which a killed run never
    gets to. Each leak is small, but it lands on the very tmpfs that retention setting
    exists to protect. Only directories carrying this module's own prefix are removed, and
    never the one this run is using.

    **Only the process that starts the session sweeps, never an xdist worker** (#540). Every
    worker imports this module and makes template scratch of its own, so a worker sweeping as it
    starts deletes the live templates of the workers that started before it: `test_schema_rules.py`,
    which raises a schema while it is collected, failed with "unable to open database file". So
    `pytest_sessionstart` calls this where the config carries no `workerinput`: in the controller,
    whose own `pytest_sessionstart` runs before xdist's starts the workers (that one is `trylast`),
    or in a run without workers. A session start rather than `pytest_configure`, which `--help` and
    `--markers` run too, outside any session and outside the lock: a sweep there would take a
    running session's templates.
    """
    root = Path(tempfile.gettempdir())
    for stale in root.glob(f"{_TEMPLATE_PREFIX}*"):
        if stale != _TEMPLATE_SCRATCH and stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)


def pytest_sessionstart(session):
    if not hasattr(session.config, "workerinput"):
        _sweep_stale_templates()


def pytest_collection_finish(session):
    """Put everything collected out of the garbage collector's reach (#540).

    The collected suite — every test module, its fixtures and parameters, some 340,000 objects
    for the core tests alone — lives to the end of the run, and the collector's full passes
    walked all of it again each time: on the Pi they took about a tenth of the core tests' run,
    in pauses of up to 1.4 seconds that lengthened as the run went on. `gc.freeze` moves what
    exists now into a generation the collector never scans. On the core tests with three workers
    it cut each worker's full collections by some 40% and the run from 10:36 to 9:49 (Pi,
    2026-10-31). The collect first, so that garbage left by collection is not frozen with it.
    """
    gc.collect()
    gc.freeze()


#: What one xdist worker takes at its peak over the full suite, in MB, and what the host is left
#: for everything else. See `pytest_xdist_auto_num_workers`. Measured on the Pi on 2026-11-01:
#: the full suite on two workers peaked at 301 MB and 402 MB of resident memory, the larger being
#: the worker that ran the source checks, against 621 MB for the whole suite in one process.
_WORKER_MEMORY_MB = 400
_MEMORY_RESERVE_MB = 512

#: Where Linux says how much memory is free. Absent on Windows and macOS.
_MEMINFO = Path("/proc/meminfo")


def _free_memory_mb() -> int | None:
    """The memory the host can give the run, in MB: what is available plus the free swap, or
    None where the host does not say."""
    try:
        text = _MEMINFO.read_text(encoding="ascii")
    except OSError:
        return None
    fields: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        if rest.split():
            fields[name] = int(rest.split()[0])
    if "MemAvailable" not in fields:
        return None
    return (fields["MemAvailable"] + fields.get("SwapFree", 0)) // 1024


def _cores() -> int:
    """The cores this process may run on, as xdist counts them without `psutil`, which the suite
    does not install."""
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def pytest_xdist_auto_num_workers(config):
    """Run as many workers as the host has memory for, up to one a core (#540).

    `pytest.ini` asks for `-n auto`, which xdist reads as one worker a core. Each worker holds the
    whole collected suite and grows as it runs, to some `_WORKER_MEMORY_MB` at its peak. A hosted
    runner has memory to spare for four, but the Raspberry Pi the bot is developed on has 1.8 GB
    shared with the editor and the agents working on it, and at three workers its swap filled
    completely, which invites the out-of-memory killer into the editor's session. So the count is
    what the host's available memory and free swap hold once `_MEMORY_RESERVE_MB` is set aside,
    clamped between one and the core count: about two on the Pi as it is used, four on CI.

    Returning None leaves the count to xdist: where `PYTEST_XDIST_AUTO_NUM_WORKERS` names one,
    which a host may set to override this, and where the host does not say what is free, as on
    Windows, whose runner has memory to spare.
    """
    if os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS"):
        return None
    free = _free_memory_mb()
    if free is None:
        return None
    return max(1, min(_cores(), (free - _MEMORY_RESERVE_MB) // _WORKER_MEMORY_MB))


def pytest_collection_modifyitems(config, items):
    from leaguebot.image.services.image_render_service import converter_available

    if converter_available(use_cache=False):
        return

    skip = pytest.mark.skip(reason="Inkscape is not installed on this host")
    for item in items:
        if "rasteriser" in item.keywords:
            item.add_marker(skip)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    """Stop every change queue the test started through `restart_queue`, once the test has run,
    and wait for the database threads a stopped worker left running.

    Here rather than in a fixture because the stop must run on the test's own event loop: after
    the test's call, while the loop is open and idle, and before the teardown that closes it,
    whichever order the fixtures are torn down in. A worker still running when the loop closes is
    cut off part way through a read, and aiosqlite's thread raises "Event loop is closed" against
    a later test (`tests/support/change_queue.py`, `stop_started_queues`). Nothing is imported
    where no test has imported the support module.

    Only the database threads started during the test's call are waited for: one a fixture opened
    in set-up stays alive until its teardown, and waiting on it would cost every such test the
    whole grace period (`test_stop_started_queues_waits_on_no_thread_a_fixture_holds`).

    The connections the bot keeps idle to lend again are closed here too, before that wait, which
    would otherwise wait on each of their threads (`stop_started_queues`); and where the test
    started no queue, closed all the same.
    """
    before = set(threading.enumerate())
    try:
        return (yield)
    finally:
        queues = sys.modules.get("tests.support.change_queue")
        if queues is not None:
            queues.stop_started_queues(started_before=before)
        else:
            close_lent_connections()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    """Close the connections the bot keeps idle once the test's fixtures are set up (#540).

    A fixture that seeds its database through `get_connection` gives its connection back to be
    lent again; closed here, it is not counted among the threads alive before the test's call,
    and the test starts with nothing lent from a database but its own
    (`tests/support/connections.py`).
    """
    try:
        return (yield)
    finally:
        close_lent_connections()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    """Close the connections the bot keeps idle once the test's fixtures are torn down (#540), so
    that none holds the test's `tmp_path` database open as pytest deletes it, which Windows
    refuses, and none is lent to the next test (`tests/support/connections.py`)."""
    try:
        return (yield)
    finally:
        close_lent_connections()


def pytest_sessionfinish(session, exitstatus):
    """Close the connections the bot keeps idle as the session ends (#540): each holds a thread
    that is not a daemon, which would keep the process alive past the run."""
    close_lent_connections()


@pytest_asyncio.fixture
async def lending(monkeypatch):
    """Switch on the lending of connections for this test: up to four are kept idle once given
    back cleanly, whatever the bot's own cap is set to, and closed as the test ends (#540)."""
    from leaguebot.core.db import database

    monkeypatch.setattr(database, "_IDLE_CONNECTIONS_KEPT", 4)
    yield
    await database.close_connections()


@pytest.fixture(autouse=True)
def _no_review_marks_carried_over():
    """Forget what became of every season review's button before and after each test.

    The marks are held in memory, by the id of the review's question (`season_review_changes.
    _PRESSES`), and the tests reuse the same ids against databases of their own: a press left
    under way by one test would otherwise refuse the next test's first press."""
    from leaguebot.core.services.season_review_changes import forget_presses

    forget_presses()
    yield
    forget_presses()


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
    from leaguebot.results.services import results_post_service

    monkeypatch.setattr(results_post_service, "POSTING_THROTTLE_SECONDS", 0)
