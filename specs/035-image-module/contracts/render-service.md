# Contract: Render Service

**Feature**: 035-image-module | **Module**: `src/services/image_render_service.py`

The engine. Its only behavioural source is **Constitution Principle XIV**, which is prescriptive
enough to build from: it names the six permitted operations, fixes recolour to a merge into inline
`style`, requires the canvas be read from the template root, and specifies the wrap's descent and
floor. This contract fixes the public API and the problem/notice split XIV.4 requires.

---

## Layering

```
src/utils/font_metrics.py              pure    — no DB, no Discord
src/utils/svg_fill.py                  pure    — no DB, no Discord
src/services/image_render_service.py   async   — subprocess, DB, notices
```

The two `utils` modules take no database handle and no Discord object. That is what makes the
engine unit-testable without a bot, and it is the property to protect as the module grows.

---

## `src/utils/font_metrics.py` — pure

Indexes the faces installed on the host through `fontTools`, resolves a CSS `font-family` list to
the face a renderer would actually land on, and measures a string by summing advance widths.

**Required behaviours**:

- A field whose first declared family is absent is measured against the substitute **and raises a
  `FONT_SUBSTITUTED` notice** — never a problem (XIV.4). A host missing a template's preferred face
  still renders.
- The index is built once and cached for the process lifetime. Building it per render costs more
  than the rasterisation.
- Measurement is what the wrap and bound operations below depend on; it must be exact enough that a
  line declared to fit does fit. Test against known-width strings in a known face.

---

## `src/utils/svg_fill.py` — pure

### `fill(spec: FillSpec) -> FillResult`

The six operations of Principle XIV.2 and **no others**. The closed set is the contract.

| Operation | Target | Effect |
|-----------|--------|--------|
| Text fill | `<text>` / `<tspan>` by `@id` | Replace text content |
| Image fill | element by `@id` | Rewrite `xlink:href` to an asset path |
| Recolour | element by `@id` | Merge a `fill:` declaration into inline `style` |
| Group removal | group by `@id` | Remove the group and its subtree |
| Vertical crop | crop-point node by `@id` | Rewrite root `height` and `viewBox` to the node's `y` |
| Text wrap | `<text>` with `shape-inside` by `@id` | Break into `<tspan>` lines against the rectangle |

```python
@dataclass
class FillResult:
    svg: bytes
    canvas: tuple[int, int]      # read from the root (XIV.1); after any crop
    unresolved: list[str]        # non-empty ⇒ a PROBLEM (XIV.3)
    notices: list[RenderNotice]  # substitutions and truncations (XIV.4)
```

`fill` **raises nothing** for a data disagreement — it reports, and the caller decides. This is what
lets `/images test` distinguish a failed render from a degraded one.

### Invariants each requiring a unit test

Drawn directly from Principle XIV. These are the subtleties where a plausible implementation is
wrong:

1. **Recolour merges, never replaces** (XIV.2). Writing `style` wholesale discards declarations the
   template set on the same element. Test: an element carrying two declarations keeps the other one
   after a recolour.
2. **Recolour must be inline, not a presentation attribute** (XIV.2). A presentation attribute loses
   to the template's own stylesheet. Test: recolouring an element the stylesheet also targets wins.
3. **Recolour does not consume the field** (XIV.2). A recoloured field must still be filled, or the
   unresolved-field check stops being honest. Test: recolour alone leaves the field in `unresolved`.
4. **Canvas comes from the root** (XIV.1). No fixed canvas anywhere. Test: two templates declaring
   different sizes both render at their own.
5. **The crop rewrites the SVG**, not the rasteriser's export area, so it does not depend on which
   way up a rasteriser counts its coordinates. Test: root `height` and `viewBox` both updated.
6. **The crop moves the unresolved-field check.** A field the cut removed from the canvas is not a
   field left unfilled; a leftover id is a problem only when it sits above the cut. Test: an
   unaddressed field below the crop point does not fail the render.
7. **Wrap descends by half a pixel** until the lines fit, and at the floor of **half** the
   template-declared size cuts at a word boundary with an ellipsis and raises `WRAP_TRUNCATED`
   (XIV.5). Test: text that fits at the floor is not truncated; text that does not is, at a word
   boundary.
8. **Line height scales with the reduced size, and the admissible line count is recomputed at the
   reduced leading** (XIV.5). This is what makes the floor buy substantially more room than the same
   line count set smaller. Test: the line count at the floor exceeds the count at full size.
9. **`inline-size` cuts at a word boundary with an ellipsis** and raises
   `INLINE_SIZE_TRUNCATED` (XIV.5). This is the only bound on a Discord display name, which is of no
   length a league controls. Test: a long name is cut, ellipsised, and noticed.

---

## `src/services/image_render_service.py` — async

### `async def render(server_id, kind, data) -> RenderOutcome`

```python
@dataclass
class RenderOutcome:
    png_paths: list[Path]        # empty ⇔ problem is not None
    problem: str | None          # non-null ⇒ render aborted, caller falls back to text
    notices: list[RenderNotice]  # may be non-empty on success
```

**The two fields are the contract.** A caller cannot mistake a degraded render for a clean one, and
cannot receive a partial image — `png_paths` is empty whenever `problem` is set. This shape is what
makes Principle XIV.4 enforceable rather than aspirational.

**Problem** (aborts, empty `png_paths`): missing template · unparseable template · no declared
canvas · unresolved field · unknown field · missing asset · converter absent · rasteriser non-zero
exit · output exceeding Discord's attachment limit.

**Notice** (render survives): `FONT_SUBSTITUTED` · `WRAP_TRUNCATED` · `INLINE_SIZE_TRUNCATED`.

### Async discipline — non-negotiable

```python
result = fill(spec)                                  # fast, pure — inline is fine
png    = await asyncio.to_thread(rasterise, result)  # blocking subprocess — MUST be off-loop
```

Rasterisation is a blocking `subprocess` call of roughly a second. Running it on the event loop
stalls the scheduler, the retry worker and every in-flight interaction for its duration
(research R2). It passes every unit test and degrades the whole bot in production, which is why it
is stated here rather than left to reviewer vigilance.

### `async def converter_available() -> bool`

Locates the Inkscape CLI. **Must not rely on PATH alone** — probe the conventional install
locations for the platform and honour an `INKSCAPE` environment variable override. On the
development host the binary is installed but its PATH entry is broken, so a PATH-only probe reports
a fatal absence that is not real.

Result cached per process with a short TTL. It is probed at enable, at every `/images config view`
and at every `/season review` (FR-007) — too often to pay a filesystem walk each time, but not so
cached that installing Inkscape needs a bot restart to be noticed.

### Notice persistence

Every notice is written to `image_render_notices` and surfaced to the calculation log channel via
the existing `OutputRouter.post_log` (Principle V, XIV.4). `/images test` additionally lists them in
its ephemeral response (FR-038).

---

## `src/services/image_sample_data.py`

One sample dataset per test kind. Constraints:

- **Reads nothing live** (FR-036) — no season, division, round, team or driver query. `/images test`
  must work on a server with no season at all (SC-005).
- **Provokes each notice kind on purpose.** Sample data should include a name long enough to trip
  `inline-size` and prose long enough to reach the wrap floor, so the problem/notice distinction is
  exercised by the diagnostic rather than assumed.
- **Sourced from the `images test <type>` data in the working specification**, which is currently
  unreadable (research R11). Reconcile before implementation.
