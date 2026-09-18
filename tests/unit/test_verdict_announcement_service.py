"""Tests for verdict_announcement_service — translate_penalty and post helpers."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from services.verdict_announcement_service import (
    translate_penalty,
    post_penalty_announcements,
    post_appeal_announcements,
)


# ---------------------------------------------------------------------------
# translate_penalty
# ---------------------------------------------------------------------------

class TestTranslatePenalty:
    def test_positive_with_sign_and_s(self):
        assert translate_penalty("+5s") == "5 seconds added"

    def test_positive_no_sign_with_s(self):
        assert translate_penalty("5s") == "5 seconds added"

    def test_positive_no_sign_no_s(self):
        assert translate_penalty("5") == "5 seconds added"

    def test_negative_with_s(self):
        assert translate_penalty("-3s") == "3 seconds removed"

    def test_negative_no_s(self):
        assert translate_penalty("-10") == "10 seconds removed"

    def test_dsq_uppercase(self):
        assert translate_penalty("DSQ") == "Disqualified"

    def test_dsq_lowercase(self):
        assert translate_penalty("dsq") == "Disqualified"

    def test_dsq_mixed_case(self):
        assert translate_penalty("Dsq") == "Disqualified"

    def test_dsq_with_whitespace(self):
        assert translate_penalty("  DSQ  ") == "Disqualified"

    def test_large_penalty(self):
        assert translate_penalty("+30s") == "30 seconds added"

    def test_single_second_removed(self):
        assert translate_penalty("-1s") == "1 seconds removed"


# ---------------------------------------------------------------------------
# post_penalty_announcements
# ---------------------------------------------------------------------------

def _make_state(db_path: str, round_id: int = 1) -> MagicMock:
    state = MagicMock()
    state.db_path = db_path
    state.round_id = round_id
    state.division_id = 1
    return state


@pytest.mark.asyncio
async def test_post_penalty_announcements_empty_list_noop():
    """Empty applied_penalties list should not call any DB or Discord APIs."""
    import asyncio
    bot = MagicMock()
    state = _make_state("irrelevant.db")
    # Should complete without raising even though db_path is fake
    await post_penalty_announcements(bot, state, [])
    bot.get_channel.assert_not_called()


@pytest.mark.asyncio
async def test_post_penalty_announcements_skips_when_no_channel_configured(tmp_path):
    """If penalty_channel_id is NULL in DB, skip silently and don't call bot.get_channel."""
    from db.database import run_migrations, get_connection

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1001, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1001, '2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Division A', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
            "VALUES (?, 1, 'STANDARD', 'AWAITING_REPORT_VERDICTS', '2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        # division_results_config with no penalty_channel_id
        await db.execute(
            "INSERT INTO division_results_config (division_id) VALUES (?)",
            (division_id,),
        )
        await db.commit()

    bot = MagicMock()
    state = _make_state(db_path, round_id=round_id)

    fake_record = {"driver_session_result_id": 99, "penalty_type": "TIME",
                   "time_seconds": 5, "description": "test", "justification": "j"}
    await post_penalty_announcements(bot, state, [fake_record])
    # Should skip without calling bot.get_channel
    bot.get_channel.assert_not_called()


@pytest.mark.asyncio
async def test_post_penalty_announcements_skips_when_channel_inaccessible(tmp_path):
    """If bot.get_channel returns None (inaccessible), skip silently."""
    from db.database import run_migrations, get_connection

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1001, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1001, '2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Division A', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
            "VALUES (?, 1, 'STANDARD', 'AWAITING_REPORT_VERDICTS', '2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO division_results_config (division_id, penalty_channel_id) VALUES (?, 55555)",
            (division_id,),
        )
        await db.commit()

    bot = MagicMock()
    bot.get_channel.return_value = None  # channel not in cache / inaccessible

    state = _make_state(db_path, round_id=round_id)
    fake_record = {"driver_session_result_id": 99, "penalty_type": "TIME",
                   "time_seconds": 5, "description": "test", "justification": "j"}

    # Should not raise
    await post_penalty_announcements(bot, state, [fake_record])
    bot.get_channel.assert_called_once_with(55555)


@pytest.mark.asyncio
async def test_post_appeal_announcements_empty_list_noop():
    """Empty applied_corrections list should not call any DB or Discord APIs."""
    bot = MagicMock()
    state = _make_state("irrelevant.db")
    await post_appeal_announcements(bot, state, [])
    bot.get_channel.assert_not_called()


