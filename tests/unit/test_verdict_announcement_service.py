"""Tests for verdict_announcement_service — translate_penalty and post helpers."""
from __future__ import annotations

import json
from types import SimpleNamespace

import discord
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
    """A stand-in for ``PenaltyReviewState``.

    ``round_number`` and ``division_name`` are set because the fault lines read them: left
    to a bare ``MagicMock`` they would be mock objects, and the message would name one.
    """
    state = MagicMock()
    state.db_path = db_path
    state.round_id = round_id
    state.division_id = 1
    state.round_number = 3
    state.division_name = "Division A"
    return state


@pytest.mark.asyncio
async def test_post_penalty_announcements_empty_list_noop():
    """Empty applied_penalties list should not call any DB or Discord APIs."""
    import asyncio
    bot = MagicMock()
    state = _make_state("irrelevant.db")
    # Should complete without raising even though db_path is fake
    assert await post_penalty_announcements(bot, state, []) == []
    bot.get_channel.assert_not_called()


@pytest.mark.asyncio
async def test_post_penalty_announcements_reports_when_no_channel_configured(tmp_path):
    """A division with no verdicts channel is a fault, not a silence (#237).

    This is the opposite of the rule the results and standings reposts keep, where an
    unconfigured channel is owed nothing. A verdicts channel is one of the three a division
    must have before its placements can be confirmed, so a round reaching a penalty verdict
    without one is an anomaly — and the penalty has already been applied.
    """
    from db.database import run_migrations, get_connection

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1001, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
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
    faults = await post_penalty_announcements(bot, state, [fake_record])

    bot.get_channel.assert_not_called()
    assert len(faults) == 1
    assert "no verdicts channel" in faults[0]
    assert "Division A" in faults[0]


@pytest.mark.asyncio
async def test_post_penalty_announcements_reports_when_channel_inaccessible(tmp_path):
    """A verdicts channel that has been deleted is named, with what went unannounced (#237)."""
    from db.database import run_migrations, get_connection

    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1001, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
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

    faults = await post_penalty_announcements(bot, state, [fake_record])

    bot.get_channel.assert_called_once_with(55555)
    assert len(faults) == 1
    assert "55555" in faults[0]
    assert "one verdict" in faults[0]


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
    #: What a sent message's id counts up from, so a test can assert on a known value.
    FIRST_MESSAGE_ID = 770001

    def __init__(self, member: "_Member | None" = None, *, channel_id: int = 4242) -> None:
        self.sent: list[tuple] = []
        self.guild = _Guild(member)
        self.id = channel_id

    async def send(self, content=None, *, file=None, **_kwargs):
        self.sent.append((content, file))
        # Discord hands back the message it created, and the bot now keeps it: a verdict that
        # cannot be found again cannot be replaced when the round is amended (#189).
        message = SimpleNamespace(id=self.FIRST_MESSAGE_ID + len(self.sent) - 1)
        return message


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

    async def _enabled(_bot):
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

    async def _render(_bot, drawing, **_kwargs):
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
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
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
            "INSERT INTO driver_profiles (discord_user_id, current_state, "
            "is_test_driver, test_display_name) VALUES (?, 'FULL_TIME', ?, ?)",
            (
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
            "UPDATE driver_profiles SET discord_user_id = '31337'",
        )
        await db.commit()

    channel = _Channel(_Member("Ada on Server"))
    bot = _Bot(db_path, channel)
    state = _make_state(db_path, round_id=seeded["round_id"])

    await post_penalty_announcements(
        bot, state, [_penalty_record(seeded["race_result_id"])]
    )

    assert [content for content, _file in channel.sent] == ["<@31337>"]


# ---------------------------------------------------------------------------
# A verdict that was not announced reaches the league (#237)
#
# The penalty is applied either way: the classification changes and the driver loses the
# places. The announcement is the only thing that tells them why, and every failure to post
# one used to end in the host's log file.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_round_that_cannot_be_read_reports_every_verdict_owed(tmp_path):
    """No context means no channel, no division and no verdicts — all of them named."""
    db_path = str(tmp_path / "unreadable.db")
    await _seed_round(db_path)

    bot = MagicMock()
    state = _make_state(db_path, round_id=999_999)

    faults = await post_penalty_announcements(
        bot, state, [_penalty_record(1), _penalty_record(2)]
    )

    assert len(faults) == 1
    # The round a league knows, not the primary key, and a count that reads as English.
    assert "Round 3 (Division A)" in faults[0]
    assert "999999" not in faults[0].replace(",", "")
    assert "2 verdicts" in faults[0]


