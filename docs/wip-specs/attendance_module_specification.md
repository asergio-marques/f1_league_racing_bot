# Attendance module
- <COMMAND CHANGE> The attendance module may be enabled via a "module enable" command akin to other modules. May only be used by server admins.
- <COMMAND CHANGE> The attendance module may be disabled via a "module disable" command akin to other modules. May only be used by server admins.
- The attendance module is disabled by default.
- The attendance module may not be enabled once the season is approved.
- Due to being dependent on the results module, the attendance module cannot be enabled while the results & standings module is disabled.
- If the results & standings module is disabled, then the attendance module shall be disabled as well.
- Attendance module activation status shall be displayed in the season review.
- This module must work with the fake driver rosters used in test mode.

## Concepts
- RSVP or check-in: Confirmation of round attendance to all members of a division in a configured channel to mark their presence or absence.
- Attendance points: Points gained upon failing to RSVP or showing up for a round.

## Configuring the attendance module
### Channels
- <NEW COMMAND> A "division rsvp-channel" command will be made available to league managers, which shall have as input a division name and a channel on which RSVP polls shall be posted by the bot.
    - If a RSVP channel is not configured for a division in the season review, then the season will fail validation.
    - Each division's RSVP channel will be displayed in the season review much alike other division channels like results, standings, weather, etc.
- <NEW COMMAND> A "division attendance-channel" command will be made available to league managers, which shall have as input a division name and a channel on which attendance for each one of the rounds will be posted by the bot.
    - If an attendance channel is not configured for a division in the season review, then the season will fail validation.
    - Each division's attendance channel will be displayed in the season review much alike other division channels like results, standings, weather, etc.

### RSVP notices
- <NEW COMMAND> An "attendance config rsvp-notice" command will be made available to league managers, which shall have as input an integer standing for a number of days. This command configures the number of days before a round at which point RSVP notices will be sent out too all drivers of a division to mark their attendance for that round.
    - By default, this value will be set to 5.
- <NEW COMMAND> An "attendance config rsvp-last-notice" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours before a round at which point the bot will notify users who have not RSVP'd until then. A value of 0 means that no "last notice" announcement will be sent out to users who have no RSVP'd.
    - By default, this value will be set to 24.
- <NEW COMMAND> An "attendance config rsvp-deadline" command will be made available to league managers, which shall have as input an integer standing for a number of hours. This command configures the number of hours before a round at which point users can no longer alter their RSVP status. A value of 0 means that the deadline lasts up until the scheduled time of the round, which no alterations permitted beyond that point.
    - By default, this value will be set to 2.
- The input from all commands shall be validated against the current settings so that the RSVP Deadline always happens after the RSVP Notice and the RSVP Last Notice, and the RSVP Last Notice always happens after RSVP Notice. Ergo, the configuration shall follow the rule Notice\*24 > LastNotice\*24 > Deadline.
- If there is an ongoing season (read: season approved/active), all three commands must be rejected.

### Attendance points
- <NEW COMMAND> An "attendance config no-rsvp-penalty" command will be made available to league managers, which shall have as input an integer standing for the number of attendance points gained upon failing to RSVP up for a round.
    - By default, this value will be 1.
- <NEW COMMAND> An "attendance config no-attend-penalty" command will be made available to league managers, which shall have as input an integer standing for the number of attendance points gained upon failing to show up for a round (regardless of check-in status).
    - By default, this value will be 1.
- <NEW COMMAND> An "attendance config no-show-penalty" command will be made available to league managers, which shall have as input an integer standing for the number of attendance points gained upon failing to show up for a round after having accepted the check-in.
    - By default, this value will be 1.
- <NEW COMMAND> An "attendance config autosack" command will be made available to league managers, which shall have as input an integer standing for the number of attendance points upon which a driver will be automatically sacked from all team seats. A value of 0 means that the autosack functionality is disabled.
    - By default, this value will be false (disabled).
- <NEW COMMAND> An "attendance config autoreserve" command will be made available to league managers, which shall have as input an integer standing for the number of attendance points upon which a driver will be unassigned from their current seat and assigned to the reserve team of the same division. A value of 0 means that the autoreserve functionality is disabled.
    - By default, this value will be false (disabled).
    - The autoreserve functionality is only applied to drivers not in the reserve team.

## RSVPing
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
- Full-time drivers will be allowed to change their chosen option until the RSVP deadline is met. After that point, the choices are locked.
- Reserve drivers will be allowed to change their chosen option until the time of the round, provided they have NOT accepted the check-in. After that point, the choices are locked.
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

## Attendance
- Once the initial round results are submitted, the attendance sheet of the round will be filled. Being listed in any of the sessions of the round will be enough to count as having attended.
- Drivers who are reserving for that division are ignored.
- Attendance points shall only be distributed once the post-race penalties results are finalized, to prevent erroneous automatic sackings due to omitting a driver on the results accidentally.
- Attendance points will be distributed as follows:
    - Failure to check-in, attended: no-rsvp-penalty points gained.
    - Failure to check-in, did not attend: no-rsvp-penalty + no-attend-penalty points gained.
    - Checked-in, attended: 0 points gained.
    - Checked-in, did not attend: no-show-penalty points gained.
- A new button will be made available in the penalty wizard (NOT available in the appeal stage) for "attendance pardons". When pressed, a form shall open, requesting a discord user ID, the type of attendance penalty excused (no RSVP, no attend, no show), and the justification.
    - The pardons attributed shall be validated against the check-in status (did check-in or not) and against the real attendance of the driver (current provisional round results).
    - The justification is merely for logging purposes, it shall not be displayed anywhere else but the logging channel. Privacy reasons.
    - Multiple pardons may be attributed to the same driver (so that a "failure to check-in, did not attend" may be fully waived).
    - The attendance pardons shall be displayed together with the list of staged penalties.
    - The drivers who had one of their attendance penalties waived by this process shall not receive attendance points for that reason.
- After the post-race penalties are approved, attendance pardons cannot be applied.

### Updating attendance sheets
- Once the post-race penalties are approved and posted, the updated attendance total shall be posted in the configured attendance channel for the division.
    - The post will be a list of drivers in descending order from most attendance points to least, mentioning each one in the form "@user - x attendance points".
    - The end of the post will always have the following text: "Drivers who reach <attendance config autoreserve> points will be moved to reserve.\nDrivers who reach <attendance config autosack> points will be removed from all driving roles in all divisions." (the \n stands for a line break)
- To prevent misunderstandings, once a new attendance total is posted on the channel, the message containing the previous one shall be deleted.
- The attendance sheet for a round must be recalculated in the case "round results amend" is used. The pardons handed out previously will be taken in considerations as well.
- After the attendance total is posted, it shall be verified whether any driver has crossed the autoreserve limits; if they are deemed to have done so, they will be unassigned from their team role and assigned to the reserve team.
- After the attendance total is posted, it shall be verified whether any driver has crossed the autosack limits; if they are deemed to have done so, they will be unassigned from all their driving roles in all divisions, full-time or otherwise, and lose their driver role (the one automatically given out when a signup is approved)

## Test mode
- <NEW COMMAND> A "test-mode rsvp" command shall be available to league managers, which will take as parameters the user ID of a driver (mandatory) and one of "accepted/tentative/rejected". This will serve to set the RSVP status of fake drivers in test mode.