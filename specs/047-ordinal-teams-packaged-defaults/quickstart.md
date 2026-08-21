# Quickstart: validating 047

How to prove this feature works. Every step runs without a Discord bot, except the rasteriser pass, which needs Inkscape and a pair of eyes.

## Prerequisites

- Python 3.13, dependencies installed (`pip install -r requirements.txt`)
- Inkscape, for the marked tests only. Its PATH entry is unreliable; the code probes the usual install locations and the `INKSCAPE` environment variable overrides
- Run everything from the repository root

## 1. Baseline, before touching anything

```
pytest tests/ -q
```

Expect a full pass. The reference taken on this branch before implementation began was **2442 passed**. Any failure here is pre-existing and must be understood before it is attributed to this feature.

## 2. The suite, after

```
pytest tests/ -q
coverage run -m pytest tests/ -q -m "not rasteriser" && coverage report
```

The suite must pass in full, and line coverage must stay at or above `MIN_COVERAGE_REQUIRED` in `.github/workflows/unit-test.yml` (**75** at the time of writing — read it from the workflow rather than trusting this number).

Expect the count to *fall* as well as rise: this feature deletes `KeyedSpec`, `LineupBinding` and `divergent_members` along with the tests that covered them. A drop in the total is correct; a drop in coverage percentage is not.

## 3. The two-tier fallback

The path worth proving by hand, because it is the one a league notices:

```
pytest tests/unit/test_asset_resolver.py -q
```

All four paths of [contracts/asset-resolution.md](contracts/asset-resolution.md) must be exercised, including the negative: a configured directory lacking the datum's file while the **packaged** directory holds a file of exactly that name must resolve to the packaged *fallback*, not to that file.

## 4. The seven graphics that draw a team

```
pytest tests/ -q -k "packaged_fallback or digit_leading"
```

FR-045 requires each of the lineup, both results graphics, both standings graphics, the attendance sheet and the verdict to be exercised — seven, not one. Testing the resolver alone does not satisfy it: the resolver is shared, but each graphic's route to it is not.

## 5. Team ordering

The check most likely to pass by accident:

```
pytest tests/ -q -k "team_order or ordinal"
```

Seed a division whose teams are added **out of alphabetical order**, then add one whose name sorts first, and assert every existing team keeps its ordinal. A test that seeds teams alphabetically passes under the old `ORDER BY name` and proves nothing. See research **R5**.

## 6. Test mode and a season under review

```
pytest tests/ -q -k "test_mode or pending_approval or season_review"
```

Three guarantees, none of them new behaviour and all of them breakable by this change:

- a driver created by test mode is drawn by its **mock name**, at its team's ordinal, and never as an unoccupied seat;
- a division seated wholly by such drivers counts as *having seated drivers* — it must not fall into the "recruited nobody" branch;
- a season **pending approval** draws the same graphic an approved season of identical composition draws.

## 7. The rasteriser pass — by hand, before reporting done

CI cannot run these. Nothing else will catch a break.

```
pytest tests/ -q -m rasteriser
```

Then render the shipped template and **look at the PNG** — never the SVG in a browser, which hides flowed text, substituted fonts and unresolvable image hrefs:

- a division of 11 two-seat teams: every block filled, none removed
- a division of 3 teams: blocks 1–3 filled, blocks 4–11 gone with no gap in the layout
- a division whose second team has recruited nobody: block 2 present, named, badged, both seats blank
- a league supplying no team badges at all: every block shows the packaged fallback, one notice per team

The third case is the one to look hardest at. A team that has recruited nobody must be **drawn empty**, and a block with no team must be **removed** — the two look similar in code and completely different on the page.

## 8. Documentation

Not optional, and covered by the `close-out` skill:

- `docs/wip-specs/image_module_specification.md` — already carries these rules; re-read it against what was built
- `README.md` and `docs/how-to/configuring-the-image-module.md` — the `resources/defaults/` paths, the relaxed team-name rules, and that divisions may now differ
- `resources/README.md` — rewritten for the new split between what ships and what a league adds
- `docs/wip-specs/known_issues.md` — anything found in passing and left unfixed
