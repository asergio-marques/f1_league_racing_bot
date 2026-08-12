# Contract: Command Surface

One new command, two altered behaviours, and one command whose data source changes. No command is
renamed or removed.

---

## NEW — `/division calendar sync`

Redraws a division's calendar and replaces the posted message.

| Property | Value |
|---|---|
| Group | `/division` — beside the existing `calendar-channel` |
| Access | League manager |
| Parameter | `division` (string, required) — the division's name, autocompleted as the group's other commands do |
| Module gate | **None.** Gated on neither the images module nor any other |
| Model | `results standings sync` and `results rounds sync`, which do the same for standings and results |

**Behaviour**

1. Resolve the division. Unknown name → rejected.
2. No `calendar_channel_id` configured → rejected with a clear error.
3. Produce the replacement: the graphic where the images module is enabled and the `calendar` toggle
   is on, the textual calendar otherwise.
4. On success — delete the old message named by `calendar_message_id`, post the new one, persist its
   id.
5. On a fatal error — reject the command, **delete nothing**, post nothing, and report what is at
   fault to the caller. Non-fatal errors are reported to the log channel and alongside the reply.

**Ordering is the contract.** The previous message is deleted only after its replacement has been
produced successfully. A failure must never leave the channel with no calendar.

**Test mode**: identical behaviour. The deletion is *not* suppressed — the forecast flow's test-mode
deletion guard does not extend to a replacement (FR-017).

**Discord budget**: `/division` gains its first `calendar`-prefixed subcommand group member. The
`/images config` and `/images template` groups are untouched, so the 25-subcommand ceiling recorded in
the wip-spec's configuration section is unaffected.

---

## ALTERED — `/season approve`

After the existing per-division lineup refresh, the calendar posting at
`src/cogs/season_cog.py` (the block commented `T017: Post calendar per division`) becomes:

- images module enabled **and** `calendar` toggle on → draw and post the graphic;
- otherwise → post the textual calendar exactly as today;
- either way → persist the resulting message id against the division.

**The message text is the textual calendar's heading, verbatim**:

```text
📅 **{division_name} — Race Calendar**
```

The graphic replaces the per-round lines beneath it, so the two forms are indistinguishable above the
fold. The heading is not re-invented for the image path — it is the same string `season_cog` already
emits, and must stay in step with it.

**Per-division isolation**: one division's fatal error causes that division alone to fall back to the
textual calendar. Every other division is still posted as an image. The loop must not abandon on the
first failure.

**Uncommanded posting**: approval is a user command, but the *calendar posting* within it is not the
thing commanded — the season approval is. Per constitution XIV.7 a fatal render here falls back to
text rather than refusing the approval, and the error is reported to the log channel and to the
approving manager.

---

## ALTERED — `/season review`

The images section gains the calendar reported at a depth beyond Layer 1 — the first image type to be
so reported. Per constitution XIV.9 the report must state which layers were applied, so a template
checked to Layer 1 alone is still shown as such.

Where the season's most demanding division holds more rounds than the configured template declares,
the review shows a **warning**, not a failure, and approval is not refused on its account.

Where a template is at fault, the review names it with its own reason and **approval is refused**
while it stands. The review itself refuses nothing.

---

## ALTERED — `/images test calendar`

The command already exists. Its data source for the `calendar` kind changes from
`image_sample_data.build_spec`'s generic sample to the calendar test data fixed in the wip-spec's
§ "Test data": a division named "Test Division", tier 1, season 1, holding one round fewer than the
template declares.

| Condition | Outcome |
|---|---|
| Template declares N > 1 rounds | N−1 rounds fabricated, image cut at round N−1's crop point |
| Template declares exactly 1 round | 1 round fabricated, image drawn at the declared height |
| Fabricated division would hold no round | Rejected with a clear error |
| Server's track list is empty | Rejected with a clear error |
| Fatal error during the render | Reported to the caller; **no fallback to text** — this command has no textual counterpart |

The fabricated rounds cover, as far as the round count allows: one of each format including mystery;
one whose track has no image file, to exercise the fallback and its notice; and dates spanning more
than one month. A round with no time recorded is deliberately **not** among them — a round holds date
and time as one moment by design, so the shape cannot be fabricated
(see [research.md § R5](../research.md)).

The other ten `/images test` kinds keep the generic sample data unchanged.

---

## ALTERED — the command that adds a round to a division

Gains a capacity guard. Where the images module is enabled, the `calendar` toggle is on, and the
configured calendar template declares fewer rounds than the division would then hold, the command is
**refused with its change unapplied**, reporting the round count, the template's capacity and the
template at fault (constitution XIV.12).

This is a *separate* guard from `placement_service._guard_image_capacity`, which counts seated drivers
and continues to serve the driver-seat collections. Reasoning in
[research.md § R3](../research.md).

---

## Unchanged

- `/images template calendar` — its validation deepens (Layer 2 now applies) but its signature,
  rejection semantics and reply shape are as delivered.
- `/division calendar-channel` — untouched.
- Every `/images config` subcommand — no new configuration is introduced by this feature.
