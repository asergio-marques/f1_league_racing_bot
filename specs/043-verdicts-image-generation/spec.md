# Feature Specification: Verdicts Image Generation

**Feature Branch**: `043-verdicts-image-generation`
**Created**: 2026-08-14
**Status**: Draft
**Input**: User description: the verdict image type — one field catalogue serving three kinds of
verdict from one template, the wrapping of a steward's free text, data resolution shared with the
textual announcement, mismatch handling, generation and posting as a static graphic replacing that
announcement, and test data.

> **Normative source.** The rules this feature implements are stated in
> [`docs/wip-specs/image_module_specification.md`](../../docs/wip-specs/image_module_specification.md)
> — § "Conventions of every graphic" and § "Verdicts image generation" — and are governed by
> Principles V, VII, IX and XIV of the constitution. This document does **not** restate those rules. It
> states what must be built, who it is for, and how each obligation is verified, and cites the wip-spec
> where the rule itself lives. Where this document and the image wip-spec disagree, the image wip-spec
> wins and this document is the one to correct.

> **What makes this type different.** The verdict is the module's **simplest** graphic and the first to
> raise the questions the six before it never had to answer. It declares **no collection at all** — no
> ordinal, no capacity, no floor — and is the only type of the module of which that is true. **One
> template serves three kinds** of verdict, told apart by the text on two fields rather than by a slot
> of their own. It is the first type to draw **free text a person wrote**, of a length nobody controls,
> and therefore the first to exercise the wrapping contract at all. It is the first graphic to
> **displace the whole of a message's body**, the announcement's every line moving onto the canvas and
> the message keeping the driver mention alone. And it is the second **static** graphic, on a ground the
> first did not use: it draws a **record of a decision taken**, not a view of a state.
>
> Constitution v4.8.0 ratified each of these: the type with **no collection** and the **several kinds
> sharing one slot** (XIV.10), the **wrapping contract** and its two template defects (XIV.5), the
> graphic **displacing all but what a picture cannot carry** (XIV.7), the **mention resolved inside a
> value** (XIV.16), the **kind of record that has no such thing at all** (XIV.3), and the **second
> ground for staticity** (XIV.17). That amendment also **inverted Rule 7**: the correspondence with the
> text path is now a floor and not a ceiling, so the flag and the badge this graphic adds to the
> announcement, and the stage it names, need no permission.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preview every kind of verdict before a steward issues one (Priority: P1)

A league manager has authored a verdicts template and wants to see what it draws before any penalty is
applied to anybody. They run `/images test verdicts` and get back six PNGs from the one template — a
time penalty added, a time penalty removed, a disqualification, an appeal, an autosack and an
autoreserve — drawn from fabricated data that exercises every case the template can be asked to carry:
a sprint session name, a verdict naming no session and no team, free text of five different lengths,
and a driver who stated no nationality.

**Why this priority**: It is the only way to see a verdict graphic without a steward penalising a real
driver, and it is what makes every later story cheap to verify. It depends on no review, no channel and
no posting path, so it can ship and deliver value on its own. It is also the only way to judge the
wrapping of free text, which is this type's whole difficulty.

**Independent Test**: Configure the verdicts template on a server that has a track list and nothing
else, run the one command, and confirm six PNGs come back with every enumerated case visible and every
non-fatal degradation listed beside them.

**Acceptance Scenarios**:

1. **Given** the verdicts template is configured and valid, **When** a league manager runs
   `/images test verdicts`, **Then** six PNGs are returned for a division named "Test Division", of
   tier 1 and season number 1, at round 1 of a track of the server's track list, and none is posted to
   any division's verdicts channel.
2. **Given** the same command, **When** the six images are drawn, **Then** one is a penalty-phase
   verdict on a session of a **sprint** round, so that a sprint session name can be judged; one carries
   a time penalty added; one a time penalty removed; one a disqualification; one is an appeal-phase
   verdict; and two are attendance sanctions, an autosack and an autoreserve.
3. **Given** the two attendance-sanction images, **When** they are drawn, **Then** each carries no
   session name and no team name, and the justification each carries names the driver by name with no
   Discord mention anywhere upon the canvas.
