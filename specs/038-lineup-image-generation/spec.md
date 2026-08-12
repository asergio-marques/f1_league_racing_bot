# Feature Specification: Lineup Image Generation

**Feature Branch**: `038-lineup-image-generation`
**Created**: 2026-08-12
**Status**: Draft
**Input**: User description: the lineup image type — field catalogue keyed by team name, team-name
constraints, data resolution, mismatch handling, generation and posting, and test data.

> **Normative source.** The rules this feature implements are stated in
> [`docs/wip-specs/image_module_specification.md`](../../docs/wip-specs/image_module_specification.md)
> — § "Conventions of every graphic" and § "Lineup image generation" — and are governed by
> Principles IX and XIV of the constitution. This document does **not** restate those rules. It
> states what must be built, who it is for, and how each obligation is verified, and cites the
> wip-spec where the rule itself lives. Where this document and the wip-spec disagree, the wip-spec
> wins and this document is the one to correct.

> **What makes this type different.** Every image type built so far addresses its repeating members
> by an ordinal. The lineup addresses its teams by the **normalised name of the team**, so that each
> team's block may be hand-designed in that team's own livery. Constitution v4.3.0 admitted that
> form — a **keyed** collection, a **singleton** collection, and a capacity **fixed by the data**
> rather than by the template. This feature is the first to use any of the three, and the first for
> which the bot constrains a value outside the image module so that a template can address it.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preview the lineup graphic before a season depends on it (Priority: P1)

A league manager who has drawn a lineup template against their own team list names it with
`/images template lineup`, runs `/images test lineup`, and receives a PNG built from fabricated
data. They can see every team block filled, one team left entirely empty, an unfilled reserve slot,
flags drawn, and portraits standing in from the directory fallback — without a season, a division
or a real driver existing.

**Why this priority**: This is the whole rendering path — keyed catalogue, nested seats, singleton
reserve block, fill, asset resolution, rasterise — behind one command that depends on no season
data. It is the only slice that can be built and demonstrated on its own, and every later story
reuses it. A manager can author and correct a template with this alone, which matters more here
than for any other image type: the lineup is the one template a league **must** author itself.

**Independent Test**: Enable the images module, name a lineup template drawn against the server's
team list, run `/images test lineup`, and confirm a PNG returns matching the fabricated division
described in the wip-spec's § "Test data".

**Acceptance Scenarios**:

1. **Given** a template declaring every team of the server's team configuration, **When**
   `/images test lineup` is run, **Then** a PNG is returned drawn for a division named "Test
   Division" of tier 1 and season 1, holding exactly those teams and the reserve team.
2. **Given** that fabricated division, **When** it is built, **Then** every team but one is filled
   to its full seat count and one team is left entirely unoccupied, so unoccupied seats can be seen.
3. **Given** a template declaring R reserve slots, **When** the division is built, **Then** R−1
   reserve drivers are fabricated, so an unfilled reserve slot can be seen.
4. **Given** the fabricated drivers, **When** they are built, **Then** their nationalities are drawn
   from those the signup wizard accepts, at least one being the value recorded for a driver who
   stated none.
5. **Given** no fabricated driver has a portrait file in the configured driver image directory,
   **When** the image is drawn, **Then** the directory's `fallback.svg` is drawn on every portrait
   field and a non-fatal error is reported alongside the command output — the field is **not**
   removed.
6. **Given** a fatal error, **When** the command is run, **Then** it is reported to the caller naming
   what is at fault and no image is posted — this command has no textual counterpart and never falls
   back.
7. **Given** the server holds no team beyond the reserve team, **When** the command is run, **Then**
   it is rejected with a clear error.

---

### User Story 2 - Team names that a template can address (Priority: P2)

A league manager adding or renaming a team is stopped at once if the name cannot become a template
field identifier — empty, not starting with a letter, colliding with another team, or claiming the
reserved word. A season review names every team already holding such a name.

**Why this priority**: A name is constrained at the one moment it is set. Without this, a league
authors a template against a name and later finds two teams claiming the same block, or an
identifier an SVG cannot carry. It ships value on its own — it protects a league that has not yet
drawn a template — and it binds whether or not the images module is enabled.

