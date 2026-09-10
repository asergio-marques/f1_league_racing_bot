"""`/season review` shows a manager what their league will actually see.

The rule this file pins, and it **reverses** 038 FR-027 (decided 2026-08-27): where an
aspect is switched on, the graphic *replaces* that section's text rather than standing
beside it. A review that shows text a league will never see is showing the wrong thing.

Three states and not two, which is the part most at risk from a well-meant simplification:

* the aspect is **off** — the league conveys the section as text, nothing is wrong, and
  the review posts the text exactly as it always did;
* the graphic **drew** — the text is not posted at all;
* the graphic was wanted and **could not be drawn** — the fault is reported to the
  manager, the text still stands in so the review stays a complete picture, and the
  approve button is withheld.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

SRC = Path(__file__).resolve().parents[2] / "src"


# ── Helpers ───────────────────────────────────────────────────────────────


def _cog(bot=None):
    from cogs.season_cog import SeasonCog

    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot if bot is not None else MagicMock()
    cog.bot.db_path = ":memory:"
    return cog


def _interaction(guild_id: int = 7) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.guild = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _division(division_id: int = 3):
    division = MagicMock()
    division.id = division_id
    division.name = "Elite"
    division.tier = 1
    return division


def _png(tmp_path: Path) -> Path:
    """A real file, since `discord.File` opens what it is given."""
    path = tmp_path / "drawn.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return path


def _function_source(path: Path, name: str, *, code_only: bool = False) -> str:
    """The source of one function. *code_only* drops its docstring.

    A docstring naming what the function must **not** do would satisfy a plain substring
    check on its own, so a test asserting an absence has to read the code alone.
    """
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            if code_only:
                body = node.body[1:] if ast.get_docstring(node) else node.body
                return "\n".join(ast.get_source_segment(text, stmt) for stmt in body)
            return ast.get_source_segment(text, node)
    raise AssertionError(f"{name} not found in {path}")


# ── The lineup graphic ────────────────────────────────────────────────────


async def test_the_lineup_aspect_being_off_leaves_the_text_to_the_caller(monkeypatch):
    from cogs.season_cog import REVIEW_IMAGE_TEXT
    import services.image_lineup_post as lineup_post

    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=False))
    interaction = _interaction()

    state = await _cog()._post_review_lineup_image(interaction, _division())

    assert state == REVIEW_IMAGE_TEXT
    interaction.followup.send.assert_not_awaited()


async def test_a_drawn_lineup_is_posted_and_the_caller_posts_no_text(monkeypatch, tmp_path):
    from cogs.season_cog import REVIEW_IMAGE_DREW
    import services.image_lineup_post as lineup_post

    png = _png(tmp_path)
    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        lineup_post,
        "render_for_command",
        AsyncMock(return_value=MagicMock(png_path=png, message=None, notices=[])),
    )
    interaction = _interaction()

    state = await _cog()._post_review_lineup_image(interaction, _division())

    assert state == REVIEW_IMAGE_DREW
    interaction.followup.send.assert_awaited_once()
    _args, kwargs = interaction.followup.send.call_args
    assert kwargs["file"] is not None
    assert kwargs["ephemeral"] is False


async def test_a_lineup_that_would_not_draw_reports_the_fault(monkeypatch):
    """The manager is told, and the caller is told to fall back to its text."""
    from cogs.season_cog import REVIEW_IMAGE_FAULT
    import services.image_lineup_post as lineup_post

    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        lineup_post,
        "render_for_command",
        AsyncMock(
            return_value=MagicMock(
                png_path=None, message="❌ the template declares no `team_1_name`"
            )
        ),
    )
    interaction = _interaction()

    state = await _cog()._post_review_lineup_image(interaction, _division())

    assert state == REVIEW_IMAGE_FAULT
    interaction.followup.send.assert_awaited_once()
    args, kwargs = interaction.followup.send.call_args
    assert "team_1_name" in args[0]
    assert kwargs["ephemeral"] is True


# ── The calendar graphic ──────────────────────────────────────────────────


async def test_the_calendar_aspect_being_off_leaves_the_text_to_the_caller(monkeypatch):
    from cogs.season_cog import REVIEW_IMAGE_TEXT
    import services.calendar_post_service as calendar_post

    monkeypatch.setattr(
        calendar_post, "image_calendar_wanted", AsyncMock(return_value=False)
    )
    interaction = _interaction()

    state = await _cog()._post_review_calendar_image(interaction, _division(), [], 3)

    assert state == REVIEW_IMAGE_TEXT
    interaction.followup.send.assert_not_awaited()


async def test_a_drawn_calendar_is_posted_and_the_caller_posts_no_text(
    monkeypatch, tmp_path
):
    from cogs.season_cog import REVIEW_IMAGE_DREW
    import services.calendar_post_service as calendar_post

    png = _png(tmp_path)
    monkeypatch.setattr(
        calendar_post, "image_calendar_wanted", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(calendar_post, "tracks_by_name", AsyncMock(return_value={}))
    monkeypatch.setattr(
        calendar_post,
        "render_for_command",
        AsyncMock(return_value=MagicMock(png_path=png, message=None, notices=[])),
    )
    interaction = _interaction()

    state = await _cog()._post_review_calendar_image(interaction, _division(), [], 3)

    assert state == REVIEW_IMAGE_DREW
    interaction.followup.send.assert_awaited_once()
    _args, kwargs = interaction.followup.send.call_args
    assert kwargs["file"] is not None


async def test_a_calendar_that_would_not_draw_reports_the_fault(monkeypatch):
    from cogs.season_cog import REVIEW_IMAGE_FAULT
    import services.calendar_post_service as calendar_post

    monkeypatch.setattr(
        calendar_post, "image_calendar_wanted", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(calendar_post, "tracks_by_name", AsyncMock(return_value={}))
    monkeypatch.setattr(
        calendar_post,
        "render_for_command",
        AsyncMock(
            return_value=MagicMock(
                png_path=None, message="❌ no value for `round_4_race_name`"
            )
        ),
    )
    interaction = _interaction()

    state = await _cog()._post_review_calendar_image(interaction, _division(), [], 3)

    assert state == REVIEW_IMAGE_FAULT
    args, kwargs = interaction.followup.send.call_args
    assert "round_4_race_name" in args[0]
    assert kwargs["ephemeral"] is True


# ── The calendar's command-output renderer ────────────────────────────────


def test_calendar_render_for_command_is_not_the_calendar_of_record():
    """It must post to no channel and write no message id — that is why it exists."""
    source = _function_source(
        SRC / "services" / "calendar_post_service.py", "render_for_command", code_only=True
    )
    assert "calendar_message_id" not in source
    assert "replace_calendar_message" not in source
    assert "channel" not in source


# ── The review's own structure ────────────────────────────────────────────


def test_the_review_posts_a_graphic_instead_of_its_text_never_beside_it():
    """Reverses 038 FR-027: the section's text is sent only where no graphic was drawn."""
    source = _function_source(SRC / "cogs" / "season_cog.py", "season_review")

    for state_call, text_send in (
        ("_post_review_calendar_image", "join(cal_lines)"),
        ("_post_review_lineup_image", "join(lineup_lines)"),
    ):
        image_at = source.index(state_call)
        text_at = source.index(text_send)
        assert image_at < text_at, f"{state_call} must decide before the text is built"

    assert "if cal_state != REVIEW_IMAGE_DREW:" in source
    assert "if lineup_state == REVIEW_IMAGE_DREW:" in source