4. **Given** the six images, **When** their free text is fabricated, **Then** it includes one text
   short enough for a single line, one filling the field exactly, one exceeding it slightly so the
   font-size reduction can be judged, one exceeding it by an order of magnitude so the reduction to the
   floor and the truncation can be judged, and one for which the steward entered neither a description
   nor a justification.
5. **Given** the fabricated drivers, **When** their flags are drawn, **Then** their nationalities are
   among those the signup wizard accepts and at least one is the value recorded for a driver who stated
   none.
6. **Given** the server's track list is empty, **When** the command is run, **Then** it is rejected
   with a clear error, there being no round for a verdict to pertain to.
7. **Given** the command meets a fatal error, **When** it runs, **Then** the error is reported to the
   league manager who invoked it and nothing is posted, there being no textual counterpart for a test
   command to fall back to.

---

### User Story 2 - A penalty and an appeal announced as a graphic (Priority: P2)

A league has enabled the `verdicts` toggle. A steward approves a penalty review with three penalties
applied. Three graphics are posted to the division's verdicts channel, each attached to a message
carrying the mention of the driver it pertains to and nothing besides. The heading, the driver line,
the sanction, the description and the justification the textual announcement carried are all on the
canvas now. Later an appeal review overturns one of them, and a fourth graphic is posted beside the
first three — the original is not edited, replaced or deleted.

**Why this priority**: It is the feature the league asked for, and the largest part of the work. It
depends on Story 1 only for confidence, not for function.

**Independent Test**: Enable the toggle on a division with a configured verdicts channel, approve a
penalty review with several penalties staged, and confirm one graphic and one message per penalty,
each carrying a mention and no other text, with the textual announcement appearing nowhere in that
channel.

**Acceptance Scenarios**:

1. **Given** the `verdicts` toggle is enabled and the template is valid, **When** a penalty review is
   approved with one or more penalties applied, **Then** one PNG is posted per penalty to the
   division's verdicts channel, each on a message carrying that driver's mention and **nothing
   besides**.
2. **Given** the same toggle, **When** an appeals review is approved with one or more corrections
   applied, **Then** one PNG is posted per correction on the same terms, its stage field reading
   "Appeal".
3. **Given** a review is approved with nothing staged, **When** it finalises, **Then** nothing is
   announced and nothing is generated.
4. **Given** a verdict has been posted, **When** anything about the decision later changes, **Then**
   that verdict's message is never edited, replaced or deleted, and no message id is persisted for it;
   a correction arrives as a verdict of its own.
5. **Given** a graphic is drawn for a penalty, **When** it is filled, **Then** it carries the driver's
   name in place of a mention, the flag of their nationality, the name of the team whose car they drove
   in that session and that team's badge — none of which the textual announcement prints.
6. **Given** the driver was a reserve standing in for another, **When** the team is resolved, **Then**
   it is the team whose car they drove and never the reserve team.
7. **Given** the render of one verdict fails, **When** the review finalises, **Then** that verdict is
   announced as text, the other verdicts of the same review are unaffected, and the verdicts of every
   other division are unaffected.
8. **Given** any verdict is generated or fails, **When** the review is finalised and its sanctions
   enforced, **Then** that finalisation and enforcement complete exactly as they would with the image
   module disabled.

---

### User Story 3 - An attendance sanction announced as a graphic (Priority: P3)

A driver reaches the attendance point threshold and is automatically sacked or moved to the reserve
team. With the toggle enabled, the announcement of that sanction is a graphic drawn from the same
template as a penalty — but it pertains to no session and names no team, so those fields come off the
canvas, and the justification the attendance module composed around a Discord mention carries the
driver's name instead.

**Why this priority**: It completes the toggle's promise — a league that enabled `verdicts` would
otherwise get a picture for its stewards' decisions and text for the bot's own. It is independent of
the review flow and is the only story exercising the emptied session, the emptied team and the mention
resolved inside a value.

**Independent Test**: Drive a test driver past the autosack threshold on a division with the toggle
enabled and a verdicts channel configured, and confirm one graphic is posted whose session and team
fields are absent and whose justification names the driver in words.

