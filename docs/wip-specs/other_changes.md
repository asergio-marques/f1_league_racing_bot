# Tracks expansion
- The current definition of tracks is far too rigid and preset, not allowing for flexibility. Instead, this concept will be expanded to allow for greater flexibility and configuration.
- Tracks will be expanded into the following data format:
    - Track ID
    - Track name
    - Grand Prix name
    - Location
    - Country
    - Sigma (for weather draw)
    - Mu (for weather draw)
    - Tier x track record (1..n) - all session types
        - Game
        - Season
        - Round
        - Lap time (per tier)
        - User ID (per tier)
    - Tier x lap record (1..n) - only for sprint race and feature
        - Game
        - Season
        - Round
        - Lap time (per tier)
        - User ID (per tier)
- By default, the following tracks will be available (lap and track records will be blank, sigma and mu will be obtained from the current default configurations):
    ID; Name; Grand Prix; Location
    1; Albert Park Circuit; Australian Grand Prix; Melbourne, Australia
    2; Shanghai International Circuit; Chinese Grand Prix; Shanghai, China
    3; Suzuka International Racing Course; Japanese Grand Prix; Suzuka, Japan
    4; Bahrain International Circuit; Bahrain Grand Prix; Sakhir, Bahrain
    5; Jeddah Corniche Circuit; Saudi Arabian Grand Prix; Jeddah, Saudi Arabia
    6; Miami International Autodrome; Miami Grand Prix; Miami, Florida, United States of America
    7; Autodromo Internazionale Enzo e Dino Ferrari; Emilia Romagna Grand Prix; Imola, Italy
    8; Circuit de Monaco; Monaco Grand Prix; Municipality of Monaco, Monaco
    9; Circuit de Barcelona-Catalunya; Barcelona-Catalunya Grand Prix; Montmeló Spain
    10; Circuit Gilles Villeneuve; Canadian Grand Prix; Montreal, Canada
    11; Red Bull Ring; Austrian Grand Prix; Spielberg, Austria
    12; Silverstone Circuit; British Grand Prix; Silverstone, United Kingdom
    13; Circuit de Spa-Francorchamps; Belgian Grand Prix; Stavelot, Belgium
    14; Hungaroring; Hungarian Grand Prix; Mogyoród, Hungary
    15; Circuit Zandvoort; Dutch Grand Prix; Zandvoort, Netherlands
    16; Autodromo Nazionale Monza; Italian Grand Prix; Monza, Italy
    17; Circuito de Madring; Spanish Grand Prix; Madrid, Spain
    18; Baku City Circuit; Azerbaijan Grand Prix; Baku, Azerbaijan
    19; Marina Bay Street Circuit; Singapore Grand Prix; Singapore City, Singapore
    20; Circuit of the Americas; United States Grand Prix; Austin, Texas, United States of America
    21; Autódromo Hermanos Rodriguez; Mexico City Grand Prix; Mexico City, Mexico
    22; Autódromo José Carlos Pace; São Paulo Grand Prix; São Paulo, Brazil
    23; Las Vegas Strip Circuit; Las Vegas Grand Prix; Las Vegas, Nevada, United States of America
    24; Lusail International Circuit; Qatar Grand Prix; Lusail, Qatar
    25; Yas Marina Circuit; Abu Dhabi Grand Prix; Abu Dhabi, United Arab Emirates
    26; Autódromo Internacional do Algarve; Portuguese Grand Prix; Portimão, Portugal
    27; Istanbul Park; Turkish Grand Prix; Istanbul, Turkey
    28; Circuit Paul Ricard; French Grand Prix; Le Castellet, France (new, use same sigma and mu as Monaco)
- Current track commands will be discarded and deleted from the codebase.
- Any custom weather configurations for tracks will be deleted in the migration to the new data schema.
- Tier <x> data structures will be created dynamically at runtime, depending on necessity.
- <COMMAND CHANGE> Due to these changes, the "division add" command's "tier" parameter is a mandatory parameter.
    - During season review, division tiers must be sequential (no gaps) and 1-indexed (lowest possible value). Failing either criteria will mean the season fails validation.
    - <NEW COMMAND> A new "division amend" command shall be made available to league managers that will intake the name of the division to be changed (mandatory), a string standing for the new name of the division (optional), an integer standing for the tier (optional), and a role standing for the division role (optional). This command will fail if neither of the optional parameters are chosen.
        - This command allows the correction of division parameters during season setup exclusively.
- <NEW COMMAND> A "track list" command will be made available to league managers, which will display the IDs and names of all tracks available.


------

# Image output

## Plan: Automatic Image Generation (SVG → PNG) for F1 League Bot

### Summary

Replace (or supplement) text-based announcements for race results and championship standings with dynamically generated PNG images. Users supply their own SVG template files (per-guild), the bot fills in live data using lxml XML manipulation, converts SVG→PNG via cairosvg, then posts as a Discord file attachment. Fallback to current text output on any failure, with error logged to the guild's log channel.

**Stack:** lxml + cairosvg (+ existing Pillow dependency) on Raspberry Pi.

---

### Decisions Made

- **Per-guild templates**: Each Discord server stores its own SVG files in `assets/templates/{guild_id}/`
- **Repeating rows**: One `<g id="row_template">` group in the SVG defines a single row; bot clones + Y-shifts it N times
- **Fonts**: System-installed fonts only (cairosvg limitation — @font-face not supported)
- **Fallback**: Silent fallback to existing text output + error logged to guild log channel
- **Rollout priority**: Race results first, championship standings second
- **Discord posting**: `discord.File(BytesIO(png_bytes))` via extended OutputRouter

