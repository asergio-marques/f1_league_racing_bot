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

import ast
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.channel_registry_service import (
    SETTING_LABELS,
    ChannelUse,
    as_text_channel,
    channel_refusal,
    find_channel_use,
    refusal,
)

SERVER_ID = 4242


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "channels.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        # Both channel columns are NOT NULL, so a configured server always holds some
        # value. These two are far from every id the tests use, so they cannot be
        # mistaken for a channel under test.
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, ?, ?)",
            (SERVER_ID, 900_000 + SERVER_ID, 910_000 + SERVER_ID),
        )
        await db.commit()
    return path


async def _season(db_path, status="SETUP"):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-03-01', ?, 1)",
            (status,),
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
                f"INSERT INTO attendance_division_config (division_id, {column}) "
                f"VALUES (?, ?) ON CONFLICT(division_id) DO UPDATE SET {column} = ?",
                (division_id, channel_id, channel_id),
            )
        elif setting == "signup":
            await db.execute(
                "INSERT INTO signup_module_config (id, signup_channel_id) "
                "VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET signup_channel_id = ?",
                (1, channel_id, channel_id),
            )
        else:
            column = {
                "interaction": "interaction_channel_id",
                "log": "log_channel_id",
                "hub": "hub_channel_id",
            }[setting]
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

    use = await find_channel_use(db_path, 500)
    assert use is not None, f"{setting} was not detected"
    assert use.setting == setting


async def test_a_free_channel_is_free(db_path):
    assert await find_channel_use(db_path, 999) is None


async def test_the_label_names_the_division_for_a_per_division_setting(db_path):
    season_id = await _season(db_path)
    division_id = await _division(db_path, season_id, "Pro")
    await _set(db_path, "results", 500, division_id=division_id)

    use = await find_channel_use(db_path, 500)

    assert use.division_name == "Pro"
    assert "**Pro**" in use.describe()


@pytest.mark.parametrize("setting", ["interaction", "log", "hub", "signup"])
async def test_a_server_setting_names_no_division(db_path, setting):
    await _set(db_path, setting, 500)

    use = await find_channel_use(db_path, 500)

    assert use.division_name is None
    assert "**" not in use.describe()


# ── The rule is server-wide ───────────────────────────────────────────────


async def test_two_divisions_may_not_share_a_channel(db_path):
    """The decision: one channel, one purpose, across the whole server."""
    season_id = await _season(db_path)
    pro = await _division(db_path, season_id, "Pro")
    await _division(db_path, season_id, "Academy")
    await _set(db_path, "results", 500, division_id=pro)

    use = await find_channel_use(db_path, 500)

    assert use == ChannelUse("results", "Pro")


async def test_one_division_may_not_use_a_channel_for_two_things(db_path):
    season_id = await _season(db_path)
    pro = await _division(db_path, season_id, "Pro")
    await _set(db_path, "calendar", 500, division_id=pro)

    use = await find_channel_use(db_path, 500)

    assert use.setting == "calendar"


# ── An archived season does not hold a channel hostage ────────────────────


@pytest.mark.parametrize("status", ["COMPLETED", "CANCELLED"])
async def test_a_finished_seasons_channel_is_free_again(db_path, status):
    """A league reusing last season's results channel is doing the ordinary thing."""
    season_id = await _season(db_path, status=status)
    division_id = await _division(db_path, season_id, "Pro")
    await _set(db_path, "results", 500, division_id=division_id)

    assert await find_channel_use(db_path, 500) is None


# ── Ignoring the setting being written ────────────────────────────────────


async def test_a_setting_does_not_block_itself(db_path):
    """Without this, changing a division's results channel would be refused by its own
    current value and the command could never be re-run."""
    season_id = await _season(db_path)
    division_id = await _division(db_path, season_id, "Pro")
    await _set(db_path, "results", 500, division_id=division_id)

    ignored = await find_channel_use(
        db_path, 500, ignore=ChannelUse("results", "Pro")
    )

    assert ignored is None


async def test_ignoring_one_setting_does_not_hide_another(db_path):
    season_id = await _season(db_path)
    pro = await _division(db_path, season_id, "Pro")
    await _set(db_path, "calendar", 500, division_id=pro)

    use = await find_channel_use(
        db_path, 500, ignore=ChannelUse("results", "Pro")
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


# ── The refusal a command sends, if any (#462) ────────────────────────────
#
# `channel_refusal` holds the rule a channel command applies before it writes, returning the
# refusal rather than sending it, so a module's command sends it in whichever state its own
# interaction is in.


def _text_channel(channel_id: int):
    return SimpleNamespace(id=channel_id, mention=f"<#{channel_id}>")


async def test_channel_refusal_passes_a_free_channel(db_path):
    season_id = await _season(db_path)
    await _division(db_path, season_id, "Pro")

    assert await channel_refusal(
        db_path, _text_channel(500), "weather", division_name="Pro"
    ) is None


async def test_channel_refusal_names_what_already_holds_the_channel(db_path):
    """A channel does one job, across the whole server."""
    season_id = await _season(db_path)
    pro = await _division(db_path, season_id, "Pro")
    await _set(db_path, "results", 500, division_id=pro)

    message = await channel_refusal(
        db_path, _text_channel(500), "weather", division_name="Pro"
    )

    assert message == refusal("<#500>", ChannelUse("results", "Pro"), same_setting=False)


async def test_channel_refusal_says_the_setting_already_holds_it_in_its_own_words(db_path):
    """Re-running a command with the value it already holds is refused, and is not a clash."""
    season_id = await _season(db_path)
    pro = await _division(db_path, season_id, "Pro")
    await _set(db_path, "weather", 500, division_id=pro)

    message = await channel_refusal(
        db_path, _text_channel(500), "weather", division_name="Pro"
    )

    assert message == refusal("<#500>", ChannelUse("weather", "Pro"), same_setting=True)


async def test_channel_refusal_holds_one_divisions_setting_against_another_s(db_path):
    """The same setting in another division is a clash, not the value already held."""
    season_id = await _season(db_path)
    pro = await _division(db_path, season_id, "Pro")
    await _division(db_path, season_id, "Academy")
    await _set(db_path, "results", 500, division_id=pro)

    message = await channel_refusal(
        db_path, _text_channel(500), "results", division_name="Academy"
    )

    assert message == refusal("<#500>", ChannelUse("results", "Pro"), same_setting=False)


# ── Core's own channel commands send it in the interaction's state ─────────
#
# `SeasonCog._refuse_channel_in_use` sends `channel_refusal`'s text for the channel commands core
# keeps. Both answer before any defer today. A fresh response after a defer is a 404, so the
# refusal asks which state the interaction is in rather than assume, a guard kept for a later
# caller that defers first. Pinned in both directions, so the guard holds before any caller
# comes to need it.


def _season_cog(db_path):
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = SimpleNamespace(db_path=db_path)
    return cog


def _interaction(*, done: bool):
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=done)
    interaction.followup.send = AsyncMock()
    return interaction


