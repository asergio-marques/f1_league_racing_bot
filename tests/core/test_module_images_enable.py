"""Switching the image module on, with or without a rasteriser.

Issue #208. Enabling images is the one module toggle that depends on a program outside the bot,
and the way it handles that absence is the point of the file.

Images' switch is image's own change on the change queue (#283, S11c): `/module enable images`
asks `module.on:images`, the member is acknowledged at once, and the queue carries it out. Each
test drives the command's handler on the bot the entry point builds, with the real queue and
"now" pinned, and runs the queue.

**The module enables even without the rasteriser** (FR-007, FR-008), so an administrator can
finish configuring while waiting for Inkscape to be installed. It says so rather than refusing —
refusing would leave a league unable to set up the module they are trying to install the
rasteriser *for*.

**Re-enabling is lossless.** The configuration is created only where it is missing, so an
existing configuration is left exactly as it was (FR-004a) — the other half of Principle X
rule 6, under which the disable keeps the configuration and re-enabling finds it as it stood; `tests/image/test_image_module_flow.py` pins both halves.

**Every aspect starts disabled on the first switch-on.** A league enabling the module for the
first time does not thereby start posting graphics; each output aspect is opted into separately,
so enabling cannot surprise a division with a picture where it expected text. Switched on again,
the aspects are as the league left them, and the reply says so rather than claiming they start
off.

**A failed enable leaves nothing.** The configuration, the flag and the audit entry are one
save, so a fault anywhere in it leaves the module off with no configuration behind it — never
on-but-broken, and never a half-made configuration.

`converter_available` is **monkeypatched rather than marked**. `CLAUDE.md`: a test that merely
passes through the rasteriser check on its way to something else is not a rasteriser test, and
marking it would silently stop it running in CI — where it would then never exercise the enable
at all.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from leaguebot.core.cogs.module_cog import ModuleCog
from leaguebot.framework.db.database import get_connection
from tests.support.change_queue import INTERACTION_CHANNEL_ID, MEMBER_ID
from tests.support.settings_league import portrait_refresh_times

#: The member as a saved log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"
#: The enable's reply where the host carries the rasteriser.
ENABLED = "✅ Image module enabled. All output aspects start disabled — use `/images config toggle`."
#: The enable's reply where the module is switched on again over its kept configuration.
ENABLED_AGAIN = (
    "✅ Image module enabled. Your configuration is kept as it stood, each output aspect on or off "
    "as you left it — `/images config view` shows which are on."
)

#: What the enable says where the daily portrait refresh could not be armed (owner's wording,
#: decided 2026-11-15 for #288).
DAILY_NOT_SCHEDULED = (
    "⚠️ The setting is stored, but the daily update could not be scheduled. It will be scheduled "
    "the next time the bot starts with the image module on, or when the module is next switched "
    "on."
)

#: The notice a host without Inkscape adds to the enable's reply, in today's words.
NO_RASTERISER = (
    "⛔ **Inkscape is not installed on the machine running the bot.**\n"
    "Image generation is disabled entirely until it is. No package dependency installs it — it "
    "is a separate program the host must carry.\n"
    "If it is installed somewhere unusual, set the `INKSCAPE` environment variable to the "
    "executable's full path."
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _league(tmp_path, *, on: bool = False):
    """A league whose settings run on the queue, the image module *on* (with its default
    configuration) or off (with none), and the module cog over its bot."""
    from tests.support.settings_league import settings_league

    league = await settings_league(tmp_path, modules=("images",) if on else ())
    return league, ModuleCog(league.bot)


def _asked(league):
    """`/module enable` as Admin, the member the league's lines name."""
    from tests.support.settings_league import interaction

    return interaction(league.bot, "module enable")


def _rasteriser(present: bool):
    """Answer the rasteriser check without needing one.

    Patched rather than marked: a test that merely passes through this check on its way to
    something else is not a rasteriser test, and marking it would stop it running in CI —
    where it would then never exercise the enable at all.
    """
    return patch(
        "leaguebot.image.services.render.image_render_service.converter_available",
        new=MagicMock(return_value=present),
    )


async def _enable(league, cog, asked, *, rasteriser: bool = True) -> None:
    """Run `/module enable images` and the queue behind it, the rasteriser *present* or not."""
    from tests.support.change_queue import run_queue

    with _rasteriser(rasteriser):
        await cog._enable_images(asked)
        await run_queue(league.bot)


async def _audit_types(db_path: str) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT change_type FROM audit_entries ORDER BY id")
        return [r["change_type"] for r in await cursor.fetchall()]


