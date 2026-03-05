# Research: Command Streamlining & QoL Improvements

No new technologies are introduced by this feature. All decisions below confirm
existing patterns or resolve design choices within the established stack.

---

## Decision 1 — Round auto-numbering approach

**Decision**: Derive round numbers by sorting all rounds in a division by `scheduled_at`
ascending after every mutation (add, amend datetime, delete) and rewriting `round_number`
values 1…N in that order atomically in a single `renumber_rounds` function.

**Rationale**: This is the simplest model that satisfies FR-003 through FR-006 and remains
consistent under all operations. There is no scenario where two rounds in the same division
have the same `scheduled_at` with a deterministic ordering requirement beyond that
(tied times are flagged as a warning per spec).

**Alternatives considered**:
- Keeping manual round numbers as a secondary sort key — rejected; the spec explicitly
  removes the round number input parameter and makes position fully datetime-derived.
- Maintaining a linked-list of round positions — rejected; unnecessary complexity for
  typical division sizes (≤25 rounds).

---

## Decision 2 — Removal of `DuplicateRoundView`

**Decision**: The existing `DuplicateRoundView` dialog (which asks the user to choose
"insert before", "insert after", or "replace" when a duplicate round number is detected
during `/round-add`) is **removed**. Insertion position is now fully determined by
`scheduled_at` sort order.

**Rationale**: The dialog existed only because round numbers were user-supplied. Since FR-003
makes numbers auto-derived, there is no longer a "conflict" to resolve — every insertion
finds its correct position automatically. The helper functions `_rounds_insert_before`,
`_rounds_insert_after`, and the view class itself can all be deleted.

**Alternatives considered**:
- Retain the dialog as an optional override — rejected; it contradicts FR-003 and
  introduces inconsistency in how round numbers are owned.

---

## Decision 3 — Subcommand group layout

**Decision**: Three `app_commands.Group` objects cover all affected commands:
- `season` group in `SeasonCog`: `setup`, `review`, `approve`, `status`, `cancel`
- `division` group in `SeasonCog`: `add`, `duplicate`, `delete`, `rename`, `cancel`
- `round` group in `SeasonCog` / `AmendmentCog` merged: `add`, `amend`, `delete`, `cancel`

`test-mode` group (`toggle`, `advance`, `review`) remains its own group in `TestModeCog`
with its existing name; only the access gate changes (no rename needed).

**Rationale**: Grouping by domain keeps related commands together in Discord autocomplete.
`round amend` is moved from `AmendmentCog` into the `round` group so all round operations
appear under one autocomplete prefix. `AmendmentCog` can be retired or kept as a thin
alias wrapper if deletion causes complexity.

**Alternatives considered**:
- Separate cog per group — rejected; adds file count without benefit; all three groups
  are season-lifecycle concerns that share `PendingConfig` state and `SeasonService`.
- Keeping `round amend` in `AmendmentCog` — acceptable but fragments the `round` group;
  merged for user-facing consistency.

---

## Decision 4 — `/season cancel` data deletion strategy

**Decision**: Reuse the cascade-delete logic already present in `reset_service.py`,
scoped to a single `season_id` rather than the whole server. Extract a shared
`delete_season(season_id)` method in `SeasonService` that both `/season cancel` and the
end-of-season teardown path can call. This ensures the deletion ordering (forecast_messages
→ phase_results → sessions → rounds → divisions → seasons) is defined in exactly one place.

**Rationale**: `reset_service.py` already has the correct FK-safe delete order (established
in the v1.1.0 bugfix session). Duplicating it in a new cancel path would be a maintenance
hazard. Centralising in `SeasonService.delete_season` aligns with the service layer's
ownership of all season data.

**Alternatives considered**:
- Calling `reset_service.reset_server_data(full=False)` from the cancel path — rejected;
  `reset_server_data` is scoped to server_id and deletes ALL seasons; `delete_season` must
  be scoped to one season_id to be safe in a future multi-season-history model.

---

## Decision 5 — `status` field placement for cancellation

**Decision**: Add a `status` TEXT column to both `divisions` and `rounds` tables via
migration `007_cancellation_status.sql`, defaulting to `'ACTIVE'`, with a CHECK constraint
restricting values to `'ACTIVE'` and `'CANCELLED'`.

The `Season` model and `seasons` table are **not** given a `CANCELLED` status. Seasons
are either deleted (via `/season cancel`) or transition through `SETUP → ACTIVE → COMPLETED`
as before. This matches the spec's clarification that season cancellation produces the same
outcome as natural season completion.

**Rationale**: Division and round cancellation needs to be persisted so the scheduler
knows to skip cancelled rounds, and so `/season-status` and test-mode review can surface
the cancelled state. Season-level cancellation does not need a status because no season
row will remain after the operation.

**Alternatives considered**:
- Soft-delete via a `deleted_at` timestamp — rejected; CHECK(status IN (...)) is simpler
  to query and is already the pattern used on `phase_results.status`.

---

## Decision 6 — Test mode access gate

**Decision**: Replace the `@channel_guard` decorator on all three `/test-mode` subcommands
with `@admin_only` (the decorator already defined in `src/utils/channel_guard.py`).
`admin_only` checks `member.guild_permissions.manage_guild`, which is the Manage Server
permission — the same gate used by `/bot-init` and `/bot-reset`.

`default_permissions=None` on the group remains unchanged (no Discord-level permission
override; Python-level gate is authoritative per the v1.1.0 bugfix).

**Rationale**: `admin_only` is already implemented and tested. No new utility code needed.
Server administrators bypass the interaction-role check (consistent with FR-033 scenario 3).

**Alternatives considered**:
- Adding a new `@admin_only_or_interaction_role` combined decorator — rejected; the spec
  explicitly states server-administrator permission is sufficient without the interaction
  role, meaning the interaction-role check must be dropped entirely for these commands.

---

## Decision 7 — README update scope

**Decision**: The README Slash Commands section is rewritten in full to reflect:
1. New subcommand group layout (`/season`, `/division`, `/round`, `/track`, `/test-mode`)
2. Removed parameters (`start_date`, `num_divisions`, `round_number`)
3. All new commands with parameter tables and access level notes
4. Updated Season Setup Workflow section to reflect new multi-step flow format

The Track Distribution Parameters section and Weather Pipeline section are unchanged.

**Rationale**: The README is the primary utilization guide for server administrators.
It must be kept current to serve that purpose. No other documentation files are in scope.
