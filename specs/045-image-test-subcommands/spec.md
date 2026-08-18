# Feature Specification: Image test commands drawn from the league's own configuration

**Feature Branch**: `hotfix/image-module-poc`
**Created**: 2026-08-18
**Status**: Draft
**Input**: User description: amend `/images test` so that it pretests a league's configuration against that league's own settings, subdividing it into one command per image kind, each taking the division (and, where the kind pertains to a round, the round number) it is to be drawn for.

## Why this change

`/images test <kind>` draws every kind from invented data: a division named "Test Division", invented teams, invented tracks, invented rounds. It answers "does this template file render at all", which is a smaller question than the one a league manager is asking. What they need to know before switching an aspect on is whether **their** template, filled with **their** divisions, **their** teams and **their** calendar, produces a picture they are happy to post. A preview of a fictitious league cannot answer that: a lineup template names its fields after real teams, a calendar crops at a real round count, and a standings grid widens with a real calendar length. The present command therefore passes on configurations that will fail in production, and fails on configurations that will succeed.

This feature replaces the single command with one command per image kind, each drawn against real league data, fabricating only what a league cannot have configured in advance — session results, forecasts, attendance records and stewards' verdicts.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preview a template against a real division (Priority: P1)

A league manager has drawn a calendar template and seated their teams. Before switching the calendar aspect on, they run the calendar preview naming one of their divisions, and receive the picture that division's calendar post would produce today — their rounds, their tracks, their dates, cropped where their round count crops it.

**Why this priority**: This is the whole point of the change. Every other story is a variation of it over a different image kind. The calendar and the lineup are the two kinds that need no fabricated outcome data at all, so they are the smallest complete slice that delivers the value.

**Independent Test**: Configure a season with one division holding rounds and teams, invoke the calendar and lineup previews for that division, and confirm the returned pictures carry that division's own rounds and team names rather than invented ones.

**Acceptance Scenarios**:

1. **Given** a division holding three configured rounds, **When** the manager previews the calendar for it, **Then** a picture is returned drawn from those three rounds, their tracks and their dates.
2. **Given** a division holding no configured round, **When** the manager previews the calendar for it, **Then** the command is refused with a message naming the missing rounds, and no picture is produced.
3. **Given** a name matching no division, **When** the manager previews any kind for it, **Then** the command is refused with a message saying no such division exists, and no picture is produced.
4. **Given** a division whose teams hold seated drivers, **When** the manager previews the lineup for it, **Then** the picture carries those drivers' names in their real seats.
5. **Given** a division whose teams are configured but hold no seated driver, **When** the manager previews the lineup for it, **Then** the picture is drawn with fabricated driver names in the empty seats rather than being refused.

---

### User Story 2 - Preview the kinds whose data a league cannot configure in advance (Priority: P2)

A league manager wants to see what a results post, a standings post, an attendance sheet or a verdict will look like for a round that has not been run. They name the division and the round, and the bot fabricates believable outcomes over their real drivers and teams, returning the pictures those posts would produce.

**Why this priority**: These are the kinds a manager most needs to see before the season starts, because they cannot be seen at all until a round is run. They depend on the division and round resolution the first story establishes.

**Independent Test**: Configure a division with teams, seated drivers and a round, preview the results, standings, attendance and verdict kinds for it, and confirm every picture carries the division's real drivers and teams with fabricated performance data.

**Acceptance Scenarios**:

1. **Given** a division with seated drivers and round 3 configured, **When** the manager previews the results for round 3, **Then** one picture is returned per session of that round's format, each drawn over those drivers.
2. **Given** the same division, **When** the manager previews the standings for round 3, **Then** both the driver standings and the constructor standings are returned.
3. **Given** a division holding no team, **When** the manager previews the results, the standings or the attendance sheet, **Then** the command is refused with a message naming the missing teams.
4. **Given** a division with teams but no seated driver, **When** the manager previews the results, **Then** the picture is drawn over fabricated drivers occupying those teams.
5. **Given** a division that has no round of the number given, **When** the manager previews any round-scoped kind, **Then** the command is refused with a message saying that division has no such round.

---

### User Story 3 - Preview the weather graphics against a real round (Priority: P3)

A league manager wants to see each of the three forecast phases, and the mystery notice, as they would be drawn for one of their rounds. They name the division and round, and the bot fabricates a forecast covering the full range of what that round's format can carry.

**Why this priority**: The weather graphics are the kinds most sensitive to the round's format — the session count and the slot count both follow from it — so previewing them against a real round is worth more than previewing them against an invented one. They are last because the weather aspect already posts and is therefore the least dark of the three groups.

