# Quickstart: Signup Module Expansion (025-signup-expansion)

## What changed

This feature does three things:

1. **Decouples signup module configuration from the enable command** — `/module enable signup` no longer takes parameters. Channel and role are now set via dedicated commands.
2. **Adds an optional auto-close timer to signup open** — signups can now be set to close automatically at a scheduled time.
3. **Adds per-division lineup announcement channels** — once all drivers are placed, a formatted lineup is posted to a configured division channel.

---

## How to set up the signup module (new flow)

### Step 1 — Enable the module

```
/module enable signup
```

The module is now active. No channel or roles are configured yet.

### Step 2 — Set the signup channel

```
/signup channel #general-signups
```

The bot will apply permission overwrites so only admins and base-role holders see it.

### Step 3 — Set the base role

```
/signup base-role @Drivers
```

Members with this role can see the signup channel and press the signup button.

### Step 4 — Set the complete role

```
/signup complete-role @SignedUp
```

This role is granted when a driver's signup is approved.

> All three steps can be done in any order and independently re-run to update values. The module does not need to be disabled first.

---

## Opening signups

### Without a timer

```
/signup open
```

Opens signups, mentions everyone with the base role, posts the signup button.

### With a close timer

```
/signup open close_time:2026-04-05 20:00
```

Same as above, but signups will automatically close at the specified UTC time. While the timer is active, `/signup close` is blocked.

> Format: `YYYY-MM-DD HH:MM` (UTC). Must be in the future.

---

## Configure a lineup announcement channel

Once drivers start being placed, you can set a channel per division to receive a formatted lineup post once all unassigned drivers have been placed:

```
/division lineup-channel Pro #pro-lineup
```

This is optional. It does not affect season approval. When every driver who signed up has been placed (no one left in the Unassigned state), the lineup is automatically posted to the configured channel.

---

## Season approval changes

If the signup module is enabled, season approval will now fail if any of these are unset:
- General signup channel
- Base role
- Complete role

The approval error message will name each missing item individually.

---

## What is NOT changed

- The signup wizard flow itself is unchanged.
- `/signup open` without `close_time` works exactly as before.
- Drivers in Unassigned or Assigned state are never affected by signup close events (manual or auto).
- The lineup channel is not required for season approval.
- All existing slot and configuration toggle commands are unchanged.
