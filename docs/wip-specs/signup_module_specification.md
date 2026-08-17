This code repository was initially for a Discord bot used for F1 game league races to pseudo-randomly generate pre-set weather behavior in rounds. A league would be able to have multiple divisions, and each division would have their own independently configured rounds.
At set times before each round (T-5 days, T-2 days, T-2 hours), phases would be triggered to inform drivers of the most recent (and accurate) generated weather prediction.
Trusted users could also alter default configuration for rain probability to customize the behavior of this weather drawing.
There is also a test mode that allows for quick and easy testing of the bot.
I want to expand the functionality of this bot little-by-little, to eventually encompass the entire business rules of a league. There will be two new persisted data structures. Then there will be a change to the existing data structure of Seasons. For the time being, keep new commands at a minimum, to those lines/bullet points denoted with <NEW COMMAND>.

# Driver Profile
From the moment of sign-up, a discord user ID will be associated to a driver profile that is persisted in server-scope.
This driver profile will hold the following information:
    - Discord User ID - unique string that identifies one and only one Discord account
    - Current state - enumeration that will have the following meanings:
        - Not Signed Up - Driver is currently inactive and is able to trigger the signup procedure
        - Pending Signup Completion - Driver is currently finalizing their initial signup
        - Pending Admin Approval - Driver's signup procedure is currently on-hold, pending trusted role approval
        - Pending Driver Correction - Driver was requested to amend a parameter in their signup by trusted role, but has yet to submit it
        - Unassigned - Driver's signup was approved by trusted role, but driver is pending assignment to a division and team
        - Assigned - Driver's signup was approved by trusted role and driver was assigned to at least 1 team
        - Season Banned - Driver is currently inactive and is unable to trigger the signup procedure for a number of races equal to the length of the season they were race banned for
        - League Banned - Driver is currently inactive and is unable to trigger the signup procedure indefinitely
    - Former driver flag (binary) - False by default, set to true once a driver participates in a round. If this value is true, then the driver entry cannot be deleted, only modified.
    - Current season assignments - 0..n - For each division in which the driver is currently participating in, the name and tier shall be stored alongside their current position, their current tally of points, and the difference of points of the driver to the current first place of that division/tier
    - Historical season participation - 0..n - For each division in which the driver participated, the name and tier shall be stored alongside the season number, their final position, their final tally of points, and the difference of points of the driver to the eventual winner of that division/tier
    - Number of previous race bans - Integer - Description self-evident, 0 by default
    - Number of previous season bans - Integer - Description self-evident, 0 by default
    - Number of previous league bans - Integer - Description self-evident, 0 by default
If a driver does not have an entry in the database, it will be assumed that they are Not Signed Up.
If a driver transitions to the Not Signed Up state and their Former Driver Flag is false, then their entry shall be deleted from the database.
There shall be a state machine in place to govern over the current state of the driver. The possible transitions shall be as follows:
    - Not Signed Up -> Pending Signup Completion
    - Pending Signup Completion -> Pending Admin Approval
    - Pending Admin Approval -> Unassigned
    - Pending Admin Approval -> Pending Driver Correction
    - Pending Driver Correction -> Pending Admin Approval
    - Pending Admin Approval -> Not Signed Up
    - Unassigned -> Assigned
    - All States except League Banned and Season Banned -> Season Banned
    - All States except League Banned -> League Banned
    - Season Banned -> Not Signed Up
    - League Banned -> Not Signed Up
    - Not Signed Up -> Unassigned (only if test mode is enabled)
    - Not Signed Up -> Assigned (only if test mode is enabled)
The transitions for the unspecified states shall be outlined in later changes.
It shall be possible for server administrators to change the Discord User ID of a driver profile for another, to cover the possible of account changes <NEW COMMAND>.
If a user leaves the Discord server, their entry must remain in the database.
When test mode is enabled, it shall be possible for a system administrator to manually set the former driver flag of a driver to true or to false <NEW COMMAND>.

