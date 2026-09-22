"""`/bot pack` — what travels with the league, and what stays with the server (issue #247).

A Discord user id means the same on every server; a channel, role or message id does not.
Pack keeps the first kind and clears the second, and is refused while the league has a
current season in any stage short of completed or cancelled.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.season import SeasonStage  # noqa: E402
from services.pack_service import KEPT_JOBS, PackRefused, pack  # noqa: E402
from services.scheduler_service import PORTRAIT_REFRESH_JOB_ID  # noqa: E402

SERVER = 4242

#: The member who packs. Nothing but a member's command packs the bot, so it is required.
ACTOR = {"actor_id": 7, "actor_name": "admin"}


def _scheduler() -> MagicMock:
    scheduler = MagicMock()
    scheduler.cancel_all.return_value = 3
    return scheduler


async def _seed(db_path: str, *, stage: str = "COMPLETED", test_mode: int = 0) -> None:
    """A league with one past season and one of everything pack keeps or clears."""
    status = {
        "COMPLETED": "COMPLETED", "CANCELLED": "CANCELLED",
        "CONFIGURATION": "SETUP", "WAITING": "SETUP", "SIGNUPS": "SETUP",
        "PLACEMENTS": "SETUP", "ONGOING": "ACTIVE", "ONGOING_SIGNUPS": "ACTIVE",
        "ONGOING_PLACEMENTS": "ACTIVE", "PENDING_COMPLETION": "ACTIVE",
    }[stage]
    async with get_connection(db_path) as db:
        await db.executescript(f"""
            INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id,
                log_channel_id, league_admin_role_id, test_mode_active,
                weather_module_enabled, signup_module_enabled, base_role_id, driver_role_id,
                hub_channel_id, hub_message_id)
            VALUES ({SERVER}, 1, 2, 3, 4, {test_mode}, 1, 1, 5, 6, 7, 8);
            INSERT INTO seasons (id, start_date, status, season_number, stage)
            VALUES (1, '2026-01-01', '{status}', 1, '{stage}');
            INSERT INTO divisions (id, season_id, name, mention_role_id, lineup_channel_id,
                lineup_message_id, calendar_channel_id, calendar_message_id)
            VALUES (1, 1, 'Pro', 50, 51, 52, 53, '54');
            INSERT INTO rounds (id, division_id, round_number, format, scheduled_at)
            VALUES (1, 1, 1, 'NORMAL', '2026-01-08T18:00:00');
            INSERT INTO forecast_messages (round_id, division_id, phase_number, message_id,
                posted_at) VALUES (1, 1, 1, 60, '2026-01-03');
            INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, channel_id,
                posted_at) VALUES (1, 1, '61', '62', '2026-01-03');
            INSERT INTO attendance_division_config (division_id, attendance_channel_id,
                attendance_message_id) VALUES (1, '63', '64');
            INSERT INTO round_submission_channels (round_id, channel_id, created_at, closed,
                results_posted, prompt_message_id, resubmit_prompt_message_id)
            VALUES (1, 65, '2026-01-08', 1, 1, 66, 67);
            INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver)
            VALUES (1, '900', 'NOT_SIGNED_UP', 0), (2, '901', 'NOT_SIGNED_UP', 1);
            INSERT INTO driver_accounts (driver_profile_id, discord_user_id) VALUES (1, '902');
            INSERT INTO driver_history_entries (discord_user_id, driver_profile_id,
                season_number, division_name) VALUES ('900', 1, 1, 'Pro');
            INSERT INTO driver_portraits (discord_user_id, avatar_key, fetched_at)
            VALUES ('900', 'abc', '2026-01-01');
            INSERT INTO default_teams (name) VALUES ('Ferrari');
            INSERT INTO team_role_configs (team_name, role_id) VALUES ('Ferrari', 70);
            INSERT INTO points_config_store (config_name) VALUES ('standard');
            INSERT INTO signup_module_config (id, signup_channel_id,
                signup_button_message_id, signup_closed_message_id,
                selected_tracks_json) VALUES (1, 80, 83, 84, '["1"]');
            INSERT INTO signup_module_settings (id, time_type) VALUES (1, 'SHORT_QUALI');
            INSERT INTO signup_wizard_records (discord_user_id, signup_channel_id)
            VALUES ('903', 85);
            INSERT INTO pending_messages (channel_id, content, failure_reason, enqueued_at)
            VALUES (86, 'hello', 'Forbidden', '2026-01-01');
            INSERT INTO season_review_prompts (id, season_id, channel_id, message_id,
                reviewer_id, posted_at) VALUES (1, 1, 87, 88, 89, '2026-01-01');
            INSERT INTO audit_entries (actor_id, actor_name, change_type, timestamp)
            VALUES (1, 'a', 'X', '2026-01-01');
        """)
        await db.commit()


@pytest.fixture
async def db_path(tmp_path) -> str:
    path = os.path.join(str(tmp_path), "test.db")
    await run_migrations(path)
    return path


async def _one(db_path: str, sql: str):
    async with get_connection(db_path) as db:
        return await (await db.execute(sql)).fetchone()


async def _all(db_path: str, sql: str) -> list:
    async with get_connection(db_path) as db:
        return await (await db.execute(sql)).fetchall()


async def _count(db_path: str, table: str) -> int:
    return (await _one(db_path, f"SELECT COUNT(*) FROM {table}"))[0]


# ── Refused while a season is current ──────────────────────────────────────


@pytest.mark.parametrize(
    "stage",
    [s.value for s in SeasonStage if s not in (SeasonStage.COMPLETED, SeasonStage.CANCELLED)],
)
async def test_pack_is_refused_while_a_season_is_current(db_path, stage):
    await _seed(db_path, stage=stage)
    scheduler = _scheduler()

    with pytest.raises(PackRefused) as refused:
        await pack(db_path, scheduler, **ACTOR)

    assert refused.value.stage == stage
    assert refused.value.season_number == 1
    scheduler.cancel_all.assert_not_called()
    assert (await _one(db_path, "SELECT server_id FROM server_configs"))[0] == SERVER
    assert await _count(db_path, "team_role_configs") == 1


@pytest.mark.parametrize("stage", ["COMPLETED", "CANCELLED"])
async def test_pack_proceeds_once_the_season_is_over(db_path, stage):
    await _seed(db_path, stage=stage)

    await pack(db_path, _scheduler(), **ACTOR)

    assert (await _one(db_path, "SELECT server_id FROM server_configs"))[0] is None


async def test_pack_proceeds_in_test_mode(db_path):
    """Decided 2026-09-19: test drivers travel with the league like every other profile."""
    await _seed(db_path, test_mode=1)

    await pack(db_path, _scheduler(), **ACTOR)

    assert (await _one(db_path, "SELECT test_mode_active FROM server_configs"))[0] == 1
    assert await _count(db_path, "driver_profiles") == 2


# ── What stays with the server ────────────────────────────────────────────


async def test_pack_frees_the_claim_and_the_four_settings(db_path):
    await _seed(db_path)

    await pack(db_path, _scheduler(), **ACTOR)

    row = await _one(db_path, "SELECT * FROM server_configs")
    assert (
        row["server_id"], row["interaction_role_id"], row["interaction_channel_id"],
        row["log_channel_id"], row["league_admin_role_id"],
    ) == (None, None, None, None, None)


async def test_pack_clears_the_team_roles_and_the_signup_channel(db_path):
    await _seed(db_path)

    result = await pack(db_path, _scheduler(), **ACTOR)

    assert result.team_roles == 1
    assert await _count(db_path, "team_role_configs") == 0
    row = await _one(db_path, "SELECT * FROM signup_module_config")
    assert (
        row["signup_channel_id"],
        row["signup_button_message_id"], row["signup_closed_message_id"],
    ) == (None, None, None)


async def test_a_pack_clears_both_league_roles(db_path):
    """They are the league's (issue #276), but a role belongs to its server: kept, the next
    server's configuration would name roles that do not exist there."""
    await _seed(db_path)

    await pack(db_path, _scheduler(), **ACTOR)

    row = await _one(db_path, "SELECT base_role_id, driver_role_id FROM server_configs")
    assert (row["base_role_id"], row["driver_role_id"]) == (None, None)


