# Contract: The lineup field catalogue

The catalogue is this image type's public surface. A league manager authors an SVG against it; the
fill pipeline and validity Layer 2 both read it. This document is the contract between those three.

Declared as `LINEUP_CATALOGUE` in `src/models/image_catalogues.py`, registered under the template key
`lineup_template`.

---

## 1. Whole-graphic fields

| Field id | Class | Operation | Notes |
|---|---|---|---|
| `division_name` | **Mandatory** | text | The name given at `/division add` |
| `season_number` | Optional | text | The server's season number |
| `division_tier` | Optional | text | The tier given at `/division add` |

## 2. The team collection — keyed, capacity fixed by the data

Members are discriminated by `<x>`, the **normalised team name**. The member set is fixed by the
division, not by the template: the template must declare exactly the division's teams, no more and no
fewer (XIV.12, data-fixed).

| Field id | Class | Operation | Asset class |
|---|---|---|---|
| `team_<x>_name` | **Mandatory** | text | — |
| `team_<x>_image` | Optional | image | `team` |
| `team_<x>_group` | Optional | removable group | — |

### The seat collection — nested, capacity fixed by the data

`<y>` runs from 1 to the seat count configured for team `<x>`. The template must declare exactly
those seats.

| Field id | Class | Operation | Asset class |
|---|---|---|---|
| `team_<x>_driver_<y>_name` | **Mandatory** | text | — |
| `team_<x>_driver_<y>_flag` | Optional | image | `flag` |
| `team_<x>_driver_<y>_image` | Optional | image | `driver` |

## 3. The reserve block — singleton, capacity fixed by the template

Bears no discriminator. Its name `reserve` is reserved: no team of a division may normalise to it.

| Field id | Class | Operation | Asset class |
|---|---|---|---|
| `reserve_group` | **Mandatory** | removable group | — |
| `reserve_name` | Optional | text | — |
| `reserve_image` | Optional | image | `team` |
| `reserve_driver_<y>_name` | **Mandatory for `<y>`=1**, optional beyond | text | — |
| `reserve_driver_<y>_flag` | Optional | image | `flag` |
| `reserve_driver_<y>_image` | Optional | image | `driver` |

`reserve_group` is the first **mandatory group** in the module (XIV.2, admitted v4.3.0): the template
must declare it, and it is removed in its entirety — taking every other `reserve_` field with it —
whenever the division fields no reserve driver. Its removal is the ordinary behaviour of a group and
raises nothing.

The reserve seat count is the **only** lineup capacity the template fixes, because a division's
reserve population varies over a season and cannot be known when the template is drawn. It is
therefore the only lineup collection to which overflow can apply.

---

## 4. The key rule

`<x>` is produced by `utils.asset_resolver.normalise` — the same function that produces an asset
filename, so one datum yields one spelling in both (Constitution XIV.13, v4.3.0):

> trim, lowercase, decompose and strip diacritics, replace each run of characters that is neither a
> letter nor a digit with a single underscore, drop leading and trailing underscores.

```
Red Bull          → team_red_bull_name          and  red_bull.svg
Force India (B)   → team_force_india_b_name     and  force_india_b.svg
Scuderia Ferrari  → team_scuderia_ferrari_name  and  scuderia_ferrari.svg
```

Because the result must serve as an XML `@id`, the datum is constrained at the command that sets it
(Principle IX): non-empty, beginning with a letter, unique in scope, and never `reserve`.

---

## 5. Enumeration contract

The catalogue answers differently depending on whether a division is in view. This is the whole point
of the binding and the reason the same object serves all three verification moments (XIV.9).

| Call | Returns |
|---|---|
| `all_mandatory_ids(root)` — no binding | `division_name`, `reserve_group`, `reserve_driver_1_name` |
| `all_mandatory_ids(root, binding)` | the above, plus `team_<x>_name` for every bound key and `team_<x>_driver_<y>_name` for every bound seat |
| `divergent_members(root, binding)` | teams and seats the template declares but the binding lacks, and vice versa — each named |
| `capacity(root)` | the reserve slot count the template declares, contiguous from 1 |

**`binding=None` is not an empty binding.** It means no division is in view, and the team-independent
answer is the correct one — not a degraded one. An empty `LineupBinding` means a division fielding no
team, which is fatal.

---

## 6. Worked example

A division with two teams — Red Bull (2 seats), Force India (B) (1 seat) — and three reserve drivers,
against a template declaring four reserve slots.

**Template must declare**: `division_name`, `team_red_bull_name`,
`team_red_bull_driver_1_name`, `team_red_bull_driver_2_name`, `team_force_india_b_name`,
`team_force_india_b_driver_1_name`, `reserve_group`, `reserve_driver_1_name`.

**Drawn**: every mandatory field filled; `reserve_driver_4_*` treated as an unoccupied seat — name
emptied, flag and image removed; optional fields the template omits simply not addressed.

**Fatal if**: the template declares `team_mercedes_name` (a team the division lacks), or
`team_red_bull_driver_3_name` (a seat beyond the team's count), or omits
`team_force_india_b_driver_1_name` (a seat the division holds), or the division fields a fifth
reserve driver against four slots.

---

## 7. What the catalogue does **not** carry

- **Values.** `"Reserve"` as a display name, and the driver-name resolution chain, are values and
  live in `image_lineup_service.py`. A catalogue classifies fields.
- **Per-league team lists.** The catalogue names the collection and how its capacity is fixed; it
  never enumerates one league's teams (XIV.11).
- **The suppression decision.** Whether a missing nationality raises a notice depends on
  `/signup nationality toggle` and rides on the drawing, not on the catalogue.
