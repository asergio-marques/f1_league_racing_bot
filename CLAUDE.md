# F1 League Racing Bot

A Discord bot for F1 game league racing. Five modules — weather, signup, results & standings,
attendance, and image generation — tied together by seasons, divisions, rounds, teams and
drivers.

## Closing out any change — MANDATORY

**Before reporting any work on this repo complete, invoke the `close-out` skill.**

Three sets of documents carry this project's knowledge and all of them go stale silently:

- `docs/wip-specs/*.md` — **the source of truth for rules.** What the bot shall do.
- `README.md` — **what a league sees.** Commands, behaviour, authoring conventions.
- `docs/how-to/*.md` — **the order to do a job in.** One guide per module, plus the core bot.

Update them by *what changed*, not by the fact that something did. A refactor or a
test-only change needs none of them. A changed rule, a new command, or **a decision the user
made in conversation** needs the wip-spec, the README too when a league can see it, and the
owning module's how-to guide when it alters a job a manager does or the order of its steps.

Decisions made in chat are the ones that go missing. When the user answers a question, that
answer is a project rule from that moment, and the chat log is not where it lives.

The `close-out` skill holds the detail: house style for each document, where a rule belongs,
and the checks to run — including the `pytest tests/ -q` run. Read it rather than guessing.

## Documentation layout

| Path | Status |
|---|---|
| `docs/wip-specs/` | **Source for rules, but known stale.** Hand-written. Edit it when building shows it is wrong. As of 2026-08-17 several are heavily outdated: **where a wip-spec and the implementation disagree, the implementation wins.** Verify against `src/` before quoting one, and correct the document rather than the code. |
| `README.md` | **Source.** User-facing. |
| `docs/how-to/` | **Becoming the source for behaviour.** Task-ordered guides for a league manager. A guide owns the *order* of a job. **A module's guide covers that module only:** core setup is named as a prerequisite and linked, never explained, and findings about core behaviour belong in the core guide or the README, not in it. |
| `docs/how-to/test-mode.md` | **Derived, hand-written.** For maintainers, not leagues — how test mode substitutes for a live season. Technical register, not strictly a `how-to/` guide, but placed in the same directory for ease. |
| `docs/wip-specs/known_issues.md` | **Not a spec.** A register of verified defects and oddities in the shipped code. Records what is wrong, never what shall be done — do not read an entry as a rule, and do not fix one without being asked. Add to it when reading the code turns up a defect out of scope for the task in hand. |
| `specs/NNN-*/` | **Derived, and a historical record.** Spec-kit output per increment. Do not hand-maintain, never copy a wip-spec rule into it, and never read it as current behaviour — it describes one increment as planned, not the bot as it stands. |
| `.specify/memory/constitution.md` | **Governance.** Amend only via `/speckit-constitution` — it carries a version bump and a sync impact report. Never edit by hand. |

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
