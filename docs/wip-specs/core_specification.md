# Core

This document specifies what the bot is before any module is switched on: the league it runs
for, the seasons it holds, and the divisions, rounds, tracks, teams and drivers a season is
built from. These concepts cannot be disabled and every module is built upon them.

Each module's own document specifies that module. Where this document names a module it names
it only to place it; the rules governing it belong to its own specification.

## Concepts
- League: one Discord server. Every record the bot keeps belongs to one server and is shared with no other.
- Season: one championship a league runs, numbered, raced on a stated edition of the game.
- Division: one championship within a season, holding its own drivers, its own calendar, its own role and its own channels.
- Tier: the standing of a division within its season. Tier 1 is the highest.
- Round: one race weekend in a division's calendar.
- Round format: the shape of a round's weekend, being one of normal, sprint, endurance or mystery.
- Session: one part of a round that is qualified or raced. The sessions of a round follow from its format and are configured by nothing else.
- Track: one circuit the bot ships knowledge of.
- Team: a constructor a driver races for. A team holds seats.
- Seat: one place in a team, held by at most one driver.
- Driver: a person who holds a driver profile upon the server.
- Module: an optional part of the bot, switched on by a league admin.
- League manager: a member holding the interaction role, who commands the bot in the interaction channel.
- League admin: a member holding the league admin role, who governs the bot upon the server and may undo a league entire.

## The league on a server

### Setting the bot up
- A single initialisation command shall establish the bot upon a server, taking four settings at once: the interaction role, the league admin role, the interaction channel and the log channel.
    - The interaction role is the role a member shall hold to command the bot at all.
    - The league admin role is the role a member shall hold to govern the bot and to do what may undo a league entire.
    - The interaction channel is the only channel in which the bot accepts commands.
    - The log channel is where the bot records what it did, what it could not find, and why something fell back.
- The initialisation command shall run once. A second run shall be refused rather than overwrite what stands, and shall name the commands that change a single setting.
- Each of the four settings shall have a command changing that setting alone.
- The initialisation command and the four single-setting commands are a league admin's, and shall run from any channel, holding either role being no part of it. They are what repairs the four settings, and a deleted channel or a withdrawn role would otherwise be unrepairable.
    - These five commands alone shall additionally accept the server's administrator permission in place of the league admin role. They are the only way back for a server that has no league admin role — one that was configured before the role existed, or one whose role has been deleted — and without them such a server could never gain one.
- A server holding no league admin role shall refuse every league admin command, and the refusal shall name the command that sets the role. It shall not fall back to the administrator permission: a league that has not chosen the role has not decided who may undo it.
- Initialisation shall create the Reserve team where the server holds no team. No other team shall be created.

### Who may do what
- A driver shall need no role and no channel. A driver reaches the bot through the buttons it posts and through their own channels.
- Two tiers of authority shall govern every command, and every command shall sit in one of them and no other.
    - **A league admin** shall hold the league admin role. A league admin governs what the bot is upon the server, and everything that may undo a league entire: initialising the bot and repairing its four settings, enabling and disabling a module, starting over, and every command of test mode.
        - A command destroying what a league is built from shall be a league admin's, where nothing puts it back: deleting or cancelling a division, a round or a season, completing or aborting a season, removing a team, sacking a driver, deleting the bot's own messages, amending the results of a round already final, approving an amendment of a season's points, and deleting a points configuration. A command whose undoing is another command — closing a signup window that may be opened again, unassigning a driver who may be assigned again, rejecting a driver who may sign up again, discarding an amendment not yet approved — is a league manager's.
        - Moving a driver and releasing a driver from a division shall be a league manager's, though a release is not put back by any command. Both are the running of a championship already under way.
    - **A league manager** shall hold the interaction role and shall command the bot in the interaction channel. A league manager runs the league: its seasons, divisions, rounds, tracks, teams, drivers and seats; the configuration of every module and the templates and artwork it draws from; the channels each division posts to; and the results, standings, verdicts, check-ins and signups that follow.
- Where this specification does not state a tier, the command is a league manager's.
- **Both tiers shall be roles the league configures, and a permission of the server shall be a route to neither.** A permission is a property of the Discord server and is given for reasons that have nothing to do with a league; a tier is a property of the league. A member may run a championship without being trusted to restructure the server, and may administer the server without being anywhere near the championship.
    - The sole exception is the initialisation command and the four single-setting commands, which the section above sets out.
- The league admin role shall carry the league manager's tier within it. A member holding it shall command the bot without also holding the interaction role, a league admin being able to do everything a league manager may.
- A league admin's command shall be given in the interaction channel, save the initialisation command and the four single-setting commands. Those alone repair the settings the channel itself depends upon, and shall run from any channel.
- A channel the bot opens to the interaction role shall be opened to the league admin role on the same terms, so that a league admin may read what they are entitled to act upon.
- A tier shall govern the action and not the command alone. Where the bot offers an action through a button of its own, that button shall ask the tier its action belongs to, which may be higher than the tier of the command that posted it.
- A button the bot offers a driver in their own channel shall ask nothing, a driver needing no role.
- A command given in a channel other than the interaction channel shall be refused, and the refusal shall be seen by the member alone.

### Channels
- Three channels shall be configured for the server — the interaction channel, the log channel and the signup channel — and eight for each division: its weather forecasts, its lineup, its calendar, its results, its standings, its verdicts, its check-in calls and its attendance.
- A channel shall serve one purpose upon a server. Every command setting a channel shall refuse a channel already set as any of the others, and shall name what holds it.
    - The rule holds across the whole server and not within a division alone. Two divisions shall not share a channel for the same purpose.
    - A command setting a channel to the value that setting already holds shall be refused in its own terms, nothing being changed by the refusal.
    - The check shall be made before anything is written, so that a refusal leaves the configuration exactly as it stood.
    - A channel recorded against a completed or cancelled season shall not be held to this rule.

### The record of what changed
- Every change to a league's configuration shall be recorded twice: as an entry stating who made it, when, in which division where one applies, what changed and from what to what; and as a line a person can read in the log channel.
- A line in the log channel shall name the member, the command and its outcome, and shall state beneath it the values that were set.
- A mention written into the log channel shall not notify anybody.
- A record too long for one message shall be divided across as many as it requires.
- A message the bot fails to post shall be kept and delivered later.

