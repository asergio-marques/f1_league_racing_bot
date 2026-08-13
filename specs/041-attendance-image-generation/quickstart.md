# Quickstart: Validating Attendance Image Generation

**Feature**: 041-attendance-image-generation | **Date**: 2026-08-13

How to prove this feature works. Every graphic is verified as a **rasterised PNG** — never as an SVG
previewed in a browser (Constitution XIV.14, CLAUDE.md). The rasteriser exposes flowed text,
substituted fonts and unresolvable hrefs that a browser silently fixes.

---

## Prerequisites

| Requirement | Check |
|---|---|
| Inkscape on the host | `/images config view` reports the rasteriser found, or set `INKSCAPE` to the executable's full path |
| Both templates authored | An SVG per slot, in the configured template directory |
| A team configuration | At least one team beyond the reserve team |
| A non-empty track list | Both test commands are rejected without one |

---

## Step 1 — The suite

```
pytest tests/ -q
```

Baseline on `main` at 5f13b2f: **1498 passed, 1 skipped**. Run it before starting and compare after.
Any failure is a real one — do not write it off as pre-existing without confirming it on a clean tree.

---

## Step 2 — Template configuration refuses a bad file

```
/images template attendance file:<a sheet template>
/images template rsvp       file:<a check-in template>
```

| Case | Expected |
|---|---|
| A sheet template with rows numbered 1, 2, 4 | Refused, naming the gap; the previous filename stays in force |
| A sheet template declaring no row at all | Refused |
| A sheet template declaring `round_format` or `session_1_name` | Refused as the wrong file for the slot (widened sibling check) |
| A check-in template declaring `row_1_driver_name` | Refused likewise |
| A sheet template declaring `round_1_number` and no `round_1_group` | **Accepted** — a template's choice, not a fault |
| A template declaring no round / no session at all | **Accepted** — the grid and the session list are optional as a unit |
| A file with a `--` inside a comment | Refused in plain terms ("a comment contains a double hyphen at line N"), never as a parser error |

---

## Step 3 — The two graphics, without a season

```
/images test attendance
/images test rsvp
```

**Sheet** — two PNGs, "Test Division", tier 1, season 1, five rounds, standing after the third:

- one with both point limits configured, one with both **disabled** — the second must show the
  autoreserve and autosack blocks *gone*, not blank
- round 2 of the fabricated calendar is a mystery round
- one round's track has no image file → the track fallback is drawn and a notice is listed
- one fewer driver than the template declares rows → the unused row is gone, not blank
- at least one driver on no points at all, one sanctioned ("Reached point limit"), one whose round was
  fully pardoned, and two level on totals in alphabetical order

**Check-in** — five PNGs: a sprint round (four sessions), a normal round (two), a mystery round, a
round whose track has no image file, and a round with the deadline configured to **0** — the last must
draw the deadline at the round's own start time.

All are reported to the invoking manager and **never posted to a division's channel**.

### Open the PNGs and check

- [ ] Every string is drawn in the face the template names, or the substitution is listed as a notice
- [ ] No name overruns what is drawn beside it — long names are cut at a word boundary with an ellipsis
- [ ] No broken-image mark anywhere (an href that is a path, not a URI)
- [ ] Empty round cells are **empty**, not "0" and not "—"
- [ ] The sheet draws **no position number** on any row
- [ ] The check-in graphic names no driver, no team and no RSVP status
- [ ] Times carry the configured zone's abbreviation

---

## Step 4 — The floor

Draw a sheet for a division holding **no driver**.

Expected: refused, naming the **division** — not a capacity complaint about the template, which is not
at fault.

---

## Step 5 — The sheet through its lifecycle

```
/images config toggle aspect:attendance enabled:true
```

1. Approve a round's post-race penalties → one message in the division's attendance channel, graphic
   attached, the textual sheet's heading as message text.
2. Amend the round via `/round results amend` → the sheet is replaced.
3. **Check the ordering.** Instrument or observe: the replacement is posted *before* the previous
   message is deleted. At no instant is the channel without a sheet.
4. Break the template (remove a mandatory field) and trigger a posting → the **textual** sheet appears
   instead, and the log channel says why. The attendance channel shows no error.
5. **The sanction gate.** With the template broken, trigger a posting for a round where a driver
   reaches the autoreserve limit → the sanction is enforced and its verdict announced regardless.
6. A cancelled round → nothing posted, nothing generated.
7. A division with no attendance channel → nothing posted, nothing generated.

---

## Step 6 — The static graphic

```
/images config toggle aspect:rsvp enabled:true
```

1. Let the notice horizon fire → the call is posted with role mention, embed, three buttons **and** the
   graphic.
2. Press each of the three buttons in turn. After each:
   - [ ] the embed's roster and status indicators change
   - [ ] the attachment is **byte-identical** and was not re-uploaded
   - [ ] the message id is unchanged — the message was edited, never reposted
3. Let the reserve distribution run at the deadline → same three checks.
4. Confirm the last notice, the distribution announcement and the no-reserve notice carry **no**
   graphic.
5. Break the template and let a call post → the call appears with role mention, embed and buttons, no
   attachment, and the round's attendance rows are opened as usual.
6. Turn the toggle **off** and post a call → identical in every respect except the attachment.

---

## Step 7 — The failed post is visible

Force the check-in send to fail (revoke Send Messages on the RSVP channel, or stub the send).

- [ ] The log channel names the season, the division and the round
- [ ] It does so with the `rsvp` toggle **off** as well as on
- [ ] Nothing is enqueued for retry — the queue carries text alone, and a call replayed as text has no
      buttons

---

## Step 8 — Season review

```
/season review
```

- [ ] A sheet template declaring fewer rounds than the season's most demanding division → **warning**,
      and the season can still be approved
- [ ] A check-in template declaring fewer sessions than the season's largest round → **warning**
- [ ] A missing mandatory field → named, and approval refused while it stands
- [ ] Each faulty template is named individually with its own reason

---

## Step 9 — The overflow refusal

Assign drivers to a division until one more would exceed the rows the sheet template declares.

- [ ] The assignment is refused and the change is not applied
- [ ] The refusal names the driver count, the declared capacity and the template

---

## What "done" looks like

- `pytest tests/ -q` at or above the baseline, no new failures
- Both graphics inspected as PNGs, not SVGs
- The sheet replaced without the channel ever being empty
- A broken template never reaching a channel drivers read
- No sanction ever missed because a picture could not be drawn
- A check-in graphic that still tells the truth after every driver has answered
