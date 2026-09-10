# Signup module

The signup module governs how a person becomes a driver of a league: the channel they sign up
in, the questions the wizard asks, and the approval that admits them.

What a driver *is* — the driver profile, its states, the former-driver rule, and placement into
a division and team — is specified in [the core specification](core_specification.md), a driver
existing whether or not this module is enabled.

# Signup wizard and flow
## Enabling signup flow
- A "module enable signup" command shall be a league admin's and shall enable signup functionality. This command shall take the module name and nothing else.
- The general sign up channel, the "base role" and the "signed up" role shall each be set by a command of their own: "signup channel", "signup base-role" and "signup complete-role".
    - Setting the sign up channel shall apply the channel permissions described below. Setting the base role shall re-apply them, removing the overwrite of the role it replaces.
    - Until all three are set, it shall not be possible to open signups.
    - While the signup module is enabled, season approval shall be blocked until all three are set, and the bot shall name each one that is missing.
- The general sign up channel shall be visible only to holders of the interaction role, holders of the league admin role, and those with the base role.
- The general sign up channel may not be the bot interaction channel.
- The bot shall modify the permissions of the channel configured as general sign-up channel so that it is only visible to holders of the league admin role, holders of the interaction role, and users with the "base role". No other interaction aside from pressing a button shall be possible in this channel, for those of the "base role".
- <NEW COMMAND> A "module disable signup" command shall be a league admin's and will disable signup functionality, clearing all settings from the previous enabling.

## Signup module configuration
- A "signup nationality" command shall be made available which will toggle on and off whether a driver's nationality is requested during the signups. By default, the nationality will be requested.
- A "test-mode nationality" command shall be made available which will toggle on and off whether a driver created by test mode may carry a nationality. By default, the nationality shall be permitted, this being the default of the "signup nationality" command it parallels. It shall be available only while test mode is active.
    - While test mode is active, this switch and not "signup nationality" shall govern wherever the image module reads whether the league collects a nationality at all. The "signup nationality" setting shall not be altered by it.
    - A driver created by test mode holds no signup record, and the nationality of such a driver shall be recorded upon the driver profile itself, in the canonical form a signup record holds it in.
    - The "test-mode roster add" command shall accept an optional nationality, in the forms the signup wizard accepts one — a nationality adjective, a country name, or "other" — and shall reject a value the wizard would reject, creating no driver. A driver created without one shall record none, which is distinct from recording "Other".
    - The command shall reject a nationality given while the "test-mode nationality" switch is off, creating no driver. A driver may still be created without one.
    - The "test-mode roster list" command shall display the nationality of each driver it lists, and shall mark a driver recording none.
