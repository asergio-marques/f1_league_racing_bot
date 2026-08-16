# Stewarding module
- <COMMAND CHANGE> The stewarding module may be enabled via a "module enable" command akin to the weather and signup modules. May only be used by server admins.
- <COMMAND CHANGE> The stewarding module may be disabled via a "module disable" command akin to the weather, signup, results and attendance modules. May only be used by server admins.
- The stewarding module is disabled by default.
- The stewarding module may not be enabled once the season is approved.
- The stewarding module is heavily connected to the results & standings module, but only part of its functionality is to be disabled if the results & standings module is disabled as well.
- The stewarding module is connected to the attendance module, and modifies its outputs.
- The stewarding module is connected to the signup module, and modifies its outputs.
- Stewarding module activation status shall be displayed in the season review.
- This module must work with the fake driver rosters used in test mode.

## Concepts
- Driver license - A individual record of a driver's history in the league, onto which the current amount of warning/penalty/discipline points and their history (and each of those points' expirations dates), plus qualifying/race/season/league ban current information and history is appended to.
- Steward - A trusted user denoted with a special role which may be different from that of league managers, which are able to see tickets and pass judgement on them.
- Head steward - A privileged user denoted with a special role that serves as the leader of the stewarding team, and serves as a tie-breaker when verdicts for a given ticket are equally split. It is mandatory that a stewarding team has a head steward. They may confer head steward responsabilities another member of the stewarding team for a temporary period.
- Temporary head steward - Also referred to as temp head steward and acting head steward. A user with similar privileges as the head steward, which last only for a limited amount of time, as a result of being deferred head steward responsabilities temporarily. While a temporary head steward is active, the head steward loses their tie-break privilege.
- Stewarding team - The collective composed of all stewards.
- Stewarding cycle - The full process for stewarding after a round is scheduled to take place. This is an informal concept, meaning it is not a strict definition, just an auxiliary name. It kicks off at the time a round is scheduled to happen with the enabling of reports for that round, and ends only when all appeals' verdicts are posted (if there were any appeals) OR when all reports' verdicts are posted (if there were any reports) OR once the report submission deadline passes (if there were no reports). It is composed of the following stages:
  - Report submission - Active starting at the scheduled round time, and automatically disabled after a configured amount of time after the scheduled round time. Period of time in which drivers or the stewarding team can initiate reports against other drivers of the division.
  - Defense submission - Active from the moment the report is created, and automatically disabled after a configured amount of time after the scheduled round time, which cannot be shorter than that of report submission. Aims to allow other drivers to provide their own version of events and evidence.
  - Report deliberation - Active from the moment the defense submission stage ends, and automatically disabled after a configured period of time. Aims to allow stewards to vote on the final verdict, providing justification. After this period is over, verdicts of all reports are posted to the configured channel.
  - Appeal submission - Active for a configured period of time after the report deliberation ends. Lasts for a configured period of time; in it, drivers involved in submitted reports can appeal their outcome, if they have the required number of appeal tokens.
  - Appeal deliberation - Active from the moment the appeal submission ends, and automatically disabled after a configured period of time. Aims to allow stewards to vote on the final verdict, providing justification. After this period is over, the verdicts of all appeals are posted to the configured channel. After this, the round is taken as final, and its results cannot be changed.
- Conduct investigation cycle - The full process for a Code of Conduct investigation can be initiated by a member of the steward team at any time. This is an informal concept, meaning it is not a strict definition, just an auxiliary name. It kicks off when the head steward (or temporary head steward) initiates a Code of Conduct investigation targeted at one or more specific driver(s), submitting a justification and evidence (which may be private to the steward team or shared with the mentioned drivers).
  - Defense submission - Active from the moment the investigation is triggered, and automatically disabled once a configured period of time elapses. In this stage, the mentioned drivers are allowed to submit defenses and additional evidence relevant to the case opened.
  - Investigation deliberation - Active once the defense submission ends, and automatically disabled once a configured period of time. Aims to allow stewards to vote on the final verdict, providing justification. After this period is over, the verdict is posted to the configured verdict channel of all divisions the reported drivers are assigned to (if the driver is assigned to two or more divisions, repeating posts must have the indication "(repost)").
