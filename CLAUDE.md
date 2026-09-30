# F1 League Racing Bot

A Discord bot for F1 game league racing. Five modules — weather, signup, results & standings,
attendance, and image generation — tied together by seasons, divisions, rounds, teams and
drivers.

## Closing out any change — MANDATORY

**Before reporting any work on this repo complete, invoke the `close-out` skill.**

Three sets of documents carry this project's knowledge and all of them go stale silently:

- `docs/wip-specs/*.md` — **the source of truth for rules.** What the bot shall do, end to end and at a functional level.
- `README.md` — **what a league sees.** Commands, behaviour, authoring conventions.
- `docs/how-to/*.md` — **the order to do a job in.** One guide per module, plus the core bot.
- `docs/design/*.md` — **why the code is shaped as it is.** One file per module.

Update them by *what changed*, not by the fact that something did. A refactor or a
test-only change needs none of them. A changed rule, a new command, or **a decision the user
made in conversation** needs the wip-spec, the README too when a league can see it, and the
owning module's how-to guide when it alters a job a manager does or the order of its steps.

Decisions made in chat are the ones that go missing. When the user answers a question, that
answer is a project rule from that moment, and the chat log is not where it lives.

**But only *functional* decisions reach a wip-spec** (decided 2026-08-27). An engineering
decision — a journal mode, a timeout, how connections are handled, where a file lives — is
not functional specification however deliberately it was taken, and **must not** become a new
wip-spec to house it. Its home is the docstring of the module or function it governs, next to
the code it constrains, where it cannot drift; pin the trade-off with a named test where the
decision is one a later reader might otherwise tune away. If a decision seems to have no
documentation home, that is the answer — not a reason to invent a spec. A wip-spec is owed
only where a league would experience the change.

The `close-out` skill holds the detail: house style for each document, where a rule belongs,
and the checks to run — including the `pytest tests/ -q` run. Read it rather than guessing.

## Documentation layout

