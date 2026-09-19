# Stewarding module
- <COMMAND CHANGE> "steward" shall be added to the list of accepted values in the "module enable" and "module disable" commands. The stewarding module may be enabled via the "module enable" command, akin to the weather and signup modules. It is a league admin's command.
- <COMMAND CHANGE> The stewarding module may be disabled via a "module disable" command akin to the weather, signup, results and attendance modules. It is a league admin's command.
- The stewarding module is disabled by default.
- The stewarding module may be enabled until the season's placements are first confirmed, or while the server holds no active season.
- The stewarding module may be disabled at any time no stewarding cycle stands open in any division. The refusal shall name each division whose cycle is open and when that cycle is due to close.
- Season bans and league bans shall remain in force while the stewarding module is disabled, as set out under Bans. This is the sole exception to a disabled module ceasing its work: a ban, once on a driver licence, is a fact about the driver and not work of the module.
- The stewarding module depends upon the results & standings module. It may not be enabled while that module is disabled.
  - Disabling the results & standings module shall be refused while any stewarding cycle stands open, the refusal naming each division whose cycle is open and when it is due to close.
  - Where no cycle stands open, disabling the results & standings module shall disable the stewarding module with it, and the cascade shall be reported, as the core specification sets out.
- If the stewarding module is enabled, then it shall not be possible to use the penalty and appeal functionality in the results & standings module. This functionality will hereby be governed by the stewarding module.
- The stewarding module is connected to the attendance module, and modifies its outputs.
  - While the stewarding module is enabled, the penalty review of the results & standings module, in which attendance pardons are given, is not used. Attendance pardons shall then be given by league managers through a command of the attendance module, taking the same inputs and validated the same way, from the posting of the round's results until its attendance points are distributed. They remain a league manager's decision and never a steward's, and their justification is written to the log channel alone.
  - A driver seeking a pardon asks the league's managers as the league sees fit; the bot offers no request of its own.
  - While the stewarding module is enabled, a round's attendance points shall be distributed once its report verdicts are posted, the moment the post-race penalties are settled, and the pardon window closes then. The attendance sanctions they give rise to are posted after that batch of verdicts, under its header.
- The stewarding module is connected to the signup module, and modifies its outputs.
- Stewarding module activation status shall be displayed in the configuration review and in the placements review.
- This module must work with the fake driver rosters used in test mode.
- The stewarding module expands the two tiers of authority the core specification sets out to four: the league admin (level 1), the league manager (level 2), the head steward (level 3) and the steward (level 4). The two new levels are impermanent, each being held by whoever the bot holds on the stewarding team, or as its head steward, at the time.
  - Levels 3 and 4 are independent of levels 1 and 2 and of one another, so as to distribute power and to keep league administration and management from interfering with stewarding. Neither is carried within any other level: a league admin or league manager holds no steward authority unless they also hold a steward role.
  - A member may hold several levels at once.
  - The head steward is a steward as well. Level 3 and its role are distinct from level 4 and its role, but the head steward holds both, and only a steward may be made head steward.
  - The numbers name the levels and do not rank levels 3 and 4 beneath the others.
- Throughout this document, "league admins" and "league managers" are to be understood as the core specification defines them, level 1 and level 2 respectively. A league admin carries the league manager's level within it, as core sets out.

## Concepts
- Driver licence - An individual record held by each driver profile of the driver's standing in the league. On it are recorded the driver's warning points, penalty points and discipline points, each with its active status and date, and the driver's bans, as follows:
  - Active bans - the number of qualifying bans and the number of race bans the driver has yet to serve, whether the driver is season banned, and how many further season bans are stacked behind the active one, and whether they are league banned. A driver holding one or more qualifying bans is qualifying banned, and one holding one or more race bans is race banned.
  - Ban history - the number of qualifying bans, race bans, season bans and league bans the driver has received throughout their time upon the server. A ban is added to the history the moment it is received.
  - Appeal tokens - the number of appeal tokens the driver holds, while appeals cost tokens.
  - Likewise, a tally of the total of each penalty type is kept.
  - The driver licence is the state of a driver's bans. A driver is banned while their licence holds an active ban, and no driver state bars them otherwise.
  - A driver licence belongs to the driver profile, not to a Discord account. Any of a driver's accounts shall name them wherever this module names a driver — a report, an appeal, a CoC investigation, a vote, a revoke command. A ticket keeps the account it was lodged under, as the core specification requires of every record, and is read as the driver's.
  - Where two driver profiles are merged, as the core specification allows, their licences shall be merged into one, so that no sanction is escaped by holding it under another profile:
    - Warning points, penalty points and discipline points shall all be carried, each keeping its own date and expiry. Nothing expires or begins again by reason of the merge.
    - The qualifying bans and race bans each has yet to serve shall be added together, and so shall the counts of their ban histories.
    - Season bans shall stack as any season bans do. Where either is league banned, the merged driver is league banned, replacing any season ban as a league ban does.
    - The merged driver shall hold the lower of the two numbers of appeal tokens.
    - An auto-rule threshold crossed on either licence shall be held as crossed on the merged one, so that the merge itself punishes nothing already punished. The merged licence shall be checked against the auto-rules at the next cycle close, as any licence is.
    - A merge shall not be refused because either driver is banned, merging being how a league brings a banned driver's other accounts under their ban.
  - Where another account is made a driver's current one, the season ban and league ban roles shall move to it as the driver's other roles do: taken from the account replaced where it is still in the server, and reported to the league manager where Discord will not move them.
  - Where another account is made a driver's current one while they are party to an open ticket, the ticket shall follow the driver. Their access to every open ticket channel shall move to the new current account and be taken from the account replaced, and the ticket shall read the new account as the involved driver.
  - A steward's vote is a record, and keeps the account it was cast under.
  - A past account of a driver cannot be a steward, head steward or temporary head steward, and shall not be added to the stewarding team. Stewarding authority is thus held only by an account belonging to no driver or by a driver's current account, so that every check this module makes of a steward against a driver compares one person with another.
  - Where the driver is on the stewarding team, their place on it, and any head steward or temporary head steward appointment, shall move to the new current account with the roles mirroring them.
- Steward - A trusted user with level 4 permission, being a member of the stewarding team the bot keeps, and denoted with a special role mirroring that team which may be different from that of league managers, which are able to see tickets and pass judgement on them.
- Head steward - A privileged user with level 3 permission denoted with a special role that serves as the leader of the stewarding team. It is mandatory that a stewarding team has a head steward. They may confer acting head steward responsibilities onto another member of the stewarding team for a temporary period. By default, the head steward is the effective head steward for all tickets.
- Acting head steward - Also referred to as temporary or temp head steward. A privileged user denoted with a special role with similar privileges as the head steward, which lasts only for a limited amount of time, as a result of being deferred head steward responsibilities temporarily. While a temporary head steward is active, they are the effective head steward by default on every ticket in place of the head steward, and may use the commands of the head steward alongside them. The head steward keeps every command of theirs, ending the temporary appointment among them.
- Effective head steward - The designated head steward for a specific ticket. By default, this is the head steward, or the acting head steward if the latter functionality is in use. This user is able to add or remove drivers from tickets, approve requests to do either, or to approve steward exclusions from tickets. Their vote may also serve as a tie-breaker when outcomes voted for a given ticket are equally split. The effective head steward for a given ticket may change while the stewarding cycle is underway.
- Stewarding team - The collective composed of all stewards.
- Effective stewarding team - The members of the stewarding team that have the ability to participate in the stewarding cycle of a given report. Depending on the drivers involved in the ticket, configurations, temporary head steward status, or exclusion requests, it is possible that not all permanent members of the stewarding team are part of the effective stewarding team. This list is mutable while the stewarding cycle is underway. By default, the effective stewarding team is composed of:
  - Effective head steward.
  - All stewarding team elements EXCEPT:
    - Those driving in the division to which a report pertains if steward conflicts of interest are not allowed.
    - Any involved driver that belongs to the stewarding team.
- Stewarding cycle - The full process for stewarding after a round is scheduled to take place. This is an informal concept, meaning it is not a strict definition, just an auxiliary name. It kicks off at the time a round is scheduled to happen with the enabling of reports for that round, and ends only when all appeals' verdicts are posted (if there were any appeals) OR when all reports' verdicts are posted (if there were any reports) OR once the report submission deadline passes (if there were no reports). It is composed of the following stages:
  - Report submission - Active starting at the scheduled round time, and automatically disabled after a configured amount of time after the scheduled round time. Period of time in which drivers or the stewarding team can initiate reports against other drivers of the division.
  - Defence submission - Active for each ticket from the moment it is lodged, and disabled for every ticket of the round at once, when the configured period has run from the end of report submission. Aims to allow other drivers to provide their own version of events and evidence.
  - Report deliberation - Active from the moment the defence submission stage ends, and automatically disabled after a configured period of time. Aims to allow stewards to vote on the final verdict, providing justification. After this period is over, verdicts of all reports are posted to the configured channel.
  - Appeal submission - Active for a configured period of time once the report deliberation ends. In it, any driver of the division may appeal a report's verdict, where they hold the appeal tokens it costs.
  - Appeal deliberation - Active from the moment the appeal submission ends, and automatically disabled after a configured period of time. Aims to allow stewards to vote on the final verdict, providing justification. After this period is over, the verdicts of all appeals are posted to the configured channel. After this, the round is taken as final, and its results cannot be changed.
- Conduct investigation cycle - The full process for a Code of Conduct investigation can be initiated at any time, by the head steward or temporary head steward, or by any steward where the league allows it. This is an informal concept, meaning it is not a strict definition, just an auxiliary name. It kicks off when the head steward (or temporary head steward) initiates a Code of Conduct investigation targeted at one or more specific driver(s), submitting a justification and evidence (which may be private to the stewarding team or shared with the mentioned drivers).
  - Defence submission - Active from the moment the investigation is triggered, and automatically disabled once a configured period of time elapses. In this stage, the mentioned drivers are allowed to submit defences and additional evidence relevant to the case opened.
  - Investigation deliberation - Active once the defence submission ends, and automatically disabled once a configured period of time. Aims to allow stewards to vote on the final verdict, providing justification. After this period is over, the verdict is posted as set out under Conduct cycle: the investigation is private, and its verdict public.
- Complainant - The driver who lodged a report, or the stewarding team as a whole for a steward's report. A driver who is complainant is an involved driver of their report; the stewarding team, as complainant, is not.
- Principal division - The highest tier division (the lowest tier number) in which a driver holds a full-time seat. A full-time driver of a tier 2 division who is a reserve driver in tier 1 has tier 2 as their principal division. A driver who is a reserve driver alone, or who holds no seat, has no principal division.
- Feature race and feature qualifying - The Feature Race and Feature Qualifying of a sprint round, or the Race and Qualifying of a round of any other format, where they are the only ones. The league sees each session named as the round's results name it.
- Involved driver - A group of drivers consisting of all drivers formally added to a report and the driver who triggered the report.
  - A ticket shall hold at most 25 involved drivers, or 25 involved users for a CoC investigation, the complainant among them. A report naming more shall not be valid, and adding a driver or user to a ticket already holding 25 shall be refused.
- Ticket - A user-submitted incident which may be either a report, an appeal or a Code of Conduct investigation. Every ticket is private: a report or an appeal is seen only by its involved drivers and the stewarding team, and a CoC investigation only by the stewarding team and the users involved in it.
- Ticket channel - The channel of a division, set by "division ticket-channel", in which the "Report incident" and "Appeal incident" buttons are posted. There is one per division.
- A ticket's channel - The channel the bot creates for a single ticket, in which its defence and deliberation take place. It may be called the report's, the appeal's or the investigation's channel where the kind of ticket matters.
- Report - May also be referred to as stewards' report. This is an incident submitted by either a driver or by the stewarding team as an anonymous collective (a steward's report), which may refer to one or more other drivers, pertaining to an incident that occurred during the most recent round. It is identified by:
  - Unique ID in the "S<x>_D<y>_R<z>_<w>" format, where <x> is the number of the season, <y> the tier of the division, <z> the number of the round, and <w> the number of the report pertaining to this season, tier and round, always written in three digits (001, 002 … 999), so that the IDs of a round sort in the order the reports were lodged. The season, division, round values can be extracted from this ID.
- Appeal - A special kind of ticket submitted by a driver which aims for a report to be judged once more, so that the ultimate verdict is passed. Only drivers appeal; the stewarding team does not appeal its own rulings. A steward who drives in a division may appeal that division's rulings, and is treated as any other driver in doing so. The submission of an appeal may require 1 or more appeal tokens to be spent. It is identified by the unique ID of the report it appeals, suffixed as set out under Appeal submission.
- Every ticket records everything its stages set out — its unique ID, the round and session it concerns, its complainant and involved drivers, its complaint and evidence, its effective stewarding team and effective head steward, every ballot cast, and its verdict — and keeps it for the life of the league. The stage a ticket stands in is the stage of its cycle, as set out under each cycle.
- Appeal token - A special kind of currency that may be required for drivers to be able to submit an appeal. Appeal tokens are accumulated on a driver licence, and expire upon the current season's end. Upon a successful appeal, drivers are returned their spent tokens, unless the league has switched this off with "steward appeal refund-toggle".
- Code of Conduct investigation - May also be referred to as a CoC investigation. A special kind of ticket and the only one which is not linked to a round, instead being linked to a driver; as such, it cannot lead to any changes in results (time penalties, warning or penalty points). It may be initiated by the head steward or temporary head steward, and by any steward only where "steward conduct steward-start-toggle" allows it. It cannot be appealed, and any decisions made are final. This functionality is optional and is disabled by default.
- Outcome - A standardised penalty table item for reports and appeals, which draws a relationship from a "standard penalty description/case" to a "standard penalty", which may be one, or multiple between time penalties, disqualifications, warning points, penalty points, qualifying bans, race bans, season bans and league bans. Each outcome has a unique ID, its identifying shorthand, which is used when voting. Additionally, there is also a "No Further Action" outcome which sets none of the possible punishments onto a driver. The outcome is decided by plurality among the votes cast, as set out under each deliberation. The list for outcomes is managed separately from that of conduct outcomes, so there may be overlap of IDs of items between the two.
- Conduct outcome - A standardised penalty table item for CoC investigations, which draws a relationship from a "standard penalty description/case" to a "standard penalty", which may be one, or multiple between discipline points, qualifying bans, race bans, season bans and league bans. Each conduct outcome has a unique ID, its identifying shorthand, which is used when voting. Additionally, there is also a "No Further Action" outcome which sets none of the possible punishments onto a driver. The conduct outcome is decided by plurality among the votes cast, as set out under each deliberation.  The list for conduct outcomes is managed separately from that of outcomes, so there may be overlap of IDs of items between the two.
- Time penalty - A possible direct outcome of a verdict for a report or appeal. Time is added or removed to a participant's total race time; note that it is not possible to remove time from a participant's total race time such that the sum of the in-race and post-race time penalties minus the time removed is lower than zero seconds (this functionality is already implemented in the results & standings module)
- Disqualification - A possible direct outcome of a verdict for a report or appeal. The driver's entry in the session of the incident is invalidated and ranked last in that session, as the results & standings module disqualifies an entry. It applies to qualifying and race sessions alike.
- Warning point - A possible direct outcome of a verdict for a report or appeal. Warning points serve as the lightest of penalties, and a minor rebuke to a driver's on-track behaviour. Warning points are accumulated on a driver licence, and expire as configured by "steward penalty warning-point-expiry". A league may turn warning points into further sanctions through the auto-rules. Warning points are only applied to a driver licence once the stewarding cycle in which it was bestowed is complete.
- Penalty point - A possible direct outcome of a verdict for a report or appeal. Penalty points are accumulated on a driver licence, and expire as configured by "steward penalty penalty-point-expiry". Depending on configuration, the accumulation of penalty points may lead to additional sanctions being applied to a driver. Penalty points are only applied to a driver licence once the stewarding cycle in which it was bestowed is complete.
- Discipline point - A possible direct outcome of a verdict for a CoC investigation. Discipline points are accumulated on a driver licence, and expire as configured by "steward conduct discipline-point-expiry", or not at all. Depending on configuration, the accumulation of discipline points may lead to additional sanctions being applied to a driver. Like CoC investigations, this functionality is optional and is disabled by default.
- Qualifying ban - A possible direct or indirect outcome of a verdict for all ticket types. Qualifying bans are appended to a driver licence. The driver that receives this sanction is thereby forbidden from taking part in all qualifying sessions of the next round they take part in of the division in which it is to be served, as set out under Bans, be it in the current season or a later one. This means that they may not set a valid lap in any of the qualifying sessions, but they must be present in the classification of the qualifying and race sessions. A failure to serve it is posted as an Automated Ruling, and the ban carries on, active on the licence; the stewarding team may lodge a report upon it where it judges a rule was broken. Qualifying bans are only applied to a driver licence once the stewarding cycle in which it was bestowed is complete.
- Race ban - A possible direct or indirect outcome of a verdict for all ticket types. Race bans are appended to a driver licence. The driver that receives this sanction is thereby forbidden from taking part in the next round of the division in which it is to be served, as set out under Bans, be it in the current season or a later one. This means that they may not be present in the classification of any sessions for the round they are banned for. A failure to serve it is posted as an Automated Ruling, and the ban carries on, active on the licence; the stewarding team may lodge a report upon it where it judges a rule was broken. Race bans are only applied to a driver licence once the stewarding cycle in which it was bestowed is complete.
- Season ban - A possible direct or indirect outcome of a verdict for all ticket types. Season bans are appended to a driver licence. The driver that receives this sanction loses all their current seats for all divisions, full-time and reserve both. Drivers with a season ban will be assigned a special role, and will be unable to engage with the signup wizard. A season ban will expire as configured by "steward penalty season-ban-type": after a number of rounds, upon a season's end, or after a fixed period of time. Season bans are only applied to a driver licence once the stewarding cycle in which it was bestowed is complete.
- League ban - A possible direct or indirect outcome of a verdict for all ticket types. League bans are appended to a driver licence. The driver that receives this sanction thereby loses all their current seats for all divisions, full-time and reserve both. A league ban bars the driver from racing in the league ever again: they will be assigned a special role, and will be unable to engage with the signup wizard, to be placed in a team, or to check in. It does not remove them from the server, where they may remain active outside of racing. It has no duration, and lasts until a league revokes it. League bans are only applied to a driver licence once the stewarding cycle in which it was bestowed is complete.
- Automated penalty rule - Also referred to as an auto-rule. A predefined, league-configured automated penalty handed out by the bot if a driver meets the criteria configured in the rule. These rules can be one of four types:
  - Single round - Rules that verify only multiples of a specific penalty accrued in a single round for one driver.
  - Multi round - Rules that verify only multiples of a specific penalty accrued across multiple rounds for one driver.
  - Active accumulation - Rules that verify only the accumulation of a specific active penalty on a driver licence.
  - Historical accumulation - Rules that verify the total accumulation of a specific penalty across a driver licence history, active, expired or served.
