"""Importing a points configuration from XML, by modal or by file.

Issue #208. `_run_xml_import` is the shared body behind both `/results config xml-import`
paths and was uncovered end to end, as were the attachment guards in front of it.

**Every outcome is audited, not just the successes.** An import that failed is exactly the event
a league asks about later — the points did not change and nobody remembers why — so each of the
five ways it can end writes a log line naming which one it was. That is parametrised here
precisely because the audit is easy to drop from a branch when a sixth is added: the failure is
silent by construction, and only a test that insists on the line will catch it.

**The three stages refuse in order and stop.** Parse, then the monotonic ordering check, then
the write. A payload that fails to parse is never ordering-checked and never reaches the
database, so an XML file with several faults reports the first stage's, not a mixture — the
alternative is a wall of errors where the later ones are consequences of the earlier.

**Ordering is a semantic check and it stands apart from parsing.** A file where P2 scores more
than P1 is well-formed XML and perfectly valid to a parser; it is a league's mistake and it
would score a season wrongly and quietly. Refusing it is the whole point of `validate_payload`
sitting between the parse and the write.

**Warnings ride along with a success.** A duplicate position id is a mistake worth telling a
manager about, but the import is well defined without it — refusing would make a manager edit a
file to remove a line the bot had already decided how to treat.

**The attachment path guards size, emptiness and encoding before parsing.** All three are
answered in terms a manager can act on rather than as a parser error about byte 0. The 100 KB
limit is a real ceiling: a points config is a few hundred rows, and anything larger is a wrong
file rather than a big one.

**A file import defers; a modal import cannot.** `send_modal` must be an interaction's first
response, so the attachment path defers itself and the modal path defers inside `on_submit`.
`_run_xml_import` therefore always answers through `followup` — a fresh response would be a 404
on both paths.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import leaguebot.results.cogs.results_cog as results_cog
from leaguebot.results.cogs.results_cog import ResultsCog, XmlImportModal, _run_xml_import
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.services.points_config_service import create_config
from tests.support.undecorate import undecorate

SERVER_ID = 10108
ACTOR_ID = 77
CONFIG = "Standard"

VALID_XML = """
<config>
  <session>
    <type>Feature Race</type>
    <position id="1">25</position>
    <position id="2">18</position>
    <fastest-lap limit="10">2</fastest-lap>
  </session>
  <session>
    <type>Sprint Race</type>
    <position id="1">8</position>
    <position id="2">7</position>
  </session>
</config>
"""

MALFORMED_XML = "<config><session><type>Feature Race</type></config>"

UNORDERED_XML = """
<config>
  <session>
    <type>Feature Race</type>
    <position id="1">10</position>
    <position id="2">25</position>
  </session>
</config>
"""

DUPLICATE_XML = """
<config>
  <session>
    <type>Feature Race</type>
    <position id="1">25</position>
    <position id="1">18</position>
  </session>