**Acceptance Scenarios**:

1. **Given** the toggle is enabled, **When** an autosack or an autoreserve sanction is enforced,
   **Then** one PNG is posted to the division's verdicts channel on a message carrying that driver's
   mention and nothing besides.
2. **Given** such a verdict is drawn, **When** it is filled, **Then** its stage field reads "Attendance
   Sanction", its session name field is **emptied** and its removable group removed where the template
   declares one, and no error whatever is reported for either.
3. **Given** the same verdict, **When** the team is resolved, **Then** the team name field is emptied
   and the team image field removed, and no error is reported.
4. **Given** the justification the attendance module composed, **When** it is placed on the canvas,
   **Then** the Discord mention within it is replaced in place by the driver's name and the text around
   it is drawn as written.
5. **Given** an attendance **pardon**, **When** it is recorded, **Then** it is written to the server's
   logging channel and carries no graphic, the `verdicts` toggle notwithstanding.
6. **Given** the enforcement of the sanction, **When** the graphic fails to generate or to post,
   **Then** the sanction has already been enforced and the driver's seats already altered.

---

### User Story 4 - Learn a verdicts template cannot draw a verdict before a steward needs it (Priority: P4)

A league manager configures a verdicts template that omits a mandatory field, or declares a wrapping
field whose rectangle does not exist, or gives a wrapping field no leading to lay out. They are told at
the moment they configure it — and again at season review — rather than discovering it when a steward
approves a review at midnight.

**Why this priority**: It is what stops a league approving a season every verdict of which then falls
back to text. It is independent of the posting path and testable entirely through the configuration
commands. This type makes the check unusually strong: every field of the catalogue is independent of
the data, so nothing is deferred to the render.

**Independent Test**: Configure a template with each defect in turn and confirm each is rejected at
configuration, named at season review, and refused at approval.

**Acceptance Scenarios**:

1. **Given** a template omitting any mandatory field, **When** it is named at `images template
   verdicts`, **Then** the command is rejected, the configuration is left as it stood, and the reason
   names the field that is missing.
2. **Given** a template whose wrapping field declares a `shape-inside` naming a rectangle the template
   does not hold, **When** it is named, **Then** it is rejected naming the field and the rectangle.
3. **Given** a template whose wrapping field has no `line-height` resolving upon it, **When** it is
   named, **Then** it is rejected naming the field. No default leading is substituted.
4. **Given** a template carrying any of those faults, **When** `season review` is run, **Then** the
   verdicts template is named as invalid with its own reason, and the season's approval is refused
   while the fault stands.
5. **Given** the verdicts template declares a field belonging to another image type's catalogue that is
   a sibling of it, **When** it is named, **Then** it is refused as the wrong file in that slot.
6. **Given** the template is valid, **When** it is checked, **Then** the whole catalogue is verified at
   every one of the three moments alike, no field of it being one that can only be checked against a
   division, a round or a classification.

---

### User Story 5 - Degradations reported to staff, never drawn for drivers (Priority: P5)

A steward writes a justification far longer than the rectangle the league drew for it, or a driver's
flag has no file of its own. The graphic is still posted; the truncation and the substitution are
reported to the league's log channel, naming the season, division, round, session and driver, and never
appear in the channel the drivers read.

**Why this priority**: It is a small addition on top of the reporting the module already does, and the
graphics are usable without it — but a league that cannot see which verdict was cut short cannot widen
the rectangle.

**Independent Test**: Point the flag directory at a directory holding only `fallback.svg`, issue a
verdict with a justification an order of magnitude too long, and confirm the picture is posted, the
fallback is drawn, the text is cut with an ellipsis, and both notices reach the log channel and no
verdicts channel.

**Acceptance Scenarios**:

1. **Given** a wrapping field whose text still exceeds its rectangle at the floor of half the declared
   size, **When** the graphic is generated, **Then** the text is cut at a word boundary, an ellipsis is
   placed at its end, the render succeeds, and a notice naming the field and the verdict reaches the
   log channel.