# ---------------------------------------------------------------------------
# The name a verdict graphic draws a driver under (#141)
# ---------------------------------------------------------------------------
#
# These drive the `post_*` entry points with a real driver in the database, which nothing
# did before: the older tests above stop at an unconfigured or inaccessible channel, and
# tests/unit/test_image_verdicts_post.py calls `_send_verdict` with the name already
# resolved. The path in between resolved every real driver to their raw user id.

SERVER_ID = 1001
DRIVER_ID = 4815162342


class _Member:
    """A guild member, as `_driver_names` reads one."""

    def __init__(self, display_name: str) -> None:
        self.display_name = display_name


class _Guild:
    """A guild `_driver_names` can read, cache miss and all.

    `fetch_member` is implemented rather than left off: absent it, the reader raises
    AttributeError, `_graphic_name` falls back to the old behaviour and the test passes
    for the wrong reason — the very thing these tests exist to catch.
    """

    def __init__(self, member: "_Member | None" = None) -> None:
        self.id = SERVER_ID
        self._member = member

    def get_member(self, _user_id):
        return self._member

    async def fetch_member(self, _user_id):
        import discord

        raise discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"), "Unknown Member"
        )

    def get_role(self, _role_id):
        return None


class _Channel:
    def __init__(self, member: "_Member | None" = None) -> None:
        self.sent: list[tuple] = []
        self.guild = _Guild(member)

    async def send(self, content=None, *, file=None, **_kwargs):
        self.sent.append((content, file))


class _Bot:
    def __init__(self, db_path: str, channel: "_Channel") -> None:
        self.db_path = db_path
        self._channel = channel

    def get_channel(self, _channel_id):
        return self._channel


@pytest.fixture()
def capture_drawings(monkeypatch, tmp_path):
    """Capture what the verdict graphic is asked to draw, without a rasteriser.

    Mirrors `stub_image_path` in tests/unit/test_image_verdicts_post.py; kept local so the
    image toggle can be switched off for the test that pins the textual announcement.
    """
    png = tmp_path / "verdict.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")

    state = {"enabled": True, "built": []}

    async def _enabled(_bot, _server_id):
        return state["enabled"]

    async def _build(_bot, **kwargs):
        """Stands in for `build_drawing` wholesale; it is not a model of it.

        The resolver below names every mention after the penalised driver, which the real
        `build_drawing` stopped doing in #142. That is harmless here — these tests assert on
        the fallback and on what was posted, never on a name — but do not read it as the
        production rule. tests/unit/test_image_verdict_mentions.py holds that.
        """
        from services.image_verdict_service import VerdictDrawing, resolve_mentions

        state["built"].append(kwargs)
        return VerdictDrawing(
            kind=kwargs["kind"],
            season_number=kwargs["season_number"],
            division_name=kwargs["division_name"],
            round_number=kwargs["round_number"],
            session_name=kwargs["session_label"],
            driver_name=kwargs["driver_name"],
            team_name=kwargs.get("team_name"),
            penalty=kwargs["penalty_description"],
            description=resolve_mentions(
                kwargs["description_text"], lambda _u: kwargs["driver_name"]
            ),
            justification=resolve_mentions(
                kwargs["justification_text"], lambda _u: kwargs["driver_name"]
            ),
        )

    async def _render(_bot, _server_id, drawing, **_kwargs):
        from services.image_verdict_post import VerdictRender

        return VerdictRender(png=png, notices=[], problem=None)

    async def _report(*_args, **_kwargs):
        return None

    async def _team(_bot, _guild, **_kwargs):
        return "Red Bull"

    from services import image_verdict_post

    monkeypatch.setattr(image_verdict_post, "verdicts_enabled", _enabled)
    monkeypatch.setattr(image_verdict_post, "build_drawing", _build)
    monkeypatch.setattr(image_verdict_post, "render_verdict", _render)
    monkeypatch.setattr(image_verdict_post, "report", _report)
    monkeypatch.setattr(image_verdict_post, "report_notices", _report)
    monkeypatch.setattr(image_verdict_post, "team_name_for_entry", _team)
    return state


