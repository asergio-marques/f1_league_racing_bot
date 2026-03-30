# Contract: Verdict Announcement Message Format

**Feature**: 026-penalty-posting-appeals  
**Service**: `verdict_announcement_service.py`  
**Applies to**: Penalty announcements (posted after penalty review approval) and appeal announcements (posted after appeals review approval)

---

## Announcement Structure

Each announcement is a single Discord message. One message is posted **per applied penalty or appeal correction**. If no penalties or corrections were staged, no announcement is posted.

```
**Season {N} {Division Name} Round {X} — {Session Name}**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
**Driver**: <@{driver_discord_id}>
**Penalty**: {penalty_description}
**Description**: {description_text}
**Justification**: {justification_text}
```

---

## Field Definitions

| Field | Source | Example |
|-------|--------|---------|
| `N` | `seasons.season_number` | `3` |
| `Division Name` | `divisions.name` | `Pro Division` |
| `X` | `rounds.round_number` | `7` |
| `Session Name` | `sessions.session_type` formatted | `Feature Race` |
| `driver_discord_id` | `driver_profiles.discord_user_id` | `123456789012345678` |
| `penalty_description` | Translated from penalty magnitude (see below) | `5 seconds removed` |
| `description_text` | Verbatim from `AddPenaltyModal.description` field | `Track limits — Turn 8` |
| `justification_text` | Verbatim from `AddPenaltyModal.justification` field | `Video evidence confirmed 3 instances` |

---

## Penalty Magnitude Translation

| Raw penalty value | Translated text |
|-------------------|-----------------|
| `+Ns` (positive time, e.g. `+5s`) | `{N} seconds removed` |
| `-Ns` (negative time, e.g. `-3s`) | `{N} seconds added` |
| `DSQ` | `Disqualified` |

Translation is performed by `verdict_announcement_service.translate_penalty(penalty_str: str) -> str`.

---

## Channel Resolution

```
if division_results_config.penalty_channel_id is None:
    return   # no verdicts channel configured — skip silently

target = bot.get_channel(int(penalty_channel_id))
if target is None:
    log_error("announcement skipped — verdicts channel inaccessible")
    return   # does NOT block finalization
```

---

## Session Name for Multi-Session Rounds

When a round has multiple sessions (e.g. Sprint format: Qualifying + Sprint Race + Feature Race), one announcement per penalty is posted. The **Session Name** in the header is the session to which the penalty belongs — derived from the `driver_session_result_id`'s linked `session.session_type`.

---

## Error Handling

- Channel send failure (permission denied, channel deleted): error is logged via `bot.output_router`; announcement skipped; **finalization is not blocked**.
- Missing driver profile (Discord ID not found): announcement posts `*(unknown driver)*` in place of mention; does not block.
