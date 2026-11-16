"""What a module hands back when core asks how a driver is named by their signup.

Core's `driver_names_read` hook is asked for a season and the drivers to name; the module that
holds the signups answers with these, so that core reads no signup record. The answer carries more
than the records: whether the league collects a driver's nationality is a setting of the same
module, and is handed back beside them even where no driver was asked for.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class SignupName:
    """What a driver's signup records of how they are named: the name they gave, their Discord
    username and their nationality. Each is None where the signup holds none."""

    name: str | None
    username: str | None
    nationality: str | None


@dataclass(frozen=True)
class SignupNames:
    """The answer to `driver_names_read`: each driver's signup, by profile id, and whether the
    league collects a driver's nationality at all.

    A driver with no signup is absent from *drivers*. *nationality_collected* is the module's own
    setting, handed back whether or not any driver was asked for.
    """

    drivers: Mapping[int, SignupName]
    nationality_collected: bool
