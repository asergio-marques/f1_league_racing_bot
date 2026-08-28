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
- Head steward - A privileged user denoted with a special role that serves as the leader of the stewarding team. It is mandatory that a stewarding team has a head steward. They may confer acting head steward responsabilities onto another member of the stewarding team for a temporary period. By default, the head steward is the effective head steward for all tickets.
- Acting head steward - Also referred to as temporary or temp head steward. A privileged user denoted with a special role with similar privileges as the head steward, which lasts only for a limited amount of time, as a result of being deferred head steward responsabilities temporarily. While a temporary head steward is active, the head steward loses their default status as effective head steward on all tickets and the ability to use commands which require head steward privilege.
- Effective head steward - The designated head steward for a specific ticket. By default, this is the head steward, or the acting head steward if the latter functionality is in use. This user is able to add or remove drivers from tickets, approve requests to do either, or to approve steward exclusions from tickets. Their vote may also serve as a tie-breaker when outcomes voted for a given ticket are equally split. The effective head steward for a given ticket may change while the stewarding cycle is underway.
- Stewarding team - The collective composed of all stewards.
- Effective stewarding team - The members of the steward team that have the ability to participate in the stewarding cycle of a given report. Depending on the drivers involved in the ticket, configurations, temporary head steward status, or exclusion requests, it is possible that not all permanent members of the stewarding team are part of the effective stewarding team. This list is mutable while the stewarding cycle is underway. By default, the effective stewarding team is composed of:
  - Effective head steward.
  - All steward team elements EXCEPT:
    - Those driving in the division to which a report pertains if steward conflicts of interest are not allowed.
    - Any involved driver that belongs to the steward team.
- Stewarding cycle - The full process for stewarding after a round is scheduled to take place. This is an informal concept, meaning it is not a strict definition, just an auxiliary name. It kicks off at the time a round is scheduled to happen with the enabling of reports for that round, and ends only when all appeals' verdicts are posted (if there were any appeals) OR when all reports' verdicts are posted (if there were any reports) OR once the report submission deadline passes (if there were no reports). It is composed of the following stages:
  - Report submission - Active starting at the scheduled round time, and automatically disabled after a configured amount of time after the scheduled round time. Period of time in which drivers or the stewarding team can initiate reports against other drivers of the division.
  - Defense submission - Active from the moment the report is created, and automatically disabled after a configured amount of time after the scheduled round time, which cannot be shorter than that of report submission. Aims to allow other drivers to provide their own version of events and evidence.
  - Report deliberation - Active from the moment the defense submission stage ends, and automatically disabled after a configured period of time. Aims to allow stewards to vote on the final verdict, providing justification. After this period is over, verdicts of all reports are posted to the configured channel.
  - Appeal submission - Active for a configured period of time after the report deliberation ends. Lasts for a configured period of time; in it, drivers involved in submitted reports can appeal their outcome, if they have the required number of appeal tokens.
  - Appeal deliberation - Active from the moment the appeal submission ends, and automatically disabled after a configured period of time. Aims to allow stewards to vote on the final verdict, providing justification. After this period is over, the verdicts of all appeals are posted to the configured channel. After this, the round is taken as final, and its results cannot be changed.
- Conduct investigation cycle - The full process for a Code of Conduct investigation can be initiated by a member of the steward team at any time. This is an informal concept, meaning it is not a strict definition, just an auxiliary name. It kicks off when the head steward (or temporary head steward) initiates a Code of Conduct investigation targeted at one or more specific driver(s), submitting a justification and evidence (which may be private to the steward team or shared with the mentioned drivers).
  - Defense submission - Active from the moment the investigation is triggered, and automatically disabled once a configured period of time elapses. In this stage, the mentioned drivers are allowed to submit defenses and additional evidence relevant to the case opened.
  - Investigation deliberation - Active once the defense submission ends, and automatically disabled once a configured period of time. Aims to allow stewards to vote on the final verdict, providing justification. After this period is over, the verdict is posted to the configured verdict channel of all divisions the reported drivers are assigned to (if the driver is assigned to two or more divisions, repeating posts must have the indication "(repost)").
