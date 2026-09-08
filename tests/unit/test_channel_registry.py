"""A channel does one job.

The bot posts to eleven configurable places — eight per division, two for the bot itself
and one for signups — and a command that sets one now refuses a channel already serving
another (decided 2026-09-06). The rule is **server-wide**: two divisions may not share a
results channel any more than a calendar may double as a log.

Why it matters beyond tidiness: several posting paths *edit or delete* the message they
posted last, finding it by an id stored against the channel. Two purposes in one channel
is how a lineup comes to delete a standings message.

Re-running a command with the value it already holds is refused too, but says so in its
own words — it is not a collision with something else, and calling it one would send a
manager hunting a conflict that does not exist.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.channel_registry_service import (  # noqa: E402
    SETTING_LABELS,
    ChannelUse,
    find_channel_use,
    refusal,
)

SERVER_ID = 4242
OTHER_SERVER = 4343


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "channels.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        # Both channel columns are NOT NULL, so a configured server always holds some
        # value. These two are far from every id the tests use, so they cannot be
        # mistaken for a channel under test.
        for server in (SERVER_ID, OTHER_SERVER):
            await db.execute(
                "INSERT INTO server_configs (server_id, interaction_role_id, "
                "interaction_channel_id, log_channel_id) VALUES (?, 1, ?, ?)",
                (server, 900_000 + server, 910_000 + server),
            )
        await db.commit()
    return path


async def _season(db_path, server_id=SERVER_ID, status="SETUP"):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-03-01', ?, 1)",
            (server_id, status),
        )
        await db.commit()
        return cursor.lastrowid


async def _division(db_path, season_id, name):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
            "VALUES (?, ?, 1, 'ACTIVE', 1)",
            (season_id, name),
        )
        await db.commit()
        return cursor.lastrowid


async def _set(db_path, setting, channel_id, *, division_id=None, server_id=SERVER_ID):
    """Point *setting* at *channel_id*, writing where the real command writes."""
    async with get_connection(db_path) as db:
        if setting in ("weather", "lineup", "calendar"):
            column = {
                "weather": "forecast_channel_id",
                "lineup": "lineup_channel_id",
                "calendar": "calendar_channel_id",
            }[setting]
            await db.execute(
                f"UPDATE divisions SET {column} = ? WHERE id = ?",
                (channel_id, division_id),
            )
        elif setting in ("results", "standings", "verdicts"):
            column = {
                "results": "results_channel_id",
                "standings": "standings_channel_id",
                "verdicts": "penalty_channel_id",
            }[setting]
            await db.execute(
                f"INSERT INTO division_results_config (division_id, {column}) "
                f"VALUES (?, ?) ON CONFLICT(division_id) DO UPDATE SET {column} = ?",
                (division_id, channel_id, channel_id),
            )
        elif setting in ("rsvp", "attendance"):
            column = f"{setting}_channel_id"
            await db.execute(
                f"INSERT INTO attendance_division_config (division_id, server_id, {column}) "
                f"VALUES (?, ?, ?) ON CONFLICT(division_id) DO UPDATE SET {column} = ?",
                (division_id, server_id, channel_id, channel_id),
            )
        elif setting == "signup":
            await db.execute(
                "INSERT INTO signup_module_config (server_id, signup_channel_id) "
                "VALUES (?, ?) ON CONFLICT(server_id) DO UPDATE SET signup_channel_id = ?",
                (server_id, channel_id, channel_id),
            )
        else:
            column = {"interaction": "interaction_channel_id", "log": "log_channel_id"}[
                setting
            ]
            await db.execute(
                f"UPDATE server_configs SET {column} = ? WHERE server_id = ?",
                (channel_id, server_id),
            )
        await db.commit()


# ── Every setting is found ────────────────────────────────────────────────


@pytest.mark.parametrize("setting", sorted(SETTING_LABELS))
async def test_a_channel_in_use_is_found_whatever_uses_it(db_path, setting):
    """All eleven, so a setting added later without a source here shows up as a gap."""
    season_id = await _season(db_path)
    division_id = await _division(db_path, season_id, "Pro")

    await _set(db_path, setting, 500, division_id=division_id)

    use = await find_channel_use(db_path, SERVER_ID, 500)
    assert use is not None, f"{setting} was not detected"
    assert use.setting == setting


async def test_a_free_channel_is_free(db_path):
    assert await find_channel_use(db_path, SERVER_ID, 999) is None


async def test_the_label_names_the_division_for_a_per_division_setting(db_path):
    season_id = await _season(db_path)
    division_id = await _division(db_path, season_id, "Pro")
    await _set(db_path, "results", 500, division_id=division_id)

    use = await find_channel_use(db_path, SERVER_ID, 500)

    assert use.division_name == "Pro"
    assert "**Pro**" in use.describe()


@pytest.mark.parametrize("setting", ["interaction", "log", "signup"])
async def test_a_server_setting_names_no_division(db_path, setting):
    await _set(db_path, setting, 500)

    use = await find_channel_use(db_path, SERVER_ID, 500)

    assert use.division_name is None
    assert "**" not in use.describe()


# ── The rule is server-wide ───────────────────────────────────────────────


async def test_two_divisions_may_not_share_a_channel(db_path):
    """The decision: one channel, one purpose, across the whole server."""
    season_id = await _season(db_path)
    pro = await _division(db_path, season_id, "Pro")
    await _division(db_path, season_id, "Academy")
    await _set(db_path, "results", 500, division_id=pro)

    use = await find_channel_use(db_path, SERVER_ID, 500)

    assert use == ChannelUse("results", "Pro")


async def test_one_division_may_not_use_a_channel_for_two_things(db_path):
    season_id = await _season(db_path)
    pro = await _division(db_path, season_id, "Pro")
    await _set(db_path, "calendar", 500, division_id=pro)

    use = await find_channel_use(db_path, SERVER_ID, 500)

    assert use.setting == "calendar"


async def test_another_servers_channel_is_not_this_servers_business(db_path):
    """The bot serves many leagues, and a channel id is unique across Discord anyway —
    but the query must still be scoped, or one league could block another."""
    other_season = await _season(db_path, server_id=OTHER_SERVER)
    other_division = await _division(db_path, other_season, "Theirs")
    await _set(
        db_path, "results", 500, division_id=other_division, server_id=OTHER_SERVER
    )

    assert await find_channel_use(db_path, SERVER_ID, 500) is None


# ── An archived season does not hold a channel hostage ────────────────────


@pytest.mark.parametrize("status", ["COMPLETED", "CANCELLED"])
async def test_a_finished_seasons_channel_is_free_again(db_path, status):
    """A league reusing last season's results channel is doing the ordinary thing."""
    season_id = await _season(db_path, status=status)
    division_id = await _division(db_path, season_id, "Pro")
    await _set(db_path, "results", 500, division_id=division_id)

    assert await find_channel_use(db_path, SERVER_ID, 500) is None