2. **Given** a field declaring a font the machine does not carry, **When** the width of its text is
   measured, **Then** the measurement is made against the face the converter would substitute and a
   notice naming the field and the font is raised.
3. **Given** a flag or team image resolves to no file and its class holds a `fallback.svg`, **When**
   the graphic is generated, **Then** the fallback is drawn, the render succeeds, and a notice naming
   the field and the datum is raised.
4. **Given** the league has switched nationality collection off entirely, **When** verdicts are drawn,
   **Then** no flag appears on any of them and **no notice whatever** is raised.
5. **Given** any notice is raised during a generation, **When** it is reported, **Then** it names the
   season, the division, the round, the session and the driver, and appears in no division's verdicts
   channel.
6. **Given** a generation was triggered by a command, **When** notices are raised, **Then** they are
   additionally reported alongside that command's output.

---

### Edge Cases

- A steward who entered neither a description nor a justification: the graphic carries the same fixed
  text the announcement carries in its place, **without** the channel emphasis that message applies.
- A steward's text written in paragraphs: the line breaks they entered begin new lines of the field,
  and a run of them leaves the blank lines between, each counting against the field's budget.
- A single word longer than the rectangle is wide — a pasted URL, an unbroken string: it is broken
  within itself rather than allowed to run off the canvas.
- A round of the mystery format: the race name field reads "Mystery GP" and the verdict is drawn like
  any other, no exemption arising.
- A division with no verdicts channel configured, or one the bot cannot reach: nothing is posted and
  nothing is generated, whatever the toggle says, exactly as the textual flow skips it.
- A penalty review approved with several penalties where one render fails: that one verdict is
  announced as text and the rest are posted as graphics, in the same channel, in the same batch.
- The posting of a generated image failing for a reason of the Discord service: the **textual**
  announcement is what is enqueued for retry, never the image.
- A driver whose Discord display name cannot be reached at generation: the name resolution chain is
  followed, and the graphic carries the same name wherever it names that person.
- A driver who renames their Discord account after a verdict is posted: the graphic is not redrawn and
  is not stale — it records the name under which the decision was issued.
- A division holding no team for the Discord role the result records: the name of the role itself is
  drawn, and is what is normalised to search for the badge.

## Requirements *(mandatory)*

### Functional Requirements

#### The field catalogue

- **FR-001**: The system MUST declare one field catalogue for the `verdicts_template` slot as a code
  constant in the module's shared declaration, serving all three kinds of verdict.
- **FR-002**: The catalogue MUST declare **no collection** — no ordinal, no key, no capacity and no
  floor. It is the only catalogue of the module of which this is true.
- **FR-003**: The catalogue MUST declare these text fields: `season_number` (optional), `division_name`
  (mandatory), `division_tier` (optional), `round_number` (mandatory), `race_name` (optional),
  `session_name` (mandatory), `verdict_stage` (mandatory), `driver_name` (mandatory), `team_name`
  (optional), `penalty` (mandatory), `description` (mandatory) and `justification` (mandatory).
- **FR-004**: The catalogue MUST declare these image fields: `driver_flag` (optional, flag class) and
  `team_image` (optional, team image class).
- **FR-005**: The catalogue MUST NOT declare a track image, a country name, a date or time of the
  round, any result of any session, any points total, any lifecycle label, or the name of the steward
  who issued the verdict.
- **FR-006**: The catalogue MUST be a **sibling** of every other catalogue of the image module that
  draws the same output aspect or belongs to the same source module; a verdicts template declaring a
  sibling's field MUST be refused at the moment the template is named. An id belonging to no catalogue
  MUST be ignored.
- **FR-007**: The catalogue MUST be declared **static** (Rule XIV.17), on the ground that it draws a
  record of a decision taken rather than a view of a state, together with the condition that makes that
  ground sound: a correction of the decision arrives as a **new verdict** and never as an edit of the
  one standing.

#### The wrapping of free text

- **FR-008**: A field carrying a `shape-inside` naming a rectangle of the template MUST be treated as a
  **wrapping field**, that rectangle being the extent of the field — its width what the text is wrapped
  against, its height what the text may occupy. The rectangle MUST NOT itself be drawn.
