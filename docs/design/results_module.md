# Results and standings — why the code is shaped as it is

This file says how the results and standings module's code is laid out and why: its tables, its
services and its command group, its changes on the queue, its timed work and start-up, what it
posts, how it meets core and the other modules, how it fails, and the constraints a later reader
might tune away. Every path is under `src/leaguebot/` unless it says otherwise.

It cites rules rather than restating them. What binds every module is `architecture.md`'s, cited by
its section heading; core's side of a seam is `core.md`'s; what a league sees is the results
specification's (`docs/wip-specs/results_module_specification.md`), cited by its section heading.
A constraint on one function lives in that function's docstring, pinned by its test, and is named
here only where the shape depends on it. Where this file and the code disagree about what the code
does, the code wins, and the file is corrected at close-out.

---

## The database

Answers to `architecture.md`, "The database".

### Results' tables

Results owns twenty-seven tables (`TABLE_OWNER` in `tests/repository/test_architecture_rules.py`),
all declared in `core/db/migrations/001_baseline.sql`. Every one of them is written on a connection
the change queue or a core hook hands in: nothing in `results/` commits.

**The submission and amendment conversations**

| Table | One row is | Held to it by |
|---|---|---|
| `round_submission_channels` | a round's results submission and its review: the channel, the flags of where it stands (`closed`, `in_penalty_review`, `results_posted`, `resubmitting`), who started a resubmission, and the ids of the review's prompt, the appeals prompt, the approval question and the resubmission's announcement | `UNIQUE (round_id)` |
| `round_amend_channels` | one amendment of a round's results: the channel, the sessions re-entered, the round as it stands before stage one, the deadline, `closed_at`, the announcements its report stage supersedes, who opened it, when its report stage is approved, when its first prompt appears | `UNIQUE (round_id)` |
| `resubmission_drafts` | one session's paste in a resubmission, held until the swap | keyed by round and session |
| `amend_drafts` | one session's paste in an amendment | keyed by amendment and session; goes with its amendment by cascade |
| `review_lock_warnings` | one "channel locked" warning waiting to be taken down | keyed by the warning's message id |

**Session results and the driver rows**

| Table | One row is | Held to it by |
|---|---|---|
| `session_results` | one session of a round as submitted: its status, the points configuration it is scored by, who submitted it, the fastest-lap override, and the messages its table occupies (the anchor and the chunk list) | `UNIQUE (round_id, session_type)` |
| `qualifying_session_results` | one driver's row in a qualifying session, naming the division's team, never its role | goes with its session by cascade |
| `race_session_results` | one driver's row in a race session, with the game's, the review's and the appeals' time penalties kept apart | goes with its session by cascade |

**Verdict records and banners**

| Table | One row is | Held to it by |
|---|---|---|
| `penalty_records` | one verdict of a penalty review on one driver's row, with the channel and messages announcing it | — |
| `appeal_records` | one correction of an appeals review, likewise, with its status | — |
| `verdict_banner_messages` | one banner heading a round's verdicts, and whether an attendance sanction card stands beneath it (`heads_sanctions`) | — |

Neither verdict table names the driver or the session: both are read off the driver row the
verdict points at, through the one join `verdict_records` holds (its module docstring says why
there is one).

**Standings snapshots and the round's standings posting**

| Table | One row is | Held to it by |
|---|---|---|
| `driver_standings_snapshots` | one driver's place in a division's championship as at one round | `UNIQUE (round_id, division_id, driver_user_id)` |
| `team_standings_snapshots` | one team's, likewise | `UNIQUE (round_id, division_id, team_instance_id)` |
| `round_standings_posts` | the standings a round posted: each championship's anchor and chunk list | keyed by the round |
| `classification_posts` | a division's opening or final classification as posted, each championship's anchor and chunk list | `PRIMARY KEY (division_id, occasion)` |

A round's standings posting is a row of its own, keyed by the round, and not a column on the
snapshot of whoever leads it: a posting's ids sit on a row whose identity is the posting
(`architecture.md`, "Posting to Discord", "Every post keeps three things"), and a recomputation
reorders and drops snapshot rows. `set_standings_message_id_on` writes it and
`_drop_superseded_driver_rows` leaves it alone (their docstrings). A division's opening and final
classifications have no round to key them by, so their ids sit in `classification_posts`, keyed by
the division and the occasion, written in the record that marks their posting done.

**Called-off notes**

| Table | One row is | Held to it by |
|---|---|---|
| `called_off_notes` | the note results posted of a round or a division called off, so that the switch-off can take it down | `uq_called_off_notes_target`, one note for each thing called off |

A season's note has no row: the switch-off reads the live season alone (`cancellation_notice`'s
module docstring).

**Points configurations, the season's points and the modification store**

| Table | One row is | Held to it by |
|---|---|---|
| `points_config_store` | a named points configuration the league holds | `config_name UNIQUE` |
| `points_config_entries` | the points one position of one session type earns in it | `UNIQUE (config_id, session_type, position)`; goes with its configuration by cascade |
| `points_config_fl` | one session type's fastest-lap bonus and position limit in it | `UNIQUE (config_id, session_type)`; goes with its configuration by cascade |
| `season_points_links` | a configuration attached to a season, by name | `UNIQUE (season_id, config_name)`; no key to the store |
| `season_points_entries`, `season_points_fl` | the season's own copy of an attached configuration's points, taken at its approval | unique over season, name and session (and position) |
| `season_amendment_state` | whether a season's points amendment mode is on, and whether its working copy differs | keyed by the season |
| `season_modification_entries`, `season_modification_fl` | the staged working copy of the season's points | as the season's own copy |

**A link to a configuration is a bare name**, and removing a configuration takes only the links of
a season in setup with it, for the reasons `points_config_service.remove_config_on`'s docstring
gives (`test_remove_config_leaves_an_approved_seasons_link_alone`). **The season scores from its own
copy**, taken by `snapshot_configs_to_season_on` inside the approval's save; only the approval of a
points amendment replaces it (`season_points_service.install_staged_points_on`).

**Results' settings**

| Table | One row is | Held to it by |
|---|---|---|
| `division_results_config` | one division's results, standings and verdicts channels, and whether its standings show reserves | keyed by the division |
| `results_module_config` | results' switch | `CHECK (id = 1)` |

`division_channels` writes each channel by an upsert naming its one column, and gives the channel it
replaced back (its module docstring).

### Results' own columns on core's tables

None. `OWN_COLUMNS` and `READS_OF_OWN_COLUMNS` in `tests/repository/test_architecture_rules.py` name
no column of results'. Results keeps its switch in its own settings table, as `architecture.md`'s
"Each table is read and written by its owner alone" has every module do but weather and signup. A
round's status is core's: results moves it through core's writers (`set_round_status_on`,
`end_round_not_held_on`, `end_rounds_awaiting_results_on`), and reads core's tables through core's
readers (`season_service.round_facts_on` among them).

