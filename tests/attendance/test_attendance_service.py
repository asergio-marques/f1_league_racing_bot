"""Unit tests for AttendanceService — T006, T012, T018."""
from __future__ import annotations

import pytest
from tests.support.switches import answer_switch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    """A migrated database: the league's server_configs row, one season, and its divisions
    10 and 11. No attendance_config row, which is how a league that has never enabled the
    module stands."""
    from leaguebot.framework.db.database import get_connection, run_migrations

    path = str(tmp_path / "test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute("INSERT INTO server_configs (server_id) VALUES (1)")
        await db.execute(
            "INSERT INTO seasons (id, start_date, status) VALUES (1, '2026-01-01', 'SETUP')"
        )
        await db.executemany(
            "INSERT INTO divisions (id, season_id, name, mention_role_id) VALUES (?, 1, ?, ?)",
            [(10, "Pro", 3010), (11, "Am", 3011)],
        )
        await db.commit()
    return path


# ---------------------------------------------------------------------------
# T006 — Lifecycle tests (enable/disable)
#
# Enabling and disabling are driven through the cog in test_module_enable_disable.py and
# test_module_disable_paths.py; what is left here is the service's own part.
# ---------------------------------------------------------------------------


async def test_attendance_switches_off_on_a_handed_connection(db_path):
    """Its flag down, its settings row and every division's check-in and attendance channels
    kept, and only each division's attendance-sheet message id cleared (a message id is not
    configuration, Principle X rule 6), all on the caller's connection and saved only when the
    caller commits; it says whether attendance was on."""
    from leaguebot.attendance.services.check_in.attendance_config_service import AttendanceService
    from leaguebot.framework.db.database import get_connection

    async with get_connection(db_path) as db:
        await db.execute("INSERT INTO attendance_config (id, module_enabled) VALUES (1, 1)")
        await db.executemany(
            "INSERT INTO attendance_division_config "
            "(division_id, attendance_channel_id, attendance_message_id) VALUES (?, ?, ?)",
            [(10, "610", "910"), (11, "611", None)],
        )
        await db.commit()
    svc = AttendanceService(db_path)

    async with get_connection(db_path) as db:
        assert await svc.switch_off_on(db) is True
        await db.rollback()
    assert (await svc.get_config()).module_enabled is True
    pro = await svc.get_division_config(10)
    assert pro is not None and pro.attendance_message_id == "910"

    async with get_connection(db_path) as db:
        assert await svc.switch_off_on(db) is True
        await db.commit()
    assert (await svc.get_config()).module_enabled is False
    pro = await svc.get_division_config(10)
    am = await svc.get_division_config(11)
    assert pro is not None and am is not None
    assert (pro.attendance_channel_id, pro.attendance_message_id) == ("610", None)
    assert am.attendance_channel_id == "611"

    async with get_connection(db_path) as db:
        assert await svc.switch_off_on(db) is False
        await db.commit()


# ---------------------------------------------------------------------------
# T012 — Division config tests
# ---------------------------------------------------------------------------


class TestSetAttendanceChannel:
    async def test_set_attendance_channel(self, db_path):
        """Division 10 (Pro) has no channels set. Its attendance channel is set to 888 on a
        connection, then committed: the division's config reads it back as the text "888", its
        RSVP channel still unset, and the setter gives back no channel replaced."""
        from leaguebot.attendance.services.check_in.attendance_config_service import AttendanceService
        from leaguebot.attendance.services.settings.division_channels import set_attendance_channel
        from leaguebot.framework.db.database import get_connection

        async with get_connection(db_path) as db:
            assert await set_attendance_channel(db, 10, 888) is None
            await db.commit()
        cfg = await AttendanceService(db_path).get_division_config(10)
        assert cfg is not None
        assert cfg.attendance_channel_id == "888"
        assert cfg.rsvp_channel_id is None


# ---------------------------------------------------------------------------
# T018 — Timing invariant tests
# ---------------------------------------------------------------------------


class TestTimingInvariantValid:
    async def test_timing_invariant_valid(self):
        from leaguebot.attendance.services.check_in.attendance_config_service import (
            validate_timing_invariant,
        )
        # notice_days=5, 5*24=120 > last_notice=24 > deadline=2 → valid
        result = validate_timing_invariant(5, 24, 2)
        assert result is None


class TestTimingInvariantNoticeTooSmall:
    async def test_timing_invariant_notice_too_small(self):
        from leaguebot.attendance.services.check_in.attendance_config_service import (
            validate_timing_invariant,
        )
        # 1*24=24, last_notice_hours=24 → 24 <= 24 → violation
        result = validate_timing_invariant(1, 24, 2)
        assert result is not None
        assert "rsvp_notice_days" in result


class TestTimingInvariantLastZeroSentinelValid:
    async def test_timing_invariant_last_zero_sentinel_valid(self):
        from leaguebot.attendance.services.check_in.attendance_config_service import (
            validate_timing_invariant,
        )
        # last_notice_hours=0 is sentinel (no last-notice ping); deadline check skipped
        result = validate_timing_invariant(5, 0, 2)
        assert result is None


class TestTimingInvariantLastNotAboveDeadlineRejected:
    @pytest.mark.parametrize(
        "notice_days, last, deadline",
        [(5, 4, 4), (5, 6, 6), (5, 1, 2)],
        ids=["last-4-equals-deadline-4", "last-6-equals-deadline-6", "deadline-2-above-last-1"],
    )
    async def test_a_last_notice_not_above_the_deadline_is_rejected(
        self, notice_days, last, deadline
    ):
        """The last notice must fall strictly before the deadline: notice_days=5 (120h) clears
        the last notice, but last <= deadline is rejected, whether the two are equal or the
        deadline is the larger."""
        from leaguebot.attendance.services.check_in.attendance_config_service import (
            validate_timing_invariant,
        )
        result = validate_timing_invariant(notice_days, last, deadline)
        assert result is not None
        assert "rsvp_last_notice_hours" in result


# ---------------------------------------------------------------------------
# recalculation_faults — the attendance half of an amendment's pre-flight check (#187)
#
# The approval of a mid-season amendment recalculates attendance as part of its cascade.
# A recalculation that could not be posted used to be swallowed under the same false
# success as the reposting, so the channels it needs are established before anything is
# written and the approval is refused entire if they are not there.
# ---------------------------------------------------------------------------

import discord  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402


async def _seed_attendance_season(
    tmp_path,
    *,
    attendance_channel_id=601,
    penalty_channel_id=602,
    autosack=3,
    autoreserve=None,
):
    """A season with one division, seeded against the real schema.

    Returns ``(db_path, season_id)``.
    """
    from leaguebot.framework.db.database import run_migrations, get_connection

    path = str(tmp_path / "recalc_faults.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        await db.execute(
            "INSERT INTO attendance_config (id, autoreserve_threshold, "
            "autosack_threshold) VALUES (1, ?, ?)",
            (autoreserve, autosack),
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) "
            "VALUES (?, 'Alpha', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, "
            "attendance_channel_id) VALUES (?, ?)",
            (division_id, attendance_channel_id),
        )
        await db.execute(
            "INSERT INTO division_results_config (division_id, penalty_channel_id) "
            "VALUES (?, ?)",
            (division_id, penalty_channel_id),
        )
        await db.commit()
    return path, season_id


