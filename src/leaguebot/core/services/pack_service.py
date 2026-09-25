"""pack_service — ready the bot to serve its league from another Discord server (issue #247).

**What is the league's travels; what is the server's stays behind.** A Discord user id is the
same on every server, so a driver's profile, accounts, history and portrait mean the same
thing on the next one, as do completed seasons, the team list, the points configurations and
every module setting that is not a channel or a role. A channel id, a role id or a message id
names something on *this* server and nothing on another, so those are cleared, with the claim
itself and the scheduled work that would post into them.

**Refused while the league has a current season** (decided 2026-09-19): a season in any stage
short of completed or cancelled, which is read off the lifecycle `stage` rather than the
coarser `status`. A current season's divisions are built on this server's roles and channels,
and a league does not pack up and leave with one under way.

**One transaction.** The check and every write share one `BEGIN IMMEDIATE`, so a season set
up between the check and the release cannot be stranded on a server the bot has left. The
audit entry shares it too (issue #383): every pack that takes effect is recorded, and a
refused one records nothing.

The Discord side is left as it is: the old server keeps the bot's messages and buttons, which
refuse from then on (see `utils.league_server.LeagueView`). Pack touches only the database.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from db.database import get_connection
from models.season import SeasonStage
from services.config_service import RELEASE_CLAIM_SQL
from services.in_memory_state import clear_in_memory_state
from services.scheduler_service import PORTRAIT_REFRESH_JOB_ID
from utils.league_bot import LeagueBot

if TYPE_CHECKING:
    from services.scheduler_service import SchedulerService

log = logging.getLogger(__name__)

#: The stages that end a season. Any other stage — or none yet — is a current season.
_FINISHED_STAGES: tuple[str, ...] = (SeasonStage.COMPLETED.value, SeasonStage.CANCELLED.value)

#: Scheduled work pack leaves armed. The daily portrait refresh is a standing instruction from
#: an image setting pack keeps, and does nothing while no server is claimed.
KEPT_JOBS: frozenset[str] = frozenset({PORTRAIT_REFRESH_JOB_ID})


class PackRefused(Exception):
    """The league has a current season. Carries its number and stage for the refusal."""

    def __init__(self, season_number: int, stage: str | None) -> None:
        self.season_number = season_number
        self.stage = stage
        super().__init__(f"season {season_number} is current (stage {stage})")


@dataclass(frozen=True)
class PackResult:
    """What pack cleared, for the reply and the log."""

    team_roles: int
    wizards: int
    queued_messages: int
    scheduled_jobs: int


async def current_season(db) -> tuple[int, str | None] | None:
    """The league's current season as (number, stage), or None where it has none."""
    placeholders = ",".join("?" for _ in _FINISHED_STAGES)
    cursor = await db.execute(
        f"SELECT season_number, stage FROM seasons "
        f"WHERE stage IS NULL OR stage NOT IN ({placeholders}) "
        f"ORDER BY id DESC LIMIT 1",
        _FINISHED_STAGES,
    )
    row = await cursor.fetchone()
    return None if row is None else (int(row["season_number"]), row["stage"])


#: The settings a league set that pack clears from `server_configs`, as the audit names them.
#: The hub's panel is left out: a message id is the bot's record of its own post.
_AUDITED_SETTINGS: tuple[str, ...] = (
    "server_id",
    "interaction_role_id",
    "interaction_channel_id",
    "log_channel_id",
    "league_admin_role_id",
    "base_role_id",
    "driver_role_id",
    "hub_channel_id",
)