async def _images_flag(db_path: str) -> int | None:
    """The image module's switch, None where no configuration row holds it."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT module_enabled FROM image_config")
        row = await cursor.fetchone()
    return None if row is None else row["module_enabled"]


async def _configuration_rows(db_path: str) -> dict[str, int]:
    """How many rows the image configuration and its aspect toggles hold."""
    async with get_connection(db_path) as db:
        counts = {}
        for table in ("image_config", "image_aspect_toggles"):
            cursor = await db.execute(f"SELECT COUNT(*) AS n FROM {table}")
            counts[table] = (await cursor.fetchone())["n"]
    return counts


async def _keep_daily_portraits(db_path: str) -> None:
    """Switch the module off with its configuration kept as a disable keeps it, portraits on and
    refreshed daily at 07:45 UTC, so that switching it on again finds the daily refresh asked
    for."""
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE image_config SET module_enabled = 0, use_pfp = 1, pfp_daily = 1, "
            "pfp_daily_time = '07:45'"
        )
        await db.commit()


async def _keep_standings_on(db_path: str) -> None:
    """Switch the module off with its configuration kept as a disable keeps it, the standings
    aspect switched on, so that switching it on again finds an aspect already on."""
    async with get_connection(db_path) as db:
        await db.execute("UPDATE image_aspect_toggles SET enabled = 1 WHERE aspect = 'standings'")
        await db.execute("UPDATE image_config SET module_enabled = 0")
        await db.commit()


async def _refuse_the_audit(db_path: str) -> None:
    """Make the audit entry, the last write of the enable's save, fail as a full disk would."""
    async with get_connection(db_path) as db:
        await db.execute(
            "CREATE TRIGGER refuse_the_audit BEFORE INSERT ON audit_entries "
            "BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Enabling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rasteriser", [True, False], ids=["with a rasteriser", "without"])
@pytest.mark.parametrize(
    "again",
    [pytest.param(False, id="first"), pytest.param(True, id="again")],
)
async def test_the_module_is_enabled(tmp_path, again, rasteriser):
    """With or without a rasteriser (FR-007 / FR-008), for the first time or again over a kept
    configuration with the standings aspect on. Refusing would leave a league unable to set up
    the module they are installing the rasteriser for. The member is acknowledged at once, and
    once the change has run the module is on; the first switch-on's reply and the Success line
    keep today's words.

    The first switch-on says every output aspect starts disabled: enabling the module does not
    thereby start posting graphics, so a division is not surprised by a picture where it
    expected text. Switched on again, the reply says the configuration is kept as the league left
    it, since an aspect left on is still on. A missing rasteriser is said, and a present one adds
    nothing: otherwise the first graphic a league expects simply does not arrive, with nothing
    connecting that to a program nobody told them about; and with nothing to warn about, a
    notice every time would be noise on the common path."""
    from tests.support.change_queue import acknowledgement
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path, on=again)
    if again:
        await _keep_standings_on(league.db_path)
    asked = _asked(league)

    await _enable(league, cog, asked, rasteriser=rasteriser)

    assert acknowledgement(asked).startswith("⏳ Turning the image module on. ")
    assert await _images_flag(league.db_path) == 1
    reply = ENABLED_AGAIN if again else ENABLED
    expected = reply if rasteriser else f"{reply}\n\n{NO_RASTERISER}"
    assert last_said(asked) == expected
    assert await log_lines(league) == [f"{NAMED} | /module enable images | Success"]


async def test_enabling_is_audited(tmp_path):
    league, cog = await _league(tmp_path)

    await _enable(league, cog, _asked(league))

    assert await _audit_types(league.db_path) == ["IMAGE_MODULE_ENABLED"]


