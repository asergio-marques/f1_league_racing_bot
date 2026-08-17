# Results and Standings module
- <COMMAND CHANGE> The results and standings module may be enabled via a "module enable" command akin to the weather and signup modules. May only be used by server admins.
- <COMMAND CHANGE> The results and standings module may be disabled via a "module disable" command akin to the weather and signup modules. May only be used by server admins.
- The results and standings module is disabled by default.

## Assigning channels to divisions
- <COMMAND CHANGE AND NEW COMMAND> When adding a division, the command shall no longer intake a weather forecast channel. Instead, there will be a new "weather channel" command, usable by trusted admins, that has as input a division name and a channel, which serves a similar purpose.
- If there is not an active season, it shall not be possible to approve the season if the weather module is enabled and not all divisions have a weather forecast channel configured.
- If there is an active season, it shall not be possible to enable the weather module if not all divisions have a weather forecast channel configured.
- <NEW COMMAND> There will be a new "results channel" command, usable by trusted admins, that has as input a division name and a channel on which race results shall be posted by the bot, formatted. May only be used by server admins.
- <NEW COMMAND> There will be a new "standings channel" command, usable by trusted admins, that has as input a division name and a channel on which standings shall be posted by the bot, formatted. May only be used by server admins.
- There shall be a "verdicts channel" command, usable by trusted admins, that has as input a division name and a channel on which penalty and appeal verdicts shall be posted by the bot. Automatic attendance sanctions are announced in the same channel.
- If the results & standings module is enabled, the approval of a season setup shall fail if any division lacks a results channel, a standings channel or a verdicts channel, or if no points configuration is attached to the season. Each missing item shall be named individually.
- If there is an active season, it shall not be possible to enable the results & standings module. It shall equally not be possible to disable it.
- Disabling the results & standings module shall disable the attendance module with it.

## Results
### Design
#### Points configurations
- There is no default points position configuration for a server (every position of every possible session gives 0 points).
- Each server has their own configuration.
- The schema of points configuration is as follows:
    - Server
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
- The design shall follow the idea that trusted users may add, remove or modify the configurations in the schema store, then attach and detach them from a season being setup via a "weak link". Once a season configuration is approved during "season review", the attached configurations' settings are copied over to the season's points schema store and remain completely independent of the server's configuration.
    - In practice, this means that any changes done while there is no season, or while a season is being setup, will be valid to any season that is approved in the future, regardless of whether the modified configuration is attached or not.
    - However, if there is an ongoing approved season, the modifications done to the configurations in the server points schema store are NOT applied to the season's own configuration of the same name.
- There shall be the possibility to amend a points system mid-season, but it will require higher permissions. Once an amending session is started (by enabling amending), a copy of the season's current points schema store will be made and placed in a "modification schema store". Any changes made will be done to this "modification store". Only upon review and approval will the settings in the modification store overwrite the points schema store of the season completely. After they are overwritten, all results and standings posted after every round of every division shall be reposted taking into consideration the new values.

#### Results and standings
- The schema for results and standings is as follows:
    - Server
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
- <NEW COMMAND> A "results config add" command will be made available to trusted server users that will intake a string which shall be the name of the points configuration to be saved in the server points schema store. The string will serve as the ID of the configuration.
    - Adding a points config to the server points schema store does not automatically append it to a season being setup.
- <NEW COMMAND> A "results config remove" command will be made available to trusted server users that will intake a string which is the ID of the points configuration to be removed from the server points schema store.
    - Removing a points config from the server points schema store does not automatically detach it from a season being setup.
- <NEW COMMAND> A "results config session" command will be made available to trusted server users that will intake the string that IDs the points configuration in the server points schema store to be changed, a coded enum for the "session type" (Sprint Quali, Sprint Race, Feature Quali, Feature Race), an integer signifying position, and an integer signifying the number of points gained.
- <NEW COMMAND> A "results config fl" command will be made available to trusted server users that will intake the string that IDs the points configuration to be changed in the server points schema store, a coded enum for the "session type" (Sprint Quali, Sprint Race, Feature Quali, Feature Race) and an integer signifying the number of points gained for having the shortest lap time in a session. The session types "Sprint Quali" and "Feature Quali" are invalid for this command.
- <NEW COMMAND> A "results config fl-plimit" command will be made available to trusted server users that will intake the string that IDs the points configuration to be changed in the server points schema store, a coded enum for the "session type" (Sprint Quali, Sprint Race, Feature Quali, Feature Race) and an integer signifying the lowest valid position for which a driver is eligible for fastest-lap points (e.g. if this is configured to 10, then if the 11th place driver gets the fastest lap, then they get no points). The session types "Sprint Quali" and "Feature Quali" are invalid for this command.

