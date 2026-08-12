# Feature Specification: Template Verification & Graphic Conventions

**Feature Branch**: `036-image-generation-conventions`
**Created**: 2026-08-12
**Status**: Draft
**Input**: Author's brief — verification of configured template files, and the conventions
that hold for every graphic the module draws. Specific image types are specified in later
sessions.

## Scope

This specification covers the rules that hold across **every** image type: when a template is
verified, what a generation may do to a field, how a field is addressed, how errors are
classified and where they are reported.

It does **not** specify any individual image type. No field catalogue is written here. Every
rule below refers to "the image type's generation specification" as the place a catalogue
lives; those documents arrive in later sessions.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A template that cannot serve is refused at the moment it is named (Priority: P1)

A league manager runs one of the `/images template <kind>` commands to point the bot at an SVG
file they have authored. The bot checks the file there and then — that the name ends in `.svg`,
that a file exists at that name inside the configured template directory, that it parses, and
that it carries every field the image type requires. If any of these fails, the command is
refused and the configuration is left exactly as it was.

**Why this priority**: This is the moment the manager is present, holding the file, and able to
fix it. A configuration that is accepted and only later found unusable moves the discovery to
a race weekend, when nobody is watching the logs. Today the command stores the value first and
reports validity as a warning afterwards; this story inverts that.

**Independent Test**: Configure a template with each class of defect — wrong extension, absent
file, malformed SVG, missing mandatory field — and confirm the command is refused each time and
that reading the configuration back shows the previous value untouched.

**Acceptance Scenarios**:

1. **Given** a valid template configured, **When** a manager names a file without a `.svg`
   ending, **Then** the command is refused, the reason names the extension, and the previously
   configured filename is still in force.
2. **Given** a template directory containing no such file, **When** a manager names it, **Then**
   the command is refused naming the full location searched, and the configuration is unchanged.
3. **Given** a file that is present but malformed, **When** a manager names it, **Then** the
   command is refused, and the message states that the file is not a valid SVG and what was
   found to be at fault.
4. **Given** a well-formed SVG that lacks a field the image type requires, **When** a manager
   names it, **Then** the command is refused and the message names the missing field.
5. **Given** a well-formed SVG carrying every mandatory field, **When** a manager names it,
   **Then** the command succeeds and the new filename is in force.

---

### User Story 2 - Season review answers for every template at once (Priority: P2)

With the image module enabled, a season review verifies every configured template on the same
terms as the configuration command. Any template that would be fatal to a generation fails
validation of the season, and the review names each file and what is wrong with it.

**Why this priority**: A season can be reviewed after templates were edited outside the bot, or
after the template directory moved. It is the one moment before a season runs at which every
template is looked at together.

**Independent Test**: Enable the module, break two of the fifteen templates in different ways,
run a season review, and confirm validation fails and that both files are named individually
with distinct reasons.

**Acceptance Scenarios**:

1. **Given** the module enabled and every template sound, **When** a season review runs,
   **Then** template verification contributes no failure.
2. **Given** the module enabled and one template missing a mandatory field, **When** a season
   review runs, **Then** the season fails validation and the report names that template and
   that field.
3. **Given** the module disabled, **When** a season review runs, **Then** templates are not
   verified and no template finding appears.

---

### User Story 3 - A generation re-checks the template against the data it is about to draw (Priority: P3)

Immediately before any graphic is produced, the bot checks the template again — this time
against the concrete values it is about to place. Data changes after a template is configured,
and a field that was satisfiable in March may not be in August.

**Why this priority**: Configuration-time verification cannot see the data. This is the check
that catches a division that outgrew its template or a value that cannot be determined.

**Independent Test**: Configure a sound template, change the underlying data so a mandatory
value can no longer be determined, trigger a generation, and confirm no image is produced and
the failure is reported.

**Acceptance Scenarios**:

