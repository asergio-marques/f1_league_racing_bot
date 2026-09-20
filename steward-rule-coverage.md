# Stewarding module — rule coverage

Generated from `docs/wip-specs/steward_module_specification.md` and the allocation in
[steward-module-issues.md](steward-module-issues.md). **Do not hand-maintain it**: regenerate it
after any change to either, and it will name any rule owned by nobody or by two issues.

Every row is one rule of the spec and the issue that owns it. A rule with no issue would appear
as `—`, which is the gap this table exists to find.

## The issues

- **S01** — The driver licence and the module's vocabulary  ·  #290
- **S02** — Enabling and disabling the module, and the four levels of authority  ·  #291
- **S03** — The module's channels  ·  #292
- **S04** — The stewarding team, its roles and its head  ·  #293
- **S05** — Timings, appeals and justification configuration  ·  #294
- **S06** — Outcomes  ·  #295
- **S07** — Penalty types, expiries and the ban configuration  ·  #296
- **S08** — Automated penalty rules, as configuration  ·  #297
- **S09** — Changing settings during a season  ·  #298
- **S10** — The ticket framework  ·  #299
- **S11** — Deliberation and the ballot  ·  #300
- **S12** — Report submission and defence  ·  #301
- **S13** — Report deliberation and the round's report verdicts  ·  #302
- **S14** — Appeal submission and the appeal tokens  ·  #303
- **S15** — Appeal deliberation  ·  #304
- **S16** — The cycle close  ·  #305
- **S17** — The cycle moves the round, and cancellation  ·  #306
- **S18** — Republishing a verdict  ·  #307
- **S19** — Auto-rule triggering  ·  #308
- **S20** — Season bans and league bans  ·  #309
- **S21** — Serving a qualifying ban or a race ban  ·  #310
- **S22** — Bans while the module is disabled  ·  #311
- **S23** — Viewing a licence, as text  ·  #312
- **S24** — Viewing a licence, as a graphic  ·  #313
- **S25** — What stewarding changes in the attendance module  ·  #314
- **S26** — Code of Conduct investigations, as configuration  ·  #315
- **S27** — Backing up a ticket's channel  ·  #316
- **S28** — The conduct investigation cycle  ·  #317
- **S29** — Revoking a penalty  ·  #318
- **S30** — Verdict output, as text  ·  #319
- **S31** — Verdict output, as a graphic  ·  #320
- **S32** — Championship penalties in the standings  ·  #321
- **S33** — The licence sheet, as text  ·  #322
- **S34** — The licence sheet, as a graphic  ·  #323
- **S35** — Packing the bot  ·  #324
- **S36** — Surviving a restart  ·  #325
- **S37** — Test mode  ·  #326
- **S38** — The LLM justification mode  ·  #327

**902 rules. Rules owned by no issue: 0.**


## MOD — The module itself

| Rule | Issue | The rule |
|---|---|---|
| STW-MOD-001 | S02 | <COMMAND CHANGE> "steward" shall be added to the list of accepted values in the "module enable"… |
| STW-MOD-002 | S02 | <COMMAND CHANGE> The stewarding module may be disabled via a "module disable" command akin to t… |
| STW-MOD-003 | S02 | The stewarding module is disabled by default. |
| STW-MOD-004 | S02 | The stewarding module may be enabled until the season's placements are first confirmed, or whil… |
| STW-MOD-005 | S02 | The stewarding module may be disabled at any time no stewarding cycle stands open in any divisi… |
| STW-MOD-006 | S02 | Season bans and league bans shall remain in force while the stewarding module is disabled, as s… |
| STW-MOD-007 | S02 | The stewarding module depends upon the results & standings module. It may not be enabled while… |
| STW-MOD-008 | S02 | Disabling the results & standings module shall be refused while any stewarding cycle stands ope… |
| STW-MOD-009 | S02 | Where no cycle stands open, disabling the results & standings module shall disable the stewardi… |
| STW-MOD-010 | S02 | If the stewarding module is enabled, then it shall not be possible to use the penalty and appea… |
| STW-MOD-011 | S25 | The stewarding module is connected to the attendance module, and modifies its outputs. |
| STW-MOD-012 | S25 | While the stewarding module is enabled, the penalty review of the results & standings module, i… |
| STW-MOD-013 | S25 | A driver seeking a pardon asks the league's managers as the league sees fit; the bot offers no… |
| STW-MOD-014 | S25 | While the stewarding module is enabled, a round's attendance points shall be distributed once i… |
| STW-MOD-015 | S02 | The stewarding module is connected to the signup module, and modifies its outputs. |
| STW-MOD-016 | S02 | Stewarding module activation status shall be displayed in the configuration review and in the p… |
| STW-MOD-017 | S37 | This module must work with the fake driver rosters used in test mode. |
| STW-MOD-018 | S02 | The stewarding module expands the two tiers of authority the core specification sets out to fou… |
| STW-MOD-019 | S02 | Levels 3 and 4 are independent of levels 1 and 2 and of one another, so as to distribute power… |
| STW-MOD-020 | S02 | A member may hold several levels at once. |
| STW-MOD-021 | S02 | The head steward is a steward as well. Level 3 and its role are distinct from level 4 and its r… |
| STW-MOD-022 | S02 | The numbers name the levels and do not rank levels 3 and 4 beneath the others. |
| STW-MOD-023 | S02 | Throughout this document, "league admins" and "league managers" are to be understood as the cor… |

## CON — Concepts

| Rule | Issue | The rule |
|---|---|---|
| STW-CON-001 | S01 | Driver licence - An individual record held by each driver profile of the driver's standing in t… |
| STW-CON-002 | S01 | Active bans - the number of qualifying bans and the number of race bans the driver has yet to s… |
| STW-CON-003 | S01 | Ban history - the number of qualifying bans, race bans, season bans and league bans the driver… |
| STW-CON-004 | S01 | Appeal tokens - the number of appeal tokens the driver holds, while appeals cost tokens. |
| STW-CON-005 | S01 | Championship record - every championship penalty the driver has received, and every team penalt… |
| STW-CON-006 | S01 | Likewise, a tally of the total of each penalty type is kept. |
| STW-CON-007 | S01 | The driver licence is the state of a driver's bans. A driver is banned while their licence hold… |
| STW-CON-008 | S01 | A driver licence belongs to the driver profile, not to a Discord account. Any of a driver's acc… |
| STW-CON-009 | S01 | Where two driver profiles are merged, as the core specification allows, their licences shall be… |
| STW-CON-010 | S01 | Warning points, penalty points and discipline points shall all be carried, each keeping its own… |
| STW-CON-011 | S01 | The qualifying bans and race bans each has yet to serve shall be added together, and so shall t… |
| STW-CON-012 | S01 | Season bans shall stack as any season bans do. Where either is league banned, the merged driver… |
| STW-CON-013 | S01 | The merged driver shall hold the lower of the two numbers of appeal tokens. |
| STW-CON-014 | S01 | An auto-rule already awarded upon either licence shall be held as awarded upon the merged one,… |
| STW-CON-075 | S01 | Where rules stand at 20 and at 30 penalty points, a driver whose two licences hold 13 and 19 tr… |
| STW-CON-076 | S01 | The merged licence shall be checked against the auto-rules at the next cycle close, as any lice… |
| STW-CON-015 | S01 | A merge shall not be refused because either driver is banned, merging being how a league brings… |
| STW-CON-016 | S01 | Where another account is made a driver's current one, the season ban and league ban roles shall… |
| STW-CON-017 | S01 | Where another account is made a driver's current one while they are party to an open ticket, th… |
| STW-CON-018 | S01 | A steward's vote is a record, and keeps the account it was cast under. |
| STW-CON-019 | S01 | A past account of a driver cannot be a steward, head steward or temporary head steward, and sha… |
| STW-CON-020 | S01 | Where the driver is on the stewarding team, their place on it, and any head steward or temporar… |
| STW-CON-021 | S04 | Steward - A trusted user with level 4 permission, being a member of the stewarding team the bot… |
| STW-CON-022 | S04 | Head steward - A privileged user with level 3 permission denoted with a special role that serve… |
| STW-CON-023 | S04 | Acting head steward - Also referred to as temporary or temp head steward. A privileged user den… |
| STW-CON-024 | S04 | Effective head steward - The designated head steward for a specific ticket. By default, this is… |
| STW-CON-025 | S04 | Stewarding team - The collective composed of all stewards. |
| STW-CON-026 | S04 | Effective stewarding team - The members of the stewarding team who take part in a given ticket.… |
| STW-CON-027 | S04 | Effective head steward. |
| STW-CON-028 | S04 | Those driving in the division to which a report or an appeal pertains, where conflicts of inter… |
| STW-CON-029 | S04 | Any involved driver, or involved user, who belongs to the stewarding team. |
| STW-CON-030 | S16 | Stewarding cycle - The full process for stewarding a round of a division. It begins when report… |
| STW-CON-031 | S12 | Report submission - Active starting at the scheduled round time, and automatically disabled aft… |
| STW-CON-032 | S12 | Defence submission - Active for each ticket from the moment it is lodged, and disabled for ever… |
| STW-CON-033 | S13 | Report deliberation - Active from the moment the defence submission stage ends, and automatical… |
| STW-CON-034 | S14 | Appeal submission - Active for a configured period of time once the report deliberation ends. I… |
| STW-CON-035 | S15 | Appeal deliberation - Active from the moment the appeal submission ends, and automatically disa… |
| STW-CON-036 | S28 | Conduct investigation cycle - The full process for a Code of Conduct investigation. It may be i… |
| STW-CON-037 | S28 | Defence submission - Active from the moment the investigation is opened, and disabled once a co… |
| STW-CON-038 | S28 | Investigation deliberation - Active once defence submission ends, and disabled once a configure… |
| STW-CON-039 | S12 | Complainant - The driver who lodged a report, or the stewarding team as a whole for a steward's… |
| STW-CON-040 | S01 | Principal division - The highest tier division (the lowest tier number) in which a driver holds… |
| STW-CON-041 | S01 | Feature race and feature qualifying - The Feature Race and Feature Qualifying of a sprint round… |
| STW-CON-042 | S10 | Involved driver - A driver who is party to a ticket: for a report, its complainant where a driv… |
| STW-CON-043 | S10 | A ticket shall hold at most 25 involved drivers, or 25 involved users for a CoC investigation,… |
| STW-CON-044 | S10 | Ticket - A user-submitted incident which may be either a report, an appeal or a Code of Conduct… |
| STW-CON-045 | S10 | Ticket channel - The channel of a division, set by "division ticket-channel", in which the "Rep… |
| STW-CON-046 | S10 | A ticket's channel - The channel the bot creates for a single ticket, in which its defence and… |
| STW-CON-047 | S12 | Report - May also be referred to as stewards' report. This is an incident submitted by either a… |
| STW-CON-048 | S12 | Unique ID in the "S<x>_D<y>_R<z>_<w>" format, where <x> is the number of the season, <y> the ti… |
| STW-CON-049 | S14 | Appeal - A special kind of ticket submitted by a driver which aims for a report to be judged on… |
| STW-CON-050 | S10 | Every ticket records everything its stages set out — its unique ID, the round and session it co… |
| STW-CON-051 | S14 | Appeal token - A special kind of currency that may be required for drivers to be able to submit… |
| STW-CON-052 | S28 | Code of Conduct investigation - May also be referred to as a CoC investigation. A special kind… |
| STW-CON-053 | S06 | Outcome - A standardised penalty table item for reports and appeals, which draws a relationship… |
| STW-CON-054 | S06 | Conduct outcome - A standardised penalty table item for CoC investigations, which draws a relat… |
| STW-CON-055 | S01 | Time penalty - A possible direct outcome of a verdict for a report or appeal. Time is added to… |
| STW-CON-056 | S01 | Disqualification - A possible direct outcome of a verdict for a report or appeal. The driver's… |
| STW-CON-057 | S01 | Championship points deduction - A possible direct outcome of a verdict for any ticket. A number… |
| STW-CON-058 | S01 | Championship disqualification - A possible direct outcome of a verdict for any ticket. The driv… |
| STW-CON-059 | S01 | Constructors' points deduction - A possible direct outcome of a verdict for any ticket, given t… |
| STW-CON-060 | S01 | Constructors' championship disqualification - A possible direct outcome of a verdict for any ti… |
| STW-CON-061 | S01 | Warning point - A possible direct outcome of a verdict for a report or appeal. Warning points s… |
| STW-CON-062 | S01 | Penalty point - A possible direct outcome of a verdict for a report or appeal. Penalty points a… |
| STW-CON-063 | S01 | Discipline point - A possible direct outcome of a verdict for a CoC investigation. Discipline p… |
| STW-CON-064 | S01 | Qualifying ban - A possible direct or indirect outcome of a verdict for all ticket types. Quali… |
| STW-CON-065 | S01 | Race ban - A possible direct or indirect outcome of a verdict for all ticket types. Race bans a… |
| STW-CON-066 | S01 | Season ban - A possible direct or indirect outcome of a verdict for all ticket types. Season ba… |
| STW-CON-067 | S01 | League ban - A possible direct or indirect outcome of a verdict for all ticket types. League ba… |
| STW-CON-068 | S08 | Automated penalty rule - Also referred to as an auto-rule. A predefined, league-configured auto… |
| STW-CON-069 | S08 | Single round - Rules that verify only multiples of a specific penalty accrued in a single round… |
| STW-CON-070 | S08 | Multi round - Rules that verify only multiples of a specific penalty accrued across multiple ro… |
| STW-CON-071 | S08 | Active accumulation - Rules that verify only the accumulation of a specific active penalty on a… |
| STW-CON-072 | S08 | Historical accumulation - Rules that verify the total accumulation of a specific penalty across… |
| STW-CON-073 | S01 | Active penalty - A penalty instance that is still active, and is yet to be served, yet to expir… |
| STW-CON-074 | S01 | A championship points deduction, a championship disqualification, a constructors' points deduct… |