#### Setting many positions at once
- There shall be a "results config bulk-session" command, available to trusted server users, that intakes the configuration name and a session type and opens a form in which many positions are given at once, one "position, points" pair per line. Blank lines are skipped; a position must be a positive integer and its points non-negative; where a position appears more than once the last value given wins and the override is reported. Every valid pair is applied and every rejected line is reported back, so a partly-wrong input still applies what was right.
- There shall be a "results config xml-import" command, available to trusted server users, that intakes the name of an existing configuration and an XML payload, given either as pasted text or as an attached file. One payload may carry several session types, each with any number of positions and a fastest-lap bonus with an optional position limit.
    - The import is applied atomically: any validation failure leaves the configuration untouched.
    - Positions not named in the payload are left as they stand, so a partial import is safe.
    - An unknown session type, negative points, a position below 1, or a fastest-lap element on a qualifying session shall be rejected. Points within one session block must not increase as position increases, and two positive values may not tie.
    - A session block carrying neither a position nor a fastest-lap element shall be skipped silently.
    - Both the success and the failure of an import shall be logged in the server's log channel.

#### Linking configs to seasons
- <NEW COMMAND> A "results config append" command will be made available to trusted server users that will intake the string that IDs the points configuration to be applied to the current season. The command is only valid if there is a season being setup; if there is no season or if there is an active (approved) season, this command fails.
    - There may be multiple points configurations attached to one season.
    - If the configuration input in "results config append" already exists in the current season, then the current season's configuration will be overwritten.
- <NEW COMMAND> A "results config detach" command will be made available to trusted server users that will intake the string that IDs the points configuration to be removed from the current season. The command is only valid if there is a season being setup; if there is no season or if there is an active (approved) season, this command fails.

#### Season approval and points configs
- <MODIFY COMMAND> All points configurations shall be listed when the "season review" command is invoked, identifying them by name.
- For a given configuration and a given session type, if a higher position is configured to yield less or the same points as a lower position (e.g. 1st = 25, 2nd = 0, 3rd = 15), the approval of a season setup will fail, and the bot shall post a text message informing as to why.

#### Changing points system mid-season
- There will be a flag denoted the modified flag that is false by default.
- <NEW COMMAND> A "results amend toggle" command will be made available to trusted server users that will enable and disable the modification of the configurations in the season points schema store. By default, this will be disabled.
    - It shall not be possible to "toggle off" if the modified flag is true.
- <NEW COMMAND> A "results amend revert" command will be made available to trusted users that will copy over the season's points schema store onto the modification schema store, thereby reverting all uncommitted modifications. This command is invalid if "results amend" is toggled off.
- Once "results amend revert" is run successfully, the "modified flag" is set to false.
- <NEW COMMAND> A "results amend session" command will be made available to trusted users that will intake the string that IDs the points configuration in the modification store to be changed, a coded enum for the "session type" (Sprint Quali, Sprint Race, Feature Quali, Feature Race), an integer signifying position, and an integer signifying the number of points gained. This command is invalid if "results amend" is toggled off.
- <NEW COMMAND> A "results amend fl" command will be made available to trusted server users that will intake the string that IDs the points configuration to be changed in the modification schema store, a coded enum for the "session type" (Sprint Quali, Sprint Race, Feature Quali, Feature Race) and an integer signifying the number of points gained for having the shortest lap time in a session. The session types "Sprint Quali" and "Feature Quali" are invalid for this command.
- <NEW COMMAND> A "results amend fl-plimit" command will be made available to trusted server users that will intake the string that IDs the points configuration to be changed in the modification schema store, a coded enum for the "session type" (Sprint Quali, Sprint Race, Feature Quali, Feature Race) and an integer signifying the lowest valid position for which a driver is eligible for fastest-lap points (e.g. if this is configured to 10, then if the 11th place driver gets the fastest lap, then they get no points). The session types "Sprint Quali" and "Feature Quali" are invalid for this command.
- There shall be a "results amend bulk-session" command, available to trusted users, that intakes the configuration name and a session type and opens a form taking many positions at once, on the same terms as "results config bulk-session". It writes to the modification store, and is invalid if "results amend" is toggled off.
- Once one of "results amend session", "results amend fl", "results amend fl-plimit" and "results amend bulk-session" is run successfully, the modified flag is set to true.
- <NEW COMMAND> A "results amend review" command will be made available to trusted server users, which will display the contents of the configurations stored in the modification store via the bot, alongside a button to approve or reject.
- If approved, then the contents of the season points schema store will be overwritten by the modification store. All round results, and standings after each round result, shall be recalculated and reposted in the appropriate channels for each division. The modified flag will then be set to false, the modification store cleared, and amending mode switched off.
- If rejected, nothing happens. The modification store will remain as it is, and the amending mode will remain active.