- Active penalty - A penalty instance that is still active, and is yet to be served, yet to expire, or yet to be revoked. Applies to warning points, penalty points, discipline points, qualifying bans, race bans, season bans and league bans.

## Configuring the stewarding module
- All configuration changes must be logged to the standard log channel.
- All configuration changes done with commands usable by the head steward, acting head steward, or other members of the stewarding team must be logged to the steward log channel.

### Channels
- Every channel this module sets is bound by the rule that a channel serves one purpose upon a server, as the core specification sets out. The channels the bot creates for each ticket are set by no command and are not bound by it.
- <NEW COMMAND> A "division ticket-channel" command will be made available to league managers, which shall have as input a division name and a channel in which drivers for that division can interact to initiate tickets (reports and appeals both).
  - If the stewarding module is enabled, confirming the season's placements shall fail if any division lacks a ticket channel.
  - Each division's ticket channel will be displayed in the placements review much alike other division channels.
- The stewarding module shall inherit the "division verdicts-channel" command from the results module.
- <NEW COMMAND> A "division licence-channel" command will be made available to league managers, which shall have as input a division name and a channel in which the licence information for the drivers of the division will be posted by the bot.
  - If the stewarding module is enabled, confirming the season's placements shall fail if any division lacks a licence channel.
  - Each division's licence channel will be displayed in the placements review much alike other division channels.
- <NEW COMMAND> A "steward channel conduct-verdicts" command will be made available to league managers, which shall have as input a channel in which the verdicts for CoC investigations will be posted. It is a single channel for the server, and not one per division.
  - If the CoC investigations feature is enabled, confirming a season's configuration shall fail while this channel is not set.
- <NEW COMMAND> A "steward channel command" command will be made available to league managers, which shall have as input a channel in which stewards will be able to input certain special bot commands. These commands must be explicitly marked as stewarding team actionable in these specifications, otherwise their use will be rejected, and no other commands but those will be accepted in this channel.
  - If the stewarding module is enabled, confirming a season's configuration shall fail while this channel is not set, naming it.
- <NEW COMMAND> A "steward channel log" command will be made available to league managers, which shall have as input a channel in which ALL commands utilised in the channel configured by "steward channel command" will be logged for audit purposes, much in the same way they are already done by the log channel input in "bot init".
  - If the stewarding module is enabled, confirming a season's configuration shall fail while this channel is not set, naming it.

### The stewarding team
- Every role this module sets serves one purpose. Setting the steward, head steward, temporary head steward, season ban or league ban role shall refuse a role already set as any other role the bot holds — the interaction role, the league admin role, the signup module's roles, a division's role, a team's role, or another of this module's — naming what holds it, and nothing shall be changed by the refusal.
- The stewarding team is held by the bot as a list, changed only by the commands below. Stewarding authority is held by being on that list, and not by holding a role. The roles configured below mirror the list, for visibility and for the permissions of channels, and the bot keeps them in step with it; a role granted by other means confers nothing.
- <NEW COMMAND> A "steward role team" command will be made available to league managers, which shall have as input a user role that will be bestowed to all users designated as stewards.
  - Where the role is changed while the team has members, the bot shall move every member from the former role to the new one.
- <NEW COMMAND> A "steward role head" command will be made available to league managers, which shall have as input a user role that will be bestowed to the user designated as head steward.
  - Where the role is changed while a head steward is appointed, the bot shall move them from the former role to the new one.
- <NEW COMMAND> A "steward team add" command will be made available to the head steward and to league admins, which shall have as input a member of the server. The member shall be added to the stewarding team, and given the role configured by "steward role team".
  - The command shall be refused for a member already on the team, and for a past account of a driver.
- <NEW COMMAND> A "steward team remove" command will be made available to the head steward and to league admins, which shall have as input a member of the stewarding team. The member shall be removed from the team, and the role configured by "steward role team" taken from them.
  - The command shall be refused for the head steward, who shall first be replaced.
  - A steward removed shall leave the effective stewarding team of every open ticket at once. Any ballot they cast shall be discarded, their removal being none of their own doing, and where they were a ticket's effective head steward, the ticket shall pass to another as when an effective head steward is reassigned.
- <NEW COMMAND> A "steward team list" command will be made available to stewards, league managers and league admins, which shall have no inputs. In reply, the bot shall post, visible to the user alone, the members of the stewarding team, marking the head steward and any temporary head steward.
- <NEW COMMAND> A "steward team head-assign" command will be made available to league admins, which shall have as input a member of the stewarding team. That member shall be made head steward, and given the role configured by "steward role head".
  - The command shall be refused for a member who is not on the stewarding team, the head steward being a steward as well.
  - Where a head steward is already appointed, they shall be replaced, and shall remain on the team as a steward, the head steward role being taken from them.
  - Upon a replacement, on every open ticket on which the former head steward was the effective head steward by default, the new head steward shall become so. A ticket whose effective head steward had been reassigned shall keep them. Where the new head steward is an involved driver on an open ticket, or has a conflict of interest upon it, that ticket shall be reassigned as when any effective head steward is so placed.
  - Upon a replacement, a temporary head steward in post shall remain so, and the new head steward may remove them as any head steward may. Where the temporary head steward is the member made head steward, their temporary appointment shall end.
- If the stewarding module is enabled, confirming the season's placements shall fail while no head steward is appointed.
- <NEW COMMAND> A "steward role temp-head" command will be made available to the head steward to be utilised in the channel configured by "steward channel command", which shall have as optional input a user role that will be bestowed to the user designated as head steward.
  - This command is only valid if no user has temporary head steward status.
  - If the input role parameter is empty, then temporary head steward functionality is deactivated.
- <NEW COMMAND> A "steward team temp-head-assign" command will be made available to the head steward to be utilised in the channel configured by "steward channel command" exclusively, which will have as input the user ID of a member belonging to the stewarding team. This command will confer the user with temporary head steward status.
  - This command is only valid if the temporary head steward role configured by "steward role temp-head" is not empty.
  - This command is only valid if the target user is part of the stewarding team and is not the head steward.
  - While the appointment lasts, the temporary head steward is the effective head steward by default on every ticket: the tie-break, the approval of drivers added, drivers removed and exclusions, and the confirmation of justifications are theirs. On every open ticket on which the head steward was the effective head steward by default, the temporary head steward shall become so; a ticket whose effective head steward had been reassigned shall keep them. When the appointment ends, those tickets shall return to the head steward.
  - While the appointment lasts, the temporary head steward may use the commands of the head steward, and the head steward keeps every one of them, "steward team temp-head-remove" among them.
- <NEW COMMAND> A "steward team temp-head-remove" command will be made available to the head steward to be utilised in the channel configured by "steward channel command" exclusively, which will have no input. This command will remove the temporary head steward status from the user currently possessing it.
  - This command is only valid if the temporary head steward role configured by "steward role temp-head" is not empty.
- <NEW COMMAND> A "steward team conflict-toggle" command will be made available to league managers, which shall determine whether stewards who are also drivers are able to participate in the stewarding cycle of tickets that pertain to divisions they are driving in, accepting a possible conflict of interest.
  - By default this setting is on, meaning stewards can review reports/appeals pertaining to the division they are driving for, and that leagues implicitly accept a possible conflict of interest.

### Timings
- <NEW COMMAND> A "steward report submission-period" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the maximum number of hours during which drivers for that division or users belonging to the stewarding team (validated by checking whether they have the stewarding team role) are able to open a report. After this time elapses, the report submission phase is over.
  - By default, this value will be set to 48.
  - Input value must be equal or greater than 1.
- <NEW COMMAND> A "steward report defence-period" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours during which any involved/mentioned driver is able to provide evidence or arguments regarding the incident in question. After this time elapses, the defence submission phase is over.
  - By default, this value will be set to 24.
  - Input value must be equal or greater than 1.
- <NEW COMMAND> A "steward report deliberation-period" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours during which members of the stewarding team can discuss and vote on the verdict pertaining to a given report. After this time elapses, the report deliberation phase is over.
  - By default, this value will be set to 24.
  - Input value must be equal or greater than 1.
- <NEW COMMAND> A "steward appeal submission-period" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours during which drivers for that division or users belonging to the stewarding team (validated by checking whether they have the stewarding team role) are able to open an appeal. After this time elapses, the appeal submission phase is over.
  - By default, this value will be set to 24.
  - Input value must be equal or greater than 0. If the value is 0, then appeals are disabled, and both the appeal submission and the appeal deliberation stages will not be scheduled.
- <NEW COMMAND> A "steward appeal deliberation-period" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours during which members of the stewarding team can discuss and vote on the verdict pertaining to a given report. After this time elapses, the appeal deliberation phase is over.
  - By default, this value will be set to 24.
  - Input value must be equal or greater than 1.
- The sum of the values of the configurations above may not exceed 168 (7 times 24 hours). This validation must be done everytime one of the commands above is run; if failed, then the new value is not accepted.
  - This bounds the configured periods alone, and not how long a cycle may run: the waits set out under each stage come on top of it.

### Appeals
- <NEW COMMAND> A "steward appeal toggle" command will be made available to league managers, which shall have no inputs. This command shall activate and deactivate the appeal system.
  - By default, the appeal functionality is enabled.
- <NEW COMMAND> A "steward appeal tokens" command will be made available to league managers, which shall have as inputs two integers: the number of appeal tokens each driver starts a season with, and the number an appeal costs. The two shall be validated together, and a pair failing validation shall be refused, the configuration being left as it stood.
  - The valid pairs are 0 and 0, meaning appeals cost nothing, or a starting number of 1 or more with a cost from 1 up to that starting number.
  - By default, both are 0. While the cost is 0, an appeal costs nothing, and no driver's tokens are counted.
  - Otherwise, a driver's number of appeal tokens shall be set to the starting number the first time they are placed in a team in a season, the reserve team included. Being placed again later in the same season shall not change it. Each appeal a driver lodges costs them the configured number, and no driver may hold more than the starting number.
- <NEW COMMAND> A "steward appeal refund-toggle" command will be made available to league managers, which shall have no inputs. This command shall toggle whether a driver whose appeal succeeds is returned the tokens it cost them.
  - By default, this is toggled on.
  - An appeal succeeds where its verdict is "Change initial verdict", whichever way the change goes: a driver seeking a lighter punishment, or none, for themselves, and a driver seeking a harsher one for another, succeed alike where the stewards change the initial verdict.
  - A refund shall never bring a driver above the starting number of tokens.

### Justifications
- <NEW COMMAND> A "steward justification final-mode" command will be made available to league managers, which shall alter the method through which the default verdict justification text is determined, which is to be provided by the bot once a verdict is reached for any ticket. The modes available are:
  - Longest - Active by default - The bot takes the longest justification, in character count, among the ballots of the winning option.
  - Own - The bot takes the effective head steward's own justification, if their ballot is of the winning option.
  - LLM - The bot feeds the justifications of all ballots of the winning option to a remote or local-running LLM, which will then provide a full professional sounding text for the justification. This mode cannot be chosen if there is no valid LLM token/communication set up.
    - An LLM is connected to the bot by whoever runs it, upon the machine the bot runs on and outside of Discord; no command of the bot sets it up. Where none is connected, this mode cannot be chosen.
    - Choosing it shall state that the stewards' justifications will be sent to that LLM, and whether it runs on the machine the bot runs upon or on a remote service.
    - Where the LLM fails to provide a text for a verdict, the fallback mode is used for that verdict.
  - By default, this value shall be set to "longest".
- <NEW COMMAND>  A "steward justification fallback-mode" command will be made available to league managers, which shall alter the backup method through which the default verdict justification text is determined, which is to be provided by the bot once a verdict is reached for any ticket. The methods available are the same as the ones listed in the requirement for "steward justification final-mode".
  - By default, this value shall be set to "own".
  - The method chosen by this command cannot be the same as the one chosen with "steward justification final-mode".
  - If "steward justification final-mode" is changed to "longest" when "steward justification fallback-mode" is already "longest", then the latter shall change to "own".
  - If "steward justification final-mode" is changed to "own" when "steward justification fallback-mode" is already "own", then the latter shall change to "longest".
  - If "steward justification final-mode" is changed to "LLM" when "steward justification fallback-mode" is already "LLM", then the latter shall change to "longest".
