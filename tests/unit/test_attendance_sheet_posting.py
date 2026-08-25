"""The attendance sheet's replacement ordering and the check-in deadline derivation.

Covers Constitution XIV.8 (produce before destroy) and XIV.7 (one derivation, living with the
data), and FR-045 / FR-026–FR-027 of
``specs/041-attendance-image-generation/spec.md``.

The ordering test is the one that matters most here. ``post_attendance_sheet`` used to delete
the previously posted sheet at the top of the function and send its successor some ninety lines
later, so a transient failure to post left the division with **no** sheet at all. The image path
falls back to this same function, which is why the ordering had to be corrected here rather than
beside it.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import aiosqlite
import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services import attendance_service  # noqa: E402
from services.attendance_service import (  # noqa: E402
    derive_checkin_deadline,
    post_attendance_sheet,
)


# ── The deadline derivation (T010/T011) ───────────────────────────────────


def test_the_deadline_is_the_round_start_less_the_configured_hours():
    start = datetime(2026, 8, 13, 20, 0, tzinfo=timezone.utc)
    assert derive_checkin_deadline(start, 6) == datetime(
        2026, 8, 13, 14, 0, tzinfo=timezone.utc
    )


def test_a_deadline_of_zero_hours_stands_at_the_rounds_own_start():
    """FR-026's stated case, needing no branch of its own."""
    start = datetime(2026, 8, 13, 20, 0, tzinfo=timezone.utc)
    assert derive_checkin_deadline(start, 0) == start


def test_the_deadline_is_timezone_aware_even_from_a_naive_round():
    naive = datetime(2026, 8, 13, 20, 0)
    result = derive_checkin_deadline(naive, 3)
    assert result.tzinfo is not None
    assert result == datetime(2026, 8, 13, 17, 0, tzinfo=timezone.utc)


def test_the_deadline_crosses_a_day_boundary_correctly():
    start = datetime(2026, 8, 13, 2, 0, tzinfo=timezone.utc)
    assert derive_checkin_deadline(start, 5) == datetime(
        2026, 8, 12, 21, 0, tzinfo=timezone.utc
    )


# ── Fakes for the posting path ────────────────────────────────────────────


class _HTTPFailure(discord.HTTPException):
    """A Discord failure that needs no response object to construct."""

    def __init__(self):  # noqa: D107 — deliberately bypasses the base signature
        pass


class _FakeMessage:
    def __init__(self, message_id: int, journal: list[str]):
        self.id = message_id
        self._journal = journal

    async def delete(self):
        self._journal.append(f"delete:{self.id}")


class _FakeChannel:
    """Records the order in which the flow sends and deletes."""

    def __init__(self, journal: list[str], *, send_fails: bool = False):
        self.journal = journal
        self.send_fails = send_fails
        self.sent_content: str | None = None
        self.sent_file = None
        self._next_id = 5000

    async def send(self, content, file=None):
        if self.send_fails:
            self.journal.append("send:FAILED")
            raise _HTTPFailure()
        self._next_id += 1
        self.sent_content = content
        self.sent_file = file
        self.journal.append(f"send:{self._next_id}")
        return _FakeMessage(self._next_id, self.journal)

    async def fetch_message(self, message_id: int):
        self.journal.append(f"fetch:{message_id}")
        return _FakeMessage(message_id, self.journal)


class _FakeMember:
    def __init__(self, name: str):
        self.display_name = name


class _FakeGuild:
    def __init__(self, channel, members: dict[int, str]):
        self._channel = channel
        self._members = members
        self.id = 1

    def get_channel(self, _channel_id):
        return self._channel

    def get_member(self, user_id):
        name = self._members.get(int(user_id))
        return _FakeMember(name) if name else None


def _fake_attachment(sentinel):
    """Stand in for a successfully drawn sheet without touching the render pipeline."""

    async def _attachment(*args, **kwargs):
        return sentinel

    return _attachment