1. **Given** a template carrying every mandatory field, **When** a generation finds a mandatory
   value it cannot determine, **Then** no image is produced and the failure is reported.
2. **Given** a template missing an optional field, **When** a generation runs, **Then** the
   image is produced without it.
3. **Given** a generation that cannot determine an optional value, **When** it runs, **Then**
   the image is produced and that field is emptied or removed.

---

### User Story 4 - A manager authors a template in an SVG editor and it works (Priority: P3)

A league manager draws a template in a graphical SVG editor. They name a layer for a field
rather than editing the node identifier the editor generated, they wrap a field and its label
in a group so both leave together when the value is absent, and they give a name field a
declared width so a long Discord name cannot run into the column beside it. The bot honours
all three.

**Why this priority**: The templates are hand-authored by people who are not editing XML. A
convention only reachable by hand-editing the file is a convention that will not be followed.

**Independent Test**: Author one template using a layer label instead of an identifier, one
using a `_group` wrapper, and one declaring `inline-size`, and confirm each behaves as
specified.

**Acceptance Scenarios**:

1. **Given** a template with no node bearing a field's identifier but a layer labelled with the
   field's name, **When** the field is addressed, **Then** the labelled layer is used.
2. **Given** a template where both a node of that identifier and a layer of that label exist and
   are different nodes, **When** the field is addressed, **Then** the node bearing the
   identifier is used.
3. **Given** a field wrapped in a group named for the field plus `_group`, **When** the rules
   would empty or remove that field, **Then** the whole group is removed and the field itself is
   left untouched.
4. **Given** the same field with no such group, **When** the rules would empty or remove it,
   **Then** the field alone is emptied or removed.
5. **Given** a group removed from a graphic, **When** the graphic is drawn, **Then** the canvas
   is the same size as it would have been with the group present.

---

### User Story 5 - A failure reaches the right audience (Priority: P2)

Nothing that goes wrong inside the module is ever shown to the drivers of the league. A
non-fatal problem goes to the server's logging channel, and to the person who ran the command
when a command caused it. A fatal problem in a posting somebody commanded is reported to that
person and nothing is posted; a fatal problem in a posting nobody commanded falls back silently
to the text output the bot has always produced.

**Why this priority**: The distinction between a commanded and an uncommanded posting is what
decides whether a manager gets the chance to fix something. It cannot be retrofitted after the
generation utilities are written.

**Independent Test**: Trigger the same fatal condition twice — once by command, once at a
scheduled horizon — and confirm the first is refused with an explanation to the caller and the
second posts the text output.

**Acceptance Scenarios**:

1. **Given** a fatal condition, **When** a user commands a posting, **Then** the command is
   refused, the caller is told what is at fault, and nothing is posted to any channel.
2. **Given** the same fatal condition, **When** a posting is reached at a horizon, at a schedule
   or at startup, **Then** the traditional text output is posted in place of the graphic.
3. **Given** a non-fatal condition in a commanded generation, **When** it occurs, **Then** the
   graphic is posted, and the condition is reported both in the logging channel and alongside
   the command's own output.
4. **Given** any condition, fatal or not, **When** it is reported, **Then** it appears in no
   channel that drivers of the league read.

---

### Edge Cases

- A filename ending in `.SVG` or `.Svg` — extension matching is case-insensitive; the file that
  is opened is whatever the host filesystem resolves.
- A filename ending in `.svg` that names a directory — treated as no file being there.
- A file whose content is not SVG at all but is well-formed XML — the root element is not `svg`,
  which is a parse failure and reported as such.
- A comment containing a run of two hyphens — the readiest way to author an unparseable file,
  and the case the "never show the raw parser error" rule exists for.
- Two fields whose `_group` wrappers are nested — the outermost group named for a field being
  removed is removed; a group inside it is removed with it.
- A field wrapped in a `_group` that is never emptied — the group is simply never removed.
- A layer labelled with a field's name that is itself inside a removed group — it goes with the
  group like any other node.
