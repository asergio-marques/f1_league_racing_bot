# Feature Specification: Ordinal addressing of teams, and packaged asset defaults

**Feature Branch**: `047-ordinal-teams-packaged-defaults`
**Created**: 2026-08-20
**Status**: Draft
**Input**: User description: "Some behavior around teams and prepackaged asset location and organization will be changed in this session. The full scope of this session is in docs/wip-specs/image_module_changes.md. First of all verify that this is aligned with image_module_specification.md (it's okay if the former has extra detail), then start working out the specification."

## Overview

Two changes to the image module, sharing one aim: a league should get working graphics out of the box, without authoring a template against its own team list and without hand-placing a fallback image for every asset class.

The first withdraws the one place in the module where a template is authored against a league's data. A lineup template today addresses each team's block by the normalised name of that team, so no shipped file can serve a league whose teams it does not know, and every division of a season is forced to field the same teams and the same seats. Teams become addressed by ordinal, as the rounds of a calendar already are, and the name of a team reaches the module only as a filename.

The second gives every asset class a fallback that ships with the bot. The packaged directories move to `resources/defaults/<class>`, and asset resolution consults the packaged fallback where a league's own configured directory holds none.

The rules are stated in full in `docs/wip-specs/image_module_changes.md`, and have already been folded into `docs/wip-specs/image_module_specification.md`. This specification is the increment that brings the implementation to them.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Draw a lineup from the template that ships (Priority: P1)

A league manager enables the image module, leaves the lineup template at its default, and posts a lineup for a division. The graphic draws: each team of the division fills a block, in the order the division holds them, with its name and its badge; each driver fills a seat.

Today this is impossible. The shipped `lineup_template.svg` names eleven invented teams ("Apex Racing", "Aurora Racing", and so on), so a league's real teams diverge from it in both directions and the render is fatal. The league must author its own file, and re-author it whenever it adds or renames a team.

**Why this priority**: It is the whole point of the change. Every other story is a consequence of it, and none of them is worth having if a lineup still cannot be drawn from a shipped file.

**Independent Test**: Resolve a division of named teams against a template declaring ordinal blocks, and assert that team 1 of the division fills block 1, team 2 fills block 2, and each block carries the name and the badge slug of the team standing at its ordinal. No division's team name appears in any field identifier.

**Acceptance Scenarios**:

1. **Given** a division fielding three teams and a template declaring five team blocks, **When** the lineup is generated, **Then** blocks 1 to 3 are filled from the division's first, second and third team, and blocks 4 and 5 are removed with no error reported.
2. **Given** a template declaring a `team_<x>_group` for each block, **When** a block has no team at its ordinal, **Then** that group is removed in its entirety.
3. **Given** a template declaring no `team_<x>_group`, **When** a block has no team at its ordinal, **Then** every field bearing that ordinal is removed one by one, and still no error is reported.
4. **Given** a division whose second team has recruited no driver, **When** the lineup is generated, **Then** block 2 is drawn with its name and badge and every seat unoccupied, and is not removed.
5. **Given** a division fielding six teams and a template declaring four team blocks, **When** the lineup is generated, **Then** generation is abandoned with a fatal error naming the two teams that would have been dropped.
6. **Given** a team occupying three seats and a template declaring two seat slots in that team's block, **When** the lineup is generated, **Then** generation is abandoned with a fatal error naming the driver that would have been dropped.
7. **Given** a division holding a reserve team, **When** the lineup is generated, **Then** the reserve block is filled from the `reserve_` fields as before, and no reserve driver appears at any `team_<x>_` ordinal.
8. **Given** a template whose team blocks are numbered 1, 2 and 4, **When** the template is verified, **Then** the gap is a fatal error.
9. **Given** a division whose seats are filled entirely by drivers created by test mode, **When** the lineup is generated, **Then** each is drawn by its mock name at the ordinal its team stands at, no seat is drawn unoccupied, and no team is treated as having recruited nobody.
10. **Given** a season pending approval, **When** its lineup is generated, **Then** the ordinals are resolved from its divisions as they stand and the graphic is identical to the one an approved season of the same composition would draw.
11. **Given** a season under review whose largest division exceeds the template's blocks, **When** `season review` runs, **Then** it reports the failure of validation and falls back to the textual lineup, and does not report a failure to render.