async def _seed_round(db_path: str) -> dict:
    """A season, a division with a verdicts channel, and one round with a race result."""
    from db.database import run_migrations, get_connection

    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 10, 20, 30)",
            (SERVER_ID,),
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-01-01', 'ACTIVE', 1)",
            (SERVER_ID,),
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) "
            "VALUES (?, 'Division A', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
            "VALUES (?, 1, 'STANDARD', 'AWAITING_REPORT_VERDICTS', '2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO division_results_config (division_id, penalty_channel_id) "
            "VALUES (?, 55555)",
            (division_id,),
        )
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type) "
            "VALUES (?, ?, 'FEATURE_RACE')",
            (round_id, division_id),
        )
        session_result_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_role_id, finishing_position) VALUES (?, ?, 888, 1)",
            (session_result_id, DRIVER_ID),
        )
        race_result_id = cursor.lastrowid
        await db.commit()

    return {"round_id": round_id, "race_result_id": race_result_id}


async def _seed_driver(
    db_path: str,
    *,
    signup_display_name: str | None = None,
    signup_username: str | None = None,
    test_display_name: str | None = None,
) -> None:
    """One driver profile, and the signup record the league holds for them where it does."""
    from db.database import get_connection

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (server_id, discord_user_id, current_state, "
            "is_test_driver, test_display_name) VALUES (?, ?, 'FULL_TIME', ?, ?)",
            (
                SERVER_ID,
                str(DRIVER_ID),
                1 if test_display_name else 0,
                test_display_name,
            ),
        )
        if signup_display_name is not None or signup_username is not None:
            await db.execute(
                "INSERT INTO signup_records (discord_user_id, "
                "discord_username, server_display_name) VALUES (?, ?, ?)",
                (str(DRIVER_ID), signup_username, signup_display_name),
            )
        await db.commit()


def _penalty_record(race_result_id: int) -> dict:
    return {
        "race_result_id": race_result_id,
        "qual_result_id": None,
        "driver_user_id": DRIVER_ID,
        "team_role_id": 888,
        "penalty_type": "TIME",
        "time_seconds": 5,
        "description": "Contact at turn four.",
        "justification": f"<@{DRIVER_ID}> was found wholly at fault.",
    }


