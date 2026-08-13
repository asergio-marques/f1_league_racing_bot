# Contract: The Two Posting Lifecycles

**Feature**: 041-attendance-image-generation
**Changes**: `src/services/attendance_service.py`, `src/services/rsvp_service.py`, and the two new
`image_*_post` modules
**Normative source**: Constitution XIV.7, XIV.8 and XIV.17 (v4.6.0)

The two graphics of this module stand in **different relations to their text**, and their lifecycles
differ accordingly. This is the document that keeps them apart.

| | Attendance sheet | Check-in graphic |
|---|---|---|
| Relation to the text | **Replaces** the textual sheet | **Displaces nothing** — added beside the embed |
| Redrawn | On every occasion the text is reposted | **Never** — static (XIV.17) |
| On a render failure | Post the textual sheet instead | Post the call **without an attachment** |
| Message on redraw | Deleted and reposted, id persisted | Never deleted; embed edited in place |
| Transport failure | Textual sheet enqueued for retry | **Not enqueued** — reported to the log channel |

---

## Part 1 — The sheet

### The ordering (FR-045)

`attendance_service.post_attendance_sheet` today:

```
1. read config, resolve channel
2. DELETE the prior message              ← too early
3. build the sheet text
4. send  → on HTTPException: log and return
5. persist the new message id
```

Required:

```
1. read config, resolve channel
2. build the replacement — the graphic, or the text where the render failed or the toggle is off
3. send the replacement  → on HTTPException: enqueue the TEXT, report, return (prior message stands)
4. DELETE the prior message
5. persist the new message id
```

The ordering belongs to this function and the image branch inherits it (author's ruling, 2026-08-13).
There must be exactly one delete site and exactly one send site.

**Invariant.** At no instant is the channel without a sheet, unless it never had one.

### Occasions (FR-044)

Post-race penalties of a round approved and posted; a round's attendance recalculated after an
amendment approved via `/round results amend`. Both already call `post_attendance_sheet`; neither call
site changes.

### Refusals to post at all (FR-046, FR-047)

No attendance channel configured, or inaccessible, or the round recorded as cancelled → nothing is
generated and nothing is posted, the toggle notwithstanding. The generation must be *skipped*, not
attempted and discarded (XIV.8, "no posting, no graphic").

### The graphic never gates a sanction (FR-048)

`enforce_attendance_sanctions` and the verdicts it announces MUST complete regardless of what the sheet
does. The sheet posting is downstream of the enforcement and is never a precondition of it (XIV.7).

**Test obligation**: with the render forced to fail, assert the autoreserve and autosack effects are
applied and their verdicts posted.

### Fallback (FR-057, FR-058)

| Origin | On a fatal error |
|---|---|
| `SCHEDULED` | Post the textual sheet in its place; report to the log channel |
| `COMMANDED` | Reject the command, post nothing, tell the caller |

---

## Part 2 — The check-in call

### One call site (R4, FR-051)

The graphic is generated **once**, in `rsvp_service.run_rsvp_notice`, at the initial post.

```
run_rsvp_notice
    ├─ build embed + view                  (unchanged)
    ├─ image_rsvp_post.try_attach(...)     ← THE ONLY GENERATION CALL IN THE MODULE
    ├─ channel.send(content, embed, view, file?)
    ├─ on HTTPException → report to log channel (FR-062), return
    ├─ bulk_insert_attendance_rows         (unchanged)
    └─ insert_embed_message                (unchanged)
```

**No image module is imported by** `RsvpView` / the button callbacks, `run_reserve_distribution`,
`run_rsvp_deadline`, or `_rebuild_embed_for_round`. Every one of those edits the embed in place and the
attachment survives untouched.

**Test obligation**: assert the import graph directly — no module reachable from a button press
references `image_rsvp_post` or `image_rsvp_service`.

### The toggle changes nothing else (FR-052)

With `rsvp` on or off, the message carries the same role mention, the same embed, the same three
buttons, composed by the same code. The only difference is the presence of a `file=`.

### What stays text (FR-053)

The last notice to unanswered drivers, the reserve-distribution announcement, and the
no-reserve-available notice carry no graphic, the toggle notwithstanding.

### Deletion of the previous round's call (FR-054)

Unchanged, and applies to a message carrying a graphic exactly as to one carrying none.

### The graphic never gates the call (FR-055)

A failed render posts the call **without an attachment** — there is no text to restore, the graphic
having displaced nothing (XIV.7). The round's attendance rows are opened exactly as they are when a
graphic succeeds.

**Test obligation**: with the render forced to fail, assert the call is posted with role mention, embed
and three buttons, and that `bulk_insert_attendance_rows` is called.

### The failed post (FR-061, FR-062)

A check-in call is **never** enqueued for retry. `retry_service.enqueue` takes `content: str` and
`attempt_delivery` reposts chunked text, so a call so enqueued would arrive with no embed, no roster and
no buttons — a message the division cannot answer.

Instead, `run_rsvp_notice`'s existing `except discord.HTTPException` branch reports to the server's log
channel via `output_router.post_log`, naming season, division and round.

**This fires with the `rsvp` toggle off.** The fault is in the call, not the picture; a league that
never enables images must still learn its calls are failing. Today the branch reaches `log.error` alone,
which no league can see.

**Test obligation**: force the send to fail with the toggle **off** and assert the log-channel report.

---

## Shared

### Notices (FR-056)

Both hooks report non-fatal degradations to the calculation log channel through
`image_results_post.report_notices`, naming season, division and round — never in a division's
attendance or RSVP channel. Where a command triggered the generation, they are additionally reported
alongside its output.

### Per-division isolation (FR-059)

The failure of one division prevents no other. Each division renders and posts on its own.

### The test commands (FR-070)

`/images test attendance` and `/images test rsvp` never fall back to text, having no textual
counterpart. A fatal error is reported to the invoking league manager and nothing is posted.