### Modules
- Five modules shall be available, each specified in its own document: signup, results and standings, attendance, weather, and image generation.
- Every module shall be disabled upon a server until a league admin enables it. A bot with no module enabled holds a calendar and nothing more.
- The rules governing a module — what enabling it requires, what disabling it clears, and what it depends upon — belong to that module's specification.
- A disabled module shall produce nothing. While a module is disabled the bot shall neither compute, record nor post any of that module's output, whatever the path arrives at it — a scheduled job, a restart, or a command that amends work arranged while the module was still enabled.
    - Nothing done while a module was disabled shall be recorded as that module's work, so that enabling the module later does not find its work already done.
- Disabling a module shall stop that module's work and no other's. Scheduled work belonging to a module that remains enabled shall continue for every round still to come, and shall be cancelled only by its own module being disabled, by the round or season it belongs to ending, or by a reset. Where a module's specification states that another module depends upon it, disabling it shall disable the dependent module too, and that cascade shall be reported.
- Which modules are enabled shall be displayed in the configuration review and the placements review.
- Seasons, divisions, rounds, tracks, teams and drivers are foundational and shall not be disabled.
- Stewarding, statistics and help are recorded as intended modules and are not built.

### Starting over
- A reset command shall delete a server's league data entire — its seasons, divisions, rounds, sessions, weather results, teams of a season, seats, driver placements and the record of changes — and shall cancel every piece of scheduled work.
    - It shall require the word `CONFIRM` to be typed exactly.
    - A fuller form shall additionally clear the server's four settings, so that the bot may be initialised again.
    - The server's team list shall survive either form.
    - It shall report what was deleted and shall be written to the log channel.
    - It shall be given in the interaction channel. It is a league admin's command and not a repair of the settings, the initialisation command being the footing it does not share.
- A command deleting the bot's own messages in a channel shall require the number to be deleted, and shall accept no fewer than one and no more than ten.
    - The number shall count the messages actually deleted. A message written by anybody other than the bot shall never be deleted and shall never be counted towards it.
    - The messages deleted shall be the most recent, the newest first.
    - The command shall look back over a bounded stretch of the channel to find them, and shall report the shortfall where it finds fewer of the bot's messages than it was asked for.
    - A message that cannot be deleted shall be reported and shall not be counted towards the number asked for.

## Seasons

### The life of a season
- A season shall stand in one of ten states:
    - **Configuration**, while the league settles what the season is to be;
    - **Waiting**, while the season waits for its signup window to open;
    - **Signups**, while that window is open;
    - **Placements**, while the season is built and its drivers are placed;
    - **Ongoing**, while it is being raced;
    - **Ongoing, signups open**, while it is being raced and a signup window is open;
    - **Ongoing, placements**, while it is being raced and the drivers of that window are placed;
    - **Pending completion**, once every one of its divisions is finished or cancelled;
    - **Completed**, once it has been completed;
    - **Cancelled**, where it was abandoned.
- The permitted transitions shall be:
    - Configuration to Waiting, upon the configuration being confirmed. Where the signup module is disabled, or the season is configured in test mode, to Placements instead.
    - Waiting to Signups, upon the signup window being opened.
    - Signups to Placements, upon the signup window being closed.
    - Placements to Ongoing, upon placements being confirmed.
    - Ongoing to Ongoing, signups open, upon a signup window being opened.
    - Ongoing, signups open to Ongoing, placements, upon the window being closed while any signup remains unsettled; to Ongoing where none does.
    - Ongoing, placements to Ongoing, upon placements being confirmed.
    - Ongoing, Ongoing, signups open and Ongoing, placements to Pending completion, as set out below.
    - Pending completion to Completed, upon the season being completed.
    - Ongoing, Ongoing, signups open and Ongoing, placements to Cancelled, upon the season being cancelled.
    - Configuration, Waiting, Signups and Placements to nothing at all, upon the season being aborted.
- A signup is **unsettled** while its driver is Unassigned, Pending Admin Approval, Awaiting Correction Parameter or Pending Driver Correction.
- Ongoing, Ongoing, signups open and Ongoing, placements are **the three ongoing states**.
- A season is **active** from the moment it is set up until its completion or cancellation has finished, or until it is aborted. A server shall hold at most one active season. A new season cannot be set up until the standing one is completed, cancelled or aborted; completed and cancelled seasons never stand in the way.
- A season configured in test mode shall never enter Ongoing, signups open.
- A season shall carry a number, assigned by the bot as one higher than the count of seasons whose placements have been confirmed. The number is provisional until the season's placements are first confirmed, and an aborted season takes none. That number shall be the one displayed in all bot output.
- A season shall carry the edition of the game it is raced on, named when the season is set up.
- A completed or cancelled season shall be immutable. Every command that would change one shall be refused.
- No season shall be completed of its own accord. A league admin shall complete it, completing a season being among the acts nothing puts back.
- A round shall stand in one of six states, each of the middle four naming what the round is waiting on:
    - **not run**, before its moment has arrived;
    - **awaiting results**, once its moment has passed and its results have not been entered;
    - **awaiting report verdicts**, once its results are posted and the reports lodged against them are being judged;
    - **awaiting appeal verdicts**, once those verdicts are posted and the appeals against them are being judged;
    - **final**, once the appeal verdicts are posted and the results stand;
    - **cancelled**, where the round was called off.
- Final and cancelled are the ends of a round's life. A round is finished when it reaches either.
- A division is finished when every one of its rounds is finished.
- A round of a league that does not run the results module shall become final when its moment passes, there being no results to await.
- A round waiting on results, on report verdicts or on appeal verdicts shall become final if the results module is disabled while its season is in one of the three ongoing states. Those three states wait upon that module alone, and a round left in one of them once the module is gone would never be finished, would hold its division open, and would leave its season unable to be completed. What else that disabling destroys is the results module's own to state.