**Independent Test**: With the images module disabled, run `/team add` and `/team rename` with each
invalid shape and confirm each is rejected with a clear error; run `/season review` on a season
holding an offending team and confirm it is named and validation fails.

**Acceptance Scenarios**:

1. **Given** a name that is empty once trimmed, or whose normalised form is empty, **When**
   `/team add` is run, **Then** it is rejected with a clear error and no team is created.
2. **Given** a name that does not begin with a letter, **When** `/team add` is run, **Then** it is
   rejected — an XML identifier may not begin with a digit.
3. **Given** a name normalising to the same value as another team of the same scope, **When**
   `/team add` is run, **Then** it is rejected naming the team it collides with.
4. **Given** a name normalising to `reserve`, **When** `/team add` is run, **Then** it is rejected.
5. **Given** `/team rename`, **When** it is run, **Then** only the **new** name is validated; a
   current name that would fail these criteria still identifies the team and the rename proceeds.
6. **Given** `/team remove`, **When** it is run, **Then** the name it takes is not validated — a team
   named before these criteria came into force must remain removable.
7. **Given** the images module disabled, **When** any of the above is run, **Then** the rejection is
   identical — these constraints are not gated on the module.
8. **Given** a season under setup holding a team that fails these criteria, **When** `/season review`
   is run, **Then** every offending team is named and validation of the season fails.
9. **Given** an already-approved season holding such a team, **When** it is read, **Then** it is not
   re-validated, and no team is renamed or removed by the introduction of these criteria.
10. **Given** a server's team configuration holding no reserve team, **When** that configuration is
    read or written, **Then** a reserve team is created in it.

---

### User Story 3 - Learn a lineup template is unusable before the season depends on it (Priority: P3)

A league manager naming a lineup template is told what it cannot draw. A season review compares it
against every division of the season and refuses approval while a divergence stands, and reports
divisions that field different teams or seat counts.

**Why this priority**: Without it, the first sign of a mismatched template is a season approval that
posts text where a league expected graphics. It depends on the catalogue from US1 but on no posting
path, and it is what makes US4 safe to switch on.

**Independent Test**: Name templates with and without the mandatory division-independent fields and
confirm acceptance or rejection; run `/season review` against a season whose divisions diverge from
the template and from each other, and confirm each divergence is named and approval refused.

**Acceptance Scenarios**:

1. **Given** a template missing a mandatory division-independent field, **When**
   `/images template lineup` names it, **Then** the command is rejected naming the field and the
   configuration is left as it stood.
2. **Given** a template whose team fields diverge from the teams of the season under setup — or from
   the server's team configuration where there is no season — **When** it is named, **Then** the
   divergence is reported as a **warning** and the command succeeds; no division exists to check
   against.
3. **Given** a season whose divisions the template does not match, **When** `/season review` is run,
   **Then** the divergence is a **failure of validation**, naming the division and the team or seat
   at fault, and approval is refused.
4. **Given** a season whose divisions field different teams or different seat counts, **When**
   `/season review` is run with the module enabled and the `lineup` toggle on, **Then** validation
   fails naming the divisions that differ.
5. **Given** that same season with the module disabled or the toggle off, **When** `/season review`
   is run, **Then** the uniformity check is not made and validation is unaffected by it.
6. **Given** a validity report, **When** it is read, **Then** it states which layers were applied, so
   a template checked shallowly is not presented as fully valid.

---

### User Story 4 - The league sees its lineup as a graphic (Priority: P4)

Each division's lineup channel carries a drawn graphic instead of the text embed, redrawn and
replaced on every occasion the textual lineup is refreshed today. A division whose generation fails
gets the textual lineup instead, and the others are unaffected.

**Why this priority**: This is the feature's visible purpose, but it is worth nothing until a manager
can preview a template (US1) and trust it (US3). It carries the highest risk of harming an existing
flow — the lineup channel is refreshed from several places across three modules — so it lands after
both.

**Independent Test**: With the module enabled and the `lineup` toggle on, approve a season, then
assign, unassign and sack drivers, and confirm the lineup channel holds exactly one message
throughout, carrying a PNG; force a fatal error in one division and confirm it alone falls back.

