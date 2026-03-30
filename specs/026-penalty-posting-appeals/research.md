# Research: Penalty Posting, Appeals, and Result Lifecycle

**Feature**: 026-penalty-posting-appeals  
**Date**: 2026-03-30  
**Status**: Complete — no NEEDS CLARIFICATION items remain

> All technical unknowns were resolved during the specification phase through direct
> codebase research (spec 023, `penalty_wizard.py`, `result_submission_service.py`).
> The user confirmed "keep the technologies" — no new dependencies introduced.

---

## Decision Log

### D-001: How the penalty stage closes

**Question**: Does closing the penalty stage require a new slash command, or does the existing wizard do it?

**Decision**: The existing `ApprovalView.approve_btn` IS the penalty-stage close action. No new command needed.

**Rationale**: The penalty wizard is entirely channel-based. All interactions happen via Discord UI components (buttons, modals) posted to the transient submission channel. The `ApprovalView` is posted by `_show_approval_step()` after the admin clicks "Approve" in `PenaltyReviewView`. Currently `ApprovalView.approve_btn` calls `finalize_round()`, which closes the channel. This feature repurposes that call: instead of closing, it transitions the round to `POST_RACE_PENALTY` and posts the `AppealsReviewView`.

**Alternatives considered**: Separate `/round penalty-close` slash command. Rejected — inconsistent with the established wizard-channel pattern; would require admins to leave the wizard channel and issue a command.

---

### D-002: Where appeals starts

**Question**: Does the appeals stage start in the same channel as the penalty review, or does it open a new channel?

**Decision**: Appeals starts automatically in the same transient submission channel. No new channel is created.

**Rationale**: The transient submission channel is already open and scoped to the round. Keeping appeals in the same channel requires no new channel lifecycle management and mirrors the penalty review flow exactly. The `_show_approval_step()` pattern (post a new View to the channel) already demonstrates how to chain review stages in a single channel.

**Alternatives considered**: Separate dedicated appeals channel. Rejected — adds channel management complexity and creates a second transient channel that also needs lifetime management and recovery.

---

### D-003: How the AddPenaltyModal is extended

**Question**: Is a new modal class created for appeals, or is the existing `AddPenaltyModal` extended?

**Decision**: The existing `AddPenaltyModal` is extended with two new `TextInput` fields (`description`, `justification`). The same class is reused for both penalty review and appeals review.

**Rationale**: The spec's assumption that description and justification are needed regardless of penalty vs. appeal is correct. Discord supports up to 5 fields per modal; `AddPenaltyModal` currently has 2 fields, so 2 more fit within limit. Reusing the class avoids duplicated validation logic.

**Alternatives considered**: Separate `AddAppealModal` class. Rejected — identical fields and validation; would create a maintenance surface with no benefit.

---

### D-004: Where result_status is tracked

**Question**: Is result lifecycle state tracked at the round level or per-session level?

**Decision**: `result_status` is tracked at the **round level** (`rounds` table). All sessions within a round advance simultaneously via the wizard.

**Rationale**: The penalty and appeals wizards operate across all sessions of a round at once. There is no per-session independent lifecycle. The existing `rounds.finalized` boolean confirms this — it was always a round-level field. The `result_status` ENUM replaces it with three states.

**Alternatives considered**: Per-session status. Rejected — the wizard has no per-session approval granularity; advancing one session independently of others in the same round is not a supported operation.

---

### D-005: finalize_round split

**Question**: How does `finalize_round()` need to change?

**Decision**: `finalize_round()` in `result_submission_service.py` is split into two paths:
1. **`finalize_penalty_review(interaction, state)`** — applies staged penalties, reposts as `Post-Race Penalty Results`, sets `result_status = POST_RACE_PENALTY`, posts penalty announcements, keeps channel open, posts `AppealsReviewView`.
2. **`finalize_appeals_review(interaction, state)`** — applies staged appeal corrections, reposts as `Final Results`, sets `result_status = FINAL`, posts appeal announcements, closes channel.

**Rationale**: The two approval events have different terminal behaviours (keep open vs. close channel) and produce different result labels. Separating them cleanly avoids a flag-based branching mess inside a single function.

**Alternatives considered**: Single `finalize_round(stage=...)` with a parameter. Rejected — parameter-based branching on a domain concept is harder to read and test independently.

---

### D-006: Migration strategy for rounds.finalized

**Question**: How does the `finalized` boolean column migrate to `result_status`?

**Decision**: Add a new `result_status TEXT NOT NULL DEFAULT 'PROVISIONAL'` column to `rounds`. Populate it from existing `finalized` values: `UPDATE rounds SET result_status = 'FINAL' WHERE finalized = 1`. The `finalized` column is retained (not dropped) in this migration to allow rollback safety; it can be dropped in a future cleanup migration.

**Rationale**: SQLite does not support `DROP COLUMN` before version 3.35.0 (2021). To maintain compatibility across deployment targets, the safer approach is to add the new column and leave the old one in place. The application code switches to reading/writing `result_status` exclusively; `finalized` becomes inert.

**Alternatives considered**: `ALTER TABLE rounds DROP COLUMN finalized`. Rejected — SQLite version constraint on Raspberry Pi deployment could be older; adding and ignoring is safer.

---

### D-007: Where /division verdicts-channel lives

**Question**: Which cog hosts the new `/division verdicts-channel` command?

**Decision**: `season_cog.py` — the `division` `app_commands.Group` is already defined there. `season_service.py` also needs modification: `get_divisions_with_results_config()` must extend its SELECT to include `penalty_channel_id` so the review display and approval gate can read it.

**Rationale**: Grep of all cogs confirmed the `division` group (`name="division"`, guild_only, default_permissions=None) lives in `SeasonCog`. All existing `/division` subcommands (`division add`, etc.) are in that file. Adding `verdicts-channel` to the same group is the natural location.

**Alternatives considered**: `results_cog.py`. Rejected — that cog uses a `results_group` hierarchy (`/results config`, `/results round`, etc.); the `/division` namespace belongs to `season_cog.py`.

---

### D-008: Technology choices (confirmed — no changes)

**Decision**: All existing technologies retained unchanged.

- Python 3.13.2 / discord.py 2.7.1: sufficient for 4-field modals and chained Views.
- aiosqlite / SQLite: sufficient; new tables are simple foreign-key joins.
- APScheduler: no new scheduled jobs introduced by this feature.
- pytest: existing test structure extended; no new test frameworks.

**Rationale**: User explicitly instructed "keep the technologies, as this is a small feature increase." All requirements are satisfied by the existing stack.