def test_a_graphic_that_would_not_draw_withholds_the_approve_button():
    """A season approved on a broken template would post the fault to the league."""
    source = _function_source(SRC / "cogs" / "season_cog.py", "season_review")

    # A list of reasons rather than a flag: an undrawable graphic and an invalid driver
    # portrait configuration both withhold the button, and they read differently.
    assert "approval_blockers: list[str] = []" in source
    assert source.count("approval_blockers.append(") == 3, (
        "both graphics and the portrait settings must raise it"
    )

    tail = source[source.index("Server-level UNASSIGNED"):]
    assert "if approval_blockers:" in tail
    guarded = tail[tail.index("if approval_blockers:"):]
    fault_branch, _, approve_branch = guarded.partition("else:")
    assert "_post_approval_prompt" not in fault_branch, (
        "the button must not be offered on a fault"
    )
    assert "image module is not correctly configured" in fault_branch
    assert "_post_approval_prompt" in approve_branch


def test_the_roleless_team_warning_survives_the_graphic():
    """A review finding the picture cannot show must be posted either way."""
    source = _function_source(SRC / "cogs" / "season_cog.py", "season_review")
    block = source[source.index("_post_review_lineup_image"):]
    drew, _, textual = block.partition("if lineup_state == REVIEW_IMAGE_DREW:")
    assert "role_warning" in textual.split("else:")[0]


