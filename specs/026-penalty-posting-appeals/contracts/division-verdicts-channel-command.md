# Contract: /division verdicts-channel Command

**Feature**: 026-penalty-posting-appeals  
**Command**: `/division verdicts-channel`  
**Cog**: `SeasonCog` (`src/cogs/season_cog.py`)  
**Group**: `division` (existing `app_commands.Group`)

---

## Command Schema

```
/division verdicts-channel <division> <channel>
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `division` | `str` | Yes | Division name; autocomplete from active divisions on this server |
| `channel` | `discord.TextChannel` | Yes | Discord text channel to use for verdict announcements |

---

## Access Control

- **Gate**: `@channel_guard` + `@admin_only` (tier-2 authority required — same as all other `division` subcommands)
- **Module gate**: Checks Results & Standings module enabled for the server before proceeding
- **Scope**: Guild-only; no DM support

---

## Behaviour

| Condition | Response |
|-----------|----------|
| Division not found for this server | Ephemeral error: `❌ Division "{name}" not found.` |
| Channel not accessible by the bot | Ephemeral error: `❌ Cannot access that channel. Ensure the bot has permission to post there.` |
| Success (first set) | Ephemeral confirmation: `✅ Verdicts channel for {division} set to #{channel.name}.` |
| Success (overwrite) | Ephemeral confirmation: `✅ Verdicts channel for {division} updated to #{channel.name}.` |

---

## Side Effects

1. `division_results_configs.penalty_channel_id` set to `str(channel.id)` for the matching `(guild_id, division_id)` row.
2. Audit log entry written: actor, division, change type `VERDICTS_CHANNEL_SET`, previous value (old channel ID or `None`), new value.

---

## Season Review Display

When the Results module is enabled, `/season review` displays the verdicts channel for each division in the per-division block, between the standings channel line and the teams block:

```
  Verdicts channel: #channel-name   ← if configured
  Verdicts channel: *(not configured)*  ← if missing
```

This is driven by `season_service.get_divisions_with_results_config()` including `penalty_channel_id` in its SELECT and LEFT JOIN result.

---

## Season Approval Gate

When the Results module is enabled, `/season approve` Gate 2 includes a check for `penalty_channel_id`. If any division is missing it, the approval is blocked:

```
❌ Season cannot be approved — R&S prerequisites not met:
• **{DivisionName}** is missing a verdicts channel — run /division verdicts-channel {DivisionName} <channel>
```

This check is added to the existing `errors` list alongside the results channel and standings channel checks.

---

## Validation

- `channel` must be resolvable via `interaction.guild.get_channel(channel.id)` (not None)
- Bot must have `send_messages` permission in `channel` — validated via `channel.permissions_for(guild.me).send_messages`
- `division` must match an existing `divisions` row for `guild_id`
