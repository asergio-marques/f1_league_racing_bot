"""The teams a manager could mean, offered as they type a command's team parameter."""

from __future__ import annotations

from leaguebot.core.utils.league_bot import LeagueBot

#: The most choices Discord shows for one autocomplete.
_MAX_CHOICES = 25


def team_choices(teams, current: str | None, *, include_reserve: bool) -> list:
    """The teams *current* could be the start of, as autocomplete choices.

    A team is typed by its **shorthand**, so that is what each choice sends. It is
    offered under both its names — "RBR — Oracle Red Bull Racing" — and matched against either,
    so a manager who remembers only the full name still finds the team. *teams* are mappings
    carrying ``name`` (the shorthand), ``full_name`` and ``is_reserve``, in the order to offer
    them. The Reserve team is left out where the command refuses it.
    """
    from discord import app_commands

    typed = (current or "").strip().casefold()
    choices = []
    for team in teams:
        if team.get("is_reserve") and not include_reserve:
            continue
        shorthand, full_name = team["name"], team.get("full_name") or team["name"]
        if typed and typed not in shorthand.casefold() and typed not in full_name.casefold():
            continue
        label = shorthand if full_name == shorthand else f"{shorthand} — {full_name}"
        choices.append(app_commands.Choice(name=label[:100], value=shorthand))
        if len(choices) == _MAX_CHOICES:
            break
    return choices


async def team_autocomplete(bot: LeagueBot, current: str | None, *, include_reserve: bool) -> list:
    """The server's teams *current* could name, for a command's team parameter.

    One list serves every command that takes a team, the division commands included: every
    division is built from the server's list, and the list is fixed from the moment a season's
    configuration is confirmed until that season ends, so a division's teams are the list's.
    """
    teams = await bot.team_service.get_teams_with_roles()
    return team_choices(teams, current, include_reserve=include_reserve)
