# Quickstart: Validating Lineup Image Generation

How to prove this feature works end to end. Scenarios follow the spec's user-story order, so each can
be run as its slice lands.

> **Verify as PNG, never as SVG in a browser** (Constitution XIV.14). The rasteriser exposes what a
> browser hides — flowed text, substituted fonts, unresolvable asset hrefs. A browser view of a
> filled SVG is not evidence.

## Prerequisites

- Inkscape installed. Its PATH entry is unreliable on the dev host; the code probes conventional
  locations and the `INKSCAPE` environment variable overrides.
- A Discord test server with the bot present, the images module enabled, and a lineup channel
  configured for at least one division.
- A lineup template authored **against that server's own team list**. The shipped
  `resources/templates/lineup_template.svg` names invented teams (`team_apex_racing`, …) and will
  not draw a real division — it is an example of the convention, not a default to name.

```bash
pytest tests/ -q          # expect 1135 passed, 1 skipped before any change
```

---

## Scenario 1 — Preview with no season data (US1)

```
/images config toggle aspect:lineup enabled:True
/images template lineup filename:my_lineup.svg
/images test lineup
```

**Expect**: a PNG attachment drawn for "Test Division", tier 1, season 1.

Open the PNG and confirm:

| Check | Why it is in the test data |
|---|---|
| Every team of the server's list appears, filled to its seat count | the ordinary case |
| Exactly one team is drawn wholly unoccupied | so empty seats can be judged |
| One reserve slot is empty | drivers are fabricated to slots − 1 |
| Every portrait is the same fallback image | no fabricated id has a portrait file |
| A non-fatal error appears **alongside the command output** naming the portraits | XIV.13 fallback notice |
| Flags vary, and at least one is the "Other" nationality | drawn from `NATIONALITY_LOOKUP` |

**Then check the failure paths:**

- Remove every non-reserve team from the server list → the command is **rejected** with a clear
  error. There is no lineup to draw.
- Name a template missing `division_name` → rejected, and the previously configured filename is
  unchanged.
- Delete `resources/drivers/fallback.svg` and rerun → **fatal**, no image posted. A commanded posting
  never falls back to text.

---

## Scenario 2 — Team names a template can address (US2)

Run these with the **images module disabled**, to prove the constraint is not gated on it.

```
/module disable images
/team add name:"" role:@X                    → rejected (empty)
/team add name:"2 Fast" role:@X              → rejected (does not begin with a letter)
/team add name:"Reserve" role:@X             → rejected (reserved)
/team add name:"Red Bull!" role:@X           → rejected if "Red Bull" exists (collides)
/team add name:"Force India (B)" role:@X     → accepted, keys to force_india_b
```

Then confirm the deliberate exemptions:

- `/team rename current_name:"2 Fast" new_name:"Apex"` — succeeds if the old name predates the rule.
  Only the **new** name is validated.
- `/team remove name:"2 Fast"` — succeeds. A team named before the rule must stay removable.
- `/season review` with an offending team present → validation fails, **every** offending team named.
- An already-approved season is not re-validated and no team is renamed or removed.

---

## Scenario 3 — The three verification moments (US3)

| Moment | Setup | Expect |
|---|---|---|
| Naming a template | template omits `reserve_group` | **Rejected**, configuration unchanged |
| Naming a template | template's teams differ from the season under setup | **Warning** below the success line; the filename **is** written |
| `/season review` | template's teams differ from a division | **Validation fails**, naming the division and the team or seat |
| `/season review` | two divisions field different teams, module on and `lineup` toggle on | **Validation fails**, naming the divisions that differ |
| `/season review` | same divergence, `lineup` toggle **off** | Not checked; validation unaffected |
| `/season approve` | any of the above standing | **Approval refused** |

The warning row is the one to watch: the command must **succeed**. A stand-in finding may not refuse
a template that is correct for the season about to be built.

Confirm too that the review states which layers were applied — a template checked to Layer 1 only
must not read as fully valid.

---

## Scenario 4 — The lineup of record (US4)

```
/module enable images
/images config toggle aspect:lineup enabled:True
/season approve
```

**Expect**: each division's lineup channel holds one message carrying a PNG.

Then exercise every trigger, checking after each that the channel holds **exactly one** message:

- `/driver assign` — redrawn
- `/driver unassign` — redrawn
- a sack — redrawn
- attendance **autoreserve** or **autosack** firing — redrawn (the driver has moved for the season)
- attendance **reserve distribution** at an RSVP deadline — **not** redrawn. This is the one trigger
  that must not fire: it composes a round's grid, not the season's assignment.

**Replacement ordering** (FR-025), the subtlest check here:

1. Break the render — rename the configured template file on disk.
2. Trigger a refresh.
3. The previously posted image **must still be there**. The old message is deleted only once its
   replacement has been produced.
4. That division falls back to the textual lineup; the logging channel carries the error; **every
   other division still posts an image**.

**And the path this feature must not disturb** (FR-025a, SC-007):

```
/images config toggle aspect:lineup enabled:False
```

Trigger a refresh and confirm the textual embed behaves exactly as before this feature — same
content, same delete-then-post order, same audit entry. This is the criterion the whole feature is
measured against.

---

## Scenario 5 — Command surfaces (US5)

```
/team lineup division:Division1 public:False   → ephemeral image
/team lineup public:True                       → one public image per division
/season review                                 → image posted *in addition to* the textual lineup
```

Confirm afterwards that neither command touched the lineup channel and that
`divisions.lineup_message_id` is unchanged. These images are command output, not the lineup of
record.

---

## Test-mode check

With test mode active and a fake roster seated, run the whole of Scenario 4. Generation, posting and
replacement must behave identically to live mode — no branch on the test-mode flag exists in any of
the three. Test drivers are drawn by their test display names, reached through the fourth link of the
name-resolution chain.

---

## Regression gate

```bash
pytest tests/ -q
```

Compare against the 1135/1/0 baseline recorded in [research.md](./research.md). Any failure is a real
one; do not write it off as pre-existing without confirming it on a clean tree.

The tests most likely to catch a mistake in this feature:

- **calendar tests** — proof that threading `binding` through `FillSpec` and `_verify_against_data`
  left the ordinal path untouched;
- **placement and attendance tests** — proof that the textual refresh still behaves as specified in
  `specs/028-season-signup-flow/`;
- **team service tests** — proof that name validation rejects the four shapes and exempts the two.