async def _cleared_configuration(db) -> dict[str, Any]:
    """The configuration pack is about to clear, for its audit entry (issue #383).

    Every setting and role a league set upon this server: the four settings and the claim,
    the two league roles, the hub, the signup channel and each team's role. The next
    server's `/bot init` is audited as replacing nothing, so this entry is the only place
    in the database the values survive.
    """
    cursor = await db.execute(f"SELECT {', '.join(_AUDITED_SETTINGS)} FROM server_configs")
    row = await cursor.fetchone()
    cleared: dict[str, Any] = {
        column: None if row is None else row[column] for column in _AUDITED_SETTINGS
    }
    cursor = await db.execute("SELECT signup_channel_id FROM signup_module_config")
    row = await cursor.fetchone()
    cleared["signup_channel_id"] = None if row is None else row["signup_channel_id"]
    cursor = await db.execute(
        "SELECT team_name, role_id FROM team_role_configs ORDER BY team_name"
    )
    cleared["team_roles"] = {r["team_name"]: r["role_id"] for r in await cursor.fetchall()}
    return cleared


async def pack(
    db_path: str,
    scheduler_service: "SchedulerService",
    bot: LeagueBot | None = None,
    *,
    actor_id: int,
    actor_name: str,
) -> PackResult:
    """Clear everything tied to this server and free the claim. Raises `PackRefused`.

    *bot*, where given, has its in-memory league state dropped too; the command passes it,
    and a test of the database alone need not. *actor_id* and *actor_name* are the member
    who packed, and have no default: nothing but a member's command packs the bot.
    """
    async with get_connection(db_path) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            season = await current_season(db)
            if season is not None:
                raise PackRefused(*season)

            cleared = await _cleared_configuration(db)

            team_roles = (await db.execute("DELETE FROM team_role_configs")).rowcount
            wizards = (await db.execute("DELETE FROM signup_wizard_records")).rowcount
            queued = (await db.execute("DELETE FROM pending_messages")).rowcount
            await db.execute("DELETE FROM season_review_prompts")

            # The signup module stays enabled with nothing configured, which is the state
            # `/module enable signup` itself leaves it in.
            await db.execute(
                "UPDATE signup_module_config SET signup_channel_id = NULL, "
                "signup_button_message_id = NULL, signup_closed_message_id = NULL"
            )

            # The league's two roles are core's (issue #276), but a role belongs to its
            # server: the next server's `/bot init` finds none, as it finds no team role.
            await db.execute(
                "UPDATE server_configs SET base_role_id = NULL, driver_role_id = NULL"
            )
            # The hub is a channel of this server, and its panel a message in it (#279). The
            # panel stays where it is, its buttons refused while no server is claimed.
            await db.execute(
                "UPDATE server_configs SET hub_channel_id = NULL, hub_message_id = NULL"
            )

            # The message ids the bot keeps to edit its own posts.
            await db.execute("DELETE FROM forecast_messages")
            await db.execute("DELETE FROM rsvp_embed_messages")
            await db.execute(
                "UPDATE divisions SET lineup_message_id = NULL, calendar_message_id = NULL"
            )
            await db.execute(
                "UPDATE attendance_division_config SET attendance_message_id = NULL"
            )
            await db.execute(
                "UPDATE round_submission_channels "
                "SET prompt_message_id = NULL, resubmit_prompt_message_id = NULL"
            )

            await db.execute(RELEASE_CLAIM_SQL)

            await db.execute(
                "INSERT INTO audit_entries "
                "(actor_id, actor_name, division_id, change_type, old_value, new_value, "
                "timestamp) VALUES (?, ?, NULL, 'BOT_PACKED', ?, ?, ?)",
                (
                    actor_id,
                    actor_name,
                    json.dumps(cleared),
                    json.dumps({**dict.fromkeys(cleared), "team_roles": {}}),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        except BaseException:
            await db.rollback()
            raise
        await db.commit()

    jobs = scheduler_service.cancel_all(keep=KEPT_JOBS)
    if bot is not None:
        clear_in_memory_state(bot)

    result = PackResult(
        team_roles=team_roles, wizards=wizards, queued_messages=queued, scheduled_jobs=jobs
    )
    log.info("Packed: %s", result)
    return result
