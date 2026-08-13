# Feature Specification: Standings Image Generation

**Feature Branch**: `040-standings-image-generation`
**Created**: 2026-08-13
**Status**: Draft
**Input**: User description: the standings image type — two field catalogues addressed by row ordinal,
the driver and constructor championships, three columns derived at generation, the optional
season-results grid, mismatch handling, generation and posting through the standings lifecycle, and
test data.

> **Normative source.** The rules this feature implements are stated in
> [`docs/wip-specs/image_module_specification.md`](../../docs/wip-specs/image_module_specification.md)
> — § "Conventions of every graphic" and § "Standings image generation" — and are governed by
> Principles VII, IX, XII and XIV of the constitution. This document does **not** restate those
> rules. It states what must be built, who it is for, and how each obligation is verified, and cites
> the wip-spec where the rule itself lives. Where this document and the wip-spec disagree, the
> wip-spec wins and this document is the one to correct.

> **What makes this type different.** Standings is the first image type that draws a **grid**. A
> results table is one dimension of members; a standings graphic is a classification crossed with a
> calendar, and a cell of it belongs to a row and to a round both. It is also the first graphic
> carrying **columns the text path does not have** — the gap to the leader, the previous position and
> the position change — and the first whose two graphics are posted where the text path posts one
> message. Constitution v4.5.0 ratified the five forms this requires: the **optional collection**
> (XIV.3), the **discriminated column group** (XIV.2), the **per-member nested capacity** (XIV.12),
> the **module-shipped closed asset class** (XIV.13), and the **derived presentation** with the
> **fallback at the failed graphic's grain** (XIV.7).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preview both standings graphics before a season depends on them (Priority: P1)

A league manager has authored a drivers template and a constructors template and wants to see what
each draws before any season, division, round or submitted result exists. They run
`/images test standings` and get back two PNGs — the driver championship and the constructor
championship — drawn from fabricated data that exercises every case the templates can be asked to
carry: a leader with an empty gap, two entries level on points, an entry on zero, a reserve driver, a
driver absent from one round, a DNF, a DNS and a DSQ, all three position-change markers, an entry the
preceding round's standings do not hold, an unused row, and rounds both run and yet to be run.

**Why this priority**: It is the only way to see a standings graphic without running a season, and it
is what makes every later story cheap to verify. It depends on no lifecycle, no channel and no
posting path, so it can ship and deliver value on its own.

**Independent Test**: Configure both templates on a server that has a team configuration and nothing
else, run `/images test standings`, and confirm two PNGs come back with every enumerated case
visible and every non-fatal degradation listed beside them.

**Acceptance Scenarios**:

1. **Given** both templates are configured and valid and the server holds teams beyond the reserve
   team, **When** a league manager runs `/images test standings`, **Then** two PNG attachments are
   returned — drivers first, constructors second — each labelled "Final Results" for a division named
   "Test Division", of tier 1 and season number 1.
2. **Given** a template declares ten rows, **When** the command runs, **Then** nine entries are
   fabricated and the tenth row is removed, so the rendering of an unused row is visible.
3. **Given** a template declares five rounds, **When** the command runs, **Then** the division holds
   five rounds and stands after the third, so both a round already run and a round yet to be run are
   drawn, with at least one run round of the sprint format and one of the normal format.
4. **Given** a template declares no round at all, **When** the command runs, **Then** the
   classification alone is drawn and no grid is attempted.
5. **Given** the server holds no team beyond the reserve team, **When** the command runs, **Then** it
   is rejected with a clear error and no image is posted.
6. **Given** a fatal error is met, **When** the command runs, **Then** the error is reported to the
   league manager who invoked it and nothing is posted — this command never falls back to text.

---

### User Story 2 - A division's standings posted as two graphics through the round lifecycle (Priority: P2)

A league has the `standings` toggle enabled. Every time the textual standings would be posted or
reposted for a round, two graphics are drawn instead and posted to the division's standings channel —
the driver championship first, the constructor championship second — each carrying its heading and
lifecycle label as message text and its table as an attachment. As the round moves through its
phases, both are redrawn and both posts replaced.

**Why this priority**: This is the feature a league actually runs. It depends on US1 only for
confidence, not for function, and delivers the whole of the type's value on its own.

**Independent Test**: Enable the toggle for a division with a configured standings channel, post a
round's results as provisional, and confirm two image messages appear where one text message did;
then close the penalty phase and confirm both are replaced.