def _attendance_guild(present=(601, 602)):
    """A guild holding *present* as text channels the bot may post in.

    The bot's own member comes from ``get_member`` rather than ``guild.me``, which is what
    the pre-flight reads (#187).
    """
    guild = MagicMock()
    guild.id = 1
    guild.get_member = lambda _user_id: object()

    def get_channel(channel_id):
        if channel_id not in present:
            return None
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
        channel.permissions_for.return_value = MagicMock()
        return channel

    guild.get_channel = get_channel
    return guild


def _plain_bot():
    bot = MagicMock()
    bot.user.id = 4242
    # The image module's drawing: no aspect is switched on, so no channel needs Attach Files.
    bot.image_drawing.aspect_attaches_files = AsyncMock(return_value=False)
    return bot


@pytest.mark.asyncio
async def test_recalculation_faults_passes_a_healthy_division(tmp_path):
    from leaguebot.attendance.services.scoring.attendance_recompute import recalculation_faults

    path, season_id = await _seed_attendance_season(tmp_path)

    assert await recalculation_faults(
        path, season_id, _attendance_guild(), (bot := _plain_bot()), drawing=bot.image_drawing
    ) == []


@pytest.mark.asyncio
async def test_recalculation_faults_names_a_deleted_attendance_channel(tmp_path):
    from leaguebot.attendance.services.scoring.attendance_recompute import recalculation_faults

    path, season_id = await _seed_attendance_season(tmp_path)

    faults = await recalculation_faults(
        path, season_id, _attendance_guild(present=(602,)), (bot := _plain_bot()), drawing=bot.image_drawing
    )

    assert len(faults) == 1, faults
    assert "attendance channel" in faults[0]
    assert "Alpha" in faults[0]


