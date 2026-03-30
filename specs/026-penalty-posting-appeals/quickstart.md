# Quickstart: Penalty Posting, Appeals, and Result Lifecycle

**Feature**: 026-penalty-posting-appeals  
**Branch**: `026-penalty-posting-appeals`

---

## Prerequisites

- Python 3.13.2 installed
- Bot token and Discord server configured
- All dependencies installed: `pip install -r requirements.txt`
- Bot has already been run once (or migrations applied) from the `main` branch

---

## Running the Migration

The migration file `src/db/migrations/026_result_status_penalty_records.sql` is applied automatically on bot startup. To verify it runs cleanly without starting the full bot:

```powershell
# Windows (PowerShell) — run from repo root
python -c "
import asyncio, aiosqlite

async def check():
    async with aiosqlite.connect('your_bot.db') as db:
        async with db.execute(\"PRAGMA table_info(rounds)\") as cur:
            cols = [row[1] async for row in cur]
        print('rounds columns:', cols)
        async with db.execute(\"PRAGMA table_info(penalty_records)\") as cur:
            cols = [row[1] async for row in cur]
        print('penalty_records columns:', cols)
        async with db.execute(\"PRAGMA table_info(appeal_records)\") as cur:
            cols = [row[1] async for row in cur]
        print('appeal_records columns:', cols)

asyncio.run(check())
"
```

Expected output includes `result_status` in rounds, and both new tables present.

---

## Running Tests

```powershell
# From repo root (Windows)
python -m pytest tests/ -v

# Run only tests related to this feature
python -m pytest tests/unit/test_penalty_wizard.py tests/unit/test_results_post_service.py tests/unit/test_verdict_announcement_service.py tests/integration/test_round_lifecycle.py -v
```

---

## Key Flow to Verify Manually

1. **Submit a round** with at least one session → initial results post should read  
   `Season N {DivisionName} Round X — {SessionName}` followed by `Provisional Results`

2. **In the submission channel**, click `Add Penalty` → modal now has 4 fields: driver, penalty value, description, justification. Fill all four.

3. **Click Approve** on `PenaltyReviewView` → `_show_approval_step()` posts `ApprovalView`

4. **Click Approve** on `ApprovalView`:
   - Results and standings reposted with label `Post-Race Penalty Results`
   - Channel stays open; `AppealsReviewView` appears
   - Penalty announcement posted to verdicts channel (or results channel if no verdicts channel configured)
   - Round `result_status` = `POST_RACE_PENALTY`

5. **In the appeals prompt**, optionally add a correction; then click Approve:
   - Results and standings reposted with label `Final Results`
   - Channel closes
   - Round `result_status` = `FINAL`
   - `round results amend` now available for this round

6. **Verify amend gate**: attempt `round results amend` on a round still in `PROVISIONAL` or `POST_RACE_PENALTY` → bot should reject with a clear error.

---

## Configuring a Verdicts Channel

```
/division verdicts-channel <division_name> <#channel>
```

- Requires tier-2 admin role
- Bot validates channel accessibility before storing
- If the channel is inaccessible, announcements are skipped without blocking finalization (no fallback)

---

## File Locations Quick Reference

| File | Purpose |
|------|---------|
| `src/db/migrations/026_result_status_penalty_records.sql` | DB schema changes |
| `src/models/round.py` | Round dataclass — `result_status` field |
| `src/services/penalty_wizard.py` | Main wizard — modal, views, transitions |
| `src/services/result_submission_service.py` | `finalize_penalty_review()`, `finalize_appeals_review()` |
| `src/services/results_post_service.py` | Heading + label on all result/standings posts |
| `src/services/penalty_service.py` | `StagedPenalty` dataclass + `apply_penalties()` |
| `src/services/verdict_announcement_service.py` | Announcement posting logic |
| `src/cogs/season_cog.py` | `/division verdicts-channel` command |
| `src/cogs/results_cog.py` | `round results amend` FINAL state gate |
