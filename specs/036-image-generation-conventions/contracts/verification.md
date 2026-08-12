# Contract: The Three Verification Moments

The same checks, applied at three moments, against different amounts of information.

| Moment | Trigger | Checks | On failure |
|---|---|---|---|
| **Configuration** | `/images template <kind>` | FR-001 … FR-004 | Reject the command; stored value unchanged |
| **Season review** | `/season review`, module enabled | FR-002 … FR-004 over all fifteen | Report every template at fault; refuse nothing |
| **Season approval** | `/season approve`, module enabled | the same evaluation | Block approval; name every template at fault |
| **Generation** | immediately before any render | FR-004 again, **plus** the data (FR-010, FR-011) | No image; outcome depends on posting origin |

## Configuration

Order matters — cheapest first, and no filesystem access until the name is plausible:

```text
1. FR-001  name ends ".svg" (case-insensitive)      → EXTENSION
2. FR-002  file exists at directory + name          → NOT_FOUND      (names the full path searched)
3. FR-003  parses as SVG, root is <svg>             → NOT_SVG        (named fault, never raw parser text)
   ─────── existing Layer 1 also checks the canvas declaration
4. FR-004  every mandatory field addressable        → MISSING_MANDATORY_FIELD (names each)
   ─────── skipped when the type's catalogue is empty; reported as unchecked, not passed
```

All four run against a **candidate** configuration that is not persisted. The stored value is
written only after all four pass (FR-005). A rejection names the file and the fault (FR-006), and
is logged to the calculation log like any other configuration event.

The check does not run when the module is disabled — the command already guards on that.

## Season review and approval

Applies checks 2–4 to all fifteen templates, from **one** evaluation serving both commands so the
two surfaces cannot disagree. Check 1 cannot fail here: a stored filename was validated when it
was stored.

`/season review` **reports**; `/season approve` **blocks**. The review displays a pending
configuration and commits nothing, so it has nothing to refuse; the approval is where the season
is stopped, beside the existing results, points and signup prerequisites (FR-008a).

- Each template gets its **own** report with its **own** reason. A report naming a group of
  templates rather than the one at fault does not satisfy FR-008.
- Where the template *directory* itself does not resolve, all fifteen share that one reason
  rather than producing fifteen near-identical file-not-found lines. Each still receives a report,
  so the caller's rendering is unchanged. *(Existing 035 behaviour, retained.)*
- The review parses each file once and shares the tree with Layer 2 (research R5).
- Any failure blocks approval, alongside the existing R&S, points and signup gates.
- With the module **disabled**, neither command verifies anything and no finding appears (FR-009).

The existing informational image section in `/season review` is retained and extended to name
each failing template; the *blocking* gate is added to `/season approve`.

## Generation

Re-checks because the data has moved since the template was configured:

| Check | Fatal? |
|---|---|
| Mandatory field absent from template (FR-012) | Yes |
| Mandatory value cannot be determined (FR-011) | Yes |
| Data supplies a field the template does not declare | Yes |
| Row data exceeds declared capacity (FR-028) | Yes |
| Optional field absent from template (FR-013) | No |
| Optional value cannot be determined (FR-013) | No — emptied or `_group` removed, notice |
| Asset unresolved (see [asset-resolution.md](./asset-resolution.md)) | Depends on fallback and classification |

A fatal check produces no image (FR-014). What happens next is the posting origin's business —
see [error-taxonomy.md](./error-taxonomy.md).

## Vacuous passing, and why it is not a silent pass

No image type has a generation specification yet, so every catalogue is empty and check 4 has
nothing to look for.

- `CatalogueLayer.applies_to()` returns **False** for an empty catalogue, so the layer is
  *skipped*, not passed.
- `evaluate_template` records depth only for layers that ran, so such a template reports
  **depth 1**.
- `depth_summary` continues to state which layers were applied and which were not.

A template that has passed only Layer 1 is therefore never presented as though it had passed a
deeper check — Constitution XIV.9, invariants 3 and 4. Populating a catalogue is the single
change that switches check 4 on for that type, at all three moments at once.

## Reporting parse faults

A file that will not parse is reported as an invalid SVG naming the file and the fault in the
module's own words (FR-046). Faults worth naming: a double hyphen inside a comment, an unclosed
or mismatched tag, an undefined entity, a stray `&`, a bad encoding declaration. Anything else
becomes "not well-formed XML at line *N*". The parser's own string goes to the application log,
never to a user.