### Configuring a season
- A season shall be begun by a setup command naming the edition of the game, and shall begin in Configuration.
- In Configuration the league shall settle the team list and the team roles, which modules are enabled and their settings, and whether the season runs in test mode. No division, round or placement shall exist in Configuration.
- The signup module shall be enabled, disabled and configured only while its season is in Configuration, or while the server holds no active season.
- Every other module may be enabled until the season's placements are first confirmed, or while the server holds no active season. Its settings may be changed at any time before then; once placements are confirmed, on the terms its own specification sets for a season being raced.
- The configuration shall be confirmed through a configuration review, run by the configuration review command.
    - The review shall report the season, whether it runs in test mode, the team list and each team's role, the modules enabled upon it and the configuration of each, and every fault that would prevent the configuration being confirmed.
    - It shall post its report as the placements review posts the subsections preceding its divisions — the season and its modules, the signup configuration, the attendance configuration, the points configurations, the weather configuration and the image outputs, one message each, in the same words — a subsection holding nothing not being posted.
    - The review shall end with a button confirming the configuration, which shall be withheld while any fault stands. The button shall be governed as the button confirming placements is: who may press it, how long it stands, the evidence it is confirmed upon, and what becomes of a review that expires or is refused.
- The configuration review shall check everything that can be checked before the season has divisions: every check the placements review makes shall be made here too, save those concerning divisions, lineups, calendars and division channels. Confirming the configuration shall require, among them:
    - where the signup module is enabled, its signup channel, its base role and its signed-up role each to be set, every one missing being named;
    - every team name to be usable as the filename of that team's artwork, whether or not the image module is enabled;
    - every requirement an enabled module states of its configuration alone — its points configurations and its templates among them — as that module's own specification sets out.
- Every check the configuration review makes shall be made again when placements are confirmed, the configuration of a module other than signup being able to change in between.
- Confirming the configuration shall fix, for the rest of the season, the team list, the game edition, test mode, and whether the signup module is enabled and how it is configured.

### Waiting and signups
- A season in Waiting shall wait for the signup window to be opened, and shall move to Signups when it is.
- A season in Signups shall move to Placements when the window closes, by command or at its close time. What the close does to the drivers still signing up is the signup module's own to state.

### Building a season
- Divisions shall be created and deleted, and rounds added and deleted, only while the season is in Placements. What may be amended once placements are confirmed is set out under Divisions and Rounds below.
- The channels of each division may be set while the season is in Placements, and at any time after until the season ends, so that a channel lost may be repaired. A division's channels belong to its season: once the season is completed, cancelled or aborted they are no longer set, repaired or read as holding a channel, and a command setting a channel with no season active shall be refused.
- Drivers shall be placed and removed while the season is in Placements, as set out under Drivers below. No role shall be granted for a placement until placements are confirmed.
- Once placements are first confirmed, nothing shall be added to a season and nothing deleted from it. Rounds and divisions may only be amended and cancelled.

### Reviewing placements
- The placements review shall be run by any holder of the interaction role. Reading what a season is configured to be is not an administrative act.
- Run while the season is in Placements, the review shall post its report publicly, as one message per subsection and not as one message carrying them all. The subsections are, in this order: the season and the modules enabled upon it; the signup configuration; the attendance configuration; the points configurations; the weather configuration; and the image outputs. The blocks describing each division follow them.
- Run while the season is in Ongoing, placements, the review shall report the lineups alone: every division's lineup as it will stand once placements are confirmed, and, publicly, every signup still unsettled.
- A subsection holding nothing shall not be posted.
- Each subsection shall further be divided across as many messages as its own length requires.
- The validations that belong to the season rather than to a module shall be posted with the first subsection, whatever modules are enabled.
- Each division's block shall state its role and every channel configured for it, and shall show its calendar and its lineup as the league will actually receive them.
- A division's calendar shall carry the faults of its own dates, whichever form the calendar takes. A calendar drawn as a graphic is drawn from the very rounds that are wrong and cannot show which of them have gone by, so the finding shall be posted beside it.
- Those faults shall be reduced to the latest round of each kind: the last round whose moment has passed, and the last round holding an elapsed window where that is a later round than the first. A round whose moment has passed shall not also be named for the windows it missed, every one of which has elapsed too. Every earlier round is implied by the round named, a calendar moved past it having been moved past them all.
- The report shall name every signup still unsettled, publicly, among its own messages; the reason the question is withheld shall refer to them.
- The report shall end with the question confirming placements.
- The placements review command shall be refused in any state but Placements and Ongoing, placements. The configuration review command shall be refused in any state but Configuration.

### Confirming placements
- No command shall confirm placements. Placements shall be confirmed by the button the placements review posts, and by no other means.
- The button shall be carried by a message of its own, asking whether the placements are accepted and naming both the member who ran the review and who may answer it.
- That message shall be posted publicly, and not to the reviewer alone. A reviewer who may not confirm is thereby able to put the question to a member who may.
- The button shall be pressed only by the member who ran the review, or by a league admin. A press by any other member shall be refused, shall say who may confirm, and shall confirm nothing; the refusal is seen by the presser alone.
- Who is pressing shall be the first thing the button settles, before the state of the season is read.
- The button shall carry no other action. The season is amended by the commands that amend it and reviewed again.
- The button shall stand for five minutes from the posting of the review that carries it. Upon their passing its message shall be deleted, and a notice posted in its place naming the reviewer, saying that the review has expired and that it must be run again.
- A review standing when the bot stops shall be treated as expired when the bot next starts.
- Placements confirmed shall have the review they were confirmed from deleted, the question and every message of the report alike. A review expired shall have its report deleted on the same terms.
- The button shall be withheld altogether where the review found something that would prevent the placements being confirmed.

#### The evidence placements are confirmed upon
- The review shall record the state of the season at the moment its report is posted, over the whole of what that report describes: the season, its divisions, its rounds, its teams and seats, its seated drivers, its unsettled signups, its channels, the modules enabled upon it, whether it runs in test mode, the server's team list and each team's role, its points configurations, the configuration of each module, and the template and artwork files its graphics are drawn from.
- The button shall refuse where that state has changed since the report was posted, shall name the parts of it that changed, and shall confirm nothing. The report read is the report confirmed.
- The state shall be compared before the season is validated and before anything is drawn, and after the member pressing has been found entitled to confirm.
- A review refused upon a changed state shall end as an expired one does.
- The confirmation shall not draw the graphics of the season. The review draws them, and withholds its own button where one will not draw.