## CFG — Configuring the module

| Rule | Issue | The rule |
|---|---|---|
| STW-CFG-001 | S02 | All configuration changes must be logged to the standard log channel. |
| STW-CFG-002 | S02 | All configuration changes done with commands usable by the head steward, acting head steward, o… |

## CHN — Channels

| Rule | Issue | The rule |
|---|---|---|
| STW-CHN-001 | S03 | Every channel this module sets is bound by the rule that a channel serves one purpose upon a se… |
| STW-CHN-002 | S03 | <NEW COMMAND> A "division ticket-channel" command will be made available to league managers, wh… |
| STW-CHN-003 | S03 | If the stewarding module is enabled, confirming the season's placements shall fail if any divis… |
| STW-CHN-004 | S03 | Each division's ticket channel will be displayed in the placements review much alike other divi… |
| STW-CHN-005 | S03 | The stewarding module shall inherit the "division verdicts-channel" command from the results mo… |
| STW-CHN-006 | S03 | <NEW COMMAND> A "division licence-channel" command will be made available to league managers, w… |
| STW-CHN-007 | S03 | If the stewarding module is enabled, confirming the season's placements shall fail if any divis… |
| STW-CHN-008 | S03 | Each division's licence channel will be displayed in the placements review much alike other div… |
| STW-CHN-009 | S03 | <NEW COMMAND> A "steward channel conduct-verdicts" command will be made available to league man… |
| STW-CHN-010 | S03 | If the CoC investigations feature is enabled, confirming a season's configuration shall fail wh… |
| STW-CHN-011 | S03 | <NEW COMMAND> A "steward channel command" command will be made available to league managers, wh… |
| STW-CHN-012 | S03 | A command shall be given in the channel of the level it is given under: by a head steward, temp… |
| STW-CHN-013 | S03 | If the stewarding module is enabled, confirming a season's configuration shall fail while this… |
| STW-CHN-014 | S03 | <NEW COMMAND> A "steward channel log" command will be made available to league managers, which… |
| STW-CHN-015 | S03 | If the stewarding module is enabled, confirming a season's configuration shall fail while this… |

## TEM — The stewarding team

| Rule | Issue | The rule |
|---|---|---|
| STW-TEM-001 | S04 | Every role this module sets serves one purpose. Setting the steward, head steward, temporary he… |
| STW-TEM-002 | S04 | The stewarding team is held by the bot as a list, changed only by the commands below. Stewardin… |
| STW-TEM-003 | S04 | <NEW COMMAND> A "steward role team" command will be made available to league managers, which sh… |
| STW-TEM-004 | S04 | Where the role is changed while the team has members, the bot shall move every member from the… |
| STW-TEM-005 | S04 | <NEW COMMAND> A "steward role head" command will be made available to league managers, which sh… |
| STW-TEM-006 | S04 | Where the role is changed while a head steward is appointed, the bot shall move them from the f… |
| STW-TEM-007 | S04 | <NEW COMMAND> A "steward team add" command will be made available to the head steward and to le… |
| STW-TEM-008 | S04 | The command shall be refused for a member already on the team, and for a past account of a driv… |
| STW-TEM-009 | S04 | <NEW COMMAND> A "steward team remove" command will be made available to the head steward and to… |
| STW-TEM-010 | S04 | The command shall be refused for the head steward, who shall first be replaced. |
| STW-TEM-011 | S04 | A steward removed shall leave the effective stewarding team of every open ticket at once. Any b… |
| STW-TEM-012 | S04 | <NEW COMMAND> A "steward team list" command will be made available to stewards, league managers… |
| STW-TEM-013 | S04 | <NEW COMMAND> A "steward team head-assign" command will be made available to league admins, whi… |
| STW-TEM-014 | S04 | The command shall be refused for a member who is not on the stewarding team, the head steward b… |
| STW-TEM-015 | S04 | Where a head steward is already appointed, they shall be replaced, and shall remain on the team… |
| STW-TEM-016 | S04 | Upon a replacement, on every open ticket on which the former head steward was the effective hea… |
| STW-TEM-017 | S04 | Upon a replacement, a temporary head steward in post shall remain so, and the new head steward… |
| STW-TEM-018 | S04 | If the stewarding module is enabled, confirming the season's placements shall fail while no hea… |
| STW-TEM-019 | S04 | <NEW COMMAND> A "steward role temp-head" command will be made available to league managers, whi… |
| STW-TEM-020 | S04 | This command is only valid if no user has temporary head steward status. |
| STW-TEM-021 | S04 | If the input role parameter is empty, then temporary head steward functionality is deactivated. |
| STW-TEM-022 | S04 | <NEW COMMAND> A "steward team temp-head-assign" command will be made available to the head stew… |
| STW-TEM-023 | S04 | This command is only valid if the temporary head steward role configured by "steward role temp-… |
| STW-TEM-024 | S04 | This command is only valid if the target user is part of the stewarding team and is not the hea… |
| STW-TEM-025 | S04 | Where a temporary head steward is already appointed, they shall be replaced: their appointment… |
| STW-TEM-026 | S04 | While the appointment lasts, the temporary head steward is the effective head steward by defaul… |
| STW-TEM-027 | S04 | While the appointment lasts, the temporary head steward may use the commands of the head stewar… |
| STW-TEM-028 | S04 | <NEW COMMAND> A "steward team temp-head-remove" command will be made available to the head stew… |
| STW-TEM-029 | S04 | This command is only valid if the temporary head steward role configured by "steward role temp-… |
| STW-TEM-030 | S04 | <NEW COMMAND> A "steward team conflict-toggle" command will be made available to league manager… |
| STW-TEM-031 | S04 | By default this setting is on, meaning stewards can review reports/appeals pertaining to the di… |
| STW-TEM-032 | S04 | A CoC investigation pertains to no division; for it, a steward is held to drive in its division… |

## TIM — Timings

| Rule | Issue | The rule |
|---|---|---|
| STW-TIM-001 | S05 | <NEW COMMAND> A "steward report submission-period" command will be made available to league man… |
| STW-TIM-002 | S05 | By default, this value will be set to 48. |
| STW-TIM-003 | S05 | Input value must be equal or greater than 1. |
| STW-TIM-004 | S05 | <NEW COMMAND> A "steward report defence-period" command will be made available to league manage… |
| STW-TIM-005 | S05 | By default, this value will be set to 24. |
| STW-TIM-006 | S05 | Input value must be equal or greater than 1. |
| STW-TIM-007 | S05 | <NEW COMMAND> A "steward report deliberation-period" command will be made available to league m… |
| STW-TIM-008 | S05 | By default, this value will be set to 24. |
| STW-TIM-009 | S05 | Input value must be equal or greater than 1. |
| STW-TIM-010 | S05 | <NEW COMMAND> A "steward appeal submission-period" command will be made available to league man… |
| STW-TIM-011 | S05 | By default, this value will be set to 24. |
| STW-TIM-012 | S05 | Input value must be equal or greater than 1. |
| STW-TIM-013 | S05 | <NEW COMMAND> A "steward appeal deliberation-period" command will be made available to league m… |
| STW-TIM-014 | S05 | By default, this value will be set to 24. |
| STW-TIM-015 | S05 | Input value must be equal or greater than 1. |
| STW-TIM-016 | S05 | The sum of the values of the configurations above may not exceed 168 (7 times 24 hours). This v… |
| STW-TIM-017 | S05 | While appeals are disabled, the appeal submission and appeal deliberation periods are not count… |
| STW-TIM-018 | S05 | This bounds the configured periods alone, and not how long a cycle may run: the waits set out u… |

## APL — Appeals

| Rule | Issue | The rule |
|---|---|---|
| STW-APL-001 | S05 | <NEW COMMAND> A "steward appeal toggle" command will be made available to league managers, whic… |
| STW-APL-002 | S05 | By default, the appeal functionality is enabled. |
| STW-APL-003 | S05 | Enabling appeals shall be refused where the sum of the periods set under Timings, the appeal su… |
| STW-APL-004 | S05 | <NEW COMMAND> A "steward appeal tokens" command will be made available to league managers, whic… |
| STW-APL-005 | S05 | The valid pairs are 0 and 0, meaning appeals cost nothing, or a starting number of 1 or more wi… |
| STW-APL-006 | S05 | By default, both are 0. While the cost is 0, an appeal costs nothing, and no driver's tokens ar… |
| STW-APL-007 | S14 | Otherwise, a driver's number of appeal tokens shall be set to the starting number the first tim… |
| STW-APL-008 | S05 | <NEW COMMAND> A "steward appeal refund-toggle" command will be made available to league manager… |
| STW-APL-009 | S05 | By default, this is toggled on. |
| STW-APL-010 | S14 | An appeal succeeds where its verdict is "Change initial verdict", whichever way the change goes… |
| STW-APL-011 | S14 | A refund shall never bring a driver above the starting number of tokens. |

## JUS — Justifications

| Rule | Issue | The rule |
|---|---|---|
| STW-JUS-001 | S05 | <NEW COMMAND> A "steward justification final-mode" command will be made available to league man… |
| STW-JUS-002 | S05 | Longest - Active by default - The bot takes the longest justification, in character count, amon… |
| STW-JUS-003 | S05 | Own - The bot takes the effective head steward's own justification, if their ballot is of the w… |
| STW-JUS-004 | S38 | LLM - The bot feeds the justifications of all ballots of the winning option to a remote or loca… |
| STW-JUS-005 | S38 | An LLM is connected to the bot by whoever runs it, upon the machine the bot runs on and outside… |
| STW-JUS-006 | S38 | Choosing it shall state that the stewards' justifications will be sent to that LLM, and whether… |
| STW-JUS-007 | S38 | Where the LLM fails to provide a text for a verdict, the fallback mode is used for that verdict. |
| STW-JUS-008 | S05 | By default, this value shall be set to "longest". |
| STW-JUS-009 | S05 | <NEW COMMAND>  A "steward justification fallback-mode" command will be made available to league… |
| STW-JUS-010 | S05 | By default, this value shall be set to "own". |
| STW-JUS-011 | S05 | The method chosen by this command cannot be the same as the one chosen with "steward justificat… |
| STW-JUS-012 | S05 | If "steward justification final-mode" is changed to "longest" when "steward justification fallb… |
| STW-JUS-013 | S05 | If "steward justification final-mode" is changed to "own" when "steward justification fallback-… |
| STW-JUS-014 | S05 | If "steward justification final-mode" is changed to "LLM" when "steward justification fallback-… |
| STW-JUS-015 | S05 | If neither "steward justification final-mode" nor "steward justification fallback-mode" are fea… |

## OUT — Outcomes

