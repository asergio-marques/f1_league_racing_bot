"""Posting a verdict as a graphic — T028-T034 (US2) and T039-T043 (US3).

Written against specs/043-verdicts-image-generation/contracts/verdicts-posting.md and
Constitution XIV.7, XIV.8 and XIV.17.

Discord is stubbed throughout: nothing here needs a running bot, a gateway connection or a
real server. That is full system testing and is done by hand against quickstart.md.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services import verdict_announcement_service as vas  # noqa: E402
from services.image_verdict_service import VerdictKind  # noqa: E402


# ── Stubs ─────────────────────────────────────────────────────────────────


class _Channel:
    def __init__(self, guild_id: int = 99) -> None:
        self.sent: list[tuple[str, object]] = []
        self.guild = type("_Guild", (), {"id": guild_id, "get_role": lambda self, r: None})()

    async def send(self, content=None, *, file=None, **_kwargs):
        self.sent.append((content, file))


class _Bot:
    def __init__(self) -> None:
        self.db_path = ":memory:"


@pytest.fixture()
def channel():
    return _Channel()


@pytest.fixture()
def stub_image_path(monkeypatch, tmp_path):
    """Make the image path answer without a database, a config or a rasteriser."""
    png = tmp_path / "verdict.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")

    state = {
        "enabled": True,
        "draws": True,
        "notices": [],
        "problem": None,
        "built": [],
        "reported": [],
        "rendered": [],
    }

    async def _enabled(_bot, _server_id):
        return state["enabled"]

    async def _build(_bot, **kwargs):
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

        state["rendered"].append(drawing)
        return VerdictRender(
            png=png if state["draws"] else None,
            notices=list(state["notices"]),
            problem=state["problem"],
        )

    async def _report(_bot, _server_id, what, detail):
        state["reported"].append(("problem", what, detail))

    async def _report_notices(_bot, _server_id, what, notices):
        state["reported"].append(("notices", what, list(notices)))

    async def _team(_bot, _guild, **_kwargs):
        return "Red Bull"

    from services import image_verdict_post

    monkeypatch.setattr(image_verdict_post, "verdicts_enabled", _enabled)
    monkeypatch.setattr(image_verdict_post, "build_drawing", _build)
    monkeypatch.setattr(image_verdict_post, "render_verdict", _render)
    monkeypatch.setattr(image_verdict_post, "report", _report)
    monkeypatch.setattr(image_verdict_post, "report_notices", _report_notices)
    monkeypatch.setattr(image_verdict_post, "team_name_for_entry", _team)
    return state


async def _send(channel, *, kind=VerdictKind.PENALTY, **overrides):
    values = dict(
        server_id=99,
        db_path=":memory:",
        round_id=1,
        kind=kind,
        season_number=3,
        division_name="Pro Division",
        round_number=7,
        session_label="Feature Race",
        driver_discord_id=123,
        driver_display_name="Ada Lovelace",
        driver_name="Ada Lovelace",
        penalty_description="5 seconds added",
        description_text="Contact at turn four.",
        justification_text="Video evidence reviewed.",
        team_name="Red Bull",
    )
    values.update(overrides)
    await vas._send_verdict(_Bot(), channel, **values)


# ── T028: the message is the mention and nothing besides ──────────────────


@pytest.mark.asyncio
async def test_the_message_carries_the_mention_and_nothing_besides(channel, stub_image_path):
    await _send(channel)

    (content, file), = channel.sent
    assert content == "<@123>"
    assert file is not None


@pytest.mark.asyncio
async def test_the_message_carries_no_trailing_display_name(channel, stub_image_path):
    """The textual announcement appends it; on the image path the name is on the canvas."""
    await _send(channel)

    (content, _file), = channel.sent
    assert "Ada Lovelace" not in content


@pytest.mark.asyncio
async def test_the_announcement_body_is_nowhere_in_the_message(channel, stub_image_path):
    await _send(channel)

    (content, _file), = channel.sent
    for moved in ("Penalty", "Description", "Justification", "Round 7", "seconds added"):
        assert moved not in content


# ── T029: the appeal stands beside the penalty, editing nothing ───────────


@pytest.mark.asyncio
async def test_an_appeal_draws_its_own_stage(channel, stub_image_path):
    await _send(channel, kind=VerdictKind.APPEAL)

    drawing, = stub_image_path["rendered"]
    assert drawing.stage == "Appeal"


@pytest.mark.asyncio
async def test_posting_a_verdict_never_edits_or_deletes_anything(channel, stub_image_path):
    """A verdict is static: posted once, and never touched again (XIV.17)."""
    await _send(channel)
    await _send(channel, kind=VerdictKind.APPEAL)

    assert len(channel.sent) == 2
    assert not hasattr(channel, "edited")
    assert not hasattr(channel, "deleted")


# ── T030: nothing is persisted ────────────────────────────────────────────


def test_the_posting_module_persists_no_message_id():
    """No table records a verdict's message, so there is no state to reconcile."""
    import inspect

    from services import image_verdict_post

    source = inspect.getsource(image_verdict_post)
    # SQL statements, not the prose: the module's own docstring explains why
    # delete-and-repost does not arise for a static type.
    for writing in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert writing not in source.upper(), f"the verdict flow must not {writing.strip()}"