#### What confirming placements requires
- No signup of the season shall be unsettled: every driver who signed up shall be placed or rejected.
- Where the season is in Placements, additionally:
    - Every check the configuration review makes shall pass.
    - Every division shall have set every channel the season will post to: its lineup channel, its calendar channel, and every channel an enabled module requires of it. Each one missing shall be named with its division.
    - The season shall hold at least one division that is not cancelled.
    - The tiers of a season's divisions shall form a sequence from 1 with no gaps.
    - Every division shall hold at least one round.
    - No two rounds of one division shall be scheduled at the same moment.
    - No round shall have a moment that has already passed, and this shall hold whatever the modules enabled. A round's result submission is armed against its own moment and is the round's one passage from awaiting its moment to awaiting its results; armed in the past it is discarded rather than run, and no command opens a submission afterwards, so the round could never take results at all and could never leave the state of not having run. The rule is the one that governs moving a round, stated at the other door so that the two cannot disagree.
    - No round shall be inside a window that one of the enabled modules configures before the round. A module disabled contributes no window; the round's own moment above is judged regardless.
    - A cancelled round shall be exempt from both, having no scheduled work left to lose. Refusing a season on account of one would leave a league unable to confirm until they deleted a record they may want to keep.
    - Each enabled module shall impose its own requirements, stated in its own specification.
- A confirmation failing any requirement shall be refused with nothing committed, and every fault shall be named, save the two date requirements above, whose faults are reduced to the latest round of each kind a division holds.

#### What confirming placements does
- Every placement not yet committed shall be committed.
- Where the season is in Placements:
    - The sessions of every round shall be created.
    - The season's scheduled work shall be armed before the season's state is changed, so that a failure to arm it leaves the season in Placements.
    - The points configurations attached to the season shall be recorded upon it as they stand.
    - Every placed driver shall be granted their division's role and their team's role.
    - Each division's lineup, calendar and opening classification shall be posted. A posting that fails shall be reported and shall not refuse the confirmation.
    - The season shall move to Ongoing and take its number.
- Where the season is in Ongoing, placements:
    - Every driver whose placement is committed by it shall be granted their division's role and their team's role.
    - The lineup of each division holding such a driver shall be posted once.
    - The season shall move to Ongoing.

### The ongoing states
- In Ongoing, a signup window may be opened, moving the season to Ongoing, signups open. A signup window shall be opened from no other ongoing state.
- In Ongoing, signups open, the championship shall carry on unchanged: rounds, results, penalties, check-ins, attendance sanctions, sacking, the amending and cancelling of rounds, and the disabling of modules.
- In Ongoing, placements, the championship shall carry on as in Ongoing, signups open. A driver whose placement is not yet committed shall stand outside it until it is: they shall hold no role, shall not appear in any lineup, shall receive no check-in call and accrue no attendance points, and shall not appear in any results or standings. A round run meanwhile shall be run without them.
- Sacking a driver, and cancelling the season, a division or a round, shall be possible only in the three ongoing states.
- No module shall be enabled in any of the three ongoing states. A module may be disabled on the terms its own specification states.
- Nothing shall be added to a season in any of the three ongoing states and nothing deleted from it. A round may be amended or cancelled and a division may be cancelled, as set out under Rounds and Divisions below.
- A cancellation is irreversible.

### Pending completion
- A season in any of the three ongoing states shall move to Pending completion as soon as every one of its divisions is finished or cancelled. There being no round left to place a driver into, a season leaving Ongoing, signups open or Ongoing, placements shall first:
    - close its signup window, where one is open;
    - discard every placement not yet committed;
    - return to Not Signed Up every driver whose signup is unsettled and every driver whose placements were all uncommitted, as the reject command would: an approved driver loses the signed-up role, and a signup in review has its channel closed.
- In Pending completion the results of a round already final may still be amended, and an amendment of the season's points may still be approved. The only other thing that may be done with the season is to complete it. No module shall be disabled in Pending completion.

### Ending a season

#### Completing a season
- Completing a season shall be a league admin's, and shall be refused in any state but Pending completion.
- Completing a season shall, in this order:
    1. post each division's final classification;
    2. record a history entry for every division each driver took part in;
    3. revoke the division, team and signup roles of the season's drivers;
    4. close the signup window, where one is open, so that no signup begins after the driver pass has gone by;
    5. run the driver pass;
    6. switch test mode off, deleting every driver created by test mode;
    7. mark the season completed.
- Once completed, the server shall hold no active season.

#### The driver pass
- The driver pass shall move every driver who is Unassigned, Assigned, Pending Signup Completion, Pending Admin Approval, Awaiting Correction Parameter or Pending Driver Correction to Not Signed Up, cancelling any signup still in progress.
- It shall then delete every real driver at Not Signed Up whose former-driver flag is false, with their placements and history entries. Their signups shall remain with the season.
- A driver created by test mode shall not be deleted by the driver pass. Test mode deletes such drivers when it is switched off, and keeps their history entries, as set out under Test mode.
- A driver whose former-driver flag is true shall be retained.

#### Cancelling a season
- Cancelling a season shall be a league admin's, shall require the word `CONFIRM`, and shall be refused in any state but the three ongoing states. A season is cancelled where it should not go on; a season that was raced to its end is completed.
- Cancelling a season shall post a notice to each division still running, shall cancel every piece of scheduled work, shall cancel every division of it that is not already cancelled, and shall discard every placement not yet committed.
- It shall then record a history entry for every division each driver took part in, as completing one does, revoke the same roles, close the signup window where one is open, run the driver pass, switch test mode off, and only then mark the season cancelled. A season that was cancelled is league history: it happened, and its drivers raced in it.
- A cancellation shall never discard a result. Only a round not yet run, or run but with its results not yet entered, may be cancelled — by itself, or by the cancelling of the division or season above it. A round further along shall keep its place and its results.

#### Aborting a season
- An abort command shall be a league admin's, shall require the word `CONFIRM`, and shall be refused in any state but Configuration, Waiting, Signups and Placements.
- Aborting a season shall delete the season and every record belonging to it, its signups included. The season shall take no number and shall leave nothing in the archive.
- Aborting shall close the signup window where one is open, revoke the signup roles of the season's drivers, run the driver pass without recording any history, and switch test mode off, deleting every driver created by test mode.
- Once aborted, the server shall hold no active season.

