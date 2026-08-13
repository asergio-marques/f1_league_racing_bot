# F1 League Racing Bot

A Discord bot for F1 game league racing. Five modules — weather, signup, results & standings,
attendance, and image generation — tied together by seasons, divisions, rounds, teams and
drivers.

## Closing out any change — MANDATORY

**Before reporting any work on this repo complete, invoke the `close-out` skill.**

Two documents carry this project's knowledge and both go stale silently:

- `docs/wip-specs/*.md` — **the source of truth for rules.** What the bot shall do.
- `README.md` — **what a league sees.** Commands, behaviour, authoring conventions.

Update them by *what changed*, not by the fact that something did. A refactor or a
test-only change needs neither. A changed rule, a new command, or **a decision the user
made in conversation** needs the wip-spec, and the README too when a league can see it.

Decisions made in chat are the ones that go missing. When the user answers a question, that
answer is a project rule from that moment, and the chat log is not where it lives.

The `close-out` skill holds the detail: house style for each document, where a rule belongs,
and the checks to run. Read it rather than guessing.

## Documentation layout

| Path | Status |
|---|---|
| `docs/wip-specs/` | **Source.** Hand-written. Edit it when building shows it is wrong. |
| `README.md` | **Source.** User-facing. |
| `specs/NNN-*/` | **Derived.** Spec-kit output per increment. Do not hand-maintain, and never copy a wip-spec rule into it. |
| `.specify/memory/constitution.md` | **Governance.** Amend only via `/speckit-constitution` — it carries a version bump and a sync impact report. Never edit by hand. |

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

Tests that pin a date must pin "now" alongside it. Several services accept a `now` parameter for
exactly this; a test that seeds a future date and lets the code read the wall clock passes today
and fails silently months later.
