"""`TestCommands`: the `/images test` subgroup, the twelve previews.

One command for each image kind, each drawn against the league's own division and, where the
kind pertains to one, its own round. Every preview runs through `run_preview`, and its reply is
worded by `send_preview` (`image/services/preview/preview_runner.py`).
"""
from __future__ import annotations

import discord
from discord import app_commands

from leaguebot.core.utils.channel_guard import league_manager_only, changes_nothing
from leaguebot.image.cogs.image_group import TEST_GROUP, ImageSubgroup
from leaguebot.image.services.preview.preview_runner import run_preview, send_preview


class TestCommands(ImageSubgroup):
    test = TEST_GROUP

    async def _run_preview(
        self,
        interaction: discord.Interaction,
        *,
        title: str,
        kind: str,
        division: str | None,
        build,
        round_number: int | None = None,
    ) -> None:
        """The body every preview command shares: the module's gate, then the runner's."""
        if not await self._module_gate(interaction, record=False):
            return

        await run_preview(
            interaction,
            bot=self.bot,
            render=self._render,
            driver_names_read=self.bot.driver_names_read,
            post_log=self.bot.output_router.post_log,
            title=title,
            kind=kind,
            division=division,
            build=build,
            round_number=round_number,
        )

    async def _render(self, *args, **kwargs):
        """Draw through the render service, which is looked up when a preview draws."""
        return await self.bot.image_render_service.render(*args, **kwargs)

    # ── The two kinds that fabricate no outcome ──────────────────────

    @test.command(
        name="calendar",
        description="Preview the calendar image for one of your divisions.",
    )
    @app_commands.describe(
        division="The division whose calendar to draw."
    )
    @league_manager_only
    @changes_nothing
    async def test_calendar(
        self, interaction: discord.Interaction, division: str
    ) -> None:
        from leaguebot.image.services.preview.image_preview_service import build_calendar_preview

        async def _build(context):
            return await build_calendar_preview(self.bot, context)

        await self._run_preview(
            interaction,
            title="Calendar",
            kind="calendar",
            division=division,
            build=_build,
        )

    @test.command(
        name="lineup",
        description="Preview the lineup image for one of your divisions.",
    )
    @app_commands.describe(
        division="The division whose lineup to draw."
    )
    @league_manager_only
    @changes_nothing
    async def test_lineup(
        self, interaction: discord.Interaction, division: str
    ) -> None:
        from leaguebot.image.services.preview.image_preview_service import build_lineup_preview

        async def _build(context):
            return await build_lineup_preview(self.bot, context)

        await self._run_preview(
            interaction,
            title="Lineup",
            kind="lineup",
            division=division,
            build=_build,
        )

    @test.command(
        name="results",
        description="Preview the results image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @league_manager_only
    @changes_nothing
    async def test_results(
        self,
        interaction: discord.Interaction,
        division: str,
        round: int,
    ) -> None:
        from leaguebot.image.services.preview.image_preview_service import build_results_preview

        async def _build(context):
            return await build_results_preview(self.bot, context, self.bot.preview_sources)

        await self._run_preview(
            interaction,
            title="Results",
            kind="results",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="standings",
        description="Preview the standings image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @league_manager_only
    @changes_nothing
    async def test_standings(
        self,
        interaction: discord.Interaction,
        division: str,
        round: int,
    ) -> None:
        from leaguebot.image.services.preview.image_preview_service import build_standings_preview

        async def _build(context):
            return await build_standings_preview(self.bot, context, self.bot.preview_sources)

        await self._run_preview(
            interaction,
            title="Standings",
            kind="standings",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="attendance",
        description="Preview the attendance sheet image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @league_manager_only
    @changes_nothing
    async def test_attendance(
        self,
        interaction: discord.Interaction,
        division: str,
        round: int,
    ) -> None:
        from leaguebot.image.services.preview.image_preview_service import build_attendance_preview

        async def _build(context):
            return await build_attendance_preview(self.bot, context)

        await self._run_preview(
            interaction,
            title="Attendance sheet",
            kind="attendance",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="rsvp",
        description="Preview the check-in call image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @league_manager_only
    @changes_nothing
    async def test_rsvp(
        self,
        interaction: discord.Interaction,
        division: str,
        round: int,
    ) -> None:
        from leaguebot.image.services.preview.image_preview_service import build_rsvp_preview

        async def _build(context):
            return await build_rsvp_preview(self.bot, context)

        await self._run_preview(
            interaction,
            title="Check-in call",
            kind="rsvp",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="verdict",
        description="Preview the verdict image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @league_manager_only
    @changes_nothing
    async def test_verdict(
        self,
        interaction: discord.Interaction,
        division: str,
        round: int,
    ) -> None:
        from leaguebot.image.services.preview.image_preview_service import build_verdict_preview

        async def _build(context):
            return await build_verdict_preview(self.bot, context)

        await self._run_preview(
            interaction,
            title="Verdict",
            kind="verdict",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="verdict-banner",
        description="Preview the verdict banner for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @league_manager_only
    @changes_nothing
    async def test_verdict_banner(
        self,
        interaction: discord.Interaction,
        division: str,
        round: int,
    ) -> None:
        from leaguebot.image.services.preview.image_preview_service import build_verdict_banner_preview

        async def _build(context):
            return await build_verdict_banner_preview(self.bot, context)

        await self._run_preview(
            interaction,
            title="Verdict banner",
            kind="verdict-banner",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="weather-p1",
        description="Preview the weather — phase 1 image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @league_manager_only
    @changes_nothing
    async def test_weather_p1(
        self,
        interaction: discord.Interaction,
        division: str,
        round: int,
    ) -> None:
        from leaguebot.image.services.preview.image_preview_service import build_weather_preview

        async def _build(context):
            return await build_weather_preview(self.bot, context, phase=1)

        await self._run_preview(
            interaction,
            title="Weather — phase 1",
            kind="weather-p1",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="weather-p2",
        description="Preview the weather — phase 2 image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @league_manager_only
    @changes_nothing
    async def test_weather_p2(
        self,
        interaction: discord.Interaction,
        division: str,
        round: int,
    ) -> None:
        from leaguebot.image.services.preview.image_preview_service import build_weather_preview

        async def _build(context):
            return await build_weather_preview(self.bot, context, phase=2)

        await self._run_preview(
            interaction,
            title="Weather — phase 2",
            kind="weather-p2",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="weather-p3",
        description="Preview the weather — phase 3 image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @league_manager_only
    @changes_nothing
    async def test_weather_p3(
        self,
        interaction: discord.Interaction,
        division: str,
        round: int,
    ) -> None:
        from leaguebot.image.services.preview.image_preview_service import build_weather_preview

        async def _build(context):
            return await build_weather_preview(self.bot, context, phase=3)

        await self._run_preview(
            interaction,
            title="Weather — phase 3",
            kind="weather-p3",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="weather-mystery",
        description="Preview the mystery notice image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @league_manager_only
    @changes_nothing
    async def test_weather_mystery(
        self,
        interaction: discord.Interaction,
        division: str,
        round: int,
    ) -> None:
        from leaguebot.image.services.preview.image_preview_service import build_weather_preview

        async def _build(context):
            return await build_weather_preview(self.bot, context, phase=0)

        await self._run_preview(
            interaction,
            title="Mystery notice",
            kind="weather-mystery",
            division=division,
            round_number=round,
            build=_build,
        )

    _division_autocomplete = ImageSubgroup._division_autocomplete

    test_calendar.autocomplete("division")(_division_autocomplete)
    test_lineup.autocomplete("division")(_division_autocomplete)
    test_results.autocomplete("division")(_division_autocomplete)
    test_standings.autocomplete("division")(_division_autocomplete)
    test_attendance.autocomplete("division")(_division_autocomplete)
    test_rsvp.autocomplete("division")(_division_autocomplete)
    test_verdict.autocomplete("division")(_division_autocomplete)
    test_verdict_banner.autocomplete("division")(_division_autocomplete)
    test_weather_p1.autocomplete("division")(_division_autocomplete)
    test_weather_p2.autocomplete("division")(_division_autocomplete)
    test_weather_p3.autocomplete("division")(_division_autocomplete)
    test_weather_mystery.autocomplete("division")(_division_autocomplete)

    async def _send_preview(
        self, interaction: discord.Interaction, *, title: str, context, outcomes
    ) -> None:
        """Reply with the pictures and what the render made do with, as the runner words it."""
        await send_preview(
            interaction,
            post_log=self.bot.output_router.post_log,
            title=title,
            context=context,
            outcomes=outcomes,
        )
