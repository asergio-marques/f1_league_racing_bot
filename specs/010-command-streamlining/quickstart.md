# Quickstart: Command Streamlining & QoL Improvements

Manual walkthrough to verify the feature end-to-end after implementation.

## Prerequisites

- Bot running locally with a fresh `bot.db` (or after `/bot-reset full:True`)
- `/bot-init` already run with a test role, interaction channel, and log channel
- Two Discord users available: one **server administrator**, one **interaction-role holder only**

---

## 1. Simplified season setup

```
/season setup
```
Expected: Ephemeral confirmation "Season is now in setup mode." No parameters required.

Run again immediately:
```
/season setup
```
Expected: Ephemeral error stating a season is already in progress.

---

## 2. Add divisions — see post-modification feedback

```
/division add  name:Pro  role:@ProDrivers  forecast_channel:#pro-weather
/division add  name:Am   role:@AmDrivers   forecast_channel:#am-weather
```
Each response should include a formatted list of all divisions configured so far.

---

## 3. Rename a division

```
/division rename  current_name:Am  new_name:Amateur
```
Expected: Confirmation shows updated division list with "Amateur".

---

## 4. Add rounds — auto-numbering

Add rounds out of chronological order:
```
/round add  division_name:Pro  format:NORMAL  scheduled_at:2026-05-10T18:00:00  track:United Kingdom
/round add  division_name:Pro  format:SPRINT  scheduled_at:2026-04-05T18:00:00  track:Bahrain
/round add  division_name:Pro  format:NORMAL  scheduled_at:2026-04-19T18:00:00  track:Australia
```

Expected after each add: confirmation states the assigned round number; final round list shows:
- Round 1 — Bahrain (Apr 05)
- Round 2 — Australia (Apr 19)  
- Round 3 — United Kingdom (May 10)

---

## 5. Duplicate a division with offset

```
/division duplicate  source_name:Pro  new_name:Amateur  role:@AmDrivers  forecast_channel:#am-weather  day_offset:0  hour_offset:2.0
```
Expected: Amateur division created with rounds at 20:00:00 on the same dates. Division list
and new division round list shown in confirmation.

---

## 6. Delete a round during setup

```
/round delete  division_name:Pro  round_number:2
```
Expected: Round 2 (Australia) removed. Remaining rounds renumbered:
- Round 1 — Bahrain (Apr 05)
- Round 2 — United Kingdom (May 10)

---

## 7. Approve the season

```
/season approve
```
Expected: Season transitions to ACTIVE.

---

## 8. Amend a round datetime — verify renumbering

```
/round amend  division_name:Pro  round_number:1  scheduled_at:2026-06-01T18:00:00
```
Expected: Bahrain shifts after United Kingdom. Round list now:
- Round 1 — United Kingdom (May 10)
- Round 2 — Bahrain (Jun 01)

---

## 9. Cancel a round

```
/round cancel  division_name:Pro  round_number:1  confirm:CONFIRM
```
Expected: Ephemeral confirmation. Check `#pro-weather` — message posted stating no weather
forecast for this round due to cancellation. No `@ProDrivers` mention in that message.

Attempt with wrong confirmation:
```
/round cancel  division_name:Pro  round_number:2  confirm:confirm
```
Expected: Aborted, no changes.

---

## 10. Cancel a division

```
/division cancel  name:Amateur  confirm:CONFIRM
```
Expected: Ephemeral confirmation. Check `#am-weather` — cancellation notice posted, no role mention.

---

## 11. Cancel the full season

As **server administrator**:
```
/season cancel  confirm:CONFIRM
```
Expected: Any remaining active division forecast channels receive cancellation notices. No role
mentions. Ephemeral confirmation. Season data deleted.

Immediately after:
```
/season setup
```
Expected: Succeeds — new setup mode entered, confirming the season was fully deleted.

---

## 12. Test mode access restriction

As **interaction-role holder (non-admin)**:
```
/test-mode toggle
```
Expected: Permission error.

As **server administrator**:
```
/test-mode toggle
```
Expected: Test mode toggled on.

---

## 13. Division delete (setup mode)

Start a fresh season:
```
/season setup
/division add  name:Temp  role:@ProDrivers  forecast_channel:#pro-weather
/division delete  name:Temp
```
Expected: Division removed. Division list shows empty.

Attempt during active season:
```
/season approve
/division delete  name:Pro
```
Expected: Rejected with message directing to `/division cancel`.
