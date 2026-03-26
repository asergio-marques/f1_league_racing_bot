# Contract: `/season setup` Command — game_edition Parameter

**Feature**: 024-season-archive  
**Command**: `/season setup`  
**Change type**: Breaking addition — new mandatory parameter

---

## Command Signature (Before)

```
/season setup
```

No parameters. Admin-only. Gated: rejects if any season exists for this server.

---

## Command Signature (After)

```
/season setup  game_edition:<integer>
```

| Parameter | Type | Required | Constraints | Description |
|-----------|------|----------|-------------|-------------|
| `game_edition` | `app_commands.Range[int, 1, 9999]` | ✅ Yes | Positive integer, ≥ 1 | The game release year/edition identifier (e.g. `25` for F1 25). Stored on the season record at setup time and retained immutably on archive. |

---

## Gating Changes

| Condition | Before | After |
|-----------|--------|-------|
| Any season exists (any status) | ❌ Rejected | — |
| SETUP or ACTIVE season exists | — | ❌ Rejected |
| All existing seasons are COMPLETED | — | ✅ Allowed |
| No seasons exist | ✅ Allowed | ✅ Allowed |

---

## Error Responses

| Trigger | Response (ephemeral) |
|---------|----------------------|
| `game_edition` not provided | Discord rejects at interaction layer (required parameter) |
| `game_edition` < 1 | Discord rejects at interaction layer (Range constraint) |
| ACTIVE season exists | `❌ A season is currently active for this server. Complete it before starting a new one.` |
| SETUP season exists | `❌ A season setup is already in progress for this server. Use /season review to continue, or cancel it first.` |

---

## Success Response

Same as existing: presents the season setup wizard accepting division/round configuration, with the additional confirmation of the recorded game edition shown in the `/season review` summary.

---

## `/season review` Summary (After)

The review embed must display `game_edition` alongside `season_number` and `start_date`:

```
Season #1 (F1 25) — starts 2026-05-01
```

Format: `Season #{season_number} (F1 {game_edition})`. This rendering happens in whatever formatter produces the review message.

---

## Affected Command Groups

No other command signatures change. The following commands gain an **immutability guard** (reject with an error if the target season is COMPLETED) but their signatures are unchanged:

- `/round cancel`, `/round delete`, `/round add`
- `/results submit`, `/results amend`
- `/season cancel`
- `/division cancel`
- `/driver assign`, `/driver unassign`, `/driver sack`

Error message for immutability guard: `❌ This season is archived (COMPLETED) and cannot be modified.`