# ── Pre-generation: every graphic drawn before any is posted ──────────────
#
# The instant deletion turned out not to be a constraint at all: `discard_render` is
# called by whoever posted the file, and nothing sweeps the render directories in the
# background, so holding a batch is a matter of not calling it yet. What bounds the batch
# is the host — `/tmp` on the Raspberry Pi is a tmpfs, so a picture waiting to be posted
# costs RAM.


def _prerender_bot():
    bot = MagicMock()
    bot.db_path = ":memory:"
    return bot


def _outcome(png):
    return MagicMock(png_path=png, message=None, notices=[])


async def test_every_graphic_is_drawn_before_any_division_block_is_posted(
    monkeypatch, tmp_path
):
    """The point of the change: the review stops dribbling out one render at a time."""
    import services.calendar_post_service as cps
    import services.image_lineup_post as lineup_post

    drawn = []

    async def _calendar(bot, server_id, division, rounds, tracks, **kwargs):
        drawn.append(("calendar", division.id))
        return _outcome(_png(tmp_path))

    async def _lineup(bot, guild, division_id):
        drawn.append(("lineup", division_id))
        return _outcome(_png(tmp_path))

    monkeypatch.setattr(cps, "image_calendar_wanted", AsyncMock(return_value=True))
    monkeypatch.setattr(cps, "tracks_by_name", AsyncMock(return_value={}))
    monkeypatch.setattr(cps, "render_for_command", _calendar)
    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(lineup_post, "render_for_command", _lineup)

    divisions = [_division(1), _division(2)]
    prepared = await _cog(_prerender_bot())._prerender_review_images(
        _interaction(), divisions, {1: [], 2: []}, 4
    )

    assert set(prepared) == {(1, "calendar"), (1, "lineup"), (2, "calendar"), (2, "lineup")}
    assert drawn == [
        ("calendar", 1), ("lineup", 1), ("calendar", 2), ("lineup", 2),
    ]


async def test_an_aspect_that_is_off_is_not_pre_rendered(monkeypatch, tmp_path):
    """A key left absent is what tells the posting helper to decide for itself."""
    import services.calendar_post_service as cps
    import services.image_lineup_post as lineup_post

    monkeypatch.setattr(cps, "image_calendar_wanted", AsyncMock(return_value=False))
    monkeypatch.setattr(cps, "tracks_by_name", AsyncMock(return_value={}))
    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        lineup_post, "render_for_command", AsyncMock(return_value=_outcome(_png(tmp_path)))
    )

    prepared = await _cog(_prerender_bot())._prerender_review_images(
        _interaction(), [_division(1)], {1: []}, 4
    )

    assert set(prepared) == {(1, "lineup")}


async def test_both_aspects_off_draws_nothing_and_reads_no_tracks(monkeypatch):
    import services.calendar_post_service as cps
    import services.image_lineup_post as lineup_post

    tracks = AsyncMock(return_value={})
    monkeypatch.setattr(cps, "image_calendar_wanted", AsyncMock(return_value=False))
    monkeypatch.setattr(cps, "tracks_by_name", tracks)
    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=False))

    prepared = await _cog(_prerender_bot())._prerender_review_images(
        _interaction(), [_division(1)], {1: []}, 4
    )

    assert prepared == {}
    tracks.assert_not_awaited()


async def test_a_pre_render_that_raises_leaves_the_key_absent(monkeypatch, tmp_path):
    """The posting helper draws it again, where the message to the manager already lives."""
    import services.calendar_post_service as cps
    import services.image_lineup_post as lineup_post

    monkeypatch.setattr(cps, "image_calendar_wanted", AsyncMock(return_value=True))
    monkeypatch.setattr(cps, "tracks_by_name", AsyncMock(return_value={}))
    monkeypatch.setattr(
        cps, "render_for_command", AsyncMock(side_effect=RuntimeError("inkscape gone"))
    )
    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        lineup_post, "render_for_command", AsyncMock(return_value=_outcome(_png(tmp_path)))
    )

    prepared = await _cog(_prerender_bot())._prerender_review_images(
        _interaction(), [_division(1)], {1: []}, 4
    )

    assert (1, "calendar") not in prepared
    assert (1, "lineup") in prepared