**Acceptance Scenarios**:

1. **Given** the toggle is enabled and both templates are valid, **When** a round's standings are to
   be posted, **Then** two messages are posted to the division's standings channel, drivers first and
   constructors second, each with its graphic attached and its heading and lifecycle label as text.
2. **Given** standings were previously posted, **When** they are to be reposted, **Then** the
   replacing messages are produced first and only then are the previous ones deleted, and the id of
   each replacement is persisted independently of the other.
3. **Given** a round is recorded as cancelled, **When** its standings would be posted, **Then**
   nothing is posted, the toggle notwithstanding.
4. **Given** the drivers graphic fails and the constructors graphic succeeds, **When** an uncommanded
   posting occurs, **Then** the constructor standings are posted as a graphic and the driver
   standings as a textual message carrying that championship's section **alone**.
5. **Given** a command triggered the generation and a fatal error is met, **When** the command runs,
   **Then** the command is rejected, nothing is posted in consequence of it, and the caller is told
   what is at fault.
6. **Given** one division's standings fail, **When** other divisions are posted, **Then** each of the
   others is generated and posted as images in its own right.

---

### User Story 3 - Read the whole season on one graphic (Priority: P3)

A league manager authors a template that carries not just the classification but the results each
entry obtained in every round of the division, laid out as a grid: a column per round, headed by the
round's number and optionally its track image; a cell per session within that round. Rounds yet to be
run are drawn and headed like any other, their cells empty, so a driver reads the season entire and
what remains of it. On the constructors graphic each round's cells stand for the team's cars one by
one, optionally naming the driver who drove each.

**Why this priority**: It is the part of the type a league cannot get from text at all, and the reason
to draw standings as a picture rather than post a table. It is entirely optional — a template
declaring none of it draws a classification alone — so it is a clean, separable slice.

**Independent Test**: Author one template with the round catalogue and one without, run
`/images test standings` against each, and confirm the first draws a grid and the second a bare
classification, with neither reporting a fault.

**Acceptance Scenarios**:

1. **Given** a template declares no field of the round catalogue, **When** a graphic is drawn,
   **Then** a classification alone is drawn and no fault is reported.
2. **Given** a template declares rounds, **When** a graphic is drawn, **Then** every round the
   division holds is drawn and headed by its number, whether it has been run or not.
3. **Given** a template declares more rounds than the division holds, **When** a graphic is drawn,
   **Then** each excess round's heading group, each row's cell group for that round and — on the
   constructors graphic — each car group of that round are removed, and no error is reported.
4. **Given** the division holds more rounds than the template declares, **When** a graphic is drawn,
   **Then** it is a fatal error naming the rounds that would have been dropped.
5. **Given** a round of the constructors graphic declares more cars than a team has seats, **When**
   that team's row is drawn, **Then** the excess car groups are removed for that row alone and no
   error is reported — a template serves rows whose teams have differing seat counts.
6. **Given** more drivers drove a team's cars in a round than the template declares cars, **When**
   the graphic is drawn, **Then** it is a fatal error naming them and the round.
7. **Given** a round already holds an ACTIVE session recording a driver for one team, **When** another
   session of that round is submitted recording that same driver for a different team, **Then** the
   submission is rejected naming the driver, the team already recorded and the conflicting session —
   so the grid never has to decide which team claims them.

---

### User Story 4 - Learn a standings template is unusable before a season depends on it (Priority: P4)

A league manager configures a template that is missing a mandatory field, declares no row, numbers its
rows with a gap, or carries a field belonging to the other championship's catalogue. They are told at
the moment they configure it, not at the moment a round is posted. A season whose divisions would
place more drivers in a classification than the drivers template has rows fails validation at
`/season review`, naming the division; and the command that would seat the driver carrying it past
that ceiling is rejected with the assignment unapplied.

**Why this priority**: It protects a league from finding out mid-season, but a league can run without
it by testing carefully. Independently valuable and independently testable.

**Independent Test**: Configure a deliberately faulty template and confirm the configuration is left
as it stood with a named reason; then seat drivers up to and past a template's row count and confirm
the last command is refused.

**Acceptance Scenarios**:

1. **Given** a template lacks a mandatory field, **When** `/images template standings-drivers` names
   it, **Then** the command is rejected, the reason names the field, and the configuration is left as
   it stood.
