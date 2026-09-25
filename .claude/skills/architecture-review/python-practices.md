# Current Python application practice — the yardstick for the design passes

The architecture pass (#282) weighs its candidate decisions against this file. Each module pass
(#283–#288) reviews against `docs/design/architecture.md` first and uses this file only for what
the architecture does not settle. **Once `architecture.md` has decided something, it wins over
this file.** This file is an input to that decision and does not outrank it.

**A practice here is a default, not a rule.** A finding that cites one must also say three
things: what adopting it would cost this bot, what defect it would have prevented or will
prevent, and what breaks if it is left alone. This is a single-league Discord bot on a
Raspberry Pi 4, with one SQLite file and one maintainer. A pattern built for a team of fifty
and a fleet of services is a finding against the proposal, not in its favour. Do not recommend
a framework, an abstraction or an extra layer for its own sake. Recommend the simplest shape
that holds the principle, and name the defect class it closes, with an issue number where the
tracker has one.

**Nothing here restates CLAUDE.md.** The type-check rules, the coverage floor, the schema
baseline, the test-host rules and the `rasteriser` marker live there. Cite them and leave them
there.

Baseline: Python 3.13 on every host (CI's runners and the Pi), so anything up to 3.13 is
available. discord.py ≥ 2.6, aiosqlite, APScheduler 3.

Every practice names its primary source. Cite that source in a finding, not this file.

---

## 1. Layers, and which way a dependency points

- **Three layers, dependencies pointing down only.** Presentation (cogs, views, modals, the
  command tree), then application services, then data access. A lower layer never imports a
  higher one. Pure domain values (the `models/` dataclasses) import no I/O at all. *Source:*
  Percival & Gregory, *Architecture Patterns with Python* (cosmicpython.com), Introduction,
  "Layering" and "The Dependency Inversion Principle"; ch. 4 for the service layer itself.
- **A cog is an adapter.** It turns an interaction into typed arguments, calls one service
  operation and renders the reply. It holds no SQL, no business rule and no transaction. The
  test of this: every rule a league experiences can be exercised by calling a service with
  no `discord.Interaction` in sight.
- **A lower layer that needs to reach up takes a seam instead of an import.** The seam is a
  callback, or a `typing.Protocol` the lower layer defines and the upper layer satisfies,
  wired at the composition root. An import under `if TYPE_CHECKING:` or inside a function
  hides the cycle from the interpreter but not from the design: it is still an upward
  dependency and is counted as one. *Source:* PEP 544 (Protocols).
- **Enforce the rule mechanically, as a fitness function.** A written layering rule decays.
  One enforced by the build does not. *Source:* import-linter documentation. **Settled here:**
  `import-linter` contracts in `.importlinter`, run by `tests/repository/test_import_contracts.py`
  within the suite, for which code may import which, and `ast` checks in
  `tests/repository/test_architecture_rules.py` for the rest (architecture.md, "How the rules
  are checked").
- **Introduce the rule onto code that breaks it as a ratchet.** List each existing breach in
  the check, naming the issue that will remove it. The test fails on any new breach, and also on any
  listed breach that has since gone, so the list can only shrink. This is how each module
  pass shows it has removed its share.

## 2. Modules, packages and boundaries

- **Package by feature first, by layer second.** Ownership then becomes a property of the
  tree rather than of a build script. *Source:* cosmicpython appendix B, "A Template Project
  Structure"; the "package by feature" argument generally. **Settled here:** a folder per module
  under `leaguebot/`, and inside each a folder per kind of code (`cogs/`, `services/`,
  `models/`, `utils/`, and `db/` in core); a file's module is the folder it sits in
  (architecture.md, "How the code is laid out").
- **Each package has a small public surface and hides its internals.** Other modules import
  from the package, not from its internal modules. `__all__` or re-exports in `__init__.py`
  declare the surface. A leading underscore marks a private module. An `independence` or
  `forbidden` contract (or an `ast` test) pins it. *Source:* PEP 8, "Public and internal
  interfaces". **Settled here:** a name with a leading underscore is used only inside its own
  module, and what a module offers the others is public, with a docstring saying what it
  promises (architecture.md, "How modules and core fit together").
- **Modules depend on core and never the reverse.** Module-to-module dependencies are
  declared, acyclic, and agree with the dependency rules in each module's wip-spec. A cycle
  between two modules means they are one module, or share a concept that belongs in core.
  **Settled here:** core reaches a module only through hooks the builder signs the module up
  to; a module uses another only as the dependency table allows, and any module may use image
  (architecture.md, "How modules and core fit together").
- **Use one top-level package with a unique name**, run with `python -m <package>`. Generic
  top-level names (`utils`, `models`, `services`, `db`, `cogs`) share one namespace with every
  installed distribution. Running a file with its directory on `sys.path` is what makes
  those bare names importable, and it is the source of #398's failure class. *Source:*
  Python Packaging User Guide, "src layout vs flat layout"; PEP 621 for `pyproject.toml`
  metadata. **Settled here:** `leaguebot`, installed editable through `pyproject.toml` and
  started with `python -m leaguebot`. `pyproject.toml` holds the packaging alone; coverage keeps
  `.coveragerc`, as CLAUDE.md sets out.
- **Size is a signal, never a target.** Split a file where responsibilities that change for
  different reasons meet, never at a line count. A module thousands of lines long, a service
  imported from twenty files, or one file importing thirty are places to look. They are not
  verdicts. A high fan-in module must be stable and narrow at its surface. A high fan-out one
  knows too much.

## 3. The composition root and dependency injection

- **Construct the object graph once, in one place, at start-up.** Pass dependencies through
  constructors. Nothing is built at import time: no reading the environment, no opening
  connections, no module-level singletons holding state. *Source:* cosmicpython ch. 13
  "Dependency Injection (and Bootstrapping)".
- **Plain constructor injection is idiomatic Python, and a DI framework rarely pays at this
  size.** The current light-weight forms are these:
  - A frozen `@dataclass(slots=True)` holding the services, built once and reached through
    one typed accessor.
  - Typed attributes declared on the `Bot` subclass, which is what `LeagueBot` does today.

  The trade-off to weigh: attributes on the bot are what discord.py's own examples do, but
  they couple every service to the bot object. A container can be built in a test without a
  bot at all. **Settled here:** typed attributes on `LeagueBot`, every service built by one
  builder; the container was rejected, since the services still need the bot for Discord
  (architecture.md, "How the code is laid out").
- **Passing the bot, or the container, into a service is the service-locator anti-pattern.**
  A service that receives `bot` and fishes other services out of it hides what it depends
  on. It also cannot be constructed without the whole graph. A service names the services it
  needs. **Settled here:** a service that needs Discord is given the bot for that and nothing
  more, and is handed the other services it needs (architecture.md, "How the code is laid
  out").
- **Configuration is read once, at start-up, into a typed settings object**, and is passed in
  from there. Nothing reads `os.environ` at import. `src/leaguebot/__main__.py` reading
  `BOT_TOKEN` at import is why `tests/conftest.py` has to plant a placeholder.
- **A base class earns its place by holding behaviour its subclasses share.** The
  `LeagueView`, `LeagueModal` and `LeagueCommandTree` bases are the precedent. Use
  `@typing.override` (PEP 698, 3.12) on every overriding method so a renamed base method
  fails the type check. **Settled here:** no `LeagueCog` base; it would hold two lines, and an
  error handler added to it would switch off the one failure path for every command
  (architecture.md, "Errors and failures").

## 4. Data access

- **All SQL lives in one layer, and every query is parameterised.** Services, or repositories
  beneath them, own it. A cog never does. *Source:* cosmicpython ch. 2 "Repository Pattern" —
  and weigh the cost: a repository per table is a layer of ceremony, which at this size may
  not pay. Confining SQL to services is the principle. Repositories are one way to hold it.
- **Every table has one owning module that writes it.** Other modules read it through the
  owner's service, or through a read model the owner publishes. A table written by two
  modules has two sets of invariants and no single keeper. **Settled here:** a module's own
  columns on a core table are that module's alone, and core does not set them either;
  `tests/repository/test_architecture_rules.py` holds every table's owner and those columns
  (architecture.md, "The database").
- **Keep transactions short, and never `await` a network call inside an open write
  transaction.** SQLite has one writer, so a Discord round-trip inside a transaction holds
  the whole bot's write lock for its duration (#155). Commit first, post second, and record
  what was posted in a transaction of its own. *Source:* SQLite "File Locking And
  Concurrency", and aiosqlite's documentation.
- **One unit of work per service operation.** The operation opens it, commits or rolls back,
  and hands out no connection. Connections are async context managers.
- **Map rows to typed values at the data boundary.** `sqlite3.Row` and plain dicts do not
  leave the data layer.
- **A stored derived value needs a named owner for its recomputation, or is computed on
  read.** Denormalised copies go stale silently when their source is amended. That is #238's
  class of defect (`total_points_after`).
- **External effects that must survive a restart go through an outbox.** Persist the intent
  in the same transaction as the state change, and a sender drains it. *Source:* "Pattern: Transactional
  outbox" (microservices.io); cosmicpython ch. 8–9 for events and the message bus behind it.

## 5. Async and concurrency

- **Never block the event loop.** Run subprocesses with `asyncio.create_subprocess_exec`, and
  push blocking or CPU-heavy work into `asyncio.to_thread`. That includes rasterising, large
  lxml parses and file I/O on the Pi's SD card. `subprocess.run` or `time.sleep` inside an
  `async def` stalls every other interaction. *Source:* asyncio documentation, "Developing
  with asyncio → Running Blocking Code"; ruff rules `ASYNC220`–`ASYNC222`, `ASYNC251`.
- **Keep a reference to every task you create.** The loop holds only weak references, so a
  task created and dropped can disappear mid-run. Its exception must also be observed. Prefer
  `asyncio.TaskGroup` (3.11) where tasks share a lifetime. *Source:* asyncio docs on
  `create_task` ("Save a reference to the result of this function"); ruff `RUF006`.
- **Put timeouts on outbound calls with `asyncio.timeout()`** (3.11), not `wait_for`
  wrappers scattered by hand.
- **Never swallow cancellation.** `except Exception` does not catch `CancelledError` (a
  `BaseException` since 3.8). A handler that catches `BaseException` must re-raise it.
- **Guard in-process critical sections with `asyncio.Lock`, held for as little as
  possible.** Never hold one across a Discord call unless ordering depends on it, and say so
  in the docstring where it does.

## 6. Time-driven work

- **The database is the authority on what is due.** The job store is a cache of it. On
  start-up, reconcile the jobs from the database, so a lost or stale job store costs nothing.
- **Jobs are idempotent and keyed by domain identity.** A job id is derived from the round
  and the event, so re-registering replaces the job rather than duplicating it, and running a
  job twice does no harm (#426, #429).
- **A run missed while the bot was down is either caught up or skipped by an explicit
  rule,** never left to the scheduler's default misfire behaviour. **Settled here:** each module
  tells core's one start-up sweep which of its runs came due; the sweep only hands each, in the
  order they fell due and one at a time, to the handler its module provides for that kind of job,
  and the handler decides what becomes of it (architecture.md, "Timed work and restarts").
- **Inject the clock.** Services take `now`. The house already does this, and CLAUDE.md
  requires tests to pin it. Datetimes are timezone-aware and UTC inside the bot
  (`datetime.now(UTC)`, 3.11 alias), and `zoneinfo` is used only for display. *Source:*
  ruff `DTZ` rules.

## 7. Output to Discord

- **One gateway for writing to channels.** Retries, rate-limit handling, the log-channel
  copy and the failure record live in that gateway, not in each caller. *Source:* ports and
  adapters (cosmicpython ch. 3 "Coupling and Abstractions"). **Settled here otherwise:** one
  small handler for each kind of post (log line, standing post, notice, bot-owned channel),
  one gateway having been rejected as needing every module's rules (architecture.md, "Posting
  to Discord").
- **A post that might later be edited or deleted returns its message identity, and the
  caller persists it.** A `.send()` whose result is thrown away is a message nothing can
  find again, which is #189's class. `tools/architecture_survey.py` counts them.
- **State first, effects second.** Commit the change and then post it. A post that fails
  after the commit is retried or recorded. It never rolls back a league's data.

## 8. Failures and logging

- **Raise specific exceptions and catch specific exceptions.** Each module has a small
  hierarchy under one base. A broad `except Exception` belongs only at a boundary: the single
  failure path, a scheduled job's runner, the output gateway. A broad handler always logs
  with `logger.exception` (or `exc_info=True`) and says why it is broad. *Source:* PEP 8,
  "Programming Recommendations"; ruff `BLE001`.
- **Chain with `raise … from err`.** Add context with `err.add_note()` (PEP 678, 3.11).
  Handle a `TaskGroup`'s failures with `except*` (PEP 654).
- **Give each module its own logger, `logging.getLogger(__name__)`,** with lazy `%`
  formatting in the call and no secrets in the message. *Source:* Python Logging HOWTO.
- **There is one failure path for interactions.** Here that is `report_failure`, reached from
  the `League*` bases' `on_error`. A second path is a divergence.

## 9. Types and data modelling

CLAUDE.md holds the type-check rules. Beyond them:

- **Domain values are `@dataclass(frozen=True, slots=True)`.** String-valued states that are
  stored in the database are `enum.StrEnum` (3.11), so the stored text and the member cannot
  drift.
- **Use `typing.NewType` for identifiers that must not be confused** — a driver profile's id
  and a Discord user id are both `int`. The type check then refuses the swap that #243's
  accounts work was about.
- **Parse, don't validate.** Turn raw input into typed values at the edge (the modal submit,
  the command handler) and pass typed values inward. A service does not re-check a string a
  cog already parsed.
- **Use `typing.Protocol` for seams, and only where a seam is needed** — to break an upward
  dependency, or to stand in a fake in a test. An interface with one implementation and no
  second use is ceremony.
- **PEP 695 generics syntax (3.12)** where a generic is written anew.

## 10. Testing the shape

- **Architecture rules are tests.** Each rule `architecture.md` settles is pinned by a named
  test that fails the build, or it is not a rule.
- **A service is testable without Discord.** If its test needs a fake bot, the service
  depends on too much.
- **Tests mirror the package tree** once services are grouped, so a module's tests are found
  where its code is. **Settled here:** `tests/<module>/`, beside `tests/repository/` for the
  repository's own checks and `tests/support/` for shared helpers (architecture.md, "How the
  code is laid out").

## 11. Tooling, for the record

These are current practice, but each is a new dependency and a CI change. A design pass
raises one as a candidate, with its cost, for the user to decide. It never introduces one on
its own. **Settled so far:** import-linter and `pyproject.toml` are adopted (above); ruff was
declined for now (architecture.md, "How the rules are checked"); a lockfile is undecided.

- **ruff** for linting and formatting. It replaces flake8, isort and black, and its
  `C901`/`PLR0912`/`PLR0915` complexity rules, `BLE001`, `ASYNC`, `DTZ`, `RUF006` and
  `TID251` (banned imports) are the checks cited above.
- **import-linter** for architecture contracts.
- **`pyproject.toml`** (PEP 621) as the single home for metadata and tool configuration.
- **A lockfile** (`uv lock`, or the standard `pylock.toml` of PEP 751), where
  `requirements.txt` pins by hand today.
