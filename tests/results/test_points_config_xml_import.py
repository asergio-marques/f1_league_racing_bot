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
on both paths. Since the import became a settings change on the change queue (#439, slice 7)
that follow-up is the queue's acknowledgement, updated with the outcome once the change has run.

**The import runs on the change queue.** What was typed or attached is judged at once: the file's
size, emptiness and encoding, its parsing and its ordering, a configuration that does not exist
and the module being off are refused before anything is queued. The rest is saved when the change
runs, the reply and the line following. The tests drive the real command, file attached, or the
real form, on a league built by the migrations with the real services and queue, and run the
queue.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.results.cogs.results_cog import ResultsCog, XmlImportModal
from tests.support import saves
from tests.support.change_queue import MEMBER_ID, change_rows, run_queue
from tests.support.settings_league import (
    interaction as member_asks,
    log_lines,
    said,
    settings_league,
)
from tests.support.undecorate import undecorate

CONFIG = "Standard"
#: The member as a log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"

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


async def _league(tmp_path, *, with_config: bool = True, enabled: bool = True) -> Any:
    """A league with results on (*enabled*), holding CONFIG with an empty table where
    *with_config*."""
    league = await settings_league(tmp_path, modules=("results",) if enabled else ())
    if with_config:
        await saves.create_config(league.db_path, CONFIG)
    return league


def _attachment(raw: bytes):
    attachment = MagicMock(spec=discord.Attachment)
    attachment.read = AsyncMock(return_value=raw)
    return attachment


async def _xml_import_command(league, *, file=None, name=CONFIG) -> Any:
    """Run `/results config xml-import` as Admin, *file* attached where given, then the queue."""
    asked = member_asks(league.bot, "results config xml-import")
    asked.response.send_modal = AsyncMock()
    await undecorate(ResultsCog.config_xml_import)(ResultsCog(league.bot), asked, name, file)
    await run_queue(league.bot)
    return asked


async def _import(league, xml=VALID_XML, *, config=CONFIG) -> Any:
    """Import *xml* into *config* by the command, the file attached, then run the queue."""
    return await _xml_import_command(league, file=_attachment(xml.encode()), name=config)


def _replied(asked) -> str:
    return "\n".join(said(asked))


async def _audited(league) -> str:
    return "\n".join(await log_lines(league))


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
    league = await _league(tmp_path)

    await _import(league)

    rows = await _rows(league.db_path)
    assert ("FEATURE_RACE", 1, 25) in rows
    assert ("SPRINT_RACE", 2, 7) in rows


async def test_a_fastest_lap_bonus_is_written(tmp_path):
    league = await _league(tmp_path)

    await _import(league)

    async with get_connection(league.db_path) as db:
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
    league = await _league(tmp_path)

    asked = await _import(league)

    replied = _replied(asked)
    assert "Feature Race" in replied
    assert "2 position(s) updated" in replied
    assert "Sprint Race" in replied


async def test_the_summary_names_the_fastest_lap_and_its_limit(tmp_path):
    """The limit is the part a league forgets it set, and the part that decides whether P11
    keeps the point."""
    league = await _league(tmp_path)

    asked = await _import(league)

    assert "FL: 2 pts, limit P10" in _replied(asked)


async def test_a_fastest_lap_without_a_limit_says_nothing_about_one(tmp_path):
    """An unlimited bonus and a bonus limited to P10 are different rules, and reporting a
    limit that was not given would read as one."""
    league = await _league(tmp_path)
    xml = VALID_XML.replace('<fastest-lap limit="10">2</fastest-lap>', "<fastest-lap>2</fastest-lap>")

    asked = await _import(league, xml)

    assert "FL: 2 pts" in _replied(asked)
    assert "limit P" not in _replied(asked)


async def test_an_empty_config_reports_no_changes(tmp_path):
    """Rather than an empty success, which reads as though something was imported."""
    league = await _league(tmp_path)

    asked = await _import(league, "<config></config>")

    assert "No changes." in _replied(asked)


async def test_an_empty_import_records_that_nothing_changed(tmp_path):
    """An import of a file naming no session changes nothing: no audit entry, and one line
    in the success form saying nothing changed, never a success."""
    league = await _league(tmp_path)
    before = await _audit_rows(league.db_path)

    await _import(league, "<config></config>")

    assert await _audit_rows(league.db_path) == before
    [audited] = await log_lines(league)
    first = audited.split("\n", 1)[0]
    assert first == f"{NAMED} | /results config xml-import | Nothing changed"
    assert "SUCCESS" not in audited
    assert CONFIG in audited


async def test_a_warning_rides_along_with_the_success(tmp_path):
    """A duplicate position id is worth telling a manager about, but the import is well
    defined without their intervention — refusing would make them edit a file to remove a
    line the bot had already decided how to treat."""
    league = await _league(tmp_path)

    asked = await _import(league, DUPLICATE_XML)

    replied = _replied(asked)
    assert "updated" in replied
    assert "Warnings:" in replied


# ---------------------------------------------------------------------------
# The three stages, in order
# ---------------------------------------------------------------------------


async def test_a_malformed_file_is_refused_with_its_errors(tmp_path):
    league = await _league(tmp_path)

    asked = await _import(league, MALFORMED_XML)

    assert "parse/validation failed" in _replied(asked)
    assert await _rows(league.db_path) == []


async def test_points_that_rise_with_position_are_refused(tmp_path):
    """Well-formed XML and perfectly valid to a parser — and it would score every race of
    the season wrongly and quietly. This check is why the ordering stage exists."""
    league = await _league(tmp_path)

    asked = await _import(league, UNORDERED_XML)

    assert "ordering validation failed" in _replied(asked)
    assert await _rows(league.db_path) == []


async def test_a_malformed_file_is_never_ordering_checked(tmp_path):
    """The stages stop at the first failure, so a file with several faults reports the
    stage it actually failed rather than a mixture whose later entries are consequences."""
    league = await _league(tmp_path)

    asked = await _import(league, MALFORMED_XML)

    assert "ordering validation" not in _replied(asked)


async def test_importing_into_a_config_that_does_not_exist_is_refused(tmp_path):
    """`xml-import` fills a configuration; it does not create one. A typo in the name would
    otherwise quietly make a second config nobody attached to a season. The refusal is audited
    in the standard refusal form, the configuration named."""
    league = await _league(tmp_path, with_config=False)

    asked = await _import(league)

    assert "not found" in _replied(asked)
    assert CONFIG in _replied(asked)
    [audited] = await log_lines(league)
    assert CONFIG in audited
    assert "FAILED (" not in audited


async def _break_the_points_table(db_path: str) -> None:
    """Take CONFIG's points table out from under the import, so that its save faults on the
    database as a damaged file would: the configuration is still there to be checked."""
    async with get_connection(db_path) as db:
        await db.execute("ALTER TABLE points_config_entries RENAME TO points_config_entries_away")
        await db.commit()


async def _nothing_saved(db_path: str) -> None:
    async with get_connection(db_path) as db:
        for table in ("points_config_entries_away", "points_config_fl"):
            cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
            assert (await cursor.fetchone())[0] == 0, f"the import saved into {table}"


async def test_an_xml_import_database_fault_names_the_config_in_one_failure_line(tmp_path):
    """Neither a traceback nor the database's own words are actionable at a manager. The
    standard failure reply says the fault is the bot's, that nothing from the import was
    saved, and that the import can be run again. One failure makes one line: the member, the
    import, the configuration and the kind of fault, and never the database's own words, which
    go to the host's log alone."""
    league = await _league(tmp_path)
    await _break_the_points_table(league.db_path)

    asked = await _import(league)

    replied = _replied(asked)
    assert "stopped on a fault in the bot, not on anything you entered" in replied
    assert "Nothing from the import was saved." in replied
    assert "Run the import again to retry." in replied
    assert "Database error" not in replied and "no such table" not in replied
    await _nothing_saved(league.db_path)
    [line] = await log_lines(league)
    assert f"<@{MEMBER_ID}>" in line
    assert "/results config xml-import" in line
    assert CONFIG in line
    assert "OperationalError" in line
    assert "no such table" not in line


# ---------------------------------------------------------------------------
# Every outcome is audited
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "xml,fragments",
    [
        pytest.param(
            VALID_XML,
            [f"{NAMED} | /results config xml-import | Success", CONFIG],
            id="success",
        ),
        pytest.param(
            MALFORMED_XML,
            ["⛔ ", f"refused for {NAMED}", "XML syntax error"],
            id="parse-error",
        ),
        pytest.param(
            UNORDERED_XML,
            ["⛔ ", f"refused for {NAMED}", "position 1 has 10 pts"],
            id="ordering",
        ),
    ],
)
async def test_the_outcome_reaches_the_log(tmp_path, xml, fragments):
    """An import that failed is exactly the event a league asks about later — the points did
    not change and nobody remembers why. A refusal is written in the standard refusal form,
    its detail as the reason. A success names the configuration and the manager: two managers
    importing different configs on the same evening is ordinary, and a line naming neither
    cannot be read back to either."""
    league = await _league(tmp_path)

    await _import(league, xml)

    [audited] = await log_lines(league)
    for fragment in fragments:
        assert fragment in audited
    assert "FAILED (" not in audited


