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
- `resources/` is **tracked** and holds what ships: the fifteen default templates and one
  `fallback.svg` per asset directory. No league-specific artwork ships. See
  [resources/README.md](resources/README.md).
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
