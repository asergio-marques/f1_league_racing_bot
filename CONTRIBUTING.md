# Contributing

A Discord bot for F1 game league racing. This guide covers reporting an issue, how issues are
prioritised, running the tests, labelling a pull request, and cutting a release.

British English is used throughout, in prose and in identifiers alike (`colour`, `normalise`).

## Reporting an issue

Say what a league actually sees before saying where the code is. Several defects in this bot are
invisible from the code alone and only surface as a confused league manager, so an issue that
opens with a stack trace buries the part that matters.

A good issue carries:

- **What happens** — the behaviour, as a league experiences it.
- **How to reproduce** — the shortest path that gets there.
- **Where it is** — the function and file, once the above are clear.

Label every issue with `bug` (or `feature-request`, `question`, `documentation`), the module it
belongs to (`module-weather`, `module-signup`, `module-results`, `module-attendance`,
`module-image`, `module-stewarding`, `module-stats`, or `core` where no single module owns it),
and one priority from below.

## Issue priorities

The question every level answers: **how much does a league lose, and can they get it back?**

### Four tests, applied in this order

1. **What survives?** Judge the state left behind, not the moment it happens. Something that
   cleans up after itself is not the same as something that persists.
2. **Does the league know?** Silence raises the level. A visible error, or a success message
   honest about what failed, lowers it. The worst case is a success message over a broken
   outcome.
3. **Can they put it right?** A command that recovers it lowers the level. Permanent loss, or
   loss that cannot be undone until the season ends, raises it.
4. **How do you get there?** A path a league walks every season raises it. A narrow combination,
   or something needing an API failure to bite, lowers it.

### The override, and its ceiling

**A stated rule broken is at least High, whatever the blast radius.** Rules exist so the rest of
the system can rely on them, and a rule that holds in most places is not a rule.

**A raise cannot carry a lesser impact into Critical.** Critical is reserved for damage to the
championship and for the module-output rule. Silence and irrecoverability raise a level within
that ceiling — they do not lift a cosmetic or presentational defect to the top. A forecast
published at the wrong time is silent and cannot be put right, and is still not Critical, because
nothing about the championship is wrong.

### The levels

**Critical** — The championship is wrong and stays wrong. Points, standings, results, attendance
records or scheduled work are damaged or destroyed, and either the league is never told or
nothing can put it back inside the season. Also here: a module producing output while switched
off, or failing to produce it while switched on. Fix before a real season runs.

**High** — A command or a pipeline does not do its job. The league is blocked, or told something
untrue, but can see that something is wrong, or can put it right by running something again.

**Medium** — The outcome is correct but described wrongly. Or a way to trigger something by hand
is missing, so a pipeline that has stalled cannot be restarted and the loss is terminal for that
round or season. Or the code and the documents disagree, so a decision is owed before either can
be trusted.

**Low** — Nobody loses anything. Cosmetic duplicates, wasted work, a setting that works but
cannot be read back, internal-only detail, dead code, code with no live caller, and gaps in test
naming or coverage.

A missing surface splits between the two. A value you cannot see is an annoyance and is Low; a
pipeline you cannot restart by hand is a dead end and is Medium.

### Calibration

Real cases, and why each landed where it did.

