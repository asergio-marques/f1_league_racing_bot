"""Unit tests for forecast_cleanup_service — Feature 007, as slice 6 of #439 leaves it.

A forecast's id is saved on the connection a save hands it (`store_forecast_message_on`); the
forecasts a test-mode flush or a clean-up a day after the race deletes are planned as one
`delete_forecast` job each (`forecast_deletes`), whose running `tests/weather/
test_forecast_changes.py` covers; and the forecast graphic does not outlive its posting attempt.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.change import StepFailedOnDiscord
from tests.support.change_queue import in_a_job

#: The mentions a forecast for a division with role 1 is sent with.
ROLE_1 = discord.AllowedMentions(everyone=False, users=False, roles=[discord.Object(1)])


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

async def _make_db(tmp_path: str) -> str:
    """Run all migrations on a fresh SQLite file and return the path."""
    db_path = os.path.join(tmp_path, "test.db")
    await run_migrations(db_path)
    return db_path


async def _seed_base(db_path: str, server_id: int = 1, test_mode: int = 0) -> tuple[int, int]:
    """Insert server_config, season, division, and round.

    Returns (division_id, round_id).
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id, test_mode_active) "
            "VALUES (?, 100, 200, 300, ?)",
            (server_id, test_mode),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status) "
            "VALUES (1, '2026-01-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, forecast_channel_id, mention_role_id) "
            "VALUES (1, 1, 'Div A', 999, 555)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (1, 1, 1, 'NORMAL', 'Bahrain', '2026-06-01T14:00:00')"
        )
        await db.commit()
    return 1, 1  # division_id=1, round_id=1


def _make_mock_message(message_id: int = 12345) -> MagicMock:
    msg = MagicMock(spec=discord.Message)
    msg.id = message_id
    return msg


def _make_bot(db_path: str, test_mode_active: bool = False) -> MagicMock:
    """Build a minimal bot mock with config_service and channel mocking."""
    bot = MagicMock()
    bot.db_path = db_path

    # config_service — returns a config with test_mode_active flag
    config = MagicMock()
    config.test_mode_active = test_mode_active
    bot.config_service.get_server_config = AsyncMock(return_value=config)

    # Discord channel mock — get_channel returns a TextChannel whose
    # get_partial_message().delete() is an AsyncMock
    partial_msg = MagicMock()
    partial_msg.delete = AsyncMock()
    channel = MagicMock(spec=discord.TextChannel)
    channel.get_partial_message = MagicMock(return_value=partial_msg)
    bot.get_channel = MagicMock(return_value=channel)

    return bot


async def _get_stored_row(db_path: str, round_id: int, division_id: int, phase_number: int):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT * FROM forecast_messages "
            "WHERE round_id = ? AND division_id = ? AND phase_number = ?",
            (round_id, division_id, phase_number),
        )
        return await cursor.fetchone()


# ---------------------------------------------------------------------------
# store_forecast_message_on (T003 / SC-001)
# ---------------------------------------------------------------------------

async def _store(db_path: str, phase: int, message_id: int) -> None:
    """Record *message_id* as phase *phase*'s forecast of round 1 in Div A, on a save this opens
    and commits, as the post job's record does."""
    from datetime import datetime, timezone

    from leaguebot.weather.services.forecast_cleanup_service import store_forecast_message_on

    async with get_connection(db_path) as db:
        await store_forecast_message_on(db, 1, 1, phase, message_id,
                                        now=datetime.now(timezone.utc))
        await db.commit()


class TestStoreForecastMessage:

    async def test_inserts_row(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)

        await _store(db_path, 1, 111)

        row = await _get_stored_row(db_path, round_id=1, division_id=1, phase_number=1)
        assert row is not None
        assert row["message_id"] == 111
        assert row["phase_number"] == 1

    async def test_replace_on_duplicate(self, tmp_path):
        """INSERT OR REPLACE: re-running a phase updates the stored ID."""
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)

        await _store(db_path, 1, 111)
        await _store(db_path, 1, 222)

        row = await _get_stored_row(db_path, 1, 1, 1)
        assert row["message_id"] == 222  # updated, not duplicated

    async def test_different_phases_coexist(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)

        await _store(db_path, 1, 100)
        await _store(db_path, 2, 200)
        await _store(db_path, 3, 300)

        for phase, expected_id in ((1, 100), (2, 200), (3, 300)):
            row = await _get_stored_row(db_path, 1, 1, phase)
            assert row["message_id"] == expected_id


