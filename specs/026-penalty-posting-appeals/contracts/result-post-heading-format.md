# Contract: Result Post Heading and Lifecycle Label Format

**Feature**: 026-penalty-posting-appeals  
**Service**: `results_post_service.py`  
**Applies to**: All results posts and standings posts produced by the bot

---

## Format

Every results post and every standings post MUST begin with:

```
**Season {N} {Division Name} Round {X} — {Session Name}**
{Label}
```

Where:

| Token | Source | Example |
|-------|--------|---------|
| `N` | `seasons.season_number` | `3` |
| `Division Name` | `divisions.name` | `Pro Division` |
| `X` | `rounds.round_number` | `7` |
| `Session Name` | `sessions.session_type` formatted (see below) | `Feature Race` |
| `Label` | Derived from `rounds.result_status` (see below) | `Provisional Results` |

---

## Lifecycle Labels

| `result_status` | Label |
|-----------------|-------|
| `PROVISIONAL` | `Provisional Results` |
| `POST_RACE_PENALTY` | `Post-Race Penalty Results` |
| `FINAL` | `Final Results` |

---

## Session Type Formatting

| Raw session_type value | Display name |
|------------------------|-------------|
| `QUALIFYING` | `Qualifying` |
| `SPRINT_RACE` | `Sprint Race` |
| `FEATURE_RACE` | `Feature Race` |
| `SPRINT_QUALIFYING` | `Sprint Qualifying` |
| `ENDURANCE_QUALIFYING` | `Feature Qualifying` |
| `ENDURANCE_RACE` | `Feature Race` |
| Any other | Title-cased raw value |

---

## Standings Post Heading

Standings posts use the same heading format. The `{Session Name}` for a standings post is the session type of the most recently posted session for that round, or the round's primary session type if displaying an aggregate standings block.

> **Implementation note**: if the standings post aggregates multiple sessions (e.g., championship standings after a Sprint weekend), the heading uses the round's last posted session type. The label reflects the round's current `result_status` at the time of posting.

---

## Amendment Results

Results produced by `round results amend` MUST always use the label `Final Results` (the command is only permitted when `result_status = FINAL`; the amended post replaces the previous `Final Results` post with a new `Final Results` post).

---

## Invariant

**Zero unlabelled posts permitted.** Every call path in `results_post_service.py` that produces a results or standings message MUST pass the heading and label. This includes:
- Initial submission results post (label: `Provisional Results`)
- Post-penalty-approval repost (label: `Post-Race Penalty Results`)
- Post-appeals-approval repost (label: `Final Results`)
- `round results amend` repost (label: `Final Results`)
- `standings sync` forced repost (label: matches current `result_status`)
- `rounds sync` forced repost (label: matches current `result_status`)
