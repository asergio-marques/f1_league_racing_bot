"""ServerConfig dataclass — per-guild bot configuration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ServerConfig:
    server_id: int
    interaction_role_id: int
    interaction_channel_id: int
    log_channel_id: int
    test_mode_active: bool = field(default=False)
    #: The test-mode counterpart of signup nationality collection. While test mode is
    #: active this stands in for it, so a maintainer may preview a league that collects no
    #: nationality without disturbing the setting real signups run on. On by default, as
    #: the setting it parallels is.
    test_mode_nationality_required: bool = field(default=True)
    previous_season_number: int = 0
    weather_module_enabled: bool = False
    signup_module_enabled: bool = False
