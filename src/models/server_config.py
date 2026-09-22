"""ServerConfig dataclass — per-guild bot configuration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ServerConfig:
    server_id: int
    interaction_role_id: int
    interaction_channel_id: int
    log_channel_id: int
    #: The role that holds the league admin tier. ``None`` where the league has not yet
    #: chosen one, which is every server configured before the role existed — a league
    #: admin command is refused while it stands rather than falling back to a Discord
    #: permission, a league that has not picked the role not having decided who may cancel
    #: its season. Set by ``/bot admin-role``, and by ``/bot init`` on a new server.
    league_admin_role_id: int | None = None
    test_mode_active: bool = field(default=False)
    #: The test-mode counterpart of signup nationality collection. While test mode is
    #: active this stands in for it, so a maintainer may preview a league that collects no
    #: nationality without disturbing the setting real signups run on. On by default, as
    #: the setting it parallels is.
    test_mode_nationality_required: bool = field(default=True)
    weather_module_enabled: bool = False
    signup_module_enabled: bool = False
    #: The league's two roles (issue #276). The **base role** is held by the league's
    #: members, and the **driver role** by its drivers — granted when a signup is approved and
    #: revoked when the driver returns to Not Signed Up. Both are the league's rather than
    #: the signup module's, so disabling signup keeps them; neither is part of the claim, and
    #: `/bot pack` clears them itself. ``None`` until `/bot base-role` or `/bot driver-role`
    #: sets one: they are required only while the signup module is enabled.
    base_role_id: int | None = None
    driver_role_id: int | None = None