</config>
"""


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name: str = "xml_import", with_config: bool = True) -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.commit()
    if with_config:
        await create_config(db_path, CONFIG)
    return db_path


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Manager"
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.send_message = AsyncMock(
        side_effect=AssertionError("replied through response; after a defer that is a 404")
    )
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.client = MagicMock()
    interaction.client.output_router = MagicMock()
    interaction.client.output_router.post_log = AsyncMock(return_value=None)
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0]) for call in interaction.followup.send.await_args_list if call.args
    )


def _audited(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.client.output_router.post_log.await_args_list
    )


async def _import(db_path, interaction, xml=VALID_XML, *, config=CONFIG):
    await _run_xml_import(interaction, xml, config, db_path)


async def _rows(db_path: str) -> list[tuple[str, int, int]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT session_type, position, points FROM points_config_entries "
            "ORDER BY session_type, position"
        )
        return [
            (r["session_type"], r["position"], r["points"]) for r in await cursor.fetchall()
        ]


# ---------------------------------------------------------------------------
# A clean import
# ---------------------------------------------------------------------------


async def test_a_valid_payload_is_written(tmp_path):
    db_path = await _make_db(tmp_path)
    interaction = _interaction()

    await _import(db_path, interaction)

    rows = await _rows(db_path)
    assert ("FEATURE_RACE", 1, 25) in rows
    assert ("SPRINT_RACE", 2, 7) in rows


async def test_a_fastest_lap_bonus_is_written(tmp_path):
    db_path = await _make_db(tmp_path)

    await _import(db_path, _interaction())

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT session_type, fl_points, fl_position_limit FROM points_config_fl"
        )
        rows = [dict(r) for r in await cursor.fetchall()]
    assert rows == [
        {"session_type": "FEATURE_RACE", "fl_points": 2, "fl_position_limit": 10}
    ]


async def test_the_summary_counts_what_changed_per_session(tmp_path):
    """A manager who has just imported a file needs to see what the bot read out of it —
    the file is not what they will be scored on, the rows are."""
    db_path = await _make_db(tmp_path)
    interaction = _interaction()

    await _import(db_path, interaction)

    replied = _replied(interaction)
    assert "Feature Race" in replied
    assert "2 position(s) updated" in replied
    assert "Sprint Race" in replied


async def test_the_summary_names_the_fastest_lap_and_its_limit(tmp_path):
    """The limit is the part a league forgets it set, and the part that decides whether P11
    keeps the point."""
    db_path = await _make_db(tmp_path)
    interaction = _interaction()

    await _import(db_path, interaction)

    assert "FL: 2 pts, limit P10" in _replied(interaction)


async def test_a_fastest_lap_without_a_limit_says_nothing_about_one(tmp_path):
    """An unlimited bonus and a bonus limited to P10 are different rules, and reporting a
    limit that was not given would read as one."""
    db_path = await _make_db(tmp_path)
    interaction = _interaction()
    xml = VALID_XML.replace('<fastest-lap limit="10">2</fastest-lap>', "<fastest-lap>2</fastest-lap>")

    await _import(db_path, interaction, xml)

    assert "FL: 2 pts" in _replied(interaction)
    assert "limit P" not in _replied(interaction)


async def test_an_empty_config_reports_no_changes(tmp_path):
    """Rather than an empty success, which reads as though something was imported."""
    db_path = await _make_db(tmp_path)
    interaction = _interaction()

    await _import(db_path, interaction, "<config></config>")

    assert "No changes." in _replied(interaction)


async def test_a_warning_rides_along_with_the_success(tmp_path):
    """A duplicate position id is worth telling a manager about, but the import is well
    defined without their intervention — refusing would make them edit a file to remove a
    line the bot had already decided how to treat."""
    db_path = await _make_db(tmp_path)
    interaction = _interaction()

    await _import(db_path, interaction, DUPLICATE_XML)

    replied = _replied(interaction)
    assert "updated" in replied
    assert "Warnings:" in replied


# ---------------------------------------------------------------------------
# The three stages, in order
# ---------------------------------------------------------------------------


async def test_a_malformed_file_is_refused_with_its_errors(tmp_path):
    db_path = await _make_db(tmp_path)
    interaction = _interaction()

    await _import(db_path, interaction, MALFORMED_XML)

    assert "parse/validation failed" in _replied(interaction)
    assert await _rows(db_path) == []


async def test_points_that_rise_with_position_are_refused(tmp_path):
    """Well-formed XML and perfectly valid to a parser — and it would score every race of
    the season wrongly and quietly. This check is why the ordering stage exists."""
    db_path = await _make_db(tmp_path)
    interaction = _interaction()

    await _import(db_path, interaction, UNORDERED_XML)

    assert "ordering validation failed" in _replied(interaction)
    assert await _rows(db_path) == []


async def test_a_malformed_file_is_never_ordering_checked(tmp_path):
    """The stages stop at the first failure, so a file with several faults reports the
    stage it actually failed rather than a mixture whose later entries are consequences."""
    db_path = await _make_db(tmp_path)
    interaction = _interaction()

    await _import(db_path, interaction, MALFORMED_XML)

    assert "ordering validation" not in _replied(interaction)


async def test_importing_into_a_config_that_does_not_exist_is_refused(tmp_path):
    """`xml-import` fills a configuration; it does not create one. A typo in the name would
    otherwise quietly make a second config nobody attached to a season."""
    db_path = await _make_db(tmp_path, name="xml_noconfig", with_config=False)
    interaction = _interaction()

    await _import(db_path, interaction)

    assert "not found" in _replied(interaction)
    assert CONFIG in _replied(interaction)


async def test_a_database_failure_is_reported_without_its_text(tmp_path, monkeypatch):
    """Neither a traceback nor the database's own words are actionable at a manager. The
    standard failure reply says the fault is the bot's, that nothing from the import was
    saved, and that the import can be run again."""
    db_path = await _make_db(tmp_path)
    interaction = _interaction()

    async def _boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("leaguebot.results.services.points_config_service.xml_import_config", _boom)

    await _import(db_path, interaction)

    replied = _replied(interaction)
    assert "stopped on a fault in the bot, not on anything you entered" in replied
    assert "Nothing from the import was saved." in replied
    assert "Run the import again to retry." in replied
    assert "Database error" not in replied
    assert "database is locked" not in replied


async def test_an_xml_import_database_fault_names_the_config_in_one_failure_line(
    tmp_path, monkeypatch
):
    """One failure makes one line: the member, the import, the configuration and the kind of
    fault, and never the database's own words, which go to the host's log alone."""
    db_path = await _make_db(tmp_path)
    interaction = _interaction()
    interaction.command.qualified_name = "results config xml-import"

    async def _boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("leaguebot.results.services.points_config_service.xml_import_config", _boom)

    await _import(db_path, interaction)

    replied = _replied(interaction)
    assert "stopped on a fault in the bot" in replied
    assert "Nothing from the import was saved." in replied
    assert "Database error" not in replied and "database is locked" not in replied
    lines = [str(c.args[0]) for c in interaction.client.output_router.post_log.await_args_list]
    [line] = lines
    assert f"<@{ACTOR_ID}>" in line
    assert "/results config xml-import" in line
    assert CONFIG in line
    assert "OperationalError" in line
    assert "database is locked" not in line


# ---------------------------------------------------------------------------
# Every outcome is audited
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "xml,fragments",
    [
        pytest.param(VALID_XML, ["SUCCESS"], id="success"),
        pytest.param(
            MALFORMED_XML,
            ["⛔ ", f"refused for Manager (<@{ACTOR_ID}>)", "XML syntax error"],
            id="parse-error",
        ),
        pytest.param(
            UNORDERED_XML,
            ["⛔ ", f"refused for Manager (<@{ACTOR_ID}>)", "position 1 has 10 pts"],
            id="ordering",
        ),
    ],
)
async def test_the_outcome_reaches_the_log(tmp_path, xml, fragments):
    """An import that failed is exactly the event a league asks about later — the points did
    not change and nobody remembers why. A refusal is written in the standard refusal form,
    its detail as the reason."""
    db_path = await _make_db(tmp_path)
    interaction = _interaction()

    await _import(db_path, interaction, xml)

    [audited] = [str(c.args[0]) for c in interaction.client.output_router.post_log.await_args_list]
    for fragment in fragments:
        assert fragment in audited
    assert "FAILED (" not in audited


async def test_an_import_into_a_missing_config_is_audited(tmp_path):
    """In the standard refusal form, the configuration named."""
    db_path = await _make_db(tmp_path, name="xml_audit_missing", with_config=False)
    interaction = _interaction()

    await _import(db_path, interaction)

    [audited] = [str(c.args[0]) for c in interaction.client.output_router.post_log.await_args_list]
    assert audited.startswith("⛔ ")
    assert f"refused for Manager (<@{ACTOR_ID}>)" in audited
    assert "not found" in audited
    assert CONFIG in audited
    assert "FAILED (" not in audited


async def test_the_log_names_the_config_and_the_manager(tmp_path):
    """Two managers importing different configs on the same evening is ordinary, and a log
    line naming neither cannot be read back to either."""
    db_path = await _make_db(tmp_path)
    interaction = _interaction()

    await _import(db_path, interaction)

    audited = _audited(interaction)
    assert CONFIG in audited
    assert "Manager" in audited
    assert "/results config xml-import" in audited


@pytest.mark.xfail(
    strict=True, reason="#442: a successful import's log line counts its rows rather than listing them"
)
async def test_a_successful_import_counts_what_it_wrote_in_the_log(tmp_path):
    """The line states beneath it the values that were set: each session, and each position's
    points, and the fastest-lap bonus with its limit."""
    db_path = await _make_db(tmp_path)
    interaction = _interaction()

    await _import(db_path, interaction)

    audited = _audited(interaction)
    values = audited.split("\n", 1)[1] if "\n" in audited else ""
    for expected in ("Feature Race", "Sprint Race", "25", "18", "8", "7", "10"):
        assert expected in values, f"{expected!r} is not among the values logged"


# ---------------------------------------------------------------------------
# The attachment path in front of it
# ---------------------------------------------------------------------------


def _attachment(raw: bytes):
    attachment = MagicMock(spec=discord.Attachment)
    attachment.read = AsyncMock(return_value=raw)
    return attachment


def _cog(db_path: str, *, enabled: bool = True) -> ResultsCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=enabled)
    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = bot
    return cog


async def _xml_import_command(cog, interaction, *, file=None, name=CONFIG):
    body = undecorate(ResultsCog.config_xml_import)
    return await body(cog, interaction, name, file)


async def test_a_command_without_a_file_opens_the_modal(tmp_path):
    """`send_modal` must be an interaction's first response, so this path must not defer."""
    db_path = await _make_db(tmp_path, name="xml_modal")
    cog = _cog(db_path)
    interaction = _interaction()

    await _xml_import_command(cog, interaction)

    interaction.response.send_modal.assert_awaited_once()
    interaction.response.defer.assert_not_awaited()
    assert isinstance(interaction.response.send_modal.await_args.args[0], XmlImportModal)