- Involved driver - A group of drivers consisting of all drivers formally added to a report and the driver who triggered the report.
- Ticket - A user-submitted incidence which may be either a report, an appeal or a Code of Conduct investigation. The former two may be public (seen by any driver of the division to which they pertain) or private (seen only by drivers involved and the stewarding team). The latter is always private to the utmost (only the steward team and the driver involved can see this).
- Report - May also be referred to as stewards' report. This is an incidence submitted by either a driver or by a member representing the steward team as an anonymous collective, which may refer to one or more other drivers, pertaining to an incident that occurred during the most recent round. A report ticket object to be persisted in the database will be under a round's own persisted object, and must contain the following information:
  - Unique ID in the "S<x>_D<y>_R<z>_<w>" format, where <x> is the number of the season, <y> the tier of the division, <z> the number of the round, and <w> the number of the report pertaining to this season, tier and round. The season, division, round values can be extracted from this ID.
  - Status, which relates to the stewarding cycle stages as follows:
    - INIT - Appeal just opened, which moves its stage to Defense submission.
    - Delib - Report deliberation.
    - CLOSE - Report deliberation over, votes tallied up, outcome decided and verdict posted.
  - User IDs of the involved drivers.
  - Session of the round (Sprint Qualifying, Sprint Race, Feature Qualifying, Feature Race).
  - Lap (empty if round was a Qualifying round).
  - Effective steward team.
    - For each member, there will be a flag noting the head steward of the effective steward team.
    - For each member, the ID of the outcome object is recorded (empty if no vote was cast).
  - ID of the outcome object settled after all votes are weighted, at the end of the deliberation phase.
- Appeal - A special kind of ticket submitted by a driver or by a member representing the steward team as an anonymous collective which aims for this ticket to be judged once more, so that the ultimate verdict is passed. The submission of an appeal by a driver may require 1 or more appeal tokens to be spent. An appeal ticket object to be persisted in the database will follow the same rules as a report ticket object, with the following exceptions:
  - Status, which relates to the stewarding cycle stages as follows:
    - INIT - Appeal just opened, appeal submission stage is still underway.
    - Delib - Appeal deliberation.
    - CLOSE - Appeal deliberation over, votes tallied up, outcome decided and verdict posted.
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
- <NEW COMMAND> A "steward toggle-conflict" command will be made available to league managers, which shall determine whether stewards who are also drivers are able to participate in the stewarding cycle of tickets that pertain to divisions they are driving in, accepting a possible conflict of interest.
  - By default this setting is on, meaning stewards can review reports/appeals pertaining to the division they are driving for, and that leagues implicitly accept a possible conflict of interest.
- <NEW COMMAND> A "steward final-justification-mode" command will be made available to league managers, which shall alter the method through which the default verdict justification text is determined, which is to be provided by the bot once a verdict is reached for any ticket. The modes available are:
  - Longest - Active by default - The bot takes the longest justification, in character count, provided by the stewards.
  - Own - The bot takes the effective head steward's own justification for the driver-outcome pair, if his vote coincided with the winning option.
  - LLM - The bot feeds the justifications given by all stewards who voted on the driver-outcome pair to a remote or local-running LLM, which will then provide a full professional sounding text for the justification. This mode cannot be chosen if there is no valid LLM token/communication set up.
  - By default, this value shall be set to "longest".
- <NEW COMMAND>  A "steward fallback-justification-mode" command will be made available to league managers, which shall alter the backup method through which the default verdict justification text is determined, which is to be provided by the bot once a verdict is reached for any ticket. The methods available are the same as the ones listed in the requirement for "steward final-justification-mode".
  - By default, this value shall be set to "own".
  - The method chosen by this command cannot be the same as the one chosen with "steward final-justification-mode".
  - If "steward final-justification-mode" is changed to "longest" when "steward fallback-justification-mode" is already "longest", then the latter shall change to "own".
  - If "steward final-justification-mode" is changed to "own" when "steward fallback-justification-mode" is already "own", then the latter shall change to "longest".
  - If "steward final-justification-mode" is changed to "LLM" when "steward fallback-justification-mode" is already "LLM", then the latter shall change to "longest".
- If neither "steward final-justification-mode" nor "steward fallback-justification-mode" are feasible options, then the bot will provide either "longest", if possible, or no text at all.
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
  - Contrary to the others, this command may be accepted if any report deliberation or appeal deliberation phases are on-going. This means that the outcome object must be appended to all ticket data objects that are not in status "closed".
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

