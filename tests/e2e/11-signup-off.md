# 11 — A league without the signup module

Builds a league that never turns the signup module on, and places its drivers as such a league
does: no window, no wizard, no approval. The configuration is confirmed straight into placements;
four fake drivers are created by `/test-mode roster add-bulk` and seated by `/driver assign`
straight from Not Signed Up; `/driver unassign` of a last placement and `/driver reject` of a
driver holding only unconfirmed placements are shown returning a driver to Not Signed Up; a
refusal is read naming a state in plain words; and the placements are confirmed. Read
[the README](README.md) first for the step format and how to report a failure. This spec reuses
`00-base.md`'s **Fixture** (its roles, channels, divisions, teams and calendar) and its
conventions for the acknowledgement and the log line, and its steps by reference, where it says
so; the drivers are its own, below.

> **Run this spec after `09-season-endings.md`, and not from the base.** The base's season fixed
> the signup module **on** when its configuration was confirmed, and the module cannot be changed
> for a season after that, so this spec cannot restore the base. `09` ends with a factory reset,
> which leaves the bot a fresh install and exactly what this spec starts from. Where you have
> not just run `09`, factory-reset first, or delete the databases as `00-base.md` step 2 says.
>
> **It ends with a season in which the signup module has never been on.** Nothing is saved as a
> backup. Before any other spec, run `00-base.md` again, from its step 1.

## What this spec does not walk

**A real driver.** Under test mode a real member is never placed, and test mode is fixed for the
season once its configuration is confirmed, so a member placed without a profile, and the driver
role granted when placements are confirmed, would need a second season with test mode off, its
points configurations made by hand, and a second real account. Both are left to the unit tests
(`test_driver_assign.py`, `test_confirm_placements_on.py`, `test_driver_reassign.py`), which hold
them. A fake driver has no account, so this spec grants no role at all, as every other does not.

## The drivers of this spec

Four fake drivers, the first four of `00-base.md`'s Fixture, created by step 12 with the IDs
the Fixture gives them:

| ID | Driver | Becomes |
|---|---|---|
| 9000000000000000001 | Adam Archer | `ALP`, Division 1 |
| 9000000000000000002 | Bruno Bianchi | `ALP`, Division 1 |
| 9000000000000000003 | Carlos Cortes | `BRV`, Division 1: placed, unassigned (step 17), then placed again (step 23) |
| 9000000000000000004 | Dirk Dekker | placed twice and turned down (steps 19 to 21), then `CHL`, Division 2 (step 24) |

## Conventions in this spec

As in `00-base.md`: every command is run in `#bot-commands`, as you, unless the step says
otherwise; **Ack:** is the `<doing>` part of the change queue's acknowledgement, and **Then:**
the outcome it is updated to; `<you>` in a log line is your display name and mention.

**A refusal** is answered to you alone and writes one line to `#bot-log`:
``⛔ <what> refused for <you> — <the reply's first line, without its mark>``. A step writes
**Reply:** and **Log:** for it. A refusal changes nothing and posts nothing.

**Under test mode `/driver` takes a driver as text**, a mention or an ID, and a team by its
shorthand, as in `00-base.md` step 32.

---

## Part A — The server, the roles and the teams, without the signup module

1. **Do** `00-base.md` steps 1 to 3: prepare the server, start from no database, start the bot.
   The `#signup` channel of its Fixture is not used here: leave it, or leave it out.

2. **Do** `00-base.md` steps 4 to 6: `/bot init`, `/bot base-role` and `/bot driver-role`.

3. **Do** `00-base.md` steps 7 to 10 and step 12: `/module enable` for `results`, `attendance`,
   `weather` and `images`, with step 7's refusal first. **Skip step 11, the signup module, and
   step 13, its channel.** Nothing in this spec turns the signup module on.

4. **Do** `00-base.md` steps 14 to 17: the image outputs, the eleven teams, `/team reserve-role`
   and `/team list`.

5. **A signup command is refused.** **Do:** `/signup open`

   **Expect:**
   - **Reply:** ``⛔ Signup module is not enabled. Use `/module enable signup` first.``
   - **Log:** ``⛔ `/signup open` refused for <you> — Signup module is not enabled. Use `/module enable signup` first.``

## Part B — The season, confirmed straight into placements

6. **Do** `00-base.md` steps 18 and 19: `/season setup game_edition:25`, then `/test-mode toggle`.
   **Skip step 20: there is no availability slot to add**, the slot being the signup
   module's setting.

