"""Heading a batch of verdicts with a banner (052).

Discord is stubbed throughout: nothing here needs a running bot, a gateway connection or a
real server.

The rules these pin are the ones a later reader could plausibly undo:

* one banner per **approval**, posted **before** the first card and **only** if a card follows
  — an attendance sanction is a verdict and is headed like one, so the sanctions a penalty
  approval enforces fall under that approval's banner rather than raising a second, and
  sanctions firing where no penalty was applied raise one of their own;
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


# ── One banner per approval, sanctions included ───────────────────────────


async def test_a_shared_poster_fires_once_across_several_paths(flow):
    """The device `finalize_penalty_review` uses to head a whole approval.

    It posts the penalty verdicts and then, further down the same call, the attendance
    sanctions that approval enforced — into the same channel for the same round. One poster
    covers both, and the second path finds it spent.
    """
    channel = _Channel()
    bot = _Bot(channel)
    head = vas.banner_for_round(bot, ":memory:", 1)

    await vas.post_penalty_announcements(bot, _State(1), _State(1).records, head=head)
    await head()  # as the attendance pipeline would call it, later in the same approval

    banners = [entry for entry in channel.sent if entry[1] is not None]
    assert len(banners) == 1


async def test_a_spent_poster_is_not_re_resolved(flow, monkeypatch):
    """Cheap as well as correct: the second call makes no query and builds no drawing."""
    calls = []

    async def _counting_context(db_path, round_id):
        calls.append(round_id)
        return dict(CONTEXT)

    monkeypatch.setattr(vas, "_get_announcement_context", _counting_context)
    head = vas.banner_for_round(_Bot(_Channel()), ":memory:", 1)
    await head()
    await head()
    assert calls == [1]


async def test_a_poster_never_called_makes_no_query_at_all(flow, monkeypatch):
    """An approval applying nothing and sanctioning nobody must cost nothing."""

    async def _boom(*_a, **_k):
        raise AssertionError("the context was read for a banner nobody asked for")

    monkeypatch.setattr(vas, "_get_announcement_context", _boom)
    vas.banner_for_round(_Bot(_Channel()), ":memory:", 1)  # built, never called


async def test_a_poster_survives_a_round_with_no_context(flow, monkeypatch):
    async def _none(_db_path, _round_id):
        return {}

    monkeypatch.setattr(vas, "_get_announcement_context", _none)
    channel = _Channel()
    await vas.banner_for_round(_Bot(channel), ":memory:", 1)()
    assert channel.sent == []


async def test_a_poster_says_nothing_where_no_verdicts_channel_is_configured(flow, monkeypatch):
    async def _no_channel(_db_path, _round_id):
        return dict(CONTEXT, penalty_channel_id=None)

    monkeypatch.setattr(vas, "_get_announcement_context", _no_channel)
    channel = _Channel()
    await vas.banner_for_round(_Bot(channel), ":memory:", 1)()
    assert channel.sent == []


# ── An attendance sanction is a verdict, and is headed like one ───────────
#
# The reasoning that first left these bare was wrong twice over: they are a *batch* (a loop
# over one round's drivers in `enforce_attendance_sanctions`), and they are posted into the
# same channel, for the same round, further down the same approval that posted the penalty
# verdicts. So they are headed — by that approval's banner where one was raised, and by one
# of their own where none was (decided 2026-09-09).


@pytest.fixture()
def sanction(monkeypatch, tmp_path):
    """`post_autosanction_announcement` with its database and its card stubbed out."""
    png = tmp_path / "banner.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    state = {"sent_verdicts": [], "png": png}

    @contextlib.asynccontextmanager
    async def _connection(_db_path):
        class _Cursor:
            async def fetchone(self):
                return dict(CONTEXT)

        class _Db:
            async def execute(self, *_a, **_k):
                return _Cursor()

        yield _Db()

    async def _send_verdict(_bot, channel, **kwargs):
        state["sent_verdicts"].append(kwargs)
        await channel.send(f"<@{kwargs['driver_discord_id']}>")

    async def _enabled(_bot, _server_id):
        return True

    async def _render(_bot, _server_id, _drawing, **_kwargs):
        return banner.BannerRender(png=state["png"])

    monkeypatch.setattr(vas, "get_connection", _connection)
    monkeypatch.setattr(vas, "_send_verdict", _send_verdict)
    monkeypatch.setattr(vas, "_get_announcement_context",
                        lambda _db, _round: _async_dict(dict(CONTEXT)))
    monkeypatch.setattr(banner, "banner_enabled", _enabled)
    monkeypatch.setattr(banner, "render_banner", _render)
    monkeypatch.setattr(banner, "discard", lambda *_a, **_k: None)
    return state


def _async_dict(value):
    async def _call():
        return value

    return _call()


async def _autosanction(bot, channel, *, driver=501, head=None):
    await vas.post_autosanction_announcement(
        bot=bot,
        db_path=":memory:",
        round_id=1,
        driver_discord_id=driver,
        driver_display_name=None,
        sanction_type="AUTOSACK",
        threshold=12,
        head=head,
    )


async def test_a_sanction_standing_alone_heads_itself(sanction):
    """The case a per-function poster left bare: a clean round, no penalty, one sacking."""
    channel = _Channel()
    await _autosanction(_Bot(channel), channel)

    assert channel.sent[0][1] is not None, "the banner is the first thing posted"
    assert channel.sent[1][0] == "<@501>"


async def test_a_round_sanctioning_three_drivers_raises_one_banner(sanction):
    """`enforce_attendance_sanctions` builds one poster and passes it to every call."""
    channel = _Channel()
    bot = _Bot(channel)
    head = vas.banner_for_round(bot, ":memory:", 1)
    for driver in (501, 502, 503):
        await _autosanction(bot, channel, driver=driver, head=head)

    banners = [entry for entry in channel.sent if entry[1] is not None]
    assert len(banners) == 1
    assert len(sanction["sent_verdicts"]) == 3


async def test_sanctions_of_a_penalty_approval_fall_under_its_banner(sanction):
    """One header over one run, not one over the penalties and another over the sacking."""
    channel = _Channel()
    bot = _Bot(channel)
    head = vas.banner_for_round(bot, ":memory:", 1)
    await head()  # as the penalty verdicts would have raised it
    await _autosanction(bot, channel, head=head)

    banners = [entry for entry in channel.sent if entry[1] is not None]
    assert len(banners) == 1


def test_both_enforcement_sites_hand_the_poster_on():
    """Structural, because it is a fact about *where* the poster is threaded.

    A sanction announced without it would build one of its own, and a round sanctioning
    three drivers would raise three banners.
    """
    import inspect

    from services import attendance_service

    source = inspect.getsource(attendance_service)
    blocks = source.split("post_autosanction_announcement(")[1:]
    assert len(blocks) == 2, "only the autosack and autoreserve enforcements announce"
    for block in blocks:
        assert "head=head," in block[:600]


def test_a_penalty_approval_shares_one_poster_with_the_attendance_pipeline():
    """The whole point of `banner_for_round`: one header over one approval."""
    import inspect

    from services import result_submission_service

    source = inspect.getsource(result_submission_service.finalize_penalty_review)
    assert source.count("banner_for_round(") == 1, "one poster, built once"
    assert "post_penalty_announcements(\n                    bot, state, applied_records, head=" in source
    assert "head=_verdict_banner," in source


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
