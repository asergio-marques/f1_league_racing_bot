"""The results settings on the change queue (#439, slice 7).

Every `/results config` command that changes a points configuration, every `/results amend`
command that stages one, and `/results reserves toggle` ask one settings change: acknowledged at
once naming what is being set, checked at the ask and again as it runs, and saving the value, its
audit entry and its log line in one save (core specification, "The record of what changed" and
"How a change is carried out"; results specification :159-217, :575-582). A fault in the save
saves nothing, tells the member so, and the lane goes on.

Each test drives the real command, form or button past its tier guard, on a database built by the
migrations, with the real queue and "now" pinned, and runs the queue. The league holds the points
configuration `Standard`, its Feature Race paying 25 and 18 with one point for the fastest lap to
the top ten; where a season is named it is season 1 with the division Pro (id 11), showing its
reserves, and an active season scores on `Standard`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.utils.interaction_errors import failure_reply
from leaguebot.core.utils.season_gate import stage_refusal
from tests.support.change_queue import MEMBER_ID, change_rows, run_queue
from tests.support.settings_league import (
    audit_rows,
    fault_in,
    interaction,
    last_said,
    log_lines,
    record_followups,
    said,
    settings_league,
    switch,
)
from tests.support.undecorate import undecorate

#: The member as a saved log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"
CONFIG = "Standard"
SEASON_ID = 1
PRO = 11
FEATURE_RACE = app_commands.Choice(name="Feature Race", value="FEATURE_RACE")
MODULE_OFF = "❌ The Results & Standings module is not enabled on this server."
NOT_ACTIVE = "❌ Amendment mode is not active."
PASTE_NOT_SAVED = "Nothing from the paste was saved. Paste it again to retry."
IMPORT_NOT_SAVED = "Nothing from the import was saved. Run the import again to retry."
XML_30_20 = (
    "<config><session><type>Feature Race</type>"
    '<position id="1">30</position><position id="2">20</position>'
    "</session></config>"
)
XML_HELD = (
    "<config><session><type>Feature Race</type>"
    '<position id="1">25</position><position id="2">18</position>'
    "</session></config>"
)
#: Each season the league may hold: its status and stage.
SEASONS = {
    "setup": ("SETUP", "CONFIGURATION"),
    "active": ("ACTIVE", "ONGOING"),
    "pending": ("ACTIVE", "PENDING_COMPLETION"),
}


# ---------------------------------------------------------------------------
# The league
# ---------------------------------------------------------------------------


async def _league(tmp_path: Any, *, season: str | None = None, attached: bool = True,
                  amend: bool = False, staged: bool = False, others: tuple[str, ...] = ()) -> Any:
    """The league of the module docstring, results alone on. *season* is None, "setup",
    "active" or "pending"; *attached* links `Standard` to it (an active one holding its points);
    *amend* turns amendment mode on, its store a copy of the season's points, and *staged* stages
    P1 at 26 in it. *others* are further configurations, each holding no points."""
    league = await settings_league(tmp_path, modules=("results",))
    async with get_connection(league.db_path) as db:
        await db.execute("INSERT INTO points_config_store (id, config_name) VALUES (1, ?)", (CONFIG,))
        for name in others:
            await db.execute("INSERT INTO points_config_store (config_name) VALUES (?)", (name,))
        for position, points in ((1, 25), (2, 18)):
            await db.execute(
                "INSERT INTO points_config_entries (config_id, session_type, position, points) "
                "VALUES (1, 'FEATURE_RACE', ?, ?)",
                (position, points),
            )
        await db.execute(
            "INSERT INTO points_config_fl (config_id, session_type, fl_points, fl_position_limit) "
            "VALUES (1, 'FEATURE_RACE', 1, 10)"
        )
        if season is not None:
            status, stage = SEASONS[season]
            await db.execute(
                "INSERT INTO seasons (id, season_number, start_date, status, stage) "
                "VALUES (?, 1, '2026-01-01', ?, ?)",
                (SEASON_ID, status, stage),
            )
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, status, mention_role_id) "
                "VALUES (?, ?, 'Pro', 1, ?, 555)",
                (PRO, SEASON_ID, "SETUP" if season == "setup" else "ACTIVE"),
            )
            await db.execute(
                "INSERT INTO division_results_config (division_id, reserves_in_standings) "
                "VALUES (?, 1)",
                (PRO,),
            )
        if season is not None and attached:
            await db.execute(
                "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
                (SEASON_ID, CONFIG),
            )
        if season in ("active", "pending") and attached:
            for table in ("season_points", "season_modification") if amend else ("season_points",):
                for position, points in ((1, 25), (2, 18)):
                    await db.execute(
                        f"INSERT INTO {table}_entries (season_id, config_name, session_type, "
                        "position, points) VALUES (?, ?, 'FEATURE_RACE', ?, ?)",
                        (SEASON_ID, CONFIG, position, points),
                    )
                await db.execute(
                    f"INSERT INTO {table}_fl (season_id, config_name, session_type, fl_points, "
                    "fl_position_limit) VALUES (?, ?, 'FEATURE_RACE', 1, 10)",
                    (SEASON_ID, CONFIG),
                )
        if amend:
            await db.execute(
                "INSERT INTO season_amendment_state (season_id, amendment_active, modified_flag) "
                "VALUES (?, 1, ?)",
                (SEASON_ID, int(staged)),
            )
        if staged:
            await db.execute(
                "UPDATE season_modification_entries SET points = 26 WHERE position = 1"
            )
        await db.commit()
    return league


async def _rows(db_path: str, sql: str) -> list[tuple[Any, ...]]:
    async with get_connection(db_path) as db:
        return [tuple(row) for row in await (await db.execute(sql)).fetchall()]


async def _write(db_path: str, sql: str, *args: Any) -> None:
    async with get_connection(db_path) as db:
        await db.execute(sql, args)
        await db.commit()


STORE = "SELECT config_name FROM points_config_store ORDER BY id"
POINTS = "SELECT position, points FROM points_config_entries ORDER BY position"
FL = "SELECT fl_points, fl_position_limit FROM points_config_fl"
LINKS = "SELECT season_id, config_name FROM season_points_links"
MODE = "SELECT amendment_active, modified_flag FROM season_amendment_state"
STAGED = "SELECT position, points FROM season_modification_entries ORDER BY position"
STAGED_FL = "SELECT fl_points, fl_position_limit FROM season_modification_fl"
RESERVES = "SELECT reserves_in_standings FROM division_results_config WHERE division_id = 11"


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------

Ask = Callable[[Any], Awaitable[Any]]


def _slash(method: str, command: str, **args: Any) -> Ask:
    """Run `/<command>` (`ResultsCog.<method>`) as Admin with *args*; the queue is not run."""
    async def ask(league: Any) -> Any:
        from leaguebot.results.cogs.results_cog import ResultsCog

        asked = interaction(league.bot, command)
        asked.response.send_modal = AsyncMock()
        await undecorate(getattr(ResultsCog, method))(ResultsCog(league.bot), asked, **args)
        return asked
    return ask


def _form(kind: str, value: str, *, name: str = CONFIG) -> Ask:
    """Submit a form as Admin: the config bulk paste ("config"), the amend one ("amend") or the
    XML import ("xml"), with *value* typed into it; the queue is not run."""
    async def ask(league: Any) -> Any:
        from leaguebot.results.cogs import results_cog

        if kind == "xml":
            modal = results_cog.XmlImportModal(name, league.db_path)
            modal.xml_payload._value = value  # type: ignore[attr-defined]
        else:
            cls = {"config": results_cog.BulkConfigSessionModal,
                   "amend": results_cog.BulkAmendSessionModal}[kind]
            modal = cls(name, FEATURE_RACE, league.db_path)
            modal.entries._value = value  # type: ignore[attr-defined]
        asked = interaction(league.bot)
        await modal.on_submit(asked)
        return asked
    return ask


def _file(xml: str, *, name: str = CONFIG) -> Ask:
    """`/results config xml-import` with *xml* attached as a file."""
    attachment = MagicMock(spec=discord.Attachment)
    attachment.read = AsyncMock(return_value=xml.encode("utf-8"))
    return _slash("config_xml_import", "results config xml-import", name=name, file=attachment)


def _refused(what: str, reply: str) -> str:
    return f"⛔ {what} refused for {NAMED} — {reply.removeprefix('❌ ')}"


def _matches(value: Any, expected: Any) -> None:
    if callable(expected):
        assert expected(value), value
    else:
        assert value == expected


def _position(config: str, position: int, points: int, **extra: Any) -> dict[str, Any]:
    return {**extra, "config": config, "session_type": "FEATURE_RACE",
            "position": position, "points": points}


def _fastest_lap(points: int, limit: int) -> dict[str, Any]:
    return {"config": CONFIG, "session_type": "FEATURE_RACE",
            "fl_points": points, "fl_position_limit": limit}


def _holds(**expected: Any) -> Callable[[Any], bool]:
    """A value holding at least *expected*."""
    return lambda value: all(value.get(key) == item for key, item in expected.items())


# ---------------------------------------------------------------------------
# Each setting, as it saves
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One results setting: the league it needs, how it is asked, what the acknowledgement
    names, the reply once run, the line after the member's name, each audit entry (its type, old
    and new value, or a test of either), and a read of what it saved with the rows it holds."""

    ask: Ask
    doing: str
    reply: str
    line: str
    audits: tuple[tuple[str, Any, Any], ...]
    query: str
    rows: list[tuple[Any, ...]]
    setup: dict[str, Any] = field(default_factory=dict)


