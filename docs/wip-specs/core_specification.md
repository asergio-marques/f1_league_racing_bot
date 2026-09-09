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
- Module: an optional part of the bot, switched on by a server administrator.
- League manager: a member holding the interaction role, who commands the bot in the interaction channel.

## The league on a server

### Setting the bot up
- A single initialisation command shall establish the bot upon a server, taking three settings at once: the interaction role, the interaction channel and the log channel.
    - The interaction role is the role a member shall hold to command the bot at all.
    - The interaction channel is the only channel in which the bot accepts commands.
    - The log channel is where the bot records what it did, what it could not find, and why something fell back.
- The initialisation command shall run once. A second run shall be refused rather than overwrite what stands, and shall name the commands that change a single setting.
- Each of the three settings shall have a command changing that setting alone.
- The initialisation command and the three single-setting commands are a league admin's, and shall run from any channel, holding the interaction role being no part of it. They are what repairs the three settings, and a deleted channel or a withdrawn role would otherwise be unrepairable.
- Initialisation shall create the Reserve team where the server holds no team. No other team shall be created.

### Who may do what
- A driver shall need no role and no channel. A driver reaches the bot through the buttons it posts and through their own channels.
- Two tiers of authority shall govern every command, and every command shall sit in one of them and no other.
    - **A league admin** shall hold the server's administrator permission. A league admin governs what the bot is upon the server, and everything that may undo a league entire: initialising the bot and repairing its three settings, enabling and disabling a module, starting over, and every command of test mode.
        - A command destroying what a league is built from shall be a league admin's, where nothing puts it back: deleting or cancelling a division, a round or a season, completing a season, removing a team, sacking a driver, and deleting the bot's own messages. A command whose undoing is another command — closing a signup window that may be opened again, unassigning a driver who may be assigned again — is a league manager's.
    - **A league manager** shall hold the interaction role and shall command the bot in the interaction channel. A league manager runs the league: its seasons, divisions, rounds, tracks, teams, drivers and seats; the configuration of every module and the templates and artwork it draws from; the channels each division posts to; and the results, standings, verdicts, check-ins and signups that follow.
- Where this specification does not state a tier, the command is a league manager's.
- The administrator permission shall carry the league manager's tier within it. A member holding it shall command the bot without holding the interaction role, a league admin being able to do everything a league manager may.
- A league admin's command shall be given in the interaction channel, save the initialisation command and the three single-setting commands. Those alone repair the settings the channel itself depends upon, and shall run from any channel.
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
- Every module shall be disabled upon a server until a server administrator enables it. A bot with no module enabled holds a calendar and nothing more.
- The rules governing a module — what enabling it requires, what disabling it clears, and what it depends upon — belong to that module's specification.
- A disabled module shall produce nothing. While a module is disabled the bot shall neither compute, record nor post any of that module's output, whatever the path arrives at it — a scheduled job, a restart, or a command that amends work arranged while the module was still enabled.
    - Nothing done while a module was disabled shall be recorded as that module's work, so that enabling the module later does not find its work already done.
- Which modules are enabled shall be displayed in the season review.
- Seasons, divisions, rounds, tracks, teams and drivers are foundational and shall not be disabled.
- Stewarding, statistics and help are recorded as intended modules and are not built.

### Starting over
- A reset command shall delete a server's league data entire — its seasons, divisions, rounds, sessions, weather results, teams of a season, seats, driver placements and the record of changes — and shall cancel every piece of scheduled work.
    - It shall require the word `CONFIRM` to be typed exactly.
    - A fuller form shall additionally clear the server's three settings, so that the bot may be initialised again.
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
- A season shall stand in one of four states: setup while it is being built, active while it is being raced, completed once it has been ended, and cancelled where it was abandoned.
- A server shall hold at most one season in setup or active at any moment. A new season cannot be built until the standing one is completed or cancelled; completed and cancelled seasons never stand in the way.
- A season shall carry a number, assigned by the bot as one higher than the count of seasons that have left setup. That number shall be the one displayed in all bot output.
- A season shall carry the edition of the game it is raced on.
- A completed or cancelled season shall be immutable. Every command that would change one shall be refused.
- No season shall end of its own accord. A league manager shall complete it.

### Building a season
- A season shall be begun by a setup command naming the edition of the game.
- While a season is in setup, its divisions and rounds may be added, amended and deleted freely, and the teams of its divisions may be changed.
- Once a season is active, nothing shall be added and nothing deleted. Rounds and divisions may only be amended and cancelled.

