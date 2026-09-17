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
- `poc/` is **gitignored scratch** — the proof of concept, plus the sample assets and the
  earlier template copies. Not a design input, and never something to port code from. The
  one exception is a *rule* it already encodes: `normalize()` in `poc/build_poc.py` calls
  itself "the spec's normalization" and is the authority on the asset slug. Quote the rule,
  never the implementation. Its scripts still resolve `resources/...` paths from before the
  move and will not run until those are repointed.

## Testing

`pytest tests/ -q` from the repo root. Run it before and after a change and compare. The suite
is expected to pass in full. Any failure is a real one; do not write it off as pre-existing
without first confirming it on a clean tree.

**A full run is cheap — use it.** `pytest tests/ -q` is some 8,200 tests and finishes in about
eight minutes on the Pi (measured 2026-09-16 at 476s, up from 319s for 4,980 when issue #208
roughly doubled the suite), because the schema-template substitution
described below removed the per-test migration cost. Guidance that a full run costs the better
part of an hour predates that change and is wrong by an order of magnitude; there is no need to
work from a grep-derived subset to avoid it. A subset is a convenience while iterating on one
module, never a substitute for the full run the paragraph above asks for.

**Never run two pytest sessions at once.** They race on the shared schema template described
below, and the loser reads a half-built database — which surfaces as a mass failure scattered
across unrelated modules, not as a lock error. If a run is already going, wait for it rather
than opening a second terminal to check one thing. Where several agents are at work in parallel
— see the `fix-issues` skill — waiting is mechanised rather than left to judgement: every run
goes behind `flock -w 3600 /tmp/f1-pytest.lock`. The rule is unchanged; the lock is only how it
is kept when nobody is watching the clock.

**Every change to production code carries its unit tests with it.** Update or add the tests in
the same change as the code, then run the suite — a production change reported complete without
a test run, or leaving tests that no longer exercise the new behaviour, is not complete.

**Every implementation task is covered by a unit test, and that test passes before the next
task begins.** Coverage is not optional and is not deferred to a later polish phase: a task is
finished when its behaviour is exercised by a test that passes, not when the code is written.
Do not start the next task on a red or absent test.

**No test may require a live Discord bot.** Anything that needs a running bot, a real gateway
connection, or a real server belongs to full system testing, which is done by hand outside this
repo. Tests here stub Discord and exercise the code beneath it.

**Scripts in `tools/` are not unit-tested** (decided 2026-09-16). They are developer tools run
by hand, verified by running them, and a broken one costs a maintainer a rerun rather than a
league anything. Do not add tests for them. The one exception is `tools/coverage_by_module.py`:
CI runs it as the per-module coverage gate, so it is part of the build and keeps its tests. Bot
code a tool happens to use is in `src/` and is tested like any other — the LCH colour maths
`tools/tier_palette.py` relies on is tested in `tests/unit/test_colour_lch.py`.

Tests that pin a date must pin "now" alongside it. Several services accept a `now` parameter for
exactly this; a test that seeds a future date and lets the code read the wall clock passes today
and fails silently months later.

**CI enforces a clean pass and a coverage floor.** `.github/workflows/unit-test.yml` runs the
suite on `ubuntu-latest` under `coverage run` and fails the build if any test fails or errors,
and separately if line coverage falls below that workflow's `MIN_COVERAGE_REQUIRED` value. A
second job runs the same suite on `windows-latest` and gates on failures only — the coverage
floor is one number, and two jobs measuring the same lines would only drift against it. Read
the threshold from the workflow rather than quoting a number here — it moves independently of
this file, and a number written here would go stale the moment someone changes it there. A
change is not complete if it drops the suite below that floor or leaves a test failing or
erroring. A skip is
not itself a build failure, but it is a gap in what "the suite passes" actually verified — treat
a new one as something to justify, not a convenient way to silence a broken test.