@pytest.fixture
async def sheet_db(tmp_path):
    """The smallest schema ``post_attendance_sheet`` reads."""
    path = str(tmp_path / "sheet.db")
    async with aiosqlite.connect(path) as db:
        await db.executescript(
            """
            CREATE TABLE attendance_division_config (
                division_id           INTEGER PRIMARY KEY,
                server_id             INTEGER,
                rsvp_channel_id       TEXT,
                attendance_channel_id TEXT,
                attendance_message_id TEXT
            );
            CREATE TABLE driver_round_attendance (
                id                 INTEGER PRIMARY KEY,
                round_id           INTEGER,
                division_id        INTEGER,
                driver_profile_id  INTEGER,
                assigned_team_id   INTEGER,
                total_points_after INTEGER
            );
            CREATE TABLE driver_season_assignments (
                driver_profile_id INTEGER,
                team_seat_id      INTEGER
            );
            CREATE TABLE team_seats (
                id               INTEGER PRIMARY KEY,
                team_instance_id INTEGER
            );
            CREATE TABLE team_instances (
                id          INTEGER PRIMARY KEY,
                division_id INTEGER,
                is_reserve  INTEGER
            );
            CREATE TABLE driver_profiles (
                id                INTEGER PRIMARY KEY,
                discord_user_id   TEXT,
                test_display_name TEXT
            );
            CREATE TABLE attendance_config (
                server_id             INTEGER PRIMARY KEY,
                autoreserve_threshold INTEGER,
                autosack_threshold    INTEGER
            );
            CREATE TABLE seasons  (id INTEGER PRIMARY KEY, server_id INTEGER);
            CREATE TABLE divisions(id INTEGER PRIMARY KEY, season_id INTEGER, name TEXT);
            CREATE TABLE rounds (
                id           INTEGER PRIMARY KEY,
                division_id  INTEGER,
                round_number INTEGER,
                format       TEXT,
                track_name   TEXT,
                status       TEXT DEFAULT 'ACTIVE'
            );

            INSERT INTO seasons  VALUES (1, 1);
            INSERT INTO divisions VALUES (7, 1, 'Division 1');
            INSERT INTO rounds VALUES (3, 7, 3, 'NORMAL', 'Silverstone Circuit', 'ACTIVE');
            INSERT INTO rounds VALUES (9, 7, 9, 'NORMAL', 'Circuit Zandvoort', 'CANCELLED');
            INSERT INTO attendance_config VALUES (1, 10, 20);
            INSERT INTO team_instances VALUES (100, 7, 0);
            INSERT INTO team_seats     VALUES (200, 100);
            INSERT INTO driver_profiles VALUES (1, '111', NULL);
            INSERT INTO driver_season_assignments VALUES (1, 200);
            INSERT INTO driver_round_attendance
                (round_id, division_id, driver_profile_id, assigned_team_id, total_points_after)
                VALUES (3, 7, 1, NULL, 4);
            """
        )
        await db.commit()
    return path


async def _config(db_path, *, prior: str | None):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM attendance_division_config")
        await db.execute(
            "INSERT INTO attendance_division_config VALUES (7, 1, NULL, '900', ?)",
            (prior,),
        )
        await db.commit()


async def _stored_message_id(db_path) -> str | None:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT attendance_message_id FROM attendance_division_config WHERE division_id = 7"
        )
        row = await cur.fetchone()
    return row[0] if row else None


# ── The replacement ordering (T012–T014) ──────────────────────────────────


@pytest.mark.asyncio
async def test_the_replacement_is_posted_before_the_previous_sheet_is_deleted(sheet_db):
    """XIV.8: at no instant is the channel without a sheet."""
    await _config(sheet_db, prior="4242")
    journal: list[str] = []
    channel = _FakeChannel(journal)
    guild = _FakeGuild(channel, {111: "Ayrton"})

    await post_attendance_sheet(None, guild, sheet_db, round_id=3, division_id=7)

    sends = [i for i, e in enumerate(journal) if e.startswith("send:")]
    deletes = [i for i, e in enumerate(journal) if e.startswith("delete:")]
    assert sends and deletes, journal
    assert max(sends) < min(deletes), f"deleted before sending: {journal}"


@pytest.mark.asyncio
async def test_exactly_one_send_site_and_one_delete_site_are_exercised(sheet_db):
    await _config(sheet_db, prior="4242")
    journal: list[str] = []
    channel = _FakeChannel(journal)
    guild = _FakeGuild(channel, {111: "Ayrton"})

    await post_attendance_sheet(None, guild, sheet_db, round_id=3, division_id=7)

    assert len([e for e in journal if e.startswith("send:")]) == 1
    assert len([e for e in journal if e.startswith("delete:")]) == 1


@pytest.mark.asyncio
async def test_a_failed_post_leaves_the_previous_sheet_standing(sheet_db):
    """The whole point of the ordering: a failure costs the league nothing it had."""
    await _config(sheet_db, prior="4242")
    journal: list[str] = []
    channel = _FakeChannel(journal, send_fails=True)
    guild = _FakeGuild(channel, {111: "Ayrton"})

    await post_attendance_sheet(None, guild, sheet_db, round_id=3, division_id=7)

    assert not any(e.startswith("delete:") for e in journal), journal
    assert await _stored_message_id(sheet_db) == "4242"