async def test_a_gate_that_raises_pre_renders_nothing(monkeypatch):
    """A review must survive a fault in the gates, as the calendar's own gate does."""
    import services.calendar_post_service as cps

    monkeypatch.setattr(
        cps, "image_calendar_wanted", AsyncMock(side_effect=RuntimeError("db down"))
    )

    prepared = await _cog(_prerender_bot())._prerender_review_images(
        _interaction(), [_division(1)], {1: []}, 4
    )

    assert prepared == {}


async def test_the_budget_stops_the_pre_render_rather_than_the_review(
    monkeypatch, tmp_path
):
    """Past the budget a division draws at the moment it is posted, as it always did.

    `/tmp` is a tmpfs on the host the bot runs on, so an unbounded batch would take the
    machine's memory rather than merely its time. The guard costs speed, never
    correctness — an absent key is exactly what an aspect being off already produces.
    """
    from cogs.season_cog import SeasonCog

    import services.calendar_post_service as cps
    import services.image_lineup_post as lineup_post

    big = tmp_path / "big.png"
    big.write_bytes(b"\x00" * 4096)

    monkeypatch.setattr(
        SeasonCog, "REVIEW_PRERENDER_BUDGET_BYTES", 4096, raising=True
    )
    monkeypatch.setattr(cps, "image_calendar_wanted", AsyncMock(return_value=True))
    monkeypatch.setattr(cps, "tracks_by_name", AsyncMock(return_value={}))
    monkeypatch.setattr(cps, "render_for_command", AsyncMock(return_value=_outcome(big)))
    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        lineup_post, "render_for_command", AsyncMock(return_value=_outcome(big))
    )

    divisions = [_division(1), _division(2), _division(3)]
    prepared = await _cog(_prerender_bot())._prerender_review_images(
        _interaction(), divisions, {1: [], 2: [], 3: []}, 4
    )

    # The first calendar alone spends the whole budget; nothing after it is held.
    assert set(prepared) == {(1, "calendar")}


async def test_a_prepared_graphic_is_posted_without_being_drawn_again(
    monkeypatch, tmp_path
):
    from cogs.season_cog import REVIEW_IMAGE_DREW
    import services.image_lineup_post as lineup_post

    render = AsyncMock()
    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(lineup_post, "render_for_command", render)
    interaction = _interaction()

    state = await _cog()._post_review_lineup_image(
        interaction, _division(), prepared=_outcome(_png(tmp_path))
    )

    assert state == REVIEW_IMAGE_DREW
    render.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()


async def test_a_prepared_calendar_is_posted_without_being_drawn_again(
    monkeypatch, tmp_path
):
    from cogs.season_cog import REVIEW_IMAGE_DREW
    import services.calendar_post_service as cps

    render = AsyncMock()
    monkeypatch.setattr(cps, "image_calendar_wanted", AsyncMock(return_value=True))
    monkeypatch.setattr(cps, "render_for_command", render)
    monkeypatch.setattr(cps, "tracks_by_name", AsyncMock(return_value={}))
    interaction = _interaction()

    state = await _cog()._post_review_calendar_image(
        interaction, _division(), [], 3, prepared=_outcome(_png(tmp_path))
    )

    assert state == REVIEW_IMAGE_DREW
    render.assert_not_awaited()


def test_nothing_pre_rendered_survives_the_review():
    """A file held for posting and never posted must not be left on a tmpfs."""
    source = _function_source(SRC / "cogs" / "season_cog.py", "season_review")

    assert "_prerender_review_images" in source
    assert "finally:" in source
    assert "_discard_prepared_review_images(prepared)" in source

    prerender_at = source.index("_prerender_review_images")
    loop_at = source.index("for div in db_divisions:")
    assert prerender_at < loop_at, "every graphic is drawn before the first is posted"