### The archive
- A completed or cancelled season and everything belonging to it shall be retained permanently and shall never be changed or deleted: its divisions, its rounds and their amendments, its weather, its results, its standings, its placements, its points configurations, its signups and the signup configuration and windows they were made under, and its record of changes.
- A driver deleted by the driver pass shall leave no placement and no history entry in the archive. Their signups shall remain.
- The archive shall be the source from which season history and statistics are drawn.
- A driver shall have taken part in a division once a placement of theirs in it has been committed, and shall remain part of it whatever becomes of that placement: a driver moved, released or sacked during the season was part of every division they held a committed placement in.
- Each driver retained by the driver pass shall keep, for every division of a season that has ended which they took part in, a history entry stating the season's number, the division's name and tier, and the driver's final position, final points and gap to the winner of that division. A driver who took part in two divisions of one season holds an entry for each, and both divisions stand in their history.
    - The entry shall record whether the driver's division was cancelled. Cancellation reaches a driver only through their division, so a season cancelled outright marks every one of its entries.
    - A driver created by test mode shall gain a history entry as any other driver does, and shall keep it after test mode deletes them.

## Divisions
- A division shall be created while its season is in Placements, taking a name, a role and a tier.
    - A division's name shall be unique within its season, without regard to case.
    - A division's tier shall be unique within its season and shall be no lower than 1.
- The tiers of a season's divisions shall form a sequence from 1 with no gaps, or confirming placements shall be refused. Divisions shall be held and displayed in ascending order of tier, tier 1 being the highest.
- A division's tier may be used to identify it in a command, but its name shall be what the bot displays.
- A division may be created by duplicating another, taking a new name, role and tier and an offset in days and hours. Every round of the source division shall be copied with its moment shifted by that offset and the copies renumbered.
    - A duplication producing a round in the past shall warn and shall not be refused.
    - A division created by duplication shall inherit none of the source division's channels.
- A division may be renamed, and its name, tier and role amended, while its season is in Placements alone.
- A division deleted while its season is in Placements shall take with it its rounds, its sessions, its teams and seats, and the placements made in it.
- A division shall stand in one of four states: setup until its season's placements are first confirmed, active once they are, finished once every one of its rounds is finished, and cancelled where it was called off.
- A division of a season in one of the three ongoing states may be cancelled, upon the word `CONFIRM`. Its rounds shall be unscheduled, every one of them that may still be cancelled shall be, a notice shall be posted to it, and it shall thereafter be excluded from the validation of tiers, from the standings and from the end of the season.
- Every division shall carry a role, which the bot mentions when it posts to that division.
- A division's calendar may be reposted on demand. A calendar already posted shall not update itself.

## Rounds
- A round shall belong to one division and shall state a moment in UTC, a format and a track.
- Round numbers shall never be entered. The rounds of a division shall be ordered by their moment and numbered from 1, and adding, deleting or re-timing one shall renumber the division.
- Two rounds of one division shall not be scheduled at the same moment. This shall be refused by every command that would create one, and again when placements are first confirmed.
- A round shall be refused where the division would thereby hold more rounds than its calendar graphic can draw.

### Building a calendar in bulk
- A command shall add many rounds to one division at once, taking a calendar written one round to a line in the form "datetime, format, track", the datetime being stated in UTC.
- A command shall add rounds to several divisions at once, taking a calendar written as XML.
    - A configuration holds one or more divisions; a division carries its name as an attribute and holds one or more rounds; a round states a datetime, a timezone, a format and a track. The rounds of a division need not be stated in chronological order.
    - The datetime of a round shall be stated in the local time of the timezone beside it and converted to UTC upon that timezone. The timezone shall be named in the IANA form and validated as one.
    - A track shall be identified by its number, by its name, or by the form the completion offers.
- Both commands shall add to the rounds a division already holds and shall never replace them.
- Both commands shall apply every validation that adding one round applies, and shall refuse the whole import where any round fails one of them, adding nothing and naming every fault at once.
- Where a division would hold more rounds than its calendar graphic can draw, the import shall be refused. The measurement shall be made of the whole import and not of each round in turn.

### Formats and sessions
- A round shall take one of four formats, and the sessions of a round shall follow from it and from nothing else:
    - Normal: a short qualifying and a long race.
    - Sprint: a sprint qualifying, a sprint race, a feature qualifying and a feature race.
    - Endurance: a full qualifying and a full race.
    - Mystery: no sessions at all.
- The sessions of every round shall be created when the season's placements are first confirmed.

### Mystery rounds
- A mystery round shall name no track. A round of any other format shall name one the bot can resolve.
- A mystery round shall hold no sessions.
- A mystery round shall not be offered as the next round of a division.
- What a mystery round receives in place of a forecast is specified by [the weather module](weather_module_specification.md).

### Amending and cancelling a round
- A round of a season whose placements have been confirmed may have its track, its moment or its format amended, behind a confirmation.
- An amendment shall be judged and carried out as one change, however many of the three fields it alters. Where any rule refuses any part of it, none of it shall happen and the round shall stand exactly as it did, and the manager shall be told so.
- Every rule shall read the round as it will stand once amended: its new moment where one is given, its present moment otherwise.
- The rules shall be judged again at the moment the amendment is confirmed and not only when it is offered, a window being able to pass while the confirmation stands. Where the answer has changed the amendment shall be abandoned and the manager invited to start again.
- A round shall not be amended once its results have been entered, nor once it has been cancelled. From the moment results are entered the drivers have reports and appeals to lodge against them, and an amendment would take that from them.
- The amendment shall be recorded in the record of what changed, whatever the modules enabled.
- The confirmation shall name what the amendment will cost before it is made: which forecasts will be withdrawn, or that those already posted will stand, and any window the round will no longer have.
- The scheduled work of an amended round shall be cancelled and armed again against its new moment. Each module shall specify what its own share of that work becomes, and a module disabled shall have none armed. The round's result submission shall be armed whatever the modules, being the round's own passage from awaiting its moment to awaiting its results.
- Each of a round's scheduled jobs shall be identified in a way that stays unique to that round for its whole life, so that renumbering a division cannot make one round's work overwrite another's.

