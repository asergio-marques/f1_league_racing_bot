# Feature Specification: Image previews across every season state

**Feature Branch**: `main`
**Created**: 2026-08-18
**Status**: Draft
**Input**: User description: the `/images test` subcommands do not work if the current season is still pending approval, or if there is no current season at all. An unapproved season's teams and driver lineups shall be used as an approved season's are; with no season at all, the season number shall be one higher than the previous season's, teams shall come from the general server configuration, and every other parameter shall be randomised; and all of it shall work under test mode, naming test-mode mock drivers by their mock name.

## Why this change

The eleven previews delivered by feature 045 resolve the server's **ACTIVE** season and refuse where there is none. That fixes the preview to the one moment in a league's life when it is least needed. A manager configures templates and artwork *before* approving a season, and a maintainer checking a template on a bare server has no season at all. In both cases every one of the eleven commands refuses, and the aspect cannot be judged until the very configuration the preview exists to check has already been committed.

This feature widens the previews to three season states — approved, pending approval, and absent — without weakening the refusals that report a genuine configuration fault.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preview a season that is still pending approval (Priority: P1)

A league manager has built a season with `/season setup`, seated their teams and drawn their calendar, but has not yet run `/season approve`. They preview the calendar and the lineup for one of that season's divisions and receive the pictures those posts would produce, drawn from the season they are about to approve.

**Why this priority**: This is the moment a preview is worth most — the configuration is complete and nothing has been committed. It is also the smallest change, because a season pending approval already holds its divisions, rounds, teams and seats in exactly the shape an approved one does.

**Independent Test**: Build a season to SETUP status with one division holding rounds and seated teams, invoke every preview for that division, and confirm each returns the picture it returns for an equivalent approved season.

**Acceptance Scenarios**:

1. **Given** a server whose only season is pending approval, **When** the manager previews the calendar for one of its divisions, **Then** a picture is returned drawn from that division's configured rounds.
2. **Given** the same server, **When** the manager previews any of the nine round-scoped kinds for a configured round, **Then** the picture is returned drawn from that round.
3. **Given** the same server, **When** the manager begins typing a division name, **Then** the divisions of the season pending approval are offered for completion.
4. **Given** a season pending approval whose division holds no configured round, **When** the manager previews the calendar for it, **Then** the command is refused for the missing rounds, exactly as an approved season's empty division is refused.
5. **Given** a server holding both an approved season and a later one pending approval, **When** the manager previews any kind, **Then** the approved season is the one drawn, and the reply names the season number drawn.

---

### User Story 2 - Preview on a server with no season at all (Priority: P2)

A league manager or maintainer has configured their teams, their templates and their artwork folders but has created no season. They invoke any of the eleven previews with no arguments and receive the picture that kind would produce, drawn over a fabricated league that carries their own team names and their own artwork.

**Why this priority**: This is the state a league is in when it first configures the image module, and the state a maintainer is in on a fresh server. It depends on nothing in the first story, but it is the larger change, because a whole league has to be invented rather than read.

**Independent Test**: On a server holding configured teams and no season, invoke each of the eleven previews with no arguments and confirm each returns a picture whose team names are the server's own and whose division, calendar, formats, round number and drivers are invented.

**Acceptance Scenarios**:

1. **Given** a server with configured teams and no season of any status, **When** the manager previews any kind, **Then** a picture is returned and no refusal is given for the missing season.
2. **Given** the same server, **When** the manager previews any kind, **Then** the reply states plainly that no season exists and that the league drawn is fabricated.
3. **Given** the same server whose most recent committed season was numbered 4, **When** the manager previews any kind, **Then** the picture carries season 5.
4. **Given** a server that has never held a season, **When** the manager previews any kind, **Then** the picture carries season 1.
5. **Given** the same server, **When** the manager previews the same kind twice, **Then** the two pictures differ in their division, calendar, round and driver names, the team names being the same on both.
6. **Given** the same server, **When** the manager supplies a division name or a round number, **Then** the value is disregarded and the fabricated league is drawn as though nothing had been supplied.
7. **Given** a server whose general configuration holds no team beyond the Reserve team, **When** the manager previews any kind, **Then** the command is refused for the missing teams.
8. **Given** the same server with no season, **When** the manager previews the mystery notice, **Then** the fabricated round is a mystery round and the notice is drawn rather than refused.
9. **Given** the same server with no season, **When** the manager previews any of the three forecast phases, **Then** the fabricated round is not a mystery round and the forecast is drawn rather than refused.

