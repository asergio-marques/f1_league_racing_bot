# Attendance module
- <COMMAND CHANGE> The attendance module may be enabled via a "module enable" command akin to other modules. May only be used by league admins.
- <COMMAND CHANGE> The attendance module may be disabled via a "module disable" command akin to other modules. May only be used by league admins.
- The attendance module is disabled by default.
- The attendance module may not be enabled once the season's placements have been confirmed.
- Due to being dependent on the results module, the attendance module cannot be enabled while the results & standings module is disabled.
- If the results & standings module is disabled, then the attendance module shall be disabled as well. Where the attendance module is enabled, disabling the results & standings module shall first warn the league that attendance will go with it and what that costs, and shall write nothing until the league confirms; the reply that follows shall name both modules as disabled. Where the attendance module is already disabled, no warning of the cascade shall be given — the results & standings module's own specification governs what it warns of in its own right.
- Disabling the attendance module shall stop every check-in call, reminder, deadline and reserve distribution still to come, for the remainder of the season, however that work is reached — a scheduled job, a restart, a button on a check-in call already posted, or a test mode command.
    - A check-in call already posted shall not be withdrawn, but its buttons shall record no further answer.
    - The season's scheduled work shall not be destroyed by the disabling, so that nothing is lost that only confirming a season's placements could create again.
- Attendance module activation status shall be displayed in the configuration review and the placements review.
- This module must work with the fake driver rosters used in test mode.

## Concepts
- RSVP or check-in: Confirmation of round attendance to all members of a division in a configured channel to mark their presence or absence.
- Attendance points: Points gained upon failing to RSVP or showing up for a round.

## Configuring the attendance module
### Channels
- <NEW COMMAND> A "division rsvp-channel" command will be made available to league managers, which shall have as input a division name and a channel on which RSVP polls shall be posted by the bot.
    - If a RSVP channel is not configured for a division in the placements review, then the season will fail validation.
    - Each division's RSVP channel will be displayed in the placements review much alike other division channels like results, standings, weather, etc.
- <NEW COMMAND> A "division attendance-channel" command will be made available to league managers, which shall have as input a division name and a channel on which attendance for each one of the rounds will be posted by the bot.
    - If an attendance channel is not configured for a division in the placements review, then the season will fail validation.
    - Each division's attendance channel will be displayed in the placements review much alike other division channels like results, standings, weather, etc.

### RSVP notices
- <NEW COMMAND> An "attendance config rsvp-notice" command will be made available to league managers, which shall have as input an integer standing for a number of days. This command configures the number of days before a round at which point RSVP notices will be sent out too all drivers of a division to mark their attendance for that round.
    - By default, this value will be set to 5.
- <NEW COMMAND> An "attendance config rsvp-last-notice" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours before a round at which point the bot will notify users who have not RSVP'd until then. A value of 0 means that no "last notice" announcement will be sent out to users who have no RSVP'd.
    - By default, this value will be set to 24.
- <NEW COMMAND> An "attendance config rsvp-deadline" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours before a round at which point users can no longer alter their RSVP status. A value of 0 means that the deadline lasts up until the scheduled time of the round, which no alterations permitted beyond that point.
    - By default, this value will be set to 2.
- The input from all commands shall be validated against the current settings so that the RSVP Deadline always happens after the RSVP Notice and the RSVP Last Notice, and the RSVP Last Notice always happens after RSVP Notice. Ergo, the configuration shall follow the rule Notice\*24 > LastNotice\*24 > Deadline.
- If the season's placements have been confirmed, all three commands must be rejected.
- A season holding a round whose RSVP Notice, RSVP Last Notice or RSVP Deadline has already passed shall fail validation. The placements review shall report it and shall withhold the button confirming placements; the confirmation shall refuse it again, with nothing committed.
    - The report shall name the latest offending round of a division and the earliest-due of that round's elapsed windows, and shall say when it was due. It shall not name every offending round, nor every window of the round it names: the round named bounds the division's calendar, and the window named bounds how far that round must move.
    - Both shall read one and the same evaluation, so that the review and the confirmation cannot disagree. The confirmation shall evaluate it afresh rather than trust the review, a round being able to cross a window while the review stands.
    - A window falling exactly at the moment of confirmation counts as having passed.
    - A cancelled round shall not be considered, holding no work to lose.
    - The league's remedy is to reschedule the round or to shorten the window, both being decisions only the league can make. The bot shall not post the notice late, nor confirm the placements without it.

