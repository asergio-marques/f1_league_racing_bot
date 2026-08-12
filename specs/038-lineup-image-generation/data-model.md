# Phase 1 Data Model: Lineup Image Generation

**No database change.** No table is created, no column is added, no migration is written. Everything
this feature draws is already stored, and `divisions.lineup_message_id` — added at v2.8.0 for the
textual lineup — already carries the one piece of mutable state the image path needs.

What follows is therefore two things: the **in-memory** shapes the render passes around, and the
**existing** records they are resolved from, with the validation rules this feature newly imposes on
one of them.

---

## 1. New in-memory shapes

### Catalogue declaration types — `src/models/image_catalogues.py`

Three frozen dataclasses beside the existing `RowSpec`, which is unchanged.

**`NestedSpec`** — a collection inside a member of another, discriminated by an ordinal.

| Field | Type | Meaning |
|---|---|---|
| `prefix` | `str` | `"driver"` — the segment between the parent's key and the field |
| `capacity` | `int \| None` | `None` where the count comes from the data (team seats); an integer where fixed |
| `fields` | `frozenset[str]` | `{"name", "flag", "image"}` |
| `mandatory_fields` | `frozenset[str]` | `{"name"}` |
| `first_member_mandatory_only` | `bool` | `True` for the reserve block: member 1 mandatory, beyond it optional (XIV.3) |
| `assets` | `dict[str, str]` | `{"flag": "flag", "image": "driver"}` |

**`KeyedSpec`** — a collection whose members are discriminated by a normalised key (XIV.11).

| Field | Type | Meaning |
|---|---|---|
| `prefix` | `str` | `"team"` |
| `fields` | `frozenset[str]` | `{"name", "image", "group"}` |
| `mandatory_fields` | `frozenset[str]` | `{"name"}` |
| `assets` | `dict[str, str]` | `{"image": "team"}` |
| `nested` | `NestedSpec \| None` | the seats |
| `capacity_from_data` | `bool` | `True` — the division fixes the member set (XIV.12) |

**`SingletonSpec`** — one member, named, bearing no discriminator.

| Field | Type | Meaning |
|---|---|---|
| `name` | `str` | `"reserve"` — reserved against every keyed sibling |
| `fields` | `frozenset[str]` | `{"name", "image", "group"}` |
| `mandatory_fields` | `frozenset[str]` | `{"group"}` — a **mandatory group** (XIV.2, v4.3.0) |
| `assets` | `dict[str, str]` | `{"image": "team"}` |
| `nested` | `NestedSpec \| None` | the reserve seats, capacity counted from the template |

`FieldCatalogue` gains `keyed: KeyedSpec | None = None` and `singleton: SingletonSpec | None = None`.
`is_empty` is extended to consider both, so a type declaring only a keyed collection is not mistaken
for an unspecified one.

### `LineupBinding` — `src/models/image_catalogues.py`

The division's shape, which is what makes the lineup catalogue answerable.

| Field | Type | Meaning |
|---|---|---|
| `team_keys` | `tuple[str, ...]` | normalised team names, excluding the reserve team |
| `seats` | `Mapping[str, int]` | key → configured seat count |

**Invariants.**

- `team_keys` holds no duplicate. A duplicate means two teams of one division normalise alike, which
  `resolve_drawing` raises as fatal before a binding is ever built.
- No key equals `"reserve"`. Enforced upstream by team-name validation (§3) and asserted here.
- Every key of `seats` appears in `team_keys`.

**Absence is meaningful.** `binding=None` is not an empty binding: it means *no division is in view*,
and the catalogue answers with its team-independent ids only. An empty `LineupBinding` means a
division that fields no team at all, which is a different thing and is fatal.

### `LineupDrawing` and friends — `src/services/image_lineup_service.py`

Modelled on `CalendarDrawing` / `CalendarRound`: every value decided, nothing drawn.

