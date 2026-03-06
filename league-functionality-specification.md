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
Teams are a component of a division that are created automatically upon division creation.
    - By default, the following teams exist and are configurable: Alpine, Aston Martin, Ferrari, Haas, McLaren, Mercedes, Racing Bulls, Red Bull, Sauber, Williams.
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

# Changes to bot initialization flow
Bot initialization flow, right now, accounts only for the weather forecast functionality. There will be a change in approach to modularize and customize bot functionality, allowing users to enable and disable parts. As the division, round, team and driver flows are all foundational concepts, they may not be disabled; however, all other modules and functionality will be installed disabled by default. For this reason, the following considerations will be had regarding the weather forecast module:
- <NEW COMMAND> A module enable weather command will be made available to server administrators to enable weather functionality.
- <NEW COMMAND> A module disable weather command will be made available to server administrators to disable weather functionality.
- Weather events shall only be scheduled if weather functionality is enabled.
- Weather events shall be deleted when weather functionality is toggled off.
- Weather events shall be created for all rounds yet to happen when weather functionality is toggled on. If any event is scheduled "in the past", then it shall be executed in order (meaning, if Phase 1 and Phase 2 must be triggered immediately, Phase 2 must be triggered after Phase 1).

# Signup wizard and flow
## Enabling signup flow
- <NEW COMMAND> A module enable signup command will be made available to server administrators to enable signup functionality. This command will take a channel designated the "sign up channel", a user role which will be denominated the "base role", and a user which will be denominated the "signed up" role.
- <NEW COMMAND> A module disable signup command will be made available to server administrators to disable signup functionality, clearing all settings from the previous enabling.
- <NEW COMMAND> A signup time-type command will be made available to server administrators that provides two options via buttons: "Time Trial" or "Short Qualification". The option chosen will be recorded for use in the signup wizard.
- <NEW COMMAND>


The name of the commands is an example; the on