## Stewarding cycle
- All inputs of the stewarding cycle must be auditable via the steward log channel. Attempts to file a report (and its data), driver addition/removal to tickets, etc etc etc. All logs must include the display name (and user ID) of the input.
- It is imperative that the steward team is seen as a unified front, so no public messages will identify or mention a member of the steward team when acting in their capabilities as steward.
  - However, as stated above, steward logs shall identify them when needed.
- By default, the bot shall post a "Ticket submission is currently closed for <X> division" in a division's report channel.
- If appeals are not enabled, once a report's verdict is posted in the appropriate channel by the end of the report deliberation phase, the incident (report) is deemed closed and final.
- If appeals are enabled, if a report is not appealed by the time the appeal submission phase ends, the incident (report) is deemed closed and final.
- Once appeal verdicts are posted at the end of the appeal deliberation phase, the incident (appeal and the report it pertains to) is deemed closed and final.

### Report submission
- At the scheduled start datetime of a round for a given division, the bot shall delete the "default" message, and post a "Report incident" button to the configured ticket channel of the division, without mentioning the division role.
- At the scheduled start datetime of a round for a given division, a countdown with the period of time configured by "steward report-submission-period" will start. Once this time elapses, the default message shall be displayed once more in the divisions' reports channel, and the "Report incident" button removed.
- When a user presses the "Report incident" button, a modal dialog shall appear, with the following elements:
  - Season - Mandatory - Integer - Automatically generated, cannot be changed by anyone. Derived from the current season's number.
  - Division - Mandatory - String - Automatically generated, cannot be changed by anyone. Derived from the division to which the report channel is associated.
  - Round - Mandatory -  Integer - Automatically generated, cannot be changed by anyone. Derived from the most recent round that took place.
  - Involved drivers - Optional - 0..n mentions - Other drivers directly or indirectly involved in the incident, whose footage or evidence may be of use to the steward team's deliberations. <CHECK FEASIBILITY OF USING A CHECKLIST WITH ALL DIVISION DRIVERS>
    - These drivers must be assigned to the division this report pertains to.
    - The report will not be valid if there is any entry here that is not an involved driver.
    - The driver who triggered the report is considered an involved driver, and is not distinct from the other drivers for the purpose of this ticket.
  - Session - Mandatory - Dropdown - Select which session the incident took place in. Options available depend on round format: if round format is sprint, then the options available shall be "Sprint Qualifying", "Sprint Race", "Feature Qualifying" and "Feature Race", otherwise, the options available are just "Qualifying" and "Race".
  - Lap - Mandatory if session = "Sprint race" or "Feature race", greyed out otherwise - Integer - The race lap in which the incident took place.
  - Complaint - Mandatory - String - Full description of the incident as per the reclaimant's understanding.
  - Evidence files - Optional - 0..5 media (image or video) - One or multiple images or video files that provide basis for the claims in the complaint.
  - Evidence links - Optional - 0..5 links - One or multiple images or video links that provide basis for the claims in the complaint.
    - Between "evidence files" and "evidence links", there must be at least one file/link. Otherwise, the report will not be valid.
- The validation of the information will be performed before closing the modal, so that users do not have to input information twice.
  - If this is not possible, if the information is not valid, then the modal shall be reopened with the same information.
- After valid submission, the report will be henceforth be identified with a unique ID following the format "S<x>_D<y>_R<z>_<w>", where <x> is the number of the season, <y> the tier of the division, <z> the number of the round, and <w> the number of the report pertaining to this season, tier and round. This report ID will be utilized for some steward commands.
- After valid submission, a channel bearing the ticket unique ID as the title will be created, with the information from the modal dialog input by the reportee summarized and posted as the header message in the channel. All involved drivers will be mentioned properly in this message.
- If the effective head steward is one of the involved drivers, or has a conflict of interest as defined by "steward toggle-conflict", the bot will post a message with a button to assign effective head steward for the ticket to someone else of the steward team. The user will be validated for the criteria above, and after they are designated effective head steward, the former one will be removed from the effective steward team for the report.
  - If the effective head steward does not assign anyone else by the time the report deliberation phase is reached, a random member of the effective steward team is to be chosen by the bot for this position.
- After valid submission, the ticket's state changes immediately to the defense submission stage, and a report data object is recorded in the database associated to this season, division and round. All remaining time in the defense submission stage will be added to the total of the defense submission stage.
  - This effectively means that all tickets' defense submission stage ends at the same time, regardless of the initial report submission taking place at the start or at the end of the report submission stage.
