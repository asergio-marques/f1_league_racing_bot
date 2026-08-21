# Contract: two-tier asset resolution

The interface between a datum and the file drawn for it. The **rules** live in `docs/wip-specs/image_module_specification.md` (The fallback image) and in Constitution XIV.13; this is the signature, the four paths, and what each reports.

## Signature

```
resolve_asset(directory: Path, datum: str, *, packaged: Path | None = None) -> AssetResolution
has_fallback(directory: Path, *, packaged: Path | None = None) -> bool
packaged_directory_for(asset_class: str) -> Path
```

`directory` is what the league configured. `packaged` is `resources/defaults/<class>`. Omitting `packaged` gives today's single-tier behaviour, which is what keeps the widening additive.

## The four paths

| # | Configured dir | Packaged dir | Drawn | Reported | `outcome` | `from_packaged` |
|---|---|---|---|---|---|---|
| 1 | holds `<slug>.svg` | *not consulted* | the datum's own file | nothing | `FOUND` | `False` |
| 2 | no `<slug>.svg`, holds `fallback.svg` | *not consulted* | the configured fallback | **notice**, naming field + datum | `FALLBACK` | `False` |
| 3 | no `<slug>.svg`, no `fallback.svg` | holds `fallback.svg` | the packaged fallback | **the same notice** | `FALLBACK` | `True` |
| 4 | no `<slug>.svg`, no `fallback.svg` | no `fallback.svg` | nothing | **problem** — render abandoned | `MISSING` | `False` |

Three invariants bind this table:

1. **The datum's own file is sought in the configured directory alone.** A file named `<slug>.svg` sitting in the packaged directory is never drawn for a league that did not supply it. Only `fallback.svg` is read from the packaged tier.
2. **Paths 2 and 3 report identically.** A league is told the datum had no file of its own; which tier answered is not something it can act on. `from_packaged` exists for tests and diagnostics and no caller may branch on it to change the notice.
3. **The tier is per class.** A flag miss is answered by `defaults/flags/fallback.svg` and never by another class's.

## Where it is called

`utils/svg_fill.py`, **one** call site, reached by every one of the fifteen graphics (research **R6**). This is what makes "the two-tier resolution applies to every asset class and every graphic" true by construction rather than by discipline.

The call site must pass `packaged=packaged_directory_for(asset_class)`. It already holds `asset_class` — it is what selects the configured directory — so nothing new must be threaded through the fill pipeline.

## Interactions

**The absent-datum rule.** Where a catalogue declares that an absent datum draws the class's fallback (the tyre case), the fallback is drawn and **no** notice is raised. That fallback is now found under the same two tiers, so a league whose tyre directory holds nothing still draws a tyre. Where neither tier holds one, the field is removed as its catalogue declares — an absent datum is never fatal for want of a file.

**`/images test`.** A preview resolves exactly as a posting for the same division would, packaged tier included. The existing rule that a test command must not *substitute* the packaged directories for the configured ones means path 1 — the datum's own file — and must not be read as withholding paths 3. Withholding it would make a preview answer differently from the posting it exists to predict.

**A league pointing a class at the packaged directory.** The two tiers become one directory. Paths 2 and 3 collapse; behaviour is identical to the single-tier case.