- A single word wider than the whole `inline-size` it is given — broken within the word rather
  than dropped.
- A field declaring `shape-inside` but no `inline-size` — see Assumptions.
- A template naming a font the machine does not carry — non-fatal, reported, and the graphic is
  drawn in a face of a different width, so line counts may differ from another machine's.
- A nationality, team or track whose name normalises to the empty string — no file can resolve,
  so it is treated as an unresolved asset and takes the fallback or the field's classification.
- An asset directory holding a `fallback.svg` and nothing else — every image field of that class
  draws the fallback and reports a non-fatal error per datum.
- A datum literally named "Fallback" — its slug collides with the reserved name and it draws the
  fallback image. Accepted; see A-007.
- A season review run while the module is enabled but no template has ever been configured — the
  packaged default filenames are verified like any other.

## Requirements *(mandatory)*

### Functional Requirements — Verification at configuration

- **FR-001**: The system MUST reject a template filename that does not end in `.svg`,
  case-insensitively.
- **FR-002**: The system MUST verify that a file exists at the configured template directory
  joined with the given filename, and MUST reject the command when it does not, naming the full
  location searched.
- **FR-003**: The system MUST verify that the file parses as SVG, and MUST reject the command
  when it does not.
- **FR-004**: The system MUST verify that the file declares every field the image type's
  generation specification marks mandatory, and MUST reject the command when any is absent,
  naming each missing field.
- **FR-005**: A rejected `/images template <kind>` command MUST leave the stored configuration
  exactly as it stood. No partial write may occur.
- **FR-006**: A rejection MUST name the file and state what was found to be at fault.

### Functional Requirements — Verification at season review

- **FR-007**: When the image module is enabled, a season review MUST apply FR-002 through FR-004
  to every configured template.
- **FR-008**: A template failing that verification MUST fail validation of the season, and the
  review MUST name the individual template at fault and its reason. A report naming a group of
  templates rather than the one at fault does not satisfy this requirement.
- **FR-009**: When the image module is disabled, a season review MUST NOT verify templates and
  MUST report no template finding.

### Functional Requirements — Verification at generation

- **FR-010**: Immediately before every generation, the system MUST verify the template's
  mandatory fields against the concrete data the graphic is to be filled with.
- **FR-011**: A mandatory field whose value cannot be determined at generation MUST be a fatal
  error.
- **FR-012**: A mandatory field absent from the template file MUST be a fatal error.
- **FR-013**: An optional field that is absent from the template, or whose value cannot be
  determined, MUST NOT be a fatal error. Where the value cannot be determined and the field is
  present, the field MUST be emptied or removed per FR-022.
- **FR-014**: A fatal error at generation MUST prevent the graphic from being produced.

### Functional Requirements — What a generation does to a field

- **FR-015**: A generation MUST perform only these operations upon a field: place text upon it;
  place an image upon it by the reference the field carries; set the colour of its text; break
  its text into lines where it is a wrapping field; truncate its text to the room the field
  declares; empty it or remove it.
- **FR-016**: Setting a colour MUST write the declaration into the element's inline style,
  merged with the declarations already standing there. It MUST NOT be written as a presentation
  attribute, and MUST NOT replace the inline style wholesale.
- **FR-017**: Setting the colour of a field MUST NOT count as filling it. A recoloured field MUST
  still be filled as any other.

### Functional Requirements — Addressing of fields

- **FR-018**: A field MUST be addressed by the identifier of a node of the SVG file. The
  identifier is normative.
- **FR-019**: Where a template declares no node bearing a field's identifier but declares a layer
  whose label is the name of that field, the labelled layer MUST be taken for that field.
- **FR-020**: Where both a node of that identifier and a layer of that label exist and are not
  the same node, the node bearing the identifier MUST be taken for that field.
