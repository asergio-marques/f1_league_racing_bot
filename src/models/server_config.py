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
    previous_season_number: int = 0