7. **Do:** `/season config-review`

   **Expect:** as `00-base.md` step 21, except that:
   - the first message reads `  Signup: ❌ Disabled` in place of `  Signup: ✅ Enabled`, the other
     four modules `✅ Enabled`;
   - no message carries the signup module's settings, and nothing reports the signup channel;
   - the question is the same, with its **✅ Confirm configuration** button.
   - **Log:** `<you> | /season config-review | Review posted`

8. **Do:** within five minutes, press **✅ Confirm configuration** on the question.

   **Expect:**
   - **Ack:** `Confirming season 1's configuration`
   - **Then:** ``✅ Season #1's configuration is confirmed. The season is now in placements — build its divisions and calendar, place its drivers, then run `/season placements-review`.``
     There is no waiting for a signup window.
   - **Posts:** every message step 7 posted in `#bot-commands` is deleted.
   - **Log:**
     ```
     <you> | /season config-review | Confirmed
       season: Season #1
       stage: PLACEMENTS
     ```

9. **Do** `00-base.md` steps 27 and 28: `/division add` for `Division 1` and `Division 2`. They
   are accepted at once: the season is already in placements.

10. **Do** `00-base.md` step 29: `/round add-xml` with the Fixture's calendar.

11. **Do** `00-base.md` steps 30 and 31: the division channels, for Division 1 and for Division 2.

## Part C — Placing drivers straight from Not Signed Up

12. **Do:** `/test-mode roster add-bulk`, paste the following into **Paste roster.csv, header and
    all** in the form **Import a test roster**, and submit.

    ```
    ID,Driver name
    9000000000000000001,Adam Archer
    9000000000000000002,Bruno Bianchi
    9000000000000000003,Carlos Cortes
    9000000000000000004,Dirk Dekker
    ```

    **Expect:**
    - **Ack:** `Adding 4 test drivers`
    - **Then:**
      ```
      ✅ Added **4** test driver(s).
      `/test-mode roster list` shows them with the mentions result submission wants.
      ```
    - **Log:**
      ```
      <you> | /test-mode roster add-bulk | Success
        drivers_added: 4
      ```
    The four are at Not Signed Up, with no seat and no signup, and no signup is to come.

13. **Someone who is no driver.** **Do:** `/driver assign user:1234 division:Division 1 team:ALP`

    **Expect:**
    - **Reply:** ``⛔ No driver found for `1234`. Give a mention or an ID of any account the driver has held.``
    - **Log:** ``⛔ `/driver assign` refused for <you> — No driver found for `1234`. Give a mention or an ID of any account the driver has held.``
    - A real member holding no profile is refused the same way, under test mode: a real driver is
      never seated in a division under test.

14. **A fake driver is placed from Not Signed Up.** **Do:** `/driver assign user:9000000000000000001 division:Division 1 team:ALP`

    **Expect:**
    - **Ack:** `Assigning **Adam Archer** to **Alpha Racing** in **Division 1**`
    - **Then:** `✅ Assigned **Adam Archer** to **Alpha Racing** in **Division 1**.`
    - **Log:**
      ```
      <you> | /driver assign | Success
        user: Adam Archer (<@9000000000000000001>)
        team: Alpha Racing
        division: Division 1
      ```
    - No role is granted and no lineup is posted: the placement is unconfirmed until the season
      is approved. With the signup module on, the same command is refused for this driver, who
      is at Not Signed Up: only an Unassigned or Assigned driver is placed.

15. **Do:** `/driver assign user:9000000000000000002 division:Division 1 team:ALP`

    **Expect:** as step 14, for **Bruno Bianchi**, `<@9000000000000000002>`; `ALP`'s second seat.

16. **Do:** `/driver assign user:9000000000000000003 division:Division 1 team:BRV`

    **Expect:** as step 14, for **Carlos Cortes**, `<@9000000000000000003>`, **Bravo Motorsport**.

17. **A last placement removed returns the driver to Not Signed Up.**
    **Do:** `/driver unassign user:9000000000000000003 division:Division 1`

    **Expect:**
    - **Ack:** `Unassigning **Carlos Cortes** from **Division 1**`
    - **Then:** `✅ Removed **Carlos Cortes** from **Bravo Motorsport** in **Division 1**.`
    - **Log:**
      ```
      <you> | /driver unassign | Success
        user: Carlos Cortes (<@9000000000000000003>)
        division: Division 1
      ```
    The seat is free again, and with the signup module off Carlos has no signup to fall back to:
    he is at Not Signed Up, not Unassigned.

