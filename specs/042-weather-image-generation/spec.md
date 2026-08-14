# Feature Specification: Weather Image Generation

**Feature Branch**: `042-weather-image-generation`
**Created**: 2026-08-13
**Status**: Draft
**Input**: User description: the weather image types — six field catalogues across three phases and
the mystery notice, the template chosen by the format of the round, the per-slot declaration floors,
data resolution shared with the textual forecast, mismatch handling, generation and posting through
the three-phase chain, and test data.

> **Normative source.** The rules this feature implements are stated in
> [`docs/wip-specs/image_module_specification.md`](../../docs/wip-specs/image_module_specification.md)
> — § "Conventions of every graphic" and § "Weather image generation" — and are governed by Principles
> IV, V, VII and XIV of the constitution. This document does **not** restate those rules. It states what
> must be built, who it is for, and how each obligation is verified, and cites the wip-spec where the
> rule itself lives. Where this document and the image wip-spec disagree, the image wip-spec wins and
> this document is the one to correct.

> **⚠️ `weather_module_specification.md` is stale and is NOT authoritative here.** Confirmed by the
> author on 2026-08-13: the weather module changed after that document was written and it has not been
> revised. It describes Phase 1 as `(Btrack × rand1 × rand2) / 3.025` over a fixed per-track percentage
> table, where the bot in fact draws `Rpc` from a Beta distribution parameterised by per-track μ and σ.
> Do not read it for the pipeline this feature draws. **Principle IV of the constitution, and the
> behaviour of the shipped weather module, govern** — the horizons, the draws, the session types and the
> per-session slot capacities all being stated there and verifiable in the code. The author will revise
> that document separately; until they do, treat any rule found only in it as unverified.

> **What makes these types different.** Weather is the module's most divided aspect: **six templates**
> serve one toggle, where every type before it had one or two. It is the first aspect whose template is
> chosen by a property of the **round** rather than of the thing drawn — the format selects between the
> plain and the sprint file of phases 2 and 3, and nothing else may enter that choice. It is the first
> to hold a **chain of postings across occasions**, each phase's posting deleting its predecessor's
> message. It is the first graphic to carry a value the text path published in **another message of the
> same flow** — the likelihood of rain, computed and posted at phase 1, standing on the phase 2 and
> phase 3 graphics. And the mystery notice is the first **kind of record given an image type of its
> own** rather than a defined literal in a shared one. Constitution v4.7.0 ratified each of these: the
> **selecting datum** (XIV.10), the **capacity fixed by the template slot** (XIV.12), the **lifecycle
> spanning occasions** and the **manner of a message being no part of the chain** (XIV.8), the
> **correspondence with the text path rather than with one message** (XIV.7), the **kind with its own
> type** (XIV.3), and **channel markup being no part of a value** (XIV.16).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preview all six weather graphics before a season depends on them (Priority: P1)

A league manager has authored six weather templates and wants to see what each draws before any
season, division or round exists. They run `/images test weather-p1`, `/images test weather-p2`,
`/images test weather-p3` and `/images test weather-mystery` and get back six PNGs in all — one from
each template — drawn from fabricated data exercising every case the templates can be asked to carry:
a rain likelihood that is not a whole percentage, each of the three session weather types, each of the
five concrete weathers, a session of one slot, a session of one weather throughout, a session of mixed
weather, and a session drawn the greatest number of slots its type allows.

**Why this priority**: It is the only way to see any weather graphic without waiting five days for a
phase horizon, and it is what makes every later story cheap to verify. It depends on no lifecycle, no
forecast channel and no posting path, so it can ship and deliver value on its own.

**Independent Test**: Configure the six templates on a server that has a track list and nothing else,
run each of the four commands, and confirm the PNGs come back with every enumerated case visible and
every non-fatal degradation listed beside them.

**Acceptance Scenarios**:

1. **Given** the phase 1 template is configured and valid, **When** a league manager runs
   `/images test weather-p1`, **Then** one PNG is returned for a division named "Test Division", of
   tier 1 and season number 1, at round 1 of a track of the server's track list, carrying a rain
   likelihood that is not a whole percentage so its rendering can be judged.
