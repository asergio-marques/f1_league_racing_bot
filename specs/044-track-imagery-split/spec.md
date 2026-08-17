# Feature Specification: Track Imagery Split

**Feature Branch**: `044-track-imagery-split`
**Created**: 2026-08-17
**Status**: Draft
**Input**: User description: "Check in images will be able to optionally take either the track's country flag or the track map, or both. Calendar images will be able to optionally take either a track's country flag or a track map, or both, for each round. All other usages of imagery to indicate a track for other tracks shall be the country flag exclusively. The prepackaged image template for check ins and calendar will be modified to use both the track maps and the flags. Update docs/wip-specs as needed. Do not modify the POC, it is out of scope."

> **The rules this increment builds to are already written.** Constitution v5.0.0 Principle XIV
> Rule 13 governs the two imagery classes, the country keying and the per-class fallbacks;
> `docs/wip-specs/image_module_specification.md` carries them under "The imagery of a round",
> "The country a flag stands for" and "When the imagery of a round is not found". This document
> states **what this increment delivers and how it is judged done** — it does not restate a rule,
> and where the two appear to disagree the wip-spec wins.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One flag directory, keyed by country (Priority: P1)

A league manager keeps a single folder of flag artwork. Every flag the bot draws — beside a
driver's name on a lineup, and above a round on a standings table — is looked up in that one
folder under the name of a **country**. A driver who signed up as `British` draws the same
`great_britain.svg` that the British Grand Prix draws.

**Why this priority**: Every other story in this increment resolves a round's flag out of this
directory, so none of them can be demonstrated until the directory is keyed the one way. It is
also the only backward-incompatible part of the increment: until it lands, a league's flag folder
is keyed on adjectives, and afterwards it is keyed on countries.

**Independent Test**: Point the flag directory at a folder holding `great_britain.svg` and
`other.svg`, generate a lineup for a division holding one `British` driver and one who stated no
nationality, and confirm the first draws `great_britain.svg`, the second `other.svg`, and no
notice is raised for either.

**Acceptance Scenarios**:

1. **Given** a driver whose signup records the nationality `British`, **When** any graphic drawing
   that driver's flag is generated, **Then** the file `great_britain.svg` is resolved from the
   configured flag directory.
2. **Given** a driver whose signup records `Other`, **When** their flag is drawn, **Then**
   `other.svg` is resolved, `Other` having been carried through as no country at all.
3. **Given** the flag directory holds no file for a driver's country, **When** their flag is drawn,
   **Then** the directory's `fallback.svg` is drawn and one non-fatal notice names the field and
   the country that had no file of its own.
4. **Given** a league that has switched nationality collection off, **When** a lineup is generated,
   **Then** no driver flag is drawn anywhere on it and no notice whatever is raised.
5. **Given** the module's nationality-to-country map, **When** it is checked against every
   canonical nationality the signup wizard admits, **Then** every one of them has a country, the
   map being total by test rather than by fallback.

---

### User Story 2 - A round is a flag everywhere it is a heading (Priority: P2)

A league manager looks at a standings table, an attendance sheet or a weather forecast. Each round
is headed by the flag of the country it is run in. No circuit outline appears on any of them,
those graphics drawing a round in a space too small for one to read.

**Why this priority**: This is the correction the increment exists for — four graphics presently
draw a circuit map at a size at which no circuit is recognisable. It delivers value on its own the
moment US1 is in place, and it is what makes the single flag directory worth having.

**Independent Test**: Generate a drivers' standings image for a division holding rounds in Great
Britain and Brazil and confirm each round heading carries that country's flag, that no field
resolves anything from the track image directory, and that the ids naming those fields end in
`_flag`.

**Acceptance Scenarios**:

1. **Given** a division holding rounds at Silverstone and Interlagos, **When** either standings
   graphic is generated, **Then** each round heading draws the flag of that round's country and no
   circuit map is resolved for the graphic at all.
2. **Given** an attendance sheet drawn over a calendar of rounds, **When** it is generated, **Then**
   each round column heading draws that round's country flag.
