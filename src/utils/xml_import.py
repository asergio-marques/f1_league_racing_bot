"""XML import utility — parse and validate XML points configuration payloads.

Requires lxml: pip install lxml  /  apt install python3-lxml
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lxml import etree

from models.points_config import SessionType

__all__ = [
    "XmlImportPayload",
    "XmlImportError",
    "parse_xml_payload",
    "validate_payload",
]

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class XmlImportPayload:
    """Parsed and structurally validated XML payload ready for DB import."""

    # session_type -> {position: points}
    positions: dict[SessionType, dict[int, int]] = field(default_factory=dict)
    # session_type -> (fl_points, fl_position_limit | None)
    fastest_laps: dict[SessionType, tuple[int, int | None]] = field(default_factory=dict)


class XmlImportError(Exception):
    """Raised when XML parsing or structural validation fails."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


# ---------------------------------------------------------------------------
# Reverse lookup: lowercased label → SessionType (built once at import time)
# ---------------------------------------------------------------------------

_SESSION_TYPE_BY_LABEL: dict[str, SessionType] = {
    st.label().lower(): st for st in SessionType
}

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


def parse_xml_payload(xml_text: str) -> tuple[XmlImportPayload, list[str]]:
    """Parse *xml_text* into an :class:`XmlImportPayload`.

    Returns ``(payload, warnings)`` where *warnings* is a list of non-fatal
    advisory messages (e.g. duplicate position ids).

    Raises :class:`XmlImportError` if any hard error is found (malformed XML,
    structural violations, unknown session type, invalid values).
    """
    # --- parse XML --------------------------------------------------------
    try:
        root = etree.fromstring(xml_text.encode(), parser=_XML_PARSER)
    except etree.XMLSyntaxError as exc:
        raise XmlImportError([f"XML syntax error: {exc}"]) from exc

    errors: list[str] = []
    warnings: list[str] = []

    positions: dict[SessionType, dict[int, int]] = {}
    fastest_laps: dict[SessionType, tuple[int, int | None]] = {}

    for session_el in root.findall("session"):
        # --- resolve <type> -----------------------------------------------
        type_el = session_el.find("type")
        if type_el is None or not (type_el.text or "").strip():
            errors.append("A <session> block is missing a <type> element.")
            continue

        raw_type = (type_el.text or "").strip().lower()
        session_type = _SESSION_TYPE_BY_LABEL.get(raw_type)
        if session_type is None:
            errors.append(
                f"Unknown session type {(type_el.text or '').strip()!r}. "
                f"Valid types: {', '.join(st.label() for st in SessionType)}."
            )
            continue

        # --- parse <position> elements ------------------------------------
        pos_dict: dict[int, int] = {}
        for pos_el in session_el.findall("position"):
            id_attr = pos_el.get("id", "").strip()
            try:
                pos_id = int(id_attr)
                if str(pos_id) != id_attr:
                    raise ValueError
            except ValueError:
                errors.append(
                    f"[{session_type.label()}] <position id={id_attr!r}> — "
                    f"id must be a positive integer."
                )
                continue
            if pos_id < 1:
                errors.append(
                    f"[{session_type.label()}] <position id={pos_id}> — "
                    f"id must be >= 1."
                )
                continue

            pts_text = (pos_el.text or "").strip()
            try:
                pts = int(pts_text)
                if str(pts) != pts_text:
                    raise ValueError
            except ValueError:
                errors.append(
                    f"[{session_type.label()}] <position id={pos_id}> — "
                    f"points value {pts_text!r} must be a non-negative integer."
                )
                continue
            if pts < 0:
                errors.append(
                    f"[{session_type.label()}] <position id={pos_id}> — "
                    f"points must be >= 0, got {pts}."
                )
                continue

            if pos_id in pos_dict:
                warnings.append(
                    f"[{session_type.label()}] Duplicate position id={pos_id}: "
                    f"previous value {pos_dict[pos_id]} overridden by {pts}."
                )
            pos_dict[pos_id] = pts

        # --- parse <fastest-lap> ------------------------------------------
        fl_el = session_el.find("fastest-lap")
        fl_entry: tuple[int, int | None] | None = None

        if fl_el is not None:
            if session_type.is_qualifying:
                errors.append(
                    f"[{session_type.label()}] Fastest-lap bonus is not permitted "
                    f"for qualifying sessions."
                )
            else:
                fl_pts_text = (fl_el.text or "").strip()
                try:
                    fl_pts = int(fl_pts_text)
                    if str(fl_pts) != fl_pts_text:
                        raise ValueError
                except ValueError:
                    errors.append(
                        f"[{session_type.label()}] <fastest-lap> — "
                        f"points value {fl_pts_text!r} must be a non-negative integer."
                    )
                    fl_pts = None  # type: ignore[assignment]

                if fl_pts is not None and fl_pts < 0:
                    errors.append(
                        f"[{session_type.label()}] <fastest-lap> — "
                        f"points must be >= 0, got {fl_pts}."
                    )
                    fl_pts = None  # type: ignore[assignment]

                limit_attr = fl_el.get("limit", "").strip()
                fl_limit: int | None = None
                if limit_attr:
                    try:
                        fl_limit = int(limit_attr)
                        if str(fl_limit) != limit_attr:
                            raise ValueError
                    except ValueError:
                        errors.append(
                            f"[{session_type.label()}] <fastest-lap limit={limit_attr!r}> — "
                            f"limit must be a positive integer."
                        )
                        fl_limit = None
                    else:
                        if fl_limit < 1:
                            errors.append(
                                f"[{session_type.label()}] <fastest-lap limit={fl_limit}> — "
                                f"limit must be >= 1."
                            )
                            fl_limit = None

                if fl_pts is not None:
                    fl_entry = (fl_pts, fl_limit)

        # --- include in payload only if there is something to upsert ------
        has_positions = bool(pos_dict)
        has_fl = fl_entry is not None
        if not has_positions and not has_fl:
            continue

        if has_positions:
            positions[session_type] = pos_dict
        if has_fl:
            fastest_laps[session_type] = fl_entry  # type: ignore[assignment]

    if errors:
        raise XmlImportError(errors)

    return XmlImportPayload(positions=positions, fastest_laps=fastest_laps), warnings


# ---------------------------------------------------------------------------
# Validator (monotonic ordering)
# ---------------------------------------------------------------------------


def validate_payload(payload: XmlImportPayload) -> list[str]:
    """Return a list of monotonic-ordering violations in *payload*.

    For each session type, position points must be non-increasing when sorted
    by position id ascending.  Zero-points entries do not break the chain
    (they are allowed to follow a zero or stay at zero), but a positive value
    that is greater-than-or-equal-to the preceding positive value is a
    violation.
    """
    errors: list[str] = []
    for session_type, pos_dict in payload.positions.items():
        sorted_pts = [pts for _, pts in sorted(pos_dict.items())]
        for i in range(len(sorted_pts) - 1):
            pts_current = sorted_pts[i]
            pts_next = sorted_pts[i + 1]
            if pts_next > 0 and pts_next >= pts_current:
                errors.append(
                    f"[{session_type.label()}] Points are not monotonically "
                    f"non-increasing: position {i + 1} has {pts_current} pts but "
                    f"position {i + 2} has {pts_next} pts."
                )
    return errors
