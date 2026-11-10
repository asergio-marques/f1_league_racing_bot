"""`/season config-review` and the confirmation of a season's configuration (issue #220).

A season begins in Configuration. The configuration review checks everything that can be
checked before the season has divisions, and ends with a button that, once pressed, fixes the
configuration for the season and moves it on: to Waiting where the signup module is enabled,
to Placements where it is not or the season runs in test mode.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.models.server_config import ServerConfig
from tests.support.review_workflows import (
    record_review_views, run_check, run_workflow, sign_up_confirmation_refused,
)
from tests.support.season_league import reply as _told
from tests.support.setup_reads import hold_setup

from tests.support.undecorate import undecorate

SERVER_ID = 7
SEASON_ID = 31
REVIEWER = 4242
ADMIN_ROLE = 444

#: The gates a test stands in, each a function of `approval_checks` that the review command and
#: the confirmation's workflow call alike.
_GATES = (
    "configuration_faults", "team_name_problems", "image_configuration_faults",
)

#: Results' points gates a test stands in, each a function of results' `season_points_service`
#: that its review section and its refusals call alike (#283).
_POINTS_GATES = (
    "missing_points_config_problems", "no_points_config_attached", "points_ordering_problems",
)


@contextmanager
def _gates_of(cog):
    """Each gate *cog* stands in for, patched on `approval_checks`, or on results' service for a
    points gate, for as long as it is held."""
    from leaguebot.core.services import approval_checks
    from leaguebot.results.services import season_points_service

    with ExitStack() as stack:
        for module, gates in ((approval_checks, _GATES), (season_points_service, _POINTS_GATES)):
            for gate in gates:
                if gate in vars(cog):
                    stack.enter_context(patch.object(module, gate, vars(cog)[gate]))
        yield


async def _faults(cog, guild=None) -> list[str]:
    """Every fault that stops season 31's configuration being confirmed, judged upon *guild*:
    the gate the review and the confirmation share, handed *cog*'s bot's services."""
    with _gates_of(cog):
        return await run_check("configuration_faults", cog.bot, SEASON_ID, guild)


async def _image_faults(cog) -> list[str]:
    """The image module's faults that need no division, read through *cog*'s bot's drawing."""
    from leaguebot.core.services import approval_checks

    return await approval_checks.image_configuration_faults(cog.bot.image_drawing)


async def _confirm(cog, interaction) -> bool:
    """Press ✅ Confirm configuration: the confirmation's workflow, handed *cog*'s bot's
    services, the gates *cog* stands in for on `approval_checks`."""
    with _gates_of(cog):
        return await run_workflow("confirm_configuration", cog.bot, interaction)


def _server_config(*, test_mode: bool = False, base=2, driver=3) -> ServerConfig:
    """The league's two roles are core's, on the server configuration (issue #276)."""
    config = ServerConfig(
        server_id=SERVER_ID,
        interaction_role_id=222,
        league_admin_role_id=ADMIN_ROLE,
        interaction_channel_id=111,
        log_channel_id=333,
        base_role_id=base,
        driver_role_id=driver,
    )
    config.test_mode_active = test_mode
    return config


def _signup_config(*, channel=1):
    return SimpleNamespace(signup_channel_id=channel)


def _bot(
    *,
    signup=False,
    results=False,
    images=False,
    signup_config=None,
    test_mode=False,
    stage=SeasonStage.CONFIGURATION,
    roles=(2, 3),
    db_path: str = ":memory:",
) -> MagicMock:
    """The bot, its services answering as told. A press of ✅ Confirm configuration asks
    the change queue for the confirmation, so a test of it gives *db_path*, a database built
    by the migrations, which the press may read; `bot.change_queue` records what is asked."""
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_signup_enabled = AsyncMock(return_value=signup)
    bot.module_service.is_results_enabled = AsyncMock(return_value=results)
    bot.module_service.is_images_enabled = AsyncMock(return_value=images)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    bot.module_service.is_weather_enabled = AsyncMock(return_value=False)
    bot.signup_module_service.get_config = AsyncMock(
        return_value=signup_config if signup_config is not None else _signup_config()
    )
    bot.config_service.get_server_config = AsyncMock(
        return_value=_server_config(test_mode=test_mode, base=roles[0], driver=roles[1])
    )
    bot.season_service.get_stage = AsyncMock(return_value=stage)
    bot.output_router.post_log = AsyncMock()
    # Results' refusals, through the hook a season's confirmation is refused through (#283).
    sign_up_confirmation_refused(bot)
    return bot


def _cog(bot: MagicMock) -> SeasonCog:
    cog = SeasonCog(bot)
    # The season being set up, read from the database (#283).
    hold_setup(bot.season_service, season_id=SEASON_ID, season_number=4, game_edition=25)
    # The team names are checked by a helper of their own, pinned elsewhere.
    cog.team_name_problems = AsyncMock(return_value=[])
    return cog


async def _migrated(tmp_path) -> str:
    """A database built by the migrations, empty, for a press to read."""
    from leaguebot.core.db.database import run_migrations

    db_path = str(tmp_path / "league.db")
    await run_migrations(db_path)
    return db_path


