# Feature Specification: Attendance Image Generation

**Feature Branch**: `041-attendance-image-generation`
**Created**: 2026-08-13
**Status**: Draft
**Input**: User description: the attendance image types — two field catalogues, the attendance sheet
addressed by row and round ordinal and the check-in graphic addressed by session ordinal, data
resolution, the optional round grid and the optional session list, mismatch handling, generation and
posting through the attendance and RSVP flows, and test data.

> **Normative source.** The rules this feature implements are stated in
> [`docs/wip-specs/image_module_specification.md`](../../docs/wip-specs/image_module_specification.md)
> — § "Conventions of every graphic" and § "Attendance image generation" — and are governed by
> Principles V, VII, XIII and XIV of the constitution. This document does **not** restate those
> rules. It states what must be built, who it is for, and how each obligation is verified, and cites
> the wip-spec where the rule itself lives. Where this document and the wip-spec disagree, the
> wip-spec wins and this document is the one to correct.

> **What makes these types different.** Attendance is the first module whose **two graphics stand in
> different relations to their text**. The sheet *replaces* the textual sheet, as every table before
> it did. The check-in graphic *replaces nothing*: the role mention, the embed, its roster, its status
> indicators and its three buttons all remain, and the picture is added beside them. It is also the
> first graphic that **outlives its own generation** — a check-in message cannot be reposted, its
> buttons being armed against it, so the embed is edited in place on every press and the attachment
> rides through untouched. Constitution v4.6.0 ratified the four forms this requires: the **static
> graphic** (XIV.17), the **graphic that displaces nothing** and **adds no precondition** (XIV.7), the
> **posting lifecycle** including produce-before-destroy and retry-as-text (XIV.8), the **collection
> floor** (XIV.12), and the **sibling relation between the graphics of one source module** (XIV.3).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preview both attendance graphics before a season depends on them (Priority: P1)

A league manager has authored a sheet template and a check-in template and wants to see what each
draws before any season, division, round or attendance record exists. They run
`/images test attendance` and get back two PNGs — one drawn with both point limits configured and one
with both disabled — and `/images test rsvp` and get back five, one per round case. Both are drawn
from fabricated data exercising every case the templates can be asked to carry: a driver on no points,
a driver sanctioned on this posting, two drivers level on totals, a pardoned round, a mystery round, a
track with no image file, rounds run and rounds yet to be run, and a deadline standing at the round's
own start time.

**Why this priority**: It is the only way to see either graphic without running a season, and it is
what makes every later story cheap to verify. It depends on no lifecycle, no channel and no posting
path, so it can ship and deliver value on its own.

**Independent Test**: Configure both templates on a server that has a team configuration and a track
list and nothing else, run each command, and confirm the PNGs come back with every enumerated case
visible and every non-fatal degradation listed beside them.

**Acceptance Scenarios**:

1. **Given** the sheet template is configured and valid and the server holds teams beyond the reserve
   team, **When** a league manager runs `/images test attendance`, **Then** two PNG attachments are
   returned for a division named "Test Division", of tier 1 and season number 1, holding five rounds
   and standing after the third — one drawn with both limits configured, one with both disabled.
2. **Given** a sheet template declares ten rows, **When** the command runs, **Then** nine drivers are
   fabricated and the tenth row is removed, so the rendering of an unused row is visible.
3. **Given** a sheet template declares no round at all, **When** the command runs, **Then** the totals
   alone are drawn and no grid is attempted.
4. **Given** the check-in template is configured and valid, **When** a league manager runs
   `/images test rsvp`, **Then** five PNGs are returned — a sprint round of four sessions, a normal
   round of two, a mystery round, a round whose track has no image file, and a round drawn against a
   deadline configured to 0 — each reported to the invoker and none posted to any division's channel.
5. **Given** the server holds no team beyond the reserve team, or its track list is empty, **When**
   `/images test attendance` runs, **Then** it is rejected with a clear error and no image is posted.
6. **Given** a fatal error is met, **When** either command runs, **Then** the error is reported to the
   league manager who invoked it and nothing is posted — these two commands never fall back to text.

---