2. **Given** a drivers template carries a field of the constructors row catalogue, **When** it is
   named, **Then** it is rejected as the wrong file for that slot.
3. **Given** a template numbers its rows 1, 2, 4, **When** it is named, **Then** it is rejected for a
   gap in the numbering — and likewise for a gap in the rounds or in the cars of a round.
4. **Given** no classification yet exists, **When** a template is verified, **Then** only the
   structural checks are made: at least one row, contiguous numbering from 1, every mandatory field of
   a row present, and each declared round carrying its number field.
5. **Given** a division would place more drivers in its classification than the drivers template has
   rows, **When** `/season review` runs, **Then** validation fails naming that division, and approval
   is refused while it stands.
6. **Given** a division's classification is one short of the template's row count, **When** a command
   would seat one more driver in it, **Then** the command is rejected and the assignment is not
   applied.
7. **Given** either template is invalid, **When** `/season review` or `/images config view` reports,
   **Then** the report says which of the drivers and constructors templates is at fault.

---

### User Story 5 - Degradations reported to staff, never drawn for drivers (Priority: P5)

A driver's flag is missing, a track image has no file, a name is too long for the room its field
declares. The graphic is still drawn, and every such degradation is reported in the server's logging
channel — naming the season, the division, the round and the championship it pertains to — and never
in the standings channel a driver reads.

**Why this priority**: It makes the module safe to run unattended, but the graphics are useful before
it is complete.

**Independent Test**: Draw a standings graphic for a division with a driver whose nationality has no
flag file, and confirm the graphic is produced, the fallback drawn, and the notice appears only in the
logging channel.

**Acceptance Scenarios**:

1. **Given** a nationality has no file and the flag directory holds a fallback, **When** a graphic is
   drawn, **Then** the fallback is drawn and a notice is raised naming the field and the datum.
2. **Given** a driver has no nationality recorded, **When** a graphic is drawn, **Then** the flag field
   is removed and a non-fatal error is reported.
3. **Given** nationality collection is switched off entirely at `signup nationality toggle`, **When** a
   graphic is drawn, **Then** no flag is drawn anywhere and **no** error whatsoever is reported.
4. **Given** any notice is raised, **When** it is reported, **Then** it appears in the logging channel
   and never in a division's standings channel; and where a command triggered the generation, it also
   appears alongside that command's output.

---

### Edge Cases

- **The first round of a division.** No preceding standings exist, so the position change cannot be
  determined for any entry: each row's position-change group is removed, or where none is declared the
  number is emptied and the marker removed, and the previous position is emptied. This is not a fault
  and raises no notice.
- **An entry the preceding round's standings do not hold** — a driver who joined mid-season, a reserve
  drawn in for the first time. Treated exactly as the first round is, per row rather than per graphic.
- **The preceding round has no standings of its own** because it was cancelled or never run. The
  graphic steps back to the most recent round that does hold standings and compares against that, so
  one cancelled round does not blank the column for every entry.
- **A driver recorded under two different team roles within one round** — qualifying for one team, the
  race for another. This is prevented at its source rather than resolved at generation: the submission
  recording the second team is rejected (FR-065). The constructors grid therefore never meets the case,
  and the wip-spec's "never on the cars of two teams" becomes an invariant the data guarantee rather
  than a rule the graphic enforces.
- **A template declaring exactly one row.** One entry is fabricated by the test command and the unused
  row is left unevaluated.
- **A division with no rounds yet.** The classification is drawn; a template declaring rounds has every
  round group removed and reports nothing.
- **A driver seated in a team who drove no session of a round** leaves that car free, and a driver not
  seated in the team is placed on the lowest-numbered car left free.
- **A car no driver drove in a round** has its group removed, or its name and cells emptied where no
  group is declared.
- **A round holding no session of a given type** — a normal-format round has no sprint sessions — leaves
  those cells emptied, not removed and not dashed.
- **An entry disqualified from a session** carries "DSQ", never the position the drop to the bottom gave
  them.
- **Both graphics fail.** Each falls back independently; the textual standings posted carry both
  sections, which between them is the whole of what the text path normally posts and nothing repeated.
- **The marker directory has been repointed by a league to a directory holding none of the three
  direction files and no fallback.** The render is abandoned per XIV.13, exactly as any other class.

