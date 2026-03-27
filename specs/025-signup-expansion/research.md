# Research: Signup Module Expansion (025-signup-expansion)

## R-001: Auto-close timer scheduling

**Decision**: Use APScheduler `DateTrigger` with `AsyncIOScheduler`, same pattern as `season_end` jobs.

**Rationale**: The existing `scheduler_service.py` already persists date-trigger jobs via `SQLAlchemyJobStore` (survives bot restarts). The `season_end` job is an almost-exact analogue — a one-shot future event at a specific UTC timestamp, scoped per-server. Reusing this pattern avoids a second persistence layer (e.g., a DB-polled daemon loop) and keeps restart-recovery logic consistent with the rest of the bot.

**Pattern**:
```
job_id: "signup_close_{server_id}"
trigger: DateTrigger(run_date=close_at, timezone="UTC")
callable: module-level _signup_close_timer_job(server_id)  ← picklable
global sentinel: _GLOBAL_SCHEDULER_SERVICE (already exists)
```

**Restart recovery**: On `on_ready()`, the SQLAlchemy job store reloads all persisted jobs automatically. However, if `close_at` has already elapsed (bot was down when the timer fired), the job will be present but unfired. The existing `_recover_missed_phases` pattern handles this gracefully — any job whose `run_date` is in the past is fired immediately. The same approach applies here: on `on_ready()`, query `signup_module_config` for rows with non-null `close_at`; if the timestamp is in the past, execute the close immediately without waiting for the scheduler.

**Alternatives considered**:
- *DB polling loop*: Poll `signup_module_config.close_at` on a tick. Rejected — adds unnecessary complexity, latency, and infrastructure divergence from existing pattern.
- *Discord scheduled events*: Only visible to users, not suitable for bot-internal triggers. Rejected.

---

## R-002: Module enable decoupling — backward compatibility

**Decision**: Remove `channel`, `base_role`, and `signed_up_role` parameters from `/module enable signup`. Dedicated commands: `/signup channel`, `/signup base-role`, `/signup complete-role`. Permission-overwrites applied lazily (on `/signup channel` execution, not on enable).

**Rationale**: The existing `_enable_signup()` flow couples three separate concerns — enabling the module flag, configuring the channel, and configuring roles — into a single atomic command. This is the only module where `enable` requires parameters (weather and results modules take none). The change brings signup in line with the uniform pattern used by other modules, and allows administrators to reconfigure channel/roles post-enable without a disable/re-enable cycle.

**Key implication — channel permission overwrites**: Currently, permission overwrites are applied inside `_enable_signup()` using the provided channel. Going forward, they must be applied (and re-applied on overwrite) inside `/signup channel`. The module-enable command itself does not touch any Discord channel.

**Season-gate implication**: All three config values (channel, base role, complete role) are currently required by `_enable_signup()`, so they are always present when the module is enabled. After decoupling, they may each be unset. Season approval must now explicitly check each one independently.

**Alternatives considered**:
- *Keep parameters as optional on enable*: Muddies the ownership of when config is applied and does not align with other modules. Rejected.
- *Create a separate `/signup setup` command grouping all three*: Less discoverable; the three values serve distinct purposes and are set independently. Rejected in favour of three discrete commands.

---

## R-003: "Unassigned drivers not dropped" — semantics of auto-close

**Decision**: The close event (manual or auto) cancels only drivers who have not yet been approved — specifically `PENDING_SIGNUP_COMPLETION`, `PENDING_DRIVER_CORRECTION`, and `PENDING_ADMIN_APPROVAL`. Drivers in `UNASSIGNED` or `ASSIGNED` states are entirely unaffected.

**Rationale**: The `execute_forced_close()` function already targets only in-progress driver states. The auto-close timer will call the same shared function. The three pending states represent drivers actively in the wizard or awaiting admin review — they have not submitted a complete, committed application. `UNASSIGNED` drivers are already "done" with the signup pipeline and awaiting placement; dropping them would silently lose committed applications that admins may have already reviewed.

**Existing code impact**: `execute_forced_close()` already filters by the three pending states. No behavioural change is needed in the cancel logic itself; only the trigger path (timer firing vs. manual command) is new.