@pytest.mark.asyncio
async def test_a_record_with_no_result_is_named_and_the_next_still_posts(tmp_path):
    """One unbuildable verdict must not cost the others theirs."""
    db_path = str(tmp_path / "partial.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path, signup_display_name="Ada")

    sent: list = []

    channel = MagicMock()

    async def _send(content=None, **kwargs):
        sent.append(content)
        message = MagicMock()
        message.id = 1
        return message

    channel.send = _send
    bot = MagicMock()
    bot.db_path = db_path  # the image path reads it; a mock here writes a file named after itself
    bot.get_channel.return_value = channel
    state = _make_state(db_path, round_id=seeded["round_id"])

    good = _penalty_record(seeded["race_result_id"])
    orphan = _penalty_record(999_999)

    faults = await post_penalty_announcements(bot, state, [orphan, good])

    assert len(faults) == 1
    assert f"<@{DRIVER_ID}>" in faults[0]
    assert "could not be read" in faults[0]
    assert sent, "the second verdict should still have been announced"


@pytest.mark.asyncio
async def test_a_verdict_whose_posting_raises_is_named(tmp_path):
    db_path = str(tmp_path / "raises.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path, signup_display_name="Ada")

    channel = MagicMock()
    channel.send = AsyncMock(side_effect=RuntimeError("Missing Permissions"))
    bot = MagicMock()
    bot.db_path = db_path  # as above — a mock db_path becomes a file in the repo root
    bot.get_channel.return_value = channel
    state = _make_state(db_path, round_id=seeded["round_id"])

    faults = await post_penalty_announcements(
        bot, state, [_penalty_record(seeded["race_result_id"])]
    )

    assert len(faults) == 1
    assert f"<@{DRIVER_ID}>" in faults[0]
    assert "Missing Permissions" in faults[0]


@pytest.mark.asyncio
async def test_an_appeal_verdict_that_cannot_be_announced_is_reported(tmp_path):
    """The worse of the two to lose: it tells a driver a sanction was overturned."""
    db_path = str(tmp_path / "appeal.db")
    seeded = await _seed_round(db_path)

    bot = MagicMock()
    bot.get_channel.return_value = None
    state = _make_state(db_path, round_id=seeded["round_id"])

    faults = await post_appeal_announcements(
        bot, state, [_penalty_record(seeded["race_result_id"])]
    )

    assert len(faults) == 1
    assert "55555" in faults[0]


@pytest.mark.asyncio
async def test_an_unannounced_autosanction_says_it_was_still_applied(tmp_path):
    """The sanction took effect; only its announcement did not.

    The distinction matters to the manager reading it: re-running the sanctions will not
    announce it, because the driver is no longer a candidate.
    """
    from services.verdict_announcement_service import post_autosanction_announcement

    db_path = str(tmp_path / "autosanction.db")
    seeded = await _seed_round(db_path)

    bot = MagicMock()
    bot.get_channel.return_value = None

    faults = await post_autosanction_announcement(
        bot=bot,
        db_path=db_path,
        round_id=seeded["round_id"],
        driver_discord_id=DRIVER_ID,
        driver_display_name="Ada",
        sanction_type="AUTOSACK",
        threshold=12,
    )

    assert len(faults) == 1
    assert "was applied" in faults[0]
    assert "not announced" in faults[0]
    assert f"<@{DRIVER_ID}>" in faults[0]


def test_the_repair_hint_says_a_verdict_cannot_be_announced_twice():
    """There is no re-announce command, and #189 records why one could not be built.

    **Attendance sanctions are no exception**, though `/attendance sync` re-runs them: a
    driver already sacked or already moved to Reserve is no longer a candidate, so a second
    run passes over them without a word. ``attendance_service._failure_reason`` states this,
    and an earlier draft of this hint promised the opposite — which would have had a manager
    run the sync, read `Success`, and believe the driver had been told.
    """
    from services.verdict_announcement_service import verdict_repair_hint

    hint = verdict_repair_hint()
    assert "cannot announce a verdict a second time" in hint
    assert "yourself" in hint
    assert "/attendance sync" not in hint


async def test_the_repair_hint_is_defined_exactly_once(tmp_path):
    """A second definition shadowed the first and would have raised `NameError` (#345).

    `verdict_repair_hint` was defined twice in this module. The later one took a `sanction`
    keyword and returned `_SANCTION_RETRY` for it — a name defined nowhere in the repository — so
    any caller passing `sanction=True` would have raised rather than returning a hint. No caller
    did, which is exactly why it sat there: the failure was latent, reachable only by the next
    person to use the parameter the signature advertised.

    Pinned by counting the definitions rather than by calling it, because calling the surviving
    one proves nothing about a shadow that would silently replace it.
    """
    import inspect

    from services import verdict_announcement_service as module

    source = inspect.getsource(module)
    assert source.count("def verdict_repair_hint") == 1
    assert "_SANCTION_RETRY" not in source


# ---------------------------------------------------------------------------
# Recording which message carries a verdict (#189)
# ---------------------------------------------------------------------------
#
# The two tables stored the channel an announcement went to and nothing more, so the bot could
# not find, edit, delete or replace a verdict it had posted. Amending a round therefore rescored
# the classification a verdict was applied to and left the verdict standing beside it, saying
# something the results no longer said, with no command able to put it right.
#
# The message is recorded as it is sent. The chunk list is written alongside the anchor because
# that is the pair every other posting uses, and a verdict batch that outgrew Discord's limit
# would otherwise bring back exactly the guesswork it was removed to prevent (#345).


async def _announcement_row(db_path: str, table: str) -> dict:
    from db.database import get_connection

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT announcement_message_id, announcement_message_ids, "
            f"announcement_channel_id FROM {table}"
        )
        return dict(await cursor.fetchone())


async def _insert_penalty(db_path: str, race_result_id: int, table: str = "penalty_records") -> int:
    from db.database import get_connection

    async with get_connection(db_path) as db:
        if table == "penalty_records":
            cursor = await db.execute(
                "INSERT INTO penalty_records (race_result_id, penalty_type, time_seconds, "
                "description, justification, applied_by, applied_at) "
                "VALUES (?, 'TIME', 5, 'Contact', 'At fault', '77', '2026-02-02T00:00:00+00:00')",
                (race_result_id,),
            )
        else:
            cursor = await db.execute(
                "INSERT INTO appeal_records (race_result_id, status, penalty_type, "
                "time_seconds, description, justification, submitted_by, submitted_at) "
                "VALUES (?, 'UPHELD', 'TIME', 3, 'Appeal', 'Upheld', '78', "
                "'2026-02-03T00:00:00+00:00')",
                (race_result_id,),
            )
        await db.commit()
        return cursor.lastrowid


@pytest.mark.asyncio
async def test_a_penalty_verdict_records_the_message_it_was_announced_in(tmp_path):
    """The id is stored against the record, which is the whole of what #189 asked for."""
    db_path = str(tmp_path / "record_penalty.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path)
    record_id = await _insert_penalty(db_path, seeded["race_result_id"])

    channel = _Channel(_Member("Ada"))
    bot = _Bot(db_path, channel)
    state = _make_state(db_path, round_id=seeded["round_id"])

    record = _penalty_record(seeded["race_result_id"]) | {"id": record_id}
    assert await post_penalty_announcements(bot, state, [record]) == []

    row = await _announcement_row(db_path, "penalty_records")
    assert row["announcement_message_id"] == str(_Channel.FIRST_MESSAGE_ID)


@pytest.mark.asyncio
async def test_a_penalty_verdict_records_the_channel_it_went_to(tmp_path):
    """The channel is written from the one actually posted to, not left to an earlier guess."""
    db_path = str(tmp_path / "record_channel.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path)
    record_id = await _insert_penalty(db_path, seeded["race_result_id"])

    channel = _Channel(_Member("Ada"), channel_id=5150)
    bot = _Bot(db_path, channel)
    state = _make_state(db_path, round_id=seeded["round_id"])

    await post_penalty_announcements(
        bot, state, [_penalty_record(seeded["race_result_id"]) | {"id": record_id}]
    )

    assert (await _announcement_row(db_path, "penalty_records"))[
        "announcement_channel_id"
    ] == "5150"


@pytest.mark.asyncio
async def test_a_verdict_records_its_chunk_list_too(tmp_path):
    """One message today, recorded in the form every other posting uses.

    Deleting a posting reads the list; a verdict that recorded only an anchor would fall back to
    the adjacency walk, which is wrong precisely where the amendment replay puts it (#345).
    """
    db_path = str(tmp_path / "record_chunks.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path)
    record_id = await _insert_penalty(db_path, seeded["race_result_id"])

    channel = _Channel(_Member("Ada"))
    bot = _Bot(db_path, channel)
    state = _make_state(db_path, round_id=seeded["round_id"])

    await post_penalty_announcements(
        bot, state, [_penalty_record(seeded["race_result_id"]) | {"id": record_id}]
    )

    row = await _announcement_row(db_path, "penalty_records")
    assert json.loads(row["announcement_message_ids"]) == [_Channel.FIRST_MESSAGE_ID]


@pytest.mark.asyncio
async def test_an_appeal_verdict_records_its_message(tmp_path):
    """`appeal_records` carries the same columns and was equally unfindable."""
    db_path = str(tmp_path / "record_appeal.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path)
    record_id = await _insert_penalty(
        db_path, seeded["race_result_id"], table="appeal_records"
    )

    channel = _Channel(_Member("Ada"))
    bot = _Bot(db_path, channel)
    state = _make_state(db_path, round_id=seeded["round_id"])

    record = _penalty_record(seeded["race_result_id"]) | {"id": record_id}
    assert await post_appeal_announcements(bot, state, [record]) == []

    row = await _announcement_row(db_path, "appeal_records")
    assert row["announcement_message_id"] == str(_Channel.FIRST_MESSAGE_ID)


@pytest.mark.asyncio
async def test_a_record_with_no_id_is_announced_all_the_same(tmp_path):
    """The announcement is the point; the record of it is not worth failing one over.

    `post_autosanction_announcement` posts a verdict with no row behind it at all, and a caller
    assembling records by hand may omit the id. Neither may lose the driver their explanation.
    """
    db_path = str(tmp_path / "no_id.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path)

    channel = _Channel(_Member("Ada"))
    bot = _Bot(db_path, channel)
    state = _make_state(db_path, round_id=seeded["round_id"])

    faults = await post_penalty_announcements(
        bot, state, [_penalty_record(seeded["race_result_id"])]
    )

    assert faults == []
    assert len(channel.sent) == 1