**Acceptance Scenarios**:

1. **Given** the module enabled and the `lineup` toggle on, **When** a season is approved, **Then**
   one graphic is generated per division from the one configured template and posted to that
   division's lineup channel as an attachment.
2. **Given** the toggle is off or the module disabled, **When** the lineup is refreshed, **Then** the
   textual lineup is posted exactly as it is today.
3. **Given** a driver is assigned, unassigned or sacked, **When** the refresh fires, **Then** the
   graphic is drawn anew and replaces the posted message.
4. **Given** the attendance module enforces an autoreserve or autosack sanction, **When** it fires,
   **Then** the lineup is redrawn — the driver has moved team for the remainder of the season.
5. **Given** the attendance module distributes reserves among teams at an RSVP deadline, **When** it
   fires, **Then** the lineup is **not** redrawn — that distribution composes one round's grid, not
   the season's assignment.
6. **Given** the image flow runs and a lineup message is already posted, **When** a replacement is
   produced, **Then** the previous message is deleted **only after** the replacement has been
   produced successfully, and the new message id is persisted.
7. **Given** the image flow runs and the replacement cannot be produced, **When** the refresh runs,
   **Then** the previous message is **not** deleted and the lineup channel is left holding the
   message it had.
7a. **Given** the module is disabled or the toggle is off, **When** the refresh runs, **Then** the
    textual lineup behaves exactly as it does today, deleting before it builds — this feature does
    not reform that path.
8. **Given** a division whose generation meets a fatal error, **When** the refresh runs, **Then**
   that division's lineup is posted as text, the error is reported in the logging channel, and every
   other division is still posted as an image.
9. **Given** a generation raising non-fatal errors, **When** the lineup is posted, **Then** those
   errors are reported in the logging channel naming the division, and **never** in the division's
   lineup channel.
10. **Given** test mode is active with a fake driver roster seated in the divisions, **When** the
    lineup is drawn, **Then** the test drivers are drawn by their test display names.

---

### User Story 5 - Read the lineup on demand and before approving a season (Priority: P5)

`/team lineup` answers with the graphic instead of the text listing, honouring its `public`
parameter. `/season review` posts the graphic alongside its existing textual lineup so a manager can
judge it before approving.

**Why this priority**: Both are conveniences over the lineup of record established in US4, and both
must be careful not to disturb it. They land last because they are the two surfaces where an image
is produced that is explicitly **not** the lineup of record.

**Independent Test**: Run `/team lineup` for one division and for all, with `public` both ways, and
confirm the images arrive with the right visibility; run `/season review` and confirm the graphic is
posted **in addition to** the textual lineup, and that neither command touches the lineup channel or
the persisted message id.

**Acceptance Scenarios**:

1. **Given** the module enabled and the toggle on, **When** `/team lineup` is run for one division,
   **Then** the graphic replaces the textual output and respects the `public` parameter.
2. **Given** `/team lineup` is run for more than one division, **When** it answers, **Then** one
   image per division is posted.
3. **Given** `/season review` is run, **When** it answers, **Then** the graphic is posted **in
   addition to** the existing textual lineup message, not in replacement of it.
4. **Given** either command produces an image, **When** it is posted, **Then** it is not recorded as
   the lineup message of the division and does not cause the message in the lineup channel to be
   deleted.
5. **Given** either command meets a fatal error, **When** it runs, **Then** the command is rejected
   naming what is at fault and nothing is posted in consequence — a commanded posting never falls
   back to text.

---

### Edge Cases

- **The shipped `resources/templates/lineup_template.svg` names fictional teams** (`team_apex_racing`,
  `team_aurora_racing`, …). It is a demonstration of the convention, not a servable default: no file
  shipped with the bot can address teams it does not know. A league naming it unchanged will meet the
  ordinary team-mismatch failure, which must say so plainly rather than reporting a broken template.
- **A team of the division that has recruited nobody.** Drawn with every seat unoccupied, never
  removed. Whether it appears at all is the template author's choice, made by declaring or declining
  that team's `_group`.
- **A division with no reserve drivers at all.** `reserve_group` is removed in its entirety, taking
  every other `reserve_` field with it. This is the ordinary behaviour of a group and raises nothing.