- If neither "steward justification final-mode" nor "steward justification fallback-mode" are feasible options, then the bot will take the longest justification among the ballots of the winning option, which is always possible, every ballot carrying one.

### Outcomes
- <NEW COMMAND> A "steward outcome add" command will be made available to league managers, which shall open a form collecting the following:
  - ID - Mandatory - Unique ID for the outcome. Maximum of 12 characters.
  - Brief - Mandatory - Unique short description of the outcome. Maximum of 50 characters.
  - Description - Optional - Long form description of the outcome. Maximum of 250 characters.
  - Applicable to qualifying? - Mandatory - Checkbox that, if ticked, represents that this outcome can be assigned to incidents from a qualifying session.
  - Applicable to race? - Mandatory - Checkbox that, if ticked, represents that this outcome can be assigned to incidents from a race session.
  - Time penalty - Optional - Integer input only. Number of whole seconds added to the total race time of the offending driver, as the results & standings module takes a time penalty. Fractions of a second shall be refused.
  - Disqualification - Optional - Checkbox that, if ticked, means that the offending driver's entry in the session of the incident is invalidated and ranked last in that session, as the results & standings module disqualifies an entry. Where it is ticked together with a time penalty, the disqualification prevails; the points and bans the outcome also sets shall apply all the same.
  - Warning points - Optional - Integer input only. Number of warning points added to the driver licence of the offending driver.
  - Penalty points - Optional - Integer input only. Number of penalty points added to the driver licence of the offending driver.
  - Qualifying bans - Optional - Integer input only. Number of qualifying bans added to the driver licence of the offending driver.
  - Race bans - Optional - Integer input only. Number of race bans added to the driver licence of the offending driver.
  - Season ban - Optional - Checkbox that, if ticked, means that the offending driver licence will accrue a ban lasted for one season.
  - League ban - Optional - Checkbox that, if ticked, means that the offending driver licence will accrue a league ban.
  - A session's list of outcomes shall hold at most 25, NFA among them. The command shall be refused where it would bring the qualifying list or the race list above 24 outcomes a league defines, and the refusal shall name the list that is full.
  - This command shall fail if the outcome has any disabled penalty types.
  - At least one of the "Time penalty", "Disqualification", "Warning point", "Penalty point", "Qualifying ban", "Race ban", "Season ban", "League ban" fields must be different from 0.
- <NEW COMMAND> A "steward outcome modify" command will be made available to league managers, which shall have as input a string standing for an outcome's ID. If this ID is valid, then a form much like the one opened by "steward outcome add" shall open, prefilled with the values of the outcome of the input ID. All fields with the exception of the ID can be modified.
  - The command shall be refused where the change would bring the qualifying list or the race list above 24 outcomes a league defines, and the refusal shall name the list that is full.
  - This command shall fail if the outcome has any disabled penalty types.
  - At least one of the "Time penalty", "Disqualification", "Warning point", "Penalty point", "Qualifying ban", "Race ban", "Season ban", "League ban" fields must be different from 0.
- <NEW COMMAND> A "steward outcome remove" command will be made available to league managers, which shall have as input a string standing for an outcome's ID. If this ID is valid, then the bot shall ask for confirmation of the deletion of the outcome, with Confirm and Cancel buttons. If confirmed, then the outcome shall be removed from the list.
- <NEW COMMAND> A "steward outcome toggle" command will be made available to league managers, which shall have as input an outcome's ID, pausing the outcome where it is active and resuming it where it is paused.
  - A paused outcome is offered upon no ballot. It stays in the list, "steward outcome list" marking it as paused, and verdicts that gave it are untouched.
  - NFA cannot be paused.
  - A paused outcome does not count towards the 24 a session's list may hold. Resuming one shall be refused where it would bring the qualifying list or the race list above 24, naming the list that is full.
- <NEW COMMAND> A "steward outcome list" command will be made available to league managers and stewards, which shall have as input a string standing for a session type (qualifying or race). In reply, the bot will post a transient (temporary, seen only to the command user) list with all the outcomes currently available for that session type, as a bullet point list as follows:
  - The list is shown in full, over several messages where it does not fit in one.
  - <brief>
    - ID: <id>
    - Rule description: <description, if not empty, otherwise this line is skipped>
    - Associated outcome: <all penalties associated with the outcome, comma concatenated>
- By default, a permanent, unremovable, unmodifiable outcome with the following values is added to the list, which has the following data It is the one exception to the rule that an outcome sets at least one penalty other than 0, which governs the outcomes a league defines:
  - ID - NFA (special reserved ID)
  - Brief - No Further Action
  - Description - Outcome which means there is no actionable offence in the reported incident, and therefore no punishment is passed upon any driver.
  - Applicable to qualifying? - Yes
  - Applicable to race? - Yes
  - Time penalty - 0
  - Disqualification - No
  - Warning point - 0
  - Penalty point - 0
  - Qualifying ban - 0
  - Race ban - 0
  - Season ban - No
  - League ban - No

### Penalty types and bans
- <NEW COMMAND> A "steward penalty toggle" command will be made available to league managers, which shall have as single input a penalty type (warning points, penalty points, time penalties, disqualifications, qualifying bans, race bans, season bans, league bans). This command shall switch that penalty type off where it is enabled, and on where it is disabled.
  - Discipline points are not among them. They exist only in the conduct cycle, and are switched on and off with it by "steward conduct toggle".
  - By default, all are enabled.
  - Toggling a penalty type off shall be refused while any outcome or conduct outcome would be left with no penalty other than 0, that penalty type being its only one. The refusal shall name those outcomes, which shall be modified or removed first, and nothing shall be changed by it.
  - If a penalty type is toggled off, and any outcome or conduct outcome has that penalty type configured (value > 0), it shall be verified if the conditions for modifying or removing an outcome/conduct outcome are in place (no current deliberations). If not, then the command fails; otherwise, a confirmation message informing the user that the outcomes will lose this penalty types will be posted, with two buttons, "Confirm" and "Cancel". If confirmed, the outcome is then modified accordingly (value = 0).
- <NEW COMMAND> A "steward role season-ban" command will be made available to league managers, which shall have as mandatory single input a role to be attributed when any driver is season banned, and removed when any driver's season ban expires or is revoked.
- <NEW COMMAND> A "steward penalty season-ban-type" command will be made available to league managers, which shall have as single input one of the following behaviours:
  - Number of rounds - The season ban expires only after a number of rounds have taken place. That number is the highest number of rounds, in the season under way, among the divisions in which the driver held a seat when the ban was applied: a driver seated in a division of 8 rounds and one of 10 is banned for 10. Where no season is under way, it is the highest number of rounds among all the divisions of the last season, whether or not the driver took part in it.
    - The rounds counted are those of the division that set the number, from the first of its rounds after the ban applied. A cancelled round is not counted.
    - Where that division has no rounds left in its season, the count carries into the next season's division of the same tier. Where the next season holds no division of that tier, it carries into the division of the next season with the highest tier number: where tier 3 is gone, tier 2. Where no season is under way when the ban applies, the count is made in the next season's division of the same tier as the last season's division that set the number, by the same rule.
    - Nothing is counted while no round takes place, between seasons or where no further season is held, and the ban stays active meanwhile.
  - Season end - The season ban expires when the season is completed, a league admin completing it.
    - A season ban applied once none of the divisions in which the driver held a seat has a round left in the season, as at the cycle close of the final round, expires instead at the completion of the next season, so that a season ban is never served by a season already over. Where no season is under way when it applies, it expires at the completion of the next season.
    - A season ban stacked behind another expires at the completion of the season after the one ending the ban before it.
  - Timed - The season ban expires only after a set amount of time, expressed in days. If this option is picked, the command shall take the number of days for the season ban as well.
  - By default, this will be set to "number of rounds".
- <NEW COMMAND> A "steward penalty warning-point-expiry" command will be made available to league managers, which shall have as single input one of the following behaviours, determining when warning points expire:
  - Number of rounds - Warning points expire once a number of rounds has taken place, counted as the rounds of a season ban are: those of the division in which they were received, carrying into the division of the same tier in a later season. The number is either one the league sets, or the length of the season, being the number of rounds of that division in the season in which the points were received.
  - Season end - Warning points expire when the season is completed. Warning points received once none of the divisions in which the driver held a seat has a round left in the season expire instead at the completion of the next season.
  - Timed - Warning points expire after a set number of days.
  - By default, this will be set to "number of rounds", the number being the length of the season.
- <NEW COMMAND> A "steward penalty penalty-point-expiry" command will be made available to league managers, which shall have as single input one of the behaviours of "steward penalty warning-point-expiry", determining alike when penalty points expire.
  - By default, this will be set to "number of rounds", the number being the length of the season.
- <NEW COMMAND> A "steward role league-ban" command will be made available to league managers, which shall have as mandatory single input a role to be attributed when any driver is season banned, and removed when any driver's league ban is revoked.

### Automated penalty rules
- <NEW COMMAND> A "steward auto-rule add-single-round" command will be made available to league managers, which shall have no inputs. The final output of this command is a rule that, without any further user input, checks for configured criteria on the outcomes of that round exclusively upon the closing of the stewarding cycle of any given round. Upon usage of this command, a form will open, collecting the following:
  - ID - Mandatory - String - Unique ID for this rule. Must not overlap with that of other auto rules, regardless of type. Maximum of 12 characters.
  - Infringement - Optional - String - An optional string for league managers to add a rule number to be printed out in the verdict.
  - Type of infractions committed - Mandatory - Dropdown - Type of penalty that must be given out to a driver in the quantity above for this rule to be triggered.
  - Number of infractions committed - Mandatory - Integer - Quantity of penalties of a certain type that a driver must have received in the latest round in the quantity above for the auto-rule to be triggered.
    - This value must be greater than 0.
  - Penalty given - Mandatory - Dropdown - Type of penalty to be bestowed upon a driver when this rule is triggered.
  - Number of penalties given - Mandatory - Integer - Quantity of penalties of the type defined in "penalty given" to be bestowed upon a driver when this rule is triggered.
    - This value must be greater than 0.
  - Once the user confirms the auto-rule, the fields will be verified, and if valid, the auto-rule will be added to the list with the "single round" type and made active immediately.
- As configured, a single-round rule will be interpreted as meaning "if a driver receives a certain amount of pre-defined penalty type in a single round, they will be handed out a number of penalties of a certain, different, kind".
- <NEW COMMAND> A "steward auto-rule add-multi-round" command will be made available to league managers, which shall have no inputs. The final output of this command is a rule that, without any further user input, checks for configured criteria on the outcomes of a previous number of rounds, the latest round completed included, upon the closing of the stewarding cycle of any given round. Upon usage of this command, a form will open, collecting the following:
  - ID - String - Unique ID for this rule. Must not overlap with that of other auto rules, regardless of type. Maximum of 12 characters.
  - Infringement - Optional - String - An optional string for league managers to add a rule number to be printed out in the verdict.
  - Number of rounds - Integer - Number of previous rounds' outcomes that will be checked for the type of infractions committed by a same driver.
    - This value must be greater than 1.
  - Type of infractions committed - Mandatory - Dropdown - Type of penalty to be detected in the driver's record in the previous rounds.
  - Number of infractions committed - Mandatory - Integer - Quantity of penalties of a certain type that a driver must have received in the latest rounds in the quantity above for the auto-rule to be triggered.
    - This value must be greater than 0.
  - Penalty given - Mandatory - Dropdown - Type of penalty to be bestowed upon a driver when this rule is triggered.
  - Number of penalties given - Mandatory - Integer - Quantity of penalties of the type defined in "penalty given" to be bestowed upon a driver when this rule is triggered.
    - This value must be greater than 0.
  - Once the user confirms the auto-rule, the fields will be verified, and if valid, the auto-rule will be added to the list with the "multi round" type and made active immediately.
- As configured, a multi-round rule will be interpreted as meaning "if a driver receives a certain amount of pre-defined penalty type across the last X rounds, they will be handed out a number of penalties of a certain, different, kind".
- <NEW COMMAND> A "steward auto-rule add-active-accumulation" command will be made available to league managers, which shall have no inputs. The final output of this command is a rule that, without any further user input, checks the drivers' licences for a given number of active penalties (that is, penalties that are yet to serve or expire) of a certain kind, upon the closing of the stewarding cycle of any given round. Upon usage of this command, a form will open, collecting the following:
  - ID - Mandatory - String - Unique ID for this rule. Must not overlap with that of other auto rules, regardless of type. Maximum of 12 characters.
  - Infringement - Optional - String - An optional string for league managers to add a rule number to be printed out in the verdict.
  - Type of infractions committed - Mandatory - Dropdown - Type of penalty that must be active in a driver licence.
  - Number of infractions committed - Mandatory - Integer - Quantity of penalties of a certain type that must be active in a driver licence for the auto-rule to be triggered.
    - This value must be greater than 0.
  - Penalty given - Mandatory - Dropdown - Type of penalty to be bestowed upon a driver when this rule is triggered.
  - Number of penalties given - Mandatory - Integer - Quantity of penalties of the type defined in "penalty given" to be bestowed upon a driver when this rule is triggered.
    - This value must be greater than 0.
  - Once the user confirms the auto-rule, the fields will be verified, and if valid, the auto-rule will be added to the list with the "active accumulation" type and made active immediately.
- As configured, an accumulation of active penalties rule will be interpreted as meaning "if a driver licence holds a certain amount of active penalties, meaning penalties not served or expired, of a pre-defined type, they will be handed out a number of penalties of a certain, different, kind".
- <NEW COMMAND> A "steward auto-rule add-historical-accumulation" command will be made available to league managers, which shall have no inputs. The final output of this command is a rule that, without any further user input, checks the drivers' licences for a given number of penalties of a certain kind across their whole history in the server, upon the closing of the stewarding cycle of any given round. Upon usage of this command, a form will open, collecting the following:
  - ID - Mandatory - String - Unique ID for this rule. Must not overlap with that of other auto rules, regardless of type. Maximum of 12 characters.
  - Infringement - Optional - String - An optional string for league managers to add a rule number to be printed out in the verdict.
  - Type of infractions committed - Mandatory - Dropdown - Type of penalty conferred to a driver licence throughout their lifetime in the present league.
  - Number of infractions committed - Mandatory - Integer - Quantity of penalties of a certain type that has to have been conferred to a driver licence throughout their lifetime in the present league.
    - This value must be greater than 0.
  - Penalty given - Mandatory - Dropdown - Type of penalty to be bestowed upon a driver when this rule is triggered.
  - Number of penalties given - Mandatory - Integer - Quantity of penalties of the type defined in "penalty given" to be bestowed upon a driver when this rule is triggered.
    - This value must be greater than 0.
  - Once the user confirms the auto-rule, the fields will be verified, and if valid, the auto-rule will be added to the list with the "historical accumulation" type and made active immediately.
- As configured, an accumulation of penalties across one's career in the league rule will be interpreted as meaning "if a driver has received a certain amount of penalties of a pre-defined type, active or not, throughout their time in the league as shown by their driver licence, they will be handed out a number of penalties of a certain, different, kind".
- The types an auto-rule may count, as its "Type of infractions committed", are warning points, penalty points, discipline points, time penalties, disqualifications, qualifying bans, race bans and season bans. Time penalties are counted as the cumulative sum of their seconds; every other type by its number.
- The types an auto-rule may hand out, as its "Penalty given", are warning points, penalty points, discipline points, time penalties, disqualifications, qualifying bans, race bans, a season ban and a league ban.
- A time penalty or disqualification an auto-rule hands out shall change the result of a race session:
  - Where the auto-rule was triggered at the close of a round's cycle, that round's feature race. It is applied as part of the close, and the round's results and standings reposted once, under the Final Results label.
  - Where it was triggered by a CoC investigation, the feature race of the next round the driver takes part in, applied at the close of that round's cycle and shown upon its results. A driver who takes part in no further round never has it applied.
