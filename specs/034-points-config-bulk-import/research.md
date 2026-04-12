# Research: Points Configuration XML Import

No new architectural paradigms are introduced. All decisions below resolve design
choices within the established Python / discord.py / aiosqlite / SQLite stack.

---

## Decision 1 — XML parsing library

**Decision**: Use `lxml` (`pip install lxml`) via its `lxml.etree` module with
`resolve_entities=False` (the default for `lxml.etree.XMLParser`) and
`no_network=True` to explicitly block all external resource fetching.

**Rationale**: `lxml` is actively maintained, widely deployed, and backed by
libxml2. It produces clear, consistent `XMLSyntaxError` exceptions on parse
failure, which simplifies error reporting. The Raspberry Pi deployment target
has `libxml2` available via `apt` and `lxml` installs cleanly from a wheel
or from source with `apt install python3-lxml`. The additional dependency weight
is acceptable given this command is an infrequent, admin-only workflow (executed
a few times per season configuration cycle, not on every interaction).

**Alternatives considered**:
- `defusedxml` — rejected; last released April 2021, making it an effectively
  unmaintained dependency. Its patch surface is stable but the lack of updates
  is an objection in any dependency audit.
- `stdlib xml.etree.ElementTree` — rejected in favour of `lxml` for explicit
  security hardening via parser flags; while Python 3.8+ mitigates the most
  severe attacks by default, using a hardened parser with explicit flags is
  a cleaner posture for untrusted user input.

---

## Decision 2 — XML schema design

**Decision**: Use a flat, text-content-first schema with attributes for IDs and
limits. Root element is `<config>` (wraps one or more `<session>` children).
`<session>` children are `<type>`, zero or more `<position id="N">`, and an
optional `<fastest-lap limit="L">`.

```xml
<config>
  <session>
    <type>Feature Race</type>
    <position id="1">30</position>
    <position id="2">27</position>
    <fastest-lap limit="15">2</fastest-lap>
  </session>
  <session>
    <type>Sprint Race</type>
    <position id="1">10</position>
  </session>
</config>
```

**Rationale**: A minimal flat schema is the simplest structure that satisfies
all spec requirements. Using a `<config>` root element avoids the XML
well-formedness requirement for exactly one root element when multiple sessions
are present (naked `<session>` blocks are not well-formed XML). Text content
for points values keeps the schema human-readable and easy to construct by hand.
Attributes for numeric identifiers (`id`, `limit`) follow standard XML practice.

**Alternatives considered**:
- `<sessions>` root with `<session>` children — functionally identical; rejected
  in favour of `<config>` which matches the domain language already used by the
  codebase (`config_name`, `PointsConfigStore`, etc.).
- Nested `<positions>` and `<fl>` wrapper elements — rejected as unnecessary
  nesting that adds verbosity without semantic value for this shallow structure.

---

## Decision 3 — Session type string matching

**Decision**: Match the `<type>` element's text content case-insensitively
against the four canonical labels: "Sprint Qualifying", "Sprint Race",
"Feature Qualifying", "Feature Race". Resolved via `SessionType.label()`
comparison after `.strip().lower()`.

**Rationale**: The spec requires an error on unrecognised types. Case-insensitive
matching reduces friction (users may type "feature race" or "FEATURE RACE").
Existing `SessionType.label()` already produces the canonical label strings;
a reverse lookup dict built at module load time avoids per-parse string iteration.

**Alternatives considered**:
- Accept `SessionType.value` strings (e.g., "FEATURE_RACE") — rejected; these
  are internal identifiers not exposed to users in existing UI. Human-readable
  labels were chosen per the XML schema example in the spec.
- Accept both label and value — rejected; dual-format acceptance increases the
  test surface and the canonical label form is unambiguous.

---

## Decision 4 — Validation strategy: staging in memory vs. staging in DB

**Decision**: Parse and validate entirely in memory before any database write.
Construct a `XmlImportPayload` dict (`session_type → {position → points}` plus
`session_type → (fl_points, fl_limit | None)`), run all structural and
monotonic checks against it, and only open a DB transaction when the payload is
fully validated.

**Rationale**: The spec describes a "backup → stage → validate → persist or
revert" workflow. Since all validation can be performed on the in-memory
payload, no DB staging table is needed. The "backup" step is only required as
a rollback mechanism; if validation passes before the first write, rollback
is never needed — the atomic transaction itself provides the safety net.
This is simpler than writing a staging row set and avoids any risk of leaving
partial state in intermediary tables.

**Alternatives considered**:
- DB-level staging tables — rejected; adds schema migration and complexity for
  no benefit when all validation can happen on in-memory data.
- Read existing rows → apply delta in Python → validate merged result — this is
  correct for the partial-update requirement (absent positions must retain their
  existing values). However, monotonic validation only needs to run on the
  positions present in the XML payload (we are not re-validating the entire
  existing config). The spec's monotonic rule applies per-session-block within
  the import, not against the entire config. This is confirmed by the spec
  text: "within each individual session block the points are monotonically
  non-increasing as position number increases."

---

## Decision 5 — Monotonic ordering check

**Decision**: Validate within each session block in the XML payload only. Sort
positions ascending by `id`; verify each successive points value is ≤ the
previous (non-zero rule: if the lower-position entry has 0 points, the
higher-position entry may also be 0 without violating monotonicity).

**Rationale**: The spec cites `validate_monotonic_ordering` conceptually, but
that function operates against `season_points_entries`. For the server config
store (no season attachment), a local check against the parsed payload is
correct and doesn't require creating a season snapshot. The "non-zero"
exception matches the existing `validate_monotonic_ordering` logic in
`season_points_service.py`.

**Alternatives considered**:
- Re-use `validate_monotonic_ordering` by writing to DB first then calling it —
  rejected; requires a DB write before validation is complete, coupling the
  staging phase to the validation phase unnecessarily.

---

## Decision 6 — File attachment handling

**Decision**: When an optional `file` (`discord.Attachment`) parameter is
provided on the command, fetch the attachment content using
`attachment.read()` (async, returns `bytes`). Decode as UTF-8. Check size
≤ 100 KB before parsing. If `file` is not provided, open a Discord modal with
a single multi-line text field.

**Rationale**: `discord.Attachment.read()` is the idiomatic discord.py method
for fetching attachment content asynchronously. The 100 KB limit is generous
for any realistic points config XML (a 4-session config with 20 positions
each is well under 2 KB even with formatting) and prevents abuse.

**Alternatives considered**:
- Read `attachment.url` via `aiohttp` manually — rejected; `attachment.read()`
  is the official discord.py helper and avoids introducing a raw HTTP client.

---

## Decision 7 — Audit log message format

**Decision**: Follow the existing `output_router.post_log` pattern used by all
other commands in `results_cog.py`. Post a single log string on success listing
the user, command name, and a summary line per session type updated.

**Rationale**: Consistency with every other command in `ResultsCog` (lines 161,
240, 292, etc. in `results_cog.py`). No new logging infrastructure required.
