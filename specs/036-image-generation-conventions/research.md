# Phase 0 Research: Template Verification & Graphic Conventions

No `NEEDS CLARIFICATION` markers survived `/speckit-specify` — both were resolved by the author
and the constitution amended to match. What follows resolves the *design* unknowns the spec
leaves open, each against the code as it actually stands after 035.

---

## R1 — How a layer label is read from an SVG

**Decision**: resolve a field name through a `FieldIndex` that consults, in order, the `@id`
index and then an index of `inkscape:label` values restricted to layer groups. The Inkscape
namespace is `http://www.inkscape.org/namespaces/inkscape`; a layer is a `<g>` carrying
`inkscape:groupmode="layer"`, and its label is `inkscape:label`.

**Rationale**: FR-019 says "a layer whose label is the name of the field", so the label index
MUST be restricted to `groupmode="layer"`. Indexing every `inkscape:label` in the document would
sweep in the labels Inkscape writes on ordinary objects, where a manager has not deliberately
named anything, and would make a collision with a field name resolve to a shape nobody intended.
FR-020's precedence rule falls out of consulting the id index first.

**Alternatives considered**:

- *Index every `inkscape:label`.* Rejected: over-broad, as above.
- *Treat the label as an alias written into the id index.* Rejected: it loses the precedence rule
  when both exist, since whichever was written last would win.
- *Support other editors' label conventions.* Rejected: no evidence any is in use; the spec's
  justification names an SVG editor's layer label, and Inkscape is already the project's
  rasteriser.

**Consequence**: `svg_document.NSMAP` gains the Inkscape namespace, and `index_by_id` is
superseded by `FieldIndex`. Existing callers of `index_by_id` in `svg_fill.fill` move over
wholesale, which is what gives every operation the fallback at once.

---

## R2 — Validate-then-store, without duplicating the validity engine

**Decision**: `ImageConfigService` gains a method returning a **candidate** `ImageConfig` — the
stored config with one field overridden, not persisted. `_set_template_filename` evaluates the
candidate through the existing `evaluate_template`, and writes only if the report is valid.

**Rationale**: the validity engine already takes an `ImageConfig` and is pure and synchronous
(`evaluate_template(TemplateContext(config=..., template_key=...))`). Overriding one field on a
copy reuses it exactly, with no second code path that could disagree with `/images config view`.
Today's [`image_cog.py:158`](../../src/cogs/image_cog.py#L158) writes first and reads the report
back, which is the same call in the wrong order.

**Alternatives considered**:

- *Write, validate, roll back on failure.* Rejected: a concurrent `/images config view` could
  observe the bad value, and a crash between write and rollback leaves it stored — exactly the
  outcome FR-005 forbids.
- *A standalone "check this filename" function.* Rejected: a second path that can drift from the
  one `/season review` uses.

**Consequence**: `ImageConfig` must be copyable with an override. It is a dataclass, so
`dataclasses.replace` suffices; no new type.

---

## R3 — Naming a parse fault without leaking the parser's text

**Decision**: classify `etree.XMLSyntaxError` into a small set of named faults by inspecting the
exception's structured fields (`lineno`, `msg`), and render a sentence naming the file, the line
and the fault in the module's own words. Anything unrecognised becomes "the file is not
well-formed XML at line *N*", never the parser's string.