## Requirements *(mandatory)*

### Functional Requirements

#### The two field catalogues

- **FR-001**: The system MUST declare two field catalogues — one for the `standings-drivers` template
  slot and one for `standings-constructors` — as code constants in the module's shared declaration,
  each separately addressable and each naming its own fields in full.
- **FR-002**: The two catalogues MUST share the declaration of the fields they hold in common — the
  season number, division name and tier, round number, race name and result status — and MUST NOT be
  one entry carrying a branch.
- **FR-003**: Both catalogues MUST declare the `row` collection discriminated by **ordinal**, numbered
  continuously from 1, its capacity fixed **by the template**.
- **FR-004**: Both catalogues MUST declare the `round` collection and the per-row cell collections it
  governs as **optional as a unit**, naming the collection at which the optional portion begins, so
  that a template declaring none of it draws a classification alone and is not faulty for it.
- **FR-005**: A field the catalogue classifies mandatory **within** the optional round portion — the
  round's number field — MUST be required only where the template declares that round at all.
- **FR-006**: The constructors catalogue MUST declare the `driver` collection nested inside a round of
  a row, its capacity fixed **by the data per containing member**: the seats configured for the team on
  that row.
- **FR-007**: The system MUST treat a field belonging to the **sibling** catalogue's row as a fatal
  error at the moment the template is named — the wrong file in that slot. An id belonging to neither
  catalogue MUST be ignored.
- **FR-008**: The system MUST fill each row's position field **from the ordinal** of that row, making
  no comparison between it and the recorded standing position.
- **FR-009**: The system MUST draw no image of the track on the classification, no date of any round,
  no name of a points configuration, and no result of any session beyond the declared cells. The
  constructors graphic MUST carry no driver nationality and no team result in a session.

#### Data resolution

- **FR-010**: The system MUST read the position and the points from the standings recorded for the
  round being drawn, and MUST NOT recompute either. Entries level on points are already separated by
  the countback in that record, and two entries never share a position.
- **FR-011**: The driver classification drawn MUST be composed exactly as the textual driver standings
  compose it: every non-reserve driver of the division, at zero points as at any other, and a reserve
  driver only where the division's reserves toggle is on and the driver holds points or has taken part
  in a race.
- **FR-012**: The constructor classification drawn MUST be composed exactly as the textual team
  standings compose it: every non-reserve team of the division, at zero points as at any other.
- **FR-013**: Every value the graphic draws that the textual standings also draw MUST be produced by
  the same formatting code the textual path calls, not by a second implementation.
- **FR-014**: The system MUST derive the gap to the leader as the first-placed entry's points less the
  entry's, rendered prefixed with a minus sign, and MUST empty it for the first-placed entry.
- **FR-015**: The system MUST derive the previous position and the position change against the
  standings recorded for the **preceding** round, the change being the number of positions separating
  the two, drawn without a sign, and "0" where the entry neither gained nor lost. Where the
  immediately preceding round holds no standings — it was cancelled, or never run — the system MUST
  step back to the most recent round that does, and MUST treat the change as undeterminable only when
  no earlier round holds standings at all.
- **FR-016**: The derivations of FR-014 and FR-015 MUST be written in the service that owns the
  standings, never in the image generation utility, so the textual path can adopt the columns without
  a second implementation. They MUST read the preceding round's record as it was persisted and MUST
  NOT recompute it.
- **FR-017**: Where the position change cannot be determined — the first round of a division, or an
  entry the preceding standings do not hold — the system MUST remove the row's position-change group in
  its entirety; where the template declares no such group, it MUST empty the number and remove the
  marker. The previous position field MUST be emptied in both cases. Neither raises a notice.
- **FR-018**: The system MUST draw the name of a person as the wip-spec's convention requires, carrying
  no Discord mention, and MUST reach the same name wherever it draws that person on one graphic.
- **FR-019**: The system MUST place, and normalise for the team image, the name of a team as the
  wip-spec's convention requires — the division's team holding the Discord role the record carries,
  falling back to the name of the role itself.
- **FR-020**: The team drawn on a row of the **drivers** graphic MUST be the team of the division
  seating that driver at the moment of generation — the reserve team for a reserve driver — and never
  the team whose car they drove in any one round.
- **FR-021**: The system MUST empty the text of a field whose value does not apply, and MUST NOT draw a
  dash in its place.