**Independent Test**: Configure a division holding one round of each format, preview each weather phase for each, and confirm the fabricated forecast covers the icons and slots the format admits.

**Acceptance Scenarios**:

1. **Given** a round of the sprint format, **When** the manager previews phase 2 for it, **Then** the picture carries all three session weather types across that round's sessions.
2. **Given** a round of the normal or endurance format, **When** the manager previews phase 2 for it, **Then** the picture carries two session weather types.
3. **Given** any non-mystery round, **When** the manager previews phase 3 for it, **Then** the fabricated slots cover all five slot types.
4. **Given** a mystery round, **When** the manager previews any of phases 1, 2 or 3 for it, **Then** the command is refused with a message saying a mystery round carries no forecast.
5. **Given** a non-mystery round, **When** the manager previews the mystery notice for it, **Then** the command is refused with a message saying the round is not a mystery round.

---

### Edge Cases

- A division name matching more than one division across seasons — resolution is scoped to one season, so the ambiguity cannot arise within scope.
- A round of the mystery format previewed for the calendar or the lineup — neither kind is refused by format, and the mystery round is drawn as the calendar draws it.
- A round whose track has no artwork in the league's configured folder — the picture is still produced, the packaged placeholder stands in, and the reply names the file it stood in for.
- A league that has configured no asset folder at all — every preview draws placeholders throughout and the reply says the class is unconfigured, rather than reporting a file as missing.
- A division holding only the reserve team — treated as holding no team, and refused where teams are required.
- A round of the endurance format previewed for the results — the sessions drawn are the two that format runs, not the four a sprint runs.
- The rasteriser being absent — refused before any resolution is attempted, as today.
- A division holding fewer rounds than the standings template declares columns for — the grid is drawn to the calendar's own length, not padded to the template's.
- A round number given that is not a positive whole number — refused as no such round.

## Requirements *(mandatory)*

### Functional Requirements

#### Command shape

- **FR-001**: The single test command taking a kind as a parameter MUST be withdrawn and replaced by one command per image kind, each named for that kind.
- **FR-002**: The commands MUST be `calendar`, `lineup`, `results`, `standings`, `attendance`, `rsvp`, `weather-p1`, `weather-p2`, `weather-p3`, `weather-mystery` and `verdict`.
- **FR-003**: `calendar` and `lineup` MUST each take one mandatory input, the division name.
- **FR-004**: Every other command MUST take two mandatory inputs, the division name and the round number.
- **FR-005**: Each command MUST remain restricted to the same users the withdrawn command was, and MUST return its pictures to the invoker alone, posting nothing to any division's channel.
- **FR-006**: Each command MUST report, alongside the pictures it returns, every non-fatal notice the render raised, as the withdrawn command did.
- **FR-007**: A fatal error MUST be reported to the invoker with no picture returned; no command in this group has a textual counterpart to fall back to.

#### Resolution and refusal

- **FR-008**: Every command MUST resolve the division name against the divisions of the server's active season, and MUST be refused where no division of that name exists.
- **FR-009**: Every round-scoped command MUST be refused where the named division holds no round of the number given.
- **FR-010**: `calendar` MUST be refused where the named division holds no configured round.
- **FR-011**: `lineup`, `results`, `standings` and `attendance` MUST be refused where the named division holds no team beyond the reserve team.
- **FR-012**: `weather-p1`, `weather-p2` and `weather-p3` MUST be refused where the round named is of the mystery format.
- **FR-013**: `weather-mystery` MUST be refused where the round named is not of the mystery format.
- **FR-014**: Every refusal MUST name which condition was not met, distinguishing a missing division from a missing round, from a missing team list, and from a wrong round format.
- **FR-015**: Refusals MUST be evaluated before any render is attempted, so that a configuration fault is never reported as a render failure.

#### Data drawn

- **FR-016**: `calendar` MUST draw the named division's own configured rounds, in their configured order, with their configured tracks, formats, dates and times.
- **FR-017**: `lineup` MUST draw the named division's own teams and their seated drivers.
- **FR-018**: Where a kind draws drivers and the named division holds no seated driver at all, the bot MUST fabricate a driver for every seat rather than refuse, so that a league that has configured teams but not yet seated them can still judge the picture.
- **FR-019**: Where the league collects driver nationality, a fabricated driver MUST be given a fabricated nationality drawn from those the signup wizard accepts; where the league does not collect it, a fabricated driver MUST be given none.
- **FR-020**: Where the named division holds at least one seated driver, its seats MUST be drawn as they stand, an unoccupied seat being drawn unoccupied as a posting would draw it. A seated driver MUST be drawn with that driver's own name and, where collected, that driver's own nationality; fabrication reaches only a division that has seated nobody.
- **FR-021**: `results`, `standings`, `attendance` and `verdict` MUST draw the named division's own teams and, where seated, its own drivers, fabricating only the outcome data those kinds carry.
- **FR-022**: `rsvp`, `weather-p1`, `weather-p2`, `weather-p3` and `weather-mystery` MUST draw the named division and round's own identity — division name, tier, season number, round number, track and schedule — and fabricate only the forecast.