#### Amending a round's moment
- A round's moment may be amended to any moment still to come, subject to the rules above.
- A round shall not be moved to a moment that has already passed, and this shall hold whatever the modules enabled. A round's result submission is armed against its own moment and is the round's one passage from awaiting its moment to awaiting its results; armed in the past it is discarded rather than run, and no command opens a submission afterwards, so the round could never take results at all. Moving a round *forward* is untouched and remains the remedy for a circuit or a format that must be corrected late.
- The amendment shall be refused where the check-in deadline computed from the round's new moment has already passed. A check-in that would open and close in the same instant asks a question nobody can answer, and the round would be recorded afterwards as perfect attendance for the whole division.
- Where the check-in deadline computed from the round's new moment has passed, nothing shall be posted afresh and no answer may be changed: the check-in is settled and the reserves are distributed against it.

#### Amending a round's track or format
- A round's track shall not be amended while a forecast drawn for it still stands, and its format shall not be amended while a forecast drawn for its sessions still stands.
- Neither shall be amended once the round's moment has passed.
- Where the round's moment is amended in the same change, both rules shall read the new moment and the forecasts as they will stand once it is amended. Moving a round is therefore the league's remedy for a circuit or a format that must be corrected late.
- A mystery round names no circuit, so its track shall not be amended unless its format is amended in the same change.
- A round of a season in one of the three ongoing states may be cancelled upon the word `CONFIRM`. Its scheduled work shall be cancelled and a notice posted.
- A round shall not be cancelled once its results have been entered. From that moment the drivers have reports and appeals to lodge against them, and calling the round off would take that from them.
- A round shall not be cancelled while a results submission for it stands open.

## Tracks
- The bot shall ship a fixed list of circuits. A league shall neither add, edit nor remove one.
- Each circuit shall carry a number, the name of the circuit, the name of its grand prix, its location, its country, and two parameters governing the draw of rain probability at that circuit.
- A circuit shall be named by its number, by its name, or by the form the completion offers, without regard to case.
- A command shall list the circuits available with their numbers.
- A circuit shall additionally hold, per tier, a track record for every session type and a lap record for the sprint and feature races alike, each stating the game, the season, the round, the lap time and the driver who set it. These records shall be held per tier and shall come into being only as a tier requires one.
- A round shall record the canonical name of its circuit.

## Teams
- A server shall hold a list of teams. The teams of a division shall be created from that list when the division is created.
- The list shall ship holding the Reserve team alone. A league shall build its own.
- The Reserve team shall always exist upon the server and in every division. It shall not be added, renamed or removed, and it shall have no limit of seats. Its role shall be set by a command of its own.
- The role of any team may be set at any time, whatever the state of the season, so that a role deleted from the server may be repaired.
    - Every driver holding a committed seat in that team shall follow the change: the team's former role shall be revoked from them, save where another team still maps to it, and its new role granted. A driver created by test mode holds no role and shall be left alone.
- Adding, renaming or removing a team shall change the server's list. It shall be permitted while the server holds no active season, or while the active season is in Configuration, and shall be refused otherwise.
- A team name shall reduce to a usable filename:
    - It shall not be empty and shall hold at least one letter or digit.
    - It shall not reduce to `reserve`.
    - It shall not reduce to the same form as another team's name in its scope — the server for the server's list, the division for the teams of a season.
    - This shall bind whether or not the image module is enabled, the reduced name being the filename under which every graphic seeks that team's artwork.
    - Only the new name shall be validated when a team is renamed, so that a team named before this rule may still be corrected.
    - A season whose configuration has been confirmed shall not be validated against this rule again.
- Every team other than Reserve shall hold two seats.
- Teams shall be ordered as they were added and not alphabetically, so that adding or renaming one never moves those already drawn.
- The placements review shall display every team, the drivers seated in each, and every signup still unsettled.

## Drivers

### The driver profile
- A driver profile shall be held at server scope and shall belong to one Discord account. A person shall hold at most one profile upon a server.
- A profile shall carry the driver's state and a former-driver flag.
- A profile shall carry, for each division the driver currently races in, that division's name and tier alongside the driver's standing in it: their position, their points, and their gap to the leader of that division.
- A profile shall carry, for each division the driver has raced in before, that division's name and tier alongside the season's number and the driver's final position, final points and gap to the winner of that division.
- A member holding no profile shall be treated as Not Signed Up.
- A profile shall be retained where the member leaves the Discord server.
- Every change of a driver's state shall be persisted.

### The states of a driver
- A driver shall stand in one of seven states:
    - Not Signed Up: inactive, and able to begin a signup.
    - Pending Signup Completion: working through their signup.
    - Pending Admin Approval: their signup awaits a league manager.
    - Awaiting Correction Parameter: a league manager has asked for changes and is choosing which answer is to be given again.
    - Pending Driver Correction: the driver has been asked to amend a named answer.
    - Unassigned: approved, and not yet placed in a division and team.
    - Assigned: placed in at least one team.
- No state shall bar a driver from signing up. A driver shall not be banned: sanctions belong to the stewarding module, which shall bring the bar together with the commands that impose and lift it.
- The permitted transitions shall be:
    - Not Signed Up to Pending Signup Completion.
    - Pending Signup Completion to Pending Admin Approval, or to Not Signed Up.
    - Pending Admin Approval to Awaiting Correction Parameter, to Unassigned, or to Not Signed Up.
    - Awaiting Correction Parameter to Pending Driver Correction, back to Pending Admin Approval where no answer is named in time or the bot restarts, or to Not Signed Up.
    - Pending Driver Correction to Pending Admin Approval, or to Not Signed Up.
    - Unassigned to Assigned, or to Not Signed Up.
    - Assigned to Unassigned, or to Not Signed Up.
    - Not Signed Up to Unassigned, and Not Signed Up to Assigned, under test mode alone.

### Leaving the league
- The former-driver flag shall be false by default and shall be set once a driver has raced a round. A driver so marked shall not be deleted, only amended.
    - A driver shall be held to have raced a round only once that round is final, and only by its final results. Results still awaiting report or appeal verdicts shall mark nobody.
    - An entry recording that the driver did not start shall not count as having raced. A driver whose only entries in a round are did-not-start entries has not raced that round.
    - An amendment of a final round's results that leaves a driver no longer having raced that round shall clear the flag, unless another final round marks them.