def _grantable_role(*, above_the_bot: bool = False) -> MagicMock:
    role = MagicMock(managed=False)
    role.is_default.return_value = False
    role.guild.me.guild_permissions.manage_roles = True
    role.guild.me.top_role.__gt__ = lambda _self, _other: not above_the_bot
    return role


def _guild(*, gone: tuple[int, ...] = (), driver_above_the_bot: bool = False) -> MagicMock:
    """The server, holding the league's two roles (2 and 3) unless told one is *gone*."""
    roles = {2: _grantable_role(), 3: _grantable_role(above_the_bot=driver_above_the_bot)}
    for role_id in gone:
        roles.pop(role_id)
    guild = MagicMock()
    guild.get_role = MagicMock(side_effect=roles.get)
    return guild


def _interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild = _guild()
    interaction.guild_id = SERVER_ID
    interaction.user.id = REVIEWER
    interaction.user.display_name = "Manager"
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


# ── What the configuration review checks ────────────────────────────────────────────


async def test_the_signup_module_needs_its_channel_and_both_roles():
    """Each fault names the command that sets it: the roles are the league's, set under
    `/bot`, not under `/signup` (issue #276)."""
    bot = _bot(signup=True, signup_config=_signup_config(channel=None), roles=(None, None))
    cog = _cog(bot)

    faults = await _faults(cog)

    assert len(faults) == 3
    assert any("signup channel" in f for f in faults)
    assert any("base role" in f and "/bot base-role" in f for f in faults)
    assert any("driver role" in f and "/bot driver-role" in f for f in faults)


@pytest.mark.parametrize("channel, roles, gone", [
    pytest.param(1, (2, 3), None, id="a plain configuration"),
    pytest.param(None, (None, None), None, id="its channel and roles unset"),
    pytest.param(1, (2, 3), (2, 3), id="its roles gone from the server"),
])
async def test_a_disabled_signup_module_is_not_checked(channel, roles, gone):
    """Nor are the league's roles, whether unset or no longer upon the server: only the signup
    module needs them. A plain configuration has no faults."""
    bot = _bot(signup=False, signup_config=_signup_config(channel=channel), roles=roles)
    guild = _guild(gone=gone) if gone is not None else None

    assert await _faults(_cog(bot), guild) == []


@pytest.mark.parametrize(
    "gone,expected",
    [(2, "**base role** is no longer on the server"), (3, "**driver role** is no longer on the server")],
)
async def test_a_league_role_gone_from_the_server_is_a_fault(gone, expected):
    """Confirming fixes both roles, so this is the last moment a league can choose another
    freely (#374). Stored is not enough."""
    cog = _cog(_bot(signup=True))

    (fault,) = await _faults(cog, _guild(gone=(gone,)))

    assert expected in fault


async def test_a_driver_role_the_bot_cannot_grant_is_a_fault():
    cog = _cog(_bot(signup=True))

    (fault,) = await _faults(cog, _guild(driver_above_the_bot=True))

    assert "Move my role above it" in fault


async def test_confirming_refuses_a_driver_role_gone_from_the_server(tmp_path):
    """The signup module is on, and the league's driver role (3) has been deleted from the server
    since the review. ✅ Confirm configuration is refused at the press, naming the driver role,
    and nothing is asked of the change queue."""
    bot = _bot(signup=True, db_path=await _migrated(tmp_path))
    cog = _cog(bot)
    interaction = _interaction()
    interaction.guild = _guild(gone=(3,))

    await _confirm(cog, interaction)

    assert "**driver role** is no longer on the server" in _told(interaction)
    assert "Nothing has been confirmed" in _told(interaction)
    assert bot.change_queue.method_calls == []


async def test_team_names_of_the_server_list_are_checked():
    cog = _cog(_bot())
    cog.team_name_problems = AsyncMock(return_value=["**!!!** reduces to nothing"])

    faults = await _faults(cog)

    cog.team_name_problems.assert_awaited_once_with(cog.bot.team_service, None)
    assert faults == ["Team name: **!!!** reduces to nothing"]


async def test_the_results_module_needs_a_points_configuration(tmp_path):
    from leaguebot.core.db.database import run_migrations

    db_path = str(tmp_path / "config_review.db")
    await run_migrations(db_path)
    bot = _bot(results=True, images=True)
    bot.db_path = db_path
    cog = _cog(bot)
    cog.team_name_problems = AsyncMock(return_value=["'Red/Blue' contains a character"])
    cog.missing_points_config_problems = AsyncMock(return_value=["Ghost"])
    cog.points_ordering_problems = AsyncMock(return_value=["Standard: 2nd above 1st"])
    cog.image_configuration_faults = AsyncMock(return_value=["Inkscape is not installed."])

    faults = await _faults(cog)

    # Each line whole, and in its place between the team names' and the image module's (#283:
    # results' lines move into results with no word changed).
    assert faults == [
        "Team name: 'Red/Blue' contains a character",
        "No points configuration is attached to this season \u2014 `/results config append`.",
        "The attached points configuration **Ghost** does not exist \u2014 build it with "
        "`/results config add`, or drop it with `/results config detach`.",
        "Points table out of order: Standard: 2nd above 1st",
        "Inkscape is not installed.",
    ]


