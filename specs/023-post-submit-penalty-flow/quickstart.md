# Quickstart: Inline Post-Submission Penalty Review

**Branch**: `023-post-submit-penalty-flow`

This guide describes the end-to-end flow for league admins after this feature is deployed.

---

## For League Admins: How to Finalize a Round

### 1. Submit all sessions as normal

Use the results submission channel that appears at round time. Enter qualifying and race results in the same format as before.

### 2. After the last session — penalty review begins automatically

Once the final session is submitted (or marked CANCELLED), the submission channel **stays open**. You will see a Penalty Review prompt appear:

```
📋 Post-Round Penalty Review — Round 5: United Kingdom
All sessions submitted. Review and apply any penalties before finalizing.

Sessions:
Feature Qualifying │ No penalties staged
Feature Race       │ No penalties staged

[ Add Penalty ]  [ No Penalties / Confirm ]
```

### 3a. If there are no penalties

Press **No Penalties / Confirm**. The approval step is shown immediately. Press **Approve** to finalize.

### 3b. If there are penalties to apply

1. Press **Add Penalty**.
2. Select the session the penalty applies to.
3. Enter the driver @mention and the penalty value:
   - Time penalties: `5`, `+5s`, `-3s` (positive = add time, negative = subtract time)
   - Disqualification: `DSQ`
4. Repeat for each penalty. The staged list updates after each entry.
5. To remove a staged penalty, press the **Remove** button next to it.
6. When done, press **Approve** to move to the approval step.

### 4. Review and approve

The approval step shows your final penalty list. Press:
- **Make Changes** to go back and edit the staged list.
- **Approve** to apply penalties and finalize the round.

### 5. What happens on approval

- All staged penalties are applied.
- Final positions and points are recomputed.
- The interim results and standings posts are replaced with the final corrected versions.
- The round is marked **Finalized** and the submission channel closes automatically.

---

## Signed Time Penalties (New Behavior)

Time penalties can be **positive** (driver gains time, may lose positions) or **negative** (driver loses time from their total, may gain positions):

| Input | Meaning |
|---|---|
| `5` or `+5` or `+5s` | Add 5 seconds to driver's race time |
| `-3` or `-3s` | Subtract 3 seconds from driver's race time |
| `DSQ` | Disqualify the driver in this session |

A zero-second penalty (`0`, `+0`, `-0`) is rejected — it has no effect.

---

## For Test Mode: Advancing Past a Round

In test mode, `/test-mode advance` will be blocked if the current division's round has been submitted but not yet finalized:

```
⏸️ Round 5 (United Kingdom) — Pro Division
   is in Post-Round Penalties state and must be finalized before advancing.
   Complete the penalty review in the submission channel.
```

Approve the penalty review (even with no penalties) to unblock the advance.

---

## Correcting a Finalized Round

Once a round is finalized, the inline penalty flow is closed. Use `/results amend` to correct results on a finalized round. That flow creates a new superseding session result and cascades standings updates.
