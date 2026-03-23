# Command Contracts: Results & Standings — Standings Design, Sync Command, and Sort-Key Correction

**Feature branch**: `022-results-standings-verification`
**Date**: 2026-03-23

---

## New Commands

### `/results standings sync`

| Property | Value |
|----------|-------|
| **Group** | `/results standings` (new subgroup under existing `results_group`) |
| **Subcommand** | `sync` |
| **Full invocation** | `/results standings sync <division>` |
| **Access tier** | Tier-2 admin (`@admin_only` + `@channel_guard`) |
| **Module gate** | R&S module must be enabled |
| **Parameter** | `division` — string — name of the division to sync |
| **Response type** | Ephemeral (deferred) |

**Success response** (standings reposted):
```
✅ Standings for **{division}** synced to the standings channel.
```

**No data response** (no completed rounds):
```
ℹ️ No completed rounds found for **{division}**. No standings to post.
```

**Error responses**:

| Condition | Message |
|-----------|---------|
| Division not found | `❌ Division '{division}' not found.` |
| R&S module not enabled | `❌ The Results & Standings module is not enabled on this server.` |
| Standings channel not configured | `❌ Division '{division}' has no standings channel configured.` |

---

## Modified Commands (existing — behaviour verified, no signature change)

### `/results reserves toggle`

| Property | Value |
|----------|-------|
| **Full invocation** | `/results reserves toggle <division>` |
| **Access tier** | Tier-2 admin (`@admin_only` + `@channel_guard`) |
| **Module gate** | R&S module must be enabled |
| **Parameter** | `division` — string |
| **Response type** | Ephemeral |

**Behaviour** (no change from existing implementation):  
Reads the current `reserves_in_standings` flag for the division; toggles it; persists via `UPSERT`; confirms new state.

**Success responses**:
```
✅ Reserve visibility for **{division}** set to **visible**.
✅ Reserve visibility for **{division}** set to **hidden**.
```

**Error responses**:

| Condition | Message |
|-----------|---------|
| Division not found | `❌ Division '{division}' not found.` |
| R&S module not enabled | `❌ The Results & Standings module is not enabled on this server.` |
