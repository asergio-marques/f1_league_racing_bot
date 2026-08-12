# Contract: Errors, Destinations and Posting Origin

Two classes of outcome, three destinations, one switch.

## Fatal vs non-fatal

| | Problem (fatal) | Notice (non-fatal) |
|---|---|---|
| Effect on the render | Aborts. No image, not even partial (FR-014) | Survives. Image is produced |
| Effect on user input | Rejects the input that caused it (FR-028) | None |
| Where reported | See destinations below | Log channel, and alongside a command's output |

### Problem kinds

| Kind | Raised by |
|---|---|
| `EXTENSION` | Filename not ending `.svg` (FR-001) |
| `NOT_FOUND` | No file at directory + filename (FR-002) |
| `NOT_SVG` | Will not parse, or root is not `<svg>` (FR-003, FR-046) |
| `MISSING_MANDATORY_FIELD` | Template lacks a mandatory field (FR-004, FR-012) |
| `UNRESOLVED_VALUE` | Mandatory value undeterminable at generation (FR-011) |
| `UNKNOWN_FIELD` | Data supplies a field the template does not declare |
| `ASSET_UNRESOLVED` | Mandatory image field, no file, no fallback (FR-044) |
| `CAPACITY_EXCEEDED` | Rows of data exceed declared capacity (FR-028) |
| `RASTERISER` | Converter absent, failed, timed out, or output too large |
| `UNKNOWN_IMAGE_TYPE` | A render was asked for a type the module does not know — a caller defect, not a league's |

`UNKNOWN_IMAGE_TYPE` is the one kind no user can provoke. It is still a `Problem` so that every
failure path returns uniformly and no traceback can escape into a Discord surface; the offending
caller is identified in the application log rather than in the user-facing message.

Every problem names the **individual** template at fault, and the field where it has one. Naming
a group of templates does not satisfy FR-008.

### Notice kinds

| Kind | Raised by |
|---|---|
| `FONT_SUBSTITUTED` | Host lacks the named font (FR-034) — names the field *and* the font |
| `INLINE_SIZE_TRUNCATED` | Single-line field cut to its declared room (FR-036) |
| `WRAP_TRUNCATED` | Wrapping field hit its size floor and was cut |
| `ASSET_FALLBACK_USED` | `fallback.svg` stood in (FR-043) — names the field *and* the datum |
| `OPTIONAL_FIELD_EMPTIED` | Optional value undeterminable (FR-013) |

## Destinations

```text
                        ┌──────────────────────────────────┐
   notice ─────────────►│ server calculation log channel   │  always (FR-031)
                        └──────────────────────────────────┘
                        ┌──────────────────────────────────┐
   notice ─────────────►│ alongside the command's output   │  when a command triggered it
                        └──────────────────────────────────┘
                        ┌──────────────────────────────────┐
   problem ────────────►│ the caller, or the log           │  per posting origin, below
                        └──────────────────────────────────┘
                        ┌──────────────────────────────────┐
   anything ───X───────►│ any channel drivers read         │  NEVER (FR-032)
                        └──────────────────────────────────┘
```

FR-032 is absolute. It holds for problems and notices alike, for commanded and scheduled
postings alike, and for the rasteriser-absent notice the module already posts.

## Posting origin

A required argument at the render-and-post entry point. Never inferred (research R6).

| Origin | Covers | On a problem |
|---|---|---|
| `COMMANDED` | A user ran a command that posts | Reject the command. Post **nothing**, to any channel. Tell the caller what is at fault and invite them to correct it (FR-030) |
| `SCHEDULED` | A horizon, the scheduler, startup, the retry queue | Fall back to the traditional text output (FR-029) |

```text
problem at generation
        │
        ├── COMMANDED ──► nothing posted; caller told the fault
        │                 (they are the one person who can fix the template)
        │
        └── SCHEDULED ──► text output posted as it always was
                          (nobody to tell; the league still needs the information)
```

Falling back silently for a commanded posting would deny the manager the chance to fix the
defect, and hide it until it next fires unattended. That is the whole reason the switch exists.

## Rejecting at the earliest moment

A problem traceable to something a user configured or commanded rejects that input wherever the
module can detect it (FR-028) — not only at generation:

| Moment | Rejection |
|---|---|
| `/images template <kind>` | Command refused; configuration left as it stood |
| `/season approve` | Season fails validation, naming what is at fault |
| A command growing a division past template capacity | Refused; the change not applied |
| A command triggering a failing generation | Refused; nothing posted in consequence |

The third is built here and inert until a catalogue declares a capacity — see Complexity
Tracking in [plan.md](../plan.md).

## Invariants

1. `png_paths` is empty whenever `problem` is set. No caller can receive a partial image or
   mistake a degraded render for a clean one. *(Existing 035 invariant, retained.)*
2. Notices raised before a problem are still reported, so an operator sees the whole picture.
   *(Existing behaviour, retained.)*
3. A raw parser error, stack trace or exception string never reaches a Discord surface. It goes
   to the application log.
4. No code path posts to a driver-read channel on an error. This is testable by enumeration:
   every error path terminates at the log channel, an ephemeral reply, or a command followup.
