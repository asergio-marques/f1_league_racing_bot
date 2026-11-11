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

Core owns twenty tables (`TABLE_OWNER` in `tests/repository/test_architecture_rules.py`), all
declared in `core/db/migrations/001_baseline.sql`. `schema_migrations` is the migrations' own
record, written by `run_migrations` alone.

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
| `audit_entries` | one recorded change | — |
| `pending_messages` | one log line the log channel has not yet taken | — |
| `queued_changes` | one change asked of the queue | CHECKs on `origin` and `state`; `queued_changes_one_running` |
| `queued_change_steps` | one job: a step of a change | `UNIQUE (change_id, position)` |
| `season_review_prompts` | one standing season-review question | keyed by its message id |

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
| `rounds` | `phase1_done`, `phase2_done`, `phase3_done` | weather |
| `rounds` | `checkin_cleared` | attendance |
| `sessions` | `phase2_slot_type`, `phase3_slots` | weather |
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
| `bot_packed` | signup, weather, attendance, results | `pack_service.pack` |

`test_every_delete_leaves_no_orphan` fills one row in every table and runs each kind of delete
against it; `test_a_merge_leaves_nothing_naming_the_driver_absorbed` does the same for a merge.

---

## How the code is laid out

Answers to `architecture.md`, "How the code is laid out". Core's code sits in `core/cogs/`,
`core/services/`, `core/models/`, `core/utils/` and `core/db/`.

### Services, and what each owns

| Area | Services | Owns |
|---|---|---|
| Configuration | `config_service` (`ConfigService`) | `server_configs`, and the claim on the server |
| Seasons, divisions, rounds | `season_service` (`SeasonService`), `season_lifecycle_service`, `season_end_service`, `round_moments`, `track_service` | seasons, divisions, rounds, sessions; reads `tracks` |
| Teams and placements | `team_service` (`TeamService`), `placement_service` (`PlacementService`), `team_role_service` (`TeamRoleService`), `template_capacity_service` (`TemplateCapacityService`), `lineup_posting` (`LineupPoster`) | the list and the divisions' teams; the seats and placements (`PlacementService`, which posts nothing, reads the role mapping through the team-role service it is handed, and gives and takes roles only through that service's helpers); `team_role_configs` and the helpers that give and take a team's Discord role (`TeamRoleService`); nothing for the capacity guards, which ask the image module's drawing whether its templates could draw a seat (`TemplateCapacityService`); nothing for the lineup's posting, the picture or its text (`LineupPoster`) |
| Drivers | `driver_service` (`DriverService`) | profiles, accounts, memberships, and the state machine; history is written by `season_end_service.write_driver_history_entries_on` |
| The change queue | `change_queue` (`ChangeQueue`), `change_types`, `queue_records`, `setting_change`, `queue_stop_view`, `typed_turns` | `queued_changes`, `queued_change_steps` |
| Timed work | `scheduler_service` (`SchedulerService`), `start_up` | the job store, a file of its own |
| Posting | `output_router` (`OutputRouter`), `notice_handler`, `standing_post_handler`, `owned_channel_handler`, `standing_posts`, `retry_service` | `pending_messages` |
| The record | `audit_service` | `audit_entries` |
| Channels | `channel_registry_service` | nothing: it reads core's channel columns, and the modules' through a hook |
| Module switches | `module_service` (`ModuleService`) | nothing: each switch is read through its module |
| The image module | `image_drawing` (the `ImageDrawing` protocol) | nothing: it declares what core asks |
| Amending a round | `amendment_rules_service`, `round_steps` | nothing: the first judges what an amendment may do and costs, pure; the second names a round in an amendment's step by the number it bears when read |
| A points amendment | `amendment_service` | nothing: `approval_faults`, what stops a mid-season points amendment being published, which results calls |
| Season reviews and confirmations | `approval_checks`, `approval_window_service` (`calendar_faults`, what is already behind a season on the day it is approved), `review_sections`, `confirmation_refusals`, `season_fingerprint_service`, `season_review_changes`, `season_review_views`, `season_review_workflows` | `season_review_prompts` |
| The hub | `hub_service`, `about_service` | `server_configs.hub_message_id` |
| Test mode | `test_mode_service`, `test_roster_service`, `backup_service` | `server_configs.test_mode_active` |
| Starting over | `pack_service`, `factory_reset_service`, `in_memory_state` | — |