- The type counted and the type handed out may be the same, each auto-rule being triggered at most once for a driver in one cycle close, and re-arming as its type does.
- A penalty type that is disabled shall be offered neither to count nor to hand out. Discipline points are offered only while the conduct cycle is enabled.
- Several auto-rules may be triggered after the same round, and one being triggered may trigger another, of whichever type, as set out under Auto-rule triggering.
- <NEW COMMAND> A "steward auto-rule modify" command will be made available to league managers, which shall have as input the ID of an auto-rule. If the ID is valid, a form similar to the one opened when an auto-rule of the same type is added will appear, prefilled with the auto-rule's data, all of it modifiable. The ID is shown, and cannot be changed.
- <NEW COMMAND> A "steward auto-rule remove" command will be made available to league managers, which shall have as input the ID of an auto-rule. If the ID is valid, the bot shall ask for confirmation of the deletion of the auto-rule, with Confirm and Cancel buttons. Once confirmed, the auto-rule will no longer be active and enforceable, and it will be deleted from the current list.
- <NEW COMMAND> A "steward auto-rule toggle" command will be made available to league managers, which shall have as input the ID of an auto-rule, pausing it where it is active and resuming it where it is paused.
  - A paused auto-rule is checked at no cycle close, and "steward auto-rule list" shall mark it as paused.
  - Resuming an auto-rule counts as adding it anew: every driver already over its threshold is held as having already triggered it, so that nothing that happened while it was paused is punished by it.
- <NEW COMMAND> A "steward auto-rule list" command will be made available to league managers and stewards, which shall have no inputs. In reply, the bot will post a transient (temporary, seen only to the command user) list with all the auto-rules currently available, as a plain text table with the following columns in order: ID, Auto-rule type, Infringement, Number of rounds, Type of infractions committed, No. of infractions committed, penalty given, number of penalties given.
  - The list is shown in full, over several messages where it does not fit in one.
  - For rules of non-multi-round-type, the "number of rounds" column shall be empty.

### Backups
- <NEW COMMAND> A "steward backup report-toggle" command will be made available to league admins, which shall have no inputs. When toggled on, once closed, the content of channels pertaining to reports and appeals will be persisted in disk to a directory.
  - This functionality shall be disabled by default.
  - It is a league admin's, the backups filling the disk of the machine the bot runs upon, which is typically a league admin's own.
  - The directory shall be "./tickets", the same as used by "steward backup conduct-toggle".
  - Channel content shall be formatted as a JSON file. Each message shall be saved as an individual element, with an ID and display name of the one sending the message associated.
  - When saving, a directory shall be created with the unique ID of the ticket, report or appeal, and the channel content shall be saved to that directory as "channel.json".
  - Any attached videos and images will be downloaded and saved, with their original filenames, to the same directory. This includes the content from the original message.
- Where a backup cannot be saved, the channel shall not be deleted, being then the only copy of the ticket. The failure shall be reported in the ticket's channel, mentioning its effective head steward and giving the reason, with a button to try again, usable by the effective head steward, the head steward and the temporary head steward for as long as the channel stands, through restarts. The channel shall be deleted only once the backup has been saved. The failure shall be written to the steward log channel as well.
- While free space on the disk holding the backups is below 1 GB, a warning shall be posted to the steward log channel, mentioning the temporary head steward where one is in post and the head steward otherwise, and repeated once a day while it remains so. The backups are kept for whoever runs the bot to retrieve, and clearing them is theirs to do.

### Code of Conduct investigations
- <NEW COMMAND> A "steward conduct toggle" command will be made available to league managers, which shall have no inputs.
  - This functionality is toggled off by default, and any of the other commands in this section fail if this functionality is toggled off.
- <NEW COMMAND> A "steward conduct steward-start-toggle" command will be made available to league managers, which shall have no inputs. This command shall toggle whether any steward, and not only the head steward and temporary head steward, may initiate a CoC investigation.
  - By default, this is toggled off, and only the head steward and temporary head steward may initiate one.
- <NEW COMMAND> A "steward conduct discipline-point-expiry" command will be made available to league managers, which shall have as single input either "never", or a number of days after which discipline points expire.
  - By default, this will be set to "never".
- <NEW COMMAND> A "steward conduct defence-period" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours during which any involved/mentioned driver is able to provide evidence or arguments relevant to the Code of Conduct investigation in question. After this time elapses, the defence submission phase is over.
  - By default, this value will be set to 24.
  - Input value must be equal or greater than 1.
- <NEW COMMAND> A "steward conduct deliberation-period" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours during which members of the stewarding team can discuss and vote on the verdict pertaining to a given Code of Conduct investigation. After this time elapses, the investigation deliberation phase is over.
  - By default, this value will be set to 24.
  - Input value must be equal or greater than 1.
- <NEW COMMAND> A "steward conduct-outcome add" command will be made available to league managers, which shall open a form collecting the following:
  - ID - Mandatory - Unique ID for the conduct outcome, among conduct outcomes. It may be the same as the ID of a report or appeal outcome, the two lists being independent. Maximum of 12 characters.
  - Brief - Mandatory - Unique short description of the conduct outcome. Maximum of 50 characters.
  - Description - Optional - Long form description of the conduct outcome. Maximum of 250 characters.
  - Discipline points - Optional - Integer input only. Number of discipline points added to the driver licence of the offending driver.
  - Qualifying bans - Optional - Integer input only. Number of qualifying bans added to the driver licence of the offending driver.
  - Race bans - Optional - Integer input only. Number of race bans added to the driver licence of the offending driver.
  - Season ban - Optional - Checkbox that, if ticked, means that the offending driver licence will accrue a ban lasted for one season.
  - League ban - Optional - Checkbox that, if ticked, means that the offending driver licence will accrue a league ban.
  - The list of conduct outcomes shall hold at most 25, NFA among them. The command shall be refused where it would bring the list above 24 conduct outcomes a league defines.
  - This command shall fail if the outcome has any disabled penalty types.
  - At least one of the "Discipline points", "Qualifying bans", "Race bans", "Season ban", "League ban" fields must be different from 0.
- <NEW COMMAND> A "steward conduct-outcome modify" command will be made available to league managers, which shall have as input a string standing for a conduct outcome's ID. If this ID is valid, then a form much like the one opened by "steward conduct-outcome add" shall open, prefilled with the values of the conduct outcome of the input ID. All fields with the exception of the ID can be modified.
  - This command shall fail if the outcome has any disabled penalty types.
  - At least one of the "Discipline points", "Qualifying bans", "Race bans", "Season ban", "League ban" fields must be different from 0.
- <NEW COMMAND> A "steward conduct-outcome remove" command will be made available to league managers, which shall have as input a string standing for a conduct outcome's ID. If this ID is valid, then the bot shall ask for confirmation of the deletion of the conduct outcome, with Confirm and Cancel buttons. If confirmed, then the conduct outcome shall be removed from the list.
- <NEW COMMAND> A "steward conduct-outcome toggle" command will be made available to league managers, which shall have as input a conduct outcome's ID, pausing it where it is active and resuming it where it is paused.
  - A paused conduct outcome is offered upon no ballot. It stays in the list, "steward conduct-outcome list" marking it as paused, and verdicts that gave it are untouched.
  - NFA cannot be paused.
  - A paused conduct outcome does not count towards the 24 the list may hold. Resuming one shall be refused where it would bring the list above 24.
- <NEW COMMAND> A "steward conduct-outcome list" command will be made available to league managers and stewards, which shall have no inputs. In reply, the bot will post a transient (temporary, seen only to the command user) list with all the conduct outcomes currently available, as a bullet point list as follows:
  - The list is shown in full, over several messages where it does not fit in one.
  - <brief>
    - ID: <id>
    - Rule description: <description, if not empty, otherwise this line is skipped>
    - Associated outcome: <all penalties associated with the outcome, comma concatenated>
- By default, a permanent, unremovable, unmodifiable conduct outcome with the following values is added to the list, which has the following data It is the one exception to the rule that a conduct outcome sets at least one penalty other than 0, which governs the conduct outcomes a league defines:
  - ID - NFA (special reserved ID)
  - Brief - No Further Action
  - Description - Outcome which means there is no actionable disciplinary offence in the reported incident, and therefore no punishment is passed upon any individual.
  - Discipline point - 0
  - Qualifying ban - 0
  - Race ban - 0
  - Season ban - No
  - League ban - No
- <NEW COMMAND> A "steward backup conduct-toggle" command will be made available to league admins, which shall have no inputs. When toggled on, once closed, the content of channels pertaining to Code of Conduct investigations will be persisted in disk to a directory.
  - This functionality shall be disabled by default.
  - It is a league admin's, the backups filling the disk of the machine the bot runs upon, which is typically a league admin's own.
  - The directory shall be "./tickets", the same as used by "steward backup report-toggle".
  - Channel content shall be formatted as a JSON file. Each message shall be saved as an individual element, with an ID and display name of the one sending the message associated.
  - When saving, a directory shall be created with the unique ID of the investigation, and the channel content shall be saved to that directory as "channel.json".
  - Any attached videos and images will be downloaded and saved, with their original filenames, to the same directory. This includes the content from the original message.

### Changing settings during a season
- Once a season's placements are confirmed, this module's settings may be changed at any time, save where a rule below sets out otherwise. A change governs what begins after it, and nothing already begun.
- The type of season ban, set by "steward penalty season-ban-type", shall not be changed while a season is ongoing.
- A setting of the stewarding cycle — the periods of its stages, whether appeals are enabled, the tokens they cost and whether they are refunded, and "steward team conflict-toggle" — shall take effect from the cycle of each division's next round. A cycle already open finishes under the settings it began with.
- A change to "steward justification final-mode" or "steward justification fallback-mode" applies to the verdicts reached after it.
- A change to a setting of the Code of Conduct investigations applies to the investigations opened after it, the expiry of discipline points excepted, which is set out below.
- Outcomes may be added while report or appeal deliberations are open. An outcome so added shall be offered upon the ballots of every deliberation still open, and the bot shall post in the steward command channel the new outcome and each open ticket whose effective stewarding team may now vote for it, so that stewards who have voted may reconsider while their votes can still be changed.
- An outcome may not be modified, removed or paused while any report or appeal deliberation is open.
- Conduct outcomes may be added while investigation deliberations are open. A conduct outcome so added shall be offered upon the ballots of every investigation deliberation still open, and the bot shall post in the steward command channel the new conduct outcome and each open investigation whose effective stewarding team may now vote for it, so that stewards who have voted may reconsider while their votes can still be changed.
- A conduct outcome may not be modified, removed or paused while any investigation deliberation is open.
- A penalty type that any outcome or conduct outcome uses may not be switched off while any deliberation is open, as set out under "steward penalty toggle".
- An auto-rule judges only what happens after it exists. When an auto-rule is added, modified or resumed, every driver whose licence is already over its threshold shall be held as having already triggered it, and it shall be triggered for them only upon a further crossing, as its type re-arms. A single round rule judges only the rounds whose cycles close after it was added, modified or resumed.
- A change to the expiry of warning points, of penalty points or of discipline points shall govern the points received from then on. Points already upon a licence keep the expiry they were given when received.

## Tickets
- What this section sets out holds for every ticket, a report, an appeal and a CoC investigation alike, save where a cycle sets out otherwise. For a CoC investigation, its involved users stand where this section names involved drivers. An investigation has no initiator among its parties: the steward who opened it holds only a steward's place in it, and where this section names the one who initiated the ticket, it names no one in an investigation.
- Every act in a ticket, and every use of a command of this module, shall be written to the steward log channel: what was done, upon which ticket where there is one, when, and by whom, by their display name and user ID. That includes the filing of a ticket and its contents, the adding or removing of a driver, requests and their answers, exclusions and handovers, mutes, the settling of an incident, grounds or justification, merges and withdrawals, and every refusal.
  - Ballots are the exception while a deliberation is open: the log records only that a steward cast, changed or removed a ballot, and not its content. Every ballot is written to the log in full once the deliberation closes.
- It is imperative that the stewarding team is seen as a unified front.
  - The effective head steward of a ticket is the face of the stewarding team to the users of that ticket, and may speak to them under their own name. They are its mouthpiece only.
  - No message a driver or other user can see shall attribute a decision, a vote, a request or a justification to any single steward, the effective head steward included. The decisions of the stewarding team are the team's as a whole.
  - No steward other than the effective head steward shall be identified or mentioned in any message a driver or other user can see.
  - Every exchange between stewards alone while the users of a ticket can see its channel shall take place in the channel configured by "steward channel command", and never in the ticket's channel: a steward's request awaiting the effective head steward's approval, and its answer; a request for exclusion, and its answer; the handing of a ticket to another effective head steward, and its acceptance or refusal. Each shall identify the ticket it concerns by its unique ID and link to its channel. The users of the ticket shall see only the outcome, from the effective head steward or the bot.
    - A request made by a driver is no exchange between stewards, and shall be answered in the ticket's channel.
    - Deliberation, from which the users of the ticket are shut out, takes place in the ticket's channel.
  - However, as stated above, steward logs shall identify them when needed.

- A ticket the stewarding team declines to judge, on procedural grounds or any other, is rejected through its ordinary decision, the justification stating why: No Further Action for every involved driver, for a report or a CoC investigation; "Uphold initial verdict", for an appeal.

### The effective head steward of a ticket
- If the effective head steward is one of the involved drivers, or has a conflict of interest as defined by "steward team conflict-toggle", the bot will post in the steward command channel a message with a button to assign effective head steward for the ticket to someone else of the stewarding team. The user will be validated for the criteria above, and after they are designated effective head steward, the former one will be removed from the effective stewarding team for the ticket.
  - If the effective head steward does not assign anyone else by the time the ticket's deliberation phase is reached, the bot shall choose one for this position: the temporary head steward, where one is in post and is neither an involved party nor holds a conflict of interest upon the ticket; otherwise, of the members of the effective stewarding team who are neither, the one who joined the stewarding team earliest.

