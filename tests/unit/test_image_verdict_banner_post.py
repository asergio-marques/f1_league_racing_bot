"""Heading a batch of verdicts with a banner (052).

Discord is stubbed throughout: nothing here needs a running bot, a gateway connection or a
real server.

The rules these pin are the ones a later reader could plausibly undo:

* one banner per batch, posted **before** the first card and **only** if a card follows;
* the message carries no text, the attachment's filename being what a search has to go on;
* with the aspect off the verdicts channel reads exactly as it did before the feature;
* a banner that cannot be drawn never costs the league a verdict.
"""
from __future__ import annotations

import contextlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services import image_verdict_banner_post as banner  # noqa: E402
from services import verdict_announcement_service as vas  # noqa: E402


# ── Stubs ─────────────────────────────────────────────────────────────────


class _Channel:
    def __init__(self, guild_id: int = 99) -> None:
        self.sent: list[tuple[str | None, object]] = []
        self.guild = type("_Guild", (), {"id": guild_id, "get_role": lambda self, r: None})()

    async def send(self, content=None, *, file=None, **_kwargs):
        self.sent.append((content, file))


class _Bot:
    def __init__(self, channel) -> None:
        self.db_path = ":memory:"
        self._channel = channel

    def get_channel(self, _id):
        return self._channel