@pytest.mark.asyncio
async def test_recalculation_faults_names_a_cancelled_division_s_channels_only_when_asked(tmp_path):
    """Division Alpha, since cancelled, has lost its attendance channel. Asked with
    `cancelled_too`, as a points approval asks, the channel is named; asked as `/attendance
    sync` asks, with nothing, a cancelled division is passed over as it is today."""
    from leaguebot.attendance.services.scoring.attendance_recompute import recalculation_faults
    from leaguebot.framework.db.database import get_connection

    path, season_id = await _seed_attendance_season(tmp_path)
    async with get_connection(path) as db:
        await db.execute("UPDATE divisions SET status = 'CANCELLED'")
        await db.commit()

    asked = await recalculation_faults(
        path, season_id, _attendance_guild(present=(602,)), (bot := _plain_bot()), drawing=bot.image_drawing, cancelled_too=True
    )
    default = await recalculation_faults(
        path, season_id, _attendance_guild(present=(602,)), (bot := _plain_bot()), drawing=bot.image_drawing
    )

    assert len(asked) == 1, asked
    assert "attendance channel" in asked[0]
    assert "Alpha" in asked[0]
    assert default == []


@pytest.mark.asyncio
async def test_recalculation_faults_names_a_deleted_verdicts_channel(tmp_path):
    """Sanctions are announced as verdicts, so that channel is part of the cascade."""
    from leaguebot.attendance.services.scoring.attendance_recompute import recalculation_faults

    path, season_id = await _seed_attendance_season(tmp_path)

    faults = await recalculation_faults(
        path, season_id, _attendance_guild(present=(601,)), (bot := _plain_bot()), drawing=bot.image_drawing
    )

    assert len(faults) == 1, faults
    assert "verdicts channel" in faults[0]


@pytest.mark.asyncio
async def test_recalculation_faults_ignores_the_verdicts_channel_without_thresholds(tmp_path):
    """`owed_sanctions` returns at once when both are unset, so a league
    using neither must not be refused for a channel it will never post to (#187)."""
    from leaguebot.attendance.services.scoring.attendance_recompute import recalculation_faults

    path, season_id = await _seed_attendance_season(
        tmp_path, autosack=None, autoreserve=None
    )

    faults = await recalculation_faults(
        path, season_id, _attendance_guild(present=(601,)), (bot := _plain_bot()), drawing=bot.image_drawing
    )

    assert faults == []


@pytest.mark.asyncio
async def test_recalculation_faults_ignores_an_unconfigured_attendance_channel(tmp_path):
    from leaguebot.attendance.services.scoring.attendance_recompute import recalculation_faults

    path, season_id = await _seed_attendance_season(
        tmp_path, attendance_channel_id=None, autosack=None, autoreserve=None
    )

    faults = await recalculation_faults(
        path, season_id, _attendance_guild(present=()), (bot := _plain_bot()), drawing=bot.image_drawing
    )

    assert faults == []


@pytest.mark.asyncio
async def test_recalculation_faults_says_nothing_about_an_absent_guild(tmp_path):
    """The results half reports that already; saying it twice would have a manager
    repairing one thing from two lines."""
    from leaguebot.attendance.services.scoring.attendance_recompute import recalculation_faults

    path, season_id = await _seed_attendance_season(tmp_path)

    assert await recalculation_faults(path, season_id, None, (bot := _plain_bot()), drawing=bot.image_drawing) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("aspect, autosack, label", [
    pytest.param("attendance", None, "attendance channel", id="attendance sheet"),
    pytest.param("verdict_banner", 3, "verdicts channel", id="verdict banner"),
    pytest.param("verdicts", 3, "verdicts channel", id="verdict picture"),
])
async def test_recalculation_faults_wants_attach_files_only_with_graphics(
    tmp_path, aspect, autosack, label
):
    """Division Alpha's attendance channel 601 and verdicts channel 602 are both there, the bot
    lacking Attach Files in each. With no picture on, the recalculation's check names nothing.
    With the pictures of *aspect* on, it names the one channel those pictures are posted in,
    *label*, as lacking Attach Files. The verdicts channel is asked only where a sanction could
    fall (an autosack threshold of 3), the attendance channel always."""
    from leaguebot.attendance.services.scoring.attendance_recompute import recalculation_faults

    path, season_id = await _seed_attendance_season(
        tmp_path, autosack=autosack, autoreserve=None
    )
    guild = _attendance_guild()
    denied = MagicMock()
    denied.attach_files = False
    guild.get_channel(601).permissions_for.return_value = denied

    def get_channel(channel_id):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
        channel.permissions_for.return_value = denied
        return channel

    guild.get_channel = get_channel

    assert await recalculation_faults(path, season_id, guild, (bot := _plain_bot()), drawing=bot.image_drawing) == []

    with_graphics = _plain_bot()
    # The image module's drawing: the module on and *aspect* on.
    with_graphics.image_drawing.aspect_attaches_files = AsyncMock(
        side_effect=lambda asked: asked == aspect
    )
    faults = await recalculation_faults(
        path, season_id, guild, with_graphics, drawing=with_graphics.image_drawing
    )

    assert len(faults) == 1, faults
    assert "Attach Files" in faults[0]
    assert label in faults[0]