### Attendance points
- <NEW COMMAND> An "attendance config no-rsvp-penalty" command will be made available to league managers, which shall have as input an integer standing for the number of attendance points gained upon failing to RSVP up for a round.
    - By default, this value will be 1.
- <NEW COMMAND> An "attendance config absent-penalty" command will be made available to league managers, which shall have as input an integer standing for the number of attendance points gained upon failing to show up for a round without having accepted the check-in. A driver who accepted and then did not show pays the no-show penalty below instead, never this one.
    - By default, this value will be 1.
- <NEW COMMAND> An "attendance config no-show-penalty" command will be made available to league managers, which shall have as input an integer standing for the number of attendance points gained upon failing to show up for a round after having accepted the check-in.
    - By default, this value will be 1.
- <NEW COMMAND> An "attendance config autosack" command will be made available to league managers, which shall have as input an integer standing for the number of attendance points upon which a driver will be automatically sacked from all team seats. A value of 0 means that the autosack functionality is disabled.
    - Autosack shall remove a driver from every seat they hold in every division, whichever division's points carried them over the threshold. A league wanting a driver dropped to reserve only in the division where they missed rounds shall use autoreserve.
    - The reply to setting a threshold other than 0 shall state that autosack removes a driver from every seat in every division, and shall name autoreserve as the setting that acts upon one division alone.
    - It is a league manager's though sacking a driver by command is a league admin's, the setting being the configuration of a module and undone by setting it back. What follows from it is a sanction the league has published in advance and which a driver earns by their own absences, and not a command destroying what a league is built from.
    - By default, this value will be false (disabled).
- <NEW COMMAND> An "attendance config autoreserve" command will be made available to league managers, which shall have as input an integer standing for the number of attendance points upon which a driver will be unassigned from their current seat and assigned to the reserve team of the same division. A value of 0 means that the autoreserve functionality is disabled.
    - By default, this value will be false (disabled).
    - The autoreserve functionality is only applied to drivers not in the reserve team.

## RSVPing
- A driver whose placement is not yet committed shall receive no check-in call, shall not be listed upon one, shall not be distributed as a reserve, and shall accrue no attendance points.
- Days before a round is scheduled to happen, the exact number of which configured via the "attendance config rsvp-notice", the bot shall post an announcement via an embed, in the configured RSVP channel for the division of the round, asking drivers if they are attending the round.
    - The embed shall be titled "Season <X> Round <X> - <Grand Prix Name of track>
    - The text of the embed shall contain:
        - Time: <Datetime of the event as a dynamic discord timestamp>
        - Location: <Location of the circuit where the event is configured to take place at> - Mystery if event type is Mystery
        - Event type: Normal/Sprint/Mystery/Endurance
        - A mini-list for each one of the teams in the division (plus reserves), containing the display name of the drivers in that team plus an indicator of their RSVP status (if the driver has not checked-in, then it will be just "()").
    - Three buttons distributed horizontally will be placed below the embedded:
        - "Accept" with the green checkmark emoji
        - "Tentative" with default background and the white question mark emoji
        - "Decline" with the red cross mark emoji