---

### User Story 2 - Run divisions that differ in composition (Priority: P2)

A league runs a top division of ten two-seat teams and a lower division of six, fielding different teams entirely. Both draw a lineup from the same template file, the lower division's four spare blocks leaving silently.

A league wanting to seat three drivers on one team must author a template declaring three slots at the block that team will stand at, the shipped file declaring two at every block. That is a template it authors once, not a restriction on how it composes its season.

Today `season review` fails validation whenever the divisions of a season differ in their teams or their seat counts, wherever the image module is enabled and the `lineup` toggle is on. The restriction exists solely because a lineup template names its fields after teams, and falls away with that.

**Why this priority**: It removes a live restriction on how a league may compose a season, but a league whose divisions already match sees no change. It follows US1 and is not worth building before it.

**Independent Test**: Review a season whose two divisions field different teams and differing team counts, and assert the review reports no divergence on that account, while a season whose largest division exceeds the template's declared blocks still fails validation naming the division and the teams at fault.

**Acceptance Scenarios**:

1. **Given** a season whose divisions field different teams and different numbers of them, **When** `season review` runs with the image module enabled and the `lineup` toggle on, **Then** validation does not fail on account of that difference.
2. **Given** a season whose divisions seat different numbers of drivers per team, and a template declaring at every block at least the largest of those counts, **When** `season review` runs, **Then** validation does not fail on account of that difference.
3. **Given** a season one division of which fields more teams than the template declares blocks, **When** `season review` runs, **Then** validation fails, naming the division and the teams that would be dropped.
4. **Given** the same season, **When** `season review` runs with the `lineup` toggle **off**, **Then** validation still fails: the check reports a template that cannot draw the season and is not gated on the toggle.
5. **Given** a season every division of which fits within the template's declared counts, **When** `season review` runs, **Then** validation reports no lineup problem, whatever the divisions' relative sizes.

---

### User Story 3 - An incomplete asset set survives without a hand-placed fallback (Priority: P2)

A league points the team image directory at a folder of its own and supplies badges for eight of its ten teams. Nothing else is placed there. The lineup, the results, the standings, the attendance sheet and the verdict all draw: the eight teams show their badges, the other two show the fallback that ships with the bot, and a non-fatal notice names each team that had no file of its own.

Today the render is abandoned, because the league's own directory holds no `fallback.svg` and the fallback tier stops there.

**Why this priority**: It is what makes a partly-supplied asset set usable, and it is independent of the lineup work. It is second only to US1 in what a league notices.

**Independent Test**: Resolve a datum with no file against a configured directory holding no fallback, with a packaged directory that holds one, and assert the packaged fallback is returned and the outcome is the non-fatal fallback outcome, not the missing one.

**Acceptance Scenarios**:

1. **Given** a configured directory holding the datum's own file, **When** the asset is resolved, **Then** that file is placed and no notice is reported.
2. **Given** a configured directory that lacks the datum's file but holds a fallback, **When** the asset is resolved, **Then** the configured fallback is placed and a non-fatal notice names the field and the datum.
3. **Given** a configured directory that lacks both, and a packaged directory of the class that holds a fallback, **When** the asset is resolved, **Then** the packaged fallback is placed and the same non-fatal notice is reported.
4. **Given** neither directory holds a fallback, **When** the asset is resolved, **Then** the error is fatal and generation is abandoned.
5. **Given** a configured directory that lacks the datum's file while the packaged directory holds a file of exactly that name, **When** the asset is resolved, **Then** the packaged file is **not** used: only a fallback is drawn from the packaged tier, never the datum's own file.

---

### User Story 4 - Packaged assets live under a defaults directory (Priority: P3)

A maintainer looks at `resources/` and can tell at a glance what ships with the bot and what a league has added. Everything packaged sits under `resources/defaults/`, one directory per asset class plus the templates.

**Why this priority**: It is organisational, and invisible to a league that never moves a directory. It must land before US3 can name a packaged directory to consult, but it delivers little on its own.