- If there are no reports submitted until this phase ends, then the stewarding cycle is considered closed.

### Defense submission
- Once the period of time configured for the duration of the report submission stage elapses, a countdown with the period of time configured by "steward defense-submission-period" will start. Once this time elapses, the ticket will enter the report deliberation phase.
- Once this phase is entered, the following buttons will be posted on the channel after the header message is posted:
  - Request input from driver - Can only be used by members of the effective steward team. When pressed, a modal is opened so that one user is mentioned (mandatory) and a justification is input (optional). When confirmed, the exact behavior depends on who triggered the exclusion:
    - If the one requesting this exclusion is a regular member of the effective stewarding team, a modal is opened so that a user mention (mandatory) and a justification is input (mandatory). When confirmed, the bot will post a message tagging the effective head steward for the ticket, stating that a steward has requested evidence from that driver, and printing the justification. If approved, then the driver will be added to the ticket, and the request process message will be deleted. If rejected, the bot will post a message to note that the request was rejected, and the request process message will be deleted.
    - If the one requesting this exclusion is the effective head steward, a modal is opened so that a user mention (mandatory) and a justification is input (mandatory). When confirmed, the driver will be added to the ticket.
    - When added, the driver will be given the same permissions as other involved drivers.
    - The command is rejected if the targeted is the one who initiated the ticket, if they are in the involved drivers list already, or if the ticket is already in report deliberation or appeal deliberation.
  - Remove driver - Can only be used by the effective head steward, rejects input by others. When pressed, a modal is opened so that one user is mentioned, and once confirmed, that driver will be removed from the list of involved drivers, and have their permissions as an involved driver removed.
    - The command is rejected if the targeted user is the one who initiated the ticket, or if they are not in the involved drivers list, or if the ticket is already in report deliberation or appeal deliberation.
  - Request remove driver - Can only be used by members of the effective steward team for this ticket and drivers, rejects input by the effective head steward. When pressed, a modal is opened so that one user is mentioned (mandatory) and a justification is input (optional). When confirmed, posts a message tagging the head steward identifying which driver is to be removed, informing of the justification given. Once the effective head steward confirms it, that driver will be removed from the list of involved drivers, and have their permissions as an involved driver removed.
    - The command is rejected if the targeted user is the one who initiated the ticket, or if they are not in the involved drivers list, or if the ticket is already in report deliberation or appeal deliberation.
  - Request exclusion - Can be used by any member of the effective steward team for this ticket. The exact behavior depends on who triggered the exclusion:
    - If the one requesting this exclusion is a regular member of the effective stewarding team, a modal is opened so that a justification is input (mandatory). When confirmed, the bot will post a message tagging the effective head steward for the ticket and identifying the steward that requested the exclusion, plus the justification. If approved, then the steward will be removed from the effective steward team for this ticket. If rejected, then a modal will be opened to assign a justification (optional), and after confirmation, theeffective head steward will be mentioned in a message in the steward command channel informing of the decision.
    - If the one requesting this exclusion is the effective head steward, a modal is opened so that a justification is input and another member of the effective stewarding team is mentioned, so that they will be assigned effective head steward privileges for this ticket. Once confirmed, the member will be requested to accept or reject. If accepted, they are made the effective head steward for the ticket's effective stewarding team. If rejected, then a modal will be opened to assign a justification (optional), and after confirmation, the steward will be mentioned in a message in the steward command channel informing of the decision.
    - If a member of the effective stewarding team requests an exclusion having already cast their vote in the deliberation phase, their vote will be excluded.
  - Mute - Can be used by anyone of the steward team. When pressed, a modal is opened so that the user inputs 1..n mentions of users from whom to remove write message/attach file permissions.
    - The command is rejected if the user is not the one who initiated the ticket, or if they are not in the involved drivers list. This can be used on stewards as well.
  - Unmute - Can be used by anyone of the steward team. When pressed, a modal is opened so that the user inputs 1..n mentions of users to whom to get write message/attach file permissions.
    - The command is rejected if the user is not the one who initiated the ticket, or if they are not in the involved drivers list. This can be used on stewards as well.