# Teams
Each server holds a list of default teams. A division's own teams are created from that list automatically upon division creation.
    - The default list ships with the "Reserve" team only. No other team is created for a server, and a league shall build its own list.
    - There is an extra team, called "Reserve" which shall always exist and shall not be configurable.
    - A server administrator shall be able to add, modify or remove a team to the default configuration (except Reserve) <NEW COMMAND>.
    - A server administrator shall be able to add, modify or remove a team to ALL the divisions of the current season only during season setup (except Reserve) <NEW COMMAND>.
    - The configurable teams (in other words, all except Reserve) shall have 2 seats, unassigned by default.
    - The Reserve team shall have no limit of available seats.
    - When reviewing the season, the list of teams and the drivers assigned to each team shall be displayed (including Reserve), alongside any unassigned drivers.

# Changes to Seasons
Beyond the aforementioned changes to seasons as a consequence of the implementation of Teams and Driver Profiles, the following functionality shall be implemented for seasons as well:
    - Each server will have a unique integer unassociated with any other data structure that identifies the number of the previous season. By default, this is 0. This season number will be the one displayed on all bot output.
    - Upon season setup, the new season will take the number recorded as above incremented by 1.
    - Upon season cancellation or completion, the server's previous season tracker shall be incremented by 1.
    - Each division shall possess a new tier parameter that is input when it is created (applies both to division add and division duplicate). The division tier may be used as an ID, but the division name shall remain as the one used in bot output.
    - Season approval will be blocked by the bot if all the divisions' tiers are not in sequential order. Furthermore, in the database, the divisions will be sorted in increasing tier order, for clarity, with tier 1 being the highest.

Please clarify possible impacts of this implementation on performance and storage footprint of the bot.

---

Some new commands are denoted below explicitly. Others may be necessary, but at the very least these are required.

# Changes to bot initialization flow
Bot initialization flow, right now, accounts only for the weather forecast functionality. There will be a change in approach to modularize and customize bot functionality, allowing users to enable and disable parts. As the division, round, team and driver flows are all foundational concepts, they may not be disabled; however, all other modules and functionality will be installed disabled by default. For this reason, the following considerations will be had regarding the weather forecast module:
- <NEW COMMAND> A "module enable weather" command will be made available to server administrators to enable weather functionality.
- <NEW COMMAND> A "module disable weather" command will be made available to server administrators to disable weather functionality.
- Weather events shall only be scheduled if weather functionality is enabled.
- Weather events shall be deleted when weather functionality is toggled off.
- Weather events shall be created for all rounds yet to happen when weather functionality is toggled on. If any event is scheduled "in the past", then it shall be executed in order (meaning, if Phase 1 and Phase 2 must be triggered immediately, Phase 2 must be triggered after Phase 1).

# Signup wizard and flow
## Enabling signup flow
- A "module enable signup" command shall be made available to server administrators to enable signup functionality. This command shall take the module name and nothing else.
- The general sign up channel, the "base role" and the "signed up" role shall each be set by a command of their own, available to server administrators: "signup channel", "signup base-role" and "signup complete-role".
    - Setting the sign up channel shall apply the channel permissions described below. Setting the base role shall re-apply them, removing the overwrite of the role it replaces.
    - Until all three are set, it shall not be possible to open signups.
    - While the signup module is enabled, season approval shall be blocked until all three are set, and the bot shall name each one that is missing.
- The general sign up channel shall be visible only to trusted users (tier 2 admins), server admins, and those with the base role.
- The general sign up channel may not be the bot interaction channel.
- The bot shall modify the permissions of the channel configured as general sign-up channel so that it is only visible to server administrators, users with the trusted role (tier 2 admins), and users with the "base role". No other interaction aside from pressing a button shall be possible in this channel, for those of the "base role".
- <NEW COMMAND> A "module disable signup" command will be made available to server administrators to disable signup functionality, clearing all settings from the previous enabling.