@pytest.mark.asyncio
async def test_the_replacements_id_is_persisted(sheet_db):
    await _config(sheet_db, prior="4242")
    journal: list[str] = []
    channel = _FakeChannel(journal)
    guild = _FakeGuild(channel, {111: "Ayrton"})

    await post_attendance_sheet(None, guild, sheet_db, round_id=3, division_id=7)

    assert await _stored_message_id(sheet_db) == "5001"


@pytest.mark.asyncio
async def test_a_first_posting_deletes_nothing(sheet_db):
    await _config(sheet_db, prior=None)
    journal: list[str] = []
    channel = _FakeChannel(journal)
    guild = _FakeGuild(channel, {111: "Ayrton"})

    await post_attendance_sheet(None, guild, sheet_db, round_id=3, division_id=7)

    assert not any(e.startswith("delete:") for e in journal)
    assert await _stored_message_id(sheet_db) == "5001"


# ── The text is unchanged by the reorder (T014) ───────────────────────────


@pytest.mark.asyncio
async def test_the_composed_sheet_text_is_unchanged_by_the_reorder(sheet_db):
    """The reorder changed **when** the message is sent, never what it says."""
    await _config(sheet_db, prior=None)
    channel = _FakeChannel([])
    guild = _FakeGuild(channel, {111: "Ayrton"})

    await post_attendance_sheet(None, guild, sheet_db, round_id=3, division_id=7)

    assert channel.sent_content == (
        "**Attendance Standings**\n"
        "\n"
        "<@111> — 4 attendance points\n"
        "\n"
        "Drivers who reach 10 points will be moved to reserve.\n"
        "Drivers who reach 20 points will be removed from all driving roles in all divisions."
    )


@pytest.mark.asyncio
async def test_the_sanction_annotation_is_unchanged_by_the_reorder(sheet_db):
    await _config(sheet_db, prior=None)
    channel = _FakeChannel([])
    guild = _FakeGuild(channel, {111: "Ayrton"})

    await post_attendance_sheet(
        None, guild, sheet_db, round_id=3, division_id=7,
        sanctioned_profile_ids={1},
    )

    assert "*(reached point limit)*" in channel.sent_content


@pytest.mark.asyncio
async def test_with_a_graphic_the_message_carries_the_heading_alone(sheet_db, monkeypatch):
    """FR-043 / XIV.16: the graphic replaces the table, it does not decorate it.

    Posting the full textual body beside the attachment would tell a league everything twice
    and ping every driver from a message whose point is the picture — and the graphic draws
    names precisely so that it carries no mention.
    """
    monkeypatch.setattr(
        attendance_service, "_sheet_attachment", _fake_attachment(object())
    )
    await _config(sheet_db, prior=None)
    channel = _FakeChannel([])
    guild = _FakeGuild(channel, {111: "Ayrton"})

    await post_attendance_sheet(None, guild, sheet_db, round_id=3, division_id=7)

    assert channel.sent_content == "**Attendance Standings**"
    assert channel.sent_file is not None
    assert "<@111>" not in channel.sent_content
    assert "attendance point" not in channel.sent_content


@pytest.mark.asyncio
async def test_without_a_graphic_the_message_carries_the_whole_textual_sheet(sheet_db):
    """The fallback loses nothing: with no picture, the text says it all."""
    await _config(sheet_db, prior=None)
    channel = _FakeChannel([])
    guild = _FakeGuild(channel, {111: "Ayrton"})

    await post_attendance_sheet(None, guild, sheet_db, round_id=3, division_id=7)

    assert channel.sent_file is None
    assert "<@111>" in channel.sent_content
    assert "attendance point" in channel.sent_content


@pytest.mark.asyncio
async def test_a_failed_post_enqueues_the_textual_sheet_for_retry(sheet_db, monkeypatch):
    """FR-060 / XIV.8: a **service** failure retries as text, never as a rendered image.

    A retry queue is durable and outlives the state that filled it, so a picture retried an
    hour later is a picture of a division that has moved on. The text is composed at the moment
    it is finally sent.
    """
    from services import retry_service

    enqueued: list[dict] = []

    async def _fake_enqueue(db_path, server_id, channel_id, content, failure_reason):
        enqueued.append(
            {"channel_id": channel_id, "content": content, "reason": failure_reason}
        )

    monkeypatch.setattr(retry_service, "enqueue", _fake_enqueue)

    await _config(sheet_db, prior="4242")
    channel = _FakeChannel([], send_fails=True)
    guild = _FakeGuild(channel, {111: "Ayrton"})
    guild.id = 1

    await post_attendance_sheet(None, guild, sheet_db, round_id=3, division_id=7)

    assert len(enqueued) == 1
    assert enqueued[0]["channel_id"] == 900
    assert "Attendance Standings" in enqueued[0]["content"]
    assert "attendance sheet for division 7" in enqueued[0]["reason"]