2. **Given** both phase 2 templates are configured and valid, **When** the manager runs
   `/images test weather-p2`, **Then** two PNGs are returned — one for a round of the sprint format
   drawn from the sprint template and holding four sessions, one for a round of the endurance format
   drawn from the plain template and holding two — and each of "Sunny", "Mixed" and "Rain" appears at
   least once across them, so each of their icons can be judged.
3. **Given** both phase 3 templates are configured and valid, **When** the manager runs
   `/images test weather-p3`, **Then** two PNGs are returned on the same two rounds, and the slots
   fabricated include a session of a single slot, a session of one weather throughout, a session of
   mixed weather, a session holding the greatest number of slots its type allows, and each of the five
   concrete weathers at least once.
4. **Given** the mystery template is configured and valid, **When** the manager runs
   `/images test weather-mystery`, **Then** one PNG is returned holding the heading fields alone, with
   no track, no session and no forecast upon it.
5. **Given** a phase 3 sprint template declares only two slots for a session, **When**
   `/images test weather-p3` runs, **Then** the command is rejected with a fatal error naming the
   sprint template of phase 3 as the one at fault and no image is posted.
6. **Given** any of the four commands meets a fatal error, **When** it runs, **Then** the error is
   reported to the league manager who invoked it and nothing is posted, there being no textual
   counterpart for a test command to fall back to.

---

### User Story 2 - A division's forecast posted as a graphic through the three phases (Priority: P2)

A league has enabled the `weather` toggle. At each of the three horizons the bot posts the forecast for
each division as a PNG attached to a message carrying the division role mention and nothing besides.
The heading the textual forecast carried is gone from both the message and the picture, the phase
description standing in its place. The chain of deletions runs exactly as it always did: phase 2's
posting deletes phase 1's message, phase 3's deletes phase 2's.

**Why this priority**: It is the feature the league actually asked for, and the largest part of the
work. It depends on Story 1 only for confidence, not for function.

**Independent Test**: Enable the toggle on a division with a configured forecast channel, advance a
round through its three horizons in test mode, and confirm three graphics are posted in turn, each
deleting its predecessor, with the textual forecast appearing nowhere in that channel.

**Acceptance Scenarios**:

1. **Given** the `weather` toggle is enabled and the phase 1 template is valid, **When** the phase 1
   horizon is reached for a division, **Then** a PNG is posted to that division's forecast channel as
   an attachment on a message carrying the division role mention and nothing else.
2. **Given** the round is of the sprint format, **When** phase 2 is posted, **Then** the graphic is
   drawn from the template configured at `images template weather-p2-sprint`, and from that template
   for no other reason than the format of the round.
3. **Given** the round is of the normal, endurance or mystery format, **When** phase 2 is posted,
   **Then** the graphic is drawn from the template configured at `images template weather-p2`.
4. **Given** the phase 1 message stands, **When** the phase 2 graphic is posted successfully, **Then**
   the phase 1 message is deleted — and not before the phase 2 message has been produced.
5. **Given** the phase 2 posting fell back to text because its render failed, **When** phase 3 is
   posted as a graphic, **Then** the phase 2 message is deleted exactly as it would have been had it
   carried a graphic, the manner of a message being no part of the chain.
6. **Given** test mode is active, **When** any phase is posted, **Then** deletions remain suppressed
   for the image flow exactly as they are for the textual flow.
7. **Given** a phase 2 or phase 3 graphic is drawn, **When** it is filled, **Then** it carries the
   likelihood of rain computed at phase 1, though neither the phase 2 nor the phase 3 textual message
   carries that value.
8. **Given** the render of one division's phase fails, **When** the other divisions are posted,
   **Then** each is unaffected, and a phase whose forecast fell back to text may be followed by a phase
   posted as a graphic.

---

### User Story 3 - The notice of a mystery round as a graphic (Priority: P3)

A round of the mystery format runs no phase and computes no forecast. At the phase 1 horizon the bot
still tells the division that the weather is not pre-generated. With the toggle enabled, that notice is
posted as a graphic drawn from its own template, holding the heading fields alone.

