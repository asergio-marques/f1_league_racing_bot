# Quickstart: Points Configuration XML Import

Manual walkthrough to verify the feature end-to-end after implementation.

## Prerequisites

- Bot running locally with a working `bot.db`
- `/bot-init` already run; Results & Standings module enabled
- At least one named config exists (e.g., `100%`) — create with `/results config add name:100%` if needed
- One Discord user available with the configured season/config (trusted-admin) role
- `lxml` installed: `pip install lxml`

---

## 1. Modal path — valid full import

Run:
```
/results config xml-import  name:100%
```

In the modal that appears, paste:
```xml
<config>
  <session>
    <type>Feature Race</type>
    <position id="1">25</position>
    <position id="2">18</position>
    <position id="3">15</position>
    <fastest-lap limit="10">1</fastest-lap>
  </session>
  <session>
    <type>Sprint Race</type>
    <position id="1">8</position>
    <position id="2">7</position>
    <fastest-lap limit="8">1</fastest-lap>
  </session>
</config>
```

Submit the modal.

**Expected**: Ephemeral success reply listing:
- Feature Race: P1→25, P2→18, P3→15; FL: 1pt (limit P10)
- Sprint Race: P1→8, P2→7; FL: 1pt (limit P8)

Run `/results config view name:100%` — values must match.

Sprint Qualifying and Feature Qualifying entries must be **unchanged** (still 0 if
the config was freshly created).

---

## 2. Modal path — partial import (positions only, no FL node)

Run:
```
/results config xml-import  name:100%
```

Paste:
```xml
<config>
  <session>
    <type>Feature Race</type>
    <position id="4">12</position>
  </session>
</config>
```

**Expected**: Success reply noting P4→12 updated for Feature Race.
Run `/results config view` — P1/P2/P3/FL values from step 1 must be unchanged.

---

## 3. File attachment path — valid import

Create a local file `import.xml` with the content from step 1.

Run:
```
/results config xml-import  name:100%  file:import.xml
```
(attach the file using Discord's attachment picker)

**Expected**: No modal opens. Config is updated as in step 1. Success reply returned.

---

## 4. Validation failure — non-monotonic

Run:
```
/results config xml-import  name:100%
```

Paste:
```xml
<config>
  <session>
    <type>Feature Race</type>
    <position id="1">10</position>
    <position id="2">20</position>
  </session>
</config>
```

**Expected**: Ephemeral error identifying Feature Race P1(10) < P2(20) as a monotonic
violation. Config **unchanged** — run `/results config view` to confirm P1 is still 25.

---

## 5. Validation failure — unrecognised session type

Run:
```
/results config xml-import  name:100%
```

Paste:
```xml
<config>
  <session>
    <type>Endurance Race</type>
    <position id="1">50</position>
  </session>
</config>
```

**Expected**: Ephemeral error naming "Endurance Race" as an unrecognised session type.
Config unchanged.

---

## 6. Validation failure — malformed XML

Run the command and paste:
```
not xml at all <<<
```

**Expected**: Ephemeral parse error. Config unchanged.

---

## 7. File too large

Attach a file > 100 KB (e.g., a binary file renamed to `.xml`) to the command.

**Expected**: Ephemeral error "File exceeds 100 KB limit." No modal. Config unchanged.

---

## 8. Config not found

Run:
```
/results config xml-import  name:nonexistent
```

Paste any valid XML.

**Expected**: Ephemeral error "Config 'nonexistent' not found." Config unchanged.

---

## 9. Audit log verification

After step 1, check the configured log channel.

**Expected**: A log entry containing the user's display name, `/results config xml-import`,
the config name, and a summary of sessions updated.