async def test_enabling_twice_does_no_work(tmp_path):
    """The second call would re-run the defaults write for no reason, and the audit would
    record an event that did not happen. The member is refused at once, nothing is queued, and
    the refusal reaches the log channel as one line in the standard form."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path, on=True)
    asked = _asked(league)

    await _enable(league, cog, asked)

    assert last_said(asked) == "⚠️ Image module is already enabled."
    assert await change_rows(league.db_path) == []
    assert await _audit_types(league.db_path) == []
    assert await log_lines(league) == [
        f"⛔ `/module enable` refused for {NAMED} — Image module is already enabled."
    ]


# ---------------------------------------------------------------------------
# Without a rasteriser
# ---------------------------------------------------------------------------


async def test_the_rasteriser_check_is_not_cached(tmp_path):
    """An administrator installing Inkscape and re-running the command must see the new
    answer, not one cached from before they installed it."""
    from tests.support.change_queue import run_queue

    league, cog = await _league(tmp_path)

    with patch(
        "leaguebot.image.services.render.image_render_service.converter_available",
        new=MagicMock(return_value=True),
    ) as available:
        await cog._enable_images(_asked(league))
        await run_queue(league.bot)

    assert available.call_args.kwargs.get("use_cache") is False


# ---------------------------------------------------------------------------
# A failed enable
# ---------------------------------------------------------------------------


async def test_a_failed_enable_leaves_the_module_off(tmp_path):
    """The configuration, the flag and the audit entry are one save, so a fault at its last
    write leaves the module off with no configuration behind it: never on with nothing
    configured, and never a half-made configuration a later enable would build on."""
    league, cog = await _league(tmp_path)
    await _refuse_the_audit(league.db_path)

    await _enable(league, cog, _asked(league))

    assert await _images_flag(league.db_path) in (None, 0)
    assert await _configuration_rows(league.db_path) == {
        "image_config": 0,
        "image_aspect_toggles": 0,
    }


async def test_an_image_enable_that_cannot_be_written_says_the_module_is_still_off(tmp_path):
    """The audit write fails inside the enable's one save. The member gets the standard
    failure reply, saying the module is still off and what to do next, with no error text; the
    log channel gets the one failure line, and never a success."""
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path)
    await _refuse_the_audit(league.db_path)
    asked = _asked(league)

    await _enable(league, cog, asked)

    replied = last_said(asked)
    assert replied == (
        "❌ `/module enable` stopped on a fault in the bot, not on anything you entered, and did "
        "not finish. The module is still off. Run `/module enable images` again once the fault "
        "is cleared. The fault is recorded in the log channel."
    )
    assert "disk I/O error" not in replied
    assert await log_lines(league) == [
        f"❌ `/module enable` failed for {NAMED} — IntegrityError. The details are in the host's log."
    ]


# ---------------------------------------------------------------------------
# The daily portrait refresh
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("use_pfp", "pfp_daily", "armed"),
    [
        pytest.param(
            1, 1, True, id="daily updates asked for",
        ),
        pytest.param(1, 0, False, id="daily updates not asked for"),
        pytest.param(0, 1, False, id="portraits off"),
    ],
)
async def test_enabling_arms_the_daily_portrait_refresh_where_the_configuration_asks_for_it(
    tmp_path, use_pfp, pfp_daily, armed
):
    """The module switched on again over a kept configuration: with portraits on and daily
    updates set for 07:45, the daily refresh is armed at 07:45; with either off, nothing is
    armed."""
    league, cog = await _league(tmp_path, on=True)
    await _keep_daily_portraits(league.db_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE image_config SET use_pfp = ?, pfp_daily = ?", (use_pfp, pfp_daily)
        )
        await db.commit()

    await _enable(league, cog, _asked(league))

    assert await _images_flag(league.db_path) == 1
    if armed:
        assert portrait_refresh_times(league.scheduler) == ["07:45"]
    else:
        assert portrait_refresh_times(league.scheduler) == []


async def test_an_enable_that_cannot_arm_the_daily_refresh_leaves_the_module_on_as_one_failure(
    tmp_path,
):
    """The scheduler refuses the daily job as the module is switched on again with daily
    portrait updates set. The module stays on; the member is told the refresh could not be
    scheduled and when it will be, with no success; the log channel records one failure and no
    success, as the portrait toggles do."""
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path, on=True)
    await _keep_daily_portraits(league.db_path)
    league.scheduler.arm_daily.side_effect = RuntimeError(
        "the job store refused the job"
    )
    asked = _asked(league)

    await _enable(league, cog, asked)

    assert await _images_flag(league.db_path) == 1
    replied = last_said(asked)
    assert DAILY_NOT_SCHEDULED in replied and "✅" not in replied
    lines = await log_lines(league)
    assert len(lines) == 1, lines
    assert lines[0].startswith(f"❌ `/module enable` failed for {NAMED}"), lines[0]


# ---------------------------------------------------------------------------
# The channels the outputs already on post to (#570, slice 3)
# ---------------------------------------------------------------------------

#: The opening and the close of the enable's refusal for channels that cannot take a picture.
STILL_OFF = (
    "⛔ The image module is still off: outputs switched on post to channels that cannot take "
    "them:"
)
WAY_OUT = (
    "Give the bot those permissions, set those channels again, or switch those outputs back to "
    "text with `/images config toggle`, then run `/module enable images` again."
)
#: The bot command channel, which the season's review posts the lineup and calendar in.
COMMAND = INTERACTION_CHANNEL_ID
#: Pro's standings channel, where results keeps its channels.
PRO_STANDINGS = 121


async def _kept_league(tmp_path, *aspects: str, results: bool = False, **server):
    """The image module switched off over its kept configuration, *aspects* left on, results on
    where *results*, a season in setup holding Pro, its standings channel 121, and a server
    holding every channel as `league_server` is told by *server*; with the module cog."""
    from tests.support.channel_faults import league_server
    from tests.support.settings_league import settings_league

    league = await settings_league(
        tmp_path, modules=("images", "results") if results else ("images",)
    )
    async with get_connection(league.db_path) as db:
        for aspect in aspects:
            await db.execute(
                "UPDATE image_aspect_toggles SET enabled = 1 WHERE aspect = ?", (aspect,)
            )
        await db.execute("UPDATE image_config SET module_enabled = 0")
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-03-01', 'SETUP', 1)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, status, tier) "
            "VALUES (1, 1, 'Pro', 1, 'SETUP', 1)"
        )
        await db.execute(
            "INSERT INTO division_results_config (division_id, standings_channel_id) "
            "VALUES (1, ?)",
            (PRO_STANDINGS,),
        )
        await db.commit()
    league_server(league.bot, **server)
    return league, ModuleCog(league.bot)


@pytest.mark.parametrize(
    ("aspects", "results", "server", "named"),
    [
        pytest.param(
            ("lineup", "calendar"), False, {"unusable": {COMMAND: ("attach_files",)}},
            ("**Lineup, Calendar**", f"<#{COMMAND}>", "Attach Files"),
            id="lineup and calendar on, command channel lacking Attach Files",
        ),
        pytest.param(
            ("standings",), True, {"gone": (PRO_STANDINGS,)},
            ("**Standings**", f"<#{PRO_STANDINGS}>", "no longer on the server"),
            id="standings on, results on, standings channel gone",
        ),
        pytest.param(
            ("standings",), False, {"gone": (PRO_STANDINGS,)}, None,
            id="standings on, results off, standings channel gone",
        ),
        pytest.param(
            ("calendar",), True, {"gone": (PRO_STANDINGS,)}, None,
            id="calendar on, standings left as text, results on, standings channel gone",
        ),
    ],
)
async def test_enabling_is_refused_where_an_output_left_on_posts_to_a_channel_that_cannot_take_it(
    tmp_path, aspects, results, server, named
):
    """The image module switched off over its kept configuration, a season in setup holding Pro.
    Lineup and Calendar left on, the bot command channel 100 lacking Attach Files; or Standings
    left on with results on, Pro's standings channel 121 deleted: `/module enable images` is
    refused, the module still off, the reply naming the channel in one bullet with the outputs
    that post to it and what it lacks or that it is gone, and the ways out; one "⛔" line and no
    audit entry. Standings left on with results off, its channel deleted, is not judged; nor,
    with results on, is the channel of Standings left as text while the Calendar is on: the
    module is switched on again as today, and channel 121 is never looked for."""
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _kept_league(tmp_path, *aspects, results=results, **server)
    asked = _asked(league)

    await _enable(league, cog, asked)

    if named is None:
        assert await _images_flag(league.db_path) == 1
        assert last_said(asked) == ENABLED_AGAIN
        guild = league.bot.get_guild.return_value
        looked_for = guild.get_channel.call_args_list + guild.fetch_channel.call_args_list
        assert PRO_STANDINGS not in [call.args[0] for call in looked_for]
        return
    assert await _images_flag(league.db_path) == 0
    reply = last_said(asked)
    assert reply.startswith(f"{STILL_OFF}\n• ")
    assert reply.endswith(f"\n{WAY_OUT}")
    bullets = [line for line in reply.split("\n") if line.startswith("• ")]
    assert len(bullets) == 1
    assert all(words in bullets[0] for words in named), bullets[0]
    assert await _audit_types(league.db_path) == []
    [line] = await log_lines(league)
    assert line.startswith(f"⛔ `/module enable` refused for {NAMED} — ")


@pytest.mark.parametrize("cause", ["a channel's fetch fails", "the server is out of reach"])
async def test_an_enable_that_cannot_tell_leaves_the_module_off_and_says_to_run_it_again(
    tmp_path, cause
):
    """The image module switched off over its kept configuration with the Calendar left on, and
    Discord answers the fetch of the bot command channel 100 with a server error, or the
    league's server is out of the bot's reach: `/module enable images` cannot make the check, so
    the module stays off, the member is told it stopped on a fault and to run it again, the one
    line is a failure, nothing is audited and nothing stops the queue."""
    from unittest.mock import MagicMock

    from tests.support.change_queue import stopped_job
    from tests.support.settings_league import last_said, log_lines

    server = {"unreadable": (COMMAND,)} if cause == "a channel's fetch fails" else {}
    league, cog = await _kept_league(tmp_path, "calendar", **server)
    if cause == "the server is out of reach":
        league.bot.get_guild = MagicMock(return_value=None)
    asked = _asked(league)

    await _enable(league, cog, asked)

    assert await _images_flag(league.db_path) == 0
    assert last_said(asked) == (
        "❌ `/module enable` stopped on a fault in the bot, not on anything you entered, and did "
        "not finish. The module is still off. Run `/module enable images` again once the fault "
        "is cleared. The fault is recorded in the log channel."
    )
    assert await _audit_types(league.db_path) == []
    [line] = await log_lines(league)
    assert line.startswith(f"❌ `/module enable` failed for {NAMED} — ")
    assert await stopped_job(league.db_path) is None
