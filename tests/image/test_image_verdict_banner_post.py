"""Heading a batch of verdicts with a banner (052).

Discord is stubbed throughout: nothing here needs a running bot, a gateway connection or a
real server.

The rules these pin are the ones a later reader could plausibly undo. Since #439 a review's
verdicts are announced by its `announce_verdict` jobs on the change queue, and the rules about the
heading over a review's verdicts are pinned there, through the queue
(`tests/results/test_report_approval_change.py`, `test_appeals_approval_change.py` and
`test_verdict_announcement_jobs.py`); what stays here is the banner itself, the poster
`/attendance sync` shares across its sanctions, and the written heading:

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

import pytest

from leaguebot.image.services import image_verdict_banner_post as banner
from leaguebot.results.services import verdict_announcement_service as vas


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

    async def _enabled(_bot):
        return state["banner_enabled"]

    async def _render(_bot, _drawing, **_kwargs):
        return banner.BannerRender(
            png=state["png"] if state["banner_draws"] else None,
            problem=state["banner_problem"],
        )

    async def _report(_bot, _what, _detail):
        return None

    def _discard(render, attachment=None):
        state["discarded"].append(getattr(render, "png", None))

    from leaguebot.image.services import image_verdict_post

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


# ── The message, and the filename that has to stand in for it ─────────────


def _drawing():
    return banner.build_drawing(
        season_number=5, division_name="Pit Wall Premier", division_tier=1,
        round_number=8, race_name="British Grand Prix", country_name="United Kingdom",
    )


async def test_the_banner_message_carries_no_text_at_all(flow):
    channel = _Channel()
    await banner.try_post(_Bot(channel), channel, _drawing())

    assert len(channel.sent) == 1
    content, file = channel.sent[0]
    assert file is not None
    assert content is None


async def test_the_attachment_is_named_for_the_round_it_heads():
    """The only handle a Discord search has, the message carrying no text."""
    from leaguebot.image.utils.image_naming import stem_for_drawing

    drawing = banner.build_drawing(
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
    flow["banner_enabled"] = False

    sent = object()

    class _Recording(_Channel):
        async def send(self, content=None, *, file=None, **_kwargs):
            await super().send(content, file=file)
            return sent

    channel = _Recording()
    drawing = banner.build_drawing(
        season_number=5, division_name="Pit Wall Premier", division_tier=1,
        round_number=8, race_name=None, country_name=None,
    )

    assert await banner.try_post(_Bot(channel), channel, drawing) is sent
    assert channel.sent == [("**Season 5 Pit Wall Premier Round 8**", None)]


async def test_the_banner_is_discarded_once_posted(flow):
    channel = _Channel()
    await banner.try_post(_Bot(channel), channel, _drawing())

    assert flow["discarded"] == [flow["png"]]


# ── One banner per approval, sanctions included ───────────────────────────


async def test_a_shared_poster_fires_once_across_several_paths(flow):
    """The device `/attendance sync` uses to head one round's sanctions.

    The autosack and the autoreserve paths each call it before their first card, into the same
    channel for the same round. One poster covers both, and the second path finds it spent.
    """
    channel = _Channel()
    bot = _Bot(channel)
    head = vas.banner_for_round(bot, ":memory:", 1)

    await head()  # as the autosack path would call it
    await head()  # and the autoreserve path, later in the same run

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


async def test_an_approval_with_nothing_to_post_posts_no_banner(flow, monkeypatch):
    """**No verdicts and no sanctions means no banner** — and no query either.

    The rule the three posting paths each hold to separately, asserted once as the rule it
    is. Every one of them calls the poster immediately before a verdict that is actually
    going out and never before deciding there is one, so an approval that applies no
    penalty and sanctions nobody never calls it at all. `finalize_penalty_review` builds
    the poster unconditionally, so building one must itself cost nothing.
    """

    async def _boom(*_a, **_k):
        raise AssertionError("the context was read for a banner nobody asked for")

    monkeypatch.setattr(vas, "_get_announcement_context", _boom)
    channel = _Channel()
    vas.banner_for_round(_Bot(channel), ":memory:", 1)  # built, never called
    assert channel.sent == []


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
    state = {"sent_verdicts": [], "png": png, "row": dict(CONTEXT)}

    @contextlib.asynccontextmanager
    async def _connection(_db_path):
        class _Cursor:
            async def fetchone(self):
                return state["row"]

        class _Db:
            async def execute(self, *_a, **_k):
                return _Cursor()

        yield _Db()

    async def _send_verdict(_bot, channel, **kwargs):
        state["sent_verdicts"].append(kwargs)
        await channel.send(f"<@{kwargs['driver_discord_id']}>")

    async def _enabled(_bot):
        return True

    async def _render(_bot, _drawing, **_kwargs):
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


def _poster(message):
    """A banner poster that has put up *message* — or none, where the switch is off."""
    async def post():
        return None

    post.message = message
    return post


@pytest.mark.parametrize("banner", [SimpleNamespace(id=6001), None])
async def test_a_sanction_card_notes_its_own_posters_banner(sanction, monkeypatch, banner):
    """The banner the card's poster put up, and no other (decided 2026-09-21). Marked, it is
    kept by every replay, so the card is never left bare. Where the poster put up none — the
    switch off, or the post failing — the banner before it in the channel heads another run,
    and marking it would keep it for ever."""
    marked: list = []

    async def _send(_bot, channel, **kwargs):
        await channel.send(f"<@{kwargs['driver_discord_id']}>")
        return SimpleNamespace(id=7001)

    async def _mark(_db_path, noted):
        marked.append(noted)

    monkeypatch.setattr(vas, "_send_verdict", _send)
    monkeypatch.setattr(vas, "_mark_banner_over_sanction", _mark)
    channel = _Channel()

    await _autosanction(_Bot(channel), channel, head=_poster(banner))

    assert marked == [banner]


async def test_a_shared_poster_remembers_the_banner_it_put_up(flow):
    """What a sanction card further down the same approval reads to know its header."""

    class _Answering(_Channel):
        async def send(self, content=None, *, file=None, **_kwargs):
            await super().send(content, file=file)
            return SimpleNamespace(id=6001)

    channel = _Answering()
    head = vas.banner_for_round(_Bot(channel), ":memory:", 1)
    assert head.message is None

    await head()

    assert head.message.id == 6001


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


async def test_a_sanction_that_is_never_announced_raises_no_banner(sanction):
    """A division with no verdicts channel posts neither the sanction nor a header.

    The `head()` call sits below every early return of `post_autosanction_announcement`
    for exactly this: a banner over a sanction that was skipped names a round to nobody.
    """
    sanction["row"] = dict(CONTEXT, penalty_channel_id=None)
    channel = _Channel()
    await _autosanction(_Bot(channel), channel)

    assert channel.sent == []
    assert sanction["sent_verdicts"] == []


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

    from leaguebot.attendance.services import attendance_service

    source = inspect.getsource(attendance_service)
    blocks = source.split("post_autosanction_announcement(")[1:]
    assert len(blocks) == 1, "only the enforcement loop, for both sanctions, announces"
    for block in blocks:
        assert "head=head," in block[:600]


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
