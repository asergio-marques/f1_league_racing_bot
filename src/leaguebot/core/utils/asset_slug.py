"""The asset slug: the rule that turns a datum into the key its artwork is found by.

Core's own rule (Constitution XIV.13): core's team names are validated against it whether
or not the image module is on, and the image module's asset lookup asks core for it rather
than carrying its own copy.

The normalisation is the project's own, and it is **underscores**. It is the rule the
proof of concept already implements — ``normalize()`` in ``poc/build_poc.py``, whose
docstring calls it "the spec's normalization" — and the rule every asset already shipped
under ``resources/`` is named by.
"""
from __future__ import annotations

import unicodedata


def normalise(text: str) -> str:
    """The slug a datum resolves to, without its extension.

    Trim, lowercase, decompose and drop combining marks, replace each run of characters
    that is neither a letter nor a digit with a single underscore, strip leading and
    trailing underscores.

        ``Red Bull Racing`` → ``red_bull_racing``
        ``São Paulo``       → ``sao_paulo``
        ``Emilia-Romagna``  → ``emilia_romagna``

    Total and pure: any string in, a string out. A datum of nothing but punctuation
    normalises to the empty string, which resolves to no file — handled by the caller as
    an unresolved asset rather than as an error here.
    """
    decomposed = unicodedata.normalize("NFKD", (text or "").strip().lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))

    out: list[str] = []
    previous_underscore = False
    for ch in stripped:
        if ch.isalnum():
            out.append(ch)
            previous_underscore = False
        elif not previous_underscore:
            out.append("_")
            previous_underscore = True

    return "".join(out).strip("_")