**Why this priority**: It is small, independent of the three phases, and completes the toggle's promise
— a league that enabled `weather` and then scheduled a mystery round would otherwise get a picture for
three of its four weather postings and text for the fourth.

**Independent Test**: Schedule a mystery round on a division with the toggle enabled, reach its phase 1
horizon, and confirm one graphic is posted carrying no track, no session and no forecast, on a message
carrying no role mention.

**Acceptance Scenarios**:

1. **Given** a round of the mystery format and the toggle enabled, **When** the phase 1 horizon is
   reached, **Then** a PNG drawn from the mystery template is posted to the division's forecast
   channel, on a message carrying **no** division role mention.
2. **Given** the same round, **When** the phase 2 and phase 3 horizons pass, **Then** nothing whatever
   is posted, on either pathway.
3. **Given** the mystery graphic is drawn, **When** it is filled, **Then** it carries the season number,
   division name, division tier and round number and nothing else — no track name, no grand prix name,
   no country, no session and no rain likelihood.
4. **Given** the mystery render fails, **When** the fallback runs, **Then** the textual notice is posted
   in its place, this being a posting no command triggered.

---

### User Story 4 - Learn a weather template cannot draw a format before a season depends on it (Priority: P4)

A league manager configures a phase 3 template that declares only two slots per session. They are told
at the moment they configure it — and again at season review — that it cannot draw the rounds the
season holds, rather than discovering it at a horizon two hours before a race.

**Why this priority**: It is what stops a league approving a season every forecast of which then falls
back to text. It is independent of the posting path and testable entirely through the configuration
commands.

**Independent Test**: Configure each of the six templates with a deliberately short declaration and
confirm each is rejected at configuration, named at season review, and refused at approval.

**Acceptance Scenarios**:

1. **Given** a phase 3 sprint template declaring fewer than three slots for any session, **When** it is
   named at `images template weather-p3-sprint`, **Then** the command is rejected, the configuration is
   left as it stood, and the reason names the template, the count it declares and the count required.
2. **Given** a phase 3 plain template declaring fewer than four slots for any session, **When** it is
   named, **Then** it is rejected on the same terms.
3. **Given** a phase 2 or phase 3 sprint template declaring fewer than four sessions, or a plain one
   declaring fewer than two, **When** it is named, **Then** it is rejected on the same terms.
4. **Given** a template declaring more members than its floor requires, **When** it is named, **Then**
   it is accepted, the surplus being removed silently at generation.
5. **Given** any of the six templates carries a fault, **When** `season review` is run, **Then** that
   template is named individually — which phase, and whether it is the sprint file, the plain file or
   the mystery notice — and the season's approval is refused while the fault stands.
6. **Given** a template declares a gap in its session numbering or in the slot numbering of any
   session, **When** it is checked at any of the three moments, **Then** it is refused.
7. **Given** a phase 2 template declares a field belonging to the phase 3 catalogue — the fields of a
   slot among them — **When** it is named, **Then** it is refused as the wrong file in that slot.

---

### User Story 5 - Degradations reported to staff, never drawn for drivers (Priority: P5)

A weather icon or track image has no file of its own and the class's fallback stands in for it. The
graphic is still posted; the substitution is reported to the league's log channel, naming the season,
division, round and phase, and never appears in the channel the drivers read.

**Why this priority**: It is a small addition on top of the reporting the module already does, and the
graphics are usable without it — but a league that cannot see which icon is missing cannot fix it.

**Independent Test**: Point the weather icon directory at a directory holding only `fallback.svg`,
generate any phase 2 graphic, and confirm the picture is posted, the fallback is drawn, and one notice
per substituted icon reaches the log channel and no forecast channel.

**Acceptance Scenarios**:

1. **Given** a weather icon or track image resolves to no file and its class holds a `fallback.svg`,
   **When** the graphic is generated, **Then** the fallback is drawn, the render succeeds, and a notice
   naming the field and the datum reaches the log channel.
2. **Given** the same class holds no `fallback.svg` either, **When** the graphic is generated, **Then**
   the render is abandoned and the phase falls back to text.