3. **Given** a weather forecast at any phase, **When** it is generated, **Then** the round's imagery
   is its country flag.
4. **Given** a league that has switched nationality collection off, **When** any of these graphics
   is generated, **Then** the flags of the **rounds** are drawn as normal, that switch governing
   only what is collected from a driver.
5. **Given** a template of one of these types that declares a track-map field, **When** it is
   configured or validated, **Then** it is refused and the offending field is named.

---

### User Story 3 - A calendar pictures each round either way, or both (Priority: P3)

A league manager authoring a calendar template chooses, round by round, whether that round shows
its country flag, its circuit map, or both side by side. A round showing neither is equally valid.

**Why this priority**: The calendar is the graphic on which a circuit map reads best and the one a
league is likeliest to want both on. It is independent of US4 and can ship without it.

**Independent Test**: Author a calendar template declaring both a flag slot and a map slot for
round 1, a flag alone for round 2, a map alone for round 3 and neither for round 4; generate the
calendar and confirm each round draws exactly what its template declared.

**Acceptance Scenarios**:

1. **Given** a calendar template declaring both a flag slot and a map slot for a round, **When** the
   calendar is generated, **Then** both are drawn for that round.
2. **Given** a calendar template declaring only one of the two for a round, **When** the calendar is
   generated, **Then** that one is drawn and the other's absence raises nothing, both being optional.
3. **Given** a round whose circuit has a map but whose country has no flag, **When** the calendar is
   generated, **Then** the map is drawn as normal, the flag draws the **flag** directory's fallback,
   and the circuit map is never substituted for the missing flag.
4. **Given** a round of the mystery format, **When** the calendar is generated, **Then** its flag
   slot draws `mystery.svg` from the flag directory and its map slot `mystery.svg` from the track
   image directory, and no field is emptied and no notice raised.

---

### User Story 4 - A check-in call pictures the round either way, or both (Priority: P3)

A driver opening a check-in call sees the round it is calling them to, pictured by its country
flag, its circuit map, or both, according to what the league's template declares.

**Why this priority**: Same value as US3 on a different graphic, and independent of it. Placed
equal in priority because neither blocks the other.

**Independent Test**: Author a check-in template declaring both slots, generate the check-in
graphic for a round, and confirm both are drawn; remove one slot and confirm the graphic is still
produced with the other.

**Acceptance Scenarios**:

1. **Given** a check-in template declaring both a flag slot and a map slot, **When** the graphic is
   generated, **Then** both are drawn.
2. **Given** a check-in template declaring neither, **When** the graphic is generated, **Then** it
   is produced with no round imagery and no notice is raised.
3. **Given** a round of the mystery format, **When** the check-in graphic is generated, **Then**
   both slots draw their class's `mystery.svg` and no mandatory field is emptied for want of a track.
4. **Given** a template that gives the flag or the map a plate or card of its own, **When** a round
   carrying no track is drawn, **Then** the removable group of those fields is removed entire, so
   that nothing stands empty under a label naming what is not there.

---

### User Story 5 - The shipped templates show both out of the box (Priority: P4)

Somebody cloning the repository generates a calendar and a check-in graphic and sees both a country
flag and a circuit map on each, drawn out of the packaged placeholder artwork, without authoring
anything.

**Why this priority**: It is the demonstration of US3 and US4 rather than a capability of its own,
and it cannot be built until they are. It is what stops the two new slots going unexercised by
anyone who has not read the spec.

**Independent Test**: On a clean clone, run the calendar and check-in test renders and confirm each
carries both a flag and a map per round, every one of them resolved from packaged files.

**Acceptance Scenarios**:

1. **Given** a clean clone with no league artwork placed, **When** the calendar test render is run,
   **Then** every round draws both a flag and a map, out of packaged placeholders.
2. **Given** a clean clone, **When** the check-in test render is run, **Then** the round draws both
   a flag and a map.
3. **Given** the packaged flag directory, **When** it is inspected, **Then** it holds a
   `mystery.svg` alongside its `fallback.svg`, authored at 3:2 and carrying no text.