- Ticket - A user-submitted incidence which may be either a report, an appeal or a Code of Conduct investigation. The former two may be public (seen by any driver of the division to which they pertain) or private (seen only by drivers involved and the stewarding team). The latter is always private to the utmost (only the steward team and the driver involved can see this).
- Report - May also be referred to as stewards' report. This is an incidence submitted by either a driver or by a member representing the steward team as an anonymous collective, which may refer to one or more other drivers, pertaining to an incident that occurred during the most recent round.
- Appeal - A special kind of ticket submitted by a driver or by a member representing the steward team as an anonymous collective which aims for this ticket to be judged once more, so that the ultimate verdict is passed. The submission of an appeal by a driver may require 1 or more appeal tokens to be spent.
- Appeal token - A special kind of currency that may be required for drivers to be able to submit an appeal. Appeal tokens are accumulated on a driver's license, and expire upon the current season's end. Upon a successful appeal, depending on configuration, drivers may be returned their spent tokens.
- Code of Conduct investigation - May also be referred to as a CoC investigation. A special kind of ticket and the only one which is not linked to a round, instead being linked to a driver; as such, it cannot lead to any changes in results (time penalties, warning or penalty points). It may only be initiated by the head steward. It cannot be appealed, and any decisions made are final. This functionality is optional and is disabled by default.
- Outcome - A standardized penalty table item for reports and appeals, which draws a relationship from a "standard penalty description/case" to a "standard penalty", which may be one, or multiple between time penalties, warning points, penalty points, qualifying bans, race bans, season bans and league bans. Each outcome has a unique ID string, which is to be used when voting, and an identifying shorthand. Additionally, there is also a "No Further Action" outcome which sets none of the possible punishments onto a driver. The outcome is decided from the majority verdict among votes casted. The list for outcomes is managed separately from that of conduct outcomes, so there may be overlap of IDs of items between the two.
- Conduct outcome - A standardized penalty table item for CoC investigations, which draws a relationship from a "standard penalty description/case" to a "standard penalty", which may be one, or multiple between discipline points, qualifying bans, race bans, season bans and league bans. Each conduct outcome has a unique ID string, which is to be used when voting, and an identifying shorthand. Additionally, there is also a "No Further Action" outcome which sets none of the possible punishments onto a driver. The conduct outcome is decided from the majority verdict among votes casted.  The list for conduct outcomes is managed separately from that of outcomes, so there may be overlap of IDs of items between the two.
- Time penalty - A possible direct outcome of a verdict for a report or appeal. Time is added or removed to a participant's total race time; note that it is not possible to remove time from a participant's total race time such that the sum of the in-race and post-race time penalties minus the time removed is lower than zero seconds (this functionality is already implemented in the results & standings module)
- Warning point - A possible direct outcome of a verdict for a report or appeal. Warning points serve as the lightest of penalties, and a minor rebuke to a driver's on-track behavior. Warning points are accumulated on a driver's license, and may expire either after a set number of races (which may be a hard-bound value or the length of the current season), upon the current season's end, or after a fixed period of time. If warning points are enabled, their accumulation will equal a penalty point. Warning points are only applied to a driver's license once the stewarding cycle in which it was bestowed is complete.
- Penalty point - A possible direct outcome of a verdict for a report or appeal. Penalty points are accumulated on a driver's license, and may expire either after a set number of races (which may be a hard-bound value or the length of the current season), upon the current season's end, or after a fixed period of time. Depending on configuration, the accumulation of penalty points may lead to additional sanctions being applied to a driver. Penalty points are only applied to a driver's license once the stewarding cycle in which it was bestowed is complete.
- Discipline point - A possible direct outcome of a verdict for a CoC investigation. Discipline points are accumulated on a driver's license, and may not expire at all, or expire only after a fixed period of time. Depending on configuration, the accumulation of discipline points may lead to additional sanctions being applied to a driver. Like CoC investigations, this functionality is optional and is disabled by default.
- Qualifying ban - A possible direct or indirect outcome of a verdict for all ticket types. Qualifying bans are appended to a driver's license. The driver that receives this sanction is thereby forbidden from taking part in all qualifying sessions in the next round they participate in of the division in which they received a qualifying ban for, be it in the current season, or the next. This means that they may not set a valid lap in any of the qualifying sessions, but they must be present in the classification of the qualifying and race sessions. If configured, the bot may automatically detect a failure to serve a qualifying ban via a round's results, and automatically open a steward team report againt the offending driver. Qualifying bans are only applied to a driver's license once the stewarding cycle in which it was bestowed is complete.
- Race ban - A possible direct or indirect outcome of a verdict for all ticket types. Race bans are appended to a driver's license. The driver that receives this sanction is thereby forbidden from taking part in the next round of the division in which they received a qualifying ban for, be it in the current season, or the next. This means that they may not be present in the classification of any sessions for the round they are banned for. If configured, the bot may automatically detect a failure to serve a race ban via a round's results, and automatically open a steward team report againt the offending driver. Race bans are only applied to a driver's license once the stewarding cycle in which it was bestowed is complete.
- Season ban - A possible direct or indirect outcome of a verdict for all ticket types. Season bans are appended to a driver's license. The driver that receives this sanction loses all their current seats for all divisions, full-time and reserve both. Drivers with a season ban will be assigned a special role, and will be unable to engage with the signup wizard. A season ban will expire after a set number of races (which may be a hard-bound value or the length of the current season), upon the current season's end (or the next season's end, if received on the final round), or after a fixed period of time. If configured, the bot may automatically detect if a driver has a given number of multiple season bans, and automatically open a steward team report against the offending driver with a view at bestowing a league ban. Season bans are only applied to a driver's license once the stewarding cycle in which it was bestowed is complete.
- League ban - A possible direct or indirect outcome of a verdict for all ticket types. League bans are appended to a driver's license. The driver that receives this sanction thereby loses all their current seats for all divisions, full-time and reserve both. A driver that receives a league ban will be banned from the league server for a configured duration of time. Upon rejoining the server, all users that receive a league ban will be assigned a special role, and will be unable to engage with the signup wizard. League bans are only applied to a driver's license once the stewarding cycle in which it was bestowed is complete.

## Configuring the stewarding module
- All configuration changes must be logged to the standard log channel.
- All configuration changes done with commands usable by the head steward, acting head steward, or other members of the steward team must be logged to the steward log channel.

### Channels
- <NEW COMMAND> A "division ticket-channel" command will be made available to league managers, which shall have as input a division name and a channel in which drivers for that division can interact to initiate tickets (reports and appeals both).
- <NEW COMMAND> A "steward command-channel" command will be made available to league managers, which shall have as input a channel in which stewards will be able to input certain special bot commands. These commands must be explicitly marked as steward team actionable in these specifications, otherwise their use will be rejected, and no other commands but those will be accepted in this channel.
- <NEW COMMAND> A "steward log-channel" command will be made available to league managers, which shall have as input a channel in which ALL commands utilized in the channel configured by "steward command-channel" will be logged for audit purposes, much in the same way they are already done by the log channel input in "bot init".

### Stewarding team setup
- <NEW COMMAND> A "steward team-role" command will be made available to league managers, which shall have as input a user role that will be bestowed to all users designated as stewards.
  - This command is only valid if no user has steward status.
  - Upon usage, this command shall be validated to check that the steward role is not the same as the one configured by "steward head-role" or "steward temp-head-role".
- <NEW COMMAND> A "steward head-role" command will be made available to league managers, which shall have as optional input a user role that will be bestowed to the user designated as head steward.
  - Upon usage, this command shall be validated to check that the head steward role is not assigned to more than 1 user, and that the role is not the same as the one configured by "steward team-role" or "steward temp-head-role".
  - This command is only valid if no user has head steward status.
  - If the input role parameter is empty, then head steward functionality is deactivated.
- <NEW COMMAND> A "steward temp-head-role" command will be made available to the head steward to be utilized in the channel configured by "steward command-channel", which shall have as optional input a user role that will be bestowed to the user designated as head steward.
  - Upon usage, this command shall be validated that the role is not the same as the one configured by "steward team-role" or "steward head-role".
  - This command is only valid if no user has temporary head steward status.
  - If the input role parameter is empty, then temporary head steward functionality is deactivated.
- <NEW COMMAND> A "steward assign-temp-head" command will be made available to the head steward to be utilized in the channel configured by "steward command-channel" exclusively, which will have as input the user ID of a member belonging to the steward team. This command will confer the user with temporary head steward status.
  - This command is only valid if the temporary head steward role configured by "steward temp-head-role" is not empty.
  - This command is only valid if the target user is part of the steward team and is not the head steward.
  - The head steward does not lose any of their powers with the exception of the voting tie-breaking capabilities, which are from then-on solely held by the temporary head steward.
- <NEW COMMAND> A "steward remove-temp-head" command will be made available to the head steward to be utilized in the channel configured by "steward command-channel" exclusively, which will have no input. This command will remove the temporary head steward status from the user currently possessing it.
  - This command is only valid if the temporary head steward role configured by "steward temp-head-role" is not empty.

### Stewarding cycle setup
- <NEW COMMAND> A "steward report-submission-period" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the maximum number of hours during which drivers for that division or users belonging to the steward team (validated by checking whether they have the steward team role) are able to open a report. After this time elapses, the report submission phase is over.
  - By default, this value will be set to 48.
  - Input value must be equal or greater than 1.
- <NEW COMMAND> A "steward defense-submission-period" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours during which any involved/mentioned driver is able to provide evidence or arguments regarding the incident in question. After this time elapses, the defense submission phase is over.
  - By default, this value will be set to 24.
  - Input value must be equal or greater than 1.
- <NEW COMMAND> A "steward report-deliberation-period" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours during which members of the steward team can discuss and vote on the verdict pertaining to a given report. After this time elapses, the report deliberation phase is over.
  - By default, this value will be set to 24.
  - Input value must be equal or greater than 1.
- <NEW COMMAND> A "steward appeal-submission-period" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours during which drivers for that division or users belonging to the steward team (validated by checking whether they have the steward team role) are able to open an appeal. After this time elapses, the appeal submission phase is over.
  - By default, this value will be set to 24.
  - Input value must be equal or greater than 0. If the value is 0, then appeals are disabled, and both the appeal submission and the appeal deliberation stages will not be scheduled.
- <NEW COMMAND> A "steward appeal-deliberation-period" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours during which members of the steward team can discuss and vote on the verdict pertaining to a given report. After this time elapses, the appeal deliberation phase is over.
  - By default, this value will be set to 24.
  - Input value must be equal or greater than 1.
- The sum of the values of the configurations above may not exceed 168 (7 times 24 hours). This validation must be done everytime one of the commands above is run; if failed, then the new value is not accepted.
- Any changes done to these values above will NOT be applied for a given division until the next round is scheduled to take place.
- <NEW COMMAND> A "steward appeal toggle" command will be made available to league managers, which shall have no inputs. This command shall activate and deactivate the appeal system.
  - By default, the appeal functionality is enabled.
- <NEW COMMAND> A "steward appeal starting-tokens" command will be made available to league managers, which shall have as input an integer standing for a number of tokens. This value will be the number of appeal tokens assigned to all drivers when they are assigned to a team.
  - By default, this value will be set to 0. This means that, effectively drivers have unlimited appeal abilities.
  - This value also serves as the maximum allowed number of appeal tokens for a given driver.
  - A driver's number of appeal tokens is reset to this value once their assignment to a team is approved (reserve team include), IF they are not assigned to a team in any division already.
- <NEW COMMAND> A "steward appeal token-spend" command will be made available to league managers, which shall have as input an integer standing for a number of tokens. This value will be the number of appeal tokens required for a driver to have so they may initiate an appeal regarding a previous report.
  - By default, this value will be set to 0. This means that, effectively drivers have unlimited appeal abilities.
  - This value cannot be greater than that configured by "steward appeal starting-tokens".
  - This value shall be ignored if the appeal is initiated by a member of the steward team AND said member is not assigned to a team of the division to which the appeal pertains.
- <NEW COMMAND> A "steward outcome add" command will be made available to league managers, which shall open a modal window with the following input fields:
  - ID - Mandatory - Unique ID for the outcome. Maximum of 10 characters.
  - Brief - Mandatory - Unique short description of the outcome. Maximum of 50 characters.
  - Description - Optional - Long form description of the outcome. Maximum of 250 characters.
  - Applicable to qualifying? - Mandatory - Checkbox that, if ticked, represents that this outcome can be assigned to incidents from a qualifying session.
  - Applicable to race? - Mandatory - Checkbox that, if ticked, represents that this outcome can be assigned to incidents from a race session.
  - Time penalty - Optional - Integer input only. Number of milisseconds added to the total race time of the offending driver.
  - Warning points - Optional - Integer input only. Number of warning points added to the driver license of the offending driver.
  - Penalty points - Optional - Integer input only. Number of penalty points added to the driver license of the offending driver.
  - Qualifying bans - Optional - Integer input only. Number of qualifying bans added to the driver license of the offending driver.
  - Race bans - Optional - Integer input only. Number of race bans added to the driver license of the offending driver.
  - Season ban - Optional - Checkbox that, if ticked, means that the offending driver's license will accrue a ban lasted for one season.
  - League ban - Optional - Checkbox that, if ticked, means that the offending driver's license will accrue a league ban.
  - Contrary to the others, this command may be accepted if any report deliberation or appeal deliberation phases are on-going.
  - At least one of the "Time penalty", "Warning point", "Penalty point", "Qualifying ban", "Race ban", "Season ban", "League ban" fields must be different from 0.
- <NEW COMMAND> A "steward outcome modify" command will be made available to league managers, which shall have as input a string standing for an outcome's ID. If this ID is valid, then a modal dialog much like the one opened by "steward outcome add" shall open, prefilled with the values of the outcome of the input ID. All fields with the exception of the ID can be modified.
  - This command shall fail if any report deliberation or appeal deliberation phases are on-going.
  - At least one of the "Time penalty", "Warning point", "Penalty point", "Qualifying ban", "Race ban", "Season ban", "League ban" fields must be different from 0.
- <NEW COMMAND> A "steward outcome remove" command will be made available to league managers, which shall have as input a string standing for an outcome's ID. If this ID is valid, then a modal dialog asking for confirmation of deletion of the outcome will appear. If accepted, then the outcome shall be removed from the list.
  - This command shall fail if any report deliberation or appeal deliberation phases are on-going.
- <NEW COMMAND> A "steward outcome list" command will be made available to league managers and stewards, which shall have as input a string standing for a session type (qualifying or race). In reply, the bot will post a transient (temporary, seen only to the command user) list with all the outcomes currently available for that session type, as a bullet point list as follows:
  - <brief>
    - ID: <id>
    - Rule description: <description, if not empty, otherwise this line is skipped>
    - Associated outcome: <all penalties associated with the outcome, comma concatenated>
- By default, a permanent, unremovable, unmodifiable outcome with the following values is added to the list, which has the following data:
  - ID - NFA (special reserved ID)
  - Brief - No Further Action
  - Description - Outcome which means there is not actionable offense in the reported incident, and therefore no punishment is passed upon any driver.
  - Applicable to qualifying? - Yes
  - Applicable to race? - Yes
  - Time penalty - 0
  - Warning point - 0
  - Penalty point - 0
  - Qualifying ban - 0
  - Race ban - 0
  - Season ban - 0

### Conduct cycle setup
- <NEW COMMAND> A "steward conduct toggle" command will be made available to league managers, which shall have no inputs.
  - This functionality is toggled off by default, and any of the other commands in this section fail if this functionality is toggled off.
- <NEW COMMAND> A "steward conduct defense-submission-period" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours during which any involved/mentioned driver is able to provide evidence or arguments relevant to the Code of Conduct investigation in question. After this time elapses, the defense submission phase is over.
  - By default, this value will be set to 24.
  - Input value must be equal or greater than 1.
- <NEW COMMAND> A "steward conduct inv-deliberation-period" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours during which members of the steward team can discuss and vote on the verdict pertaining to a given Code of Conduct investigation. After this time elapses, the investigation deliberation phase is over.
  - By default, this value will be set to 24.
  - Input value must be equal or greater than 1.
- <NEW COMMAND> A "steward conduct-outcome add" command will be made available to league managers, which shall open a modal window with the following input fields:
  - ID - Mandatory - Unique ID for the conduct outcome. Maximum of 10 characters.
  - Brief - Mandatory - Unique short description of the conduct outcome. Maximum of 50 characters.
  - Description - Optional - Long form description of the conduct outcome. Maximum of 250 characters.
  - Discipline points - Optional - Integer input only. Number of warning points added to the driver license of the offending driver.
  - Race bans - Optional - Integer input only. Number of race bans added to the driver license of the offending driver.
  - Season ban - Optional - Checkbox that, if ticked, means that the offending driver's license will accrue a ban lasted for one season.
  - League ban - Optional - Checkbox that, if ticked, means that the offending driver's license will accrue a league ban.
  - Contrary to the others, this command may be accepted if any report investigation deliberation phase is on-going.
  - At least one of the "Discipline points", "Qualifying bans", "Race bans", "Season ban", "League ban" fields must be different from 0.
- <NEW COMMAND> A "steward conduct-outcome modify" command will be made available to league managers, which shall have as input a string standing for an conduct outcome's ID. If this ID is valid, then a modal dialog much like the one opened by "steward conduct-outcome add" shall open, prefilled with the values of the conduct outcome of the input ID. All fields with the exception of the ID can be modified.
  - This command shall fail if any investigation deliberation phase is on-going.
  - At least one of the "Discipline points", "Qualifying bans", "Race bans", "Season ban", "League ban" fields must be different from 0.
- <NEW COMMAND> A "steward conduct-outcome remove" command will be made available to league managers, which shall have as input a string standing for an conduct outcome's ID. If this ID is valid, then a modal dialog asking for confirmation of deletion of the conduct outcome will appear. If accepted, then the conduct outcome shall be removed from the list.
  - This command shall fail if any investigation deliberation phase is on-going.
- <NEW COMMAND> A "steward conduct-outcome list" command will be made available to league managers and stewards, which shall have no inputs. In reply, the bot will post a transient (temporary, seen only to the command user) list with all the conduct outcomes currently available, as a bullet point list as follows:
  - <brief>
    - ID: <id>
    - Rule description: <description, if not empty, otherwise this line is skipped>
    - Associated outcome: <all penalties associated with the outcome, comma concatenated>
- By default, a permanent, unremovable, unmodifiable cpmdict outcome with the following values is added to the list, which has the following data:
  - ID - NFA (special reserved ID)
  - Brief - No Further Action
  - Description - Outcome which means there is not actionable disciplinary offense in the reported incident, and therefore no punishment is passed upon any individual.
  - Discipline point - 0
  - Qualifying ban - 0
  - Race ban - 0
  - Season ban - 0

<<< move these>>>
- <NEW COMMAND> A "steward conduct-inv start" command will be made available to the head steward and temporary head steward roles to be utilized in the channel configured by "steward command-channel" exclusively, which will have as input 1 or more user IDs of a server member (not necessarily a driver, only requires the "base_role" as configured by "module enable signup"), so that a CoC investigation is opened against said user.

- <NEW COMMAND> A "steward conduct-inv add" command will be made available to the league managers... <<< --- TBD --- >>>

### Penalties