# Results and Standings module
- <COMMAND CHANGE> The results and standings module may be enabled via a "module enable" command akin to the weather and signup modules. May only be used by league admins.
- <COMMAND CHANGE> The results and standings module may be disabled via a "module disable" command akin to the weather and signup modules. May only be used by league admins.
- The results and standings module is disabled by default.

## Assigning channels to divisions
- <COMMAND CHANGE AND NEW COMMAND> When adding a division, the command shall no longer intake a weather forecast channel. Instead, there will be a new "weather channel" command that has as input a division name and a channel, which serves a similar purpose.
- It shall not be possible to confirm a season's placements if the weather module is enabled and not all divisions have a weather forecast channel configured.
- <NEW COMMAND> There will be a new "results channel" command that has as input a division name and a channel on which race results shall be posted by the bot, formatted.
- <NEW COMMAND> There will be a new "standings channel" command that has as input a division name and a channel on which standings shall be posted by the bot, formatted.
- There shall be a "verdicts channel" command that has as input a division name and a channel on which penalty and appeal verdicts shall be posted by the bot. Automatic attendance sanctions are announced in the same channel.
- If the results & standings module is enabled, confirming a season's placements shall fail if any division lacks a results channel, a standings channel or a verdicts channel, or if no points configuration is attached to the season. Each missing item shall be named individually.
    - The confirmation shall also fail if a points configuration attached to the season no longer exists in the league points schema store, naming each such configuration and saying how to put it right. A season shall never be left unconfirmable without being told why.
