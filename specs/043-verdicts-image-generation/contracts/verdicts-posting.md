# Contract: Verdict Generation and Posting

**Feature**: `043-verdicts-image-generation`
**Implemented in**: `src/services/image_verdict_post.py`, attaching at three existing trigger points
**Governs**: Constitution XIV.7, XIV.8, XIV.17, Principles V and VII

---

## Trigger points

Three, in two shipped modules. Each already posts a textual announcement; each gains an image path in
front of it.

| Kind | Function | Module |
|---|---|---|
| Penalty | `post_penalty_announcements` | `verdict_announcement_service` |
| Appeal | `post_appeal_announcements` | `verdict_announcement_service` |
| Attendance sanction | `post_autosanction_announcement` | `verdict_announcement_service`, called by the attendance module |

**One graphic and one message per verdict.** A review applying *n* penalties posts *n* of each.
A review approved with nothing staged announces nothing and generates nothing.

## The message

With the `verdicts` toggle enabled and the template valid:

```
content:     <@{driver_discord_id}>        ← the mention, and nothing besides
attachment:  the rendered PNG
channel:     division_results_config.penalty_channel_id
```

Everything the textual announcement carried — heading, driver line, sanction, description,
justification — moves onto the canvas. The mention stays because a picture cannot carry one (XIV.16).
This is XIV.7's far pole: a graphic displacing all but the unpicturable.

Note the textual announcement appends the driver's display name after the mention. The image message
does **not**: the name is on the graphic.

## Lifecycle

**Static** (XIV.17). Posted once; never edited, replaced or deleted. **No message id is persisted.**

`image_verdict_post` therefore writes nothing and reads no message state. XIV.8's delete-and-repost does
not arise in any form, and the module MUST NOT add a message table "for symmetry" with the other six
types.

A correction of the decision arrives as a **different verdict** with its own graphic. The first stands
as a true record of what was decided when it was decided.

## Ordering — the graphic is downstream of everything

XIV.7's precondition clause, and the one thing in this contract that is not about pictures:

> The generation and posting of a verdict MUST NOT prevent, delay or condition the finalisation of a
> review or the enforcement of a sanction.

The review is finalised and the sanction enforced **before** any render is attempted, exactly as they
are with the image module disabled. A render that fails must find that work already done. The failure of
one verdict prevents neither the other verdicts of the same review nor those of any other division.

This is the rule most easily broken by writing the natural code — building the message and sending it
whole quietly makes a rasteriser the gate on a league's sanctions.

## Skips — no posting, no graphic

Where the source module would post nothing, nothing is generated:

- no verdicts channel configured for the division (`penalty_channel_id` is null);
- the channel inaccessible;
- an attendance **pardon**, which is no verdict at all — it is recorded in the server's logging channel
  and carries no graphic, the toggle notwithstanding.

The toggle decides how a posting is dressed, never whether it happens.

## Failure

| Failure | Result |
|---|---|
| Fatal error, posting triggered by no command | That verdict is announced as **text** instead |
| Fatal error, posting triggered by a command | The command is **rejected**, nothing posted, the caller told what is at fault |
| Posting fails for a **service** reason | The **textual** announcement is enqueued for retry. A generated image MUST NOT be enqueued |
| `images test verdicts` meets a fatal error | Reported to the invoking league manager; nothing posted. It has no textual counterpart to fall back to |

Because nothing is persisted, the fallback is the existing textual call at the same point, unchanged.
There is no state to reconcile.

## Notices

Gathered during generation, reported to the **server's logging channel**, naming the season, the
division, the round, the session and the driver. Never to a division's verdicts channel — no problem and
no notice may reach a channel drivers read (XIV.4, Principle V).

Where a command triggered the generation, notices are **additionally** reported alongside its output.

## The test command

`/images test verdicts` generates **six** images from the one template, reported to the invoking league
manager and **never** posted to any division's verdicts channel:

| # | Kind | Exercises |
|---|---|---|
| 1 | Penalty, time added, sprint round | Sprint session naming |
| 2 | Penalty, time removed | The other sign of a time penalty |
| 3 | Penalty, disqualification | The non-time sanction |
| 4 | Appeal | The appeal stage string |
| 5 | Autosack | Emptied session and team; mention inside free text |
| 6 | Autoreserve | The same, on the other sanction |

All drawn for a division named "Test Division", tier 1, season number 1, at round 1 of a track from the
server's track list. **An empty track list rejects the command** with a clear error — there is no round
for a verdict to pertain to.

Free text across the six covers: one line; exactly full; slightly over (exercises reduction); an order
of magnitude over (exercises the floor, the cut and the notice); and one with neither description nor
justification entered. Nationalities are values the signup wizard accepts, at least one being the value
recorded for a driver who stated none.