## Signup module configuration
- A "signup nationality" command shall be made available which will toggle on and off whether a driver's nationality is requested during the signups. By default, the nationality will be requested.
- A "signup time-type" command shall be made available which toggles between two options: "Time Trial" or "Short Qualification". The option chosen will be recorded for use in the signup wizard. By default, the "Time Trial" option is active.
- A "signup time-image" command shall be made available which will toggle on and off whether an image is required to be posted so that a signup time is accepted. The option chosen will be recorded for use in the signup wizard. By default, the time image will be necessary.
- A "signup config view" command shall display the configured channel, both roles, whether signups are open, and all three settings above. It shall be the only signup command that remains available while the module is disabled.
- A "signup time-slot add" command shall be made available to introduce the time slots users may select for their availability. This command will accept a day of the week and a time of day in the HH:mm format (military time) or hh:mm AM/PM format. There will be no configured time slots by default. Each time slot shall be attributed an integer, in chronological order. Slot times are recorded and displayed as UTC; no per-server timezone shall be configurable.
- A "signup time-slot remove" command shall be made available to remove time slots configured above. The available time slots will be listed with clarification to what day of the week and time of day they pertain to. If there are no configured time slots, this command will be blocked.
- A "signup time-slot list" command shall list the configured time slots.
- No more than 25 time slots may be configured, and time slots may not be added or removed while signups are open.
- The three configuration commands above and the time-slot commands shall be available to holders of the Manage Server permission.
- A "signup open" command shall be made available, in which 0..x tracks of those configured must be selected by the user; these are the sign-up tracks to be shown to the user later, so the choices will require persistance.
    - This command shall additionally accept an optional close time, as an ISO 8601 UTC datetime that shall be in the future. When given, the bot shall close the signup window automatically at that instant, and shall re-arm or immediately execute the closure after a restart.
    - The announcement shall state the close time when one is set.
- It shall not be possible to open signups if there are no configured time slots.
- It shall be possible to set 0 signup tracks. Where no tracks are set, no signup times shall be collected and drivers shall have no time sum to be seeded on.
- Once enabled, the bot shall post a button that allows the initiation of the signup procedure by users with the "base role" in the "general sign up channel". Likewise, the bot shall post a message informing that "signups are open", listing the signup tracks as well, as well as whether image proof of the signup times is necessary.
- A "signup close" command shall be made available, no parameters. When run, if there are no incomplete signups (no users in the Pending Signup Completion, Pending Admin Approval, Pending Driver Correction states), the signups will be immediately closed; otherwise, if there are incomplete signups, the administrator shall be informed of all the drivers and their signup channels, and prompted to either confirm or cancel the closing of the signups via buttons.
        - Closing the window shall transition only drivers in the Pending Signup Completion state to Not Signed Up. Drivers in Pending Admin Approval and Pending Driver Correction shall retain their state and may still be approved, rejected or corrected after the window has closed.
- Once closed, the button that allows the initiation of the signup shall be deleted by the bot, which shall likewise post a message informing that "signups are closed".

## Signup wizard
### Key driver state transitions
    - When a driver state changes to "not signed up", if the "former driver flag" is set to true, the entry in the database will remain, but their signup data shall be deleted with the exception of their signup channel (covered by another requirement).
    - When a driver state changes to "not signed up", if the "former driver flag" is set to false, the entry in the database will be deleted.
    - Every change of a driver's state shall be persisted.