- **FR-021**: A field belonging to member *x* of a repeating row MUST be identified as
  `row_<x>_<field>`, where *x* is the row's number written plainly, without padding
  (`row_1_fastest_lap`, `row_10_fastest_lap`). The row group, where one is declared, is
  `row_<x>_group` per FR-022.

### Functional Requirements — Removable groups

- **FR-022**: Any field named in an image type's catalogue, mandatory or optional, MAY be wrapped
  in a group bearing the name of that field followed by `_group`.
- **FR-023**: Where such a group is declared, it MUST be removed in its entirety wherever the
  rules would have the field emptied or removed, and the field itself MUST be left untouched.
- **FR-024**: Where no such group is declared, the field itself MUST be emptied or removed.
- **FR-025**: A template MAY declare `_group` wrappers beyond those an image type's catalogue
  names explicitly, and the system MUST honour them on the same terms.
- **FR-026**: The removal of a group MUST NOT resize the canvas.

### Functional Requirements — Errors and the rejection of input

- **FR-027**: Every error condition MUST be classified fatal or non-fatal by the image type's
  generation specification.
- **FR-028**: A fatal error traceable to something a user configured or commanded MUST reject
  that input at every moment the system is in a position to detect it, specifically:
  a `/images template <kind>` command naming such a template (FR-005); a season review (FR-008);
  a command that would carry a division past what its configured templates can draw, which MUST
  be rejected with the change it carried not applied; and a command that triggers a generation
  meeting such an error, which MUST be rejected with nothing posted in consequence.
- **FR-029**: Fallback to the traditional text output MUST apply only to a posting no user
  commanded — one reached at a horizon, at a schedule or at startup.
- **FR-030**: A posting a user commanded MUST NOT fall back to text. The caller MUST be told what
  is at fault and invited to correct it.
- **FR-031**: A non-fatal error MUST be reported in the server's logging channel, and
  additionally alongside the output of the command where a command triggered the generation.
- **FR-032**: No error, fatal or non-fatal, may be reported in a channel read by the drivers of
  the league.

### Functional Requirements — The canvas

- **FR-033**: The width and the height a template declares MUST be the width and the height at
  which it is drawn, and the conversion to PNG MUST honour them. No canvas may be assumed of any
  template. The vertical crop of the calendar graphic is the sole exception and is specified with
  that image type.

### Functional Requirements — Fonts

- **FR-034**: The substitution of a font the machine does not carry MUST be non-fatal and MUST be
  reported, naming both the field and the font.
- **FR-035**: The documentation offered wherever templates are configured MUST state that a
  template either embeds the font it names or is authored against the font the host resolves,
  and that wrapping, line count and reduced size are consequently properties of the machine that
  drew the graphic.

### Functional Requirements — The room a text is given

- **FR-036**: A text field declaring an `inline-size` and no `shape-inside` whose text exceeds
  that room MUST be truncated at a word boundary, ended with an ellipsis, and MUST raise a
  non-fatal error naming the field.
- **FR-037**: A single word wider than the declared room MUST be broken within itself rather than
  dropped.
- **FR-038**: A field declaring both `inline-size` and `shape-inside` is a wrapping field, and
  MUST be wrapped and reduced as specified for the verdicts graphic rather than truncated.
- **FR-039**: A field declaring neither MUST be drawn as a single line of unbounded width.

### Functional Requirements — Images placed on a graphic

- **FR-040**: The system MUST NOT pad or letterbox an image at generation.
- **FR-041**: The documentation offered wherever assets are configured MUST state that an image
  file is authored at exactly the aspect of the slot it fills, padded with transparent margins
  where its subject does not fill that aspect, and that an image of another aspect is
  letterboxed by the converter with its outermost pixels carried outward across the band.
- **FR-042**: An image field MUST resolve its file as the configured directory for that asset
  class, joined with the normalised form of the datum it depicts, with an `.svg` extension.
  Normalisation is: trim, lowercase, decompose and strip diacritics, replace every run of
  characters that is neither a letter nor a digit with a single underscore, and drop leading and
  trailing underscores. `Red Bull Racing` resolves to `red_bull_racing.svg`; `São Paulo` resolves
  to `sao_paulo.svg`.