# ---------------------------------------------------------------------------
# forecast_deletes: the deletions a flush or a clean-up plans, one job a forecast (T013 / SC-004,
# T010 / SC-005)
# ---------------------------------------------------------------------------


async def _planned(db_path: str, **which) -> list[tuple[str, int, int, int]]:
    """The jobs `forecast_deletes` plans for *which*: each name, division, phase and message."""
    from leaguebot.weather.services.forecast_cleanup_service import forecast_deletes

    return [
        (job.name, job.payload["division_id"], job.payload["phase"],
         int(job.payload["message_id"]))
        for job in await forecast_deletes(db_path, **which)
    ]


async def _record(db_path: str, *rows: tuple[int, int, int]) -> None:
    """Record round 1's forecasts *rows*, each its division, phase and message id."""
    async with get_connection(db_path) as db:
        for division_id, phase, message_id in rows:
            await db.execute(
                "INSERT INTO forecast_messages "
                "(round_id, division_id, phase_number, message_id, posted_at) "
                "VALUES (1, ?, ?, ?, '2026-06-01T12:00:00')",
                (division_id, phase, message_id),
            )
        await db.commit()


async def _div_b(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, forecast_channel_id, mention_role_id) "
            "VALUES (2, 1, 'Div B', 888, 666)"
        )
        await db.commit()


class TestFlushPendingDeletions:

    async def test_flushes_all_stored_messages_for_server(self, tmp_path):
        """SC-004: Div A's Phase 1, 2 and 3 forecasts of round 1 (1001-1003) recorded when test
        mode is switched off: the flush plans one delete_forecast job for each, and deletes
        nothing itself."""
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path, test_mode=0)
        await _record(db_path, (1, 1, 1001), (1, 2, 1002), (1, 3, 1003))

        assert sorted(await _planned(db_path)) == [
            ("delete_forecast", 1, 1, 1001),
            ("delete_forecast", 1, 2, 1002),
            ("delete_forecast", 1, 3, 1003),
        ]
        async with get_connection(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM forecast_messages")
            assert (await cursor.fetchone())[0] == 3

    async def test_flush_empty_is_noop(self, tmp_path):
        """No forecast recorded: the flush plans nothing."""
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)

        assert await _planned(db_path) == []

    async def test_flush_pending_deletions_clears_accumulated_rows(self, tmp_path):
        """T014: Phase 1 and 2 forecasts of round 1 recorded in Div A (1001, 1002) and Div B
        (2001, 2002): the flush plans four delete_forecast jobs, one for each."""
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path, test_mode=0)
        await _div_b(db_path)
        await _record(db_path, (1, 1, 1001), (1, 2, 1002), (2, 1, 2001), (2, 2, 2002))

        planned = await _planned(db_path)

        assert sorted(planned) == [
            ("delete_forecast", 1, 1, 1001),
            ("delete_forecast", 1, 2, 1002),
            ("delete_forecast", 2, 1, 2001),
            ("delete_forecast", 2, 2, 2002),
        ]


class TestRunPostRaceCleanup:

    async def test_deletes_phase3_for_all_divisions(self, tmp_path):
        """SC-005: round 1's Phase 3 forecasts stand in Div A (301) and Div B (302): the clean-up
        a day after the race plans the deletion of both."""
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)
        await _div_b(db_path)
        await _record(db_path, (1, 3, 301), (2, 3, 302))

        planned = await _planned(db_path, round_id=1, phases=[3])

        assert sorted(planned) == [("delete_forecast", 1, 3, 301), ("delete_forecast", 2, 3, 302)]

    async def test_a_phases_filter_plans_only_the_phases_it_names(self, tmp_path):
        """Round 1's Phase 1, 2 and 3 forecasts recorded in Div A: asked for Phase 3 alone
        (`phases=[3]`), `forecast_deletes` plans the deletion of Phase 3's alone. Which phases a
        clean-up a day after the race asks for is the clean-up's own choice, pinned in
        `test_forecast_changes.py` (whichever of Phases 1 to 3 is still recorded)."""
        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)
        await _record(db_path, (1, 1, 101), (1, 2, 102), (1, 3, 103))

        assert await _planned(db_path, round_id=1, phases=[3]) == [("delete_forecast", 1, 3, 103)]


