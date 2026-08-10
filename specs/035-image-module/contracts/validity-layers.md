# Contract: Validity Layers

**Feature**: 035-image-module | **Module**: `src/services/image_validity_service.py`

This is the extension point. The author's instruction was that what makes a template valid be left
open, defined incrementally in later sessions, and **wired into this**. This contract is what those
later sessions plug into.

Governed by Constitution Principle XIV.9. Read that first; this file is its implementation shape.

---

## The protocol

```python
class ValidityLayer(Protocol):
    number: int          # ordering — 1 is cheapest, runs first
    name: str            # appears in a report; must distinguish this layer's failures
    def applies_to(self, template_key: str) -> bool: ...
    def check(self, ctx: TemplateContext) -> LayerResult: ...
```

```python
@dataclass
class LayerResult:
    passed: bool
    reason: str | None   # required when passed is False; distinguishes this layer's failure modes
```

`applies_to` is what lets a layer be ratified **per image type**. Layer 2 will return `True` only
for templates whose field catalogue exists, so a type without one is checked to Layer 1 and
reported as such — never enforced against a catalogue that has not been written (XIV.9).

---

## The registry

```python
LAYERS: list[ValidityLayer] = [ResolutionLayer()]   # this increment
```

Evaluation, per template: run layers in `number` order, skipping those whose `applies_to` is false;
stop at the first failure; record the highest layer number actually applied as `depth_checked`.

Adding a layer is **one list entry plus one class**. If a later session finds itself editing a cog,
a command signature, `AspectStatus`, or the report renderer to add a layer, the stable-surface
invariant has been broken and the design has failed.

---

## Layer 1 — Resolution (implemented in this increment)

The only mandatory layer, applying to all fifteen templates (spec FR-028c).

| Check | Failure reason |
|-------|----------------|
| Path resolves inside the configured directory | `directory not found: <path>` or `file not found: <path>` |
| Parses as well-formed SVG | `not well-formed SVG: <parser detail>` |
| Root declares `width` and `height` | `template declares no canvas (missing width or height on root)` |

All three reasons must be **mutually distinguishable** — US2 acceptance scenario 4 requires a file
that exists but does not parse to read differently from one that is missing.

**Directory-level short-circuit**: when the template directory itself does not resolve, report that
once rather than fifteen file-not-found lines (spec Edge Cases). Every template still receives a
`ValidityReport`; they share one reason.

---

## Layers 2–4 — reserved, not implemented

Named here so the surface is settled before their definitions arrive.

| # | Name | Will check | Needs |
|---|------|-----------|-------|
| 2 | Catalogue conformance | Every field the image type requires is declared by `@id` | That type's field catalogue |
| 3 | Bounds declaration | Fields taking unbounded text declare `inline-size` or `shape-inside` | Catalogue + which fields are unbounded |
| 4 | Trial render | Template fills and rasterises against sample data | Sample data per type |

A later session implements one of these by writing the class and appending it to `LAYERS`. Nothing
else changes.

---

## The four invariants

From Principle XIV.9. Each has a test in `tests/unit/test_image_validity_layers.py` — they are the
executable form of the author's "must be wired into this as well".

### 1. Stable surface

Adding a layer changes neither the configuration commands, the three reported states, nor the
structure of `ValidityReport`. Only the set of reasons grows.

**Test**: register a synthetic Layer 2 in a fixture and assert the command surface and the
`ValidityReport` field set are byte-identical to before.

### 2. Specific attribution

Every layer names the individual template at fault, never the group (FR-032).

**Test**: invalidate only `weather_p3_sprint_template` and assert the report names phase 3, the
sprint variant, and reports the other five weather templates valid. Same for the qualifying/race
and drivers/constructors pairs.

### 3. Declared depth

A report states which layers were applied. A template that passed only Layer 1 is never presented
as though it passed a deeper check (FR-028b).

**Test**: assert `depth_checked == 1` for every template, and that the rendered `/images config
view` text contains the depth. A report that omits it fails.

### 4. No silent pass

An image type for which a deeper layer is not yet ratified is reported as checked to the depth
available, not as fully valid.

**Test**: with a synthetic Layer 2 whose `applies_to` is false for `calendar_template`, assert that
template reports `depth_checked == 1` while others report `2` — and that `calendar_template` is not
described as fully valid.

---

## Relationship to `AspectStatus`

`ValidityReport` is per template; `AspectStatus` is per aspect and aggregates 1, 2 or 6 of them.

An aspect is `ENABLED_INVALID` (FR-031) when its toggle is on **and** any of:

- any backing template's `ValidityReport.valid` is false;
- its `source_module` is disabled;
- the SVG-to-PNG converter is absent.

`blocking_reasons` carries one entry per cause, each naming the specific template or the specific
module — never "weather is invalid" (FR-032).

---

## What this contract deliberately does not fix

The *content* of Layers 2–4. That is the open question the author asked be left open, and settling
it here would defeat the purpose. What is fixed is the shape those definitions arrive in, so a
later session extends this feature rather than rewriting it.