ACTIVE = {"season": "active"}
AMENDING = {"season": "active", "amend": True}

CASES: dict[str, Case] = {
    "config-add": Case(
        _slash("config_add", "results config add", name="Sprint"),
        "Adding points configuration **Sprint**",
        "✅ Config **Sprint** created. All positions default to 0 points.",
        "/results config add | Success\n  config: Sprint",
        (("POINTS_CONFIG_ADD", {}, {"config": "Sprint"}),),
        STORE, [(CONFIG,), ("Sprint",)],
    ),
    "config-remove": Case(
        _slash("config_remove", "results config remove", name=CONFIG),
        f"Removing points configuration **{CONFIG}**",
        f"✅ Config **{CONFIG}** removed.",
        f"/results config remove | Success\n  config: {CONFIG}",
        (("POINTS_CONFIG_REMOVE", _holds(config=CONFIG, detached_from=[]), {}),),
        STORE, [],
    ),
    "config-session": Case(
        _slash("config_session", "results config session", name=CONFIG, session=FEATURE_RACE,
               position=1, points=26),
        f"Setting **Feature Race** position 1 to 26 pts in config **{CONFIG}**",
        f"✅ Set **Feature Race** position 1 → 26 pts in config **{CONFIG}**.",
        f"/results config session | Success\n  config: {CONFIG}\n"
        "  session: Feature Race, position: 1, points: 26",
        (("POINTS_CONFIG_SESSION_SET", _position(CONFIG, 1, 25), _position(CONFIG, 1, 26)),),
        POINTS, [(1, 26), (2, 18)],
    ),
    "config-fl": Case(
        _slash("config_fl", "results config fl", name=CONFIG, session=FEATURE_RACE, points=2),
        f"Setting the fastest-lap bonus for **Feature Race** to 2 pts in config **{CONFIG}**",
        f"✅ Set fastest-lap bonus for **Feature Race** → 2 pts in config **{CONFIG}**.",
        f"/results config fl | Success\n  config: {CONFIG}\n  session: Feature Race, fl_bonus: 2",
        (("POINTS_CONFIG_FL_SET", _fastest_lap(1, 10), _fastest_lap(2, 10)),),
        FL, [(2, 10)],
    ),
    "config-fl-plimit": Case(
        _slash("config_fl_plimit", "results config fl-plimit", name=CONFIG, session=FEATURE_RACE,
               limit=8),
        f"Setting the fastest-lap position limit for **Feature Race** to top 8 in config **{CONFIG}**",
        f"✅ Set fastest-lap position limit for **Feature Race** → top 8 eligible in config **{CONFIG}**.",
        f"/results config fl-plimit | Success\n  config: {CONFIG}\n"
        "  session: Feature Race, fl_position_limit: 8",
        (("POINTS_CONFIG_FL_SET", _fastest_lap(1, 10), _fastest_lap(1, 8)),),
        FL, [(1, 8)],
    ),
    "config-bulk-session": Case(
        _form("config", "1, 30\n2, 20"),
        f"Setting 2 position(s) of **Feature Race** in config **{CONFIG}**",
        f"✅ Applied to config **{CONFIG}** (Feature Race):\n  P1 → 30 pts\n  P2 → 20 pts",
        f"/results config bulk-session | 2 change(s)\n  config: {CONFIG}, session: Feature Race\n"
        "  P1 → 30 pts\n  P2 → 20 pts",
        (("POINTS_CONFIG_SESSION_SET", _position(CONFIG, 1, 25), _position(CONFIG, 1, 30)),
         ("POINTS_CONFIG_SESSION_SET", _position(CONFIG, 2, 18), _position(CONFIG, 2, 20))),
        POINTS, [(1, 30), (2, 20)],
    ),
    "config-xml-import-form": Case(
        _form("xml", XML_30_20),
        f"Importing into config **{CONFIG}**",
        f"✅ Config **{CONFIG}** updated:\n  **Feature Race**: 2 position(s) updated",
        f"/results config xml-import | Success\n  config: {CONFIG}\n  Feature Race: P1 30, P2 20",
        (("POINTS_CONFIG_SESSION_SET", _position(CONFIG, 1, 25), _position(CONFIG, 1, 30)),
         ("POINTS_CONFIG_SESSION_SET", _position(CONFIG, 2, 18), _position(CONFIG, 2, 20))),
        POINTS, [(1, 30), (2, 20)],
    ),
    "config-xml-import-file": Case(
        _file(XML_30_20),
        f"Importing into config **{CONFIG}**",
        f"✅ Config **{CONFIG}** updated:\n  **Feature Race**: 2 position(s) updated",
        f"/results config xml-import | Success\n  config: {CONFIG}\n  Feature Race: P1 30, P2 20",
        (("POINTS_CONFIG_SESSION_SET", _position(CONFIG, 1, 25), _position(CONFIG, 1, 30)),
         ("POINTS_CONFIG_SESSION_SET", _position(CONFIG, 2, 18), _position(CONFIG, 2, 20))),
        POINTS, [(1, 30), (2, 20)],
    ),
    "config-append": Case(
        _slash("config_append", "results config append", name=CONFIG),
        f"Attaching config **{CONFIG}** to the current season",
        f"✅ Config **{CONFIG}** attached to the current season.",
        f"/results config append | Success\n  config: {CONFIG}",
        (("POINTS_CONFIG_ATTACH",
          {"season_id": SEASON_ID, "config": CONFIG, "attached": False},
          {"season_id": SEASON_ID, "config": CONFIG, "attached": True}),),
        LINKS, [(SEASON_ID, CONFIG)],
        {"season": "setup", "attached": False},
    ),
    "config-detach": Case(
        _slash("config_detach", "results config detach", name=CONFIG),
        f"Detaching config **{CONFIG}** from the current season",
        f"✅ Config **{CONFIG}** detached from the current season.",
        f"/results config detach | Success\n  config: {CONFIG}",
        (("POINTS_CONFIG_DETACH",
          {"season_id": SEASON_ID, "config": CONFIG, "attached": True},
          {"season_id": SEASON_ID, "config": CONFIG, "attached": False}),),
        LINKS, [],
        {"season": "setup"},
    ),
    "amend-toggle-on": Case(
        _slash("amend_toggle", "results amend toggle"),
        "Turning amendment mode on",
        "✅ Amendment mode enabled. Modification store initialised.",
        "/results amend toggle | Success\n  amendment_mode: enabled",
        (("POINTS_AMENDMENT_MODE_SET",
          {"season_id": SEASON_ID, "amendment_active": False},
          {"season_id": SEASON_ID, "amendment_active": True}),),
        MODE, [(1, 0)],
        ACTIVE,
    ),
    "amend-toggle-off": Case(
        _slash("amend_toggle", "results amend toggle"),
        "Turning amendment mode off",
        "✅ Amendment mode disabled.",
        "/results amend toggle | Success\n  amendment_mode: disabled",
        (("POINTS_AMENDMENT_MODE_SET",
          {"season_id": SEASON_ID, "amendment_active": True},
          {"season_id": SEASON_ID, "amendment_active": False}),),
        MODE, [(0, 0)],
        AMENDING,
    ),
    "amend-revert": Case(
        _slash("amend_revert", "results amend revert"),
        "Reverting the modification store",
        "✅ Modification store reverted to current season points.",
        "/results amend revert | Success",
        (("POINTS_AMENDMENT_REVERTED",
          lambda old: old["season_id"] == SEASON_ID and len(old["staged"]) == 1,
          {"season_id": SEASON_ID, "staged": []}),),
        STAGED, [(1, 25), (2, 18)],
        {**AMENDING, "staged": True},
    ),
    "amend-session": Case(
        _slash("amend_session", "results amend session", name=CONFIG, session=FEATURE_RACE,
               position=1, points=27),
        f"Staging **{CONFIG}** Feature Race P1 → 27 pts",
        f"✅ Updated in modification store: **{CONFIG}** Feature Race P1 → 27 pts.",
        f"/results amend session | Success\n  config: {CONFIG}\n"
        "  session: Feature Race, position: 1, points: 27",
        (("POINTS_AMENDMENT_STAGED", _position(CONFIG, 1, 25, season_id=SEASON_ID),
          _position(CONFIG, 1, 27, season_id=SEASON_ID)),),
        STAGED, [(1, 27), (2, 18)],
        AMENDING,
    ),
    "amend-fl": Case(
        _slash("amend_fl", "results amend fl", name=CONFIG, session=FEATURE_RACE, points=2),
        f"Staging **{CONFIG}** Feature Race FL bonus → 2 pts",
        f"✅ Updated in modification store: **{CONFIG}** Feature Race FL bonus → 2 pts.",
        f"/results amend fl | Success\n  config: {CONFIG}\n  session: Feature Race, fl_bonus: 2",
        (("POINTS_AMENDMENT_FL_STAGED",
          _holds(season_id=SEASON_ID, config=CONFIG, session_type="FEATURE_RACE", fl_points=1),
          _holds(season_id=SEASON_ID, config=CONFIG, session_type="FEATURE_RACE", fl_points=2)),),
        STAGED_FL, [(2, 10)],
        AMENDING,
    ),
    "amend-fl-plimit": Case(
        _slash("amend_fl_plimit", "results amend fl-plimit", name=CONFIG, session=FEATURE_RACE,
               limit=8),
        f"Staging **{CONFIG}** Feature Race FL position limit → top 8",
        f"✅ Updated in modification store: **{CONFIG}** Feature Race FL position limit → top 8.",
        f"/results amend fl-plimit | Success\n  config: {CONFIG}\n"
        "  session: Feature Race, fl_position_limit: 8",
        (("POINTS_AMENDMENT_FL_STAGED",
          _holds(season_id=SEASON_ID, config=CONFIG, fl_position_limit=10),
          _holds(season_id=SEASON_ID, config=CONFIG, fl_position_limit=8)),),
        STAGED_FL, [(1, 8)],
        AMENDING,
    ),
    "amend-bulk-session": Case(
        _form("amend", "1, 30\n2, 20"),
        f"Staging 2 position(s) of **{CONFIG}** Feature Race",
        f"✅ Amended in modification store for config **{CONFIG}** (Feature Race):\n"
        "  P1 → 30 pts\n  P2 → 20 pts",
        f"/results amend bulk-session | 2 change(s)\n  config: {CONFIG}, session: Feature Race\n"
        "  P1 → 30 pts\n  P2 → 20 pts",
        (("POINTS_AMENDMENT_STAGED", _position(CONFIG, 1, 25, season_id=SEASON_ID),
          _position(CONFIG, 1, 30, season_id=SEASON_ID)),
         ("POINTS_AMENDMENT_STAGED", _position(CONFIG, 2, 18, season_id=SEASON_ID),
          _position(CONFIG, 2, 20, season_id=SEASON_ID))),
        STAGED, [(1, 30), (2, 20)],
        AMENDING,
    ),
    "reserves-toggle": Case(
        _slash("reserves_toggle", "results reserves toggle", division="Pro"),
        "Setting reserve visibility for **Pro** to **hidden**",
        "✅ Reserve visibility for **Pro** set to **hidden**.",
        "/results reserves toggle | Success\n  division: Pro\n  reserves_in_standings: hidden",
        (("RESERVES_IN_STANDINGS_SET", lambda old: old not in (None, ""),
          lambda new: new not in (None, "")),),
        RESERVES, [(0,)],
        ACTIVE,
    ),
}