18. **A refusal names the state in plain words.**
    **Do:** `/driver unassign user:9000000000000000003 division:Division 1`

    **Expect:**
    - **Reply:** `⛔ Driver must be in Assigned state to be unassigned (current state: Not Signed Up).`
    - **Log:** ``⛔ `/driver unassign` refused for <you> — Driver must be in Assigned state to be unassigned (current state: Not Signed Up).``

19. **A driver holding only unconfirmed placements.** **Do**, in this order:
    1. `/driver assign user:9000000000000000004 division:Division 1 team:CHL`
    2. `/driver assign user:9000000000000000004 division:Division 2 team:CHL`

    **Expect**, for each, step 14's, for **Dirk Dekker**, `<@9000000000000000004>`,
    **Charlie GP** and the division of the command. Two placements, in two divisions, none of
    them confirmed.

20. **A driver with none is not turned down.** **Do:** `/driver reject user:9000000000000000003`

    **Expect:**
    - **Reply:** `⛔ **Carlos Cortes** holds no unconfirmed placement to turn down.`
    - **Log:** ``⛔ `/driver reject` refused for <you> — **Carlos Cortes** holds no unconfirmed placement to turn down.``

21. **Reject takes the driver's every placement.** **Do:** `/driver reject user:9000000000000000004`

    **Expect:**
    - **Ack:** `Turning down **Dirk Dekker**`
    - **Then:** `✅ Turned down **Dirk Dekker**. They are no longer signed up.`
    - **Log:**
      ```
      <you> | /driver reject | Success
        user: Dirk Dekker (<@9000000000000000004>)
      ```
    - Both of Dirk's placements are gone and their seats free, and he is at Not Signed Up. No
      role is taken: none was given for an unconfirmed placement.

22. **Both placements are gone.** **Do:** `/driver unassign user:9000000000000000004 division:Division 2`

    **Expect:**
    - **Reply:** `⛔ Driver must be in Assigned state to be unassigned (current state: Not Signed Up).`
    - **Log:** ``⛔ `/driver unassign` refused for <you> — Driver must be in Assigned state to be unassigned (current state: Not Signed Up).``

23. **A turned-down driver is placed again.** **Do:** `/driver assign user:9000000000000000003 division:Division 1 team:BRV`

    **Expect:** as step 14, for **Carlos Cortes**, `<@9000000000000000003>`, **Bravo Motorsport**
    in **Division 1**.

24. **Do:** `/driver assign user:9000000000000000004 division:Division 2 team:CHL`

    **Expect:** as step 14, for **Dirk Dekker**, `<@9000000000000000004>`, **Charlie GP** in
    **Division 2**.

## Part D — Confirming the placements

25. **Do:** `/season placements-review`

    **Expect:** as `00-base.md` step 35, except that:
    - the lineups hold what Part C placed: Division 1's `Alpha Racing` block with Adam Archer and
      Bruno Bianchi and its `Bravo Motorsport` block with Carlos Cortes, and Division 2's
      `Charlie GP` block with Dirk Dekker; every other seat blank, and no reserve;
      > UNVERIFIED: how the lineup picture draws a division with no reserve driver. Settle it on
      > the run and correct the step.
    - the report's `**Modules**` section lists Signup as `❌ Disabled`, with no settings message
      for it;
    - the question is the same, with its **✅ Approve** button.
    - **Log:** `<you> | /season placements-review | Review posted`

26. **Do:** within five minutes, press **✅ Approve** on the question, then **Approve without
    saving**.

    **Expect:** as `00-base.md` steps 36 and 37, except that the lineups hold the drivers of step
    25, the standings and the attendance sheets name them (Adam Archer, Bruno Bianchi and Carlos
    Cortes in Division 1, Dirk Dekker in Division 2), and:
    - **Then:** `✅ **Season approved and activated!**` and `Season #1 (ID: 1)`.
    - **Roles:** none granted: the four have no Discord account. With the signup module off a
      real driver would be given `@Driver` here, beside their division and team roles; the unit
      tests hold that.
    - **Log:**
      ```
      <you> | /season placements-review | Placements confirmed
        season: 1
        season_id: 1
      ```

## Where this leaves the server

A season in which the signup module has never been on, ongoing, with four fake drivers placed.
No backup is saved. **Run `00-base.md` again, from its step 1, before any other spec.**
