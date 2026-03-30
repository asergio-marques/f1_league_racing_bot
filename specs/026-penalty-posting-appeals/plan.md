# Implementation Plan: Penalty Posting, Appeals, and Result Lifecycle

**Branch**: `026-penalty-posting-appeals` | **Date**: 2026-03-30 | **Spec**: [specs/026-penalty-posting-appeals/spec.md](spec.md)  
**Input**: Feature specification from `specs/026-penalty-posting-appeals/spec.md`

## Summary

Extends the post-submission penalty review wizard (spec 023) with a three-state round result lifecycle (`PROVISIONAL` → `POST_RACE_PENALTY` → `FINAL`). When the tier-2 admin approves the penalty review, the transient submission channel now transitions to an **Appeals Review** state rather than closing — closing only once the appeals review is approved too. Every results and standings post gains a standard heading and lifecycle label. A new `verdict_announcement_service` posts one announcement per applied penalty or appeal correction to the per-division configured verdicts channel. The existing `AddPenaltyModal` is expanded with mandatory description and justification fields (same modal reused for appeals). `round results amend` is gated to rounds in `FINAL` state only.

## Technical Context

**Language/Version**: Python 3.13.2  
**Primary Dependencies**: discord.py 2.7.1 (`app_commands`, `discord.ui.Modal`, `discord.ui.View`), aiosqlite ≥ 0.19, APScheduler ≥ 3.10  
**Storage**: SQLite via aiosqlite; schema migrations in `src/db/migrations/` as numbered SQL files applied on bot startup  
**Testing**: pytest (`python -m pytest tests/ -v` from repo root); unit tests in `tests/unit/`, integration in `tests/integration/`  
**Target Platform**: Linux (Raspberry Pi); developed on Windows  
**Project Type**: Discord bot service  
**Performance Goals**: All interaction callbacks must call `interaction.response.defer()` before any DB or network I/O to stay within Discord's 3-second response window  
**Constraints**: SQLite single-writer; all persistent `discord.ui.View` instances must use `timeout=None` and be re-registered on bot restart so the appeals prompt survives a restart  
**Scale/Scope**: Single Discord server per bot instance; small-to-medium league servers

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Verdict | Notes |
|-----------|---------|-------|
| I. Trusted Configuration Authority | ✅ PASS | New wizard interactions reuse `_require_lm` (tier-2 gate). `/division verdicts-channel` uses `@admin_only`. No implicit super-user paths introduced. |
| II. Multi-Division Isolation | ✅ PASS | `penalty_channel_id` stored per `DivisionResultsConfig`; all announcement state and wizard state is division-scoped; no cross-division reads. |
| III. Resilient Schedule Management | ✅ PASS | No interaction with scheduling, postponements, or track changes. |
| IV. Three-Phase Weather Pipeline | ✅ PASS | Entirely separate module; zero overlap with weather pipeline. |
| V. Observability & Change Audit Trail | ✅ PASS | Audit log entries written for each penalty and appeal approval, including description and justification (FR-020). No silent mutations. |
| VI. Incremental Scope Expansion | ✅ PASS | Penalty adjudication and appeals formally ratified as in-scope item 10 (constitution v2.7.0). |
| VII. Output Channel Discipline | ✅ PASS | Verdicts channel is a module-introduced channel documented in spec (FR-011). Required for season approval (FR-026). Bot skips announcement if channel is inaccessible; does not post to unregistered channels. |
| VIII. Driver Profile Integrity | ✅ PASS | Driver state machine unaffected; existing driver lookup in wizard reused without modification. |
| IX. Team & Division Structural Integrity | ✅ PASS | No team-level changes; division structure unaffected. |
| X. Modular Feature Architecture | ✅ PASS | Feature lives in the Results & Standings module; `/division verdicts-channel` checks module-enabled gate. |
| XI. Signup Wizard Integrity | ✅ PASS | Completely unaffected. |
| XII. Race Results & Championship Integrity | ✅ PASS | Lifecycle labels, FINAL gate on amend, and standings recomputation all align with constitution v2.7.0 Penalty Announcements + Penalty Appeals subsections. |

**Gate result**: All 12 principles pass. No violations.

## Project Structure

### Documentation (this feature)

```text
specs/026-penalty-posting-appeals/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   ├── division-verdicts-channel-command.md
│   ├── announcement-message-format.md
│   └── result-post-heading-format.md
└── tasks.md             # Phase 2 output (/speckit.tasks command — NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/
├── db/
│   └── migrations/
│       └── 026_result_status_penalty_records.sql   # NEW — result_status column, penalty_records,
│                                                   #        appeal_records tables, penalty_channel_id
├── models/
│   └── round.py                                    # MODIFY — result_status: str replaces finalized: bool
├── services/
│   ├── penalty_wizard.py                           # MODIFY — +2 modal fields; AppealsReviewView new class;
│   │                                               #           penalty-approve splits from appeals-approve
│   ├── result_submission_service.py                # MODIFY — finalize_round split into two paths:
│   │                                               #           penalty-approval and appeals-approval
│   ├── results_post_service.py                     # MODIFY — add heading + lifecycle label to all
│   │                                               #           results and standings post functions
│   ├── penalty_service.py                          # MODIFY — apply_penalties stores PenaltyRecord rows;
│   │                                               #           StagedPenalty carries description + justification
│   ├── season_service.py                           # MODIFY — get_divisions_with_results_config query
│   │                                               #           extended to include penalty_channel_id
│   └── verdict_announcement_service.py             # NEW — post one announcement per penalty/correction
│                                                   #        to verdicts channel; skip if inaccessible
└── cogs/
    ├── results_cog.py                              # MODIFY — gate `round results amend` to FINAL state only
    └── season_cog.py                               # MODIFY — /division verdicts-channel command;
                                                    #           /season review shows verdicts channel;
                                                    #           /season approve blocks if verdicts channel
                                                    #           missing on any division (Gate 2 extension)
bot.py                                              # MODIFY — register AppealsReviewView for restart
                                                    #           recovery: re-post appeals prompt for
                                                    #           rounds in POST_RACE_PENALTY state

tests/
├── unit/
│   ├── test_penalty_wizard.py                      # MODIFY — expanded modal fields, appeals view behaviour
│   ├── test_results_post_service.py                # MODIFY — assert heading and label on all post types
│   └── test_verdict_announcement_service.py        # NEW — announcement formatting, inaccessible channel
│                                                   #        skips cleanly, empty-staged list produces no post
└── integration/
    └── test_round_lifecycle.py                     # NEW/MODIFY — full three-state lifecycle:
                                                    #   PROVISIONAL → POST_RACE_PENALTY → FINAL
```

**Structure Decision**: Single project (existing layout). All changes land within the existing `src/` and `tests/` trees. One new service file (`verdict_announcement_service.py`); all other changes modify existing files. One new migration file (`026_result_status_penalty_records.sql`).
