# Contract: Per-Class Asset Aspect

**Feature**: `044-track-imagery-split`
**Implemented in**: `src/models/image_constants.py` (`ASSET_CLASS_ASPECTS`), `src/services/image_validity_service.py` (Layer 2)
**Governs**: Constitution XIV.6 — "One class carries one aspect, and every slot of that class MUST carry it"

**This is the only new mechanism in the increment.** Nothing enforces per-class aspect today; XIV.6
was silent on it until v5.0.0 and now obliges Layer 2 to refuse a template that breaks it.

---

## The rule

One class of image carries one aspect, and **every slot of that class carries it — on every
template, of every image type**.

| Class | Aspect |
|---|---|
| `flag` | 3:2 |
| `track` | 1:1 |
| `team`, `driver` | 1:1 |
| `marker`, `weather`, `tyre` | 1:1 |

**The ratio binds, not the pixel size.** A template may draw a flag slot at any dimensions it likes
so long as they are 3:2.

**Two classes need not match each other, and flag and track deliberately do not.** The constraint
is *within* a class, never *across* two. A template drawing both — which only the calendar and the
check-in graphic may — places two slots of differing shape, and that is the business of whoever
authors it.

## Why it must be enforced rather than documented

A league authors **one file per datum of a class**, and XIV.6 forbids the generator to pad. A class
serving slots of two aspects would letterbox that one file wherever it did not match, and **no
artwork the league could supply would answer it** — the same `united_kingdom.svg` cannot be correct
in a 3:2 slot and a 1:1 one at once.

The failure is also invisible where a league would look for it. The SVG is well-formed, the id is
right, the file resolves; the distortion appears only in the raster, which is why XIV.14 exists and
why a documentation-only rule would not have held.

## The check

**Where**: Layer 2, `CatalogueLayer`, in `image_validity_service.py`. Not a new layer — Layer 2 is
the layer holding the catalogue, and the check needs the catalogue to know a field's class. Layer 1
is resolution and canvas only. Adding a layer would engage XIV.9's ratification requirement for no
benefit.

**What it does**, for each image field the type's catalogue names:

1. Read the slot's declared width and height from the template.
2. Look up the class's expected aspect in `ASSET_CLASS_ASPECTS`.
3. Compare `width / height` against it within tolerance.
4. On mismatch: a **problem**, naming the field, the class, the expected aspect and the found one.

**Tolerance is required, not a convenience.** Template geometry is authored in Inkscape and carries
floating-point values — `120.00001 / 80` is not `1.5` in binary floating point, and an exact
comparison would reject every template a human drew. A **1% relative tolerance** admits honest
authoring while still catching a square slot given a 3:2 flag, which is a 50% error. There is no
plausible authoring mistake that lands inside 1%.

**A slot declaring no usable width or height** is already a Layer 2 fault under XIV.5's rules for a
rectangle declaring no usable dimensions; this check does not restate it and does not divide by
zero — it defers to the existing fault.

## Reporting

A mismatch is a **problem** and not a notice: the render is abandoned. It is a structural fault in
the template, caught when the template is configured and at season review, which is where XIV.9 puts
structural faults — not at generation, where a league would learn per posting.

The message names all four of field, class, expected and found, because a league seeing only "wrong
aspect" cannot tell whether to reshape the slot or re-author the artwork.

## What this check does *not* do

- **It does not check the asset files.** It checks *slots*. A league's own artwork authored at the
  wrong aspect is caught by XIV.14's PNG verification and by the eye, not here. Checking every file
  in every configured directory at validation time would be a filesystem sweep for a fault the
  league sees immediately.
- **It does not fix anything.** No padding, no letterboxing, no scaling. XIV.6 forbids the generator
  to pad, and a checker that repaired templates would be deciding what a graphic looks like.
- **It does not fix the aspects in governance.** XIV.6 leaves the numbers out deliberately —
  "The aspect a class carries is not fixed by this Principle." `ASSET_CLASS_ASPECTS` is the
  authority and `resources/README.md` is its league-facing statement. Changing one is a change to
  every template declaring that class's slots.

## Tests

All offline; none needs a bot or a rasteriser.

- A template whose flag slot is 3:2 passes.
- A template whose flag slot is square is refused, and the message names field, class, expected and
  found.
- A template whose track slot is 3:2 is refused.
- A slot at 120.00001 × 80 passes — the tolerance case, which is the one a naive implementation
  fails.
- Each of the seven re-authored packaged templates passes for every image field it declares. This
  is what proves the re-geometry of the four converted types actually happened.