#### The season results grid

- **FR-022**: The system MUST draw every round the division holds, not only those already run, heading
  each by its number. A round yet to be run, and a round recorded as cancelled, MUST keep its group and
  carry emptied cells.
- **FR-023**: The system MUST place in a result cell the finishing position recorded for the driver the
  cell stands for in that session of that round, or "DNF", "DNS" or "DSQ" where that is the recorded
  outcome. An entry dropped to the bottom of a session by a disqualification MUST carry "DSQ".
- **FR-024**: The system MUST place exactly one cell per session, and MUST empty a cell where the round
  holds no session of that type, the round is yet to be run or cancelled, or the driver took no part in
  that session.
- **FR-025**: The system MUST identify a round of the grid by its number alone, drawing its image only
  in addition where the template declares one, and MUST carry no grand prix name for any round of the
  grid.
- **FR-026**: On the constructors graphic, the system MUST resolve each round's cars against that round
  alone: the drivers who drove are those whose result in a session of that round records that team's
  Discord role; a seated driver is placed on the car of their seat ordinal and leaves it free if they
  drove no session; a driver not seated in the team takes the lowest-numbered car left free; and no
  driver is placed on two cars nor on the cars of two teams. The last of these is guaranteed by FR-065
  and MUST NOT be re-adjudicated at generation.
- **FR-027**: The system MUST remove the group of a car no driver drove in a round, or empty that car's
  name and cells where the template declares no such group.

#### Assets

- **FR-028**: The system MUST resolve a driver flag from the configured flag directory by the
  normalised nationality, and MUST remove the field with a non-fatal error where no nationality is
  recorded. Where nationality collection is switched off at its source, no flag is drawn anywhere and
  **no** error is reported.
- **FR-029**: The system MUST resolve a team image from the configured team image directory by the
  normalised team name of FR-019.
- **FR-030**: The system MUST resolve a round's image from the configured track image directory as the
  calendar graphic does, naming the round's number in any error reported.
- **FR-031**: The system MUST resolve a position-change marker from the configured marker directory by
  the direction of the change — `gained`, `lost` or `unchanged`.
- **FR-032**: The module MUST ship `gained.svg`, `lost.svg` and `unchanged.svg` in the packaged marker
  directory beside its `fallback.svg`, the three being a closed vocabulary the module defines rather
  than values a league supplies.
- **FR-033**: `README.md` and `resources/README.md` MUST name the three marker files as shipped
  reserved filenames, beside `tracks/mystery.svg`.

#### Validity and mismatches

- **FR-034**: The system MUST verify the fields that do not depend on a classification at every moment
  a template is verified, a missing mandatory one being fatal.
- **FR-035**: At the moments no classification exists, the system MUST verify only that the template
  declares at least one row, that its rows are numbered continuously from 1 and each carries every
  mandatory field of a row, that any rounds it declares are numbered continuously from 1 and each
  carries its number field, and that any cars a round declares are numbered continuously from 1.
- **FR-036**: The system MUST verify the classification-dependent fields against the classification
  being drawn, immediately before the render.
- **FR-037**: The system MUST remove in its entirety the group of a row declared in excess of the
  entries of the classification, taking every other field of that row with it, and MUST report no
  error.
- **FR-038**: The system MUST treat entries in excess of the rows a template declares as fatal, naming
  the drivers or the teams that would have been dropped.
- **FR-039**: The system MUST remove, for a round declared in excess of the rounds the division holds,
  that round's heading group, every row's cell group for it and — on the constructors graphic — every
  car group of it, reporting no error; and where the template declares no group for an ordinal, MUST
  remove every field bearing that ordinal one by one instead.
- **FR-040**: The system MUST treat rounds of the division in excess of those a template declares as
  fatal, naming them.
- **FR-041**: The system MUST remove, per row, the car groups a round declares in excess of the seats
  configured for that row's team, reporting no error; and MUST treat drivers who drove a team's cars in
  a round in excess of the cars the template declares for it as fatal, naming them and the round.
- **FR-042**: The system MUST treat each of the following as fatal, naming what is at fault: a
  mandatory field the template does not hold; a template declaring no row at all; a gap in the
  numbering of the rows, the rounds or a round's cars; a field of the other championship's row
  catalogue; and a mandatory field whose value cannot be determined at generation.