- **A division with more reserve drivers than the template declares slots.** Fatal, naming them. The
  reserve count varies over a season and cannot be known when the template is authored, so this is
  the one lineup collection whose capacity the template fixes.
- **A seat configured but unoccupied.** Its `_name` text is emptied and its `_flag` and `_image`
  fields removed — not omitted as the textual lineup omits it, the layout being fixed.
- **A driver with no nationality recorded.** The `_flag` field is removed and a non-fatal error
  raised. **But** where the league has switched nationality collection off entirely with
  `/signup nationality toggle`, a lineup with no flags at all raises nothing — the absence is
  configured, not a gap (constitution XIV.4).
- **A driver whose Discord account has left the server.** The name resolution chain falls through to
  the signup record, then the username, then the test display name, then the user ID. It never fails
  and never emits a mention, which a picture cannot carry.
- **A nationality recorded for which the flag directory holds no file.** The directory's
  `fallback.svg` is drawn and a notice raised. Absent both, fatal.
- **A league that turns the `lineup` toggle on mid-season with divergent divisions.** The uniformity
  check runs at season review, which an approved season does not repeat, so the divergence is met at
  generation instead: the divisions the template cannot draw fall back to text and the rest are
  drawn. Enabling the aspect must not retroactively invalidate a running season.
- **Two teams of a division normalising to the same key.** Fatal at generation. US2 stops this being
  reachable through the commands, but a season predating US2 may already hold it.
- **A gap in the reserve slot numbering, or a template declaring no reserve slot while
  `reserve_group` is present.** A gap is a fault of the template (constitution XIV.11).
- **A template declaring a `team_<x>_` field for a team the division does not hold**, or a
  `team_<x>_driver_<y>_` field whose `<y>` exceeds that team's seat count. Both fatal — a
  data-fixed capacity diverges fatally in either direction (constitution XIV.12).
- **A driver assigned in more than one division.** Drawn in the graphic of each. A driver may hold at
  most one seat per division, so no driver appears twice in one graphic.

## Requirements *(mandatory)*

### Functional Requirements

**The catalogue and the render**

- **FR-001**: The system MUST declare the lineup's field catalogue as a code constant in the shared
  declaration module (`models/image_catalogues.py`), classifying each field mandatory or optional and
  naming the asset class of each image field, per constitution Rule 10. The fields are those listed
  in the wip-spec's § "Lineup image generation".
- **FR-002**: The catalogue declaration MUST be extended to express three forms it cannot express
  today: a collection **keyed** by a normalised datum rather than an ordinal, a **singleton**
  collection bearing no discriminator (`reserve_`), and a collection whose **capacity is fixed by the
  data** rather than counted from the template (constitution Rules 11 and 12). The existing
  ordinal-and-template-capacity form MUST continue to work unchanged for the calendar.
- **FR-003**: The system MUST address a team's fields as `team_<x>_<field>` and a seat's as
  `team_<x>_driver_<y>_<field>`, where `<x>` is the team name normalised by the module's existing
  slug rule (`utils/asset_resolver.normalise`) and `<y>` is the seat number from 1.
- **FR-004**: The system MUST classify `reserve_driver_<y>_name` mandatory for `<y>` equal to 1 and
  optional beyond it, and MUST classify `reserve_group` **mandatory** — a group the template must
  declare, removed in its entirety when the division fields no reserve driver.
- **FR-005**: The system MUST resolve a driver's name by the first non-empty value of the chain in
  the wip-spec's § "Resolution of the data to be placed", and MUST NOT emit a Discord mention on a
  graphic.
- **FR-006**: The system MUST resolve a driver's flag from the configured flag directory by the
  normalised nationality, their portrait from the configured driver image directory by their Discord
  user ID, and a team's image from the configured team image directory by the normalised team name —
  the reserve team included — each with the three outcomes fixed by constitution Rule 13.
- **FR-007**: The system MUST place drivers within a team in ascending seat number, the reserve team
  included, so that a reserve seat vacated and reused draws in its seat's place rather than in
  joining order.
- **FR-008**: The system MUST empty the `_name` text and remove the `_flag` and `_image` fields of a
  seat that is configured but unoccupied.