The change types core registers each sit in a file of their own beside these (`*_change.py`,
`*_changes.py`, `bot_settings.py`, `division_settings.py`, `test_mode_settings.py`), and are listed
under "How a change is carried out".

**`season_lifecycle_service` is module functions, not a class on the bot.** It reads the database
directly so that the signup window's forced close can call it with nothing but a path (its
docstring). The same holds for the other services of plain functions: they are handed what they
need by whoever calls them, as architecture.md's "Services stay attached to the bot" has it.
`approval_faults` is handed core's module switches (`ModuleService`) by results' cog and change
type. A cog's reads are asked of a core service, as architecture.md's "What each part may do" has
it: `pack_service.what_stops_a_pack`, `track_service.list_tracks` and `resolve_track`,
`PlacementService.division_lineup`. `/season config-review` reads `bot.configuration_faults`, the
reader the builder makes (`_make_configuration_faults`), which asks
`approval_checks.configuration_faults` with the services, signup's among them, as they stand when it
is asked, so that core reaches the module only as architecture.md's "How modules and core fit
together" has it.

**Core's models** (`core/models/`) are plain data and the enums the schema ties to: the season's
status and stage, a division's and a round's status, the driver's state, the change queue's
vocabulary (`change.py`), the dependency table (`module_dependencies.py`) and `Refused`
(`refusal.py`).

**Core's helpers** (`core/utils/`) are the bot's type (`league_bot.py`), the one-league claim and
the base classes for the command tree, buttons and forms (`league_server.py`), the hook types
(`hooks.py`), the failure path for interactions (`interaction_errors.py`), the refusal and the
standard log lines (`log_lines.py`), `bounded_autocomplete` (`autocomplete.py`), the posting guard
(`in_a_job.py`), and plain formatting, parsing and time helpers.

**The database code** is `core/db/database.py`: `get_connection`, which lends idle connections
again, `close_connections`, `run_migrations`, and the helpers `sole_row` and `inserted_id`.

### The entry point

`__main__.py` builds the bot. `build` makes the services and the hooks, signs the modules up to
every hook several of them join (the `_sign_up_to_*` functions, each naming the modules' functions
in the order the hook calls its subscribers), and calls each module's wiring (`<module>/wiring.py`,
`architecture.md`, "How the code is laid out") for the rest of that module's part: its own hooks
(`sign_up`), its switch (`register_switch`, through `register_module_switches`), its change types
(`register_change_types`) and its kinds of timed job (`register_scheduler_callbacks`). It signs up
core's own About option (`register_hub_options`), loads the cogs, registers the persistent views and
sets the start-up steps (`_start_up_steps`), all before the bot connects. Beside core's own part,
the builder keeps what joins modules: every service, every hook and the order of its subscribers, a
hand-in from one module to another, the cogs and views, and the order of the steps. Core has no
wiring of its own. `test_build_returns_every_service_complete` and
`test_every_kind_is_registered_before_the_scheduler_starts` pin that. `LeagueBot`
(`core/utils/league_bot.py`) declares every attribute the builder attaches, each hook among them,
and `test_every_attribute_bot_py_attaches_is_declared` holds the two equal.

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

`retry_cog` holds no command: it runs the retry loop for log lines (below). No module's command
sits under one of these groups.

---

## How a change is carried out

Answers to `architecture.md`, "How a change is carried out", which designs the queue; the core
specification's section of the same name holds what a league is told.

### The queue's code

