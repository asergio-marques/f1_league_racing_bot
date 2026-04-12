# Contract: `/results config` — xml-import subcommand

*Access level: Trusted admin (holds the configured season/config role).*
*All commands require the correct interaction channel and guild context.*

---

## `/results config xml-import`

**Access**: Trusted admin  
**Module gate**: Results & Standings module must be enabled  
**Season state required**: None (server-level config store; no active season required)

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | String | ✅ | Name of the existing points configuration to import into |
| `file` | Attachment | ❌ | Optional `.xml` file attachment containing the import payload |

### Behaviour — file absent (modal path)

1. Bot opens a Discord modal titled **"XML Points Config Import"** with a single
   multi-line text field labelled `"XML payload"` (max 4 000 chars).
2. User pastes XML and submits.
3. Bot parses, validates, and applies as described below.

### Behaviour — file present (attachment path)

1. No modal is opened.
2. Bot defers the interaction response ephemerally.
3. Bot reads the attachment:
   - If size > 100 KB → ephemeral error; no further processing.
   - If content is empty → ephemeral error; no further processing.
4. Bot parses, validates, and applies as described below.

### Parse & validate (both paths)

1. Parse XML with `lxml.etree` (`resolve_entities=False`, `no_network=True`).
   - Parse failure → ephemeral error with parse message; stop.
2. For each `<session>` block:
   - Extract `<type>` text; match case-insensitively to known session types.
     Unrecognised value → collect error; stop after all blocks checked; report all failures.
   - Extract `<position id="N">` elements; validate `N` is integer ≥ 1, text is integer ≥ 0.
     Duplicate `id` within a block → last-wins; warning included in success reply.
   - Extract `<fastest-lap>` element (optional); validate text is integer ≥ 0.
     `limit` attribute, if present, must be integer ≥ 1.
     `<fastest-lap>` on a qualifying session type → error.
3. If any structural errors collected → ephemeral error listing all failures; stop.
4. Run monotonic check: within each session block, sorted by position ascending, each
   successive non-zero points value must be ≤ previous. Violation → ephemeral error
   naming the session and offending positions; stop.
5. Config existence check: `name` must exist in `points_config_store` for this server.
   Not found → ephemeral `ConfigNotFoundError`-mapped error; stop.

### Apply (validation passed)

1. Open a single DB transaction.
2. For each session type in the payload, upsert its position rows and FL row (if present).
3. Commit atomically.
4. On DB error → roll back; ephemeral error.

### Success response

Ephemeral reply with a summary table:

```
✅ Imported into config **100%**:
| Session           | Positions updated | FL updated |
|-------------------|--------------------|------------|
| Feature Race      | P1–P3 (3 positions)| ✅ 1pt / limit P10 |
| Sprint Race       | P1–P2 (2 positions)| ✅ 1pt / limit P8  |
⚠️ Warnings: Feature Race — duplicate position id "2" (last value used)
```

### Audit log entry (success only)

Posted to the server's configured log channel via `output_router.post_log`:

```
{user.display_name} (<@{user.id}>) | /results config xml-import | {N} session(s) updated
  config: {name}
  sessions: {comma-separated list of updated session type labels}
```

### Error responses (all ephemeral)

| Condition | Message |
|-----------|---------|
| Module not enabled | ❌ Results & Standings module is not enabled. |
| File > 100 KB | ❌ File exceeds the 100 KB size limit. |
| Empty file | ❌ Attached file is empty. |
| XML parse failure | ❌ XML parse error: {lxml message} |
| Unknown session type | ❌ Unrecognised session type: "{value}". Valid types: Sprint Qualifying, Sprint Race, Feature Qualifying, Feature Race. |
| Fastest-lap on qualifying | ❌ Fastest-lap node is not valid for qualifying session: "{type}" |
| Structural validation | ❌ Validation failed:\n• {error 1}\n• {error 2}\n… |
| Monotonic violation | ❌ Monotonic ordering violation in {session}: P{a} ({x} pts) < P{b} ({y} pts) |
| Config not found | ❌ Config **{name}** not found on this server. |
| DB error | ❌ Database error during import; no changes were made. |