### While a ticket is open to its parties
- A ticket is open to its parties during the defence submission of a report or a CoC investigation, and during the appeal submission of an appeal.
- Once this phase is entered, the following buttons will be posted on the channel after the header message is posted:
  - Add driver - Can be used by members of the effective stewarding team for this ticket, by the one who initiated the ticket, and by its involved drivers. Brings a driver into the ticket, most often one believed to hold footage or evidence of use in assessing the incident. When pressed, a form is opened so that one driver is named (mandatory) and a justification is given. The exact behaviour depends on who pressed it:
    - If pressed by the effective head steward, the justification is optional. When confirmed, the driver will be added to the ticket.
    - If pressed by a regular member of the effective stewarding team, the justification is mandatory. When confirmed, the bot will post in the steward command channel a message tagging the effective head steward for the ticket, stating that a steward has asked for that driver to be added, and printing the justification. If approved, then the driver will be added to the ticket, and the request process message will be deleted. If rejected, the bot will post a message to note that the request was rejected, and the request process message will be deleted.
    - If pressed by the one who initiated the ticket or an involved driver, the justification is mandatory. When confirmed, the bot will post the request in the ticket's channel, for the effective head steward to approve or reject there. If approved, then the driver will be added to the ticket; if rejected, the bot will note so. The request message will be deleted either way.
    - When added, the driver will be given the same permissions as other involved drivers.
    - The command is rejected if the ticket already holds 25 involved drivers, if the driver named holds no seat in the division, full-time or reserve, if they are the one who initiated the ticket, if they are in the involved drivers list already, or if the ticket is already in report deliberation or appeal deliberation.
    - In a CoC investigation, which pertains to no division, the member named need hold no seat: any member holding the base role may be added, as any may be investigated.
  - Remove driver - Can be used by members of the effective stewarding team for this ticket, by the one who initiated the ticket, and by its involved drivers. Takes a driver out of the list of involved drivers, and removes their permissions as an involved driver. When pressed, a form is opened so that one involved driver is named (mandatory) and a justification is given. The exact behaviour depends on who pressed it:
    - If pressed by the effective head steward, the justification is optional. When confirmed, the driver will be removed from the ticket.
    - If pressed by a regular member of the effective stewarding team, the justification is mandatory. When confirmed, the bot will post in the steward command channel a message tagging the effective head steward for the ticket, identifying the driver to be removed and printing the justification. If approved, then the driver will be removed from the ticket, and the request process message will be deleted. If rejected, the bot will post a message to note that the request was rejected, and the request process message will be deleted.
    - If pressed by the one who initiated the ticket or an involved driver, the justification is mandatory. When confirmed, the bot will post the request in the ticket's channel, for the effective head steward to approve or reject there. If approved, then the driver will be removed from the ticket; if rejected, the bot will note so. The request message will be deleted either way.
    - The command is rejected if the targeted user is the one who initiated the ticket, or if they are not in the involved drivers list, or if the ticket is already in report deliberation or appeal deliberation.
  - Request exclusion - Can be used by any member of the effective stewarding team for this ticket. The exact behaviour depends on who triggered the exclusion:
    - If the one requesting this exclusion is a regular member of the effective stewarding team, a form is opened so that a justification is input (mandatory). When confirmed, the bot will post in the steward command channel a message tagging the effective head steward for the ticket and identifying the steward that requested the exclusion, plus the justification. If approved, then the steward will be removed from the effective stewarding team for this ticket, and mentioned in a message in the steward command channel informing them of it. If rejected, then a form will be opened to give a justification (optional), and after confirmation, the steward who requested the exclusion will be mentioned in a message in the steward command channel informing them of the decision and the justification given.
    - If the one requesting this exclusion is the effective head steward, a form is opened so that a justification is input and another member of the effective stewarding team is mentioned, so that they will be assigned effective head steward privileges for this ticket. Once confirmed, the member will be requested to accept or reject. If accepted, they are made the effective head steward for the ticket's effective stewarding team. If rejected, then a form will be opened to give a justification (optional), and after confirmation, the steward will be mentioned in a message in the steward command channel informing of the decision.
      - The effective head steward remains so until a member accepts. After a rejection, they may name another member of the effective stewarding team, as many times as they wish.
      - Where no member has accepted by the time the deliberation phase is reached, the bot shall choose one as it chooses a replacement for an effective head steward who has not assigned one, the effective head steward who asked to be excluded being passed over.
      - Once a member accepts, the former effective head steward is excluded from the ticket and leaves its effective stewarding team.
    - A member of the effective stewarding team who has cast a ballot shall be refused an exclusion. They shall remove their ballot first, and may then request it.
  - Mute - Can be used by any member of the effective stewarding team for this ticket. When pressed, a form is opened so that the user inputs 1..n mentions of users from whom to remove write message/attach file permissions.
    - Until the deliberation phase, the command is rejected for any user who is neither the one who initiated the ticket nor in the involved drivers list.
    - During the deliberation phase, it applies to members of the effective stewarding team instead, and only the effective head steward may use it upon them; the effective head steward cannot be its target. A steward so muted loses only the writing of messages and the attaching of media in the channel: they may still read it, cast and change their ballot, and request exclusion.
  - Unmute - Can be used by any member of the effective stewarding team for this ticket. When pressed, a form is opened so that the user inputs 1..n mentions of users to whom to get write message/attach file permissions.
    - Until the deliberation phase, the command is rejected for any user who is neither the one who initiated the ticket nor in the involved drivers list.
    - During the deliberation phase, it applies to members of the effective stewarding team instead, and only the effective head steward may use it upon them; the effective head steward cannot be its target. A steward so muted loses only the writing of messages and the attaching of media in the channel: they may still read it, cast and change their ballot, and request exclusion.
- The buttons above remain in the channel through the deliberation phase, save "Add driver" and "Remove driver", which shall be withdrawn once it begins: the involved drivers are fixed once voting opens, so that every ballot covers the same drivers. "Request exclusion", the effective head steward's handover among it, "Mute" and "Unmute" remain available.
- When a driver is added to a ticket, they will be considered an involved driver, and given the same permissions as other involved drivers.
- When a driver is removed from a ticket, they will no longer be considered an involved driver, and the permissions of an involved driver will be removed from him.
- When a driver is added to or removed from a ticket, the bot will edit the "header message" (containing the post information) to account for the addition/removal, and post a new message informing of this change, mentioning the driver and a justification if given.
- If a driver was added to or removed from a ticket as a result of a request, then the message with the request will be deleted after confirmation/rejection.
- During this phase, regular members of the effective stewarding team only have read permission for the channel. They shall be able to utilise the aforementioned buttons (as per their own specification).
- During this phase, the effective head steward shall have read/write and attach media permission for the channel.
- During this phase, the user who initiated the ticket and all users marked as involved drivers shall have read/write and attach media permission for the channel.
- This phase cannot be terminated early.

### Deliberation
- Once this phase is entered, the involved drivers lose all permission to read, write or attach media to the channel.
- Once this phase is entered, the members of the effective stewarding team will gain the permission to write or attach media to the channel.
- Once this phase is entered, a single button titled "Vote" is posted by the bot. This button will serve for members of the effective stewarding team to cast, modify, or remove their ballot on the outcome of the ticket. A ballot is one steward's whole view of the incident, or of the case. The button opens the steward's ballot, seen by them alone, which is as follows, save where a cycle sets out otherwise:
  - Steward's display name - Shown, and not to be changed. Display name of the steward whose ballot it is.
  - Ticket ID - Shown, and not to be changed. Unique ID of the report, appeal or investigation which is being voted on.
  - For each involved driver:
    - Outcome - Dropdown - Mandatory - Dropdown containing the outcomes the cycle offers, and NFA, displaying their IDs, allowing the steward to select 1 of them. NFA by default.
    - Infringement - String - Optional - String standing for the ID/number which was allegedly violated by that driver. Useful for final verdict write-up.
  - Justification - String - Mandatory - A free form text with a 1000 character limit for the steward to give their reasonings for the whole ballot.
  - The ballot as it stands - every involved party's outcome, kept in view in whole while any one of them is being changed, so that the steward confirms their whole view and not a single choice.
  - "Cancel", "Remove vote" where the steward has already voted, and "Confirm".
- A steward's ballot is only valid via "Confirm" if all mandatory fields are filled.
- Once a steward's ballot is deemed valid, all data for the ballot will be recorded and persisted.
- If a steward reopens their ballot after having voted, it will show their ballot as it was cast.
- A ballot shall be counted whole. Two ballots are the same option only where they give every involved driver the same outcome, and no ballot shall be divided by driver for counting.
- The bot shall show no steward the ballots of others, nor any count of them, before the deliberation closes. Stewards may discuss in the channel and change their ballots until it closes.
- If the steward has chosen "Confirm" upon reopening their ballot, the previously persisted ballot will be modified to align with the ballot as it now stands.
- If the steward has chosen "Cancel" upon reopening their ballot, no change is to occur to their current ballot.
- If the steward has chosen "Remove vote" upon reopening their ballot, the previously persisted ballot will be deleted, and it will be as if the steward had never voted.
- At the end of the countdown period for this phase, all stewarding team members except for the effective head steward lose message write permission for the ticket's channel.
- At the end of the countdown period for this phase, the ballots will be counted. For the purpose of counting, only cast ballots will be taken into consideration for the determination of plurality. This means that in a situation where the effective stewarding team for a ticket consists of 9 people, and only 5 of those people have voted, only those 5 ballots will be used for assessing the ultimate verdict.
- If any one option reaches plurality without a tie, the ultimate result of the ticket will be that option: the outcome it gives each involved driver.
- If two or more options are tied, and the effective head steward's ballot is one of them, the ultimate result of the ticket will be the option of the effective head steward.
- If two or more options are tied, and the effective head steward's ballot is none of them (also covers the possibility of the effective head steward not voting at all), the bot will trigger a cascade of events:
  - For a report or an appeal, the bot shall post a message on the verdicts channel saying that the round's verdicts are slightly delayed, in the words each cycle gives. For a CoC investigation, no such message is posted.
  - In the ticket's channel, the bot shall post a button per option tied as the most voted, each showing the outcome it gives each involved driver. The one assigned as effective head steward will be the only one able to use these buttons. The option chosen dictates the ultimate verdict.
  - A timer counts down 1 hour from the moment the buttons are posted; if the effective head steward has not picked an option once this timer runs out, then the option that reached its final count the earliest will be the final verdict.
    - A ballot's time is the moment it was last confirmed. An option reaches its final count at the latest time among its ballots, and of the tied options, the one whose moment came first shall be the final verdict.
- Once the ultimate result of a ticket is reached through any of the mediums above, the bot will:
  - Post a message informing the effective head steward of the decision reached (the outcome given each involved driver, and for each the infringement most often given them among the ballots of the winning option), providing a default justification text determined via the method configured by "steward justification final-mode" (or "steward justification final-mode", if the primary method is not feasible).
  - Buttons usable only by the effective head steward of the ticket, one for accepting the default justification text as provided by the bot as-is, another to modify the default justification in a form, prefilled with it.
  - In a message different from the one above, the justifications of all ballots of the winning option will be presented as a reference.
  - Start a timer counting down 1 hour from the moment the buttons are posted; if the effective head steward has not confirmed the justification text once this timer runs out, then the one provided by the bot will be utilised for the verdict post.
- Once the justification message is settled, either via effective head steward confirmation or by timeout, the bot will remove all write permissions to the channel, and generate the final output to be posted in the verdicts channel.
  - The format of the final output is set out under Verdict output.

## Stewarding cycle
- By default, the bot shall post a "Ticket submission is currently closed for <X> division" in a division's ticket channel.
- If appeals are not enabled, once a report's verdict is posted in the appropriate channel by the end of the report deliberation phase, the incident (report) is deemed closed and final.
- If appeals are enabled, if a report is not appealed by the time the appeal submission phase ends, the incident (report) is deemed closed and final.
- Once appeal verdicts are posted at the end of the appeal deliberation phase, the incident (appeal and the report it pertains to) is deemed closed and final.

### The cycle moves the round
- A round whose stewarding cycle stands open cannot be cancelled. Where its division or its season is cancelled meanwhile, the cycle shall run to its close as any other, and the division or season shall be marked cancelled only once the cycle has closed.
- A driver sacked, or leaving the server, while party to an open ticket shall remain an involved driver of it, keeping their line upon every ballot. The ticket shall go on without a driver who has left; should they return before it closes, their access to its channel shall be restored.
- While the stewarding module is enabled, a round's stewarding cycle shall move the round through its states, in place of the review stages of the results & standings module.
  - Report submission opens at the round's scheduled start, while the round still awaits its results, so that drivers may report an incident as soon as the round is raced and whatever the pace at which its results are entered.
  - The round is awaiting report verdicts once its results are posted, and until the verdicts of its reports are posted.
  - It is then awaiting appeal verdicts until the verdicts of its appeals are posted, and becomes final at the close of its cycle.
  - Where no appeal is lodged, the round becomes final once appeal submission ends.
  - Where appeals are disabled, the round becomes final once the verdicts of its reports are posted, and never awaits appeal verdicts.
- A round cancelled before its scheduled start has no stewarding cycle, report submission never having opened for it. A round cannot be cancelled once its scheduled start has passed.
- A round whose every session is submitted as cancelled shall have its stewarding cycle ended once its results are posted, and its tickets closed without a verdict.
  - The bot shall post in each such ticket's channel that the round was cancelled and the ticket closed without a verdict. The channel shall then be removed as any closed ticket's channel is.
  - Tickets so closed shall have no effect upon any driver licence, and shall trigger no auto-rule.
- Report deliberation shall run in its own time whether or not the round's results have been posted. The verdicts of the round's reports, and the reposting of its results and standings, shall wait until its results are posted, and the cycle shall go no further until they are.
- The results and standings of the round shall be labelled as the round moves:
  - Reposted once the report verdicts are posted, they shall carry the Post-Race Penalty Results label, or the Final Results label where appeals are disabled.
  - Reposted once the appeal verdicts are posted, or once appeal submission ends with no appeal lodged, they shall carry the Final Results label.

### Report submission
- At the scheduled start datetime of a round for a given division, the bot shall delete the "default" message, and post a "Report incident" button to the configured ticket channel of the division, without mentioning the division role.
- Where more than one round of a division has report submission open at once, a separate "Report incident" button shall be posted for each, naming the round it pertains to.
- At the scheduled start datetime of a round for a given division, a countdown with the period of time configured by "steward report submission-period" will start. Once this time elapses, the default message shall be displayed once more in the division's ticket channel, and the "Report incident" button removed.
- It shall be possible to members of the stewarding team to report an incident in the same manner a regular driver can.
  - Where the steward holds no seat in the division, full-time or reserve, the report is a steward's report. The stewarding team as a whole is its complainant, shown as "The Stewards" wherever a user can see it. The steward who lodged it is named in the steward log alone, and is not an involved driver.
  - Where the steward holds a seat in the division, the report is a driver's report as any other. Before it is filed, the bot shall warn them that they are filing it as a driver and not as a member of the stewarding team, and they shall confirm or withdraw.
- When a user presses the "Report incident" button, a form shall appear, collecting the following:
  - Season - Shown, and not asked for. Derived from the current season's number.
  - Division - Shown, and not asked for. Derived from the division to which the ticket channel is associated.
  - Round - Shown, and not asked for. Derived from the round whose report submission is open, as named by the button pressed.
  - Involved drivers - Optional - 0..n members - Other drivers directly or indirectly involved in the incident, whose footage or evidence may be of use to the stewarding team's deliberations. They are chosen from the server's members by searching for them by name, several at once, and not from a list of every member; any who holds no seat in the division, full-time or reserve, is refused and named, and the form is shown again with the rest kept.
    - These drivers must be assigned to the division this report pertains to.
    - The report will not be valid if there is any entry here that is not an involved driver.
    - The driver who triggered the report is considered an involved driver, and is not distinct from the other drivers for the purpose of this ticket.
  - Session - Mandatory - Dropdown - Select which session the incident took place in. Options available depend on round format: if round format is sprint, then the options available shall be "Sprint Qualifying", "Sprint Race", "Feature Qualifying" and "Feature Race", otherwise, the options available are just "Qualifying" and "Race".
  - Lap - Mandatory where the session is a race, a sprint race or the feature race; not asked otherwise - Integer - The race lap in which the incident took place.
  - Complaint - Mandatory - String - Full description of the incident as per the complainant's understanding.
  - Evidence files - Optional - 0..5 media (image or video) - One or multiple images or video files that provide basis for the claims in the complaint.
    - Evidence files are attached as part of lodging the ticket, before it is filed.
  - Evidence links - Optional - 0..5 links - One or multiple images or video links that provide basis for the claims in the complaint.
    - Between "evidence files" and "evidence links", there must be at least one file/link. Otherwise, the report will not be valid.
