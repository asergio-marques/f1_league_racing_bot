"""Unit tests for rendering the points configuration listing (#200)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.points_config import SessionType
from utils.results_formatter import format_config_list


def test_format_config_list_names_each_config_and_what_it_carries():
    out = format_config_list(
        "this server",
        [
            ("Half Points", [SessionType.FEATURE_RACE]),
            ("Standard", [SessionType.SPRINT_RACE, SessionType.FEATURE_RACE]),
        ],
    )

    assert "**Standard**" in out
    assert "Sprint Race, Feature Race" in out
    assert "**Half Points**" in out
    assert "2 configurations." in out


def test_format_config_list_names_the_scope_it_read():
    """The reply must say which store answered, since the manager chose one."""
    assert "**Points configurations — this server**" in format_config_list("this server", [])
    assert "**Points configurations — season 4**" in format_config_list("season 4", [])


def test_format_config_list_calls_out_a_config_with_no_entries():
    """The trap #200 names — an empty config must not read as a complete one."""
    out = format_config_list("this server", [("Never Filled", [])])

    assert "no entries yet" in out
    assert "⚠️" in out


def test_format_config_list_on_an_empty_store_names_how_to_add_one():
    out = format_config_list("this server", [])

    assert "None yet" in out
    assert "/results config add" in out


def test_format_config_list_counts_one_config_in_the_singular():
    out = format_config_list("this server", [("Standard", [SessionType.FEATURE_RACE])])

    assert "1 configuration." in out
    assert "1 configurations." not in out


def test_format_config_list_labels_sessions_as_the_results_posts_do():
    """Reuses format_session_label, so a session reads the same wherever it appears."""
    out = format_config_list(
        "this server",
        [("Standard", [SessionType.SPRINT_QUALIFYING, SessionType.FEATURE_QUALIFYING])],
    )

    assert "Sprint Qualifying, Feature Qualifying" in out