**Rationale for keeping AWAITING_CORRECTION_PARAMETER** in the cancelled set: This state is transient (5-minute window) and is part of the admin review pipeline on the driver's side; the driver's data is not yet committable.

**Alternatives considered**:
- *Cancel only PENDING_SIGNUP_COMPLETION*: Would leave drivers stuck in PENDING_ADMIN_APPROVAL or PENDING_DRIVER_CORRECTION with no signup channel after close. Rejected.
- *Cancel all non-ASSIGNED states*: Would drop UNASSIGNED drivers who have valid committed signups. Explicitly rejected per user requirement.

---

## R-004: Lineup announcement trigger — "all placed" definition

**Decision**: A lineup post fires for a division when: (a) a driver assignment change event occurs in that division (assign, unassign, or sack), AND (b) after the event, the query `SELECT COUNT(*) FROM driver_profiles dp JOIN season_assignments sa ON ... WHERE sa.division_id = ? AND dp.current_state = 'UNASSIGNED'` returns 0, AND (c) at least one driver is assigned to the division.

**Rationale**: The trigger must be per-division, not server-wide. The "at least one assigned" guard prevents spurious posts before any placement has occurred (e.g., on a fresh season with no drivers yet). The post is triggered by assignment mutations in `placement_service`, not by any cron/scheduler job — this keeps it synchronous with the action and avoids race conditions.

**Channel posting**: Calls `placement_service` or a new `signup_lineup_service` function. The lineup message lists all teams (excluding Reserve if empty) with assigned drivers. If Reserve has assigned drivers, they are listed.

**Alternatives considered**:
- *Scheduled poll*: Simpler but introduces latency; the lineup would appear asynchronously after the last placement. Rejected — the user expects the post immediately after the last driver is placed.
- *Trigger from DriverCog directly*: Possible but mixes display logic with command handling. Preferred to centralise in `placement_service` post-assign/unassign/sack so the logic is consistent regardless of which command path triggers it.

---

## R-005: Signup open — base-role mention

**Decision**: Use `role.mention` in the signup-open post (e.g., `@Drivers Signups are now open!`). The bot requires the "Mention @everyone, @here, and All Roles" permission (already documented in README as required).

**Rationale**: The permission is already listed as a prerequisite. Using `role.mention` in a normal message will ping all role holders if the role's "Allow anyone to @mention this role" is not set; this is the desired behaviour for the league signup announcement.

**Alternatives considered**:
- *`allowed_mentions` with `roles=[base_role]`*: Required for roles where mentionability is off (the common case for private league roles). Should be set explicitly to avoid unexpected silent no-pings. The `allowed_mentions=discord.AllowedMentions(roles=[base_role])` argument must be passed alongside the `role.mention` in the message content.

---

## R-006: `/signup config` group vs. top-level `/signup` subcommands

**Decision**: New configuration commands are added as subcommands of the existing `/signup` group: `/signup channel`, `/signup base-role`, `/signup complete-role`. They are NOT nested under a `/signup config` sub-group.

**Rationale**: The existing `/signup config channel` and `/signup config roles` commands already exist but are wired to the old module-enable path. These will be replaced with flattened `/signup channel`, `/signup base-role`, `/signup complete-role` commands. Adding a `/signup config` sub-group would require changing the existing command structure; the flat naming under `/signup` is consistent with e.g. `/signup open`, `/signup close`, `/signup nationality`, and keeps the command tree shallow per the bot's single-interaction-preference rule (Principle I / Bot Behavior Standards).

**Impact on existing code**: The old `/signup config channel` and `/signup config roles` commands need to be removed or migrated. The new commands implement the same logic but independently (separate commands for channel, base-role, and complete-role rather than one command for all roles together).

---

## R-007: Migration numbering

**Decision**: The next available migration is `024_signup_expansion.sql` (current highest is `023_round_amend_channels.sql`). Both schema changes (add `close_at` to `signup_module_config` and create `signup_division_config`) will be placed in a single migration file to keep the feature atomic at the DB level.

**Alternatives considered**:
- *Two separate migrations*: More granular but adds noise for a same-feature change. Rejected.
