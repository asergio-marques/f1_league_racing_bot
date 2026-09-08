"""What each channel of a server is already being used for.

A channel does one job. The bot posts to eleven configurable places — eight per division,
two for the bot itself and one for signups — and pointing two of them at one channel
interleaves two kinds of posting in it: a calendar among results, a check-in call among
forecasts. Worse, several posting paths *edit or delete* the message they posted last, and
they find it by an id stored against the channel, so two purposes sharing a channel is how
a lineup ends up deleting a standings message.

The rule is therefore **server-wide** (decided 2026-09-06): a channel serving one purpose
anywhere on the server cannot be given a second, not even in another division. Two
divisions posting their results to one channel would produce two sets of tables nobody can
tell apart.

Read at the moment a channel is set, never cached: a manager can clear a channel by other
routes, and a stale answer would refuse a channel that is genuinely free.
"""

from __future__ import annotations

from dataclasses import dataclass

from db.database import get_connection

__all__ = ["ChannelUse", "find_channel_use", "SETTING_LABELS"]


#: Every configurable channel, by the label a league reads. The key is what the calling
#: command passes; the value is what the refusal names.
SETTING_LABELS: dict[str, str] = {
    "calendar": "calendar",
    "lineup": "lineup",
    "results": "results",
    "standings": "standings",
    "verdicts": "verdicts",
    "rsvp": "check-in call",
    "attendance": "attendance",
    "weather": "weather forecast",
    "interaction": "bot command",
    "log": "bot log",
    "signup": "signup",
}


@dataclass(frozen=True)
class ChannelUse:
    """What a channel is already doing.

    *setting* is a key of :data:`SETTING_LABELS`; *division_name* is None for the three
    settings that belong to the server rather than to a division.
    """

    setting: str
    division_name: str | None = None

    @property
    def label(self) -> str:
        return SETTING_LABELS.get(self.setting, self.setting)

    def describe(self) -> str:
        """The phrase a refusal puts after "already the"."""
        if self.division_name is None:
            return f"{self.label} channel"
        return f"{self.label} channel for **{self.division_name}**"


#: The per-division settings, as (setting key, table, column). The divisions row carries
#: three of them directly; the other five hang off it in two config tables.
_DIVISION_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("weather", "divisions", "forecast_channel_id"),
    ("lineup", "divisions", "lineup_channel_id"),
    ("calendar", "divisions", "calendar_channel_id"),
    ("results", "division_results_config", "results_channel_id"),
    ("standings", "division_results_config", "standings_channel_id"),
    ("verdicts", "division_results_config", "penalty_channel_id"),
    ("rsvp", "attendance_division_config", "rsvp_channel_id"),
    ("attendance", "attendance_division_config", "attendance_channel_id"),
)

#: The server-wide settings, as (setting key, table, column, server key column).
_SERVER_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("interaction", "server_configs", "interaction_channel_id"),
    ("log", "server_configs", "log_channel_id"),
    ("signup", "signup_module_config", "signup_channel_id"),
)


async def find_channel_use(
    db_path: str,
    server_id: int,
    channel_id: int,
    *,
    ignore: ChannelUse | None = None,
) -> ChannelUse | None:
    """What *channel_id* is already used for on *server_id*, or None where it is free.

    *ignore* names a use to disregard — the setting being written. Without it, changing a
    division's results channel to a different channel would be refused by its own current
    value the moment the caller re-checks, and a command could never be re-run.

    Only the **live** season is searched. A completed or cancelled season keeps its channel
    columns, and a league reusing a channel for its next season is doing the ordinary
    thing, not colliding with its own history.
    """
    async with get_connection(db_path) as db:
        for setting, table, column in _SERVER_SOURCES:
            if ignore is not None and ignore.setting == setting:
                continue
            cursor = await db.execute(
                f"SELECT 1 FROM {table} WHERE server_id = ? AND {column} = ? LIMIT 1",
                (server_id, channel_id),
            )
            if await cursor.fetchone() is not None:
                return ChannelUse(setting)

        for setting, table, column in _DIVISION_SOURCES:
            if table == "divisions":
                sql = (
                    f"SELECT d.name FROM divisions d "
                    f"JOIN seasons s ON s.id = d.season_id "
                    f"WHERE s.server_id = ? AND s.status IN ('SETUP', 'ACTIVE') "
                    f"  AND d.{column} = ? LIMIT 1"
                )
            else:
                sql = (
                    f"SELECT d.name FROM {table} c "
                    f"JOIN divisions d ON d.id = c.division_id "
                    f"JOIN seasons s ON s.id = d.season_id "
                    f"WHERE s.server_id = ? AND s.status IN ('SETUP', 'ACTIVE') "
                    f"  AND c.{column} = ? LIMIT 1"
                )
            cursor = await db.execute(sql, (server_id, channel_id))
            row = await cursor.fetchone()
            if row is None:
                continue
            use = ChannelUse(setting, row[0])
            if ignore is not None and ignore == use:
                continue
            return use

    return None


def refusal(channel_mention: str, use: ChannelUse, *, same_setting: bool) -> str:
    """The message a command replies with, refusing *channel_mention*.

    *same_setting* distinguishes the two refusals a manager can meet. Re-running a command
    with the value it already holds is not a collision with something else, and telling
    them it clashes would send them looking for a conflict that does not exist.
    """
    if same_setting:
        return (
            f"ℹ️ {channel_mention} is already the {use.describe()}. "
            f"Nothing was changed."
        )
    return (
        f"❌ {channel_mention} is already the {use.describe()}. "
        f"A channel does one job — pick one that is not in use, or clear the other "
        f"setting first."
    )