**Independent Test**: Assert the default value of each `images config *-directory` and of `images config template-directory` names its `resources/defaults/` location, and that every packaged file — the seven fallbacks, the closed-set files and the fifteen templates — is present at the new path.

**Acceptance Scenarios**:

1. **Given** a server that has configured no directory for a class, **When** an asset of that class is resolved, **Then** it is sought under `resources/defaults/<class>`.
2. **Given** a server that has configured no template directory, **When** a template is sought, **Then** it is sought under `resources/defaults/templates`.
3. **Given** the packaged marker, weather and track directories, **When** their contents are listed, **Then** the closed-set files each class ships — the three direction markers, the eight weather icons, `mystery.svg` — sit beside that class's `fallback.svg` at the new location, unaltered.
4. **Given** a league that has configured a directory of its own for a class, **When** an asset of that class is resolved, **Then** the datum's own file is sought in the configured directory alone, the packaged directory being consulted only for a fallback.

---

### User Story 5 - Name a team as the league pleases (Priority: P3)

A manager adds a team named "2Fast Motorsport". It is accepted. The name no longer has to serve as an identifier in an XML document, only as a filename, so a leading digit is admitted.

**Why this priority**: It is a small relaxation that follows from US1, and few leagues are blocked by it today. It cannot be built before the ordinal addressing that removes the reason for the rule.

**Independent Test**: Validate a set of team names against the relaxed criteria and assert that a leading digit passes while empty, empty-when-normalised, colliding and `reserve`-normalising names are still refused.

**Acceptance Scenarios**:

1. **Given** the name "2Fast Motorsport", **When** `team add` is invoked, **Then** it is accepted.
2. **Given** a name that is empty once trimmed, or whose normalised form is empty, **When** `team add` or `team rename` is invoked, **Then** it is refused with a clear error.
3. **Given** a name normalising to the same value as another team of the same scope, **When** `team add` or `team rename` is invoked, **Then** it is refused, naming the team it collides with.
4. **Given** a name normalising to `reserve`, **When** `team add` or `team rename` is invoked, **Then** it is refused.
5. **Given** a team named before these criteria came into force, **When** `team rename` or `team remove` names it as the *current* team, **Then** the current name is not validated and the command proceeds.
6. **Given** a season already approved holding a team that fails these criteria, **When** the season is read, **Then** it is not re-validated and no team is renamed or removed.

### Edge Cases

- A template declaring one team block and one seat slot within it is the minimum, and is valid.
- A template declaring `team_1_name` but no `team_1_driver_1_name` is invalid: the mandatory fields must be declared throughout every block the template numbers.
- Seat slots are numbered per block, and each block's seat numbering must itself run continuously from 1.
- A division fielding no team at all draws every block removed, the reserve block alone remaining. This is not an error.
- A team is added to a division mid-season: it takes the next free position, so the ordinals already drawn do not move.
- A team seating three drivers stands at ordinal 2 in one division and ordinal 5 in another. The template must declare three seat slots at both blocks, the ceiling being a property of the block and not of the team.
- A team is configured with three seats but has filled only two, and the template declares two slots at its ordinal. This draws without error: nobody is dropped. The empty third seat is not shown, which is the one thing a league loses by declaring a block smaller than its teams are configured for.
- A division holds a mix of signed-up drivers and drivers created by test mode. Both are drawn by their own names, and neither kind is drawn as an unoccupied seat.
- A driver created by test mode carries no nationality, no signup record being made for one, and is drawn without a flag as a posting would draw them.
- A league adds a team beyond the shipped template's eleventh block. Generation is fatal until it authors a template declaring a twelfth; nothing else can raise the ceiling.
- Two divisions of one season resolve the same ordinal to different teams. Nothing of a block save the name and the badge varies with the team, so one template serves both.
- A configured directory that cannot be resolved is reported as a rejected configuration, not as a class with no directory, and the packaged fallback does not paper over it.
- A datum whose normalised form is `fallback` resolves to the fallback file, in the configured directory as today.
- A league configures a directory that happens to be the packaged directory of the class. The two tiers are then one and the same, and resolution behaves exactly as the single-tier case did.