- The information shall be validated before the form is closed, so that users do not have to enter it twice. Where it is not valid, the form shall be shown again with what was entered kept.
- After valid submission, the report will be henceforth identified with a unique ID following the format "S<x>_D<y>_R<z>_<w>", where <x> is the number of the season, <y> the tier of the division, <z> the number of the round, and <w> the number of the report pertaining to this season, tier and round, always written in three digits (001, 002 … 999), so that the IDs of a round sort in the order the reports were lodged.
- After valid submission, a channel bearing the report's unique ID as the title will be created, with the information from the form, as entered by the complainant summarised and posted as the header message in the channel. All involved drivers shall be mentioned properly in this message.
- After valid submission, the ticket's state changes immediately to the defence submission stage, and the report is recorded against its season, division and round.
  - Defence submission for the ticket stays open until the period configured by "steward report defence-period" has run from the end of report submission. Every ticket of the round thus leaves defence submission at the same moment, and a ticket lodged in the last minute of report submission still has the whole of that period for its defence.
- There is no limit upon the number of reports a driver may lodge in a round.
- Where two or more reports of the same round concern one incident, the effective head steward of any of them may, during defence submission, merge one into another, as an exchange between stewards in the steward command channel. Nothing is merged unless a steward so judges.
  - The report merged in shall keep its own unique ID, and close with a verdict of its own, posted with the round's report verdicts as any other: rejected as a duplicate of the surviving report, naming it. It shall have no effect upon any licence, and shall not be appealable; the surviving report may be appealed as any other. Its channel shall be removed as any closed ticket's is.
  - Its complainant and involved drivers shall join the surviving ticket as involved drivers, and its complaint and evidence be posted into the surviving ticket's channel.
- The complainant may ask to withdraw their report during defence submission. The withdrawal shall take effect only upon the effective head steward's approval, who shall weigh, among all else, whether other reports were merged into it, and whose complainants would lose their case with it.
  - A report withdrawn shall close with a verdict of No Further Action, posted with the round's report verdicts as any other, stating that the report was withdrawn. It shall have no effect upon any licence, and shall not be appealable.
  - A steward's report may be withdrawn by its effective head steward.
- If there are no reports submitted until this phase ends, then the stewarding cycle closes then. A round with no tickets goes through its cycle close as any other, at the end of report submission: its results and standings are reposted under the Final Results label, its licence sheet reposted, and the auto-rules checked, the close being applied entire or not at all.

### Defence submission
- A ticket enters this phase the moment it is lodged. Once the period of time configured for the duration of the report submission stage elapses, a countdown with the period of time configured by "steward report defence-period" will start, and once it elapses, every ticket of the round will enter the report deliberation phase together.
- The rules for a ticket open to its parties, set out under Tickets, hold throughout this phase.

### Report deliberation
- Once this phase is entered, a countdown with the period of time configured by "steward report deliberation-period" will start.
- Once this phase is entered, the bot shall post in the ticket's channel, for the effective head steward, the incident as the verdict is to describe it: proposed as the complaint, word for word. Buttons usable only by the effective head steward shall accept it as it stands, or open it for editing, prefilled. It may be settled at any time until the ultimate result is reached; where it has not been, it joins the justification in the hour that follows, and is published as proposed should that hour run out.
- The ballot of a report is as set out under Tickets. Its outcomes are those currently configured that are applicable to the session of the incident.
- Where a tie is left unbroken, the message on the verdicts channel reads "The report verdicts for Round <x> are delayed while the stewards settle a decision. They will follow shortly." It shall be deleted once the round's report verdicts are posted.
- Only after the final output is determined for all reports pertaining to a given round of a given division, will they be posted, in the order the reports were lodged, which their IDs follow, in the verdicts channel.
- The channel will not be deleted upon the publishing of the report verdicts. A ticket's channel is kept until its round's stewarding cycle closes, the appeal submission and appeal deliberation stages having need of it.
- The report deliberation phase is only considered over once all reports pertaining to a given round of a given division are posted to the appropriate channel.
  - If appeals functionality is enabled, then the stewarding cycle will move on to that phase.
  - Otherwise, then the stewarding cycle is considered closed.
- Once the report deliberation phase is considered over, the round results and the standings after the round will be reposted, with all accrued time penalties factored in exclusively.

### Appeal submission
- Once this phase is entered, the bot shall delete the "default" message (written again once the report submission phase ends), and post an "Appeal incident" button to the configured ticket channel of the division, without mentioning the division role.
- Where more than one round of a division has appeal submission open at once, a separate "Appeal incident" button shall be posted for each, naming the round it pertains to.
- Once this phase is entered, a countdown with the period of time configured by "steward appeal submission-period" will start. Once this time elapses, the default message shall be displayed once more in the division's ticket channel, and the "Appeal incident" button removed.
- When a user presses the "Appeal incident" button, it will be verified if they meet the requirements for lodging an appeal, which shall be:
  - Is part of the division of that report, whether or not they were involved in it: a driver may appeal a ruling they believe incongruent with others, though it concerned other drivers.
  - Their current number of tokens is equal to or greater than the cost configured by "steward appeal tokens".
- Where the user is a member of the stewarding team, the bot shall warn them before the appeal is filed that they are filing it as a driver and not as a member of the stewarding team, and they shall confirm or withdraw.
- Once it is determined that the user meets the requirements to perform an appeal, a form shall appear, collecting the following:
  - Season - Shown, and not asked for. Derived from the current season's number.
  - Division - Shown, and not asked for. Derived from the division to which the ticket channel is associated.
  - Round - Shown, and not asked for. Derived from the round whose appeal submission is open, as named by the button pressed.
  - Report ID - Mandatory - String - The unique ID of the report that the driver wishes to appeal, as per the report's channel's title and its verdict.
    - Auto-rule verdicts' or other appeals' IDs are not accepted. A steward's report may be appealed as any other.
    - A report may be appealed once. Where an appeal of it has already been lodged, the appeal shall be refused, naming the appeal already open. The driver who lodged the open appeal alone pays for it in tokens, and alone is refunded.
    - The driver who lodges an appeal is an involved driver of it, whether or not they were involved in the report, and their licence is on the line as every involved driver's is: the ballots of the appeal give an outcome to them as to the report's involved drivers.
  - Justification - Mandatory - String - Reason for the appeal, outlining the reason as to why the user disagreed with the initial verdict handed out.
  - Evidence files - Optional - 0..5 media (image or video) - One or multiple images or video files that provide basis for the claims in the complaint.
    - Evidence files are attached as part of lodging the ticket, before it is filed.
  - Evidence links - Optional - 0..5 links - One or multiple images or video links that provide basis for the claims in the complaint.
    - Between "evidence files" and "evidence links", there must be at least one file/link. Otherwise, the appeal will not be valid.
- The information shall be validated before the form is closed, so that users do not have to enter it twice. Where it is not valid, the form shall be shown again with what was entered kept.
- After valid submission, the appeal will be henceforth identified with a unique ID following the format "<Report ID>_APPEAL", where <Report ID> is the full ID of the original report.
- After valid submission, a channel bearing the appeal's unique ID as the title will be created, with the information from the form, as entered by the appellant summarised and posted as the header message in the channel, and with an additional link to the channel that pertains to the original report, for the stewarding team's reference.
- As this is considered a different ticket, the effective stewarding team for this ticket may not necessarily be the same one as the original report's by default.
- The rules for a ticket open to its parties, set out under Tickets, hold throughout this phase.
- If a report is not appealed by the end of the appeal submission phase, the stewarding cycle for that ticket will be deemed closed.
- If there are no appeals submitted until this phase ends, then the stewarding cycle is considered closed.

### Appeal deliberation
- Once this phase is entered, a countdown with the period of time configured by "steward appeal deliberation-period" will start.
- Once this phase is entered, the bot shall post in the ticket's channel, for the effective head steward, the grounds of appeal as the verdict is to describe them: proposed as the appellant's justification, word for word. They are settled as the incident of a report is, and until the same moment. The incident itself is taken unchanged from the report's verdict.
- The ballot of an appeal is as set out under Tickets, save that:
  - It shows the Appeal ID.
  - It carries first a Decision - Dropdown - Mandatory - A dropdown consisting of two options, "Uphold initial verdict" and "Change initial verdict".
  - Its line for each involved driver is greyed out unless the decision is "Change initial verdict". Its outcomes are those currently configured that are applicable to the session of the original report's incident, and NFA, and each line is prefilled with the outcome the initial verdict gave that driver.
  - Where a tie is left unbroken, the message on the verdicts channel reads "The appeal verdicts for Round <x> are delayed while the stewards settle a decision. They will follow shortly." It shall be deleted once the round's appeal verdicts are posted.
- A ballot with the decision "Change initial verdict" is only valid where it gives at least one involved driver an outcome other than the initial verdict gave them.
- A ballot with the decision "Uphold initial verdict" is the option of the initial verdict, and is counted as such.
- Only after the final output is determined for all appeals pertaining to a given round of a given division, will they be posted, in the order of the reports they appeal, which their IDs follow, and not in the order the appeals were lodged, in the verdicts channel.
- Once the stewarding cycle of a round closes, the channels of all its tickets, reports and appeals alike, shall be removed. Where "steward backup report-toggle" is toggled on, the content of every one of them shall be persisted to disk immediately, and once that has succeeded the channels shall be deleted. Where it is toggled off, a 7 day countdown shall be initiated, at the end of which the channels shall be deleted.
  - Where appeals are disabled, or no appeal is lodged, the cycle closes earlier, and its channels are removed from then.
- The appeal deliberation phase is only considered over once all appeals pertaining to a given round of a given division are posted to the appropriate channel.
- Once the appeal deliberation phase is considered over, the round results and the standings after the round will be reposted with the appeals' time penalties factored in.
  - If a verdict was changed in an appeal in comparison to the original report, the time penalty value displayed in the appeals column should take the latter into consideration (e.g. a penalty of 5 seconds that was rescinded should show as -5s in the appeal column)

### Cycle close
- The close of a cycle shall be applied entire or not at all. Before anything is written to any driver licence, the bot shall establish that every channel the close will post to exists and can be posted to: the division's verdicts, results, standings and licence channels, and the licence channel of every other division whose sheet the close will post anew.
  - Where any of them cannot be posted to, nothing shall be written, and the cycle shall wait at its close. The steward log channel and the log channel shall name the division and the channel at fault, and the command that repairs it.
  - Once the channel is repaired, the close shall go ahead.
  - The close shall not be recorded as done before the postings it claims have been made.
- Once all tickets for a given round of a given division reach this stage, or at once where the round has none, warning points, penalty points, qualifying bans, race bans, season bans and league bans are made effective and added to a driver licence. After this is done, it will be checked whether the driver licences infringe upon any of the auto-rules configured. The cycle pertaining to a round of a given division, the licences checked are those of the drivers seated in that division and of any other involved driver of that round's tickets.
  - Auto-rule handling is set out under Auto-rule triggering.
- Once auto-rules are verified, the previous licence sheet shall be deleted, and an updated one, with the penalties of the latest round updated, will be posted.
  - Licence sheet posting is set out under Licence sheet output.
- <NEW COMMAND> A "steward verdict republish" command will be made available to the head steward (or acting head steward, if active), which shall have as mandatory input the unique ID of a report, an appeal or a CoC investigation. If the ID is valid, a form will show the verdict as published — its ID, its decision and its justification — of which the justification alone may be changed. The user must confirm before the change is validated.
  - If there was an actual modification to the justification, and if this field is not empty/whitespace, the verdict shall be edited in place: its text, and its image rendered again, in the message in which it was originally posted. No other verdict is touched, and the verdicts channel keeps its order.
  - A verdict may be republished only until the next batch of verdicts lands upon it, so that the verdicts channel keeps its order:
    - A report's verdict, until the round's appeal verdicts are posted; or, where appeals are disabled or none was lodged, until the round's cycle closes.
    - An appeal's verdict, until the division's next round begins; or, for the final round of a season, until the season is completed.
    - A Code of Conduct Verdict, until the next Code of Conduct Verdict is posted.
    - An Automated Ruling carries no justification of the stewards' and shall not be republished.
  - There is no command to correct the outcome of a verdict. Drivers are judged by a collective of their peers, guarded against bias by the conflict of interest rules and against error by appeals. What remains is corrected by the revoke commands, for points and bans, and by the results & standings module's amendment of a round's results, for a time penalty or a disqualification.

## Conduct cycle
- Once an investigation's verdict is posted in the appropriate channel by the end of the investigation deliberation phase, the incident (investigation) is deemed closed and final.

### Trigger
- <NEW COMMAND> A "steward conduct start" command will be made available to the head steward and the temporary head steward, and to every steward where "steward conduct steward-start-toggle" allows it, to be utilised in the channel configured by "steward channel command" exclusively, which will have as input 1 or more user IDs of a server member (not necessarily a driver, only requires the "base_role" as configured by "module enable signup"), so that a CoC investigation is opened against said user.
- When valid usage, a form shall be shown, collecting the following:
  - Involved users - Mandatory - 0..n mentions - Other users directly or indirectly involved in the event being investigated, whose footage or evidence may be of use to the stewarding team's deliberations.
  - Complaint - Mandatory - String - Full description of the event being investigated by the stewarding team.
  - Evidence files - Optional - 0..5 media (image or video) - One or multiple images or video files that provide basis for the claims in the complaint.
    - Evidence files are attached as part of lodging the ticket, before it is filed.
  - Evidence links - Optional - 0..5 links - One or multiple images or video links that provide basis for the claims in the complaint.
    - Between "evidence files" and "evidence links", there must be at least one file/link. Otherwise, the investigation will not be valid.
- Usage of the "steward conduct start" shall be valid at all times of a season's, a division's, or round's lifecycle, and is therefore not pegged to any one round, division or season.
- The information shall be validated before the form is closed, so that users do not have to enter it twice. Where it is not valid, the form shall be shown again with what was entered kept.
- After valid submission, the investigation will be henceforth identified with a unique ID following the format "COC_INV<x>", where <x> is the number of the previous CoC investigation of this league plus 1, written in at least three digits: COC_INV001 … COC_INV999, then COC_INV1000, and so on, their order being that of their numbers
- After valid submission, a channel bearing the investigation's unique ID as the title will be created, with the information from the form, as entered by the steward who opened it summarised and posted as the header message in the channel. All involved users shall be mentioned properly in this message.
- Upon valid submission, a CoC investigation ticket will move immediately onto the defence submission stage.

### Defence submission
- Once the investigation information is validated and the channel opened with the relevant information, a countdown with the period of time configured by "steward conduct defence-period" will start. Once this time elapses, the ticket will enter the investigation deliberation phase.
- The rules for a ticket open to its parties, set out under Tickets, hold throughout this phase, the involved users standing as its involved drivers.

### Investigation deliberation
- Once this phase is entered, a countdown with the period of time configured by "steward conduct deliberation-period" will start.
- The ballot of an investigation is as set out under Tickets, save that it shows the Investigation ID, and gives a line to each involved user. Its outcomes are all conduct outcomes currently configured that are not paused.
- Only after the final output is determined for the investigation, it will be posted immediately in the channel configured by "steward channel conduct-verdicts", whether or not the user holds a seat and whether or not a season is live.
  - Where a season is live and the user holds a seat in any of its divisions, full-time or reserve, the verdict shall also be posted in the verdicts channel of each of those divisions, each such post carrying the indication "(repost)".
- After the verdict is posted successfully, the conduct cycle will be considered closed.
- Once its verdict is posted, the investigation's channel shall be removed. Where "steward backup conduct-toggle" is toggled on, its content shall be persisted to disk immediately, and once that has succeeded the channel shall be deleted. Where it is toggled off, a 7 day countdown shall be initiated, at the end of which the channel shall be deleted.