- **FR-043**: `/season review` MUST fail validation, naming the division, where a division of the
  season would place more drivers in its classification than the configured drivers template has rows,
  and season approval MUST be refused while that stands.
- **FR-044**: A command that would carry a division's classification past the rows its configured
  template declares MUST be rejected with its change unapplied — a driver assignment against the
  drivers template, and a team assignment against the constructors template.
- **FR-045**: `/season review` and `/images config view` MUST report which of the drivers and
  constructors templates is invalid, never the pair as one.

#### Generation and posting

- **FR-046**: With the `standings` toggle enabled, the system MUST generate both graphics wherever the
  textual standings are posted and post them to the division's standings channel as two messages —
  drivers first, constructors second — each carrying its heading and lifecycle label as message text
  and its graphic as an attachment.
- **FR-047**: The system MUST persist the id of each of the two messages independently of the other,
  so either may be deleted and replaced without disturbing its sibling.
- **FR-048**: Where the textual flow edits the standings message in place, the image flow MUST instead
  delete and repost, producing the replacing messages **before** deleting the message they replace —
  whether the replacement is a graphic or a textual fallback.
- **FR-049**: The system MUST redraw and replace both posts on every occasion the textual standings
  are currently reposted: first provisional posting, penalty phase closure, appeal phase closure,
  standings resynchronisation by command, approval of a session amendment, a points configuration
  change causing recalculation, and that recalculation cascading to following rounds.
- **FR-050**: The system MUST post nothing for a round recorded as cancelled, the toggle
  notwithstanding.
- **FR-051**: The system MUST replace the textual standings in the division's configured standings
  channel and there alone, and MUST leave the results posted alongside them to the results type, the
  failure of one preventing neither the other.
- **FR-052**: The failure of one championship MUST NOT prevent the other. Where one falls back, its
  textual message MUST carry that championship's section **alone**, and MUST NOT repeat what the
  surviving graphic carries.
- **FR-053**: The system MUST report non-fatal errors in the server's logging channel, naming the
  season, the division, the round and the championship, and never in a division's standings channel;
  and additionally alongside the output of a command that triggered the generation.
- **FR-054**: The system MUST fall back to the textual standings on a fatal error in a posting no
  command triggered, and MUST reject the command and post nothing where a command did trigger it,
  reporting to the logging channel and to the invoking user.
- **FR-055**: The failure of one division MUST NOT prevent the others being generated and posted as
  images.
- **FR-056**: Where a generated image fails to post for a reason of the Discord service rather than of
  the generation, it is the **textual** standings that MUST be enqueued for retry.

#### Test data

- **FR-057**: `/images test standings` MUST generate two images — one per template — for a division
  named "Test Division", of tier 1 and season number 1, both labelled "Final Results", holding as many
  rounds as the template declares and standing after all but two of them.
- **FR-058**: Where a template declares fewer than three rounds, the division MUST hold the rounds it
  declares and stand after the first; where it declares none, the classification alone MUST be drawn.
- **FR-059**: At least one round already run MUST be of the sprint format and at least one of the
  normal format, so a round of four sessions and one of two are both evaluated.
- **FR-060**: The command MUST fabricate one entry fewer than the rows the template declares, drawn
  from the server's team configuration; where a template declares a single row, one entry MUST be
  fabricated and the unused row left unevaluated.
- **FR-061**: The fabricated entries MUST exhibit the cases the wip-spec's § "Test data" enumerates for
  each championship, insofar as the declared row count allows.
- **FR-062**: The nationalities given MUST be among those the signup wizard accepts, at least one being
  that recorded for a driver who stated none.
- **FR-063**: The command MUST be rejected with a clear error where the server holds no team beyond the
  reserve team.
- **FR-064**: A fatal error met by `/images test standings` MUST be reported to the invoking league
  manager with no image posted, this command being the one exception to the fallback rule.

#### The upstream data invariant

- **FR-065**: Result submission MUST reject a session that would record a driver under a different team
  role than another ACTIVE session of the same round already records for them, naming the driver, the
  team already recorded and the conflicting session. This is the one requirement of this feature that
  falls outside the image module. The existing per-session check ties a **seated** driver to their
  current seat but is evaluated against a mapping that can change between submissions, and exempts a
  **reserve** driver from any team restriction at all — so neither closes this case today. Constraining
  the datum belongs to the module that owns it; discovering the collision at render time does not.

