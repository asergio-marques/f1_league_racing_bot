# Phase 1 contracts: the eleven `/images test` commands

The interface this feature exposes is Discord application commands. This document is the contract each honours after the change; where the change is none, the row says so.

**Unchanged for all eleven**: `@channel_guard` + `@admin_only`, ephemeral reply to the invoker, nothing posted to any division channel, rasteriser checked before anything is resolved.

---

## Command shape

| Command | `division` | `round` | Change |
|---|---|---|---|
| `/images test calendar` | optional | — | was mandatory |
| `/images test lineup` | optional | — | was mandatory |
| `/images test results` | optional | optional | both were mandatory |
| `/images test standings` | optional | optional | both were mandatory |
| `/images test attendance` | optional | optional | both were mandatory |
| `/images test rsvp` | optional | optional | both were mandatory |
| `/images test verdict` | optional | optional | both were mandatory |
| `/images test weather-p1` | optional | optional | both were mandatory |
| `/images test weather-p2` | optional | optional | both were mandatory |
| `/images test weather-p3` | optional | optional | both were mandatory |
| `/images test weather-mystery` | optional | optional | both were mandatory |

Optionality is a platform-level relaxation only. Whether a value is *required* is decided at resolution by whether a season exists, which the platform cannot express.

---

## Resolution contract

Evaluated in this order, and entirely before any render is attempted (FR-006, inherited from 045).

```
1. rasteriser available?            → no  ⇒ refuse (unchanged)
2. season = ACTIVE, else SETUP, else none
3. season exists?
   ├─ yes → 3a. division supplied?  → no  ⇒ refuse REASON_MISSING_INPUT
   │        3b. round supplied (if kind needs one)?
   │                                → no  ⇒ refuse REASON_MISSING_INPUT
   │        3c. division resolves?  → no  ⇒ refuse REASON_NO_DIVISION
   │        3d. round resolves?     → no  ⇒ refuse REASON_NO_ROUND
   │        3e. kind-specific refusals of 045 (rounds, teams, format)
   │        3f. draw
   └─ no  → 3g. server team list non-empty (reserve excluded)?
            │   → no, and kind draws a roster ⇒ refuse REASON_NO_SERVER_TEAMS
            │   → no, and kind draws none     ⇒ fabricate and draw
            └─ yes ⇒ fabricate and draw; division and round supplied are disregarded
```

---

## Refusal messages

Each names the condition that was not met, so a manager can tell the four apart from the message alone (SC-003).

| Reason | Message shape |
|---|---|
| `REASON_MISSING_INPUT` | names the parameter omitted and that this server has a season to draw it against |
| `REASON_NO_DIVISION` | names the divisions the season holds (unchanged) |
| `REASON_NO_ROUND` | names the round numbers the division holds (unchanged) |
| `REASON_NO_ROUNDS` | points at `/round add` (unchanged) |
| `REASON_NO_TEAMS` | points at `/team add`, says the **division** holds none (unchanged) |
| `REASON_NO_SERVER_TEAMS` | points at `/team add`, says the **server** has configured none, and names the six kinds that would still draw |
| `REASON_MYSTERY_ROUND` / `REASON_NOT_MYSTERY_ROUND` | unchanged |

`REASON_NO_SEASON` is withdrawn. No path raises it after this change.

---

## Reply contract

The reply 045 defined, with three additions. All are lines in the one reply, not a second channel (045's A-012 stands).

| Element | When | Source |
|---|---|---|
| Header, per-picture ✅/❌, attachments | always | unchanged |
| Fallback and directory-fault report | always | unchanged (FR-023) |
| Notices | when the render raised any | unchanged |
| Invented-drivers note | a real division with no seated driver | unchanged (045) |
| **Pending-approval note** | season drawn is SETUP | FR-004 |
| **Fabricated-league banner** | no season exists | FR-024 — states that no season exists, that the league is invented, and that the team names are the server's own |
| **No-nationality tally** | league collects nationality and ≥1 seated driver has none | FR-028 |

The header names the season number drawn on both paths (FR-004).

---

## Autocomplete contract

`division` completes against the divisions of whichever season the resolution selects (FR-003). On a season-less server it offers nothing, which is acceptable because the parameter is optional there (A-011).

The autocomplete must not raise: it is already wrapped so that a failure yields an empty list rather than breaking the command, and that stands.

---

## What this feature does not change

- The field catalogue, crop rules or template contract of any of the eleven kinds.
- What 045 fabricates for outcomes, forecasts, attendance records and verdicts.
- Which users may invoke a preview.
- Any posting path. The standings aspect remains previewable and unposted.