**Rationale**: FR-046 forbids surfacing the raw parser error, and the spec singles out the
double-hyphen-in-comment case as the readiest way to produce one. Today
[`svg_document.py:58`](../../src/utils/svg_document.py#L58) does `str(exc).split("\n")[0]`, which
is precisely the raw text. The faults worth naming, from the classes a hand-authored file
actually hits: a double hyphen inside a comment, an unclosed or mismatched tag, an undefined
entity, a stray `&`, and a bad encoding declaration.

**Alternatives considered**:

- *Pre-scan the file for the known traps before parsing.* Rejected: duplicates the parser badly
  and cannot cover the general case.
- *Keep the raw text as a trailing detail.* Rejected: the requirement is that a user never sees
  it. It goes to the application log instead, where an operator can still reach it.

---

## R4 — Where Layer 2 plugs in, and why it is safe to add now

**Decision**: add `CatalogueLayer` (`number = LAYER_CATALOGUE = 2`) to
`image_validity_service.LAYERS`. Its `applies_to(template_key)` returns **False** for any image
type whose catalogue declares no mandatory fields, so it is skipped rather than passed.

**Rationale**: the 035 design states that adding a layer must be "one class and one list entry",
and that editing a cog, a command signature, `ValidityReport` or the report renderer means the
design has failed. This addition touches neither. The `applies_to` gate is what satisfies XIV.9's
"no silent pass": `evaluate_template` records `depth` only for layers that actually ran, so a
type with an empty catalogue still reports depth 1, and `depth_summary` continues to say the
catalogue check was not applied.

**Alternatives considered**:

- *Have `applies_to` return True and pass trivially.* Rejected: it would report depth 2 for a
  type nothing was checked against — the silent pass XIV.9.4 forbids.
- *Wait until the first image type is specified.* Rejected: Layer 2 is the thing that makes
  FR-004 possible at all, and building it with the first graphic would mix cross-cutting
  machinery into a session scoped to one image.

---

## R5 — Reusing one parse across a fifteen-template verification

**Decision**: give `evaluate_all_templates` an in-call parse cache keyed by resolved path, passed
to the layers through `TemplateContext`. The cache lives for one evaluation and is not memoised
across calls.

**Rationale**: Layer 1 parses the file to check well-formedness and canvas; Layer 2 needs the
same tree to look for mandatory fields. Without sharing, a season review parses fifteen files
twice. Caching across calls would be wrong in the other direction — a manager edits a template
and re-runs `/images config view` expecting to see the change.

**Alternatives considered**:

- *A module-level cache with a TTL,* as `find_converter` uses. Rejected: the rasteriser's
  location does not change while the bot runs; a template's content does, constantly, and that is
  the whole point of re-checking.

---

## R6 — How the module knows whether a posting was commanded

**Decision**: an explicit `PostingOrigin` enum (`COMMANDED` / `SCHEDULED`) passed as a required
argument into the render-and-post entry point. It is never inferred.

**Rationale**: FR-029/FR-030 make this the switch between "fall back to text" and "reject and
explain", so getting it wrong is a user-visible defect in both directions. The obvious inference
— "is there a Discord `Interaction` in scope?" — is unreliable: a command can schedule work that
completes later, and the retry queue re-posts messages that originated from a command. A required
argument forces every call site to state which it is, and a new call site cannot default into the
wrong behaviour.

**Alternatives considered**:

- *Infer from the presence of an `Interaction`.* Rejected, as above.
- *Default to `SCHEDULED`.* Rejected: silently falling back to text is the failure mode that
  hides defects, so it must not be what an unconsidered call site gets.

**Consequence**: `OutputRouter` is untouched. The distinction belongs to the render entry point,
not to the transport, because it decides whether a posting happens at all.

---

## R7 — Where asset resolution lives, and the exact normalisation

**Decision**: a new pure module `src/utils/asset_resolver.py` holding `normalise(text)` and
`resolve_asset(directory, datum)`. The normalisation is adopted verbatim from the proof of
concept: trim, lowercase, NFKD-decompose, drop combining marks, replace each run of
non-alphanumeric characters with a single underscore, strip leading and trailing underscores.

**Rationale**: `normalize()` in `resources/poc/build_poc.py` documents itself as "the spec's
normalization", and every asset already shipped under `resources/` is named by it. This is the
rule the constitution now states at XIV.13 — the hyphenated form ratified earlier in this session
was an invention and has been withdrawn. Adopting the POC's *rule* is not the same as porting its
code: the function is nine lines and is rewritten with the project's own tests.

**Alternatives considered**:

- *Reuse `utils/paths.py`.* Rejected: that module is about containment of configured paths within
  the project root, a security concern, and mixing a naming rule into it muddles both.
- *Put it in `nationality_data.py`.* Rejected: flags are one of seven asset classes.

---

## R8 — The choke point for the division-capacity guard

**Decision**: `placement_service.assign_driver` is the single point at which a driver enters a
division, and is where the guard goes.

**Rationale**: a search for the ways a driver joins a division finds exactly one service method.
Guarding there covers the signup wizard, manual placement and bulk import alike, without wiring
the same check into three cogs.

**Consequence**: the guard reads capacities from the catalogue module and is inert while those
are empty (see Complexity Tracking in [plan.md](./plan.md)). It activates by data when the first
image type declares a capacity, with no further code change.

---

## R9 — What changes in already-delivered 035 code

Recorded so the work is visible rather than discovered during implementation. Both were called
out in the v3.0.0 sync impact report.

| File | Today | Required |
|---|---|---|
| [`utils/svg_document.py:58`](../../src/utils/svg_document.py#L58) | `SvgParseError(str(exc).split("\n")[0])` — the raw parser text | A named fault (R3) |
| [`cogs/image_cog.py:158`](../../src/cogs/image_cog.py#L158) | Writes the filename, then reports validity as a warning | Validate a candidate, write only on success (R2) |
| [`utils/svg_fill.py:81`](../../src/utils/svg_fill.py#L81) | `index_by_id` throughout | `FieldIndex` with the label fallback (R1) |
| [`models/image_constants.py:200`](../../src/models/image_constants.py#L200) | Three notice kinds | Adds `ASSET_FALLBACK_USED`, `OPTIONAL_FIELD_EMPTIED` |

Nothing in 035 contradicts the new rules beyond these. The six fill operations, the problem/notice
split, the text-bounds behaviour and the layered-validity surface were all built to rules this
feature does not change.
