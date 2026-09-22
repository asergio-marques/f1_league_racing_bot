"""`/team lineup` and `/team reserve-role`.

Issue #208. `tests/unit/test_team_cog.py` covers adding, removing and renaming teams; the
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
rather_than_falling_back` holds that, and its neighbour holds the matching resource rule: every
file already drawn in the batch is discarded when a later division rejects, which is the one
leak no posting path accounts for.

**The text path draws every seat, filled or empty.** An empty seat is information — it is where
a manager can place somebody — so it is listed as empty rather than omitted, and the seats are
enumerated from the team's capacity rather than from the rows that happen to exist.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.season import SeasonStage  # noqa: E402

from cogs.team_cog import TeamCog  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

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
) -> TeamCog:
    bot = MagicMock()
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


def _no_graphic():
    """The image module switched off, so the command takes its textual path."""
    return patch("services.image_lineup_post.lineup_enabled", new=AsyncMock(return_value=False))


# ---------------------------------------------------------------------------
# The refusal ladder
# ---------------------------------------------------------------------------


async def test_no_active_season_is_refused(tmp_path):
    cog = _make_cog(season=None)
    interaction = _interaction()

    with _no_graphic():
        await _lineup(cog, interaction)

    assert "No season is being raced" in _sent(interaction)


async def test_an_unknown_division_is_refused_by_name(tmp_path):
    cog = _make_cog(resolve=None)
    interaction = _interaction()

    with _no_graphic():
        await _lineup(cog, interaction, division="Division 9")

    assert "Division 9" in _sent(interaction)
    assert "not found" in _sent(interaction)


async def test_a_season_with_no_divisions_says_so(tmp_path):
    cog = _make_cog(divisions=[])
    interaction = _interaction()

    with _no_graphic():
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

    with _no_graphic():
        await _lineup(cog, interaction)

    sent = _sent(interaction)
    assert sent.index("Division 1") < sent.index("Division 2")


async def test_a_seated_driver_is_named(tmp_path):
    member = MagicMock()
    member.display_name = "Lewis"
    cog = _make_cog(teams=[_team("Alpha", 2, [(1, 55, "4242"), (2, None, None)])])
    interaction = _interaction(members={4242: member})

    with _no_graphic():
        await _lineup(cog, interaction)

    assert "Lewis" in _sent(interaction)


async def test_an_empty_seat_is_listed_as_empty(tmp_path):
    """An empty seat is where a manager can place somebody, so omitting it would hide the
    thing the command is most often run to find."""
    cog = _make_cog(teams=[_team("Alpha", 2, [(1, 55, "4242"), (2, None, None)])])
    interaction = _interaction()

    with _no_graphic():
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

    with _no_graphic():
        await _lineup(cog, interaction)

    sent = _sent(interaction)
    for seat in ("Seat 1", "Seat 2", "Seat 3"):
        assert seat in sent


async def test_a_driver_who_has_left_is_listed_by_id(tmp_path):
    cog = _make_cog(teams=[_team("Alpha", 1, [(1, 55, "4242")])])
    interaction = _interaction(members={})

    with _no_graphic():
        await _lineup(cog, interaction)

    assert "4242" in _sent(interaction)


async def test_a_division_with_no_teams_says_so(tmp_path):
    """Distinct from a division that does not exist, and a real state during setup."""
    cog = _make_cog(teams=[])
    interaction = _interaction()

    with _no_graphic():
        await _lineup(cog, interaction)

    assert "(no teams)" in _sent(interaction)


async def test_asking_publicly_sends_publicly(tmp_path):
    """The default is ephemeral; `public` is how a manager shows the division its lineup."""
    cog = _make_cog(teams=[_team("Alpha", 1, [(1, None, None)])])
    interaction = _interaction()

    with _no_graphic():
        await _lineup(cog, interaction, public=True)

    assert interaction.followup.send.await_args.kwargs["ephemeral"] is False


# ---------------------------------------------------------------------------
# The graphic path
# ---------------------------------------------------------------------------


def _graphic(outcome):
    return (
        patch("services.image_lineup_post.lineup_enabled", new=AsyncMock(return_value=True)),
        patch(
            "services.image_lineup_post.render_for_command",
            new=AsyncMock(return_value=outcome),
        ),
    )


async def test_a_rejected_graphic_refuses_rather_than_falling_back(tmp_path, monkeypatch):
    """The caller is the one person able to fix the template, so drawing text instead
    would hide a broken template from the only person who could put it right."""
    outcome = SimpleNamespace(
        action="REJECTED", message="Template is missing a field.", png_path=None, notices=[]
    )
    enabled, render = _graphic(outcome)
    cog = _make_cog(teams=[_team("Alpha", 1, [(1, None, None)])])
    interaction = _interaction()

    with enabled, render:
        await _lineup(cog, interaction)

    sent = _sent(interaction)
    assert "Template is missing a field." in sent
    assert "was not drawn" in sent
    assert "Seat 1" not in sent


async def test_a_rejection_discards_the_files_already_drawn(tmp_path):
    """One `finally` around the whole batch. A rejection part way through abandons every
    picture drawn before it, and without the discard those files are the one leak no
    posting path accounts for."""
    png = tmp_path / "lineup.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    outcomes = [
        SimpleNamespace(action="POSTED", message="", png_path=png, notices=[]),
        SimpleNamespace(action="REJECTED", message="bad", png_path=None, notices=[]),
    ]
    discarded: list = []

    cog = _make_cog(
        divisions=[_division(11, "Division 1", 1), _division(12, "Division 2", 2)],
        teams=[_team("Alpha", 1, [(1, None, None)])],
    )
    interaction = _interaction()

    with patch(
        "services.image_lineup_post.lineup_enabled", new=AsyncMock(return_value=True)
    ), patch(
        "services.image_lineup_post.render_for_command",
        new=AsyncMock(side_effect=outcomes),
    ), patch(
        "services.image_render_service.discard_attachment",
        new=lambda *files: discarded.extend(files),
    ):
        await _lineup(cog, interaction)

    assert discarded, "the file drawn for the first division was never discarded"


async def test_a_drawn_lineup_is_sent_as_files(tmp_path):
    png = tmp_path / "lineup.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    outcome = SimpleNamespace(action="POSTED", message="", png_path=png, notices=[])
    enabled, render = _graphic(outcome)
    cog = _make_cog(teams=[_team("Alpha", 1, [(1, None, None)])])
    interaction = _interaction()

    with enabled, render, patch(
        "services.image_render_service.discard_attachment", new=lambda *f: None
    ):
        await _lineup(cog, interaction)

    assert interaction.followup.send.await_args.kwargs.get("files")
    assert "Seat 1" not in _sent(interaction)


# ---------------------------------------------------------------------------
# /team reserve-role
# ---------------------------------------------------------------------------


async def test_the_reserve_role_is_set(tmp_path):
    cog = _make_cog()
    interaction = _interaction()
    role = MagicMock()
    role.id = 4242
    role.name = "Reserves"
    role.mention = "@Reserves"

    await undecorate(TeamCog.team_reserve_role)(cog, interaction, role)

    cog.bot.placement_service.set_team_role_config.assert_awaited_once()
    args = cog.bot.placement_service.set_team_role_config.await_args
    assert args.args[0] == "Reserve"
    assert args.args[1] == 4242
    assert "set to" in _sent(interaction)


async def test_omitting_the_role_clears_the_mapping(tmp_path):
    """The parameter is optional precisely so it can be cleared; a league that stops using
    a reserve role has no other way to unset it."""
    cog = _make_cog()
    interaction = _interaction()

    await undecorate(TeamCog.team_reserve_role)(cog, interaction, None)

    cog.bot.placement_service.delete_team_role_config.assert_awaited_once()
    cog.bot.placement_service.set_team_role_config.assert_not_awaited()
    assert "cleared" in _sent(interaction)


async def test_setting_the_reserve_role_is_logged_with_the_role(tmp_path):
    cog = _make_cog()
    interaction = _interaction()
    role = MagicMock()
    role.id = 4242
    role.name = "Reserves"
    role.mention = "@Reserves"

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

    with _no_graphic():
        await _lineup(cog, interaction)

    assert "Oracle Red Bull Racing" in _sent(interaction)