async def test_the_image_module_faults_are_included():
    bot = _bot(images=True)
    cog = _cog(bot)
    cog.image_configuration_faults = AsyncMock(return_value=["Inkscape is not installed."])

    assert await _faults(cog) == [
        "Inkscape is not installed."
    ]


# ── The command ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("stage", [SeasonStage.WAITING, SeasonStage.PLACEMENTS, None])
async def test_the_review_is_refused_outside_configuration(stage):
    bot = _bot(stage=stage)
    cog = _cog(bot)
    interaction = _interaction()
    interaction.response.is_done = MagicMock(return_value=False)

    await undecorate(SeasonCog.season_config_review)(cog, interaction)

    reply = interaction.response.send_message.await_args.args[0]
    assert "no season in configuration" in reply
    interaction.response.defer.assert_not_awaited()


# ── Confirming ─────────────────────────────────────────────────────────────────────


async def test_confirming_refuses_on_a_fault_found_afresh(tmp_path):
    """The signup module is on and its signup channel has been cleared since the review.
    ✅ Confirm configuration is refused at the press, and nothing is asked of the change queue."""
    bot = _bot(signup=True, signup_config=_signup_config(channel=None),
               db_path=await _migrated(tmp_path))
    cog = _cog(bot)
    interaction = _interaction()

    await _confirm(cog, interaction)

    assert "Nothing has been confirmed" in _told(interaction)
    assert bot.change_queue.method_calls == []


# ── The button ─────────────────────────────────────────────────────────────────────
#
# The button reads its review from the review's row, not from its view's memory: the question's
# `posted_at` for its five minutes and the `evidence` its report was computed from for whether the
# season has changed (#439, slice 6b: C93, C97a). It deletes and posts nothing itself: a review it
# ends is taken down by the queue (C92, C97). These build the league's queue with Alex's
# configuration review of season 1 standing in channel 700 (report R1 and R2, question Q), as the
# press tests of every review's button do.

CONFIG_REVIEW = "/season config-review"
#: The area of the season these tests change, as the review's evidence names it.
TEAM_LIST = "the team list"


async def _standing_review(tmp_path):
    """The league's queue, and Alex's configuration review of season 1 standing in channel 700,
    posted a moment ago."""
    from tests.core.test_review_outcomes_recorded import _standing

    return await _standing(tmp_path, CONFIG_REVIEW)


async def _team_list_since_the_review(env, monkeypatch, now: str = "as reviewed") -> None:
    """Every standing review's row records the evidence its report was computed from, the team
    list "as reviewed"; the season as it stands now has the team list as *now*."""
    import json

    from leaguebot.core.db.database import get_connection
    from leaguebot.core.services import season_fingerprint_service as sfs

    async with get_connection(env.db_path) as db:
        await db.execute("UPDATE season_review_prompts SET evidence = ?",
                         (json.dumps({TEAM_LIST: "as reviewed"}),))
        await db.commit()

    async def _now(*_args, **_kwargs):
        return sfs.SeasonFingerprint({TEAM_LIST: now})

    monkeypatch.setattr(sfs, "take_fingerprint", _now)


#: The workflow of `season_review_workflows` the ✅ Confirm configuration button hands a press on to.
CONFIRM = "confirm_configuration"


def _confirm_view(env, monkeypatch, *, goes_through: bool = True):
    """Alex's review's ✅ Confirm configuration, the view the builder's factory makes for the
    question, whose confirmation, `season_review_workflows.confirm_configuration`, is stood in and
    recorded, and goes through or is refused at its own check. Returns the view and the
    stand-ins, the confirmation's under `CONFIRM`."""
    from tests.core.test_review_outcomes_recorded import _view_on

    view, workflows = _view_on(env, "configuration", CONFIRM, monkeypatch)
    getattr(workflows, CONFIRM).return_value = goes_through
    return view, workflows


def asking_the_take_down(env, workflows) -> None:
    """Make the stood-in confirmation ask the review's take-down, ending confirmed, as the real
    one does once its gates pass."""
    from tests.core.test_review_outcomes_recorded import asking_the_take_down as asking

    asking(env, workflows, CONFIRM)


def _press(env, user_id: int = REVIEWER, name: str = "Alex", *, question=None):
    """A press of the button on *question* (Alex's Q unless given), in channel 700, by *name*."""
    from tests.core.test_review_outcomes_recorded import _press_on

    interaction = _press_on(env, user_id, name)
    if question is not None:
        interaction.message = question
    return interaction


async def _take_downs(env) -> list[dict]:
    from tests.core.test_review_outcomes_recorded import _take_downs as take_downs

    return await take_downs(env)


def _lines(env, mark: str) -> list[str]:
    from tests.core.test_review_outcomes_recorded import _lines as lines

    return lines(env, mark)