- **FR-009**: Any text field of the catalogue MUST be permitted to be declared a wrapping field. The
  system MUST NOT restrict wrapping to `description` and `justification`, nor require it of them.
- **FR-010**: The text MUST be broken **first** at the line breaks its author entered, and each piece so
  obtained broken again at word boundaries into lines no wider than the rectangle. A run of author
  breaks MUST leave the blank lines between them, each counting against the field's budget as a line of
  text does.
- **FR-011**: A single word wider than the rectangle MUST be broken **within itself** rather than
  allowed to overrun. The same MUST hold of a single-line field declaring an `inline-size`.
- **FR-012**: The lines the rectangle admits MUST be its height divided by the **line height in force**,
  which is the `line-height` resolving upon the field, whether declared on it or inherited by it.
- **FR-013**: A wrapping field upon which **no `line-height` resolves** MUST be a fatal error naming the
  field. No default leading may be substituted. *(This changes present behaviour: a leading of 1.2 is
  substituted today — see Assumptions.)*
- **FR-014**: A wrapping field whose `shape-inside` names a rectangle the template does not hold MUST be
  a fatal error naming the field and the rectangle.
- **FR-015**: A wrapping field whose rectangle declares no usable width or height MUST be a fatal error
  on the same terms, the field having been given no room to lay out.
- **FR-016**: Where the text at the template-declared size occupies more lines than the rectangle
  admits, the field's size MUST be reduced by half-pixel steps and the text wrapped again, to a floor of
  **half** the declared size.
- **FR-017**: The line height in force MUST follow the font size, reducing in the same proportion, and
  the admissible line count MUST be recomputed at the reduced leading, so that a field set smaller holds
  **more lines** rather than the same number more widely spaced.
- **FR-018**: Text still exceeding the rectangle at the floor MUST be truncated at a word boundary, an
  ellipsis placed at its end, and a **non-fatal** notice raised naming the field and the verdict.
- **FR-019**: Each wrapping field MUST be reduced **on its own**. The canvas MUST NOT be resized and no
  other field may follow the size of the field reduced.
- **FR-020**: `shape-inside` MUST be removed from the field once its lines are laid out.
- **FR-021**: A field declaring an `inline-size` and **no** `shape-inside` MUST NOT be treated as a
  wrapping field. It is a single-line field declaring the room it is given and is truncated with an
  ellipsis and a notice.
- **FR-022**: The width of a text MUST be measured against the font family, weight, style and size the
  field declares. Where that font is not installed, the measurement MUST be made against the face the
  converter would substitute and a notice raised naming the field and the font.
- **FR-023**: The measurement MUST **err narrow**, so that a line the measurement admits is a line the
  canvas holds.
- **FR-024**: The system MUST impose no limit on the length of a verdict's free text at its source. A
  text too long for the rectangle is answered by FR-016 through FR-018 and by nothing else.

#### Data resolution

- **FR-025**: Every value the graphic draws that the textual announcement also draws MUST be produced by
  the same formatting code the textual path calls. The system MUST NOT hold a second implementation of
  any such rendering.
- **FR-026**: `penalty` MUST be the **descriptive** rendering the textual announcement carries — a time
  penalty, a disqualification, a sacking and a move to the reserve team alike — and MUST NOT be the
  compact rendering a results graphic places in a sanction column.
- **FR-027**: `verdict_stage` MUST be fixed text: "Post-Race Penalty" for a verdict of the penalty
  phase, "Appeal" for one of the appeal phase, and "Attendance Sanction" for one enforced by the
  attendance module.
- **FR-028**: `session_name` MUST be "Sprint Qualifying", "Sprint Race", "Feature Qualifying" or
  "Feature Race" for a round of the sprint format and "Qualifying" or "Race" for a round of any other,
  produced by the same rendering the results graphic uses.
- **FR-029**: For a verdict of an attendance sanction, `session_name` MUST be **emptied** and its
  removable group removed where the template declares one, and **no** error reported. The field remains
  mandatory: the template MUST still declare it. The label "Attendance Sanction" MUST NOT be written
  into it, standing on `verdict_stage` alone.
