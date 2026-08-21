# Ordinal addressing of teams

## The governing principle

- A template shall be authorable in ignorance of the teams, the drivers and the tracks of any one league, and one file shall serve every league alike.
- No collection of any graphic shall be distinguished by a datum of the league. Every member of every collection bears an ordinal, or is the single member of a singleton collection.

## Addressing

- The fields of a lineup template shall be addressed by the ordinal of the team, as the fields of a calendar template are addressed by the ordinal of the round: `team_<x>_name`, `team_<x>_image`, `team_<x>_driver_<y>_name`, `team_<x>_driver_<y>_flag`, `team_<x>_driver_<y>_image`.
- `<x>` shall be a value between 1 and the number of team blocks the template declares, numbered continuously from 1. A gap in the numbering is a fatal error.
- `<y>` shall be a value between 1 and the number of seat slots the template declares within the block of ordinal `<x>`.
- A template shall additionally admit `team_<x>_group`, an optional removable group wrapping every other field of that team. It is optional where `reserve_group` is mandatory, a template declining it having the fields of that ordinal removed one by one instead.
- The reserve team shall continue to be addressed as a singleton, by `reserve_` fields, and shall never be addressed via `team_<x>_` fields.

## The correspondence between an ordinal and a team

- The team drawn in the block of ordinal `<x>` shall be the team standing at position `<x>` in the team list of the division being drawn, the reserve team excepted, that list being ordered as the division holds it.
- The correspondence shall be held by the division, and shall be resolved afresh at each generation. It shall be recorded in no template.
- A team added to a division shall take the next free position, so that the teams already drawn do not move.
- The team list of a division shall be held in the order the teams were added to it, and in no order derived from their names. The lineup posting path, the `images test` preview path and the team listing of `season review` shall read that one order.
- The same ordinal may stand for a different team in another division, and for a differently named team in another league.

## Capacity

- The capacity of every collection of the module shall be fixed by the template. The kind of capacity fixed by the data, of which the teams of a division and the seats of a team were the only instances, is withdrawn.
- Teams of the division in excess of the team blocks the template declares shall be a fatal error, naming the teams that would be dropped.
- Drivers occupying a team's seats in excess of the seat slots the template declares within that team's block shall be a fatal error, naming the drivers that would be dropped. A seat the team is configured with but no driver occupies shall not count towards this, omitting it dropping nobody.
- Team blocks declared in excess of the teams of the division shall have `team_<x>_group` removed in its entirety, or every field bearing that ordinal removed one by one where the template declares no such group, and no error shall be reported.
- Seat slots declared in excess of the seats a team is configured with shall be removed silently, and no error shall be reported. A slot within the team's configured seats that no driver occupies shall instead be drawn unoccupied: a vacancy a league can see is not a surplus slot.
- A member the data hold but leave empty is not an unused member, and shall be drawn empty rather than removed. A team of the division that has recruited nobody shall be drawn with every seat unoccupied; only an ordinal the division fields no team at shall be removed.
- `team_<x>_group` belongs to a place in the layout and not to a team. The rule by which a league decided whether a team that had recruited nobody was drawn at all, by declaring or declining that team's own group, is withdrawn: a fielded team is always drawn.
- A division fielding fewer teams than the template declares shall be drawn without error, as a division fielding fewer rounds than a calendar template declares is.
- Every collection standing inside a member of another and bounded by a configured value of that containing member shall behave alike, no graphic being excepted. The seats of a team on a lineup and the cars of a round on a constructors grid are the two instances, and one rule governs both: the members the template declares are a ceiling and not a count, over-declaration is never an error, and the fatal test is against the data actually drawn and never against the configured value itself.

## Uniformity of divisions

- The divisions of a season may field different teams, and different numbers of seats in each.
- The requirement that they field the same teams and the same number of seats, and the validation of it at season review, are withdrawn.

## Verification

- No field of a lineup graphic depends upon the team list of a league, so every field of it shall be verifiable against the template alone, at every moment the template is verified. No divergence of this graphic shall be reported as a warning.
- When the template is configured, it shall be verified that it declares `division_name`, at least one team block and at least one seat slot within it, each numbered continuously from 1, that the blocks declare `team_<x>_name` and `team_<x>_driver_<y>_name` throughout, and that it declares `reserve_group` with at least one reserve slot, of which the first declares `reserve_driver_1_name`.
- At generation, and at season review against every division of the season, the counts the template declares shall be measured against the division, an excess on the side of the division being fatal at generation and a failure of validation at review.
- These checks shall be made whether or not the `lineup` toggle is on. They report a template that cannot draw the season, and shall never restrict how a league may compose one.
- Across the module, a stand-in shall stand in for how many members will be drawn and never for which: a calendar template is compared against a round count and a lineup template against a count of teams and of seats, neither against a list of names.

## The name of a team