3. **Given** any notice is raised during a generation, **When** it is reported, **Then** it names the
   season, the division, the round and the phase, and appears in no division's forecast channel.
4. **Given** a generation was triggered by a command, **When** notices are raised, **Then** they are
   additionally reported alongside that command's output.

---

### Edge Cases

- A round amended from the normal format to the sprint format between phase 1 and phase 2: phase 2 is
  drawn from the sprint template, and phase 1's message is deleted by phase 2's posting as usual.
- A phase 3 plain template drawing a round of the normal format, whose qualifying allows two slots: the
  third and fourth slots of that session are removed silently, and no notice arises.
- A sprint round's sprint race, which allows exactly one slot: the second and third slots of that
  session are removed silently on a template declaring three.
- A division with no forecast channel configured, or one the bot cannot reach: nothing is posted and
  nothing is generated, whatever the toggle says.
- The `weather` toggle enabled while one or more of the six templates is invalid: the toggle reports
  "enabled but invalid", naming which of the six.
- An amendment invalidating the forecasts of a round: the invalidation notice remains message text
  regardless of the toggle, and the phases re-run behind it.
- The posting of a generated image failing for a reason of the Discord service: the **textual** forecast
  is what is enqueued for retry, never the image.
- A phase 1 graphic drawn for a round whose stored rain probability cannot be read: the render fails,
  that field being mandatory on the phase 1 template.
- A phase 2 or phase 3 graphic in the same case: the field is optional there, so it is emptied and the
  graphic is drawn.

## Requirements *(mandatory)*

### Functional Requirements

#### The six field catalogues

- **FR-001**: The system MUST declare six field catalogues — one for each of the `weather_p1_template`,
  `weather_p2_template`, `weather_p2_sprint_template`, `weather_p3_template`, `weather_p3_sprint_template`
  and `weather_mystery_template` slots — as code constants in the module's shared declaration, each
  separately addressable and each naming its own fields in full.
- **FR-002**: All six catalogues MUST be **siblings**: a template declaring a field belonging to any of
  the other five MUST be refused at the moment the template is named. The fields of a slot appearing on
  a phase 2 template are the named instance of this. An id belonging to no catalogue MUST be ignored.
- **FR-003**: Each of the four phase-bearing catalogues MUST declare the heading fields — `season_number`
  (optional), `division_name` (mandatory), `division_tier` (optional), `phase_description` (mandatory),
  `round_number` (mandatory), `track_name` (mandatory), `race_name` (optional), `country_name`
  (optional), `track_image` (optional, track image class) and `rain_probability`.
- **FR-004**: `rain_probability` MUST be **mandatory** on the phase 1 catalogue and **optional** on the
  phase 2 and phase 3 catalogues.
- **FR-005**: The phase 1 catalogue MUST declare no field beyond the heading fields.
- **FR-006**: The mystery catalogue MUST declare `season_number`, `division_name`, `division_tier` and
  `round_number` and **nothing else** — no phase description, no track name, no grand prix name, no
  country, no track image, no rain likelihood, no session and no slot.
- **FR-007**: The two phase 2 catalogues MUST declare the `session` collection discriminated by
  **ordinal**, numbered continuously from 1 in the order the sessions are run, holding
  `session_<x>_group` (mandatory), `session_<x>_name` (mandatory), `session_<x>_slot_type` (mandatory)
  and `session_<x>_slot_type_icon` (optional, weather icon class).
- **FR-008**: The two phase 3 catalogues MUST declare the same four fields per session with
  `session_<x>_slot_type` **optional**, plus `session_<x>_summary` (optional), plus the nested `slot`
  collection discriminated by **ordinal** holding `session_<x>_slot_<y>_group` (mandatory),
  `session_<x>_slot_<y>_label` (mandatory) and `session_<x>_slot_<y>_icon` (optional, weather icon
  class).
- **FR-009**: `session_<x>_slot_type` MUST be resolved as a field of session *x* and never as a member of
  the nested `slot` collection. The system MUST distinguish the two through the catalogue and MUST NOT
  derive the distinction by parsing the identifier.