- It shall not be possible to enable the results & standings module once the season's placements have been confirmed.
- Disabling the results & standings module shall remain possible once the season's placements have been confirmed. A league that finds its results unworkable part-way through a championship shall be able to stop running them, and shall not be held to the module until the season ends.
- Disabling the results & standings module once the season's placements have been confirmed shall destroy that season's results entire: every classification recorded, every standing computed from them, and every results and standings message already posted shall be deleted. The module's configuration shall be kept — the points configurations, the season's own copy of them, and each division's results, standings and verdicts channels — those being settings rather than output. Every penalty and appeal verdict already announced shall be removed from where it was posted, and the banner heading its round's verdicts with it; the announcement of an automatic attendance sanction shall stay where it is, as shall a banner that also heads one. Decided 2026-09-21 (#189), withdrawing the rule that announced verdicts remain. A message the bot cannot remove shall be named to the league with a link to each, for removal by hand, and shall not be counted as removed. Decided 2026-09-21. An amendment still open shall be ended and its channel deleted, the round it was amending being destroyed with the rest.
- Disabling the results & standings module once the season's placements have been confirmed shall end every round of that season still awaiting results, report verdicts or appeal verdicts, each round being closed as having run without results. Nothing else could ever move such a round once the module is gone, and a round left waiting would hold its division open and its season uncompletable for ever. A round whose moment has not yet come shall be left alone, being closed in its own time by the passage of that moment. The rounds shall be ended even where the destruction of the season's results stops part-way, the league being told that it did not finish and that some of the season's messages may remain for removal by hand. Decided 2026-09-21.
- Disabling the results & standings module shall first tell the league what the disable will destroy and what it will keep, and shall write nothing until the league confirms. This shall be so whether or not the attendance module is enabled.
- Disabling the results & standings module shall disable the attendance module with it.

## Results
### Design
#### Points configurations
- There is no default points position configuration for a league (every position of every possible session gives 0 points).
- The schema of points configuration is as follows:
    - League
        |
        --> Points schema store
            |
            --> Configuration "100%"
                |
                --> Session "Sprint Qualifying"
                    |
                    --> 1st = 3 points
                    --> 2nd = 2 points
                    ...
                | 
                --> Session "Sprint Race"
                    |
                    --> 1st = 10 points
                    --> 2nd = 9 points
                    ...
                    --> Fastest lap = 1 point
                    --> Fastest lap position limit = 10
                ...
                --> Session "Feature Qualifying"
                ...
                --> Session "Feature Race"
            |
            --> Configuration "75%"
            ...
            --> Configuration "50%"
            ...
            --> Other configurations...
        |
        --> Season
            |
            --> Points schema store
                |
                --> Configuration "100%"
                ...
                --> Configuration "50%"
            |
            --> Modification schema store

    END
- The design shall follow the idea that a league manager may add, remove or modify the configurations in the schema store, then attach and detach them from a season whose placements are yet to be first confirmed via a "weak link". Once the season's placements are first confirmed from the "placements review", the attached configurations' settings are copied over to the season's points schema store and remain completely independent of the league's configuration.
    - In practice, this means that any changes done while there is no season, or before a season's placements are first confirmed, will be valid to any season whose placements are confirmed in the future, regardless of whether the modified configuration is attached or not.
    - However, once the season's placements have been confirmed, the modifications done to the configurations in the league points schema store are NOT applied to the season's own configuration of the same name.
- There shall be the possibility to amend a points system mid-season, but it will require higher permissions. Once an amending session is started (by enabling amending), a copy of the season's current points schema store will be made and placed in a "modification schema store". Any changes made will be done to this "modification store". Only upon review and approval will the settings in the modification store overwrite the points schema store of the season completely. After they are overwritten, all results and standings posted after every round of every division shall be reposted taking into consideration the new values.
    - The higher permission is the league admin's tier, and it is asked of the review command that carries the approval. Starting an amending session, making changes to the modification store and discarding them are a league manager's; reviewing them is not, the review and the approval being one command. Approval overwrites the season's points entire and nothing undoes it.

#### Results and standings
- The schema for results and standings is as follows:
    - League
        |
        --> Division
            |
            --> Round
                |
                --> ID = "1"
                |
                --> Type = "SPRINT"
                |
                --> Session "Sprint Qualifying"
                    |
                    --> Type
                    |
                    --> Points Configuration name
                    |
                    --> Results
                |
                --> Session "Sprint Race"
                ...
                --> Session "Feature Qualifying"
                ...
                --> Session "Feature Race"
                ...
                --> Driver Standings after round
                    |
                    --> Driver 1
                        |
                        --> Discord ID
                        |
                        --> Points
                        |
                        --> Finishes 1st place
                        ...
                        --> Finishes nth place
                        |
                        --> 1st place first obtained on Round Number
                        ...
                        --> nth place first obtained on Round Number
                    |
                    --> Driver2..n
                |
                --> Team standings after round
                    |
                    --> Team A
                        |
                        --> Role
                        |
                        --> Points
                        |
                        --> Finishes 1st place
                        ...
                        --> Finishes nth place
                        |
                        --> 1st place first obtained on Round Number
                        ...
                        --> nth place first obtained on Round Number
                    |
                    --> Team B..n
            |
            --> Round
                |
                --> ID = "2"
                |
                --> Type = "NORMAL"
                |
                --> Session "Feature Qualifying"
                ...
                --> Session "Feature Race"
                ...
                --> Driver Standings after round
                ...
                --> Team Standings after round
                ...
    
- The main design idea is that results are submitted per round, and a points configuration is applied in order to calculate the points obtained by each finishing position in each session independently.
- The usage of different points configurations in different sessions will allow things like partial points attribution (for a race that did not reach 50% of race distance, for example).
- The results for each session will be persisted indefinitely in the database, as that will be useful for further feature implementations. This is not something that may be deferred, as there will be holes in data otherwise.

### Detailed functionality specification for results
#### Assumption
- If no command is used to specify the number of points obtained by finishing in a given position, session type, and configuration, 0 points is to be assumed.

#### Adding, removing, modifying point configurations
- <NEW COMMAND> A "results config add" command will intake a string which shall be the name of the points configuration to be saved in the league points schema store. The string will serve as the ID of the configuration.
    - The name shall hold no role mention, "@everyone" or "@here", in any case, no emoji, no Discord markup and no mention of a member, as a division's name shall not. Decided 2026-09-21 (#362) and 2026-09-23 (#388).
    - Adding a points config to the league points schema store does not automatically append it to a season whose placements are yet to be confirmed.
- <NEW COMMAND> A "results config remove" command will intake a string which is the ID of the points configuration to be removed from the league points schema store. It is a league admin's: the configuration is deleted outright and nothing puts one back.
    - Removing a points config shall also detach it from any season **whose placements are yet to be first confirmed**, so that no season is left attached to a configuration that does not exist. A season whose placements have been confirmed shall keep its attachment: it holds its own copy of the points, taken at that confirmation, and that copy is what its results are scored and chosen from.
    - Where a season whose placements are yet to be first confirmed stands on the configuration, the command shall name that season, state that the season will be left needing another configuration before its placements can be confirmed, and shall delete nothing until the manager confirms. Where no such season is attached, it shall remove the configuration without asking.
- <NEW COMMAND> A "results config session" command will intake the string that IDs the points configuration in the league points schema store to be changed, a coded enum for the "session type" (Sprint Quali, Sprint Race, Feature Quali, Feature Race), an integer signifying position, and an integer signifying the number of points gained.
- <NEW COMMAND> A "results config fl" command will intake the string that IDs the points configuration to be changed in the league points schema store, a coded enum for the "session type" (Sprint Quali, Sprint Race, Feature Quali, Feature Race) and an integer signifying the number of points gained for having the shortest lap time in a session. The session types "Sprint Quali" and "Feature Quali" are invalid for this command.
- <NEW COMMAND> A "results config fl-plimit" command will intake the string that IDs the points configuration to be changed in the league points schema store, a coded enum for the "session type" (Sprint Quali, Sprint Race, Feature Quali, Feature Race) and an integer signifying the lowest valid position for which a driver is eligible for fastest-lap points (e.g. if this is configured to 10, then if the 11th place driver gets the fastest lap, then they get no points). The session types "Sprint Quali" and "Feature Quali" are invalid for this command.
- For a given configuration and a given session type, a lower finishing position shall never be worth as much as or more than the position above it. Two positive values tying is a violation of this; positions worth nothing below the points-paying places are not.
    - A command that sets points shall apply the change and then report that the table is out of order, naming every position at fault. It shall not refuse the change. Filling a table in passes through states that are momentarily out of order — a position set before the one above it, a table repaired from the bottom up — and refusing them would put ordinary ways of building a table out of reach.
    - The refusal falls where a table stops being a draft: the first confirmation of a season's placements, and the approval of a mid-season amendment.

#### Setting many positions at once
- There shall be a "results config bulk-session" command that intakes the configuration name and a session type and opens a form in which many positions are given at once, one "position, points" pair per line. Blank lines are skipped; a position must be a positive integer and its points non-negative; where a position appears more than once the last value given wins and the override is reported. Every valid pair is applied and every rejected line is reported back, so a partly-wrong input still applies what was right.
- There shall be a "results config xml-import" command that intakes the name of an existing configuration and an XML payload, given either as pasted text or as an attached file. One payload may carry several session types, each with any number of positions and a fastest-lap bonus with an optional position limit.
    - The import is applied atomically: any validation failure leaves the configuration untouched.
    - Positions not named in the payload are left as they stand, so a partial import is safe.
    - An unknown session type, negative points, a position below 1, or a fastest-lap element on a qualifying session shall be rejected. Points within one session block must not increase as position increases, and two positive values may not tie.
    - A session block carrying neither a position nor a fastest-lap element shall be skipped silently.
    - Both the success and the failure of an import shall be logged in the server's log channel.

#### Linking configs to seasons
- <NEW COMMAND> A "results config append" command will intake the string that IDs the points configuration to be applied to the current season. The command is only valid if there is a season whose placements are yet to be first confirmed; if there is no season, or once the season's placements have been confirmed, this command fails.
    - The command shall fail if no configuration of that name exists in the league points schema store, and shall say so. A name that was mistyped shall never be reported as attached.
    - There may be multiple points configurations attached to one season.
    - If the configuration input in "results config append" already exists in the current season, then the current season's configuration will be overwritten.
- <NEW COMMAND> A "results config detach" command will intake the string that IDs the points configuration to be removed from the current season. The command is only valid if there is a season whose placements are yet to be first confirmed; if there is no season, or once the season's placements have been confirmed, this command fails.

#### Confirming placements and points configs
- <MODIFY COMMAND> All points configurations shall be listed when the "season config-review" or the "season placements-review" command is invoked, identifying them by name. Each of the checks below shall be made by both reviews, and shall refuse both the confirmation of the configuration and the confirmation of placements.
    - Any of them that no longer exists in the league points schema store shall be named as such and reported as blocking confirmation, and the review shall not offer the placements for confirmation while one stands. The review and the confirmation shall judge this identically.
- For a given configuration and a given session type, if a higher position is configured to yield less or the same points as a lower position (e.g. 1st = 25, 2nd = 0, 3rd = 15), confirming a season's placements will fail, and the bot shall post a text message informing as to why.
    - This shall be judged on the configurations attached to the season as they stand at the moment of the first confirmation of the season's placements.
    - The "season placements-review" command shall report the same fault, naming every position at fault, and shall not offer the placements for confirmation while one stands. The review and the confirmation shall judge this identically.

#### Changing points system mid-season
- There will be a flag denoted the modified flag that is false by default.
- <NEW COMMAND> A "results amend toggle" command will enable and disable the modification of the configurations in the season points schema store. By default, this will be disabled.
    - It shall not be possible to "toggle off" if the modified flag is true.
- <NEW COMMAND> A "results amend revert" command will copy over the season's points schema store onto the modification schema store, thereby reverting all uncommitted modifications. This command is invalid if "results amend" is toggled off.
- Once "results amend revert" is run successfully, the "modified flag" is set to false.
- <NEW COMMAND> A "results amend session" command will intake the string that IDs the points configuration in the modification store to be changed, a coded enum for the "session type" (Sprint Quali, Sprint Race, Feature Quali, Feature Race), an integer signifying position, and an integer signifying the number of points gained. This command is invalid if "results amend" is toggled off.
- <NEW COMMAND> A "results amend fl" command will intake the string that IDs the points configuration to be changed in the modification schema store, a coded enum for the "session type" (Sprint Quali, Sprint Race, Feature Quali, Feature Race) and an integer signifying the number of points gained for having the shortest lap time in a session. The session types "Sprint Quali" and "Feature Quali" are invalid for this command.
- <NEW COMMAND> A "results amend fl-plimit" command will intake the string that IDs the points configuration to be changed in the modification schema store, a coded enum for the "session type" (Sprint Quali, Sprint Race, Feature Quali, Feature Race) and an integer signifying the lowest valid position for which a driver is eligible for fastest-lap points (e.g. if this is configured to 10, then if the 11th place driver gets the fastest lap, then they get no points). The session types "Sprint Quali" and "Feature Quali" are invalid for this command.
- There shall be a "results amend bulk-session" command that intakes the configuration name and a session type and opens a form taking many positions at once, on the same terms as "results config bulk-session". It writes to the modification store, and is invalid if "results amend" is toggled off.
- Once one of "results amend session", "results amend fl", "results amend fl-plimit" and "results amend bulk-session" is run successfully, the modified flag is set to true.
- <NEW COMMAND> A "results amend review" command shall be a league admin's, and will display the contents of the configurations stored in the modification store via the bot, alongside a button to approve or reject. It is seen by the member who ran it alone.
- An amendment whose staged tables are out of order shall not be approved. The rule is the one confirming a season's placements holds, in the same words: within one configuration and one session type, a lower position shall never be worth as much as or more than the position above it.
    - "results amend review" shall show the offending positions alongside the staged changes, so the fault is visible while the decision is being taken.
    - Approving such an amendment shall change nothing at all — not the season's points, not the modification store, not the amending mode — and shall say why. The staged changes remain, to be repaired.
- If approved, then the contents of the season points schema store will be overwritten by the modification store. All round results, and standings after each round result, shall be recalculated and reposted in the appropriate channels for each division. The modified flag will then be set to false, the modification store cleared, and amending mode switched off.
    - Only a round that has results shall be reposted. A round not yet raced has nothing posted for it and shall have nothing posted for it by the recalculation, its standings included.
    - Each round shall be reposted under the state that round has reached, as the "results standings sync" and "results rounds sync" commands do. Amending the points of a season shall not move a round to a different state nor label it as though it had.
- An amendment shall not be approved unless everything its approval will do can be done. Either the whole of an approval succeeds or none of it does; there is no partial approval.
    - Before the season's points are overwritten, the bot shall establish that every division's configured results and standings channels exist and can be posted to, and — where the attendance module is enabled — that the same holds of its attendance channel, and of its verdicts channel where an autosack or autoreserve threshold is set. A channel a division has not configured is not a fault.
    - Where any of it cannot be done, the approval shall be refused and shall change nothing at all: not the season's points, not the modification store, not the amending mode.
    - "results amend review" shall name the division and the channel at fault alongside the staged changes, so the fault is visible while the decision is being taken, and shall say which command repairs it.
    - The approval shall be refused, and change nothing, while any division of the season has an amendment of a round's results open, whose corrections the reposts would otherwise publish before they are approved. "results amend review" shall name the round being amended and its channel alongside the staged changes. Decided 2026-09-21.
    - The attendance recalculation an approval performs shall be applied entire or not at all, the running totals of every later round included.
    - The attendance sanctions the recalculation sets off are the one exception: a sanction that does not apply shall not undo the approval. The reply to the approval shall list each such sanction and the "attendance sync" command that finishes it, as the attendance module specification describes.
- An approval shall not be recorded as a success before the reposting it claims has been done.
- If rejected, nothing happens. The modification store will remain as it is, and the amending mode will remain active.

#### Listing and viewing configs
- <NEW COMMAND> A "results config list" command shall list the points configurations a store holds. There is one mandatory input, the store to read. For each configuration it shall name the session types that carry at least one points entry, so that a configuration which has been created but never filled in is distinguishable from a complete one; such a configuration shall be reported as holding no entries rather than omitted.
- <NEW COMMAND> A "results config view" will view a points configuration. There are two mandatory inputs, the store to read and the name/ID of the points configuration, and one optional input, the session type whose points configuration is to be posted; if this optional parameter is omitted, then the configuration for all sessions pertaining to the input name/ID shall be posted.
- **Both commands take the store as a mandatory input with no default**, that store being either the league points schema store or the current season's own. A season takes its copy of each attached configuration when its placements are first confirmed, and the two stores diverge from that moment; a command that inferred the store would therefore report figures the league does not race for, so the choice is the caller's.
- Reading the league points schema store requires no season to exist. Reading the season's store when there is no season shall be refused, and the refusal shall name the league points schema store as the alternative.
- When listing points configuration for any session, if all positions beyond a certain point yield 0 points, then they shall all be listed as "xth+" to prevent repetition.

### Submitting round results
- At the scheduled time of every round, the bot will create a new channel for the division that had just had its round, in the category of the interaction channel, notifying the interaction role and no other role to input the results of each session applicable to the round in the following order: Sprint Quali, Sprint Race, Feature Quali, Feature Race.
    - If the round type is not Sprint, then Sprint Quali and Sprint Race will be omitted.
    - Each round will be requested in order; i.e. the user will have to first input the Sprint Quali's results exclusively, then Sprint Race, etc.
- The bot shall read the inputs of the league manager to create the data entry for the results of the session. The expected format depends on the type of session, but will always require Position, Driver, and Team.
- For the driver column, a driver holding a committed placement in that division must be tagged. If the driver holds no committed placement in the division, or if there is no driver tagged at all, then the input will fail.
- For the team column, the team's shorthand must be typed. Decided 2026-09-22 (#381): a team is named by its shorthand and by nothing else, a role being only what its drivers are granted. A role mention in the column, like "@everyone", a mention of a member or a Discord ID typed out, shall be refused saying what it is and that a team is named by its shorthand.
    - The shorthand shall be read without regard to case, as naming the team of the division that holds it, and the result shall be recorded against that team. A team whose shorthand or role is changed shall keep every result recorded before the change (#375).
    - A team holding no role shall not be named at all, as it never has been: its drivers are outside the division's submission on the same terms.
- The team tagged in the Team column shall never be the reserve team. A reserve stands in for a team's car and is recorded under that team; the reserve team fields no cars of its own.
- No more than two drivers shall be recorded under any one team within a single session, counting a reserve standing in for that team against its two.
- If the results input for any session within a round are not valid, then they shall be requested once more.
- There is a special reserved input for every session which is "CANCELLED". This allows users to not submit results for sessions that were not run due to whatever issues.
- All inputs pertaining to round results must be logged in the log channel configured for the server. The season number, division and round number shall be explicited for each raw result input logged for easy search.
- Once the results introduced to a given session are attributed, the bot shall post buttons, each containing the name of a points configuration in the season, for the user to choose one. This will be saved together with the results of a session. So for each session, there's one chosen configuration from which to get the points-per-position information. This information shall be persisted.
    - Where exactly one configuration is attached to the season, it shall be chosen without asking and the choice stated.
- A driver shall be recorded under one team across every session of a round; see "One driver, one team, for the whole of a round" below.
- The channel shall be private to the server's admin role, and shall be deleted once the round is settled.
- <MODIFIED COMMAND> There is a "round cancel" command initially implemented in the scope of the weather module. Its functionality shall be enhanced to also cancel the request for round results specified by the first bullet point. If this request for round results has already been triggered, the "round cancel" command will fail. It shall equally fail once any results exist for the round.
- Where a round, a division or a season is cancelled, the bot shall post to the division's results channel a note that no results shall be posted for that round, or no further results for that division or season. The note shall be posted silently, mentioning nobody and notifying nobody, the notification being the attendance module's to carry, and only while the module is enabled. Decided 2026-09-19 (#175).

#### The format of an amendment
- A re-insertion through "round results amend" shall take the same format as a first submission, without further columns. Decided 2026-09-20 (#345), withdrawing the two sanction columns that carried the post-race and appeal penalties.
    - An amendment replays the round's report and appeal stages, where each sanction is kept, changed or removed with its justification and its author intact. A sanction shall not be re-entered as a column of the classification.
    - A paste carrying the withdrawn columns shall be refused, and the refusal shall say that sanctions are now reviewed rather than pasted.

#### Sprint Quali and Feature 
- The expected format for Race results is "Position, Driver, Team, Tyre, Best Lap, Gap", with each line representing a different player's result.
    - An example input would be:
        1, @Just Some Guy, @Mercedes-AMG Petronas F1 Team, Soft, 1:11.606, N/A
        2, @Yet Another Guy, @Oracle Red Bull Racing, Soft, 1:11.645, +0.039
        3, @Making Up Stuff, @Stake F1 Team Kick Sauber, Soft, 1:11.808, +0.202
        4, @Running Out Of Ideas, @Oracle Red Bull Racing, Soft, 1:11.839, +0.233
        5, @Last One, @Scuderia Ferrari HP, Medium, 1:11.962, +0.356
        6, @REALLY The Last One, @Atlassian Williams Racing, N/A, N/A, N/A
- Qualifying results are considered valid if the expected format is met for all of the lines, and if they meet ALL of the following criteria:
    - The positions denoted are in descending order (1st to last) and continuous (no gaps);
    - The entries are ordered by outcome: those that set a valid lap first, then DNF, then DNS, then DSQ;
    - All drivers are assigned to the division of the round;
    - The shorthand in the Team column of every line does indeed name one of the teams;
    - For every entry, the driver in the Driver column is assigned to the team identified in the Team column OR is assigned to the reserve team;
    - For every entry, the Tyre column either records no compound at all — being empty or "N/A" — OR names one of the five compounds a session may be run on;
        - The five are "Soft", "Medium", "Hard", "Intermediate" and "Wet", and there is no sixth. They are a closed set the bot itself defines and no league chooses.
        - Each is accepted under any of its spellings, matched without regard to case, spacing or punctuation: "Soft", "Softs" or "S"; "Medium", "Mediums" or "M"; "Hard", "Hards" or "H"; "Intermediate", "Intermediates", "Inter", "Inters" or "I"; "Wet", "Wets", "ExWets" or "W".
        - The compound recorded shall be the canonical name of the five, whichever spelling was submitted.
        - A compound outside the set shall be rejected, naming the five, rather than recorded.
        - An entry recording no compound is not an error. A tyre is a value the submission of a session need not carry.
    - For every entry, the Best Lap column either contains the strings "DNS", "DNF" or "DSQ" OR follows one of the following formats:
        - "seconds.milisseconds";
        - "minutes:seconds.milisseconds";
        - "hours:minutes:seconds.milisseconds".
    - For all entries aside the one in 1st position, the Gap column for the positions either contains the strings "N/A" OR follows one of the following formats:
        - "seconds.milisseconds";
        - "minutes:seconds.milisseconds";
        - "hours:minutes:seconds.milisseconds";
        - "+seconds.milisseconds";
        - "+minutes:seconds.milisseconds";
        - "+hours:minutes:seconds.milisseconds".
- For the 1st position, the Gap column's input is ignored entirely.
- For any given entry, if the Best Lap column contains "DNF" but the Gap column contains a valid value, the Best Lap for that entry shall be calculated by adding the Gap to the Best Lap of the 1st position driver.

#### Sprint Race and Feature Race
- The expected format for Race results is "Position, Driver, Team, Total Time, Fastest Lap, Time Penalties", with each line representing a different player's result.
    - An example input would be:
        1, @Just Some Guy, @Mercedes-AMG Petronas F1 Team, 46:23.569, 1:14.523, 0.000
        2, @Yet Another Guy, @Oracle Red Bull Racing, +5.321, 1:14.232, 3.000
        3, @Making Up Stuff, @Stake F1 Team Kick Sauber, +1:09.321, 1:14.332, 13.000
        4, @Running Out Of Ideas, @Oracle Red Bull Racing, +1 Lap, 1:14.300, 0.000
        5, @Last One, @Scuderia Ferrari HP, DNF, 1:15.098, 10.000
        6, @REALLY The Last One, @Atlassian Williams Racing, DSQ, N/A, 0.000
- Race results are considered valid if the expected format is met for all of the line, and if they meet ALL of the following criteria:
    - The positions denoted are in descending order (1st to last) and continuous (no gaps);
    - The entries are ordered by outcome: finishers on the lead lap first, then lapped drivers, then DNF, then DNS, then DSQ;
    - Among the lapped drivers, the number of laps behind does not decrease as the position increases;
    - All drivers are assigned to the division of the round;
    - The shorthand in the Team column of every line does indeed name one of the teams;
    - For every entry, the driver in the Driver column is assigned to the team identified in the Team column OR is assigned to the reserve team;
    - For the entry in 1st position, the total race time follows one of the following formats:
        - "seconds.milisseconds";
        - "minutes:seconds.milisseconds";
        - "hours:minutes:seconds.milisseconds".
    - For all entries aside the one in 1st position, the total race time for the positions either contains the strings "DNS", "DNF" or "DSQ" OR follows one of the following formats:
        - "seconds.milisseconds";
        - "minutes:seconds.milisseconds";
        - "hours:minutes:seconds.milisseconds";
        - "+seconds.milisseconds";
        - "+minutes:seconds.milisseconds";
        - "+hours:minutes:seconds.milisseconds";
        - "x laps" (also Laps);
        - "+x laps" (also Laps).
    - The Time Penalties column follows the "seconds.milisseconds", "minutes:seconds.milisseconds" or "hours:minutes:seconds.milisseconds" format.
- Every time in either submission format shall be written with a dot and exactly three digits of milliseconds after it, and seconds and minutes written after a colon shall be two digits under sixty. It is the one form every time the bot reads is written in, a driver's signup lap times included. Decided 2026-09-21 (#362).
- This is not a hard requirement, but it is presumed that the Time Penalties input have already been added to the Total Time, so no further calculations are required.
- In preparation of further functionality, the data tables on which race results are saved shall possess two extra columns for Post-Stewarding Total Time and Post-Race Time Penalties.
- It is always presumed that the Total Time column already includes the time noted in the Race Time Penalties
- If the Total Time column specifies "DNS", "DNF" or "DSQ", then these drivers shall not be eligible to receive points
- If the Total Time column specifies "DNS", "DNF" or "DSQ", the Fastest Lap column may be ignored if it does not follow the "x:xx.xxx" or "xx.xxx" formats.
- In all other cases, drivers are eligible for scoring.
- The fastest-lap bonus is normally awarded to the driver whose Fastest Lap time is the lowest across all valid entries in the submitted block. In the rare case where two or more drivers share the exact same fastest-lap time, an optional **FL override header** may prepended to the submission block on its own line, in the format `FL: <@user_id>`, to explicitly designate the fastest-lap holder. The override bypasses time-based comparison entirely and does not change or replace any of the per-driver Fastest Lap fields. If the override names a driver not present in the submitted results, the submission is rejected. If no override is provided and a tie occurs, the driver whose row appears first in the submission (i.e. with the lower finishing position) receives the bonus implicitly. The FL override is race-only; it has no effect on qualifying submissions.

#### One driver, one team, for the whole of a round
- A driver shall be recorded under one team across every session of a round. A submission recording a driver under a team different from the one another active session of that round already records for them shall be rejected, naming the driver, the team already recorded by its name, and the session recording it.
- The criteria above tie a driver seated in a team to the team they are seated in, but are applied to one session at a time and against the seats as they stand at that moment, and a driver of the reserve team is tied to no team at all, being free to stand in for any. Neither closes this case, and a reserve standing in for two different teams within one round would otherwise be recorded.
- The constructor standings graphic places each driver who drove a team's cars in a round upon one of those cars, and cannot place one driver upon the cars of two teams. That is guaranteed here, at the moment the input is given and where it can be named and corrected, rather than discovered when a graphic is drawn.

### After submission of results
- Once the results for all sessions in a round are submitted and validated, the results shall be output into the configured results channel for the division in a prettier table-like format. The results to be output are as follows:
    - Qualifying sessions: Position, Discord display name, Team, Tyre, Best Lap, Gap, Points Gained
    - Race sessions: Position, Discord display name, Team, Total Time, Fastest Lap, Time Penalties, Points Gained (if any)
- Every posting of results or standings shall name a team by its name, never by a mention of its role.
- The points conferred to each driver will be determined by the points configuration applied to each session: points are attributed by finishing position and, depending on the settings of the configuration (points for fastest lap and placement limit for fastest lap points), for the lowest lap time as well.
- A driver who did not start, or was disqualified, receives neither position points nor the fastest-lap bonus. A driver who did not finish receives no position points but remains eligible for the bonus.

#### The three states of a round's results
- Every results and standings posting shall carry a label naming the state the round has reached, and each new state shall replace the posting of the one before it rather than adding to it.
    - **Provisional Results** — every session submitted, no sanction applied.
    - **Post-Race Penalty Results** — stage one of the review committed.
    - **Final Results** — stage two of the review committed. The round is settled.
- A round re-submitted through the resubmit button shall be published as provisional again, marked as amended.
- Where a session was cancelled, the results channel shall carry a note to that effect in place of a table.

#### Corrections to the rendering of the table
- A time penalty shall be rendered in seconds, signed, and to the precision with which it was recorded: a penalty of a whole number of seconds carries no decimal part, and one carrying a fraction of a second is rendered to three decimal places. Five seconds is "+5s" and five and a half "+5.500s". A penalty is never rounded to a whole second for display, and never rounded away from zero.
    - This is the rendering the image module re-presents. The graphic derives nothing of its own, so the table and the graphic shall render a penalty by one and the same code and cannot differ.
    - Corrected 2026-08-13. The table formerly rendered every penalty column by an integer division of its milliseconds, showing five and a half seconds as "+5s", a fraction below one as "+0s", and a credit of five and a half seconds applied on appeal as "-6s".
- The best lap column of a qualifying table shall be emptied for an entry that was classified and set no time.
    - An entry that did not finish, did not start or was disqualified carries its outcome in that column instead, whatever lap may have been recorded for it: the outcome displaces the lap rather than standing in for a missing one.
    - Corrected 2026-08-13. The column formerly fell back to the outcome wherever no lap was held, so a classified entry with no lap printed the word "CLASSIFIED" where a lap time belongs, while a disqualified entry holding a lap printed the lap.

### Revising results
#### Penalties and appeals are settled in the submission channel
Penalties are not applied by a command. Once every session of a round has been submitted or cancelled, the submission channel remains open and carries the round through two review stages before it closes. Each stage is worked from buttons, and the results are published under three headings in turn: provisional, post-race penalty, and final. These are the labels the league reads on the posted results; the round's own lifecycle states, which the two reviews move it through, are set out in the [core specification](core_specification.md).

- Any message posted in the submission channel while it is in a review stage shall be deleted, with a reply saying why. A resubmission in progress is exempt.
- Every button in both stages shall be usable by holders of the server's interaction role.
- A round in which every session was cancelled shall skip both stages: the channel closes and no standings are computed for that round.

**Stage one — post-race penalties.** The prompt shall carry:
- **Add Penalty** — asks which session, then takes the driver as a mention or user ID, the sanction, a description and a justification. Both texts are mandatory and both are published in the verdict.
    - A description or justification holding a role mention, "@everyone" or "@here", in any case, shall be refused, saying which text holds it. A mention of a driver shall stand. Decided 2026-09-21 (#204).
    - A description or justification holding an emoji, whether one of the server's own or a standard one, shall be refused likewise, saying which text holds it and naming the emoji. A symbol that is text by default, such as a tick, a star or an arrow, is no emoji. Decided 2026-09-21 (#204).
    - A description or justification holding Discord markup shall be refused likewise, saying which text holds it and quoting the markup found: formatting (bold, italics, underline, strikethrough, code, spoilers), a heading, quote or subtext line, a masked link, and a mention of a channel, a time or a command. A list and a bare link are no markup. Decided 2026-09-21 (#204).
    - A verdict shall notify nobody but the people it mentions, whatever its texts hold. A role mention posted in one shall notify none of the role's holders, and "@everyone" or "@here" nobody.
    - The sanction shall be either "DSQ" or a whole number of seconds, positive or negative. Fractions of a second shall be rejected.
    - A sanction of no seconds shall be rejected, the refusal pointing to no further action. Decided 2026-09-24 (#138).
    - A driver may instead be given **no further action**: a finding that the incident was investigated and no penalty follows. It shall alter no classification, and its verdict shall name the driver and the incident and say that no penalty follows, never reading as a sanction. Decided 2026-09-24 (#138).
        - No further action shall be given by entering "NFA", in any case, in place of the sanction. Decided 2026-09-24.
        - Its verdict shall carry "No further action" where a sanction's verdict carries the sanction, in the textual announcement and on the graphic alike. Decided 2026-09-24.
    - "DSQ" invalidates the entry, which is ranked last in that session.
    - A number of seconds is added to the driver's total race time.
    - For "Sprint Qualifying" and "Feature Qualifying", only "DSQ" and no further action are accepted.
    - A negative sanction shall be rejected where its magnitude exceeds the time penalties the driver already carries in that session, counting anything staged in the same review, or where it would produce a negative total race time.
    - A driver not present in the chosen session's results shall be rejected.
- **No Penalties / Confirm** — proceeds with no penalty applied. Where penalties are staged, it shall first ask for confirmation that they are to be discarded. The staged attendance pardons shall be kept.
- **Approve** — proceeds with what is staged. It shall be unavailable while no penalty is staged, whatever attendance pardons are.
- **Resubmit Initial Results** — discards the staged penalties and attendance pardons, takes down the prompt and any approval message, and restarts collection in the same channel from the first session.
    - The resubmission shall supersede the round's submitted results rather than delete them. The submitted results shall stand, published and counted, until every session has been submitted again, and shall then be replaced all at once. Decided 2026-09-17 (issue #210).
    - Team agreement across the sessions of the round shall be checked against the sessions of the resubmission, not the results being replaced.
    - The resubmission shall carry a button labelled "Cancel", usable by league managers. Pressing it shall end the resubmission, keep the submitted results, and post the stage-one prompt again. The staged penalties and pardons it discarded shall not be restored. Decided 2026-09-17.
    - A resubmission that fails before its results are saved shall end as a cancelled one does, and say so in the channel.
    - A restart during a resubmission shall keep the submitted results and restore stage one, saying in the channel that the sessions entered so far were lost.
    - The resubmission shall be refused where the submission channel no longer exists, and nothing shall be discarded.
- **Attendance Pardon** — stages an attendance pardon, per the attendance module specification.
- One **Remove** button per staged penalty, and one per staged attendance pardon.

**Approve** on the prompt shall commit stage one at once. **No Penalties / Confirm** shall instead post an approval message carrying **Make Changes**, which returns to staging with the list intact, and **Approve**, which commits. Committing shall apply every penalty, recompute positions, times and points for the sessions affected, republish the round's results and standings under the post-race penalty state, recompute the standings of every later round, and post one verdict per decision to the division's verdicts channel.

- **A control of stage one shall act only while stage one is the round's current stage.** Decided 2026-09-23 (#402). Once stage one is committed, while a resubmission is collecting, or once a newer prompt has replaced the one pressed, a button or form of the stage shall be refused, saying why, and shall change nothing.
- Committing stage one shall take down its prompt and its approval message.
- A second commit pressed while the first is still being applied shall be refused.
- The approval message shall be withdrawn when anything staged changes or **Make Changes** is pressed, and a second one shall replace the first. Its buttons shall act only on the approval message last posted.
- The approval message shall list the attendance pardons staged, which its **Approve** grants, and shall not say that nothing is staged while any is. Their justifications stay in the log channel (#403).

**Stage two — appeals.** Committing stage one shall post a second prompt to the same channel, carrying **Add Correction**, **No Changes / Confirm**, **Approve** and one **Remove** per staged correction. A correction takes and validates the same values as a penalty, no further action included, and is the surface for overturning one.

Approving stage two shall apply any staged corrections, republish the round's results and standings under the final state, post a verdict for each correction, recompute the standings of every later round, mark the round final, and delete the submission channel. There shall be no second confirmation on this stage.

#### Amending a submitted session
- <NEW COMMAND> A "round results amend" command shall be a league admin's, amending a round already final overwriting the classification the league raced with nothing to put it back. It shall intake a division name and round number mandatorily, and optionally, a session name as well. Where the session is omitted, the bot shall ask which sessions to amend, and any number of the round's sessions may be chosen.
    - The command shall be refused for a round that has not been marked final by both review stages.
    - The amendment shall be carried out in a channel created for the purpose, private to the server's admin role, carrying a button to abandon it.
    - The points configuration recorded for each amended session shall be kept where it is still attached to the season; otherwise the user shall be asked to choose one for it.
- **An amendment shall cover as many of a round's sessions as the user chooses.** Decided 2026-09-21. A round's reports and appeals are reviewed together, so the amended sessions shall share one amendment: each shall be entered in turn, in running order, and nothing written until the last is in; their reports and appeals shall be reviewed together; and the division rebuilt once.
    - A paste that is refused shall end the whole amendment, the pastes already accepted included, and nothing shall be written. The sessions are not asked for again one by one: the user prepares every classification before starting. Decided 2026-09-21.
    - Each classification, and each choice of points configuration the user is asked for, shall be awaited for a set period. Where it passes, the amendment shall end as a refused paste does, and the user who ran the command shall be told that it expired and may be run again (#135).

- **An amendment shall replay the round's lifecycle in three stages.** Decided 2026-09-20 (#345). A round is amended the way it was raced, so that a corrected round is indistinguishable from one submitted correctly the first time.
    - **Stage one — the classification.** The corrected results shall be re-inserted. Nothing shall be posted.
    - **Stage two — the reports.** The reports the round already carries shall be shown back, each able to be kept, changed or removed, and further ones added. The round's attendance pardons shall be amendable in this stage, and in this stage alone. The first pass's resubmission of the whole round shall not be offered; a classification is redone by abandoning the amendment and starting another.
    - **Stage three — the appeals.** The appeals the round already carries shall be shown back on the same terms.
    - Approving a stage of an amendment shall not move the round. The round is already final: it shall not be returned to either review state, its division shall not be finished a second time, and its season shall not be wound down again.
    - A stage approved without change shall leave the round's decisions exactly as they stood.
    - A stage shall be approved once. A second approval of the same stage shall change nothing.
    - Approving an amendment's reports shall take down its prompt and approval message. A control that would change a report or an attendance pardon after that shall be refused, saying why. Decided 2026-09-23 (#402).
- **A division shall have one amendment open at a time.** Decided 2026-09-21. An amendment of any round of a division in which another amendment is still open shall be refused, and the refusal shall name the round, the sessions and the channel of the open one. The last stage of an amendment reposts the whole division, and would otherwise publish the other's unapproved classification.
- **A division with an amendment open shall commit nothing else.** Decided 2026-09-21. While an amendment of one of its rounds is open, the submission of any other round of the division, first or resubmitted, shall refuse every commit: a session's results, a session entered as cancelled, and the approval of either review stage. The "results standings sync" and "results rounds sync" commands shall be refused for the division likewise. The refusal shall name the round being amended and its channel, and the submission shall remain open for the user to try again once the amendment has ended. An amendment's first stage recalculates the division from its unapproved classification, which the other round's postings would otherwise publish.

- **A verdict shall survive an amendment.** A penalty or appeal verdict shall follow its driver onto the amended classification, keeping its justification, its author and the time it was given. An amendment shall not discard the record of why a driver's result changed.
    - Where a driver carrying a verdict is absent from the corrected classification, the amendment shall be refused, and the refusal shall name the driver.

- **An amendment shall touch the sessions it amends and no other.** Decided 2026-09-20 (#345). A round's other sessions keep the decisions they already carry: their reports and appeals are not reopened, not rewritten and not re-applied. A session's decisions are reviewed by including that session in the amendment.

- **An amendment shall publish nothing until its last stage is approved.** The classification is recorded as it is entered, but the league continues to read the round it raced until the reports and appeals have been settled — a round part-way through an amendment shall not be published as provisional.

- **An amendment shall rewrite a round's decisions, not add to them.** Decided 2026-09-20 (#345). Approving a stage shall leave the round carrying exactly the reports, appeals and attendance pardons that stage held — no more and no fewer.
    - The round's existing records shall be removed before the approved set is written, so that a decision kept is kept once rather than twice, and a decision removed in the stage is removed from the round.
    - Every decision shall be shown back in its stage before this happens, so that what is written out again carries the justification, the author and the time it was originally given.
    - It follows that amending a round twice shall leave it as the second amendment settled it, and shall not compound the sanctions of the first.

- **An amendment not carried through shall be undone.** Decided 2026-09-20 (#345). The first stage commits the corrected classification, so an amendment whose later stages are never approved would otherwise leave a round scored from one classification and published from another.
    - Where the report and appeal stages are not approved within a set period, the round shall be put back as it stood before the amendment began, and the league told in the log channel that the amendment lapsed and may be run again. Nothing shall be reposted, nothing having been posted.
    - The period shall run from the moment the corrected classification is written and cover both stages together. Approving the report stage shall not extend it: the user prepares the review beforehand. Decided 2026-09-21.
    - Abandoning an amendment with its button, a restart during it, or a failure part-way through a stage shall undo it on the same terms rather than leave it part-made.
    - Once its last stage has been approved, an amendment can no longer be abandoned.
    - An amendment abandoned before its classification was inserted has nothing to undo, and shall simply end.

- **An amendment shall rebuild everything the division's channels show**, in the order a league reads them: the results, the standings, the attendance sheet, the round's report verdicts, then its appeal verdicts.
    - Every round of the division shall be reposted, in round order, and not the amended round alone — a repost being a new message, reposting one round alone would leave the channel out of sequence.
    - All of a round's verdicts shall be announced, in order, and not only those the amendment changed.
    - In every channel the replacement shall be posted before what it replaces is removed, so that a failure part-way leaves the league what it already had.
    - A round's superseded verdict announcements, and the banner heading them, shall be removed only once every one of that round's replacements has been posted. Where one could not be, the originals shall be left standing, and the league told which, with a way to find each.
    - A round left with no verdict at all shall have its superseded announcements and their banner removed likewise. A banner that also heads an attendance sanction card shall be kept, the card being no verdict and staying where it is. Decided 2026-09-21.
    - The attendance sheet shall be reposted once, against the round the running totals stand at, there being one live sheet rather than one per round.
    - Where any of it could not be posted, the league shall be told rather than left to find out from a channel out of order.

#### A verdict that was not announced shall be reported
Every penalty and every appeal correction is announced in the division's verdicts channel. The announcement is the only thing that tells a driver why their classification changed, so a verdict that never reaches the channel shall be reported rather than passed over.

- Each verdict that could not be announced shall be named in the log channel, under an entry marked `Incomplete`, and the league manager who approved the review shall be told the same in their own reply.
- A division with no verdicts channel set shall be reported, not skipped. A verdicts channel is one of the three a division shall have before its season's placements are confirmed, so a round reaching a verdict without one is a fault.
- One verdict that cannot be announced shall not stop the rest. Each is attempted, and each that fails is named with the driver it was owed to.
- **A verdict announcement shall record the message it was posted in**, so that it can be replaced or removed. Decided 2026-09-20 (#345, #189), withdrawing the rule that the bot shall not announce a decided verdict a second time.
    - An amendment replaying a round shall remove the announcements of the classification it replaces and announce the round's verdicts afresh.
    - A superseded announcement that is left standing shall be named, and the league told that it is still standing, rather than a clean replacement being claimed.
    - Where a verdict could not be announced at all, the report shall still direct the manager to post the decision themselves once the cause is repaired.
    - Disabling the module once the season's placements have been confirmed shall remove the season's announcements, as set out at the head of this specification. Decided 2026-09-21 (#189).
- An automatic attendance sanction is a verdict for this purpose and is no exception to the rule above. Re-running the sanctions shall not announce one that already applied, the driver no longer being a candidate, so such a sanction shall be reported as applied but not announced.
- The decisions themselves stand regardless. The penalties are applied, the corrections hold, and only the announcement is outstanding.

#### Attendance that was not recorded shall be reported
Approving a penalty review records who attended the round and awards the attendance points. Both write to the league's record, and that record feeds the auto-reserve and auto-sack thresholds.

- Where either fails, the league manager and the log channel shall be told, under an entry marked `Incomplete` and distinct from the one the sanctions raise, ending with the command that recalculates the round.
- The distinction is between a record that is wrong and a posting that is missing: a failed sheet or announcement leaves the record right, and shall not be reported as though the record were at fault.

#### A republication that does not land shall be reported
Committing either review stage, and amending a submitted session, republish the round's results and the standings of every later round. Where any of that could not be posted, the league shall be told rather than left to find out from an empty channel.

- The republication shall establish that a channel can receive a posting before it deletes what is already there. Where it cannot, what is already posted shall be left standing, so the worst outcome is that the league keeps the older version rather than losing both.
- Everything that could not be posted shall be named in the log channel, under an entry marked `Incomplete` rather than `Success`, ending with the commands that finish the job once the cause is repaired.
- Where the revision was worked from a button, the league manager who approved it shall be told the same in their own reply, in place of a plain success.
- A channel a division has never been given shall not be reported. Nothing is posted for it and nothing is owed.
- The revision itself shall go through regardless. The penalties are applied, the corrections stand, the round's state moves on and the championship is recalculated; only the posting is outstanding.


## Standings
### Design
- Standings have two forms: driver standings and team standings. In both, the ranking criteria shall be as follows, in order:
    - 1st - Total number of points (higher is better);
    - 2nd - If equal on points, the number of wins (1st place finishes) is compared, with the tiebreaker being won by the one with the most wins;
    - 3rd - If equal on wins, the number of 2nd place finishes is compared, with the tiebreaker being won by the one with the most 2nd places; 
    - 4th - If equal, then 3rd place finishes is compared, and so on until a difference arises.
    - 5th - If at the end both drivers are still tied, then the first to take the highest position will win the tie-breaker (e.g. 0 1st finishes, 1 2nd finish for both drivers, first one to have gotten 2nd wins).
    - 6th - If both are still tied, an entry that has taken part in at least one session shall rank above one that has taken part in none.
    - 7th - The final tiebreak, applied where every criterion above has failed to separate two entries: alphabetically by the name of the team, with the reserve team after every named team and a driver holding no seat in the division after the reserves; alphabetically by the name of the driver within a team; and, where two drivers carry the same name or two teams the same name, the smaller identifier first — the driver's Discord user ID, or, for two teams, the team added to the division first. Drivers are ordered on the name the standings are drawn under, so that the order and the sheet agree. The name is the one resolved at the moment the standings are computed: where a driver has since been renamed, a round reposted later may place two entries tied on everything the other way about, which is accepted and is not a fault. The last step is what makes the order total: display names are not unique on Discord and two drivers can genuinely share one.
    - NOTE: For countback tiebreakers, only Feature Race sessions are relevant.
- A driver who has appeared in a division's driver standings shall remain in them for the rest of the season, whether or not they still hold a seat in the division.
- In driver standings, all drivers that have partaken in a division are ranked according to their total accrued points and finishes in each round of said division.
- In team standings, all teams are ranked according to the total points and finishes accrued by those driving their cars in each round of said division.
    - This means that, in the case of Reserve drivers who may drive for Team A in one round and Team B in another, will have their points and finishes in the first go to Team A in the standings, and to Team B in the latter.
    - A team shall stand as one entry for the whole season, whatever role it has held: a change of its role shall neither split it nor move its points.
- Both standings are recalculated after the results of each round are submitted and validated, with the points obtained in that round added to the total.
- Beyond the postings after each round, both standings shall be posted on the two occasions that bracket a season:
    - Upon the season's placements being first confirmed, an **opening classification** shall be posted to the standings channel of each division, holding every driver and every team upon nought points. It is the grid as it stands before a round has been run.
        - Nothing has been scored, so the countback above separates nobody: it is ordered by the final tiebreak alone, which is the rule stated rather than derived.
    - Upon the season completing, a **final classification** shall be posted to the same channel, holding the classification of the last round of the division for which results were posted. A division which ran no round publishes none.
- Neither posting carries message text where it is drawn as a graphic; where it is written out as text, it is headed by the phrase naming the occasion — "Opening Classification" or "Final Classification".
- Neither posting replaces a standings message nor has its ID recorded: a standings message belongs to the round it was posted for, and neither of these stands after a round. Both are posted beside the standings of the rounds, and "results standings sync" reaches neither.
- The failure of either shall never prevent a season's placements from being confirmed nor the season from completing, and the failure of one division shall not prevent the others.
- When the results of a session are amended or when penalties are applied, the standings of all rounds after the one modified (including) shall be recalculated by the bot.
- A driver's results are specific to one division. Assuming a driver participates in two different divisions, the points gained by driving in Division X are accounted for in the standings for Division X only, and their standing in Division Y is unaffected.
- Driver and Team standings are to be saved at round-scope: this makes it easier to organize information and to trace the progress of a championship. As such, the following information shall be saved for each driver and team within the standings table recorded in each round (which pertains to the state after a round):
    - Discord ID (driver standings) / the division's team (team standings)
    - Total points (so far)
    - Finishes place n
    - nth place first obtained on Round Number
        - NOTE: There will be a "Finishes place n" entry for each place a driver has finished in. If there are no entries for a given place, then it is assumed that the drivers finished 0 times in that position.
        - NOTE: There will be a "nth place first obtained on Round Number" for each place a driver has finished in. If there are no entries for a given place, then it is assumed that the drivers finished 0 times in that position.

### Detailed functionality specification for standings
- The commands of this section belong to a season being raced. Each of them — the reserves setting, the standings sync, the rounds sync, and every command of the mid-season points amendment — shall act upon the season the server holds live, and shall be refused where the season is pending completion or has ended. A completed or cancelled season is an archive and is never changed; and once every division of a season is finished or cancelled there is nothing left to repost that anybody will race under, the season's points are settled, and amending the results of a round already final is the only correction still open, which reposts of its own accord.
    - It follows that amendment mode left switched on when a season reaches Pending completion cannot be switched off again. Nothing shall prevent the season being completed on that account, and the modification store shall simply never be applied.
    - Where a repost that followed an amendment could not be delivered, the line telling a league manager how to finish it shall name a command that is actually available: the two sync commands while the season is being raced, and re-running the round's own results amendment once the season is pending completion.
- <NEW COMMAND> A "results reserves toggle" command takes the name of a division and applies to that division alone. When toggled on, drivers belonging to the Reserve team will be relevant for the driver standings, and will therefore show up in the classification. When toggled off, drivers belonging to the Reserve team will accrue points all the same, but will not show up in the driver standings. Reserves shall be shown by default.
- <NEW COMMAND> A "results standings sync" command will take as input the name of a division. It shall delete every standings message the bot holds for that division and post the standings of each round that has results afresh, in round order, each under the state that round has reached.
- <NEW COMMAND> A "results rounds sync" command will take as input the name of a division. It shall delete every session results message the bot holds for that division and post the results of every session of every round afresh, in round order, each under the state that round has reached.
- A driver whose placement is not yet committed shall not appear in any results or standings, and shall accrue nothing from a round run before it is.
- Every driver holding a committed non-reserve seat of a division shall appear in the driver standings from the outset, on zero points, whether or not they have taken part in a round. Every non-reserve team of the division shall likewise appear in the team standings.
- If a driver that was assigned only to the reserve team in a given division is then moved to a configurable team, the points they have accrued as a driver will stand all-the-same, and will be reflected on their position on the standings.


Name of commands is not mandatory, a better one may be used instead.