class _State:
    """One review's worth of applied penalties."""

    def __init__(self, count: int = 2) -> None:
        self.db_path = ":memory:"
        self.round_id = 1
        self.records = [
            {"race_result_id": index, "qual_result_id": None, "driver_user_id": 100 + index,
             "penalty_type": "TIME_PENALTY", "time_seconds": 5,
             "description": "Contact.", "justification": "Reviewed.",
             "team_role_id": None}
            for index in range(1, count + 1)
        ]


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
    """The announcement path with its database, its cards and its render stubbed out."""
    png = tmp_path / "season5_division1_round8_verdict_banner.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")

    state = {
        "context": dict(CONTEXT),
        "result_context": {
            "round_number": 8, "session_type": "FEATURE_RACE", "format": "ENDURANCE",
            "round_id": 1, "division_id": 7,
        },
        "banner_enabled": True,
        "banner_draws": True,
        "banner_problem": None,
        "sent_verdicts": [],
        "discarded": [],
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

    async def _team(_bot, _guild, **_kwargs):
        return None

    async def _enabled(_bot, _server_id):
        return state["banner_enabled"]

    async def _render(_bot, _server_id, _drawing, **_kwargs):
        return banner.BannerRender(
            png=state["png"] if state["banner_draws"] else None,
            problem=state["banner_problem"],
        )

    async def _report(_bot, _server_id, _what, _detail):
        return None

    def _discard(render, attachment=None):
        state["discarded"].append(getattr(render, "png", None))

    from services import image_verdict_post

    monkeypatch.setattr(vas, "_get_announcement_context", _context)
    monkeypatch.setattr(vas, "_get_result_context", _result_context)
    monkeypatch.setattr(vas, "_send_verdict", _send_verdict)
    monkeypatch.setattr(vas, "get_connection", _connection)
    monkeypatch.setattr(image_verdict_post, "team_name_for_entry", _team)

    monkeypatch.setattr(banner, "banner_enabled", _enabled)
    monkeypatch.setattr(banner, "render_banner", _render)
    monkeypatch.setattr(banner, "report", _report)
    monkeypatch.setattr(banner, "report_notices", _report)
    monkeypatch.setattr(banner, "discard", _discard)
    return state


# ── One banner per batch, and only where a card follows ───────────────────


async def test_a_batch_of_two_posts_exactly_one_banner(flow):
    channel = _Channel()
    await vas.post_penalty_announcements(_Bot(channel), _State(2), _State(2).records)

    banners = [entry for entry in channel.sent if entry[1] is not None]
    assert len(banners) == 1
    assert len(flow["sent_verdicts"]) == 2


async def test_the_banner_stands_before_the_first_card(flow):
    channel = _Channel()
    await vas.post_penalty_announcements(_Bot(channel), _State(2), _State(2).records)

    assert channel.sent[0][1] is not None, "the banner is the first thing posted"
    assert channel.sent[1][0] == "<@101>"


async def test_a_batch_that_produces_no_card_posts_no_banner(flow):
    """Every record failing to resolve leaves nothing to head.

    Posted eagerly the banner would stand alone over an empty run, which is why it is
    posted lazily, immediately before the first card that actually goes out.
    """
    flow["result_context"] = {}
    channel = _Channel()
    await vas.post_penalty_announcements(_Bot(channel), _State(2), _State(2).records)

    assert channel.sent == []
    assert flow["sent_verdicts"] == []


async def test_an_appeal_batch_is_headed_too(flow):
    channel = _Channel()
    await vas.post_appeal_announcements(_Bot(channel), _State(1), _State(1).records)

    banners = [entry for entry in channel.sent if entry[1] is not None]
    assert len(banners) == 1


# ── The message, and the filename that has to stand in for it ─────────────


async def test_the_banner_message_carries_no_text_at_all(flow):
    channel = _Channel()
    await vas.post_penalty_announcements(_Bot(channel), _State(1), _State(1).records)

    content, file = channel.sent[0]
    assert file is not None
    assert content is None


async def test_the_attachment_is_named_for_the_round_it_heads():
    """The only handle a Discord search has, the message carrying no text."""
    from utils.image_naming import stem_for_drawing

    drawing = banner.build_drawing(
        season_number=5, division_name="Pit Wall Premier", division_tier=1,
        round_number=8, race_name="British Grand Prix", country_name="United Kingdom",
    )
    assert stem_for_drawing(drawing, banner.BANNER_TEMPLATE_KEY) == (
        "season5_division1_round8_verdict_banner"
    )


# ── The aspect off, and the render failing ────────────────────────────────


async def test_with_the_aspect_off_the_channel_reads_as_it_always_did(flow):
    """The banner is additive: nothing stood above a run of verdicts before it."""
    flow["banner_enabled"] = False
    channel = _Channel()
    await vas.post_penalty_announcements(_Bot(channel), _State(2), _State(2).records)

    assert [content for content, _file in channel.sent] == ["<@101>", "<@102>"]
    assert all(file is None for _content, file in channel.sent)


async def test_a_render_that_fails_heads_the_batch_in_words(flow):
    flow["banner_draws"] = False
    flow["banner_problem"] = "RASTERISER"
    channel = _Channel()
    await vas.post_penalty_announcements(_Bot(channel), _State(1), _State(1).records)

    assert channel.sent[0] == ("**Season 5 Pit Wall Premier Round 8**", None)
    assert len(flow["sent_verdicts"]) == 1


async def test_a_banner_that_raises_never_costs_the_league_its_verdicts(flow, monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(banner, "try_post", _boom)
    channel = _Channel()
    await vas.post_penalty_announcements(_Bot(channel), _State(2), _State(2).records)

    assert len(flow["sent_verdicts"]) == 2


async def test_the_banner_is_discarded_once_posted(flow):
    channel = _Channel()
    await vas.post_penalty_announcements(_Bot(channel), _State(1), _State(1).records)

    assert flow["discarded"] == [flow["png"]]


# ── heading_text ──────────────────────────────────────────────────────────


def test_the_written_heading_names_season_division_and_round():
    drawing = banner.build_drawing(
        season_number=5, division_name="Pit Wall Premier", division_tier=1,
        round_number=8, race_name=None, country_name=None,
    )
    assert banner.heading_text(drawing) == "**Season 5 Pit Wall Premier Round 8**"


def test_an_unnumbered_season_leaves_the_prefix_out():
    drawing = banner.build_drawing(
        season_number=None, division_name="Elite", division_tier=None,
        round_number=3, race_name=None, country_name=None,
    )
    assert banner.heading_text(drawing) == "**Elite Round 3**"