#### Viewing configs after season approval
- <NEW COMMAND> A "results config view" will be made available to trusted server users to view the many points configurations applied to the current season. There is one mandatory input, the name/ID of the points configuration, and one optional input, the session type whose points configuration is to be posted; if this optional parameter is omitted, then the configuration for all sessions pertaining to the input name/ID shall be posted.
- While the season is in setup, the command reads the server points schema store; once the season is approved, it reads the season's own store.
- When listing points configuration for any session, if all positions beyond a certain point yield 0 points, then they shall all be listed as "xth+" to prevent repetition.

### Submitting round results
- At the scheduled time of every round, the bot will create a new channel adjacent to the results channel of the division that had just had its round, notifying the trusted user role to input the results of each session applicable to the round in the following order: Sprint Quali, Sprint Race, Feature Quali, Feature Race.
    - If the round type is not Sprint, then Sprint Quali and Sprint Race will be omitted.
    - Each round will be requested in order; i.e. the user will have to first input the Sprint Quali's results exclusively, then Sprint Race, etc.
- The bot shall read the inputs of the trusted user to create the data entry for the results of the session. The expected format depends on the type of session, but will always require Position, Driver, and Team.
- For the driver column, a driver that is assigned to that division must be tagged. If the driver is not assigned to the division, or if there is no driver tagged at all, then the input will fail.
- For the team column, a team role must be tagged as well. This will allow easy identification of the team.
- The team tagged in the Team column shall never be the reserve team. A reserve stands in for a team's car and is recorded under that team; the reserve team fields no cars of its own.
- No more than two drivers shall be recorded under any one team within a single session, counting a reserve standing in for that team against its two.
- If the results input for any session within a round are not valid, then they shall be requested once more.
- There is a special reserved input for every session which is "CANCELLED". This allows users to not submit results for sessions that were not run due to whatever issues.
- All inputs pertaining to round results must be logged in the log channel configured for the server. The season number, division and round number shall be explicited for each raw result input logged for easy search.
- Once the results introduced to a given session are attributed, the bot shall post buttons, each containing the name of a points configuration in the season, for the user to choose one. This will be saved together with the results of a session. So for each session, there's one chosen configuration from which to get the points-per-position information. This information shall be persisted.
    - Where exactly one configuration is attached to the season, it shall be chosen without asking and the choice stated.
- A driver shall be recorded under one team role across every session of a round; see "One driver, one team, for the whole of a round" below.
- The channel shall be private to the server's admin role, and shall be deleted once the round is settled.
- <MODIFIED COMMAND> There is a "round cancel" command initially implemented in the scope of the weather module. Its functionality shall be enhanced to also cancel the request for round results specified by the first bullet point. If this request for round results has already been triggered, the "round cancel" command will fail. It shall equally fail once any results exist for the round.

#### The two sanction columns of an amendment
- The formats below describe a first submission. A re-insertion through "round results amend" shall carry two further columns after them, holding the post-race penalty and the appeal penalty, so that an amendment preserves sanctions already applied rather than discarding them.
    - For a race session, each is either "N/A", a number of seconds, or "DSQ".
    - For a qualifying session, each is either "N/A" or "DSQ".
    - Setting both to "DSQ" on one line shall be rejected.
    - An entry either of whose sanction columns is "DSQ" shall be recorded as disqualified whatever its time column says.

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
    - The role in the Team column of every line does indeed belong to one of the teams;
    - For every entry, the driver in the Driver column is assigned to the team identified in the Team column OR is assigned to the reserve team;
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
    - The role in the Team column of every line does indeed belong to one of the teams;
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
- This is not a hard requirement, but it is presumed that the Time Penalties input have already been added to the Total Time, so no further calculations are required.
- In preparation of further functionality, the data tables on which race results are saved shall possess two extra columns for Post-Stewarding Total Time and Post-Race Time Penalties.
- It is always presumed that the Total Time column already includes the time noted in the Race Time Penalties
- If the Total Time column specifies "DNS", "DNF" or "DSQ", then these drivers shall not be eligible to receive points
- If the Total Time column specifies "DNS", "DNF" or "DSQ", the Fastest Lap column may be ignored if it does not follow the "x:xx.xxx" or "xx.xxx" formats.
- In all other cases, drivers are eligible for scoring.
- The fastest-lap bonus is normally awarded to the driver whose Fastest Lap time is the lowest across all valid entries in the submitted block. In the rare case where two or more drivers share the exact same fastest-lap time, an optional **FL override header** may prepended to the submission block on its own line, in the format `FL: <@user_id>`, to explicitly designate the fastest-lap holder. The override bypasses time-based comparison entirely and does not change or replace any of the per-driver Fastest Lap fields. If the override names a driver not present in the submitted results, the submission is rejected. If no override is provided and a tie occurs, the driver whose row appears first in the submission (i.e. with the lower finishing position) receives the bonus implicitly. The FL override is race-only; it has no effect on qualifying submissions.

