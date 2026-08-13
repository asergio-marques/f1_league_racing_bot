# Contract: the three derived columns

The gap to the leader, the previous position and the position change are the first values a graphic
draws that the textual path does not. Constitution XIV.7 as amended at v4.5.0 admits them as
**presentation** rather than computation, on two conditions. This contract is those conditions made
concrete.

## Where the derivation lives

**In `src/services/standings_service.py`, not in the image module.** The image utility calls it and
holds no arithmetic of its own.

This is the first condition of XIV.7's derived-presentation clause: the derivation is written where
the data live, so the textual standings can adopt the columns later by calling the same function
rather than by growing a second implementation free to drift.

A reviewer's test for this contract: **no subtraction, comparison or sign decision involving points
or positions may appear in `image_standings_service.py`.** It receives the finished record.

## What may be derived, and what may not

| May be derived | May not be derived |
|---|---|
| The difference between two points totals | The **order** of the classification |
| The distance between two recorded positions | The **countback** separating entries level on points |
| The **direction** of that distance | Any points award, eligibility or sanction |

The second column is the second condition: a value requiring a *rule* to reach is a computation and
stays forbidden. The classification's order arrives already settled in the persisted
`standing_position`; the graphic reads it and never re-establishes it.

## The reference round

The record is derived against the **most recent round of the division that holds standings**, at or
below the round being drawn, exclusive of it.

A round recorded as cancelled, and a round yet to be run, hold no standings and are **stepped over**.
Settled by the author this session; written into the wip-spec's § "Standings image generation" so the
rule lives in the source rather than only here.

The alternative — treating the immediately preceding round's absence as undeterminable — would empty
the column for every entry of the graphic drawn after any cancelled round, which reads as a fault
rather than as a rule.

## The record

| Field | Rule |
|---|---|
| `gap_to_leader` | the first-placed entry's points less this entry's, drawn with a leading minus sign. **Empty** for the first-placed entry |
| `previous_position` | the position this entry held in the reference round |
| `change` | the number of positions separating the two, drawn **without a sign**; `0` where neither gained nor lost |
| `direction` | `gained` where the entry now stands higher, `lost` where lower, `unchanged` where the same |

`direction` is the datum the marker asset resolves from — the filename is the direction, normalised
by the ordinary slug rule.

## When it cannot be determined

The whole record is absent — not partially filled — in exactly two cases:

1. **No earlier round holds standings.** The first round of a division, or a division every earlier
   round of which was cancelled.
2. **The reference round's standings do not hold this entry.** A driver who joined mid-season; a
   reserve drawn in for the first time.

Then, per FR-017:

- `row_<x>_position_change_group` is removed in its entirety; or where the template declares no such
  group, `row_<x>_position_change` is emptied and `row_<x>_position_change_marker` removed;
- `row_<x>_previous_position` is emptied.

**No notice is raised.** These are values the data determine to be absent, not values that could not
be determined at all — XIV.3's distinction, and XIV.4 reserves its notice for the latter.

The gap to the leader is **never** in this state: it needs only the classification being drawn, which
always exists when a graphic is drawn at all.

## Rendering

Each of the four is rendered by the same code the textual path would use if it drew them, per XIV.7's
shared-rendering clause. Since the textual path draws none of them today, "the same code" means: the
renderer lives beside the other standings renderers in `results_formatter.py` and is called by the
image path, so that adopting a column into the text path is a call and not a reimplementation.