| Path | Status |
|---|---|
| `docs/wip-specs/` | **Source for rules, but known stale.** Hand-written. **End-to-end, high-level functional specification only** — what the bot does, as a league experiences it. Edit it when building shows it is wrong. As of 2026-08-17 several are heavily outdated: **where a wip-spec and the implementation disagree, the implementation wins.** Verify against `src/` before quoting one, and correct the document rather than the code. |
| `README.md` | **Source.** User-facing. |
| `docs/wip-specs/core_specification.md` | **Source, and the home of everything no module owns** (decided 2026-09-08). Leagues, seasons, divisions, tiers, rounds, sessions, tracks, teams, seats, drivers, the channels and the log, which modules exist, what survives a restart, and test mode. It absorbed `other_changes.md`, `module_list.md` and the signup spec's Driver Profile, Teams, Changes to Seasons and initialisation sections, all four of which are now deleted or trimmed — do not recreate a grab-bag document beside it. A rule belongs here unless a single module owns it: **core owns the driver, signup owns the signing up**, and core names the modules while each module's own spec keeps its enable, disable and dependency rules. |
| `docs/how-to/` | **Becoming the source for behaviour.** Task-ordered guides for a league manager. A guide owns the *order* of a job. **A module's guide covers that module only:** core setup is named as a prerequisite and linked, never explained, and findings about core behaviour belong in the core guide or the README, not in it. |
| `docs/design/` | **Why the code is shaped as it is** (decided 2026-09-20). `architecture.md` holds what binds every module (decided 2026-09-25, #282), and one file per module holds the rest, pointing to it rather than repeating it: its structure, the alternatives rejected and the trade-offs taken. **Source for intent, never for detail** — where it and the code disagree about what the code *does*, the code wins and the document is corrected, as with a wip-spec. It restates no functional rule, citing the wip-spec's requirement IDs instead. **It documents the design, not its history** (decided 2026-09-25): each decision, and the alternatives it rejected, is written in the section it governs, never gathered in a list at the head; it cites other documentation (the wip-specs, the constitution, CLAUDE.md, other design files, docstrings) and never an issue or a commit; it keeps no register of where the code falls short, which the architecture checks and the tracker hold; and it records no provenance, neither who took a decision nor when nor how, only the decision and its reasons. A decision constraining one function still belongs in that function's docstring, pinned by a named test: the design file holds the shape, the docstring the local constraint. Written before a module is built and corrected at close-out. |
| `docs/how-to/test-mode.md` | **Derived, hand-written.** For maintainers, not leagues — how test mode *is used*, as a walkthrough. Technical register, not strictly a `how-to/` guide, but placed in the same directory for ease. The **rules** test mode holds to live in the core specification's `## Test mode` section (decided 2026-09-08); this guide stays the walkthrough and its overlap with that section is intended. |
| GitHub issues | **The register of defects** (decided 2026-09-09). Records what is wrong, never what shall be done — do not read an issue as a rule, and do not fix one without being asked. Raise one when reading the code turns up a defect out of scope for the task in hand. It replaced `docs/wip-specs/known_issues.md`, whose sixty-six entries were migrated and the document deleted; do not recreate it. `CONTRIBUTING.md` holds the priority levels and what a good issue carries. |
| `specs/NNN-*/` | **Derived, and a historical record.** Spec-kit output per increment. Do not hand-maintain, never copy a wip-spec rule into it, and never read it as current behaviour — it describes one increment as planned, not the bot as it stands. |
| `.specify/memory/constitution.md` | **Governance.** Amend only via `/speckit-constitution` — it carries a version bump and a sync impact report. Never edit by hand. The sync impact reports at the head of the file are a **historical record** and say what was true when each was written: a line inside one is never edited, even where it names a document since deleted or a question since settled. Correct the body of the constitution and let the new report say what became of it (decided 2026-09-10, reaffirmed 2026-09-15). |

**Where the how-to guides are heading** (decided 2026-08-18). The README will come to point readers
*at* the guides, which become the source of truth on bot behaviour and how to use it. The guides are
therefore no longer "derived, never restate a rule": a guide restating a rule the README also carries
is intended, not duplication to prune. The old rule inverted the dependency and is withdrawn.

Two things follow while the change is part-made. Correct **both** documents when behaviour turns out
to differ from what is written — the README is still what a league reads today. And do not delete a
guide's prose on the grounds that the README owns the rule; that reasoning no longer holds.

## Spec-kit task generation

`/speckit-tasks` must never write a task or subtask that requires a live Discord bot — no
"run the bot and invoke the command", no posting to a real channel, no manual check against a
running server. That is full system testing: it happens by hand, after implementation, and is
out of scope for the task list. Verification tasks it does write are automated ones that run
under `pytest`.

Test coverage is **not** optional here, whatever the stock spec-kit template says. Every
implementation task must carry the unit test that covers it — named in the task itself or as
its own task the implementation task depends on — and no story may park its coverage in a
later polish phase.

## Working conventions

- **British English** throughout, in prose and in identifiers alike (`colour`, `normalise`).
- **Verify generated images as PNG, never as SVG in a browser.** The rasteriser exposes
  bugs the browser hides — flowed text, substituted fonts, unresolvable image hrefs.
- Inkscape is the SVG rasteriser. It is installed but its PATH entry is unreliable; the
  code probes the conventional install locations, and the `INKSCAPE` environment variable
  overrides.
- `resources/` splits in two. `resources/defaults/` is **tracked** and holds what ships: the
  fifteen default templates, one `fallback.svg` per asset directory, and the closed-set files
  (marker directions, weather icons, `mystery.svg`). No league-specific artwork ships.
  `resources/league/` is where a league puts its own — a folder per class, kept by `.gitkeep`
  and otherwise **gitignored**, so an update to the bot cannot overwrite it and its contents
  never reach a diff. See [resources/README.md](resources/README.md).
- **Stage paths by name. Never `git add -A`, `git add .` or `git commit -a`** (decided
  2026-09-17). Too much here is ignored or untracked for a blanket stage to be safe: the
  league's artwork above, `poc/` below, and the worktrees under `.claude/worktrees/` — each a
  full checkout carrying its own `.git`, which commits as a bare gitlink pointing at a commit
  nobody else can reach. Run `git status --porcelain` and stage from what it shows. The rule
  binds hardest when several agents work in parallel, where a blanket stage cannot tell one
  agent's change from another's; see the `fix-issues` skill.
- **Move or rename a file with `git mv`, in a commit apart from any change to its content**
  (decided 2026-09-25), so git records a rename and the file's history follows it. The move
  into module folders (`docs/design/architecture.md`, "How the code is laid out") and the split of
  `season_cog.py` depend on it.
- **Every pull request tracks an issue and carries its labels; every release is cut by the
  Release workflow** (decided 2026-09-21, #259). The rules — label groups, the `internal` file
  test, the version scheme, when each bump is due — are in `CONTRIBUTING.md`, under "Pull
  requests" and "Releases", and the required check `pr-label-check` refuses a pull request
  that breaks them. Label a pull request as you open it, not afterwards. Go-live is `v1.0.0`.
  `VERSION` holds a placeholder GitHub fills in on download (#258): never write a value into it.
- **Every issue is built through the `work-issue` workflow** (decided 2026-09-26, #461): through
  the `fix-issue` skill, `fix-issues` for a batch, or `design-review` for a design pass. It checks
  the plan against `docs/design/architecture.md`, the design files and the wip-specs before the
  user sees it, writes the failing tests first, then builds, reviews and tests in rounds until
  they pass, and stops for the user at the plan, the tests and the result. Nothing is built by
  hand around it, unless the user chooses the light path at Gate 1 for a mechanical change or one
  that only applies a decided rule, which `fix-issue` describes. How it runs is the skills' to say.
- `poc/` is **gitignored scratch** — the proof of concept, plus the sample assets and the
  earlier template copies. Not a design input, and never something to port code from. The
  one exception is a *rule* it already encodes: `normalize()` in `poc/build_poc.py` calls
  itself "the spec's normalization" and is the authority on the asset slug. Quote the rule,
  never the implementation. Its scripts still resolve `resources/...` paths from before the
  move and will not run until those are repointed.

## Testing

This section holds the rules. The history behind them, where one is kept, sits in the docstrings of
the tests and the settings files that pin them.

- **`pytest tests/ -q` from the repo root, before and after a change, and compare.** It is expected
  to pass in full, and a full run is cheap: a subset is for iterating, never a substitute. Any
  failure is real: never write one off as pre-existing without confirming it on a clean tree. No
  measured duration or test count belongs in this file: measure when you need to know, and keep the
  answer somewhere cheap to correct.
- **Never run two pytest sessions at once.** They race on the shared schema template and fake a mass
  failure across unrelated modules. Where agents work in parallel, every run goes behind
  `flock -w 3600 /tmp/f1-pytest.lock`, as the `fix-issues` skill gives it.
- **Every change to production code carries its unit tests,** updated or added in the same change,
  and the suite is run. A change reported complete without a run, or leaving tests that no longer
  exercise the new behaviour, is not complete. Every implementation task is covered by a test that
  passes before the next task begins; coverage is never deferred to a polish phase.
- **No test may require a live Discord bot** — tests stub Discord. Full system testing is by hand.
- **Scripts in `tools/` are not unit-tested,** and no tests are added for them, except those a
  workflow runs: `coverage_by_module.py`, `check_pr_labels.py`, `next_version.py` and
  `changed_tests.py`. Bot code a tool uses lives in `src/` and is tested like any other.
- **A test that pins a date pins "now" too**, through the `now` parameter where a service takes one.
- **CI gates on a clean pass, a coverage floor and mypy.** `.github/workflows/unit-test.yml` runs
  the suite on Linux under coverage, gated on failures and errors and on `MIN_COVERAGE_REQUIRED` —
  read the number there, never quote it here — and on Windows, gated on failures and errors only.
  The floor holds for `src/` and for each module: `tools/coverage_by_module.py --fail-under` names
  every module below it, a module being its folder under `src/leaguebot/` (the tool keeps no list of
  its own) and a file outside the package `UNASSIGNED`. A change is not complete if it drops below
  the floor or leaves a test failing or erroring. A new skip needs a reason, and is never a way to
  silence a broken test. To measure by hand (`COVERAGE_CORE=sysmon`, Python 3.12+, is not optional
  on the Pi):

  ```
  COVERAGE_CORE=sysmon python3 -m coverage run -m pytest tests/ -q -m "not rasteriser"
  python3 -m coverage json -q -o coverage.json
  python3 tools/coverage_by_module.py coverage.json --module weather
  ```

- **Only `src/` is measured, and `.coveragerc` alone scopes it** — never `pyproject.toml` or
  `setup.cfg`, which coverage ignores while `.coveragerc` exists (`test_coverage_scope.py`).
- **Before speeding up the Windows job,** read
  `python3 tools/compare_test_timings.py --run <run id>`, which pairs every test across both jobs'
  JUnit reports (kept 30 days).
- **The architecture's rules are tests** (`test_import_contracts.py`, `test_architecture_rules.py`),
  run with the suite, so CI needs no step of its own for them. Each lists today's breaches with the
  issue that fixes them, and fails on a new breach and on a fixed one still listed: fix a breach and
  delete its line in the same commit. `docs/design/architecture.md` says what each rule is and why.
- **mypy checks all of `src/`, not the tests; nothing is exempt, and nothing may be made so.** Run
  `.venv/bin/mypy` from the repository root before reporting a change to `src/` complete. `mypy.ini`
  has no section but `[mypy]`, no library is skipped, and no `# type: ignore` appears in `src/`
  (`test_type_check_config.py`). Where the check cannot see what the code knows, the code says it: a
  narrowing with its reason, a helper that raises by name (`guild_of`, `channel_id_of`, `sole_row`,
  `inserted_id`), a declared type; a `cast` only where true by construction, with a docstring saying
  why (`bot_of`, `as_text_channel`). A `None` that can really happen is a defect: fix it, with a
  test, rather than narrowing it away.
- **The bot is `LeagueBot`** (`src/leaguebot/core/utils/league_bot.py`): every attribute the entry
  point attaches is declared there first, a `bot` parameter is annotated as it, and an interaction's
  bot is reached through `bot_of(interaction)`. A library that ships no types is described in
  `stubs/` (fontTools, APScheduler 3) for the part the bot uses and no more; using more means
  extending the stub, which `test_library_stubs.py` holds to the installed library. lxml uses the
  pinned `types-lxml`.
- **A test must not depend on what the host carries.** The suite runs on Windows, CI and the Pi,
  which differ in libraries, fonts and libxml2. Never assert on the first item an index yields:
  choose with `sorted()`, never dict or `rglob` order. Where the host's resources are the subject,
  pick one that has the property and `pytest.skip` with a reason where none does. Make the test
  host-agnostic rather than bend CI. Pinning `requirements.txt` governs a virtualenv, not apt's
  `dist-packages`.
- **No test may depend on `.env`.** `tests/conftest.py` gives `BOT_TOKEN` a placeholder, so
  `leaguebot.__main__` may be imported at module level; add no per-file default
  (`test_suite_needs_no_dotenv.py`).
- **A test that constructs a `discord.ui.View` or `Modal` is `async def`** (`asyncio_mode = auto`
  needs no decorator); never `asyncio.run()` in a sync test.
- **Run with the virtualenv that carries the pins**, built from `requirements.txt` with the bot
  installed into it (`pip install -e .`), never a system Python importing apt's `dist-packages`;
  `pytest.ini` puts nothing on the path. The bot needs discord.py 2.6 or later, which apt does not
  ship. **A worktree sharing that virtualenv runs with `PYTHONPATH=src`** — `tests/conftest.py`
  refuses otherwise — and never runs `pip install -e .`, which moves every checkout onto the
  worktree's code.
- **The suite keeps no scratch:** `pytest.ini`'s retention settings drop a passing test's `tmp_path`
  as it finishes and keep a failing one's until the session ends, and `tests/conftest.py` sweeps the
  template scratch (`test_scratch_retention.py`). **A mass failure across unrelated modules is a
  full `/tmp` until proved otherwise:** check `df -h /tmp`, and never read an exit code through
  `| tail`.
- **The schema is built once:** `tests/conftest.py` substitutes `run_migrations` with one that
  copies a finished template, migrating in earnest only where the target holds data or the
  migrations have changed. Keep calling `run_migrations`; never run the SQL yourself or loop over
  the migrations directory (`test_migration_steps.py`).
- **A test builds its schema from the production migrations, never from a copy:** `run_migrations`
  on a file under `tmp_path`, not `:memory:`, seeded through `get_connection`, parents first. Never
  write a `CREATE TABLE` for a table the migrations declare; a table of the test's own is fine.
  `test_migration_steps.py` names the exceptions.
- **The schema starts from one baseline,** `src/leaguebot/core/db/migrations/001_baseline.sql`.
  Until go-live (`v1.0.0`) a schema change edits it; from go-live on, every change is a new
  migration numbered after it, with a test of its own, and no applied file is edited.
  `run_migrations` refuses a database recording a migration the bot does not carry; its docstring
  holds the detail.
- **A test that needs Inkscape carries the `rasteriser` marker,** which CI deselects and
  `tests/conftest.py` skips where Inkscape is absent; never a bare `converter_available()` skip.
  Mark only a test that really rasterises (inspects a PNG, asserts pixels or real output paths); one
  that only passes the check monkeypatches `converter_available` to `True` and stays in CI. **Before
  reporting image work complete, run `pytest tests/ -q -m rasteriser`** on a host with Inkscape, and
  verify the output as PNG.