### Cycle close
- After leaving the investigation deliberation stage, the verdict output of a Code of Conduct investigation will be posted immediately.
- After leaving the investigation deliberation stage, all outcomes are made effective and added to the user's driver licence. After that, it will be checked whether the driver licences of any driver infringe upon any of the auto-rules configured.
  - If for some reason the user does not have a driver licence at this point, it will be created before applying the outcome.
  - Auto-rule handling is set out under Auto-rule triggering.
- Once auto-rules are verified, the previous licence sheet of all divisions to which the user belongs to shall be deleted, and an updated one, with the penalties of the latest round updated, will be posted.
  - This is only valid if the user is a driver and a season is currently active.
  - Licence sheet posting is set out under Licence sheet output.

## Revoking penalties
- Revoke commands are a last-ditch measure against honest mistakes, and should not be used unless in a pinch.
- <NEW COMMAND> A "steward revoke warning-point" command will be made available to the head steward (or acting head steward, if active), which shall have as mandatory inputs a mention for a user, the number of active and total warning points to be revoked from their driver licence, and a justification to be available in the steward log.
  - This command fails if there is no such driver with that ID, if warning points are disabled, or if the driver found does not possess the input number of warning points in their history as per their driver licence.
  - The removal of points will be guarded against underflow, meaning that active and total warning points have a minimum limit of 0.
  - The warning points to be removed from the record will be those acquired most recently.
- <NEW COMMAND> A "steward revoke penalty-point" command will be made available to the head steward (or acting head steward, if active), which shall have as mandatory inputs a mention for a user, the number of active and total penalty points to be revoked from their driver licence, and a justification to be available in the steward log.
  - This command fails if there is no such driver with that ID, if warning points are disabled, or if the driver found does not possess the input number of warning points in their history as per their driver licence.
  - The removal of points will be guarded against underflow, meaning that active and total penalty points have a minimum limit of 0.
  - The penalty points to be removed from the record will be those acquired most recently.
- <NEW COMMAND> A "steward revoke discipline-point" command will be made available to the head steward (or acting head steward, if active), which shall have as mandatory inputs a mention for a user, the number of active and total discipline points to be revoked from their driver licence, and a justification to be available in the steward log.
  - This command fails if there is no such driver with that ID, if the conduct cycle is disabled, or if the driver found does not possess the input number of discipline points in their history as per their driver licence.
  - The removal of points will be guarded against underflow, meaning that active and total discipline points have a minimum limit of 0.
  - The discipline points to be removed from the record will be those acquired most recently.
- <NEW COMMAND> A "steward revoke quali-ban" command will be made available to the head steward (or acting head steward, if active), which shall have as mandatory inputs a mention for a user, and a justification to be available in the steward log.
  - This command fails if there is no such driver with that ID, if qualifying bans are disabled, or if the driver found does not possess a currently active qualifying ban.
  - This command can only remove one qualifying ban that is yet to be served. A qualifying ban that has already been served cannot be removed from the record.
  - If the user has multiple qualifying bans, one is removed from their driver licence.
- <NEW COMMAND> A "steward revoke race-ban" command will be made available to the head steward (or acting head steward, if active), which shall have as mandatory inputs a mention for a user, and a justification to be available in the steward log.
  - This command fails if there is no such driver with that ID, if race bans are disabled, or if the driver found does not possess a currently active race ban.
  - This command can only remove one race ban that is yet to be served. A race ban that has already been served cannot be removed from the record.
  - If the user has multiple race bans, one is removed from their driver licence.
- <NEW COMMAND> A "steward revoke season-ban" command will be made available to the head steward (or acting head steward, if active), which shall have as mandatory inputs a mention for a user, and a justification to be available in the steward log.
  - This command fails if there is no such driver with that ID, if season bans are disabled, or if the driver found does not possess a currently active season ban.
  - This command can only remove a season ban that is still active. A season ban that has already been served cannot be removed from the record.
  - Where season bans are stacked, only the active one is removed, and the one stacked behind it begins at once.
  - Upon removal of the season ban, the season ban role will be removed from them, unless a stacked season ban begins in its place.
- <NEW COMMAND> A "steward revoke league-ban" command will be made available to the head steward (or acting head steward, if active), which shall have as mandatory inputs a mention for a user, and a justification to be available in the steward log.
  - This command fails if there is no such driver with that ID, if league bans are disabled, or if the driver found does not possess a currently active league ban.
  - Upon removal of the league ban, the league ban role will be removed from their current account where it is in the server, and otherwise shall not be given to it when it returns.
  - A season ban the league ban replaced shall not be restored.
- While the stewarding module is disabled, "steward revoke season-ban" and "steward revoke league-ban" shall be available to league admins, and shall be the only commands of this module available. The justification shall be written to the log channel.
  - Lifting a season or league ban while the module is disabled is left entirely to the league: no ban expires while the module is disabled.

## Auto-rule triggering
- If any auto-rule configured is infringed upon, then an additional automated verdict document will be published by the bot. Where it was triggered at the close of a round's cycle, it is posted in the verdicts channel of that round's division, under that round's header; where it was triggered by a CoC investigation, it is posted as set out under Verdict output. This document shall inform which rule was broken, and the punishment to be handed out.
  - Its format is set out under Verdict output.
  - For output purposes, two different formats may be used, depending on what was the trigger of the auto-rule:
    - If the trigger was the close of a round's cycle, the "S<x>_D<y>_R<z>_AR<w>" format will be followed, where <x> is the number of the season, <y> the tier of the division, <z> the number of the round, and <w> the number of the Automated Ruling of this round, always written in three digits, as in "S3_D1_R5_AR001". The rulings upon qualifying bans and race bans served or not served share this numbering with the round's auto-rules, so that every Automated Ruling of a round sorts together.
    - If the trigger was a CoC investigation, the "COC_INV<x>_AR<y>" format will be followed, where <x> is the number of the CoC investigation which triggered the auto-rule, as its own ID writes it, and <y> is the number of the auto-rule triggered by it, starting at 1 and rising with each, written in three digits, as in "COC_INV001_AR001".
  - It is possible that an auto-rule triggers another auto-rule. The auto-rules shall be checked again after any is triggered, until none is triggered that has not already been. Each auto-rule shall be triggered at most once for a driver in one cycle close, so that no rule punishes the same driver twice in one close, and the checking always comes to an end.
- As a way to prevent drivers from being penalised twice for going over a threshold (e.g. an auto rule being triggered when a driver licence reaches 4 penalty points when a driver goes from 2 penalty points to 5, meaning they could be handed out two instances of the automated penalty), thresholds shall function in a flip-flop manner. This means that, in the example given, once a driver goes over the 4 penalty point threshold of the automated penalty, they can only infringe it after their licence's active penalty points tally goes under 4 penalty points.
  - How a threshold re-arms depends upon the type of the auto-rule:
    - Single round - It does not flip-flop. It is judged afresh for each round, and triggered for each round in which its criteria are met.
    - Multi round - It flip-flops upon the count within its window of rounds: once triggered, it can be triggered again only once that count has gone under the threshold.
    - Active accumulation - It flip-flops upon the tally of active penalties, as above.
    - Historical accumulation - The lifetime tally only rises, so it is triggered each time the tally crosses a further multiple of the threshold: for a threshold of 10, at 10, at 20, at 30, and so on.

## Bans
- A driver whose licence records any sanction, active or not, shall never be deleted. Returning to Not Signed Up, they shall be retained as a former driver is, their licence with them, a league having most cause to keep the record of a driver it has sanctioned.

- Whether a qualifying ban or a race ban was served in a round shall be judged from the round's results, once its initial results are posted, and posted then as an Automated Ruling to the verdicts channel of the division, in a batch of its own. The driver licence shall be updated at once, so that a ban served no longer stands against the driver's next round, whenever that falls.
  - A race ban is judged in every round in which it is to be served, and a ruling posted whether it was served or not.
  - A qualifying ban is judged, and a ruling posted, only where the driver took part in the round. Where they did not, nothing is posted, and the ban carries on.
  - Where the round's results are resubmitted before the round is final, the judgement shall be made again. Where it changes, a new Automated Ruling shall correct the earlier one; where it does not, nothing is posted.
  - This is the one change to a licence made before a cycle closes. It only ever confirms that a ban carries on or clears it, and never imposes one.

- A driver who gets a season ban or a league ban while party to open tickets shall remain an involved driver of each, keeping their access to defend themselves and their line upon every ballot.
- A driver so banned is no longer of any division, and may lodge no further appeal. Their bans being applied only once the appeal stage of the round that gave rise to them has ended, they will have had that round's appeals open to them.
- Where a ban frees a driver's seat for a round whose reserves have already been distributed, and the round has not yet begun, the distribution of reserves for that round shall be run again in full, as the attendance module distributes them, and its message posted again in the division's check-in channel in place of the former one.

### While the module is disabled
- Season bans and league bans on a driver licence shall remain in force while the stewarding module is disabled. A driver holding one shall be unable to engage the signup wizard, shall not be assigned to a team, and shall be unable to check in.
- No season or league ban shall expire while the module is disabled. It shall be lifted only by a league admin revoking it.
- A season or league ban shall be held while the module is disabled, as qualifying and race bans are: the time and the races that pass while it is disabled shall not count towards its expiry, and the count shall resume where it stood once the module is enabled again.
  - A season ban expiring upon a season's end whose season ended while the module was disabled shall instead expire upon the end of the first season to end with the module enabled.
- Qualifying bans and race bans shall be held while the module is disabled: they shall be neither enforced nor served, and shall resume once the module is enabled again.
- Warning points, penalty points and discipline points shall be held while the module is disabled, as bans are: the rounds, time and season ends that pass meanwhile shall not count towards their expiry, and the count shall resume where it stood once the module is enabled again.

### Qualifying bans
- Whether a driver has a qualifying ban is only determined after the closing of a stewarding cycle, and after factoring in the auto-rules.
- A qualifying ban shall be served in the driver's principal division, whatever division the offences behind it were committed in, the licence being the driver's and its points being held across divisions.
  - A driver with no principal division, being a reserve driver alone, shall serve it at the next upcoming round of all the divisions in which they are a reserve driver.
- If the attendance module is enabled, a driver with a qualifying ban to be served in a given division as per the requirement above shall be told so by the bot in that division's check-in channel, and reminded of it closer to the round:
  - A full-time driver shall be told when the check-in call for the round is posted. A reserve driver shall be told when they are given a seat for the round at the distribution of reserves, in the message that tells them the team they are racing for.
  - Every such driver shall be reminded at the check-in deadline, alongside the message distributing the reserves. A reserve given their seat then is told and reminded in the one message.
  - These messages must be deleted alongside the check-in call for the round.
- If the attendance module is disabled, no such notice is posted. The outstanding ban is shown on the licence sheet.
- The qualifying ban will be considered "served" if the driver set no lap time in any qualifying session of the round in which they must serve the ban, whether classified in it without a time or absent from it, and took part in the round, appearing in the results of any of its sessions, regardless of their final result.
  - Additionally, a qualifying ban being correctly served will trigger the immediate posting of a verdict to the verdicts channel of the division informing of this. Its format is set out under Verdict output.
- If a driver who has a qualifying ban does not take part in a round (missing in the results of all sessions), then their qualifying ban will be considered unserved, and will carry on to their next round, silently.
- If a driver who has a qualifying ban fails to serve it properly by setting a lap time in any qualifying session of the round, then their qualifying ban will be considered unserved, and will carry on to their next round.
  - Additionally, the failure to serve a qualifying ban properly will trigger the immediate posting of a verdict to the verdicts channel of the division informing of this. Its format is set out under Verdict output.
- As qualifying bans are assigned to a driver licence, they do not expire upon a season's end. A qualifying ban not served in the season in which it was received shall be served in whichever later season the driver next takes part in, whether that is the following season or any after it.
  - In that later season, it shall be served as any ban is: in the driver's principal division in that season, or, where they are a reserve driver alone, at the next upcoming round of all the divisions in which they are a reserve driver.
  - A driver who takes part in no later season shall keep the qualifying ban pending on their licence.

### Race bans
- Whether a driver has a race ban is only determined after the closing of a stewarding cycle, and after factoring in the auto-rules.
- A race ban shall be served in the driver's principal division, whatever division the offences behind it were committed in, the licence being the driver's and its points being held across divisions.
  - A driver with no principal division, being a reserve driver alone, shall serve it at the next upcoming round of all the divisions in which they are a reserve driver.
  - A race ban bars the driver from that one round of that one division. They may race in another division's round meanwhile, as a reserve driver or otherwise, so that no single ban makes them sit out a round in two divisions.
- If the attendance module is enabled and any driver has a race ban to be served in a given division as per the requirement above, their vote in the check-in will be immediately discarded/deleted by the bot if present, and a message shall be posted to the check-in channel informing that they cannot check-in due to a race ban.
  - This driver will not be allowed to vote in the check-in for this round again.
  - This driver will not be punished with attendance points for failing to RSVP for the round: the bot shall give them an automatic attendance pardon, written to the log channel as any pardon is.
  - This message must be deleted alongside the RSVP for the round.
- The race ban will be considered "served" if the driver is not listed in the results of any session pertaining to the round in which they must serve the ban.
  - Additionally, a race ban being correctly served will trigger the immediate posting of a verdict to the verdicts channel of the division informing of this. Its format is set out under Verdict output.
- If a driver who has a race ban fails to serve it properly by being listed in the results of any session pertaining to the round in which they must serve the ban, then their race ban will be considered unserved, and will carry on to the next round of that division.
  - Additionally, the failure to serve a race ban properly will trigger the immediate posting of a verdict to the verdicts channel of the division informing of this. Its format is set out under Verdict output.
- As race bans are assigned to a driver licence, they do not expire upon a season's end. A race ban not served in the season in which it was received shall be served in whichever later season the driver next takes part in, whether that is the following season or any after it.
  - In that later season, it shall be served as any ban is: in the driver's principal division in that season, or, where they are a reserve driver alone, at the next upcoming round of all the divisions in which they are a reserve driver.
  - A driver who takes part in no later season shall keep the race ban pending on their licence.

### Season bans
- Whether a driver has a season ban is only determined after the closing of a stewarding cycle, and after factoring in the auto-rules.
- If a driver who is currently participating in a division gets a season ban, they will immediately be unassigned from all seats they hold, including Reserve team seats, across all divisions.
- A driver who gets a season ban shall be returned to Not Signed Up as a sacking returns them: every seat freed, every division, team and signup role revoked.
- If the attendance module is enabled and any driver has a season ban enforced unto them, their vote in any divisions' check-in will be immediately discarded/deleted by the bot if present, and a message shall be posted to the check-in channel informing that they cannot check-in due to a season ban.
- If the sign-up module is enabled and any driver has a season ban enforced unto them, any existing sign-ups, completed and uncompleted, will be cancelled and discarded.
- If the sign-up module is enabled and any driver has a season ban enforced unto them, they will be unable to engage the sign-up wizard.
- If any driver has a season ban, regardless of sign-up module status, they will be unable to be assigned to a team.
- Season bans will be considered served after the criteria configured in "steward penalty season-ban-type".
- Season bans stack. A season ban received while one is active shall begin when the one before it is served, and not before. Where season bans last until a season's end, a second one received during a season shall therefore bar the driver from the whole of the next.
- A season ban received while the driver is league banned shall be added to their ban history, and shall not be made active.

### League bans
- A driver cannot be season banned and league banned at once, the effect of the two being the same. A league ban received by a season-banned driver replaces the season ban, which ceases to be active together with any season bans stacked behind it.
  - Revoking that league ban shall not restore the season bans it replaced. Timing a revocation is left to the league.