### Reviewing a season
- The season review shall be run by any holder of the interaction role. Reading what a season is configured to be is not an administrative act.
- The review shall post its report publicly, as one message per subsection and not as one message carrying them all. The subsections are, in this order: the season and the modules enabled upon it; the signup configuration; the attendance configuration; the points configurations; the weather configuration; and the image outputs. The blocks describing each division follow them.
- A subsection holding nothing shall not be posted.
- Each subsection shall further be divided across as many messages as its own length requires.
- The validations that belong to the season rather than to a module shall be posted with the first subsection, whatever modules are enabled.
- Each division's block shall state its role and every channel configured for it, and shall show its calendar and its lineup as the league will actually receive them.
- The report shall state how many drivers are not yet placed in a division.
- The report shall end with the question approving the season.

### Approving a season
- No command shall approve a season. A season shall be approved by the button the season review posts, and by no other means.
- The button shall be carried by a message of its own, asking whether the season configuration is accepted and naming both the member who ran the review and who may answer it.
- That message shall be posted publicly, and not to the reviewer alone. A reviewer who may not approve is thereby able to put the question to a member who may.
- The button shall be pressed only by the member who ran the review, or by a server administrator. A press by any other member shall be refused, shall say who may approve, and shall approve nothing; the refusal is seen by the presser alone.
- Who is pressing shall be the first thing the button settles, before the state of the season is read.
- The button shall carry no other action. The season is amended by the commands that amend it and reviewed again.
- The button shall stand for five minutes from the posting of the review that carries it. Upon their passing its message shall be deleted, and a notice posted in its place naming the reviewer, saying that the review has expired and that it must be run again.
- A review standing when the bot stops shall be treated as expired when the bot next starts.
- A season approved shall have the review it was approved from deleted, the question and every message of the report alike. A review expired shall have its report deleted on the same terms.
- The button shall be withheld altogether where the review found something that would prevent the season being raced as configured.

#### The evidence a season is approved upon
- The season review shall record the state of the season at the moment its report is posted, over the whole of what that report describes: the season, its divisions, its rounds, its teams and seats, its seated drivers, its channels, the modules enabled upon it, its points configurations, the configuration of each module, and the template and artwork files its graphics are drawn from.
- The button shall refuse where that state has changed since the report was posted, shall name the parts of it that changed, and shall approve nothing. The report read is the report approved.
- The state shall be compared before the season is validated and before anything is drawn, and after the member pressing has been found entitled to approve.
- A review refused upon a changed state shall end as an expired one does.
- The approval shall not draw the graphics of the season. The review draws them, and withholds its own button where one will not draw.

#### What approval requires
- The tiers of a season's divisions shall form a sequence from 1 with no gaps.
- Every division shall hold at least one round.
- No two rounds of one division shall be scheduled at the same moment.
- Every team name shall be usable as the filename of that team's artwork, whether or not the image module is enabled.
- Each enabled module shall impose its own requirements, stated in its own specification.
- A season failing any requirement shall be refused with nothing committed, and every fault shall be named.

#### What approval does
- The sessions of every round shall be created.
- The season's scheduled work shall be armed before the season's state is changed, so that a failure to arm it leaves the season in setup.
- The points configurations attached to the season shall be recorded upon it as they stand.
- Every placed driver shall be granted their division's role and their team's role.
- Each division's lineup, calendar and opening classification shall be posted. A posting that fails shall be reported and shall not refuse the season.

### Amending an active season
- Once a season is active nothing shall be added to it and nothing deleted from it. A round may be amended or cancelled and a division may be cancelled, as set out under Rounds and Divisions below.
- A cancellation is irreversible.

### Ending a season
- Completing a season shall post each division's final classification, shall record a history entry for every placed driver, shall revoke the division, team and signup roles from them, and shall mark the season completed.
- Cancelling a season shall require the word `CONFIRM`, shall post a notice to each division still running, shall cancel every piece of scheduled work, and shall revoke the same roles. A season is cancelled where it should never have existed; a season that was raced is completed.

### The archive
- A completed season and everything belonging to it shall be retained permanently and shall never be changed or deleted: its divisions, its rounds and their amendments, its weather, its results, its standings, its placements, its points configurations and its record of changes.
- The archive shall be the source from which season history and statistics are drawn.
- Each driver placed in a completed season shall gain a history entry stating the season's number, the division's name and tier, and the driver's final position, final points and gap to the winner of that division.