- The name of a team shall reach the image module as a filename and in no other way.
- One rule of normalization shall serve every class of asset: a team name, a country, a track, a tyre compound and a condition of weather are all normalized by it.
- The normalized form names a file and never a field of a template. It shall be bound by what a filename admits, and not by what the identifier of a node of an XML document admits.
- The image of a team shall be searched for under a filename equal to the normalized form of that name, in every graphic that draws one: the lineup, the two results graphics, the two standings graphics, the attendance sheet and the verdict.
- The constraints upon the names of teams shall belong to every graphic that draws a badge, and not to the lineup alone.
- A name shall be rejected where it is empty once trimmed, where its normalized form is empty, where it normalizes to the same value as another team of the same scope, or where it normalizes to `reserve`.
- The requirement that a name begin with a letter is withdrawn, and a name beginning with a digit shall be admitted.

## What ships

- The `lineup_template.svg` shipped with the bot shall be redrawn to address its teams by ordinal, and shall carry no team name of any league, invented or real.
- It shall thereby serve any league, as every other template of the module already does.
- It shall keep the shape of the file it replaces: eleven team blocks of two seat slots each, beside the reserve block of six slots, and shall declare `team_<x>_group` for each of its team blocks.
- The refusal of a preview on a server that has configured no team shall stand, its reason restated: a lineup drawn for such a server removes every block and shows nothing. It no longer rests on a template naming its fields after real teams.

## Out of scope

- Any change to how the badge of a team is resolved. It is resolved from the normalized team name today and shall continue to be.
- Any change to the reserve block, which remains a singleton whose seats are fixed by the template.
- Any change to the behaviour of the other six graphics that draw a team badge. Their fields already bear ordinals.
- Any command for reordering the teams of a division. The ordinal follows the position a team holds in the division's team list.

# Packaged directory relocation and two-tier fallback resolution

## The packaged directory

- The packaged directory of an asset class shall move from "resources/<class>" to "resources/defaults/<class>". The affected classes are the seven asset directories — tracks, teams, flags, drivers, markers, weather, tyres — and the template directory.
- The default value read by every "images config *-directory" command, and the default value of "images config template-directory", shall be updated to name the new location.
- The packaged directory is the directory shipped with the module for a class, and is distinct from the directory a league has configured for it. Where a league has not moved a class's directory, the two remain one and the same.
- Nothing shipped in a packaged directory changes in kind: the fallback of each class, the closed-set files a class ships beside its fallback (the marker directions, the weather icons, "mystery.svg"), and the fifteen templates all move to the new location unaltered.

## Two-tier fallback resolution

- The resolution of an asset shall gain a second tier: where the datum's own file is not found and the configured directory holds no fallback, the packaged directory of that class shall be consulted for one before the miss is treated as fatal.
- The resolution of an asset therefore has four outcomes and no others:
    - the file named by the normalized datum is found in the configured directory, and is placed upon the field;
    - it is not found, but the configured directory holds a fallback, whereupon that fallback is placed upon the field and a non-fatal error reported, naming the field and the datum that had no file of its own;
    - it is not found and the configured directory holds no fallback, but the packaged directory of the class holds one, whereupon that fallback is placed upon the field and the same non-fatal error reported;
    - it is not found and neither the configured directory nor the packaged directory holds a fallback, whereupon the error is fatal and the generation is abandoned.
- Every existing statement in the specification that a directory "holds" or "holds no" fallback shall be read as this two-tier check taken as a whole, and not as the configured directory alone.
- A league whose configured directory carries no "fallback.svg" of its own no longer needs to place one there for the class to survive an incomplete asset set; the packaged fallback now answers a miss the configured directory cannot.

## Out of scope

- Any change to how the datum's own file is sought in the configured directory. It is still sought there alone, and only its absence triggers the fallback tiers. (The packaged directory gains a datum-own-file search of its own for closed-set classes only — see "Closed-set packaged fallback" below, carried in a later change.)
- Any change to the "images config *-directory" commands themselves, their validation, or the requirement that a configured directory resolve inside the project root.
- Any repository-layout convention for where a league keeps the directories it configures (e.g. a "resources/leagues/" convention). That is a documentation and repository-organisation matter for README.md, resources/README.md and the how-to guides, not a rule the module enforces or the specification states.

# Closed-set packaged fallback searches the datum's own file

## The rule

- For a class whose data are a closed set the module itself defines — the marker and weather classes — the packaged directory's search is no longer confined to its "fallback.svg". Where the configured directory holds neither the datum's own file nor a fallback of its own, the packaged directory of a closed-set class shall be searched for the datum's own file before its "fallback.svg" is drawn.
- This holds whether or not the league has pointed the class at a directory of its own. A league did not choose the closed-set vocabulary and cannot be incomplete against it, so moving the directory does not change what a miss against it resolves to.
- The four-outcome table of the two-tier resolution above is unchanged. This refines its third row ("the packaged directory of the class holds one") for the two closed-set classes alone, and adds no fifth outcome.
- The five open-set classes — tracks, teams, flags, drivers, tyres — are unaffected in every respect: the datum's own file in their packaged directory is still never drawn for a league that did not supply it.

## Out of scope

- Any change to the marker and weather vocabularies themselves, or to which classes are closed sets. `mystery.svg` is unaffected: it is not a per-datum closed set and was never resolved through this mechanism.
- Any change to the non-fatal error a league sees when a fallback of either kind is drawn. It is the same notice regardless of which tier, or which file within the packaged tier, answered the miss.