## Requirements *(mandatory)*

### Functional Requirements

#### Ordinal addressing of teams

- **FR-001**: A lineup template MUST address the fields of a team by the ordinal of that team — `team_<x>_name`, `team_<x>_image`, `team_<x>_driver_<y>_name`, `team_<x>_driver_<y>_flag`, `team_<x>_driver_<y>_image` — and MUST NOT address any field by a datum of the league.
- **FR-002**: `<x>` MUST be a value between 1 and the number of team blocks the template declares, numbered continuously from 1. A gap in the numbering MUST be a fatal error.
- **FR-003**: `<y>` MUST be a value between 1 and the number of seat slots the template declares within the block of ordinal `<x>`, numbered continuously from 1 within that block. Each block MUST be free to declare its own number of seat slots, independently of every other block.
- **FR-004**: A template MUST admit an optional `team_<x>_group` wrapping every other field of that team. Where a template declines it, the fields of that ordinal MUST be removable one by one instead.
- **FR-005**: The reserve team MUST continue to be addressed as a singleton by `reserve_` fields, MUST NOT be addressed via any `team_<x>_` field, and MUST NOT occupy an ordinal.
- **FR-006**: The team drawn in the block of ordinal `<x>` MUST be the team standing at position `<x>` in the team list of the division being drawn, the reserve team excepted, ordered as the division holds it.
- **FR-007**: The correspondence between an ordinal and a team MUST be resolved afresh at each generation from the division, and MUST be recorded in no template.
- **FR-008**: A team added to a division MUST take the next free position, so that the teams already drawn do not move. The order a division holds its teams in MUST therefore be the order they were added in, and MUST NOT depend on their names: an order sorted by name would move every team of later name when one is added, and would move a team when it is renamed.
- **FR-009**: The lineup posting path, the `/images test` preview path and the team listing `season review` reads MUST agree on that order, so that the ordinal a team occupies on the graphic is the position it occupies in the text printed beside it.

#### Capacity

- **FR-010**: The capacity of every collection of the module MUST be fixed by the template. The kind of capacity fixed by the data — of which the teams of a division and the seats of a team were the only instances — MUST be withdrawn.
- **FR-011**: Teams of the division in excess of the team blocks the template declares MUST be a fatal error, naming the teams that would be dropped. A division MAY therefore field any number of teams from zero up to the block count the template declares, and no number above it. Raising that ceiling MUST be possible by authoring a template that declares more blocks, and MUST require nothing else.
- **FR-012**: Drivers occupying a team's seats in excess of the seat slots the template declares within that team's block MUST be a fatal error, naming the drivers that would be dropped. A seat the team is configured with but no driver occupies MUST NOT count towards this: omitting it drops nobody.
- **FR-013**: Team blocks declared in excess of the teams of the division MUST be removed — by `team_<x>_group` in its entirety, or field by field where the template declares no such group — and no error MUST be reported.
- **FR-014**: Seat slots declared in excess of the seats a team is configured with MUST be removed silently, and no error MUST be reported. A slot within the team's configured seats that no driver occupies MUST instead be drawn unoccupied — a vacancy the league can see is not a surplus slot.
- **FR-015**: A team the division fields that has recruited nobody MUST be drawn with every seat unoccupied, and MUST NOT be removed. Only an ordinal the division fields no team at MUST be removed.
- **FR-016**: The shortfall FR-013 removes has no lower bound: a division fielding no team at all MUST be drawn without error, as every team block removed and the reserve block alone.
- **FR-017**: The seat ceiling MUST be positional and not per-team: the ceiling on a team's drivers is the slot count of the block at the ordinal that team stands at, in the division being drawn. A league whose teams differ in size MUST therefore declare, at each block, at least the largest number of drivers any team may seat at that ordinal in any division, the spare slots being removed under FR-014.
- **FR-018**: Every collection standing inside a member of another and bounded by a configured value of that containing member MUST behave identically, with no exception for any one graphic. The seats of a team on a lineup and the cars of a round on a constructors grid are the two instances, and one rule governs both:
    - the members the template declares are a **ceiling** and not a count;
    - over-declaration is never an error, the surplus being removed silently per containing member;
    - the fatal test is against the **data actually drawn** — the drivers who occupy the seats, or who drove the cars — and never against the configured value itself.