async def test_a_command_with_a_file_defers_and_imports(tmp_path):
    """The file is read, parsed and written, none of which fits in three seconds reliably."""
    db_path = await _make_db(tmp_path, name="xml_file")
    cog = _cog(db_path)
    interaction = _interaction()

    await _xml_import_command(cog, interaction, file=_attachment(VALID_XML.encode()))

    interaction.response.defer.assert_awaited_once()
    interaction.response.send_modal.assert_not_awaited()
    assert ("FEATURE_RACE", 1, 25) in await _rows(db_path)


async def test_an_oversized_file_is_refused_before_it_is_parsed(tmp_path):
    """A points config is a few hundred rows; anything past 100 KB is the wrong file rather
    than a big one, and parsing it first is how a wrong file becomes a slow failure."""
    db_path = await _make_db(tmp_path, name="xml_big")
    cog = _cog(db_path)
    interaction = _interaction()

    await _xml_import_command(cog, interaction, file=_attachment(b"<config/>" + b"x" * 100_001))

    assert "too large" in _replied(interaction)
    assert await _rows(db_path) == []


async def test_an_empty_file_is_named_as_empty(tmp_path):
    """Rather than as an XML syntax error at byte 0, which sends a manager looking for a
    fault in a file that has nothing in it at all."""
    db_path = await _make_db(tmp_path, name="xml_empty")
    cog = _cog(db_path)
    interaction = _interaction()

    await _xml_import_command(cog, interaction, file=_attachment(b""))

    assert "empty" in _replied(interaction)