## Divisions
- A division shall be created during setup, taking a name, a role and a tier.
    - A division's name shall be unique within its season, without regard to case.
    - A division's tier shall be unique within its season and shall be no lower than 1.
- The tiers of a season's divisions shall form a sequence from 1 with no gaps, or approval shall be refused. Divisions shall be held and displayed in ascending order of tier, tier 1 being the highest.
- A division's tier may be used to identify it in a command, but its name shall be what the bot displays.
- A division may be created by duplicating another, taking a new name, role and tier and an offset in days and hours. Every round of the source division shall be copied with its moment shifted by that offset and the copies renumbered.
    - A duplication producing a round in the past shall warn and shall not be refused.
    - A division created by duplication shall inherit none of the source division's channels.
- A division may be renamed, and its name, tier and role amended, during setup alone.
- A division deleted during setup shall take with it its rounds, its sessions, its teams and seats, and the placements made in it.
- A division of an active season may be cancelled, upon the word `CONFIRM`. Its rounds shall be unscheduled, a notice shall be posted to it, and it shall thereafter be excluded from the validation of tiers, from the standings and from the end of the season.
- Every division shall carry a role, which the bot mentions when it posts to that division.
- A division's calendar may be reposted on demand. A calendar already posted shall not update itself.

## Rounds
- A round shall belong to one division and shall state a moment in UTC, a format and a track.
- Round numbers shall never be entered. The rounds of a division shall be ordered by their moment and numbered from 1, and adding, deleting or re-timing one shall renumber the division.
- Two rounds of one division shall not be scheduled at the same moment. This shall be refused by every command that would create one, and again at approval.
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
- The sessions of every round shall be created when the season is approved.

### Mystery rounds
- A mystery round shall name no track. A round of any other format shall name one the bot can resolve.
- A mystery round shall hold no sessions.
- A mystery round shall not be offered as the next round of a division.
- What a mystery round receives in place of a forecast is specified by [the weather module](weather_module_specification.md).

### Amending and cancelling a round
- A round of an active season may have its track, its moment or its format amended, behind a confirmation. The amendment shall be recorded, the weather already drawn for the round invalidated, its scheduled work re-armed, a notice posted, and every phase whose horizon has passed run again.
- A round may be cancelled upon the word `CONFIRM`. Its scheduled work shall be cancelled and a notice posted.
- A round shall not be cancelled while a results submission for it stands open, nor where any result has been recorded against it.

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
- Adding, renaming or removing a team shall change the server's list and, where a season stands in setup, every division of that season. It shall be refused once the season is active.
- A team name shall reduce to a usable filename:
    - It shall not be empty and shall hold at least one letter or digit.
    - It shall not reduce to `reserve`.
    - It shall not reduce to the same form as another team's name in its scope — the server for the server's list, the division for the teams of a season.
    - This shall bind whether or not the image module is enabled, the reduced name being the filename under which every graphic seeks that team's artwork.
    - Only the new name shall be validated when a team is renamed, so that a team named before this rule may still be corrected.
    - A season already approved shall not be validated against this rule again.
- Every team other than Reserve shall hold two seats.
- Teams shall be ordered as they were added and not alphabetically, so that adding or renaming one never moves those already drawn.
- The season review shall display every team, the drivers seated in each, and every driver not yet placed.

## Drivers

### The driver profile
- A driver profile shall be held at server scope and shall belong to one Discord account. A person shall hold at most one profile upon a server.
- A profile shall carry the driver's state, a former-driver flag, and the counts of race, season and league bans they have taken.
- A profile shall carry, for each division the driver currently races in, that division's name and tier alongside the driver's standing in it: their position, their points, and their gap to the leader of that division.
- A profile shall carry, for each division the driver has raced in before, that division's name and tier alongside the season's number and the driver's final position, final points and gap to the winner of that division.
- A member holding no profile shall be treated as Not Signed Up.
- A profile shall be retained where the member leaves the Discord server.
- Every change of a driver's state shall be persisted.

### The states of a driver
- A driver shall stand in one of nine states:
    - Not Signed Up: inactive, and able to begin a signup.
    - Pending Signup Completion: working through their signup.
    - Pending Admin Approval: their signup awaits a league manager.
    - Awaiting Correction Parameter: a league manager has asked for changes and is choosing which answer is to be given again.
    - Pending Driver Correction: the driver has been asked to amend a named answer.
    - Unassigned: approved, and not yet placed in a division and team.
    - Assigned: placed in at least one team.
    - Season Banned: inactive, and unable to begin a signup for the length of the season they were banned for.
    - League Banned: inactive, and unable to begin a signup indefinitely.