def test_the_posting_module_never_edits_or_deletes_a_message():
    import inspect

    from services import image_verdict_post

    source = inspect.getsource(image_verdict_post)
    assert ".edit(" not in source
    assert ".delete(" not in source


# ── T032/T033: the render never gates, and one failure costs one graphic ──


@pytest.mark.asyncio
async def test_a_failed_render_falls_back_to_the_textual_announcement(
    channel, stub_image_path
):
    stub_image_path["draws"] = False
    await _send(channel)

    (content, file), = channel.sent
    assert file is None
    assert "**Penalty**: 5 seconds added" in content
    assert "<@123>" in content


@pytest.mark.asyncio
async def test_the_toggle_being_off_posts_the_text_unchanged(channel, stub_image_path):
    stub_image_path["enabled"] = False
    await _send(channel)

    (content, file), = channel.sent
    assert file is None
    assert "**Justification**: Video evidence reviewed." in content
    assert stub_image_path["rendered"] == [], "nothing may be rendered with the toggle off"


@pytest.mark.asyncio
async def test_one_verdict_failing_does_not_stop_the_next(channel, stub_image_path):
    stub_image_path["draws"] = False
    await _send(channel)
    stub_image_path["draws"] = True
    await _send(channel, driver_discord_id=456)

    assert channel.sent[0][1] is None, "the first fell back to text"
    assert channel.sent[1][1] is not None, "the second still drew"


@pytest.mark.asyncio
async def test_an_exception_in_the_image_path_still_posts_the_announcement(
    channel, stub_image_path, monkeypatch
):
    """A graphic never costs a league its announcement (XIV.7's precondition clause)."""
    from services import image_verdict_post

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("rasteriser exploded")

    monkeypatch.setattr(image_verdict_post, "render_verdict", _boom)
    await _send(channel)

    (content, file), = channel.sent
    assert file is None
    assert "**Description**: Contact at turn four." in content


# ── T039-T041: the attendance sanction ────────────────────────────────────


@pytest.mark.asyncio
async def test_an_attendance_sanction_names_no_session_and_no_team(channel, stub_image_path):
    await _send(
        channel,
        kind=VerdictKind.ATTENDANCE_SANCTION,
        session_label=None,
        team_name=None,
        penalty_description="Sacked",
    )

    drawing, = stub_image_path["rendered"]
    assert drawing.stage == "Attendance Sanction"
    assert drawing.session_name is None
    assert drawing.team_name is None