**The floor applies to each module as well as to `src/` as a whole** (decided 2026-09-16,
issue #208 — it reverses the earlier "reported, never gated"). One number for the whole
repository is something a module with no tests at all can sit inside unnoticed, which issue
#161 was exactly. `tools/coverage_by_module.py` groups a `coverage json` report by module and,
given `--fail-under`, exits non-zero naming every module below it. CI passes
`MIN_COVERAGE_REQUIRED` to it, so the floor is written once and read at two grains; it runs
before the whole-repo gate and prints the table either way, so the breakdown survives a
failing build. Run it by hand as:

```
COVERAGE_CORE=sysmon python3 -m coverage run -m pytest tests/ -q -m "not rasteriser"
python3 -m coverage json -q -o coverage.json
python3 tools/coverage_by_module.py coverage.json --module weather
```

`COVERAGE_CORE=sysmon` is **not optional on the Pi**: coverage's default C tracer reached 3%
of the suite in ten minutes there, where the sys.monitoring backend ran the whole of it in
under four. It needs Python 3.12+, and both the Pi and CI are on 3.13.

**What is measured is `src/`, and that lives in `.coveragerc`.** The suite is some 36,000
statements and is ~98% "covered" by construction, because a test file's lines are hit by
running it — before #208 nothing scoped the run, so the gate counted the suite and reported
86% while the bot sat at 68.8%, under the floor, on every green build. Do not add a scope to
`pyproject.toml` or `setup.cfg`: coverage reads those first and the two would drift.
`tests/unit/test_coverage_scope.py` pins both halves.

A file matching no rule in the tool is printed as `UNASSIGNED` rather than absorbed into
`core`, and is gated like any other bucket — so add new services to `RULES` when it says so.

**A test must not depend on what the host happens to carry.** The suite runs on three
materially different environments — a Windows development machine, CI's runners, and the
Raspberry Pi 4 the bot runs on — and they differ in library versions, installed fonts and
which libxml2 lxml was linked against. A test that asserts on whatever the host hands it
passes wherever its author sat and fails everywhere else, silently, until someone runs it
elsewhere. So: never assert on the first item an index yields — choose deterministically with
`sorted()`, never dict insertion or `rglob` order — and where the host's own resources are
genuinely the subject, pick one that actually has the property under test and `pytest.skip`
with a reason when none does. Making the tests host-agnostic is the remedy; bending CI to
resemble one host is not (decided 2026-08-26, after twenty-seven tests failed on the Pi alone
and nowhere else).

Note that pinning `requirements.txt` does **not** settle this. A Debian or Raspberry Pi host
that installs from apt imports out of `/usr/lib/python3/dist-packages`, which pip never writes
to, so the pins govern CI and a virtualenv and nothing else.

**No test may depend on `.env`.** A development host carries a gitignored one; CI runners do
not. `src/bot.py` reads `BOT_TOKEN` at import time, so `tests/conftest.py` gives it a
placeholder before collection — import `bot` at module level freely, and do not add a per-file
default. Five test files passed on the Pi and failed collection on both runners before this
(2026-09-16); `tests/unit/test_suite_needs_no_dotenv.py` pins it.

**A test that constructs a `discord.ui.View` or `Modal` must be `async def`.** apt's discord.py
2.5.0 calls `asyncio.get_running_loop()` in `View.__init__`; the pinned 2.7.1 defers it. So a
sync test that builds one passes on CI and raises `RuntimeError: no running event loop` on the
Pi — the version divergence above, in its most common concrete form. `pytest.ini` sets
`asyncio_mode = auto`, so `async def` is the whole fix and needs no decorator; do not reach for
`asyncio.run()` in a sync test, which closes the loop on return and leaves the view bound to a
dead one (decided 2026-09-08, after five such tests failed on the Pi alone).

**The suite keeps no scratch.** `pytest.ini` sets `tmp_path_retention_count = 0` and
`tmp_path_retention_policy = failed`, and `tests/conftest.py` sweeps the template scratch
pytest does not own. The `tmp_path` trees fill the 923 MB tmpfs `/tmp` is on the Pi two
separate ways — three retained sessions under pytest's default, or **one** run's own trees,
which reached 817 MB at some 7,000 tests because every test that migrates a database copies
the schema template into its directory and keeps it until the session ends. Either fails
dishonestly, as 0-byte PNGs and `database or disk is full` scattered across unrelated
modules. The policy drops a *passing* test's scratch as it finishes; a **failing** test's is
kept, so a red run can still be inspected (decided 2026-09-16, measured at 152 MB against
2.9 MB on the same subset). All three mechanisms are pinned by
`tests/unit/test_scratch_retention.py` (decided 2026-09-08).

**A mass failure across unrelated modules is a full `/tmp` until proved otherwise.** The two
mechanisms above exist to prevent it and either can be defeated — by an interrupted run that
left its trees behind, or by something else on the host filling the tmpfs. Check `df -h /tmp`
before reading a broad red run as a regression, and never read a suite's exit code through
`| tail`, which reports the pager's status instead.

**The schema is built once, not once per test.** `tests/conftest.py` substitutes
`run_migrations` with a version that raises the schema a single time and copies the finished
file thereafter. Keep calling `run_migrations` in fixtures exactly as before — the
substitution is transparent, and falls back to migrating in earnest whenever the target
already holds data, or the set of migration files differs from the one a template was built
from. Do not sidestep it by opening a connection and running the SQL yourself: that
reintroduces the cost it exists to remove. Applying every migration and committing after each
runs to some forty flushes per database, which Linux absorbs and Windows does not — it is
what took the `windows-latest` job from three minutes to over an hour.

**A test that needs Inkscape carries the `rasteriser` marker and does not run in CI.** Inkscape
is a separate program, too heavy to install on a hosted runner for what it returns there, so
both jobs in `.github/workflows/unit-test.yml` deselect the marker with `-m "not rasteriser"`
(the Windows runner has no Inkscape either). The marker is
the only mechanism: `tests/conftest.py` also skips marked tests, with a reason, on a local host
that has no Inkscape. Do not write a bare `if not converter_available(): pytest.skip(...)` guard
inside a test — mark it instead.

Mark a test only when it genuinely rasterises — it inspects a PNG, asserts pixels, or asserts
real output paths exist. A test that merely passes through the rasteriser check on its way to
something else is **not** a rasteriser test: monkeypatch `converter_available` to `True` and keep
it in CI, or it will silently stop testing what it claims the moment Inkscape is absent. The
rasteriser check runs first in `render_for_posting`, so without the patch such a test gets a
`RASTERISER` problem instead of the outcome it means to pin.

**Run the marked tests by hand before reporting image-module work complete:**
`pytest tests/ -q -m rasteriser` on a host with Inkscape. CI cannot cover them, so nothing else
will catch a break. Verify their output as PNG, never as SVG in a browser.