### User Story 2 - A division's attendance sheet posted as a graphic (Priority: P2)

A league has the `attendance` toggle enabled. Every time the textual sheet would be posted for a
division, a graphic is drawn instead and posted to the division's attendance channel, carrying the
heading of the textual sheet as message text. The previous sheet is replaced, so exactly one stands in
the channel at any moment.

**Why this priority**: This is the half of the feature a league runs unattended, and it delivers the
sheet's whole value on its own. It depends on US1 only for confidence, not for function.

**Independent Test**: Enable the toggle for a division with a configured attendance channel, approve a
round's post-race penalties, and confirm one image message appears where a text message did; then
amend the round and confirm the message is replaced.

**Acceptance Scenarios**:

1. **Given** the toggle is enabled and the template is valid, **When** a round's attendance sheet is to
   be posted, **Then** one message is posted to the division's attendance channel with the graphic
   attached and the textual sheet's heading as message text.
2. **Given** a sheet was previously posted, **When** a new one is to be posted, **Then** the replacing
   message is produced **first** and only then is the previous one deleted, and the id of the
   replacement is persisted.
3. **Given** a round is recorded as cancelled, **When** its sheet would be posted, **Then** nothing is
   posted and nothing is generated, the toggle notwithstanding.
4. **Given** no attendance channel is configured for the division, or it is inaccessible, **When** a
   sheet would be posted, **Then** nothing is generated and nothing is posted, as the textual flow
   posts nothing.
5. **Given** the render fails on an uncommanded posting, **When** the sheet is to be posted, **Then**
   the textual sheet is posted in its place and the fault is reported to the logging channel.
6. **Given** the render fails, **When** the autoreserve or autosack sanctions of that round are due,
   **Then** those sanctions are enforced regardless, and their verdicts announced as they always were.
7. **Given** one division's sheet fails, **When** other divisions are posted, **Then** each of the
   others is generated and posted as an image in its own right.

---

### User Story 3 - A check-in call carrying a graphic that never goes stale (Priority: P3)

A league has the `rsvp` toggle enabled. When a round's check-in call is posted, a graphic naming the
round, its sessions, its date and the moment check-in locks is attached to the message that already
carries the role mention, the embed and the three buttons. Drivers press the buttons; the embed's
roster changes with every press; the picture does not, and is never redrawn.

**Why this priority**: It is the half of the feature that displaces nothing, so a league gets it purely
as an addition. It is placed after the sheet because it delivers less — the call already works — and
because it is the story carrying the static-graphic obligation, which is worth verifying against a
working sheet path.

**Independent Test**: Enable the toggle for a division with a configured RSVP channel, let the notice
horizon fire, confirm the call is posted with an attachment, then press each of the three buttons and
confirm the embed changes while the attachment is untouched and the message is never reposted.

**Acceptance Scenarios**:

1. **Given** the toggle is enabled and the template is valid, **When** a check-in call is posted,
   **Then** the message carries the role mention, the embed, the three buttons **and** the graphic, all
   composed exactly as the textual flow composes them.
2. **Given** a call carrying a graphic stands, **When** a driver answers it, the reserves are
   distributed, or anything else the embed carries changes, **Then** the embed alone is edited in
   place, the attachment is untouched, and no graphic is generated.
3. **Given** the toggle is enabled, **When** the last notice, the reserve-distribution announcement or
   the no-reserve-available notice is posted, **Then** each remains message text and carries no
   graphic.
4. **Given** the render fails, **When** the call is to be posted, **Then** the call is posted without an
   attachment — role mention, embed and buttons entire — and the round's attendance rows are opened
   exactly as they are when a graphic succeeds.
5. **Given** the call of the preceding round is to be deleted at the posting of the next, **When** that
   happens, **Then** a message carrying a graphic is deleted exactly as one carrying none is.
6. **Given** the toggle is off, **When** a call is posted, **Then** the flow behaves in every respect as
   it did before this feature.

---

### User Story 4 - Learn an attendance template is unusable before a season depends on it (Priority: P4)

