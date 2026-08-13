# Phase 0 Research: Standings Image Generation

Every decision below was reached against the code as it stands on `main` at 6714041 plus the v4.5.0
constitution amendment. Where a decision changes code outside the image module it is cross-referenced
to [plan.md](./plan.md) § Complexity Tracking.

---

## R1 — Modelling the grid (the load-bearing decision)

**Decision.** Extend the shared declaration module with four additions rather than expressing the
grid inside the standings catalogue:

| Addition | Serves |
|---|---|
| `FieldCatalogue.columns: RowSpec \| None` | the second top-level collection, `round_<z>` |
| `RowSpec.nested: NestedSpec \| None` | `row_<x>_round_<z>_<field>` — the grid proper |
| `NestedSpec.nested: NestedSpec \| None` | `row_<x>_round_<z>_driver_<w>_<field>` — the third level |
| `optional_unit: bool` on `RowSpec` / `NestedSpec` | the round portion being optional as a whole (XIV.3, v4.5.0) |

**Rationale.** `RowSpec` and `NestedSpec` each already model exactly one ordinal, and `NestedSpec`
already takes its parent's id as a `stem` string — so `row_3` and `row_3_round_7` are both valid
stems with no change to `field_id`, `group_id` or `declared_capacity`. The nesting is genuinely
compositional; what is missing is only the *link* between the levels.

The alternative — enumerating the grid's ids in the standings catalogue — was rejected on two counts.
XIV.11 forbids a catalogue expressing a collection as "an enumeration of its members' ids", and
XIV.10 requires the catalogue to be *the same object* validity Layer 2 reads, so a grid known only to
the utility could not be checked at the moment a template is named.

**Alternatives considered.** A generic n-dimensional `GridSpec` replacing both existing classes: more
uniform, but it would rewrite the calendar, lineup and results catalogues to gain nothing they need,
and every one of them is shipped and tested.

---

## R2 — Which ordinals a cell is addressed on

**Decision.** A cell of the drivers grid is `row_<x>_round_<z>_<session>_result`. A cell of the
constructors grid is `row_<x>_round_<z>_driver_<w>_<session>_result`. The round's heading is
`round_<z>_number` / `round_<z>_image`, at top level and **not** under any row.

**Rationale.** The wip-spec states the reason in as many words: a result cell belongs to its row and
to its round both, and a node of an SVG file has one parent. The cell therefore lives under the row,
and the column group carries chrome alone — which is XIV.2's discriminated column group as ratified
at v4.5.0. A template author laying the grid out in columns still numbers the cells by row first,
because the row is what removes them.

**Consequence for removal.** One capacity decision on a round ordinal removes ids from three
families: `round_<z>_group`, `row_<x>_round_<z>_group` for every row, and
`row_<x>_round_<z>_driver_<w>_group` for every car of every row. XIV.12's "one capacity may govern
several id families" is exactly this, and the utility emits all three families into `FillSpec.remove`
from a single decision.

---

## R3 — Per-row car capacity

**Decision.** The cars a round declares are a **ceiling**. For each row, cars beyond that row's
team's configured seat count are removed silently; the fatal test is against the drivers who actually
drove that team's cars in that round.

**Rationale.** One template draws every row, and the rows are different teams with potentially
different seat counts, so no single declared count can satisfy XIV.12's data-fixed branch in both
directions. This is precisely the per-containing-member case v4.5.0 added, and the wip-spec's
§ "The capacity of a collection" was corrected in the same change window to admit it.

**Implementation note.** The seat count is read from the configuration at each check and never frozen
into the catalogue (XIV.12), so `NestedSpec.capacity` stays None and the count arrives through the
binding, as `LineupBinding.seats` already supplies it for the lineup.

---

## R4 — The three derived columns

**Decision.** `standings_service.py` gains a derivation returning, per entry: the gap to the leader,
the previous position, the position change magnitude, and the direction (`gained` / `lost` /
`unchanged`) or `None` where it cannot be determined. `image_standings_service` calls it and performs
no arithmetic.

**Rationale.** XIV.7 as amended at v4.5.0 admits these as *presentation* — arithmetic over figures the
text path already publishes — on two conditions. The first is that the derivation lives with the
data, so the text path can adopt the columns later without a second implementation. Putting it in the
standings service is that condition discharged. The second is that nothing requiring a *rule* is
derived: the countback is already applied in the persisted `standing_position` and is read, never
re-established.

**The step-back rule.** Where the immediately preceding round holds no standings — cancelled, or
never run — the derivation steps back to the most recent round that does, and reports
undeterminable only when no earlier round holds any. Settled by the author this session and written
into the wip-spec's § "Standings image generation". This is a query change, not an arithmetic one:
the snapshot lookup orders by round number descending below the round drawn and takes the first hit.

**Alternatives considered.** Deriving in the image utility: rejected by XIV.7. Adding the columns to
the textual standings in this increment: rejected as scope the spec places out of scope, and not
required by the rule.

---

## R5 — What is checked when

**Decision.**

| Moment | Checked |
|---|---|
| `/images template standings-<kind>` | Layer 1; every non-row mandatory field; ≥1 row, contiguous from 1, each carrying every mandatory row field; rounds (if any) contiguous from 1 and each carrying its number field; cars (if any) contiguous from 1; no sibling-catalogue field |
| `/season review` | the above, plus the row ceiling against the drivers each division would place in its classification, and against its team count |
| Immediately before a render | the above, plus the classification, the division's round count, and the per-round drivers-per-team count |