The queue is `ChangeQueue` (`core/services/change_queue.py`), and it is one class: asking, the
dispatcher, a change's start and end, a step's run, the stop and its notice, Retry and Discard, the
audit and the held reply are one state machine around one lock order (`_working[lane]`, then
`_asking`, then `_writing`) and the attributes they share. *Rejected:* mixins, which would split the
file and leave one state machine whose lock order no single class states; and collaborator objects,
which would reach into the queue's private state or be handed all of it (`ChangeQueue`'s docstring).
What reads none of that state is out of the file. `core/services/change_types.py` holds the types a
change is made of, `Step` and `ChangeType` with the contexts a check, a step and an outcome are
handed, and the decisions that govern them (a post's id saved with its mark, a step's four kinds, a
job that ends on a fault); `core/services/queue_records.py` holds the bookkeeping over the queue's
tables, what is in hand, what was ever asked, the job numbering and the emptying of a restored
database, which a check, a recovery, a restore and a factory reset call without a queue. The queue
file keeps the constants the class reads: `RETRY_AFTER`, the bot's own tries;
`CONVERSATION_LANES_AT_ONCE`; `UPDATABLE_FOR`, how long a reply may be updated;
`WORKER_PAUSE_AFTER_FAULT`; and `NOTICE_REPOST_EVERY`; with `_confine` and `QUEUE_TABLES`. The plain data is in `core/models/change.py`: `StepKind` (`SAVE`,
`ACT`, `DELETE`, `EDIT`), `Verdict`, `StepResult`, `StepFailedOnDiscord`, `GuildUnavailable` and
the lane names. The queue's module docstring holds the engineering decisions
that govern the queue, each pinned by a test in `tests/core/test_change_queue.py`, `test_change_queue_lanes.py` or
`test_change_queue_controls.py`. Among them:

- **One save at a time across lanes**, under an in-process lock (`ChangeQueue._writing`) held for
  the save alone, with a fixed lock order. *Rejected:* one worker for every lane, which holds a
  conversation behind the league's Discord calls; and SQLite's own busy wait, which gives up where
  a save is held longer (the module docstring; `test_two_lanes_saves_never_overlap`).
