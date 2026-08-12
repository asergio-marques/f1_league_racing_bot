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
- `resources/` is gitignored. Its default *paths* are configuration and legitimate to
  reference; its *contents* are not a design input, except where the user points at the
  proof of concept for a rule it already implements.

## Testing

`pytest tests/ -q` from the repo root. Run it before and after a change and compare — the
suite has pre-existing failures unrelated to most work (as of 2026-08-12: 22, in
`test_attendance_tracking.py`, `test_rsvp_service.py` and `test_season_end_service.py`).
Compare against the baseline you recorded; do not read a non-zero failure count as your own.