4. **Given** a calendar drawn for a division holding a mystery round, **When** the test render is
   run on a clean clone, **Then** that round draws the packaged `mystery.svg` of each class,
   neither letterboxed.

---

### Edge Cases

- **A country with several circuits.** Las Vegas, Miami and the Circuit of the Americas all resolve
  one `united_states.svg`. This is the intended result and no attempt is made to tell them apart.
- **A mystery round.** Conceals its track and thereby its country; both classes resolve the datum
  `Mystery`, and both directories carry a `mystery.svg` for it — the track directory's already
  shipped, the flag directory's newly authored at that class's 3:2.
- **A flag missing where a map is present, and the reverse.** Each class answers its own miss with
  its own fallback. Neither class is ever substituted for the other.
- **Neither the file nor a fallback.** The render is abandoned, as it is for any asset class.
- **A nationality with no country in the map.** A defect of the module, caught by a test over the
  map; never a render-time branch.
- **Nationality collection switched off.** Suppresses driver flags only. A round's flag is drawn
  regardless, standing for the round and not for a driver.
- **A disallowed template.** A standings, attendance or weather template declaring a track-map field
  is refused when configured and at validation, the field being named.
- **A converted slot left at the wrong shape.** A round heading re-pointed from the track class to
  the flag class but left at 1:1 draws every flag letterboxed, the generator never padding. This is
  the likeliest defect of the conversion and is why FR-013b is stated separately from FR-013.
- **A league upgrading with an adjective-keyed flag folder.** Every driver draws the fallback until
  the files are renamed to countries. The bot is not in production, so this affects no live league,
  but it is the one visible consequence of the change and belongs in the release note.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every flag drawn by any graphic MUST resolve from the single configured flag
  directory, under the normalised name of a country.
- **FR-002**: The module MUST ship a map relating every canonical nationality the signup wizard
  admits to the name of its country, and a driver's flag MUST resolve from that country.
- **FR-003**: The totality of that map over the signup wizard's canonical nationalities MUST be
  covered by a unit test. An unmapped nationality MUST NOT be answered by a fallback at render time.
- **FR-004**: `Other` MUST be carried through unchanged and resolve `other.svg`, being no country.
- **FR-005**: A round's flag MUST resolve from the country recorded by that round's track object.
- **FR-006**: The calendar and the check-in graphic MUST each be able to draw a country flag, a
  circuit map, both, or neither, every one of those four being a valid template.
- **FR-007**: The calendar MUST make that choice **per round**, one round drawing both while another
  draws one or neither.
- **FR-008**: The standings graphics, the attendance sheet and the weather forecasts MUST draw a
  round's country flag and MUST NOT draw a circuit map.
- **FR-009**: A template of a type other than the calendar and the check-in graphic that declares a
  track-map field MUST be refused, both when it is configured and at validation, naming the field.
- **FR-010**: Field ids MUST name the class they draw: a slot drawing a country flag carries the
  `_flag` suffix, a slot drawing a circuit map the `_image` suffix.
- **FR-011**: A miss in either class MUST draw that class's own `fallback.svg` and raise one
  non-fatal notice. Neither class MUST EVER be substituted for the other.
- **FR-012**: A round of the mystery format MUST resolve both classes from the datum `Mystery`, and
  the module MUST ship `resources/flags/mystery.svg` beside `resources/tracks/mystery.svg`.
- **FR-012a**: `resources/flags/mystery.svg` is the **only new packaged asset** this increment adds.
  `resources/tracks/mystery.svg` already ships at 1:1 and is unchanged. The new file MUST be
  authored at the flag class's 3:2, as plain SVG with no `clipPath`, gradient or filter, and MUST
  carry **no text** — text in an asset is subject to font substitution and would rasterise
  differently from one machine to the next. It is a placeholder in the same sense as the packaged
  fallbacks and is a reserved filename a league may replace but not rename.
- **FR-013**: The packaged calendar and check-in templates MUST each declare both a flag slot and a
  map slot, and MUST render both from packaged artwork on a clean clone.
- **FR-013a**: Every flag slot on every packaged template MUST carry the flag class's aspect of 3:2,
  and every track-map slot the track class's aspect of 1:1, whether the flag stands for a driver or
  for a round. The two classes need not match each other.
