# Contract: Asset Resolution

How a datum becomes a file on disk, and what happens when it does not.

## Normalisation

```text
normalise(text):
    trim
    lowercase
    NFKD-decompose, drop combining marks
    every run of characters that is neither a letter nor a digit → a single "_"
    drop leading and trailing "_"
```

| Datum | Normalised | File |
|---|---|---|
| `Red Bull Racing` | `red_bull_racing` | `red_bull_racing.svg` |
| `São Paulo` | `sao_paulo` | `sao_paulo.svg` |
| `Emilia-Romagna` | `emilia_romagna` | `emilia_romagna.svg` |
| `  McLaren  ` | `mclaren` | `mclaren.svg` |
| `Portugal` | `portugal` | `portugal.svg` |
| `!!!` | `` (empty) | never resolves — treated as an unresolved asset |

**Underscores, not hyphens.** This is the rule `resources/poc/build_poc.py` already implements
and the one every asset shipped under `resources/` is named by. Constitution v2.13.0 briefly
stated a hyphen; it was withdrawn in v3.0.0.

## Location

```text
<the ImageConfig column configured for this asset class> / normalise(datum) + ".svg"
```

The catalogue names the asset class for each image field; the class maps to one of the seven
configured directory columns. A utility MUST NOT construct a path from anything else — no
per-graphic subdirectory, no datum-derived directory, no extension other than `.svg`.

Every resolved path stays subject to the existing `resolve_within_project_root` containment
check. A configured directory that escapes the project root is already a Layer 1 failure.

## When the file is absent

```text
                      normalise(datum) + ".svg" in the class's directory
                                        │
                        ┌───────────────┴───────────────┐
                     exists                        does not exist
                        │                               │
                        ▼                   ┌───────────┴───────────┐
                   use it            fallback.svg in           no fallback
                                     that directory                 │
                                            │              ┌────────┴────────┐
                                            ▼          mandatory         optional
                                     use it;               │                 │
                                     notice                ▼                 ▼
                              ASSET_FALLBACK_USED       PROBLEM      empty the field, or
                                                     ASSET_UNRESOLVED  remove its _group;
                                                                          notice
```

| Case | Outcome | Source |
|---|---|---|
| File present | Used | — |
| Absent, directory holds `fallback.svg` | Fallback used; notice naming the field **and the datum** | FR-043 |
| Absent, no fallback, field mandatory | Problem; render aborts | FR-044 |
| Absent, no fallback, field optional | Field emptied or `_group` removed; notice | FR-044 |

The fallback applies to mandatory and optional fields alike. "Mandatory" says the template must
carry the slot and the data must yield a value — not that every nationality owns a bespoke flag
(Constitution XIV.3, spec A-008).

## `fallback.svg`

- A reserved name inside each asset directory. Optional; a league that supplies none simply gets
  the classification behaviour above.
- Bound by the same authoring rule as any other asset: plain SVG, authored at exactly the aspect
  of the slot, never padded by the generator (FR-045).
- Where one asset class serves slots of differing aspects, the fallback is authored to whatever
  aspect the class's ordinary assets use — the same constraint those already carry.
- A datum literally named "Fallback" normalises to `fallback` and collides. Accepted (spec A-007).

## Authoring rules the module must document

Stated wherever assets are configured (FR-041):

- An asset is authored at exactly the aspect of the slot it fills, padded with **transparent
  margins** where the subject does not fill that aspect.
- An asset of another aspect is letterboxed, and the converter fills the band by carrying the
  outermost pixels outward — so a border or background colour at the asset's edge is smeared
  across the band. The module does not pad at generation (FR-040).

## Invariants

1. Normalisation is pure and total: same input, same output, no filesystem access.
2. Resolution is deterministic: no globbing, no case-insensitive directory scan, no "try `.png`
   as well". The name is computed, then tested for existence, once.
3. A missing asset is never silently skipped. It always produces a notice or a problem.