| Rule | Issue | The rule |
|---|---|---|
| STW-OUT-001 | S06 | <NEW COMMAND> A "steward outcome add" command will be made available to league managers, which… |
| STW-OUT-002 | S06 | ID - Mandatory - Unique ID for the outcome. Maximum of 12 characters. |
| STW-OUT-003 | S06 | Brief - Mandatory - Unique short description of the outcome. Maximum of 50 characters. |
| STW-OUT-004 | S06 | Description - Optional - Long form description of the outcome. Maximum of 250 characters. |
| STW-OUT-005 | S06 | Applicable to qualifying? - Mandatory - Checkbox that, if ticked, represents that this outcome… |
| STW-OUT-006 | S06 | Applicable to race? - Mandatory - Checkbox that, if ticked, represents that this outcome can be… |
| STW-OUT-007 | S06 | Time penalty - Optional - Integer input only. Number of whole seconds added to the total race t… |
| STW-OUT-008 | S06 | Disqualification - Optional - Checkbox that, if ticked, means that the offending driver's entry… |
| STW-OUT-009 | S06 | Championship points deducted - Optional - Integer input only. Number of points taken from the d… |
| STW-OUT-010 | S06 | Championship disqualification - Optional - Checkbox that, if ticked, means that the driver is r… |
| STW-OUT-011 | S06 | Constructors' points deducted - Optional - Integer input only. Number of points taken from a te… |
| STW-OUT-012 | S06 | Constructors' championship disqualification - Optional - Checkbox that, if ticked, means that a… |
| STW-OUT-013 | S06 | Every penalty above is given to a driver, save the constructors' points deduction and the const… |
| STW-OUT-014 | S06 | Warning points - Optional - Integer input only. Number of warning points added to the driver li… |
| STW-OUT-015 | S06 | Penalty points - Optional - Integer input only. Number of penalty points added to the driver li… |
| STW-OUT-016 | S06 | Qualifying bans - Optional - Integer input only. Number of qualifying bans added to the driver… |
| STW-OUT-017 | S06 | Race bans - Optional - Integer input only. Number of race bans added to the driver licence of t… |
| STW-OUT-018 | S06 | Season ban - Optional - Checkbox that, if ticked, means that the offending driver's licence wil… |
| STW-OUT-019 | S06 | League ban - Optional - Checkbox that, if ticked, means that the offending driver licence will… |
| STW-OUT-020 | S06 | A session's list of outcomes shall hold at most 25, NFA among them. The command shall be refuse… |
| STW-OUT-021 | S06 | This command shall fail if the outcome has any disabled penalty types. |
| STW-OUT-022 | S06 | At least one of the "Time penalty", "Disqualification", "Championship points deducted", "Champi… |
| STW-OUT-023 | S06 | <NEW COMMAND> A "steward outcome modify" command will be made available to league managers, whi… |
| STW-OUT-024 | S06 | The command shall be refused where the change would bring the qualifying list or the race list… |
| STW-OUT-025 | S06 | This command shall fail if the outcome has any disabled penalty types. |
| STW-OUT-026 | S06 | At least one of the "Time penalty", "Disqualification", "Championship points deducted", "Champi… |
| STW-OUT-027 | S06 | <NEW COMMAND> A "steward outcome remove" command will be made available to league managers, whi… |
| STW-OUT-028 | S06 | <NEW COMMAND> A "steward outcome toggle" command will be made available to league managers, whi… |
| STW-OUT-029 | S06 | A paused outcome is offered upon no ballot. It stays in the list, "steward outcome list" markin… |
| STW-OUT-030 | S06 | NFA cannot be paused. |
| STW-OUT-031 | S06 | A paused outcome does not count towards the 24 a session's list may hold. Resuming one shall be… |
| STW-OUT-032 | S06 | <NEW COMMAND> A "steward outcome list" command will be made available to league managers and st… |
| STW-OUT-033 | S06 | The list is shown in full, over several messages where it does not fit in one. |
| STW-OUT-034 | S06 | <brief> |
| STW-OUT-035 | S06 | ID: <id> |
| STW-OUT-036 | S06 | Rule description: <description, if not empty, otherwise this line is skipped> |
| STW-OUT-037 | S06 | Associated outcome: <all penalties associated with the outcome, comma concatenated> |
| STW-OUT-038 | S06 | By default, a permanent, unremovable, unmodifiable outcome is added to the list, with the follo… |
| STW-OUT-039 | S06 | ID - NFA (special reserved ID) |
| STW-OUT-040 | S06 | Brief - No Further Action |
| STW-OUT-041 | S06 | Description - Outcome which means there is no actionable offence in the reported incident, and… |
| STW-OUT-042 | S06 | Applicable to qualifying? - Yes |
| STW-OUT-043 | S06 | Applicable to race? - Yes |
| STW-OUT-044 | S06 | Time penalty - 0 |
| STW-OUT-045 | S06 | Disqualification - No |
| STW-OUT-046 | S06 | Championship points deducted - 0 |
| STW-OUT-047 | S06 | Championship disqualification - No |
| STW-OUT-048 | S06 | Constructors' points deducted - 0 |
| STW-OUT-049 | S06 | Constructors' championship disqualification - No |
| STW-OUT-050 | S06 | Warning point - 0 |
| STW-OUT-051 | S06 | Penalty point - 0 |
| STW-OUT-052 | S06 | Qualifying ban - 0 |
| STW-OUT-053 | S06 | Race ban - 0 |
| STW-OUT-054 | S06 | Season ban - No |
| STW-OUT-055 | S06 | League ban - No |

## PEN — Penalty types and bans

| Rule | Issue | The rule |
|---|---|---|
| STW-PEN-001 | S07 | <NEW COMMAND> A "steward penalty toggle" command will be made available to league managers, whi… |
| STW-PEN-002 | S07 | Discipline points are not among them. They exist only in the conduct cycle, and are switched on… |
| STW-PEN-003 | S07 | By default, all are enabled. |
| STW-PEN-004 | S07 | Toggling a penalty type off shall be refused while any outcome or conduct outcome would be left… |
| STW-PEN-005 | S07 | If a penalty type is toggled off, and any outcome or conduct outcome has that penalty type conf… |
| STW-PEN-006 | S07 | <NEW COMMAND> A "steward role season-ban" command will be made available to league managers, wh… |
| STW-PEN-007 | S07 | <NEW COMMAND> A "steward penalty season-ban-type" command will be made available to league mana… |
| STW-PEN-008 | S07 | Number of rounds - The season ban expires only after a number of rounds have taken place. That… |
| STW-PEN-009 | S07 | The rounds counted are those of the division that set the number, from the first of its rounds… |
| STW-PEN-010 | S07 | Where that division has no rounds left in its season, the count carries into the next season's… |
| STW-PEN-011 | S07 | Nothing is counted while no round takes place, between seasons or where no further season is he… |
| STW-PEN-012 | S07 | Season end - The season ban expires when the season is completed, a league admin completing it. |
| STW-PEN-013 | S07 | A season ban applied once none of the divisions in which the driver held a seat has a round lef… |
| STW-PEN-014 | S07 | A season ban stacked behind another expires at the completion of the season after the one endin… |
| STW-PEN-015 | S07 | Timed - The season ban expires only after a set amount of time, expressed in days. If this opti… |
| STW-PEN-016 | S07 | By default, this will be set to "number of rounds". |
| STW-PEN-017 | S07 | <NEW COMMAND> A "steward penalty warning-point-expiry" command will be made available to league… |
| STW-PEN-018 | S07 | Number of rounds - Warning points expire once a number of rounds has taken place, counted as th… |
| STW-PEN-019 | S07 | Season end - Warning points expire when the season is completed. Warning points received while… |
| STW-PEN-020 | S07 | Timed - Warning points expire after a set number of days. |
| STW-PEN-021 | S07 | By default, this will be set to "number of rounds", the number being the length of the season. |
| STW-PEN-022 | S07 | <NEW COMMAND> A "steward penalty penalty-point-expiry" command will be made available to league… |
| STW-PEN-023 | S07 | By default, this will be set to "number of rounds", the number being the length of the season. |
| STW-PEN-024 | S07 | <NEW COMMAND> A "steward role league-ban" command will be made available to league managers, wh… |

## ARL — Automated penalty rules

| Rule | Issue | The rule |
|---|---|---|
| STW-ARL-001 | S08 | <NEW COMMAND> A "steward auto-rule add-single-round" command will be made available to league m… |
| STW-ARL-002 | S08 | ID - Mandatory - String - Unique ID for this rule. Must not overlap with that of other auto rul… |
| STW-ARL-003 | S08 | Infringement - Optional - String - An optional string for league managers to add a rule number… |
| STW-ARL-004 | S08 | Type of infractions committed - Mandatory - Dropdown - Type of penalty that must be given out t… |
| STW-ARL-005 | S08 | Number of infractions committed - Mandatory - Integer - Quantity of penalties of a certain type… |
| STW-ARL-006 | S08 | This value must be greater than 0. |
| STW-ARL-007 | S08 | Penalty given - Mandatory - Dropdown - Type of penalty to be bestowed upon a driver when this r… |
| STW-ARL-008 | S08 | Number of penalties given - Mandatory - Integer - Quantity of penalties of the type defined in… |
| STW-ARL-009 | S08 | This value must be greater than 0. |
| STW-ARL-010 | S08 | Once the user confirms the auto-rule, the fields will be verified, and if valid, the auto-rule… |
| STW-ARL-011 | S08 | As configured, a single-round rule will be interpreted as meaning "if a driver receives a certa… |
| STW-ARL-012 | S08 | <NEW COMMAND> A "steward auto-rule add-multi-round" command will be made available to league ma… |
| STW-ARL-013 | S08 | ID - Mandatory - String - Unique ID for this rule. Must not overlap with that of other auto rul… |
| STW-ARL-014 | S08 | Infringement - Optional - String - An optional string for league managers to add a rule number… |
| STW-ARL-015 | S08 | Number of rounds - Mandatory - Integer - Number of previous rounds' outcomes that will be check… |
| STW-ARL-016 | S08 | This value must be greater than 1. |
| STW-ARL-017 | S08 | Type of infractions committed - Mandatory - Dropdown - Type of penalty to be detected in the dr… |
| STW-ARL-018 | S08 | Number of infractions committed - Mandatory - Integer - Quantity of penalties of a certain type… |
| STW-ARL-019 | S08 | This value must be greater than 0. |
| STW-ARL-020 | S08 | Penalty given - Mandatory - Dropdown - Type of penalty to be bestowed upon a driver when this r… |
| STW-ARL-021 | S08 | Number of penalties given - Mandatory - Integer - Quantity of penalties of the type defined in… |
| STW-ARL-022 | S08 | This value must be greater than 0. |
| STW-ARL-023 | S08 | Once the user confirms the auto-rule, the fields will be verified, and if valid, the auto-rule… |
| STW-ARL-024 | S08 | As configured, a multi-round rule will be interpreted as meaning "if a driver receives a certai… |
| STW-ARL-025 | S08 | <NEW COMMAND> A "steward auto-rule add-active-accumulation" command will be made available to l… |
| STW-ARL-026 | S08 | ID - Mandatory - String - Unique ID for this rule. Must not overlap with that of other auto rul… |
| STW-ARL-027 | S08 | Infringement - Optional - String - An optional string for league managers to add a rule number… |
| STW-ARL-028 | S08 | Type of infractions committed - Mandatory - Dropdown - Type of penalty that must be active in a… |
| STW-ARL-029 | S08 | Number of infractions committed - Mandatory - Integer - Quantity of penalties of a certain type… |
| STW-ARL-030 | S08 | This value must be greater than 0. |
| STW-ARL-031 | S08 | Penalty given - Mandatory - Dropdown - Type of penalty to be bestowed upon a driver when this r… |
| STW-ARL-032 | S08 | Number of penalties given - Mandatory - Integer - Quantity of penalties of the type defined in… |
| STW-ARL-033 | S08 | This value must be greater than 0. |
| STW-ARL-034 | S08 | Once the user confirms the auto-rule, the fields will be verified, and if valid, the auto-rule… |
| STW-ARL-035 | S08 | As configured, an accumulation of active penalties rule will be interpreted as meaning "if a dr… |
| STW-ARL-036 | S08 | <NEW COMMAND> A "steward auto-rule add-historical-accumulation" command will be made available… |
| STW-ARL-037 | S08 | ID - Mandatory - String - Unique ID for this rule. Must not overlap with that of other auto rul… |
| STW-ARL-038 | S08 | Infringement - Optional - String - An optional string for league managers to add a rule number… |
| STW-ARL-039 | S08 | Type of infractions committed - Mandatory - Dropdown - Type of penalty conferred to a driver li… |
| STW-ARL-040 | S08 | Number of infractions committed - Mandatory - Integer - Quantity of penalties of a certain type… |
| STW-ARL-041 | S08 | This value must be greater than 0. |
| STW-ARL-042 | S08 | Penalty given - Mandatory - Dropdown - Type of penalty to be bestowed upon a driver when this r… |
| STW-ARL-043 | S08 | Number of penalties given - Mandatory - Integer - Quantity of penalties of the type defined in… |
| STW-ARL-044 | S08 | This value must be greater than 0. |
| STW-ARL-045 | S08 | Once the user confirms the auto-rule, the fields will be verified, and if valid, the auto-rule… |
| STW-ARL-046 | S08 | As configured, an accumulation of penalties across one's career in the league rule will be inte… |
| STW-ARL-047 | S08 | The types an auto-rule may count, as its "Type of infractions committed", are warning points, p… |
| STW-ARL-048 | S08 | The types an auto-rule may hand out, as its "Penalty given", are warning points, penalty points… |
| STW-ARL-049 | S08 | An auto-rule judges one driver's licence, and neither counts nor hands out a penalty given to a… |
| STW-ARL-050 | S08 | A time penalty or disqualification an auto-rule hands out shall change the result of a race ses… |
| STW-ARL-051 | S08 | Where the auto-rule was triggered at the close of a round's cycle, that round's feature race. I… |
| STW-ARL-052 | S08 | Where it was triggered by a CoC investigation, the feature race of the next round the driver ta… |
| STW-ARL-053 | S08 | A championship points deduction or a championship disqualification an auto-rule hands out shall… |
| STW-ARL-054 | S08 | Where the auto-rule was triggered at the close of a round's cycle, in that round's division, ap… |
| STW-ARL-055 | S08 | Where it was triggered by a CoC investigation, in each division the driver races in, as the ver… |
| STW-ARL-056 | S08 | The type counted and the type handed out may be the same, each auto-rule being triggered at mos… |
| STW-ARL-057 | S08 | A penalty type that is disabled shall be offered neither to count nor to hand out. Discipline p… |
| STW-ARL-058 | S08 | Several auto-rules may be triggered after the same round, and one being triggered may trigger a… |
| STW-ARL-059 | S08 | <NEW COMMAND> A "steward auto-rule modify" command will be made available to league managers, w… |
| STW-ARL-060 | S08 | <NEW COMMAND> A "steward auto-rule remove" command will be made available to league managers, w… |
| STW-ARL-061 | S08 | <NEW COMMAND> A "steward auto-rule toggle" command will be made available to league managers, w… |
| STW-ARL-062 | S08 | A paused auto-rule is checked at no cycle close, and "steward auto-rule list" shall mark it as… |
| STW-ARL-063 | S08 | Resuming an auto-rule counts as adding it anew: every driver already over its threshold is held… |
| STW-ARL-064 | S08 | <NEW COMMAND> A "steward auto-rule list" command will be made available to league managers and… |
| STW-ARL-065 | S08 | The list is shown in full, over several messages where it does not fit in one. |
| STW-ARL-066 | S08 | For rules of non-multi-round-type, the "number of rounds" column shall be empty. |