#### One driver, one team, for the whole of a round
- A driver shall be recorded under one team role across every session of a round. A submission recording a driver under a team role different from the one another active session of that round already records for them shall be rejected, naming the driver, the team already recorded and the session recording it.
- The criteria above tie a driver seated in a team to the team they are seated in, but are applied to one session at a time and against the seats as they stand at that moment, and a driver of the reserve team is tied to no team at all, being free to stand in for any. Neither closes this case, and a reserve standing in for two different teams within one round would otherwise be recorded.
- The constructor standings graphic places each driver who drove a team's cars in a round upon one of those cars, and cannot place one driver upon the cars of two teams. That is guaranteed here, at the moment the input is given and where it can be named and corrected, rather than discovered when a graphic is drawn.

### After submission of results
- Once the results for all sessions in a round are submitted and validated, the results shall be output into the configured results channel for the division in a prettier table-like format. The results to be output are as follows:
    - Qualifying sessions: Position, Discord display name, Team, Tyre, Best Lap, Gap, Points Gained
    - Race sessions: Position, Discord display name, Team, Total Time, Fastest Lap, Time Penalties, Points Gained (if any)
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
Penalties are not applied by a command. Once every session of a round has been submitted or cancelled, the submission channel remains open and carries the round through two review stages before it closes. Each stage is worked from buttons, and a round passes through three published states: provisional, post-race penalty, and final.

- Any message posted in the submission channel while it is in a review stage shall be deleted, with a reply saying why.
- Every button in both stages shall be usable by holders of the server's interaction role.
- A round in which every session was cancelled shall skip both stages: the channel closes and no standings are computed for that round.

**Stage one — post-race penalties.** The prompt shall carry:
- **Add Penalty** — asks which session, then takes the driver as a mention or user ID, the sanction, a description and a justification. Both texts are mandatory and both are published in the verdict.
    - The sanction shall be either "DSQ" or a whole number of seconds, positive or negative. Fractions of a second shall be rejected.
    - "DSQ" invalidates the entry, which is ranked last in that session.
    - A number of seconds is added to the driver's total race time.
    - For "Sprint Qualifying" and "Feature Qualifying", only "DSQ" is accepted.
    - A negative sanction shall be rejected where its magnitude exceeds the time penalties the driver already carries in that session, counting anything staged in the same review, or where it would produce a negative total race time.
    - A driver not present in the chosen session's results shall be rejected.
- **No Penalties / Confirm** — proceeds with nothing applied. Where entries are staged, it shall first ask for confirmation that they are to be discarded.
- **Approve** — proceeds with what is staged. It shall be unavailable while nothing is staged.
- **Resubmit Initial Results** — discards the staged penalties, supersedes the round's submitted results, and restarts collection from the first session.
- **Attendance Pardon** — stages an attendance pardon, per the attendance module specification.
- One **Remove** button per staged entry.

Approving stage one shall present the staged list again with a choice of returning to staging, with the list intact, or committing. Committing shall apply every penalty, recompute positions, times and points for the sessions affected, republish the round's results and standings under the post-race penalty state, recompute the standings of every later round, and post one verdict per decision to the division's verdicts channel.

**Stage two — appeals.** Committing stage one shall post a second prompt to the same channel, carrying **Add Correction**, **No Changes / Confirm**, **Approve** and one **Remove** per staged correction. A correction takes and validates the same values as a penalty, and is the surface for overturning one.

