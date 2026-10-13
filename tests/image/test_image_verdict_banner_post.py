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


# ── An attendance sanction is a verdict, and is headed like one ───────────
#
# A sanction is posted into the same channel, for the same round, as the round's verdicts, so it
# is headed: by the round's banner where one stands, and by one of its own where none does
# (decided 2026-09-09). Since #439 (slice 6b) `/attendance sync` asks the change queue to announce
# its sanctions, and the announcement's own heading job heads its cards; one banner over several
# cards, recorded with its job's mark, and a refused card are pinned in
# tests/attendance/test_attendance_sync.py, and the shared poster the sync built until then is
# gone with the announcer.

NO_SANCTIONS_ANNOUNCE = (
    "#439: /attendance sync posts the verdicts and banner of its sanctions itself, off the "
    "change queue"
)


@pytest.mark.xfail(strict=True, reason=NO_SANCTIONS_ANNOUNCE)
async def test_a_sanction_standing_alone_heads_itself(tmp_path):
    """Pro's round 3 had no penalty applied, so no heading stands in its verdicts channel (704).
    A sync moves Lewis to reserve and asks the queue to announce it, as the bot. The queue's
    first job posts a heading in 704; Lewis's card follows beneath it; and the heading is
    recorded as heading a sanction card, so no replay of the round's verdicts takes it down."""
    from leaguebot.core.db.database import get_connection
    from tests.attendance.test_attendance_sync import _ask_announcement, _cards
    from tests.results.test_report_approval_change import _cards_posted_for_real
    from tests.support import review_league as rl
    from tests.support.change_queue import run_queue

    league = await rl.review_league(tmp_path, attendance=True)
    _cards_posted_for_real(league)

    await _ask_announcement(league, (rl.LEWIS_PROFILE, rl.LEWIS))
    await run_queue(league.bot)

    assert await rl.stopped_at(league) is None
    assert _cards(league) == [rl.LEWIS_PROFILE]
    heading, _card = league.sent_to(rl.VERDICTS_CHANNEL)
    async with get_connection(league.db_path) as db:
        assert await vas._banners_heading_sanctions_on(db, [heading]) == {heading}


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