#### Fabricated outcomes

- **FR-023**: `results` MUST fabricate a believable classification for every session the named round's format runs, and return one picture per session.
- **FR-024**: A fabricated classification MUST place every drawn driver exactly once and MUST carry times, gaps and positions consistent with one another, so that a manager judges the drawing and not an obvious nonsense.
- **FR-025**: `standings` MUST fabricate the round-by-round results the standings are computed from, and MUST return both the driver standings and the constructor standings for the round named.
- **FR-026**: The standings drawn MUST be those standing after the round named, so that a manager can see the grid at the width their calendar gives it.
- **FR-027**: `attendance` MUST fabricate an attendance record for every drawn driver over the rounds up to and including the one named, covering the range of states a sheet can carry.
- **FR-028**: The attendance sheet MUST draw a driver's flag where the league collects nationality and MUST draw none where it does not, as the posted sheet does.
- **FR-029**: `weather-p1` MUST fabricate a likelihood of rain between 0 and 100 per cent.
- **FR-030**: `weather-p2` MUST fabricate a session weather type for every session of the named round; where the round is of the sprint format all three types MUST appear among them, and otherwise two MUST appear.
- **FR-031**: `weather-p3` MUST fabricate weather slots for every session of the named round, and all five slot types MUST appear among them.
- **FR-032**: `verdict` MUST fabricate a sanction and free text, the text being long enough to exercise the wrapping of the field it is drawn in.
- **FR-033**: The driver a fabricated verdict pertains to MUST be one of the named division's seated drivers where there is one, and a fabricated driver otherwise.
- **FR-034**: The sanction a fabricated verdict carries MUST be drawn from: five seconds added to race time; ten seconds added to race time; three seconds removed from race time; disqualification. These are the sanctions the bot can record and issue, and the preview MUST NOT draw one it cannot.

#### Artwork

- **FR-035**: Every preview MUST resolve its artwork — team badges, driver flags and track imagery — from the league's own configured asset directories, as the posting path does, so that the preview follows the live path and not a path of its own.
- **FR-036**: Where an asset the league configured cannot be found, the preview MUST fall back to the packaged placeholder for that asset class rather than fail, as the posting path does.
- **FR-037**: The reply MUST list every fallback the render used, naming the asset it stood in for and why it was used, so that a manager reading the reply can tell a deliberate placeholder from a missing file.
- **FR-038**: An asset class the league has not configured at all MUST be reported as such, distinctly from a class that is configured but missing the particular file.

## Key Entities

- **Division**: The league's own division, resolved by name within the active season. Carries the tier, the season number, the teams, the calendar and the channels a real post would use.
- **Round**: A round of that division's calendar, resolved by number. Carries the format, from which the session list and the slot counts follow, the track and the schedule.
- **Team**: A team of the division, carrying its name, its badge and its seats. The reserve team is not counted when deciding whether a division holds teams.
- **Seat**: A place in a team, either holding a driver the league has seated or empty and filled with a fabricated driver for the preview.
- **Fabricated outcome**: Session classifications, standings, attendance records, forecasts and verdicts invented for the preview. Never written to the league's records and never posted.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A league manager can see, for any one of their divisions, the picture each of the eleven kinds would produce for that division, without running a round and without creating a test season.
- **SC-002**: Every picture a preview returns carries the league's own division name, tier, season number and, where the kind draws them, the league's own team names and seated driver names.
- **SC-003**: A preview refused for a configuration reason names that reason, and a manager can tell from the message alone which of the four conditions was not met.
- **SC-004**: A lineup template naming teams the league does not have is caught by the lineup preview, because the preview is drawn against the league's real team list.
- **SC-005**: A calendar template that crops correctly at the fabricated round count but not at the league's own is caught by the calendar preview.
- **SC-006**: No preview writes to, alters or posts any of the league's records; running every preview leaves the league's data exactly as it was.
- **SC-007**: A forecast preview covers, for the round's format, every session weather type and every weather slot type that format admits, so a manager judges every icon in one picture per phase.
- **SC-008**: A league that has configured its own badges, flags and track imagery sees them in every preview that draws them, and a league that has configured none sees placeholders and is told which.
- **SC-009**: A manager reading a preview's reply can name every asset the picture could not find, without opening the log channel or the picture itself.