A league manager sets a template with `/images template attendance` or `/images template rsvp` and is
told at once if the file cannot draw what it will be asked to draw. What can be checked without a
division is checked there; what needs a division is checked at `/season review` against the most
demanding one, as a warning; what needs the concrete data is checked before the render.

**Why this priority**: It converts a class of unattended posting failure into a command-time refusal.
It is valuable only once there is something to draw, so it follows the two posting stories.

**Independent Test**: Set a sheet template with a gap in its row numbering and confirm the command is
refused with that reason and the previous filename left in force.

**Acceptance Scenarios**:

1. **Given** a sheet template missing a mandatory division-independent field, **When** it is named,
   **Then** the command is refused naming the field, and the configuration is left as it stood.
2. **Given** a sheet template declaring no row at all, **When** it is named, **Then** the command is
   refused.
3. **Given** a template whose rows, rounds or sessions are numbered with a gap, **When** it is named,
   **Then** the command is refused naming which of the three numberings is at fault.
4. **Given** a sheet template declaring a field of the check-in catalogue, **When** it is named, **Then**
   the command is refused as holding the wrong file for that slot.
5. **Given** a sheet template declaring fewer rounds than the most demanding division of the season
   holds, **When** `/season review` runs, **Then** it is reported as a **warning** and the season may
   still be approved.
6. **Given** a division holds more drivers than the sheet template declares rows, **When** the sheet is
   drawn, **Then** the render fails naming the drivers that would have been dropped.
7. **Given** a driver assignment would carry a division past the rows its configured sheet template
   declares, **When** the assignment is commanded, **Then** it is refused and the change is not applied.

---

### User Story 5 - Degradations reported to staff, never drawn for drivers (Priority: P5)

Non-fatal degradations — a substituted font, a truncated name, a flag or track image falling back — are
reported to the server's logging channel naming the season, division and round, and never in the
attendance or RSVP channel a division's drivers read.

**Why this priority**: It is a cross-cutting obligation of every story above rather than a slice of its
own, and it is listed last because it is verified through them.

**Independent Test**: Post a sheet for a division holding a driver whose nationality has no flag file
and confirm the fallback is drawn, the notice appears in the logging channel, and the attendance
channel carries only the sheet.

**Acceptance Scenarios**:

1. **Given** a driver's nationality resolves to no flag file and the directory holds a fallback,
   **When** the sheet is drawn, **Then** the fallback is drawn and a notice naming the field and the
   nationality goes to the logging channel alone.
2. **Given** nationality collection is switched off at its source, **When** a sheet is drawn, **Then**
   no flag is drawn anywhere and **no** notice is raised.
3. **Given** a command triggered the generation, **When** a non-fatal degradation occurs, **Then** it is
   additionally reported alongside that command's output.

---

### Edge Cases