async def _saves_with_record(tmp_path: Any, case: Case) -> Any:
    """Ask *case*, acknowledged at once, run the queue, and assert the value, its audit entries
    and its one line were saved together, the acknowledgement updated with today's words; return
    the league."""
    league = await _league(tmp_path, **case.setup)

    asked = await case.ask(league)
    assert said(asked)[0].startswith(f"⏳ {case.doing}. ")
    await run_queue(league.bot)

    assert last_said(asked) == case.reply
    assert await _rows(league.db_path, case.query) == case.rows
    audits = await audit_rows(league.db_path)
    assert [(a["change_type"], a["actor_id"]) for a in audits] == [
        (kind, MEMBER_ID) for kind, _old, _new in case.audits
    ]
    for audit, (_kind, old, new) in zip(audits, case.audits):
        _matches(audit["old_value"], old)
        _matches(audit["new_value"], new)
    assert await log_lines(league) == [f"{NAMED} | {case.line}"]
    return league


# ---------------------------------------------------------------------------
# Defect 13: each saved with its audit entry and line
# ---------------------------------------------------------------------------


async def test_adding_a_configuration_saves_it_with_its_audit_entry_and_line(tmp_path):
    """`/results config add Sprint`, once run, holds Sprint, one `POINTS_CONFIG_ADD` entry from
    nothing to the name, and its Success line."""
    await _saves_with_record(tmp_path, CASES["config-add"])


