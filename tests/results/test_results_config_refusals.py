"""Every refusal of the `/results config` group is recorded in the log channel (#482).

The rule is the core specification's "The record of what changed": a refusal is recorded as one
line naming the member, what was refused and why. Each case runs a `/results config` command, or
presses a button of `/results config remove`'s confirmation, in a way the bot turns away, and
expects today's reply, seen by the member alone, and exactly one standard refusal line.

The points configurations live in a real database built from the production migrations, so a
case holds whatever the command reads before it refuses; the season is the season service's
answer, since only its status decides the refusal.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.cogs.results_cog import ResultsCog, _ConfirmRemoveConfigView
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.points_config_service import (
    ConfigNotFoundError,
    create_config,
)
from tests.support.undecorate import undecorate

SERVER_ID = 10482
ACTOR_ID = 4242
OTHER_ADMIN = 5151
CONFIG = "100%"
MISSING = "75%"

_NOT_YET = "#482: the refusal is answered but not recorded in the log channel"


async def _db(tmp_path) -> str:
    """A server holding the one points configuration CONFIG, with no season built on it."""
    db_path = os.path.join(str(tmp_path), "config_refusals.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.commit()
    await create_config(db_path, CONFIG)
    return db_path


def _cog(db_path: str, *, season_status: str | None = "SETUP") -> ResultsCog:
    """The results cog, the module on, and the server's season in *season_status* (None: none)."""
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_results_enabled = AsyncMock(return_value=True)
    season = None if season_status is None else SimpleNamespace(id=1, status=season_status)
    bot.season_service.get_season_for_server = AsyncMock(return_value=season)
    bot.output_router.post_log = AsyncMock(return_value=None)
    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = bot
    return cog


def _interaction(cog: ResultsCog, command: str | None, user_id: int = ACTOR_ID, name: str = "Alex"):
    """An interaction by *name*, answering as Discord's does; *command* None for a button."""
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction = MagicMock()
    interaction.client = cog.bot
    interaction.guild_id = SERVER_ID
    if command is None:
        interaction.command = None
    else:
        interaction.command.qualified_name = command
    interaction.user.id = user_id
    interaction.user.display_name = name
    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


def _choice(session: SessionType, label: str):
    return SimpleNamespace(value=session.value, name=label)


RACE = _choice(SessionType.FEATURE_RACE, "Feature Race")
QUALIFYING = _choice(SessionType.FEATURE_QUALIFYING, "Feature Qualifying")


async def _add(cog, interaction):
    await undecorate(ResultsCog.config_add)(cog, interaction, "<@1>")


async def _add_duplicate(cog, interaction):
    await undecorate(ResultsCog.config_add)(cog, interaction, CONFIG)


async def _remove_missing(cog, interaction):
    await undecorate(ResultsCog.config_remove)(cog, interaction, MISSING)


async def _remove_gone_meanwhile(cog, interaction):
    with patch(
        "leaguebot.results.services.points_config_service.remove_config",
        new=AsyncMock(side_effect=ConfigNotFoundError(CONFIG)),
    ):
        await undecorate(ResultsCog.config_remove)(cog, interaction, CONFIG)


async def _session_missing(cog, interaction):
    await undecorate(ResultsCog.config_session)(cog, interaction, MISSING, RACE, 1, 25)


async def _fl_missing(cog, interaction):
    await undecorate(ResultsCog.config_fl)(cog, interaction, MISSING, RACE, 1)


async def _fl_qualifying(cog, interaction):
    await undecorate(ResultsCog.config_fl)(cog, interaction, CONFIG, QUALIFYING, 1)


async def _plimit_missing(cog, interaction):
    await undecorate(ResultsCog.config_fl_plimit)(cog, interaction, MISSING, RACE, 10)


async def _plimit_qualifying(cog, interaction):
    await undecorate(ResultsCog.config_fl_plimit)(cog, interaction, CONFIG, QUALIFYING, 10)


async def _append(cog, interaction):
    await undecorate(ResultsCog.config_append)(cog, interaction, CONFIG)


async def _append_missing(cog, interaction):
    await undecorate(ResultsCog.config_append)(cog, interaction, MISSING)


async def _detach(cog, interaction):
    await undecorate(ResultsCog.config_detach)(cog, interaction, CONFIG)


_MENTION_REFUSAL = (
    "A mention of a member in the configuration name would notify them wherever it is "
    "posted. Remove it, then try again."
)


