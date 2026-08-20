# Contract: the lineup template's fields, ordinal form

The interface a league manager authors an SVG against. The **rules** governing it live in `docs/wip-specs/image_module_specification.md` (Lineup image generation) and are not restated here; this is the field list and the shape of the shipped file.

A field is addressed by an element's `@id`, or by an `inkscape:label` where no such `@id` exists (Constitution XIV.2).

## Fields

`<x>` is a team block, 1 to the number of blocks the template declares, contiguous from 1.
`<y>` is a seat slot within block `<x>`, 1 to the number that block declares, contiguous from 1 **within that block**.

| Field | Class | Notes |
|---|---|---|
| `division_name` | text | Mandatory |
| `season_number` | text | Optional |
| `division_tier` | text | Optional |
| `season_number_group`, `division_tier_group` | group | Optional; removed with their field |
| `team_<x>_group` | group | **Optional.** Wraps every field of block `<x>`. Absent, the fields are removed one by one |
| `team_<x>_name` | text | Mandatory in every block the template numbers |
| `team_<x>_image` | image — `team` | Optional. Resolved from the **normalised team name**, never from the ordinal |
| `team_<x>_driver_<y>_name` | text | Mandatory in every slot the block numbers |
| `team_<x>_driver_<y>_flag` | image — `flag` | Optional. Resolved from the driver's **country** |
| `team_<x>_driver_<y>_image` | image — `driver` | Optional. Resolved from the Discord **user id** |
| `team_<x>_driver_<y>_group` | group | Optional |
| `reserve_group` | group | **Mandatory.** The reserve block is a singleton and carries no ordinal |
| `reserve_name` | text | Optional |
| `reserve_image` | image — `team` | Optional |
| `reserve_driver_<y>_name` | text | Slot 1 mandatory; every slot beyond it optional |
| `reserve_driver_<y>_flag` | image — `flag` | Optional |
| `reserve_driver_<y>_image` | image — `driver` | Optional |

**No field of this graphic bears a datum of any league.** That is the whole of the change, and it is what makes every field verifiable against the template alone, at every moment the template is verified.

## What the shipped file declares

`resources/defaults/templates/lineup_template.svg`, redrawn:

- **11 team blocks**, each wrapped in `<g id="team_<x>_group">`
- **2 seat slots** per block
- **6 reserve slots** in `reserve_group`
- zero team names — no `@id` and no `inkscape:label` in the file names a team of any league, invented or real

Eleven blocks covers the ten default constructor teams with one spare. The file it replaces declares the same 11 × 2 + 6 shape under eleven invented team names, with **no** per-team `<g>` at all (research **R4**), so the redraw is a structural edit and not a rename.

## Migration for a league that authored its own template

A league holding a keyed lineup template must re-author it. There is no compatibility path and none is offered: a keyed template is refused under the new catalogue, and the failure names the ordinal fields it does not declare.

The mechanical part is a rename — `team_red_bull_name` → `team_1_name` — choosing an ordinal per team that matches the order the division holds its teams in. The part that is not mechanical is that a block's artwork may no longer assume which team fills it: a block drawn in Red Bull's livery will, sooner or later, be filled by another team.

## Verification moments

| Moment | Checked | Severity |
|---|---|---|
| `images template lineup <file>` | Structure alone: `division_name`; ≥1 block, contiguous; ≥1 slot in each, contiguous; `team_<x>_name` and `team_<x>_driver_<y>_name` throughout; `reserve_group` with ≥1 slot and `reserve_driver_1_name` | Fatal — the command is rejected |
| Generation | Division team count ≤ blocks; each team's seat count ≤ that block's slots | Fatal — generation abandoned, naming the teams or the drivers dropped |
| `season review` | The same counts, against **every** division | Failure of validation, naming the division and what is at fault |

All three run **whether or not the `lineup` toggle is on**: they report a template that cannot draw the season, and never restrict how a league may compose one. No moment of this graphic compares against a stand-in, so **no divergence of it is ever a warning**.
