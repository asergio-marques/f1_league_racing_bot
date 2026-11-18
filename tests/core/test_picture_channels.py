"""Switching a picture on checks the channels it posts to (#570, slice 3).

An output switched to a picture posts a file, so every channel it posts to must take one, and
whatever else its use needs. Core answers its own channels for the two outputs it posts (each
division's lineup and calendar channel, and the bot command channel a season's review posts them
in), each module its own; the check, run before the switch is saved, judges each channel once on
Discord and gathers every one the bot cannot use or that is no longer on the server. Where it
cannot tell, it ends on a fault and nothing is saved.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.models.channel_needs import ATTACH, EMBED, SEND, VIEW
from leaguebot.core.services.seasons.approval_checks import Presence, PresenceState
from leaguebot.core.services.settings.channel_registry_service import ChannelUse
from leaguebot.core.services.settings.config_service import ConfigService
from leaguebot.framework.db.database import get_connection, run_migrations
from leaguebot.framework.models.change import StepFailedOnDiscord
from leaguebot.framework.services.queue.change_types import StepContext
from leaguebot.framework.services.queue.setting_change import HALTED
from leaguebot.framework.utils.hooks import AwaitedHook
from tests.support.channel_faults import answer_attachments, league_server

SERVER_ID = 4242
COMMAND_CHANNEL = 90

#: The divisions of the live season the check judges, by id: Pro with both channels set, Am with
#: its lineup channel alone.
PRO, AM = 2, 3


async def _league(tmp_path, status: str = "ACTIVE") -> str:
    """A league whose bot command channel is 90, with a completed season's division Old (lineup
    31, calendar 32) and a live season in *status* holding Pro (lineup 11, calendar 12), Am
    (lineup 21, no calendar) and Dropped, cancelled (lineup 41, calendar 42)."""
    db_path = str(tmp_path / "pictures.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id, "
            "log_channel_id) VALUES (?, 1, ?, 91)",
            (SERVER_ID, COMMAND_CHANNEL),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2025-03-01', 'COMPLETED', 1), (2, '2026-03-01', ?, 2)",
            (status,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, status, tier, "
            "lineup_channel_id, calendar_channel_id) VALUES "
            "(1, 1, 'Old', 1, 'FINISHED', 1, 31, 32), "
            "(2, 2, 'Pro', 1, 'ACTIVE', 1, 11, 12), "
            "(3, 2, 'Am', 1, 'ACTIVE', 2, 21, NULL), "
            "(4, 2, 'Dropped', 1, 'CANCELLED', 3, 41, 42)"
        )
        await db.commit()
    return db_path


async def _core_answer(db_path: str, aspects: set[str]) -> list:
    """Core's answer for *aspects* switched on, Pro and Am handed, with a drawing answering that
    no picture attaches a file yet (the switch is not saved)."""
    from leaguebot.core.services.modules.picture_channels import core_picture_channels

    drawing = answer_attachments(MagicMock())
    async with get_connection(db_path) as db:
        return await core_picture_channels(
            ConfigService(db_path), drawing, db=db, divisions={PRO: "Pro", AM: "Am"},
            aspects=frozenset(aspects),
        )


async def test_core_answers_its_channels_only_for_the_lineup_and_calendar_outputs(tmp_path):
    """Lineup, Calendar and Results switched on together: core answers each handed division's
    lineup channel (Pro's 11, Am's 21) with what a lineup needs and Attach Files, Pro's calendar
    channel 12 with what a calendar needs and Attach Files, Am's unset calendar not at all, and
    the bot command channel 90 once, for both outputs, with Attach Files; nothing for Results,
    which is not core's. Lineup alone answers the command channel for the lineup only; Results
    and Standings alone answer nothing."""
    from leaguebot.core.services.modules.picture_channels import PictureChannel

    db_path = await _league(tmp_path)
    lineup_needs = (VIEW, SEND, EMBED, ATTACH)
    calendar_needs = (VIEW, SEND, ATTACH)

    def lineup(channel_id: int, division: str) -> PictureChannel:
        return PictureChannel(
            frozenset({"lineup"}), channel_id, ChannelUse("lineup", division),
            "/division lineup-channel", lineup_needs,
        )

    def command(*aspects: str) -> PictureChannel:
        return PictureChannel(
            frozenset(aspects), COMMAND_CHANNEL, ChannelUse("interaction"),
            "/bot interaction-channel", calendar_needs,
        )

    answered = await _core_answer(db_path, {"lineup", "calendar", "results"})
    assert len(answered) == 4
    assert set(answered) == {
        lineup(11, "Pro"),
        PictureChannel(
            frozenset({"calendar"}), 12, ChannelUse("calendar", "Pro"),
            "/division calendar-channel", calendar_needs,
        ),
        lineup(21, "Am"),
        command("lineup", "calendar"),
    }

    answered = await _core_answer(db_path, {"lineup"})
    assert len(answered) == 3
    assert set(answered) == {lineup(11, "Pro"), lineup(21, "Am"), command("lineup")}

    assert await _core_answer(db_path, {"results", "standings"}) == []


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.league_settings.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    return bot


def _ctx(bot, db_path: str) -> StepContext:
    return StepContext(
        bot=bot, db_path=db_path, change_id=1, kind="images.toggle",
        payload={"aspect": "lineup", "enabled": True}, what="switching the lineup to a picture",
        actor_id=None, actor_name=None, named="Tester", steps=(),
        step_name="check_picture_channels",
    )


def _check(answers: list, *, aspects: set[str], handed: list | None = None):
    """The check, its aspects *aspects* and the hook answering *answers*, recording what each
    call of the hook is handed into *handed*. The refusal lays each channel collected as
    `fault_line` words it, one to a line."""
    from leaguebot.core.services.modules.picture_channels import (
        fault_line,
        picture_channels_check,
    )

    async def aspects_of(_db, *_handed):
        return frozenset(aspects)

    async def answer(db, divisions, aspects):
        if handed is not None:
            handed.append((dict(divisions), set(aspects)))
        return list(answers)

    def refusal(collected, *_handed):
        return {
            "reply": "\n".join(fault_line(channel, presence) for channel, presence in collected),
            "reason": "channels cannot take it",
        }

    hook = AwaitedHook()
    hook.subscribe(answer)
    return picture_channels_check(aspects_of, picture_channels_read=hook, refusal=refusal)


def _answered(channel_id: int, setting: str, division: str | None, command: str, needs):
    from leaguebot.core.services.modules.picture_channels import PictureChannel

    return PictureChannel(
        frozenset({"lineup"}), channel_id, ChannelUse(setting, division), command, needs
    )


async def test_the_check_refuses_naming_what_each_channel_lacks_and_each_channel_gone(tmp_path):
    """Lineup switched on, Pro's lineup channel 11 lacking Embed Links, the bot command channel
    90 lacking Attach Files, Pro's calendar channel 12 deleted and Am's lineup channel 21 fine:
    the check halts, naming 11 with Embed Links, 90 with Attach Files and 12 as no longer on the
    server with the command that sets it, and nothing of 21. Each `fault_line` reads as the reply
    lays it. The step is named `check_picture_channels`."""
    from leaguebot.core.services.modules.picture_channels import fault_line

    db_path = await _league(tmp_path)
    bot = _bot()
    league_server(bot, unusable={11: ("embed_links",), 90: ("attach_files",)}, gone=(12,))
    lineup_needs = (VIEW, SEND, EMBED, ATTACH)
    answers = [
        _answered(11, "lineup", "Pro", "/division lineup-channel", lineup_needs),
        _answered(12, "calendar", "Pro", "/division calendar-channel", (VIEW, SEND, ATTACH)),
        _answered(21, "lineup", "Am", "/division lineup-channel", lineup_needs),
        _answered(90, "interaction", None, "/bot interaction-channel", (VIEW, SEND, ATTACH)),
    ]
    before = _check(answers, aspects={"lineup"})

    result = await before.step.run(_ctx(bot, db_path))

    assert before.step.name == "check_picture_channels"
    assert result.result == {
        HALTED: {
            "reply": (
                "<#11>, the lineup channel for **Pro**: Embed Links\n"
                "<#12>, the calendar channel for **Pro**: no longer on the server — "
                "`/division calendar-channel`\n"
                "<#90>, the bot command channel: Attach Files"
            ),
            "reason": "channels cannot take it",
        }
    }
    lacking = Presence(PresenceState.UNUSABLE, ("Attach Files", "Embed Links"))
    assert fault_line(answers[3], lacking) == (
        "<#90>, the bot command channel: Attach Files, Embed Links"
    )


@pytest.mark.parametrize("status", ["SETUP", "ACTIVE"])
async def test_cancelled_divisions_and_finished_seasons_are_passed_over(tmp_path, status):
    """The live season in setup or running holds Pro, Am and Dropped, cancelled; a completed
    season holds Old. The check hands the owners of the channels Pro and Am alone, with the
    outputs being switched on, and passes as nothing they answer fails."""
    db_path = await _league(tmp_path, status)
    bot = _bot()
    league_server(bot)
    handed: list = []
    before = _check(
        [_answered(11, "lineup", "Pro", "/division lineup-channel", (VIEW, SEND, EMBED, ATTACH))],
        aspects={"lineup", "calendar"}, handed=handed,
    )

    result = await before.step.run(_ctx(bot, db_path))

    assert handed == [({PRO: "Pro", AM: "Am"}, {"lineup", "calendar"})]
    assert HALTED not in result.result


@pytest.mark.parametrize(
    ("aspects", "answers"),
    [
        pytest.param(set(), [], id="no output switched on"),
        pytest.param({"lineup"}, [], id="no channel answered"),
    ],
)
async def test_nothing_of_discord_is_read_where_no_output_is_switched_on_or_no_channel_answers(
    tmp_path, aspects, answers
):
    """Switching an output off asks nothing of the channels; switching Lineup on where no
    channel is answered asks nothing of Discord either. Either way the check passes, finding
    nothing, and the league's server is never looked for."""
    db_path = await _league(tmp_path)
    bot = _bot()
    bot.get_guild = MagicMock(side_effect=AssertionError("Discord was read"))
    before = _check(answers, aspects=aspects)

    result = await before.step.run(_ctx(bot, db_path))

    assert result.result == {}
    bot.get_guild.assert_not_called()


@pytest.mark.parametrize("cause", ["no server to ask", "the server answers with an error"])
async def test_a_check_that_cannot_be_made_ends_on_a_fault(tmp_path, cause):
    """Lineup switched on, Pro's lineup channel 11 answered: where the league's server is out of
    the bot's reach, the check ends on a fault saying so; where Discord answers the fetch of 11
    with a server error, that error ends it. Neither is a refusal, and nothing is saved."""
    db_path = await _league(tmp_path)
    bot = _bot()
    if cause == "no server to ask":
        bot.get_guild = MagicMock(return_value=None)
        raised: tuple[type[BaseException], ...] = (StepFailedOnDiscord,)
    else:
        league_server(bot, unreadable=(11,))
        raised = (discord.HTTPException, StepFailedOnDiscord)
    before = _check(
        [_answered(11, "lineup", "Pro", "/division lineup-channel", (VIEW, SEND, EMBED, ATTACH))],
        aspects={"lineup"},
    )

    with pytest.raises(raised):
        await before.step.run(_ctx(bot, db_path))