# ---------------------------------------------------------------------------
# 042 — post_phase_message: the chain across occasions (T016, T038)
#
# The mixed-manner chain of FR-046 is tested here rather than in
# test_round_lifecycle.py, which the task named: that file's harness bootstraps a results
# round for the penalty lifecycle and carries no forecast fixtures, while this one already
# runs the migrations and seeds divisions and rounds. The chain lives in
# forecast_cleanup_service, so it is tested against the real table it writes.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The forecast graphic does not outlive its posting attempt
#
# Two of the three exits from the graphic branch abandon the picture *before* any send,
# and those are the paths nothing else would catch: there is no sweeper behind this, so a
# `finally` that did not cover them would leak on every unreachable channel.
# ---------------------------------------------------------------------------

class TestTheForecastGraphicIsDiscarded:

    @staticmethod
    def _artifact(tmp_path):
        directory = Path(str(tmp_path)) / "f1bot_render_weather"
        directory.mkdir(exist_ok=True)
        png = directory / "weather_p2_template.png"
        png.write_bytes(b"\x89PNG")
        return png

    @staticmethod
    def _attachment(png):
        import discord

        return discord.File(str(png), filename="weather_2.png")

    @pytest.mark.asyncio
    async def test_it_is_gone_once_the_forecast_has_posted(self, tmp_path):
        from leaguebot.weather.services.forecast_cleanup_service import post_phase_message

        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)
        png = self._artifact(tmp_path)

        bot = _make_bot(db_path)
        bot.get_channel.return_value.send = AsyncMock(return_value=_make_mock_message(222))

        with in_a_job():
            await post_phase_message(
                bot, division_id=1, channel_id=99,
                phase_number=2, text="phase 2 forecast",
                attachment=self._attachment(png), attachment_text="<@&1>", mentions=ROLE_1,
            )

        assert not png.exists()

    @pytest.mark.asyncio
    async def test_it_is_gone_when_the_send_fails(self, tmp_path):
        """The post raises for the change queue, whose next try posts the text; the picture goes
        nowhere."""
        import discord

        from leaguebot.weather.services.forecast_cleanup_service import post_phase_message

        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)
        png = self._artifact(tmp_path)

        bot = _make_bot(db_path)
        bot.get_channel.return_value.send = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=500), "upload failed")
        )

        with pytest.raises(StepFailedOnDiscord):
            with in_a_job():
                await post_phase_message(
                    bot, division_id=1, channel_id=99,
                    phase_number=2, text="phase 2 forecast",
                    attachment=self._attachment(png), attachment_text="<@&1>", mentions=ROLE_1,
                )

        assert not png.exists()

    @pytest.mark.asyncio
    async def test_it_is_gone_when_the_channel_cannot_be_fetched(self, tmp_path):
        """An abandonment path: the picture is built, and no send is ever attempted."""
        import discord

        from leaguebot.weather.services.forecast_cleanup_service import post_phase_message

        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)
        png = self._artifact(tmp_path)

        bot = _make_bot(db_path)
        bot.get_channel.return_value = None
        bot.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "no such channel")
        )

        with pytest.raises(StepFailedOnDiscord):
            with in_a_job():
                await post_phase_message(
                    bot, division_id=1, channel_id=99,
                    phase_number=2, text="phase 2 forecast",
                    attachment=self._attachment(png), attachment_text="<@&1>", mentions=ROLE_1,
                )

        assert not png.exists(), "a picture abandoned before its send must still be removed"

    @pytest.mark.asyncio
    async def test_it_is_gone_when_the_channel_is_not_a_text_channel(self, tmp_path):
        """The second abandonment path, and the same requirement."""
        from leaguebot.weather.services.forecast_cleanup_service import post_phase_message

        db_path = await _make_db(str(tmp_path))
        await _seed_base(db_path)
        png = self._artifact(tmp_path)

        bot = _make_bot(db_path)
        bot.get_channel.return_value = MagicMock()  # not specced as a TextChannel

        with pytest.raises(StepFailedOnDiscord):
            with in_a_job():
                await post_phase_message(
                    bot, division_id=1, channel_id=99,
                    phase_number=2, text="phase 2 forecast",
                    attachment=self._attachment(png), attachment_text="<@&1>", mentions=ROLE_1,
                )

        assert not png.exists(), "a picture abandoned before its send must still be removed"


# ---------------------------------------------------------------------------
# _discord_delete: the channel fetched where it is not cached, and what a refusal does
#
# It raises where it cannot delete, for the delete job to judge.
# ---------------------------------------------------------------------------

def _refusal(cls: type[discord.HTTPException], status: int, text: str) -> discord.HTTPException:
    response = MagicMock(status=status, reason=text)
    return cls(response, text)