- **FR-010**: The session ordinal MUST be declared a **place in the layout and not a datum**: the system
  MUST draw no session number, the ordinal serving only to address the fields of that session.
- **FR-011**: No weather catalogue MUST declare a Discord mention, a phase number, a date or time of the
  round, a driver name or a team name.

#### Template selection

- **FR-012**: Each of the phase 2 and phase 3 catalogues MUST name the **format of the round** as the
  datum selecting between its two slots. The sprint slot MUST be chosen for a round of the sprint format
  and the plain slot for a round of every other, and **nothing else** may enter that choice — not a
  count of the sessions actually present, not a configuration beyond the one naming the templates, and
  not a fall back to the other slot when the selected one is unconfigured or invalid.
- **FR-013**: The phase 1 graphic MUST be drawn from one template for every format, holding no session
  and needing no selection.

#### Declaration floors

- **FR-014**: Each of the four phase 2 and phase 3 catalogues MUST declare its capacity **fixed by the
  template slot**, stating the least a template filling that slot must declare:
  - phase 2 sprint and phase 3 sprint: **four** sessions;
  - phase 2 plain and phase 3 plain: **two** sessions;
  - phase 3 sprint: **three** slots for each session declared;
  - phase 3 plain: **four** slots for each session declared.
- **FR-015**: The floors of FR-014 MUST be read from the module's existing per-format session list and
  per-session-type slot capacities, and MUST NOT be restated as literals in the image module.
- **FR-016**: A template declaring **fewer** members than its floor MUST be a fatal error naming the
  template, the count declared and the count required. This check reads the template and a constant
  alone, so it MUST be applied at all three validity moments — the command naming the template, season
  review, and immediately before a render — and MUST refuse at each with that moment's severity.
- **FR-017**: A template declaring **more** members than its floor MUST NOT be a divergence. The surplus
  MUST be removed at generation by its group, silently, raising no notice.
- **FR-018**: A gap in the numbering of a template's sessions, or in the numbering of the slots of any
  session, MUST be a fatal error at every validity moment.
- **FR-019**: Season review MUST name each faulty weather template individually — which phase, and
  whether it is the sprint file, the plain file or the mystery notice — and the season's approval MUST
  be refused while any stands.

#### Data resolution

- **FR-020**: Every value the graphic draws that the textual forecast also draws MUST be produced by the
  same formatting code the textual path calls. The system MUST NOT hold a second implementation of any
  such rendering.
- **FR-021**: Where a shared rendering presently exists only as part of composing a whole textual message
  — the rain likelihood as a percentage, and the capitalised session weather type — it MUST be made
  separately callable so that both paths produce it from one place, rather than the graphic reproducing
  it.
- **FR-022**: `phase_description` MUST be the fixed text "Initial chance of rain" for phase 1, "Initial
  session forecast" for phase 2 and "Final session forecast" for phase 3.
- **FR-023**: `rain_probability` MUST be the likelihood computed at phase 1, rendered as a percentage
  **rounded to the nearest whole number** with the percent sign included. The phase 2 and phase 3
  graphics MUST carry that same stored value and MUST NOT recompute it.
- **FR-023a**: The **textual** phase 1 message MUST be corrected to that same rounding. It presently
  renders one decimal place. Both paths MUST then draw the corrected value from the one rendering of
  FR-021, so that the graphic and the message cannot disagree. *(Ruled by the author on 2026-08-13; the
  ruling stands on its own and not on any wip-spec.)*
- **FR-024**: `track_name` MUST be the name recorded for the round, and `race_name` and `country_name`
  MUST be read from the track object. `track_image` MUST be resolved from the track name by the module's
  normalisation rule in the configured track image directory.
- **FR-025**: `session_<x>_name` MUST be "Sprint Qualifying", "Sprint Race", "Feature Qualifying" or
  "Feature Race" for a round of the sprint format and "Qualifying" or "Race" for a round of any other,
  carrying no qualifier of the session's length.