- **FR-030**: For a verdict of an attendance sanction, `team_name` MUST be emptied and `team_image`
  removed, and no error reported.
- **FR-031**: `driver_name` MUST be resolved by the module's existing name-resolution chain, and the
  same name MUST be drawn wherever that person is named on the graphic. The graphic MUST carry no
  Discord mention.
- **FR-032**: A Discord mention appearing **within** any text the graphic places MUST be replaced, in
  the position it stands, by the name of the person it addresses, resolved by that same chain, with the
  text around it drawn as written. The justification the attendance module composes for a sacking and
  for a move to the reserve team is written around such a mention.
- **FR-033**: `description` and `justification` MUST be placed **verbatim**. Where the steward entered
  neither, the fixed text the textual announcement carries in its place MUST be carried in turn,
  **without** the channel emphasis that message applies. The separation MUST be made where the value is
  produced; the image utility MUST NOT strip markup out of a string handed to it.
- **FR-034**: `team_name`, and the datum normalised to search for `team_image`, MUST be resolved as the
  results graphic resolves them: the team of the division holding the Discord role the result records,
  falling back to the name of the role itself where the division holds no such team.
- **FR-035**: The team MUST be the team whose car the driver drove in the session the verdict pertains
  to, which for a reserve standing in for another is that car's team and never the reserve team.
- **FR-036**: `driver_flag` MUST be resolved as it is for the lineup graphic. Where the nationality is
  absent the field MUST be removed and a notice raised; where the league has switched nationality
  collection off at its source, the field MUST be removed and **no notice whatever** raised.
- **FR-037**: `round_number` MUST be read from the round object and `race_name` from the track object of
  that round. A round of the mystery format MUST draw "Mystery GP" on `race_name`.
- **FR-038**: `division_tier` MUST be emptied where the division carries no tier.
- **FR-039**: Where any value does not apply, its text field MUST be **emptied** and MUST NOT be filled
  with a dash or any other placeholder. An image field in the same case MUST be **removed**.
- **FR-040**: The graphic MUST derive nothing and decide nothing. Every value it draws MUST be read as
  the owning module recorded it or rendered by that module's own code.

#### Mismatch handling

- **FR-041**: The whole catalogue MUST be verified at every one of the three validity moments alike —
  the command naming the template, season review, and immediately before every generation — no field of
  it being one that can only be checked against a division, a round or a classification.
- **FR-042**: A mandatory field the template does not hold MUST be a fatal error naming the field.
- **FR-043**: A mandatory field whose value cannot be **determined** at generation MUST be a fatal error
  naming the field. A value the data determine to be **nothing** is determined and is not this case.
- **FR-044**: Season review MUST name the verdicts template individually where it is at fault, with its
  own reason, and the season's approval MUST be refused while the fault stands.
- **FR-045**: A flag or team image resolving to no file MUST be answered by the module's asset rules: the
  class's `fallback.svg` with a notice where one exists, and a fatal error where none does.

#### Generation and posting

- **FR-046**: With the `verdicts` toggle enabled, a verdict MUST be posted as a PNG attached to a message
  carrying the mention of the driver the verdict pertains to and **nothing besides**.
- **FR-047**: One graphic and one message MUST be produced **per verdict**; a review applying several
  penalties posts one of each for every penalty it applies.
- **FR-048**: A graphic MUST be generated on every occasion a textual announcement is currently posted:
  a penalty review approved with one or more penalties applied, an appeals review approved with one or
  more corrections applied, and an autosack or autoreserve sanction enforced.
- **FR-049**: A review approved with nothing staged MUST announce nothing and generate nothing.
- **FR-050**: A verdict MUST be posted **once** and never edited, replaced or deleted, and **no message
  id** may be persisted for it.
- **FR-051**: The graphic MUST replace the textual announcement in the division's configured verdicts
  channel and **there alone**.
- **FR-052**: An attendance **pardon** MUST carry no graphic and remain a record in the server's logging
  channel, the `verdicts` toggle notwithstanding.