class TestDiscordDelete:

    async def test_a_channel_not_cached_is_fetched_and_the_message_deleted(self, tmp_path):
        from leaguebot.weather.services.forecast_cleanup_service import _discord_delete

        bot = _make_bot(await _make_db(str(tmp_path)))
        channel = bot.get_channel.return_value
        bot.get_channel.return_value = None
        bot.fetch_channel = AsyncMock(return_value=channel)

        with in_a_job():
            await _discord_delete(bot, 999, 4242)

        bot.fetch_channel.assert_awaited_once_with(999)
        channel.get_partial_message.assert_called_once_with(4242)
        channel.get_partial_message.return_value.delete.assert_awaited_once()

    async def test_a_channel_that_is_not_a_text_channel_raises_naming_it(self, tmp_path):
        """A channel that is no text channel stops the job, keeping the channel and the message in
        its result; nothing is deleted."""
        from leaguebot.weather.services.forecast_cleanup_service import _discord_delete

        bot = _make_bot(await _make_db(str(tmp_path)))
        bot.get_channel.return_value = MagicMock(spec=discord.VoiceChannel)

        with pytest.raises(StepFailedOnDiscord, match="channel 999 is not a text channel") as err:
            with in_a_job():
                await _discord_delete(bot, 999, 4242)

        assert err.value.result == {"channel_id": 999, "message_id": 4242}

    async def test_discord_s_refusal_is_raised_as_it_is(self, tmp_path):
        from leaguebot.weather.services.forecast_cleanup_service import _discord_delete

        bot = _make_bot(await _make_db(str(tmp_path)))
        refusal = _refusal(discord.Forbidden, 403, "Missing Permissions")
        bot.get_channel.return_value.get_partial_message.return_value.delete.side_effect = refusal

        with pytest.raises(discord.Forbidden) as err:
            with in_a_job():
                await _discord_delete(bot, 999, 4242)

        assert err.value is refusal


# ---------------------------------------------------------------------------
# post_phase_message sends through the standing-post handler (#441)
#
# A forecast is a standing post: its text, in parts where it is too long for one message, and its
# graphic go through `post_standing`, each with the mentions the caller states, and the ids of
# the messages sent come back in order. A text forecast refused keeps the stop notice's words.
# ---------------------------------------------------------------------------

NOT_THROUGH_THE_HANDLER = "#441: a forecast is not yet sent through the standing-post handler"


def _spy_on_post_standing(monkeypatch) -> list[tuple[tuple, dict]]:
    """Record every `post_standing` call, wherever the weather service takes it from, and let
    each through to the real handler."""
    from leaguebot.core.services import standing_post_handler
    from leaguebot.weather.services import forecast_cleanup_service

    real = standing_post_handler.post_standing
    calls: list[tuple[tuple, dict]] = []

    async def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return await real(*args, **kwargs)

    monkeypatch.setattr(standing_post_handler, "post_standing", spy)
    monkeypatch.setattr(forecast_cleanup_service, "post_standing", spy, raising=False)
    return calls


def _content_of(call: tuple[tuple, dict]):
    args, kwargs = call
    return args[1] if len(args) > 1 else kwargs.get("content")


def _sending(channel: MagicMock, *, first_id: int, refuse_from: int | None = None) -> None:
    """*channel* answers each send with a message whose id counts up from *first_id*, and
    refuses the send numbered *refuse_from* (from 1) and every one after it."""
    count = 0

    async def send(*_args, **_kwargs):
        nonlocal count
        count += 1
        if refuse_from is not None and count >= refuse_from:
            raise _refusal(discord.Forbidden, 403, "Missing Permissions")
        return _make_mock_message(first_id + count - 1)

    channel.send = AsyncMock(side_effect=send)