- **FR-043**: Each asset directory MAY hold a **generic fallback image** under the reserved name
  `fallback.svg`. Where the normalised filename does not resolve and that directory holds a
  fallback, the fallback MUST be used, for a mandatory field and an optional field alike, and a
  non-fatal error MUST be reported naming the field and the datum that had no file of its own.
- **FR-044**: Where the normalised filename does not resolve and the directory holds no
  fallback, the outcome MUST follow the field's classification: fatal for a mandatory field,
  and for an optional field, the field is emptied or removed per FR-022 with a non-fatal error
  reported.
- **FR-045**: The fallback image is bound by FR-040 and FR-041 exactly as any other asset: it is
  authored at the aspect of the slot, and the system MUST NOT pad it at generation. Where one
  asset class serves slots of differing aspects, its fallback MUST be authored to the same
  aspect its ordinary assets are.

### Functional Requirements — Reporting of template validity

- **FR-046**: A file that cannot be parsed MUST be reported as an invalid SVG file, naming the
  file and what was found to be at fault. The raw error of the parser MUST NOT be surfaced to a
  user.
- **FR-047**: The documentation offered wherever templates are configured MUST state that
  `text-transform` is not honoured by the converter, and that a fixed label wanted in capitals is
  typed in capitals.

### Key Entities

- **Field**: A named addressable point in a template, reached by node identifier and, failing
  that, by layer label. Carries a name, a classification of mandatory or optional, and the set of
  operations its image type applies to it.
- **Field catalogue**: The set of fields an image type declares, split into mandatory and
  optional. Declared by that image type's generation specification; not written here.
- **Removable group**: A group named for a field plus `_group`, standing around that field and
  the static chrome introducing it, removed together with the value when the value is absent.
- **Error classification**: Fatal or non-fatal, declared per condition per image type,
  determining whether a graphic is produced and where the condition is reported.
- **Posting origin**: Commanded or uncommanded. Determines whether a fatal error rejects the
  input with an explanation or falls back to the text output.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A league manager naming an unusable template learns why at the moment they name it,
  in a single exchange, without consulting a log.
- **SC-002**: No configuration mutation survives a rejected template command: reading the
  configuration back after any rejection returns the value that stood before it.
- **SC-003**: Every one of the four verification failure classes — extension, absence, parse,
  missing mandatory field — produces a message distinguishable from the other three, and names
  the individual template at fault.
- **SC-004**: A season review with *n* defective templates names all *n* individually, not the
  first, and not the group.
- **SC-005**: No graphic is ever posted that is missing a mandatory value.
- **SC-006**: Across every failure path, the count of error messages reaching a driver-read
  channel is zero.
- **SC-007**: A commanded posting that cannot be drawn never silently substitutes text: the
  caller receives a statement of the fault in 100% of cases.
- **SC-008**: A league manager can author a working template using only a graphical SVG editor —
  setting layer labels and grouping — without hand-editing node identifiers.

## Assumptions

- **A-001**: Extension matching is case-insensitive, and the `.svg` check is on the name alone.
  Whether the file is really SVG is settled by FR-003, not by its name.
- **A-002**: A field declaring `shape-inside` but no `inline-size` is treated as a wrapping
  field. The brief defines a wrapping field as declaring both and says a field declaring
  "neither" is a single unbounded line, leaving this combination unstated; a `shape-inside` is
  meaningless except as a wrap instruction, so it is honoured as one.
- **A-003**: "Emptied" means the field's text is cleared and the node retained; "removed" means
  the node is deleted. Which applies to a given field is the image type's catalogue to state.
  Where a `_group` is declared, the distinction stops mattering — the group goes.
- **A-004**: Verification at season review reports through the season review's existing
  validation surface rather than introducing a new one.