- **FR-053**: Where no verdicts channel is configured for the division, or the channel is inaccessible,
  nothing MUST be generated and nothing posted, exactly as the textual flow skips it.
- **FR-054**: The generation and posting of a verdict MUST NOT prevent, delay or condition the
  finalisation of a review or the enforcement of a sanction. Every such change of state MUST complete
  exactly as it would with the image module disabled, and a render that fails MUST find that work
  already done.
- **FR-055**: The failure of one verdict MUST prevent neither the other verdicts of the same review nor
  the verdicts of any other division.

#### Errors, notices and fallback

- **FR-056**: A fatal error met at any step of the generation or posting of a verdict MUST cause that
  verdict to be announced in the textual manner instead, where the posting was triggered by no command.
- **FR-057**: Where a **command** triggered the generation, a fatal error MUST reject that command,
  nothing MUST be posted in consequence, and the caller MUST be told what is at fault.
- **FR-058**: Where the posting of a generated image fails for a reason of the Discord service rather
  than of the generation, the **textual** announcement MUST be what is enqueued for retry. A generated
  image MUST NOT be enqueued.
- **FR-059**: `images test verdicts` MUST NOT fall back, having no textual counterpart. A fatal error met
  by it MUST be reported to the league manager who invoked it and no image posted.
- **FR-060**: Non-fatal notices gathered during a generation MUST be reported in the server's logging
  channel, naming the season, the division, the round, the session and the driver they pertain to, and
  MUST NOT appear in any division's verdicts channel. Where a command triggered the generation they MUST
  additionally be reported alongside its output.

#### Test data

- **FR-061**: `images test verdicts` MUST generate **six** images from the one template, each drawn for a
  division named "Test Division", of tier 1 and season number 1, at round 1 of a track of the server's
  track list, each reported to the league manager who invoked the command and **never** posted to any
  division's verdicts channel.
- **FR-062**: The six cases MUST be: a penalty-phase verdict carrying a time penalty **added**, drawn for
  a session of a round of the **sprint** format so a sprint session name can be judged; a penalty-phase
  verdict carrying a time penalty **removed**; a penalty-phase verdict carrying a **disqualification**;
  an **appeal**-phase verdict; an **autosack**; and an **autoreserve**.
- **FR-063**: The fabricated descriptions and justifications MUST include, so far as six cases allow: one
  short enough for a single line; one filling the field to the greatest number of lines it admits; one
  exceeding that by a little; one exceeding it by an order of magnitude; and one for which the steward
  entered neither.
- **FR-064**: The nationalities of the fabricated drivers MUST be among those the signup wizard accepts,
  at least one being the value recorded for a driver who stated none.
- **FR-065**: Where the server's track list is empty, the command MUST be rejected with a clear error,
  there being no round for a verdict to pertain to.

### Key Entities

This feature introduces **no** new entity and amends none.

- **A verdict's message is not recorded.** It is posted once and never edited, replaced or deleted, so
  no table holds its id and the image flow adds no column to any that exists. This is the static
  declaration of FR-007 at its strongest.
- **`PenaltyRecord`, `AppealRecord` and the division's configured verdicts channel** already carry
  everything the three kinds of verdict are read from, and are read as they stand.
- **The attendance module's autosack and autoreserve enforcements** already compose their own
  description and justification, and are read as they stand.
- **The descriptive rendering of a sanction** already exists in the verdict announcement service and is
  the code FR-026 obliges the graphic to call.
- **The text-measurement dependency** already exists and is declared; this is the first image type to
  exercise it for wrapping rather than for single-line truncation.
- **The `verdicts_template` slot, the `verdicts` aspect and its toggle, the `images test verdicts` value,
  the flag directory and the team image directory** are all part of the configuration surface delivered
  at 035 and 036, and are read as they stand.
- **No asset class is added and no file is shipped.** Neither the flag nor the team-image vocabulary is
  the module's own, so a league supplies both on the ordinary terms.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A league manager can see all six kinds of verdict graphic without a penalty existing, a
  steward acting or a driver being sanctioned, in one command and under a minute.