async def _prompt_rows(env) -> int:
    from leaguebot.core.db.database import get_connection

    async with get_connection(env.db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM season_review_prompts")
        (count,) = await cursor.fetchone()
    return count


async def test_a_refused_confirmation_leaves_the_review_standing(tmp_path, monkeypatch):
    """Every review's button alike: Alex presses ✅ Confirm configuration a moment after the
    question appears, the season unchanged, and the confirmation is refused at its own check (a
    fault found afresh). The review and its button stand, to be pressed again within its five
    minutes: nothing is deleted or posted, no take-down is asked of the queue, its row is kept
    and its expiry timer is neither re-armed nor disarmed."""
    from tests.core.test_review_outcomes_recorded import _expiry_timer_touched

    env = await _standing_review(tmp_path)
    await _team_list_since_the_review(env, monkeypatch)
    view, workflows = _confirm_view(env, monkeypatch, goes_through=False)

    await type(view).approve(view, _press(env), MagicMock())

    getattr(workflows, CONFIRM).assert_awaited_once()
    assert env.channel.deleted == [] and env.channel.sent == []
    assert await _take_downs(env) == []
    assert await _prompt_rows(env) == 1
    assert _expiry_timer_touched(env) == []


async def test_a_confirmation_that_goes_through_takes_the_review_down(tmp_path, monkeypatch):
    """The one press that ends the review: Admin (77) presses ✅ Confirm configuration on season
    3's review in channel 610, the season unchanged since its report and nothing at fault. Before
    the queue runs, the review's take-down has been asked of it ahead of the confirmation, ending
    confirmed and naming the question and the report, and nothing has been deleted. Once the queue
    runs, the question and the report are deleted, the review's row is forgotten, nothing more is
    posted in channel 610, and no lapse line is written."""
    import json

    from leaguebot.core.services.season_review_changes import TAKE_DOWN
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.season_league import (
        CONFIGURATION_CONFIRM_KIND,
        REVIEW_CHANNEL,
        configuring_league,
        post_config_review,
        press_approve,
    )

    league = await configuring_league(tmp_path, monkeypatch=monkeypatch)
    review = await post_config_review(league)

    await press_approve(league, review)

    assert not league.errors, league.errors
    rows = await change_rows(league.db_path)
    assert [row["kind"] for row in rows] == [TAKE_DOWN, CONFIGURATION_CONFIRM_KIND]
    payload = rows[0]["payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert (payload["ending"], payload["question_id"]) == ("confirmed", review.prompt.id)
    assert sorted(payload["report_ids"]) == sorted(message.id for message in review.report)
    assert [event for event in league.events if event[0] == "delete"] == []

    await run_queue(league.bot)

    deleted = {event[2] for event in league.events if event[:2] == ("delete", REVIEW_CHANNEL)}
    assert deleted == {review.prompt.id, *(message.id for message in review.report)}
    assert await league.rows("SELECT * FROM season_review_prompts") == []
    sent = [event for event in league.events if event[:2] == ("send", REVIEW_CHANNEL)]
    assert len(sent) == 1 + len(review.report)
    assert [line for line in league.bot.log_channel.sent if line.startswith("⌛")] == []


async def test_confirming_one_review_ends_another_of_the_same_season_when_pressed(
    tmp_path, monkeypatch
):
    """Two configuration reviews of season 1 stand in channel 700: Alex's older one (R1, R2, Q)
    and a newer question Q2, both as the season stood. Alex confirms the newer, and the queue
    takes it down. The season has moved on since, so when Alex then presses Confirm on the older
    he is refused, privately, and nothing is confirmed; the older ends as an expired one does.
    Once the queue runs, Q, R1 and R2 are deleted and the expiry notice pinging Alex posted,
    once; the log holds one refusal line for the press and no lapse line."""
    from datetime import datetime, timezone

    from leaguebot.core.db.database import get_connection
    from tests.support.change_queue import acknowledgement, post_in, run_queue
    from tests.support.review_prompts import store_review_prompt

    env = await _standing_review(tmp_path)
    channel = env.channel
    newer = post_in(channel, env.bot.user)
    async with get_connection(env.db_path) as db:
        await store_review_prompt(
            db, season_id=1, channel_id=channel.id, message_id=newer.id, reviewer_id=REVIEWER,
            review=CONFIG_REVIEW, posted_at=datetime.now(timezone.utc).isoformat(),
        )
        await db.commit()
    await _team_list_since_the_review(env, monkeypatch)
    view, workflows = _confirm_view(env, monkeypatch)
    asking_the_take_down(env, workflows)
    await type(view).approve(view, _press(env, question=newer), MagicMock())
    await run_queue(env.bot)
    assert [m.id for m in channel.deleted] == [newer.id]
    assert channel.sent == []
    await _team_list_since_the_review(env, monkeypatch, now="moved on")
    older_view, older_workflows = _confirm_view(env, monkeypatch)
    pressing = _press(env)

    await type(older_view).approve(older_view, pressing, MagicMock())

    getattr(older_workflows, CONFIRM).assert_not_awaited()
    assert "Nothing has been confirmed" in acknowledgement(pressing)
    await run_queue(env.bot)
    assert {m.id for m in channel.deleted} == {newer.id, channel.q.id, channel.r1.id,
                                               channel.r2.id}
    [notice] = channel.sent
    assert "<@4242> your review has expired" in notice
    assert len(_lines(env, "⛔ ")) == 1, _lines(env, "")
    assert _lines(env, "⌛ ") == []
    assert await _prompt_rows(env) == 0


# ── The report ─────────────────────────────────────────────────────────────────────
#
# The command computes the report and the evidence it is computed from, and asks the queue to post
# the review, `season.review.post`, with the manager's interaction (#439, slice 6b: C96): the
# report's sections and the question are posted by that change's jobs, in order, and the
# question's view is the builder's to make (C93). These run the command on a cog whose change
# queue records what it is asked, and read the review from the request.

POST = "season.review.post"
#: The channel the manager runs the command in, where the review is posted.
COMMAND_CHANNEL = 111


def _report_bot(**kwargs) -> MagicMock:
    bot = _bot(**kwargs)
    bot.team_service.get_teams_with_roles = AsyncMock(
        return_value=[
            {"name": "Alpha", "role_id": 3001, "is_reserve": False},
            {"name": "Reserve", "role_id": None, "is_reserve": True},
        ]
    )
    bot.signup_module_service.get_settings = AsyncMock(
        return_value=SimpleNamespace(
            time_type="TIME_TRIAL", time_image_required=True, nationality_required=False
        )
    )
    bot.signup_module_service.get_slots = AsyncMock(
        return_value=[SimpleNamespace(display_label="Sunday 20:00")]
    )
    bot.change_queue.ask = AsyncMock(return_value=1)
    # The review sections hook as the builder signs signup, attendance, results and weather up
    # to it, each section read through the stubs above.
    from leaguebot.__main__ import _sign_up_to_review_sections

    _sign_up_to_review_sections(bot)
    return bot


async def _review_asked(cog, interaction, monkeypatch) -> dict:
    """Run `/season config-review` as the manager in channel 111, the season's evidence the team
    list "as reported", and return the review it asked the queue to post: one request, of
    `season.review.post`, carrying the manager's interaction."""
    from leaguebot.core.services import season_fingerprint_service as sfs

    async def _evidence(*_args, **_kwargs):
        return sfs.SeasonFingerprint({"the team list": "as reported"})

    made = record_review_views(monkeypatch)
    monkeypatch.setattr(sfs, "take_fingerprint", _evidence)
    interaction.client = cog.bot
    interaction.channel_id = COMMAND_CHANNEL
    interaction.followup.send = AsyncMock(return_value=MagicMock())
    with _gates_of(cog):
        await undecorate(SeasonCog.season_config_review)(cog, interaction)
    asked = [call for call in cog.bot.change_queue.ask.await_args_list
             if (call.kwargs.get("kind") or call.args[0]) == POST]
    assert len(asked) == 1, cog.bot.change_queue.ask.await_args_list
    [call] = asked
    assert call.kwargs.get("interaction") is interaction
    assert made == []
    return call.kwargs.get("payload") or call.args[1]


def _texts(review: dict) -> list[str]:
    """What the review posts, in order: its report's sections, then its question, if any."""
    return [*review["report"], *([review["question"]] if review.get("question") else [])]


async def _report(cog, interaction, monkeypatch) -> list[str]:
    return _texts(await _review_asked(cog, interaction, monkeypatch))


async def test_a_sound_configuration_is_reported_and_offered_for_confirmation(monkeypatch):
    """Manager runs `/season config-review` in channel 111 on a sound configuration of season 31
    (test mode on, signup on, results off; teams Alpha with its role, Reserve with none). The
    command posts nothing itself: it asks the queue to post the review in channel 111, with the
    manager's interaction, naming season 31, the manager as reviewer and `/season config-review`,
    and carrying the evidence its report was computed from. The report's first section heads the
    review and says what is on and off and which role each team has, warning of the reserve
    team's missing role; the review ends in the question asking to confirm the configuration,
    open for the next 5 minutes.
    The command writes no line to the log channel itself, no refusal and no "Review posted": the
    line naming who ran it is written once its question is posted (#482, criterion 6)."""
    bot = _report_bot(signup=True, test_mode=True)
    cog = _cog(bot)
    interaction = _run_by_the_manager(bot, _interaction())

    review = await _review_asked(cog, interaction, monkeypatch)

    messages = _texts(review)
    report = messages[0]
    assert "Configuration Review (Season #4 — F1 25)" in report
    assert "Test mode: ✅ On" in report
    assert "Signup: ✅ Enabled" in report
    assert "Results: ❌ Disabled" in report
    assert "Alpha → <@&3001>" in report
    assert "Reserve → no role" in report
    assert "Reserve team has no role assigned" in report
    assert "Do you confirm this season's configuration?" in messages[-1]
    assert "for the next 5 minutes" in messages[-1]
    assert (review["channel_id"], review["season_id"]) == (COMMAND_CHANNEL, SEASON_ID)
    assert (review["reviewer_id"], review["review"]) == (REVIEWER, "/season config-review")
    assert review["evidence"]
    interaction.followup.send.assert_not_awaited()
    assert not [line for line in _logged(bot) if "Review posted" in line]
    assert not any(line.startswith("⛔") for line in _logged(bot))


async def test_a_test_season_with_nothing_attached_is_refused_without_a_promise(
    tmp_path, monkeypatch
):
    """The half of #409 folded in from the configuration review. Test mode attaches Standard
    and Half Points when it is enabled and at no other moment (decided 2026-09-23), and the
    review once promised them "on approval" while refusing in the same report for want of one.
    A test season with results on and no points configuration attached: the review asked of the
    queue says none is attached, promises nothing, refuses for want of one, and asks no question.
    """
    from leaguebot.core.db.database import run_migrations

    db_path = str(tmp_path / "config_review.db")
    await run_migrations(db_path)
    bot = _report_bot(results=True, test_mode=True, db_path=db_path)
    cog = _cog(bot)
    cog.missing_points_config_problems = AsyncMock(return_value=[])
    cog.points_ordering_problems = AsyncMock(return_value=[])

    text = "\n".join(await _report(cog, _interaction(), monkeypatch))

    assert "**Points Configs:** *(none attached)*" in text
    assert "auto-seeded" not in text
    assert "No points configuration is attached to this season" in text
    assert "Do you confirm this season's configuration?" not in text


@pytest.mark.parametrize("signup", [True, False])
async def test_the_configuration_review_reports_the_league_s_roles(monkeypatch, signup):
    """The one review that reports them (issue #276). They are the league's, so the review asked
    of the queue shows them whether or not signup is on, and confirming the configuration fixes
    them."""
    cog = _cog(_report_bot(signup=signup))

    messages = await _report(cog, _interaction(), monkeypatch)

    assert "Base role: <@&2>" in messages[0]
    assert "Driver role: <@&3>" in messages[0]


async def test_every_enabled_modules_configuration_is_reported(monkeypatch):
    """The placements review's subsections, save the divisions, in the same words (#220): with
    signup, results, attendance, weather and images on, the review asked of the queue carries each
    module's configuration, the image outputs last and in a message of their own."""
    from leaguebot.results.services import season_points_service
    from leaguebot.weather.services import weather_config_service

    bot = _report_bot(signup=True, results=True, images=True)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.module_service.is_weather_enabled = AsyncMock(return_value=True)
    bot.attendance_service.get_config = AsyncMock(
        return_value=SimpleNamespace(
            rsvp_notice_days=5, rsvp_last_notice_hours=24, rsvp_deadline_hours=2,
            no_rsvp_penalty=1, absent_penalty=2, no_show_penalty=3,
            autoreserve_threshold=None, autosack_threshold=10,
        )
    )
    monkeypatch.setattr(
        season_points_service, "get_season_config_names",
        AsyncMock(return_value=["Standard", "Sprint"]),
    )
    monkeypatch.setattr(
        weather_config_service, "get_weather_pipeline_config",
        AsyncMock(return_value=SimpleNamespace(phase_1_days=5, phase_2_days=2, phase_3_hours=2)),
    )
    cog = _cog(bot)
    cog._build_image_review_section = AsyncMock(return_value=["**Image outputs**", "  lineup: on"])
    # The faults are pinned above; here only what is reported.
    cog.configuration_faults = AsyncMock(return_value=[])

    review = await _review_asked(cog, _interaction(), monkeypatch)
    messages = review["report"]
    text = "\n".join(_texts(review))

    # Each section its own message, after the season's, in the order the builder signs the
    # modules up, which is the placements review's order (core specification, "Confirming the
    # configuration").
    headings = ("**Signup Config**", "**Attendance Config**", "**Points Configs:**",
                "**Weather Config**", "**Image outputs**")
    assert len(messages) == 1 + len(headings), messages
    for message, heading in zip(messages[1:], headings):
        assert message.startswith(heading), (heading, message)
    assert "Available slots: Sunday 20:00" in text
    assert "Auto-sack threshold: 10 pts" in text
    assert "**Points Configs:** Standard, Sprint" in text
    assert "Phase 3 deadline: 2h before race" in text
    assert messages[-1] == "**Image outputs**\n  lineup: on"


async def test_a_disabled_modules_configuration_is_not_reported(monkeypatch):
    """With every module off, the review asked of the queue carries no module's configuration."""
    cog = _cog(_report_bot())

    text = "\n".join(await _report(cog, _interaction(), monkeypatch))

    for heading in ("Signup Config", "Attendance Config", "Points Configs", "Weather Config"):
        assert heading not in text


async def test_the_image_outputs_are_reported_when_the_module_is_on(monkeypatch):
    """With the image module on, the review asked of the queue carries its outputs."""
    bot = _report_bot(images=True)
    cog = _cog(bot)
    cog._build_image_review_section = AsyncMock(return_value=["**Image outputs**", "  lineup: on"])
    cog.image_configuration_faults = AsyncMock(return_value=[])

    messages = await _report(cog, _interaction(), monkeypatch)

    assert any("**Image outputs**" in m and "lineup: on" in m for m in messages)


async def test_a_faulty_configuration_is_reported_without_a_button(monkeypatch):
    """Signup is on and the league's base role is unset. The review asked of the queue ends in
    the faults, naming the base role and `/bot base-role`, and saying to run
    `/season config-review` again once they are put right; it asks no question to confirm."""
    bot = _report_bot(signup=True, roles=(None, 3))
    cog = _cog(bot)

    messages = await _report(cog, _interaction(), monkeypatch)

    assert "cannot be confirmed yet" in messages[-1]
    assert "**base role** — `/bot base-role`" in messages[-1]
    assert "/season config-review" in messages[-1]
    assert not any("Do you confirm this season's configuration?" in m for m in messages)


async def test_the_followup_is_restored_after_the_report(monkeypatch):
    """The review wraps the followup to collect what it says privately; a later reply must not be
    caught."""
    cog = _cog(_report_bot())
    interaction = _interaction()
    interaction.client = cog.bot

    original = AsyncMock(return_value=MagicMock())
    interaction.followup.send = original

    with _gates_of(cog):
        await undecorate(SeasonCog.season_config_review)(cog, interaction)

    assert interaction.followup.send is original


# ── The image module's checks ──────────────────────────────────────────────────────


_RASTERISER = "Inkscape is not installed on this host."
_PORTRAIT = "Driver portraits are fetched from a folder that does not exist."


def _image_cog(
    *, rasteriser=None, templates=(), colours=(), portrait=None, unreadable=(),
):
    """A cog whose drawing answers the image configuration's four questions as given; each
    question named in *unreadable* raises instead, as a check that cannot be read does."""
    bot = _bot(images=True)
    drawing = MagicMock()
    drawing.rasteriser_fault = MagicMock(return_value=rasteriser)
    answers = {
        "template_faults": list(templates),
        "colour_shortfall_problems": list(colours),
        "portrait_fault": portrait,
    }
    for name, answer in answers.items():
        if name in unreadable:
            setattr(drawing, name, AsyncMock(side_effect=OSError("gone")))
        else:
            setattr(drawing, name, AsyncMock(return_value=answer))
    bot.image_drawing = drawing
    return _cog(bot)


async def test_a_sound_image_configuration_has_no_faults():
    cog = _image_cog()

    assert await _image_faults(cog) == []
    cog.bot.image_drawing.template_faults.assert_awaited_once_with()


async def test_every_image_fault_that_needs_no_division_is_named():
    """The drawing program, a broken template a switched-on output draws, a per-tier colour and
    the portrait settings, each in the image module's words, in that order; core adds the
    colour's own label. Which templates are drawn is the image module's to say
    (`tests/image/test_image_drawing_review.py`)."""
    cog = _image_cog(
        rasteriser=_RASTERISER,
        templates=["Template **Lineup**: it has no driver name field."],
        colours=["tier 2 has no colour for slot accent"],
        portrait=_PORTRAIT,
    )

    assert await _image_faults(cog) == [
        _RASTERISER,
        "Template **Lineup**: it has no driver name field.",
        "Tier colour: tier 2 has no colour for slot accent",
        _PORTRAIT,
    ]


async def test_templates_that_cannot_be_read_are_a_fault_not_a_pass():
    cog = _image_cog(
        rasteriser=_RASTERISER,
        colours=["tier 2 has no colour for slot accent"],
        portrait=_PORTRAIT,
        unreadable=("template_faults",),
    )

    faults = await _image_faults(cog)

    assert faults == [_RASTERISER, "The image templates could not be read."]


async def test_a_portrait_check_whose_switch_nobody_signed_up_for_raises():
    """The images module's switch reader was never signed up, so the portrait check raises
    `SwitchNotSignedUp`: a fault in the bot's assembly, which the image faults raise rather than
    stand aside from as a check that cannot be read."""
    from leaguebot.core.services.module_service import SwitchNotSignedUp

    cog = _image_cog()
    cog.bot.image_drawing.portrait_fault = AsyncMock(side_effect=SwitchNotSignedUp("images"))

    with pytest.raises(SwitchNotSignedUp, match="images"):
        await _image_faults(cog)


@pytest.mark.parametrize(
    ("unreadable", "kept"),
    [
        pytest.param(
            "colour_shortfall_problems", [_RASTERISER, _PORTRAIT], id="colours",
        ),
        pytest.param(
            "portrait_fault",
            [_RASTERISER, "Tier colour: tier 2 has no colour for slot accent"],
            id="portraits",
        ),
    ],
)
async def test_an_unreadable_colour_or_portrait_check_stands_aside(unreadable, kept):
    """A per-tier colour check, or a portrait settings check, that cannot be read never refuses
    a season: it is dropped, and every other fault is still named. Takes the place of
    `test_per_tier_colour_validation.py::test_a_reader_fault_never_refuses_a_season` and
    `test_season_review_images.py::test_the_blocker_never_blocks_a_season_it_could_not_read`."""
    cog = _image_cog(
        rasteriser=_RASTERISER,
        colours=["tier 2 has no colour for slot accent"],
        portrait=_PORTRAIT,
        unreadable=(unreadable,),
    )

    assert await _image_faults(cog) == kept


# ── The button, on a season unchanged since the report ─────────────────────────────


async def test_an_unchanged_configuration_is_handed_to_the_confirmation_with_its_evidence(
    tmp_path, monkeypatch
):
    """Alex presses ✅ Confirm configuration on his review, the team list as its report described
    it. The confirmation is worked once, handed the press, the button's own name (which it
    records any refusal under) and the review's evidence, which the confirmation is judged against
    again when it runs. The button deletes nothing and asks no take-down itself: that is the
    confirmation's, asked of the queue with it."""
    from leaguebot.core.services.season_review_workflows import review_button

    env = await _standing_review(tmp_path)
    await _team_list_since_the_review(env, monkeypatch)
    view, workflows = _confirm_view(env, monkeypatch)
    interaction = _press(env)

    await type(view).approve(view, interaction, MagicMock())

    confirmation = getattr(workflows, CONFIRM)
    confirmation.assert_awaited_once()
    assert confirmation.await_args.args == (interaction,)
    asked = confirmation.await_args.kwargs
    assert asked["what"] == review_button("Confirm configuration", CONFIG_REVIEW)
    assert asked["fingerprint"] == {TEAM_LIST: "as reviewed"}
    assert env.channel.deleted == []
    assert await _take_downs(env) == []


# ── What the review records in the log channel (#482) ─────────────────────────────


def _run_by_the_manager(bot, interaction):
    """The manager's run of `/season config-review`, connected to the bot's log channel so that
    a line the command writes can be read. It reads as Discord's does: not answered until the
    command replies or defers, and answered from then on."""
    interaction.client = bot
    interaction.command.qualified_name = "season config-review"
    answered = {"done": False}

    async def _answer(*_args, **_kwargs):
        answered["done"] = True

    interaction.response.is_done = MagicMock(side_effect=lambda: answered["done"])
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    return interaction


def _logged(bot) -> list[str]:
    """The lines written to the log channel, in order."""
    return [str(call.args[0]) for call in bot.output_router.post_log.await_args_list]


_NOT_IN_CONFIGURATION = (
    "⛔ There is no season in configuration. `/season setup` begins one; a season whose "
    "configuration is confirmed is reviewed with `/season placements-review`."
)


@pytest.mark.parametrize(
    "stage, set_up",
    [
        pytest.param(SeasonStage.WAITING, True, id="a_season_waiting_for_signups"),
        pytest.param(SeasonStage.PLACEMENTS, True, id="a_season_in_placements"),
        pytest.param(None, False, id="no_season_being_set_up"),
    ],
)
async def test_every_config_review_refusal_is_recorded(stage, set_up):
    """The refusal answers as today and writes one refusal line (#482, criterion 1)."""
    bot = _bot(stage=stage)
    cog = _cog(bot)
    if not set_up:
        hold_setup(bot.season_service, None)
    interaction = _run_by_the_manager(bot, _interaction())

    await undecorate(SeasonCog.season_config_review)(cog, interaction)

    interaction.response.send_message.assert_awaited_once_with(
        _NOT_IN_CONFIGURATION, ephemeral=True
    )
    interaction.response.defer.assert_not_awaited()
    assert _logged(bot) == [
        f"⛔ `/season config-review` refused for Manager (<@{REVIEWER}>) — "
        f"{_NOT_IN_CONFIGURATION[2:]}"
    ]


# ── The confirmation's other refusals are recorded (#482) ───────────────────────────
#
# The core specification's "The record of what changed": a refusal is one line naming the
# member, what was refused and why. A refusal by ✅ Confirm configuration names the button and
# the review it belongs to, and one whose reply is a list carries every item of it.


def _manager_confirming(bot):
    """Manager (id 4242) pressing ✅ Confirm configuration, connected to the bot's log channel,
    and reading as Discord's does: not answered until it replies or defers."""
    interaction = _interaction()
    interaction.client = bot
    answered = {"done": False}

    async def _answer(*_args, **_kwargs):
        answered["done"] = True

    interaction.response.is_done = MagicMock(side_effect=lambda: answered["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    return interaction


async def test_confirming_with_no_season_in_configuration_is_refused_and_recorded(tmp_path):
    """The configuration review's question is pressed once no season is being set up: refused at
    once, one refusal line recorded, and nothing asked of the change queue."""
    bot = _bot(db_path=await _migrated(tmp_path))
    cog = _cog(bot)
    hold_setup(bot.season_service, None)
    interaction = _manager_confirming(bot)

    await _confirm(cog, interaction)

    interaction.response.send_message.assert_awaited_once_with(
        "⛔ There is no season in configuration.", ephemeral=True
    )
    assert bot.change_queue.method_calls == []
    logged = [call.args[0] for call in bot.output_router.post_log.await_args_list]
    assert len(logged) == 1, logged
    assert logged[0].startswith("⛔ ")
    assert "Confirm configuration" in logged[0] and "/season config-review" in logged[0]
    assert logged[0].endswith(
        " refused for Manager (<@4242>) — There is no season in configuration."
    )


async def test_confirming_a_configuration_with_faults_is_refused_with_every_fault_recorded(
    tmp_path,
):
    """Manager (id 4242) confirms a configuration that now holds two faults: refused, both
    named in the reply and the one refusal line, and nothing asked of the change queue."""
    bot = _bot(db_path=await _migrated(tmp_path))
    cog = _cog(bot)
    faults = [
        "The signup module is enabled but has no signup channel — set one with `/signup channel`.",
        "**Pro** has no lineup channel — set one with `/division lineup-channel`.",
    ]
    cog.configuration_faults = AsyncMock(return_value=faults)
    interaction = _manager_confirming(bot)

    await _confirm(cog, interaction)

    reply = _told(interaction)
    assert reply.startswith("⛔ The configuration cannot be confirmed:")
    assert "Nothing has been confirmed" in reply
    assert bot.change_queue.method_calls == []
    logged = [call.args[0] for call in bot.output_router.post_log.await_args_list]
    assert len(logged) == 1, logged
    head = logged[0].splitlines()[0]
    assert head.startswith("⛔ ")
    assert "Confirm configuration" in head and "/season config-review" in head
    assert " refused for Manager (<@4242>) — " in head
    for fault in faults:
        assert fault in logged[0], (fault, logged[0])