### Links, and what stops a delete

No link of results' own is there to stop a delete. Results' tables refer to `rounds`, `divisions`,
`seasons`, `team_instances` and `driver_profiles` with no delete action, and inside results only the
driver rows (from `session_results`), the configuration's points (from `points_config_store`) and
an amendment's drafts (from `round_amend_channels`) go by cascade. The verdict records refer to the
driver rows with no delete action, so every path that removes driver rows removes or re-points
their verdicts first: the purge deletes them ahead of the sessions (`_delete_rows`), and an
amendment detaches and re-points them around its rewrite. An amendment that would leave a verdict
with no row to point at is refused (`AmendmentWouldOrphanVerdictError`). *Rejected:* deleting such a
verdict, which discards the record of why the driver lost places, and leaving it orphaned, where no
read could find it again (its docstring).

### Results deletes its own rows

Results signs up to every one of core's delete hooks (`core.md`, "Each module deletes its own rows")
and works on the delete's own connection, in its save, committing nothing:

| Hook | `results_purge_service`'s function | What it does |
|---|---|---|
| `rounds_deleted` | `rounds_deleted_on` | deletes the rounds' sessions with their verdicts, their standings and posting, their submission and amendment records, drafts, banners and called-off notes |
| `divisions_deleted` | `divisions_deleted_on` | deletes the divisions' channel settings, called-off notes and classification postings |
| `season_deleted` | `season_deleted_on` | deletes the season's copy of its points, its amendment state and its working copy |
| `profiles_deleted` | `profiles_deleted_on` | lets go of the drivers' profiles in the driver rows and standings, which keep naming them by account |
| `profiles_merged` | `profiles_merged_on` | moves the absorbed driver's rows onto the driver kept |
| `bot_packed` | `bot_packed_on` | clears the ids of the submission prompts, by which they would be edited |

### What a restart loses

What a penalty review stages (penalties, pardons and corrections) is held in memory, as the results
specification's "Penalties and appeals are settled in the submission channel" lets a restart lose
it, and as `architecture.md`'s "How a change is carried out" allows of state a specification lets a
restart lose. A form or a press hands the review it stands for to `hold_review`
(`results/services/review/review_state.py`, keyed by round and stage), and the staging change reads
it there when it runs; a restart finds none, and the staging is then no longer due, as one staged
against a prompt a restart replaced is (`review_stage_change`'s docstring;
`test_a_staging_against_a_replaced_prompt_is_not_due`). What a change refuses is still read from
the database and the queue. Once Approve is pressed, what is staged travels in the approval's
payload, so a stop loses none of it. `clear_held_reviews` empties the store on `/bot pack` and
`/bot factory-reset` (`in_memory_state_cleared`;
`test_clearing_a_leagues_state_drops_the_penalty_reviews_held_for_staging`).

The drafts are rows, and outlive a restart, but a restart ends the work that held them: a
resubmission cut short is ended by the review's opening (results specification, "Penalties and
appeals are settled in the submission channel"), and an amendment open at the restart is lapsed
(results specification, "Amending a submitted session"), each clearing its drafts in its own save.

---

## How the code is laid out

Answers to `architecture.md`, "How the code is laid out". Results' code sits in `results/cogs/`,
`results/services/`, `results/models/` and `results/utils/`, beside `results/wiring.py`.

### Services, by topic

Results' services are grouped by topic into seven packages under `results/services/`, as
`architecture.md` lets a module's design file group them. The two amendments sit apart:
`round_amendment/` amends a round's results, and `points/` holds the season's points-amendment mode
beside the configurations it amends. The table names a service by its file.