def test_the_discard_removes_every_graphic_still_held(tmp_path, monkeypatch):
    from cogs.season_cog import SeasonCog

    import services.image_render_service as render_service

    removed = []
    monkeypatch.setattr(
        render_service, "discard_render", lambda *paths: removed.extend(paths)
    )

    prepared = {
        (1, "calendar"): _outcome(tmp_path / "a.png"),
        (2, "lineup"): _outcome(tmp_path / "b.png"),
    }
    SeasonCog._discard_prepared_review_images(prepared)

    assert removed == [tmp_path / "a.png", tmp_path / "b.png"]
    assert prepared == {}


def test_the_posting_loop_pops_what_it_posts():
    """What the discard finds is what was never sent, and only that."""
    source = _function_source(SRC / "cogs" / "season_cog.py", "season_review")

    assert 'prepared.pop((div.id, "calendar"), None)' in source
    assert 'prepared.pop((div.id, "lineup"), None)' in source


# ── Approval trusts the review's render, and proves the season is unchanged ──
#
# The approval used to draw every graphic a second time to prove they still drew. That is
# withdrawn (2026-09-07): the review draws them, and the button refuses unless the season
# still fingerprints as the one the review described — so the review's render is evidence
# for the approval, and repeating it was a full rasterisation per division for an answer
# already in hand.


def test_the_approval_draws_nothing_itself():
    """The render pass is gone, and must not creep back in."""
    approve = _function_source(SRC / "cogs" / "season_cog.py", "_do_approve", code_only=True)

    for drawn in ("_undrawable_graphics", "render_for_command", "render_lineup"):
        assert drawn not in approve, f"{drawn} draws at approval; the review does that"


def test_approval_refuses_before_it_commits_anything():
    """The fingerprint stands where the render stood: ahead of everything committed."""
    source = _function_source(SRC / "cogs" / "season_cog.py", "approve")

    gate_at = source.index("differs_from")
    assert source.index("_may_approve") < gate_at, (
        "who is pressing is settled before what they are pressing on"
    )
    assert gate_at < source.index("_do_approve"), (
        "the season must be proven unchanged before it is approved"
    )


def test_a_changed_season_approves_nothing():
    """The refusal returns rather than merely reporting."""
    source = _function_source(SRC / "cogs" / "season_cog.py", "approve")

    branch = source[source.index("if changed:") : source.index("await self._cog._do_approve")]
    assert "return" in branch
    assert "Nothing has been approved" in branch

    # The cheap module checks come *before* it, so a league missing a channel is not made
    # to pay for a rasterisation it was never going to keep (settled 2026-09-07).


def test_the_review_and_the_approval_read_the_same_evaluation():
    """Both must refuse on the same fault, or a manager can commit past the review."""
    review = _function_source(SRC / "cogs" / "season_cog.py", "season_review")
    approve = _function_source(SRC / "cogs" / "season_cog.py", "_do_approve")

    assert "approval_blockers" in review
    # The review still withholds its button on a graphic that will not draw. The approval
    # no longer re-draws to find that out — it refuses unless the season still fingerprints
    # as the one the review described, which is the same evidence reached more cheaply.
    assert "differs_from" in _function_source(
        SRC / "cogs" / "season_cog.py", "approve"
    )

    # The portrait settings block on both surfaces too, and through one helper so that the
    # two cannot disagree about whether a season may be approved.
    assert "_portrait_configuration_blocker" in review
    assert "_portrait_configuration_blocker" in approve


# ── The driver portrait settings in the review, and at approval ───────────


def _portrait_cog(**config):
    from unittest.mock import AsyncMock, MagicMock
    from types import SimpleNamespace
    from cogs.season_cog import SeasonCog

    values = {
        "use_pfp": False,
        "pfp_prerender": True,
        "pfp_daily": False,
        "pfp_daily_time": "03:00",
    }
    values.update(config)
    bot = MagicMock()
    bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    bot.image_config_service.get_config = AsyncMock(
        return_value=None if values.get("_absent") else SimpleNamespace(**values)
    )
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    return cog, bot


async def test_the_review_states_the_portrait_settings_when_disabled():
    cog, _bot = _portrait_cog(use_pfp=False)

    lines = await cog._portrait_review_lines(1)

    # Stated even when off: "the bot is not obtaining portraits" is an answer, not an
    # absence of one, for a manager deciding whether the season is configured.
    assert any("Driver portraits from Discord: disabled" in line for line in lines)


