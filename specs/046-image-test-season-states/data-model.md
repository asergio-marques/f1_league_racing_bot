# Phase 1 data model: image previews across every season state

**No database entity is added, amended or removed, and there is no migration.** Every structure below lives in memory for the duration of one invocation. This is recorded explicitly so that it is not re-derived as a schema change.

---

## Existing entities, read as they stand

| Entity | Table | Read for |
|---|---|---|
| Season | `seasons` | Status and `season_number`. The lookup widens from ACTIVE to ACTIVE-or-SETUP (FR-001), and `MAX(season_number)` over non-SETUP rows gives the fabricated league its number (FR-010). |
| Division | `divisions` | Name, tier. Resolved within the season selected. |
| Round | `rounds` | Number, format, track, schedule. |
| Team instance | `team_instances` | Name, seat count, reserve flag. |
| Team seat | `team_seats` | Seat number and its occupant. |
| Driver profile | `driver_profiles` | `test_display_name` and `is_test_driver` for a mock driver. |
| Signup record | `signup_records` | Display name, username, nationality. A mock driver has no row here. |
| Default team | `default_teams` | **New reader.** The server-level team list, which is the fabricated league's one non-invented part (FR-011). |
| Track | `tracks` | **New reader.** Name and country, sampled for the fabricated calendar (FR-018). |

`server_configs.previous_season_number` is **not** read. It is written by nothing and is 0 on every server; see research R3.

---

## `PreviewContext` — amended

The context 045 introduced, extended so that it is complete without a database row behind it. The added fields are what let one fabricated context serve all eleven builders unchanged.

| Field | Status | Meaning |
|---|---|---|
| `server_id`, `season_number` | existing | — |
| `division_id`, `division_name`, `division_tier` | existing | For a fabricated league, `division_id` is a sentinel matching no row. |
| `round` | existing | The round drawn, or `None` for the two kinds that take none. |
| `teams`, `drivers`, `display_names` | existing | — |
| `nationality_collected` | existing | Read from `signup_module_settings`. |
| `asset_directories`, `directory_faults` | existing | The league's own, on both paths (FR-023). |
| `fabricated_drivers` | existing | Drivers invented into a real division's empty seats (045). |
| **`rounds`** | **new** | The division's calendar, resolved once. Replaces three re-queries by `division_id` (research R4) and is the only way a fabricated calendar reaches the builders. |
| **`fabricated_league`** | **new** | True where the whole league is invented. Drives the reply banner (FR-024). Distinct from `fabricated_drivers`, which marks a *real* division with invented occupants. |
| **`season_pending_approval`** | **new** | True where the season drawn is SETUP. Drives the reply note (FR-004). |
| **`drivers_without_nationality`** | **new** | Count of seated drivers drawn with no flag where the league collects nationality (FR-028). |

**Invariant**: `fabricated_league` and `season_pending_approval` are never both true — a fabricated league exists precisely because no season does.

**Invariant**: where `fabricated_league` is true, `fabricated_drivers` is also true. Every seat of a fabricated team is filled by FR-019.

---

## `FabricatedLeague` — new, transient

Built by `image_preview_league.build_fabricated_context`, which returns a fully-formed `PreviewContext`. It is a factory output rather than a persisted shape; the fields below are what it randomises.

| Field | Source | Rule |
|---|---|---|
| `season_number` | `MAX(season_number)` over non-SETUP seasons, plus 1 | FR-010 — derived, not randomised |
| `teams` | `default_teams`, reserve excluded, with their configured `max_seats` | FR-011 — read, not randomised |
| `division_name` | randomised | FR-013 |
| `division_tier` | randomised within the range a real division admits | FR-013, A-012 |
| `rounds` | randomised length, each with a randomised format and a track sampled without replacement from `tracks` | FR-013, FR-015, FR-018 |
| `round` | one of `rounds`, chosen to satisfy the kind's format demand | FR-016, FR-017 |
| `drivers` | one per seat, randomised names, nationality where collected | FR-019, FR-020 |

**Determinism seam**: the factory takes `rng: random.Random | None` and `now: datetime | None`, both defaulting to live values. Production passes neither and gets fresh randomness per invocation (FR-014); tests pass a seeded `Random` and a pinned `now` and assert on exact output. Pinning `now` alongside the seed is obligatory — the fabricated calendar is dated relative to it.

---

## `PreviewKind` — new classification constant

One entry per command, replacing the ad-hoc `require_*` flags 045 passed at each call site. It is the single source for the refusal split (FR-012), the format demand (FR-017), and which parameters a command requires (FR-008).

| Kind | `needs_round` | `draws_roster` | `format_demanded` |
|---|---|---|---|
| `calendar` | ✗ | ✗ | — |
| `lineup` | ✗ | ✓ | — |
| `results` | ✓ | ✓ | any |
| `standings` | ✓ | ✓ | any |
| `attendance` | ✓ | ✓ | any |
| `verdict` | ✓ | ✓ | any |
| `rsvp` | ✓ | ✗ | any |
| `weather-p1` | ✓ | ✗ | not mystery |
| `weather-p2` | ✓ | ✗ | not mystery |
| `weather-p3` | ✓ | ✗ | not mystery |
| `weather-mystery` | ✓ | ✗ | mystery |

`draws_roster` is verified against each builder, not against the spec prose — see research R5. The two entries that surprise are `rsvp` (draws no roster, so it draws on a team-less server) and `verdict` (draws one, so it does not).

---

## Refusal reasons — amended

045's reason constants stand unchanged. Two are added:

| Reason | Raised when | Requirement |
|---|---|---|
| `REASON_NO_SEASON` | **withdrawn as a refusal** | Was FR-001's blocker; a season-less server now fabricates. The constant is removed rather than left unraised. |
| `REASON_NO_DIVISION`, `REASON_NO_ROUND`, `REASON_NO_ROUNDS`, `REASON_NO_TEAMS`, `REASON_MYSTERY_ROUND`, `REASON_NOT_MYSTERY_ROUND` | unchanged | FR-006 |
| **`REASON_MISSING_INPUT`** | a season exists and a required parameter was omitted | FR-008 |
| **`REASON_NO_SERVER_TEAMS`** | no season, no configured team, and the kind draws a roster | FR-012 |

`REASON_NO_SERVER_TEAMS` is deliberately distinct from `REASON_NO_TEAMS`: one says the server has configured no teams at all, the other that a division holds none. FR-012 requires a manager to be able to tell them apart.

---

## State transitions

None. No entity changes state, and nothing is written. The feature is read-and-invent throughout, which is what makes FR-025 testable as a straightforward before-and-after comparison of every table.
