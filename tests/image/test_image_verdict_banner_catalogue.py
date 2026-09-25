"""The verdict banner field catalogue, and the file the module ships for it.

The banner is the header of a batch of verdicts — one graphic above the run of cards a
review produced, naming the round they were taken upon. It is the smallest catalogue of the
module: two mandatory fields, no collection, no singleton, and nobody named.

Two of its rules are decisions rather than deductions, and each is pinned here so that a
later reader does not tune it away:

* **It declares no ``session_name``.** One review closes on a whole round and may sanction a
  qualifying entry and a race entry in the same breath, so a banner naming one session would
  misname half the cards beneath it.
* **The round takes the headline and the grand prix the line under it**, which is the
  opposite way round from a forecast. The round is the only thing on the banner that is
  always known.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_catalogues import (  # noqa: E402
    DIVISION_LOGO_FIELD,
    catalogue_for,
)
from models.image_constants import (  # noqa: E402
    ASPECTS,
    ASPECT_LABELS,
    ASPECT_SOURCE_MODULE,
    ASPECT_TEMPLATES,
    LIVE_POSTING_ASPECTS,
    PREVIEW_KINDS,
    TEMPLATE_COLUMNS,
    TEMPLATE_COMMAND_NAMES,
    TEMPLATE_LABELS,
    TEST_KIND_TEMPLATES,
)
from utils.image_naming import IMAGE_SUBJECTS, subject_for_template  # noqa: E402
from utils.svg_document import FieldIndex, parse_svg_bytes  # noqa: E402

KEY = "verdict_banner_template"
ASPECT = "verdict_banner"

TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "resources" / "defaults" / "templates" / "verdict_banner_template.svg"
)

EXPECTED_MANDATORY = {"division_name", "round_number"}

EXPECTED_OPTIONAL = {
    "season_number",
    "season_number_group",
    "division_tier",
    "division_tier_group",
    "race_name",
    "race_name_group",
    "country_name",
    "country_name_group",
    "track_flag",
    "track_flag_group",
    DIVISION_LOGO_FIELD,
}


def _declared() -> set[str]:
    return set(FieldIndex(parse_svg_bytes(TEMPLATE.read_bytes())).declared())


# ── The catalogue ─────────────────────────────────────────────────────────

def test_the_catalogue_holds_exactly_the_two_facts_a_banner_must_carry():
    catalogue = catalogue_for(KEY)
    assert set(catalogue.mandatory) == EXPECTED_MANDATORY
    assert set(catalogue.optional) == EXPECTED_OPTIONAL


def test_the_banner_declares_no_collection_and_no_singleton():
    """It draws no verdict and knows of no driver: the cards do that."""
    catalogue = catalogue_for(KEY)
    assert catalogue.rows is None
    assert catalogue.columns is None
    assert catalogue.singleton is None


def test_the_banner_names_no_session():
    """A review may sanction a qualifying entry and a race entry in one breath.

    The round is as fine as a banner heading a whole batch may be, so `session_name` is
    absent from the catalogue rather than optional in it — a template declaring it would be
    told the field is unknown.
    """
    catalogue = catalogue_for(KEY)
    assert "session_name" not in catalogue.mandatory
    assert "session_name" not in catalogue.optional


def test_the_flag_resolves_in_the_flag_directory():
    assert catalogue_for(KEY).assets["track_flag"] == "flag"


# ── The tables that make it a ninth aspect ────────────────────────────────

def test_the_banner_is_an_aspect_of_its_own():
    """Not a second template of `verdicts`.

    A league may draw cards without banners or banners without cards, and an unusable
    banner file cannot stop a verdict being posted.
    """
    assert ASPECT in ASPECTS
    assert ASPECT_TEMPLATES[ASPECT] == (KEY,)
    assert ASPECT_TEMPLATES["verdicts"] == ("verdicts_template",)


def test_the_banner_is_wired_into_every_registry_the_module_reads():
    assert TEMPLATE_COLUMNS[KEY] == "verdict_banner_template.svg"
    assert TEMPLATE_COMMAND_NAMES[KEY] == "verdict-banner"
    assert TEMPLATE_LABELS[KEY] == "Verdict banner"
    assert ASPECT_LABELS[ASPECT] == "Verdict banner"
    assert ASPECT_SOURCE_MODULE[ASPECT] == "results"
    assert ASPECT in LIVE_POSTING_ASPECTS
    assert TEST_KIND_TEMPLATES["verdict-banner"] == (KEY,)
    assert PREVIEW_KINDS["verdict-banner"]["needs_round"] is True
    assert PREVIEW_KINDS["verdict-banner"]["draws_roster"] is False


def test_every_template_key_is_carried_by_every_table():
    """The registries are hand-maintained and a sixteenth is sixteen chances to miss one."""
    keys = set(TEMPLATE_COLUMNS)
    assert set(TEMPLATE_COMMAND_NAMES) == keys
    assert set(TEMPLATE_LABELS) == keys
    assert {key for keys_ in ASPECT_TEMPLATES.values() for key in keys_} == keys
    assert set(IMAGE_SUBJECTS) == keys
    assert set(ASPECT_TEMPLATES) == set(ASPECTS) == set(ASPECT_LABELS)
    assert set(ASPECT_SOURCE_MODULE) == set(ASPECTS)


def test_a_banner_is_named_after_what_it_is_of():
    assert subject_for_template(KEY) == "verdict_banner"


# ── The shipped file ──────────────────────────────────────────────────────

def test_the_shipped_banner_declares_every_mandatory_field():
    assert EXPECTED_MANDATORY <= _declared()


def test_the_shipped_banner_declares_nothing_the_catalogue_does_not_know():
    catalogue = catalogue_for(KEY)
    known = set(catalogue.mandatory) | set(catalogue.optional)
    assert _declared() <= known


def test_the_shipped_banner_declines_the_country_name_and_the_division_logo():
    """Both stay in the catalogue for a league that wants them.

    Nothing the module ships declares a logo slot, and the banner writes its country as a
    flag rather than as a word — the same split the forecasts already make.
    """
    declared = _declared()
    assert "country_name" not in declared
    assert DIVISION_LOGO_FIELD not in declared


def test_the_shipped_banner_declares_no_colour_slot():
    """A declared slot left unset blocks its aspect once per-tier colours are on.

    Shipping one would make every league set a colour before the banner could post, which
    is why none of the sixteen declares one.
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    assert not re.search(r"colour-(fill|stroke|stop)-", text)


def test_the_round_takes_the_headline_and_the_grand_prix_the_line_under_it():
    """The mandatory fact holds the prominent slot, so the optional one may simply go.

    Built the other way round — the grand prix in the headline, as a forecast has it — a
    round whose circuit matches no track record lost its heading entirely and left the band
    all but empty. Do not swap these back.
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    headline = re.search(r'<text class="track"[^>]*y="(\d+)"[^>]*>(.*?)</text>', text, re.S)
    assert headline is not None
    assert 'id="round_number"' in headline.group(2)

    grand_prix = re.search(r'<text id="race_name"[^>]*class="sub"[^>]*y="(\d+)"', text)
    assert grand_prix is not None
    assert int(grand_prix.group(1)) > int(headline.group(1))


def test_the_optional_fields_each_carry_a_removable_group():
    """A value that cannot be determined takes its whole block away, not just its text."""
    declared = _declared()
    for field in ("season_number", "division_tier", "race_name", "track_flag"):
        assert f"{field}_group" in declared, field