- **FR-009**: The system MUST raise no notice for a field emptied or removed because the league
  switched that datum's collection off at its source, and MUST raise the ordinary notice where the
  league collects the datum and merely holds none for that member (constitution Rule 4).

**Team names a template can address**

- **FR-010**: `/team add` and `/team rename` MUST reject, with a clear error and no mutation, a name
  that is empty once trimmed, whose normalised form is empty, that does not begin with a letter, that
  normalises to the same value as another team of the same scope, or that normalises to `reserve`.
  The scope is the server for the server's team list and the division for the teams of a season.
- **FR-011**: Of the two names `/team rename` takes, only the **new** one MUST be validated. The name
  taken by `/team remove` MUST NOT be validated.
- **FR-012**: These constraints MUST hold whether or not the images module is enabled and whether or
  not the `lineup` toggle is on.
- **FR-013**: `/season review` MUST fail validation of the season naming every team — of any division
  of the season, or of the server's team configuration — that does not meet these criteria. An
  already-approved season MUST NOT be re-validated against them, and no team may be renamed or
  removed by their introduction.
- **FR-014**: A reserve team MUST be created in a server's team configuration whenever that
  configuration is read or written and none is present.

**Validity and failure**

- **FR-015**: The system MUST verify a named lineup template at the moment it is named, at season
  review, and immediately before every generation, all three reading one and the same evaluation, per
  constitution Rule 9.
- **FR-016**: At the moment a template is named, the system MUST verify as a **rejection** those
  mandatory fields that do not depend on the teams — `division_name`, `reserve_group`, and at least
  one reserve slot numbered continuously from 1 whose first declares `reserve_driver_1_name` — and
  MUST report a divergence of the team-dependent fields against the season under setup, or the
  server's team configuration where there is no season, as a **warning** only, the command
  succeeding. The reserve block is team-independent, which is what makes it checkable this early.
- **FR-017**: At season review, the system MUST compare the template against **every** division of
  the season, a divergence being a failure of validation naming the division and the team or seat at
  fault, and approval being refused while it stands.
- **FR-018**: At season review, and only where the module is enabled and the `lineup` toggle is on,
  the system MUST additionally verify that the divisions of the season field the same teams and the
  same number of seats in each, a divergence being a failure of validation naming the divisions that
  differ.
- **FR-019**: The system MUST treat each condition listed as fatal in the wip-spec's § "Handling of
  mismatches between division and template" as a fatal error naming what is at fault.
- **FR-020**: The system MUST report non-fatal errors to the server's logging channel naming the
  division, additionally alongside a command's output where a command triggered the generation, and
  MUST NOT report any error in a division's lineup channel.
- **FR-021**: A fatal error met by an uncommanded posting MUST fall back to the textual lineup for
  that division alone; a fatal error met by a commanded posting MUST reject the command with nothing
  posted in consequence. The failure of one division MUST NOT prevent the others being drawn.

**Posting**

- **FR-022**: The system MUST generate one graphic per division from the single configured template
  and post it to that division's lineup channel as an attachment, replacing the textual lineup
  entirely.
- **FR-023**: The system MUST redraw and replace the lineup on every occasion the textual lineup is
  refreshed today: season approval, a driver being assigned, unassigned or sacked, and the
  enforcement of the attendance module's autoreserve and autosack sanctions.
- **FR-024**: The system MUST NOT redraw the lineup on the attendance module's distribution of
  reserves among teams at an RSVP deadline. The graphic carries the season's assignment, not a
  round's grid.
- **FR-025**: **Where the image flow runs**, the system MUST delete the previously posted lineup
  message only **after** the message replacing it has been produced successfully — the graphic, or
  the textual lineup where that flow falls back — and MUST persist the new message id, so that the
  lineup channel holds at most one lineup message and never none.
- **FR-025a**: Where the image flow does **not** run — the module disabled or the `lineup` toggle
  off — the textual lineup MUST keep its present behaviour exactly, including the present
  delete-then-build order in `placement_service._refresh_lineup_post`. FR-025 introduces an ordering
  for the image flow; it does not reform the textual path, which was specified with the current order
  in `specs/028-season-signup-flow/` and is not reopened by this feature.
