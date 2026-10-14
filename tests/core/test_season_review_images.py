"""`/season placements-review` shows a manager what their league will actually see.

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
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.season_cog import _ReviewPoster

SRC = Path(__file__).resolve().parents[2] / "src"

#: Why the review poster's tests fail until #439 slice 6b's C1 is built.
NO_POSTER_REPLY = "#439: the review poster's send is not yet renamed reply"


# ── Helpers ───────────────────────────────────────────────────────────────


def _cog(bot=None):
    from leaguebot.core.cogs.season_cog import SeasonCog

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
    from leaguebot.core.cogs.season_cog import REVIEW_IMAGE_TEXT
    import leaguebot.image.services.image_lineup_post as lineup_post

    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=False))
    interaction = _interaction()

    state = await _cog()._post_review_lineup_image(_ReviewPoster(interaction), _division())

    assert state == REVIEW_IMAGE_TEXT
    interaction.followup.send.assert_not_awaited()


async def test_a_drawn_lineup_is_posted_and_the_caller_posts_no_text(monkeypatch, tmp_path):
    from leaguebot.core.cogs.season_cog import REVIEW_IMAGE_DREW
    import leaguebot.image.services.image_lineup_post as lineup_post

    png = _png(tmp_path)
    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        lineup_post,
        "render_for_command",
        AsyncMock(return_value=MagicMock(png_path=png, message=None, notices=[])),
    )
    interaction = _interaction()

    state = await _cog()._post_review_lineup_image(_ReviewPoster(interaction), _division())

    assert state == REVIEW_IMAGE_DREW
    interaction.followup.send.assert_awaited_once()
    _args, kwargs = interaction.followup.send.call_args
    assert kwargs["file"] is not None
    assert kwargs["ephemeral"] is False


async def test_a_lineup_drawn_here_obtains_missing_portraits_first(monkeypatch, tmp_path):
    """The review is judged on its drawing, so a driver seated since the last daily update
    is fetched before it is made, whichever trigger the league chose (#407)."""
    import leaguebot.image.services.image_lineup_post as lineup_post

    render = AsyncMock(return_value=_outcome(_png(tmp_path)))
    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(lineup_post, "render_for_command", render)

    await _cog()._post_review_lineup_image(_ReviewPoster(_interaction()), _division())

    assert render.await_args.kwargs["obtain_missing_portraits"] is True


async def test_a_lineup_that_would_not_draw_reports_the_fault(monkeypatch):
    """The manager is told, and the caller is told to fall back to its text."""
    from leaguebot.core.cogs.season_cog import REVIEW_IMAGE_FAULT
    import leaguebot.image.services.image_lineup_post as lineup_post

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

    state = await _cog()._post_review_lineup_image(_ReviewPoster(interaction), _division())

    assert state == REVIEW_IMAGE_FAULT
    interaction.followup.send.assert_awaited_once()
    args, kwargs = interaction.followup.send.call_args
    assert "team_1_name" in args[0]
    assert kwargs["ephemeral"] is True


# ── The calendar graphic ──────────────────────────────────────────────────


async def test_the_calendar_aspect_being_off_leaves_the_text_to_the_caller(monkeypatch):
    from leaguebot.core.cogs.season_cog import REVIEW_IMAGE_TEXT
    import leaguebot.core.services.calendar_post_service as calendar_post

    monkeypatch.setattr(
        calendar_post, "image_calendar_wanted", AsyncMock(return_value=False)
    )
    interaction = _interaction()

    state = await _cog()._post_review_calendar_image(_ReviewPoster(interaction), _division(), [], 3)

    assert state == REVIEW_IMAGE_TEXT
    interaction.followup.send.assert_not_awaited()


async def test_a_drawn_calendar_is_posted_and_the_caller_posts_no_text(
    monkeypatch, tmp_path
):
    from leaguebot.core.cogs.season_cog import REVIEW_IMAGE_DREW
    import leaguebot.core.services.calendar_post_service as calendar_post

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

    state = await _cog()._post_review_calendar_image(_ReviewPoster(interaction), _division(), [], 3)

    assert state == REVIEW_IMAGE_DREW
    interaction.followup.send.assert_awaited_once()
    _args, kwargs = interaction.followup.send.call_args
    assert kwargs["file"] is not None


async def test_a_calendar_that_would_not_draw_reports_the_fault(monkeypatch):
    from leaguebot.core.cogs.season_cog import REVIEW_IMAGE_FAULT
    import leaguebot.core.services.calendar_post_service as calendar_post

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

    state = await _cog()._post_review_calendar_image(_ReviewPoster(interaction), _division(), [], 3)

    assert state == REVIEW_IMAGE_FAULT
    args, kwargs = interaction.followup.send.call_args
    assert "round_4_race_name" in args[0]
    assert kwargs["ephemeral"] is True


# ── The calendar's command-output renderer ────────────────────────────────


def test_calendar_render_for_command_is_not_the_calendar_of_record():
    """It must post to no channel and write no message id — that is why it exists."""
    source = _function_source(
        SRC / "leaguebot" / "core" / "services" / "calendar_post_service.py", "render_for_command", code_only=True
    )
    assert "calendar_message_id" not in source
    assert "replace_calendar_message" not in source
    assert "channel" not in source


# ── The review's own structure ────────────────────────────────────────────


def test_the_review_posts_a_graphic_instead_of_its_text_never_beside_it():
    """Reverses 038 FR-027: the section's text is sent only where no graphic was drawn."""
    source = _function_source(SRC / "leaguebot" / "core" / "cogs" / "season_cog.py", "season_review")

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
    source = _function_source(SRC / "leaguebot" / "core" / "cogs" / "season_cog.py", "season_review")

    # A list of reasons rather than a flag: an undrawable graphic and a fault of the image
    # configuration — the portrait settings among them — both withhold the button, and they
    # read differently.
    assert "approval_blockers: list[str] = []" in source
    assert source.count("approval_blockers.append(") == 2, "both graphics must raise it"
    # Every fault of the image configuration, through the helper the confirmation refuses
    # on (#396) — not the portrait settings alone, which left a broken results template
    # and a missing rasteriser named as blocking and then approved.
    assert "approval_blockers += await self._image_configuration_faults()" in source

    tail = source[source.index("Server-level unsettled signups"):]
    assert "if approval_blockers:" in tail

    # Independent causes withhold the button, so the offer is guarded on every one of
    # them rather than sitting in an `else` belonging to any single one (#121, #122,
    # #181, #132). Assembled from the causes rather than pinned as a string: a new cause
    # is meant to be added here, and a test that only accepted the two it was written
    # with would read as though adding a third were the mistake.
    #
    # Matched against the source with its whitespace collapsed, because the condition is
    # now long enough to wrap and how it is wrapped is not the rule being pinned — a
    # reformat that changes nothing about which causes guard the button should not fail
    # this (#132).
    causes = (
        "approval_blockers",
        "calendar_faults_found",
        "points_faults",
        "phantom_configs",
        "no_points_attached",
        "name_problems",
        "unsettled",
        "channel_faults",
        "no_divisions",
    )
    offer = re.compile(
        r"if\s*\(?\s*"
        + r"\s+and\s+".join(re.escape(f"not {cause}") for cause in causes)
        + r"\s*\)?\s*:"
    )
    match = offer.search(tail)
    assert match is not None, (
        f"the approval prompt is not guarded on all of {', '.join(causes)}"
    )
    offer_at = match.start()
    fault_branch = tail[tail.index("if approval_blockers:"):offer_at]
    assert "_post_approval_prompt" not in fault_branch, (
        "the button must not be offered on a fault"
    )
    assert "image module is not correctly configured" in fault_branch
    assert "_post_approval_prompt" in tail[offer_at:]


def test_the_roleless_team_warning_survives_the_graphic():
    """A review finding the picture cannot show must be posted either way."""
    source = _function_source(SRC / "leaguebot" / "core" / "cogs" / "season_cog.py", "season_review")
    block = source[source.index("_post_review_lineup_image"):]
    drew, _, textual = block.partition("if lineup_state == REVIEW_IMAGE_DREW:")
    assert "role_warning" in textual.split("else:")[0]


@pytest.mark.xfail(strict=True, reason=NO_POSTER_REPLY)
def test_the_calendar_date_faults_survive_the_graphic():
    """#181: the same rule as the roleless-team warning above, for the calendar.

    A calendar graphic is drawn from the very rounds that are wrong and says nothing about
    which of them have gone by, so the finding has to be posted whichever form the calendar
    took. Posted only where there is one: a healthy division gets no extra message.
    """
    source = _function_source(SRC / "leaguebot" / "core" / "cogs" / "season_cog.py", "season_review")
    block = source[source.index("_post_review_calendar_image"):]

    assert "fault_lines = self._calendar_fault_lines(" in block
    textual, _, drew = block.partition("elif fault_lines:")
    assert "cal_lines.extend(fault_lines)" in textual, (
        "the text form must carry the faults"
    )
    assert "poster.reply(" in drew, (
        "the drawn form must post them as their own message"
    )


def test_the_approval_windows_are_read_once_for_the_whole_season():
    """Not per division. Eight divisions must not pay eight times for two configs."""
    source = _function_source(SRC / "leaguebot" / "core" / "cogs" / "season_cog.py", "season_review")

    assert source.count("await self._approval_windows(") == 1
    setup, _, loop = source.partition("for div in db_divisions:")
    assert "_approval_windows" in setup, "read before the per-division loop, not inside it"


def _outcome(png):
    return MagicMock(png_path=png, message=None, notices=[])


# ── Approval trusts the review's render, and proves the season is unchanged ──
#
# The approval used to draw every graphic a second time to prove they still drew. That is
# withdrawn (2026-09-07): the review draws them, and the button refuses unless the season
# still fingerprints as the one the review described — so the review's render is evidence
# for the approval, and repeating it was a full rasterisation per division for an answer
# already in hand.


def test_the_approval_draws_nothing_itself():
    """The render pass is gone, and must not creep back in."""
    approve = _function_source(SRC / "leaguebot" / "core" / "cogs" / "season_cog.py", "_do_approve", code_only=True)

    for drawn in ("_undrawable_graphics", "render_for_command", "render_lineup"):
        assert drawn not in approve, f"{drawn} draws at approval; the review does that"


def test_approval_refuses_before_it_commits_anything():
    """The fingerprint stands where the render stood: ahead of everything committed."""
    source = _function_source(SRC / "leaguebot" / "core" / "cogs" / "season_cog.py", "approve")

    gate_at = source.index("_changed_since_review()")
    assert source.index("_may_approve") < gate_at, (
        "who is pressing is settled before what they are pressing on"
    )
    assert gate_at < source.index("_do_approve"), (
        "the season must be proven unchanged before it is approved"
    )


def test_a_changed_season_approves_nothing():
    """The refusal returns rather than merely reporting."""
    source = _function_source(SRC / "leaguebot" / "core" / "cogs" / "season_cog.py", "approve")

    branch = source[source.index("if changed:") : source.index("await self._cog._do_approve")]
    assert "return" in branch
    assert "Nothing has been approved" in branch

    # The cheap module checks come *before* it, so a league missing a channel is not made
    # to pay for a rasterisation it was never going to keep (settled 2026-09-07).


def test_the_review_and_the_approval_read_the_same_evaluation():
    """Both must refuse on the same fault, or a manager can commit past the review."""
    review = _function_source(SRC / "leaguebot" / "core" / "cogs" / "season_cog.py", "season_review")
    approve = _function_source(SRC / "leaguebot" / "core" / "cogs" / "season_cog.py", "_do_approve")

    assert "approval_blockers" in review
    # The review still withholds its button on a graphic that will not draw. The approval
    # no longer re-draws to find that out — it refuses unless the season still fingerprints
    # as the one the review described, which is the same evidence reached more cheaply.
    assert "_changed_since_review()" in _function_source(
        SRC / "leaguebot" / "core" / "cogs" / "season_cog.py", "approve"
    )
    assert "differs_from" in _function_source(
        SRC / "leaguebot" / "core" / "cogs" / "season_cog.py", "_changed_since_review"
    )

    # The image configuration blocks on both surfaces too, and through one helper so that
    # the two cannot disagree about whether a season may be approved (#396). The review
    # reads the portrait settings through it.
    assert "_image_configuration_faults" in review
    assert "_image_configuration_faults" in approve


# ── The driver portrait settings in the review, and at approval ───────────


def _portrait_cog(**config):
    from unittest.mock import AsyncMock, MagicMock
    from types import SimpleNamespace
    from leaguebot.core.cogs.season_cog import SeasonCog

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

    lines = await cog._portrait_review_lines()

    # Stated even when off: "the bot is not obtaining portraits" is an answer, not an
    # absence of one, for a manager deciding whether the season is configured.
    assert any("Driver portraits from Discord: disabled" in line for line in lines)


async def test_the_review_names_both_update_triggers_and_the_time_in_utc():
    cog, _bot = _portrait_cog(
        use_pfp=True, pfp_prerender=True, pfp_daily=True, pfp_daily_time="07:45"
    )

    lines = await cog._portrait_review_lines()
    text = "\n".join(lines)

    assert "enabled" in text
    assert "before each render" in text
    assert "daily at 07:45 UTC" in text


async def test_the_review_marks_an_invalid_portrait_configuration():
    cog, _bot = _portrait_cog(use_pfp=True, pfp_prerender=False, pfp_daily=False)

    text = "\n".join(await cog._portrait_review_lines())

    assert "⛔" in text
    assert "neither pre-render nor daily updates" in text


async def test_the_review_section_survives_a_configuration_it_cannot_read():
    cog, bot = _portrait_cog()
    bot.image_config_service.get_config.side_effect = RuntimeError("gone")

    lines = await cog._portrait_review_lines()

    # A review must never fail because of this section.
    assert any("could not be read" in line for line in lines)


async def test_the_blocker_fires_only_on_an_invalid_configuration():
    invalid, _ = _portrait_cog(use_pfp=True, pfp_prerender=False, pfp_daily=False)
    assert await invalid._portrait_configuration_blocker() is not None

    for values in (
        {"use_pfp": False, "pfp_prerender": False, "pfp_daily": False},
        {"use_pfp": True, "pfp_prerender": True, "pfp_daily": False},
        {"use_pfp": True, "pfp_prerender": False, "pfp_daily": True},
        {"use_pfp": True, "pfp_prerender": True, "pfp_daily": True},
    ):
        cog, _ = _portrait_cog(**values)
        assert await cog._portrait_configuration_blocker() is None, values


async def test_the_blocker_stands_aside_where_the_image_module_is_disabled():
    cog, bot = _portrait_cog(use_pfp=True, pfp_prerender=False, pfp_daily=False)
    bot.module_service.is_images_enabled = AsyncMock(return_value=False)

    assert await cog._portrait_configuration_blocker() is None


async def test_the_blocker_never_blocks_a_season_it_could_not_read():
    cog, bot = _portrait_cog()
    bot.image_config_service.get_config.side_effect = RuntimeError("gone")

    assert await cog._portrait_configuration_blocker() is None


def test_the_approval_gate_returns_rather_than_merely_reporting():
    """The portrait settings refuse through the image configuration gate (#396), whose
    refusal must stop the approval rather than report and carry on, before the approval is
    asked of the change queue (#439). Driven, not read, in `test_do_approve_gates.py`."""
    source = _function_source(SRC / "leaguebot" / "core" / "cogs" / "season_cog.py", "_do_approve")

    branch = source[source.index("if image_faults:"):]
    branch = branch[: branch.index("change_queue.ask")]
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
# decorative: a league manager holding only the interaction role may run `/season placements-review`,
# and anyone who can read the channel can see the button they post.


from leaguebot.core.cogs.season_cog import _ApproveView  # noqa: E402

REVIEWER = 4242


ADMIN_ROLE = 444

#: Why a press read from the review's row fails until #439 slice 6b's C93 and C97a are built.
NO_PRESS_FROM_THE_ROW = (
    "#439: a season review's button reads its review from its view's memory (the message it was "
    "bound to, a fingerprint, a deadline) rather than from the review's row, and deletes the "
    "review itself, off the change queue"
)
#: Why the take-down's tests fail until #439 slice 6b's C92 is built.
NO_TAKE_DOWN = (
    "#439: a season review is taken down by its view and the restart's sweep at once, off the "
    "change queue"
)


@pytest.fixture(autouse=True)
def _no_approval_in_hand(monkeypatch):
    """The view asks whether an approval of its season is in hand, reading the change queue's
    tables (#439); the answer is given here: none is."""
    monkeypatch.setattr(
        "leaguebot.core.cogs.season_cog.approval_in_hand", AsyncMock(return_value=False)
    )


async def _standing_placements(tmp_path):
    """The league's queue, and Alex's placements review of season 1 standing in channel 700 (its
    report R1 and R2, then its question Q), posted a moment ago and recorded in
    `season_review_prompts`."""
    from tests.core.test_review_outcomes_recorded import _standing

    return await _standing(tmp_path, "/season placements-review")


def _view_on(env):
    """Alex's review's Approve button, built as the question's view is, on a cog whose approval
    (`_do_approve`) is recorded and goes through."""
    from tests.core.test_review_outcomes_recorded import _view_on as view_on

    return view_on(env, _ApproveView, "_do_approve")


def _press(env, user_id: int = REVIEWER, *, league_admin: bool = False):
    """A press of the button on Alex's question Q, in channel 700. *league_admin* gives the
    presser the league's admin role, which is the tier; Discord's Administrator permission is no
    route to it (#116)."""
    from tests.core.test_review_outcomes_recorded import _press_on
    from tests.support.change_queue import ADMIN_ROLE_ID

    interaction = _press_on(env, user_id, "Alex" if user_id == REVIEWER else "Sam")
    role = MagicMock()
    role.id = ADMIN_ROLE_ID
    interaction.user.roles = [role] if league_admin else []
    return interaction


async def _season_unchanged(env, monkeypatch) -> None:
    from tests.core.test_review_outcomes_recorded import _season_since_the_review

    await _season_since_the_review(env, monkeypatch)


async def _take_downs(env) -> list[dict]:
    from tests.core.test_review_outcomes_recorded import _take_downs as take_downs

    return await take_downs(env)


@pytest.mark.xfail(strict=True, reason=NO_PRESS_FROM_THE_ROW)
async def test_the_reviewer_may_approve(tmp_path, monkeypatch):
    """Alex's placements review stands in channel 700, posted a moment ago, the season unchanged
    since its report. Alex, who ran it, presses Approve: the approval is worked."""
    env = await _standing_placements(tmp_path)
    await _season_unchanged(env, monkeypatch)
    view, cog = _view_on(env)

    await _ApproveView.approve(view, _press(env), MagicMock())

    cog._do_approve.assert_awaited_once()


@pytest.mark.xfail(strict=True, reason=NO_PRESS_FROM_THE_ROW)
async def test_a_league_admin_may_approve_somebody_elses_review(tmp_path, monkeypatch):
    """Alex's placements review stands, the season unchanged. Sam, who holds the league's admin
    role but did not run the review, presses Approve: the approval is worked."""
    env = await _standing_placements(tmp_path)
    await _season_unchanged(env, monkeypatch)
    view, cog = _view_on(env)

    await _ApproveView.approve(view, _press(env, 99, league_admin=True), MagicMock())

    cog._do_approve.assert_awaited_once()


async def test_another_league_manager_may_not_approve(tmp_path):
    """The case the public message creates — only the reviewer or a league admin. Sam, a league
    manager who neither ran Alex's review nor holds the admin role, presses Approve: he is refused
    privately, told nothing has been approved, and the review is left alone: nothing deleted or
    posted, no take-down asked of the queue."""
    from tests.support.change_queue import acknowledgement

    env = await _standing_placements(tmp_path)
    view, cog = _view_on(env)
    interaction = _press(env, 99)

    await _ApproveView.approve(view, interaction, MagicMock())

    cog._do_approve.assert_not_awaited()
    assert "Nothing has been approved" in acknowledgement(interaction)
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    assert env.channel.deleted == [] and env.channel.sent == []
    assert await _take_downs(env) == []


async def test_nobody_but_the_reviewer_may_approve_while_no_admin_role_is_set(tmp_path):
    """A league with no admin role set (one that predates it, or deleted it). Sam, holding the
    role the league once used and so Discord's old way in, presses Approve on Alex's review: he
    is refused, nothing is approved, and no take-down is asked."""
    from tests.support.change_queue import acknowledgement

    env = await _standing_placements(tmp_path)
    config = await env.bot.config_service.get_server_config(7)
    config.league_admin_role_id = None
    view, cog = _view_on(env)
    interaction = _press(env, 99, league_admin=True)

    await _ApproveView.approve(view, interaction, MagicMock())

    cog._do_approve.assert_not_awaited()
    assert "Nothing has been approved" in acknowledgement(interaction)
    assert await _take_downs(env) == []


async def test_the_access_check_runs_before_the_fingerprint():
    """A press by somebody who may not approve costs no query."""
    import inspect

    source = inspect.getsource(_ApproveView.approve)

    assert source.index("_may_approve") < source.index("_changed_since_review()"), (
        "the fingerprint is taken for a member who may not approve anyway"
    )


# ── Taking the review down ────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=NO_TAKE_DOWN)
async def test_a_report_message_already_gone_does_not_stop_the_rest(tmp_path):
    """One deleted by hand must not strand the others. Alex's review stands in channel 700, but
    its report message R1 has been deleted by hand. The bot asks for the review to be taken down,
    ending confirmed, and the queue runs: R2 and the question Q are deleted all the same, R1's
    delete completing as already gone, and the queue is not stopped."""
    import discord

    from leaguebot.core.models.change import ChangeOrigin
    from tests.support.change_queue import http_error, run_queue, stopped_job

    env = await _standing_placements(tmp_path)
    channel = env.channel
    r1, r2, q = channel.r1, channel.r2, channel.q
    r1.delete = AsyncMock(side_effect=http_error(discord.NotFound, status=404, text="Unknown Message"))
    await env.bot.change_queue.ask(
        "season.review.take_down",
        {"channel_id": 700, "question_id": q.id, "report_ids": [r1.id, r2.id],
         "reviewer_id": REVIEWER, "review": "/season placements-review", "ending": "confirmed"},
        what="taking the placements review down", origin=ChangeOrigin.BOT,
    )

    await run_queue(env.bot)

    r2.delete.assert_awaited_once()
    q.delete.assert_awaited_once()
    assert await stopped_job(env.db_path) is None


# ── A press refused, or cancelled, leaves the review standing (#439) ───────
#
# The core specification's "Confirming placements": a press refused at the press by the
# approval's own gates, or cancelled at the test-mode backup question, leaves the review and its
# button standing, to be pressed again within its five minutes, as a press that fails on a fault
# does. A press whose approval goes through asks the queue to take the review down, ahead of the
# approval's work, whatever then becomes of it (core :288). These drive the real `_do_approve`
# behind the button, so that what the helper tells the view is what is tested, not what a double
# says. The review is read from its row: its question Q (999) posted a moment ago in channel 111,
# its report R (900–902), the evidence its report was computed from matching the season now.

GATED_QUESTION = 999
GATED_REPORT = (900, 901, 902)


async def _gated_view(tmp_path, monkeypatch, *, round_in_days: float = 30.0,
                      test_mode: bool = False):
    """A posted review whose button reaches the real `_do_approve`, its prompt recorded in a
    migrated database. Every gate answers "nothing wrong" but the date of its one round, which
    *round_in_days* places, and every module is off. The queue's ask answers with a change's id."""
    import json
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from leaguebot.core.cogs.season_cog import SeasonCog
    from leaguebot.core.db.database import get_connection, run_migrations
    from leaguebot.core.models.round import Round, RoundFormat
    from leaguebot.core.models.season import SeasonStage
    from leaguebot.core.models.server_config import ServerConfig
    from leaguebot.core.services import season_fingerprint_service as sfs
    from tests.support.review_prompts import store_review_prompt

    db_path = str(tmp_path / "review.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await store_review_prompt(
            db, season_id=1, channel_id=111, message_id=GATED_QUESTION, reviewer_id=REVIEWER,
            posted_at=datetime.now(timezone.utc).isoformat(),
            report_message_ids=GATED_REPORT,
        )
        await db.execute("UPDATE season_review_prompts SET evidence = ?",
                         (json.dumps({"season": "as reviewed"}),))
        await db.commit()

    async def _now(*_args, **_kwargs):
        return sfs.SeasonFingerprint({"season": "as reviewed"})

    monkeypatch.setattr(sfs, "take_fingerprint", _now)

    cog = SeasonCog.__new__(SeasonCog)
    bot = cog.bot = MagicMock()
    bot.db_path = db_path
    bot.change_queue.ask = AsyncMock(return_value=1)
    bot.output_router.post_log = AsyncMock()
    bot.config_service.get_server_config = AsyncMock(
        return_value=ServerConfig(
            server_id=7,
            interaction_role_id=222,
            league_admin_role_id=ADMIN_ROLE,
            interaction_channel_id=111,
            log_channel_id=333,
            test_mode_active=test_mode,
        )
    )
    cog._pending = {
        REVIEWER: SimpleNamespace(server_id=7, season_id=1, season_number=1, divisions=[])
    }
    bot.season_service.get_stage = AsyncMock(return_value=SeasonStage.PLACEMENTS)
    cog._season_has_divisions = AsyncMock(return_value=True)
    cog._placement_confirmation_faults = AsyncMock(return_value=([], []))
    bot.season_service.validate_division_tiers = AsyncMock()
    bot.season_service.get_divisions = AsyncMock(
        return_value=[SimpleNamespace(id=1, name="Premier", tier=1)]
    )
    bot.season_service.get_division_rounds = AsyncMock(
        return_value=[
            Round(
                id=1,
                division_id=1,
                round_number=1,
                format=RoundFormat.NORMAL,
                track_name="Silverstone",
                scheduled_at=datetime.now(timezone.utc) + timedelta(days=round_in_days),
            )
        ]
    )
    for module in ("weather", "attendance", "results", "signup", "images"):
        setattr(bot.module_service, f"is_{module}_enabled", AsyncMock(return_value=False))

    async def _no_windows():
        return None, None

    bot.approval_windows = _no_windows
    cog._team_name_problems = AsyncMock(return_value=[])
    cog._lineup_problems = AsyncMock(return_value=[])

    question = MagicMock(id=GATED_QUESTION, delete=AsyncMock())
    question.channel.id = 111
    question.channel.send = AsyncMock()
    return _ApproveView(cog, REVIEWER), cog, question


def _gated_member():
    import discord

    member = MagicMock(spec=discord.Member)
    member.id = REVIEWER
    member.display_name = "Alex"
    member.roles = []
    return member


def _gated_press(question):
    """The reviewer's press on *question*, its refusal unrecorded: the log line is not this
    section's."""
    interaction = MagicMock()
    interaction.guild_id = 7
    interaction.user = _gated_member()
    interaction.client = None
    interaction.channel_id = 111
    interaction.message = question
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _said(interaction) -> str:
    calls = [
        *interaction.response.send_message.await_args_list,
        *interaction.followup.send.await_args_list,
    ]
    return "\n".join(str(call.args[0]) for call in calls if call.args)


def _asked(cog, kind: str) -> list[dict]:
    """The payload of each change of *kind* the press asked of the queue, in order."""
    return [call.args[1] for call in cog.bot.change_queue.ask.await_args_list
            if call.args and call.args[0] == kind]


async def _prompt_rows(cog) -> int:
    from leaguebot.core.db.database import get_connection

    async with get_connection(cog.bot.db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM season_review_prompts")
        (count,) = await cursor.fetchone()
    return count


def _assert_standing(cog, question) -> None:
    """The question undeleted, nothing posted beside it, and no take-down asked of the queue."""
    question.delete.assert_not_awaited()
    question.channel.send.assert_not_awaited()
    assert _asked(cog, "season.review.take_down") == []


@pytest.mark.xfail(strict=True, reason=NO_PRESS_FROM_THE_ROW)
async def test_a_press_refused_by_a_gate_leaves_the_review_standing(tmp_path, monkeypatch):
    """The review's one round is dated 90 days ago, so Alex's Approve is refused at the press,
    told its round's date has already gone by. Nothing is asked of the queue, the review is left
    standing with its row, and pressed again within its five minutes the press is judged afresh
    and refused the same way."""
    view, cog, question = await _gated_view(tmp_path, monkeypatch, round_in_days=-90)
    interaction = _gated_press(question)

    await _ApproveView.approve(view, interaction, MagicMock())

    assert "dates that have already gone by" in _said(interaction)
    cog.bot.change_queue.ask.assert_not_awaited()
    _assert_standing(cog, question)
    assert await _prompt_rows(cog) == 1

    again = _gated_press(question)
    await _ApproveView.approve(view, again, MagicMock())

    assert "dates that have already gone by" in _said(again)
    cog.bot.change_queue.ask.assert_not_awaited()
    _assert_standing(cog, question)
    assert await _prompt_rows(cog) == 1


@pytest.mark.xfail(strict=True, reason=NO_PRESS_FROM_THE_ROW)
async def test_a_press_cancelled_at_the_backup_question_leaves_the_review_standing(
    tmp_path, monkeypatch
):
    """Under test mode Alex presses Approve and cancels at the backup question: he is told
    nothing has been approved or saved, nothing is asked of the queue, and the review stands with
    its row, to be pressed again."""
    from leaguebot.core.cogs.season_cog import _BackupBeforeApprovalView

    async def _cancelled(self):
        self.answer = "cancel"
        return True

    monkeypatch.setattr(_BackupBeforeApprovalView, "wait", _cancelled)
    view, cog, question = await _gated_view(tmp_path, monkeypatch, test_mode=True)
    interaction = _gated_press(question)

    await _ApproveView.approve(view, interaction, MagicMock())

    assert "Nothing has been approved, and nothing has been saved." in _said(interaction)
    cog.bot.change_queue.ask.assert_not_awaited()
    _assert_standing(cog, question)
    assert await _prompt_rows(cog) == 1


@pytest.mark.xfail(strict=True, reason=NO_PRESS_FROM_THE_ROW)
async def test_a_review_lapsed_at_the_backup_question_is_still_taken_down(
    tmp_path, monkeypatch
):
    """Under test mode Alex presses Approve and leaves the backup question unanswered until the
    review's five minutes, counted from its question's posting, are up. He is told the review
    expired while the backup question went unanswered; no approval is asked; and the review's
    take-down is asked of the queue once, ending expired and naming Q and its report, the button
    deleting and posting nothing itself."""
    from datetime import datetime, timedelta, timezone

    from leaguebot.core.cogs.season_cog import _BackupBeforeApprovalView
    from leaguebot.core.db.database import get_connection

    holder = {}

    async def _unanswered(self):
        async with get_connection(holder["db_path"]) as db:
            await db.execute(
                "UPDATE season_review_prompts SET posted_at = ?",
                ((datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),),
            )
            await db.commit()
        self.answer = None
        return True

    monkeypatch.setattr(_BackupBeforeApprovalView, "wait", _unanswered)
    view, cog, question = await _gated_view(tmp_path, monkeypatch, test_mode=True)
    holder["db_path"] = cog.bot.db_path
    interaction = _gated_press(question)

    await _ApproveView.approve(view, interaction, MagicMock())

    assert "expired while the backup question went unanswered" in _said(interaction)
    assert _asked(cog, "season.approve") == []
    [payload] = _asked(cog, "season.review.take_down")
    assert (payload["ending"], payload["question_id"]) == ("expired", GATED_QUESTION)
    assert sorted(payload["report_ids"]) == list(GATED_REPORT)
    question.delete.assert_not_awaited()
    question.channel.send.assert_not_awaited()


@pytest.mark.xfail(strict=True, reason=NO_PRESS_FROM_THE_ROW)
async def test_a_press_the_queue_refuses_still_takes_the_review_down(tmp_path, monkeypatch):
    """Every gate at the press passes, so Alex's approval goes through the button and the review's
    take-down is asked of the queue first, ending confirmed and naming Q and its report. The
    queue's own check of the approval then refuses it (a date gone by while the press was worked,
    say): nothing is saved, but the review is taken down all the same, whatever became of the
    approval (core :288); the button deletes and posts nothing itself."""
    view, cog, question = await _gated_view(tmp_path, monkeypatch)

    async def _ask(kind, _payload, **_kwargs):
        return None if kind == "season.approve" else 1

    cog.bot.change_queue.ask = AsyncMock(side_effect=_ask)

    await _ApproveView.approve(view, _gated_press(question), MagicMock())

    kinds = [call.args[0] for call in cog.bot.change_queue.ask.await_args_list]
    assert kinds == ["season.review.take_down", "season.approve"]
    [payload] = _asked(cog, "season.review.take_down")
    assert (payload["ending"], payload["question_id"]) == ("confirmed", GATED_QUESTION)
    assert sorted(payload["report_ids"]) == list(GATED_REPORT)
    question.delete.assert_not_awaited()
    question.channel.send.assert_not_awaited()


@pytest.mark.xfail(strict=True, reason=NO_PRESS_FROM_THE_ROW)
async def test_a_press_asked_of_the_queue_takes_the_review_down(tmp_path, monkeypatch):
    """The one press that ends the review: every gate passed, Alex's approval is asked of the
    change queue, and the review's take-down is asked before it, ending confirmed and naming Q
    and its report R. The button deletes and posts nothing itself: the queue takes the review
    down."""
    view, cog, question = await _gated_view(tmp_path, monkeypatch)

    await _ApproveView.approve(view, _gated_press(question), MagicMock())

    kinds = [call.args[0] for call in cog.bot.change_queue.ask.await_args_list]
    assert kinds == ["season.review.take_down", "season.approve"]
    [payload] = _asked(cog, "season.review.take_down")
    assert (payload["ending"], payload["question_id"]) == ("confirmed", GATED_QUESTION)
    assert sorted(payload["report_ids"]) == list(GATED_REPORT)
    question.delete.assert_not_awaited()
    question.channel.send.assert_not_awaited()


# ── The question the button is attached to ────────────────────────────────


# ── Collecting the report, so approving can clear it ──────────────────────


@pytest.mark.xfail(strict=True, reason=NO_POSTER_REPLY)
async def test_the_poster_keeps_every_public_message():
    """Every public message a review sends is kept, so that approving it can take them down."""
    interaction = MagicMock()
    sent = [MagicMock(), MagicMock()]
    interaction.followup.send = AsyncMock(side_effect=sent)
    poster = _ReviewPoster(interaction)

    await poster.reply("first", ephemeral=False)
    await poster.reply("second", ephemeral=False)

    assert poster.posted == sent


@pytest.mark.xfail(strict=True, reason=NO_POSTER_REPLY)
async def test_the_poster_asks_for_the_message_back():
    """`followup.send` returns None unless `wait=True`, so without it nothing is kept and the
    report could never be cleared."""
    interaction = MagicMock()
    original = AsyncMock(return_value=MagicMock())
    interaction.followup.send = original

    await _ReviewPoster(interaction).reply("body", ephemeral=False)

    assert original.await_args.kwargs["wait"] is True


@pytest.mark.xfail(strict=True, reason=NO_POSTER_REPLY)
async def test_the_poster_leaves_ephemeral_messages_alone():
    """An ephemeral followup is the reviewer's alone and cannot be deleted by id; the
    fault reports among them are how a manager knows what to fix."""
    interaction = MagicMock()
    original = AsyncMock(return_value=MagicMock())
    interaction.followup.send = original
    poster = _ReviewPoster(interaction)

    assert await poster.reply("a fault", ephemeral=True) is None

    assert poster.posted == []
    assert "wait" not in original.await_args.kwargs


@pytest.mark.xfail(strict=True, reason=NO_POSTER_REPLY)
async def test_the_poster_passes_a_file_and_a_view_on():
    interaction = MagicMock()
    original = AsyncMock(return_value=MagicMock())
    interaction.followup.send = original
    attachment, view = MagicMock(), MagicMock()

    await _ReviewPoster(interaction).reply("body", ephemeral=False, file=attachment, view=view)

    assert original.await_args.kwargs["file"] is attachment
    assert original.await_args.kwargs["view"] is view


def test_nothing_in_a_review_sends_around_the_poster():
    """A message sent past the poster is not kept, and approving the review would leave it
    standing. Each review, and each helper it posts through, sends through the poster alone."""
    tree = ast.parse((SRC / "leaguebot" / "core" / "cogs" / "season_cog.py").read_text(encoding="utf-8"))
    reviews = {
        "season_review",
        "_review_mid_season_placements",
        "season_config_review",
        "_post_review_lineup_image",
        "_post_review_calendar_image",
        "_post_approval_prompt",
        "_send_channel_faults",
    }
    found = {
        node.name: sorted(
            call.lineno
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and ast.unparse(call.func).endswith("followup.send")
        )
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in reviews
    }
    assert set(found) == reviews, "a review or helper was renamed — name it here"
    assert {name: lines for name, lines in found.items() if lines} == {}


@pytest.mark.xfail(strict=True, reason=NO_POSTER_REPLY)
def test_the_prompt_is_public_and_says_who_may_answer():
    """Public so a reviewer who cannot approve can put the question to someone who can."""
    source = _function_source(SRC / "leaguebot" / "core" / "cogs" / "season_cog.py", "_post_approval_prompt")

    assert "ephemeral=False" in source
    # A public send through the poster waits for the message and hands it back, so the
    # prompt can be bound and later deleted; `test_the_poster_asks_for_the_message_back`.
    assert "message = await poster.reply(" in source
    assert "administrator" in source
    assert "await view.bind(message)" in source


async def test_the_review_offers_only_the_approve_button():
    """The Go Back to Edit button is withdrawn (2026-09-07): it did nothing but print
    advice, and a second button on a public message is a second thing to mis-press."""
    import discord

    view = _ApproveView(MagicMock(), REVIEWER)
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
    from leaguebot.core.cogs.season_cog import SeasonCog
    from leaguebot.core.utils.channel_guard import LEAGUE_MANAGER, TIER_ATTRIBUTE

    assert getattr(SeasonCog.season_review.callback, TIER_ATTRIBUTE) == LEAGUE_MANAGER


def test_the_approve_command_is_withdrawn():
    """A season is approved from the review's button and from nowhere else.

    A command that could be run without a review let a manager commit a season they had
    not looked at, which is the whole thing the window above exists to prevent.
    """
    from leaguebot.core.cogs.season_cog import SeasonCog

    assert "approve" not in {c.name for c in SeasonCog.season.commands}