async def test_a_file_that_is_not_utf8_is_named_as_such(tmp_path):
    """A config exported from a Windows editor in the wrong encoding is a real thing to
    meet, and "could not be decoded" is what tells a manager to re-save it."""
    db_path = await _make_db(tmp_path, name="xml_encoding")
    cog = _cog(db_path)
    interaction = _interaction()

    await _xml_import_command(cog, interaction, file=_attachment(b"\xff\xfe<config/>"))

    assert "UTF-8" in _replied(interaction)


async def test_the_command_is_refused_while_the_module_is_off(tmp_path):
    """Importing points into a module nobody is running configures something no code reads,
    and the gate runs before either path is chosen."""
    db_path = await _make_db(tmp_path, name="xml_disabled")
    cog = _cog(db_path, enabled=False)
    interaction = _interaction()
    # The module gate replies before anything defers, so `response` is the right channel
    # for it — the guard the other tests carry would be wrong here.
    interaction.response.send_message = AsyncMock()

    await _xml_import_command(cog, interaction, file=_attachment(VALID_XML.encode()))

    interaction.response.send_modal.assert_not_awaited()
    assert await _rows(db_path) == []


async def test_the_modal_defers_before_importing(tmp_path):
    """The modal path cannot defer in the command — `send_modal` has to be the first
    response — so the deferral moves into `on_submit`, where the work is."""
    db_path = await _make_db(tmp_path, name="xml_modal_submit")
    modal = XmlImportModal(CONFIG, db_path)
    modal.xml_payload._value = VALID_XML  # type: ignore[attr-defined]
    interaction = _interaction()

    await modal.on_submit(interaction)

    interaction.response.defer.assert_awaited_once()
    assert ("FEATURE_RACE", 1, 25) in await _rows(db_path)


# ---------------------------------------------------------------------------
# Every refusal reaches the log channel, and every change is recorded (#442)
# ---------------------------------------------------------------------------