- **A-005**: The image type generation specifications that FR-004, FR-010 and FR-027 depend on do
  not yet exist. Until an image type has one, its mandatory-field set is empty and those checks
  pass vacuously for it. This matches the existing layered-validity contract, under which a
  check not yet ratified is reported as not applied rather than as passed.
- **A-006**: "A command that would carry a division past what its configured templates can draw"
  (FR-028) refers to a division growing beyond the row capacity a template provides — for
  example, adding a driver to a full grid. The capacity itself is declared per image type.
- **A-007**: The generic fallback image is a reserved filename, `fallback.svg`, inside each asset
  directory. The brief asked for "the possibility of using a generic fallback image" and pointed
  at the proof of concept, which supplies the resolution rule but no fallback of its own. A
  reserved name in the directory that already exists is the least machinery that satisfies it:
  no new configuration field, nothing for a template to declare, and a league adds one by
  dropping a file in. `fallback` cannot collide with a real datum's slug in any realistic case.
- **A-008**: A missing asset is non-fatal wherever a fallback exists, including for a mandatory
  field. A mandatory *field* is about the template carrying the slot, not about every datum
  having a bespoke image — which is the case the flag example in the brief describes.

## Constitution Impact

This specification contradicts the constitution at **v2.13.0** in five places. All five are now
settled here; the constitution MUST be amended to match before implementation begins. Rules
XIV.11 and XIV.13 were ratified in the previous session from answers given without sight of
`docs/wip-specs/image_module_specification.md`, which is deny-listed, and both were guessed
wrong. That reconciliation was recorded as `TODO(WIP_SPEC_RECONCILIATION)`; this section
discharges it.

| Constitution rule | Contradiction | Amendment required |
|---|---|---|
| **XIV.2** — "The only contract … is the set of element `@id` values" | A layer label is a fallback address (FR-019/FR-020) | The identifier is normative; a layer label of the field's name is consulted when no node bears the identifier |
| **XIV.3** — a render fails if any catalogued field is unfilled | Catalogues split into mandatory and optional; an unfilled optional field is not a failure (FR-013) | Carry the mandatory/optional distinction into the rule |
| **XIV.7** — "A failed render MUST fall back to the text output" | Fallback is restricted to uncommanded postings (FR-029/FR-030) | A commanded posting is rejected with an explanation and posts nothing |
| **XIV.11** — `<collection>_<NN>_<field>`, zero-padded | The form is `row_<x>_<field>`, unpadded (FR-021) | Replace the convention; drop the padding and the per-collection prefix |
| **XIV.13** — hyphenated slug; per-field placeholder declared by the template | The slug is **underscore**-separated (FR-042); the fallback is a `fallback.svg` per asset **directory**, not per template field (FR-043) | Replace both halves of the rule |

**On XIV.13 specifically.** The hyphen was an invention of the previous session. The normalisation
this specification states is the one the proof of concept already implements — `normalize()` in
`resources/poc/build_poc.py`, whose docstring calls it "the spec's normalization" — so it is what
the author's own document requires and what every asset already shipped under `resources/` is
named for. Ratifying the hyphen would have renamed every asset in the project.

The specification is consistent with, and in places sharpens, XIV.1 (canvas), XIV.4
(fatal/non-fatal), XIV.5 (text bounds), XIV.6 (asset aspect), XIV.9 (layered validity), XIV.12
(capacity overflow is fatal, detected at the command that would exceed it) and XIV.14
(verification through the rasterised PNG).

## Out of Scope

- Any individual image type's field catalogue, layout or data mapping.
- The calendar graphic's vertical crop, which is specified with that image type.
- The verdicts graphic's wrapping and size-reduction behaviour, referenced by FR-038 and
  specified with that image type.
- Wiring the eight output toggles to their source modules, which remains a later increment.
- Any change to the command surface: no command is added, removed or renamed here.