- **FR-013b**: The round-heading slots of the standings, attendance and weather templates MUST be
  **re-geometried** from the track class's 1:1 to the flag class's 3:2 as part of their conversion.
  A converted slot left at its old shape would letterbox every flag drawn into it.
- **FR-013c**: A template declaring a slot of a class at an aspect other than that class's MUST be
  refused, naming the offending field.
- **FR-014**: Switching nationality collection off MUST suppress driver flags only, leaving a
  round's flag drawn.
- **FR-015**: `docs/wip-specs/image_module_specification.md` MUST carry the rules this increment
  builds to, and `README.md` and `resources/README.md` MUST be brought into step with the delivered
  behaviour before the increment is reported complete.
- **FR-016**: Every implementation task MUST carry a passing unit test, and no test may require a
  live Discord bot.

### Key Entities

- **Nationality-to-country map**: A module-shipped constant relating each canonical nationality
  adjective to a country name. Total over the signup wizard's vocabulary. Not a database table, and
  no schema changes with it.
- **Flag asset class**: The one directory serving both a driver's flag and a round's, keyed on a
  country. Carries `fallback.svg` and, newly, `mystery.svg`.
- **Track asset class**: Circuit maps, keyed on the circuit, drawn by the calendar and the check-in
  graphic alone. Unchanged in keying; narrowed in who may draw it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A league manager needs **one** folder of flag artwork, named for countries, to flag
  every driver and every round across all graphics — down from one folder of nationality flags plus
  one of circuit artwork used for both purposes.
- **SC-002**: Every round heading on a standings table, an attendance sheet and a weather forecast
  is identifiable at the size it is drawn, where a circuit outline at that size was not.
- **SC-003**: A league can present a round four ways on a calendar or a check-in call — flag, map,
  both, or neither — by editing its template alone, with no configuration command run and no bot
  restart.
- **SC-004**: A clean clone draws both a flag and a map on its calendar and check-in renders at the
  first attempt, with no artwork placed and no configuration set.
- **SC-005**: A league whose artwork set is incomplete still gets every graphic: a missing file in
  either class degrades to that class's placeholder and reports which value needed it, and never
  produces the imagery of the other class.
- **SC-006**: The full test suite passes, and every behaviour above is exercised by a test that
  requires no running bot.

## Assumptions

- **The rules are settled and are not reopened here.** Constitution v5.0.0 ratified the two classes,
  the country keying, the calendar-and-check-in restriction, the per-class fallbacks and
  `flags/mystery.svg`. This increment implements them.
- **No migration and no backfill.** The bot is not in production, so no live league's flag directory
  needs converting and no stored data changes. The rename is a release note, not a data task.
- **No configuration surface is added.** Both directories are already configurable. Nothing in this
  increment adds a command, a parameter or a stored setting; the choice lives in the template.
- **No database change.** The nationality-to-country map is a shipped constant, and `Track.country`
  already exists.
- **Aspect ratios are unchanged per class, and are uniform across templates**: flags at 3:2
  (120 × 80) and circuit maps at 1:1 (120 × 120), as `resources/README.md` records. The two classes
  do not share an aspect with each other and are not required to; a template drawing both draws two
  slots of differing shape, which is the template author's business and no change to the generator,
  which never pads. What is required is uniformity **within** a class: a league authors one file per
  datum, so a class serving two aspects would letterbox that file wherever it did not match.
- **`poc/` is untouched**, as the user directed and as the project conventions already require.
- **Verification is by PNG**, never by SVG in a browser.

## Out of Scope

- Any change to `poc/`.
- A per-league or per-graphic configuration toggle choosing flag over map. The choice is expressed
  by the template, and adding a setting was considered and rejected when the rules were ratified.
- A second flag directory, or keying a round's flag on the circuit so that circuits in one country
  could differ. One country, one flag, deliberately.
- Any change to the results or verdicts graphics beyond the flag rekey they inherit from US1 —
  neither pictures a round.
- Full system testing against a live Discord server, which is done by hand outside this repository.