def _sent(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


#: Every refusal `/results config xml-import` makes: the file attached (None for the module
#: gate, which refuses before any file is read), whether a config exists, and a fragment of
#: the reply the line's reason carries.
XML_IMPORT_REFUSALS = [
    pytest.param(b"<config/>" + b"x" * 100_001, True, True, "too large", id="too-large"),
    pytest.param(b"", True, True, "empty", id="empty"),
    pytest.param(b"\xff\xfe<config/>", True, True, "UTF-8", id="not-utf8"),
    pytest.param(VALID_XML.encode(), True, False, "not enabled", id="module-off"),
    pytest.param(MALFORMED_XML.encode(), True, True, "XML syntax error", id="parse"),
    pytest.param(UNORDERED_XML.encode(), True, True, "position 1 has 10 pts", id="ordering"),
    pytest.param(VALID_XML.encode(), False, True, "not found", id="config-not-found"),
]


@pytest.mark.parametrize("raw, with_config, enabled, said", XML_IMPORT_REFUSALS)
async def test_every_xml_import_refusal_reaches_the_log_channel(
    tmp_path, raw, with_config, enabled, said
):
    """Each refusal answers the manager as before, saves nothing, and writes exactly one line
    in the standard refusal form, its detail as the reason."""
    db_path = await _make_db(tmp_path, name="xml_refusals", with_config=with_config)
    cog = _cog(db_path, enabled=enabled)
    interaction = _interaction()
    interaction.command.qualified_name = "results config xml-import"
    if not enabled:
        interaction.response.is_done = MagicMock(return_value=False)
        interaction.response.send_message = AsyncMock()

    await _xml_import_command(cog, interaction, file=_attachment(raw))

    assert said in _sent(interaction)
    assert await _rows(db_path) == []
    lines = [str(c.args[0]) for c in interaction.client.output_router.post_log.await_args_list]
    [line] = lines
    assert line.startswith("⛔ ")
    assert "/results config xml-import" in line
    assert f"refused for Manager (<@{ACTOR_ID}>)" in line
    assert said in line


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


async def _audit_rows(db_path) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT actor_id, actor_name, old_value, new_value, timestamp FROM audit_entries "
            "ORDER BY id"
        )
        return [dict(r) for r in await cursor.fetchall()]


def _values(text) -> list[str]:
    """Every value a JSON audit value carries, as text, however it is nested."""
    found: list[str] = []

    def _walk(node):
        if isinstance(node, dict):
            for value in node.values():
                _walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                _walk(value)
        else:
            found.append(str(node))

    _walk(json.loads(text) if text else None)
    return found


FL_ONLY_XML = """
<config>
  <session>
    <type>Feature Race</type>
    <fastest-lap limit="10">2</fastest-lap>
  </session>
</config>
"""


@pytest.mark.parametrize(
    "case",
    [
        *[
            pytest.param(
                case,
                marks=pytest.mark.xfail(
                    strict=True,
                    reason="#442: an XML import writes no audit entry for what it changed",
                ),
            )
            for case in ("positions", "fastest-lap")
        ],
        pytest.param(
            "log-line",
            marks=pytest.mark.xfail(
                strict=True,
                reason="#442: a successful import's log line counts its rows rather than "
                "listing the values set",
            ),
        ),
    ],
)
async def test_an_xml_import_records_each_change_from_what_to_what(tmp_path, case):
    """An import changes a configuration, so each change is recorded as an audit entry: who,
    when, and each position's points — or the fastest-lap bonus and its limit — from what to
    what. The log line states the values set."""
    from leaguebot.results.services.points_config_service import set_session_points
    from leaguebot.results.models.points_config import SessionType
    from leaguebot.results.services.points_config_service import xml_import_config
    from leaguebot.results.utils.xml_import import parse_xml_payload

    db_path = await _make_db(tmp_path, name=f"xml_audit_{case}")

    if case == "log-line":
        interaction = _interaction()
        await _import(db_path, interaction)
        audited = _audited(interaction)
        values = audited.split("\n", 1)[1] if "\n" in audited else ""
        for expected in ("Feature Race", "Sprint Race", "25", "18", "8", "7"):
            assert expected in values
        return

    if case == "positions":
        await set_session_points(db_path, CONFIG, SessionType.FEATURE_RACE, 1, 20)
        payload, _ = parse_xml_payload(
            VALID_XML.replace('<fastest-lap limit="10">2</fastest-lap>', "")
        )
    else:
        payload, _ = parse_xml_payload(FL_ONLY_XML)

    await xml_import_config(
        db_path, CONFIG, payload, actor_id=ACTOR_ID, actor_name="Manager#0001", now=NOW
    )

    rows = await _audit_rows(db_path)
    assert all(r["actor_id"] == ACTOR_ID for r in rows)
    assert all(r["actor_name"] == "Manager#0001" for r in rows)
    assert all(r["timestamp"] == NOW.isoformat() for r in rows)
    if case == "positions":
        # Feature Race P1 from 20 to 25, P2 new at 18; Sprint Race P1 new at 8, P2 new at 7.
        assert len(rows) == 4
        news = [_values(r["new_value"]) for r in rows]
        olds = [_values(r["old_value"]) for r in rows]
        changed = next(i for i, new in enumerate(news) if "25" in new)
        assert "20" in olds[changed], "the change is recorded from what it was"
        for points in ("18", "8", "7"):
            assert any(points in new for new in news)
    else:
        [row] = rows
        assert {"2", "10"} <= set(_values(row["new_value"]))
