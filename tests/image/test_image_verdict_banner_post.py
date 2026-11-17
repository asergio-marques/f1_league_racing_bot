"""Heading a batch of verdicts with a banner (052).

Discord is stubbed throughout: nothing here needs a running bot, a gateway connection or a
real server.

The rules these pin are the ones a later reader could plausibly undo. Since #439 a review's
verdicts are announced by its `announce_verdict` jobs on the change queue, and the rules about the
heading over a review's verdicts are pinned there, through the queue
(`tests/results/test_report_approval_change.py`, `test_appeals_approval_change.py` and
`test_verdict_announcement_jobs.py`); what stays here is the banner itself, the heading of a
sanction `/attendance sync` asks the queue to announce, and the written heading:

* one banner per **approval**, posted **before** the first card and **only** if a card follows
  — an attendance sanction is a verdict and is headed like one, so the sanctions a penalty
  approval enforces fall under that approval's banner rather than raising a second, and
  sanctions firing where no penalty was applied raise one of their own;
* a drawn banner's message carries no text, the attachment's filename being what a search has
  to go on;
* every batch is headed, the aspect choosing only whether in a picture or in words — with it
  off, the module off or the template unusable, the heading is posted as text (#246);
* a header that cannot be posted, drawn or written, never costs the league a verdict.
"""
from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.image.services.aspects import image_verdict_banner_post as banner
from leaguebot.image.models import verdict_banner_drawing
from leaguebot.results.services.review import verdict_announcement_service as vas
from tests.support.change_queue import in_a_job


# ── Stubs ─────────────────────────────────────────────────────────────────


class _Channel:
    """A verdicts channel keeping what is sent to it, and refusing it where *refuses*."""

    id = 4242

    def __init__(self, guild_id: int = 99, *, refuses: bool = False) -> None:
        self.sent: list[tuple[str | None, object]] = []
        self.mentions: list[object] = []
        self.refuses = refuses
        self.guild = type("_Guild", (), {"id": guild_id, "get_role": lambda self, r: None})()

    async def send(self, content=None, *, file=None, allowed_mentions=None, **_kwargs):
        if self.refuses:
            raise discord.HTTPException(MagicMock(status=500), "upload failed")
        self.sent.append((content, file))
        self.mentions.append(allowed_mentions)
        return SimpleNamespace(id=777)


class _Bot:
    def __init__(self, channel) -> None:
        self.db_path = ":memory:"
        self._channel = channel

    def get_channel(self, _id):
        return self._channel


CONTEXT = {
    "season_number": 5,
    "division_name": "Pit Wall Premier",
    "division_tier": 1,
    "penalty_channel_id": 4242,
    "round_number": 8,
    "race_name": "British Grand Prix",
    "country_name": "United Kingdom",
}


@pytest.fixture()
def flow(monkeypatch, tmp_path):
    """The announcement path with its database, its cards and its render stubbed out, and the
    image drawing the heading job is handed: its gate open for the `verdict_banner` aspect alone
    while `banner_wanted`, and its render drawing the png while `banner_draws`."""
    from leaguebot.image.services.render.image_drawing import ImageDrawing

    png = tmp_path / "season5_division1_round8_verdict_banner.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")

    state = {
        "context": dict(CONTEXT),
        "result_context": {
            "round_number": 8, "session_type": "FEATURE_RACE", "format": "ENDURANCE",
            "round_id": 1, "division_id": 7,
        },
        "banner_wanted": True,
        "banner_draws": True,
        "banner_problem": None,
        "sent_verdicts": [],
        "png": png,
    }

    async def _context(_db_path, _round_id):
        return dict(state["context"])

    async def _result_context(_db_path, _race, _qual):
        return dict(state["result_context"]) if state["result_context"] else {}

    async def _send_verdict(_bot, channel, **kwargs):
        state["sent_verdicts"].append(kwargs)
        await channel.send(f"<@{kwargs['driver_discord_id']}>")

    @contextlib.asynccontextmanager
    async def _connection(_db_path):
        class _Cursor:
            async def fetchone(self):
                return None

        class _Db:
            async def execute(self, *_a, **_k):
                return _Cursor()

        yield _Db()

    async def _team(*_args, **_kwargs):
        return None

    async def _wanted(_self, aspect, _template_key=None):
        return aspect == banner.BANNER_ASPECT and state["banner_wanted"]

    async def _render(_config, _renderer, _drawing, **_kwargs):
        return banner.BannerRender(
            png=state["png"] if state["banner_draws"] else None,
            problem=state["banner_problem"],
        )

    monkeypatch.setattr(vas, "_get_announcement_context", _context)
    monkeypatch.setattr(vas, "_get_result_context", _result_context)
    monkeypatch.setattr(vas, "_send_verdict", _send_verdict)
    monkeypatch.setattr(vas, "get_connection", _connection)
    monkeypatch.setattr(vas, "team_name_for_entry", _team)

    monkeypatch.setattr(ImageDrawing, "aspect_wanted", _wanted)
    monkeypatch.setattr(banner, "render_banner", _render)
    state["drawing"] = ImageDrawing(
        MagicMock(), MagicMock(), MagicMock(),
        renderer=MagicMock(), post_log=AsyncMock(), db_path=":memory:",
    )
    return state