### Wizard flow
    - Once the signup button is pressed by someone in the "not signed up" state, the bot will create a new channel titled "username-signup", in which the signup wizard shall be engaged. This channel shall be visible only to the user who engaged the signup wizard, the tier 2 admins and the server administrators.
    - Tier 2 admins and server administrators shall be able to type at will in the signup channels of all drivers.
    - The start of the signup wizard shall change the state of the driver from "not signed up" to "pending signup completion".
    - Pressing the signup wizard button by a driver in any other state will yield an appropriate error message visible only to them (already signed up, banned from signing up due to a league/season ban).
    - During the wizard, the bot shall request the following information from the user one-by-one and in this order, recording their answers:
        - Nationality - only accepts codes from national flags standard in Discord or the string "other", case insensitive
        - Platform (Steam/EA/Xbox/Playstation) - single choice (buttons)
        - Platform ID - string input, no bot validation
        - Availability - string input, the various configurations chosen via the "signup time-slot" command shall be printed in ID order, associating each one with their ID. The user will then type all IDs pertaining to time slots they are available in. Multiple can be chosen; they must be separated by spaces, commas, or comma+space.
        - Full-Time Driver or Reserve driver - single choice (buttons)
        - Preferred teams - choice of a maximum of 3 among teams configured (excludes Reserves) - if possible do buttons, otherwise have 3 preferred team dialogs that removes those previously selected, this way there is a ranked choice. An extra option will be added for "no preference" and the likes.
        - Optional preferred teammate - string input. Make available a button that will account for the "no preference" option.
        - Time Trial/Qualification time for track 1..x - Each track is to be done in turn. Each track is only accepted once the time is posted in the M:ss.mss or M:ss:mss format together with an image (depending on configuration). Text displayed to user shall vary according to the admin configuration of Time Trial or Short Qualification. If the user posts in the format of M:ss:mss, it shall be converted to M:ss.mss. If the milliseconds are not to 3 decimal points, rounding or zeros will be assumed. Strip for whitespaces.
        - Extra notes/observations - string input, limited to 50 characters. Make available a button that will account for the "no preference" option.
    - There shall be an "unengaged" state for the signup wizard. This, and the information requests above, shall mark the only states necessary for the signup wizard entity itself.
    - It shall be possible to have multiple users engage in the signup wizard flow simultaneously and independently of one another. Therefore, as a natural conclusion of the requirement immediately above, the current state of the signup wizard will be individual pertaining to each driver entry in the database.
    - If a user enters the "unassigned" or the "not signed up" state, their signup wizard state will immediately change to "unengaged".
    - While in the "unengaged" state, the user/driver performing the signup shall lose the ability to post messages in their signup channel. It shall be possible for the driver to post messages in the channel in any other state of the signup wizard.
    - The signup wizard shall ignore any and all messages by users that are not the driver performing the signup.
    - Once engaged, the signup wizard shall also record the users' Discord username and the name displayed on the server.
        - If the Discord User ID associated with a driver profile is changed, the discord username and display name shall likewise be overwritten by those of the new account.
    - Once all the information in this form is finalized, the one performing the signup shall enter the "pending admin approval" state.
    - A button shall be made available to drivers in "pending signup completion", "pending admin approval" and "pending driver correction" states to withdraw their signup. This will be the only way to make corrections due to constraints. This button will be available throughout the signup wizard as well.
    - Once the "pending admin approval" state is reached, signup information shall be persisted (excluding images) alongside the new state. The bot shall post 3 buttons: 1 to approve signup, 1 to request changes, 1 to reject sign-up outright. These buttons are only usable by trusted users.
    - If the user leaves the server during the signup (meaning Pending Signup Completion, Pending Admin Approval, Pending Driver Correction states), their signup will be cancelled and the channel deleted immediately.
    - If the driver does not change from the "Pending Signup Completion" state for 24 hours, their signup will be cancelled and the channel deleted after another 24 hours (still visible to the driver at hand, but impossible to interact with it). A message informing of the cancellation shall be posted by the bot.
    - If the driver does not change from the "Pending Driver Correction" state for 24 hours, their signup will be cancelled and the channel deleted after another 24 hours (still visible to the driver at hand, but impossible to interact with it). A message informing of the cancellation shall be posted by the bot.
    - If a driver's signup is rejected by a trusted user, their signup will be cancelled and the channel deleted after another 24 hours (still visible to the driver at hand, but impossible to interact with it). A message informing of the cancellation shall be posted by the bot.
    - Once a signup is deemed cancelled, the driver's state will change to "not signed up".
    - If a driver with an active signup channel reengages the signup procedure via the signup wizard start button, any existing signup channel associated with the user shall be deleted immediately.
    - If a driver's signup is approved via the aforementioned button by a trusted user, the driver entry shall move to the "unassigned" state.
    - If changes are requested to a drivers' signup, the driver will be changed to the "pending driver correction" state. Several buttons will appear, each one pertaining to a sign up parameter, which is to be pressed by trusted user roles exclusively to designate which aspect of the signup requires new information.
    - If the trusted user does not select a signup parameter to correct in 5 minutes, then the user will be moved back to "pending admin approval" state. It may be necessary or convenient to add a "pending correction parameter" state to drivers for this, between "pending admin approval" and "pending driver correction".
    - Once the parameter to be changed is selected, the driver state will then change to "pending driver correction" state, and the signup wizard shall transition directly from the "unengaged" state to the parameter's appropriate state. The acceptance criteria are the same for each one of the parameters. After valid input (depending on each parameter), the driver will be moved to the "pending admin approval" state, and the wizard to "unengaged".
        - This effectively means that when a driver is in "pending signup completion" state, the signup wizard shall transition states sequentially, but when a driver is in "pending driver correction" state, the signup wizard will hop from "unengaged" to parameter states and back directly.

