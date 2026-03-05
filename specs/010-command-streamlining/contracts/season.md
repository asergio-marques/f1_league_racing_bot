# Contract: `/season` Command Group

*Access level noted per subcommand. "Trusted admin" = holds the configured season/config role.*
*All commands require the interaction channel and guild context unless noted.*

---

## `/season setup`

**Access**: Trusted admin  
**Season state required**: None (must have no existing season)

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| *(none)* | | | |

**Preconditions**: No season (any state) exists for this server.  
**Postconditions**: Season created in `SETUP` state. Confirmation sent ephemerally.  
**Error cases**: Season already exists → ephemeral error naming the conflict.

---

## `/season review`

**Access**: Trusted admin  
**Season state required**: `SETUP`

No parameters. Displays pending configuration with **Approve** and **Go Back to Edit** buttons.

---

## `/season approve`

**Access**: Trusted admin  
**Season state required**: `SETUP`

No parameters. Commits all pending divisions and rounds; transitions season to `ACTIVE`; arms scheduler.

---

## `/season status`

**Access**: Interaction role  
**Season state required**: `ACTIVE`

No parameters. Shows active season overview: divisions, next scheduled round per division.

---

## `/season cancel`

**Access**: **Server administrator (Manage Server permission)**  
**Season state required**: `ACTIVE`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `confirm` | String | ✅ | Must be exactly `CONFIRM` (case-sensitive) |

**Preconditions**: Season is `ACTIVE`.  
**Postconditions**: Cancellation notice posted to every `ACTIVE` division's forecast channel (no role mentions). Season and all associated data deleted from database. New season may be configured immediately.  
**Error cases**: Wrong confirmation string → aborted, no changes. Season not `ACTIVE` → rejected. Invoker lacks Manage Server permission → permission error.