- **A post's id is saved with its mark** (`Step.record`). *Rejected:* a `SAVE` job after each post
  to write its id, which doubles the jobs a change numbers and stops on (`change_types`' docstring).
- **A change starts only if it is still queued:** the claim that sets it running is conditioned
  on its state in the statement, so a change ended while its check ran does not start
  (`test_a_change_discarded_while_its_check_runs_does_not_start`).
- **A kept reply is the same entry held in memory**, let go once it can no longer be updated.
  *Rejected:* a second table of replies (`ChangeQueue.keep_reply`'s docstring;
  `test_a_kept_reply_no_later_change_takes_over_is_let_go_once_it_cannot_be_updated`).

The stop notice's buttons are one persistent view, `QueueStopView`, registered as the queue
starts. A message typed into a conversation's channel is answered through `typed_turns`.

### A league's settings

Every settings command, core's own included, declares a `Setting`, and `setting_change` builds its
change type, so the line, the audit entry, the answer for a value already held and the answer for
a fault are formed in one place. `setting_change`'s docstring holds the shape and its rejected
alternatives, each pinned by a test in `tests/core/test_setting_change.py`.

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
| `core.test_mode_toggle` | `/test-mode toggle` | the flag, the modules' part, the audit entry; turned off, every fake driver deleted | forecasts cleared, the backup deleted |
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
`module.off:<name>` (`module_on` and `module_off` in `core/models/change.py`), which `ModuleCog`
asks for by that kind.

Each file's docstring holds its change's check, its steps and the reason for each. Three shapes
recur:

- **A save that judges again.** A change whose state may move between its check and its save
  judges again on the save's connection; a refusal found there writes nothing and ends the
  change rather than stopping the lane (`core.round_amend`, `core.driver_reassign`). An approval or
  a mid-season confirmation retried after a fault plans its judgement again before it saves.
- **A fault in a job stops the lane**, save in `/bot init`'s save, a `Setting`'s save and the
  steps after it, and a role move (`role_moves.role_move_step`), each of which ends on a fault
  instead. `/season setup`'s
  docstring says why its save stops the lane where `/bot init`'s does not.
- **A step the league cannot discard** is marked so where dropping it would leave a season or a
  round stuck: the approval's arming, an amendment's arming, a cancellation's save, a
  re-arming's two jobs, and a review's `forget`. The mid-season confirmation has none, for the
  reason its docstring gives.

---

## Timed work and restarts

Answers to `architecture.md`, "Timed work and restarts". What each kind of job does when missed is
the core specification's ("When the bot stops").

### The scheduler service

`SchedulerService` (`core/services/scheduler_service.py`) is the one place a job is armed, found or
removed. **Its job store is a SQLite file of its own**, apart from the league's database, for the
reason its module docstring gives: the store writes synchronously on the event loop's thread.

Every job runs through one module-level callable, `_one_off_timer_job`, which looks up the handler
the builder registered for the job's kind (`register_timer`) and hands it the job's key: a round's
id, a one-off timer's key, or nothing for a standing job. A kind with no handler is passed over with
a warning. **A job runs in an empty context** (`_JobScheduler`), so it can never take an
interaction for its own
(`test_a_job_scheduled_from_an_admitted_interaction_finds_no_member_to_warn`).
A one-off timer is armed by `arm_once` (`architecture.md`, "Timed work and restarts"). A round's
job is named from the round's id and its kind (`_round_job_suffix`).

### Core's own timed events

Core has one timer of its own, `review_expiry`. A review's `arm_expiry` job arms it from the moment
the question's job was marked done, so the window starts when the question appears; how long the
window is, `REVIEW_WINDOW`'s comment says. When it fires, `review_expiry_timer` reads the row again
and asks `season.review.take_down`, or nothing where the review is gone or already being taken down.
What became of a review's button is held in memory (`_PRESSES`), for the reason its comment gives.
**Missed while the bot was down**, the timer fires on start, and finds the review already in hand:
start-up's step for expired review prompts asks the take-down first, since the scheduler is paused
until start-up ends.

**A round's timed work is armed by three changes**: the season's approval (its `ARM` job), and the
amendment and re-arming of a round (`round_amend_change`), through the scheduler service's arming
functions. Each reads when a round's events fall from `round_event_moments`
(`core/services/round_moments.py`), which is pure: no database, no clock, no scheduler. A
cancellation removes a round's work (`cancel_round`, `cancel_rounds`) before its save. The handlers
for a round's kinds are the modules'.

### Start-up

`main()` reads the settings, swaps in a staged restore, applies the migrations, builds the bot and
connects it, in that order (`test_migrations_run_before_the_bot_connects`). When Discord first
reports the bot ready, `start_up` (`core/services/start_up.py`) runs once per process:

- **It starts the scheduler paused**, so the job store can be read while no job runs, and resumes
  it last, in a `finally`, so a queue that fails to start does not hold the jobs paused.
- **It runs each step in turn, each contained**: a step that raises is logged and the next runs.
- **The queue's start is its last step.** `ChangeQueue.start` registers the stop notice's view,
  leaves every stopped job stopped, delivers lines never tried, posts any stop notice that never
  landed, and starts the dispatcher.
- **A guard of its own** makes a second ready, after a network drop, run nothing.

Each is pinned by a test in `tests/core/test_start_up.py`; `ChangeQueue.start`'s docstring says
what the queue's start does.

The steps are a fixed tuple the builder makes (`_start_up_steps`), in an order its comments give
where the order matters (`test_the_builder_orders_the_start_up_steps`). Each module's recovery is
a function of its wiring, which the builder names in the step; core's own, the review prompts and
the hub, are the builder's. Results' amendment sweep is armed by
`results.wiring.arm_amendment_sweep` and signup's wizards recovered by `signup.wiring.recover_wizards`,
each a function of its module's wiring like the recoveries. The missed clean-ups are one step the
builder composes of weather's part and attendance's:

| # | Step | What it does |
|---|---|---|
| 1 | amendment sweep | arms results' standing sweep of lapsed amendments |
| 2 | portrait refresh job | arms the image module's daily refresh where it is on |
| 3 | signup close timer | closes a signup window whose close passed, or arms its timer again |
| 4 | orphaned amend channels | asks each open amendment's lapse; before step 5, for the reason its comment gives |
| 5 | orphaned submission channels | asks each open submission's review or restart |
| 6 | expired review prompts | core's: asks each standing review's take-down, and routes its Approve view again |
| 7 | missed phases | asks each weather phase whose moment passed |
| 8 | missed check-in calls | asks each check-in call that fell due |
| 9 | RSVP views and deadlines | routes each check-in message's view again and asks each deadline passed |
| 10 | missed clean-ups | asks each forecast's and check-in's clean-up that fell due |
| 11 | hub | core's: routes the hub's buttons again and asks the panel's refresh |
| 12 | wizards | arms each signup wizard's inactivity timer again |

The retry loop for log lines is no step (`architecture.md`, "Start-up runs once, in one
function"): core's is `RetryCog.retry_loop` (`retry_cog`), started by `LeagueBot.setup_hook`
(`test_login_starts_the_retry_loop_once`).

### The clock

The queue's clock, `ChangeQueue.now`, is the bot's: the builder hands services, change types and
the log writer `now=lambda: bot.change_queue.now()`. In the running bot it is the wall clock in UTC;
a test hands the queue a clock of its own.

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
| A module's cancellation notice | notice | the module's channel | `cancellation_notice_service.post_cancellation_notice`, which the modules' jobs call | not kept |
| A log line | log line | `server_configs.log_channel_id` | `OutputRouter.post_log`, or `queue_log_on` in a step's save | `pending_messages`, until delivered |
| The stop notice | log line with buttons | the log channel | `ChangeQueue._post_notice` → `OutputRouter.post_notice` | `queued_change_steps.notice_message_id` |

A replaced standing post is deleted by a job after the new one, built by `standing_posts`
(`replaced`, `delete_replaced_step`), which also removes a copy a failed `record` left
(`remove_kept`). Its docstring holds the shape;
`test_the_text_lineup_is_posted_before_the_old_one_is_deleted` and
`test_moving_the_hub_posts_the_new_panel_before_deleting_the_old` pin it.

**Where a picture falls back to text** (Constitution XIV, rules 7 and 8): the lineup's in
`LineupPoster._refresh_lineup_post`, the review's in `season_review_changes`' `draw_and_post`,
and the calendar's in `post_division_calendar`, whose `commanded` parameter tells a posting a member
asked for from one that was not (`test_an_uncommanded_posting_falls_back_to_the_text`). The lineup's
and the calendar's post job ask for text on every try after the first (`as_text`).

**Who a post may mention is stated where it is sent** (`architecture.md`, "Posting to Discord");
who that is, is the core specification's ("The record of what changed", "How a change is
carried out"). The stop notice's mention is decided by `ChangeQueue._admins_to_notify`, and a log
line's mentions are wrapped by the router (`test_every_mention_in_a_log_line_is_written_as_code`).
`post_cancellation_notice` posts with the mentions its caller, a module's notice job, hands it.

**Core's posts off the queue** are `architecture.md`'s exceptions ("Every post is made in a
job"), in `OutputRouter`, `ChangeQueue._update_reply`, `typed_turns` and `BotCog`'s factory reset.
`/team lineup` answers the member with its picture as a reply and records nothing.

---

## How modules and core fit together

Answers to `architecture.md`, "How modules and core fit together".

### The hook types

`core/utils/hooks.py` declares `Hook`, whose subscribers are plain functions, and `AwaitedHook`,
whose subscribers are awaited one after another; each `call` returns what each subscriber handed
back, in the order they signed up. A subscriber that raises is not caught: the caller's own failure
path has it (the module docstring;
`test_a_hook_calls_its_subscribers_in_order_on_the_handed_connection`).
The builder makes every hook, signs the modules up and attaches it to the bot; a cog reads it off
the bot and hands it on, and a change type is handed it by the builder.

### The hooks core declares

Each is declared on `LeagueBot`, whose comment on it says what it hands over and what core does with
the answers. "A" is an `AwaitedHook`, "P" a plain `Hook`; the subscribers are in sign-up order,
which each `_sign_up_to_*` docstring states where it matters.

| Hook | Type | Subscribers | Core calls it in |
|---|---|---|---|
| `round_windows_read` | A | attendance, weather | `round_moments.read_round_windows` |
| `phases_drawn_read` | A | weather | `round_moments.read_phases_drawn` |
| `round_amended`, `round_clean_ups_due` | A | weather, attendance | `round_amend_change` |
| `round_armed` | A | attendance, weather | `round_amend_change` |
| `amendment_steps` | P | weather, attendance | `round_amend_change` |
| `amendment_step_failure` | P | weather | `round_amend_change` |
| `cancellation_refused` | A | results | `cancellation_changes` |
| `rounds_called_off` | A | results, attendance | `cancellation_changes` |
| `cancellation_notices` | A | attendance, weather, results | `cancellation_changes`; results' not-held round end calls it too |
| `cancellation_steps` | P | results, attendance (three), weather, results | `cancellation_changes` |
| `round_ended_not_held` | A | attendance | — (results' not-held round end calls it) |
| `rounds_made_final` | A | results | `season_service` (`end_rounds_awaiting_results_on`, `close_raced_rounds_on`) |
| `placements_confirmed`, `placements_confirmed_steps` | A, P | results, attendance | `season_approval_change` |
| `configuration_confirmed` | A | signup | `configuration_confirm_change` |
| `confirmation_refused` | A | results | `confirmation_refusals` |
| `review_sections_read` | A | signup, attendance, results, weather | `review_sections` |
| `division_lines_read` | A | results, attendance, weather | `review_sections` |
| `division_channel_faults_read` | A | weather, results, attendance | `approval_checks` |
| `driver_names_read` | A | signup | `approval_checks` |
| `fingerprint_areas_read` | A | weather, results, attendance, signup, image | `season_fingerprint_service` |
| `channel_uses_read` | A | weather, results, attendance, signup | `channel_registry_service.find_channel_use` |
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
| `test_mode_flushed` | A | weather | `season_end_service` |
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

`ModuleService` reads no module's table or column; `is_<module>_enabled` and `is_enabled_on` are
the readers `architecture.md` names ("How modules and core fit together").

### The dependency table

`NEEDS` in `core/models/module_dependencies.py` holds one entry, attendance needing results; `needs`
and `taken_down_with` read it. Its names are the modules' folder names, the ones `.importlinter`
uses, so the table and the import rules cannot drift apart
(`test_every_module_is_under_the_rules_between_modules`). A name it does not hold needs nothing,
which is how `/module`'s "images" reads (the docstring). `ModuleCog` reads it to refuse an enable
whose dependency is off and to warn of what a disable takes down; the builder hands results'
switch-off attendance's, for the cascade.

### The image protocol

`ImageDrawing` (`core/services/image_drawing.py`) declares what core asks of the image module, and
the image module's class of the same name implements it; the builder makes it and attaches it as
`bot.image_drawing`. Its methods fall in five groups:

- **Capacity questions**, each answering what the template cannot hold, or nothing: the four
  placement guards (`TemplateCapacityService`), a round's (`round_add_changes`), and the season review's.
- **Gates and pictures**: `calendar_wanted`, `draw_calendar`, `lineup_wanted`, `draw_lineup`,
  `draw_lineup_for_command`, `draw_review_lineup`, `discard_picture`. The review's lineup is drawn
  through `draw_review_lineup`, as the builder's drawing of a review's picture asks it, so that
  every picture core draws is drawn through this protocol and a change to the protocol reaches each
  of them. What a gate that cannot be read answers is `architecture.md`'s
  ("Errors and failures").
- **The review's image section and its faults**: `rasteriser_fault`, `review_section`,
  `template_faults`, `colour_shortfall_problems`, `portrait_fault`. The image module words each;
  core decides which refuse. The module docstring says which faults each lets out.
- **The fingerprint's part**: `artwork_signature`.
- **Portraits**: `discard_portraits`, for a reassign. A season's driver pass discards through the
  `drivers_deleted` hook instead.

`TemplateCapacityService` and `LineupPoster` are handed the drawing by the builder; the cogs read it off the bot.

---

## Errors and failures

Answers to `architecture.md`, "Errors and failures", which names each failure path and every
stated exception, core's among them; each docstring there named carries its reason.

| Starting point | Core's code |
|---|---|
| A command, button or form | `LeagueCommandTree.on_error`, `LeagueView.on_error`, `LeagueModal.on_error` (`core/utils/league_server.py`), each calling `report_failure` (`core/utils/interaction_errors.py`) |
| A Discord event | `on_event_error` (`core/utils/league_server.py`), attached by the builder with `setattr` |
| An autocomplete | `bounded_autocomplete` (`core/utils/autocomplete.py`) |
| A change | `ChangeQueue._start` and `_run_step`, leading to `_stop`, or to `on_fault` or `on_check_fault` where the type gives one |
| A scheduled job | `_one_off_timer_job` (`core/services/scheduler_service.py`), which logs a handler's fault on the host |
| A start-up step | `start_up`, each step contained |
| The retry loop | `RetryCog.retry_loop` and `retry_service.attempt_delivery` |

`report_failure` never raises: its reply and its line are each tried on their own. Its docstring
gives the reason for the reply's words. The tree steps aside for a command with its own error
handler (`test_a_command_with_its_own_error_handler_is_left_alone`). `on_event_error` drops the
listener's own arguments, which can hold a member's message, and never raises
(`test_the_event_error_handler_never_raises`).

**The log writer contains its own faults**, as `architecture.md` ("Posting to Discord") has it:
in `OutputRouter._read_config`, `_try_send` and `_enqueue_if_configured`
(`test_a_database_fault_reading_the_log_channel_is_contained`). `log_destination` alone raises,
being a query rather than a line posted (its docstring).

**Core's refusal and fault types**, each caught by name:

| Type | Where | What it is |
|---|---|---|
| `Refused` | `core/models/refusal.py` | a refusal whose text the member reads; subclasses `ValueError` |
| `PackRefused` | `core/services/pack_service.py` | `/bot pack`'s refusals, worded by the cog from its fields |
| `InvalidDriverTransition` | `core/models/driver_profile.py` | a fault: a move past the transition table, caught by a caller that expects the race |
| `InvalidStageTransition` | `core/models/season.py` | a season asked to move to a stage its lifecycle does not allow from where it is |
| `SwitchNotSignedUp` | `core/services/module_service.py` | a fault in the bot's assembly (`architecture.md`, "How modules and core fit together") |
| `StepFailedOnDiscord` | `core/models/change.py` | a step's failure on Discord, carrying what the job keeps and a clause for the stop notice |
| `GuildUnavailable` | `core/models/change.py` | the league's server missing from the cache: a failure on Discord's side |

---

## Constraints a later reader might tune away

Each is stated where it lives and pinned by the test named.

| Constraint | Stated in | Pinned by |
|---|---|---|
| SQLite's busy timeout is five seconds, sized for the longest legitimate write; an autocomplete asks for a shorter one (`AUTOCOMPLETE_TIMEOUT_SECONDS`) | `core/db/database.py`, `_BUSY_TIMEOUT_SECONDS` | `test_connections_carry_an_explicit_busy_timeout` |
| The database runs in WAL with `synchronous` left at FULL, durability chosen over speed | `_enable_wal` | `test_migrations_leave_the_database_in_wal_mode`, `test_connections_keep_full_durability` |
| Four idle connections are kept to be lent again | `_IDLE_CONNECTIONS_KEPT` | `test_no_more_than_four_idle_connections_are_kept` |
| `server_configs` holds one row; the claim's condition is in its statement, so two servers cannot both win; releasing the claim keeps the row | `config_service`'s docstring, `ConfigService.claim_on` | `test_a_single_row_table_refuses_a_second_row`, `test_only_one_server_claims_a_released_row`, `test_releasing_the_claim_keeps_what_is_the_league_s` |
| One live season, held by the schema rather than each reader | `idx_seasons_one_live`; `tests/core/test_one_live_season_per_server.py`'s docstring | `test_a_second_live_season_is_refused` |
| A placed driver's seat stops a division's delete | `delete_division_on` | `test_a_division_with_a_driver_placed_in_it_is_not_deleted` |
| A reassign judges every refusal on its save's connection before it writes | `DriverService.reassign_judged_on`, `driver_changes`' docstring | `test_a_failed_driver_reassign_changes_nothing`, `test_a_refusal_found_inside_the_save_ends_the_reassign_and_the_lane_goes_on` |
| `write_transition` is the one writer of a driver's state | `write_transition` | `test_every_reset_goes_through_the_transition_table` |
| The driver has no ban states | `DriverState` | `test_the_driver_has_seven_states_and_no_ban_among_them` |
| The scheduler starts paused and is resumed last, so a restart's take-down of a review comes before its timer | `core/services/start_up.py` | `test_no_job_runs_until_start_up_has_ended` |
| A fingerprint that cannot be taken is empty, and differs from every fingerprint | `season_fingerprint_service.take_fingerprint` | `test_a_fingerprint_that_could_not_be_taken_differs_from_everything` |
| A review's expiry asked twice posts one notice | `season_review_changes.claim_take_down` | `test_a_review_expired_twice_at_once_posts_one_notice` |
| A season starts on the UTC date of the queue's clock | `season_setup_change`'s docstring | `test_the_start_date_is_the_utc_date` |
| Every store of league state in memory is cleared by pack and factory reset, or named as holding none | `core/services/in_memory_state.py` | `tests/core/test_in_memory_state.py` |

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

**One writer of a driver's state.** `ALLOWED_TRANSITIONS` (`core/services/driver_service.py`) is the
table, and `write_transition` is the one statement that writes `current_state`: it raises
`InvalidDriverTransition` for a move the table does not allow, having written nothing, and commits
nothing. Every caller changing a state inside a larger write calls it rather than the update
(its docstring). `DriverService.transition_on` creates a profile where none exists and the table
allows the move from Not Signed Up; no transition deletes one.

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
and the audit entry. Turning it off is one save too (`switch_test_mode_off_on`), deleting every fake
driver through `delete_driver_profiles`, followed by jobs that clear the forecasts posted under test
mode and delete the saved backup. A season's end switches it off in its own `end` save. Whether a
real driver stands in the way is counted by `count_live_real_drivers`. `/test-mode nationality` is a
`Setting`.

**Five more test-mode commands are each a change of their own, in one shape:**
`/test-mode set-former-driver` (`core.test_mode_former_driver`) and the four roster commands of
`test_roster_changes` (`core.test_roster_add`, `core.test_roster_add_bulk`, `core.test_roster_remove`,
`core.test_roster_clear`). Each carries what the command was given, is judged without a write at the ask and
again as it starts, and is one save of its rows and their audit entries, the roster's writers
taking the connection of the save and committing nothing. What the reply of a discarded save needs
is read into the payload at the ask (`fill`), and a fault in a save stops the lane. What each kind
judges and writes is its docstring's. A roster is measured against the template capacities with the
bot's own template capacity service, the one carrying the image module's drawing, as a real
placement is,
and one the file shows wrong on its own is refused by the form before the ask.

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