@pytest.mark.parametrize("route", ["straight", "confirmed"])
async def test_removing_a_configuration_records_its_whole_table_and_the_seasons_it_left(
    tmp_path, route
):
    """`/results config remove Standard`: straight where no season stands on it, or through
    ✅ Remove it anyway where season 1, in setup, does. Once run, the configuration is gone, one
    `POINTS_CONFIG_REMOVE` entry holds as its old value the name, every position it paid, its
    fastest-lap bonus and limit, and the seasons it was detached from (none, or season 1), and
    the Success line is written."""
    if route == "straight":
        league = await _saves_with_record(tmp_path, CASES["config-remove"])
        old = (await audit_rows(league.db_path))[0]["old_value"]
    else:
        league = await _league(tmp_path, season="setup")
        asked = await CASES["config-remove"].ask(league)
        sent = (asked.response.send_message.await_args_list
                + asked.followup.send.await_args_list)
        view = next(call.kwargs["view"] for call in sent if call.kwargs.get("view") is not None)
        button = next(item for item in view.children
                      if isinstance(item, discord.ui.Button) and "Remove it anyway" in item.label)
        press = interaction(league.bot)
        await button.callback(press)
        await run_queue(league.bot)

        assert last_said(press) == f"✅ Config **{CONFIG}** removed."
        assert await _rows(league.db_path, STORE) == []
        assert await _rows(league.db_path, LINKS) == []
        audits = await audit_rows(league.db_path)
        assert [(a["change_type"], a["actor_id"], a["new_value"]) for a in audits] == [
            ("POINTS_CONFIG_REMOVE", MEMBER_ID, {})
        ]
        assert await log_lines(league) == [
            f"{NAMED} | /results config remove | Success\n  config: {CONFIG}"
        ]
        old = audits[0]["old_value"]
    assert old["config"] == CONFIG
    assert old["detached_from"] == ([] if route == "straight" else [1])
    assert list(old["positions"].values()) == [{"1": 25, "2": 18}]
    assert list(old["fastest_laps"].values()) == [{"fl_points": 1, "fl_position_limit": 10}]


async def test_setting_a_position_saves_it_with_its_audit_entry_and_line(tmp_path):
    """`/results config session Standard Feature Race 1 26`: P1 pays 26, one
    `POINTS_CONFIG_SESSION_SET` entry from 25 to 26, and today's Success line."""
    await _saves_with_record(tmp_path, CASES["config-session"])


async def test_setting_a_fastest_lap_bonus_saves_it_with_its_audit_entry_and_line(tmp_path):
    """`/results config fl Standard Feature Race 2`: the bonus is 2, the limit kept at 10, one
    `POINTS_CONFIG_FL_SET` entry from 1 to 2, and today's Success line."""
    await _saves_with_record(tmp_path, CASES["config-fl"])


async def test_setting_a_fastest_lap_limit_saves_it_with_its_audit_entry_and_line(tmp_path):
    """`/results config fl-plimit Standard Feature Race 8`: the limit is 8, the bonus kept at 1,
    one `POINTS_CONFIG_FL_SET` entry from top 10 to top 8, and today's Success line."""
    await _saves_with_record(tmp_path, CASES["config-fl-plimit"])


async def test_a_bulk_paste_saves_its_positions_audit_entries_and_line_together(tmp_path):
    """The bulk form pasting P1 30 and P2 20 into Standard's Feature Race: both saved, one
    `POINTS_CONFIG_SESSION_SET` entry per position, and one "2 change(s)" line with both values."""
    await _saves_with_record(tmp_path, CASES["config-bulk-session"])


@pytest.mark.parametrize("route", ["config-xml-import-form", "config-xml-import-file"])
async def test_an_xml_import_saves_its_values_audit_entries_and_line_together(tmp_path, route):
    """An XML import of P1 30 and P2 20 into Standard's Feature Race, through the form and as an
    attached file: both saved, an entry per position, and one Success line with the values."""
    await _saves_with_record(tmp_path, CASES[route])


async def test_attaching_a_configuration_saves_it_with_its_audit_entry_and_line(tmp_path):
    """`/results config append Standard` with season 1 in setup holding none: attached, one
    `POINTS_CONFIG_ATTACH` entry from not attached to attached, and its Success line."""
    await _saves_with_record(tmp_path, CASES["config-append"])


async def test_detaching_a_configuration_saves_it_with_its_audit_entry_and_line(tmp_path):
    """`/results config detach Standard` with season 1 in setup holding it: detached, one
    `POINTS_CONFIG_DETACH` entry from attached to not, and its Success line."""
    await _saves_with_record(tmp_path, CASES["config-detach"])


@pytest.mark.parametrize("route", ["amend-toggle-on", "amend-toggle-off"])
async def test_turning_amendment_mode_on_and_off_saves_each_with_its_audit_entry_and_line(
    tmp_path, route
):
    """`/results amend toggle` on season 1, under way, with the mode off and then on: the mode
    switched, one `POINTS_AMENDMENT_MODE_SET` entry from the old state to the new, and its line."""
    await _saves_with_record(tmp_path, CASES[route])