## BKP — Backups

| Rule | Issue | The rule |
|---|---|---|
| STW-BKP-001 | S27 | <NEW COMMAND> A "steward backup report-toggle" command will be made available to league admins,… |
| STW-BKP-002 | S27 | This functionality shall be disabled by default. |
| STW-BKP-003 | S27 | It is a league admin's, the backups filling the disk of the machine the bot runs upon, which is… |
| STW-BKP-004 | S27 | The directory shall be "./tickets", the same as used by "steward backup conduct-toggle". |
| STW-BKP-005 | S27 | Channel content shall be formatted as a JSON file. Each message shall be saved as an individual… |
| STW-BKP-006 | S27 | When saving, a directory shall be created with the unique ID of the ticket, report or appeal, a… |
| STW-BKP-007 | S27 | Any attached videos and images will be downloaded and saved, with their original filenames, to… |
| STW-BKP-008 | S27 | Where a backup cannot be saved, the channel shall not be deleted, being then the only copy of t… |
| STW-BKP-009 | S27 | While free space on the disk holding the backups is below 1 GB, a warning shall be posted to th… |

## COC — Code of Conduct investigations

| Rule | Issue | The rule |
|---|---|---|
| STW-COC-001 | S26 | <NEW COMMAND> A "steward conduct toggle" command will be made available to league managers, whi… |
| STW-COC-002 | S26 | This functionality is toggled off by default, and any of the other commands in this section fai… |
| STW-COC-003 | S26 | <NEW COMMAND> A "steward conduct steward-start-toggle" command will be made available to league… |
| STW-COC-004 | S26 | By default, this is toggled off, and only the head steward and temporary head steward may initi… |
| STW-COC-005 | S26 | <NEW COMMAND> A "steward conduct discipline-point-expiry" command will be made available to lea… |
| STW-COC-006 | S26 | By default, this will be set to "never". |
| STW-COC-007 | S26 | <NEW COMMAND> A "steward conduct defence-period" command will be made available to league manag… |
| STW-COC-008 | S26 | By default, this value will be set to 24. |
| STW-COC-009 | S26 | Input value must be equal or greater than 1. |
| STW-COC-010 | S26 | <NEW COMMAND> A "steward conduct deliberation-period" command will be made available to league… |
| STW-COC-011 | S26 | By default, this value will be set to 24. |
| STW-COC-012 | S26 | Input value must be equal or greater than 1. |
| STW-COC-013 | S26 | <NEW COMMAND> A "steward conduct-outcome add" command will be made available to league managers… |
| STW-COC-014 | S26 | ID - Mandatory - Unique ID for the conduct outcome, among conduct outcomes. It may be the same… |
| STW-COC-015 | S26 | Brief - Mandatory - Unique short description of the conduct outcome. Maximum of 50 characters. |
| STW-COC-016 | S26 | Description - Optional - Long form description of the conduct outcome. Maximum of 250 character… |
| STW-COC-017 | S26 | Discipline points - Optional - Integer input only. Number of discipline points added to the dri… |
| STW-COC-018 | S26 | Qualifying bans - Optional - Integer input only. Number of qualifying bans added to the driver… |
| STW-COC-019 | S26 | Race bans - Optional - Integer input only. Number of race bans added to the driver licence of t… |
| STW-COC-020 | S26 | Season ban - Optional - Checkbox that, if ticked, means that the offending driver's licence wil… |
| STW-COC-021 | S26 | League ban - Optional - Checkbox that, if ticked, means that the offending driver licence will… |
| STW-COC-022 | S26 | Championship points deducted - Optional - Integer input only. Number of points taken from the u… |
| STW-COC-023 | S26 | Championship disqualification - Optional - Checkbox that, if ticked, means that the user is rem… |
| STW-COC-024 | S26 | Constructors' points deducted - Optional - Integer input only. Number of points taken from a te… |
| STW-COC-025 | S26 | Constructors' championship disqualification - Optional - Checkbox that, if ticked, means that a… |
| STW-COC-026 | S26 | Every penalty above is given to a user, save the constructors' points deduction and the constru… |
| STW-COC-027 | S26 | The list of conduct outcomes shall hold at most 25, NFA among them. The command shall be refuse… |
| STW-COC-028 | S26 | This command shall fail if the outcome has any disabled penalty types. |
| STW-COC-029 | S26 | At least one of the "Discipline points", "Championship points deducted", "Championship disquali… |
| STW-COC-030 | S26 | <NEW COMMAND> A "steward conduct-outcome modify" command will be made available to league manag… |
| STW-COC-031 | S26 | This command shall fail if the outcome has any disabled penalty types. |
| STW-COC-032 | S26 | At least one of the "Discipline points", "Championship points deducted", "Championship disquali… |
| STW-COC-033 | S26 | <NEW COMMAND> A "steward conduct-outcome remove" command will be made available to league manag… |
| STW-COC-034 | S26 | <NEW COMMAND> A "steward conduct-outcome toggle" command will be made available to league manag… |
| STW-COC-035 | S26 | A paused conduct outcome is offered upon no ballot. It stays in the list, "steward conduct-outc… |
| STW-COC-036 | S26 | NFA cannot be paused. |
| STW-COC-037 | S26 | A paused conduct outcome does not count towards the 24 the list may hold. Resuming one shall be… |
| STW-COC-038 | S26 | <NEW COMMAND> A "steward conduct-outcome list" command will be made available to league manager… |
| STW-COC-039 | S26 | The list is shown in full, over several messages where it does not fit in one. |
| STW-COC-040 | S26 | <brief> |
| STW-COC-041 | S26 | ID: <id> |
| STW-COC-042 | S26 | Rule description: <description, if not empty, otherwise this line is skipped> |
| STW-COC-043 | S26 | Associated outcome: <all penalties associated with the outcome, comma concatenated> |
| STW-COC-044 | S26 | By default, a permanent, unremovable, unmodifiable conduct outcome is added to the list, with t… |
| STW-COC-045 | S26 | ID - NFA (special reserved ID) |
| STW-COC-046 | S26 | Brief - No Further Action |
| STW-COC-047 | S26 | Description - Outcome which means there is no actionable disciplinary offence in the reported i… |
| STW-COC-048 | S26 | Discipline point - 0 |
| STW-COC-049 | S26 | Championship points deducted - 0 |
| STW-COC-050 | S26 | Championship disqualification - No |
| STW-COC-051 | S26 | Constructors' points deducted - 0 |
| STW-COC-052 | S26 | Constructors' championship disqualification - No |
| STW-COC-053 | S26 | Qualifying ban - 0 |
| STW-COC-054 | S26 | Race ban - 0 |
| STW-COC-055 | S26 | Season ban - No |
| STW-COC-056 | S26 | League ban - No |
| STW-COC-057 | S26 | <NEW COMMAND> A "steward backup conduct-toggle" command will be made available to league admins… |
| STW-COC-058 | S26 | This functionality shall be disabled by default. |
| STW-COC-059 | S26 | It is a league admin's, the backups filling the disk of the machine the bot runs upon, which is… |
| STW-COC-060 | S26 | The directory shall be "./tickets", the same as used by "steward backup report-toggle". |
| STW-COC-061 | S26 | Channel content shall be formatted as a JSON file. Each message shall be saved as an individual… |
| STW-COC-062 | S26 | When saving, a directory shall be created with the unique ID of the investigation, and the chan… |
| STW-COC-063 | S26 | Any attached videos and images will be downloaded and saved, with their original filenames, to… |

## SET — Changing settings during a season

| Rule | Issue | The rule |
|---|---|---|
| STW-SET-001 | S09 | Once a season's placements are confirmed, this module's settings may be changed at any time, sa… |
| STW-SET-002 | S09 | The type of season ban, set by "steward penalty season-ban-type", shall not be changed while a… |
| STW-SET-003 | S09 | A setting of the stewarding cycle — the periods of its stages, whether appeals are enabled, the… |
| STW-SET-004 | S09 | A change to "steward justification final-mode" or "steward justification fallback-mode" applies… |
| STW-SET-005 | S09 | A change to a setting of the Code of Conduct investigations applies to the investigations opene… |
| STW-SET-006 | S09 | Outcomes may be added while report or appeal deliberations are open. An outcome so added shall… |
| STW-SET-007 | S09 | An outcome may not be modified, removed or paused while any report or appeal deliberation is op… |
| STW-SET-008 | S09 | Conduct outcomes may be added while investigation deliberations are open. A conduct outcome so… |
| STW-SET-009 | S09 | A conduct outcome may not be modified, removed or paused while any investigation deliberation i… |
| STW-SET-010 | S09 | A penalty type that any outcome or conduct outcome uses may not be switched off while any delib… |
| STW-SET-011 | S09 | An auto-rule judges only what happens after it exists. When an auto-rule is added, modified or… |
| STW-SET-012 | S09 | A change to the expiry of warning points, of penalty points or of discipline points shall gover… |

## TKT — Tickets