- **FR-026**: `session_<x>_slot_type` MUST be one of "Sunny", "Mixed" or "Rain". The phase 3 graphic MUST
  carry the type phase 2 drew for that session, read as persisted.
- **FR-027**: `session_<x>_slot_<y>_label` MUST be one of "Clear", "Light Cloud", "Overcast", "Wet" or
  "Very Wet".
- **FR-028**: The icon of a weather type and the icon of a concrete weather MUST both be resolved from
  that same text by the module's normalisation rule in the configured weather icon directory, so that
  "Sunny" yields `sunny.svg` and "Very Wet" yields `very_wet.svg`.
- **FR-029**: `session_<x>_summary` MUST be the session's whole slot sequence rendered as the textual
  phase 3 message renders it, **without the channel emphasis that message applies**. The separation MUST
  be made in the shared renderer, which MUST be able to produce the value unadorned; the image utility
  MUST NOT strip markup out of a string handed to it.
- **FR-030**: Sessions MUST be placed in the order they are run and the slots of a session in the order
  they were drawn.
- **FR-031**: `division_tier` MUST be emptied where the division carries no tier, rather than drawn as a
  placeholder.
- **FR-032**: Where any value does not apply, its text field MUST be **emptied** and MUST NOT be filled
  with a dash or any other placeholder.
- **FR-033**: The graphic MUST NOT carry any intermediate value of a phase's calculation. The calculation
  log channel MUST remain textual in its entirety.

#### Shipped assets

- **FR-034**: The module MUST ship `sunny.svg`, `mixed.svg`, `rain.svg`, `clear.svg`, `light_cloud.svg`,
  `overcast.svg`, `wet.svg` and `very_wet.svg` in the packaged weather icon directory, beside the
  `fallback.svg` already there, so that a league draws every forecast without authoring an icon.
- **FR-035**: Each shipped icon MUST be plain SVG carrying no clip path, gradient or filter, authored at
  the aspect ratio of the slot it fills.

#### Mismatch handling at generation

- **FR-036**: Sessions the template declares **in excess** of the sessions the round holds MUST have
  their `session_<x>_group` removed in its entirety, taking every field of that session with it, and no
  error reported.
- **FR-037**: Sessions the round holds **in excess** of those the template declares MUST be a fatal
  error naming the sessions that would have been dropped.
- **FR-038**: Slots the template declares **in excess** of those drawn for a session MUST have their
  `session_<x>_slot_<y>_group` removed in its entirety, and no error reported.
- **FR-039**: Slots drawn for a session **in excess** of those the template declares for it MUST be a
  fatal error naming that session.
- **FR-040**: Removing a `session_<x>_group` on a phase 3 template MUST take the fields of that session's
  slots with it.
- **FR-041**: A mandatory field the template does not hold, and a mandatory field whose value cannot be
  determined at generation, MUST each be a fatal error naming what is at fault.

#### Generation and posting

- **FR-042**: With the `weather` toggle enabled, the forecast of a phase MUST be posted as a PNG attached
  to a message carrying the division role mention and **nothing besides**. The heading the textual
  forecast carries MUST appear neither on the message nor on the graphic, `phase_description` standing
  in its place.
- **FR-043**: The graphic MUST be generated anew on every occasion the textual forecast is currently
  posted: a phase run at its horizon, a phase re-run after an amendment invalidated the round's
  forecasts, a phase advanced by test mode, and a phase run at startup after its horizon passed while
  the bot was offline.
- **FR-044**: The chain of deletions MUST be unchanged: phase 2's posting deletes phase 1's message,
  phase 3's posting deletes phase 2's, and phase 3's message is deleted when the textual flow deletes it
  today.
- **FR-045**: A message MUST NOT be deleted until the message replacing it has been produced
  successfully, whether that replacement is the graphic or the text a fallback substituted.
- **FR-046**: The **manner** of a message MUST be no part of the chain: a message posted as text may be
  deleted by an occasion posted as a graphic and the reverse, each occasion reading which message stands
  and never how it was drawn.