#### Uniformity of divisions

- **FR-019**: The requirement that the divisions of a season field the same teams and the same number of seats in each, and its validation at season review, MUST be withdrawn.

#### Verification

- **FR-020**: When a lineup template is configured, it MUST be verified that it declares `division_name`, at least one team block and at least one seat slot within it, each numbered continuously from 1; that the blocks declare `team_<x>_name` and `team_<x>_driver_<y>_name` throughout; and that it declares `reserve_group` with at least one reserve slot, of which the first declares `reserve_driver_1_name`. A mandatory field that is absent MUST be a fatal error rejecting the command.
- **FR-021**: At generation, the counts the template declares MUST be measured against the division, an excess on the side of the division being fatal.
- **FR-022**: At season review, the same measurement MUST be made against every division of the season, an excess being a failure of validation naming the division and the team or seat at fault.
- **FR-023**: The checks of FR-020 to FR-022 MUST be made whether or not the `lineup` toggle is on.
- **FR-024**: Every divergence of the lineup graphic MUST be fatal or a failure of validation, and MUST NEVER be reported as a warning: every field of it is verifiable against the template alone.
- **FR-025**: Across the module, a stand-in MUST stand in for how many members will be drawn and never for which: a calendar template is compared against a round count and a lineup template against a count of teams and of seats, neither against a list of names.

#### The name of a team

- **FR-026**: The name of a team MUST reach the image module as a filename and in no other way.
- **FR-027**: One rule of normalisation MUST serve every class of asset — a team name, a country, a track, a tyre compound and a condition of weather alike. The rule is: the datum trimmed of whitespace, stripped of diacritics, converted to lowercase, with every run of characters that is neither a letter nor a digit replaced by a single underscore, and any leading or trailing underscore removed. "Red Bull" becomes `red_bull`; "Force India (B)" becomes `force_india_b`.
- **FR-028**: The normalised form MUST be bound by what a filename admits, and not by what the identifier of a node of an XML document admits.
- **FR-029**: The image of a team MUST be searched for under a filename equal to the normalised form of that name, in every graphic that draws one: the lineup, the two results graphics, the two standings graphics, the attendance sheet and the verdict.
- **FR-030**: `team add` and `team rename` MUST reject a name that is empty once trimmed, whose normalised form is empty, that normalises to the same value as another team of the same scope, or that normalises to `reserve`. The scope is the server for the server's team list, and the division for the teams of a season; two divisions of one season MAY each field a team normalising to the same value. Of the two names `team rename` takes, only the new one MUST be validated.
- **FR-031**: The requirement that a team name begin with a letter MUST be withdrawn; a name beginning with a digit MUST be admitted.
- **FR-032**: `season review` MUST fail validation where any team of any division, or of the server's team configuration, fails FR-030, naming every offending team. Seasons already approved MUST NOT be re-validated, and no team MUST be renamed or removed by the change.
- **FR-033**: These constraints MUST hold whether or not the image module is enabled and whatever the `images config toggle` settings are.

#### What ships

- **FR-034**: The shipped `lineup_template.svg` MUST be redrawn to address its teams by ordinal, and MUST carry no team name of any league, invented or real.
- **FR-035**: The redrawn template MUST declare a `team_<x>_group` for each of its team blocks, so that a league sees the removable group in a working example.
- **FR-036**: The redrawn template MUST keep the shape of the file it replaces: eleven team blocks of two seat slots each, beside the reserve block of six slots. Eleven blocks covers the ten default constructor teams with one spare.

#### Packaged directory relocation

- **FR-037**: The packaged directory of an asset class MUST move from `resources/<class>` to `resources/defaults/<class>`, for the seven asset classes — tracks, teams, flags, drivers, markers, weather, tyres — and for the template directory.
- **FR-038**: The default value read by every `images config *-directory` command, and the default value of `images config template-directory`, MUST name the new location.
- **FR-039**: Nothing shipped in a packaged directory MUST change in kind: each class's fallback, the closed-set files a class ships beside it (the marker directions, the weather icons, `mystery.svg`), and the fifteen templates all move unaltered.