async def test_reverting_the_modification_store_records_what_was_discarded(tmp_path):
    """`/results amend revert` with P1 staged at 26: the store back to 25 and 18, one
    `POINTS_AMENDMENT_REVERTED` entry whose old value names the one row that differed and whose
    new value names none, and its Success line."""
    await _saves_with_record(tmp_path, CASES["amend-revert"])


async def test_staging_a_position_saves_it_with_its_audit_entry_and_line(tmp_path):
    """`/results amend session Standard Feature Race 1 27`: P1 staged at 27, one
    `POINTS_AMENDMENT_STAGED` entry from 25 to 27, and today's Success line."""
    await _saves_with_record(tmp_path, CASES["amend-session"])


async def test_staging_a_fastest_lap_bonus_saves_it_with_its_audit_entry_and_line(tmp_path):
    """`/results amend fl Standard Feature Race 2`: the bonus staged at 2, one
    `POINTS_AMENDMENT_FL_STAGED` entry from 1 to 2, and today's Success line."""
    await _saves_with_record(tmp_path, CASES["amend-fl"])


async def test_staging_a_fastest_lap_limit_saves_it_with_its_audit_entry_and_line(tmp_path):
    """`/results amend fl-plimit Standard Feature Race 8`: the limit staged at 8, one
    `POINTS_AMENDMENT_FL_STAGED` entry from top 10 to top 8, and today's Success line."""
    await _saves_with_record(tmp_path, CASES["amend-fl-plimit"])


async def test_an_amend_bulk_paste_saves_its_positions_audit_entries_and_line_together(tmp_path):
    """The amend bulk form pasting P1 30 and P2 20: both staged, one `POINTS_AMENDMENT_STAGED`
    entry per position, and one "2 change(s)" line with both values."""
    await _saves_with_record(tmp_path, CASES["amend-bulk-session"])


async def test_toggling_reserve_visibility_records_it_against_the_division(tmp_path):
    """`/results reserves toggle Pro`, reserves shown: hidden, one `RESERVES_IN_STANDINGS_SET`
    entry carrying Pro's division id, and its Success line naming the division."""
    league = await _saves_with_record(tmp_path, CASES["reserves-toggle"])

    audit = (await audit_rows(league.db_path))[0]
    assert audit["division_id"] == PRO
    assert audit["old_value"] != audit["new_value"]


# ---------------------------------------------------------------------------
# On the queue
# ---------------------------------------------------------------------------


async def test_a_results_setting_whose_audit_entry_fails_saves_nothing_and_the_lane_goes_on(
    tmp_path
):
    """`/results config add Sprint` whose audit write faults: Sprint is not saved, the member is
    told nothing was saved and to run it again, one failure line is written and no Success line,
    and `/results config add Other` asked behind it is saved."""
    league = await _league(tmp_path)

    asked = await _slash("config_add", "results config add", name="Sprint")(league)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot, steps=1)
    behind = await _slash("config_add", "results config add", name="Other")(league)
    await run_queue(league.bot)

    assert last_said(asked) == failure_reply(
        "`/results config add`", "Nothing was saved. Run `/results config add` again to retry."
    )
    assert await _rows(league.db_path, STORE) == [(CONFIG,), ("Other",)]
    lines = await log_lines(league)
    assert [line for line in lines if line.startswith("❌ ")] == [
        line for line in lines if line.startswith(f"❌ `/results config add` failed for {NAMED} — ")
    ] != []
    assert not any("config: Sprint" in line for line in lines)
    assert last_said(behind) == "✅ Config **Other** created. All positions default to 0 points."


async def test_a_configuration_removed_ahead_of_a_points_change_refuses_it_when_it_runs(tmp_path):
    """`/results config remove Standard` asked, then `/results config session Standard Feature
    Race 1 26`: the second is acknowledged, and when it runs is refused as not found, nothing
    written to a configuration that is gone."""
    league = await _league(tmp_path)

    await _slash("config_remove", "results config remove", name=CONFIG)(league)
    second = await CASES["config-session"].ask(league)
    assert said(second)[0].startswith(f"⏳ {CASES['config-session'].doing}. ")
    await run_queue(league.bot)

    reply = f"❌ Config **{CONFIG}** not found."
    assert last_said(second) == reply
    assert (await log_lines(league))[-1] == _refused("`/results config session`", reply)
    assert await _rows(league.db_path, POINTS) == []


async def test_a_setting_asked_before_results_is_turned_off_and_run_after_is_refused(tmp_path):
    """`/results config add Sprint` asked while results is on, the module turned off before it
    runs: refused when it runs with the module refusal, Sprint not added."""
    league = await _league(tmp_path)

    asked = await CASES["config-add"].ask(league)
    await switch(league.bot, ("results",), on=False)
    await run_queue(league.bot)

    assert last_said(asked) == MODULE_OFF
    assert await log_lines(league) == [_refused("`/results config add`", MODULE_OFF)]
    assert await _rows(league.db_path, STORE) == [(CONFIG,)]
    assert await audit_rows(league.db_path) == []


async def test_amendment_mode_turned_off_ahead_of_a_staged_change_refuses_it_when_it_runs(tmp_path):
    """With amendment mode on and nothing staged, `/results amend toggle` (off) is asked, then
    `/results amend session Standard Feature Race 1 27`: the second is acknowledged, and when it
    runs is refused as the mode not active, nothing staged."""
    league = await _league(tmp_path, **AMENDING)

    await CASES["amend-toggle-off"].ask(league)
    second = await CASES["amend-session"].ask(league)
    assert said(second)[0].startswith(f"⏳ {CASES['amend-session'].doing}. ")
    await run_queue(league.bot)

    assert last_said(second) == NOT_ACTIVE
    assert (await log_lines(league))[-1] == _refused("`/results amend session`", NOT_ACTIVE)
    assert await _rows(league.db_path, MODE) == [(0, 0)]
    assert [a["change_type"] for a in await audit_rows(league.db_path)] == [
        "POINTS_AMENDMENT_MODE_SET"
    ]


async def test_a_season_reaching_pending_completion_ahead_of_a_reserves_toggle_refuses_it(tmp_path):
    """`/results reserves toggle Pro` asked while season 1 is under way; the season reaches
    Pending completion before it runs: refused then in today's pending-completion words, the
    reserves still shown."""
    league = await _league(tmp_path, **ACTIVE)

    asked = await CASES["reserves-toggle"].ask(league)
    await _write(league.db_path, "UPDATE seasons SET stage = 'PENDING_COMPLETION' WHERE id = 1")
    await run_queue(league.bot)

    reply = stage_refusal(SeasonStage.PENDING_COMPLETION, "results reserves toggle")
    assert last_said(asked) == reply
    assert await log_lines(league) == [_refused("`/results reserves toggle`", reply)]
    assert await _rows(league.db_path, RESERVES) == [(1,)]