---

### User Story 3 - Preview under test mode (Priority: P3)

A maintainer running test mode previews an image and sees their mock drivers drawn by the mock names `/test-mode roster add` gave them, whether the test season is approved, pending approval, or absent altogether.

**Why this priority**: Test mode is how a maintainer reaches every kind without a real league, so the previews must hold under it. It is last because it is largely a consequence of the first two stories rather than a mechanism of its own.

**Independent Test**: Seat a division with mock drivers under test mode in a season pending approval, preview every kind that draws drivers, and confirm each picture carries the mock names.

**Acceptance Scenarios**:

1. **Given** a division seated with mock drivers, **When** the manager previews the lineup, **Then** each seat carries that mock driver's mock name.
2. **Given** the same division, **When** the manager previews the results, the standings, the attendance sheet or a verdict, **Then** every driver drawn carries a mock name and none is invented.
3. **Given** a division seated with mock drivers in a season pending approval, **When** the manager previews any kind, **Then** it is drawn exactly as it is for an approved season.
4. **Given** the league collects driver nationality and a mock driver has none recorded, **When** the manager previews a kind that draws flags, **Then** that driver is drawn without a flag and the reply names how many drivers carried no nationality.
5. **Given** test mode is on and the server holds no season, **When** the manager previews any kind, **Then** the fabricated league is drawn as it is with test mode off.

---

### Edge Cases

- A server holding an approved season and a season pending approval — the approved one is drawn, and the season pending approval is not previewable while it stands.
- A server whose only season is COMPLETED or CANCELLED — treated as no season at all, and the fabricated league is drawn at one number higher than the highest number that server has committed.
- A season pending approval that has just been re-snapshotted, wiping its team instances — its divisions hold no team, and the team-requiring kinds are refused for that, as they are for an approved season.
- A server with no season whose configured teams number one — the fabricated league is drawn over that one team, and the grids are as short as that makes them.
- A server with no season whose configured teams carry one seat each — every fabricated grid holds one driver per team.
- A fabricated calendar drawn for the calendar preview — it carries rounds of more than one format, so the format markers are all judged in one picture.
- A round number supplied to a round-scoped preview on a server with no season — disregarded without a refusal, the fabricated round standing in its place.
- A division name supplied on a server with no season — disregarded without a refusal.
- A division name or round number omitted on a server that *does* hold a season — refused as a missing input, since a real season is resolved against and not invented.
- The division autocomplete on a season-less server — offers nothing, and the parameter being optional the command runs regardless.
- A mock driver whose mock name is longer than the field it is drawn in — drawn as the posting path draws it, the template's own bound applying.
- The rasteriser being absent — refused before any season is resolved, as it is today.

## Requirements *(mandatory)*

### Functional Requirements

#### Which season a preview draws

- **FR-001**: A preview MUST draw the server's approved season where there is one, and the server's season pending approval where there is no approved one.
- **FR-002**: A season pending approval MUST be drawn exactly as an approved season is — its divisions, its rounds, its teams, its seats and its seated drivers, with no substitution and no additional fabrication.
- **FR-003**: The division autocomplete MUST offer the divisions of whichever season FR-001 selects.
- **FR-004**: The reply MUST name the season number drawn and MUST state where that season is pending approval.
- **FR-005**: A season that is COMPLETED or CANCELLED MUST NOT be drawn, and a server holding only such seasons MUST be treated as holding no season.

#### Refusals where a season exists

- **FR-006**: Where a season exists under FR-001, every refusal feature 045 defines MUST stand unchanged — an unknown division, an absent round, a division holding no configured round, a division holding no team beyond the Reserve team, a forecast asked of a mystery round, and a mystery notice asked of a round that is not one.
- **FR-007**: Where a season exists under FR-001, a preview MUST NOT fabricate a division, a round, a calendar, a format or a team list to stand in for missing configuration.
- **FR-008**: Where a season exists under FR-001, a preview invoked without a division name, or a round-scoped preview invoked without a round number, MUST be refused for the missing input.

#### The fabricated league