def _case(case_id, command, run, reply, *, season_status="SETUP"):
    return pytest.param(
        command, run, reply, season_status,
        id=case_id,
        marks=pytest.mark.xfail(strict=True, reason=_NOT_YET),
    )


@pytest.mark.parametrize(
    ("command", "run", "reply", "season_status"),
    [
        _case("add-name", "results config add", _add, f"❌ {_MENTION_REFUSAL}"),
        _case(
            "add-duplicate", "results config add", _add_duplicate,
            f"❌ A config named **{CONFIG}** already exists on this server.",
        ),
        _case(
            "remove-not-found", "results config remove", _remove_missing,
            f"❌ Config **{MISSING}** not found.",
        ),
        _case(
            "remove-gone-meanwhile", "results config remove", _remove_gone_meanwhile,
            f"❌ Config **{CONFIG}** not found.",
        ),
        _case(
            "session-not-found", "results config session", _session_missing,
            f"❌ Config **{MISSING}** not found.",
        ),
        _case(
            "fl-not-found", "results config fl", _fl_missing,
            f"❌ Config **{MISSING}** not found.",
        ),
        _case(
            "fl-qualifying", "results config fl", _fl_qualifying,
            "❌ Fastest-lap bonus cannot be set for qualifying sessions.",
        ),
        _case(
            "fl-plimit-not-found", "results config fl-plimit", _plimit_missing,
            f"❌ Config **{MISSING}** not found.",
        ),
        _case(
            "fl-plimit-qualifying", "results config fl-plimit", _plimit_qualifying,
            "❌ Position limit cannot be set for qualifying sessions.",
        ),
        _case(
            "append-no-season", "results config append", _append,
            "❌ No season found for this server.", season_status=None,
        ),
        _case(
            "append-not-in-setup", "results config append", _append,
            "❌ Config attachment is only allowed for seasons in SETUP.", season_status="ACTIVE",
        ),
        _case(
            "append-not-found", "results config append", _append_missing,
            f"❌ Config **{MISSING}** does not exist on this server, so nothing was attached. "
            "Check the spelling, or create it with `/results config add`.",
        ),
        _case(
            "detach-no-season", "results config detach", _detach,
            "❌ No season found for this server.", season_status=None,
        ),
        _case(
            "detach-not-in-setup", "results config detach", _detach,
            "❌ Config detachment is only allowed for seasons in SETUP.", season_status="ACTIVE",
        ),
    ],
)
async def test_every_config_refusal_is_recorded(tmp_path, command, run, reply, season_status):
    """The manager Alex is refused as today, and one line records the refusal and its reason."""
    cog = _cog(await _db(tmp_path), season_status=season_status)
    interaction = _interaction(cog, command)

    await run(cog, interaction)

    interaction.followup.send.assert_awaited_once_with(reply, ephemeral=True)
    reason = reply.removeprefix("❌ ")
    cog.bot.output_router.post_log.assert_awaited_once_with(
        f"⛔ `/{command}` refused for Alex (<@{ACTOR_ID}>) — {reason}"
    )


@pytest.mark.xfail(strict=True, reason=_NOT_YET)
@pytest.mark.parametrize("button", ["confirm", "cancel"])
async def test_a_confirmation_pressed_by_another_admin_is_recorded(tmp_path, button):
    """Alex asked to remove 100% from a season in setup; another admin, Sam, presses one of
    the confirmation's buttons. Sam is told it is not their action, nothing is removed, and
    one line records the refusal naming the button, the removal it belongs to, and Sam."""
    db_path = await _db(tmp_path)
    cog = _cog(db_path)
    view = _ConfirmRemoveConfigView(cog, ACTOR_ID, CONFIG)
    interaction = _interaction(cog, None, user_id=OTHER_ADMIN, name="Sam")
    label = {"confirm": "Remove it anyway", "cancel": "Cancel"}[button]

    await getattr(type(view), button)(view, interaction, MagicMock(label=label))

    interaction.response.send_message.assert_awaited_once_with(
        "⛔ Not your action.", ephemeral=True
    )
    [line] = [c.args[0] for c in cog.bot.output_router.post_log.await_args_list]
    assert line.startswith("⛔ ")
    assert label in line
    assert "`/results config remove`" in line
    assert CONFIG in line
    assert line.endswith(f"refused for Sam (<@{OTHER_ADMIN}>) — Not your action.")
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM points_config_store WHERE config_name = ?", (CONFIG,)
        )
        assert (await cursor.fetchone())[0] == 1
