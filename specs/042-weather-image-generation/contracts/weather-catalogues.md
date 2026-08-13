# Contract: The Six Weather Field Catalogues

**Feature**: `042-weather-image-generation` | Constitution XIV.3, XIV.10, XIV.11

Six entries in `CATALOGUES`, one per template slot. Each is separately addressable and names its own
fields in full; the four phase-bearing ones share the declaration of the heading fields they hold in
common, as XIV.10 permits.

---

## The heading fields

Held by `weather_p1_template`, `weather_p2_template`, `weather_p2_sprint_template`,
`weather_p3_template` and `weather_p3_sprint_template`.

| Field | Class | Asset class | Note |
|---|---|---|---|
| `season_number` | optional | — | |
| `division_name` | **mandatory** | — | |
| `division_tier` | optional | — | Emptied where the division carries no tier (FR-031) |
| `phase_description` | **mandatory** | — | Fixed text per phase (FR-022) |
| `round_number` | **mandatory** | — | |
| `track_name` | **mandatory** | — | |
| `race_name` | optional | — | |
| `country_name` | optional | — | |
| `track_image` | optional | `track` | |
| `rain_probability` | **phase 1: mandatory** · phases 2–3: optional | — | FR-004 |

Each optional field may carry a `_group`, declared optional, so a template composing a fixed label or a
separator around it loses the chrome with the value (XIV.2). `race_name_group` is declared though its
field is mandatory on no weather type — the group is what a template reaches to drop a label.

## `weather_p1_template`

The heading fields and **nothing else** (FR-005). `rain_probability` is mandatory here and only here.

## `weather_mystery_template`

Four fields and nothing else (FR-006):

| Field | Class |
|---|---|
| `season_number` | optional |
| `division_name` | **mandatory** |
| `division_tier` | optional |
| `round_number` | **mandatory** |

No phase description, no track name, no grand prix name, no country, no track image, no rain likelihood,
no session, no slot. A mystery round conceals its track and runs no session; the notice says a forecast is
not coming, and has nothing else to say.

> This is the first image type in the module that exists for a **kind of record** rather than for an
> output aspect — the form Constitution XIV.3 admitted at v4.7.0. It is not an exemption: it draws every
> field of its own catalogue in full.

## `weather_p2_template` and `weather_p2_sprint_template`

The heading fields, plus one ordinal collection.

```
rows = RowSpec(
    prefix="session",
    capacity=None,          # counted from the template
    minimum=2,              # sprint variant: 4       (see declaration-floor.md)
    fields={"group", "name", "slot_type", "slot_type_icon"},
    mandatory_fields={"group", "name", "slot_type"},
    valueless_fields={"group"},
    assets={"slot_type_icon": "weather"},
)
```

| Per-session field | Class | Note |
|---|---|---|
| `session_<x>_group` | **mandatory** | Removed entire when the round holds no session of that ordinal (FR-036) |
| `session_<x>_name` | **mandatory** | |
| `session_<x>_slot_type` | **mandatory** | The type of weather drawn for the session |
| `session_<x>_slot_type_icon` | optional | Weather icon class |

## `weather_p3_template` and `weather_p3_sprint_template`

The same collection with `slot_type` reclassified **optional**, one field added, and a nest.

```
rows = RowSpec(
    prefix="session",
    capacity=None,
    minimum=2,              # sprint variant: 4
    fields={"group", "name", "slot_type", "slot_type_icon", "summary"},
    mandatory_fields={"group", "name"},
    valueless_fields={"group"},
    assets={"slot_type_icon": "weather"},
    nested=NestedSpec(
        prefix="slot",
        capacity=None,
        minimum=4,          # sprint variant: 3
        fields={"group", "label", "icon"},
        mandatory_fields={"group", "label"},
        assets={"icon": "weather"},
    ),
)
```

| Per-slot field | Class | Note |
|---|---|---|
| `session_<x>_slot_<y>_group` | **mandatory** | Removed entire when the session holds no slot of that ordinal (FR-038) |
| `session_<x>_slot_<y>_label` | **mandatory** | The concrete weather |
| `session_<x>_slot_<y>_icon` | optional | Weather icon class |

Removing `session_<x>_group` takes the slots of that session with it (FR-040), which containment already
gives: the slot ids are built on the session's stem.

---

## The two meanings of `slot`, and why they do not collide

A phase 3 template carries both `session_1_slot_type` and `session_1_slot_1_label`. They are told apart by
the catalogue and never by parsing (FR-009, XIV.11 as amended at v4.7.0). This is not an assertion of
intent — it holds mechanically:

| Id | `RowSpec` pattern `^session_(\d+)(?:_.*)?$` | `NestedSpec` pattern `^session_1_slot_(\d+)(?:_.*)?$` | Canonical form |
|---|---|---|---|
| `session_1_slot_type` | matches → session 1 ✅ | **no match** (`type` is not `\d+`) | `session_#_slot_type` |
| `session_1_slot_1_label` | matches → session 1 ✅ | matches → slot 1 ✅ | `session_#_slot_#_label` |

So the session-level field cannot inflate the slot count, and the two canonical forms are distinct for the
sibling and unknown-field checks. A session holds one slot at phase 2 and one to four at phase 3, which is
the author's framing and is what the declaration reflects.

---

## Sibling relation

All six are siblings of one another through the **aspect** relation alone: `ASPECT_TEMPLATES["weather"]`
holds all six keys. A template declaring a field of any other weather catalogue is refused at the moment
it is named (FR-002) — including the named instance of a slot field appearing on a phase 2 template, which
is caught because `session_#_slot_#_label` belongs to a phase 3 catalogue and to no phase 2 one.

No sibling code changes. See research R4.

---

## What no weather catalogue declares

Stated because their absence is a requirement (FR-011), not an oversight:

- no Discord mention — the role mention stays in the message text (XIV.16);
- no phase number — `phase_description` carries the phase in words;
- no date and no time of the round — which is why XIV.15's time-zone rule is not reached by any weather
  type;
- no driver name and no team name;
- no intermediate value of any phase's calculation — those stay in the log channel (FR-033);
- no session ordinal drawn as a datum: the ordinal addresses fields and is never itself drawn (FR-010).