- A "signup time-type" command shall be made available which toggles between two options: "Time Trial" or "Short Qualification". The option chosen will be recorded for use in the signup wizard. By default, the "Time Trial" option is active.
- A "signup time-image" command shall be made available which will toggle on and off whether an image is required to be posted so that a signup time is accepted. The option chosen will be recorded for use in the signup wizard. By default, the time image will be necessary.
- A "signup config view" command shall display the configured channel, both roles, whether signups are open, and all three settings above. It shall be the only signup command that remains available while the module is disabled.
- A "signup time-slot add" command shall be made available to introduce the time slots users may select for their availability. This command will accept a day of the week and a time of day in the HH:mm format (military time) or hh:mm AM/PM format. There will be no configured time slots by default. Each time slot shall be attributed an integer, in chronological order. Slot times are recorded and displayed as UTC; no per-server timezone shall be configurable.
- A "signup time-slot remove" command shall be made available to remove time slots configured above. The available time slots will be listed with clarification to what day of the week and time of day they pertain to. If there are no configured time slots, this command will be blocked.
- A "signup time-slot list" command shall list the configured time slots.
- No more than 25 time slots may be configured, and time slots may not be added or removed while signups are open.
- The three configuration commands above and the time-slot commands are a league manager's.
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
### Wizard flow
    - Once the signup button is pressed by someone in the "not signed up" state, the bot will create a new channel titled "username-signup", in which the signup wizard shall be engaged. This channel shall be visible only to the user who engaged the signup wizard, the holders of the interaction role and the holders of the league admin role.
    - A league manager and a league admin alike shall be able to type at will in the signup channels of all drivers.
    - The start of the signup wizard shall change the state of the driver from "not signed up" to "pending signup completion".
    - Pressing the signup wizard button by a driver in any other state will yield an appropriate error message visible only to them (already signed up, banned from signing up due to a league/season ban).
    - During the wizard, the bot shall request the following information from the user one-by-one and in this order, recording their answers:
        - Nationality - accepts a nationality adjective ("British"), a country name ("United Kingdom"), or the string "other", case insensitive. A two-letter country code is not accepted. The vocabulary covers every UN member state, together with Palestine and Taiwan; a value outside it is rejected and the answer re-requested.
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
    - Once the "pending admin approval" state is reached, signup information shall be persisted (excluding images) alongside the new state. The bot shall post 3 buttons: 1 to approve signup, 1 to request changes, 1 to reject sign-up outright. These buttons are a league manager's, and shall be refused to the driver whose signup they judge, who can read the channel they are posted in.
    - If the user leaves the server during the signup (meaning Pending Signup Completion, Pending Admin Approval, Pending Driver Correction states), their signup will be cancelled and the channel deleted immediately.
    - If the driver does not change from the "Pending Signup Completion" state for 24 hours, their signup will be cancelled and the channel deleted after another 24 hours (still visible to the driver at hand, but impossible to interact with it). A message informing of the cancellation shall be posted by the bot.
    - If the driver does not change from the "Pending Driver Correction" state for 24 hours, their signup will be cancelled and the channel deleted after another 24 hours (still visible to the driver at hand, but impossible to interact with it). A message informing of the cancellation shall be posted by the bot.
    - If a driver's signup is rejected by a league manager, their signup will be cancelled and the channel deleted after another 24 hours (still visible to the driver at hand, but impossible to interact with it). A message informing of the cancellation shall be posted by the bot.
    - Once a signup is deemed cancelled, the driver's state will change to "not signed up".
    - If a driver with an active signup channel reengages the signup procedure via the signup wizard start button, any existing signup channel associated with the user shall be deleted immediately.
    - If a driver's signup is approved via the aforementioned button by a league manager, the driver entry shall move to the "unassigned" state.
    - If changes are requested to a drivers' signup, the driver will be changed to the "pending driver correction" state. Several buttons will appear, each one pertaining to a sign up parameter, which is to be pressed by a league manager exclusively to designate which aspect of the signup requires new information.
    - If the league manager does not select a signup parameter to correct in 5 minutes, then the user will be moved back to "pending admin approval" state. It may be necessary or convenient to add a "pending correction parameter" state to drivers for this, between "pending admin approval" and "pending driver correction".
    - Once the parameter to be changed is selected, the driver state will then change to "pending driver correction" state, and the signup wizard shall transition directly from the "unengaged" state to the parameter's appropriate state. The acceptance criteria are the same for each one of the parameters. After valid input (depending on each parameter), the driver will be moved to the "pending admin approval" state, and the wizard to "unengaged".
        - This effectively means that when a driver is in "pending signup completion" state, the signup wizard shall transition states sequentially, but when a driver is in "pending driver correction" state, the signup wizard will hop from "unengaged" to parameter states and back directly.

## Placement of drivers
    - A "signup unassigned list" command shall list all users in the "unassigned" state. This command will return text containing the following data, per line, and in seeding order:
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
    - A "signup unassigned export" command shall return the same drivers as a CSV file in seeding order. The columns shall be: seed, display name, Discord user ID, driver type, time sum, one column per configured availability slot marking those the driver selected, three preferred team columns, platform and platform ID.
    - The placement of a driver into a division and team is specified in [the core specification](core_specification.md).

The name of the commands is an example and only tentative. If further commands are required, please inform.