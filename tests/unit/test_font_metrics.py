"""Font resolution and measurement — T056.

A missing first-choice family is a **notice**, never a problem (Constitution XIV.4): a
host without a template's preferred face still renders.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.font_metrics import (  # noqa: E402
    ResolvedFont,
    font_index,
    measure,
    parse_font_family,
    resolve_family,
)


@pytest.fixture(scope="module")
def index():
    return font_index()


# ── Family list parsing ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "declaration,expected",
    [
        ("Inter", ["Inter"]),
        ("Inter, Arial, sans-serif", ["Inter", "Arial", "sans-serif"]),
        ("'Inter Tight', Arial", ["Inter Tight", "Arial"]),
        ('"Inter Tight" , Arial', ["Inter Tight", "Arial"]),
        (None, []),
        ("", []),
    ],
)
def test_family_lists_are_parsed_in_order(declaration, expected):
    assert parse_font_family(declaration) == expected


# ── Resolution and substitution ───────────────────────────────────────────


def test_an_installed_family_resolves_without_substitution(index):
    installed = next(iter(index))
    resolved = resolve_family(installed)
    assert resolved.substituted is False
    assert resolved.path is not None


def test_a_missing_first_choice_falls_through_to_the_next(index):
    """The face a renderer would actually land on, not the one that was asked for."""
    installed = next(name for name in index if " " not in name)
    resolved = resolve_family(f"NoSuchFaceAnywhere, {installed}")

    assert resolved.requested == "NoSuchFaceAnywhere"
    assert resolved.family.lower() == installed.lower()
    assert resolved.substituted is True


def test_a_wholly_absent_family_still_resolves_to_something(index):
    """Resolution never fails; it substitutes and says so."""
    resolved = resolve_family("NoSuchFaceAnywhere")
    assert resolved.requested == "NoSuchFaceAnywhere"
    assert resolved.substituted is True


def test_generic_family_resolves_and_counts_as_substitution():
    resolved = resolve_family("sans-serif")
    assert resolved.substituted is True


def test_index_is_built_once_per_process():
    """Rebuilding per render would cost more than the rasterisation."""
    first = font_index()
    second = font_index()
    assert first is second


def test_index_is_non_empty_on_a_real_host(index):
    assert len(index) > 0


# ── Measurement ───────────────────────────────────────────────────────────


def test_empty_string_measures_zero(index):
    assert measure("", resolve_family(next(iter(index))), 20) == 0.0


def test_measurement_scales_linearly_with_size(index):
    resolved = resolve_family(next(iter(index)))
    at_10 = measure("Verstappen", resolved, 10)
    at_20 = measure("Verstappen", resolved, 20)
    assert at_20 == pytest.approx(at_10 * 2, rel=1e-6)


def test_a_longer_string_is_wider(index):
    resolved = resolve_family(next(iter(index)))
    assert measure("Verstappen", resolved, 20) > measure("Ver", resolved, 20)


def test_measurement_is_proportional_not_monospaced(index):
    """A proportional face must not measure 'iii' and 'WWW' the same."""
    resolved = resolve_family("Arial")
    if resolved.path is None:
        pytest.skip("Arial not installed on this host")
    assert measure("WWW", resolved, 20) > measure("iii", resolved, 20)


def test_a_line_declared_to_fit_does_fit(index):
    """The property the wrap depends on: measurement must be tight enough to trust."""
    resolved = resolve_family("Arial")
    if resolved.path is None:
        pytest.skip("Arial not installed on this host")

    text = "The stewards reviewed the incident"
    width = measure(text, resolved, 20)

    # Adding any character must exceed the measured width.
    assert measure(text + "x", resolved, 20) > width
    # And the prefix must be strictly narrower.
    assert measure(text[:-1], resolved, 20) < width


def test_measurement_falls_back_rather_than_raising():
    """An unresolvable face still yields an estimate — a wrap must never crash."""
    unresolvable = ResolvedFont(None, None, "NoSuchFace", substituted=True)
    width = measure("Verstappen", unresolvable, 20)
    assert width > 0


def test_unknown_glyphs_do_not_raise(index):
    resolved = resolve_family(next(iter(index)))
    assert measure("中文\U0001f600", resolved, 20) >= 0


# ══════════════════════════════════════════════════════════════════════════
# 043 — the face measured is the one declared, and the measurement errs narrow
#
# specs/043-verdicts-image-generation/contracts/text-wrapping.md § Measurement.
# The whole line budget of a wrapped field rests on these two.
# ══════════════════════════════════════════════════════════════════════════


def _is_monospace(path) -> bool:
    """Whether *path* is a fixed-pitch face.

    Asked of the font rather than of its name: "Liberation Mono" is easy to spot, "Courier"
    and "Consolas" are not, and a family is free to call itself anything.
    """
    from fontTools.ttLib import TTFont  # noqa: PLC0415

    try:
        font = TTFont(path, fontNumber=0, lazy=True)
    except Exception:  # noqa: BLE001
        return False
    try:
        if bool(getattr(font["post"], "isFixedPitch", 0)):
            return True
        # PANOSE bProportion 9 is "monospaced" for the Latin text family.
        return int(getattr(font["OS/2"].panose, "bProportion", 0)) == 9
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            font.close()
        except Exception:  # noqa: BLE001
            pass


def _a_family_with_two_weights():
    """A proportional family this host carries at both a regular and a bold weight, or None.

    Two things this must not do. It must not pick a **monospace** family: bold and regular
    faces of one share their advance widths by definition, so "bold measures wider" is false
    there and says nothing about the code under test. And it must not depend on the order
    `face_index()` happens to be built in, which follows `rglob` and so the filesystem — a
    host whose first two-weight family is Liberation Mono would fail where another passes,
    which is precisely how this went unnoticed until it ran on a Raspberry Pi.
    """
    from utils.font_metrics import face_index  # noqa: PLC0415

    for (family, italic), faces in sorted(face_index().items()):
        if italic:
            continue
        weights = {weight for weight, _width, _path in faces}
        if not (any(w < 600 for w in weights) and any(w >= 600 for w in weights)):
            continue
        if any(_is_monospace(path) for _weight, _width, path in faces):
            continue
        return family
    return None


def test_a_bold_declaration_resolves_to_a_heavier_face_than_a_regular_one():
    """Measuring a bold field against the regular face admits lines that do not fit."""
    family = _a_family_with_two_weights()
    if family is None:
        pytest.skip("no family on this host carries both a regular and a bold face")

    regular = resolve_family(family, bold=False)
    bold = resolve_family(family, bold=True)

    assert regular.path != bold.path, "weight must select among a family's faces"
    sample = "Handgloves reviewed mixing quartz"
    assert measure(sample, bold, 40.0) > measure(sample, regular, 40.0)


def test_the_regular_face_chosen_is_the_nearest_to_400_not_the_first_alphabetically():
    """DejaVuSans-ExtraLight sorts before DejaVuSans and is materially narrower."""
    from utils.font_metrics import face_index  # noqa: PLC0415

    for (family, italic), faces in sorted(face_index().items()):
        if italic or len(faces) < 2:
            continue
        normal = [face for face in faces if face[0] < 600 and face[1] == 5]
        if len({face[0] for face in normal}) < 2:
            continue
        chosen = resolve_family(family, bold=False).path
        nearest = min(
            normal, key=lambda face: (abs(face[0] - 400), abs(face[1] - 5), str(face[2]))
        )[2]
        assert chosen == nearest
        return
    pytest.skip("no family on this host carries two distinct non-bold weights")


@pytest.mark.rasteriser
def test_measurement_errs_narrow_against_what_the_rasteriser_draws():
    """A line the measurement admits must be a line the canvas holds (XIV.5).

    Compared against the converter's own query rather than against a guess: it reports the
    drawn width of the element, kerning and shaping included, which is exactly the number
    the measurement must not fall below.
    """
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from services.image_render_service import find_converter  # noqa: PLC0415

    executable = find_converter()

    family = "DejaVu Sans"
    resolved = resolve_family(family, bold=False)
    if resolved.substituted or resolved.path is None:
        pytest.skip(f"{family} is not installed on this host")

    sample = "Handgloves reviewed mixing quartz"
    size = 40.0
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="200">'
        f'<text id="probe" x="0" y="100" '
        f'style="font-family:{family};font-size:{size:g}px">{sample}</text></svg>'
    )

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "probe.svg"
        path.write_text(svg, encoding="utf-8")
        completed = subprocess.run(
            [executable, str(path), "--query-id=probe", "--query-width"],
            capture_output=True,
            text=True,
            timeout=180,
        )

    if completed.returncode != 0 or not completed.stdout.strip():
        pytest.skip("the rasteriser could not report a drawn width on this host")

    drawn = float(completed.stdout.strip())
    measured = measure(sample, resolved, size)

    assert measured >= drawn, (
        f"measurement must err narrow: measured {measured:.2f}px but the rasteriser "
        f"draws {drawn:.2f}px, so a line admitted here would overrun the canvas"
    )


def test_a_condensed_face_never_stands_in_for_its_normal_sibling():
    """Arial Narrow declares "Arial" as its typographic family (name ID 16).

    Indexed on family alone it can win the "Arial" slot outright, and every line is then
    measured narrower than the rasteriser draws it — the direction "errs narrow" forbids.
    Found by rendering a verdict to PNG and watching its justification run off the canvas,
    which is the whole reason XIV.14 requires the check be made against the raster.
    """
    from utils.font_metrics import face_index  # noqa: PLC0415

    for (family, italic), faces in face_index().items():
        if italic:
            continue
        widths = {width for _weight, width, _path in faces}
        if 5 not in widths or all(width == 5 for width in widths):
            continue

        chosen = resolve_family(family, bold=False).path
        chosen_width = next(
            width for _weight, width, path in faces if path == chosen
        )
        assert chosen_width == 5, (
            f"{family} resolved to a face of width class {chosen_width}, "
            f"which is narrower or wider than the normal face the renderer will use"
        )
        return

    pytest.skip("no family on this host carries both a normal and a condensed face")