async def test_the_review_names_both_update_triggers_and_the_time_in_utc():
    cog, _bot = _portrait_cog(
        use_pfp=True, pfp_prerender=True, pfp_daily=True, pfp_daily_time="07:45"
    )

    lines = await cog._portrait_review_lines(1)
    text = "\n".join(lines)

    assert "enabled" in text
    assert "before each render" in text
    assert "daily at 07:45 UTC" in text


async def test_the_review_marks_an_invalid_portrait_configuration():
    cog, _bot = _portrait_cog(use_pfp=True, pfp_prerender=False, pfp_daily=False)

    text = "\n".join(await cog._portrait_review_lines(1))

    assert "⛔" in text
    assert "neither pre-render nor daily updates" in text


async def test_the_review_section_survives_a_configuration_it_cannot_read():
    cog, bot = _portrait_cog()
    bot.image_config_service.get_config.side_effect = RuntimeError("gone")

    lines = await cog._portrait_review_lines(1)

    # A review must never fail because of this section.
    assert any("could not be read" in line for line in lines)


async def test_the_blocker_fires_only_on_an_invalid_configuration():
    invalid, _ = _portrait_cog(use_pfp=True, pfp_prerender=False, pfp_daily=False)
    assert await invalid._portrait_configuration_blocker(1) is not None

    for values in (
        {"use_pfp": False, "pfp_prerender": False, "pfp_daily": False},
        {"use_pfp": True, "pfp_prerender": True, "pfp_daily": False},
        {"use_pfp": True, "pfp_prerender": False, "pfp_daily": True},
        {"use_pfp": True, "pfp_prerender": True, "pfp_daily": True},
    ):
        cog, _ = _portrait_cog(**values)
        assert await cog._portrait_configuration_blocker(1) is None, values


async def test_the_blocker_stands_aside_where_the_image_module_is_disabled():
    cog, bot = _portrait_cog(use_pfp=True, pfp_prerender=False, pfp_daily=False)
    bot.module_service.is_images_enabled = AsyncMock(return_value=False)

    assert await cog._portrait_configuration_blocker(1) is None


async def test_the_blocker_never_blocks_a_season_it_could_not_read():
    cog, bot = _portrait_cog()
    bot.image_config_service.get_config.side_effect = RuntimeError("gone")

    assert await cog._portrait_configuration_blocker(1) is None


def test_the_approval_gate_returns_rather_than_merely_reporting():
    source = _function_source(SRC / "cogs" / "season_cog.py", "_do_approve")

    assert "Gate 4c" in source
    branch = source[source.index("if portrait_fault is not None:"):]
    branch = branch[: branch.index("snapshot_configs_to_season")]
    assert "return" in branch
    assert "Season cannot be approved" in branch


# ── The approval window, and who may answer it ────────────────────────────
#
# A review is a photograph of the season at the moment it was posted, and the button
# commits on the strength of it. A manager who edits a round, moves a channel or reseats
# a driver and then presses a button from half an hour ago would approve a season nobody
# has reviewed — so the button stands for five minutes and is then deleted (2026-09-07).
#
# The message is public, which is what makes the access check load-bearing rather than
# decorative: a league manager holding only the interaction role may run `/season review`,
# and anyone who can read the channel can see the button they post.


from cogs.season_cog import _ApproveView  # noqa: E402

REVIEWER = 4242


def _approve_view(reviewer_id: int = REVIEWER):
    cog = MagicMock()
    cog._do_approve = AsyncMock()
    cog.bot.db_path = "/nonexistent/nowhere.db"
    view = _ApproveView(cog, reviewer_id)
    view._server_id = 7
    view._season_id = 1
    return view, cog


def _member(user_id: int, administrator: bool = False):
    member = MagicMock()
    member.id = user_id
    member.guild_permissions.administrator = administrator
    return member


def _button_interaction(user=None):
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.user = user if user is not None else _member(REVIEWER)
    return interaction


async def test_the_reviewer_may_approve():
    view, cog = _approve_view()

    await _ApproveView.approve(view, _button_interaction(), MagicMock())

    cog._do_approve.assert_awaited_once()


async def test_a_server_administrator_may_approve_somebody_elses_review():
    view, cog = _approve_view()
    presser = _member(99, administrator=True)

    await _ApproveView.approve(view, _button_interaction(presser), MagicMock())

    cog._do_approve.assert_awaited_once()


