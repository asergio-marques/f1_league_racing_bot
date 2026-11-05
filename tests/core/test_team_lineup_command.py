"""`/team lineup` and `/team reserve-role`.

Issue #208. `tests/core/test_team_cog.py` covers adding, removing and renaming teams; the
lineup listing and the reserve-role mapping were unexecuted.

**`/team lineup` has two quite different outputs and the choice between them is a league
setting.** Where the image module's lineup graphic is enabled the command sends pictures; where
it is not, it writes the same information out as text. Both have to work, because a league
without the graphic is not a league without a lineup — and the textual path is the one that
gets forgotten, since the graphic is what anybody looks at while developing.

**The output is explicitly not the lineup of record** (FR-028). Nothing is written to
`lineup_message_id` and the lineup channel is untouched: this is a manager asking to see the
lineup, not the bot publishing it. A reader wiring this into the posting path would give a
league a second lineup post every time somebody ran the command.

**A rejected graphic refuses rather than falling back to text.** The caller is the one person
able to fix the template, so silently drawing text instead would hide a broken template from
the only person who could put it right (Constitution XIV.7). `test_a_rejected_graphic_refuses_
rather_than_falling_back` holds that, and `test_team_cog.py`'s `TestTeamLineupDiscardsItsPictures`
holds the matching resource rule: every file already drawn in the batch is discarded when a later
division rejects, which is the one leak no posting path accounts for.

**The text path draws every seat, filled or empty.** An empty seat is information — it is where
a manager can place somebody — so it is listed as empty rather than omitted, and the seats are
enumerated from the team's capacity rather than from the rows that happen to exist.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.models.season import SeasonStage

from leaguebot.core.cogs.team_cog import TeamCog
from tests.support.lineup_drawing import LineupDrawingStub
from tests.support.undecorate import undecorate

#: Why the drawn path's tests fail until the build.
_THROUGH_THE_DRAWING = (
    "#283: /team lineup still draws through the image module's own functions, not the drawing "
    "the bot carries"
)

SERVER_ID = 9208
SEASON_ID = 3
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _division(div_id: int, name: str, tier: int):
    return SimpleNamespace(id=div_id, name=name, tier=tier)


def _team(name: str, max_seats: int, seats: list[tuple[int, int | None, str | None]], *,
           full_name: str | None = None):
    return {
        "name": name,
        "full_name": full_name or name,
        "max_seats": max_seats,
        "is_reserve": name == "Reserve",
        "seats": [
            {"seat_number": n, "driver_profile_id": pid, "discord_user_id": uid}
            for n, pid, uid in seats
        ],
    }


def _make_cog(
    *,
    season=SimpleNamespace(id=SEASON_ID, season_number=1),
    divisions=None,
    teams=None,
    resolve=(11, "Division 1"),
    drawing: LineupDrawingStub | None = None,
) -> TeamCog:
    """The cog over a bot whose drawing is *drawing*: by default one that does not draw the
    lineup, so the command takes its textual path."""
    bot = MagicMock()
    bot.image_drawing = drawing if drawing is not None else LineupDrawingStub(wanted=False)
    bot.season_service = MagicMock()
    bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    # `/team reserve-role` is refused once the season is pending completion (issue #224), so
    # it reads the live season for its stage. Ongoing: the mapping is repaired mid-season.
    bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=SimpleNamespace(
            id=SEASON_ID, season_number=1, stage=SeasonStage.ONGOING
        )
    )
    bot.season_service.get_divisions = AsyncMock(
        return_value=divisions if divisions is not None else [_division(11, "Division 1", 1)]
    )
    bot.placement_service = MagicMock()
    bot.placement_service.resolve_division = AsyncMock(return_value=resolve)
    bot.placement_service.set_team_role_config = AsyncMock(return_value=None)
    bot.placement_service.delete_team_role_config = AsyncMock(return_value=None)
    bot.placement_service.swap_team_role = AsyncMock(return_value=0)
    bot.placement_service.team_holding_role = AsyncMock(return_value=None)
    bot.team_service = MagicMock()
    bot.team_service.get_teams_with_roles = AsyncMock(return_value=[])
    bot.team_service.get_division_teams = AsyncMock(
        return_value=teams if teams is not None else []
    )
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = TeamCog.__new__(TeamCog)
    cog.bot = bot
    return cog


def _interaction(members: dict | None = None):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Manager"
    interaction.user.__str__ = lambda self: "Manager#0001"  # type: ignore[assignment]
    guild = MagicMock()
    guild.get_member = MagicMock(side_effect=lambda uid: (members or {}).get(int(uid)))
    interaction.guild = guild
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _sent(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.followup.send.await_args_list
        + interaction.response.send_message.await_args_list
        if call.args
    )


async def _lineup(cog, interaction, *, division=None, public=False):
    await undecorate(TeamCog.team_lineup)(cog, interaction, division, public)


# ---------------------------------------------------------------------------
# The refusal ladder
# ---------------------------------------------------------------------------


async def test_no_active_season_is_refused(tmp_path):
    cog = _make_cog(season=None)
    interaction = _interaction()

    await _lineup(cog, interaction)

    assert "No season is being raced" in _sent(interaction)


async def test_an_unknown_division_is_refused_by_name(tmp_path):
    cog = _make_cog(resolve=None)
    interaction = _interaction()

    await _lineup(cog, interaction, division="Division 9")

    assert "Division 9" in _sent(interaction)
    assert "not found" in _sent(interaction)


async def test_a_season_with_no_divisions_says_so(tmp_path):
    cog = _make_cog(divisions=[])
    interaction = _interaction()

    await _lineup(cog, interaction)

    assert "No divisions found" in _sent(interaction)


# ---------------------------------------------------------------------------
# The textual path
# ---------------------------------------------------------------------------


async def test_every_division_is_listed_in_tier_order(tmp_path):
    """Tier order, not the order the query returned — a league reads its divisions from
    the top down."""
    cog = _make_cog(
        divisions=[_division(12, "Division 2", 2), _division(11, "Division 1", 1)]
    )
    interaction = _interaction()

    await _lineup(cog, interaction)

    sent = _sent(interaction)
    assert sent.index("Division 1") < sent.index("Division 2")


async def test_a_seated_driver_is_named(tmp_path):
    member = MagicMock()
    member.display_name = "Lewis"
    cog = _make_cog(teams=[_team("Alpha", 2, [(1, 55, "4242"), (2, None, None)])])
    interaction = _interaction(members={4242: member})

    await _lineup(cog, interaction)

    assert "Lewis" in _sent(interaction)


async def test_an_empty_seat_is_listed_as_empty(tmp_path):
    """An empty seat is where a manager can place somebody, so omitting it would hide the
    thing the command is most often run to find."""
    cog = _make_cog(teams=[_team("Alpha", 2, [(1, 55, "4242"), (2, None, None)])])
    interaction = _interaction()

    await _lineup(cog, interaction)

    sent = _sent(interaction)
    assert "Seat 1" in sent
    assert "Seat 2" in sent
    assert "(empty)" in sent


async def test_seats_are_enumerated_from_the_team_s_capacity(tmp_path):
    """A team of two whose seat rows are missing must still show two seats — the capacity
    is what the league configured, and the rows are only what happens to exist."""
    cog = _make_cog(teams=[_team("Alpha", 3, [])])
    interaction = _interaction()

    await _lineup(cog, interaction)

    sent = _sent(interaction)
    for seat in ("Seat 1", "Seat 2", "Seat 3"):
        assert seat in sent


async def test_a_driver_who_has_left_is_listed_by_id(tmp_path):
    cog = _make_cog(teams=[_team("Alpha", 1, [(1, 55, "4242")])])
    interaction = _interaction(members={})

    await _lineup(cog, interaction)

    assert "4242" in _sent(interaction)


async def test_a_division_with_no_teams_says_so(tmp_path):
    """Distinct from a division that does not exist, and a real state during setup."""
    cog = _make_cog(teams=[])
    interaction = _interaction()

    await _lineup(cog, interaction)

    assert "(no teams)" in _sent(interaction)


async def test_asking_publicly_sends_publicly(tmp_path):
    """The default is ephemeral; `public` is how a manager shows the division its lineup."""
    cog = _make_cog(teams=[_team("Alpha", 1, [(1, None, None)])])
    interaction = _interaction()

    await _lineup(cog, interaction, public=True)

    assert interaction.followup.send.await_args.kwargs["ephemeral"] is False


# ---------------------------------------------------------------------------
# The graphic path
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=_THROUGH_THE_DRAWING)
async def test_a_rejected_graphic_refuses_rather_than_falling_back(tmp_path):
    """The league draws its lineup, and Division 1's lineup cannot be drawn: the template is
    missing a field. The manager is told why, by that one message alone, and nothing else is
    sent, the text least of all: the caller is the one person able to fix the template, so
    drawing text instead would hide a broken template from the only person who could put it
    right. It is drawn as the command's output, never as the lineup of record."""
    drawing = LineupDrawingStub(problem="the lineup: `team_1` — Template is missing a field")
    cog = _make_cog(teams=[_team("Alpha", 1, [(1, None, None)])], drawing=drawing)
    interaction = _interaction()

    await _lineup(cog, interaction)

    [call] = interaction.followup.send.await_args_list
    assert call.args[0] == (
        "❌ the lineup: `team_1` — Template is missing a field\n"
        "_Lineup for **Division 1** was not drawn._"
    )
    assert call.kwargs["ephemeral"] is True
    assert drawing.drawn_for_command == [11]
    assert drawing.drawn == []