@pytest.mark.asyncio
async def test_penalty_verdict_names_the_driver_not_their_id(tmp_path, capture_drawings):
    """A real driver is drawn under their server display name, never their user id (#141)."""
    db_path = str(tmp_path / "test.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path, signup_display_name="Signed Up Name")

    channel = _Channel(_Member("Ada on Server"))
    bot = _Bot(db_path, channel)
    state = _make_state(db_path, round_id=seeded["round_id"])

    await post_penalty_announcements(
        bot, state, [_penalty_record(seeded["race_result_id"])]
    )

    assert len(capture_drawings["built"]) == 1
    drawn = capture_drawings["built"][0]["driver_name"]
    assert drawn == "Ada on Server"
    assert str(DRIVER_ID) not in drawn


@pytest.mark.asyncio
async def test_penalty_verdict_falls_back_to_the_signup_name(tmp_path, capture_drawings):
    """Unreachable on the server, the driver is drawn under the name the league recorded."""
    db_path = str(tmp_path / "test.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path, signup_display_name="Signed Up Name")

    channel = _Channel(None)  # no member: left the server, or not in the cache
    bot = _Bot(db_path, channel)
    state = _make_state(db_path, round_id=seeded["round_id"])

    await post_penalty_announcements(
        bot, state, [_penalty_record(seeded["race_result_id"])]
    )

    assert capture_drawings["built"][0]["driver_name"] == "Signed Up Name"


@pytest.mark.asyncio
async def test_penalty_verdict_resolves_the_mention_in_the_justification(
    tmp_path, capture_drawings
):
    """The mention a steward wrote is drawn as the driver's name, not as their id (#141).

    Resolved through the production path rather than a lambda standing in for it. The lambda
    this used to carry answered with the penalised driver's name whatever id it was handed,
    which was a faithful model of `build_drawing` at the time and of the defect in it (#142):
    a test reconstructing the code it is testing agrees with it by construction.
    """
    db_path = str(tmp_path / "test.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path, signup_display_name="Signed Up Name")

    channel = _Channel(_Member("Ada on Server"))
    bot = _Bot(db_path, channel)
    state = _make_state(db_path, round_id=seeded["round_id"])

    await post_penalty_announcements(
        bot, state, [_penalty_record(seeded["race_result_id"])]
    )

    built = capture_drawings["built"][0]
    from services.image_verdict_post import _mention_names
    from services.image_verdict_service import resolve_mentions

    names = await _mention_names(
        bot,
        channel.guild,
        driver_discord_id=built["driver_discord_id"],
        driver_name=built["driver_name"],
        texts=(built["description_text"], built["justification_text"]),
    )
    drawn = resolve_mentions(
        built["justification_text"], lambda user_id: names.get(str(user_id), str(user_id))
    )
    assert drawn == "Ada on Server was found wholly at fault."


@pytest.mark.asyncio
async def test_appeal_verdict_names_the_driver_not_their_id(tmp_path, capture_drawings):
    """An appeal correction names the driver the same way a penalty does (#141)."""
    db_path = str(tmp_path / "test.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path, signup_display_name="Signed Up Name")

    channel = _Channel(_Member("Ada on Server"))
    bot = _Bot(db_path, channel)
    state = _make_state(db_path, round_id=seeded["round_id"])

    record = _penalty_record(seeded["race_result_id"])
    record["time_seconds"] = -5
    await post_appeal_announcements(bot, state, [record])

    assert capture_drawings["built"][0]["driver_name"] == "Ada on Server"


@pytest.mark.asyncio
async def test_mock_driver_is_still_drawn_under_its_test_name(tmp_path, capture_drawings):
    """A test driver keeps the name test mode gave it — the case that always worked."""
    db_path = str(tmp_path / "test.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path, test_display_name="Mock Driver")

    channel = _Channel(None)  # a mock driver is nobody on the server
    bot = _Bot(db_path, channel)
    state = _make_state(db_path, round_id=seeded["round_id"])

    await post_penalty_announcements(
        bot, state, [_penalty_record(seeded["race_result_id"])]
    )

    assert capture_drawings["built"][0]["driver_name"] == "Mock Driver"


# ---------------------------------------------------------------------------
# The attendance sanction's verdict — the third call site (#141)
# ---------------------------------------------------------------------------
#
# Reached from `attendance_service`, which hands over the driver's test display name. That
# name still stands in the *textual* announcement, where the driver is a live mention and
# Discord resolves the name itself; only the graphic, which can carry no mention, resolves
# one of its own.


@pytest.mark.asyncio
async def test_autosanction_verdict_names_the_driver_not_their_id(
    tmp_path, capture_drawings
):
    """A sacking names the driver as a penalty does, and never by their user id (#141)."""
    from services.verdict_announcement_service import post_autosanction_announcement

    db_path = str(tmp_path / "test.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path, signup_display_name="Signed Up Name")

    channel = _Channel(_Member("Ada on Server"))
    bot = _Bot(db_path, channel)

    with patch(
        "services.verdict_announcement_service.banner_for_round",
        return_value=AsyncMock(),
    ):
        await post_autosanction_announcement(
            bot,
            db_path,
            seeded["round_id"],
            DRIVER_ID,
            None,  # a real driver carries no test display name
            "AUTOSACK",
            12,
        )

    assert len(capture_drawings["built"]) == 1
    drawn = capture_drawings["built"][0]["driver_name"]
    assert drawn == "Ada on Server"
    assert str(DRIVER_ID) not in drawn


@pytest.mark.asyncio
async def test_autosanction_message_still_carries_the_mention(tmp_path, capture_drawings):
    """With graphics off, the textual announcement is untouched: a mention, not a name."""
    from services.verdict_announcement_service import post_autosanction_announcement

    capture_drawings["enabled"] = False

    db_path = str(tmp_path / "test.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path, signup_display_name="Signed Up Name")

    channel = _Channel(_Member("Ada on Server"))
    bot = _Bot(db_path, channel)

    with patch(
        "services.verdict_announcement_service.banner_for_round",
        return_value=AsyncMock(),
    ):
        await post_autosanction_announcement(
            bot,
            db_path,
            seeded["round_id"],
            DRIVER_ID,
            "Mock Driver",
            "AUTORESERVE",
            12,
        )

    assert capture_drawings["built"] == []
    content, file = channel.sent[-1]
    assert file is None
    assert f"<@{DRIVER_ID}> (Mock Driver)" in content
    assert "Moved to Reserve" in content


@pytest.mark.asyncio
async def test_a_verdict_on_a_result_under_a_past_account_names_the_current_one(
    tmp_path, capture_drawings
):
    """E7 (issue #243): the result stands under DRIVER_ID, and the driver has moved on."""
    from db.database import get_connection

    db_path = str(tmp_path / "test.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET discord_user_id = '31337' WHERE server_id = ?",
            (SERVER_ID,),
        )
        await db.commit()

    channel = _Channel(_Member("Ada on Server"))
    bot = _Bot(db_path, channel)
    state = _make_state(db_path, round_id=seeded["round_id"])

    await post_penalty_announcements(
        bot, state, [_penalty_record(seeded["race_result_id"])]
    )

    assert [content for content, _file in channel.sent] == ["<@31337>"]
