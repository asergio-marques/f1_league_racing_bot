"""Unit tests for posting a session's results as a graphic — T026, T027, T041, T042.

The graphic is an alternative output beside the text, never a replacement for it
(Constitution XIV.7). Every test here asserts either that the image path ran, or that the
textual body still runs exactly as it did before 039 where it did not.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

pytestmark = pytest.mark.asyncio


async def _seeded(tmp_path, *, result_status="PROVISIONAL", message_id=None):
    """A database holding one season, division, round and session result."""
    from db.database import get_connection, run_migrations
    from models.session_result import SessionResult

    db_path = str(tmp_path / "image.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cur = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 3)"
        )
        season_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO divisions (season_id, name, tier, mention_role_id) "
            "VALUES (?, 'Main', 1, 777)",
            (season_id,),
        )
        division_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, result_status, "
            "scheduled_at) VALUES (?, 5, 'STANDARD', ?, '2026-06-01T18:00:00')",
            (division_id, result_status),
        )
        round_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "results_message_id) VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', ?)",
            (round_id, division_id, message_id),
        )
        session_id = cur.lastrowid
        await db.commit()

    return db_path, SessionResult(
        id=session_id,
        round_id=round_id,
        division_id=division_id,
        session_type="FEATURE_RACE",
        status="ACTIVE",
        config_name=None,
        submitted_by=None,
        submitted_at=None,
        results_message_id=message_id,
    )


def _channel(sent, order=None, previous=None):
    channel = AsyncMock()

    async def send(content=None, **kwargs):
        if order is not None:
            order.append("posted")
        sent.append((content, kwargs))
        message = MagicMock()
        message.id = 4242
        return message

    channel.send = send
    if previous is not None:
        channel.fetch_message = AsyncMock(return_value=previous)
    return channel


def _guild():
    guild = MagicMock()
    guild.id = 1
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(side_effect=Exception("not found"))
    guild.get_role.return_value = None
    return guild


def _bot(db_path, *, module=True, toggle=True, valid=True):
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_images_enabled = AsyncMock(return_value=module)
    bot.image_config_service.get_toggles = AsyncMock(return_value={"results": toggle})
    bot.image_validity_service.template_reports = AsyncMock(
        return_value={"results_race_template": MagicMock(valid=valid)}
    )
    bot.output_router.post_log = AsyncMock()
    return bot


def _decision(*, posts=True, rejects=False, png=None, notices=(), problem=None):
    decision = MagicMock()
    decision.posts_image = posts
    decision.rejects = rejects
    decision.png_paths = [png] if png is not None else []
    decision.notices = list(notices)
    decision.problem = problem
    decision.caller_message = MagicMock(return_value="❌ the template is at fault")
    return decision


def _drawing():
    return MagicMock(template_key="results_race_template", session_name="Race")


# ── The textual path is untouched where the image path does not run ───────


async def test_no_bot_leaves_the_textual_path_exactly_as_it_was(tmp_path):
    from services.results_post_service import post_session_results

    db_path, session_result = await _seeded(tmp_path)
    sent: list = []

    await post_session_results(
        db_path=db_path,
        session_result=session_result,
        driver_rows=[],
        points_map={},
        results_channel=_channel(sent),
        guild=_guild(),
        round_number=5,
        track_name="Monaco",
        label="Provisional Results",
    )

    assert len(sent) == 1
    assert "Provisional Results" in sent[0][0]
    assert "file" not in sent[0][1]


async def test_the_module_being_disabled_leaves_the_text(tmp_path):
    from services.results_post_service import post_session_results

    db_path, session_result = await _seeded(tmp_path)
    sent: list = []

    await post_session_results(
        db_path=db_path,
        session_result=session_result,
        driver_rows=[],
        points_map={},
        results_channel=_channel(sent),
        guild=_guild(),
        round_number=5,
        track_name="Monaco",
        label="Provisional Results",
        bot=_bot(db_path, module=False),
    )

    assert len(sent) == 1 and "file" not in sent[0][1]


async def test_the_aspect_being_off_leaves_the_text(tmp_path):
    from services.results_post_service import post_session_results

    db_path, session_result = await _seeded(tmp_path)
    sent: list = []

    await post_session_results(
        db_path=db_path,
        session_result=session_result,
        driver_rows=[],
        points_map={},
        results_channel=_channel(sent),
        guild=_guild(),
        round_number=5,
        track_name="Monaco",
        label="Provisional Results",
        bot=_bot(db_path, toggle=False),
    )

    assert len(sent) == 1 and "file" not in sent[0][1]


async def test_an_invalid_template_falls_back_to_the_textual_table(tmp_path):
    """An uncommanded posting whose render fails leaves the league its results."""
    from services.results_post_service import post_session_results

    db_path, session_result = await _seeded(tmp_path)
    sent: list = []

    await post_session_results(
        db_path=db_path,
        session_result=session_result,
        driver_rows=[],
        points_map={},
        results_channel=_channel(sent),
        guild=_guild(),
        round_number=5,
        track_name="Monaco",
        label="Provisional Results",
        bot=_bot(db_path, valid=False),
    )

    assert len(sent) == 1
    assert "Provisional Results" in sent[0][0]


# ── The image path, when it does run ──────────────────────────────────────


async def test_a_posted_graphic_replaces_the_message_and_persists_the_new_id(tmp_path):
    """The replacement is produced first; the old message is deleted only after it exists."""
    from db.database import get_connection
    from services import image_results_post
    from services.results_post_service import post_session_results

    db_path, session_result = await _seeded(tmp_path, message_id=111)
    sent: list = []
    order: list[str] = []

    png = tmp_path / "results.png"
    png.write_bytes(b"\x89PNG")

    previous = AsyncMock()

    async def delete():
        order.append("deleted")

    previous.delete = delete
    channel = _channel(sent, order=order, previous=previous)

    with patch.object(
        image_results_post, "build_drawing", AsyncMock(return_value=_drawing())
    ), patch.object(
        image_results_post,
        "render_png",
        AsyncMock(return_value=_decision(png=png)),
    ):
        message_id = await post_session_results(
            db_path=db_path,
            session_result=session_result,
            driver_rows=[],
            points_map={},
            results_channel=channel,
            guild=_guild(),
            round_number=5,
            track_name="Monaco",
            label="Final Results",
            bot=_bot(db_path),
        )

    assert message_id == 4242
    assert order == ["posted", "deleted"]

    content, kwargs = sent[0]
    # The heading and the lifecycle label stay message text; the table does not.
    assert "Final Results" in content
    assert "Round 5" in content
    assert "file" in kwargs

    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                "SELECT results_message_id FROM session_results WHERE id = ?",
                (session_result.id,),
            )
        ).fetchone()
    assert row["results_message_id"] == 4242


async def test_a_failed_render_leaves_the_previous_message_in_place(tmp_path):
    """The league keeps the results it had rather than losing them to a failed rebuild."""
    from db.database import get_connection
    from services import image_results_post
    from services.results_post_service import post_session_results

    db_path, session_result = await _seeded(tmp_path, message_id=111)
    sent: list = []

    with patch.object(
        image_results_post, "build_drawing", AsyncMock(return_value=_drawing())
    ), patch.object(
        image_results_post,
        "render_png",
        AsyncMock(return_value=_decision(posts=False, problem=MagicMock(detail="boom"))),
    ):
        await post_session_results(
            db_path=db_path,
            session_result=session_result,
            driver_rows=[],
            points_map={},
            results_channel=_channel(sent),
            guild=_guild(),
            round_number=5,
            track_name="Monaco",
            label="Final Results",
            bot=_bot(db_path),
        )

    # The textual table went out in its place, and the stored id moved to it.
    assert len(sent) == 1 and "file" not in sent[0][1]
    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                "SELECT results_message_id FROM session_results WHERE id = ?",
                (session_result.id,),
            )
        ).fetchone()
    assert row["results_message_id"] == 4242


# ── Reporting and the commanded/uncommanded split (T041, T042) ────────────


async def test_a_problem_is_reported_naming_the_session(tmp_path):
    from services import image_results_post
    from services.results_post_service import post_session_results

    db_path, session_result = await _seeded(tmp_path)
    sent: list = []
    bot = _bot(db_path)

    with patch.object(
        image_results_post, "build_drawing", AsyncMock(return_value=_drawing())
    ), patch.object(
        image_results_post,
        "render_png",
        AsyncMock(
            return_value=_decision(posts=False, problem=MagicMock(detail="no such field"))
        ),
    ):
        await post_session_results(
            db_path=db_path,
            session_result=session_result,
            driver_rows=[],
            points_map={},
            results_channel=_channel(sent),
            guild=_guild(),
            round_number=5,
            track_name="Monaco",
            label="Final Results",
            bot=bot,
        )

    bot.output_router.post_log.assert_awaited()
    reported = bot.output_router.post_log.await_args.args[1]
    assert "Main" in reported and "5" in reported and "Race" in reported
    assert "no such field" in reported
    # And nothing of the sort reached the results channel.
    assert all("no such field" not in (content or "") for content, _ in sent)


async def test_a_commanded_posting_is_rejected_and_posts_nothing(tmp_path):
    """XIV.7 — a user at the keyboard is told what is at fault rather than given text."""
    from models.image_module import PostingOrigin
    from services import image_results_post

    db_path, session_result = await _seeded(tmp_path)
    sent: list = []

    with patch.object(
        image_results_post, "build_drawing", AsyncMock(return_value=_drawing())
    ), patch.object(
        image_results_post,
        "render_png",
        AsyncMock(return_value=_decision(posts=False, rejects=True)),
    ):
        outcome = await image_results_post.try_post(
            _bot(db_path),
            _guild(),
            _channel(sent),
            heading="**Main Round 5**",
            label="Final Results",
            session_result=session_result,
            driver_rows=[],
            points_map={},
            round_number=5,
            race_name="Monaco",
            is_sprint=False,
            result_status="FINAL",
            division_name="Main",
            origin=PostingOrigin.COMMANDED,
        )

    assert outcome.action == image_results_post.REJECTED
    assert outcome.message
    assert sent == []


async def test_notices_are_reported_when_a_graphic_does_post(tmp_path):
    from services import image_results_post
    from services.results_post_service import post_session_results

    db_path, session_result = await _seeded(tmp_path)
    sent: list = []
    png = tmp_path / "results.png"
    png.write_bytes(b"\x89PNG")
    bot = _bot(db_path)
    notice = MagicMock(field_id="row_1_driver_flag", notice_kind="OPTIONAL_FIELD_EMPTIED")

    with patch.object(
        image_results_post, "build_drawing", AsyncMock(return_value=_drawing())
    ), patch.object(
        image_results_post,
        "render_png",
        AsyncMock(return_value=_decision(png=png, notices=[notice])),
    ), patch.object(
        image_results_post, "report_notices", AsyncMock()
    ) as reporter:
        await post_session_results(
            db_path=db_path,
            session_result=session_result,
            driver_rows=[],
            points_map={},
            results_channel=_channel(sent),
            guild=_guild(),
            round_number=5,
            track_name="Monaco",
            label="Final Results",
            bot=bot,
        )

    reporter.assert_awaited()


# ── Enablement is checked per template, not per aspect ────────────────────


async def test_enablement_is_answered_for_the_named_template_alone(tmp_path):
    """A sound qualifying template still draws while a faulty race one falls back."""
    from services.image_results_post import results_enabled

    db_path, _ = await _seeded(tmp_path)
    bot = MagicMock()
    bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    bot.image_config_service.get_toggles = AsyncMock(return_value={"results": True})
    bot.image_validity_service.template_reports = AsyncMock(
        return_value={
            "results_qualifying_template": MagicMock(valid=True),
            "results_race_template": MagicMock(valid=False),
        }
    )

    assert await results_enabled(bot, 1, "results_qualifying_template")
    assert not await results_enabled(bot, 1, "results_race_template")


async def test_an_unreadable_toggle_never_breaks_a_posting(tmp_path):
    from services.image_results_post import results_enabled

    bot = MagicMock()
    bot.module_service.is_images_enabled = AsyncMock(side_effect=RuntimeError("db gone"))
    assert not await results_enabled(bot, 1, "results_race_template")


# ── The lifecycle: result_status reaches the drawing (T028) ───────────────


@pytest.mark.parametrize(
    "status,penalty_closed,appeal_closed",
    [
        ("PROVISIONAL", False, False),
        ("POST_RACE_PENALTY", True, False),
        ("FINAL", True, True),
    ],
)
async def test_the_rounds_stage_reaches_the_drawing(
    tmp_path, status, penalty_closed, appeal_closed
):
    """The stage is read from the round, so the label drawn and the phase rule agree."""
    from services import image_results_post
    from services.results_post_service import post_session_results

    db_path, session_result = await _seeded(tmp_path, result_status=status)
    sent: list = []
    png = tmp_path / "results.png"
    png.write_bytes(b"\x89PNG")

    captured: dict = {}

    async def capture(bot, guild, **kwargs):
        captured.update(kwargs)
        return _drawing()

    with patch.object(image_results_post, "build_drawing", capture), patch.object(
        image_results_post, "render_png", AsyncMock(return_value=_decision(png=png))
    ):
        await post_session_results(
            db_path=db_path,
            session_result=session_result,
            driver_rows=[],
            points_map={},
            results_channel=_channel(sent),
            guild=_guild(),
            round_number=5,
            track_name="Monaco",
            label="Provisional Results",
            bot=_bot(db_path),
        )

    assert captured["result_status"] == status

    from services.image_results_service import _PENALTY_CLOSED_AT, _APPEAL_CLOSED_AT

    assert (captured["result_status"] in _PENALTY_CLOSED_AT) is penalty_closed
    assert (captured["result_status"] in _APPEAL_CLOSED_AT) is appeal_closed


async def test_the_division_tier_and_season_reach_the_drawing(tmp_path):
    from services import image_results_post
    from services.results_post_service import post_session_results

    db_path, session_result = await _seeded(tmp_path)
    sent: list = []
    png = tmp_path / "results.png"
    png.write_bytes(b"\x89PNG")
    captured: dict = {}

    async def capture(bot, guild, **kwargs):
        captured.update(kwargs)
        return _drawing()

    with patch.object(image_results_post, "build_drawing", capture), patch.object(
        image_results_post, "render_png", AsyncMock(return_value=_decision(png=png))
    ):
        await post_session_results(
            db_path=db_path,
            session_result=session_result,
            driver_rows=[],
            points_map={},
            results_channel=_channel(sent),
            guild=_guild(),
            round_number=5,
            track_name="Monaco",
            label="Final Results",
            bot=_bot(db_path),
        )

    assert captured["division_name"] == "Main"
    assert captured["division_tier"] == 1
    assert captured["season_number"] == 3
    assert captured["race_name"] == "Monaco"


# ── Failure isolation and the retry path (T043, T046, T047) ───────────────


async def test_a_discord_failure_posting_the_image_falls_back_to_the_text(tmp_path):
    """XIV.7 and FR-042 — a service fault is not a generation fault, and text is what
    gets posted and, if need be, enqueued for retry."""
    import discord

    from services import image_results_post
    from services.results_post_service import post_session_results

    db_path, session_result = await _seeded(tmp_path)
    png = tmp_path / "results.png"
    png.write_bytes(b"\x89PNG")

    sent: list = []
    attempts: list[str] = []
    channel = AsyncMock()

    async def send(content=None, **kwargs):
        if "file" in kwargs:
            attempts.append("image")
            raise discord.HTTPException(MagicMock(status=503), "service unavailable")
        attempts.append("text")
        sent.append((content, kwargs))
        message = MagicMock()
        message.id = 4242
        return message

    channel.send = send

    with patch.object(
        image_results_post, "build_drawing", AsyncMock(return_value=_drawing())
    ), patch.object(
        image_results_post, "render_png", AsyncMock(return_value=_decision(png=png))
    ):
        await post_session_results(
            db_path=db_path,
            session_result=session_result,
            driver_rows=[],
            points_map={},
            results_channel=channel,
            guild=_guild(),
            round_number=5,
            track_name="Monaco",
            label="Final Results",
            bot=_bot(db_path),
        )

    assert attempts == ["image", "text"]
    assert len(sent) == 1 and "Final Results" in sent[0][0]


async def test_an_unexpected_fault_in_the_image_path_never_blocks_the_posting(tmp_path):
    """The unit of failure is one graphic (XIV.4): the league still gets its results."""
    from services import image_results_post
    from services.results_post_service import post_session_results

    db_path, session_result = await _seeded(tmp_path)
    sent: list = []

    with patch.object(
        image_results_post,
        "try_post",
        AsyncMock(side_effect=RuntimeError("something unforeseen")),
    ):
        await post_session_results(
            db_path=db_path,
            session_result=session_result,
            driver_rows=[],
            points_map={},
            results_channel=_channel(sent),
            guild=_guild(),
            round_number=5,
            track_name="Monaco",
            label="Final Results",
            bot=_bot(db_path),
        )

    assert len(sent) == 1
    assert "Final Results" in sent[0][0]
    assert "file" not in sent[0][1]


async def test_a_resolution_fault_is_reported_and_the_text_still_posts(tmp_path):
    from services import image_results_post
    from services.results_post_service import post_session_results

    db_path, session_result = await _seeded(tmp_path)
    sent: list = []
    bot = _bot(db_path)

    with patch.object(
        image_results_post,
        "build_drawing",
        AsyncMock(side_effect=RuntimeError("no name could be resolved")),
    ):
        await post_session_results(
            db_path=db_path,
            session_result=session_result,
            driver_rows=[],
            points_map={},
            results_channel=_channel(sent),
            guild=_guild(),
            round_number=5,
            track_name="Monaco",
            label="Final Results",
            bot=bot,
        )

    bot.output_router.post_log.assert_awaited()
    assert len(sent) == 1 and "file" not in sent[0][1]


# ── The rendered file does not outlive the posting attempt ────────────────
#
# Both outcomes, because they are the whole point: a transport failure re-posts the
# textual table and never the picture, so a kept file would be a file nothing reads.


def _render_artifact(tmp_path, name="results_race_template.png"):
    """A PNG sitting where `render` puts one, so the ownership guard recognises it."""
    directory = tmp_path / "f1bot_render_results"
    directory.mkdir()
    png = directory / name
    png.write_bytes(b"\x89PNG")
    return png


async def test_the_rendered_file_is_gone_once_the_graphic_has_posted(tmp_path):
    from services import image_results_post
    from services.results_post_service import post_session_results

    db_path, session_result = await _seeded(tmp_path)
    png = _render_artifact(tmp_path)

    with patch.object(
        image_results_post, "build_drawing", AsyncMock(return_value=_drawing())
    ), patch.object(
        image_results_post, "render_png", AsyncMock(return_value=_decision(png=png))
    ):
        await post_session_results(
            db_path=db_path,
            session_result=session_result,
            driver_rows=[],
            points_map={},
            results_channel=_channel([]),
            guild=_guild(),
            round_number=5,
            track_name="Monaco",
            label="Final Results",
            bot=_bot(db_path),
        )

    assert not png.exists()
    assert not png.parent.exists()


async def test_the_rendered_file_is_gone_when_the_send_fails(tmp_path):
    """The textual table goes to the retry queue; the picture goes nowhere at all."""
    import discord

    from services import image_results_post
    from services.results_post_service import post_session_results

    db_path, session_result = await _seeded(tmp_path)
    png = _render_artifact(tmp_path)

    channel = AsyncMock()
    sent: list = []

    async def send(content=None, **kwargs):
        if "file" in kwargs:
            raise discord.HTTPException(MagicMock(status=500), "upload failed")
        sent.append((content, kwargs))
        return MagicMock(id=4242)

    channel.send = send

    with patch.object(
        image_results_post, "build_drawing", AsyncMock(return_value=_drawing())
    ), patch.object(
        image_results_post, "render_png", AsyncMock(return_value=_decision(png=png))
    ):
        await post_session_results(
            db_path=db_path,
            session_result=session_result,
            driver_rows=[],
            points_map={},
            results_channel=channel,
            guild=_guild(),
            round_number=5,
            track_name="Monaco",
            label="Final Results",
            bot=_bot(db_path),
        )

    assert not png.exists(), "a failed upload must not strand the picture"
    assert sent, "the textual table still posts"
