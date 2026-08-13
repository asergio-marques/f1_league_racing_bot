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
- If there is not an active season, it shall not be possible to approve the season if the results & standings module is enabled AND not all divisions have a results channel and a standings configured AND there is no existing points configuration (every position of every possible session gives 0 points).
- If there is an active season, it shall not be possible to enable the results & standings module.

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
- <NEW COMMAND> A "results amend toggle" command will be made available to server administrators that will enable and disable the modification of the configurations in the season points schema store. By default, this will be disabled.
    - It shall not be possible to "toggle off" if the modified flag is true.
- <NEW COMMAND> A "results amend revert" command will be made available to trusted users that will copy over the season's points schema store onto the modification schema store, thereby reverting all uncommitted modifications. This command is invalid if "results amend" is toggled off.
- Once "results amend revert" is run successfully, the "modified flag" is set to false.
- <NEW COMMAND> A "results amend session" command will be made available to trusted users that will intake the string that IDs the points configuration in the modification store to be changed, a coded enum for the "session type" (Sprint Quali, Sprint Race, Feature Quali, Feature Race), an integer signifying position, and an integer signifying the number of points gained. This command is invalid if "results amend" is toggled off.
- <NEW COMMAND> A "results amend fl" command will be made available to trusted server users that will intake the string that IDs the points configuration to be changed in the modification schema store, a coded enum for the "session type" (Sprint Quali, Sprint Race, Feature Quali, Feature Race) and an integer signifying the number of points gained for having the shortest lap time in a session. The session types "Sprint Quali" and "Feature Quali" are invalid for this command.
- <NEW COMMAND> A "results amend fl-plimit" command will be made available to trusted server users that will intake the string that IDs the points configuration to be changed in the modification schema store, a coded enum for the "session type" (Sprint Quali, Sprint Race, Feature Quali, Feature Race) and an integer signifying the lowest valid position for which a driver is eligible for fastest-lap points (e.g. if this is configured to 10, then if the 11th place driver gets the fastest lap, then they get no points). The session types "Sprint Quali" and "Feature Quali" are invalid for this command.
- Once one of "results amend session", "results amend fl" and "results amend fl-plimit" is run successfully, the modified flag is set to true.
- <NEW COMMAND> A "results amend review" command will be made available to server administrators, which will display the contents of the configurations stored in the modification store via the bot, alongside a button to approve or reject.
- If approved, then the contents of the season points schema store will be overwritten by the modification store. All round results, and standings after each round result, shall be recalculated and reposted in the appropriate channels for each division. The modified flag will then be set to false, and the modification store cleared.
- If rejected, nothing happens. The modification store will remain as it is, and the amending mode will remain active.

#### Viewing configs after season approval
- <NEW COMMAND> A "results config view" will be made available to trusted server users to view the many points configurations applied to the current season. There is one mandatory input, the name/ID of the points configuration, and one optional input, the session type whose points configuration is to be posted; if this optional parameter is omitted, then the configuration for all sessions pertaining to the input name/ID shall be posted.
- When listing points configuration for any session, if all positions beyond a certain point yield 0 points, then they shall all be listed as "xth+" to prevent repetition.

### Submitting round results
- At the scheduled time of every round, the bot will create a new channel adjacent to the results channel of the division that had just had its round, notifying the trusted user role to input the results of each session applicable to the round in the following order: Sprint Quali, Sprint Race, Feature Quali, Feature Race.
    - If the round type is not Sprint, then Sprint Quali and Sprint Race will be omitted.
    - Each round will be requested in order; i.e. the user will have to first input the Sprint Quali's results exclusively, then Sprint Race, etc.
- The bot shall read the inputs of the trusted user to create the data entry for the results of the session. The expected format depends on the type of session, but will always require Position, Driver, and Team.
- For the driver column, a driver that is assigned to that division must be tagged. If the driver is not assigned to the division, or if there is no driver tagged at all, then the input will fail.
- For the team column, a team role must be tagged as well. This will allow easy identification of the team.
- If the results input for any session within a round are not valid, then they shall be requested once more.
- There is a special reserved input for every session which is "CANCELLED". This allows users to not submit results for sessions that were not run due to whatever issues.
- All inputs pertaining to round results must be logged in the log channel configured for the server. The season number, division and round number shall be explicited for each raw result input logged for easy search.
- Once the results introduced to a given session are attributed, the bot shall post buttons, each containing the name of a points configuration in the season, for the user to choose one. This will be saved together with the results of a session. So for each session, there's one chosen configuration from which to get the points-per-position information. This information shall be persisted.
- <MODIFIED COMMAND> There is a "round cancel" command initially implemented in the scope of the weather module. Its functionality shall be enhanced to also cancel the request for round results specified by the first bullet point. If this request for round results has already been triggered, the "round cancel" command will fail.

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