- **FR-047**: The image flow MUST delete exactly as the textual flow deletes, in test mode and outside
  it alike, and MUST NOT hold any rule about deletion the textual flow does not.
  *(Corrected 2026-08-14: the wip-spec said deletions "remain suppressed while the test mode is active,
  as they are for the textual flow". They are not — suppression was removed from
  `delete_forecast_message` at an earlier increment. The parity was always the point, and it is what
  survives.)*
- **FR-048**: The graphic MUST replace the textual forecast in the division's configured forecast channel
  and there alone.
- **FR-049**: The failure of one phase MUST prevent neither the phases that follow it nor the same phase
  of any other division.
- **FR-050**: Where the source module would post nothing — no forecast channel configured, the channel
  unreachable — nothing MUST be generated and nothing posted, whatever the toggle says.
- **FR-051**: The generation and posting of a graphic MUST NOT prevent, delay or condition anything the
  weather module would have done without it. Every draw, every persisted result and every calculation
  log entry MUST complete exactly as it would with the module disabled.

#### The mystery notice

- **FR-052**: With the toggle enabled, the notice posted at the phase 1 horizon for a round of the
  mystery format MUST be drawn from the mystery template and posted on a message carrying **no** division
  role mention, its textual counterpart carrying none.
- **FR-053**: Nothing MUST be posted at the phase 2 and phase 3 horizons of a mystery round, on either
  pathway.
- **FR-054**: The notice posted when an amendment invalidates a round's forecasts MUST remain message
  text, the `weather` toggle notwithstanding.

#### Errors, notices and fallback

- **FR-055**: A fatal error met at any step of the generation or posting of a phase MUST cause that
  phase's forecast to be posted in the textual manner instead, where the posting was triggered by no
  command.
- **FR-056**: Where a **command** triggered the generation, a fatal error MUST reject that command,
  nothing MUST be posted in consequence, and the caller MUST be told what is at fault.
- **FR-057**: Where the posting of a generated image fails for a reason of the Discord service rather
  than of the generation, the **textual** forecast MUST be what is enqueued for retry. A generated image
  MUST NOT be enqueued.
- **FR-058**: The four `images test weather-*` commands MUST NOT fall back, having no textual counterpart.
  A fatal error met by one MUST be reported to the league manager who invoked it and no image posted.
- **FR-059**: Non-fatal errors gathered during a generation MUST be reported in the server's log channel,
  naming the season, the division, the round and the phase, and MUST NOT appear in any division's
  forecast channel. Where a command triggered the generation they MUST additionally be reported alongside
  its output.

#### Test data

- **FR-060**: Each of the four `images test weather-*` commands MUST generate an image for **each
  template it exercises**, drawn for a division named "Test Division", of tier 1 and season number 1, at
  round 1 of a track of the server's track list — six images in all across the four commands.
- **FR-061**: `images test weather-p2` and `images test weather-p3` MUST each generate two images: one
  for a round of the sprint format drawn from that phase's sprint template and holding four sessions, and
  one for a round of the endurance format drawn from its plain template and holding two, whose race is
  the only session the module may draw four slots for.
- **FR-062**: `images test weather-p1` MUST fabricate a rain likelihood that is **not** a whole
  percentage, so its rendering can be judged.
- **FR-063**: `images test weather-p2` MUST fabricate a weather type for every session of both rounds
  such that each of the three types appears at least once.
- **FR-064**: `images test weather-p3` MUST fabricate slots including, so far as the sessions and slots
  the templates declare allow: a session of a single slot; a session all of whose slots carry one
  weather; a session whose slots differ; a session holding the greatest number of slots its type allows;
  and each of the five concrete weathers at least once.
- **FR-065**: `images test weather-mystery` MUST generate the notice of a mystery round, holding no
  session and carrying no forecast.
- **FR-066**: Where a template declares fewer sessions than the round fabricated for it holds, or fewer
  slots than a fabricated session holds, the fatal error of FR-016 MUST be met and reported, naming which
  of the phase's two templates was at fault.

### Key Entities

This feature introduces **no** new entity and amends none.

- **`forecast_messages`** already keys a posted message by round, division and phase, and already admits
  phase `0` for the mystery notice. Each phase's message is separately addressable and separately
  replaceable, which is all the chain of FR-044 needs.