- **FR-026**: `/team lineup` MUST post the graphic in place of its textual output, respecting its
  `public` parameter, and one image per division where it is invoked for more than one.
- **FR-027**: `/season review` MUST post the graphic **in addition to** its existing textual lineup
  message.
- **FR-028**: The images posted by `/team lineup` and `/season review` MUST NOT be recorded as the
  lineup message of the division and MUST NOT cause the message in the lineup channel to be deleted.

**Test data**

- **FR-029**: `/images test lineup` MUST build the fabricated division described in the wip-spec's
  § "Test data" — named "Test Division", tier 1, season 1, holding exactly the server's team
  configuration including the reserve team; every team but one filled to its seat count and one left
  entirely unoccupied; reserve drivers one fewer than the template's reserve slots; nationalities
  from those the signup wizard accepts including the value recorded for a driver who stated none; and
  no driver holding a portrait file, so the driver directory's fallback and the notice it raises can
  be seen.
- **FR-030**: `/images test lineup` MUST be rejected with a clear error where the server holds no team
  beyond the reserve team, and MUST NOT fall back to text on a fatal error.

**Scope boundaries**

- **FR-031**: The graphic MUST carry no round, session, result, standing, attendance figure or
  Discord mention.
- **FR-032**: The system MUST NOT alter the textual lineup path. It MUST remain functional, and
  behaviourally identical to today, when the module is disabled, the toggle is off, or a render
  fails. The end this feature serves is that a lineup **may** be posted as an image instead of text;
  everything the bot does today when it is not is preserved as it stands.
- **FR-033**: No image type other than the lineup is in scope. The other thirteen unspecified
  templates MUST continue to be checked only to the depth currently ratified for them, and the
  calendar's behaviour MUST be unchanged.
- **FR-034**: No new configuration is introduced. Every value read — template filename, team image,
  flag and driver image directories, the `lineup` toggle — was configured in the 035 increment.

**Test mode**

- **FR-035**: The lineup MUST be fully functional while test mode is active, behaving identically to
  live mode in generation, posting and replacement. No branch on the test-mode flag may be introduced
  into any of the three.
- **FR-036**: The system MUST draw test drivers seated by the fake roster exactly as it draws real
  ones, resolving their names through the chain of FR-005, which reaches the test display name where
  no Discord account and no signup record yields one.

### Key Entities

- **Lineup field catalogue**: the authoritative list of the ids the lineup render addresses, the
  operation each receives, its mandatory/optional classification, the asset class of each image
  field, and the shape of its three collections — teams keyed by normalised name with a data-fixed
  capacity, seats nested under a team with a data-fixed capacity, and the reserve singleton whose
  seats the template's capacity fixes. One entry in the shared catalogue module; the same object read
  by the fill pipeline and by validity checking.
- **Team (server default and division instance)**: read for its name, seat count and reserve flag.
  Its **name** is newly constrained, at `/team add`, `/team rename` and `/season review`. Not
  otherwise modified.
- **Team seat**: read for its seat number and the driver occupying it. Reserve seats are created on
  demand and the lowest vacated one is reused, which is what makes seat order — not joining order —
  the drawn order.
- **Driver profile and signup record**: read for the Discord user id, test-driver flag, test display
  name, recorded server display name, username and nationality. Not modified.
- **Division**: read for its name and tier; its existing `lineup_message_id` is written on every
  lineup posting, textual or graphic, as it is today. No new column.
- **Render notice**: the existing non-fatal record, raised here for a substituted font, a truncated
  name, a removed flag where a nationality is missing but collected, and a team image, flag or
  portrait standing in from `fallback.svg`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A league manager can go from a drawn template to a previewed lineup PNG using one
  command and no season, division or driver data.
- **SC-002**: 100% of the fatal conditions listed in the wip-spec's mismatch section are reported
  naming the specific template, division, team or seat at fault — never a generic failure and never a
  group of templates in place of the one at fault.
- **SC-003**: A lineup refresh in which one division's graphic cannot be drawn still posts every
  other division's lineup as an image, and posts the failing one as text.
- **SC-004**: With the lineup drawn as an image, a division's lineup channel holds exactly one
  lineup message after every refresh, and never zero — verified across a sequence of assignments,
  unassignments and a forced generation failure.