async def test_another_league_manager_may_not_approve():
    """The case the public message creates. Manage Server is deliberately not enough —
    only the reviewer or a server administrator."""
    view, cog = _approve_view()
    presser = _member(99, administrator=False)
    presser.guild_permissions.manage_guild = True
    interaction = _button_interaction(presser)

    await _ApproveView.approve(view, interaction, MagicMock())

    cog._do_approve.assert_not_awaited()
    reply = interaction.response.send_message.await_args.args[0]
    assert "Nothing has been approved" in reply
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_the_access_check_runs_before_the_fingerprint():
    """A press by somebody who may not approve costs no query."""
    import inspect

    source = inspect.getsource(_ApproveView.approve)

    assert source.index("_may_approve") < source.index("take_fingerprint"), (
        "the fingerprint is taken for a member who may not approve anyway"
    )


async def test_the_view_stops_listening_on_the_window():
    """discord.py's own timeout is what fires the deletion, so it must be the window."""
    from cogs.season_cog import APPROVAL_WINDOW_SECONDS

    view, _cog = _approve_view()

    assert view.timeout == APPROVAL_WINDOW_SECONDS


# ── Expiry deletes the question and says so ───────────────────────────────


def _bound_view():
    view, cog = _approve_view()
    message = MagicMock()
    message.delete = AsyncMock()
    message.channel.send = AsyncMock()
    view._message = message
    return view, cog, message


async def test_the_expired_prompt_is_deleted():
    """Left standing, a public message offers a button nobody may press."""
    view, _cog, message = _bound_view()

    await view.on_timeout()

    message.delete.assert_awaited_once()


async def test_the_expiry_notice_pings_the_reviewer():
    view, _cog, message = _bound_view()

    await view.on_timeout()

    notice = message.channel.send.await_args.args[0]
    assert f"<@{REVIEWER}>" in notice
    assert "/season review" in notice


async def test_a_prompt_already_gone_still_posts_the_notice():
    """Deleted by hand between the review and the timeout."""
    import discord

    view, _cog, message = _bound_view()
    message.delete = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))

    await view.on_timeout()

    message.channel.send.assert_awaited_once()


async def test_a_changed_season_expires_the_prompt_rather_than_leaving_it():
    """The report is stale either way, so the question must not stand."""
    from services.season_fingerprint_service import SeasonFingerprint

    view, cog, message = _bound_view()
    view._fingerprint = SeasonFingerprint({"season": "abc"})
    interaction = _button_interaction()

    async def _changed(*_args, **_kwargs):
        return SeasonFingerprint({"season": "def"})

    import services.season_fingerprint_service as _sfs

    original = _sfs.take_fingerprint
    _sfs.take_fingerprint = _changed
    try:
        await _ApproveView.approve(view, interaction, MagicMock())
    finally:
        _sfs.take_fingerprint = original

    cog._do_approve.assert_not_awaited()
    message.delete.assert_awaited_once()


async def test_approving_clears_the_review_it_was_approved_from():
    """The report describes a season awaiting a decision, and the decision is taken.

    Left standing it is a long scroll of a state that has moved on. The approval's own
    confirmation is ephemeral and survives, so the manager still sees the outcome.
    """
    view, cog, message = _bound_view()
    report = [MagicMock(delete=AsyncMock()) for _ in range(3)]
    view.carries(report)

    await _ApproveView.approve(view, _button_interaction(), MagicMock())

    cog._do_approve.assert_awaited_once()
    message.delete.assert_awaited_once()
    for posted in report:
        posted.delete.assert_awaited_once()


async def test_an_expired_review_is_cleared_too():
    """A review nobody may answer must not be left claiming a decision is pending."""
    view, _cog, message = _bound_view()
    report = [MagicMock(delete=AsyncMock()) for _ in range(2)]
    view.carries(report)

    await view.on_timeout()

    message.delete.assert_awaited_once()
    for posted in report:
        posted.delete.assert_awaited_once()


async def test_a_report_message_already_gone_does_not_stop_the_rest():
    """One deleted by hand must not strand the other eleven."""
    import discord

    view, _cog, message = _bound_view()
    gone = MagicMock(delete=AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "x")))
    survivor = MagicMock(delete=AsyncMock())
    view.carries([gone, survivor])

    await _ApproveView.approve(view, _button_interaction(), MagicMock())

    survivor.delete.assert_awaited_once()