#### Two-tier fallback resolution

- **FR-040**: Asset resolution MUST have exactly four outcomes: the datum's own file found in the configured directory; not found but the configured directory holds a fallback; not found and the configured directory holds no fallback but the packaged directory of the class does; not found and neither holds a fallback.
- **FR-041**: The second and third outcomes MUST place the fallback upon the field and report the same **notice**, naming the field and the datum that had no file of its own. The fourth MUST be fatal and MUST abandon generation.
- **FR-042**: The datum's own file MUST continue to be sought in the configured directory alone. The packaged directory MUST be consulted for a fallback only.
- **FR-043**: Every statement in the specification that a directory "holds" or "holds no" fallback MUST be read as this two-tier check taken as a whole, and not as the configured directory alone.

#### Coverage of the graphics that draw a team

- **FR-044**: The two-tier resolution of FR-040 to FR-043 MUST apply to every asset class and every graphic of the module, and MUST NOT be particular to the team class or to the lineup.
- **FR-045**: Each of the seven graphics that draw a team badge — the lineup, the two results graphics, the two standings graphics, the attendance sheet and the verdict — MUST be exercised by a test proving it draws a packaged-tier fallback for a team whose badge the configured directory does not hold, and accepts a team name beginning with a digit. Exercising the resolver alone MUST NOT be taken to satisfy this.

#### Test mode, and a season under review

- **FR-046**: The lineup MUST read the same data in the same way whether test mode is set or not. Test mode determines what data the server holds, never how the correspondence of FR-006 is resolved nor how seats are filled.
- **FR-047**: A driver created by test mode MUST be drawn by the mock name it was created with, at whatever ordinal its team stands at, in every graphic that draws a driver, and MUST NEVER be treated as an unoccupied seat.
- **FR-048**: A division seated wholly with drivers created by test mode MUST count as a division that has seated drivers. FR-015's team that "has recruited nobody" MUST NOT be read to cover it, and no driver MUST be fabricated over it.
- **FR-049**: A season pending approval MUST be drawn exactly as an approved season is — its divisions, teams, seats and seated drivers taken as they stand, the ordinal correspondence of FR-006 resolved from them — and nothing MUST be substituted or fabricated on account of its status alone.
- **FR-050**: The lineup graphic attached to `season review` MUST be drawn wherever the season passes the checks of FR-020 to FR-022. Where an excess of teams or of seats makes the render fatal, the review MUST report that failure of validation and fall back to the textual lineup; it MUST NOT fail the command, and MUST NOT report the fault as a failure to render.
- **FR-051**: A command of the `/images test` family MUST resolve its assets under the two-tier resolution exactly as a posting for that division would. The existing rule that such a command MUST NOT substitute the packaged directories for those the league configured MUST be read as FR-042 states it — the datum's own file is sought in the configured directory alone — and MUST NOT be read as withholding the packaged fallback tier from these commands.

#### The invented-league preview

- **FR-052**: `/images test` MUST continue to refuse a preview on a server that has configured no team at all, for the kinds that draw a team or a driver. The reason recorded in the code MUST no longer cite a lineup template naming its fields after real teams, that rationale being withdrawn with FR-001; the reason the specification already gives — that these draw a team or a driver and no seat exists to fabricate a driver into — MUST stand in its place.

### Key Entities