- When a driver is added to a ticket, they will be considered an involved driver, and given the same permissions as other involved drivers.
- When a driver is remove from a ticket, they will no longer be considered an involved driver, and the permissions of an involved driver will be removed from him.
- When a driver is added to or removed from a ticket, the bot will edit the "header message" (containing the post information) to account for the addition/removal, and post a new message informing of this change, mentioning the driver and a justification if given.
- If a driver was added to or removed from a ticket as a result of a request, then the message with the request will be deleted after confirmation/rejection.
- During this phase, regular members of the effective stewarding team only have read permission for the channel. They shall be able to utilize the aforementioned buttons (as per their own specification).
- During this phase, the effective head steward shall have read/write and attach media permission for the channel.
- During this phase, the user who initiated the report and all users marked as involved drivers shall have read/write and attach media permission for the channel.
- This phase cannot be terminated early.

### Report deliberation
- Once this phase is entered, the involved drivers lose all permission to read, write or attach media to the channel.
- Once this phase is entered, the members of the effective reporting teams will gain the permission to write or attach media to the channel.
- Once this phase is entered, a countdown with the period of time configured by "steward report-deliberation-period" will start.
- Once this phase is entered, a single button titled "Vote" is posted by the bot. This button will serve for members of the effective stewarding team to cast, modify, or remove their vote on the outcome of the report. The button opens a modal which is as follows:
  - Steward's display name - String - Greyed out, cannot be changed. Display name of the steward that initiated the vote.
  - Report ID - String - Greyed out, cannot be changed. Unique ID of the report which is being voted on.
  - Driver - Dropdown - Contains the display names of the driver all involved drivers. This is the user who will receive the outcome.
  - Infringement - String - Optional - Optional string rule number which was allegedly violated. Useful for final verdict write-up.
  - Outcome - Dropdown - Mandatory - Dropdown containing all outcomes currently configured, displaying their IDs, allowing the steward to select 1 of them.
    - PROBLEM WITH THIS DESIGN: what if a steward wants to penalize multiple drivers? this current approach stops that. A method I thought of would be to construct the modal dynamically, with each involved drivers getting an outcome field, but that would make the tally of the votes and the generation of the verdict a pain.
  - Justification - String - Mandatory - A free form text with a 1000 character limit for the steward to give their reasonings for the vote.
  - Two or three buttons at the bottom - "Cancel", "Remove vote" if the steward is reopening the vote dialog after having voted, and "Confirm".
- A steward's vote is only valid via "Confirm" if all mandatory fields are filled.
- Once a steward's vote is deemed valid, all data for the vote will be recorded and persisted.
- If a steward reopens the vote modal dialog after having voted, the dialog will be pre-filled with their previous data.
- If the steward has chosen "Confirm" upon reopening the dialog, the previously persisted vote information will be modified to align with the current information in the modal.
- If the steward has chosen "Cancel" upon reopening the dialog, no change is to occur to their current vote.
- If the steward has chosen "Remove vote" upon reopening the dialog, the previously persisted vote information will be deleted, and it will be as if the steward had never voted.
- At the end of the countdown period for this phase, all stewarding team members except for the effective head steward lose message write permission for the report channel.
- At the end of the countdown period for this phase, all outcomes from votes will be counted. For the purpose of counting, only cast votes will be taken into consideration for the determination of plurality. This means that in a situation where the effective stewarding team for a ticket consists of 9 people, and only 5 of those people have voted, only those 5 votes will be used for assessing the ultimate verdict.
- Driver-outcome pairs are both considered a vote for the purpose of vote tallying. This means that "Driver A-Outcome X" and "Driver B-Outcome X" are votes for two different things.
    - The sole exception for this is Outcome NFA, which is a fixed, immutable set that is always part of the list of outcomes. "Driver A-Outcome NFA" and "Driver B-Outcome NFA" are effectively the same vote expressed twice.
- If any one driver-outcome pair reaches plurality without a tie, the ultimate result of the report will be that outcome being applied to that driver.
- If two or more driver-outcome pairs are tied, and the effective head steward has voted in one of them, the ultimate result of the report will be the outcome voted by the effective head steward.
- If two or more driver-outcome pairs are tied, and the effective head steward has not voted in either of them (also covers the possibility of the effective head steward not voting at all), the bot will trigger a cascade of events:
  - The bot shall post a message on the verdicts channel saying "Verdicts for round <x> are slightly delayed, please stand by."
  - On the report's channel, the bot shall post a button per driver-outcome pair that is tied as the most voted option. The one assigned as effective head steward will be the only one able to use these buttons. The pair chosen dictates the ultimate verdict.
  - A timer counts down 1 hour from the moment the buttons are posted; if the effective head steward has not picked an driver-outcome pair once this timer runs out, then the driver-outcome pair that reached their final vote count the earliest will be the final verdict.