## Assumptions

- **A-001**: "Division name" resolves within the **active season** of the server. A division of an archived season is not previewable; the preview is a check on what the league is about to run.
- **A-002**: The command group keeps the name `test` under `/images`, giving `/images test calendar` and so on. Eleven subcommands sit within the platform's ceiling for one group.
- **A-003**: The verdict command is named `verdict`, in the singular, as the user's description names it, notwithstanding that the aspect it previews is named `verdicts`.
- **A-004**: "The teams list" for a division means that division's own team instances, not the server's default team list, since it is the division's teams a lineup, a results grid and an attendance sheet are drawn from.
- **A-005**: A round number is a whole number as the calendar records it; a value matching no round is refused by FR-009 without a separate rule.
- **A-006**: The results preview returns one picture per session of the round's format, drawn from the qualifying template for qualifying sessions and the race template for races, as the posted results do.
- **A-007**: Fabricated data is generated afresh on each invocation and is not reproducible between invocations. A manager judging a drawing does not need the same numbers twice.
- **A-008**: The attendance sheet does carry a driver nationality element — a flag field per driver, drawn where the league collects nationality and removed where it does not — which answers the question raised in the feature description.
- **A-009**: The preview continues to refuse outright when the rasteriser is unavailable, before any resolution or fabrication, as the withdrawn command did.
- **A-010**: Where a kind needs a track and the round's track is concealed, as a mystery round conceals it, the picture is drawn as the posted one would be rather than refused.
- **A-011**: The rule that the preview always drew packaged placeholders is withdrawn by FR-035. The standing warning to that effect in `docs/how-to/configuring-the-image-module.md` and the corresponding lines in `README.md` are wrong from that moment and are corrected as part of this change.
- **A-012**: The fallback report of FR-037 is an extension of the notice report the preview already returns, not a second channel of its own; a manager reads one reply, not two.
- **A-013**: The three sanctions named in the feature description that the bot cannot record — no further action, a qualifying ban and a race ban — are out of scope. Widening the recorded penalty vocabulary is a steward-module change and is not undertaken here.

## Documentation impact

The rules of this feature are recorded in `docs/wip-specs/image_module_specification.md`, which is the source: the command family at the Configuration section, the family's common rules in a new "The test commands" section under Conventions, and the per-type fabrication rules in each type's "Test data" section.

`README.md` and the how-to guides describe the bot as it stands today, and are therefore **not** changed until this feature ships. Sixteen passages across three files become wrong on the day it does, and are corrected with it:

- `README.md` — the `/images test` command reference, the Inkscape prerequisite note, the lineup authoring note, and the "checking your work" note.
- `docs/how-to/configuring-the-image-module.md` — the walkthrough steps invoking the command, the checklist, the troubleshooting table, and above all the standing warning that `/images test` never shows a league's own artwork, which FR-035 withdraws.
- `docs/how-to/configuring-the-results-module.md` — the note pointing at `/images test standings`.

`.specify/memory/constitution.md` needs a **PATCH** amendment when this ships, raised through `/speckit-constitution` and never by hand. No Core Principle is removed or redefined, but two statements in the versioned entity inventory describe the test command as a parameter carrying choice values, and this feature makes those values subcommands:

- "New Entities (v4.8.0)" names "the `images test verdicts` value" — which becomes the `verdict` subcommand.
- "New Entities (v4.7.0)" names "the four `images test weather-*` values" — which become four subcommands.

The amendment belongs to the implementing increment rather than to this one, on the precedent the constitution sets for `README.md`: a document that describes the bot as it is, is corrected when the behaviour it describes exists.

**Constitution Check note for the plan.** FR-035 and FR-036 bring the test path into compliance with Rule XIV.13 rather than away from it. That rule admits three outcomes for an asset — found, fallback drawn with a non-fatal error, or fatal where the directory holds no fallback — and the withdrawn command sidestepped all three by substituting the packaged directories for the league's own. Drawing the league's configured directories is Rule XIV.13 applied, not an exception to it.

## Out of Scope

- Wiring the standings aspect to a posting path. The standings remain previewable and unposted; this feature changes how they are previewed, not whether they post.
- Any change to how the eight aspects are toggled, validated or configured.
- Any change to the templates themselves, their field catalogues or their crop rules.
- Previewing a division of an archived season.
- Persisting or re-serving a preview; each invocation renders afresh.
- Widening the recorded penalty vocabulary to carry no-further-action, a qualifying ban or a race ban. The preview draws only sanctions the bot can issue (FR-034), and extending what it can issue is a steward-module change.