- A league ban bars the driver, and so every Discord account they own, the licence being the driver's and not an account's. It bans no account from the server.
- The bot cannot know an account that has never been attached to the driver. Where a banned driver returns under such an account, the league shall attach it to them by reassigning or merging as the core specification sets out, and the merged licence shall carry the ban to it.
- Whether a driver has a league ban is only determined after the closing of a stewarding cycle, and after factoring in the auto-rules.
- If a driver who is currently participating in a division gets a league ban, they will immediately be unassigned from all seats they hold, including Reserve team seats, across all divisions.
- A driver who gets a league ban shall be returned to Not Signed Up as a sacking returns them: every seat freed, every division, team and signup role revoked.
- If the attendance module is enabled and any driver has a league ban enforced unto them, their vote in any divisions' check-in will be immediately discarded/deleted by the bot if present, and a message shall be posted to the check-in channel informing that they cannot check-in due to a league ban.
- If the sign-up module is enabled and any driver has a league ban enforced unto them, any existing sign-ups, completed and uncompleted, will be cancelled and discarded.
- If the sign-up module is enabled and any driver has a league ban enforced unto them, they will be unable to engage the sign-up wizard.
- If any driver has a league ban, regardless of sign-up module status, they will be unable to be assigned to a team.

## Viewing a licence
- A driver licence is a public record of the league.
- While the stewarding module is enabled, the panel of the hub channel the core specification sets out shall offer "View licence". The presser names a driver, themselves or any other, and the bot shows that driver licence in full, seen by the presser alone: their active points with the date and expiry of each, their active bans with their expiries and any season bans stacked, their ban history, their discipline points, their appeal tokens, and the ID of the ticket or ruling each entry came from. A member holding no driver licence is told so.
- A licence so shown shall have a textual output and an image output of its own, as the licence sheet does. The image output may carry detail its textual output does not, but shall omit nothing its textual output carries.
- A licence belongs to no single division. Where the driver has a principal division, it dresses the licence as a whole. A driver with no principal division, being a reserve driver alone or holding no seat at all, has their licence drawn in the template's own colours.

### Textual
- The textual output of a licence shall have the following data, in this order:
  - The driver's name and nationality.
  - Current - every division the driver currently races in, each with their team and whether they are a full-time or reserve driver there.
  - Points - for each of warning points, penalty points and discipline points, the number active, and when they expire, soonest first: how many expire at each round or date, and the division of each round named.
  - Bans - for each of qualifying bans, race bans, season bans and league bans, the number active, and where each is to be served, or when it ends.
  - Record - the number of bans of each kind received across the driver's time in the league, and the appeal tokens held, while appeals cost tokens.
  - The ID of the ticket or ruling each active point or ban came from.

### Image
- The image output of a licence shall be consistent with the visual outputs the image module already draws. It shall support the following data fields for the bot to insert information:
  - Driver name - Mandatory
  - Driver nationality flag - Optional
  - Driver portrait - Optional - drawn where the league's portrait settings provide one
  - Principal division logo and colours - Optional - the logo of the principal division, and its per-tier colours applied to the licence as a whole, the league's own logo among what they colour
  - Current - Mandatory - a row per division the driver currently races in: the division's name, in the ordinary ink, and its logo; the team, with its logo; and whether full-time or reserve. Each row may carry an accent in its division's colour.
  - Points - Mandatory - a row per type of point: the number active, and the expiries, soonest first
  - Bans - Mandatory - a row per type of ban: the number active, and where each is to be served, or when it ends
  - Record - Mandatory - the bans received across the driver's time in the league, and the appeal tokens held, while appeals cost tokens

## Verdict output
- While the stewarding module is enabled, the results & standings module's penalty and appeal reviews are not used, and every verdict of a round is this module's, shaped as this section specifies, and drawing on the results & standings module's rendering, templates included, as far as it can.
- While the stewarding module is disabled, the results & standings module issues its verdicts as it always has, and nothing in this section applies to them.
- Where the image module specification describes the verdict graphic or the verdict banner otherwise, this section governs the verdicts this module issues, and the image module specification shall be amended to carry it.
- This section specifies how the verdicts for reports, appeals, CoC investigations and auto-rule triggering must be shaped and formatted.
- Every verdict shall carry a label naming its kind, in its textual and its image output alike:
  - Report Verdict - the verdict of a report, in place of the Post-Race Penalty label the results & standings module gives its own.
  - Appeal Verdict - the verdict of an appeal, in place of the Appeal label the results & standings module gives its own.
  - Code of Conduct Verdict - the verdict of a CoC investigation.
  - Automated Ruling - a verdict the bot issues itself: an auto-rule triggered, and a qualifying ban or race ban served or not served.
  - Attendance Sanction - issued by the attendance module, and unchanged by this one.
- Every verdict shall show the round it pertains to, or the round that gave rise to it, by its season, division, round number and grand prix name, in its textual and its image output alike.
  - A Report Verdict and an Appeal Verdict shall show as well the session and lap of the incident.
  - An Automated Ruling shall show the round whose results or whose cycle close gave rise to it.
- A Code of Conduct Verdict pertains to no division and to no round, and shall show the season alone: the season live when it is posted, or none between seasons. So shall an Automated Ruling given rise to by a CoC investigation.
  - In the textual output the lines it has no value for shall be left out. In the image output the fields it has no value for shall be emptied, and removed where the template declares them removable, without any error being reported.
- A verdict posted again as a repost shall be identical to the original, save the indication "(repost)".
- The image output of a verdict may carry detail its textual output does not, but shall omit nothing its textual output carries.
- An Appeal Verdict shall show, for each involved driver whose outcome the appeal changed, the change and the outcome that now stands: the initial outcome, what was altered — a time penalty reduced by 5 seconds, a penalty point removed, a penalty lifted entirely — and the final outcome, restated in full. Drivers whose outcome did not change shall be shown as unchanged. An appeal that upholds the initial verdict shall say so, and restate it.
  - In each row of its decision, an Appeal Verdict shall carry these changes, in its textual and its image output alike.
- Verdicts shall be posted sequentially and in alphabetic order of their unique ID, among those of the same kind.
  - Attendance sanctions are the attendance module's own, carry no such ID, and are not ordered by this rule. They shall keep the place the attendance module gives them: after the verdicts of the batch that gave rise to them, under that batch's header.
  - This means that "S1_D1_R1_001", "S1_D1_R1_002", ""S1_D1_R1_003", ... will be the correct order.
  - For the purpose of appeals, the ID of the report to which they correspond will be taken and suffixed with "_APPEAL".
- If they pertain to a report, appeal, or auto-rule triggered by either (including auto-rules which were triggered by other auto-rules which were triggered by reports or appeals), verdicts shall be posted in the verdicts channel of the division to which they pertain.
  - Every batch of verdicts posted together for a round shall be headed by a header naming the round, by its season, division, round number and grand prix name. The report verdicts of a round, its appeal verdicts, and the automated rulings of its cycle close are each a batch of their own, and each shall be headed.
    - Where the verdict banner aspect of the image module is on, the header is the verdict banner. Where it is off, or the banner cannot be drawn, the header shall be posted as text.
    - A Code of Conduct Verdict pertains to no round and is headed by none.
- If they pertain to a Code of Conduct investigation, or auto-rule triggered by a CoC investigation's own verdict (or an auto-rule triggered by auto-rule triggered by CoC inv), verdicts shall be posted as a CoC investigation's verdict is: in the channel configured by "steward channel conduct-verdicts", and also in the verdicts channel of each division of the live season in which the user holds a seat, marked "(repost)".

### Automated Rulings
- An Automated Ruling is issued by the bot, with no complaint and no stewards' justification. It fills the fields of a verdict as follows, in its textual and its image output alike:
  - Decision - a single row, for the driver it concerns: for an auto-rule, what the rule handed out, the rule's infringement standing as the infringement; for a ban served, "N/A"; for a ban not served, that the ban carries on.
  - Incident - in its place, a description the bot writes. For an auto-rule, the rule's ID, its infringement where it has one, and what was crossed, such as "6 active penalty points, at or above the threshold of 6". For a ban, what the round's results showed, such as "set no lap time in any qualifying session" or "set a lap time in Sprint Qualifying".
  - Justification given - for an auto-rule, "As per the rules of this league", naming the rule's infringement where it has one, as in "As per rule 4.2 of the rules of this league". For a ban served or not served, "Not applicable".

### Textual
- The textual output of verdicts shall have the following data, all on the same message:
  - Label of the verdict's kind
  - Unique ID of the report/appeal/CoC investigation
  - Season, division, round number
  - Grand prix name, session name, lap (if session is a race session)
  - Decision - one line per involved driver, as a mention: their outcome, in plain language, several penalties being separated by commas, and the infringement most often given them among the ballots of the winning option, where any was. The outcome's ID is not shown. Drivers given an outcome other than NFA come first, in the order they are listed upon the ticket, and those given NFA last, as "No further action".
  - Incident - the incident as the effective head steward settled it, for a report; for an appeal, the incident as the report's verdict published it, and the grounds of appeal as settled. For a CoC investigation, the complaint the stewards gave.
  - Justification given for outcome

### Image
- The image output of verdicts shall be consistent with the visual outputs the image module already draws. As every one of them does, it shall admit the division logo and the per-tier colours the image module provides, and a league's own logo is artwork of its template rather than a field. Its fields shall carry the names, and the optional or mandatory standing, the image module gives the same data on its other graphics — on those linked to a round above all, and on the results graphics first among them. It shall support the following data fields for the bot to insert information:
  - Label of the verdict's kind - Mandatory
  - Unique ID of the report/appeal/CoC investigation - Mandatory
  - Season number - Optional
  - Division name - Mandatory
  - Round number - Mandatory
  - Grand prix name - Optional
  - Grand prix flag - Optional - drawn as the verdict banner draws the flag of the round
  - Session name - Mandatory
  - Lap (if session is a race session) - Optional
  - Decision - Mandatory - one row per involved driver: their server display name, with their nationality flag and their team where the template declares them, as the rows of the results graphics draw them; their outcome, in the descriptive language the textual announcement carries; and the infringement most often given them, where any was. The outcome's ID is not shown. Drivers given an outcome other than NFA come first and those given NFA last, and the two shall be told apart.
  - Incident - Mandatory - as in the textual output, placed where the image module places a verdict's description. An Appeal Verdict adds the grounds of appeal.
  - Justification given for outcome - Mandatory

## Licence sheet output
- The licence sheet for a given division shall be posted in the channel configured by "division licence-channel".
- Whenever a driver licence changes, whatever caused the change — a cycle close, a Code of Conduct Verdict, a ruling upon a ban served or not served, a revocation, or a merge — the licence sheet of every division in which they hold a seat shall be posted anew, the last message containing it being deleted.
- A qualifying ban or race ban outstanding shall appear upon the sheet of the division in which it is to be served alone.
- The licence sheet shall be posted in the appropriate channel at the start of a season as well.
- The licence sheet shall contain obligatorily all full-time and reserve drivers assigned to that division.
- The drivers will be listed ordered according to the following list of criteria in descending order of priority:
  - Number of active penalty points, highest first;
  - Number of active warning points, highest first;
  - Team name alphabetical order;
  - Display name alphabetical order.

### Textual
- There will be two main parts to the licence sheet: current points status and outstanding bans to be served.
- The current points status will consist of a list of all drivers, with their corresponding current tally of active penalty points and active warning points, and, while appeals cost tokens, the number of appeal tokens each holds.
  - The order will be as specified above.
- The outstanding bans list will consist of a list of all drivers with active qualifying bans or race bans to serve, ordered with race bans first, then qualifying bans. Within each, the drivers with the most bans outstanding come first, and then the order set out above.
- The textual output shall end with a list of the drivers suspended from racing: those season banned or league banned who held a seat in the division when they were banned. Each shall be shown with when their ban ends: the round, the date or the season's completion for a season ban, stacked season bans counted in, and "permanent" for a league ban. A driver shall remain listed until their ban ends or is revoked, into a later season too, upon the sheet of the division of the same tier.

### Image
- The licence sheet shall be an aspect of the image module, following the pattern every other aspect follows:
  - <COMMAND CHANGE> A "licence" value shall be added to the "images config toggle" command, switching the image output of the licence sheet on and off. It shall be off by default.
  - <NEW COMMAND> An "images template licence" command shall take in a string standing for the filename of the template licence sheet image. By default, the filename shall be "licence_template.svg".
  - While the aspect is off, or where generating the image fails, the licence sheet shall be posted as text.
- The image output will be similar to that of the attendance sheet, and consistent with the visual outputs the image module already draws. As every one of them does, it shall admit the division logo and the per-tier colours the image module provides, and a league's own logo is artwork of its template rather than a field. The following data elements shall be allowed:
  - Season, division - Mandatory
  - After Round X - Mandatory
  - Grand Prix name - Optional
  - Type of post - Mandatory, always "Licence Information", can be hardcoded
  - Table-list of drivers - Mandatory, with the following columns:
    - Driver display name - Mandatory
    - Driver nationality flag - Optional
    - Team name - Optional
    - Team logo - Optional
    - Total penalty points - Mandatory if penalty points enabled
    - Total warning points - Mandatory if warning points enabled
    - Total active penalty points from prior season - Mandatory if penalty points are enabled and their expiry, as configured by "steward penalty penalty-point-expiry", is other than season end
    - Total active warning points from prior season - Mandatory if warning points are enabled and their expiry, as configured by "steward penalty warning-point-expiry", is other than season end
    - Column for each round - Optional, displays data as 1PP (penalty point), 1W (warning), QB (qualifying ban), RB (race ban), SB (season ban), LB (league ban), can show multiples of those
      - Cells with penalties may gain a highlighting background as well!
    - Current outstanding ban to serve - Mandatory
    - Appeal tokens held - Mandatory while appeals cost tokens
  - Drivers suspended from racing - Optional - the list the textual output ends with. A template not declaring it leaves it out; the textual output always carries it.


## When the bot restarts
- All of this module's scheduled work shall survive the bot stopping and starting again. What came due while the bot was stopped shall be carried out when it starts, in the order it would have happened. A ticket's buttons, ballots and pending requests shall keep working.
- The time the bot was stopped, or cut off from Discord, shall not count against any window in which a user or a steward acts: report submission, defence submission, the deliberations, appeal submission, the conduct cycle's stages, and the hour given the effective head steward to break a tie or to confirm a justification. Each such window open during the gap shall be lengthened by the gap, and every later stage of the same cycle moved on by it alike. A gap of no more than a few minutes may be disregarded.
  - Clocks in which no one acts run on regardless: the expiry of a timed season ban, and the countdown to the deletion of a closed ticket's channel.
- A cycle close waiting for a channel to be repaired shall be tried again whenever the bot starts, and whenever the command that sets that channel is run.

## Test mode
- The stages of a stewarding cycle are stepped through with the core specification's test mode commands, firing the next scheduled event at once and reporting what has run and what remains.
- <NEW COMMAND> A "test-mode report lodge" command shall be available to league managers, lodging a report as a fake driver, with the inputs the report form takes: the division, the round, the involved drivers, the session, the lap and the complaint. The need for evidence shall be waived.
- <NEW COMMAND> A "test-mode appeal lodge" command shall be available to league managers, lodging an appeal as a fake driver, taking the ID of the report appealed and a justification.
- While test mode is enabled, fake drivers may be added to the stewarding team with "steward team add", so that a panel may be built without as many accounts.
- <NEW COMMAND> A "test-mode ballot cast" command shall be available to league managers, casting a ballot as a fake steward upon a ticket in deliberation, taking an outcome for each involved driver and a justification.
- Every one of these shall be refused while test mode is off, and written to the log channel.