**Rationale.** XIV.9's structural checks read the template alone, so they are complete at every
moment and refuse at each. The classification does not exist until the round is run and must not be
approximated earlier. The row ceiling is the one check that *is* knowable at season review, because
the division's driver and team counts are configured — which is why FR-043 makes it a validation
failure there rather than a warning.

**The assignment refusal.** FR-044 fires at the command that would grow a division past the ceiling.
This is XIV.12's "rejected at the earliest moment", and it is not a fourth validity moment — v4.5.0
added a sentence to XIV.9 saying exactly that, so a reader does not take the three-moment list as
closing it off.

---

## R6 — Posting two graphics where the text path posts one message

**Decision.** Split `post_standings` into a section-formatting step and a posting step. The image
path posts two messages and persists two ids; the text path composes both sections into one message
as it does today; a per-championship fallback posts that championship's section alone.

**Rationale.** `post_standings` is a single choke point — five call sites, all of which route through
it — so the image branch has one home. But it currently welds composition to posting: it builds one
`content` string from both sections and either edits the existing message or chunks a new one. FR-052
requires a fallback covering the failed championship alone, which that shape cannot express.

**Message ids.** `standings_message_id` is a single column on `driver_standings_snapshots`, written on
the row of the top-ranked driver. A second nullable column sits beside it. Both are written on every
posting so the two flows never disagree; the textual flow leaves the second null, which is also the
state every existing row is already in.

**Replacement ordering.** FR-048: produce the replacement, then delete the old. The existing code
deletes first when the content will not fit one message — that ordering is inverted for the image
path and left alone for the text path, which is not replacing an attachment.

---

## R7 — Where the posting hooks in

**Decision.** Inside `post_standings`, as `post_session_results` hosts the results branch. The five
call sites — first provisional posting, phase closures, `repost_standings_for_division`,
`repost_subsequent_standings`, and the amendment path — need no change, which is what makes FR-049's
seven occasions free.

**Rationale.** Every occasion the spec enumerates already reaches `post_standings`. Hooking there
rather than at each call site is what keeps the seven occasions from becoming seven code paths that
can drift.

---

## R8 — The result cells

**Decision.** A cell carries the finishing position, or `DNF` / `DNS` / `DSQ` from the recorded
outcome. The outcome literal comes from the same renderer the textual table uses (XIV.7). A cell is
emptied — never dashed, never removed — where the round holds no session of that type, the round is
unrun or cancelled, or the driver took no part.

**Rationale.** These are the same four determined-empty cases XIV.3 governs; `FillSpec.empty_quietly`
already carries exactly this meaning and raises no notice, having been added for the results type's
open sanction phases.

**A disqualified driver** carries `DSQ` and not the position the drop to the bottom produced. The
results module persists both the renumbered position and the outcome, so this is a read of the
outcome field and not a reconstruction.

---

## R9 — One driver, one team, for the whole of a round

**Decision.** Constrain it at result submission (FR-065) rather than resolve it at generation.

**Rationale.** The premise that the submission validator already prevents this was checked and found
false. `_validate` in `result_submission_service.py` ties a **seated** driver to
`driver_team_map[driver]` — their seat *at that submission*, from a mapping that can change between
sessions — and **exempts reserve drivers outright**, with a comment stating there is no team-match
restriction for them. No cross-session check exists anywhere in a round. A reserve subbing for team A
in sprint qualifying and team B in the feature race passes today.

That is the same reserve-substitution case the constructors grid's driver-name field exists to depict,
so the graphic cannot treat it as unreachable. Constraining the datum is the owning module's business
(XIV.11); discovering the collision at render time is not.

**Cost, recorded.** A league can no longer record one reserve standing in for two different teams
within one round. Accepted by the author this session and written into
`docs/wip-specs/results_module_specification.md`.

**Forward-only, and that is sufficient.** The check constrains *new* submissions. No backfill,
migration or repair pass is needed: the bot is not yet running in production, so no recorded round can
already hold a driver under two team roles. The constructors utility may therefore treat the invariant
as guaranteed rather than defensively re-adjudicating it, which is what FR-026 relies on.

---

## R10 — The marker asset class

**Decision.** Ship `gained.svg`, `lost.svg` and `unchanged.svg` in `resources/markers/`, beside the
`fallback.svg` already there.

**Rationale.** XIV.13 as amended at v4.5.0: a class whose data are a closed set the module itself
defines is shipped complete by the module, the league having nothing to be incomplete against. It is
the rule `tracks/mystery.svg` already follows. Without it every row of every standings graphic would
draw the fallback and raise a notice — three identical arrows and a notice apiece, which is not a
degradation a league can act on.

**Authoring.** Bound by XIV.6 as any asset: plain SVG, no `clipPath`, gradient or filter, authored at
the slot's aspect, no text (font substitution would reach it). `resources/README.md` lists the marker
class at 64 × 64.

---

## R11 — Test data

**Decision.** `image_sample_data.build_standings_drawing` fabricates both classifications from the
server's team configuration, one entry fewer than the template's row count, with a calendar as long
as the template declares and standing after all but two rounds.

**Rationale.** This mirrors `build_results_drawing`, which already reads the server's teams for the
same reason: a fabricated team name that resolved to no asset file would make every test render
report a fallback notice and teach a league nothing about its own artwork.

**The enumerated cases** — a leader with an empty gap, two entries level on points, a zero-point
entry, a reserve driver, an absent driver, DNF/DNS/DSQ, all three markers, an entry the preceding
standings do not hold — are fitted in order of the spec's enumeration, as many as the declared row
count allows, so a one-row template still produces a valid graphic.

**Rejection.** No team beyond the reserve team means no classification to draw, and the command is
rejected before any render is attempted (FR-063).