async def _head(flow, channel):
    """Run the results heading job's step over round 8's verdicts in *channel*, on its first try,
    with the image drawing it is handed."""
    from leaguebot.results.services.review import review_verdicts

    ctx = SimpleNamespace(
        step_payload={"round_id": 1, "round_number": 8}, db_path=":memory:",
        bot=_Bot(channel), kept=None, tries=0,
    )
    with in_a_job():
        return await review_verdicts._announce_heading(ctx, flow["drawing"])


# ── The message, and the filename that has to stand in for it ─────────────


async def test_the_banner_message_carries_no_text_at_all(flow):
    """The heading job posts the banner the drawing hands back as a message of the picture alone,
    notifying nobody."""
    channel = _Channel()

    await _head(flow, channel)

    assert len(channel.sent) == 1
    content, file = channel.sent[0]
    assert file is not None
    assert content is None
    [mentions] = channel.mentions
    assert (mentions.everyone, mentions.users, mentions.roles) == (False, False, False)


async def test_the_attachment_is_named_for_the_round_it_heads():
    """The only handle a Discord search has, the message carrying no text."""
    from leaguebot.image.utils.image_naming import stem_for_drawing

    drawing = verdict_banner_drawing.resolve_drawing(
        season_number=5, division_name="Pit Wall Premier", division_tier=1,
        round_number=8, race_name="British Grand Prix", country_name="United Kingdom",
    )
    assert stem_for_drawing(drawing, banner.BANNER_TEMPLATE_KEY) == (
        "season5_division1_round8_verdict_banner"
    )


# ── The aspect off, and the render failing ────────────────────────────────


async def test_a_text_heading_is_returned_so_it_can_be_taken_down(flow):
    """The message is what an amendment records and later removes (#345).

    A heading that went unrecorded would be left standing over the empty space where the run
    it headed used to be.
    """
    flow["banner_wanted"] = False
    channel = _Channel()

    result = await _head(flow, channel)

    assert result.result == {"message_id": 777, "channel_id": channel.id}
    assert channel.sent == [("**Season 5 Pit Wall Premier Round 8**", None)]


@pytest.mark.parametrize("refuses", [False, True], ids=["posted", "refused"])
async def test_the_banner_is_discarded_once_posted(flow, monkeypatch, refuses):
    """The banner's file is discarded through the drawing once its post is made, whether Discord
    took it or refused it, so no rendered png outlives the heading."""
    from leaguebot.framework.models.change import StepFailedOnDiscord

    discard = MagicMock()
    monkeypatch.setattr(flow["drawing"], "discard_attachment", discard)
    channel = _Channel(refuses=refuses)

    if refuses:
        with pytest.raises(StepFailedOnDiscord):
            await _head(flow, channel)
    else:
        await _head(flow, channel)

    [call] = discard.call_args_list
    [attachment] = call.args
    assert attachment.filename == flow["png"].name


# ── An attendance sanction is a verdict, and is headed like one ───────────
#
# A sanction is posted into the same channel, for the same round, as the round's verdicts, so it
# is headed: by the round's banner where one stands, and by one of its own where none does
# (decided 2026-09-09). `/attendance sync` is one change on the queue, whose heading job heads its
# cards; a sanction standing alone heading itself is pinned in
# tests/attendance/test_attendance_sync_change.py, and the banner marked as heading a sanction card
# by test_a_sanction_card_marks_exactly_its_banner.


# ── heading_text ──────────────────────────────────────────────────────────


def test_the_written_heading_names_season_division_and_round():
    drawing = verdict_banner_drawing.resolve_drawing(
        season_number=5, division_name="Pit Wall Premier", division_tier=1,
        round_number=8, race_name=None, country_name=None,
    )
    assert verdict_banner_drawing.heading_text(drawing) == "**Season 5 Pit Wall Premier Round 8**"


def test_an_unnumbered_season_leaves_the_prefix_out():
    drawing = verdict_banner_drawing.resolve_drawing(
        season_number=None, division_name="Elite", division_tier=None,
        round_number=3, race_name=None, country_name=None,
    )
    assert verdict_banner_drawing.heading_text(drawing) == "**Elite Round 3**"