- The permitted transitions shall be:
    - Not Signed Up to Pending Signup Completion.
    - Pending Signup Completion to Pending Admin Approval, or to Not Signed Up.
    - Pending Admin Approval to Awaiting Correction Parameter, to Unassigned, or to Not Signed Up.
    - Awaiting Correction Parameter to Pending Driver Correction, back to Pending Admin Approval where no answer is named in time, or to Not Signed Up.
    - Pending Driver Correction to Pending Admin Approval, or to Not Signed Up.
    - Unassigned to Assigned, or to Not Signed Up.
    - Assigned to Unassigned, or to Not Signed Up.
    - Pending Admin Approval, Pending Driver Correction, Unassigned and Assigned to Season Banned or to League Banned. A driver still working through their signup, or awaiting the naming of an answer to correct, shall not be banned from where they stand.
    - Season Banned to League Banned.
    - Season Banned to Not Signed Up, and League Banned to Not Signed Up.
    - Not Signed Up to Unassigned, and Not Signed Up to Assigned, under test mode alone.

### Leaving the league
- The former-driver flag shall be false by default and shall be set once a driver has raced a round. A driver so marked shall not be deleted, only amended.
- A driver returning to Not Signed Up shall be deleted where the flag is false, and shall be retained with their personal details cleared where it is true, so that the results they raced for remain attributed.
- A driver may be sacked from Unassigned or Assigned. Sacking shall free every seat they hold, revoke every division, team and signup role, and return them to Not Signed Up.

### Placement into a division and team
- A command shall place a driver, taking the driver, a division named by its tier or by its name, and a team of that division.
    - The driver shall be Unassigned or Assigned.
    - The driver shall hold at most one seat in any one division, and may hold a seat in more than one division.
    - The team shall have a seat free. The Reserve team shall always have one.
    - A season shall stand in setup or active. Placement shall not require the season to be active.
    - A driver who was Unassigned shall become Assigned.
    - Where the season is active the division's role and the team's role shall be granted at once. Where it is in setup no role shall be granted until the season is approved.
- A placement shall be refused where it would carry a division beyond what its lineup, its attendance sheet or its standings graphic can draw, and nothing shall be changed by the refusal.
- A command shall remove a driver from a division.
    - The driver shall be Assigned and shall hold a seat in the division named.
    - A driver holding no other placement shall return to Unassigned.
    - The division's role shall be revoked. The team's role shall be revoked only where the driver holds no other seat, across all divisions, mapping to that role.
- Every successful placement, removal and sacking shall cause the division's lineup to be deleted and posted again in its lineup channel.

### Changing the account behind a profile
- A server administrator shall be able to re-key a driver profile onto another Discord account, so that a person changing account keeps their history.
- The new account shall be accepted whether or not it is still a member of the server.
- A profile shall not be re-keyed onto an account that already holds one.

## When the bot stops
- The bot is a program somebody shall keep running. While it is stopped nothing happens.
- When it starts again it shall recover: the weather phases that came due, where the weather module is enabled; the check-in calls and deadlines that came due; a signup window's closing timer, closing the window at once where its moment has passed; interrupted result submissions, which shall be cleared and reopened with the league manager told to submit again; penalty and appeal reviews, which shall be posted again rather than discarded; abandoned amendment channels, which shall be deleted; season reviews left standing, which shall be expired; and seasons left part-built.
- The end of a season shall not be recovered. A league manager shall complete it.
- Anything else that came due while the bot was stopped is missed.
- A message the bot failed to post shall be retried until it is delivered, shall survive a restart, and its eventual delivery shall be recorded in the log channel. A message still undelivered after about an hour shall be reported there.

## Test mode
Test mode exists because the bot is almost entirely time-driven, and a season's behaviour
cannot be observed by waiting for it. [Testing with test mode](../how-to/test-mode.md) describes how to use it; this
section states the rules it holds to.

### What test mode is
- Test mode shall be a state of the server, persisted and surviving a restart, switched by a toggle asking what configuring a league asks.
- Test mode shall provide a command firing the next scheduled event at once, in the order the events would have fired, without altering the moment any of them was scheduled for.
- Test mode shall provide a command reporting, for every round, which of its scheduled work has run and which remains.
- Test mode shall provide synthetic drivers, so that a division may be filled and raced without real Discord accounts.
- While test mode is enabled, a switch of its own shall stand in for the signup module's nationality setting, so that both may be seen without altering what a league's real signups ask.
- While test mode is enabled, a server administrator shall be able to set a driver's former-driver flag by hand, to true or to false. This shall be possible in no other circumstance.
- Every test mode command but the toggle shall re-read the state at the moment it is given and shall be refused while test mode is off.