# ── The question the button is attached to ────────────────────────────────


# ── Collecting the report, so approving can clear it ──────────────────────


async def test_the_recorder_collects_every_public_message():
    """One interception point rather than ten. The review sends from ten places, several
    inside helpers, and a collector threaded through all of them would be forgotten by the
    eleventh caller."""
    from cogs.season_cog import SeasonCog

    interaction = MagicMock()
    sent = [MagicMock(), MagicMock()]
    interaction.followup.send = AsyncMock(side_effect=sent)
    posted: list = []

    SeasonCog._recording_followup(interaction, posted)
    await interaction.followup.send("first")
    await interaction.followup.send("second")

    assert posted == sent


async def test_the_recorder_asks_for_the_message_back():
    """`followup.send` returns None unless `wait=True`, so without it nothing is collected
    and the report could never be cleared."""
    from cogs.season_cog import SeasonCog

    interaction = MagicMock()
    original = AsyncMock(return_value=MagicMock())
    interaction.followup.send = original

    SeasonCog._recording_followup(interaction, [])
    await interaction.followup.send("body")

    assert original.await_args.kwargs["wait"] is True


async def test_the_recorder_leaves_ephemeral_messages_alone():
    """An ephemeral followup is the reviewer's alone and cannot be deleted by id; the
    fault reports among them are how a manager knows what to fix."""
    from cogs.season_cog import SeasonCog

    interaction = MagicMock()
    original = AsyncMock(return_value=MagicMock())
    interaction.followup.send = original
    posted: list = []

    SeasonCog._recording_followup(interaction, posted)
    await interaction.followup.send("a fault", ephemeral=True)

    assert posted == []
    assert "wait" not in original.await_args.kwargs


async def test_the_recorder_returns_the_original_for_restoring():
    from cogs.season_cog import SeasonCog

    interaction = MagicMock()
    original = AsyncMock(return_value=MagicMock())
    interaction.followup.send = original

    returned = SeasonCog._recording_followup(interaction, [])

    assert returned is original
    assert interaction.followup.send is not original


def test_the_review_restores_the_followup_it_wrapped():
    """The interaction outlives the command, and a wrapper left in place would collect
    into a list nothing will ever read."""
    source = _function_source(SRC / "cogs" / "season_cog.py", "season_review")

    assert "interaction.followup.send = original_followup" in source


def test_the_prompt_is_public_and_says_who_may_answer():
    """Public so a reviewer who cannot approve can put the question to someone who can."""
    source = _function_source(SRC / "cogs" / "season_cog.py", "_post_approval_prompt")

    assert "ephemeral=False" in source
    assert "wait=True" in source, "the message must be returned so it can be deleted"
    assert "administrator" in source
    assert "await view.bind(message)" in source


async def test_the_review_offers_only_the_approve_button():
    """The Go Back to Edit button is withdrawn (2026-09-07): it did nothing but print
    advice, and a second button on a public message is a second thing to mis-press."""
    import discord

    view, _cog = _approve_view()
    buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]

    assert len(buttons) == 1
    assert "Approve" in buttons[0].label


def test_a_league_manager_may_run_the_review():
    """Reading what a season is configured to be is not an administrative act.

    Approving it is the narrower right, and lives on the button rather than the command —
    so the review sits at the league manager tier while the button asks for more.

    Asserted through the tier the guard records rather than by reading the decorator names
    out of the source: the names have already changed once, and a test that greps for them
    reports a rename as a permission change.
    """
    from cogs.season_cog import SeasonCog
    from utils.channel_guard import LEAGUE_MANAGER, TIER_ATTRIBUTE

    assert getattr(SeasonCog.season_review.callback, TIER_ATTRIBUTE) == LEAGUE_MANAGER


def test_the_approve_command_is_withdrawn():
    """A season is approved from the review's button and from nowhere else.

    A command that could be run without a review let a manager commit a season they had
    not looked at, which is the whole thing the window above exists to prevent.
    """
    from cogs.season_cog import SeasonCog

    assert "approve" not in {c.name for c in SeasonCog.season.commands}