- **Team block**: A place in the lineup layout, bearing an ordinal. It belongs to the layout and not to a team. Holds the team's name and badge fields and a nested collection of seat slots, and optionally a removable group wrapping them.
- **Seat slot**: A place within a team block, bearing an ordinal within that block. Holds a driver's name, flag and portrait fields.
- **Reserve block**: The lineup's singleton collection, addressed by name and not by ordinal, holding its own seat slots. Its `reserve_group` is mandatory in a template.
- **Packaged directory**: The directory shipped with the module for an asset class, at `resources/defaults/<class>`. Carries that class's `fallback.svg` and any closed-set files it ships. Distinct from the directory a league has configured, though the two are one where a league has not moved it.
- **Configured directory**: The directory a league has pointed an asset class at. The only tier in which a datum's own file is sought.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A league that has enabled the image module and configured nothing beyond it can post a lineup for a division of any composition within the shipped template's capacity, having authored no template and no asset file.
- **SC-002**: The shipped lineup template contains zero team names: no identifier in it names a team of any league, invented or real.
- **SC-003**: A season whose divisions field different teams, and different numbers of them, passes review, where today it fails.
- **SC-004**: A league supplying badges for a subset of its teams draws every graphic that shows a badge, with a notice per unsupplied team and no abandoned render — with no file placed by the league in the fallback position.
- **SC-005**: Every one of the four asset-resolution outcomes is exercised by a passing test, and no fifth outcome exists.
- **SC-006**: Each of the seven graphics that draw a team badge is exercised by a passing test proving it draws a packaged-tier fallback and accepts a team name beginning with a digit — seven graphics, not one.
- **SC-007**: A division fielding any number of teams from zero to the template's declared block count draws without error, and one team above it is refused with the offending teams named. The same holds for drivers within a block.
- **SC-008**: Every packaged file — seven fallbacks, the closed-set files of the marker, weather and track classes, and the fifteen templates — is reachable at its `resources/defaults/` location, and no `images config` default names an old one.
- **SC-009**: A team name beginning with a digit is accepted, and each of the four remaining rejection criteria still refuses the name it names.
- **SC-010**: The shipped lineup template declares eleven team blocks of two seat slots each and a reserve block of six, and a division of eleven two-seat teams draws with every block filled and none removed.
- **SC-011**: A lineup drawn under test mode shows every mock driver by its mock name, and is byte-for-byte the graphic the same division would draw were those drivers signed up instead.
- **SC-012**: A season pending approval draws the same lineup an approved season of identical composition draws, and `season review` attaches it whenever the season passes validation.
- **SC-013**: The full test suite passes and line coverage stays at or above the floor the CI workflow sets.

## Assumptions

- **A-001**: The bot is not in production, so no data migration is needed for a server holding a configured directory that names an old `resources/<class>` path. A league that has explicitly configured a directory keeps whatever it configured; only the *default* moves.
- **A-002**: The insertion order of a division's teams is recoverable from data already stored, so FR-008 needs no new ordering column and no reordering command. Adding a team appends.
- **A-003**: The non-fatal notice reported for a packaged-tier fallback is worded identically to the configured-tier one. A league is told the datum had no file of its own; which tier answered is not information it can act on.
- **A-004**: The refusal of FR-052 is a behaviour the specification already states and already justifies on grounds this feature does not touch. Only the rationale carried in the code is stale, so this is a comment-and-message correction and not a change of behaviour.
- **A-005**: `resources/README.md`, `README.md` and the how-to guides are updated for the new packaged paths as part of this work. Any repository-layout convention for where a league keeps its *own* directories is documentation, not a rule the module enforces.

## Out of Scope

- Any change to how the badge of a team is resolved. It is resolved from the normalised team name today and continues to be.
- Any change to the reserve block, which remains a singleton whose seats are fixed by the template.
- Any change to the behaviour of the other six graphics that draw a team badge. Their fields already bear ordinals.
- Any command for reordering the teams of a division.
- Any change to the `images config *-directory` commands themselves, their validation, or the requirement that a configured directory resolve inside the project root.
- Any repository-layout convention for where a league keeps the directories it configures.

## Dependencies

- **D-001**: `.specify/memory/constitution.md` states the rules this feature withdraws — Principle IX ("Team name validity" requiring a leading letter, and "Uniform divisions where a lineup graphic is drawn"), and Principle XIV rules 11 (keyed template ids), 12 (collection capacity) and 13 (asset resolution). It MUST be amended via `/speckit-constitution` before or alongside implementation; it is never edited by hand.
- **D-002**: `docs/wip-specs/image_module_specification.md` already carries these rules and is the source of truth for them. `docs/wip-specs/image_module_changes.md` is the change register for this session and adds no rule the specification lacks.