async def test_two_presses_of_the_amendment_toggle_while_the_first_waits_are_one_change(tmp_path):
    """`/results amend toggle` run twice with the mode off before the queue runs: both read
    "off" and ask to turn it on, so the second is refused as a repeat, and once run the mode is
    on, with one audit entry."""
    league = await _league(tmp_path, **ACTIVE)

    await CASES["amend-toggle-on"].ask(league)
    second = await CASES["amend-toggle-on"].ask(league)
    await run_queue(league.bot)

    assert last_said(second).startswith(
        "⚠️ Turning amendment mode on has already been asked for and has not started yet, "
        "so it was not asked for again."
    )
    assert await _rows(league.db_path, MODE) == [(1, 0)]
    assert len(await audit_rows(league.db_path)) == 1
    assert len(await change_rows(league.db_path)) == 1


async def test_an_amendment_toggle_found_already_on_when_it_runs_changes_nothing(tmp_path):
    """A flip resolved when asked: `/results amend toggle` with the mode off asks to turn it on.
    Before the change runs the mode is turned on (written here as a change that started before
    the ask would leave it: the flag set and the season's points copied into the store). When it
    runs it finds the mode already on: it changes nothing and says so, never turning it back
    off, with one "Nothing changed" line and no audit entry."""
    league = await _league(tmp_path, **ACTIVE)

    asked = await CASES["amend-toggle-on"].ask(league)
    assert said(asked)[0].startswith("⏳ Turning amendment mode on. ")
    for table, columns in (("entries", "position, points"),
                           ("fl", "fl_points, fl_position_limit")):
        await _write(
            league.db_path,
            f"INSERT INTO season_modification_{table} (season_id, config_name, session_type, "
            f"{columns}) SELECT season_id, config_name, session_type, {columns} "
            f"FROM season_points_{table}",
        )
    await _write(
        league.db_path,
        "INSERT INTO season_amendment_state (season_id, amendment_active, modified_flag) "
        "VALUES (?, 1, 0)",
        SEASON_ID,
    )
    await run_queue(league.bot)

    reply = last_said(asked)
    assert reply.startswith("ℹ️ Nothing changed: "), reply
    assert "amendment mode" in reply.lower() and "already **on**" in reply
    assert await _rows(league.db_path, MODE) == [(1, 0)]
    assert await _rows(league.db_path, STAGED) == [(1, 25), (2, 18)]
    assert await audit_rows(league.db_path) == []
    lines = await log_lines(league)
    assert len(lines) == 1
    assert lines[0].split("\n")[0] == f"{NAMED} | /results amend toggle | Nothing changed"


async def test_the_ordering_warning_is_judged_on_the_table_the_change_left(tmp_path):
    """`/results config session` P1 → 20 is asked, then P2 → 24, in order with the P1 of 25
    held when asked: the second is acknowledged, and once both run, P2 above P1 leaves the table
    out of order, so the second's reply carries the ordering warning and the first's does not."""
    league = await _league(tmp_path)

    first = await _slash("config_session", "results config session", name=CONFIG,
                         session=FEATURE_RACE, position=1, points=20)(league)
    second = await _slash("config_session", "results config session", name=CONFIG,
                          session=FEATURE_RACE, position=2, points=24)(league)
    assert said(second)[0].startswith(
        f"⏳ Setting **Feature Race** position 2 to 24 pts in config **{CONFIG}**. "
    )
    await run_queue(league.bot)

    assert last_said(first) == f"✅ Set **Feature Race** position 1 → 20 pts in config **{CONFIG}**."
    assert last_said(second).startswith(
        f"✅ Set **Feature Race** position 2 → 24 pts in config **{CONFIG}**.\n"
        f"⚠️ **{CONFIG}**'s Feature Race table is now out of order:"
    )
    assert last_said(second).endswith(
        "The change has been saved, but a season cannot be approved on this table."
    )


async def test_a_same_value_stage_leaves_the_modified_flag_as_it_stood(tmp_path):
    """`/results amend session Standard Feature Race 1 25` with the mode on and nothing staged:
    acknowledged, then answered that nothing changed, with a "Nothing changed" line, no audit
    entry, and the modified flag still clear."""
    league = await _league(tmp_path, **AMENDING)

    asked = await _slash("amend_session", "results amend session", name=CONFIG,
                         session=FEATURE_RACE, position=1, points=25)(league)
    assert said(asked)[0].startswith(f"⏳ Staging **{CONFIG}** Feature Race P1 → 25 pts. ")
    await run_queue(league.bot)

    assert last_said(asked) == (
        f"ℹ️ Nothing changed: **{CONFIG}** Feature Race P1 already stands at 25 pts in the "
        "modification store."
    )
    assert await log_lines(league) == [
        f"{NAMED} | /results amend session | Nothing changed\n  config: {CONFIG}\n"
        "  session: Feature Race, position: 1, points: 25"
    ]
    assert await audit_rows(league.db_path) == []
    assert await _rows(league.db_path, MODE) == [(1, 0)]


async def test_a_removal_asked_without_confirmation_is_refused_when_a_setup_season_took_it_up_meanwhile(
    tmp_path
):
    """`/results config remove Standard` asked while no season stands on it, so without asking
    first; before it runs, season 1, in setup, attaches it. It is refused when it runs, saying it
    was attached since and to run the command again to be asked first; Standard stays."""
    league = await _league(tmp_path, season="setup", attached=False)

    asked = await CASES["config-remove"].ask(league)
    await _write(
        league.db_path,
        "INSERT INTO season_points_links (season_id, config_name) VALUES (1, ?)", CONFIG,
    )
    await run_queue(league.bot)

    reply = (
        f"❌ **{CONFIG}** was attached to Season #1 since you asked, so it was not removed. "
        "Run `/results config remove` again to be asked first."
    )
    assert last_said(asked) == reply
    assert await log_lines(league) == [_refused("`/results config remove`", reply)]
    assert await _rows(league.db_path, STORE) == [(CONFIG,)]


async def test_an_approval_in_hand_refuses_a_staging_asked_before_it_when_it_runs(tmp_path):
    """In the points league (P1 staged at 26), `/results amend session Standard Feature Race 1
    27` is asked, then `/results amend review`'s ✅ Approve is pressed before the queue runs: the
    staging, reaching its turn with the approval waiting behind it, is refused naming the
    approval, and P1 stays staged at 26."""
    from leaguebot.results.cogs.results_cog import ResultsCog
    from tests.support.points_league import (
        admin_interaction,
        points_league,
        points_state,
        press_approve,
    )
    from tests.support.points_league import FEATURE_RACE as RACE

    league = await points_league(tmp_path)
    asked = record_followups(admin_interaction(league, "results amend session"))
    await undecorate(ResultsCog.amend_session)(
        ResultsCog(league.bot), asked, name=CONFIG, session=RACE, position=1, points=27
    )
    await press_approve(league)
    await run_queue(league.bot, steps=1)

    assert last_said(asked).startswith("⏸️ Season 1's points amendment is being approved")
    assert ("Standard", "FEATURE_RACE", 1, 26) in (await points_state(league))["staged"]


