# Validation guide: image test commands

How to prove this feature works. Everything here runs under `pytest` with Discord stubbed — no task in this feature requires a live bot, a real gateway, or a real server, and full system testing is done by hand outside this repo.

## Prerequisites

- `pytest tests/ -q` green on a clean tree before starting, so any failure met later is known to be new.
- Inkscape resolvable, either on `PATH` or via the `INKSCAPE` environment variable. Tests that rasterise skip without it; tests that resolve, refuse or fabricate do not need it.

## The three things worth proving

### 1. The preview draws the league's data, not invented data

The core claim of the feature. Seed a season with one division, teams and rounds, then assert the drawing carries them.

- Build a `PreviewContext` for a division holding three rounds; assert the calendar drawing holds exactly those three, in configured order, with their tracks and dates.
- Assert the lineup drawing carries the seeded team names and seated driver names.
- Assert every drawing carries the seeded division name, tier and season number.

Assert against the `*Drawing` dataclass each `resolve_drawing` returns, not against the rendered picture. The drawing is where the data lands; the picture is where the template's layout is judged, and that is a separate concern proven by (3).

### 2. Every refusal fires on its own condition, in order

One test per row of the refusal table in [contracts/command-surface.md](contracts/command-surface.md), plus the two ordering cases:

- a wrong round number on a division with no teams reports the **round**, not the teams;
- a mistyped division with a wrong round number reports the **division**.

Assert that no render was attempted — a refusal that renders first and discards the result satisfies the message but not FR-015.

### 3. Assets resolve through the league's configured directories

The decision that makes this a configuration pretest rather than a template check.

- Point an asset directory at a temporary directory holding a file for one datum and no fallback; assert the matching datum resolves to that file.
- Point it at a directory holding a `fallback.svg` and no matching file; assert the fallback is drawn and a `NOTICE_ASSET_FALLBACK_USED` notice names the class, the datum and the field.
- Configure a directory that cannot be resolved; assert the reply names the configured value and the reason, and does **not** report the class as unconfigured (FR-038, research R3).

### Fabrication invariants

Worth their own tests because they are the part a reader cannot eyeball:

- a fabricated classification places every drawn driver exactly once, positions `1..n` with no gap, intervals increasing with position (FR-024);
- a sprint round's phase 2 forecast carries all three session weather types; a two-session round carries two (FR-030);
- a phase 3 forecast carries all five slot types for every non-mystery format, within each session type's `MAX_SLOTS` (FR-031);
- a fabricated verdict's sanction is one of `+5s`, `+10s`, `-3s`, `DSQ` and never anything else (FR-034);
- drivers are fabricated only where the division has seated nobody, and never seat-by-seat (FR-018, FR-020).

**Pin `now` wherever a date is pinned.** Several of these seed rounds at fixed dates; a test that seeds a future date and lets the code read the wall clock passes today and fails silently months later.

## Judging the pictures

Automated tests prove the data and the refusals. They do not prove the picture looks right, and cannot.

Render to **PNG** and look at the PNG. Inspecting the filled SVG in a browser does not satisfy Rule XIV.14 and must not be offered as evidence: the two disagree on flowed text, substituted fonts, unresolvable asset references and the crop — which is most of what this feature exists to let a manager judge.

The cases worth an eye, none of which an assertion covers:

- the calendar's crop at the league's own round count, which is the case SC-005 names;
- a lineup template against the league's real team names, which is the case SC-004 names;
- the verdict's longest free text, at the wrap floor, with its truncation and notice;
- a phase 3 forecast at a format's maximum slot count.

## Regression check

`pytest tests/ -q` green, compared against the baseline taken before starting. The suite stood at 2089 passed, 1 skipped when this plan was written.