- When a driver picks any of the three options above, the RSVP status indicator in the embed shall change:
    - Green checkmark emoji within the brackets if accepted (will race)
    - White question mark emoji within the brackets if tentative (uncertain)
    - Red cross mark emoji within the brackets if declined (won't show up)
- Only a driver holding a confirmed placement in the division of the round may answer its check-in call. Anybody else pressing one of the buttons shall be told they are not a member of the division and nothing shall be recorded, whether they hold an unconfirmed placement, a seat in another division, or no driver profile at all.
    - Who may see the channel a call is posted in is the league's own to set and the bot shall not manage it.
- A driver of the division may answer a check-in call standing for one of its rounds whether or not they were of the division when it was posted, and their answer shall be recorded as any other driver's is. A driver assigned, moved or confirmed into a division after its call has gone out holds no attendance record for that round until they answer.
    - Where such a driver never answers, they shall hold no attendance record for that round at all: no failure to RSVP is recorded against them, they accrue no penalty for the round, they count towards neither sanction threshold, and they do not appear upon its attendance sheet. This is deliberate, and it is the fair outcome — the call went out before they were of the division, so they may never have been asked.
    - They shall nonetheless be listed upon the standing call itself, showing no answer, since the call is drawn from the division's roster as it stands rather than from the answers recorded. A driver shown upon a call with no attendance record behind them is therefore expected, not a fault.
- Full-time drivers will be allowed to change their chosen option until the RSVP deadline is met. After that point, the choices are locked.
- Reserve drivers will be allowed to change their chosen option until the time of the round, provided they have NOT accepted the check-in. After that point, the choices are locked.
- A driver shall never be told their answer was recorded where it was not.
- RSVP status shall be persisted under the round data entries in the database, as they will be necessary later.

### Distribution of reserves
- Once the RSVP deadline is reached, reserves that have confirmed their presence with "accepted" will be distributed by teams according to the following priority:
    1. Teams with no full-time drivers seated at all;
    2. Teams in which drivers have declined the check-in;
    3. Teams in which drivers have failed to RSVP;
    4. Teams with a physically vacant seat, having some full-time drivers seated;
    5. Teams that have already received a reserve for this round. A team at priorities 1 to 4 shall drop to this priority once it receives its first reserve, so that no team receives a second reserve while another team still needs one;
    6. Teams in which drivers have marked themselves as tentative.
- Teams whose full-time drivers have all accepted and whose seats are all filled shall not be candidates.
- If there are two or more teams that fit the same priority, the following tie-breakers will be used, in order:
    - Lowest positioned team in the Constructors' Championship of that division. Teams with no standings snapshot yet, such as before the first round of a season, shall be placed after every ranked team.
    - Alphabetical order of team name.
- Reserves shall be picked according to the time they confirmed their attendance; first ones to accept the check-in shall be the first to be placed in a team.
    - Every time a reserve changes RSVP status to accepted, the time will be updated. So flip-flopping on attendance is bad.
- After distribution of reserves, any reserves that have confirmed attendance but remain without a seat for the round will be considered as being "on standby".
- After standbys are determined, the post shall post a message on the check-in channel of the division mentioning the Discord users and informing them of the team they are racing for. The standby reserve drivers shall also be informed of their standby status, to be ready to jump into the race in case someone no-shows.

## Changes to a round
- Where a round is amended, the check-in work already armed for it shall be cancelled and armed again against the round's new moment, and only while the module is enabled. Nothing else arms it: a round amended and not armed again asks nobody whether they are racing, opens no attendance records, distributes no reserves, charges nobody, and is recorded afterwards as perfect attendance for the whole division.
- The call, the last notice and the deadline shall each be judged by one question: would it have been posted already, were the round always to have stood at its new moment?
    - Where it would not, it shall be armed for its new moment.
    - Where it would, a moment already past shall not be honoured retroactively and nothing shall be posted in its place.
- The check-in call already posted for an amended round shall be treated as follows.
    - Where its window would have passed under the round's new moment and the deadline has not, the call shall be posted again carrying what changed. The call names the circuit, the sessions and the moment, and an amendment may have changed all three under it.
    - Where its window is ahead again, the call standing shall be taken down and the armed call shall post afresh at its new moment.
    - Where the deadline computed from the round's new moment has passed, nothing shall be posted and nothing taken down.
- Where the call is posted again, every answer already recorded shall be carried over and drivers may change them as usual until the deadline. A driver of the division holding no recorded answer shall be recorded as not having answered, and an answer recorded for a driver no longer of the division shall be discarded.
- The last notice and the distribution announcement posted for the round shall be taken down with the call they belong to, so that a division is never left reading a reminder or a distribution for a round that has since changed.
- Where a round, a division or a season is cancelled, the bot shall post to the division's check-in channel a notice that the round, the division or the season has been cancelled and that there is no check-in to answer for it. The notice shall mention the division's role, as the check-in call does: it is the one notification a cancellation carries, the weather and results modules posting only silent notes and core posting nothing. It shall be posted only while the module is enabled. Decided 2026-09-19 (#175).
    - The check-in call posted for each round the cancellation calls off shall then be taken down, together with its last notice and its distribution announcement, whether or not the notice could be posted. The answers recorded for the round shall be kept. A message the bot cannot delete shall be named to the league admin, to be removed by hand.
    - An answer given on the call of a cancelled round shall be refused and not recorded.
    - Before the call is taken down, the check-in of each such round shall be written to the log channel with the cancellation: every driver it recorded, grouped by their answer, and, where the reserves have been distributed, each reserve with the team they were sent to or their standby. A round whose call was never posted is not written.
    - Both shall happen only while the module is enabled. Decided 2026-09-19.

## Attendance
- Once the initial round results are submitted, the attendance sheet of the round will be filled. Being listed in any of the sessions of the round will be enough to count as having attended.
- A driver recorded as having attended a round shall not be recorded absent again by any later recording from that round's results. The record errs in the driver's favour, they having had no opportunity to justify themselves.
    - Two things rebuild a round from its results outright, and may set a recorded attendance back: the "attendance sync" command, and the amendment of the round's results. Decided 2026-09-21 (#345): an amendment corrects what the round was, and a driver it removes from the classification did not attend it.
- Drivers who are reserving for that division are ignored.
- A driver's attendance points shall be counted separately in each division they race in.
- Attendance points shall only be distributed once the post-race penalties results are finalized, to prevent erroneous automatic sackings due to omitting a driver on the results accidentally.
- Attendance points will be distributed as follows:
    - Failure to check-in, attended: no-rsvp-penalty points gained.
    - Failure to check-in, did not attend: no-rsvp-penalty + absent-penalty points gained.
    - Checked-in, attended: 0 points gained, whichever answer was given.
    - Accepted the check-in, did not attend: no-show-penalty points gained.
    - Answered tentative or declined, did not attend: absent-penalty points gained.
- A new button will be made available in the penalty wizard (NOT available in the appeal stage) for "attendance pardons". When pressed, a form shall open, requesting a discord user ID, the type of attendance penalty excused (no RSVP, absent, no show), and the justification.
    - The pardons attributed shall be validated against the check-in status (did check-in or not) and against the real attendance of the driver (current provisional round results).
    - The justification is merely for logging purposes, it shall not be displayed anywhere else but the logging channel. Privacy reasons.
    - A justification holding a role mention, "@everyone" or "@here", in any case, shall be refused, saying why, as a penalty's texts are. A mention of a driver shall stand. Decided 2026-09-21 (#204).
    - Multiple pardons may be attributed to the same driver (so that a "failure to check-in, did not attend" may be fully waived).
    - The attendance pardons shall be displayed together with the list of staged penalties.
    - The drivers who had one of their attendance penalties waived by this process shall not receive attendance points for that reason.
- After the post-race penalties are approved, attendance pardons cannot be applied.
    - **Amending a round reopens them.** Decided 2026-09-20 (#345). An amendment replays the round's report stage, where the pardons it already carries shall be shown back, each able to be kept, changed or removed, and further ones added — on the same terms as the reports beside them, and in that stage alone.
    - The round shall carry exactly the pardons that stage held when it was approved. A pardon removed in the stage shall be removed from the round.

### Updating attendance sheets
- Once the post-race penalties are approved and posted, the updated attendance total shall be posted in the configured attendance channel for the division.
    - The post will be a list of drivers in descending order from most attendance points to least, mentioning each one in the form "@user - x attendance points".
    - The end of the post will always have the following text: "Drivers who reach <attendance config autoreserve> points will be moved to reserve.\nDrivers who reach <attendance config autosack> points will be removed from all driving roles in all divisions." (the \n stands for a line break)
- The sheet of a division shall list every driver currently seated in the division, and every driver who has held a seat in it during the present season, full-time or Reserve, whether or not they still hold it. A Reserve driver shall be listed from the round they were first placed into a seat for, and shall stay listed thereafter.
- To prevent misunderstandings, once a new attendance total is posted on the channel, the message containing the previous one shall be deleted.
- Beyond the postings after each round, the sheet shall be posted on the two occasions that bracket a season:
    - Upon the season's placements being first confirmed, an **opening sheet** shall be posted, holding every driver holding a committed seat in the division upon nought attendance points. It is read from the seats of the division, the attendance record holding nothing at that moment, and is ordered alphabetically by the name of the team and alphabetically by the name of the driver within a team.
    - Upon the season completing, a **final sheet** shall be posted, holding the attendance record as it stands at the last round of the division for which results were posted.
- Neither sheet is about a round, so neither is prevented by a round recorded as cancelled. Where either is written out as text rather than drawn, it is headed by the phrase naming the occasion — "Attendance — Opening Classification" or "Attendance — Final Classification".
- The opening sheet takes the place of the previous sheet in the ordinary way, so that the sheet of the first round replaces it and the division is never left holding a stale opening sheet beside a live one. The **final sheet does not**: it is posted beside the sheet of the last round and both stand. This is the one exception to the rule above that only one attendance total stands in the channel at a time.
- The failure of either shall never prevent a season's placements from being confirmed nor the season from completing, and the failure of one division shall not prevent the others.
- The attendance sheet for a round must be recalculated in the case "round results amend" is used. The pardons the round carries after the amendment shall be taken into consideration as well.
    - The sheet shall be reposted against the round the running totals stand at — the division's latest — and not against the round amended, there being one live sheet rather than one per round.
    - Any sanction the recalculation warrants shall be enforced against that latest round and no earlier one. A correction to an earlier round shall not undo a sanction already applied nor apply one retrospectively: the past is not rewritten. Decided 2026-09-20 (#345).
    - It follows that a driver whom the correction takes past a limit is sanctioned now, at the division's latest round, and not at the round where the limit would first have been crossed. Decided 2026-09-21.
- Recalculating a round's attendance shall carry the new totals through every later round of the division whose penalties have been approved, so that no sheet later drawn against one of them — the final sheet of the season included — publishes a total the recalculation has superseded.
- The total recorded against a round shall be the driver's total **as at that round**: the points of every earlier round of the division whose penalties have been approved, and that round's own. It shall not be the season's total, so that a figure which looks wrong can be traced round by round.
- It follows that a division's current total stands at its latest such round. Where a recalculation carries totals forward, the sheet posted and the limits verified shall be those of the latest round it recalculated, not of the round it was asked to recalculate from.
- After the attendance total is posted, it shall be verified whether any driver has crossed the autoreserve limits; if they are deemed to have done so, they will be moved to the reserve team of the same division, as the command moving a driver moves them.
- After the attendance total is posted, it shall be verified whether any driver has crossed the autosack limits; if they are deemed to have done so, they will be sacked: removed from all their driving roles in all divisions, full-time or otherwise, losing their driver role (the one automatically given out when a signup is approved). The sheet of every division they held a seat in shall then be posted again.

### Sanctions that do not apply
- Every driver over a threshold shall be attempted, whatever befell the driver before them. A sanction that fails shall never stop the others, and a sanction already applied shall never be undone.
- A division with no reserve team shall be a failure of the autoreserve of each driver owed one, not a sanction silently passed over.
- Where any sanction did not apply, or applied but could not be announced, the log channel shall be told in one entry naming each driver, the sanction and the reason, ending with the "attendance sync" command that finishes the job. The league manager whose action set the sanctions off — the approval of a penalty review, or of an amendment — shall be told the same in the reply to that action, which shall no longer report a plain success.
- A run of the sanctions that cannot begin at all, the league's server being unreachable or the run failing outright, shall be reported the same way.
- **The sanctions shall not run at all upon a round whose attendance record is known to be wrong.** Where recording the round's attendance, or awarding its points, failed, the totals the thresholds are read from are unsound, and a driver could be sacked upon a number the bot has already established it cannot trust. The run shall be deferred and reported as such, and the "attendance sync" command shall both repair the record and apply whatever is then owed. A failure merely to *post* — a sheet, an announcement — does not make the record wrong and shall not defer the run.

### Resynchronising attendance
- <NEW COMMAND> An "attendance sync" command shall be made available to league managers, which shall have as input a division name and a round number.
    - It shall be available only while the season is in one of the three ongoing stages, and only for a round whose penalties have been approved. It shall be refused otherwise, with nothing changed.
    - It shall be refused, with nothing changed, where any channel the recalculation posts to cannot be reached, as the approval of an amendment is.
    - It shall be refused, with nothing changed, while a round of the division has an amendment of its results open, naming the round being amended and its channel. The recalculation would otherwise read that amendment's corrections before they are approved, and its sheet and sanctions would stand were the amendment then abandoned. Decided 2026-09-21.
    - It shall recalculate the attendance of every round of the division from the one given onwards whose penalties have been approved, from their results, as one change that lands whole or not at all. Rounds before the one given shall be left alone.
    - It shall then post the sheet of the latest of those rounds, and enforce the sanctions against that round.
    - It shall be safe to run again: a driver already sacked or already in the reserve team shall not be sanctioned a second time, so a second run applies only what the first did not.
    - The reply shall list the sanctions applied and any still not applied, and the run shall be written to the log channel.

### Posting a check-in call by hand
- <NEW COMMAND> An "attendance post-check-in" command shall be made available to league managers, which shall have as input a division name and a round number. Decided 2026-09-20 (#123).
    - It shall post the check-in call of the round given, as the scheduled call would have posted it, opening the round's attendance records with it.
    - It is a last resort, for a call the bot should have posted and did not. A round whose call never went out opens no attendance records, and is afterwards recorded as perfect attendance for the whole division; this is what allows a league to repair that. A league needing it routinely has a cause that has not been fixed.
    - It shall be available only while the season is in one of the three ongoing stages, and shall be refused otherwise, with nothing changed.
    - It shall be refused, with nothing changed, where the round is cancelled, there being no check-in to answer for it.
    - It shall be refused, with nothing changed, where a check-in call for the round is already standing. The reply shall say that amending the round is what posts a call again, that being the path which carries every answer already given across. A second call standing beside the first would divide a division's answers between two messages.
    - Where the scheduled call posts while the command is running, the command shall post nothing on top of it and shall say so. A round shall never carry two check-in calls: the second would leave the first answerable by drivers and tracked by nothing, so its answers would be lost and its buttons never closed at the deadline.
    - It shall be refused, with nothing changed, before the moment the call was due to be posted. The scheduled call is still to come, and posting earlier would override the notice period the league configured.
    - It shall be refused, with nothing changed, once the check-in deadline of the round has passed, a call posted after it being one nobody can answer. Where the deadline is set to zero, and so disabled, the moment of the round itself shall be the boundary: a call shall not be posted for a round already under way or run.
    - The reply shall say whether a call is standing once it has run, and not merely that the command was accepted; and the run shall be written to the log channel either way.
    - Where a check-in call fails to post, the log entry reporting it shall name this command, with the division and round already filled in.

## Test mode
- A "test-mode rsvp set-status" command shall be available to league managers, which will take as its parameter the name of a division (mandatory). This will serve to set the RSVP status of fake drivers in test mode.
    - The command shall require the division to belong to a season in one of the three ongoing states and to have a check-in call currently posted; it shall be refused otherwise.
    - The statuses shall be given in bulk through a modal, one entry per line in the form "<user ID>, <status>", the status being one of "accept", "tentative" or "decline".
    - A driver omitted from the entries shall keep the status they hold. No entry shall return a driver to not having checked in.
    - An entry naming a driver without a profile, or without an attendance record for the round, or carrying a status that cannot be read, shall be reported and passed over; the remaining entries shall still be applied.
    - The check-in call shall be redrawn once after the entries are applied, and the change shall be written to the log channel.