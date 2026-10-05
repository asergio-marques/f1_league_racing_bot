"""Tests for verdict_announcement_service: the penalty wording, the sanction announcement, the
banner records and the repair hint."""
from __future__ import annotations

from types import SimpleNamespace

import discord
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from leaguebot.results.services.verdict_announcement_service import (
    describe_penalty,
    translate_penalty,
)
from tests.support.teams import seed_team_instances  # noqa: E402


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

    @pytest.mark.parametrize("typed", ["NFA", "nfa", "  Nfa  "])
    def test_no_further_action_says_no_penalty_follows(self, typed):
        assert translate_penalty(typed) == NO_FURTHER_ACTION


#: What a cleared driver's verdict reads, in the announcement and on the graphic alike
#: (decided 2026-09-24, #138).
NO_FURTHER_ACTION = "No further action"


def test_no_further_action_is_described_as_no_penalty_and_never_as_a_disqualification():
    """It carries no seconds, as a disqualification does — and a record with no seconds was
    read as one, so it has to be recognised before that fallback is reached (#138)."""
    assert describe_penalty("NFA", None) == NO_FURTHER_ACTION


def test_a_disqualification_is_still_described_as_one():
    assert describe_penalty("DSQ", None) == "Disqualified"


# ---------------------------------------------------------------------------
# The name a verdict graphic draws a driver under (#141)
# ---------------------------------------------------------------------------
#
# A penalty's and a correction's verdict are announced by the review's `announce_verdict` jobs,
# whose naming tests are tests/results/test_verdict_announcement_jobs.py (#439). What follows
# drives the attendance sanction's announcement with a real driver in the database.

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

    #: The id of the first *verdict*. Since #246 every batch is headed — as a banner where the
    #: aspect is on, in words where it is off, and this stub bot has no image module at all —
    #: so the heading takes `FIRST_MESSAGE_ID` and the verdict beneath it the next.
    FIRST_VERDICT_ID = 770002

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

    Mirrors `stub_image_path` in tests/image/test_image_verdicts_post.py; kept local so the
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
        production rule. tests/image/test_image_verdict_mentions.py holds that.
        """
        from leaguebot.image.services.image_verdict_service import VerdictDrawing, resolve_mentions

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
        from leaguebot.image.services.image_verdict_post import VerdictRender

        return VerdictRender(png=png, notices=[], problem=None)

    async def _report(*_args, **_kwargs):
        return None

    async def _team(_bot, _guild, **_kwargs):
        return "Red Bull"

    from leaguebot.image.services import image_verdict_post

    monkeypatch.setattr(image_verdict_post, "verdicts_enabled", _enabled)
    monkeypatch.setattr(image_verdict_post, "build_drawing", _build)
    monkeypatch.setattr(image_verdict_post, "render_verdict", _render)
    monkeypatch.setattr(image_verdict_post, "report", _report)
    monkeypatch.setattr(image_verdict_post, "report_notices", _report)
    monkeypatch.setattr(image_verdict_post, "team_name_for_entry", _team)
    return state


async def _seed_round(db_path: str) -> dict:
    """A season, a division with a verdicts channel, and one round with a race result."""
    from leaguebot.core.db.database import run_migrations, get_connection

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
        await seed_team_instances(db, division_id, 888)
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
            "team_instance_id, finishing_position) VALUES (?, ?, 888, 1)",
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
    from leaguebot.core.db.database import get_connection

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
    from leaguebot.results.services.verdict_announcement_service import post_autosanction_announcement

    db_path = str(tmp_path / "test.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path, signup_display_name="Signed Up Name")

    channel = _Channel(_Member("Ada on Server"))
    bot = _Bot(db_path, channel)

    with patch(
        "leaguebot.results.services.verdict_announcement_service.banner_for_round",
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
    from leaguebot.results.services.verdict_announcement_service import post_autosanction_announcement

    capture_drawings["enabled"] = False

    db_path = str(tmp_path / "test.db")
    seeded = await _seed_round(db_path)
    await _seed_driver(db_path, signup_display_name="Signed Up Name")

    channel = _Channel(_Member("Ada on Server"))
    bot = _Bot(db_path, channel)

    with patch(
        "leaguebot.results.services.verdict_announcement_service.banner_for_round",
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


# ---------------------------------------------------------------------------
# A verdict that was not announced reaches the league (#237)
#
# The penalty is applied either way: the classification changes and the driver loses the
# places. The announcement is the only thing that tells them why, and every failure to post
# one used to end in the host's log file.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unannounced_autosanction_says_it_was_still_applied(tmp_path):
    """The sanction took effect; only its announcement did not.

    The distinction matters to the manager reading it: re-running the sanctions will not
    announce it, because the driver is no longer a candidate.
    """
    from leaguebot.results.services.verdict_announcement_service import post_autosanction_announcement

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
    from leaguebot.results.services.verdict_announcement_service import verdict_repair_hint

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

    from leaguebot.results.services import verdict_announcement_service as module

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


async def test_the_banner_records_the_message_it_was_posted_as(tmp_path):
    """**So an amendment can take it down with the run it heads** (#345). A banner belongs to no
    verdict record, so without this the replay removed a round's announcements and left the
    header standing over the empty space, then posted a fresh one below it."""
    import os

    from leaguebot.core.db.database import get_connection, run_migrations
    from leaguebot.image.services import image_verdict_banner_post
    from leaguebot.results.services import verdict_announcement_service as vas

    db_path = os.path.join(str(tmp_path), "banner_record.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 3, '2026-01-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (1, 1, 'Pro', 1, 555)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
            "VALUES (7, 1, 2, '2026-02-01T18:00:00+00:00', 'NORMAL', 'FINAL')"
        )
        await db.execute(
            "INSERT INTO division_results_config (division_id, penalty_channel_id) "
            "VALUES (1, 4242)"
        )
        await db.commit()

    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=MagicMock())
    with patch.object(
        image_verdict_banner_post, "try_post", new=AsyncMock(return_value=MagicMock(id=9911))
    ), patch.object(
        image_verdict_banner_post, "build_drawing", new=MagicMock()
    ):
        await vas.banner_for_round(bot, db_path, 7)()

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT round_id, channel_id, message_id FROM verdict_banner_messages"
        )
        assert [tuple(r) for r in await cursor.fetchall()] == [(7, "4242", "9911")]


async def test_a_batch_that_heads_itself_records_its_banner(tmp_path):
    """**The fallback banner was invisible to the replay** (#345).

    A poster handed no banner posts one of its own — the appeals stage of a first pass, an
    attendance sanction firing alone. Unrecorded, an amendment took that run's cards down and
    left the header standing over the empty space, then posted a fresh one below it.
    """
    import os

    from leaguebot.core.db.database import get_connection, run_migrations
    from leaguebot.image.services import image_verdict_banner_post
    from leaguebot.results.services import verdict_announcement_service as vas

    db_path = os.path.join(str(tmp_path), "banner_fallback.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 3, '2026-01-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (1, 1, 'Pro', 1, 555)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
            "VALUES (7, 1, 2, '2026-02-01T18:00:00+00:00', 'NORMAL', 'FINAL')"
        )
        await db.commit()

    channel = MagicMock()
    channel.id = 4242
    with patch.object(
        image_verdict_banner_post, "try_post", new=AsyncMock(return_value=MagicMock(id=9912))
    ), patch.object(image_verdict_banner_post, "build_drawing", new=MagicMock()):
        await vas._banner_once_recorded(
            MagicMock(), channel, {"division_name": "Pro"}, db_path, 7
        )()

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT message_id FROM verdict_banner_messages")
        assert [r[0] for r in await cursor.fetchall()] == ["9912"]


def test_a_round_label_names_the_round_as_a_league_numbers_it():
    from leaguebot.results.services.verdict_announcement_service import _round_label

    assert _round_label(SimpleNamespace(round_number=3, division_name="Pro")) == "Round 3 (Pro)"


def test_a_round_label_without_a_round_number_says_the_round():
    from leaguebot.results.services.verdict_announcement_service import _round_label

    assert _round_label(SimpleNamespace()) == "The round"
    assert _round_label(SimpleNamespace(round_number="not a number")) == "The round"