| Issue | Level | Why |
|---|---|---|
| [#113](https://github.com/asergio-marques/f1_league_racing_bot/issues/113) | Critical | Breaks a stated rule, and marks work done that can never be redone |
| [#114](https://github.com/asergio-marques/f1_league_racing_bot/issues/114) | Critical | Charges penalties in a switched-off module, with no way back inside the season |
| [#110](https://github.com/asergio-marques/f1_league_racing_bot/issues/110) | High | A configured setting is silently discarded, and the pipeline runs on the wrong one |
| [#112](https://github.com/asergio-marques/f1_league_racing_bot/issues/112) | Medium | Right outcome, wrong self-description |
| [#115](https://github.com/asergio-marques/f1_league_racing_bot/issues/115) | Low | The damage self-cleans; a duplicated notice is all that survives |
| [#118](https://github.com/asergio-marques/f1_league_racing_bot/issues/118) | Low | A setting that is applied correctly but has no command to read it back |

Do not carry a priority across from an older record without re-deriving it. Both of the mistakes
worth learning from went in opposite directions: an entry filed as middling turned out to break a
rule and was Critical, and one filed as serious turned out to clean up after itself and was Low.

## Running the tests

```
pytest tests/ -q
```

Run it before and after a change and compare. The suite is expected to pass in full; any failure
is a real one, so confirm it on a clean tree before writing it off as pre-existing.

Every change to production code carries its unit tests with it, in the same change.

**A test must exercise the code it names.** A helper that recomputes production logic in the test
file is a defect in the test, not a convenience: the copy agrees with itself forever, so it cannot
fail when the shipped code is wrong and it cannot fail when the shipped code is deleted — while
still reporting green and counting towards the coverage figure. Call the function. Where it sits
behind decorators, reach its body with `tests/support/undecorate.py`, which exists for exactly that;
reimplementing is not the fallback. `tests/repository/test_no_inline_reimplementation.py` refuses
the least visible form of this, where a test branches on production state and then performs the
action itself.

**A test builds its schema from the migrations.** Call `run_migrations` and seed the rows the test
needs; never write a `CREATE TABLE` for a table the migrations already declare. A hand-written copy
has the columns and none of the constraints, defaults or triggers, so a test on it passes on data
the bot refuses. `tests/repository/test_migration_steps.py` refuses one, and names the few tests
whose subject is the schema itself.

No test may require a live Discord bot. Anything needing a running bot, a real gateway connection
or a real server belongs to full system testing, done by hand outside this repo.

Tests that rasterise SVG carry the `rasteriser` marker and need Inkscape, so CI deselects them
with `-m "not rasteriser"`. Run them by hand on a host with Inkscape before reporting image work
complete:

```
pytest tests/ -q -m rasteriser
```

Verify generated images as PNG, never as SVG in a browser — the rasteriser exposes bugs the
browser hides.

CI runs the suite on Linux and Windows and gates on a coverage floor — applied both to `src/`
as a whole and to **each module separately**, so a thin module cannot hide inside a healthy
average. Read the threshold from `.github/workflows/unit-test.yml` rather than assuming one.
Coverage measures `src/` only; `.coveragerc` says so and explains why.

Before opening a pull request, check where your change left the module you touched:

```
COVERAGE_CORE=sysmon python3 -m coverage run -m pytest tests/ -q -m "not rasteriser"
python3 -m coverage json -q -o coverage.json
python3 tools/coverage_by_module.py coverage.json --fail-under 75
```

CI also runs a type check over `src/`, and gates on it. Run it from the repository root the same
way:

```
mypy
```

**Nothing is exempt from it.** There is no `# type: ignore` in `src/`, no module is excused an
error code and no library is skipped, and tests refuse each. Where the check cannot see something
the code knows, say it in the code — a narrowing with its reason, or one of the helpers that
raises by name — rather than switching the check off.

**The bot is typed as `LeagueBot`** (`src/leaguebot/core/utils/league_bot.py`). Declare an attribute
there before the entry point, `src/leaguebot/__main__.py`, attaches it, annotate a `bot` parameter
as it, and reach an interaction's bot through `bot_of(interaction)`. A library that ships no types
is described in `stubs/`; using more of one means extending its stub, which `stubtest` then holds to
the installed version.

## Pull requests

Every pull request tracks at least one issue and carries that issue's labels. A release's notes
are grouped by those labels, and the required check `pr-label-check` refuses a pull request that
breaks any of the rules below.

- **Name the issue in the description.** `Closes #N` closes it on merge; `Part of #N` names an
  issue the pull request fixes only in part, and leaves it open. An issue linked in the sidebar
  counts as well. A pull request tracking no issue is refused, so a change that has none — a
  document, a tool — gets an issue first.
- **Carry a label from each of three groups**, each taken from an issue the pull request tracks:
  - **work type** — `bug`, `feature-request`, `tech-debt`, `documentation` or `question`;
  - **severity** — `Critical`, `High`, `Medium` or `Low`;
  - **module** — `core` or a `module-*` label.

  A pull request tracking several issues may carry any of their labels, but none that no tracked
  issue has. An issue missing a group has to be labelled before its pull request can pass.
- **`internal` follows the files.** A pull request that changes nothing a league sees must carry
  `internal`, which leaves it out of the release notes; one that changes anything a league sees
  must not. A league sees `src/`, `resources/defaults/`, `docs/how-to/` other than
  `test-mode.md`, `README.md` and `requirements.txt`. A renamed file counts under its old path
  too.

The check runs again whenever the labels, the description or the commits change. To see what it
will say, run it against the open pull request:

```
python3 tools/check_pr_labels.py <number>
```

Every pull request closed before 2026-09-22 was labelled in one pass (#259). Those tracking an
issue carry its labels; those tracking none were labelled by judgement, their severity estimated
after the fact rather than recorded at the time.

## Releases

A release is cut from `main` when there is something worth delivering, and its number says what
kind of thing that is.

**Versions are `vMAJOR.MINOR.PATCH`**, and a release's tag and its name are the same. Only a tag
of exactly that form names a version.

| Bump | When |
|---|---|
| **minor** | A module lands, or a feature a league would notice |
| **patch** | Fixes that should reach a host between minor releases. A Critical fix is released on its own, without waiting for anything else |
| **major** | Before go-live, go-live itself and nothing else. After it, a release that takes away or changes a command, a setting or a file convention a league relies on, or one a host cannot install without doing something by hand |

**A build between releases is `vMAJOR.MINOR.PATCH-N`**, the last release and the number of changes
merged since it, so `v0.4.0-230` follows `v0.4.0-229`. Pull requests are squash-merged, one commit
each on `main`, which is what keeps the number in merge order. Nobody stamps it: `VERSION` holds two
placeholders, the version and the date and time its last change was made, that GitHub fills in
whenever it packages the code, and a clone asks git instead. **Never write a value into `VERSION`**
— see `src/leaguebot/core/utils/version.py`.

**Go-live is `v1.0.0`.** Every release before it is marked a pre-release. From `v1.0.0` on the
migration baseline is frozen, and every schema change is a new migration (see `run_migrations`
in `src/leaguebot/core/db/database.py`).

**To cut one:**

1. In **Actions → Release → Run workflow**, choose `main` and the bump.
2. The workflow refuses unless CI passed on that commit, works out the version from the highest
   tag, and leaves a **draft** release carrying GitHub's notes: every pull request merged since
   the last release, grouped under New features, Fixes and Other changes, with `internal` ones
   left out.
3. Open the draft, write a short summary by module above the generated list, and press
   **Publish**.

**There is no changelog file.** The Releases page is the changelog. A file in the repository
could only be brought up to date through a pull request of its own, which a workflow cannot open
and get checked, so it would fall behind the releases it claims to list.

**A published release cannot be changed.** Releases here are immutable: once one is published,
its tag can be neither moved nor deleted. A draft creates no tag, so put anything right there —
and delete a mistaken draft rather than publishing it.

**An upgrade a host has to act on** is written in the README, beside the behaviour it concerns,
and names the version it applies from.

The four releases before this rule were tagged `*_prototype` and named `v0.1-alpha` to
`v0.4-alpha`. On 2026-09-21 they became `v0.1.0` to `v0.4.0`, the last three republished on the
new tags. A clone still holding the old tags drops them with `git fetch --prune --prune-tags`.