### Key Entities

One entity is amended; no entity is introduced.

- **DriverStandingsSnapshot** — **amended**. Gains a second message-id column beside the existing one,
  so the two championship graphics may be replaced independently. Both are set on the row of the
  top-ranked driver, as the existing column already is. This is the only part of this feature reaching
  outside the image module.
- **TeamStandingsSnapshot** — one row of the constructor classification, carrying the team's Discord
  role, its standing position and its points total.
- **Round** — carries the round number, the track, the format deciding which sessions exist, the
  cancelled state, and the lifecycle stage the label follows from.
- **QualifyingSessionResult / RaceSessionResult** — supply each grid cell's recorded position or
  outcome, and the team role by which a driver is placed on a car.
- **Team / Division** — the seats configured per team, the division's name and tier, and the reserves
  toggle deciding the driver classification's composition.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A league manager can author both standings templates and confirm each draws correctly
  without a season, a division, a round or a submitted result existing, using one command.
- **SC-002**: Every value appearing in both the graphic and the textual standings of the same round is
  identical, character for character, for every case the test data exhibit.
- **SC-003**: A standings template that cannot draw a classification is named, with its own reason and
  distinguished from its sibling, at the moment it is configured — not at the moment a round is posted.
- **SC-004**: A league is told its drivers template cannot hold its season at `/season review`, and is
  refused the driver assignment that would overflow it — never discovering it at a posting.
- **SC-005**: No render fault or degradation is ever visible in a channel drivers read; every one is
  visible to staff in the logging channel.
- **SC-006**: The failure of one championship leaves a league fully informed of both, each told exactly
  once, with nothing repeated between the surviving graphic and the textual substitute.
- **SC-007**: A league that switches the `standings` toggle off returns to the textual standings with no
  loss of information beyond the three derived columns and the grid, which text never carried.
- **SC-008**: A fresh clone draws every standings graphic from the first render, the marker directory
  shipping all three of its direction files.
- **SC-009**: Every generated image is verified as a rasterised PNG, never as an SVG previewed in a
  browser.

## Assumptions

- The countback separating entries level on points is applied by the standings service and persisted in
  the recorded standing position. The graphic reads that order and never re-derives it.
- FR-065 constrains new submissions and needs no backfill. The bot is not yet running in production,
  so no recorded round can already hold a driver under two team roles, and FR-026 may treat the
  invariant as guaranteed rather than re-adjudicating it. The same fact is why the migration in Key
  Entities needs no data repair.
- The row of the top-ranked driver carries both message ids, as it already carries the one. Existing
  rows keep their current column as the driver standings message; the new column starts empty.
- FR-044 extends the wip-spec's driver-assignment rule to the constructors template by the same
  reasoning — a team assignment grows the constructor classification exactly as a driver assignment
  grows the driver one, and the constitution states the rule for any command that would carry a
  division past what its templates can draw.
- "The name of a person" and "the name of a team" are the conventions now stated in the wip-spec's
  § "Conventions of every graphic"; this feature calls them and restates neither.
- The eight-aspect toggle, the two template slots, and the marker, flag, team-image and track-image
  directories were delivered with the module's configuration surface at 035 and 036, and are read as
  they stand.
- The lifecycle label drawn on the graphic is the same label the message text carries; XIV.16 permits a
  plain label in both places, and a graphic forwarded away from its message must still say which phase
  it stands after.

## Out of Scope

- Every other image type: attendance, RSVP, weather and verdicts, and any change to the calendar,
  lineup or results types already built.
- Any change to how standings are computed, ordered, tie-broken or recalculated. The graphic is a
  second presentation of one output, not a second output.
- Any change to result submission beyond the single cross-session team check of FR-065. That check is
  **in** scope by decision: the constructors grid depends on the invariant, and it is cheaper and more
  actionable to refuse the submission than to fail every later render drawn against the data. It
  forecloses recording one reserve standing in for two different teams within one round, which the
  submission validator permits today.
- Any change to the results posted alongside a round's standings, which the results section of the
  wip-spec governs.
- Adding the three derived columns to the **textual** standings. The derivations are written in the
  standings service so the text path may adopt them later; doing so is not part of this increment.
- Any change to the textual standings path beyond two things: making the values it already renders
  reachable for the graphic, and enabling it to post one championship's section alone as the
  fallback of FR-052 requires.
