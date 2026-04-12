# Feature Specification: Points Configuration XML Import

**Feature Branch**: `034-points-config-bulk-import`
**Created**: 2026-04-12
**Status**: Draft
**Input**: User description: "A command `results config xml-import` that opens a modal (or accepts a file attachment) to receive a structured XML payload representing an entire points configuration, validates it, and applies it to a named config."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Happy-Path XML Import via Modal (Priority: P1)

A league administrator wants to set up or overwrite the points distribution for an entire config (all session types at once) by pasting a pre-prepared XML document into a single modal field, without having to issue separate per-session commands.

**Why this priority**: This is the core value proposition of the feature — eliminate the repeated, error-prone per-session workflow.

**Independent Test**: Can be fully tested by running `/results config xml-import name:"100%"` with a well-formed XML covering at least one session type, verifying the database values match the XML after submission.

**Acceptance Scenarios**:

1. **Given** a named config exists, **When** the admin runs `/results config xml-import name:"100%"` (no attachment), **Then** a modal opens containing a single multi-line text field labelled for XML input.
2. **Given** valid XML is submitted in the modal, **When** the bot processes it, **Then** positions and fastest-lap values from the XML are persisted to the database and the bot responds with a success summary.
3. **Given** the XML omits a `<fastest-lap>` node for a session, **When** the import is processed, **Then** the existing fastest-lap values for that session are left unchanged.
4. **Given** the XML omits a specific `<position>` id for a session, **When** the import is processed, **Then** the points for that position are left unchanged.

---

### User Story 2 — XML Import via File Attachment (Priority: P2)

An administrator whose XML document exceeds the modal's 4 000-character limit can supply the payload as a plain-text `.xml` file attachment instead, bypassing the modal entirely.

**Why this priority**: Large configs with many positions across all four session types may exceed the Discord modal limit; file input must be available as an alternative.

**Independent Test**: Can be fully tested by running `/results config xml-import name:"100%" file:<attachment>` and verifying the config is updated correctly without a modal appearing.

**Acceptance Scenarios**:

1. **Given** a valid `.xml` file is attached to the command, **When** the admin invokes `/results config xml-import name:"100%" file:<attachment>`, **Then** no modal appears and the file content is processed directly.
2. **Given** the attached file contains valid XML, **When** processing completes, **Then** the config is updated and a success summary is returned.
3. **Given** the attached file is not a valid `.xml` file or is empty, **When** the command is invoked, **Then** an error is returned and the config is left unchanged.

---

### User Story 3 — Validation Failure and Rollback (Priority: P2)

An administrator submits malformed or logically invalid XML. The bot must reject the import cleanly without partially modifying the config.

**Why this priority**: Partial writes to an active config can corrupt race result calculations; atomic rollback is non-negotiable.

**Independent Test**: Can be fully tested by submitting XML with a monotonically increasing points sequence (P1 < P2) and verifying no change is made to the database.

**Acceptance Scenarios**:

1. **Given** XML contains a position with a non-integer or negative points value, **When** processed, **Then** an error is returned listing the offending field, and the config is unchanged.
2. **Given** XML contains points that increase as position number increases (non-monotonic) for any session block, **When** processed, **Then** an error is returned identifying the session and positions at fault, and the config is unchanged.
3. **Given** XML contains an unrecognised `<type>` value, **When** processed, **Then** the entire import is rejected with an error naming the unrecognised type, and the config is unchanged.
4. **Given** XML is structurally malformed (not parseable), **When** processed, **Then** a parse error is returned and the config is unchanged.

---

### User Story 4 — Partial-Session XML (Priority: P3)

An administrator wants to update only a subset of session types in one import (e.g., only Feature Race) while leaving others untouched.

**Why this priority**: Supports incremental config workflows; the partial-update semantics are a key design decision but secondary to getting any import working end-to-end.

**Independent Test**: Can be tested by importing XML containing only one `<session>` block and verifying the other three session types are unmodified.

**Acceptance Scenarios**:

1. **Given** XML contains only a Feature Race block, **When** imported, **Then** Sprint Qualifying, Sprint Race, and Feature Qualifying entries remain unchanged.
2. **Given** a session block is present but contains no `<position>` or `<fastest-lap>` children, **When** imported, **Then** that session's config is unmodified.

---

### Edge Cases

- What happens when the named config does not exist on the server? → Return a `ConfigNotFoundError`-mapped error; no write occurs.
- What if the same `<position id>` appears more than once within a session block? → Last-value-wins (consistent with existing bulk-session behaviour), with a warning included in the success response.
- What if `<fastest-lap limit>` is set to 0 or a non-positive integer? → Treat as a structural validation error; reject import.
- What if a `<position>` id is 0 or negative? → Treat as a structural validation error; reject import.
- What if the modal is submitted empty? → Return an early error without touching the database.
- What if the file attachment exceeds a reasonable size (e.g., > 100 KB)? → Reject with a size-limit error; no read or write occurs.