### Entering and leaving it
- A server shall be either running a real league or under test, never both.
- Test mode shall not be enabled while the server holds a real driver whose state is anything other than Not Signed Up. A driver profile retained at Not Signed Up is a former driver and shall not stand in the way.
    - The refusal shall name how many real drivers the server holds, and the state of test mode shall be left unchanged.
- Test mode shall not be enabled while the signup window is open. The refusal shall direct the administrator to close the window, and the window shall be left open — enabling test mode shall not close it.
- Test mode shall not be disabled while a season that has started holds a driver created by test mode. Such a season shall hold test mode open until it is completed, and the refusal shall direct the administrator to complete it.
    - Neither a season yet to start nor a completed one shall stand in the way.
- While test mode is enabled:
    - A real driver shall not begin a signup. The sign-up button shall refuse them, and the command opening a signup window shall be refused.
    - A real driver shall not be placed in a team. A driver created by test mode shall still be placed freely.
- Enabling test mode shall create and attach two ordinary points configurations, "Standard" and "Half Points", to a season standing in setup or active, unless a configuration of that name is already attached to it. They shall be created as ordinary configurations of the server and shall be indistinguishable from ones a league made itself, so that a test season passes the points requirement of approval without one being built by hand.
- Disabling test mode shall delete every driver created by test mode upon the server, across every division.

### Fake drivers and rosters
- A driver created by test mode shall be seated directly into a team of a division, bypassing the signup entirely, and shall be indistinguishable thereafter from one added any other way.
- The identifiers of such drivers shall be drawn from a range above any identifier a real Discord account can hold.
- A driver created by test mode shall never be granted or revoked a Discord role.
- A command shall take a whole roster at once, as comma-separated values written one driver to a line, stating the driver's identifier, name, team, division and nationality. A header line naming the columns shall be accepted and ignored.
    - The identifier stated for a driver shall be the identifier the driver is created with. The roster is authoritative, the files generated beside it naming its drivers by those identifiers.
    - An identifier below the range reserved for test drivers shall be refused.
    - Two drivers sharing an identifier, and two sharing a name, shall each be refused.
    - The nationality of a driver may be omitted, and is otherwise validated as the command adding one driver validates it.
    - A division named by the roster which already holds drivers shall be refused, and only that division; the remaining divisions of the roster may still be imported.
    - The whole import shall be refused where any driver of it fails any validation, and every fault shall be named at once. Nothing shall be seated in that case.

### Saving a state and returning to it
- Four commands shall be available for saving the state of the bot and returning to it: one saving, one locking what was saved, one reporting what is saved, and one restoring it. They shall be subcommands of the test mode commands, that being the only circumstance in which they run.
- Every one of them shall be refused unless the server is in test mode, and unless the member holds the administrator permission of the server.
- Saving shall copy both the league database and the database of the scheduler, so that the jobs of a season are restored beside the season itself.
- Saving shall replace whatever was saved before, save where the saved state has been locked.
- The lock shall be set and unset by the same command. A state locked shall refuse to be overwritten by a save, and the lock shall record the member who set it and the moment they did.
- Restoring shall be confirmed before anything is done, and shall be confirmed by the member who commanded it and by no other.
- Restoring shall refuse a saved state that cannot be read as a database, and shall refuse before anything of the live state is disturbed.
- Restoring shall keep a copy of the state it replaces, so that a restore nobody wanted may be walked back.
- Restoring shall not replace the databases while the bot runs. It shall prepare the replacement, and the replacement shall be made when the bot next starts, before any part of the bot has opened either database. The manager shall be told that a restart is required.
- A state restored shall carry the test mode flag it was saved with.

### Saving a season before it is approved
- Where the server is in test mode, the approval of a season shall ask whether the databases are to be saved before it commits anything. The question shall be put after every validation of the season has passed and before the first thing is written.
- The question shall not be put where the server is not in test mode.
- The question shall offer three answers: to save and approve, to approve without saving, and to abandon the approval.
- The question shall be given what remains of the validity of the review, and not a validity of its own. A review left unanswered at this question shall expire exactly when it would otherwise have expired, and the season shall not be approved.
- A saved state that cannot be taken shall not refuse the season. The manager shall be told that it was not taken and the approval shall continue.
