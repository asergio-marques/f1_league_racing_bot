"""The test roster changes only while the season is in Placements (issue #220).

Fake drivers are seated, removed and cleared where real drivers are placed: in Placements.
Test mode replicates the live flow rather than inventing one of its own. Listing the roster
stays free.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.test_mode_cog import TestModeCog as _Cog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.season import SeasonStage, status_of_stage
from tests.support.undecorate import undecorate

SERVER_ID = 22030


async def _cog(tmp_path, stage: SeasonStage | None) -> _Cog:
    path = str(tmp_path / "roster_stage.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) VALUES (?, 1, 2, 3, 1)",
            (SERVER_ID,),
        )
        if stage is not None:
            await db.execute(
                "INSERT INTO seasons (start_date, status, season_number, stage) "
                "VALUES ('2026-09-17', ?, 1, ?)",
                (status_of_stage(stage).value, stage.value),
            )
        await db.commit()
    cog = _Cog.__new__(_Cog)
    cog.bot = MagicMock()
    cog.bot.db_path = path
    cog.bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(test_mode_active=True)
    )
    return cog


def _interaction():
    """Nothing has answered it yet, so a refusal goes out as its response."""
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    return interaction


async def test_the_roster_may_change_in_placements(tmp_path):
    cog = await _cog(tmp_path, SeasonStage.PLACEMENTS)
    interaction = _interaction()

    assert await cog._refuse_roster_change_outside_placements(interaction) is False
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    "stage", [None, SeasonStage.CONFIGURATION, SeasonStage.ONGOING, SeasonStage.COMPLETED]
)
async def test_the_roster_may_not_change_outside_placements(tmp_path, stage):
    cog = await _cog(tmp_path, stage)
    interaction = _interaction()

    assert await cog._refuse_roster_change_outside_placements(interaction) is True
    assert "only be changed while the season is in placements" in (
        interaction.response.send_message.await_args.args[0]
    )


# ── Every refusal is recorded (#442) ──────────────────────────────────────


def _recording(cog, interaction, command: str):
    """Give the refusal a log channel to reach and a member and a command to name."""
    cog.bot.output_router.post_log = AsyncMock()
    interaction.client = cog.bot
    interaction.user.id = 77
    interaction.user.display_name = "Admin"
    interaction.command.qualified_name = command
    return [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]


def _lines(cog) -> list[str]:
    return [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]


@pytest.mark.parametrize("record", [True, False], ids=["recorded", "not-recorded"])
async def test_the_placements_check_logs_its_refusal_unless_record_is_false(tmp_path, record):
    """The check answers the member either way. With `record` left at its default it writes
    one line in the standard refusal form; with `record=False` it writes none."""
    cog = await _cog(tmp_path, SeasonStage.ONGOING)
    interaction = _interaction()
    _recording(cog, interaction, "test-mode roster add")

    refused = await cog._refuse_roster_change_outside_placements(interaction, record=record)

    assert refused is True
    assert "only be changed while the season is in placements" in (
        interaction.response.send_message.await_args.args[0]
    )
    if record:
        [line] = _lines(cog)
        assert line.startswith("⛔ ")
        assert "/test-mode roster add" in line
        assert "refused for Admin (<@77>)" in line
    else:
        assert _lines(cog) == []


async def _placements_approved(league) -> None:
    await league.write("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING'")


async def _test_mode_off(league) -> None:
    await league.set_test_mode(False)


#: Every refusal the cog makes of a roster change before it is asked of the queue: the command,
#: its arguments, what is done to `season_league`'s league first, and a fragment of the reply.
#: Those `/test-mode roster remove` and `clear` make, and a nationality refused while switched
#: off, are tested with their commands in `test_test_mode_roster_remove.py`,
#: `test_test_mode_roster_clear.py` and `test_test_mode_roster_add.py`.
ROSTER_REFUSALS = [
    pytest.param("roster_add", ("Mock", "McLaren", "Am"), _placements_approved,
                 "only be changed while the season is in placements", id="add-outside-placements"),
    pytest.param("roster_add_bulk", (), _placements_approved,
                 "only be changed while the season is in placements",
                 id="add-bulk-outside-placements"),
    pytest.param("roster_add", ("Mock", "McLaren", "Am"), _test_mode_off,
                 "only available when test mode is enabled", id="add-outside-test-mode"),
    pytest.param("roster_add", ("Mock", "Nobody", "Am"), None, "⛔",
                 id="add-refused-by-the-roster"),
]


@pytest.mark.parametrize("command, args, meanwhile, said_", ROSTER_REFUSALS)
async def test_every_test_roster_refusal_reaches_the_log_channel(
    tmp_path, command, args, meanwhile, said_
):
    """On the bot the builder makes, `season_league`'s season 3 in Placements under test mode;
    then the season's placements are approved (Ongoing), or test mode is switched off, or
    neither. Admin (77) runs `/test-mode roster add` of Mock to McLaren in Am, or to Nobody, a
    team Am does not hold, or `/test-mode roster add-bulk`. Each is refused at once, seen by Admin
    alone and answered as before; the import form is not opened, nothing is queued, no fake
    driver is created, and the log channel takes one line in the standard form, naming the
    command and Admin."""
    from leaguebot.core.cogs import test_mode_cog
    from tests.support.change_queue import change_rows, member
    from tests.support.season_league import season_league
    from tests.support.settings_league import interaction as member_asks
    from tests.support.settings_league import said, unruled

    league = await season_league(tmp_path, test_mode=True)
    if meanwhile is not None:
        await meanwhile(league)
    cog = test_mode_cog.TestModeCog.__new__(test_mode_cog.TestModeCog)
    cog.bot = league.bot
    name = "test-mode roster " + command.removeprefix("roster_").replace("_", "-")
    interaction = member_asks(league.bot, name, user=member(77, "Admin", "Admin#0001"))
    interaction.response.send_modal = AsyncMock()

    await undecorate(getattr(test_mode_cog.TestModeCog, command))(cog, interaction, *args)

    [reply] = said(interaction)
    assert said_ in reply
    [sent] = interaction.response.send_message.await_args_list
    assert sent.kwargs.get("ephemeral") is True
    interaction.response.send_modal.assert_not_awaited()
    assert await change_rows(league.db_path) == []
    assert await league.rows("SELECT 1 FROM driver_profiles WHERE is_test_driver = 1") == []
    assert [unruled(str(line)) for line in league.bot.log_channel.sent] == [
        f"⛔ `/{name}` refused for Admin (`<@77>`) — {reply[2:]}"
    ]