@pytest.mark.xfail(
    strict=True,
    reason="#449: the check-in call's channel does not yet need Read Message History",
)
async def test_attendance_names_each_division_s_missing_or_deleted_channels(db_path):
    """Pro's RSVP channel 130 is gone from the server and its attendance channel 131 is there;
    Am has neither. Attendance on names Pro's RSVP channel as gone and both of Am's as missing,
    RSVP first, each with the command that sets it; with no server, only Am's two. Attendance off
    names nothing.

    On a server where the bot lacks Embed Links in both of Pro's channels, the RSVP channel, where
    the check-in call is posted as an embed, is named with what it lacks; the attendance channel,
    which needs no embed, is not. Where the bot lacks Attach Files in the RSVP channel and Read
    Message History in the attendance channel, which a repost of the sheet needs, the attendance
    channel is named for it; the RSVP channel is named for Attach Files only with check-in pictures
    on. Where the bot holds View Channel, Send Messages and Embed Links in the RSVP channel but not
    Read Message History, which it reads the check-in call back with to redraw it after an answer
    and to take its buttons off at the deadline, the RSVP channel is named for it."""
    from unittest.mock import AsyncMock, MagicMock

    from leaguebot.attendance.services.check_in.attendance_config_service import AttendanceService
    from leaguebot.attendance.services.hooks.attendance_hooks import division_channel_faults
    from leaguebot.framework.db.database import get_connection
    from tests.support.channel_faults import answer_attachments, server_holding

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, rsvp_channel_id, "
            "attendance_channel_id) VALUES (10, '130', '131')"
        )
        await db.commit()

    async def faults(on: bool, guild, *pictures: str) -> dict[int, list[str]]:
        modules = MagicMock()
        answer_switch(modules, "attendance", on)
        found = await division_channel_faults(
            AttendanceService(db_path), modules,
            drawing=answer_attachments(MagicMock(), *pictures),
            divisions={10: "Pro", 11: "Am"}, guild=guild,
        )
        return {division_id: lines for division_id, lines in found.items() if lines}

    am = {11: [
        "**Am** has no RSVP channel — `/attendance channel rsvp`.",
        "**Am** has no attendance channel — `/attendance channel attendance`.",
    ]}
    assert await faults(True, server_holding(gone=(130,))) == {
        10: ["**Pro**'s RSVP channel is no longer on the server — `/attendance channel rsvp`."],
        **am,
    }
    assert await faults(True, None) == am
    assert await faults(False, server_holding(gone=(130,))) == {}
    assert await faults(True, server_holding(unusable={130: ("embed_links",), 131: ("embed_links",)})) == {
        10: [
            "**Pro**'s RSVP channel is missing permissions the bot needs there: Embed Links — "
            "give the bot them, or `/attendance channel rsvp`."
        ],
        **am,
    }
    lacking = server_holding(unusable={130: ("attach_files",), 131: ("read_message_history",)})
    no_history = (
        "**Pro**'s attendance channel is missing permissions the bot needs there: "
        "Read Message History — give the bot them, or `/attendance channel attendance`."
    )
    assert await faults(True, lacking) == {10: [no_history], **am}
    assert await faults(True, lacking, "rsvp") == {
        10: [
            "**Pro**'s RSVP channel is missing permissions the bot needs there: Attach Files — "
            "give the bot them, or `/attendance channel rsvp`.",
            no_history,
        ],
        **am,
    }
    assert await faults(True, server_holding(unusable={130: ("read_message_history",)})) == {
        10: [
            "**Pro**'s RSVP channel is missing permissions the bot needs there: "
            "Read Message History — give the bot them, or `/attendance channel rsvp`."
        ],
        **am,
    }