- **FR-009**: Where the server holds no season under FR-001, a preview MUST draw a fabricated league rather than refuse.
- **FR-010**: The fabricated league's season number MUST be one higher than the highest season number the server has already committed, and MUST be 1 where the server has committed none.
- **FR-011**: The fabricated league's teams MUST be the teams of the server's general configuration, excluding the Reserve team, with each team carrying the seat count that configuration gives it.
- **FR-012**: Every preview MUST be refused where the server holds no season and its general configuration holds no team beyond the Reserve team, the refusal naming the missing teams and distinguishing itself from the refusal FR-006 gives for a division holding none.
- **FR-013**: The fabricated league's division name, division tier, calendar, round formats, round number, round tracks, round schedule and driver names MUST all be randomised.
- **FR-014**: The fabricated league MUST be randomised afresh on each invocation, so that two invocations of one kind differ in everything FR-013 randomises.
- **FR-015**: The fabricated calendar MUST hold rounds of more than one format where it holds more than one round.
- **FR-016**: The round a round-scoped preview draws MUST be one of the fabricated calendar's own rounds, and its number MUST be the number that round carries in that calendar.
- **FR-017**: The round drawn for the mystery notice MUST be of the mystery format, and the round drawn for each of the three forecast phases MUST NOT be, so that no preview of a fabricated league is refused for the round's format.
- **FR-018**: The fabricated round's track MUST be one the bot's own track data carries, so that the track imagery resolves as it resolves for a real round.
- **FR-019**: Every seat of every fabricated team MUST be filled with a fabricated driver.
- **FR-020**: A fabricated driver MUST be given a fabricated nationality where the league collects nationality, and none where it does not.
- **FR-021**: The division name and round number parameters MUST become optional on every command that carries them.
- **FR-022**: Where the server holds no season, a supplied division name and a supplied round number MUST be disregarded, and no refusal MUST be given for supplying them or for omitting them.
- **FR-023**: A fabricated league MUST resolve its artwork in the league's own configured asset directories, and MUST report its fallbacks, exactly as a preview of a real season does.
- **FR-024**: The reply MUST state that no season exists and that the league drawn is fabricated, and MUST distinguish what was taken from the server's configuration from what was invented.
- **FR-025**: A fabricated league MUST NOT be written to the server's records; no season, division, round, team, seat or driver of it MUST survive the invocation.

#### Test mode

- **FR-026**: A test-mode mock driver MUST be drawn by the mock name it was created with, in every kind that draws drivers.
- **FR-027**: A mock driver MUST NOT be treated as an unoccupied seat, and a division seated entirely with mock drivers MUST NOT have drivers fabricated over it.
- **FR-028**: Where the league collects nationality and a seated driver has none recorded, the driver MUST be drawn without a flag, as the posting path draws them, and the reply MUST name how many drivers were drawn without one.
- **FR-029**: Every preview MUST behave identically whether test mode is on or off; test mode MUST change what data exists, never how a preview reads it.

## Key Entities

- **Season under preview**: The season a preview draws — the approved one where there is one, the one pending approval otherwise, and none at all where neither exists.
- **Fabricated league**: A complete league invented for one invocation where no season exists. Carries a season number derived from the server's history, teams read from the server's general configuration, and a randomised division, calendar and roster. Never persisted.
- **General team configuration**: The server-level team list, held independently of any season, from which a season's divisions are seeded. The source of the fabricated league's teams, and the one part of that league that is not invented.
- **Mock driver**: A driver created by test mode rather than by signup. Carries a mock display name and no signup record, and is therefore recorded with no nationality.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A league manager can preview all eleven kinds before approving a season, and each returns the same picture it returns after approval for the same configuration.
- **SC-002**: A manager who has configured teams, templates and artwork but no season can preview all eleven kinds without creating a season.
- **SC-003**: No preview of a fabricated league is refused for a missing season, a missing division, a missing round, or a round of the wrong format.
- **SC-004**: A manager reading a preview's reply can always tell which season it drew, or that it drew none, without opening the picture.
- **SC-005**: A manager cannot mistake a fabricated league for their own, because the reply names every part of it that was invented.
- **SC-006**: A configuration fault inside a season that exists is still reported as such, and none of the six refusals of feature 045 is weakened.
- **SC-007**: Two consecutive previews of one kind on a season-less server differ in division, calendar, round and driver names, and agree on team names.
- **SC-008**: A maintainer running test mode sees their mock drivers' mock names in every picture that draws drivers, in all three season states.
- **SC-009**: Running every preview on a season-less server leaves the server's records exactly as they were.

## Assumptions