async def test_a_pack_clears_the_hub_and_the_record_of_its_panel(db_path):
    """The hub is a channel of the server left behind (issue #279), and its panel a message
    there: the next server's hub is set afresh."""
    await _seed(db_path)

    await pack(db_path, _scheduler(), **ACTOR)

    row = await _one(db_path, "SELECT hub_channel_id, hub_message_id FROM server_configs")
    assert (row["hub_channel_id"], row["hub_message_id"]) == (None, None)


async def test_pack_clears_wizards_the_retry_queue_and_the_review_prompt(db_path):
    await _seed(db_path)

    result = await pack(db_path, _scheduler(), **ACTOR)

    assert (result.wizards, result.queued_messages) == (1, 1)
    assert await _count(db_path, "signup_wizard_records") == 0
    assert await _count(db_path, "pending_messages") == 0
    assert await _count(db_path, "season_review_prompts") == 0


async def test_pack_clears_the_message_ids_the_bot_edits_by(db_path):
    await _seed(db_path)

    await pack(db_path, _scheduler(), **ACTOR)

    assert await _count(db_path, "forecast_messages") == 0
    assert await _count(db_path, "rsvp_embed_messages") == 0
    division = await _one(db_path, "SELECT * FROM divisions")
    assert (division["lineup_message_id"], division["calendar_message_id"]) == (None, None)
    attendance = await _one(db_path, "SELECT * FROM attendance_division_config")
    assert attendance["attendance_message_id"] is None
    submission = await _one(db_path, "SELECT * FROM round_submission_channels")
    assert submission["prompt_message_id"] is None
    assert submission["resubmit_prompt_message_id"] is None