async def test_a_refusal_before_a_defer_answers_the_interaction(db_path):
    season_id = await _season(db_path)
    pro = await _division(db_path, season_id, "Pro")
    await _set(db_path, "results", 500, division_id=pro)
    interaction = _interaction(done=False)

    refused = await _season_cog(db_path)._refuse_channel_in_use(
        interaction, _text_channel(500), "lineup", division_name="Pro"
    )

    assert refused is True
    interaction.response.send_message.assert_awaited_once_with(
        refusal("<#500>", ChannelUse("results", "Pro"), same_setting=False), ephemeral=True
    )
    interaction.followup.send.assert_not_awaited()


async def test_a_refusal_after_a_defer_follows_up_instead(db_path):
    season_id = await _season(db_path)
    pro = await _division(db_path, season_id, "Pro")
    await _set(db_path, "results", 500, division_id=pro)
    interaction = _interaction(done=True)

    refused = await _season_cog(db_path)._refuse_channel_in_use(
        interaction, _text_channel(500), "lineup", division_name="Pro"
    )

    assert refused is True
    interaction.followup.send.assert_awaited_once_with(
        refusal("<#500>", ChannelUse("results", "Pro"), same_setting=False), ephemeral=True
    )
    interaction.response.send_message.assert_not_awaited()


async def test_a_free_channel_is_not_refused(db_path):
    season_id = await _season(db_path)
    await _division(db_path, season_id, "Pro")
    interaction = _interaction(done=False)

    refused = await _season_cog(db_path)._refuse_channel_in_use(
        interaction, _text_channel(500), "lineup", division_name="Pro"
    )

    assert refused is False
    interaction.response.send_message.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


# ── The hub (issue #279) ──────────────────────────────────────────────────


async def test_the_hub_refuses_a_channel_a_division_posts_to(db_path):
    """A panel among results would be pushed out of sight by the next round's posts."""
    season_id = await _season(db_path)
    division_id = await _division(db_path, season_id, "Pro")
    await _set(db_path, "results", 600, division_id=division_id)

    use = await find_channel_use(db_path, 600, ignore=ChannelUse("hub"))

    assert use == ChannelUse("results", "Pro")


async def test_a_division_refuses_the_hub_channel(db_path):
    """The rule holds both ways: the hub's channel is held for the hub alone."""
    await _set(db_path, "hub", 601)

    use = await find_channel_use(db_path, 601, ignore=ChannelUse("standings", "Pro"))

    assert use == ChannelUse("hub")
    assert use.describe() == "hub channel"


# ---------------------------------------------------------------------------
# A configured channel is a text channel (#228)
# ---------------------------------------------------------------------------

SRC = Path(__file__).resolve().parents[2] / "src"


def test_as_text_channel_hands_back_the_channel_it_was_given():
    """A narrowing for the type check alone: the same object, and None stays None."""
    channel = MagicMock()
    assert as_text_channel(channel) is channel
    assert as_text_channel(None) is None


def test_every_command_that_sets_a_channel_takes_a_text_channel():
    """What makes `as_text_channel` true: a channel is only ever set as a text channel.

    A command taking any other kind of channel would store an id that `as_text_channel` then
    calls a text channel, and the check would believe it.
    """
    offenders = sorted(
        f"{path.relative_to(SRC).as_posix()}:{arg.lineno} {arg.arg}: {ast.unparse(arg.annotation)}"
        for path in sorted(SRC.glob("leaguebot/*/cogs/*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for arg in node.args.args + node.args.kwonlyargs
        if arg.annotation is not None
        and "Channel" in ast.unparse(arg.annotation)
        and ast.unparse(arg.annotation).replace(" | None", "") != "discord.TextChannel"
    )
    assert offenders == []


def test_the_bot_makes_no_channel_but_a_text_channel():
    """The other half: a channel the bot creates, and later finds by its id, is a text channel."""
    makes_other = re.compile(r"\.create_(voice|stage|forum|category)(_channel)?\(")
    offenders = sorted(
        f"{path.relative_to(SRC).as_posix()}:{number}"
        for path in SRC.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if makes_other.search(line)
    )
    assert offenders == []