- **A-001**: "Current season" means the season the server is running or about to run — one of ACTIVE or SETUP status. A COMPLETED or CANCELLED season is not one, which FR-005 states.
- **A-002**: Where a server holds both an approved season and a later one pending approval, the approved one is drawn. The season a league is running is the one its templates are judged against, and the divisions of the two very largely coincide. A season pending approval alongside a running one is therefore not previewable, which is a deliberate narrowing and not an oversight.
- **A-003**: "The previous season's number" is the highest season number the server has already committed. Season numbers are issued when a season is first snapshotted, at one higher than the count of seasons that have reached ACTIVE, COMPLETED or CANCELLED status, so FR-010 restates the rule the bot already numbers seasons by rather than introducing a second one.
- **A-004**: The `server_configs.previous_season_number` column is not that number. It is written by nothing and read by nothing, and is not the source FR-010 draws on.
- **A-005**: "The general server configuration" for teams means the server-level default team list, which is what seeds a division's teams when a season is created. The Reserve team is excluded from it, as it is excluded from every other count of a division's teams.
- **A-006**: A season pending approval already holds its divisions, rounds, teams, seats and driver assignments in the same tables and the same shape as an approved season, so FR-002 requires the season lookup to widen and requires nothing else of the resolution.
- **A-007**: Fabricated drivers, fabricated outcomes, fabricated forecasts and fabricated verdicts are those feature 045 already defines. This feature adds a fabricated *league* around them; it does not restate or alter what they fabricate.
- **A-008**: Feature 045's rule that fabrication is deterministic in the driver's index is withdrawn for the fabricated league by FR-014. The outcome fabrication 045 defines is untouched.
- **A-009**: A mock driver is already named by its mock name where nothing earlier in the naming chain answers, and a mock driver has no server display name and no signup record, so FR-026 fixes existing behaviour as a rule rather than changing it. It is nonetheless testable and is tested.
- **A-010**: A mock driver carries no nationality because test mode creates the profile without a signup record, and nationality is a signup field. FR-028 draws such a driver without a flag rather than inventing one, because a seated driver is drawn as they stand.
- **A-011**: The division autocomplete offering nothing on a season-less server is acceptable because FR-021 makes the parameter optional. A manager on such a server supplies neither parameter.
- **A-012**: The fabricated calendar's length, the fabricated division's tier and the fabricated round's schedule are randomised within the bounds the bot already accepts for a real season. No preview draws a league the bot could not have been configured to run.
- **A-013**: FR-012 refuses **every** kind on a server with no season and no configured teams, including the five that draw no team and no driver — the calendar and the four weather kinds. The fabricated league is built once and whole, and either can be built or cannot; a server in this state is one `/team add` away from every preview working. This is deliberately wider than feature 045's FR-011, which refuses only the kinds that draw teams, because there the division exists and only part of it is missing.

## Documentation impact

The rules of this feature are recorded in `docs/wip-specs/image_module_specification.md`, in the "The test commands" section feature 045 created: which season the previews draw, the refusals that stand, and the fabricated league.

`README.md`, `docs/how-to/configuring-the-image-module.md` and `docs/how-to/test-mode.md` describe the previews as requiring an active season, and each becomes wrong on the day this ships:

- `README.md` — the `/images test` command reference, where the division and round parameters become optional and the active-season requirement is withdrawn.
- `docs/how-to/configuring-the-image-module.md` — the walkthrough and the checklist, both of which place the previews after a season exists. The previews now come before one, which changes the order of the job the guide owns.
- `docs/how-to/test-mode.md` — the "Previewing images" section, which states that "a server with no active season is refused" and that a test season must be built and approved before a preview can be drawn. Both statements are withdrawn.

`.specify/memory/constitution.md` needs no amendment **for this feature**. No entity is added, removed or redefined: the eleven commands stand, two of their parameters becoming optional.

One amendment is nonetheless outstanding from feature 045 and is raised through `/speckit-constitution` when this ships, never by hand. The constitution stands at v5.0.0, last amended 2026-08-17, which is before 045 landed, and its versioned entity inventory still describes the test command as a parameter carrying choice values — "the `images test verdicts` value" under New Entities (v4.8.0), and "the four `images test weather-*` values" under New Entities (v4.7.0). Those values have been subcommands since 045. The correction is a **PATCH**: no Core Principle is removed or redefined.

## Out of Scope

- Any change to what the eleven previews fabricate for outcomes, forecasts, attendance records or verdicts. Feature 045 defines that and it is untouched.
- Making a season pending approval previewable alongside a running approved one.
- Previewing a COMPLETED or CANCELLED season.
- Any change to the refusals, the artwork resolution or the fallback reporting where a season exists.
- Any change to how test mode creates, names or seats its mock drivers, including giving them a nationality.
- Writing `server_configs.previous_season_number`, or removing it.