- A driver whose flag is false and who reaches Not Signed Up, by any route — a sack, a rejection of their signup, a withdrawal, a signup cancelled or timed out, or the reject command — shall be **pending deletion**.
    - A driver pending deletion shall not be deleted at once. They shall be deleted by the driver pass of the season's completion, cancellation or abort.
    - Until then the profile shall stand at Not Signed Up and may sign up again. Whether to accept them is the league's to decide.
- A driver whose flag is true and who reaches Not Signed Up shall be retained, with nothing of their profile or their signups cleared, so that the results they raced for remain attributed.
- A committed driver may be sacked, only while the season is in one of the three ongoing states. A driver who is not committed shall not be sacked: an uncommitted placement is removed by the command removing a driver from a division, and an Unassigned driver is turned down by the reject command. Sacking shall free every seat they hold, revoke every division, team and signup role, and return them to Not Signed Up.

### Placement into a division and team
- A placement shall be **committed** once placements have been confirmed with it standing, and uncommitted until then. A driver holding a committed placement is a **committed driver**; a driver placed or awaiting placement who holds none is an **uncommitted driver**.
- No placement command shall post a lineup, nor grant a role, for a placement that is uncommitted. The lineups are posted and the roles granted when placements are confirmed.
- A command shall place a driver, taking the driver, a division named by its tier or by its name, and a team of that division.
    - The driver shall be Unassigned or Assigned.
    - The season shall stand in Placements, or in Ongoing, placements where the driver is uncommitted.
    - The driver shall hold at most one seat in any one division, and may hold a seat in more than one division.
    - The team shall have a seat free. The Reserve team shall always have one.
    - A driver who was Unassigned shall become Assigned.
- A command shall remove a driver from a division.
    - The driver shall be Assigned and shall hold a seat in the division named.
    - The season shall stand in Placements, or in Ongoing, placements where the placement is uncommitted.
    - A driver holding no other placement shall return to Unassigned.
- A command shall move a committed driver from their seat in one division to a team of the same division or of another: from a full-time seat to Reserve, from one team to another, or from one division to another.
    - The season shall stand in one of the three ongoing states.
    - A driver shall not be moved into a division other than the one they leave where they already hold a seat.
    - The team shall have a seat free. The Reserve team shall always have one.
    - The move shall be made as one change. The roles of the seat left shall be revoked where no other seat of the driver maps to them, the roles of the seat taken shall be granted, and the lineup of each division it touches shall be posted once.
    - A driver moved to another division shall leave the points they scored in the division they left.
- A command shall release a committed driver from one division, while they keep every other seat they hold.
    - The season shall stand in one of the three ongoing states.
    - The command shall be refused for a driver's only committed seat, a placement not yet committed elsewhere not counting as one. Sacking or moving the driver applies there.
    - The division's role shall be revoked. The team's role shall be revoked only where the driver holds no other seat, across all divisions, mapping to that role. The division's lineup shall be posted again.
- A command shall reject a driver who is Unassigned.
    - The season shall stand in Placements or in Ongoing, placements.
    - The driver shall return to Not Signed Up and lose the signed-up role.
- A placement or a move shall be refused where it would carry a division beyond what its lineup, its attendance sheet or its standings graphic can draw, and nothing shall be changed by the refusal.
- Every successful move, release and sacking shall cause the lineup of each division it touches to be deleted and posted again in its lineup channel, once.

### A driver's accounts
- A driver shall own every Discord account they have raced under. One of them is their **current** account; the others are their past accounts. Any of them identifies the driver.
- A league manager shall be able to make another account a driver's current one, so that a person changing account keeps their history. The account it replaces joins the driver's past accounts.
- Nothing the league holds of the driver shall be rewritten when their account changes. A signup, a result, a standing, a fastest lap and a history entry each keep the account they were written under, and a completed season stays exactly as it was.
- A driver's results, standings, points, positions and history shall count them once, whichever of their accounts each result stands under. Everything drawn or posted from then on shall name them by their current account — a completed season's results or standings drawn again included. A message already posted is not rewritten.
- Any account of the driver's shall name them wherever a driver is named: a result submitted or resubmitted, a penalty, a pardon, an appeal, an amendment, a command, a check-in. It is read at the moment of use, so a submission or a review already open when the account changes accepts both.
- The driver may be named to the command by any of their accounts, and one of their own past accounts may be made current again.
- Only a member of the server may be made current.
- The driver's roles shall belong to their current account. Making an account current shall give it the signed-up, division and team roles the driver holds, and take them from the account it replaces where that account is still in the server. A role Discord will not move shall be reported to the league manager; the change of account stands.
- A signup channel held open after an approval or a rejection shall move to the new current account, and shall still be deleted when it was already due. Where the new account holds a held channel of its own, that one is kept and the replaced account's is deleted at once.
- An account shall belong to one driver in a league. An account that is a past account of another driver shall be refused.
- The change shall be refused while either the driver or the new account has a signup in progress — collecting, in review, or in correction — and the league manager told to finish or withdraw it first.
- A test-mode driver shall not be given a real account, nor a real driver a test-mode one.
- An account shall not be made current where it already holds a driver profile, or results, standings or history of its own in the league. Every refusal shall change nothing.
- A past account of a driver shall not sign up; the signup button names the driver's current account instead.
- Only a driver's current account leaving the server is the driver leaving it; a past account leaving changes nothing.
- A change of account shall touch nothing of any other league upon the bot. A person who holds a profile in two leagues keeps the other league's untouched.
- A driver's portrait shall not be carried, a portrait being the picture of the account itself. The one obtained for the account replaced shall be discarded, and the new account's own shall be obtained as any driver's is.

## When the bot stops
- The bot is a program somebody shall keep running. While it is stopped nothing happens.
- When it starts again it shall recover: the weather phases that came due, where the weather module is enabled; the check-in calls and deadlines that came due; a signup window's closing timer, closing the window at once where its moment has passed and moving its season on as a close does; interrupted result submissions, which shall be cleared and reopened with the league manager told to submit again; penalty and appeal reviews, which shall be posted again rather than discarded; abandoned amendment channels, which shall be deleted; configuration and placements reviews left standing, which shall be expired; and seasons left part-built.
- The end of a season shall not be recovered. A league admin shall complete it.
- Anything else that came due while the bot was stopped is missed.
- A message the bot failed to post shall be retried until it is delivered, shall survive a restart, and its eventual delivery shall be recorded in the log channel. A message still undelivered after about an hour shall be reported there.

