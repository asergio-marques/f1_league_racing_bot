# Core — why the code is shaped as it is

This file says how core's own code is laid out and why: its tables, its services and commands, its
changes on the queue, its timed work and start-up, what it posts, how it meets the modules, how it
fails, and the three parts of the league core alone owns (the season's lifecycle, the driver and
their accounts, and test mode). Every path is under `src/leaguebot/` unless it says otherwise.

It cites rules rather than restating them. What applies to every part of the bot is
`architecture.md`'s, cited by its section heading; this file says only how core's code carries it
out. What a league sees is the core specification's (`docs/wip-specs/core_specification.md`),
cited by its section heading. A constraint on one function lives in that function's docstring,
pinned by its test, and is named here only where the shape depends on it. Where this file and the
code disagree about what the code does, the code wins, and the file is corrected at close-out.

---

## The database

Answers to `architecture.md`, "The database".

### Core's tables

Core owns sixteen tables (`TABLE_OWNER` in `tests/repository/test_architecture_rules.py`), all
declared in `framework/db/migrations/001_baseline.sql`. The framework's four, the queue's, the
record's and the log lines', and the migrations' own record are `framework.md`'s ("The database");
core asks of them as below.

| Table | One row is | Held to it by |
|---|---|---|
| `server_configs` | the league's bot configuration and its claim on a server | `CHECK (id = 1)`; `server_id UNIQUE` |
| `seasons` | one season, live or archived | `idx_seasons_one_live`, a unique index over the live statuses; the `seasons_stage_*` triggers |
| `divisions` | one division of a season | `UNIQUE (season_id, name COLLATE NOCASE)`; a CHECK on `status` |
| `rounds` | one round of a division's calendar | a CHECK on `status` |
| `sessions` | one session of a round | made from the round's format (`create_sessions_for_round_on`) |
| `tracks` | one circuit a round may name | seeded by the baseline; `name UNIQUE` |
| `default_teams` | one team on the league's list | `name` (the shorthand) `UNIQUE`; the Reserve row seeded |
| `team_role_configs` | the Discord role of one team on the list | `team_name UNIQUE`; `uq_team_role_configs_role_id` |
| `team_instances` | a division's copy of one team on the list | `UNIQUE (division_id, name)` |
| `team_seats` | one seat of a division's team, and who holds it | `UNIQUE (team_instance_id, seat_number)` |
| `driver_profiles` | one driver | `discord_user_id UNIQUE` |
| `driver_accounts` | one Discord account a driver has held | `discord_user_id UNIQUE`; written by triggers |
| `driver_history_entries` | a driver's result in one division of a past season | unique over profile, season number and division name |
| `driver_season_assignments` | a driver's placement in a division of a season | `committed` filled by a trigger when inserted empty |
| `driver_division_memberships` | a driver having held a committed placement in a division | written by triggers on the placements |
| `season_review_prompts` | one standing season-review question | keyed by its message id |

**Core writes the audit log and reaches the queue through the framework.** The framework's four
tables are not written or read by core's own SQL. A change's audit entry is written by the
framework's writers (`record_change_on`, `record_system_change_on`), each handed the raw old and new
values, which the writer encodes as JSON with sorted keys, so no core site chooses an encoding of its
own; core asks the change queue through `queue_records` and the retry queue through `retry_service`
(reading, and clearing it in a pack), each on the connection it is handed where it has one.

**A season keeps two states.** `status` is the coarse one, `stage` the lifecycle's, and the
database keeps them in step: the `seasons_stage_*` triggers fill `stage` from `status`, carry it
with a write of `status` alone, and refuse a pair that does not match. `core/models/season.py`
says why both are kept, and `test_season_stage_matches_status` and
`test_every_status_and_stage_pair_is_held_by_the_triggers` pin the mapping.

**A round's and a division's statuses and the queue's states are CHECKs**, each tied to its enum by
a test in `tests/core/test_schema_rules.py`
(`test_a_division_s_and_a_round_s_statuses_match_their_enums`, and the two for the queue).
A division's `status` is stored rather than worked out from its rounds, and
`refresh_division_status_on` brings it up to date wherever a round's outcome settles; its docstring
gives the reason.

