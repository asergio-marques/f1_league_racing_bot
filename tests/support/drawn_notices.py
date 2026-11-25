"""A picture's render notices as image hands them back to core and the modules (#635): grouped
by kind beneath a heading in plain words, a lone notice naming its field. The job's log line and
the command's reply carry them as they came, and never bullet them a second time."""

from __future__ import annotations

#: The words of the one notice a picture is drawn with below, to find the log line holding it by.
NOTICE = "round 12 did not fit"

#: That notice as the drawing hands it back: its kind's heading, then the notice naming its field.
GROUPED_NOTICE = ("  Text set smaller", f"    • `round_12_track` — {NOTICE}")


def logged_block(drawn: str, notices: tuple[str, ...] = GROUPED_NOTICE) -> str:
    """The notice block a job's log line ends in for the picture *drawn* ("calendar — Season 3
    Pro"): its heading, then the grouped *notices* exactly as the drawing handed them back."""
    return "\n".join([f"Image render notices — {drawn}:", *notices])


def told_section(
    label: str, link: str | None, notices: tuple[str, ...] = GROUPED_NOTICE
) -> str:
    """The "Image notices" section a reply ends in for one picture, *label*, drawn with the
    grouped *notices*, linked to *link*: the framework's own section, which the reply carries
    whole."""
    from leaguebot.framework.models.change import DrawnNotices
    from leaguebot.framework.utils.log_lines import drawn_notices_section

    return drawn_notices_section([DrawnNotices(label=label, notices=notices, link=link)])