## Test mode
Test mode exists because the bot is almost entirely time-driven, and a season's behaviour
cannot be observed by waiting for it. [Testing with test mode](../how-to/test-mode.md) describes how to use it; this
section states the rules it holds to.

### What test mode is
- Test mode shall be a state of the server, persisted and surviving a restart, switched by a toggle asking what configuring a league asks. It is chosen for a season, and holds for that season until it ends.
- Test mode shall provide a command firing the next scheduled event at once, in the order the events would have fired, without altering the moment any of them was scheduled for.
- Test mode shall provide a command reporting, for every round, which of its scheduled work has run and which remains.
- Test mode shall provide synthetic drivers, so that a division may be filled and raced without real Discord accounts.
- While test mode is enabled, a switch of its own shall stand in for the signup module's nationality setting, so that both may be seen without altering what a league's real signups ask.
- While test mode is enabled, a league admin shall be able to set a driver's former-driver flag by hand, to true or to false. This shall be possible in no other circumstance.
- Every test mode command but the toggle shall re-read the state at the moment it is given and shall be refused while test mode is off.

### Entering and leaving it
- A server shall be either running a real league or under test, never both.
- The toggle shall be refused unless the server holds a season in Configuration. Test mode shall be fixed for the season when its configuration is confirmed, and shall be switched off when the season is completed, cancelled or aborted.
- Test mode shall not be enabled while the server holds a real driver whose state is anything other than Not Signed Up. A driver profile at Not Signed Up is a former driver or pending deletion, and shall not stand in the way.
    - The refusal shall name how many real drivers the server holds, and the state of test mode shall be left unchanged.
- While test mode is enabled:
    - A real driver shall not be placed in a team. A driver created by test mode shall still be placed freely.
    - The season shall pass from Configuration straight to Placements, and shall never open a signup window.
- Enabling test mode shall create and attach two ordinary points configurations, "Standard" and "Half Points", to the season in Configuration, unless a configuration of that name is already attached to it. They shall be created as ordinary configurations of the server and shall be indistinguishable from ones a league made itself, so that a test season passes the points requirement of confirming placements without one being built by hand.
- Test mode shall not relax any requirement of confirming placements beyond the points configurations above. In particular, a season holding a round whose moment has passed, or a round already inside one of its enabled modules' configured windows, shall be refused under test mode exactly as it is refused otherwise, so a test season built in the past shall not be confirmable — and the first of those shall refuse it with every module switched off. A test season that quietly lost its check-ins would misreport attendance precisely as a real one does, and is a worse thing to be testing against than a calendar that has to be moved forward.
- Switching test mode off, by the toggle or at the end of its season, shall delete every driver created by test mode upon the server, across every division, and shall delete the saved state as set out under Saving a state and returning to it.
    - The history entries of a driver so deleted shall be kept, identified by the driver's identifier, whether or not the driver had raced. A driver created by test mode in a later season under the same identifier shall hold that history as its own.

### Fake drivers and rosters
- A driver created by test mode shall be seated directly into a team of a division, bypassing the signup entirely, while the season is in Placements, and shall be indistinguishable thereafter from one added any other way.
- The identifiers of such drivers shall be drawn from a range above any identifier a real Discord account can hold.
- A driver created by test mode shall never be granted or revoked a Discord role.
- The commands adding, removing and clearing drivers created by test mode shall be refused in any state but Placements.
- A command shall take a whole roster at once, as comma-separated values written one driver to a line, stating the driver's identifier, name, team, division and nationality. A header line naming the columns shall be accepted and ignored.
    - The identifier stated for a driver shall be the identifier the driver is created with. The roster is authoritative, the files generated beside it naming its drivers by those identifiers.
    - An identifier below the range reserved for test drivers shall be refused.
    - Two drivers sharing an identifier, and two sharing a name, shall each be refused.
    - The nationality of a driver may be omitted, and is otherwise validated as the command adding one driver validates it.
    - A division named by the roster which already holds drivers shall be refused, and only that division; the remaining divisions of the roster may still be imported.
    - The whole import shall be refused where any driver of it fails any validation, and every fault shall be named at once. Nothing shall be seated in that case.

### Saving a state and returning to it
- Four commands shall be available for saving the state of the bot and returning to it: one saving, one locking what was saved, one reporting what is saved, and one restoring it. They shall be subcommands of the test mode commands, that being the only circumstance in which they run.
- Every one of them shall be refused unless the server is in test mode, and unless the member holds the league admin role.
- Saving shall copy both the league database and the database of the scheduler, so that the jobs of a season are restored beside the season itself.
- Saving shall replace whatever was saved before, save where the saved state has been locked.
- The saved state shall be deleted when test mode is switched off by the toggle, and when the season it was taken for is completed. A locked state shall be deleted with it, the lock refusing a save rather than outliving the run it was taken in. A season cancelled or aborted shall leave the saved state as it stands, being a season abandoned rather than run to its end.
- The lock shall be set and unset by the same command. A state locked shall refuse to be overwritten by a save, and the lock shall record the member who set it and the moment they did.
- Restoring shall be confirmed before anything is done, and shall be confirmed by the member who commanded it and by no other.
- Restoring shall refuse a saved state that cannot be read as a database, and shall refuse before anything of the live state is disturbed.
- Restoring shall keep a copy of the state it replaces, so that a restore nobody wanted may be walked back.
- Restoring shall not replace the databases while the bot runs. It shall prepare the replacement, and the replacement shall be made when the bot next starts, before any part of the bot has opened either database. The manager shall be told that a restart is required.
- A state restored shall carry the test mode flag it was saved with.

### Saving a season before its placements are confirmed
- Where the server is in test mode, the first confirmation of a season's placements shall ask whether the databases are to be saved before it commits anything. The question shall be put after every validation of the season has passed and before the first thing is written.
- The question shall not be put where the server is not in test mode.
- The question shall offer three answers: to save and confirm, to confirm without saving, and to abandon the confirmation.
- The question shall be given what remains of the validity of the review, and not a validity of its own. A review left unanswered at this question shall expire exactly when it would otherwise have expired, and the placements shall not be confirmed.
- A saved state that cannot be taken shall not refuse the season. The manager shall be told that it was not taken and the confirmation shall continue.
