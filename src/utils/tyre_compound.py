"""The tyre compounds a session may be run on, and the spellings a steward may use.

The five compounds are a **closed set the module itself defines** (Constitution XIV.13,
v7.8.0), in the same sense as the three directions a standing position can move and the
eight weathers a forecast can draw. A league did not choose them — the game offers five and
no sixth — so the module ships an icon for each under `resources/defaults/tyres/`, and a
league that has drawn none still gets five correct pictures rather than five placeholders.

**Why this lives in `utils/` and not in `models/image_constants.py`.** The compound is a
results datum that an image happens to draw, not a fact about image assets, and
`image_constants` deliberately does not restate the vocabulary of any closed class: it
asserts closure at the level of the *class*, leaving each vocabulary with the module that
owns it, precisely so the two cannot drift. This is the tyre's such module, as
`math_utils` is the weather's and `standings_service` the position changes'. It sits in
`utils/` rather than `models/` because it reuses :func:`asset_resolver.normalise`, and
nothing under `models/` imports from `utils/`.

**Why the alias table is keyed on the normalised form.** Constitution XIV.13 requires one
normalisation rule for every class, and reusing it here makes the spelling a steward may
type and the filename that spelling resolves to provably the same rule. `ExWets`,
`ex wets` and `EX-WETS` are therefore one alias and not three, and no second table of
punctuation variants can fall out of step with the first.
"""
from __future__ import annotations

from utils.asset_resolver import normalise

#: The five compounds, in the order the game offers them and every message lists them.
TYRE_COMPOUNDS: tuple[str, ...] = ("Soft", "Medium", "Hard", "Intermediate", "Wet")

#: Normalised spelling -> canonical compound. Every canonical name is its own alias, so a
#: value already canonical round-trips without a special case.
#:
#: The set is what a steward actually writes on a submission line: the full word, its
#: plural, and the single letter the game's own HUD uses. `intermediate` additionally
#: answers to `inter`, and `wet` to `exwets`, those being what the in-game tyre is called
#: as often as not.
TYRE_COMPOUND_ALIASES: dict[str, str] = {
    normalise(alias): canonical
    for canonical, aliases in {
        "Soft": ("Soft", "Softs", "S"),
        "Medium": ("Medium", "Mediums", "M"),
        "Hard": ("Hard", "Hards", "H"),
        "Intermediate": ("Intermediate", "Intermediates", "Inter", "Inters", "I"),
        "Wet": ("Wet", "Wets", "ExWet", "ExWets", "Ex Wet", "Ex Wets", "W"),
    }.items()
    for alias in aliases
}

#: What a submission writes where no compound was recorded, beside an empty field.
#:
#: An absent tyre is a state the module depicts rather than a gap it reports — the
#: qualifying catalogue declares the field `fallback_when_absent` — so these are accepted
#: and stored as null. A closed vocabulary constrains what a compound may **be**, never
#: whether one was recorded.
_NOT_RECORDED: frozenset[str] = frozenset({"", "n_a", "na"})


def records_no_tyre(text: str | None) -> bool:
    """Whether *text* records no compound at all, rather than naming one wrongly."""
    return normalise(text or "") in _NOT_RECORDED


def canonicalise_tyre(text: str | None) -> str | None:
    """The compound *text* names, in its canonical spelling, or None where it names none.

    None answers both an unrecognised compound and a field recording none, which the
    caller tells apart with :func:`records_no_tyre` — the two differ in what they oblige,
    an unrecognised compound being a submission to reject and an absent one a state to
    draw.
    """
    return TYRE_COMPOUND_ALIASES.get(normalise(text or ""))


def tyre_compound_list() -> str:
    """The five compounds as a message names them — `Soft, Medium, Hard, ...`."""
    return ", ".join(TYRE_COMPOUNDS)