- **`Session`** already carries the phase 2 weather type and the phase 3 slot sequence drawn for it —
  the values the phase 2 and phase 3 graphics place.
- **The per-format session list and per-session-type slot capacities** are the constants FR-014's floors
  are computed from, and are read where they already live.
- **The six template slots, the `weather` aspect and its toggle, the weather icon directory and the four
  `images test weather-*` values** are all part of the configuration surface delivered at 035 and 036,
  and are read as they stand.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A league manager can see all six weather graphics without a season, a division or a round
  existing, in four commands and under a minute.
- **SC-002**: A league manager configuring a template too small for the rounds their season holds is told
  so at the moment they configure it — not at a phase horizon — in 100% of cases.
- **SC-003**: Across a full round of a division, every one of the three forecasts is posted as a picture,
  each superseding its predecessor, leaving exactly one weather message standing at any moment.
- **SC-004**: A failed render costs at most one picture: the phase falls back to text, and the phases
  after it and the same phase of every other division post as graphics unaffected, in 100% of cases.
- **SC-005**: No problem and no notice raised by weather image generation ever appears in a channel
  drivers read.
- **SC-006**: A league that has supplied no weather artwork of its own still draws every forecast with a
  correct icon on every session and every slot, no fallback being needed for any value the module itself
  defines.
- **SC-007**: Every value appearing on both a weather graphic and its textual counterpart reads
  identically, and a change to how the text path renders such a value appears on the graphic with no
  further work.
- **SC-008**: Enabling or disabling the `weather` toggle changes what the forecast channel receives and
  changes nothing about which draws are made, which results are persisted, or what the calculation log
  records.

## Assumptions

- **The rain likelihood is rounded to the nearest whole percentage on both paths.** Ruled by the author
  in conversation on 2026-08-13, when the graphic's rounding was put to them: the textual phase 1 message
  is to round to the nearest integer as well, so the **text path is corrected** (FR-023a). The ruling
  rests on the author's decision alone — `weather_module_specification.md` happens to agree, but it is
  stale and was not the basis. This is the one place this increment changes what the textual forecast
  says. Nothing pins the present format: no test asserts on the phase 1 message's rendering.
- **The shared renderings are extracted rather than duplicated.** FR-021 and FR-029 assume the right
  repair for a rendering presently embedded in a message builder is to lift it out, both paths then
  calling it. The alternative — the image utility reproducing or post-processing the string — is what
  Rule 7 and Rule 16 of the constitution forbid.
- **The mystery notice keeps its present recording.** It is posted at the phase 1 horizon and recorded
  distinctly from a phase 1 forecast, no forecast having been computed. The graphic changes what that
  posting looks like and nothing about when it happens or how it is tracked.
- **The eight shipped icons are placeholders in the same sense as the fifteen shipped templates.** They
  are drawn to be correct and replaceable, not to be a league's final artwork, and `resources/` continues
  to hold no league-specific artwork.
- **A phase 3 plain template serves both the normal and the endurance formats**, so its four-slot floor
  is reached only by an endurance race; on a normal round the last cells of every session are removed.
  This is the ordinary silent-removal behaviour and not a degradation.
- **The `track_image` class is the existing track image directory**, shared with the calendar and
  attendance types, and a mystery round never reaches a phase graphic so never resolves one here.

## Out of Scope

- Every other image type. The calendar, lineup, results, standings, attendance, check-in and verdicts
  types are untouched.
- The weather pipeline itself: the horizons, the draws, the formulas, the slot counts, the persistence
  of results and the calculation log are Principle IV's and are read, never changed.
- The text path's own content, with **one exception**: the phase 1 rain-likelihood rounding of FR-023a,
  which is corrected to the rule the weather wip-spec already states. Beyond that the textual forecast is
  refactored only so far as FR-021 and FR-029 require a shared rendering to be callable, and what it
  *says* is unchanged.
- New configuration commands. All six template slots, the toggle, the weather icon directory and the four
  test values already exist.
- Any change to the invalidation notice, which stays textual.