**`LineupSeat`** — `seat_number: int`, `driver_name: str` (empty where unoccupied),
`flag_datum: str | None`, `portrait_datum: str | None`, `occupied: bool`.

**`LineupTeam`** — `key: str`, `display_name: str`, `image_datum: str`,
`seats: list[LineupSeat]`, `is_reserve: bool`.

**`LineupDrawing`** — `division_name: str`, `division_tier: str | None`,
`season_number: str | None`, `teams: list[LineupTeam]`, `reserve: LineupTeam | None`,
`nationality_collected: bool`.

`reserve` is `None` where the division fields no reserve driver, which is what drives
`reserve_group`'s removal (FR-004). `nationality_collected` carries R11's suppression decision onto
the drawing so the fill pipeline keeps one rule.

---

## 2. Existing records read

| Record | Read for | Modified? |
|---|---|---|
| `default_teams` | name, `max_seats`, `is_reserve` — the server team configuration `/images test lineup` draws | **Names newly validated** (§3). A reserve row is created where absent (FR-014). |
| `team_instances` | per-division team name, seat count, reserve flag | Names validated at `/season review` (FR-013) |
| `team_seats` | `seat_number`, `driver_profile_id` — the seat order the graphic draws in | No |
| `driver_profiles` | `discord_user_id`, `is_test_driver`, `test_display_name` | No |
| `signup_records` | `server_display_name`, `discord_username`, `nationality` | No |
| `signup_config` | `nationality_required` — R11's suppression switch | No |
| `divisions` | `name`, `tier`, `lineup_channel_id`, `lineup_message_id` | `lineup_message_id` written on posting, as today |
| `seasons` | `season_number` | No |
| `image_config` | template filename, team/flag/driver directories | No |
| `image_aspect_toggle` | the `lineup` row | No |
| `render_notices` | written for substitutions, truncations, fallbacks, emptied optionals | Appended, as today |

Seat order deserves a note: `team_seats.seat_number` is already the stable order the graphic needs.
`placement_service` allocates the lowest free seat and creates a reserve seat on demand, so a reserve
seat vacated by an unassignment is reused rather than appended — which is exactly why FR-007 draws by
seat number and not by joining order. Nothing has to change for that to hold.

---

## 3. New validation rules on an existing record

Applied to a **team name** at `/team add`, at `/team rename` (new name only), and to every existing
team at `/season review`. Not gated on the image module (FR-012).

| # | Rule | Message names |
|---|---|---|
| 1 | Non-empty once trimmed, and non-empty once normalised | the offending input |
| 2 | Normalised form begins with a letter | the input and its normalised form |
| 3 | Normalised form is unique within scope — server for the team list, division for a season's teams | the team it collides with |
| 4 | Normalised form is not `reserve` | the reserved word |

**Scope note.** Rules 1, 2 and 4 are properties of the name alone. Rule 3 needs the sibling set, which
differs by caller: `add_default_team` compares against the server list, `season_team_add` against the
division's teams.

**Not validated**, deliberately: the *current* name taken by `/team rename`, and the name taken by
`/team remove`. Both identify a team that already exists, and validating them would strand a team
named before this rule — unrenameable and unremovable (FR-011).

**Already-approved seasons are not re-validated** (FR-013), and no team is renamed or removed by the
rule's introduction. The ten shipped default names all pass, so no existing server is affected.

---

## 4. State transitions

None. The lineup graphic is a projection of state the placement and attendance modules own; it holds
no lifecycle of its own. The one piece of mutable state it touches — `lineup_message_id` — moves
between exactly two conditions:

```
        ┌──────────── replacement produced successfully ────────────┐
        │                                                          ▼
 (message posted, id persisted)                          (new message posted,
        │                                                  old deleted, id rewritten)
        │
        └── replacement could not be produced ──▶ (unchanged; old message survives)
```

The third arrow is FR-025 and applies to the **image path only**. On the textual path the old
message is deleted first, as it is today, and that ordering is deliberately preserved (FR-025a).