# ---------------------------------------------------------------------------
# The attachment path in front of it
# ---------------------------------------------------------------------------


async def test_a_command_without_a_file_opens_the_modal(tmp_path):
    """`send_modal` must be an interaction's first response, so this path must not defer."""
    league = await _league(tmp_path)

    asked = await _xml_import_command(league)

    asked.response.send_modal.assert_awaited_once()
    asked.response.defer.assert_not_awaited()
    assert isinstance(asked.response.send_modal.await_args.args[0], XmlImportModal)


async def test_a_command_with_a_file_defers_and_imports(tmp_path):
    """The file is read and parsed, which does not fit in three seconds reliably, so the command
    defers; the queue's acknowledgement follows as a follow-up, and the import is saved when the
    change runs."""
    league = await _league(tmp_path)

    asked = await _import(league)

    asked.response.defer.assert_awaited_once()
    asked.response.send_modal.assert_not_awaited()
    assert said(asked)[0].startswith("⏳ ")
    assert ("FEATURE_RACE", 1, 25) in await _rows(league.db_path)


async def test_the_modal_defers_before_importing(tmp_path):
    """The modal path cannot defer in the command — `send_modal` has to be the first
    response — so the deferral moves into `on_submit`, where the slow read is; the queue's
    acknowledgement follows as a follow-up, and the import is saved when the change runs."""
    league = await _league(tmp_path)
    modal = XmlImportModal(CONFIG, league.db_path)
    modal.xml_payload._value = VALID_XML  # type: ignore[attr-defined]
    asked = member_asks(league.bot)

    await modal.on_submit(asked)
    await run_queue(league.bot)

    asked.response.defer.assert_awaited_once()
    assert said(asked)[0].startswith("⏳ ")
    assert ("FEATURE_RACE", 1, 25) in await _rows(league.db_path)