@pytest.mark.asyncio
async def test_the_composed_justification_reaches_the_canvas_as_a_name(
    channel, stub_image_path
):
    """The attendance module writes `<@id> (name)`; the graphic mentions nobody."""
    await _send(
        channel,
        kind=VerdictKind.ATTENDANCE_SANCTION,
        session_label=None,
        team_name=None,
        justification_text="<@123> (Ada Lovelace) has reached the 12 point limit.",
    )

    drawing, = stub_image_path["rendered"]
    assert "<@" not in drawing.justification
    assert drawing.justification == "Ada Lovelace has reached the 12 point limit."


@pytest.mark.asyncio
async def test_a_sanction_that_falls_back_says_attendance_sanction_in_its_heading(
    channel, stub_image_path
):
    """The message has nowhere else to put the label; the graphic puts it on the stage."""
    stub_image_path["draws"] = False
    await _send(
        channel,
        kind=VerdictKind.ATTENDANCE_SANCTION,
        session_label=None,
        team_name=None,
        penalty_description="Sacked",
    )

    (content, _file), = channel.sent
    assert "Attendance Sanction" in content


# ── The absent description and justification (FR-033) ─────────────────────


@pytest.mark.asyncio
async def test_the_graphic_carries_the_placeholder_without_channel_emphasis(
    channel, stub_image_path
):
    await _send(
        channel,
        description_text=vas.NOT_PROVIDED,
        justification_text=vas.NOT_PROVIDED,
    )

    drawing, = stub_image_path["rendered"]
    assert drawing.description == "(not provided)"
    assert "*" not in drawing.description
    assert "*" not in drawing.justification


@pytest.mark.asyncio
async def test_the_message_keeps_the_emphasis_it_always_applied(channel, stub_image_path):
    stub_image_path["draws"] = False
    await _send(
        channel,
        description_text=vas.NOT_PROVIDED,
        justification_text=vas.NOT_PROVIDED,
    )

    (content, _file), = channel.sent
    assert "*(not provided)*" in content


# ── T053-T057 groundwork: notices reach the log, never the verdicts channel ─


@pytest.mark.asyncio
async def test_notices_are_reported_and_never_sent_to_the_verdicts_channel(
    channel, stub_image_path
):
    from models.image_module import RenderNotice

    stub_image_path["notices"] = [
        RenderNotice(
            image_type="verdicts_template",
            notice_kind="WRAP_TRUNCATED",
            detail="`justification` was cut at the floor.",
        )
    ]
    await _send(channel)

    kinds = [entry[0] for entry in stub_image_path["reported"]]
    assert "notices" in kinds

    (content, _file), = channel.sent
    assert content == "<@123>", "no notice may reach a channel drivers read"


@pytest.mark.asyncio
async def test_a_notice_names_the_season_division_round_session_and_driver(
    channel, stub_image_path
):
    from models.image_module import RenderNotice

    stub_image_path["notices"] = [
        RenderNotice(
            image_type="verdicts_template",
            notice_kind="WRAP_TRUNCATED",
            detail="`justification` was cut at the floor.",
        )
    ]
    await _send(channel)

    _kind, subject, _payload = stub_image_path["reported"][0]
    for named in ("Season 3", "Pro Division", "Round 7", "Feature Race", "Ada Lovelace"):
        assert named in subject


def test_an_attendance_pardon_reaches_no_verdict_announcement_and_so_no_graphic():
    """A pardon is no verdict: it is a logging-channel record, whatever the toggle says.

    Asserted structurally because it is a fact about *where* the announcement is called from:
    the two enforcement sites, and nowhere else. A pardon changes the points a round conferred
    and announces nothing.
    """
    import inspect

    from services import attendance_service

    source = inspect.getsource(attendance_service)
    calls = source.count("post_autosanction_announcement(")
    assert calls == 2, "only the autosack and autoreserve enforcements may announce"

    for block in source.split("post_autosanction_announcement(")[1:]:
        preceding = source.split(block)[0][-600:] if block else ""
        assert "pardon" not in preceding.lower().split("async def")[-1]