@pytest.mark.asyncio
async def test_a_queue_failure_never_masks_the_original_posting_failure(
    sheet_db, monkeypatch
):
    from services import retry_service

    async def _boom(*args, **kwargs):
        raise RuntimeError("queue is down")

    monkeypatch.setattr(retry_service, "enqueue", _boom)

    await _config(sheet_db, prior="4242")
    channel = _FakeChannel([], send_fails=True)
    guild = _FakeGuild(channel, {111: "Ayrton"})
    guild.id = 1

    # Must not raise, and must still leave the prior sheet standing.
    await post_attendance_sheet(None, guild, sheet_db, round_id=3, division_id=7)
    assert await _stored_message_id(sheet_db) == "4242"


@pytest.mark.asyncio
async def test_a_cancelled_round_posts_nothing_and_generates_nothing(sheet_db):
    """FR-047: a cancelled round distributes no points and produces no sheet.

    The guard stands here as well as upstream so that no graphic is ever generated for a
    posting that will not happen (XIV.8 — no posting, no graphic).
    """
    await _config(sheet_db, prior="4242")
    journal: list[str] = []
    channel = _FakeChannel(journal)
    guild = _FakeGuild(channel, {111: "Ayrton"})

    await post_attendance_sheet(None, guild, sheet_db, round_id=9, division_id=7)

    assert journal == []
    assert await _stored_message_id(sheet_db) == "4242"


@pytest.mark.asyncio
async def test_no_channel_configured_posts_nothing_and_deletes_nothing(sheet_db):
    """FR-046: where the textual flow posts nothing, nothing happens at all."""
    async with aiosqlite.connect(sheet_db) as db:
        await db.execute("DELETE FROM attendance_division_config")
        await db.execute(
            "INSERT INTO attendance_division_config VALUES (7, 1, NULL, NULL, '4242')"
        )
        await db.commit()

    journal: list[str] = []
    channel = _FakeChannel(journal)
    guild = _FakeGuild(channel, {111: "Ayrton"})

    await post_attendance_sheet(None, guild, sheet_db, round_id=3, division_id=7)

    assert journal == []


# ── The sheet graphic does not outlive its posting attempt ────────────────


def _sheet_artifact(tmp_path):
    directory = tmp_path / "f1bot_render_attendance"
    directory.mkdir()
    png = directory / "attendance_template.png"
    png.write_bytes(b"\x89PNG")
    return png


def _real_attachment(png):
    """A `discord.File` over *png*, as `_sheet_attachment` builds for real."""
    import discord

    async def _attachment(*_args, **_kwargs):
        return discord.File(str(png), filename="attendance.png")

    return _attachment


@pytest.mark.asyncio
async def test_the_sheet_graphic_is_gone_once_it_has_posted(
    sheet_db, monkeypatch, tmp_path
):
    png = _sheet_artifact(tmp_path)
    monkeypatch.setattr(attendance_service, "_sheet_attachment", _real_attachment(png))
    await _config(sheet_db, prior=None)
    channel = _FakeChannel([])
    guild = _FakeGuild(channel, {111: "Ayrton"})

    await post_attendance_sheet(None, guild, sheet_db, round_id=3, division_id=7)

    assert channel.sent_file is not None, "the sheet still posts as a graphic"
    assert not png.exists()
    assert not png.parent.exists()


@pytest.mark.asyncio
async def test_the_sheet_graphic_is_gone_when_the_send_fails(
    sheet_db, monkeypatch, tmp_path
):
    """The textual sheet is enqueued for retry; the picture never is (XIV.8, FR-060)."""
    png = _sheet_artifact(tmp_path)
    monkeypatch.setattr(attendance_service, "_sheet_attachment", _real_attachment(png))
    await _config(sheet_db, prior="4242")
    channel = _FakeChannel([], send_fails=True)
    guild = _FakeGuild(channel, {111: "Ayrton"})

    await post_attendance_sheet(None, guild, sheet_db, round_id=3, division_id=7)

    assert not png.exists(), "a failed upload must not strand the sheet"