# ── Ignoring the setting being written ────────────────────────────────────


async def test_a_setting_does_not_block_itself(db_path):
    """Without this, changing a division's results channel would be refused by its own
    current value and the command could never be re-run."""
    season_id = await _season(db_path)
    division_id = await _division(db_path, season_id, "Pro")
    await _set(db_path, "results", 500, division_id=division_id)

    ignored = await find_channel_use(
        db_path, SERVER_ID, 500, ignore=ChannelUse("results", "Pro")
    )

    assert ignored is None


async def test_ignoring_one_setting_does_not_hide_another(db_path):
    season_id = await _season(db_path)
    pro = await _division(db_path, season_id, "Pro")
    await _set(db_path, "calendar", 500, division_id=pro)

    use = await find_channel_use(
        db_path, SERVER_ID, 500, ignore=ChannelUse("results", "Pro")
    )

    assert use == ChannelUse("calendar", "Pro")


# ── What the refusal says ─────────────────────────────────────────────────


def test_a_clash_tells_the_manager_what_holds_the_channel():
    message = refusal("#pro-results", ChannelUse("results", "Pro"), same_setting=False)

    assert "❌" in message
    assert "results channel for **Pro**" in message
    assert "does one job" in message


def test_re_setting_a_channel_to_itself_is_not_reported_as_a_clash():
    """Refused, but in its own words: nothing else holds the channel."""
    message = refusal("#pro-results", ChannelUse("results", "Pro"), same_setting=True)

    assert "ℹ️" in message
    assert "already the" in message
    assert "Nothing was changed" in message
    assert "does one job" not in message


def test_every_setting_has_a_label_a_league_would_recognise():
    for setting, label in SETTING_LABELS.items():
        assert label and not label.endswith("_channel_id"), setting