@pytest.mark.parametrize("public", [False, True], ids=["private", "public"])
@pytest.mark.xfail(strict=True, reason=_THROUGH_THE_DRAWING)
async def test_a_drawn_lineup_is_sent_as_files(tmp_path, public):
    """The league draws its lineup, and Division 1's lineup is drawn, with a notice. The manager
    is sent the picture in place of the text, under the division's name in bold, seen by them
    alone unless they asked for it publicly; the picture is handed back to the drawing to release once sent; and the command
    itself posts nothing to the log channel, because the drawing has already reported the
    notice there."""
    png = tmp_path / "lineup.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    drawing = LineupDrawingStub(picture=png, notices=("a fallback flag stood in for Zara's",))
    cog = _make_cog(teams=[_team("Alpha", 1, [(1, None, None)])], drawing=drawing)
    interaction = _interaction()

    await _lineup(cog, interaction, public=public)

    [call] = interaction.followup.send.await_args_list
    assert call.args[0] == "**Division 1**"
    assert [each.filename for each in call.kwargs["files"]] == ["lineup.png"]
    assert call.kwargs["ephemeral"] is (not public)
    assert "Seat 1" not in _sent(interaction)
    assert drawing.discarded == [png]
    cog.bot.output_router.post_log.assert_not_awaited()