## Placement of drivers
    - A "signup unassigned list" command shall be made available to trusted role users to list all users in the "unassigned" state. This command will return text containing the following data, per line, and in seeding order:
        - Seeding (to be explained later)
        - Discord User ID and display name
        - Platform
        - Availability
        - Full-Time/Reserve preference
        - Preferred teams, if full-time, in order from their signup
        - Preferred teammate, use N/A if signup information holds "no preference"
        - Sum of all signup times
        - Signup notes
    - An easier, quicker way to implement the above command will be to have an "unassigned" driver list that is indexed by seeding number, holding only the "discord user ID" and the sum of all signup times. This last parameter shall determine the seeding; drivers with lower signup time sum shall be seeded higher (e.g. 3:40.055 would be seed 1, 3:40.097 seed 2, 3:41.423 seed 3, etc). This way, the seeding is always kept up to date.
        - The sum of signup times shall be computed at the transition to "unassigned" and shall not be recomputed thereafter. Drivers with no recorded time shall be seeded last, and ties shall be broken by order of approval.
    - A "signup unassigned export" command shall be made available to trusted role users, returning the same drivers as a CSV file in seeding order. The columns shall be: seed, display name, Discord user ID, driver type, time sum, one column per configured availability slot marking those the driver selected, three preferred team columns, platform and platform ID.
    - <NEW COMMAND> A "driver assign" command shall be made available to trusted role users, which will take a discord user ID, an integer signifying a division tier (or a string signifying a division name), and an integer signifying one of the currently configured teams (plus Reserve).
        - In order for this command to be valid, the driver profile associated with the ID shall be in state "unassigned" or "assigned" (this permits multiple tier assignment). 
        - In order for this command to be valid, the team of the tier input shall have at least 1 open spot (if configurable). Reserve teams have limitless seats, so it shall always be possible to append a driver to a Reserve seat.
        - In order for this command to be valid, the driver may not have been assigned to any other team (including Reserve) in the same tier.
        - In order for this command to be valid, there shall be a season in setup or active state. Placement shall not require the season to be active.
        - If the "driver assign" command is successful and the driver state is "unassigned", it shall change to "assigned".
        - If the season is active, the discord user shall be granted the roles pertaining to the division and to the team they were assigned to, immediately. If the season is in setup, no roles shall be granted at this point; they shall all be granted when the season is approved.
    - <NEW COMMAND> A "driver unassign" command shall be made available to trusted role users, which will take a discord user ID an an integer signifying a division tier (or a string signifying a division name).
        - Clarity: This is the only command that allows the change from "assigned" to "unassigned"
        - In order for this command to be valid, the driver profile associated with the ID must be in state "assigned".
        - In order for this command to be valid, the driver must be assigned to a team in the division specified.
        - If the "driver unassign" command is successful and the driver has no other assignments, its state shall change to "unassigned".
        - If the season is active, the discord user shall have the role pertaining to the division they were assigned to removed. If the season is in setup, no role shall be removed, the driver never having held one.
        - A team role shall be revoked only where the driver no longer holds a seat in any team, across all divisions, that maps to that role.
    - Every successful assignment, unassignment and sacking shall cause the division's lineup message to be deleted and reposted in the division's configured lineup channel.
    - <NEW COMMAND> A "driver sack" command shall be made available to trusted role users, which will take a discord user ID.
        - In order for this command to be valid, the driver profile associated with the ID must be in state "unassigned" or "assigned"
        - If the "driver sack" command is successful, the driver's state will be changed to "not signed up".
        - If the "driver sack" command is successful, all driver roles pertaining to all divisions will be removed.
        - As part of this flow will be useful in future "season ban" and "league ban" functionality to come later, the removal of division roles shall be easily reusable.

The name of the commands is an example and only tentative. If further commands are required, please inform.