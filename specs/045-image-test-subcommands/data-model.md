# Phase 1 data model: image test commands

**No entity is added and none is amended.** The preview reads the league's existing records and fabricates the rest in memory; nothing it produces is written. This document therefore describes the *resolution* the preview performs and the in-memory shapes it builds, not a schema change.

## Entities read as they stand

| Entity | Source | Read for |
|---|---|---|
| `Season` | `season_service.get_active_season(server_id)` | Scoping the division name (A-001); the season number every graphic draws |
| `Division` | `season_service.get_divisions(season_id)` | Name, tier; the subject of every preview |
| `Round` | `season_service.get_division_rounds(division_id)` | Number, format, track, `scheduled_at`; the format drives sessions and slots |
| Team instances | `team_service.get_division_teams(division_id)` | Team names, seat counts, `is_reserve`, seated driver profiles |
| `DriverProfile` | seat rows joined to `driver_profiles` | Seated drivers' names and nationalities |
| Signup settings | `signup_module_service` | `nationality_required` — whether a flag is drawn at all |
| `ImageConfig` | `image_config_service.get_config(server_id)` | The eight asset directories, the presentation preferences, the time zone |
| `SESSIONS_BY_FORMAT`, `MAX_SLOTS` | `models/session.py` | The session list and slot ceiling of a format |

## Resolution result

The eleven commands share one resolution step, which either refuses or yields a context every preview then draws from.

```
PreviewContext
  season_number: int
  division_name: str
  division_tier: int
  division_id: int
  round: Round | None          # None for calendar and lineup
  teams: list[TeamInstance]    # empty only where the kind does not need teams
  drivers: list[PreviewDriver]
  nationality_collected: bool
  asset_directories: dict[str, Path]
  directory_faults: list[DirectoryFault]
```

### `PreviewDriver`

One driver as the preview will draw them. The `fabricated` flag exists so the reply can say the picture is not showing a real roster.

```
PreviewDriver
  key: int                     # profile id, or a synthetic id for a fabricated driver
  display_name: str
  nationality: str | None      # None where the league does not collect it
  team_name: str
  seat_number: int
  fabricated: bool
```

**Population rule (FR-018, FR-020).** Where the division holds at least one seated driver, seats are drawn as they stand and an unoccupied seat stays unoccupied. Where it holds none, every seat is filled with a fabricated driver. The rule is all-or-nothing per division, never per seat.

### `DirectoryFault` (FR-038, research R3)

The failure the posting path discards. Captured so the reply can distinguish a rejected path from a missing file.

```
DirectoryFault
  asset_class: str
  configured_value: str
  reason: str                  # why resolve_within_project_root rejected it
```

## Fabricated shapes

Each is built to the same type the posting path passes to `resolve_drawing`, so no `resolve_drawing` signature changes.

| Preview | Fabricated | Passed to |
|---|---|---|
| calendar | *nothing* | `image_calendar_service.resolve_drawing` |
| lineup | drivers only, and only where none is seated | `image_lineup_service.resolve_drawing` |
| results | a classification per session of the round's format | `image_results_service.resolve_drawing` (once per session) |
| standings | results for every round up to and including the named one | `image_standings_service.resolve_drawing` (drivers, then constructors) |
| attendance | an attendance record per driver per round up to the named one | `image_attendance_service.resolve_drawing` |
| rsvp | *nothing* | `image_rsvp_service.resolve_drawing` |
| weather-p1 | a likelihood of rain, not a whole percentage | `image_weather_service.resolve_drawing` |
| weather-p2 | a session weather type per session | as above |
| weather-p3 | a slot sequence per session | as above |
| weather-mystery | *nothing* | as above |
| verdict | a sanction, a session, a driver and free text, once per case | `image_verdict_service.resolve_drawing` (once per case) |

### Constraints on fabricated classifications (FR-024)

- Every drawn driver appears exactly once.
- Positions are `1..n` with no gap and no repeat.
- The leader carries a total time; every other finisher carries an interval to the leader that increases with position.
- A driver who did not finish, did not start, or was disqualified is renumbered to the bottom, as the results module renumbers them.
- Points follow the division's own points configuration where one is set.

### Constraints on fabricated forecasts (FR-029 to FR-031)

- Phase 1: a likelihood in `[0, 100]`, deliberately fractional.
- Phase 2: one type per session; all three of sunny, mixed and rainy across a sprint round's four sessions, two across a two-session round.
- Phase 3: a slot sequence per session, within that session type's `MAX_SLOTS`, with all five of clear, light cloud, overcast, wet and very wet appearing across the round. Reachable for every non-mystery format (research R6).

### Constraints on fabricated verdicts (FR-032 to FR-034)

- The sanction is one of `+5s`, `+10s`, `-3s`, `DSQ` — the vocabulary `verdict_announcement_service.describe_penalty` can render, and no other.
- The driver is one of the division's own.
- The session is one the named round is run over.
- Free text is drawn at five lengths, the longest exceeding the field by an order of magnitude so the floor, the cut and the notice can be judged.

## State transitions

**None.** No preview writes to, alters, or deletes any record, and none posts to a channel (FR-005, SC-006). The one write the render already performs — persisting notices for the log channel — is the existing behaviour of `ImageRenderService.render` and is unchanged.
