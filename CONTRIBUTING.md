# Contributing

A Discord bot for F1 game league racing. This guide covers reporting an issue, how issues are
prioritised, and running the tests.

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
`module-image`, or `core` where no single module owns it), and one priority from below.

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

CI runs the suite on Linux and Windows and gates on a coverage floor; read the threshold from
`.github/workflows/unit-test.yml` rather than assuming one.
