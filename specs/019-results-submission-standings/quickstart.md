# Quickstart: Results & Standings Module (019)

**Feature**: `019-results-submission-standings`  
**Date**: 2026-03-18

---

## Prerequisites

- Python 3.13.2+
- All dependencies installed: `pip install -r requirements.txt`
- A Discord bot token in `.env` (or environment variable)
- The 018-results-standings foundation already merged to main (module enable/disable, channel commands, approval gates, migration 016)

---

## Running the Bot

```bash
cd src
python bot.py
```

On startup, all pending SQL migrations in `src/db/migrations/` are applied automatically in filename order. Migration `017_results_core.sql` will be applied on first run of this branch.

---

## Running Tests

```bash
# From project root
pytest

# Run only unit tests
pytest tests/unit/

# Run only integration tests
pytest tests/integration/

# Run tests for a specific new service
pytest tests/unit/test_points_config_service.py -v
pytest tests/unit/test_standings_service.py -v
```

All tests use pytest-asyncio with temporary in-memory SQLite databases. No Discord connection required for unit tests.

---

## Module Setup Flow (End-to-End)

This is the intended administrator setup sequence for a server using the Results & Standings module for the first time:

1. **Enable module** (server admin):
   ```
   /module enable results
   ```

2. **Set division channels** (trusted admin):
   ```
   /division results-channel division:"Pro" channel:#pro-results
   /division standings-channel division:"Pro" channel:#pro-standings
   ```

3. **Create a points config** (trusted admin):
   ```
   /results config add name:"100%"
   /results config session name:"100%" session:Feature Race position:1 points:25
   /results config session name:"100%" session:Feature Race position:2 points:18
   ... (repeat for all positions)
   /results config fl name:"100%" session:Feature Race points:1
   /results config fl-plimit name:"100%" session:Feature Race limit:10
   ```

4. **Begin season setup** (trusted admin), then attach the config:
   ```
   /season setup ...
   /results config append name:"100%"
   ```

5. **Review and approve season** (trusted admin):
   ```
   /season review
   /season approve
   ```
   The approval gate checks: all divisions have results and standings channels, at least one config is attached, and all attached configs have monotonically non-increasing points within each session type.

6. **Round results are submitted automatically** — at each round's scheduled start time the bot creates a `results-sub-{division}-r{N}` channel and notifies tier-2 admins.

---

## Adding a New Service

All services follow the same structure. Create `src/services/your_service.py`:

```python
"""YourService — brief description."""
from __future__ import annotations
from db.database import get_connection

class YourService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def some_method(self, server_id: int) -> ...:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute("SELECT ...", (server_id,))
            row = await cursor.fetchone()
        return ...
```

Register the service on `bot.py`'s `Bot.__init__` (same pattern as `self.module_service`, `self.season_service`, etc.).

---

## Adding a New Command

New commands go in a cog. Create `src/cogs/your_cog.py`:

```python
"""YourCog — brief description."""
from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands

class YourCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    results_group = app_commands.Group(name="results", description="Results commands")

    @results_group.command(name="something", description="Does something")
    @app_commands.describe(name="Config name")
    async def something(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        # ... module gate check, then business logic
        await interaction.followup.send("Done.", ephemeral=True)

async def setup(bot) -> None:
    await bot.add_cog(YourCog(bot))
```

Register in `bot.py`'s `setup_hook` with `await self.load_extension("cogs.your_cog")`. For the results commands this feature introduces, they all go into `src/cogs/results_cog.py`.

---

## Module Gate Pattern

Every command in the Results & Standings module must check that the module is enabled before proceeding:

```python
if not await self.bot.module_service.is_results_enabled(interaction.guild_id):
    await interaction.followup.send(
        "The Results & Standings module is not enabled on this server.", ephemeral=True
    )
    return
```

---

## Key File Locations

| File | Purpose |
|------|---------|
| `src/db/migrations/017_results_core.sql` | All new tables for this feature |
| `src/cogs/results_cog.py` | All `/results` and `/round results` commands |
| `src/services/points_config_service.py` | Server-level config store CRUD |
| `src/services/season_points_service.py` | Season snapshot, view, monotonic validation |
| `src/services/result_submission_service.py` | Submission channel + session wizard |
| `src/services/standings_service.py` | Standings computation + snapshot persistence |
| `src/services/results_post_service.py` | Format + post results/standings to Discord |
| `src/services/penalty_service.py` | Penalty wizard state machine |
| `src/services/amendment_service.py` | Extend with modification store workflow |
| `src/services/scheduler_service.py` | Extend with `results_r{id}` job |
| `src/utils/results_formatter.py` | Table-formatting helpers |
| `tests/unit/test_standings_service.py` | Standings computation unit tests |
| `tests/unit/test_result_submission_service.py` | Submission validation unit tests |