**What is derived is kept by the database or by one function.** A driver's accounts are written
by triggers, so that no path creating a profile can miss them
(`tests/core/test_driver_accounts.py`'s docstring). A driver's division memberships are written
by triggers too, recording each committed placement as it is committed and never cleared by a
placement changing (`write_driver_history_entries_on`'s docstring, which reads them). A round's
number is recomputed from the rounds' moments by `renumber_rounds_on`, and a round's sessions are
replaced as a set by `create_sessions_for_round_on`. A division's teams copy the list's names when
the division is seeded and keep them, so a season keeps the names it ran under (the baseline's
comment on `team_instances`).

**`team_role_configs` is linked to the list by name.** A team's rename re-keys its role through
`TeamRoleService.rename_team_role_config_on`.

### The modules' own columns on core's tables

These columns are the modules' own, as `architecture.md` allows, and `OWN_COLUMNS` in
`tests/repository/test_architecture_rules.py` lists them:

| Table | Column | Module |
|---|---|---|
| `rounds` | `phase1_done`, `phase2_done`, `phase3_done`, `mystery_notice_given` | weather |
| `rounds` | `checkin_cleared` | attendance |
| `sessions` | `phase2_slot_type` | weather |
| `divisions` | `forecast_channel_id` | weather |
| `server_configs` | `weather_module_enabled` | weather |
| `server_configs` | `signup_module_enabled` | signup |

Core's code keeps off them by naming its columns: its inserts on these tables name core's columns
alone, so the modules' columns take their defaults, and its reads select core's columns alone.
`ConfigService.claim_on` writes neither module flag. A module's switch is read through the module
(below, "Keyed hooks"), never from the column by core. A round's status is core's, set through
core's service for results.

### Links, and what stops a delete

Inside core, the links act on a delete as follows:

| Link | On delete |
|---|---|
| `divisions.season_id` | cascade |
| `driver_division_memberships`' three links | cascade |
| `driver_accounts.driver_profile_id` | cascade |
| `queued_change_steps.change_id` | cascade |
| `driver_history_entries.driver_profile_id` | set null, so the history keeps the driver by account |
| `rounds.division_id`, `sessions.round_id`, `team_instances.division_id`, `team_seats`' two links, the four links of `driver_season_assignments` | no action |

`season_review_prompts.season_id` and `audit_entries.division_id` carry no foreign key. A season's
delete removes its review prompts by hand; the audit keeps its record of a division deleted
(`KEPT_REFERENCES` in `tests/core/test_deletes.py`).

**A placed driver stops a division's delete.** `driver_season_assignments.team_seat_id` names the
seat with no delete action, so removing the seats fails while a placement points at one, and the
delete's save rolls back whole. That is the backstop; the change's check refuses first
(`division_delete_change`, through `SeasonService.division_holds_a_placed_driver`). The rule is the
core specification's ("Divisions"); `delete_division_on`'s docstring states the shape, and
`test_a_division_with_a_driver_placed_in_it_is_not_deleted` pins it. Adding a cascade to that key,
or deleting the placements first, would undo it.

**A season leaves `SETUP` with its number committed**, and `delete_season` raises for any season
not in `SETUP`, for the reason its docstring gives
(`test_a_season_whose_number_is_committed_is_refused`). **The Reserve team** is seeded by the
baseline and refused removal by `TeamService.judge_remove_on`.

### Each module deletes its own rows

Core deletes only its own rows. For each kind of delete it calls a hook first, on the delete's own
connection, and each module signed up deletes or re-points its rows there, in the same save; core
then deletes its own, and the caller commits. The builder makes these hooks in
`_sign_up_to_deletes` and `_sign_up_to_pack`:

| Hook | Subscribers, in order | Core calls it in |
|---|---|---|
| `rounds_deleted` | attendance, results, weather | `delete_round_on`, `delete_division_on`, `delete_season` |
| `divisions_deleted` | attendance, results | `delete_division_on`, `delete_season` |
| `season_deleted` | results, signup | `delete_season` |
| `profiles_deleted` | attendance, results | `delete_driver_profiles` |
| `profiles_merged` | attendance, results | `DriverService._merge` |
| `bot_packed` | signup, weather, attendance, results, image | `pack_service.pack` |

A part may also hand the pack back the ids of the jobs it keeps (`KEPT_JOB_IDS`), as image's does
for its daily portrait refresh; the pack keeps their union.

`test_every_delete_leaves_no_orphan` fills one row in every table and runs each kind of delete
against it; `test_a_merge_leaves_nothing_naming_the_driver_absorbed` does the same for a merge.

---

## How the code is laid out

Answers to `architecture.md`, "How the code is laid out". Core's code sits in `core/cogs/`,
`core/services/`, `core/models/` and `core/utils/`. The machinery it runs on, the database code, the
change queue, posting, the module switches, the retry loop's cog, the bot's type, the hook types and
the failure paths, is the framework's (`framework/`), whose design is `framework.md`'s; this file
names it only where core's own code rests on it.

### Services, and what each owns

Each of core's services sits in a topic folder of `core/services/`
(`architecture.md`, "How the code is laid out"). The table names a service by its file, not its
folder.

| Area | Services | Owns |
|---|---|---|
| Configuration | `config_service` (`ConfigService`) | `server_configs`, and the claim on the server; it also fulfils the framework's protocols, answering the league's settings (the claimed server, the settings row) and the authority tiers (who is a league admin, who a league manager) the change queue asks, each tier delegating to `channel_guard`, where the rule stays |
| Seasons, divisions, rounds | `season_service` (`SeasonService`), `season_lifecycle_service`, `season_end_service`, `round_moments`, `track_service`, `round_context` (`round_context`, `division_context`, `division_rounds`) | seasons, divisions, rounds, sessions; reads `tracks`; `round_context` answers a round's, and a division's, season, tier and grand prix for the module that heads a picture with them (image's pictures), and `division_rounds` a division's rounds in order, for a picture that lists them |
| Teams and placements | `team_service` (`TeamService`), `placement_service` (`PlacementService`), `team_role_service` (`TeamRoleService`), `template_capacity_service` (`TemplateCapacityService`), `lineup_posting` (`LineupPoster`), `lineup_command` (`lineups_for_command`), `division_seating` (`division_seating`) | the list and the divisions' teams; the seats and placements (`PlacementService`, which posts nothing, reads a team's role through the team-role service it is handed, joins `team_role_configs` in its own queries for the roles a driver's placements carry, reads the driver role from the settings row, and gives and takes roles only through that service's helpers); `team_role_configs` and the helpers that give and take a team's Discord role (`TeamRoleService`); nothing for the capacity guards, which ask the image module's drawing whether its templates could draw a seat (`TemplateCapacityService`); nothing for the lineup's posting, the picture or its text (`LineupPoster`), nor for `/team lineup`'s pictures, which refuse rather than fall back to text (`lineups_for_command`); the seats and who sits in them, read as the lineup of record shows them, for every module that lists who races in a division (`division_seating`: the text lineup, image's lineup, standings and previews, each handing in its own filter on the state of the driver who sits); the portrait roster reads `active_season_driver_ids` beside it, which takes no filter: every driver assigned in the active season, a placement not yet confirmed included, test drivers left out |
| Drivers | `driver_service` (`DriverService`), `driver_names` | profiles, accounts, memberships, and the state machine; history is written by `season_end_service.write_driver_history_entries_on`; nothing for `driver_names`, which reads a driver's name, username and nationality, and whether the league collects a nationality, from the profile, the accounts, the test-mode fields and signup's hook |
| A round's timed work | `round_jobs` (`RoundJobs`) | nothing: it is handed the rounds it arms, and reads the live seasons' rounds only to cancel their jobs (`cancel_live_rounds`) |
| Channels | `channel_registry_service` | nothing: it reads core's channel columns, and the modules' through a hook |
| The image module | `image_drawing` (the `ImageDrawing` protocol) | nothing: it declares what core asks |
| Amending a round | `amendment_rules_service`, `round_steps` | nothing: the first judges core's own rules of what an amendment may do (the fields, the round's status, the moment and a mystery round given a circuit alone) and gathers each module's part of the judgement through `amendment_judged`; the second names a round in an amendment's step by the number it bears when read |
| Season reviews and confirmations | `approval_checks`, `approval_window_service` (`calendar_faults`, what is already behind a season on the day it is approved), `review_sections`, `confirmation_refusals`, `season_fingerprint_service`, `season_review_changes`, `season_review_views`, `season_review_workflows` | `season_review_prompts` |
| The hub | `hub_service`, `about_service` | `server_configs.hub_message_id` |
| A module switched on | `kept_channels` (`kept_channels_check`) | nothing: it reads the channels a module kept through the reader its switch-on hands it, asks the registry, through the bot's `channel_uses_read` hook that every module holding channels answers, which are taken, and Discord which are gone or unusable |
| Test mode | `test_mode_service`, `test_roster_service`, `backup_service` | `server_configs.test_mode_active` |
| Starting over | `pack_service`, `factory_reset_service`, `in_memory_state` | — |

The services core runs on, the change queue, the scheduler, posting, the record of what changed
and the module switches, are the framework's (`framework.md`, "Services, and what each owns").

The change types core registers each sit in a file of their own beside these (`*_change.py`,
`*_changes.py`, `bot_settings.py`, `division_settings.py`, `test_mode_settings.py`), and are listed
under "How a change is carried out".

**`season_lifecycle_service` is module functions, not a class on the bot.** It reads the database
directly so that the signup window's forced close can call it with nothing but a path (its
docstring). The same holds for the other services of plain functions: they are handed what they
need by whoever calls them, as architecture.md's "Services stay attached to the bot" has it.
A cog's reads are asked of a core service, as architecture.md's "What each part may do" has
it: `pack_service.what_stops_a_pack`, `track_service.list_tracks` and `resolve_track`,
`PlacementService.division_lineup`. `/season config-review` reads `bot.configuration_faults`, the
reader the builder makes (`_make_configuration_faults`), which asks
`approval_checks.configuration_faults` with the services and the hooks as they stand when it is
asked. Signup's share of what it finds, the signup channel and the league's two roles, arrives
through the `configuration_faults_read` hook, so that core reaches the module only as
architecture.md's "How modules and core fit together" has it and holds no rule of signup's.

**A channel is judged by what its use needs, and the owner of the channel says what that is.**
`approval_checks.channel_presence` is the strict reader: it finds a channel in the cache or by a
fetch and answers `PRESENT`, `GONE` or `UNUSABLE` with what the bot lacks there, and raises on any
answer Discord does not prove, a server error or a bot member it cannot read. `judged_presence`
stands aside on that raise, the stated exception in architecture.md's "Errors and failures", which
is what a confirmation wants, and is the strict reader's one caller: it raises so that a caller free
to refuse on a check it could not make may. The gates before a repost and a recalculation judge from
the cache alone, through `results_post_service.channel_fault`. Core's own `channel_fault` words the
confirmation's line, and takes the *needs* its caller hands it, so it holds no list of channels and
no permission of its own. It also words a channel of no division, the server's, where its division
name is None, which signup's configuration faults use, so one wording serves the division channels
and signup's at the configuration. A need is data (`core/models/channel_needs.py`) and each part builds its
channels' from it in its own service (`channel_needs`: weather's forecast channel, results' three,
attendance's two), asking the image module's drawing, handed through the module's wiring, only
whether a picture could attach a file. The alternatives rejected: one table of every channel's needs
in core, which would hold each module's rule (architecture.md, "What each part may do"); and each
gate keeping its own list, which lets two gates judge one channel differently.

**Core's own two channels are named through the same hook as the modules'.** The lineup and the
calendar need the image module's answer to build their needs, which is awaited, so
`core_division_channel_faults` (its subscriber of `division_channel_faults_read`, signed up before
every module's, so a division's lines read core's first) builds them where it can await, from
`core_channel_needs`. `division_channel_faults` holds no list of channels, so the review and the two
confirmations cannot disagree about one and `season_approval_change` holds none.

**The approval's queued check judges what the press judged, whole.** Between the press and the
queue reaching the approval, any of it can change, and a module's own channel is as likely to be
deleted as a division's. The check therefore reads `confirmation_refused` again, handed the server,
after the division channels and before the drawing program, and gives the first refusal as the
press does; a retried save judges through it too (`season_approval_change`'s docstring holds the
order). Asking the hook, not a list of modules' rules,
keeps core free of any module's rule. *Rejected:* re-judging only the channels the check already
names, which leaves a module's refusals (signup's channel, results' points configurations) judged
only at the press. *Rejected:* the
check reading the image module's configuration for a switch, which is the module's to answer
(`ImageDrawing.calendar_attaches_files` and `lineup_attaches_files`).

**Core's models** (`core/models/`) are plain data and the enums the schema ties to: the season's
status and stage, a division's and a round's status, the driver's state and the dependency table
(`module_dependencies.py`), and the permissions a saved channel needs (`channel_needs.py`: `Needs`
and the named permissions, which each part states its channels' needs from). The change queue's
vocabulary and `Refused` are the framework's models (`framework.md`, "How the code is laid out").

**Core's helpers** (`core/utils/`) are the tier guards (`channel_guard.py`), the season-stage gate
(`season_gate.py`), the team choices a command's autocomplete offers (`team_autocomplete.py`),
core's own listings (`formatting.py`), readers of what a league types (`input_validator.py`,
`round_import.py`, `roster_import.py`), the asset slug (`asset_slug.py`) and the nationalities a
driver may give (`nationality_data.py`). The bot's type, the one-league claim and the base classes,
the hook types, the failure paths, the standard log lines, `bounded_autocomplete` and the posting
guard are the framework's helpers (`framework/utils/`, `framework.md`). The helpers that know
nothing of the bot, reading a typed time, the time zones, the version and the fitting of text to
Discord's limits, are the `utils` layer's (`architecture.md`, "How the code is laid out"), and the
rendering of a date for a graphic is the image module's.

**The database code** is the framework's (`framework.md`, "The database").

### The entry point

`__main__.py` builds the bot. `build` makes the services and the hooks, signs the modules up to
every hook several of them join (the `_sign_up_to_*` functions, each naming the modules' functions
in the order the hook calls its subscribers), and calls each module's wiring (`<module>/wiring.py`,
`architecture.md`, "How the code is laid out") for the rest of that module's part: its own hooks
(`sign_up`), its switch (`register_switch`, through `register_module_switches`), its change types
(`register_change_types`) and its kinds of timed job (`register_scheduler_callbacks`). It signs up
core's own About option (`register_hub_options`), loads the cogs, registers the persistent views and
signs the start-up steps up to the starting hook (`_sign_up_to_starting`), all before the bot
connects; `main()` then holds the change queue (`ChangeQueue.hold`), so that a request made before
the sweep has asked its own waits for it. Beside core's own part, the builder keeps what joins
modules: every service, every hook and the order of its subscribers, a hand-in from one module to
another, the cogs and views, and the order of the modules' start-up turns. Core has no wiring of its
own. `test_build_returns_every_service_complete` and
`test_every_kind_is_registered_before_the_scheduler_starts` pin that. `LeagueBot`
declares every attribute the builder attaches (`framework.md`, "How the code is laid out").

### Command groups

| Group | Cog | Commands |
|---|---|---|
| `/bot` | `bot_cog` | `init`, `log-channel`, `interaction-channel`, `interaction-role`, `admin-role`, `base-role`, `driver-role`, `hub-channel`, `pack`, `factory-reset` |
| `/season` | `season_cog` | `setup`, `placements-review`, `config-review`, `status`, `cancel`, `abort`, `complete` |
| `/division` | `division_cog` | `add`, `duplicate`, `delete`, `rename`, `amend`, `cancel`, `lineup-channel`, `calendar-channel`, `calendar-sync` |
| `/round` | `round_cog` | `add`, `add-bulk`, `add-xml`, `amend`, `delete`, `cancel` |
| `/driver` | `driver_cog` | `reassign`, `assign`, `unassign`, `move`, `release`, `reject`, `sack` |
| `/team` | `team_cog` | `add`, `modify`, `remove`, `list`, `lineup`, `reserve-role` |
| `/module` | `module_cog` | `enable`, `disable` |
| `/test-mode` | `test_mode_cog` | `toggle`, `nationality`, `advance`, `review`, `set-former-driver`; `backup` (`save`, `lock`, `status`, `restore`); `roster` (`add`, `add-bulk`, `remove`, `list`, `clear`) |
| `/track` | `track_cog` | `list` |
| — | `clean_cog` | `/clean-bot`, a top-level command |

No module's command sits under one of these groups. The retry loop for log lines runs in the
framework's cog (`framework/cogs/retry_cog.py`), which holds no command.

---

## How a change is carried out

Answers to `architecture.md`, "How a change is carried out", which designs the queue; the core
specification's section of the same name holds what a league is told.

The queue that carries every change out, its lanes, its stop and its records, is the framework's
(`framework.md`, "How a change is carried out"); what follows is core's own part of it.

### A league's settings

Every settings command, core's own included, declares a `Setting`, which the framework's
`setting_change` builds into its change type (`framework.md`, "Settings").

Core's settings, each of kind `core.setting.<name>`:

| Command | Overtakes a stop | Steps after the save |
|---|---|---|
| `/bot log-channel`, `/bot interaction-channel` | yes | none |
| `/bot interaction-role`, `/bot admin-role` | yes | the hub's permissions set again |
| `/bot base-role` | no | the modules told (`base_role_changed`), the hub's permissions, and the role granted to every driver where the season's configuration is fixed |
| `/bot driver-role` | no | the role granted to every driver where the season's configuration is fixed |
| `/bot hub-channel` | yes | the old hub stood down and the new one's permissions set, then the panel's refresh asked |
| `/division lineup-channel`, `/division calendar-channel` | yes | none |
| `/test-mode nationality` | no | none |

Which settings overtake a stop is `Setting.repairs_the_bot` (`architecture.md`, "A job that fails
stops its lane"), set in `bot_settings.py` and `division_settings.py`. A step that carries
the save's line writes it only once it has succeeded (`carries_line`; `setting_change`'s
docstring).

### Core's change types

Every core change type runs on the league lane; none is a conversation's. Each is registered by
`register_change_types` in the entry point, handed what it needs of another module there.

| Kind | Asked by | The all-or-nothing save | After it |
|---|---|---|---|
| `core.bot_init` | `/bot init` | the claim, the four settings and the audit entry | — |
| `core.season_setup` | `/season setup` | the season and its audit entry | — |
| `core.division_add`, `core.division_duplicate` | `/division add`, `duplicate` | the division with its teams (and, duplicated, its rounds) | — |
| `core.division_rename`, `core.division_amend` | `/division rename`, `amend` | the division's fields, audited as a whole state | — |
| `core.division_delete` | `/division delete` | the division, its rounds and teams, and each module's rows through the delete hooks | — |
| `core.round_add`, `core.round_import` | `/round add`, `add-bulk`, `add-xml` | the round or the whole import, renumbered | — |
| `core.round_amend` | `/round amend` while the season is set up | the round, renumbered | — |
| `core.round_delete` | `/round delete` | the round and its sessions, renumbered | — |
| `season.round.amend` | `/round amend` on an ongoing season | the round, the modules' part (`round_amended`) and the audit entries | timed work armed again; the modules' posts and clean-ups |
| `season.round.rearm` | a cancellation refused in its save | none | the round's timed work armed again |
| `season.round.cancel`, `season.division.cancel` | `/round cancel`, `/division cancel` | the rounds cancelled, the division finished, the season moved on | the modules' notices, the calendar, the closing line |
| `season.cancel` | `/season cancel` | `apply`: the divisions and rounds cancelled; then `end`: history, driver pass, season cancelled | notices, calls taken down, calendars, roles |
| `season.complete` | `/season complete` | `settle`, then `end` (history, driver pass, season completed) | each division's final posts, roles |
| `season.abort` | `/season abort` | `end`: the driver pass and the season deleted | signup channels, roles, portraits |
| `season.wind_down` | a follow-on of what finished the last division | the season turned down to Pending completion | — |
| `season.review.post` | `/season placements-review`, `config-review` | none: the question's row is its job's `record` | the review's posts and its expiry timer |
| `season.review.take_down` | the expiry timer, start-up, a refused press, Approve | `forget`, which drops the row | — |
| `season.configuration.confirm` | ✅ Confirm configuration | the stage, signup's kept settings, the audit entry | — |
| `season.approve` | ✅ Approve | sessions, points snapshot, placements committed, the season Ongoing | arming, roles, lineups, calendars, the modules' opening posts |
| `season.placements.confirm` | ✅ Confirm placements, mid-season | placements, the season back to Ongoing | roles, lineups |
| `season.approve.tell`, `channel.tell` | an approval, or a mid-season confirmation, refused or discarded | none | one line in the review's channel |
| `core.driver_*` (seven) | `/driver …` | the seats or the state, and the audit entry | role moves, lineups |
| `core.team_*` (four) | `/team add`, `modify`, `remove`, `reserve-role` | the list and the role mapping, audited | role moves |
| `core.test_mode_toggle` | `/test-mode toggle` | the flag, the modules' part, the audit entries (its own and those the modules' part hands back); turned off, every fake driver deleted | the jobs the modules hand back for what test mode posted, the backup deleted |
| `core.test_mode_former_driver` | `/test-mode set-former-driver` | the flag and the audit entry | — |
| `core.test_roster_add` | `/test-mode roster add` | the fake driver, its seat and assignment, and the audit entry | — |
| `core.test_roster_add_bulk` | `/test-mode roster add-bulk` | every driver of the file, their seats and assignments, and an audit entry for each | — |
| `core.test_roster_remove` | `/test-mode roster remove` | the fake driver deleted, their history kept, and the audit entry | — |
| `core.test_roster_clear` | `/test-mode roster clear` | every fake driver of the division deleted, their history kept, and an audit entry for each | — |
| `hub.refresh` | the bot, wherever what the panel offers may change | none: the panel's id is its job's `record` | the old panel deleted where the hub moved |
| `calendar.sync` | `/division calendar-sync` | `close`, last: a save writing the line | before it: the post, then the deletion of the calendar it replaced |
| `lineup.post` | the bot, after a change made off the queue moves a driver | none: the lineup's id and audit entry are its post's `record` | the post, then the deletion of the lineup it replaced |
| `core.clean` | `/clean-bot` | `close`, last: a save writing the line | before it: `choose`, then one `delete_message` for each message chosen |