Approving stage two shall apply any staged corrections, republish the round's results and standings under the final state, post a verdict for each correction, recompute the standings of every later round, mark the round final, and delete the submission channel. There shall be no second confirmation on this stage.

#### Amending a submitted session
- <NEW COMMAND> A "round results amend" command shall be made available to trusted server users that will intake a division name and round number mandatorily, and optionally, a session name as well. Where the session is omitted, the bot shall ask which one. The user will then be requested to re-insert session results in the same format as when first submitting round results, extended by the two sanction columns described under "Submitting round results".
    - The command shall be refused for a round that has not been marked final by both review stages.
    - The results shall be re-inserted in a channel created for the purpose, private to the server's admin role, carrying a button to abandon the amendment.
    - After insertion, the results are validated for format, and the standings of all rounds after the one that was amended (including) shall be output once more.
    - The points configuration recorded for the session shall be kept where it is still attached to the season; otherwise the user shall be asked to choose one.

## Standings
### Design
- Standings have two forms: driver standings and team standings. In both, the ranking criteria shall be as follows, in order:
    - 1st - Total number of points (higher is better);
    - 2nd - If equal on points, the number of wins (1st place finishes) is compared, with the tiebreaker being won by the one with the most wins;
    - 3rd - If equal on wins, the number of 2nd place finishes is compared, with the tiebreaker being won by the one with the most 2nd places; 
    - 4th - If equal, then 3rd place finishes is compared, and so on until a difference arises.
    - 5th - If at the end both drivers are still tied, then the first to take the highest position will win the tie-breaker (e.g. 0 1st finishes, 1 2nd finish for both drivers, first one to have gotten 2nd wins).
    - NOTE: For countback tiebreakers, only Feature Race sessions are relevant.
- In driver standings, all drivers that have partaken in a division are ranked according to their total accrued points and finishes in each round of said division.
- In team standings, all teams are ranked according to the total points and finishes accrued by those driving their cars in each round of said division.
    - This means that, in the case of Reserve drivers who may drive for Team A in one round and Team B in another, will have their points and finishes in the first go to Team A in the standings, and to Team B in the latter.
- Both standings are recalculated after the results of each round are submitted and validated, with the points obtained in that round added to the total.
- When the results of a session are amended or when penalties are applied, the standings of all rounds after the one modified (including) shall be recalculated by the bot.
- A driver's results are specific to one division. Assuming a driver participates in two different divisions, the points gained by driving in Division X are accounted for in the standings for Division X only, and their standing in Division Y is unaffected.
- Driver and Team standings are to be saved at round-scope: this makes it easier to organize information and to trace the progress of a championship. As such, the following information shall be saved for each driver and team within the standings table recorded in each round (which pertains to the state after a round):
    - Discord ID (driver standings) / Team role (team standings)
    - Total points (so far)
    - Finishes place n
    - nth place first obtained on Round Number
        - NOTE: There will be a "Finishes place n" entry for each place a driver has finished in. If there are no entries for a given place, then it is assumed that the drivers finished 0 times in that position.
        - NOTE: There will be a "nth place first obtained on Round Number" for each place a driver has finished in. If there are no entries for a given place, then it is assumed that the drivers finished 0 times in that position.

### Detailed functionality specification for standings
- <NEW COMMAND> A "results reserves toggle" command will be made available to trusted server users, which takes the name of a division and applies to that division alone. When toggled on, drivers belonging to the Reserve team will be relevant for the driver standings, and will therefore show up in the classification. When toggled off, drivers belonging to the Reserve team will accrue points all the same, but will not show up in the driver standings. Reserves shall be shown by default.
- <NEW COMMAND> A "results standings sync" command will be made available to trusted server users, which will take as input the name of a division. It shall delete every standings message the bot holds for that division and post the standings of each round that has results afresh, in round order, each under the state that round has reached.
- <NEW COMMAND> A "results rounds sync" command will be made available to trusted server users, which will take as input the name of a division. It shall delete every session results message the bot holds for that division and post the results of every session of every round afresh, in round order, each under the state that round has reached.
- Every driver seated in a non-reserve seat of a division shall appear in the driver standings from the outset, on zero points, whether or not they have taken part in a round. Every non-reserve team of the division shall likewise appear in the team standings.
- Where two entries cannot be separated on points or on countback, an entry that has taken part in at least one session shall rank above one that has taken part in none.
- If a driver that was assigned only to the reserve team in a given division is then assigned to a configurable team, the points they have accrued as a driver will stand all-the-same, and will be reflected on their position on the standings.


Name of commands is not mandatory, a better one may be used instead.