@pytest.mark.parametrize(
    ("kind", "setup", "what"),
    [
        ("config", {}, "`/results config bulk-session`"),
        ("amend", AMENDING, "`/results amend bulk-session`"),
    ],
    ids=["config-bulk-session", "amend-bulk-session"],
)
async def test_a_fault_in_a_paste_s_save_saves_none_of_it_says_paste_it_again_and_the_lane_goes_on(
    tmp_path, kind, setup, what
):
    """A bulk paste of P1 30 and P2 20 whose audit write faults: neither position is saved, the
    member is told nothing from the paste was saved and to paste it again, one failure line is
    written, and `/results config add Other` asked behind it is saved."""
    league = await _league(tmp_path, **setup)
    table = POINTS if kind == "config" else STAGED

    asked = await _form(kind, "1, 30\n2, 20")(league)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot, steps=1)
    behind = await _slash("config_add", "results config add", name="Other")(league)
    await run_queue(league.bot)

    assert last_said(asked) == failure_reply(what, PASTE_NOT_SAVED)
    assert await _rows(league.db_path, table) == [(1, 25), (2, 18)]
    lines = await log_lines(league)
    assert [line for line in lines if line.startswith(f"❌ {what} failed for {NAMED} — ")] != []
    assert last_said(behind) == "✅ Config **Other** created. All positions default to 0 points."


async def test_a_fault_in_an_import_s_save_says_run_the_import_again_and_the_lane_goes_on(tmp_path):
    """An XML import of P1 30 and P2 20 whose audit write faults: nothing is saved, the member
    is told nothing from the import was saved and to run it again, one failure line names the
    configuration, and `/results config add Other` asked behind it is saved."""
    from leaguebot.results.cogs.results_cog import xml_import_named

    league = await _league(tmp_path)

    asked = await _form("xml", XML_30_20)(league)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot, steps=1)
    behind = await _slash("config_add", "results config add", name="Other")(league)
    await run_queue(league.bot)

    what = xml_import_named(CONFIG)
    assert last_said(asked) == failure_reply(what, IMPORT_NOT_SAVED)
    assert await _rows(league.db_path, POINTS) == [(1, 25), (2, 18)]
    lines = await log_lines(league)
    assert [line for line in lines if line.startswith(f"❌ {what} failed for {NAMED} — ")] != []
    assert last_said(behind) == "✅ Config **Other** created. All positions default to 0 points."


@pytest.mark.parametrize(
    "route",
    [
        "amend-bulk-session",
        "amend-fl",
        "amend-fl-plimit",
        "amend-revert",
        "amend-session",
        "amend-toggle-off",
        "amend-toggle-on",
        "config-add",
        "config-append",
        "config-bulk-session",
        "config-detach",
        "config-fl",
        "config-fl-plimit",
        "config-remove",
        "config-session",
        "config-xml-import-file",
        "config-xml-import-form",
        "reserves-toggle",
    ],
)
async def test_the_results_settings_are_acknowledged_naming_what_is_set(tmp_path, route):
    """Each results settings command, form or import is answered first with the queue's
    acknowledgement naming what is being set (a form's and a file import's as a follow-up to its
    deferral), and nothing is saved before the queue runs."""
    case = CASES[route]
    league = await _league(tmp_path, **case.setup)
    before = await _rows(league.db_path, case.query)

    asked = await case.ask(league)

    assert said(asked)[0].startswith(f"⏳ {case.doing}. ")
    assert await _rows(league.db_path, case.query) == before
    assert len(await change_rows(league.db_path)) == 1


async def test_reserve_visibility_flips_from_the_state_read_when_asked(tmp_path):
    """`/results reserves toggle Pro` with Pro's reserves hidden: it asks to show them, says so
    in its acknowledgement, and once run they are shown, with the reply saying visible."""
    league = await _league(tmp_path, **ACTIVE)
    await _write(league.db_path, "UPDATE division_results_config SET reserves_in_standings = 0")

    asked = await CASES["reserves-toggle"].ask(league)
    assert said(asked)[0].startswith("⏳ Setting reserve visibility for **Pro** to **visible**. ")
    await run_queue(league.bot)

    assert last_said(asked) == "✅ Reserve visibility for **Pro** set to **visible**."
    assert await _rows(league.db_path, RESERVES) == [(1,)]


async def test_a_reserves_toggle_found_already_switched_when_it_runs_changes_nothing(tmp_path):
    """A flip resolved when asked: `/results reserves toggle Pro` with Pro's reserves hidden asks
    to show them. Before the change runs they are shown (written here as a change that started
    before the ask would leave them). When it runs it finds them already visible: it changes
    nothing and says so, never hiding them again, with one "Nothing changed" line and no audit
    entry."""
    league = await _league(tmp_path, **ACTIVE)
    await _write(league.db_path, "UPDATE division_results_config SET reserves_in_standings = 0")

    asked = await CASES["reserves-toggle"].ask(league)
    assert said(asked)[0].startswith("⏳ Setting reserve visibility for **Pro** to **visible**. ")
    await _write(league.db_path, "UPDATE division_results_config SET reserves_in_standings = 1")
    await run_queue(league.bot)

    reply = last_said(asked)
    assert reply.startswith("ℹ️ Nothing changed: "), reply
    assert "**Pro**" in reply and "already **visible**" in reply
    assert await _rows(league.db_path, RESERVES) == [(1,)]
    assert await audit_rows(league.db_path) == []
    lines = await log_lines(league)
    assert len(lines) == 1
    assert lines[0].split("\n")[0] == f"{NAMED} | /results reserves toggle | Nothing changed"


async def test_an_xml_form_submitted_after_the_module_was_turned_off_is_refused(tmp_path):
    """The XML import form, shown while results was on, is submitted after the module was
    turned off: refused with the module refusal and its "⛔" line, nothing imported."""
    from leaguebot.results.cogs.results_cog import xml_import_named

    league = await _league(tmp_path)
    await switch(league.bot, ("results",), on=False)

    asked = await _form("xml", XML_30_20)(league)
    await run_queue(league.bot)

    assert last_said(asked) == MODULE_OFF
    assert await log_lines(league) == [_refused(xml_import_named(CONFIG), MODULE_OFF)]
    assert await _rows(league.db_path, POINTS) == [(1, 25), (2, 18)]