---

### Architecture

#### New Module: `src/image_gen/`

```
src/image_gen/
  __init__.py
  loader.py        # Template resolution: guild-specific → error
  renderer.py      # SVG manipulation (lxml) + PNG export (cairosvg)
  contracts.py     # Typed dataclasses per template type (RaceResultsData, StandingsData, etc.)
  formatters/
    __init__.py
    results.py     # DriverSessionResult list → RaceResultsData
    standings.py   # StandingsSnapshot list → StandingsData
```

#### Template File Convention

```
assets/templates/{guild_id}/
  results_qualifying.svg
  results_race.svg
  results_sprint_qualifying.svg
  results_sprint_race.svg
  standings_drivers.svg
  standings_teams.svg
```

#### SVG Template Contract

**Fixed fields** (single text value): addressed by `id` attribute on `<text>` elements
- e.g., `<text id="title">`, `<text id="round_info">`

**Row template** (variable-length list):
- One `<g id="row_template">` group, placed at the first row's Y position
- Children addressed by `id`: `<text id="position">`, `<text id="driver_name">`, `<text id="team_name">`, `<text id="points">`, `<text id="gap">`
- Bot reads the group's Y and height (via `transform` or bounding box), clones it N times with incremented Y, renames IDs to `row_{n}_{field}` for XML uniqueness
- Original `row_template` group is removed after cloning

#### Data Contracts

```python
@dataclass
class ResultRow:
    position: str      # "1", "DNF", "DSQ", "DNS"
    driver_name: str
    team_name: str
    gap: str           # "+1.234s", "1:23.456", ""
    tyre: str          # optional
    points: str        # "25", "0"
    fastest_lap: bool  # for indicator styling

@dataclass
class RaceResultsData:
    title: str         # "S1 — Round 3 — Bahrain | Race Results"
    rows: list[ResultRow]

@dataclass
class StandingsRow:
    position: str
    driver_name: str
    team_name: str
    points: str
    change: str        # "+2", "-1", "–"

@dataclass
class StandingsData:
    title: str
    rows: list[StandingsRow]
```

#### Rendering Pipeline (renderer.py)

1. `load_template(guild_id, template_name) → lxml.etree._Element` — parse SVG with lxml
2. `bind_fields(tree, fields: dict[str, str])` — find elements by ID, set text content
3. `bind_rows(tree, rows: list[dict])` — find `row_template`, clone N times with Y offset, bind fields per row
4. `render_to_png(tree) → bytes` — serialize SVG → bytes, call `cairosvg.svg2png(bytestring=...)`, return PNG bytes

#### Integration Points

- `results_post_service.post_session_results()` → call image gen, post `discord.File` with caption, fallback to current text formatter
- `results_post_service.post_standings()` → same for standings
- `output_router.py` → add `post_image(channel_id, png_bytes, caption=None)` method using `discord.File(BytesIO(png_bytes), filename="result.png")`
- Error path: `except Exception as e: await output_router.post_log(guild_id, f"Image generation failed: ...")` then fall through to text

---

### Dependencies

New pip packages:
- `lxml` (XML manipulation — likely not in requirements.txt yet)
- `cairosvg` (SVG→PNG — new)

System packages on Raspberry Pi (one-time setup):
- `sudo apt install libcairo2 libffi-dev python3-dev`

---

### cairosvg Limitations to Document

- No CSS `@font-face` — fonts must be installed system-wide on the Pi
- Only 3 SVG filters supported: `feOffset`, `feBlend`, `feFlood` — no blur/drop-shadow/glow
- No SVG animations or interactivity (irrelevant for static images)
- Basic text layout only; complex typography not supported

---

### Phase 1 Scope (Race Results + Standings)

1. Create `src/image_gen/` module skeleton
2. Implement `loader.py` with per-guild path resolution
3. Implement `renderer.py`:
   - Fixed field binding (lxml tree → find by ID → set text)
   - Row template cloning + Y-offset algorithm
   - cairosvg PNG export
4. Implement `contracts.py` data classes
5. Implement `formatters/results.py` (DriverSessionResult list → RaceResultsData)
6. Implement `formatters/standings.py` (StandingsSnapshot list → StandingsData)
7. Extend `output_router.py` with `post_image()` method
8. Integrate into `results_post_service.post_session_results()` with try/except fallback
9. Integrate into `results_post_service.post_standings()` with try/except fallback
10. Write unit tests for renderer (with example SVG fixture) and formatters

### Phase 2 Scope (Later)

- Driver lineup image (`placement_service._maybe_post_lineup`)
- Weather forecast images (phase1/2/3 services)
- Template upload/management commands
- Validation tooling for template authors

---

### Verification

1. `python -m pytest tests/ -v` — all existing tests still pass
2. Manual test: place a sample SVG in `assets/templates/{test_guild_id}/results_race.svg`, trigger a result post, verify PNG attachment appears in Discord
3. Verify fallback: rename the template file, confirm text output appears and log channel receives error message
4. Verify per-guild isolation: two guilds with different templates produce different images

---

### Open Questions

- How are template files deployed to the bot? (Manually copied to Pi, or uploaded via a bot command?) Recommend: Phase 1 = manual file placement; Phase 2 = bot command upload.
- Should the PNG be posted as a standalone message or replace the existing message (edit-in-place is currently used for results)? Likely standalone for images since Discord edit supports attachments.