async def test_pack_clears_the_scheduled_work_but_the_portrait_refresh(db_path):
    await _seed(db_path)
    scheduler = _scheduler()

    result = await pack(db_path, scheduler, **ACTOR)

    scheduler.cancel_all.assert_called_once_with(keep=KEPT_JOBS)
    assert KEPT_JOBS == frozenset({PORTRAIT_REFRESH_JOB_ID})
    assert result.scheduled_jobs == 3


async def test_pack_drops_the_league_state_held_in_memory(db_path, monkeypatch):
    await _seed(db_path)
    cleared = []
    monkeypatch.setattr(
        "services.pack_service.clear_in_memory_state", lambda bot: cleared.append(bot)
    )
    bot = object()

    await pack(db_path, _scheduler(), bot, **ACTOR)

    assert cleared == [bot]


# ── What travels with the league ──────────────────────────────────────────


async def test_pack_keeps_every_driver_with_their_accounts_history_and_portrait(db_path):
    await _seed(db_path)

    await pack(db_path, _scheduler(), **ACTOR)

    assert await _count(db_path, "driver_profiles") == 2
    account = await _one(
        db_path, "SELECT driver_profile_id FROM driver_accounts WHERE discord_user_id = '902'"
    )
    assert account[0] == 1
    assert await _count(db_path, "driver_history_entries") == 1
    assert await _count(db_path, "driver_portraits") == 1


async def test_pack_keeps_the_past_season_whole(db_path):
    await _seed(db_path)

    await pack(db_path, _scheduler(), **ACTOR)

    assert await _count(db_path, "seasons") == 1
    assert await _count(db_path, "divisions") == 1
    assert await _count(db_path, "rounds") == 1
    submission = await _one(db_path, "SELECT * FROM round_submission_channels")
    assert (submission["closed"], submission["results_posted"]) == (1, 1)
    assert await _count(db_path, "audit_entries WHERE change_type = 'X'") == 1


async def test_pack_keeps_the_teams_points_and_module_settings(db_path):
    await _seed(db_path)

    await pack(db_path, _scheduler(), **ACTOR)

    assert await _count(db_path, "default_teams") == 1
    assert await _count(db_path, "points_config_store") == 1
    row = await _one(db_path, "SELECT * FROM server_configs")
    assert (row["weather_module_enabled"], row["signup_module_enabled"]) == (1, 1)
    signup = await _one(db_path, "SELECT * FROM signup_module_config")
    assert signup["selected_tracks_json"] == '["1"]'
    settings = await _one(db_path, "SELECT * FROM signup_module_settings")
    assert settings["time_type"] == "SHORT_QUALI"


# ── The record of the pack ────────────────────────────────────────────────


async def test_pack_is_audited_with_every_setting_and_role_it_cleared(db_path):
    """Issue #383. The log line goes to a server the league is leaving; the audit entry is
    what keeps who packed and what the settings held. Message ids are the bot's record of
    its own posts, not configuration, and are not in it."""
    await _seed(db_path)

    await pack(db_path, _scheduler(), **ACTOR)

    row = await _one(db_path, "SELECT * FROM audit_entries WHERE change_type = 'BOT_PACKED'")
    assert (row["actor_id"], row["actor_name"], row["division_id"]) == (7, "admin", None)
    assert json.loads(row["old_value"]) == {
        "server_id": SERVER,
        "interaction_role_id": 1,
        "interaction_channel_id": 2,
        "log_channel_id": 3,
        "league_admin_role_id": 4,
        "base_role_id": 5,
        "driver_role_id": 6,
        "hub_channel_id": 7,
        "signup_channel_id": 80,
        "team_roles": {"Ferrari": 70},
    }
    assert json.loads(row["new_value"]) == {
        "server_id": None,
        "interaction_role_id": None,
        "interaction_channel_id": None,
        "log_channel_id": None,
        "league_admin_role_id": None,
        "base_role_id": None,
        "driver_role_id": None,
        "hub_channel_id": None,
        "signup_channel_id": None,
        "team_roles": {},
    }


async def test_a_refused_pack_records_nothing(db_path):
    """The entry shares the pack's transaction, so a pack that did not happen is not audited
    as though it had."""
    await _seed(db_path, stage="CONFIGURATION")

    with pytest.raises(PackRefused):
        await pack(db_path, _scheduler(), **ACTOR)

    rows = await _all(db_path, "SELECT change_type FROM audit_entries")
    assert [r["change_type"] for r in rows] == ["X"]