# ---------------------------------------------------------------------------
# Fold-ins
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ask", "setup", "command", "reply"),
    [
        (_form("config", "1, 25\n2, 18"), {}, "/results config bulk-session",
         f"ℹ️ Nothing changed: every position pasted already stands in config **{CONFIG}**."),
        (_form("amend", "1, 25\n2, 18"), AMENDING, "/results amend bulk-session",
         "ℹ️ Nothing changed: every position pasted already stands"),
        (_form("xml", XML_HELD), {}, "/results config xml-import",
         "ℹ️ Nothing changed: every value imported already stands"),
        (_form("amend", "1, 25"), AMENDING, "/results amend bulk-session",
         "ℹ️ Nothing changed: every position pasted already stands"),
    ],
    ids=[
        "config-bulk-session", "amend-bulk-session", "config-xml-import",
        "amend-bulk-session-one-line",
    ],
)
async def test_a_paste_or_import_of_values_already_held_changes_nothing_and_says_so(
    tmp_path, ask, setup, command, reply
):
    """A bulk paste, an amend bulk paste and an XML import of P1 25 and P2 18, or an amend bulk
    paste of P1 25 alone, which Standard already pays (or the store already stages): answered
    that nothing changed, recorded with a "Nothing changed" line, and no audit entry written; an
    amend bulk paste leaves the modified flag clear."""
    league = await _league(tmp_path, **setup)

    asked = await ask(league)
    await run_queue(league.bot)

    assert last_said(asked).startswith(reply)
    lines = await log_lines(league)
    assert len(lines) == 1 and lines[0].startswith(f"{NAMED} | {command} | Nothing changed")
    assert await audit_rows(league.db_path) == []
    assert await _rows(league.db_path, MODE) == ([(1, 0)] if setup else [])


async def test_attaching_a_configuration_already_attached_changes_nothing_and_says_so(tmp_path):
    """`/results config append Standard` with season 1, in setup, already holding it: answered
    that nothing changed, a "Nothing changed" line, no audit entry, one link still."""
    league = await _league(tmp_path, season="setup")

    asked = await CASES["config-append"].ask(league)
    await run_queue(league.bot)

    assert last_said(asked) == (
        f"ℹ️ Nothing changed: config **{CONFIG}** is already attached to the current season."
    )
    lines = await log_lines(league)
    assert len(lines) == 1
    assert lines[0].startswith(f"{NAMED} | /results config append | Nothing changed")
    assert await audit_rows(league.db_path) == []
    assert await _rows(league.db_path, LINKS) == [(SEASON_ID, CONFIG)]


@pytest.mark.parametrize(
    ("season", "reply"),
    [
        pytest.param(
            "setup",
            "❌ `/results amend toggle` amends the points of a season under way. A season still "
            "being set up takes its points from the configurations attached to it: change those "
            "with `/results config`.",
        ),
        (None, stage_refusal(None, "results amend toggle")),
        ("pending", stage_refusal(SeasonStage.PENDING_COMPLETION, "results amend toggle")),
    ],
    ids=["setup", "no-season", "pending-completion"],
)
async def test_amendment_mode_is_refused_while_the_season_is_in_setup(tmp_path, season, reply):
    """`/results amend toggle` with season 1 in setup is refused at once with the new refusal
    pointing to `/results config`; with no season, or season 1 pending completion, today's
    refusals stand. Nothing is queued, the mode stays off, and one "⛔" line records it."""
    league = await _league(tmp_path, season=season)

    asked = await CASES["amend-toggle-on"].ask(league)

    assert last_said(asked) == reply
    assert await change_rows(league.db_path) == []
    assert await _rows(league.db_path, MODE) == []
    assert await log_lines(league) == [_refused("`/results amend toggle`", reply)]


@pytest.mark.parametrize(
    ("method", "command", "args", "reply", "setup"),
    [
        ("config_session", "results config session", {"position": 0, "points": 10},
         "❌ Position must be >= 1, got 0.", {}),
        ("config_session", "results config session", {"position": 1, "points": -1},
         "❌ Points must be >= 0, got -1.", {}),
        ("config_fl", "results config fl", {"points": -1},
         "❌ Points must be >= 0, got -1.", {}),
        ("config_fl_plimit", "results config fl-plimit", {"limit": 0},
         "❌ Position limit must be >= 1, got 0.", {}),
        ("amend_session", "results amend session", {"position": 0, "points": 10},
         "❌ Position must be >= 1, got 0.", AMENDING),
        ("amend_fl", "results amend fl", {"points": -1},
         "❌ Points must be >= 0, got -1.", AMENDING),
        ("amend_fl_plimit", "results amend fl-plimit", {"limit": 0},
         "❌ Position limit must be >= 1, got 0.", AMENDING),
    ],
    ids=["config-session-position", "config-session-points", "config-fl", "config-fl-plimit",
         "amend-session", "amend-fl", "amend-fl-plimit"],
)
async def test_a_position_below_one_negative_points_or_a_limit_below_one_is_refused_at_once(
    tmp_path, method, command, args, reply, setup
):
    """A position below 1, negative points or a fastest-lap limit below 1, given to a
    single-value `/results config` or `/results amend` command, is refused at once in the bulk
    paste's words, nothing queued and nothing written, with one "⛔" line."""
    league = await _league(tmp_path, **setup)
    before = await _rows(league.db_path, POINTS) + await _rows(league.db_path, STAGED)

    asked = await _slash(method, command, name=CONFIG, session=FEATURE_RACE, **args)(league)

    assert last_said(asked) == reply
    assert await change_rows(league.db_path) == []
    assert await _rows(league.db_path, POINTS) + await _rows(league.db_path, STAGED) == before
    assert await log_lines(league) == [_refused(f"`/{command}`", reply)]


@pytest.mark.parametrize(
    ("ask", "what"),
    [
        (_slash("amend_session", "results amend session", name="Other", session=FEATURE_RACE,
                position=1, points=30), "`/results amend session`"),
        (_slash("amend_fl", "results amend fl", name="Other", session=FEATURE_RACE, points=2),
         "`/results amend fl`"),
        (_slash("amend_fl_plimit", "results amend fl-plimit", name="Other",
                session=FEATURE_RACE, limit=8), "`/results amend fl-plimit`"),
        (_form("amend", "1, 30", name="Other"), "`/results amend bulk-session`"),
    ],
    ids=["session", "fl", "fl-plimit", "bulk-session"],
)
async def test_an_amend_command_naming_a_configuration_the_season_does_not_hold_is_refused(
    tmp_path, ask, what
):
    """With amendment mode on, season 1 scoring on Standard alone and the configuration Other in
    the server's store, each amend command naming Other is refused, naming the season's
    configurations, nothing staged and no audit entry."""
    league = await _league(tmp_path, **AMENDING, others=("Other",))

    asked = await ask(league)
    await run_queue(league.bot)

    reply = f"❌ Config **Other** is not one of this season's points configurations: {CONFIG}."
    assert last_said(asked) == reply
    assert await log_lines(league) == [_refused(what, reply)]
    assert await _rows(
        league.db_path, "SELECT COUNT(*) FROM season_modification_entries WHERE config_name = 'Other'"
    ) == [(0,)]
    assert await audit_rows(league.db_path) == []