## Requirements *(mandatory)*

### Functional Requirements

**Command & Input**

- **FR-001**: The system MUST expose a `/results config xml-import` slash command accepting a required `name` (config name) string parameter and an optional `file` (XML attachment) parameter.
- **FR-002**: When `file` is **not** provided, the command MUST open a Discord modal containing a single multi-line text field for XML input.
- **FR-003**: When `file` **is** provided, the command MUST skip the modal and process the attachment content directly.
- **FR-004**: The system MUST reject attached files that are empty or exceed 100 KB, returning a user-facing error without modifying the config.

**XML Schema & Parsing**

- **FR-005**: The system MUST accept XML structured as one or more `<session>` elements, each containing a `<type>` text element, zero or more `<position id=N>` elements (where N is a positive integer), and an optional `<fastest-lap limit=L>` element (where L is a positive integer or absent).
- **FR-006**: The `<type>` value MUST map to one of the four known session types using case-insensitive matching: "Sprint Qualifying", "Sprint Race", "Feature Qualifying", "Feature Race".
- **FR-007**: If the XML contains any `<type>` value that does not match a known session type, the system MUST reject the entire import and return an error naming the unrecognised value.
- **FR-008**: If XML is structurally malformed (unparseable), the system MUST return a parse error and leave the config unchanged.
- **FR-009**: If multiple `<position>` elements share the same `id` within one session block, the last occurrence wins; the success response MUST include a warning identifying the duplicates.

**Validation**

- **FR-010**: The system MUST validate all `<position>` point values are non-negative integers.
- **FR-011**: The system MUST validate all `<position id>` attributes are positive integers (≥ 1).
- **FR-012**: The system MUST validate the `<fastest-lap limit>` attribute, when present, is a positive integer (≥ 1).
- **FR-013**: The system MUST validate `<fastest-lap>` point values are non-negative integers.
- **FR-014**: After applying XML changes to a staging copy, the system MUST verify that within each individual session block the points are monotonically non-increasing as position number increases (i.e., a higher-numbered position must not earn more points than a lower-numbered position, except where the lower-positioned entry has zero points).
- **FR-015**: If any validation rule (FR-010–FR-014) fails, the system MUST reject the entire import, report all failing rules, and leave the database unchanged.

**Apply / Rollback**

- **FR-016**: Before modifying any database rows the system MUST capture a backup of existing `points_config_entries` and `points_config_fl` rows for the target config.
- **FR-017**: The system MUST apply all changes from the XML to the database as a single atomic transaction; either all changes persist or none do.
- **FR-018**: If any database error occurs during the write, the system MUST roll back to the pre-import state and return a user-facing error.
- **FR-019**: Session types absent from the XML MUST be left unchanged in the database.
- **FR-020**: Within a session type present in the XML, position IDs absent from the XML MUST be left unchanged in the database.
- **FR-021**: Within a session type present in the XML, if `<fastest-lap>` is absent, the existing fastest-lap entry MUST be left unchanged.

**Response**

- **FR-022**: On success, the system MUST respond with a summary listing each session type updated, the positions changed, and the new fastest-lap values (if updated).
- **FR-023**: On failure, the system MUST respond with a user-facing error message identifying the root cause (parse error, unknown session type, validation failure, or database error); the response MUST be ephemeral.
- **FR-024**: The success summary MUST be ephemeral and MUST be posted to the audit log channel following the existing audit log pattern.

### Key Entities

- **PointsConfigStore**: Represents a named points configuration scoped to a Discord server. Identified by `config_name` + `server_id`.
- **PointsConfigEntry**: A single (config, session type, position) → points mapping stored in `points_config_entries`.
- **PointsConfigFastestLap**: The fastest-lap bonus and position limit for a (config, session type) pair, stored in `points_config_fl`.
- **XmlImportPayload**: The parsed, in-memory representation of a submitted XML document, holding session-type-keyed dictionaries of position → points and optional fastest-lap data. Never persisted directly.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An administrator can update all four session types in a single command invocation, replacing a workflow that previously required at minimum four separate bulk-session commands.
- **SC-002**: An invalid import (parse error, unknown session type, or monotonic violation) is rejected within the same interaction response with no database modification occurring.
- **SC-003**: A valid import for a config with up to 40 positions across four session types completes and is reflected in the config display within 5 seconds of submission.
- **SC-004**: File-based imports up to 100 KB are accepted and processed without error.
- **SC-005**: The audit log captures every import attempt (success or failure) with sufficient detail to reconstruct what was attempted.