| Rule | Issue | The rule |
|---|---|---|
| STW-TKT-001 | S10 | What this section sets out holds for every ticket, a report, an appeal and a CoC investigation… |
| STW-TKT-002 | S10 | Every act in a ticket, and every use of a command of this module, shall be written to the stewa… |
| STW-TKT-003 | S10 | A command, button or form of this module that fails, as the core specification's rule upon a fa… |
| STW-TKT-004 | S10 | Ballots are the exception while a deliberation is open: the log records only that a steward cas… |
| STW-TKT-005 | S10 | It is imperative that the stewarding team is seen as a unified front. |
| STW-TKT-006 | S10 | The effective head steward of a ticket is the face of the stewarding team to the users of that… |
| STW-TKT-007 | S10 | No message a driver or other user can see shall attribute a decision, a vote, a request or a ju… |
| STW-TKT-008 | S10 | No steward other than the effective head steward shall be identified or mentioned in any messag… |
| STW-TKT-009 | S10 | Every exchange between stewards alone while the users of a ticket can see its channel shall tak… |
| STW-TKT-010 | S10 | A request made by a driver is no exchange between stewards, and shall be answered in the ticket… |
| STW-TKT-011 | S10 | Deliberation, from which the users of the ticket are shut out, takes place in the ticket's chan… |
| STW-TKT-012 | S10 | However, as stated above, steward logs shall identify them when needed. |
| STW-TKT-013 | S10 | A ticket the stewarding team declines to judge, on procedural grounds or any other, is rejected… |
| STW-TKT-014 | S10 | If the effective head steward is one of the involved drivers, or has a conflict of interest as… |
| STW-TKT-015 | S10 | If the effective head steward does not assign anyone else by the time the ticket's deliberation… |
| STW-TKT-016 | S10 | A ticket is open to its parties during the defence submission of a report or a CoC investigatio… |
| STW-TKT-017 | S10 | Once this phase is entered, the following buttons will be posted on the channel after the heade… |
| STW-TKT-018 | S10 | Add driver - Can be used by members of the effective stewarding team for this ticket, by the on… |
| STW-TKT-019 | S10 | If pressed by the effective head steward, the justification is optional. When confirmed, the dr… |
| STW-TKT-020 | S10 | If pressed by a regular member of the effective stewarding team, the justification is mandatory… |
| STW-TKT-021 | S10 | If pressed by the one who initiated the ticket or an involved driver, the justification is mand… |
| STW-TKT-022 | S10 | When added, the driver will be given the same permissions as other involved drivers. |
| STW-TKT-023 | S10 | The command is rejected if the ticket already holds 25 involved drivers, if the driver named ho… |
| STW-TKT-024 | S10 | In a CoC investigation, which pertains to no division, the member named need hold no seat: any… |
| STW-TKT-025 | S10 | Remove driver - Can be used by members of the effective stewarding team for this ticket, by the… |
| STW-TKT-026 | S10 | If pressed by the effective head steward, the justification is optional. When confirmed, the dr… |
| STW-TKT-027 | S10 | If pressed by a regular member of the effective stewarding team, the justification is mandatory… |
| STW-TKT-028 | S10 | If pressed by the one who initiated the ticket or an involved driver, the justification is mand… |
| STW-TKT-029 | S10 | The command is rejected if the targeted user is the one who initiated the ticket, or if they ar… |
| STW-TKT-030 | S10 | Request exclusion - Can be used by any member of the effective stewarding team for this ticket.… |
| STW-TKT-031 | S10 | If the one requesting this exclusion is a regular member of the effective stewarding team, a fo… |
| STW-TKT-032 | S10 | If the one requesting this exclusion is the effective head steward, a form is opened so that a… |
| STW-TKT-033 | S10 | The effective head steward remains so until a member accepts. After a rejection, they may name… |
| STW-TKT-034 | S10 | Where no member has accepted by the time the deliberation phase is reached, the bot shall choos… |
| STW-TKT-035 | S10 | Once a member accepts, the former effective head steward is excluded from the ticket and leaves… |
| STW-TKT-036 | S10 | A member of the effective stewarding team who has cast a ballot shall be refused an exclusion.… |
| STW-TKT-037 | S10 | Mute - Can be used by any member of the effective stewarding team for this ticket. When pressed… |
| STW-TKT-038 | S10 | Until the deliberation phase, the command is rejected for any user who is neither the one who i… |
| STW-TKT-039 | S10 | During the deliberation phase, it applies to members of the effective stewarding team instead,… |
| STW-TKT-040 | S10 | Unmute - Can be used by any member of the effective stewarding team for this ticket. When press… |
| STW-TKT-041 | S10 | Until the deliberation phase, the command is rejected for any user who is neither the one who i… |
| STW-TKT-042 | S10 | During the deliberation phase, it applies to members of the effective stewarding team instead,… |
| STW-TKT-043 | S10 | The buttons above remain in the channel through the deliberation phase, save "Add driver" and "… |
| STW-TKT-044 | S10 | When a driver is added to a ticket, they will be considered an involved driver, and given the s… |
| STW-TKT-045 | S10 | When a driver is removed from a ticket, they will no longer be considered an involved driver, a… |
| STW-TKT-046 | S10 | When a driver is added to or removed from a ticket, the bot will edit the "header message" (con… |
| STW-TKT-047 | S10 | If a driver was added to or removed from a ticket as a result of a request, then the message wi… |
| STW-TKT-048 | S10 | During this phase, regular members of the effective stewarding team only have read permission f… |
| STW-TKT-049 | S10 | During this phase, the effective head steward shall have read/write and attach media permission… |
| STW-TKT-050 | S10 | During this phase, the user who initiated the ticket and all users marked as involved drivers s… |
| STW-TKT-051 | S10 | This phase cannot be terminated early. |

## DEL — Deliberation

| Rule | Issue | The rule |
|---|---|---|
| STW-DEL-001 | S11 | A team may be sanctioned upon a ticket only where it holds at least one full-time driver in the… |
| STW-DEL-002 | S11 | Each line of a ballot applies only its own part of an outcome. A driver's line offers the outco… |
| STW-DEL-003 | S11 | Once this phase is entered, the involved drivers lose all permission to read, write or attach m… |
| STW-DEL-004 | S11 | Once this phase is entered, the members of the effective stewarding team will gain the permissi… |
| STW-DEL-005 | S11 | Once this phase is entered, a single button titled "Vote" is posted by the bot. This button wil… |
| STW-DEL-006 | S11 | Steward's display name - Shown, and not to be changed. Display name of the steward whose ballot… |
| STW-DEL-007 | S11 | Ticket ID - Shown, and not to be changed. Unique ID of the report, appeal or investigation whic… |
| STW-DEL-008 | S11 | Outcome - Dropdown - Mandatory - Dropdown containing the outcomes the cycle offers, and NFA, di… |
| STW-DEL-009 | S11 | Infringement - String - Optional - String standing for the ID/number which was allegedly violat… |
| STW-DEL-010 | S11 | Team outcome - Dropdown - Dropdown containing the outcomes the cycle offers that carry a team p… |
| STW-DEL-011 | S11 | Justification - String - Mandatory - A free form text with a 1000 character limit for the stewa… |
| STW-DEL-012 | S11 | The ballot as it stands - every involved party's outcome, kept in view in whole while any one o… |
| STW-DEL-013 | S11 | "Cancel", "Remove vote" where the steward has already voted, and "Confirm". |
| STW-DEL-014 | S11 | A steward's ballot is only valid via "Confirm" if all mandatory fields are filled. |
| STW-DEL-015 | S11 | Once a steward's ballot is deemed valid, all data for the ballot will be recorded and persisted. |
| STW-DEL-016 | S11 | If a steward reopens their ballot after having voted, it will show their ballot as it was cast. |
| STW-DEL-017 | S11 | A ballot shall be counted whole. Two ballots are the same option only where they give every inv… |
| STW-DEL-018 | S11 | The bot shall show no steward the ballots of others, nor any count of them, before the delibera… |
| STW-DEL-019 | S11 | If the steward has chosen "Confirm" upon reopening their ballot, the previously persisted ballo… |
| STW-DEL-020 | S11 | If the steward has chosen "Cancel" upon reopening their ballot, no change is to occur to their… |
| STW-DEL-021 | S11 | If the steward has chosen "Remove vote" upon reopening their ballot, the previously persisted b… |
| STW-DEL-022 | S11 | At the end of the countdown period for this phase, all stewarding team members except for the e… |
| STW-DEL-023 | S11 | At the end of the countdown period for this phase, the ballots will be counted. For the purpose… |
| STW-DEL-024 | S11 | If any one option reaches plurality without a tie, the ultimate result of the ticket will be th… |
| STW-DEL-025 | S11 | If two or more options are tied, and the effective head steward's ballot is one of them, the ul… |
| STW-DEL-026 | S11 | If two or more options are tied, and the effective head steward's ballot is none of them (also… |
| STW-DEL-027 | S11 | For a report or an appeal, the bot shall post a message on the verdicts channel saying that the… |
| STW-DEL-028 | S11 | In the ticket's channel, the bot shall post a button per option tied as the most voted, each sh… |
| STW-DEL-029 | S11 | A timer counts down 1 hour from the moment the buttons are posted; if the effective head stewar… |
| STW-DEL-030 | S11 | A ballot's time is the moment it was last confirmed. An option reaches its final count at the l… |
| STW-DEL-031 | S11 | Once the ultimate result of a ticket is reached through any of the mediums above, the bot will: |
| STW-DEL-032 | S11 | Post a message informing the effective head steward of the decision reached (the outcome given… |
| STW-DEL-033 | S11 | Buttons usable only by the effective head steward of the ticket, one for accepting the default… |
| STW-DEL-034 | S11 | In a message different from the one above, the justifications of all ballots of the winning opt… |
| STW-DEL-035 | S11 | Start a timer counting down 1 hour from the moment the buttons are posted; if the effective hea… |
| STW-DEL-036 | S11 | Once the justification message is settled, either via effective head steward confirmation or by… |
| STW-DEL-037 | S11 | The format of the final output is set out under Verdict output. |

## CYC — Stewarding cycle

| Rule | Issue | The rule |
|---|---|---|
| STW-CYC-001 | S17 | By default, the bot shall post a "Ticket submission is currently closed for <X> division" in a… |
| STW-CYC-002 | S17 | If appeals are not enabled, once a report's verdict is posted in the appropriate channel by the… |
| STW-CYC-003 | S17 | If appeals are enabled, if a report is not appealed by the time the appeal submission phase end… |
| STW-CYC-004 | S17 | Once appeal verdicts are posted at the end of the appeal deliberation phase, the incident (appe… |
| STW-CYC-005 | S17 | A round whose stewarding cycle stands open cannot be cancelled. Where its division or its seaso… |
| STW-CYC-006 | S17 | A driver sacked, or leaving the server, while party to an open ticket shall remain an involved… |
| STW-CYC-007 | S17 | While the stewarding module is enabled, a round's stewarding cycle shall move the round through… |
| STW-CYC-008 | S17 | Report submission opens at the round's scheduled start, while the round still awaits its result… |
| STW-CYC-009 | S17 | The round is awaiting report verdicts once its results are posted, and until the verdicts of it… |
| STW-CYC-010 | S17 | It is then awaiting appeal verdicts until the verdicts of its appeals are posted, and becomes f… |
| STW-CYC-011 | S17 | Where no appeal is lodged, the round becomes final once appeal submission ends. |
| STW-CYC-012 | S17 | Where appeals are disabled, the round becomes final once the verdicts of its reports are posted… |
| STW-CYC-013 | S17 | Where a round, a division or a season is cancelled, the stewarding module shall say what it mea… |
| STW-CYC-014 | S17 | A round cancelled before its scheduled start has no stewarding cycle, report submission never h… |
| STW-CYC-015 | S17 | A round whose every session is submitted as cancelled shall have its stewarding cycle ended onc… |
| STW-CYC-016 | S17 | The bot shall post in each such ticket's channel that the round was cancelled and the ticket cl… |
| STW-CYC-017 | S17 | Tickets so closed shall have no effect upon any driver licence, and shall trigger no auto-rule. |
| STW-CYC-018 | S17 | Report deliberation shall run in its own time whether or not the round's results have been post… |
| STW-CYC-019 | S17 | The results and standings of the round shall be labelled as the round moves: |
| STW-CYC-020 | S17 | Reposted once the report verdicts are posted, they shall carry the Post-Race Penalty Results la… |
| STW-CYC-021 | S17 | Reposted once the appeal verdicts are posted, or once appeal submission ends with no appeal lod… |
| STW-CYC-022 | S12 | At the scheduled start datetime of a round for a given division, the bot shall delete the "defa… |
| STW-CYC-023 | S12 | Where more than one round of a division has report submission open at once, a separate "Report… |
| STW-CYC-024 | S12 | At the scheduled start datetime of a round for a given division, a countdown with the period of… |
| STW-CYC-025 | S12 | It shall be possible to members of the stewarding team to report an incident in the same manner… |
| STW-CYC-026 | S12 | Where the steward holds no seat in the division, full-time or reserve, the report is a steward'… |
| STW-CYC-027 | S12 | Where the steward holds a seat in the division, the report is a driver's report as any other. B… |
| STW-CYC-028 | S12 | When a user presses the "Report incident" button, a form shall appear, collecting the following: |
| STW-CYC-029 | S12 | Season - Shown, and not asked for. Derived from the current season's number. |
| STW-CYC-030 | S12 | Division - Shown, and not asked for. Derived from the division to which the ticket channel is a… |
| STW-CYC-031 | S12 | Round - Shown, and not asked for. Derived from the round whose report submission is open, as na… |
| STW-CYC-032 | S12 | Involved drivers - Optional - 0..n members - Other drivers directly or indirectly involved in t… |
| STW-CYC-033 | S12 | Session - Mandatory - Dropdown - Select which session the incident took place in. Options avail… |
| STW-CYC-034 | S12 | Lap - Mandatory where the session is a race, a sprint race or the feature race; not asked other… |
| STW-CYC-035 | S12 | Complaint - Mandatory - String - Full description of the incident as per the complainant's unde… |
| STW-CYC-036 | S12 | Evidence files - Optional - 0..5 media (image or video) - One or multiple images or video files… |
| STW-CYC-037 | S12 | Evidence files are attached as part of lodging the ticket, before it is filed. |
| STW-CYC-038 | S12 | Evidence links - Optional - 0..5 links - One or multiple images or video links that provide bas… |
| STW-CYC-039 | S12 | Between "evidence files" and "evidence links", there must be at least one file/link. Otherwise,… |
| STW-CYC-040 | S12 | The information shall be validated before the form is closed, so that users do not have to ente… |
| STW-CYC-041 | S12 | After valid submission, the report will be henceforth identified with a unique ID following the… |
| STW-CYC-042 | S12 | After valid submission, a channel bearing the report's unique ID as the title will be created,… |
| STW-CYC-043 | S12 | After valid submission, the ticket's state changes immediately to the defence submission stage,… |
| STW-CYC-044 | S12 | Defence submission for the ticket stays open until the period configured by "steward report def… |
| STW-CYC-045 | S12 | There is no limit upon the number of reports a driver may lodge in a round. |
| STW-CYC-046 | S12 | Where two or more reports of the same round concern one incident, the effective head steward of… |
| STW-CYC-047 | S12 | The report merged in shall keep its own unique ID, and close with a verdict of its own, posted… |
| STW-CYC-048 | S12 | Its complainant and involved drivers shall join the surviving ticket as involved drivers, and i… |
| STW-CYC-049 | S12 | The complainant may ask to withdraw their report during defence submission. The withdrawal shal… |
| STW-CYC-050 | S12 | A report withdrawn shall close with a verdict of No Further Action, posted with the round's rep… |
| STW-CYC-051 | S12 | A steward's report may be withdrawn by its effective head steward. |
| STW-CYC-052 | S12 | If there are no reports submitted until this phase ends, then the stewarding cycle closes then.… |
| STW-CYC-053 | S12 | A ticket enters this phase the moment it is lodged. Once the period of time configured for the… |
| STW-CYC-054 | S12 | The rules for a ticket open to its parties, set out under Tickets, hold throughout this phase. |
| STW-CYC-055 | S13 | Once this phase is entered, a countdown with the period of time configured by "steward report d… |
| STW-CYC-056 | S13 | Once this phase is entered, the bot shall post in the ticket's channel, for the effective head… |
| STW-CYC-057 | S13 | The ballot of a report is as set out under Tickets. Its outcomes are those currently configured… |
| STW-CYC-058 | S13 | Where a tie is left unbroken, the message on the verdicts channel reads "The report verdicts fo… |
| STW-CYC-059 | S13 | Only after the final output is determined for all reports pertaining to a given round of a give… |
| STW-CYC-060 | S13 | The channel will not be deleted upon the publishing of the report verdicts. A ticket's channel… |
| STW-CYC-061 | S13 | The report deliberation phase is only considered over once all reports pertaining to a given ro… |
| STW-CYC-062 | S13 | If appeals functionality is enabled, then the stewarding cycle will move on to that phase. |
| STW-CYC-063 | S13 | Otherwise, then the stewarding cycle is considered closed. |
| STW-CYC-064 | S13 | Once the report deliberation phase is considered over, the round results and the standings afte… |
| STW-CYC-065 | S14 | A report may be appealed only while its round's appeal submission is open. Once it closes, the… |
| STW-CYC-066 | S14 | Once this phase is entered, the bot shall delete the "default" message (written again once the… |
| STW-CYC-067 | S14 | Where more than one round of a division has appeal submission open at once, a separate "Appeal… |
| STW-CYC-068 | S14 | Once this phase is entered, a countdown with the period of time configured by "steward appeal s… |
| STW-CYC-069 | S14 | When a user presses the "Appeal incident" button, it will be verified if they meet the requirem… |
| STW-CYC-070 | S14 | Is part of the division of that report, whether or not they were involved in it: a driver may a… |
| STW-CYC-071 | S14 | Their current number of tokens is equal to or greater than the cost configured by "steward appe… |
| STW-CYC-072 | S14 | Where the user is a member of the stewarding team, the bot shall warn them before the appeal is… |
| STW-CYC-073 | S14 | Once it is determined that the user meets the requirements to perform an appeal, a form shall a… |
| STW-CYC-074 | S14 | Season - Shown, and not asked for. Derived from the current season's number. |
| STW-CYC-075 | S14 | Division - Shown, and not asked for. Derived from the division to which the ticket channel is a… |
| STW-CYC-076 | S14 | Round - Shown, and not asked for. Derived from the round whose appeal submission is open, as na… |
| STW-CYC-077 | S14 | Report ID - Mandatory - String - The unique ID of the report that the driver wishes to appeal,… |
| STW-CYC-078 | S14 | Auto-rule verdicts' or other appeals' IDs are not accepted. A steward's report may be appealed… |
| STW-CYC-079 | S14 | A report may be appealed once. Where an appeal of it has already been lodged, the appeal shall… |
| STW-CYC-080 | S14 | The driver who lodges an appeal is an involved driver of it, whether or not they were involved… |
| STW-CYC-081 | S14 | Justification - Mandatory - String - Reason for the appeal, outlining the reason as to why the… |
| STW-CYC-082 | S14 | Evidence files - Optional - 0..5 media (image or video) - One or multiple images or video files… |
| STW-CYC-083 | S14 | Evidence files are attached as part of lodging the ticket, before it is filed. |
| STW-CYC-084 | S14 | Evidence links - Optional - 0..5 links - One or multiple images or video links that provide bas… |
| STW-CYC-085 | S14 | Between "evidence files" and "evidence links", there must be at least one file/link. Otherwise,… |
| STW-CYC-086 | S14 | The information shall be validated before the form is closed, so that users do not have to ente… |
| STW-CYC-087 | S14 | After valid submission, the appeal will be henceforth identified with a unique ID following the… |
| STW-CYC-088 | S14 | After valid submission, a channel bearing the appeal's unique ID as the title will be created,… |
| STW-CYC-089 | S14 | As this is considered a different ticket, the effective stewarding team for this ticket may not… |
| STW-CYC-090 | S14 | The rules for a ticket open to its parties, set out under Tickets, hold throughout this phase. |
| STW-CYC-091 | S14 | If no appeal is lodged by the end of appeal submission, the round's stewarding cycle closes the… |
| STW-CYC-092 | S15 | Once this phase is entered, a countdown with the period of time configured by "steward appeal d… |
| STW-CYC-093 | S15 | Once this phase is entered, the bot shall post in the ticket's channel, for the effective head… |
| STW-CYC-094 | S15 | The ballot of an appeal is as set out under Tickets, save that: |
| STW-CYC-095 | S15 | It shows the Appeal ID. |
| STW-CYC-096 | S15 | It carries first a Decision - Dropdown - Mandatory - A dropdown consisting of two options, "Uph… |
| STW-CYC-097 | S15 | Its line for each involved driver is greyed out unless the decision is "Change initial verdict"… |
| STW-CYC-098 | S15 | Its line for each team that may be sanctioned, the appeal's involved drivers being counted, is… |
| STW-CYC-099 | S15 | Where a tie is left unbroken, the message on the verdicts channel reads "The appeal verdicts fo… |
| STW-CYC-100 | S15 | A ballot with the decision "Change initial verdict" is only valid where it gives at least one i… |
| STW-CYC-101 | S15 | A ballot with the decision "Uphold initial verdict" is the option of the initial verdict, and i… |
| STW-CYC-102 | S15 | Only after the final output is determined for all appeals pertaining to a given round of a give… |
| STW-CYC-103 | S15 | Once the stewarding cycle of a round closes, the channels of all its tickets, reports and appea… |
| STW-CYC-104 | S15 | Where appeals are disabled, or no appeal is lodged, the cycle closes earlier, and its channels… |
| STW-CYC-105 | S15 | The appeal deliberation phase is only considered over once all appeals pertaining to a given ro… |
| STW-CYC-106 | S15 | Once the appeal deliberation phase is considered over, the round results and the standings afte… |
| STW-CYC-107 | S15 | If a verdict was changed in an appeal in comparison to the original report, the time penalty va… |
| STW-CYC-108 | S16 | The close of a cycle shall be applied entire or not at all. Before anything is written to any d… |
| STW-CYC-109 | S16 | Where any of them cannot be posted to, nothing shall be written, and the cycle shall wait at it… |
| STW-CYC-110 | S16 | Once the channel is repaired, the close shall go ahead. |
| STW-CYC-111 | S16 | The close shall not be recorded as done before the postings it claims have been made. |
| STW-CYC-112 | S16 | Once all tickets for a given round of a given division reach this stage, or at once where the r… |
| STW-CYC-113 | S16 | Auto-rule handling is set out under Auto-rule triggering. |
| STW-CYC-114 | S16 | Once auto-rules are verified, the previous licence sheet shall be deleted, and an updated one,… |
| STW-CYC-115 | S16 | Licence sheet posting is set out under Licence sheet output. |
| STW-CYC-116 | S18 | <NEW COMMAND> A "steward verdict republish" command will be made available to the head steward… |
| STW-CYC-117 | S18 | If there was an actual modification to the justification, and if this field is not empty/whites… |
| STW-CYC-118 | S18 | A verdict may be republished only until the next batch of verdicts lands upon it, so that the v… |
| STW-CYC-119 | S18 | A report's verdict, until the round's appeal verdicts are posted; or, where appeals are disable… |
| STW-CYC-120 | S18 | An appeal's verdict, until the division's next round begins; or, for the final round of a seaso… |
| STW-CYC-121 | S18 | A Code of Conduct Verdict, until the next Code of Conduct Verdict is posted. |
| STW-CYC-122 | S18 | An Automated Ruling carries no justification of the stewards' and shall not be republished. |
| STW-CYC-123 | S18 | There is no command to correct the outcome of a verdict. Drivers are judged by a collective of… |

## CCY — Conduct cycle

| Rule | Issue | The rule |
|---|---|---|
| STW-CCY-001 | S28 | Once an investigation's verdict is posted in the appropriate channel by the end of the investig… |
| STW-CCY-002 | S28 | <NEW COMMAND> A "steward conduct start" command will be made available to the head steward and… |
| STW-CCY-003 | S28 | When valid usage, a form shall be shown, collecting the following: |
| STW-CCY-004 | S28 | Involved users - Optional - 0..n mentions - Further users to be investigated alongside those th… |
| STW-CCY-005 | S28 | Complaint - Mandatory - String - Full description of the event being investigated by the stewar… |
| STW-CCY-006 | S28 | Evidence files - Optional - 0..5 media (image or video) - One or multiple images or video files… |
| STW-CCY-007 | S28 | Evidence files are attached as part of lodging the ticket, before it is filed. |
| STW-CCY-008 | S28 | Evidence links - Optional - 0..5 links - One or multiple images or video links that provide bas… |
| STW-CCY-009 | S28 | Between "evidence files" and "evidence links", there must be at least one file/link. Otherwise,… |
| STW-CCY-010 | S28 | Usage of the "steward conduct start" shall be valid at all times of a season's, a division's, o… |
| STW-CCY-011 | S28 | The information shall be validated before the form is closed, so that users do not have to ente… |
| STW-CCY-012 | S28 | After valid submission, the investigation will be henceforth identified with a unique ID follow… |
| STW-CCY-013 | S28 | After valid submission, a channel bearing the investigation's unique ID as the title will be cr… |
| STW-CCY-014 | S28 | Upon valid submission, a CoC investigation ticket will move immediately onto the defence submis… |
| STW-CCY-015 | S28 | Once the investigation information is validated and the channel opened with the relevant inform… |
| STW-CCY-016 | S28 | The rules for a ticket open to its parties, set out under Tickets, hold throughout this phase,… |
| STW-CCY-017 | S28 | Once this phase is entered, a countdown with the period of time configured by "steward conduct… |
| STW-CCY-018 | S28 | The ballot of an investigation is as set out under Tickets, save that it shows the Investigatio… |
| STW-CCY-019 | S28 | Only after the final output is determined for the investigation, it will be posted immediately… |
| STW-CCY-020 | S28 | Where a season is ongoing and the user holds a seat in any of its divisions, full-time or reser… |
| STW-CCY-021 | S28 | After the verdict is posted successfully, the conduct cycle will be considered closed. |
| STW-CCY-022 | S28 | Once its verdict is posted, the investigation's channel shall be removed. Where "steward backup… |
| STW-CCY-023 | S28 | Once the investigation's verdict is posted, the outcomes it gives are made effective: each invo… |
| STW-CCY-024 | S28 | A championship points deduction or championship disqualification given to a user who races in n… |
| STW-CCY-025 | S28 | A team penalty given once its season is no longer ongoing shall have no effect, and the verdict… |
| STW-CCY-026 | S28 | The licence sheets touched are posted anew as set out under Licence sheet output, and the stand… |

## REV — Revoking penalties

| Rule | Issue | The rule |
|---|---|---|
| STW-REV-001 | S29 | Revoke commands are a last-ditch measure, and should not be used unless in a pinch. |
| STW-REV-002 | S29 | Every revoke command shall take, as a mandatory input, whether the sanction is annulled or lift… |
| STW-REV-003 | S29 | Annulled - the sanction should never have been given. It shall leave the licence entirely, its… |
| STW-REV-038 | S29 | The bot shall keep the sanction marked annulled, so that a verdict already posted can still be… |
| STW-REV-004 | S29 | Lifted - the sanction was rightly given, and is ended early. It shall stay upon the licence and… |
| STW-REV-005 | S29 | Only an active sanction may be annulled or lifted. |
| STW-REV-006 | S29 | <NEW COMMAND> A "steward revoke warning-point" command will be made available to the head stewa… |
| STW-REV-007 | S29 | This command fails if warning points are disabled, or if the user holds fewer active warning po… |
| STW-REV-008 | S29 | The warning points revoked will be the active ones received most recently. |
| STW-REV-009 | S29 | <NEW COMMAND> A "steward revoke penalty-point" command will be made available to the head stewa… |
| STW-REV-010 | S29 | This command fails if penalty points are disabled, or if the user holds fewer active penalty po… |
| STW-REV-011 | S29 | The penalty points revoked will be the active ones received most recently. |
| STW-REV-012 | S29 | <NEW COMMAND> A "steward revoke discipline-point" command will be made available to the head st… |
| STW-REV-013 | S29 | This command fails if the conduct cycle is disabled, or if the user holds fewer active discipli… |
| STW-REV-014 | S29 | The discipline points revoked will be the active ones received most recently. |
| STW-REV-015 | S29 | <NEW COMMAND> A "steward revoke quali-ban" command will be made available to the head steward (… |
| STW-REV-016 | S29 | This command fails if qualifying bans are disabled, or if the user holds no active qualifying b… |
| STW-REV-017 | S29 | It revokes one active qualifying ban, the one received most recently. |
| STW-REV-018 | S29 | <NEW COMMAND> A "steward revoke race-ban" command will be made available to the head steward (o… |
| STW-REV-019 | S29 | This command fails if race bans are disabled, or if the user holds no active race ban. |
| STW-REV-020 | S29 | It revokes one active race ban, the one received most recently. |
| STW-REV-021 | S29 | <NEW COMMAND> A "steward revoke season-ban" command will be made available to the head steward… |
| STW-REV-022 | S29 | This command fails if season bans are disabled, or if the user holds no active season ban. |
| STW-REV-023 | S29 | Where season bans are stacked, only the active one is removed, and the one stacked behind it be… |
| STW-REV-024 | S29 | Upon removal of the season ban, the season ban role will be removed from them, unless a stacked… |
| STW-REV-025 | S29 | <NEW COMMAND> A "steward revoke league-ban" command will be made available to the head steward… |
| STW-REV-026 | S29 | This command fails if league bans are disabled, or if the user holds no active league ban. |
| STW-REV-027 | S29 | Upon removal of the league ban, the league ban role will be removed from their current account… |
| STW-REV-028 | S29 | A season ban the league ban replaced shall not be restored. |
| STW-REV-029 | S29 | <NEW COMMAND> A "steward revoke championship-penalty" command will be made available to the hea… |
| STW-REV-030 | S29 | This command fails if the driver holds no such penalty, or if that penalty type is disabled. |
| STW-REV-031 | S29 | A penalty held upon the licence, not yet applied to any championship, shall be named by the tic… |
| STW-REV-032 | S29 | Upon revocation, the division's drivers' standings shall be calculated and posted anew, whether… |
| STW-REV-033 | S29 | <NEW COMMAND> A "steward revoke team-penalty" command will be made available to the head stewar… |
| STW-REV-034 | S29 | This command fails if the team holds no such penalty, or if that penalty type is disabled. |
| STW-REV-035 | S29 | Upon revocation, the division's constructors' standings shall be calculated and posted anew, wh… |
| STW-REV-036 | S29 | While the stewarding module is disabled, "steward revoke season-ban" and "steward revoke league… |
| STW-REV-037 | S29 | Lifting a season or league ban while the module is disabled is left entirely to the league: no… |

## ART — Auto-rule triggering

| Rule | Issue | The rule |
|---|---|---|
| STW-ART-001 | S19 | If any auto-rule configured is infringed upon, then an additional automated verdict document wi… |
| STW-ART-002 | S19 | Its format is set out under Verdict output. |
| STW-ART-003 | S19 | For output purposes, two different formats may be used, depending on what was the trigger of th… |
| STW-ART-004 | S19 | If the trigger was the close of a round's cycle, the "S<x>_D<y>_R<z>_AR<w>" format will be foll… |
| STW-ART-005 | S19 | If the trigger was a CoC investigation, the "COC_INV<x>_AR<y>" format will be followed, where <… |
| STW-ART-006 | S19 | It is possible that an auto-rule triggers another auto-rule. The auto-rules shall be checked ag… |
| STW-ART-007 | S19 | As a way to prevent drivers from being penalised twice for going over a threshold (e.g. an auto… |
| STW-ART-008 | S19 | Single round - It does not flip-flop. It is judged afresh for each round, and triggered for eac… |
| STW-ART-009 | S19 | Multi round - It flip-flops upon the count within its window of rounds: once triggered, it can… |
| STW-ART-010 | S19 | Active accumulation - It flip-flops upon the tally of active penalties, as above. |
| STW-ART-011 | S19 | Historical accumulation - The lifetime tally only rises, so it is triggered each time the tally… |

## BAN — Bans

| Rule | Issue | The rule |
|---|---|---|
| STW-BAN-001 | S20 | A driver whose licence records any sanction, active or not, a team penalty recorded through the… |
| STW-BAN-002 | S21 | Whether a qualifying ban or a race ban was served in a round shall be judged from the round's r… |
| STW-BAN-003 | S21 | A race ban is judged in every round in which it is to be served, and a ruling posted whether it… |
| STW-BAN-004 | S21 | A qualifying ban is judged, and a ruling posted, only where the driver took part in the round.… |
| STW-BAN-005 | S21 | Where the round's results are resubmitted before the round is final, the judgement shall be mad… |
| STW-BAN-006 | S21 | It is the one change a round makes to a licence before its stewarding cycle closes, the verdict… |
| STW-BAN-007 | S20 | A driver who gets a season ban or a league ban while party to open tickets shall remain an invo… |
| STW-BAN-008 | S20 | A driver so banned is no longer of any division, and may lodge no further appeal. Their bans be… |
| STW-BAN-009 | S25 | Where a ban frees a driver's seat for a round whose reserves have already been distributed, and… |
| STW-BAN-010 | S22 | Season bans and league bans on a driver licence shall remain in force while the stewarding modu… |
| STW-BAN-011 | S22 | No season or league ban shall expire while the module is disabled. It shall be lifted only by a… |
| STW-BAN-012 | S22 | A season or league ban shall be held while the module is disabled, as qualifying and race bans… |
| STW-BAN-013 | S22 | A season ban expiring upon a season's end whose season ended while the module was disabled shal… |
| STW-BAN-014 | S22 | Qualifying bans and race bans shall be held while the module is disabled: they shall be neither… |
| STW-BAN-015 | S22 | Warning points, penalty points and discipline points shall be held while the module is disabled… |
| STW-BAN-016 | S21 | Whether a driver has a qualifying ban is only determined at the close of a stewarding cycle or… |
| STW-BAN-017 | S21 | A qualifying ban shall be served in the driver's principal division, whatever division the offe… |
| STW-BAN-018 | S21 | A driver with no principal division, being a reserve driver alone, shall serve it at the next u… |
| STW-BAN-019 | S25 | If the attendance module is enabled, a driver with a qualifying ban to be served in a given div… |
| STW-BAN-020 | S25 | A full-time driver shall be told when the check-in call for the round is posted. A reserve driv… |
| STW-BAN-021 | S25 | Every such driver shall be reminded at the check-in deadline, alongside the message distributin… |
| STW-BAN-022 | S25 | These messages must be deleted alongside the check-in call for the round. |
| STW-BAN-023 | S21 | If the attendance module is disabled, no such notice is posted. The outstanding ban is shown on… |
| STW-BAN-024 | S21 | The qualifying ban will be considered "served" if the driver set no lap time in any qualifying… |
| STW-BAN-025 | S21 | Additionally, a qualifying ban being correctly served will trigger the immediate posting of a v… |
| STW-BAN-026 | S21 | If a driver who has a qualifying ban does not take part in a round (missing in the results of a… |
| STW-BAN-027 | S21 | If a driver who has a qualifying ban fails to serve it properly by setting a lap time in any qu… |
| STW-BAN-028 | S21 | Additionally, the failure to serve a qualifying ban properly will trigger the immediate posting… |
| STW-BAN-029 | S21 | As qualifying bans are assigned to a driver licence, they do not expire upon a season's end. A… |
| STW-BAN-030 | S21 | In that later season, it shall be served as any ban is: in the driver's principal division in t… |
| STW-BAN-031 | S21 | A driver who takes part in no later season shall keep the qualifying ban pending on their licen… |
| STW-BAN-032 | S21 | Whether a driver has a race ban is only determined at the close of a stewarding cycle or a cond… |
| STW-BAN-033 | S21 | A race ban shall be served in the driver's principal division, whatever division the offences b… |
| STW-BAN-034 | S21 | A driver with no principal division, being a reserve driver alone, shall serve it at the next u… |
| STW-BAN-035 | S21 | A race ban bars the driver from that one round of that one division. They may race in another d… |
| STW-BAN-036 | S25 | If the attendance module is enabled and a driver has a race ban to be served at a round, any an… |
| STW-BAN-037 | S25 | The driver shall be unable to answer that check-in call again. |
| STW-BAN-038 | S25 | They shall not be given attendance points for not answering it: the bot shall give them an auto… |
| STW-BAN-039 | S25 | The notice shall be deleted alongside the check-in call it concerns, and posted again where tha… |
| STW-BAN-040 | S21 | The race ban will be considered "served" if the driver is not listed in the results of any sess… |
| STW-BAN-041 | S21 | Additionally, a race ban being correctly served will trigger the immediate posting of a verdict… |
| STW-BAN-042 | S21 | If a driver who has a race ban fails to serve it properly by being listed in the results of any… |
| STW-BAN-043 | S21 | Additionally, the failure to serve a race ban properly will trigger the immediate posting of a… |
| STW-BAN-044 | S21 | As race bans are assigned to a driver licence, they do not expire upon a season's end. A race b… |
| STW-BAN-045 | S21 | In that later season, it shall be served as any ban is: in the driver's principal division in t… |
| STW-BAN-046 | S21 | A driver who takes part in no later season shall keep the race ban pending on their licence. |
| STW-BAN-047 | S20 | Whether a driver has a season ban is only determined at the close of a stewarding cycle or a co… |
| STW-BAN-048 | S20 | A driver who gets a season ban shall be returned to Not Signed Up at once, as a sacking returns… |
| STW-BAN-049 | S20 | If the attendance module is enabled and any driver has a season ban enforced unto them, their v… |
| STW-BAN-050 | S20 | If the sign-up module is enabled and any driver has a season ban enforced unto them, any existi… |
| STW-BAN-051 | S20 | If the sign-up module is enabled and any driver has a season ban enforced unto them, they will… |
| STW-BAN-052 | S20 | If any driver has a season ban, regardless of sign-up module status, they will be unable to be… |
| STW-BAN-053 | S20 | Season bans will be considered served after the criteria configured in "steward penalty season-… |
| STW-BAN-054 | S20 | Season bans stack. A season ban received while one is active shall begin when the one before it… |
| STW-BAN-055 | S20 | A season ban received while the driver is league banned shall be added to their ban history, an… |
| STW-BAN-056 | S20 | A driver cannot be season banned and league banned at once, the effect of the two being the sam… |
| STW-BAN-057 | S20 | Revoking that league ban shall not restore the season bans it replaced. Timing a revocation is… |
| STW-BAN-058 | S20 | A league ban bars the driver, and so every Discord account they own, the licence being the driv… |
| STW-BAN-059 | S20 | The bot cannot know an account that has never been attached to the driver. Where a banned drive… |
| STW-BAN-060 | S20 | Whether a driver has a league ban is only determined at the close of a stewarding cycle or a co… |
| STW-BAN-061 | S20 | A driver who gets a league ban shall be returned to Not Signed Up at once, as a sacking returns… |
| STW-BAN-062 | S20 | If the attendance module is enabled and any driver has a league ban enforced unto them, their v… |
| STW-BAN-063 | S20 | If the sign-up module is enabled and any driver has a league ban enforced unto them, any existi… |
| STW-BAN-064 | S20 | If the sign-up module is enabled and any driver has a league ban enforced unto them, they will… |
| STW-BAN-065 | S20 | If any driver has a league ban, regardless of sign-up module status, they will be unable to be… |

## LIC — Viewing a licence

| Rule | Issue | The rule |
|---|---|---|
| STW-LIC-001 | S23 | A driver licence is a public record of the league. |
| STW-LIC-002 | S23 | While the stewarding module is enabled, the panel of the hub channel the core specification set… |
| STW-LIC-003 | S23 | A licence so shown shall have a textual output and an image output of its own, as the licence s… |
| STW-LIC-004 | S23 | A licence belongs to no single division. Where the driver has a principal division, it dresses… |
| STW-LIC-005 | S23 | The textual output of a licence shall have the following data, in this order: |
| STW-LIC-006 | S23 | The driver's name and nationality. |
| STW-LIC-007 | S23 | Current - every division the driver currently races in, each with their team and whether they a… |
| STW-LIC-008 | S23 | Points - for each of warning points, penalty points and discipline points, the number active, a… |
| STW-LIC-009 | S23 | Bans - for each of qualifying bans, race bans, season bans and league bans, the number active,… |
| STW-LIC-010 | S23 | Record - the number of bans of each kind received across the driver's time in the league, the c… |
| STW-LIC-011 | S23 | The ID of the ticket or ruling each active point or ban came from. |
| STW-LIC-012 | S24 | The image output of a licence shall be consistent with the visual outputs the image module alre… |
| STW-LIC-013 | S24 | Driver name - Mandatory |
| STW-LIC-014 | S24 | Driver nationality flag - Optional |
| STW-LIC-015 | S24 | Driver portrait - Optional - drawn where the league's portrait settings provide one |
| STW-LIC-016 | S24 | Principal division logo and colours - Optional - the logo of the principal division, and its pe… |
| STW-LIC-017 | S24 | Current - Mandatory - a row per division the driver currently races in: the division's name, in… |
| STW-LIC-018 | S24 | Points - Mandatory - a row per type of point: the number active, and the expiries, soonest first |
| STW-LIC-019 | S24 | Bans - Mandatory - a row per type of ban: the number active, and where each is to be served, or… |
| STW-LIC-020 | S24 | Record - Mandatory - the bans received across the driver's time in the league, the championship… |

## VER — Verdict output

| Rule | Issue | The rule |
|---|---|---|
| STW-VER-001 | S30 | While the stewarding module is enabled, the results & standings module's penalty and appeal rev… |
| STW-VER-002 | S30 | While the stewarding module is disabled, the results & standings module issues its verdicts as… |
| STW-VER-003 | S30 | Where the image module specification describes the verdict graphic or the verdict banner otherw… |
| STW-VER-004 | S30 | This section specifies how every verdict this module issues must be shaped and formatted: Repor… |
| STW-VER-005 | S30 | Every verdict shall carry a label naming its kind, in its textual and its image output alike: |
| STW-VER-006 | S30 | Report Verdict - the verdict of a report, in place of the Post-Race Penalty label the results &… |
| STW-VER-007 | S30 | Appeal Verdict - the verdict of an appeal, in place of the Appeal label the results & standings… |
| STW-VER-008 | S30 | Code of Conduct Verdict - the verdict of a CoC investigation. |
| STW-VER-009 | S30 | Automated Ruling - a verdict the bot issues itself: an auto-rule triggered, and a qualifying ba… |
| STW-VER-010 | S30 | Attendance Sanction - issued by the attendance module, and unchanged by this one. |
| STW-VER-011 | S30 | Every verdict shall show the round it pertains to, or the round that gave rise to it, by its se… |
| STW-VER-012 | S30 | A Report Verdict and an Appeal Verdict shall show as well the session and lap of the incident. |
| STW-VER-013 | S30 | An Automated Ruling shall show the round whose results or whose cycle close gave rise to it. |
| STW-VER-014 | S30 | A Code of Conduct Verdict pertains to no division and to no round, and shall show the season al… |
| STW-VER-015 | S30 | In the textual output the lines it has no value for shall be left out. In the image output the… |
| STW-VER-016 | S30 | A verdict posted again as a repost shall be identical to the original, save the indication "(re… |
| STW-VER-017 | S30 | A verdict shall notify only the involved drivers its decision names. A role mention, "@everyone… |
| STW-VER-018 | S31 | The image output of a verdict may carry detail its textual output does not, but shall omit noth… |
| STW-VER-019 | S30 | An Appeal Verdict shall show, for each involved driver whose outcome the appeal changed, the ch… |
| STW-VER-020 | S30 | In each row of its decision, an Appeal Verdict shall carry these changes, in its textual and it… |
| STW-VER-021 | S30 | Verdicts shall be posted sequentially and in alphabetic order of their unique ID, among those o… |
| STW-VER-022 | S30 | Attendance sanctions are the attendance module's own, carry no such ID, and are not ordered by… |
| STW-VER-023 | S30 | This means that "S1_D1_R1_001", "S1_D1_R1_002", "S1_D1_R1_003", ... will be the correct order. |
| STW-VER-024 | S30 | For the purpose of appeals, the ID of the report to which they correspond will be taken and suf… |
| STW-VER-025 | S30 | If they pertain to a report, appeal, or auto-rule triggered by either (including auto-rules whi… |
| STW-VER-026 | S30 | Every batch of verdicts posted together for a round shall be headed by a header naming the roun… |
| STW-VER-027 | S30 | Where the verdict banner aspect of the image module is on, the header is the verdict banner. Wh… |
| STW-VER-028 | S30 | A Code of Conduct Verdict pertains to no round and is headed by none. |
| STW-VER-029 | S30 | If they pertain to a Code of Conduct investigation, or auto-rule triggered by a CoC investigati… |
| STW-VER-030 | S30 | An Automated Ruling is issued by the bot, with no complaint and no stewards' justification. It… |
| STW-VER-031 | S30 | Decision - a single row, for the driver it concerns: for an auto-rule, what the rule handed out… |
| STW-VER-032 | S30 | Incident - in its place, a description the bot writes. For an auto-rule, the rule's ID, its inf… |
| STW-VER-033 | S30 | Justification given - for an auto-rule, "As per the rules of this league", naming the rule's in… |
| STW-VER-034 | S30 | The textual output of verdicts shall have the following data, all on the same message: |
| STW-VER-035 | S30 | Label of the verdict's kind |
| STW-VER-036 | S30 | Unique ID of the report/appeal/CoC investigation |
| STW-VER-037 | S30 | Season, division, round number |
| STW-VER-038 | S30 | Grand prix name, session name, lap (if session is a race session) |
| STW-VER-039 | S30 | Decision - one line per involved driver, as a mention: their outcome, in plain language, severa… |
| STW-VER-040 | S30 | Incident - the incident as the effective head steward settled it, for a report; for an appeal,… |
| STW-VER-041 | S30 | Justification given for outcome |
| STW-VER-042 | S31 | The image output of verdicts shall be consistent with the visual outputs the image module alrea… |
| STW-VER-043 | S31 | Label of the verdict's kind - Mandatory |
| STW-VER-044 | S31 | Unique ID of the report/appeal/CoC investigation - Mandatory |
| STW-VER-045 | S31 | Season number - Optional |
| STW-VER-046 | S31 | Division name - Mandatory |
| STW-VER-047 | S31 | Round number - Mandatory |
| STW-VER-048 | S31 | Grand prix name - Optional |
| STW-VER-049 | S31 | Grand prix flag - Optional - drawn as the verdict banner draws the flag of the round |
| STW-VER-050 | S31 | Session name - Mandatory |
| STW-VER-051 | S31 | Lap (if session is a race session) - Optional |
| STW-VER-052 | S31 | Decision - Mandatory - one row per involved driver: their server display name, with their natio… |
| STW-VER-053 | S31 | Incident - Mandatory - as in the textual output, placed where the image module places a verdict… |
| STW-VER-054 | S31 | Justification given for outcome - Mandatory |

## STD — Standings output

| Rule | Issue | The rule |
|---|---|---|
| STW-STD-001 | S32 | The standings of the results & standings module shall show the championship penalties this modu… |
| STW-STD-002 | S32 | A points deduction lowers the total of the driver or team it is given to, and shall be marked:… |
| STW-STD-003 | S32 | A driver or team disqualified from the championship shall be shown in the standings with "DSQ"… |
| STW-STD-004 | S32 | A championship penalty takes effect with the verdict that gives it, as time penalties and disqu… |
| STW-STD-005 | S32 | Where a Code of Conduct Verdict gives a championship penalty or a team penalty, the standings i… |

## SHT — Licence sheet output

| Rule | Issue | The rule |
|---|---|---|
| STW-SHT-001 | S33 | The licence sheet for a given division shall be posted in the channel configured by "division l… |
| STW-SHT-002 | S33 | Whenever a driver licence changes, whatever caused the change — a cycle close, a Code of Conduc… |
| STW-SHT-003 | S33 | A qualifying ban or race ban outstanding shall appear upon the sheet of the division in which i… |
| STW-SHT-004 | S33 | Upon the season's placements being first confirmed, an opening licence sheet shall be posted in… |
| STW-SHT-005 | S33 | The licence sheet shall contain obligatorily all full-time and reserve drivers assigned to that… |
| STW-SHT-006 | S33 | The drivers will be listed ordered according to the following list of criteria in descending or… |
| STW-SHT-007 | S33 | Number of active penalty points, highest first; |
| STW-SHT-008 | S33 | Number of active warning points, highest first; |
| STW-SHT-009 | S33 | Team name alphabetical order; |
| STW-SHT-010 | S33 | Display name alphabetical order. |
| STW-SHT-011 | S33 | There will be two main parts to the licence sheet: current points status and outstanding bans t… |
| STW-SHT-012 | S33 | The current points status will consist of a list of all drivers, with their corresponding curre… |
| STW-SHT-013 | S33 | The order will be as specified above. |
| STW-SHT-014 | S33 | The outstanding bans list will consist of a list of all drivers with active qualifying bans or… |
| STW-SHT-015 | S33 | The textual output shall end with a list of the drivers suspended from racing: those season ban… |
| STW-SHT-016 | S34 | The licence sheet shall be an aspect of the image module, following the pattern every other asp… |
| STW-SHT-017 | S34 | <COMMAND CHANGE> A "licence" value shall be added to the "images config toggle" command, switch… |
| STW-SHT-018 | S34 | <NEW COMMAND> An "images template licence" command shall take in a string standing for the file… |
| STW-SHT-019 | S34 | While the aspect is off, or where generating the image fails, the licence sheet shall be posted… |
| STW-SHT-020 | S34 | The image output will be similar to that of the attendance sheet, and consistent with the visua… |
| STW-SHT-021 | S34 | Season, division - Mandatory |
| STW-SHT-022 | S34 | Occasion - Mandatory - The occasion the sheet stands at, as text: "After Round <n>" naming the… |
| STW-SHT-023 | S34 | Grand Prix name - Optional - The round the occasion names, and left out upon an opening sheet |
| STW-SHT-024 | S34 | Type of post - Mandatory, always "Licence Information", can be hardcoded |
| STW-SHT-025 | S34 | Table-list of drivers - Mandatory, with the following columns: |
| STW-SHT-026 | S34 | Driver display name - Mandatory |
| STW-SHT-027 | S34 | Driver nationality flag - Optional |
| STW-SHT-028 | S34 | Team name - Optional |
| STW-SHT-029 | S34 | Team logo - Optional |
| STW-SHT-030 | S34 | Active penalty points - Mandatory if penalty points are enabled |
| STW-SHT-031 | S34 | Active warning points - Mandatory if warning points are enabled |
| STW-SHT-032 | S34 | Active penalty points carried from earlier seasons - Mandatory if penalty points are enabled |
| STW-SHT-033 | S34 | Active warning points carried from earlier seasons - Mandatory if warning points are enabled |
| STW-SHT-034 | S34 | Column for each round - Optional, displays data as 1PP (penalty point), 1W (warning), QB (quali… |
| STW-SHT-035 | S34 | A cell holding a penalty may be drawn with a highlighted background. |
| STW-SHT-036 | S34 | Current outstanding ban to serve - Mandatory |
| STW-SHT-037 | S34 | Appeal tokens held - Mandatory while appeals cost tokens |
| STW-SHT-038 | S34 | Drivers suspended from racing - Optional - the list the textual output ends with. A template no… |

## PCK — When the bot is packed

| Rule | Issue | The rule |
|---|---|---|
| STW-PCK-001 | S35 | A pack clears the stewarding team and its head steward, along with this module's channels and r… |
| STW-PCK-002 | S35 | A pack keeps every driver licence with the sanctions upon it, these belonging to the driver and… |
| STW-PCK-003 | S35 | A pack shall be refused while any ticket is open, naming them, a ticket's channel being of the… |

## RST — When the bot restarts

| Rule | Issue | The rule |
|---|---|---|
| STW-RST-001 | S36 | All of this module's scheduled work shall survive the bot stopping and starting again. What cam… |
| STW-RST-002 | S36 | The time the bot was stopped, or cut off from Discord, shall not count against any window in wh… |
| STW-RST-003 | S36 | Clocks in which no one acts run on regardless: the expiry of a timed season ban, and the countd… |
| STW-RST-004 | S36 | A cycle close waiting for a channel to be repaired shall be tried again whenever the bot starts… |

## TST — Test mode

| Rule | Issue | The rule |
|---|---|---|
| STW-TST-001 | S37 | The stages of a stewarding cycle are stepped through with the core specification's test mode co… |
| STW-TST-002 | S37 | <NEW COMMAND> A "test-mode report lodge" command shall be available to league admins, lodging a… |
| STW-TST-003 | S37 | <NEW COMMAND> A "test-mode appeal lodge" command shall be available to league admins, lodging a… |
| STW-TST-004 | S37 | While test mode is enabled, fake drivers may be added to the stewarding team with "steward team… |
| STW-TST-005 | S37 | <NEW COMMAND> A "test-mode ballot cast" command shall be available to league admins, casting a… |
| STW-TST-006 | S37 | Every one of these shall be refused while test mode is off, and written to the log channel. |
