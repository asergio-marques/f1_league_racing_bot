# Phase 0 research: image test commands drawn from the league's own configuration

## R1 — The seam the preview must sit on

**Decision.** The preview calls each type's `resolve_drawing(**values)` and `build_fill_spec(drawing, root, asset_directories=...)` directly, and does **not** call the post services' `build_drawing(bot, ...)`.

**Rationale.** Every image type is already built in two halves. The `image_*_post.py` module reads the database and hands values to a pure `resolve_drawing()` in the matching `image_*_service.py`; that function returns a `*Drawing` dataclass, and `build_fill_spec()` turns it into a `FillSpec`. `resolve_drawing` is keyword-only and takes plain values — `image_results_service.resolve_drawing(session_type=…, division_name=…, driver_rows=…)`, `image_standings_service.resolve_drawing(template_key=…, snapshots=…)`, `image_attendance_service.resolve_drawing(records=…, nationalities=…)`.

That is exactly the boundary the preview needs. Above it sits the database read the preview must replace, because the outcomes it draws do not exist. Below it sits every rule about how a drawing becomes a picture — the catalogues, the capacities, the crop, the asset resolution — all of which the preview must obey identically. Sitting on this line means the preview shares the whole of the second half and none of the first.

**Alternatives considered.** Refactoring the post services to accept injected data was rejected: it would put a test-only parameter on every posting path, and the posting paths are the code that must stay simplest. Calling `build_drawing(bot, …)` and then mutating the returned drawing was rejected because several drawings are computed, not merely assembled — the standings compute positions and gaps from snapshots — so a mutated drawing is not a drawing the same inputs would have produced.

## R2 — Why the withdrawn command showed packaged artwork, and what changes

**Finding.** `image_sample_data.py` hardcodes the packaged asset paths and never reads the league's configuration at all:

```
for asset_class, relative in (("flag", "resources/flags"), …):
    directories[asset_class] = resolve_within_project_root(relative)
```

The posting path does the opposite, reading each directory from the stored configuration:

```
config = await bot.image_config_service.get_config(server_id)
for asset_class, column in (("flag", "flag_directory"), …):
    directories[asset_class] = resolve_within_project_root(getattr(config, column))
```

**Decision.** The preview uses the second form. This is the whole of FR-035, and it is a four-line change per type rather than new machinery.

**Consequence for FR-036 and FR-037: they are already built.** `utils/svg_fill.py` resolves each asset through `resolve_asset()`, which reports `found`, `used_fallback`, or neither, and emits a `RenderNotice` of kind `NOTICE_ASSET_FALLBACK_USED` carrying the field, the asset class and the datum that had no file. The cog already returns notices alongside the pictures. Feeding the league's directories in is therefore sufficient to satisfy the fallback-reporting requirement; no new reporting channel is needed, which is what A-012 anticipated.

## R3 — What "an asset class the league has not configured" actually means (FR-038)

**Finding.** Every asset directory column is `NOT NULL DEFAULT 'resources/…'` in `039_image_module.sql`. A league therefore always has a configured directory, defaulting to the packaged one. The literal reading of FR-038 — a class with no directory — is unreachable through ordinary configuration.

The reachable case is different and more useful. The posting path resolves each directory inside a `try` and discards the failure:

```
try:
    directories[asset_class] = resolve_within_project_root(getattr(config, column))
except Exception:
    pass
```

A directory that is configured but cannot be resolved — a path escaping the project root, a directory that does not exist — is silently omitted from `directories`. `svg_fill` then reports `image field X names asset class Y, which is not configured`, which becomes a **fatal** unresolved-value problem. The manager is told the class is unconfigured when in truth their configured path was rejected, and never told why.

**Decision.** FR-038 is satisfied by distinguishing three states in the preview's reply: the directory resolved and the file was found; the directory resolved and the fallback stood in (the existing notice); the directory did not resolve, naming the configured value and the reason it was rejected. The preview captures the exception the posting path discards rather than changing the posting path's behaviour.

**Logged separately.** The silent `except Exception: pass` in the posting path is a defect in its own right — a league whose flag directory is misconfigured gets a fatal render with a misleading reason on a real post. It is out of scope here and belongs in `docs/wip-specs/known_issues.md`.

## R4 — Command nesting depth

**Decision.** `test` becomes an `app_commands.Group` nested inside the existing `images` group, giving `/images test calendar`.

**Rationale.** Discord permits one level of subcommand groups beneath a top-level command — command → group → subcommand — and no more. `/images config toggle` and `/images template calendar` already use exactly that depth, so the eleven previews introduce no new nesting and satisfy the `/domain action` convention in Bot Behavior Standards on the precedent those two set.

`/images` gains no subcommand by this change: `test` occupies the one slot it already occupies, and the eleven commands sit inside it, well within the twenty-five ceiling. `_verify_discord_group_limits()` must gain the new group so the ceiling is checked for it too.

## R5 — Resolving a division by name

**Decision.** Resolve against the divisions of the active season via `season_service.get_active_season()` then `get_divisions(season_id)`, matching on name. Offer autocomplete on the parameter.

**Rationale.** `Division.name` is unique within a season but not across seasons, so the active season is what makes a bare name unambiguous (A-001). There is no existing division-name autocomplete to reuse; `season_cog.round_add_track_autocomplete` is the pattern to model one on.

**Round numbers.** `season_service.get_division_rounds(division_id)` returns the division's rounds; the preview matches `Round.round_number`. `Round.format` gives the format, from which `SESSIONS_BY_FORMAT` and `MAX_SLOTS` in `models/session.py` give the session list and slot ceilings the weather and results previews need. These are read as they stand and MUST NOT be restated, per the constitution's v4.7.0 entity note.

## R6 — Which sessions and slots a forecast preview can cover

**Finding.** From `models/session.py`:

| Format | Sessions | Total slots |
|---|---|---|
| Normal | 2 | 2 + 3 = 5 |
| Sprint | 4 | 2 + 1 + 2 + 3 = 8 |
| Endurance | 2 | 3 + 4 = 7 |
| Mystery | 0 | 0 |

**Consequence.** FR-031 (all five slot types must appear) is satisfiable for every non-mystery format, the normal format reaching it exactly at five slots with no room to spare. FR-030 is satisfiable as written: a sprint round's four sessions can carry all three session weather types, and a two-session round can carry two.

This also settles a case the spec's edge list raised: no format admits fewer slots than the five types require, so no "insofar as the format allows" hedge is needed on FR-031.

## R7 — The verdict preview's sprint-session case

**Finding.** The withdrawn command drew one case against a fabricated sprint round specifically so the naming of a sprint session could be judged. A preview drawn against a real round cannot choose the format.

**Decision.** The session a fabricated verdict pertains to is one of those the named round is actually run over. The sprint-session case is reached by previewing a sprint round, not by fabricating one. This is recorded in the wip-spec and is a deliberate loss of a test case, accepted as the cost of drawing real data.

## R8 — What becomes of `image_sample_data.py`

**Decision.** Retired. Its long-name and long-prose constants move to the fabrication module, because they still serve a purpose FR-032 states — text long enough to exercise the wrapping — but its `build_*_drawing` functions, its fabricated Test Division and its hardcoded packaged directories all go.

**Rationale.** Every function in it is built on a premise this feature reverses. Keeping it beside the new path would leave two ways to build a preview, one of which draws the wrong thing.