### After submission of results
- Once the results for all sessions in a round are submitted and validated, the results shall be output into the configured results channel for the division in a prettier table-like format. The results to be output are as follows:
    - Qualifying sessions: Position, Discord display name, Team, Tyre, Best Lap, Gap, Points Gained
    - Race sessions: Position, Discord display name, Team, Total Time, Fastest Lap, Time Penalties, Points Gained (if any)
- The points conferred to each driver will be determined by the points configuration applied to each session: points are attributed by finishing position and, depending on the settings of the configuration (points for fastest lap and placement limit for fastest lap points), for the lowest lap time as well.

#### Corrections to the rendering of the table
- A time penalty shall be rendered in seconds, signed, and to the precision with which it was recorded: a penalty of a whole number of seconds carries no decimal part, and one carrying a fraction of a second is rendered to three decimal places. Five seconds is "+5s" and five and a half "+5.500s". A penalty is never rounded to a whole second for display, and never rounded away from zero.
    - This is the rendering the image module re-presents. The graphic derives nothing of its own, so the table and the graphic shall render a penalty by one and the same code and cannot differ.
    - Corrected 2026-08-13. The table formerly rendered every penalty column by an integer division of its milliseconds, showing five and a half seconds as "+5s", a fraction below one as "+0s", and a credit of five and a half seconds applied on appeal as "-6s".
- The best lap column of a qualifying table shall be emptied for an entry that was classified and set no time.
    - An entry that did not finish, did not start or was disqualified carries its outcome in that column instead, whatever lap may have been recorded for it: the outcome displaces the lap rather than standing in for a missing one.
    - Corrected 2026-08-13. The column formerly fell back to the outcome wherever no lap was held, so a classified entry with no lap printed the word "CLASSIFIED" where a lap time belongs, while a disqualified entry holding a lap printed the lap.

### Revising results
- <NEW COMMAND> A "round results penalize" command shall be made available to trusted server users that will mandatorily intake a division name and round number. This will initiate a wizard with the following states:
        - Start - A button for each session in will be posted by the bot. If there are no penalties registered by the user, show only a "cancel" button. If there are penalties registered by the user, show also a "review" button.
        - Insert User ID - After choosing the session, the bot will request a user ID repeatedly until a user ID is provided. It will be possible to "go back" via a button, which will make the wizard go back to the session buttons.
        - Insert time penalty - After inserting a user ID, the bot will request a string representing the time penalty (in seconds) to be added to the driver's total race time. There will also be a button for disqualifying a driver. There will also be a button for cancelling, which will make the user return to "Insert User ID", and another to cancel and go back to "Start".
            - If "DSQ", then the driver's result will be invalidated, and they will be dropped to the bottom of the results' table, being ranked last.
            - If an integer, that number will then be added to the total race time of the driver.
            - If the session chosen is "Sprint Qualifying" or "Feature Qualifying", then no time penalties are accepted; only disqualification is accepted.
            - If the input for the penalty is valid, the bot will request a new user ID.
        - Review - Once the review button is pressed, a list of penalties will be displayed, with a button to "approve", "make changes" or "cancel".
    - Once cancelled in "Review", the wizard with exit entirely.
    - Once "Make changes" is chosen in "Review", the wizard will return to the "Start" state.
    - Once approved in "Review", the corrections will be applied, the gap to leader recalculated for all drivers, and the positions of all drivers in all sessions will be recalculated and reposted in the appropriate channels.    
- <NEW COMMAND> A "round results amend" command shall be made available to trusted server users that will intake a division name and round number mandatorily, and optionally, a session name as well. The user will then be requested to re-insert session results in the same format as when first submitting round results.
    - After insertion, the results are validated for format, and the standings of all rounds after the one that was amended (including) shall be output once more.

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
- <NEW COMMAND> A "results reserves toggle" command will be made available to trusted server users. When toggled on, drivers belonging to the Reserve team will be relevant for the driver standings, and will therefore show up in the classification. When toggled off, drivers belonging to the Reserve team will accrue points all the same, but will not show up in the driver standings.
- <NEW COMMAND> A "standings sync" command will be made available to trusted server users, which will take as input the name of a division.
- If a driver that was assigned only to the reserve team in a given division is then assigned to a configurable team, the points they have accrued as a driver will stand all-the-same, and will be reflected on their position on the standings.


Name of commands is not mandatory, a better one may be used instead.