@pytest.mark.xfail(strict=True, reason=_THROUGH_THE_DRAWING)
async def test_a_drawing_that_fails_fails_the_command_and_releases_what_it_drew(tmp_path):
    """The league draws its lineup across two divisions: Division 1's is drawn, and drawing
    Division 2's raises (the database cannot be read). The error is not caught and worded by the
    command: it leaves the command, for the bot's failure reply and log line to report, and the
    picture already drawn for Division 1 is still handed back to release. Nothing is sent."""
    png = tmp_path / "lineup.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    drawing = LineupDrawingStub(
        by_division={11: {"picture": png}, 12: {"fault": RuntimeError("database is locked")}}
    )
    cog = _make_cog(
        divisions=[_division(11, "Division 1", 1), _division(12, "Division 2", 2)],
        teams=[_team("Alpha", 1, [(1, None, None)])],
        drawing=drawing,
    )
    interaction = _interaction()

    with pytest.raises(RuntimeError, match="database is locked"):
        await _lineup(cog, interaction)

    assert drawing.drawn_for_command == [11, 12]
    assert drawing.discarded == [png]
    interaction.followup.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# /team reserve-role
# ---------------------------------------------------------------------------


async def test_setting_the_reserve_role_is_logged_with_the_role(tmp_path):
    cog = _make_cog()
    interaction = _interaction()
    # A team's role must be one the bot can grant (#381), so the double answers that check.
    role = MagicMock()
    role.id = 4242
    role.name = "Reserves"
    role.mention = "@Reserves"
    role.is_default.return_value = False
    role.managed = False
    role.guild.me.guild_permissions.manage_roles = True
    role.guild.me.top_role.__gt__ = lambda _self, _other: True

    await undecorate(TeamCog.team_reserve_role)(cog, interaction, role)

    logged = cog.bot.output_router.post_log.await_args.args[0]
    assert "/team reserve-role" in logged
    assert "Reserves" in logged


async def test_clearing_the_reserve_role_is_logged_as_cleared(tmp_path):
    cog = _make_cog()
    interaction = _interaction()

    await undecorate(TeamCog.team_reserve_role)(cog, interaction, None)

    assert "cleared" in cog.bot.output_router.post_log.await_args.args[0]


async def test_the_lineup_names_each_team_by_its_full_name():
    """A team is shown by its full name and typed by its shorthand (#381)."""
    cog = _make_cog(
        teams=[_team("RBR", 2, [(1, 7, "900"), (2, None, None)], full_name="Oracle Red Bull Racing")]
    )
    interaction = _interaction()

    await _lineup(cog, interaction)

    assert "Oracle Red Bull Racing" in _sent(interaction)