# ---------------------------------------------------------------------------
# Every refusal reaches the log channel, and every change is recorded (#442)
# ---------------------------------------------------------------------------


#: Every refusal `/results config xml-import` makes: the file attached, whether a config exists,
#: whether the module is on, and a fragment of the reply the line's reason carries.
XML_IMPORT_REFUSALS = [
    pytest.param(b"<config/>" + b"x" * 100_001, True, True, "too large", id="too-large"),
    pytest.param(b"", True, True, "empty", id="empty"),
    pytest.param(b"\xff\xfe<config/>", True, True, "UTF-8", id="not-utf8"),
    pytest.param(VALID_XML.encode(), True, False, "not enabled", id="module-off"),
    pytest.param(MALFORMED_XML.encode(), True, True, "XML syntax error", id="parse"),
    pytest.param(UNORDERED_XML.encode(), True, True, "position 1 has 10 pts", id="ordering"),
    pytest.param(VALID_XML.encode(), False, True, "not found", id="config-not-found"),
]


@pytest.mark.parametrize("raw, with_config, enabled, said_", XML_IMPORT_REFUSALS)
async def test_every_xml_import_refusal_reaches_the_log_channel(
    tmp_path, raw, with_config, enabled, said_
):
    """Each refusal answers the manager at once as before, queues and saves nothing, and writes
    exactly one line in the standard refusal form, its detail as the reason.

    A file past 100 KB is refused before it is parsed, since parsing it first is how a wrong
    file becomes a slow failure. An empty file is named as empty, rather than as an XML syntax
    error at byte 0 that sends a manager looking for a fault in a file with nothing in it. A
    file exported in the wrong encoding, as a Windows editor can, is named as not UTF-8, which
    is what tells a manager to re-save it. With the module off an import would configure
    something no code reads, and the gate runs before either path is chosen, so a file
    attached opens no form."""
    league = await _league(tmp_path, with_config=with_config, enabled=enabled)

    asked = await _xml_import_command(league, file=_attachment(raw))

    asked.response.send_modal.assert_not_awaited()
    assert said_ in _replied(asked)
    assert await _rows(league.db_path) == []
    assert await change_rows(league.db_path) == []
    [line] = await log_lines(league)
    assert line.startswith("⛔ ")
    assert "/results config xml-import" in line
    assert f"refused for {NAMED}" in line
    assert said_ in line


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


@pytest.mark.parametrize("case", ["positions", "fastest-lap", "log-line"])
async def test_an_xml_import_records_each_change_from_what_to_what(tmp_path, case):
    """An import changes a configuration, so each change is recorded as an audit entry: who,
    when, and each position's points — or the fastest-lap bonus and its limit — from what to
    what. The log line states the values set: each session, each position's points, and the
    fastest-lap bonus with its limit."""
    from datetime import datetime

    from leaguebot.results.models.points_config import SessionType

    league = await _league(tmp_path)

    if case == "log-line":
        await _import(league)
        audited = await _audited(league)
        values = audited.split("\n", 1)[1] if "\n" in audited else ""
        for expected in ("Feature Race", "Sprint Race", "25", "18", "8", "7", "10"):
            assert expected in values, f"{expected!r} is not among the values logged"
        return

    if case == "positions":
        await saves.set_session_points(league.db_path, CONFIG, SessionType.FEATURE_RACE, 1, 20)
        await _import(league, VALID_XML.replace('<fastest-lap limit="10">2</fastest-lap>', ""))
    else:
        await _import(league, FL_ONLY_XML)

    rows = await _audit_rows(league.db_path)
    assert all(r["actor_id"] == MEMBER_ID for r in rows)
    assert all(datetime.fromisoformat(r["timestamp"]).utcoffset() is not None for r in rows)
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