- Once the ultimate result of a report is reached through any of the mediums above, the bot will:
  - Post a message informing the effective head steward of the decision reached (which driver is struck with what penalty, what the majority voted infringement was), providing a default justification text determined via the method configured by "steward final-justification-mode" (or "steward final-justification-mode", if the primary method is not feasible).
  - Buttons usable only by the effective head steward of the ticket, one for accepting the default justification text as provided by the bot as-is, another to modify the default justification via a modal (text is already autoloaded into modal prompt).
  - In a message different from the one above, the justifications provided by all stewards that chose this driver-outcome pair will be presented as a reference.
  - Start a timer counting down 1 hour from the moment the buttons are posted; if the effective head steward has not confirmed the justification text once this timer runs out, then the one provided by the bot will be utilized for the verdict post.
- Once the justification message is settled, either via effective head steward confirmation or by timeout, the bot will remove all write permissions to the channel, and generate the final output to be posted in the verdicts channel.
  - The format of the final output is determined in another section.
- Only after the final output is determined for all reports pertaining to a given round of a given division, will they be posted, in report ID alphabetical order (which will coincide with the submission order), in the verdicts channel.
- The channel will not be deleted even after publishing of the verdicts.
- The report deliberation phase is only considered over once all reports pertaining to a given round of a given division are posted to the appropriate channel.
  - If appeals functionality is enabled, then the stewarding cycle will move on to that phase.
  - Otherwise, then the stewarding cycle is considered closed.
- Once the report deliberation phase is considered over, the round results will be reposted, with all accured penalties factored in.
  - This functionality is somewhat implemented already, just a matter of reusing it.
  
### Appeal submission
- Once this phase is entered, the bot shall delete the "default" message (written again once the report submission phase ends), and post an "Appeal incident" button to the configured ticket channel of the division, without mentioning the division role.
- Once this phase is entered, a countdown with the period of time configured by "steward appeal-submission-period" will start. Once this time elapses, the default message shall be displayed once more in the divisions' reports channel, and the "Appeal incident" button removed.
- When a user presses the "Appeal incident" button, it will be verified if they meet the requirements for lodging an appeal, which shall be:
  - ...
- Once it is determined that the user meets the requirements to perform an appeal, a modal dialog shall appear, with the following elements:
  - Season - Mandatory - Integer - Automatically generated, cannot be changed by anyone. Derived from the current season's number.
  - Division - Mandatory - String - Automatically generated, cannot be changed by anyone. Derived from the division to which the report channel is associated.
  - Round - Mandatory -  Integer - Automatically generated, cannot be changed by anyone. Derived from the most recent round that took place.
  - Report ID - Mandatory - String - The unique ID of the report that the drivers wishes to appeal, as per the report channel's title.
  - Justification - Mandatory - String - Reason for the appeal, outlining the reason as to why the user disagreed with the initial verdict handed out.
  - Evidence files - Optional - 0..5 media (image or video) - One or multiple images or video files that provide basis for the claims in the complaint.
  - Evidence links - Optional - 0..5 links - One or multiple images or video links that provide basis for the claims in the complaint.
    - Between "evidence files" and "evidence links", there must be at least one file/link. Otherwise, the appeal will not be valid.
- The validation of the information will be performed before closing the modal, so that users do not have to input information twice.
  - If this is not possible, if the information is not valid, then the modal shall be reopened with the same information.

- <NEW COMMAND> steward retract-verdict - lets effective head steward revise the justification in a verdict
- If there are no appeals submitted until this phase ends, then the stewarding cycle is considered closed.

### Appeal deliberation
- 

### Cycle close
- 

## Conduct cycle
### Trigger
- <NEW COMMAND> A "steward conduct-inv start" command will be made available to the head steward and temporary head steward roles to be utilized in the channel configured by "steward command-channel" exclusively, which will have as input 1 or more user IDs of a server member (not necessarily a driver, only requires the "base_role" as configured by "module enable signup"), so that a CoC investigation is opened against said user.

### Defense submission
- <NEW COMMAND> A "steward conduct-inv add" command will be made available to the league managers... <<< --- TBD --- >>>

### Investigation deliberation
- 

## Verdict output
### Textual


### Image