Each module's switch is a change of the module's own, of kind `module.on:<name>` or
`module.off:<name>` (`module_on` and `module_off` in `framework/models/change.py`), which
`ModuleCog` asks for by that kind.

**A switch-on of a module that keeps channels does its reading before its save.** Weather, results,
attendance and signup keep their channels while off, so each switch-on is a `Setting` with a step
before the save (`Setting.before`, `framework.md`, "Settings"): `kept_channels_check`
(`core/services/modules/kept_channels.py`). It takes the reader of the module's own channels that
each switch-on hands it (`KeptReader`: a connection and the live season's divisions in, the kept
channels out, awaiting only the connection; a kept channel is a division's, or the server's, as
signup's is, and then names no division and is spoken of as "the signup channel"), asks the registry
which are taken (`find_channel_use`, through the bot's `channel_uses_read` hook, which every module
holding channels answers), and `channel_presence` which are gone or unusable. `architecture.md`
gives the arrangement in "How modules and core fit together" and the invariant the reading rests on
in "How a change is carried out"; core_specification.md, "Modules" and "Channels", gives the rules a
league sees. `live_divisions_on` (`channel_registry_service`) names the divisions of the live
season, which the registry and the check both read, so that they judge the same channels.

Each file's docstring holds its change's check, its steps and the reason for each. Three shapes
recur:

- **A save that judges again.** A change whose state may move between its check and its save
  judges again on the save's connection; a refusal found there writes nothing and ends the
  change rather than stopping the lane (`core.round_amend`, `core.driver_reassign`). An approval or
  a mid-season confirmation retried after a fault plans its judgement again before it saves.
- **An amendment is judged by core and by each module.** `judged_with_the_modules` asks
  `amendment_judged` of each module, handing no module's windows: each reads and keeps its own in its
  `AmendmentPart`. The `judge` job's record is `{judged, kept}`, the parts' `kept` merged and stored
  as given, named by no module, and handed back by the save and the arming to every module, each
  reading only its own; a module finding none of its own in a record made before an update reads afresh. The
  steps a module plans for an amendment are worded by the module (`amendment_step_failure`), so
  core's outcome names none of them.
- **A test-mode flush is whatever the modules hand back.** A change that switches test mode off or
  ends a season plans the jobs `test_mode_flushed` returns, on the save's connection. The abort, which
  plans them before the save that switches test mode off, asks (`fill`) with a flag that the jobs are
  due only where test mode is still on as each comes up, and the module's job reads it; core names
  no module's step.
- **A fault in a job stops the lane**, save in `/bot init`'s save, a `Setting`'s steps before its
  save, its save and the steps after it, and a role move (`role_moves.role_move_step`), each of
  which ends on a fault instead. `/season setup`'s docstring says why its save stops the lane where
  `/bot init`'s does not.
- **A step the league cannot discard** is marked so where dropping it would leave a season or a
  round stuck: the approval's arming, an amendment's arming, a cancellation's save, a
  re-arming's two jobs, and a review's `forget`. The mid-season confirmation has none, for the
  reason its docstring gives.

---

## Timed work and restarts

Answers to `architecture.md`, "Timed work and restarts". What each kind of job does when missed is
the core specification's ("When the bot stops").

### Core's round jobs

The scheduler a round's jobs are armed on, its one callable, `_one_off_timer_job`, which runs each
handler through the job runner, and its job store, is the framework's (`framework.md`, "The
scheduler").

`RoundJobs` (`core/services/rounds/round_jobs.py`, on the bot as `round_jobs`) is core's side: it
arms and cancels a round's jobs, removes any stale season-end job, and reads test mode's pending
jobs and queued events, through the scheduler's generic calls. A round's job is named from the
round's id and its kind (`_round_job_suffix`). The services that arm or cancel a round's jobs are
handed it by the builder, a module's wiring or a cog; the cogs, the wirings and the builder alone
read `bot.round_jobs`. It is handed the database path as well as the scheduler, to read the rounds
it cancels for (`cancel_live_rounds`, which weather's switch-off asks for its own kinds). It holds
the scheduler under a name other than `_scheduler`, which the check that nothing reaches past the
scheduler reads as a reach wherever it appears outside the scheduler. A module's own standing job
is its module's (`architecture.md`, "Timed work and restarts").

### Core's own timed events

Core has one timer of its own, `review_expiry`. A review's `arm_expiry` job arms it from the moment
the question's job was marked done, so the window starts when the question appears; how long the
window is, `REVIEW_WINDOW`'s comment says. When it fires, `review_expiry_timer` reads the row again
and asks `season.review.take_down`, or nothing where the review is gone or already being taken down.
What became of a review's button is held in memory (`_PRESSES`), for the reason its comment gives.
**Missed while the bot was down**, the timer fires on start, and finds the review already in hand:
start-up's step for expired review prompts, core's first, asks the take-down first, since the
scheduler is paused until start-up ends.

**A round's timed work is armed by three changes**: the season's approval (its `ARM` job), and the
amendment and re-arming of a round (`round_amend_change`), through `RoundJobs.arm_leads`. The rules
they serve are `architecture.md`'s, "Timed work and restarts"; what follows is core's own code.

- **`RoundLead`** (`core/models/round.py`) is the plain record a module answers with: a timer's
  kind, its offset from the round's moment, its label, and the marks core reads (armed only while
  ahead, a clean-up, left to `/test-mode advance`, run at once by an amendment, and the words the
  approval's date check quotes). It names no module.
- **`arm_leads`** gives each lead the id, name and key a round's timer has always had and arms it,
  nothing more. Running a lead at once (`run_lead_now`), or under test mode taking down the job
  just armed for a moment already past, stays in the amendment's arming step, chosen by a lead's
  marks, never by its kind.
- **The approval** reads every module's leads through `read_round_leads` and arms them for every
  round. **The amendment and re-arming** take them from each module's `round_armed`, a
  `RoundArming`, and a round already raced, or ended not held, is armed its clean-up leads alone,
  from the approval's read (`read_round_leads`).
- **`results_left_to_advance`** is core's one rule for whether test mode leaves the submission to
  the advance; where it does, the approval and the amendment drop the leads marked as left to it.
- **`sign_up_kinds`** is where a module signs its round kinds up at build; `RoundJobs` knows a job
  is a round's, and which phase it is, by that register alone.
- **`clean_up_moment`** is core's one shared next-day figure; every other moment is the module's
  own function (`forecast_leads`, `check_in_leads`). Results' reason for answering every round is
  in `results_module.md`, "Timed work and restarts".

### Start-up

`main()` reads the settings, swaps in a staged restore, applies the migrations, builds the bot and
connects it, in that order (`test_migrations_run_before_the_bot_connects`). When Discord first
reports the bot ready, the framework's `start_up` runs once per process, as the sweep of everything
that came due while the bot was stopped.

Its design is the framework's (`framework.md`, "Start-up"); what follows is which steps it runs, and
in what order.

The steps are signed up to `StartingHook` by the builder (`_sign_up_to_starting`): core's two, by
name, then each module's, by the module's own `start_up_steps` function in its wiring bound to the
bot, under the module's switch key, the modules in the order the core specification lists them
(`test_the_builder_orders_the_start_up_steps`). A module's function names its steps in its own order,
with the comments that order them. Each step is a recovery, which looks its work up when it runs;
turning each into the handler for its kind of job, called in the module's turn, is the module's
own design pass.

| Turn | Step | What it does |
|---|---|---|
| core | expired review prompts | asks each standing review's take-down, and routes its Approve view again |
| core | hub | routes the hub's buttons again and asks the panel's refresh |
| signup | signup close timer | asks the close of a signup window whose close passed, or the arming of the timer of one still ahead |
| signup | wizards | asks the ending of each signup whose channel is gone, the release of each driver awaiting a correction parameter, and the expiry of each signup left unanswered for 24 hours; arms nothing |
| signup | held signup channels | asks the deletion of each hold past its `held_until`, whatever signup's switch says (`runs_while_off`); arms nothing |
| results | amendment sweep | arms results' standing sweep of lapsed amendments |
| results | orphaned amend channels | asks each open amendment's lapse; before the submission channels, for the reason its comment gives |
| results | orphaned submission channels | asks each open submission's review or restart |
| results | missed round starts | asks the opening of each round whose start passed while the bot was down, through the `results` job's own handler |
| attendance | missed check-in calls | asks each check-in call that fell due |
| attendance | RSVP views and deadlines | routes each check-in message's view again and asks each deadline passed |
| attendance | missed check-in clean-ups | asks each check-in's clean-up that fell due |
| weather | missed phases | asks each weather phase whose moment passed |
| weather | missed forecast clean-ups | asks each forecast's clean-up that fell due |
| image | portrait refresh job | arms the image module's daily refresh |

Results also arms the amendment sweep when it is switched on (`module_on_results`'s `After` step),
and its switch-off removes it (`results_off_change`'s `disarm_sweep`), so the sweep runs whenever
results is on, a restart or not. The builder records the moment the bot came up
(`LeagueBot.came_up_at`), which the wiring hands to results' `results` job and its catch-up alike,
so both judge a start passed while the bot was down against one value.

### The clock

Core's services and change types judge by the queue's clock, which the builder hands them
(`framework.md`, "The clock").

---

## Posting to Discord

Answers to `architecture.md`, "Posting to Discord", which lays out the four handlers and their
files; this section says what core posts through them.

| Post | Kind | Channel | Posted by | Found again by |
|---|---|---|---|---|
| A division's lineup | standing post | `divisions.lineup_channel_id` | `lineup_posting.lineup_step` → `LineupPoster.refresh_lineup` | `divisions.lineup_message_id`, saved in the job's `record` |
| A division's calendar | standing post | `divisions.calendar_channel_id` | `calendar_post_service.calendar_steps` | `divisions.calendar_message_id`, saved in the job's `record` |
| The hub's panel | standing post | `server_configs.hub_channel_id` | `hub_service.refresh_panel`, which edits in place or posts again where gone | `server_configs.hub_message_id`, saved in the job's `record` |
| A season review's report, pictures and drawing notice | notice | the channel the review was run in | `season.review.post`'s jobs | the question's row (`season_review_prompts.report_message_ids`) |
| A season review's question | notice | as above | `post_question` | `season_review_prompts.message_id` |
| A review's expiry notice | notice | as above | `season.review.take_down`'s `post_expiry` | not kept |
| The approval's batch notice | notice | the review's channel | `season.approve`'s `post_batch_notice` | its job's result, read by the job that deletes it |
| A line to the review's channel | notice | the review's channel | `season.approve.tell`, `channel.tell` | not kept |
| A module's cancellation notice | notice | the module's channel | `cancellation_notice_service`: `post_cancellation_notice`, which attendance's and weather's jobs call and which drops the message, and `post_cancellation_message`, which results' job calls and which hands the message back | not kept, except results', in `called_off_notes`, where its switch-off finds it again |

The log channel's lines and the stop notice are the framework's (`framework.md`, "Posting to
Discord"). A replaced standing post of core's is deleted by a job after the new one, built by the
framework's `standing_posts`;
`test_the_text_lineup_is_posted_before_the_old_one_is_deleted` and
`test_moving_the_hub_posts_the_new_panel_before_deleting_the_old` pin it.

**Where a picture falls back to text** (Constitution XIV, rules 7 and 8): the lineup's in
`LineupPoster._refresh_lineup_post`, the review's in `season_review_changes`' `draw_and_post`,
and the calendar's in `post_division_calendar`, whose `commanded` parameter tells a posting a member
asked for from one that was not (`test_an_uncommanded_posting_falls_back_to_the_text`). The lineup's
and the calendar's post job ask for text on every try after the first (`as_text`).

**Who a post may mention is stated where it is sent** (`architecture.md`, "Posting to Discord"); who
that is, is the core specification's ("The record of what changed", "How a change is carried out").
`post_cancellation_notice` and `post_cancellation_message` post with the mentions their caller, a
module's notice job, hands them.

**Core's posts off the queue** are `architecture.md`'s exceptions ("Every post is made in a
job"): `BotCog`'s factory reset; the framework's are `framework.md`'s.
`/team lineup` answers the member with its picture as a reply and records nothing.

---

## How modules and core fit together

Answers to `architecture.md`, "How modules and core fit together".

### The hook types

The hook types, `Hook`, `AwaitedHook` and `StartingHook`, are the framework's (`framework.md`, "The
hook types"); the builder makes every hook and signs the modules up.

### The hooks core declares

Each is declared on `LeagueBot`, whose comment on it says what it hands over and what core does with
the answers. "A" is an `AwaitedHook`, "P" a plain `Hook`; the subscribers are in sign-up order,
which each `_sign_up_to_*` docstring states where it matters.

| Hook | Type | Subscribers | Core calls it in |
|---|---|---|---|
| `starting` | `StartingHook`, read rather than called | core's two steps, then signup, results, attendance, weather, image | `start_up` |
| `round_leads_read` | A | attendance, weather, results | `round_moments.read_round_leads`, called with no arguments: for the season's approval (its date check and its arming), the review and a clean-ups-only re-arm; results answers whatever its switch |
| `phases_drawn_read` | A | weather | `round_moments.read_phases_drawn`, whose one caller is `/season status` |
| `amendment_judged` | A | attendance, weather | `round_amend_change`, and `/round amend`'s confirmation (`round_cog`); handed no windows, each module reading and keeping its own |
| `round_amended`, `round_clean_ups_due` | A | weather, attendance | `round_amend_change` |
| `round_armed` | A | attendance, weather, results | `round_amend_change` |
| `amendment_steps` | P | weather, attendance | `round_amend_change` |
| `amendment_step_failure` | P | weather, attendance | `round_amend_change` |
| `cancellation_refused` | A | results | `cancellation_changes` |
| `rounds_called_off` | A | results, attendance | `cancellation_changes` |
| `cancellation_notices` | A | attendance, weather, results | `cancellation_changes`, and `season_service.end_round_not_held_on`, results' not-held round end's writer |
| `cancellation_steps` | P | results, attendance (three), weather, results | `cancellation_changes` |
| `rounds_made_final` | A | results | `season_service` (`end_rounds_awaiting_results_on`, `close_raced_rounds_on`) |
| `placements_confirmed`, `placements_confirmed_steps` | A, P | results, attendance | `season_approval_change` |
| `configuration_confirmed` | A | signup | `configuration_confirm_change` |
| `confirmation_refused` | A | results, signup | `confirmation_refusals`, which hands every subscriber the season and the server where its caller has one (the press, the placements report and `approval_checks` do, as does the queued check; a caller with none passes None, and a subscriber that needs the server then judges what it can without), and `season_approval_change`, whose check reads it again at the queue |
| `configuration_faults_read` | A | signup | `approval_checks` |
| `review_sections_read` | A | signup, attendance, results, weather | `review_sections` |
| `division_lines_read` | A | results, attendance, weather | `review_sections` |
| `division_channel_faults_read` | A | core, weather, results, attendance | `approval_checks` (core's own subscriber, `core_division_channel_faults`, names the lineup and calendar channels, and `division_channel_faults` asks for the rest) |
| `driver_names_read` | A | signup | `approval_checks` (the unsettled signups, named by their latest signup) and `driver_names`, core's reader of how a driver is named and of whether a nationality is collected; the builder hands the hook, and image, results and attendance each ask the reader with it for every picture and ordering that names a driver |
| `fingerprint_areas_read` | A | weather, results, attendance, signup, image | `season_fingerprint_service` |
| `channel_uses_read` | A | weather, results, attendance, signup | `channel_registry_service.find_channel_use`, and `kept_channels_check`, which asks it as a module is switched on; weather, results, attendance and signup answer it only while their own switch is on |
| `setting_channels_read` | A | weather, results, attendance, signup | `factory_reset_service.gather_targets` |
| `base_role_changed` | A | signup | `bot_settings` (`/bot base-role`'s step) |
| `driver_turned_down` | A | signup | `driver_changes`, `season_lifecycle_service` |
| `driver_account_changed`, `driver_account_changed_steps` | A, P | signup | `driver_changes` (`/driver reassign`) |
| `divisions_raced_read` | A | results | `driver_service` |
| `final_round_read` | A | results | `season_end_changes` |
| `season_completed`, `season_completed_steps` | A, P | results, attendance | `season_end_changes` |
| `season_standing_read` | A | results | `season_end_service` |
| `season_end_refused` | A | results | `season_end_service` |
| `season_end_closes`, `driver_returned_jobs` | P | signup | `season_lifecycle_service` |
| `drivers_returned` | A | signup | `season_lifecycle_service`, `season_end_changes`, `cancellation_changes` |
| `season_end_steps` | P | signup, weather | `season_lifecycle_service` |
| `drivers_deleted` | A | image | `season_end_service` |
| `test_mode_turned_on` | A | results | `test_mode_toggle_change` |
| `season_approved` | A | results | `season_approval_change` |
| `test_mode_flushed` | A | weather | `test_mode_toggle_change`, `season_end_changes` (the season's end, and the abort's `fill`) and `cancellation_changes` (a season's cancellation), which plan the jobs it hands back and name none |
| `pending_steps_read` | A | weather, attendance, results | `test_mode_service` |
| `review_marks_read` | A | weather, results, attendance | `test_mode_service` |
| `in_memory_state_cleared` | P | results, signup | `in_memory_state.clear_in_memory_state` |
| `rounds_deleted`, `divisions_deleted`, `season_deleted`, `profiles_deleted`, `profiles_merged`, `bot_packed` | A | see "The database" | see "The database" |

**A step a module plans is defined by the module.** Where a hook's subscribers hand back jobs to
plan, a plain hook beside it (`*_steps`) carries each module's step definitions by name, which core
merges into the change type's steps (`season_lifecycle_service.steps_of_the_modules` and its
callers). Core then plans what it is handed without knowing whose it is.

### Keyed hooks

A keyed hook takes one function for each key. Each is an object the builder makes:

| Keyed hook | Key | Signed up by | A key nobody signed up |
|---|---|---|---|
| `ModuleService.register_switch` | the module's switch name (`images` for the image module) | `register_module_switches` | `SwitchNotSignedUp` (`test_a_switch_no_module_signed_up_for_is_an_error_not_off`) |
| `SchedulerService.register_timer` | the kind of timed job | `register_scheduler_callbacks`, and `register_change_types` for one-off timers | logged and passed over |
| `AdvanceSteps.register_step` | the kind of a round's job | `register_scheduler_callbacks`, beside each kind's handler | `AdvanceStepFailed` (`test_advance_reports_a_kind_with_no_step_signed_up`) |
| `HubOptions.register_option` | the option's key, part of the button's custom id | `register_hub_options`, which signs core's About | a press refused |

`ModuleService` and `SchedulerService` are the framework's (`framework.md`, "The module service"
and "The scheduler"); core asks for a module's switch by the module's name.

### The dependency table

`NEEDS` in `core/models/module_dependencies.py` holds one entry, attendance needing results; `needs`
and `taken_down_with` read it. Its names are the modules' folder names, the ones `.importlinter`
uses, so the table and the import rules cannot drift apart
(`test_every_module_is_under_the_rules_between_modules`). A name it does not hold needs nothing,
which is how `/module`'s "images" reads (the docstring). `ModuleCog` reads it to refuse an enable
whose dependency is off and to warn of what a disable takes down; the builder hands results'
switch-off attendance's, for the cascade.

### The image protocol

`ImageDrawing` (`core/services/modules/image_drawing.py`) declares what core asks of the image
module, and the image module's class of the same name implements it; the builder makes it and
attaches it as `bot.image_drawing`. Its methods fall in five groups:

- **Capacity questions**, each answering what the template cannot hold, or nothing: the four
  placement guards (`TemplateCapacityService`), a round's (`round_add_changes`), and the season
  review's.
- **Gates and pictures**: `calendar_wanted`, `calendar_attaches_files`, `draw_calendar`,
  `lineup_wanted`, `lineup_attaches_files`, `draw_lineup`,
  `draw_lineup_for_command`, `draw_review_lineup`, `discard_picture`. The review's lineup is drawn
  through `draw_review_lineup`, as the builder's drawing of a review's picture asks it, so that
  every picture core draws is drawn through this protocol and a change to the protocol reaches each
  of them. What a gate that cannot be read answers is `architecture.md`'s ("Errors and failures").
- **The review's image section and its faults**: `rasteriser_fault`, `review_section`,
  `template_faults`, `colour_shortfall_problems`, `portrait_fault`. The image module words each;
  core decides which refuse. The module docstring says which faults each lets out.
- **The fingerprint's part**: `artwork_signature`.
- **Portraits**: `discard_portraits`, for a reassign. A season's driver pass discards through the
  `drivers_deleted` hook instead.

`TemplateCapacityService` and `LineupPoster` are handed the drawing by the builder; the cogs read it
off the bot.

---

## Errors and failures

Answers to `architecture.md`, "Errors and failures", which names each failure path and every
stated exception, core's among them; each docstring there named carries its reason.

| Starting point | The code |
|---|---|
| The slash-command sync at start-up | `on_ready`, which logs a refusal on the host and reports it to the log channel by `start_up_failure_line` |

The framework's failure paths, a command, button or form, a Discord event, an autocomplete, a
change, a scheduled job, a background task, the start-up sweep and the retry loop, and the log
writer's containment of its own faults, are `framework.md`'s ("Errors and failures").

**Core's refusal and fault types**, each caught by name; the framework's, `Refused` among them,
are `framework.md`'s:

| Type | Where | What it is |
|---|---|---|
| `PackRefused` | `core/services/upkeep/pack_service.py` | `/bot pack`'s refusals, worded by the cog from its fields |
| `InvalidDriverTransition` | `core/models/driver_profile.py` | a fault: a move past the transition table, caught by a caller that expects the race |
| `InvalidStageTransition` | `core/models/season.py` | a season asked to move to a stage its lifecycle does not allow from where it is |

---

## Constraints a later reader might tune away

Each is stated where it lives and pinned by the test named. The framework's are `framework.md`'s.

| Constraint | Stated in | Pinned by |
|---|---|---|
| `server_configs` holds one row; the claim's condition is in its statement, so two servers cannot both win; releasing the claim keeps the row | `config_service`'s docstring, `ConfigService.claim_on` | `test_a_single_row_table_refuses_a_second_row`, `test_only_one_server_claims_a_released_row`, `test_releasing_the_claim_keeps_what_is_the_league_s` |
| One live season, held by the schema rather than each reader | `idx_seasons_one_live`; `tests/core/test_one_live_season_per_server.py`'s docstring | `test_a_second_live_season_is_refused` |
| A placed driver's seat stops a division's delete | `delete_division_on` | `test_a_division_with_a_driver_placed_in_it_is_not_deleted` |
| A reassign judges every refusal on its save's connection before it writes | `DriverService.reassign_judged_on`, `driver_changes`' docstring | `test_a_failed_driver_reassign_changes_nothing`, `test_a_refusal_found_inside_the_save_ends_the_reassign_and_the_lane_goes_on` |
| `write_transition` is the one writer of a driver's state | `write_transition` | `test_every_reset_goes_through_the_transition_table` |
| The driver has no ban states | `DriverState` | `test_the_driver_has_seven_states_and_no_ban_among_them` |
| A fingerprint that cannot be taken is empty, and differs from every fingerprint | `season_fingerprint_service.take_fingerprint` | `test_a_fingerprint_that_could_not_be_taken_differs_from_everything` |
| A review's expiry asked twice posts one notice | `season_review_changes.claim_take_down` | `test_a_review_expired_twice_at_once_posts_one_notice` |
| A season starts on the UTC date of the queue's clock | `season_setup_change`'s docstring | `test_the_start_date_is_the_utc_date` |
| Every store of league state in memory is cleared by pack and factory reset, or named as holding none | `core/services/upkeep/in_memory_state.py` | `tests/core/test_in_memory_state.py` |

---

## The season lifecycle

The stages, and which event moves a season from which to which, are the core specification's ("The
life of a season").

**Two columns, kept in step by the database.** `SeasonStatus` and `SeasonStage`
(`core/models/season.py`) are the two states; `STAGES_OF_STATUS` maps one to the other, and the
triggers hold every row to it (see "The database"). `ONGOING_STAGES` names the three ongoing
stages. `ALLOWED_STAGE_TRANSITIONS` is the table of permitted moves; an abort is not among them,
since an aborted season is deleted.

**Each move is written by one function, on the caller's connection, committing nothing**, so that
the move is saved with whatever caused it:

| Event | Function | Called by |
|---|---|---|
| A season set up | `SeasonService.begin_setup_on` | `core.season_setup` |
| Its configuration confirmed | `confirm_configuration_on` | `season.configuration.confirm` |
| A signup window opened or closed | `advance_on_window_open_on`, `advance_on_window_close_on` | the signup module's window changes |
| Placements approved | `transition_to_active_on` | `season.approve` |
| Placements confirmed mid-season | `confirm_placements_on` | `season.placements.confirm` |
| Every division done | `advance_to_pending_completion_on` | a division finishing, a window closing, `season.wind_down`, `season.complete`, a cancellation |
| Completed | `complete_season_on` | `season.complete`'s `end` |
| Cancelled | `cancel_season_on` | `season.cancel`'s `end` |
| Aborted | `SeasonService.delete_season` | `season.abort`'s `end` |

The stage functions in `season_lifecycle_service` write through `_move_on`, which conditions the
write on the stage it read and raises `InvalidStageTransition` where none matched. The status-only
writers in `season_service` are guarded on the status (moving to Ongoing, on the stage as well), and
the triggers carry the stage with them.
`season_lifecycle_service` decides which move an event calls for, so that a command, a timer and a
restart reaching the same event move the season alike (its docstring).

**The season's end is a chain of changes.** Whatever finishes the last division asks
`season.wind_down`, as the bot's, in its own save; the wind-down turns the season down to Pending
completion. `/season complete`, `/season cancel` and `/season abort` are each one change whose
driver pass and final mark (and, but for an abort, the history) are saved last, in one `end` save
(their docstrings).

---

## The driver profile and its accounts

What a driver is, their states and their accounts, are the core specification's ("Drivers", "The
states of a driver", "A driver's accounts").

**One writer of a driver's state.** `ALLOWED_TRANSITIONS`
(`core/services/drivers/driver_service.py`) is the table, and `write_transition` is the one
statement that writes `current_state`: it raises `InvalidDriverTransition` for a move the table does
not allow, having written nothing, and commits nothing. Every caller changing a state inside a
larger write calls it rather than the update (its docstring). `DriverService.transition_on` creates
a profile where none exists and the table allows the move from Not Signed Up; no transition deletes
one.

**The accounts are a table the database keeps.** `driver_profiles.discord_user_id` names the
current account; `driver_accounts` lists every account the driver has held, written by two
triggers on `driver_profiles`, and goes with the profile by cascade
(`test_creating_a_profile_lists_its_account`,
`test_deleting_a_profile_frees_every_account_it_held`).
A stored record keeps the account it was written under, and the rest of the bot reads it by three
helpers, whose comment in `driver_service` gives the rules: `current_account_of` for an account
arriving from Discord, `accounts_of` to find stored records, and `current_account_map` to count or
draw them.

**A reassign is one save.** `driver_reassign_change` judges in its check and again on the save's
connection (`DriverService.reassign_judged_on`), then writes; where the new account is another
driver's current one, the two profiles merge (`DriverService._merge`), the modules re-pointing their
rows through `profiles_merged` first. Role moves, the modules' jobs (`driver_account_changed`) and
the portrait's discard follow the save as jobs of their own.

**A driver's history outlives the profile.** `driver_history_entries` names the driver by account
and lets go of the profile on delete. `delete_driver_profiles` (`season_lifecycle_service`) is the
one delete of profiles but a merge: it calls `profiles_deleted`, vacates seats, deletes placements,
deletes the history unless asked to keep it, and deletes the profiles. `keep_history` says which;
the core specification's "The driver pass" and "Entering and leaving it" say when.

---

## Test mode

What test mode is, and what it allows, are the core specification's ("Test mode").

**The switch is a change of its own**, `core.test_mode_toggle`, carrying the state it sets rather
than a flip. Turning it on is one save: the flag, the modules' part through `test_mode_turned_on`,
and the audit entries, its own first and then those the modules' part hands back in a
`TestModePart` (the names it seeded and the audit entries of its writes, declared by core as
`AmendmentPart` is). Turning it off is one save too (`switch_test_mode_off_on`), deleting every fake
driver through `delete_driver_profiles`, followed by the jobs the modules hand back through `test_mode_flushed`
to clear what test mode posted, and the deletion of the saved backup. A season's end switches it off in its own `end` save. Whether a
real driver stands in the way is counted by `count_live_real_drivers`. `/test-mode nationality` is a
`Setting`.

**Five more test-mode commands are each a change of their own, in one shape:** `/test-mode
set-former-driver` (`core.test_mode_former_driver`) and the four roster commands of
`test_roster_changes` (`core.test_roster_add`, `core.test_roster_add_bulk`,
`core.test_roster_remove`, `core.test_roster_clear`). Each carries what the command was given, is
judged without a write at the ask and again as it starts, and is one save of its rows and their
audit entries, the roster's writers taking the connection of the save and committing nothing. What
the reply of a discarded save needs is read into the payload at the ask (`fill`), and a fault in a
save stops the lane. What each kind judges and writes is its docstring's. A roster is measured
against the template capacities with the bot's own template capacity service, the one carrying the
image module's drawing, as a real placement is: the drawing is asked in the check, before the
save opens, and the save judges the rest again on its own connection, measures no capacity again
and asks nothing of the drawing. The lane runs one change at a time, so what the check was told
still holds. One the file shows wrong on its own is refused by the form before the ask.

**Fake drivers are seeded, not moved through the state machine.** `test_roster_service` inserts a
fake driver at Assigned, flagged as a test driver, with a synthetic id above any real Discord id;
its docstring says why it bypasses `write_transition`. They are removed through
`delete_driver_profiles`, keeping their history.

**Advancing reads one moment and asks the modules.** `advance_next_step` reads the next step and
the steps passed over at one `now`, for the reason its docstring gives. Each module hands back its
pending steps through `pending_steps_read`, and core orders them by `STEP_ORDER`. The step for each
kind of a round's job is signed up on `AdvanceSteps`; `run_advance_step` takes the round's job of
that kind down before it runs the step, so a step that raises leaves no job to ask it again. The
review (`build_review_summary`) lays out the divisions and rounds and asks the modules' marks
through `review_marks_read`.

**A saved state is a copy of the whole database, restored at start-up.** `backup_service` copies
through SQLite's backup API, and a restore is staged, then swapped in by `main()` before any service
is built, for the reasons its module docstring gives; a restore brings back an empty queue and
carries the job numbering on. The backups are a testing convenience, not disaster recovery (the
same docstring).