- **SC-005**: No error, fatal or otherwise, ever appears in a channel the drivers of the league read.
- **SC-006**: Every invalid team name shape is rejected at the command that would set it, with the
  images module disabled, so that no league can reach a state where two teams claim one template
  block.
- **SC-007**: Refreshing a lineup with the module disabled or the toggle off produces the textual
  lineup unchanged from the behaviour before this feature, in content, ordering and message
  identity alike. This is the criterion the whole feature is measured against: the image is an
  alternative output, and everything else stands as it is.
- **SC-008**: Every graphic verified during development and in tests is verified as a rasterised PNG,
  never as an SVG in a browser.

## Assumptions

- **The ten shipped default team names all pass the new criteria.** Alpine, Aston Martin, Ferrari,
  Haas, McLaren, Mercedes, Racing Bulls, Red Bull, Sauber and Williams each begin with a letter and
  normalise uniquely, so no existing server is broken by FR-010 and no migration is owed.
- **`normalise()` in `utils/asset_resolver.py` is the one normalisation.** It already implements the
  rule the wip-spec states for `<x>`, and is what resolves asset filenames. Constitution v4.3.0 Rule
  13 fixes that the key and the filename come from the same rule, so no second implementation is
  written.
- **The reserve team is named "Reserve"** (`team_service._RESERVE_NAME`), normalising to `reserve`,
  which is why that word is the one reserved by FR-010.
- **Reserve seats already behave as the graphic needs.** `placement_service` creates a reserve seat on
  demand and reuses the lowest vacated one, so ascending seat number is already the stable order
  FR-007 draws.
- **`/images test lineup` already exists** and renders generic sample data through the shared render
  service. This feature replaces its data source for the lineup kind alone, leaving the other kinds
  as they are.
- **The textual lineup is preserved exactly.** It is an embed listing teams and mentions, refreshed
  from `placement_service._refresh_lineup_post` and its three callers. This feature adds an image
  alternative beside it. It does not restyle the embed, change its trigger conditions, or reorder
  its delete-and-post — that path was specified in `specs/025-signup-expansion/` and
  `specs/028-season-signup-flow/` and is deliberately not reopened here.
- **The lineup rules that live only in `specs/`, not in a wip-spec, are left there.** The textual
  lineup was specified through those two increments and never written back into
  `docs/wip-specs/signup_module_specification.md`. That gap is known and is out of scope: this
  feature reads the current behaviour as the requirement and adds an image path beside it.
- **`/season review`'s existing image-module section is extended, not replaced.** The lineup becomes
  the second image type reported at a depth beyond Layer 1, after the calendar.
- **The shipped `lineup_template.svg` is left as it stands.** It demonstrates the keyed convention
  with fictional teams and is documented as an example to author against, not a default to name. It
  is not rewritten to match the ten default teams, which would make it look servable when a league
  that has edited its team list would still find it wrong.
- **No `/test-mode` command is added or altered.** Every lineup trigger — season approval, driver
  placement, autoreserve, autosack — is already reachable while test mode is active.

## Clarifications Resolved

Three questions were put to the author on 2026-08-12 while amending the constitution to v4.3.0. The
decisions are carried in the requirements above and the rules themselves live in the wip-spec and the
constitution.

- **Team name constraints bind unconditionally** (FR-012), while the **season uniformity check is
  gated** on the module and the `lineup` toggle (FR-018). A name costs nothing to constrain at the
  moment it is set and a league enabling the module later would otherwise hold names it could not
  correct; requiring every division to field identical teams is a real restriction on how a league
  runs its season and is owed only by a league that draws the graphic.
- **A missing driver portrait draws the directory fallback and raises a notice** (FR-006), not a
  silent removal. Constitution Rule 13's three outcomes hold whatever the field's classification, and
  `resources/drivers/fallback.svg` already ships. The wip-spec's test-data sentence was corrected to
  match.
- **A configured absence raises no notice** (FR-009). Where a league has switched nationality
  collection off at its source, a lineup with no flags is exactly what was configured; reporting it
  once per driver on every render would bury the notices that mean something.