- A division holding **no driver at all** — the sheet has no subject and the render is refused, naming
  the division (XIV.12's floor).
- A sheet drawn early in a season, where most round cells are empty because most rounds are unrun.
- A round whose attendance is not yet finalised: its column keeps its group and heading, and every cell
  under it is emptied.
- A round every penalty of which was pardoned: an empty cell, with no trace of the pardon anywhere.
- A driver holding no record at all for a round, drawn identically to a driver the round cost nothing.
- A round of the mystery format on the sheet's grid, and as the subject of a check-in call.
- A template declaring more rounds than the division holds, with and without a `round_<z>_group`.
- A check-in template declaring four sessions for a round that holds two.
- A driver sacked at an earlier round, absent from the sheet as they are from the textual one.
- Both point limits disabled, with and without the template declaring the two groups.
- A deadline configured to 0 hours, standing at the round's own scheduled time.
- The Discord service refusing the post after a successful render.

## Requirements *(mandatory)*

### Functional Requirements

#### The two field catalogues

- **FR-001**: The system MUST declare two field catalogues — one for the `attendance_template` slot and
  one for `rsvp_template` — as code constants in the module's shared declaration, each separately
  addressable and each naming its own fields in full.
- **FR-002**: The sheet catalogue MUST declare the `row` collection discriminated by **ordinal**,
  numbered continuously from 1, its capacity fixed **by the template**, and MUST declare a **floor** on
  it: a sheet drawn for a division holding no driver is a fatal error naming the division.
- **FR-003**: The sheet catalogue MUST declare the `round` collection, and the per-row cell collection
  it governs, as **optional as a unit**, naming the collection at which the optional portion begins, so
  that a template declaring none of it draws the totals alone and is not faulty for it.
- **FR-004**: The check-in catalogue MUST declare the `session` collection discriminated by **ordinal**,
  numbered continuously from 1 in the order the sessions are run, **optional as a unit**, so that a
  template declaring none of it names no session and is not faulty for it.
- **FR-005**: A field a catalogue classifies mandatory **within** an optional portion — `round_<z>_number`
  on the sheet, `session_<x>_group` and `session_<x>_name` on the check-in graphic — MUST be required
  only where the template declares that member at all.
- **FR-006**: The two catalogues MUST be **siblings**: a template declaring a field belonging to the
  other MUST be a fatal error at the moment the template is named, the two being the graphics of one
  source module. An id belonging to neither catalogue MUST be ignored.
- **FR-007**: The sheet's row ordinal MUST be declared a **place in the layout and not a datum**. The
  system MUST draw no standing position on the sheet, two drivers level on totals standing level.
- **FR-008**: The sheet MUST carry no RSVP status of any driver, no pardon and no justification of one,
  no date of any round, no result of any session, and no grand prix name for any round of the grid.
- **FR-009**: The check-in graphic MUST carry no name of a driver, no name of a team, no RSVP status, no
  attendance point and no Discord mention, and none of the day, hour and notice counts the module is
  configured with, the check-in lock moment excepted.

#### Data resolution — the sheet

- **FR-010**: The system MUST compose the sheet exactly as the textual sheet composes it: every driver
  of the division holding a finalised attendance record for the round the sheet stands after — every
  non-reserve driver, every reserve distributed into a seat for that round, and every driver sanctioned
  upon this posting. A driver sacked at an earlier round MUST be absent.
- **FR-011**: The system MUST order rows by total attendance points descending, drivers level on totals
  ordered alphabetically by the name resolved for them, which is the order the textual sheet uses.
- **FR-012**: The total on a row MUST be the total accrued by that driver **in that division** after the
  round the sheet stands after, and never a total across divisions.
- **FR-013**: Every value the graphic draws that the textual sheet also draws MUST be produced by the
  same formatting code the textual path calls, not by a second implementation.
- **FR-014**: The system MUST place in a round cell the attendance points that round conferred upon that
  driver, read from the record the module persisted for that round, and MUST NOT derive it. The cell
  MUST be emptied where the round conferred none, where its attendance is not yet finalised, where the
  round is yet to be run, where it is recorded as cancelled, where the driver holds no record for it,
  and where a pardon waived every point it would have conferred.
- **FR-015**: An empty round cell MUST mean **zero points**. The six cases of FR-014 MUST NOT be
  distinguished from one another, each conferring none, and none of them is a value the graphic could
  not determine. No notice and no error of any kind MUST be raised for an empty cell.
- **FR-016**: The system MUST draw **every** round the division holds, not only those already run,
  identifying each by its number and drawing its image only in addition where the template declares one.
- **FR-017**: The system MUST place "Reached point limit" in a row's sanction field for a driver moved to
  the reserve team or removed from their driving roles **upon this posting**, and MUST empty it for
  every other driver. The sheet MUST NOT distinguish which of the two sanctions was enforced.
- **FR-018**: The system MUST place the autoreserve and autosack limits from the server's attendance
  configuration; where one functionality is disabled it MUST remove that limit's group in its entirety,
  or empty the limit field where the template declares no such group. Neither raises a notice.
- **FR-019**: The system MUST resolve a driver's name as the wip-spec's "name of a person" convention
  requires, carrying no Discord mention, and MUST reach the same name wherever it names that driver.
- **FR-020**: The team on a row MUST be the team of the division seating that driver **at the moment of
  generation** — the reserve team for a reserve driver — and never the team whose car they drove in any
  one round. Its name MUST be resolved by the wip-spec's "name of a team" convention.

#### Data resolution — the check-in graphic

- **FR-021**: The system MUST re-present the values the check-in embed shows and derive none of them by
  rules of its own.
- **FR-022**: The system MUST place the round's format as "Normal", "Sprint", "Endurance" or "Mystery",
  which is the text the embed carries.
- **FR-023**: The system MUST read the track name from the round as the embed's location, and the grand
  prix name and country from that round's track record.
- **FR-024**: The system MUST name a session "Sprint Qualifying", "Sprint Race", "Feature Qualifying" or
  "Feature Race" for a round of the sprint format, and "Qualifying" or "Race" for a round of any other,
  as the weather graphic does, carrying no qualifier of the session's length.
- **FR-025**: The system MUST render the round's date and time from the round record, in the module's
  configured date format, time format and single time zone, with the zone's abbreviation appended to
  the time.
- **FR-026**: The system MUST derive the check-in lock moment as the round's scheduled time less the
  configured `rsvp-deadline` hours, a configuration of 0 placing it at the round's scheduled time, and
  MUST render it as the round's own date and time are rendered.
- **FR-027**: The derivation of FR-026 MUST be written in the attendance service, never in the image
  generation utility, so the textual path can adopt it without a second implementation.
- **FR-028**: The system MUST draw the deadline the module enforces upon **full-time** drivers, and MUST
  NOT draw the later deadline a reserve driver is held to.

#### Shared resolution

- **FR-029**: The system MUST draw a round of the mystery format as the wip-spec's convention requires —
  "Mystery GP" for the grand prix, "Mystery" for the track and the country, its image resolved from the
  datum "Mystery" — and MUST empty no mandatory field of either graphic for want of a track.
- **FR-030**: The system MUST empty the text of a field whose value does not apply, and MUST NOT draw a
  dash in its place. A field carrying an image MUST be removed rather than emptied.

#### Assets

- **FR-031**: The system MUST resolve a driver flag from the configured flag directory by the normalised
  nationality, and MUST remove the field with a non-fatal error where no nationality is recorded. Where
  nationality collection is switched off at its source, no flag is drawn anywhere and **no** error is
  reported.
- **FR-032**: The system MUST resolve a team image from the configured team image directory by the
  normalised team name of FR-020.
- **FR-033**: The system MUST resolve a round's image, and the check-in graphic's track image, from the
  configured track image directory as the calendar graphic does, naming the round's number in any error
  reported for a cell of the grid.

#### Validity and mismatches

- **FR-034**: The system MUST verify the fields that do not depend on a division at every moment a
  template is verified, a missing mandatory one being fatal.
- **FR-035**: At template configuration and at season review the system MUST verify structurally, against
  the template alone: that a sheet template declares at least one row, numbered continuously from 1 and
  holding every mandatory field of a row; that the rounds it declares, if any, are numbered continuously
  from 1 and each hold the field carrying its number; and that the sessions a check-in template declares,
  if any, are numbered continuously from 1 and hold every mandatory field of a session.
- **FR-036**: At season review the system MUST additionally compare a sheet template's rounds against the
  greatest number of rounds any division of the season holds, and a check-in template's sessions against
  the largest number of sessions any round of the season holds, a divergence being a **warning** only.
- **FR-037**: At generation the system MUST verify against the concrete division and round being drawn.
- **FR-038**: Rows declared in excess of the sheet's drivers MUST be removed by their `row_<x>_group` in
  its entirety, with no error reported; drivers in excess of the declared rows MUST be a fatal error
  naming the drivers that would have been dropped.
- **FR-039**: Rounds declared in excess of the division's rounds MUST be removed by their
  `round_<z>_group` together with the cell of that ordinal on **every** row, with no error reported;
  where the template declares no such group for that ordinal, every field bearing it MUST be removed one
  by one. Rounds of the division in excess of those declared MUST be a fatal error naming them.
- **FR-040**: Sessions declared in excess of the round's sessions MUST be removed by their
  `session_<x>_group` in its entirety, with no error reported; sessions of the round in excess of those
  declared MUST be a fatal error naming the sessions that would have been dropped.
- **FR-041**: Each of the following MUST be a fatal error naming what is at fault: a mandatory field the
  template does not hold; a sheet template declaring no row at all; a gap in the numbering of rows,
  rounds or sessions; a field of the sibling catalogue; a mandatory field whose value cannot be
  determined at generation; and a sheet drawn for a division holding no driver at all.
- **FR-042**: A command that would carry a division past the rows its configured sheet template declares
  MUST be rejected with its change unapplied.

#### Generation and posting — the sheet

- **FR-043**: With the `attendance` toggle enabled, the system MUST generate the sheet wherever the
  textual sheet is posted and post it to the division's configured attendance channel as an attachment
  of a message carrying the textual sheet's heading as message text, and there alone.
- **FR-044**: The system MUST redraw and replace the sheet on every occasion the textual sheet is
  currently posted: approval and posting of a round's post-race penalties, and recalculation of a
  round's attendance after an amendment approved via `/round results amend`.
- **FR-045**: The sheet's posting lifecycle MUST be owned by the **textual** sheet flow and inherited by
  the image path, not reimplemented beside it. That flow MUST produce the replacing message **before**
  deleting the message it replaces — whether the replacement is the graphic or the textual sheet — and
  MUST persist the id of the replacement, so that at most one sheet stands in the channel at any moment.
  The textual flow deletes before it posts today and MUST be reordered; the image path then inherits the
  corrected order rather than carrying a rule of its own.
- **FR-046**: The system MUST generate and post nothing where no attendance channel is configured for the
  division or the channel is inaccessible, as the textual flow posts nothing.
- **FR-047**: The system MUST generate and post nothing for a round recorded as cancelled, the toggle
  notwithstanding.
- **FR-048**: The generation and posting of the sheet MUST NOT prevent, delay or condition the enforcement
  of the autoreserve and autosack sanctions, nor the announcement of their verdicts. The failure of one
  MUST NOT prevent the other.

#### Generation and posting — the check-in graphic

- **FR-049**: With the `rsvp` toggle enabled, the system MUST attach the graphic to the message carrying
  the division role mention, the embed and the three buttons, posted to the division's configured RSVP
  channel and there alone.
- **FR-050**: The system MUST generate the graphic on every occasion a check-in call is currently posted:
  the configured `rsvp-notice` horizon being reached, the call being advanced by test mode, and the call
  being posted at startup after that horizon passed while the bot was offline.
- **FR-051**: The check-in graphic MUST be declared a **static graphic**. It MUST be generated once, at
  the moment the call is posted, and MUST NOT be regenerated upon a driver answering, upon the
  distribution of the reserves, or upon any other change the embed carries. The embed alone is edited,
  in place; the message MUST NOT be deleted and reposted while the call stands.
- **FR-052**: The `rsvp` toggle MUST alter the textual check-in flow in no respect: the embed, its roster,
  its status indicators and its three buttons MUST be composed exactly as they are with the toggle off.
- **FR-053**: The last notice to unanswered drivers, the reserve-distribution announcement and the
  no-reserve-available notice MUST remain message text and carry no graphic, the toggle notwithstanding.
- **FR-054**: The deletion of the preceding round's check-in messages at the posting of the next call MUST
  be unchanged, and MUST apply to a message carrying a graphic as to one carrying none.
- **FR-055**: The generation or posting of a check-in graphic MUST NOT prevent the call itself from being
  posted, nor the round's attendance rows from being opened.

#### Errors and fallback

- **FR-056**: The system MUST report non-fatal errors to the server's logging channel, naming the season,
  the division and the round, and never in a division's attendance or RSVP channel; and additionally
  alongside the output of a command that triggered the generation.
- **FR-057**: On a fatal error in a posting no command triggered, the system MUST post the textual sheet in
  place of the sheet graphic, and MUST post the check-in call **without an attachment** — role mention,
  embed and buttons entire — there being no text to restore for a graphic that displaced nothing.
- **FR-058**: On a fatal error in a posting a command triggered, the system MUST reject the command, post
  nothing in consequence of it, and report to the logging channel and to the invoking user.
- **FR-059**: The failure of one division MUST NOT prevent the others being generated and posted as images.
- **FR-060**: Where a generated **sheet** fails to post for a reason of the Discord service rather than
  of the generation, it is the **textual** sheet that MUST be enqueued for retry. A generated image MUST
  NOT be enqueued.
- **FR-061**: A check-in call MUST NOT be enqueued for retry, whether it carried a graphic or none. The
  retry queue carries text alone, and a call replayed as text would arrive with no buttons and no
  roster — a message the division cannot answer.
- **FR-062**: Where a check-in call fails to post for any reason, the system MUST report it to the
  server's logging channel, naming the season, the division and the round, so staff can post it again.
  This MUST hold with the `rsvp` toggle off as with it on, the fault being in the call and not in the
  picture. The failure is today written only to the application log, where no league can see it.

#### Test data

- **FR-063**: `/images test attendance` MUST generate two sheets for a division named "Test Division", of
  tier 1 and season number 1, holding five rounds and standing after the third — one with both limits
  configured, one with both disabled.
- **FR-064**: Round 2 of the fabricated calendar MUST be of the mystery format, and one round MUST have a
  track of the server's list for which no image file exists, so the fallback and its notice are evaluated.
- **FR-065**: The command MUST fabricate one driver fewer than the rows the template declares, drawn from
  the server's team configuration; where a template declares a single row, one driver MUST be fabricated
  and the unused row left unevaluated.
- **FR-066**: The fabricated drivers MUST exhibit the cases the wip-spec's § "Test data" enumerates,
  insofar as the declared row count allows.
- **FR-067**: The nationalities given MUST be among those the signup wizard accepts, at least one being
  that recorded for a driver who stated none.
- **FR-068**: `/images test attendance` MUST be rejected with a clear error where the server holds no team
  beyond the reserve team or its track list is empty.
- **FR-069**: `/images test rsvp` MUST generate one image for each of five cases — a sprint round, a normal
  round, a mystery round, a round whose track has no image file, and a round drawn against a deadline
  configured to 0 — each for "Test Division", tier 1, season number 1, at round 1, each reported to the
  invoking league manager and none posted to any division's RSVP channel.
- **FR-070**: The rounds fabricated for `/images test rsvp` MUST span more than one month and more than
  one half of the day, so the configured date and time formats are evaluated.
- **FR-071**: `/images test rsvp` MUST be rejected with a clear error where the server's track list is empty.
- **FR-072**: A fatal error met by either test command MUST be reported to the invoking league manager with
  no image posted, these being the one exception to the fallback rule.

### Key Entities

No entity is introduced and none is amended. Both graphics already have the column their lifecycle needs,
and the two lifecycles differ.

- **AttendanceDivisionConfig** — carries the division's attendance channel and `attendance_message_id`,
  the one sheet message. The image flow deletes that message and persists its replacement's id in the
  same column, as the results and lineup flows do with theirs.
- **RsvpEmbedMessage** — carries the check-in call's message id and channel. The image flow leaves it
  entirely alone: the call is never deleted and reposted while it stands, which is the point of the
  static declaration.
- **AttendanceConfig** — supplies the autoreserve and autosack thresholds the sheet draws, and the
  `rsvp_deadline_hours` from which the check-in lock moment is derived.
- **DriverRoundAttendance** — the per-round record supplying each grid cell's points and each row's total,
  read as persisted and never recomputed.
- **Round / Track** — the round number, scheduled time, format deciding which sessions exist, cancelled
  state, and the track record supplying the grand prix name and country.
- **Division / Team** — the division's name and tier, and the team seating each driver at generation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A league manager can author both attendance templates and confirm each draws correctly
  without a season, a division, a round or an attendance record existing, using two commands.
- **SC-002**: Every value appearing in both a graphic and the textual output of the same round is
  identical, character for character, for every case the test data exhibit.
- **SC-003**: An attendance template that cannot draw is named, with its own reason and distinguished
  from its sibling, at the moment it is configured — not at the moment a round is posted.
- **SC-004**: A league is told its sheet template cannot hold its season at `/season review`, and is
  refused the driver assignment that would overflow it — never discovering it at a posting.
- **SC-005**: A check-in graphic posted at the notice horizon still tells the truth after every driver has
  answered, the picture carrying no value any press can change.
- **SC-006**: Enabling the `rsvp` toggle changes nothing a driver can interact with: the same embed, the
  same roster, the same three buttons, with a picture added.
- **SC-007**: No attendance sanction is ever missed, delayed or altered because a graphic could not be
  drawn or posted.
- **SC-008**: At most one attendance sheet stands in a division's channel at any moment, and no failed
  render ever leaves that channel empty of the sheet it had.
- **SC-009**: No render fault or degradation is ever visible in a channel drivers read; every one is
  visible to staff in the logging channel.
- **SC-010**: A league that switches either toggle off returns to the textual output with no loss of
  information beyond the per-round points column, which the textual sheet never carried.
- **SC-011**: Every generated image is verified as a rasterised PNG, never as an SVG previewed in a browser.

## Assumptions

- The bot is not yet running in production, so no schema change, backfill or data repair arises from this
  increment.
- The per-round points a sheet draws are read from the record the attendance module already persists for
  every round. This is the sole value the graphic carries that the textual sheet does not, and it is a
  re-presentation of a persisted figure rather than a derivation.
- "Reached point limit" is the annotation the type defines for its sanction field, being the textual
  sheet's own annotation with the emphasis and parentheses it applies stripped. The textual sheet appends
  " *(reached point limit)*"; the graphic draws the plain literal.
- The check-in graphic's grand prix name and country are read from the round's track record. The embed
  carries the track name as its location and not these two, so the graphic reaches the same record the
  calendar graphic reaches rather than deriving anything.
- FR-045 places the ordering in the textual sheet flow and has the image path inherit it, rather than
  giving the image path a rule of its own. The textual flow deletes before it posts today, so the
  reorder is in scope: produce-before-destroy is what stops a failed render leaving a division with no
  sheet at all, and it cannot hold for the image path while the path it falls back to breaks it. One
  implementation also means the two cannot drift, which a second one beside it would invite.
- FR-055 is bounded to what the image path adds. The existing flow already opens a round's attendance rows
  only after the call posts successfully; this feature must not extend that dependency to the render, and
  does not otherwise change it.
- "The name of a person" and "the name of a team" are the conventions stated in the wip-spec's
  § "Conventions of every graphic"; this feature calls them and restates neither.
- The eight-aspect toggle, the two template slots, and the flag, team-image and track-image directories
  were delivered with the module's configuration surface at 035 and 036, and are read as they stand.
  `ASPECT_SOURCE_MODULE` already maps both aspects to the attendance module, which is the relation FR-006
  reads.
- A sheet is drawn after a round whose attendance is finalised; a round of the grid whose attendance is
  not yet finalised is an ordinary emptied column and not a fault.

## Out of Scope

- Every other image type: weather and verdicts, and any change to the calendar, lineup, results or
  standings types already built.
- Any change to how attendance points are calculated, accrued, pardoned or totalled, or to how the
  autoreserve and autosack thresholds are enforced. The graphic is a second presentation of one output,
  not a second output.
- Any change to the check-in flow beyond attaching a picture: the embed's composition, its roster, its
  status indicators, its three buttons, the notice horizons, the reserve distribution and the deadlines
  are all untouched.
- Adding the per-round points column to the **textual** sheet.
- Adding a driver's flag or team badge to any textual output.
- Distinguishing autoreserve from autosack on the sheet. The verdict announced for the driver names which
  was enforced, and the sheet is not where they are told apart.
- Any change to the retry queue itself. It carries text alone, which is why FR-061 keeps the check-in
  call out of it rather than extending it to carry embeds, views and attachments. Teaching it to
  reconstitute a call is a larger change to shared infrastructure and belongs to its own increment.
- Opening a round's attendance rows when its check-in call fails to post. FR-062 makes that failure
  visible to staff instead; changing the ordering would penalise every driver at the deadline for a
  call none of them could see.
- Any change to the retry queue beyond ensuring the textual form, and never a rendered image, is what
  enters it.