@pytest.mark.xfail(strict=True, reason=NOT_THROUGH_THE_HANDLER)
async def test_a_text_forecast_goes_through_the_standing_post_handler_in_parts(
    tmp_path, monkeypatch
):
    """The Phase 2 forecast of division 1, too long for one message, is posted as text to channel
    999, letting role 1 notify. Each part `chunk_message` divides it into goes through the
    standing-post handler, in order, to that channel and with role 1's mentions; the ids of the
    messages sent come back in order."""
    from leaguebot.core.services.output_router import OutputRouter
    from leaguebot.core.utils.messages import chunk_message
    from leaguebot.weather.services.forecast_cleanup_service import post_phase_message

    bot = _make_bot(await _make_db(str(tmp_path)))
    bot.output_router = OutputRouter(bot, bot.config_service)
    channel = bot.get_channel.return_value
    _sending(channel, first_id=501)
    calls = _spy_on_post_standing(monkeypatch)
    text = "\n".join(f"line {i}" * 40 for i in range(200))
    parts = chunk_message(text)
    assert len(parts) > 1, "this forecast was meant to be divided"

    with in_a_job():
        sent = await post_phase_message(bot, division_id=1, channel_id=999, phase_number=2,
                                        text=text, mentions=ROLE_1)

    assert [call[0][0] for call in calls] == [channel] * len(parts)
    assert [_content_of(call) for call in calls] == parts
    assert all(call[1]["mentions"] is ROLE_1 for call in calls)
    assert list(sent) == list(range(501, 501 + len(parts)))


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            "second-part-refused",
            marks=pytest.mark.xfail(strict=True, reason=NOT_THROUGH_THE_HANDLER),
        ),
        "channel-unreachable",
        "not-a-text-channel",
    ],
)
async def test_a_text_forecast_part_refused_keeps_the_parts_sent(tmp_path, monkeypatch, case):
    """The Phase 2 forecast is posted as text to channel 999, letting role 1 notify, and fails:
    - second-part-refused: it is two parts long and Discord refuses the second. The first went
      through the standing-post handler, and the stop is raised keeping its id and the channel
      (``new``, ``channel_id``) for the next try to remove, its reason Discord's refusal;
    - channel-unreachable: the channel is not cached and Discord says it is not found;
    - not-a-text-channel: the channel is a voice channel.
    Each raises `StepFailedOnDiscord` in the words of today's stop notice, "the forecast could not
    be posted to <#999>: {reason}"."""
    from leaguebot.core.services.output_router import OutputRouter
    from leaguebot.core.utils.messages import chunk_message
    from leaguebot.weather.services.forecast_cleanup_service import post_phase_message

    bot = _make_bot(await _make_db(str(tmp_path)))
    bot.output_router = OutputRouter(bot, bot.config_service)
    channel = bot.get_channel.return_value
    calls = _spy_on_post_standing(monkeypatch)
    text = "a" * 1500 + "\n" + "b" * 1500
    if case == "second-part-refused":
        assert len(chunk_message(text)) == 2, "this forecast was meant to be two parts"
        _sending(channel, first_id=501, refuse_from=2)
        reason = str(_refusal(discord.Forbidden, 403, "Missing Permissions"))
    elif case == "channel-unreachable":
        gone = _refusal(discord.NotFound, 404, "Unknown Channel")
        bot.get_channel.return_value = None
        bot.fetch_channel = AsyncMock(side_effect=gone)
        reason = str(gone)
    else:
        bot.get_channel.return_value = MagicMock(spec=discord.VoiceChannel)
        reason = "channel 999 is not a text channel"

    with pytest.raises(StepFailedOnDiscord) as err:
        with in_a_job():
            await post_phase_message(bot, division_id=1, channel_id=999, phase_number=2,
                                     text=text, mentions=ROLE_1)

    assert str(err.value) == f"the forecast could not be posted to <#999>: {reason}"
    if case == "second-part-refused":
        assert err.value.result == {"new": [501], "channel_id": 999}
        assert [_content_of(call) for call in calls] == chunk_message(text)


@pytest.mark.xfail(strict=True, reason=NOT_THROUGH_THE_HANDLER)
async def test_a_graphic_forecast_goes_through_the_standing_post_handler(tmp_path, monkeypatch):
    """The Phase 2 forecast of division 1 is posted as a graphic to channel 999, on a message
    carrying role 1's mention and letting role 1 notify. It goes through the standing-post
    handler once, to that channel, with the mention as its text, the graphic as its file and role
    1's mentions; the id of the message sent comes back alone."""
    from leaguebot.weather.services.forecast_cleanup_service import post_phase_message

    bot = _make_bot(await _make_db(str(tmp_path)))
    channel = bot.get_channel.return_value
    channel.send = AsyncMock(return_value=_make_mock_message(222))
    calls = _spy_on_post_standing(monkeypatch)
    png = TestTheForecastGraphicIsDiscarded._artifact(tmp_path)
    attachment = TestTheForecastGraphicIsDiscarded._attachment(png)

    with in_a_job():
        sent = await post_phase_message(
            bot, division_id=1, channel_id=999, phase_number=2, text="phase 2 forecast",
            attachment=attachment, attachment_text="<@&1>", mentions=ROLE_1,
        )

    [call] = calls
    assert call[0][0] is channel
    assert _content_of(call) == "<@&1>"
    assert call[1]["file"] is attachment
    assert call[1]["mentions"] is ROLE_1
    assert list(sent) == [222]
