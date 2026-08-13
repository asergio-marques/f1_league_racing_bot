# Phase 1 Data Model: Attendance Image Generation

**Feature**: 041-attendance-image-generation | **Date**: 2026-08-13

**No table is created, altered or dropped, and no dataclass gains a field.** This document records
what is *read*, and the two facts that make the absence of a migration correct rather than an
oversight.

---

## Why there is no migration

Every image type before this one asked whether its posting needed a message id of its own. Both of
these already have one, and the two differ in a way that matters:

| Graphic | Message id | What this feature does to it |
|---|---|---|
| Attendance sheet | `attendance_division_config.attendance_message_id` | Writes it, as the textual flow already does. The image branch deletes the prior message and persists the replacement's id in the same column (XIV.8) |
| Check-in call | `rsvp_embed_messages.message_id` | **Nothing.** The call is never deleted and reposted while it stands; the embed is edited in place and the attachment rides through (XIV.17) |

The second row is the point of declaring the check-in type static. A graphic that had to be replaced
would need the message replaced, which would break the buttons armed against it — `bot.add_view(...,
message_id=...)` at `src/bot.py:430` re-arms each view against a stored id at startup, so reposting
would orphan every live call.

---

## Read by the attendance sheet

### DriverRoundAttendance (`src/models/attendance.py`)

The row of record for one driver in one round of one division. Two of its columns are the whole of the
sheet's numeric content:

| Field | Drawn as | Notes |
|---|---|---|
| `points_awarded` | The round cell `row_<x>_round_<z>_points` | **Net of pardons**, set at finalisation. A round every penalty of which was pardoned already reads 0 here, so FR-014's pardon case needs no separate branch — the sheet carries no trace of a pardon because the persisted figure carries none |
| `total_points_after` | The row total `row_<x>_points` | Cumulative **within the division**, which is FR-012 already satisfied by the column's own semantics |
| `driver_profile_id` | Resolves the name and nationality | Through the wip-spec's "name of a person" convention |
| `assigned_team_id` | Not drawn | The team of a row is the driver's seat **at generation** (FR-020), not the team they drove for in a round |

**An absent row and a zero row are drawn identically**, which is FR-015. A driver who holds no record
for a round and a driver whose round conferred nothing both produce an empty cell, and neither is a
value that could not be determined. The author's ruling of 2026-08-13 states the meaning: an empty cell
equals 0.

**State transition that matters.** `points_awarded` is `None` until the round's attendance is finalised.
The sheet treats `None` and `0` alike — both empty — so a round drawn before finalisation needs no
special case beyond keeping its column and heading (FR-014).

### AttendanceConfig

| Field | Drawn as |
|---|---|
| `autoreserve_threshold` | `autoreserve_limit`, its group removed entirely when null/0 (FR-018) |
| `autosack_threshold` | `autosack_limit`, likewise |

Both are server-level. A disabled functionality is a **configured absence** (XIV.4) and raises no
notice.

### AttendanceDivisionConfig

Supplies `attendance_channel_id` — and its absence is FR-046: no channel, no posting, therefore no
generation.

### Round / Track / Division / Team

Read as every image type before this one reads them: round number, track (for the grid image and the
mystery case), division name and tier, and the team seating each driver at the moment of generation.

---

## Read by the check-in graphic

| Source | Field | Drawn as |
|---|---|---|
| `Round` | `round_number`, `scheduled_at`, `round_format` | `round_number`, `round_date`, `round_time`, `round_format` |
| `Round` | track reference | `track_name` — the value the embed shows as its Location |
| `Track` | grand prix name, country | `race_name` (mandatory), `country_name` (optional) |
| `AttendanceConfig` | `rsvp_deadline_hours` | `deadline_date` / `deadline_time`, via `derive_checkin_deadline` (R7) |
| `Round.round_format` | — | The session list: four names for a sprint round, two for any other (FR-024) |

**Nothing per-driver is read at all.** No `DriverRoundAttendance`, no roster, no RSVP status, no team.
That absence is the static declaration's substance (FR-009, XIV.17): the fields the buttons mutate are
not merely undrawn, they are unreachable from this utility.

---

## Derived values

One, and it is the only arithmetic in the feature:

```
derive_checkin_deadline(scheduled_at, deadline_hours) -> datetime
    = scheduled_at - timedelta(hours=deadline_hours)
```

Lives in `attendance_service` (R7, XIV.7). `deadline_hours = 0` yields the round's own start time,
which is FR-026's stated case and needs no branch.

The deadline drawn is the one enforced on **full-time** drivers. The later deadline a reserve is held
to is carried by neither the graphic nor the embed (FR-028), and this function is not the place it
would be added.

---

## Ordering and composition

Both are the textual sheet's and are not re-derived (FR-010, FR-011):

- **Composition** — every driver holding a finalised record for the round: every non-reserve driver of
  the division, every reserve distributed into a seat for that round, and every driver sanctioned upon
  this posting. A driver sacked at an earlier round holds no seat and is absent.
- **Order** — `total_points_after` descending, ties broken alphabetically on the **resolved** name, so
  the tie-break reads the same string the graphic draws.

The row ordinal is a place in the layout and is never drawn (FR-007, XIV.11). Two drivers level on
totals stand level, and the sheet carries no position field to disagree with them.

---

## Entity summary

| Entity | Change |
|---|---|
| `DriverRoundAttendance` | Read only |
| `AttendanceConfig` | Read only |
| `AttendanceDivisionConfig` | Read; `attendance_message_id` written through the existing path |
| `RsvpEmbedMessage` | Read only — deliberately untouched |
| `AttendancePardon` | **Not read.** Pardons are already applied to `points_awarded`, and the sheet carries no trace of one |
| `Round`, `Track`, `Division`, `Team`, `DriverProfile` | Read only |