| Folder | Services | Chiefly writes |
|---|---|---|
| `submission/` | `submission_changes` (a round's opening, the restart of a first paste cut short, a round whose every session is cancelled, and the closing of its channel), `submission_turn_change` (each paste a turn, for a submission and a resubmission, and the configuration's choice), `submission_steps` (the channel made and the lines posted in a dialogue), `resubmission_changes` (the resubmission started and its swap), `result_submission_service` (the submission's state, a session's save, the swap's writer, the configuration picker, the cancellation refusal and the round called off), `paste_reading` (a paste read and judged), `round_start` (what a round's start asks), `submission_recovery` and `review_recovery` (what a restart does to an open submission) | `round_submission_channels`, `resubmission_drafts`, `session_results` and the driver rows |
| `review/` | `review_open_change` (the review opened, its prompt put up, the appeals prompt put back, a stale review closed), `review_stage_change` (a staging), `review_confirm_change` (the approval question), `review_lock_change` (the locked channel's warning), `report_approval_change` and `appeals_approval_change` (the two approvals), `review_posting` (every posting job a review plans), `review_verdicts` (the verdict, sheet and sanction jobs), `review_changes` (what holds a round or a division), `review_prompts` (the prompts and the appeal records), `review_state` (what a review holds, and the store above), `penalty_service` (a penalty validated and applied), `verdict_records` (the verdict join), `verdict_announcement_service` (a verdict or a card posted, and the banners' records), `penalty_wizard`, `penalty_review_views`, `appeals_review_views` (the review's buttons and forms) | `penalty_records`, `appeal_records`, `verdict_banner_messages`, `review_lock_warnings` |
| `round_amendment/` | `amend_changes` (the amendment opened), `amend_turn_change` (each paste a turn, stage one recorded, the channel closed), `amend_end_changes` (the cancel, the lapse, the lapse's timer and the restart's recovery), `amendment_stage_changes` (the two stages approved), `amendment_writes` (the snapshot, the rewrite, the revert, the holds and the sweep), `amend_views` (Cancel Amendment) | `round_amend_channels`, `amend_drafts` |
| `points/` | `points_config_service` (the league's configurations), `points_settings` (`/results config` and the reserves toggle as settings), `points_paste` (a points table pasted at once), `season_points_service` (attaching, the season's copy, validation, the staged install), `amendment_mode_service` and `amendment_settings` (the amendment mode and its settings), `points_amendment_change` (its approval), `points_amendment_faults` (what stops its publication), `scoring` (points applied to a session's rows) | the points tables, the season's copy and the modification store |
| `standings/` | `standings_service` (standings computed, the snapshots and the cascade), `season_standings_posts` (the opening and final classifications, and the season's history reads), `standings_sync_change` | the snapshots |
| `publishing/` | `results_post_service` (a table or standings posted, edited and deleted, the standings posting's and the classifications' ids, the reserves flag, the repost faults), `rounds_sync_change`, `cancellation_notice` (the called-off note) | `round_standings_posts`, `classification_posts`, `called_off_notes` |
| `module/` | `module_switch_settings` (the switch-on and the switch reader), `results_off_change` (the switch-off), `results_purge_service` (the season erased, and the delete, merge and pack hooks), `channel_settings` and `division_channels` (a division's channels), `fingerprint_areas`, `test_mode_steps`, `attendance_hook` (`AttendanceAfterReview`) | `division_results_config`, `results_module_config` |

**Every service is module functions**, and every change type a factory the builder calls with what
it needs (`architecture.md`, "Services stay attached to the bot"): `LeagueBot` holds no service of
results'. A function that needs Discord is handed the bot for that, and the review's state
(`PenaltyReviewState`) carries it for the review's views.

### The cog

`/results` is one group in one cog, `ResultsCog` (`results/cogs/results_cog.py`), composed from one
class for each subgroup (`ConfigCommands`, `PointsAmendCommands`, `ReservesCommands`,
`StandingsCommands`, `RoundsCommands`, `ChannelCommands`), each in a file of its own, as
`architecture.md`'s cog bullet ("What each part may do") has a cog too large to read split.
`results_group.py` holds the one group, `RESULTS_GROUP`, its six subgroups beneath it, and
`ResultsSubgroup`, which declares for the type checker what a subgroup takes from the cog (the
bot and the gates). *Rejected:* a `Protocol` in its place, which cannot be a base beside
`commands.Cog`, whose metaclass it conflicts with (`ResultsSubgroup`'s docstring). `ResultsCog`
keeps what the subgroups share: the bot, the `on_message` listener that turns a message typed in a
round's channels into a turn, and the gates (`_module_gate`, `_answer_nothing_changed`,
`_sync_gate`).

**The subgroups are declared together**, in `results_group.py`, in the order a league sees them,
and each subgroup's class takes its own as a class attribute. Discord is sent a group's children in
the order they join it, so declaring them in one file fixes the order whichever subgroup's file a
process imports first (`test_the_results_subgroups_are_sent_in_their_listed_order`,
`test_the_listed_order_holds_whichever_subgroup_s_file_is_imported_first`). *Rejected:* each
subgroup's file declaring its own, with the cog importing them in order, which holds the order only
while nothing imports a subgroup's file before the cog. `test_results_command_tree.py` pins the
whole tree, and that every command is bound to the cog that is loaded.

### The wiring, the helpers and the models

`results/wiring.py` is results' part of the builder (`architecture.md`, "How the code is laid
out"): `register_switch`, `sign_up` (the hooks results alone joins), a `sign_up_to_*` function for
each hook several modules join, which the entry point calls in their order, `register_change_types`
(handed attendance's share as `attendance` and its switch-off as `attendance_off_on`),
`register_scheduler_callbacks` and `start_up_steps`. It holds no rule and no database code: each
start-up step stands for a function of results' services.

`results/utils/` holds `results_formatter` (one rendering for the text table and the picture, its
docstring says), `points_ordering` (the ordering rule, held once for its four callers) and
`xml_import`. `results/models/` holds plain data: a session's result, a points configuration, a
standings snapshot, the amendment mode's state, `PointsChanged` (what a points amendment's approval
changed) and `RecoveryFailed` (below, "Errors and failures").

---

## How a change is carried out

Answers to `architecture.md`, "How a change is carried out"; the results specification holds what a
league is told.

### The lanes

The code gives a results change one of three lanes:

- **the league lane**, the default: every command's change, every approval, an opening, a swap, a
  lapse, a sync and the switch;
- **the round's lane**, `channel:<submission channel>`, the conversation of a round's submission and
  its review: a paste's turn, the configuration's choice, a resubmission's start, a staging, the
  approval question, the locked channel's warning and its clear, the review's prompt put up, and
  the closing of a submission whose every session is cancelled;
- **the amendment's lane**, `channel:<amendment channel>`: a paste's turn, the configuration's
  choice, a staging and the approval question of the amendment's review, a stage's prompt, and the
  closing of an amendment that ends itself.

A division has no lane of its own. What must wait on a division reads the queue instead:
`review_changes.division_job_in_hand` and `round_in_hand` give a review's, an approval's, an
amendment's or a cancellation's change in hand, a stopped one included. Each conversation's change
type declares the tables it writes (`ChangeType.writes`), and so do the swap of a round's results
on the league lane and every setting.

### What starts each change

The kinds are `results.<area>.<what>`. The jobs in **bold** are each one save, everything the change
must write all or nothing; the posts follow it. A step marked † cannot be discarded.

**A command**

| Kind | Asked by | Jobs |
|---|---|---|
| `results.setting.config_*` (seven), `results.setting.amend_*` (four), `results.setting.reserves` | the `/results config` and `/results amend` commands that change a value, `/results reserves toggle` | a `Setting`: **the value and its audit entry** (`core.md`, "A league's settings") |
| `results.setting.results_channel`, `standings_channel`, `verdicts_channel` | `/results channel …` | a `Setting` that overtakes a stopped job (`Setting.repairs_the_bot`) |
| `module.on:results` | core's `/module enable results` | a `Setting`: **the flag and the audit entry**; then the hub's refresh and the sweep armed, each an `After` step |
| `module.off:results` | core's `/module disable results` | **`switch_off`**: the season erased, the flag down, attendance off where cascaded, the rounds waiting on results closed; `disarm_sweep`; one `take_down` for each message or channel; **`close`** |
| `results.rounds.sync`, `results.standings.sync` | `/results rounds sync`, `/results standings sync` | each round's posts, each followed by the deletion of what it replaces; `close`. Repeatable |
| `results.amend.open` | `/results rounds amend` | `delete_stale`, the channel made and recorded, `post_opening`, the first prompt, `arm_lapse` |
| `results.submission.open` | `/test-mode advance`, through `advance_step` | as under "A timer" |

**A button**

| Kind | Asked by | Jobs |
|---|---|---|
| `results.review.stage` | a form or press of the review | the item applied to the review held in memory; the approval question withdrawn; the prompt redrawn. Repeatable, on the review's channel's lane (the round's, or the amendment's) |
| `results.review.confirm` | No Penalties / Confirm, and the like | `post_approval`, its id saved with its mark. Repeatable, on the review's channel's lane (the round's, or the amendment's) |
| `results.reports.approve` | Approve, stage one | `names`; **`apply`**: penalties, points, the round's attendance through the hook, the snapshots from the round on, the round's status; the posts; the heading and one verdict each; the sheet and the sanctions; `close`† |
| `results.appeals.approve` | Approve, stage two | `names`; **`apply`**: corrections and appeal records, points, snapshots, the round final with its former drivers, the division's status, the submission and amendment rows closed; the posts; the verdicts; `delete_channel`; `close`† |
| `results.resubmission.start` | 🔄 Resubmit Initial Results | **`mark`**†; the prompt and approval question deleted; the announcement posted; `first_prompt`; on the round's lane |
| `results.review.open` (returning cancelled) | the resubmission's Cancel | as under "A follow-on" |
| `results.submission.config`, `results.resubmission.config`, `results.amend.config` | the configuration picker | **the session saved** (or held as a draft) under the configuration chosen |
| `results.amend.cancel` | Cancel Amendment | `names`; **`revert`**: the round put back from its snapshot, the standings recomputed, the drafts dropped, the row closed; `delete_channel` |
| `results.amendment.reports.approve` | Approve, an amendment's report stage | **`apply`**: the superseded announcements noted, the amended sessions' verdicts written back with the points; `close`, which asks the amendment's lane for `results.amend.prompt` |
| `results.amendment.appeals.approve` | Approve, an amendment's appeals stage | `names`; **`apply`**: the appeals, pardons, former drivers, snapshots, attendance recalculated, the snapshot and deadline released, the row closed; the division's rebuild; the superseded verdicts and banners taken down; `delete_channel`; `close` |
| `results.points_amendment.approve` | Approve on `/results amend review` | `plan_names`, one `names` for each division; **`apply`**: the staged points installed, every raced session scored again, every division's snapshots recomputed, attendance recalculated, the audit entry; each division's posts, sheet and sanctions; `close` |

**A message typed in a channel**

`ResultsCog.on_message` routes a message: in a round's channel under review to `ask_lock`; from an
amendment's opener in its channel to `take_amended_paste`; from a league manager in a round's open
submission to `take_paste`. The two pastes are marked ⏳ and asked as turns, or turned away while
an earlier turn waits (`architecture.md`, "Every post is made in a job", the turn-away); a message
typed in a locked review channel is asked as `results.review.lock`, which is no turn.

| Kind | Lane | Jobs |
|---|---|---|
| `results.submission.turn` | the round's | `judge`; `settle`; **`save_session`**: the session's header, rows and points, and, on the last session, `results.review.open` or `results.submission.all_cancelled` asked in the same save; where the season holds several configurations, the picker is posted instead (results specification, "Submitting round results") |
| `results.resubmission.turn` | the round's | as above, with **`save_draft`** in place of the save; the last draft asks `results.resubmission.swap` |
| `results.amend.turn` | the amendment's | `judge`; the paste deleted; **`save_draft`**; the next prompt and its lapse timer armed again (results specification, "Amending a submitted session"). A refused paste's **`end_amendment`** drops the drafts and closes the row, asking `results.amend.close` |
| `results.review.lock` | the round's | the message deleted; the warning posted, its row saved with its mark; `arm_clear`. Repeatable |

**A timer**

| Kind | Asked by | Jobs |
|---|---|---|
| `results.submission.open` | the round's `results` job, through `ask_round_start` | `start_round`†, the round off Not run through core's writer, or, with results off, the round FINAL and the wind-down asked; `create_channel`†, the channel recorded with its mark; `post_opening`; the first prompt |
| `results.amend.lapse` | the `amend_lapse` timer, the amendment sweep, or a restart | `names`; **`revert`**, as the cancel's; `delete_channel`; `record_lapse`. Repeatable |
| `results.review.lock.clear` | the `review_lock_clear` timer | `delete_warning`, its record deleting the row |

**A restart**

| Kind | Asked by | Jobs |
|---|---|---|
| `results.review.open`, `results.appeals.open`, `results.review.close_stale` | `review_recovery.recover_review` | the review put back at its stage, or a stale one closed; `results.appeals.open`'s `open`† and `close`† |
| `results.submission.restart` | `recover_orphaned_submission_channels`, for a first paste cut short | **`clear`**†: the round's sessions and the submission row, with the line; `delete_channel`; `results.submission.open` asked afresh |
| `results.submission.close_channel` | `recover_review`, for a round ended as not held | as under "A follow-on" |
| `results.amend.lapse` | `recover_orphaned_amend_channels` | as under "A timer" |
| `results.submission.open` | `recover_missed_round_starts`, through `ask_round_start` | as under "A timer" |

**A follow-on**, asked in another change's save

| Kind | Asked by | Jobs |
|---|---|---|
| `results.review.open` | the last paste, a swap, a resubmission's Cancel, a restart, or an approval whose `apply` a league admin discards | `names`; **`open`**: the snapshots, the review flag, the round's status, a return's flags cleared; the posting jobs; the prompt, and the old prompt's deletion; `close`†. A member's request hands the prompt to `results.review.prompt` on the round's lane |
| `results.review.prompt` | the review's or the report approval's `close` | the prompt (the appeals prompt for stage two) put up on the round's lane, with its line; `close`† |
| `results.resubmission.swap` | the last resubmission draft | **`swap`**: the round's results replaced by the drafts, the drafts deleted, the review asked again |
| `results.submission.all_cancelled` | the last paste, or the swap, where every session is cancelled | `names`; **`end_round`**†, through core's `end_round_not_held_on`; each session's line; the old tables and standings deleted; the later rounds' standings; the notices; the calendar; `ask_close` |
| `results.submission.close_channel` | `results.submission.all_cancelled` | the closing line and the channel deleted, on the round's lane |
| `results.amend.stage_one` | the last amendment draft | `names`; **`record_stage_one`**: the round snapshotted, the classification rewritten, points and standings recomputed, nothing posted |
| `results.amend.prompt` | stage one's save, and the report stage's `close` | a stage's prompt put up, on the amendment's lane |
| `results.amend.close` | an amendment ending itself (a refused paste, stage one refused) | the channel deleted, on the amendment's lane |

**A hook.** Core's changes call results' functions inside their own saves (below, "How modules and
core fit together"): the deletes and the pack, the rounds called off and made final, the season's
approval and completion, test mode turned on. The jobs results plans through a hook (the opening
and final classifications, the deletion of a called-off submission channel, the called-off note)
are core's change's jobs, each defined by results.

Each file's docstring holds its change's check, its jobs and the reason for each.

### The two stages of a round's review

Stage one is the report review and stage two the appeals review (results specification, "Penalties
and appeals are settled in the submission channel"). Each is staged on the round's lane and
approved on the league lane:

- **A staging is a change** that reads the review the press handed to `hold_review`, applies the
  item to it, withdraws the approval question and redraws the prompt, each a job of its own
  (`review_stage_change`'s docstring). A redraw Discord refuses stops the round's lane alone, and
  a Retry draws it again without staging twice.
- **An approval carries what is staged in its payload** and saves it in one `apply`, then plans
  its posts from `review_posting`, its verdicts, sheet and sanctions from `review_verdicts`, and
  closes in a job that runs last whatever is discarded before it (an opening job). A discard of
  `apply` asks the review open again, so a fresh prompt replaces the dead one.
- **The prompt is the conversation's.** Where a member's request opened the review or approved stage
  one, the prompt is put up by `results.review.prompt` on the round's lane, so a prompt Discord
  refuses stops that round alone.
- **An Approve waits for the review's lane:** it is refused while a staging or a confirmation is in
  hand there, and where the approval question it is pressed on is no longer the one the submission
  row names (`report_approval_change`'s docstring).

An amendment's review is the same review, held in the amendment's channel, with its stages
approved by `amendment_stage_changes`.

### The recomputation path

Points are applied by `scoring` (a session from its configuration, a round's sessions again) inside
whichever save changed them: a session's save, an approval's `apply`, an amendment's stage, the swap
or a points amendment (`results_post_service.rescore_season`). Penalties are added to the driver
rows by `penalty_service.apply_penalties_on`, which only adds, so an amendment's report stage
deletes the amended sessions' verdict records and writes the approved set back over rows written
afresh. A session that cannot be scored raises, and the save rolls back whole.

**The standings are recomputed in the same save, from the round on.**
`standings_service.cascade_recompute_from_round_on` recomputes and saves the snapshots of every
round of the division from the given one onwards, on the names a `names` job resolved before the
save. The posting jobs then republish the round and, where `plan_posts` is asked for later rounds,
each later round of the division that has standings posted, in round order; a points amendment or
an amendment's final stage rebuilds the whole division instead (`plan_division_posts`). A posting
draws the stored snapshot; `/results standings sync` alone works the standings out again as its job
runs (`post_standings_afresh`).

---

## Timed work and restarts

Answers to `architecture.md`, "Timed work and restarts". What each of results' timed events does
when missed is the results specification's ("Submitting round results", "Amending a submitted
session") and the core specification's ("When the bot stops").

### The round's `results` job

Core arms each round's `results` job (`SchedulerService.schedule_result_submission_jobs`, `core.md`,
"Core's own timed events") with no lateness limit, so a job whose moment passes while the bot is
down fires once it is back. Its handler, which the wiring registers, is `ask_round_start`
(`results/services/submission/round_start.py`), and results' start-up catch-up calls the same
function, so a job run on time and one caught up take one path. It reads the round again and asks
`results.submission.open` only for a round still to be run whose opening is not already in hand, so
the late job and the catch-up, in either order, ask once
(`test_the_late_job_and_the_catch_up_both_firing_open_the_round_once`).

**The opening's check judges the round still due as it comes up to run**: its start passed by the
queue's clock, and the round still to be run. An amendment or a cancellation of the round in hand
ahead of the opening then decides it, and a refused one leaves the opening to run: nothing holds the
opening back for such a change (`ask_round_start`'s docstring;
`test_a_round_whose_amendment_stopped_across_the_restart_moves_its_start_ahead_is_not_opened`).

**Under test mode a start that passed before the bot came up is left to `/test-mode advance`.**
`bot.came_up_at`, set once by the builder, is handed both to the job's handler and to the catch-up,
so the two judge "before the bot came up" by one value (`ask_round_start`'s docstring;
`test_under_test_mode_a_missed_round_start_is_left_to_the_advance`). `/test-mode advance` runs
`submission_changes.advance_step`, registered on `advance_steps` beside the job's handler.

### The amendment sweep

The amendment sweep is a standing sweep (`architecture.md`, "A standing sweep is an interval job
that re-reads its rows each time it runs"): an `IntervalTrigger` every `AMENDMENT_SWEEP_MINUTES`,
armed with no lateness limit by `SchedulerService.schedule_amendment_sweep`, whose handler,
`amendment_writes.sweep_expired_amendments`, reads every amendment's deadline against the `now` it
is handed and asks `results.amend.lapse` for each past it. **It is a sweep and not one timer per
amendment** because an amendment is left abandoned by no event the bot sees, and it is an interval
job rather than one that re-arms itself because a sweep that raised would then be the last
(`schedule_amendment_sweep`'s docstring; `test_the_sweep_is_scheduled_as_a_standing_job`,
`test_the_sweep_is_re_armed_by_the_scheduler_not_by_its_own_callback`). A lapse is not due while a
stage of the amendment is in hand, stopped included, so the next run asks again.

It is armed in results' start-up turn (`arm_amendment_sweep`) and by the switch-on's `After` step,
and removed by the switch-off's `disarm_sweep`, as a switch-off undoes what the switch-on set up
(`results_off_change`'s docstring; `test_turning_results_off_removes_the_amendment_sweep`).

### The lapse and the lock's clear

Two one-off timers (`architecture.md`, "A delay is a one-off timer that asks a change"), each armed
through `SchedulerService.arm_once` by a job of its own after the save of the row it is keyed to:

- **`amend_lapse`**, keyed by the amendment and the moment its prompt appeared, so a timer whose
  prompt a later one replaced finds itself stale. `amend_lapse_timer` reads the row again and asks
  `results.amend.lapse`, or nothing. Armed with no lateness limit, one missed while the bot was
  down fires as it starts, and asks nothing where nothing is due.
- **`review_lock_clear`**, keyed by the warning's message id. `lock_clear_timer` reads
  `review_lock_warnings` again and asks `results.review.lock.clear`, or nothing where the row is
  gone, a missed one firing as the bot starts, as the lapse's does. The clear is a change of its own
  because the warning's life is the timer's, not the lock's (`review_lock_change`'s docstring).

### Results' start-up turn

`start_up_steps` names results' four steps, run in results' turn of the start-up sweep while
results is on (`core.md`, "Start-up"):

1. **amendment sweep**, which arms the sweep;
2. **orphaned amend channels** (`recover_orphaned_amend_channels`), which asks the lapse of every
   amendment a previous process left open;
3. **orphaned submission channels** (`recover_orphaned_submission_channels`), which hands each open
   submission to `review_recovery.recover_review`, and asks `results.submission.restart` for one
   that is no review;
4. **missed round starts** (`recover_missed_round_starts`), which hands each round of the live
   season still to be run whose start has passed to `ask_round_start`.

The amendments come before the submissions so that the standings a recovered submission posts are
built from the round as it raced, and the missed starts come last so that a round one of the
recoveries asked open again is left to that opening (the comments in `start_up_steps`;
`test_the_builder_orders_the_start_up_steps`). Each recovery asks the queue and posts nothing
itself; `recover_review` leaves a round whose review or submission a change is carrying out to it,
and reads `queue_records.ever_asked` as the durable mark that a last paste finished (its module
docstring).

**A failed recovery reports once.** A round that cannot be recovered stops none of the others; once
every round is gone through, the step raises one `RecoveryFailed` naming each, which the start-up
sweep reports to the log channel in one line
(`test_a_round_that_cannot_be_recovered_is_reported_and_the_others_still_are`).

---

## Posting to Discord

Answers to `architecture.md`, "Posting to Discord", which lays out the four handlers. Every post
results makes is a job of a change, and every function that posts carries `posts_in_a_job`.

| Post | Kind | Channel | May mention | Found again by | The switch-off |
|---|---|---|---|---|---|
| A session's results table, or its "cancelled" line | standing post | the division's results channel | as text, the drivers it names; as a picture, nobody | `session_results.results_message_id` and `results_message_ids`, saved in the job's `record` (`_record_session_post`) | takes it down |
| A round's standings | standing post | the division's standings channel | as for a table | `round_standings_posts`, saved in the job's `record` (`set_standings_message_id_on`) | takes it down |
| The opening and final classifications | standing post | the division's standings channel | as for a table | `classification_posts`, written in the record that marks the posting done; a part posted before a failure is kept on the job and removed by its next try | takes the opening one down; a final classification belongs to a completed season, which the switch-off never reads |
| A verdict | notice | the division's verdicts channel | the driver it names, and no role (`_VERDICT_MENTIONS`) | `penalty_records` or `appeal_records`, announcement columns, saved in the job's `record` (`_record_announcement_on`) | takes it down |
| The heading over a round's verdicts | notice, posted by the image module's `image_verdict_banner_post.try_post` | the verdicts channel | — | `verdict_banner_messages`, saved in the job's `record` | takes it down, but where it heads a sanction card |
| An attendance sanction card | notice | the verdicts channel | the driver it names | not kept; its banner is marked `heads_sanctions` | leaves it, as attendance's |
| The called-off note | notice, silent, through core's `post_cancellation_message` | the division's results channel | nobody | `called_off_notes`, saved in the job's `record` (`record_note_on`) | takes it down |
| The batch notice | notice | the submission or amendment channel | nobody | the job's result, read by `delete_batch_notice` | goes with the channel |
| A submission or amendment channel | bot-owned channel | beside the interaction channel | — | `round_submission_channels.channel_id`, `round_amend_channels.channel_id`, saved in the job's `record` | takes it down |
| The submission's opening line | in a bot-owned channel | the submission channel | the interaction role | the job's result | goes with the channel |
| The locked channel's warning | in a bot-owned channel | the submission channel | the member who typed | `review_lock_warnings` | goes with the channel |
| A prompt, an acknowledgement, a refusal, the resubmission's announcement | in a bot-owned channel | the dialogue's channel | nobody | the job's result; the announcement on `resubmit_prompt_message_id`; an amendment's prompt's moment on `prompt_posted_at`, which its lapse timer reads | goes with the channel |
| The review's prompt, the appeals prompt, the approval question | in a bot-owned channel | the submission channel | nobody | `prompt_message_id`, `appeals_prompt_message_id`, `approval_message_id`; an amendment's are held on the review in memory and in the payloads that take them down | goes with the channel |
| A log line | log line | the log channel | nobody, the router wrapping its mentions | the change's lines, saved with its step | — |

Which of results' posts the switch-off takes down is the results specification's switch-off rule
(the rules on disabling the module, under "Assigning channels to divisions"), and each it takes
down is a post that might later be deleted, with its id kept, as `architecture.md`'s "Every post
keeps three things" has it. `results_purge_service.erase_season_on` reads every id first, from the
rows the same save then erases, and hands one take-down item to each `take_down` job (its
docstring). The one case left standing, and why, is that module's docstring's.

**Who a table may notify.** A table written out as text notifies the drivers it names and no role
(`_TABLE_MENTIONS`), and a table drawn as a picture carries no mention text, so it notifies nobody.
A verdict notifies the driver it names, whether drawn or written (the results specification,
"Penalties and appeals are settled in the submission channel"). Every prompt in a submission or
amendment channel mentions nobody; a dialogue line mentions only the roles and members its payload
names (`submission_steps.post_step`).

### Posting and reposting a batch

A republication is a run of jobs planned by `review_posting` (`plan_posts`, `plan_division_posts`):
one for each session's table and one for each round's standings, each followed by a `delete_message`
job for each message it replaces, bracketed by the batch notice's pair of jobs. Its module
docstring holds the shape:

- **Post the replacement, then delete what it replaces**, so the new message stands before the old
  one goes. *Rejected:* deleting the old message before posting the new one, which leaves the
  channel empty where Discord refuses the post (`review_posting`'s docstring;
  `test_a_replacement_is_posted_before_the_old_message_is_deleted`).
- **A post's ids are saved with its done mark**, in the job's `record`.
- **The job reads its channel when it runs**, so a channel set while it stands stopped is posted to
  on its next try.
- **A division is rebuilt whole and in round order**, a repost landing at the bottom of a
  channel (`plan_division_posts`'s docstring).

**A table too long for one message is split** (`_split_content`, under Discord's limit, on line
breaks) and sent through the standing-post handler one chunk after another, with nothing pacing the
posts but the lane, one job at a time. **Every chunk's id is recorded**, the anchor first, and a
deletion takes down exactly the messages recorded. *Rejected:* finding a posting's later chunks by
walking forward from its anchor, which cannot tell one posting's continuation from the next posting
down (`_send_chunked`'s and `_delete_posting`'s docstrings;
`test_nothing_that_merely_follows_the_posting_is_deleted`). **A posting is whole or absent:** a send
that fails part-way takes its first chunks down, and what it could not take down travels on the
failure for the next try to remove first
(`test_a_posting_that_fails_part_way_takes_its_first_chunks_down`).

**The text standings are edited in place** where the new text fits in one message and the job is not
a fresh repost; a shorter table hands back the chunks it no longer fills for deletion
(`produce_standings`).

### Pictures and their fallbacks

The image module draws (below, "How modules and core fit together"). **A picture is tried on a
job's first try alone**; a retry posts text (Constitution XIV, rule 8;
`test_a_post_discord_refuses_stops_the_queue_and_is_tried_again_as_text`). Where a picture cannot be
drawn, the table, the standings or the verdict goes out as text and the job returns the fault as a
line of the log channel, worded by `picture_fallback_line`, as the image specification asks. The
three catch-alls that do it, in `produce_session_results`, `produce_standings` and `_send_verdict`,
are `architecture.md`'s fifth place ("Errors and failures"), each returning the fault on what it
hands back (`PostedTable.fault`, `SentVerdict`)
(`test_a_results_picture_that_fails_posts_the_table_as_text_and_says_why_in_the_log`,
`test_a_standings_picture_that_fails_posts_the_standings_as_text_and_says_why_in_the_log`,
`test_a_sanction_card_whose_picture_fails_is_posted_as_text_and_says_why_in_the_log`).

**Results' posts off the queue** are `architecture.md`'s exceptions alone ("Every post is made in a
job"): its replies, the configuration picker's edit of the message pressed (`_ConfigSelectView`),
and the log lines the router delivers.

---

## How modules and core fit together

Answers to `architecture.md`, "How modules and core fit together".

### The hooks results signs up to

Results declares no hook, and calls none of core's list hooks: where results ends a round as not
held or turns itself off, it hands core's writer the hook (`cancellation_notices`,
`rounds_made_final`), and the writer calls it. The hooks results joins, and what it does at each:

| Hook | Results' function | What results does |
|---|---|---|
| `starting` | `wiring.start_up_steps` | its four start-up steps (above) |
| `confirmation_refused` | `season_points_service.confirmation_refusals` | refuses a season's confirmation for its own settings |
| `cancellation_refused` | `result_submission_service.cancellation_refusal` | refuses a cancellation of a round whose submission accepted results |
| `rounds_called_off` | `result_submission_service.close_called_off_on` | closes the empty submissions of the rounds called off |
| `cancellation_notices` | `cancellation_notice.notices_on` | plans the called-off note |
| `cancellation_steps` (twice) | `review_posting.cancellation_steps`, `cancellation_notice.notify_results_steps` | defines the deletion of a submission channel and the note's job |
| `rounds_made_final` | `result_submission_service.rounds_made_final_on` | marks the drivers who raced as former drivers |
| `season_approved` | `season_points_service.snapshot_configs_to_season_on` | copies the attached configurations onto the season |
| `placements_confirmed`, `placements_confirmed_steps` | `season_standings_posts.placements_confirmed_on`, `opening_steps` | plans and defines the opening classification |
| `season_completed`, `season_completed_steps` | `season_standings_posts.season_completed_on`, `final_steps` | plans and defines the final classification |
| `final_round_read` | `season_standings_posts.final_round_on` | reads a division's last round with results |
| `season_standing_read` | `season_standings_posts.season_standing_on` | reads a driver's standing for the season's history |
| `season_end_refused` | `amendment_writes.season_end_refusal` | refuses a season's end while a round is being amended |
| `divisions_raced_read` | `results_purge_service.divisions_raced_on` | reads the divisions a driver's accounts raced in |
| `test_mode_turned_on` | `points_config_service.seed_test_configs_on` | seeds the test configurations, whatever results' switch |
| `review_sections_read` | `season_points_service.review_section` | results' section of a season review |
| `division_lines_read`, `division_channel_faults_read`, `channel_uses_read`, `setting_channels_read` | `division_channels.division_lines`, `division_channel_faults`, `channel_uses`, `setting_channels` | a division's channels, read for the reviews, the channel registry and the factory reset |
| `fingerprint_areas_read` | `fingerprint_areas.fingerprint_areas` | results' configuration in the season's fingerprint |
| `pending_steps_read`, `review_marks_read` | `test_mode_steps.pending_steps`, `review_marks` | a round's submission for `/test-mode advance` and its review |
| `in_memory_state_cleared` | `review_state.clear_held_reviews` | the reviews held in memory |
| the six delete and pack hooks | see "The database" | — |

Results' switch is the keyed hook `ModuleService.register_switch` under `"results"`, read by
`module_switch_settings.results_enabled_on` from `results_module_config`. Results' own work
checks the switch where it starts, and a hook's subscriber that needs it reads it itself.

### Attendance, through `AttendanceAfterReview`

What a round's review asks of attendance is the protocol results declares,
`AttendanceAfterReview` (`results/services/module/attendance_hook.py`), as `architecture.md`'s
"Results reaches attendance through a protocol, handed in by the builder" designs; attendance
implements it, and the builder hands it to results' change types as `attendance`. Its writers
write on the save they are handed and commit nothing, so the round's attendance lands with the
penalties and points or not at all; its posting methods raise `StepFailedOnDiscord` for the queue to
stop on. Which methods act whatever attendance's switch says is the protocol's docstring's, and
`architecture.md`'s. Through it, results reads a not-held round's check-in (`check_in_read_on`),
an amendment's snapshot of the round's pardons and their return (`pardons_of_on`,
`put_back_pardons_on`), a driver's attendance row and a division's latest scored round, so that
results reads and writes none of attendance's tables. The one job of results' own that serves
attendance alone, the heading over the sanction cards, asks `enabled` and is dropped while
attendance is off (`review_verdicts`' docstring).

Turning results off takes attendance with it where the league confirmed it: the builder hands
`register_change_types` attendance's switch-off as `attendance_off_on`, which `switch_off` calls on
its own connection (`architecture.md`, "Each module turns itself on and off").

### The dependency table and the image module

Results needs no module. Attendance needs results (`NEEDS`, `core.md`, "The dependency table"),
which is why results' switch-off offers the cascade above.

Results imports no module but image (the `results-uses-no-other-module-but-image` contract). The
image module draws results' pictures: for a session's table, the standings and the verdicts'
heading its `try_post` posts the picture and hands back what it posted, which results' job records
and whose predecessors it deletes; for a verdict, `image_verdict_post` renders it and results posts
it. A retry asks for no picture (above, "Pictures and their fallbacks").

### The `/results` group

| Subgroup | Class | Commands |
|---|---|---|
| `config` | `ConfigCommands` | `add`, `remove`, `session`, `fl`, `fl-plimit`, `append`, `detach`, `list`, `view`, `bulk-session`, `xml-import` |
| `amend` | `PointsAmendCommands` | `toggle`, `revert`, `session`, `fl`, `fl-plimit`, `bulk-session`, `review` |
| `reserves` | `ReservesCommands` | `toggle` |
| `standings` | `StandingsCommands` | `sync` |
| `rounds` | `RoundsCommands` | `sync`, `amend` |
| `channel` | `ChannelCommands` | `results`, `standings`, `verdicts` |

No results command sits under a group of core's or of another module's. Results' switch is asked by
core's `/module enable` and `/module disable` by kind (`module.on:results`, `module.off:results`),
and `/test-mode advance` reaches results through the step results registers.

---

## Errors and failures

Answers to `architecture.md`, "Errors and failures", which names each failure path and every stated
exception, results' among them.

| Starting point | Results' part |
|---|---|
| A command, button or form | the base classes and `report_failure`; no cog or view of results' has an error handler. The cog catches by name only what it turns into a refusal (`ConfigNotFoundError`, `UnicodeDecodeError`), and `points_settings.run_xml_import` an `XmlImportError`; a lapsed confirmation's buttons that cannot be taken down are logged as a warning, the lapse recorded all the same |
| A change | its check refuses a member's request and drops the bot's where the work is no longer due; a job that fails stops its lane, with the stop notice, Retry and Discard |
| A timed job | the scheduler's job runner (`core.md`, "Errors and failures"); the sweep catches each lapse it cannot ask, logs it on the host and goes on, so one amendment stuck stops none of the rest |
| A start-up step | `RecoveryFailed`, raised once a recovery has gone through every round, which the start-up sweep reports in one line |
| A picture | the fallback to text and its line, above |

**What a stop leaves.** Every job of a review, an approval, an amendment's stage, a sync or the
switch-off is a job like any other, and a failure stops its lane (`review_verdicts`' docstring holds
that no job of the review's announcements swallows one). A step results marks undiscardable is one
whose discard would leave a round never opened, never final or without its prompt, or a resubmission
unrecorded: the opening's `start_round` and `create_channel`, the restart's `clear`, the not-held
end's `end_round`, the review's `close`, the prompt change's `close`, `results.appeals.open`'s
`open` and `close`, both approvals' `close`, and the resubmission's `mark` (each reader says what
dropping it would leave). The results specification says what each discard leaves a league.

**Refusals by name.** A change's refusals are its check's, worded where the check is written. Two
are raised inside a save and caught by name there: `StagedPointsNotApprovable`, which refuses a
points amendment's save having written nothing (`points_amendment_change`'s docstring), and
`AmendmentWouldOrphanVerdictError`, which rolls stage one back to its savepoint and ends the
amendment as a refused paste does (`amend_stage_one_change`'s docstring).

**The stated exceptions** of results' are `architecture.md`'s, each with its docstring:
`amend_end_changes._names_step` (an amendment's lapse and cancel go ahead with no names),
`points_amendment_faults.approval_faults` (an attendance switch that cannot be read never refuses
an amendment) and `_ConfigSelectView._put_back` (a put-back after a press already answered).
`CATCH_ALLS_ALLOWED` names them with the three picture fallbacks.

**What a restart does to interrupted work** (the core specification's "When the bot stops", and the
results specification's "Penalties and appeals are settled in the submission channel" and
"Amending a submitted session" say what a league sees):

- **a change cut off** is finished by the queue from its first job not done, so an approval that
  saved finishes its posts, every verdict announced once; **a stopped job** stays stopped;
- **a first paste cut short** is restarted (`results.submission.restart`), the sessions entered
  lost, and **a review** is put back at its stage with a fresh prompt (`recover_review`);
- **what a review staged** is lost with the memory that held it (above, "What a restart loses");
- **a resubmission** is ended by the review's opening, the round's results standing;
- **an amendment** is lapsed, the round put back where stage one had written;
- **a round's start that passed** is opened late (above), save under test mode;
- **the configuration picker, Cancel Amendment and the resubmission's Cancel** are not registered
  at start-up; the work they belong to is ended or restarted by the recoveries above. The review's
  three persistent views (`PenaltyReviewView`, `ApprovalView`, `AppealsReviewView`) are registered
  by the builder.

---

## Constraints a later reader might tune away

Each is stated where it lives and pinned by the test named.

| Constraint | Stated in | Pinned by |
|---|---|---|
| A replacement is posted before the message it replaces is deleted | `review_posting`'s docstring | `test_a_replacement_is_posted_before_the_old_message_is_deleted` |
| A round's standings posting is kept on its own row, never on a snapshot | `set_standings_message_id_on`, `_drop_superseded_driver_rows` | `test_a_recomputation_that_reorders_a_round_leaves_its_standings_posting_found` |
| A posting's chunks are the ids recorded; nothing that follows it is deleted | `_send_chunked`, `_delete_posting` | `test_nothing_that_merely_follows_the_posting_is_deleted`, `test_a_posting_that_fails_part_way_takes_its_first_chunks_down` |
| The opening re-judges the round as it comes up to run; nothing holds it back | `ask_round_start`, `submission_changes`' docstring | `test_a_round_whose_amendment_stopped_across_the_restart_moves_its_start_ahead_is_not_opened`, `test_the_late_job_and_the_catch_up_both_firing_open_the_round_once` |
| Under test mode a start that passed before the bot came up is left to the advance | `ask_round_start` | `test_under_test_mode_a_missed_round_start_is_left_to_the_advance` |
| The amendment sweep is a standing interval job, not a timer per amendment nor one that re-arms itself | `SchedulerService.schedule_amendment_sweep` | `test_the_sweep_is_scheduled_as_a_standing_job`, `test_the_sweep_is_re_armed_by_the_scheduler_not_by_its_own_callback` |
| Turning results off removes the sweep turning it on arms | `results_off_change`'s docstring | `test_turning_results_off_removes_the_amendment_sweep` |
| Amendments are recovered before submissions, and missed starts after both | the comments in `wiring.start_up_steps` | `test_the_builder_orders_the_start_up_steps` |
| A failed recovery stops none of the other rounds and is reported once | `recover_orphaned_submission_channels`, `RecoveryFailed` | `test_a_round_that_cannot_be_recovered_is_reported_and_the_others_still_are` |
| A staging against a prompt a restart replaced is no longer due, and stages nothing | `review_stage_change`'s docstring | `test_a_staging_against_a_replaced_prompt_is_not_due` |
| An approval's writes are one save | `report_approval_change`'s and `points_amendment_change`'s docstrings | `test_penalties_and_attendance_are_saved_in_one_save`, `test_the_points_rescoring_standings_and_attendance_are_saved_in_one_save` |
| A retry posts text, the picture tried on the first try alone | `review_posting`'s docstring | `test_a_post_discord_refuses_stops_the_queue_and_is_tried_again_as_text` |
| A verdict notifies its driver and no group | `_VERDICT_MENTIONS` | `test_the_textual_verdict_notifies_drivers_and_no_group` |
| A sanction card marks the banner over it, which is then never taken down | the baseline's comment on `verdict_banner_messages` | `test_a_sanction_card_marks_exactly_its_banner`, `test_a_banner_heading_a_sanction_card_is_kept`, `test_a_banner_over_a_sanction_card_stays` |
| The picker claims a choice before its edit and releases it once the message is put back | `_ConfigSelectView`'s docstring | `test_the_picker_s_claim_is_held_until_it_is_put_back` |
| `/results`' subgroups are imported in the order a league sees them | the comment in `results_cog.py` | `test_the_results_subgroups_are_sent_in_their_listed_order` |