- **SC-002**: A league manager configuring a verdicts template that cannot draw a verdict is told so at
  the moment they configure it — not when a steward approves a review — in 100% of cases.
- **SC-003**: A steward's free text of any length reaches the canvas either whole or cut with an ellipsis
  and a notice, and never runs off the edge of the graphic or overlaps what is drawn beside it.
- **SC-004**: The paragraphs a steward wrote appear on the graphic as the paragraphs they wrote.
- **SC-005**: A failed render costs at most one picture: that verdict falls back to text, and every other
  verdict of the same review and of every other division posts as a graphic unaffected, in 100% of cases.
- **SC-006**: No verdict graphic ever delays or prevents the finalisation of a review or the enforcement
  of a sanction, in 100% of cases.
- **SC-007**: No Discord mention appears anywhere on any verdict graphic, including inside the free text
  the attendance module composes.
- **SC-008**: No problem and no notice raised by verdict image generation ever appears in a channel
  drivers read.
- **SC-009**: Every value appearing on both a verdict graphic and its textual counterpart reads
  identically, and a change to how the text path renders such a value appears on the graphic with no
  further work.
- **SC-010**: Enabling or disabling the `verdicts` toggle changes what the verdicts channel receives and
  changes nothing about which penalties are applied, which appeals are resolved, or which sanctions are
  enforced.

## Assumptions

- **The default leading is removed rather than kept.** The fill pipeline substitutes a line height of
  1.2 where a wrapped field declares none. FR-013 makes that a fatal error, per the wip-spec and
  constitution XIV.5. The constant predates any wrapping image type and **no shipped template reaches
  it**: of the fifteen packaged templates, `verdicts_template.svg` is the only one declaring
  `shape-inside` at all, and both of its wrapped fields — `description` and `justification` — declare
  `line-height`. Removing the default therefore breaks nothing that renders today.
- **The wrapping contract is general, not the verdict's own.** The wip-spec's conventions section defers
  to the verdicts section for it, so FR-008 through FR-024 are written as obligations of the module and
  are expected to be exercised by later image types unchanged.
- **The fixed text for an absent description or justification is the text path's, unadorned.** The
  textual announcement wraps it in channel emphasis; FR-033 assumes the right repair is for the value to
  be produced unadorned where it is produced, both paths then applying what they need — not for the
  image utility to strip markup out of a string handed to it.
- **A verdict's team is read from the result record.** For a penalty or an appeal the session result
  records the Discord role of the team the driver drove for, which is what FR-034 resolves. An
  attendance sanction has no such record, which is why FR-030 empties both fields rather than resolving
  anything.
- **The name drawn is the name at the moment of generation, and is correct forever after.** The static
  declaration of FR-007 rests on this: a verdict records the name under which the decision was issued,
  and a later rename does not falsify it.
- **The sanction rendering is inherited whole.** FR-026 calls the existing descriptive rendering and
  asserts nothing about what it returns. Its behaviour was **confirmed correct by the author on
  2026-08-14**: a positive magnitude is time *added* to the driver's time, a negative one time
  *removed*. The function's docstring stated the reverse and has been corrected in this change; the
  `specs/026-*/` message-format contract carries the same inversion and is left alone, `specs/` being
  derived and not hand-maintained. Nothing about the rendering itself changes, and the graphic must not
  restate it, which FR-025 forbids.

## Out of Scope

- Every other image type. The calendar, lineup, results, standings, attendance, check-in and weather
  types are untouched.
- The penalty, appeal and attendance-sanction flows themselves: what is decided, by whom, on what
  evidence, and what a sanction does to a driver's seats are the owning modules' and are read, never
  changed.
- The content of the textual announcement. It is refactored only so far as FR-033 requires the fixed
  absent-value text to be callable unadorned, and what it *says* is unchanged. No stage is added to it.
- The steward module. This type is specified against the penalty and appeal flow as it stands; the
  catalogue and template are expected to change when that module lands, and nothing here anticipates
  how.
- New configuration commands. The template slot, the toggle, the test value and both asset directories
  already exist.
- Any persistence of a verdict's message id, which FR-050 forbids.
