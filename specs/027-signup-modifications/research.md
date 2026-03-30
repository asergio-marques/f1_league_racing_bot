# Research: Signup Module Modifications and Enhancements

**Feature**: `027-signup-modifications`
**Branch**: `027-signup-modifications`
**Date**: 2026-03-30

---

## 1. Nationality Validation: Bundled Static Lookup

**Decision**: Implement `NATIONALITY_LOOKUP` as a plain Python `dict[str, str]` in a new
`src/utils/nationality_data.py` module. Keys are lowercase nationality adjectives AND
lowercase country names; values are the canonical Title-Case nationality adjective
(e.g., `"french"` → `"French"`, `"france"` → `"French"`). The entry `"other"` maps to
`"Other"` as the universal fallback.

**Rationale**: A static in-memory dict is instantaneous, zero-dependency, and trivially
testable. The full list of sovereign states fits comfortably within a ~250-entry dict.
No runtime I/O, no database round-trip, and no external package needed.

**Alternatives considered**:
- **`pycountry` library**: rejected — adds an external dependency; covers historical
  states, territories, and subdivisions beyond the required scope; requires packaging on
  the Raspberry Pi deployment target.
- **Database-stored lookup**: rejected — unnecessary indirection for static reference data;
  would need a schema migration, queries, and admin interface to update.
- **Retain 2-letter ISO codes and extend**: rejected — user requirement explicitly
  specifies full names; 2-letter codes must be rejected with a clear error.

---

## 2. Multi-line Bulk Points Input: Discord Modal

**Decision**: Implement `results bulk-config session` and `results bulk-amend session`
using `discord.ui.Modal` with a single `discord.ui.TextInput(style=discord.TextStyle.paragraph)`.
The command handler calls `await interaction.response.send_modal(modal)` as the **initial
response** (modal IS the response — no `defer()` call first).

**Rationale**: Discord slash commands do not support native multi-line text input. The
Modal API is the only Discord-native mechanism for collecting structured multi-line text
in a single interaction. `TextInput.paragraph` allows up to 4,000 characters — sufficient
for 20+ `<position>, <points>` pairs. The modal holds `config_name` and `session_type`
as instance attributes captured at command invocation time.

**Modal parse contract**:
- Split on `\n`; strip each line; skip blank lines.
- Parse each non-blank line as `<int>, <int>`. Position must be ≥ 1; points must be ≥ 0.
- Collect errors in a list; apply valid lines in one DB transaction after parsing all lines.
- Duplicate positions: last value wins; noted in confirmation output.
- Empty submission (no parseable lines): no changes; user informed.

**Alternatives considered**:
- **Multiple individual slash command invocations**: rejected by spec; tedious for a 20-
  position table requiring 20 separate interactions.
- **Comma-separated single-line parameter**: rejected — Discord `str` parameters are
  capped at 100 characters, which is insufficient for bulk entries; still requires parsing
  without the UX benefit of a multi-line display.

---

## 3. CSV Generation: stdlib csv + io.StringIO / io.BytesIO

**Decision**: Use Python's built-in `csv.writer(io.StringIO())` to build the CSV content,
then encode via `io.BytesIO(content.encode("utf-8-sig"))` (UTF-8 with BOM). Deliver as
`discord.File(BytesIO_buffer, filename="unassigned_drivers.csv")` in an ephemeral response.

**Rationale**: `csv` is part of the Python standard library — no additional packages
needed (user requirement). UTF-8-BOM ensures Excel opens the file correctly without a
manual encoding dialog. `csv.writer` handles quoting of fields that contain commas.

**Slot columns**: The exporting command fetches the server's active `AvailabilitySlot`
records first. Slot labels (`make_label(day_of_week, time_hhmm)`) are used as column
headers. Driver rows mark `"X"` where `slot_sequence_id` is in the driver's
`availability_slot_ids`, or leave the cell empty otherwise.

**Alternatives considered**:
- **pandas**: rejected — heavyweight external dependency; unnecessary for simple tabular
  output.
- **Plain string concatenation**: rejected — fragile for display names or team names
  containing commas or double-quotes; `csv.writer` handles quoting automatically.

---

## 4. handle_member_remove Bug Fix (Pre-existing)

**Decision**: Remove the `raise NotImplementedError` at the end of
`wizard_service.handle_member_remove()`. The error was silently swallowed by discord.py's
event dispatcher in production but left the wizard-cleanup log path incomplete and
prevented wizard-state log notifications from being added.

**Rationale**: The `raise NotImplementedError` is an unfinished implementation stub.
The wizard cleanup logic above it is correct and should run without interruption. After
removing the stub, log posting for wizard-state drivers (FR-L001/FR-L002) is added
immediately after the cleanup block.

**Alternatives considered**:
- **Full rewrite of handle_member_remove**: rejected — the existing cleanup logic for
  wizard states is correct and complete; only the trailing stub and missing log call
  need to change.

---

## 5. Unassigned Command Restructure: App Commands Group

**Decision**: Create `unassigned_group = app_commands.Group(name="unassigned", ...)` as a
subgroup under the `signup` command group. Register `list` and `export` as subcommands on
`unassigned_group`. Remove the existing bare `@signup.command(name="unassigned")`.

**Rationale**: Discord's App Commands system only supports the subcommand-group structure
for commands of the form `/signup unassigned list`. There is no mechanism to have both a
bare `/signup unassigned` command and subcommands under it simultaneously;
the restructure is required.

**Alternatives considered**:
- **Rename existing command + add top-level `/signup export-unassigned`**: rejected —
  violates Bot Behavior Standards (subcommand-group convention); fragments a coherent
  administrative surface across unrelated command paths.

---

## 6. Server-Leave Logging Architecture

**Decision**: Split leave-logging responsibility between two locations:
1. **Wizard-state drivers** (Pending Signup Completion, Pending Admin Approval, etc.):
   log posting is added inside `wizard_service.handle_member_remove()` after the cleanup
   block (same method that already handles their cleanup).
2. **UNASSIGNED / ASSIGNED drivers**: after the `wizard_service.handle_member_remove()`
   call returns in `signup_cog.on_member_remove()`, query `driver_profiles` for the
   departing member. If state is UNASSIGNED or ASSIGNED, call `output_router.post_log()`
   with the driver's display name, Discord UID, and state label.

**Rationale**: `wizard_service` already owns the wizard-state lifecycle; it has the
context to post a log entry at the same time as cleanup. UNASSIGNED/ASSIGNED drivers
require a separate DB query that fits naturally in the cog's event handler, which already
holds the `guild` and `member` objects.

**Alternatives considered**:
- **Centralise all leave logging in wizard_service**: rejected — wizard_service does not
  own UNASSIGNED/ASSIGNED profile logic; passing guild member objects deep into the
  service layer for non-wizard states violates SRP.
- **New dedicated leave_logging_service**: rejected — over-engineering; two straightforward
  query-and-post operations do not justify a new service abstraction.
