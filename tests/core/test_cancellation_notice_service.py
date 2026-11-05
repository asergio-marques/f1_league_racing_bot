"""What core keeps of a round's, a division's or a season's cancellation (#175, #283).

Each module words, finds its channel for and posts its own notice of a cancellation, and its tests
pin that (`tests/<module>/test_cancellation_notice.py`); the check-in record a round called off
writes in the log is attendance's, and so are its tests (`tests/attendance/test_check_in_record.py`).
Core keeps its own: the failures a cancellation names in its reply and its log line.
"""
from __future__ import annotations

from leaguebot.core.services import cancellation_notice_service as cns

# ── What could not be notified is named ────────────────────────────────────


def test_failures_are_named_for_the_reply():
    lines = cns.failure_lines([cns.NoticeFailure("Pro", "results channel", "no channel is set")])
    assert "Not notified" in lines
    assert "**Pro** — results channel: no channel is set" in lines
    assert cns.failure_lines([]) == ""


# ── The reply fits one message; the log carries everything ─────────────────


def test_a_long_list_of_failures_is_summed_up_in_the_reply():
    failures = [
        cns.NoticeFailure(f"Division {n}", "results channel", "x" * 500) for n in range(30)
    ]
    lines = cns.failure_lines(failures)
    assert len(lines) < 2000
    assert "and 22 more" in lines
    assert lines.count("  • ") == cns.MAX_LINES + 1


def test_the_log_names_every_failure_in_full():
    failures = [cns.NoticeFailure(f"Division {n}", "results channel", "gone") for n in range(30)]
    logged = cns.failure_log_lines(failures)
