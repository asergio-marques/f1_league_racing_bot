# 02 — Roster and team changes

Everything test mode lets a league do to its roster and its teams without a real driver:
`/test-mode roster add`, `add-bulk`, `sign-up-bulk`, `remove`, `clear` and `list`,
`/test-mode nationality` and `/test-mode set-former-driver`, and `/team add`, `modify`, `remove`
and `lineup`, each with the refusals and the edge cases the code has, not only the path that works.
Read [the README](README.md) first for the step format and how to report a failure, and run
[`00-base.md`](00-base.md) first: this spec reuses its **Fixture** throughout, every name, value
and driver, and its conventions for the acknowledgement and the log line.

## Where this spec starts, and why

**It starts by restoring the base, like every other spec, and ends by restoring it again.**

A fake driver is created, at Not Signed Up, and removed **in any stage of the season** while test
mode is on (`test_roster_changes.standing_refusal_on`). What the roster keeps is a fake driver who
has raced or holds a confirmed seat, which only `/driver sack` frees, and one part way through a
signup. The base ends with Season #1 approved and ongoing, every Fixture driver in a confirmed
seat, so the restored base is where those refusals are shown (Part B).

A fake driver is seated as any driver is: signed up, approved, then placed with `/driver assign`.
To walk that, the spec ends Season #1 and builds a second season through its signup window into
its placements, which `src/` allows only one way from the base:

- **`/season abort`** is refused: it abandons only a season whose placements were never
  confirmed (`ABORT_ONLY_BEFORE`), and step 30 shows the refusal.
- **`/season complete`** is refused while rounds are still to be run, and would delete the saved
  backup if it went through: completing a test season discards it.
- **`/season cancel`** ends an ongoing season, switches test mode off and deletes the fake
  drivers, and **keeps the saved backup**: a season cancelled is one abandoned
  (`cancellation_changes`, `end`). That is the route taken (Part C).

With no season live the team list is open, which is where `/team add`, `modify` and `remove`
are shown (Part D). A new season, Season #2, is then set up, given test mode, confirmed and its
signup window opened (Part E), and the roster commands are run there, through the window and into
placements (Part F). Part G restores the base, so the next spec finds it as `00-base.md` left it.

The other way — running `00-base.md` only as far as its placements and branching from there — was
rejected: it starts from no database file, so it costs the base itself, and the next spec could
restore nothing until `00-base.md` had been run again in full.

**The base keeps only one backup, and two things here would cost it.** Never turn test mode
off with `/test-mode toggle` while this spec runs, and never `/season complete`: either deletes
the saved backup, lock and all, and then Part G has nothing to restore. (`/test-mode backup save`
cannot overwrite it: `00-base.md` locks it.) Should either happen, run `00-base.md` again before
the next spec.

## Conventions in this spec

As in `00-base.md`: every command is run in `#bot-commands`, as you, unless the step says
otherwise; **Ack:** is the `<doing>` part of the change queue's acknowledgement
(`⏳ <doing>. This message will be updated when it is done; …`), and **Then:** the outcome it is
updated to; `<you>` in a log line is your display name and mention.

**A refusal** is answered to you alone, and writes one line to `#bot-log`:

```
⛔ <what> refused for <you> — <reason>
```

`<what>` is the command in backticks (`` `/test-mode roster add` ``) or, for a form, its title
in curly quotes (`the “Add a team” form`), and `<reason>` is the reply's first line without the
mark it opens with. A step writes **Log:** with the whole line. A refusal the change queue makes
at the ask carries no acknowledgement: the reply is the refusal, and nothing is queued.

**A view records nothing**: `/test-mode roster list`, `/team list` and `/team lineup` write no
line, a refusal of them included (`changes_nothing`).

**The roster list** (`/test-mode roster list`) answers, to you alone, a table in a code block
under the heading `**Fake Driver Roster**`, or `**Fake Driver Roster — <division>**` where a
division is given, with the columns `Name`, `Mention`, `State`, `Nationality` and `Seats` under a
rule of 110 `-`, and beneath it:

```
Copy mention strings above when submitting results in the format:
`Position, <@user_id>, <team shorthand>, ...`
```

Every fake driver is listed, in the order they were created, or, where a division is given, only
those seated in it. A state is shown in words (`Not Signed Up`, `Unassigned`, `Assigned`), a
nationality is the one the driver's signup gave (`—` where none), and a seat is the team's
shorthand, ` = `, its full name and its division in brackets, as `ALP = Alpha Racing (Division 1)`
(`none` where the driver holds none), so the shorthand the footer names can be copied from it. A
long roster comes in more than one message. A step quotes the table alone.

**A role made by hand.** Before step 1, create one role in Discord, outside the bot:
`@Lima Racing`, **below** the bot's own role and held by nobody. It is the free role a team is
given in Parts B and D. It is not in the base's backup, so Part G's restore leaves it on the
server: delete it by hand at the end if you wish.

---

## Part A — Restoring the base

1. **Do:** `/test-mode backup status`

   **Expect:**
   - **Reply:**
     ```
     📦 **Saved backup**
       Taken: <timestamp: the moment 00-base.md saved the backup>
       Size: <size: as 00-base.md saved it> KB
       Readable: yes
       Locked: locked by <your display name> at <the moment 00-base.md locked the backup, ISO 8601>
     ```
     A `Readable:` other than `yes`, or the reply
     ``📭 There is no saved backup. Take one with `/test-mode backup save`.``, means the base must
     be built again: run `00-base.md`.
   - **Log:** nothing; it is a view.

2. **Do:** `/test-mode backup restore`

   **Expect:**
   - **Reply**, with two buttons, **♻️ Restore** and **Cancel**:
     ```
     ⚠️ **Restore the backup taken <relative timestamp: when 00-base.md saved the backup>?**
     Everything the bot holds now — seasons, rounds, drivers, results and every scheduled job — is replaced by what that backup holds. A copy of the current database is kept beside it first.
     **The bot must be restarted afterwards** to pick it up.
     ```
   - **Log:** nothing yet.

3. **Do:** within two minutes, press **♻️ Restore**.

   **Expect:**
   - **Reply:**
     ```
     ✅ The backup is staged. **Restart the bot** and it will come back on the restored database — under a service it will restart itself; from a terminal, stop it and run it again.
     The database being replaced was copied to `bot.prerestore.db` first, so this can be walked back by hand if it was not what you wanted.
     ```
   - **Log:**
     ```
     <you> | /test-mode backup restore | Success
       restore: staged; the bot must be restarted to come back on it
     ```

4. **Restart the bot.** From a terminal, stop it and run `python -m leaguebot` again; under a
   service, restart the service (`systemctl restart <unit>`).

   **Expect:**
   - **Host log:** `Started on a restored database.`, before `Logged in as <bot user> (id=<bot id>)`.
   - `bot.bkup.db`, `scheduler.bkup.db` and `backup.lock` are still in the bot host folder.
   - Nothing posted on Discord.

---

## Part B — Once the season is running

Season #1 is ongoing, its signup window closed, and every Fixture driver holds a confirmed seat.
The roster still takes new fake drivers and lets go of those nobody has placed, but keeps every
driver who holds a confirmed seat. The team list is fixed, but a team's role is not.

### The roster, while the season runs

5. **A driver from a name.** **Do:** `/test-mode roster add driver_name:Yann Young`

   **Expect:**
   - **Ack:** `Adding fake driver **Yann Young**`
   - **Then:**
     ```
     ✅ Added fake driver **Yann Young**.
     Mention string (copy-paste into results): `<@9000000000000000046>`
     ```
     The command takes a name alone: the driver is at Not Signed Up, with no seat and no
     nationality. The ID is one above the highest fake driver's (`9000000000000000045`).
   - **Log:**
     ```
     <you> | /test-mode roster add | Success
       driver: Yann Young
     ```
   - **Posts:** nothing.

6. **A roster pasted.** **Do:** `/test-mode roster add-bulk`, paste the following into
   **Paste roster.csv, header and all** in the form **Import a test roster**, and submit.

   ```
   ID,Driver name
   9000000000000000101,Zoe Zimmer
   ```

   **Expect:**
   - **Ack:** `Adding 1 test driver`
   - **Then:**
     ```
     ✅ Added **1** test driver(s).
     `/test-mode roster list` shows them with the mentions result submission wants.
     ```
     The ID is the one the file gives, not one the bot allocates.
   - **Log:**
     ```
     <you> | /test-mode roster add-bulk | Success
       drivers_added: 1
     ```

7. **A driver in a confirmed seat.** **Do:** `/test-mode roster remove user_id:9000000000000000001`

   **Expect:**
   - **Reply:** ``⛔ **Adam Archer** has raced or holds a confirmed seat. Run `/driver sack` first; a fake driver who has raced stays until test mode ends.``
   - **Log:** ``⛔ `/test-mode roster remove` refused for <you> — **Adam Archer** has raced or holds a confirmed seat. Run `/driver sack` first; a fake driver who has raced stays until test mode ends.``

8. **A division of them.** **Do:** `/test-mode roster clear division:Division 1`

   **Expect:** the whole division refused, every driver kept named, in the order they were
   created, which is the Fixture's.
   - **Reply:** ``⛔ **Division 1** holds fake drivers who have raced or hold a confirmed seat: **Adam Archer**, **Bruno Bianchi**, **Carlos Cortes**, **Dirk Dekker**, **Emil Engel**, **Felix Fournier**, **Gabriel Gomes**, **Hiro Hayashi**, **Ian Irwin**, **Jari Jokinen**, **Kevin Keller**, **Liam Lavoie**, **Mateo Morales**, **Niels Nygaard**, **Olivier Orsini**, **Pim Prasert**, **Quinn Quayle**, **Rafael Rossi**, **Samuel Shaw**, **Tomas Torres**, **Ugo Ulivi**, **Victor Vance**, **Willem Wouters**, **Xavier Xu**. Run `/driver sack` first; a fake driver who has raced stays until test mode ends.``
   - **Log:** ``⛔ `/test-mode roster clear` refused for <you> — **Division 1** holds fake drivers who have raced or hold a confirmed seat: **Adam Archer**, **Bruno Bianchi**, **Carlos Cortes**, **Dirk Dekker**, **Emil Engel**, **Felix Fournier**, **Gabriel Gomes**, **Hiro Hayashi**, **Ian Irwin**, **Jari Jokinen**, **Kevin Keller**, **Liam Lavoie**, **Mateo Morales**, **Niels Nygaard**, **Olivier Orsini**, **Pim Prasert**, **Quinn Quayle**, **Rafael Rossi**, **Samuel Shaw**, **Tomas Torres**, **Ugo Ulivi**, **Victor Vance**, **Willem Wouters**, **Xavier Xu**. Run `/driver sack` first; a fake driver who has raced stays until test mode ends.``

9. **`/test-mode set-former-driver` takes a mention or an ID**, a fake driver's included. **Do:**
   `/test-mode set-former-driver user:1234 value:True`

   **Expect:**
   - **Reply:** ``⛔ No driver found for `1234`. Give a mention or an ID of any account the driver has held.``
   - **Log:** ``⛔ `/test-mode set-former-driver` refused for <you> — No driver found for `1234`. Give a mention or an ID of any account the driver has held.``

10. **Do:** `/test-mode set-former-driver user:9000000000000000101 value:True`

    **Expect:**
    - **Ack:** `Setting **Zoe Zimmer**'s former-driver flag to **True**`
    - **Then:**
      ```
      ✅ former_driver flag updated.
         User     : Zoe Zimmer
         Old value: False
         New value: True
      ```
    - **Log:**
      ```
      <you> | /test-mode set-former-driver | Success
        user: Zoe Zimmer (<@9000000000000000101>)
        former_driver: False -> True
      ```

11. **A driver who has raced.** The flag counts as racing. **Do:**
    `/test-mode roster remove user_id:9000000000000000101`

    **Expect:**
    - **Reply:** ``⛔ **Zoe Zimmer** has raced or holds a confirmed seat. Run `/driver sack` first; a fake driver who has raced stays until test mode ends.``
    - **Log:** ``⛔ `/test-mode roster remove` refused for <you> — **Zoe Zimmer** has raced or holds a confirmed seat. Run `/driver sack` first; a fake driver who has raced stays until test mode ends.``
    - Zoe Zimmer stays until the season's end deletes every fake driver (step 31).

12. **A driver nobody placed.** **Do:** `/test-mode roster remove user_id:9000000000000000046`

    **Expect:**
    - **Ack:** `Removing fake driver **Yann Young**`
    - **Then:** `✅ Removed fake driver **Yann Young**.` No team is named: the driver held no seat.
    - **Log:**
      ```
      <you> | /test-mode roster remove | Success
        driver: Yann Young
        user_id: 9000000000000000046
      ```

13. **Signing up, with the window closed.** **Do:** `/test-mode roster sign-up-bulk`, paste the
    following into **Paste ID,Driver name,Nationality** in the form **Sign up fake drivers**, and
    submit.

    ```
    ID,Driver name,Nationality
    9000000000000000001,Adam Archer,British
    9000000000000000102,Zack Ziegler,
    ```

    **Expect:** refused by the change queue at the ask, every fault together: first what the
    signup module answers, then what the database shows in the list. No acknowledgement, and no
    driver created.
    - **Reply:**
      ```
      ⛔ The list was not signed up — 2 problem(s). **No drivers were added.**
      • Signups are not open: fake drivers are signed up only while the window is open.
      • Line 2: a driver with ID 9000000000000000001 is already on this server.
      ```
    - **Log:** `⛔ the “Sign up fake drivers” form refused for <you> — The list was not signed up — 2 problem(s). **No drivers were added.**`

14. **Listing.** **Do:** `/test-mode roster list division:Division 2`

    **Expect:** as `00-base.md`'s listing of Division 2: headed `**Fake Driver Roster — Division 2**`,
    the 21 Division 2 drivers of the Fixture, each `Assigned`, with the Fixture's nationality, Aaron
    Abbott alone seated in `Charlie GP (Division 2)` and nobody in `Bravo Motorsport`. Zoe Zimmer
    holds no seat, so a list of a division leaves her out. No log line.

### Other refusals of a running season

15. **Do:** `/season setup game_edition:25`

    **Expect:**
    - **Reply:** `❌ A season is currently active for this server. Complete it before starting a new one.`
    - **Log:** ``⛔ `/season setup` refused for <you> — A season is currently active for this server. Complete it before starting a new one.``

### The team list, fixed

16. **Do:** `/team add`

    **Expect:**
    - **Reply:** ``⛔ The team list is fixed for Season 1 now that its configuration has been confirmed. `/team add` is available again once the season has ended, or while a new season is in configuration.``
      No form opens.
    - **Log:** ``⛔ `/team add` refused for <you> — The team list is fixed for Season 1 now that its configuration has been confirmed. `/team add` is available again once the season has ended, or while a new season is in configuration.``

17. **Do:** `/team remove name:KLO`

    **Expect:**
    - **Reply:** ``⛔ The team list is fixed for Season 1 now that its configuration has been confirmed. `/team remove` is available again once the season has ended, or while a new season is in configuration.``
    - **Log:** ``⛔ `/team remove` refused for <you> — The team list is fixed for Season 1 now that its configuration has been confirmed. `/team remove` is available again once the season has ended, or while a new season is in configuration.``

### A team's role, changed while the season runs

18. **Do:** `/team modify team:ALP`

    **Expect:** the form **Modify a team** opens holding **one field only**, **Role**, filled
    with `@Alpha Racing`. The **Shorthand** and **Full name** fields are not offered: the team
    list is fixed.

    **Then do:** choose `@Lima Racing` in **Role**, and submit.

    **Expect:**
    - **Ack:** `Changing **Alpha Racing**`
    - **Then:**
      ```
      ✅ Team "Alpha Racing" updated.
        Role: <@&<Lima Racing role id>>
      ```
      No line of drivers moved: a fake driver holds no roles and is left out of the move
      (`TeamRoleService.seated_user_ids_on`).
    - **Log:**
      ```
      <you> | /team modify | Success
        team: Alpha Racing
        shorthand: ALP
        role: Lima Racing (<@&<Lima Racing role id>>)
        seated drivers moved: 0
      ```
    - **Posts:** nothing. `#d1-lineup` and `#d2-lineup` keep their pictures: a lineup draws a
      team by its full name, never its role, and a change of role reposts no lineup.

19. **A role another team holds.** **Do:** `/team modify team:ALP`, choose `@Bravo Motorsport` in
    **Role**, and submit.

    **Expect:**
    - **Reply:** `⛔ <@&<Bravo Motorsport role id>> is already the role of "BRV". A role belongs to one team only. Nothing was written.`
    - **Log:** `⛔ the “Modify a team” form refused for <you> — <@&<Bravo Motorsport role id>> is already the role of "BRV". A role belongs to one team only. Nothing was written.`

20. **Do:** `/team modify team:ALP`. The form opens with **Role** filled with `@Lima Racing`, as
    step 18 left it. Choose `@Alpha Racing`, and submit.

    **Expect:**
    - **Ack:** `Changing **Alpha Racing**`
    - **Then:**
      ```
      ✅ Team "Alpha Racing" updated.
        Role: <@&<Alpha Racing role id>>
      ```
    - **Log:** as step 18, with `role: Alpha Racing (<@&<Alpha Racing role id>>)`.
    - `@Lima Racing` is free again, for Part D.

21. **A form submitted as it stands.** **Do:** `/team modify team:ALP`, and submit the form
    without touching it.

    **Expect:**
    - **Reply:** `Nothing changed: "Alpha Racing" stands as it was.` No acknowledgement: nothing
      is asked of the change queue.
    - **Log:**
      ```
      <you> | /team modify | Nothing changed
        team: Alpha Racing
        the form was submitted as it stood
      ```

22. **The Reserve team.** The autocomplete does not offer it: type it. **Do:** `/team modify team:Reserve`

    **Expect:**
    - **Reply:** ``⛔ The Reserve team's role is set with `/team reserve-role`, and its names are not a league's to change.`` No form opens.
    - **Log:** ``⛔ `/team modify` refused for <you> — The Reserve team's role is set with `/team reserve-role`, and its names are not a league's to change.``

23. **Do:** `/team modify team:XYZ`

    **Expect:**
    - **Reply:** ``⛔ No team in the server's list has the shorthand `XYZ`.``
    - **Log:** ``⛔ `/team modify` refused for <you> — No team in the server's list has the shorthand `XYZ`.``

### The lineups

**No command in this spec reposts a lineup.** The bot posts a division's lineup at the season's
approval, at a mid-season confirmation of placements, on `/driver move`, `release` and `sack`,
and after the attendance sanctions a penalty review applies or a penalty recalculation
makes, among others (`lineup_posting`, `penalty_recalculation`), every one of which needs a
real driver or belongs to another spec. A change of a team's role plans role moves only, a test
roster change posts nothing, and in placements no lineup is posted until approval, not even on
`/driver assign`. `/team lineup` draws the lineup on demand, which is what this section checks.

24. **Do:** `/team lineup`

    **Expect:**
    - **Reply**, seen by you alone: `**Division 1**`, a line break, `**Division 2**`, with two PNG
      pictures attached, Division 1's lineup then Division 2's, each as `00-base.md` describes
      them at its approval: Division 2's `Bravo Motorsport` with both seats blank and `Charlie GP`
      with one. Where a picture drew with notices, a second reply, to you alone, headed
      `⚠️ **Image notices**`, naming `lineup — Division 1` and `lineup — Division 2` with their
      notices.
    - **Posts:** nothing in `#d1-lineup` or `#d2-lineup`.
    - **Log:** nothing; it is a view.

25. **A division by its tier, shown to the channel.** **Do:** `/team lineup division:2 public:True`

    **Expect:**
    - **Posts** (`#bot-commands`, public): `**Division 2**`, with Division 2's lineup attached.
      Any image notices come in a reply to you alone, never in the channel.
    - **Log:** nothing.

26. **Do:** `/team lineup division:Division 9 public:True`

    **Expect:**
    - **Reply**, seen by you alone: ``⛔ Division `Division 9` not found.`` The public
      "thinking…" is taken down first, so nothing stays in the channel.
    - **Log:** nothing.

### Refused for the channel and for the role

27. **The wrong channel.** In `#d1-lineup`, not `#bot-commands`:

    **Do:** `/test-mode roster clear division:Division 1`

    **Expect:**
    - **Reply:** `⛔ This command can only be used in the configured interaction channel.`
    - **Log:** ``⛔ `/test-mode roster clear` refused for <you> — This command can only be used in the configured interaction channel.``

    **Then do**, in `#d1-lineup` still: `/test-mode roster list division:Division 1`

    **Expect:**
    - **Reply:** `⛔ This command can only be used in the configured interaction channel.`
    - **Log:** nothing: a view records nothing, its refusal included.

28. **Without the league admin role.** In Discord, take `@League Admin` off yourself. Back in
    `#bot-commands`:

    **Do:** `/test-mode roster clear division:Division 1`

    **Expect:**
    - **Reply:** `⛔ This command is a league admin's. You need **League Admin**.`
    - **Log:** ``⛔ `/test-mode roster clear` refused for <you> — This command is a league admin's. You need **League Admin**.``

    **Then do:** `/team lineup`

    **Expect:**
    - **Reply:** `⛔ You don't have permission to use this command. You need **League Manager**, or **League Admin**.`
      You hold neither role now. (A league admin passes a league manager's command; the
      manager's tier is the interaction role or the admin role.)
    - **Log:** nothing: `/team lineup` is a view.

    **Then:** give yourself `@League Admin` again, in Discord. Every later step needs it.

---

## Part C — Ending Season #1

29. **Do:** `/season cancel confirm:yes`

    **Expect:**
    - **Reply:** ``❌ Type exactly `CONFIRM` in the `confirm` field to proceed.``
    - **Log:** ``⛔ `/season cancel` refused for <you> — Type exactly `CONFIRM` in the `confirm` field to proceed.``

30. **Abort is not the way out of an ongoing season.** **Do:** `/season abort confirm:CONFIRM`

    **Expect:**
    - **Reply:** ``❌ `/season abort` is available only before a season's placements are first confirmed. An ongoing season is cancelled with `/season cancel`.``
    - **Log:** ``⛔ `/season abort` refused for <you> — `/season abort` is available only before a season's placements are first confirmed. An ongoing season is cancelled with `/season cancel`.``

31. **Do:** `/season cancel confirm:CONFIRM`

    **Expect:**
    - **Ack:** `Cancelling season 1`
    - **Then:** `✅ Season cancelled.`, followed, where a calendar drew with notices, by a blank
      line and a `⚠️ **Image notices**` section naming each calendar with its notices.
    - **Posts**, for each division in turn, Division 1 then Division 2 (`N` being its number):
      - `#dN-rsvp`:
        ```
        <@&<Division N role id>>
        📢 **Season Cancelled**
        The season has been cancelled. There are no further check-ins to answer.
        ```
      - `#dN-weather`:
        ```
        📢 **Season Cancelled**
        No further weather forecasts will be posted this season.
        ```
      - `#dN-results`:
        ```
        📢 **Season Cancelled**
        No further results will be posted this season.
        ```
      - `#dN-calendar`: the division's calendar, posted again as a picture with its three rounds
        cancelled, and the calendar posted at the base's approval deleted.
      - Nothing in `#dN-lineup`, `#dN-standings` or `#dN-attendance`: their pictures stay.
      The order is per division, in tier order: the check-in notice, the weather note, the
      results note, then the calendar, as the modules sign up to the cancellation notices.
      > UNVERIFIED: how the calendar draws a cancelled round.
    - **Roles:** none taken. Fake drivers hold none.
    - **Log:**
      ```
      <you> | /season cancel | Success
      ```
      Nothing follows it: a round called off carries its check-in's audit beneath the line only
      where a check-in call stood, and the base posted none.
    - **Test mode is off**, and every fake driver deleted, as the season's end does it. **The saved
      backup is kept**: `bot.bkup.db`, `scheduler.bkup.db` and `backup.lock` are still in the bot
      host folder.

    **To abandon the spec from here until step 49**, a restore is refused: test mode is off and
    no season is live, and the backup commands run in test mode alone. Run steps 47 and 49 (a
    season set up, test mode on), then Part G. The backup is kept throughout.

---

## Part D — No season live

Season #1 is cancelled and no season is live. Test mode is off, so every test-mode command but
the toggle is refused; the team list is open.

### Test mode, off

32. **Do:** `/test-mode backup status`

    **Expect:**
    - **Reply:** ``⛔ The backup commands run only while the server is in **test mode**. They copy and replace the whole database, which is not something to do to a league that is running. Turn test mode on with `/test-mode toggle` first.``
    - **Log:** nothing: the status is a view. The backup is still on disk (step 31); step 50 sees
      it again once test mode is back on.

33. **Do:** `/test-mode roster add driver_name:Adam Archer`

    **Expect:**
    - **Reply:** `⛔ This command is only available when test mode is enabled.`
    - **Log:** ``⛔ `/test-mode roster add` refused for <you> — This command is only available when test mode is enabled.``

34. **Do:** `/test-mode roster list division:Division 1`

    **Expect:**
    - **Reply:** `⛔ This command is only available when test mode is enabled.`
    - **Log:** nothing.

35. **Do:** `/test-mode nationality`

    **Expect:**
    - **Reply:** `⛔ This command is only available when test mode is enabled.`
    - **Log:** ``⛔ `/test-mode nationality` refused for <you> — This command is only available when test mode is enabled.``

36. **Do:** `/test-mode toggle`

    **Expect:**
    - **Reply:** ``⛔ Test mode can only be switched while a season is in configuration. Start one with `/season setup`; a season whose configuration is confirmed keeps test mode as it stands until the season ends.``
    - **Log:** ``⛔ `/test-mode toggle` refused for <you> — Test mode can only be switched while a season is in configuration. Start one with `/season setup`; a season whose configuration is confirmed keeps test mode as it stands until the season ends.``

37. **Do:** `/team lineup`

    **Expect:**
    - **Reply:** `⛔ No season is being raced, so no lineup is confirmed.`
    - **Log:** nothing.

### The team list, open

38. **Do:** `/team list`

    **Expect:** as `00-base.md`'s `/team list`: headed `**Server team list:**`, the eleven teams `ALP`
    to `KLO`, then `Reserve`. No log line.

39. **Do:** `/team add`, and in the form **Add a team** fill **Shorthand** `LMA`, **Full name**
    `Lima Racing`, **Role** `@Lima Racing`, and submit.

    **Expect:**
    - **Ack:** `Adding **Lima Racing**`
    - **Then:** ``✅ Team "Lima Racing" added as `LMA`, with role <@&<Lima Racing role id>>.``
    - **Log:**
      ```
      <you> | /team add | Success
        team: Lima Racing
        shorthand: LMA
        role: Lima Racing (<@&<Lima Racing role id>>)
      ```

40. **What a team may not be.** **Do**, once for each row below: `/team add`, fill the form with
    the row's **Shorthand**, **Full name** and **Role**, and submit. `@Driver` stands in as a role
    no team holds, so that the names alone are judged; none of these rows adds a team, so it is
    never given to one.

    | Shorthand | Full name | Role | Reply |
    |---|---|---|---|
    | `ALP` | `Alpha Two` | `@Driver` | `⛔ A default team named "ALP" already exists.` |
    | `alp` | `Alpha Two` | `@Driver` | `⛔ "alp" and "ALP" both reduce to "alp", so both would draw the same team image. Choose a more distinct name.` |
    | `Reserve` | `Reserve Two` | `@Driver` | `⛔ The team name "Reserve" is protected and cannot be managed.` |
    | `reserve` | `Reserve Two` | `@Driver` | `⛔ "reserve" reduces to "reserve", which is reserved for the Reserve team of every division. Choose another name.` |
    | `A,B` | `Alpha Bravo` | `@Driver` | `⛔ "A,B" holds a comma. A shorthand is typed into the comma-separated rows of a results submission, so it cannot hold one.` |
    | `???` | `Question Racing` | `@Driver` | `⛔ "???" holds no letter or digit, so it cannot name an image file. Choose a name with at least one.` |
    | `MKE` | `alpha racing` | `@Driver` | `⛔ "Alpha Racing" is already the full name of a team. A full name names one team only.` |
    | `NOV` | `**November**` | `@Driver` | ```⛔ The formatting `` **November** `` in the full name reads differently as text and on a graphic. Remove it, then try again.``` |
    | `NOV` | `November Racing` | `@Alpha Racing` | `⛔ <@&<Alpha Racing role id>> is already the role of "ALP". A role belongs to one team only.` |
    | `NOV` | `November Racing` | `@Lima Racing` | `⛔ <@&<Lima Racing role id>> is already the role of "LMA". A role belongs to one team only.` |
    | `NOV` | `November Racing` | the bot's own role | `⛔ <@&<bot's role id>> is managed by an integration — a bot, a subscription or a linked role — so Discord will not let me grant it. Choose a role of the team's own.` |

    **Expect**, for each row: the **Reply** as the table gives it, to you alone, and no
    acknowledgement; and the **Log**
    `⛔ the “Add a team” form refused for <you> — <the reply, without its ⛔>`.
    The role is judged before the names, so the last three rows are refused for the role
    whatever their names.
    > UNVERIFIED: that the form's role picker offers the bot's own role. Where it does not, skip
    > the last row.

41. **Do:** `/team modify team:LMA`

    **Expect:** the form **Modify a team** opens with all three fields, filled: **Shorthand**
    `LMA`, **Full name** `Lima Racing`, **Role** `@Lima Racing`. The names are offered: the
    team list is open.

    **Then do:** set **Shorthand** to `LIM` and **Full name** to `Lima Motorsport`, leave
    **Role**, and submit.

    **Expect:**
    - **Ack:** `Changing **Lima Racing**`
    - **Then:**
      ```
      ✅ Team "Lima Motorsport" updated.
        Full name: "Lima Racing" → "Lima Motorsport"
        Shorthand: `LMA` → `LIM`
        Its artwork is now looked for as `lim`, not `lma` — rename the file in the team image directory.
      ```
    - **Log:**
      ```
      <you> | /team modify | Success
        team: Lima Motorsport
        shorthand: LIM
        seated drivers moved: 0
      ```
      No `role:` line: the role was not changed.

42. **Do:** `/team modify team:LIM`, set **Shorthand** to `ALP`, and submit.

    **Expect:**
    - **Reply:** `⛔ A default team named "ALP" already exists.`
    - **Log:** `⛔ the “Modify a team” form refused for <you> — A default team named "ALP" already exists.`
    - `LIM` is unchanged.

43. **The Reserve team cannot be removed.** The autocomplete does not offer it: type it.
    **Do:** `/team remove name:Reserve`

    **Expect:**
    - **Reply:** `⛔ The team name "Reserve" is protected and cannot be managed.`
    - **Log:** ``⛔ `/team remove` refused for <you> — The team name "Reserve" is protected and cannot be managed.``

44. **Do:** `/team remove name:XYZ`

    **Expect:**
    - **Reply:** ``⛔ No team in the server's list has the shorthand `XYZ`.``
    - **Log:** ``⛔ `/team remove` refused for <you> — No team in the server's list has the shorthand `XYZ`.``

45. **Do:** `/team remove name:LIM`

    **Expect:**
    - **Ack:** `Removing **Lima Motorsport**`
    - **Then:** `✅ Team "Lima Motorsport" removed from the server list.`
    - **Log:**
      ```
      <you> | /team remove | Success
        team: Lima Motorsport
        shorthand: LIM
      ```

46. **Do:** `/team list`

    **Expect:** as step 38: the Fixture's eleven teams and `Reserve`, and no `Lima Motorsport`.

---

## Part E — Season #2, up to its signup window

A season set up now is numbered one above the highest the league has committed: Season #1 is
cancelled, so this is Season #2.

47. **Do:** `/season setup game_edition:25`

    **Expect:**
    - **Ack:** `Starting the season setup`
    - **Then:**
      ```
      ✅ Season setup started. **Season #2 (F1 25)** is being configured.

      Settle its team list, its modules and their settings, and test mode, then run `/season config-review` to confirm the configuration.
      Its divisions and rounds are added once it reaches placements.
      ```
    - **Log:**
      ```
      <you> | /season setup | Success
        season: Season #2 (F1 25)
      ```

48. **Do:** `/team list`

    **Expect:**
    - **Reply:**
      ```
      ⚠️ Season 2 divisions differ from the server list.

      **Server list:**
        Alpha Racing — `ALP` → <@&<Alpha Racing role id>>
        …one line per team, as in step 38, Reserve last…
        Reserve — `Reserve` → <@&<Reserve role id>>

      **Season 2 effective teams:**
        *(empty)*
      ```
      Season #2 has no division yet, so it holds no team, and the list reads as differing from
      the server's (`TeamCog.team_list`). Step 76 shows it agreeing once the divisions exist.
    - **Log:** nothing.

49. **Do:** `/test-mode toggle`

    **Expect:**
    - **Ack:** `Turning test mode on`
    - **Then:** ``✅ Test mode **enabled**. Use `/test-mode advance` to step through phases, or `/test-mode review` to inspect season status.``
      No `📌 Added default point configs` line: the server already holds `Standard` and
      `Half Points` from Season #1, and test mode seeds them only on a server holding none
      (`seed_test_configs_on`). Nor are they attached to Season #2: step 53 attaches one.
    - **Log:**
      ```
      <you> | /test-mode toggle | Success
        test_mode: enabled
      ```

50. **The backup survived the cancellation.** **Do:** `/test-mode backup status`

    **Expect:** as step 1: taken when `00-base.md` saved it, readable, locked. No log line.

51. **A driver in configuration.** The roster changes in any stage. **Do:**
    `/test-mode roster add driver_name:Emil Engel`

    **Expect:**
    - **Ack:** `Adding fake driver **Emil Engel**`
    - **Then:**
      ```
      ✅ Added fake driver **Emil Engel**.
      Mention string (copy-paste into results): `<@9000000000000000001>`
      ```
      The cancellation deleted every fake driver, so the ID allocated starts again from the first:
      a single add takes one above the highest fake driver's on the server, and there is none.
    - **Log:**
      ```
      <you> | /test-mode roster add | Success
        driver: Emil Engel
      ```

52. **Do:** `/results config append name:Half Pionts`

    **Expect:**
    - **Reply:** ``❌ Config **Half Pionts** does not exist on this server, so nothing was attached. Check the spelling, or create it with `/results config add`.``
    - **Log:** ``⛔ `/results config append` refused for <you> — Config **Half Pionts** does not exist on this server, so nothing was attached. Check the spelling, or create it with `/results config add`.``

53. **Do:** `/results config append name:Standard`

    **Expect:**
    - **Ack:** `Attaching config **Standard** to the current season`
    - **Then:** `✅ Config **Standard** attached to the current season.`
    - **Log:**
      ```
      <you> | /results config append | Success
        config: Standard
      ```

54. **Do:** `/season config-review`

    **Expect:** as `00-base.md`'s `/season config-review`, with `Season #2 — F1 25` in the first
    message's heading and with the same remark on the acknowledgement left standing. In particular
    no message opening `⛔ **The configuration cannot be confirmed yet.**`: without step 53 it
    would carry ``No points configuration is attached to this season — `/results config append`.``
    The results section reads `**Points Configs:** Standard`, where `00-base.md` had both
    configurations attached.

55. **Do:** within five minutes, press **✅ Confirm configuration** on the question.

    **Expect:**
    - **Ack:** `Confirming season 2's configuration`
    - **Then:** ``✅ Season #2's configuration is confirmed. The season now waits for its signup window — open it with `/signup open`.``
      The signup module is on, so the season waits for its window, test mode or not.
    - **Posts:** every message step 54 posted in `#bot-commands` is deleted.
    - **Log:**
      ```
      <you> | /season config-review | Confirmed
        season: Season #2
        stage: WAITING
      ```

56. **Divisions wait for placements.** **Do:** `/division add name:Division 1 role:@Division 1 tier:1`

    **Expect:**
    - **Reply:** `⛔ Divisions can only be added while the season is in placements — once its configuration is confirmed and its signup window has closed.`
    - **Log:** ``⛔ `/division add` refused for <you> — Divisions can only be added while the season is in placements — once its configuration is confirmed and its signup window has closed.``

57. **Do:** `/signup open`

    **Expect:**
    - **Ack:** `Opening signups`
    - **Then:** `✅ Signups opened. Button posted in <#<signup id>>.`
    - **Posts** (`#signup`): an embed titled `🏁 Driver Signups Are Open!`, listing the base's
      availability slots under `**Available time slots:**` and `No tracks specified` under
      `**Tracks:**`, ending `<@&<Members role id>> — click below to sign up!`, with a **Sign Up**
      button. The `🔒 Signups are now closed.` notice the base's close left in `#signup` is
      deleted.
      > UNVERIFIED: that the base's closed notice still stands to be deleted; where an earlier spec
      > took it down, the deletion finds it gone and nothing more is said.
    - **Log:** `<you> | /signup open | Success`
    - Season #2 is now in its signup window.

---

## Part F — The roster, through the signup window and into placements

Season #2's signup window is open and it has no division yet; Emil Engel is its one fake driver,
at Not Signed Up. Test mode's nationality switch is **on**, as `00-base.md` left it.

### Listing

58. **Do:** `/test-mode roster list`

    **Expect:**
    - **Reply**, under `**Fake Driver Roster**`:
      ```
      Name                 Mention                        State                      Nationality    Seats
      --------------------------------------------------------------------------------------------------------------
      Emil Engel           <@9000000000000000001>         Not Signed Up              —              none
      ```
    - **Log:** nothing.

59. **Do:** `/test-mode roster list division:Division 1`

    **Expect:**
    - **Reply:** `⛔ Division 'Division 1' not found in the active season.` Season #2 has no division
      until placements.
    - **Log:** nothing.

### Importing a roster

60. **A file wrong on its own.** **Do:** `/test-mode roster add-bulk`, paste the following into
    **Paste roster.csv, header and all**, and submit.

    ```
    ID,Driver name
    9000000000000000002,Bruno Bianchi,Italian
    1234,Carlos Cortes
    abc,Dirk Dekker
    9000000000000000006,Felix Fournier
    9000000000000000006,Gabriel Gomes
    9000000000000000008,Felix Fournier
    9000000000000000009,
    9000000000000000010,**Jari Jokinen**
    ```

    **Expect:** the form reads every line before refusing, and refuses the whole file; no
    acknowledgement, nothing queued.
    - **Reply:**
      ```
      ⛔ The roster was not imported — 7 problem(s). **No drivers were added.**
      • Line 2: expected 2 columns (ID, driver name) but found 3.
      • Line 3: 1234 is below the range test drivers use. A real Discord account cannot be created as a fake driver.
      • Line 4: `abc` is not a driver ID.
      • Line 6: driver ID 9000000000000000006 is already used on line 5.
      • Line 7: `Felix Fournier` is already named on line 5.
      • Line 8: the driver has no name.
      • Line 9: The formatting `` **Jari Jokinen** `` in the driver name reads differently as text and on a graphic. Remove it, then try again.
      ```
      Line 5 is sound, and is not listed.
    - **Log:** `⛔ the “Import a test roster” form refused for <you> — The roster was not imported — 7 problem(s). **No drivers were added.**`

61. **Do:** `/test-mode roster add-bulk`, paste the following, and submit.

    ```
    ID,Driver name
    9000000000000000002,Bruno Bianchi
    9000000000000000003,Carlos Cortes
    9000000000000000004,Dirk Dekker
    ```

    **Expect:**
    - **Ack:** `Adding 3 test drivers`
    - **Then:**
      ```
      ✅ Added **3** test driver(s).
      `/test-mode roster list` shows them with the mentions result submission wants.
      ```
    - **Log:**
      ```
      <you> | /test-mode roster add-bulk | Success
        drivers_added: 3
      ```
    - **Posts:** nothing. The three are at Not Signed Up, like Emil Engel.

62. **The same file again.** **Do:** step 61 again, the same paste.

    **Expect:** refused whole by the change queue at the ask; nothing added twice, no
    acknowledgement.
    - **Reply:**
      ```
      ⛔ The roster was not imported — 3 problem(s). **No drivers were added.**
      • Line 2: a driver with ID 9000000000000000002 is already on this server.
      • Line 3: a driver with ID 9000000000000000003 is already on this server.
      • Line 4: a driver with ID 9000000000000000004 is already on this server.
      ```
    - **Log:** `⛔ the “Import a test roster” form refused for <you> — The roster was not imported — 3 problem(s). **No drivers were added.**`

### Creating one driver

63. **A name that would notify.** **Do:** `/test-mode roster add driver_name:@everyone Dekker`

    **Expect:**
    - **Reply:** ``⛔ `@everyone` in the driver name would notify everybody it covers. Remove it, then try again.``
    - **Log:** ``⛔ `/test-mode roster add` refused for <you> — `@everyone` in the driver name would notify everybody it covers. Remove it, then try again.``
    - Nobody is notified: the mention is quoted in code in both.

### Signing fake drivers up

64. **A list wrong on its own.** **Do:** `/test-mode roster sign-up-bulk`, paste the following into
    **Paste ID,Driver name,Nationality**, and submit.

    ```
    ID,Driver name,Nationality
    9000000000000000021,Felix Fournier
    9000000000000000022,Gabriel Gomes,Klingon
    ```

    **Expect:** refused by the form before anything is asked; no acknowledgement. A row of an ID and
    a name alone is refused for its width: a blank nationality is still written, as a comma.
    - **Reply:**
      ```
      ⛔ The list was not signed up — 2 problem(s). **No drivers were added.**
      • Line 2: expected 3 columns (ID, driver name, nationality) but found 2.
      • Line 3: `Klingon` is not a nationality the bot knows.
      ```
    - **Log:** `⛔ the “Sign up fake drivers” form refused for <you> — The list was not signed up — 2 problem(s). **No drivers were added.**`

65. **Do:** `/test-mode nationality`

    **Expect:**
    - **Ack:** `Turning test-mode nationality off`
    - **Then:**
      ```
      ✅ Test-mode nationality: **OFF**.
      The signup wizard does not ask it, and graphics draw no flag at all.
      ```
    - **Log:**
      ```
      <you> | /test-mode nationality | Success
        test_mode_nationality: disabled
      ```

66. **A nationality the switch does not collect, and a driver already made.** **Do:**
    `/test-mode roster sign-up-bulk`, paste the following, and submit.

    ```
    ID,Driver name,Nationality
    9000000000000000002,Bruno Bianchi,
    9000000000000000021,Felix Fournier,British
    ```

    **Expect:** refused by the change queue at the ask, every fault together, the database's before
    the switch's; no acknowledgement, and Felix Fournier not created.
    - **Reply:**
      ```
      ⛔ The list was not signed up — 2 problem(s). **No drivers were added.**
      • Line 2: a driver with ID 9000000000000000002 is already on this server.
      • Line 3: a nationality is given, but `/test-mode nationality` is off. Leave it blank, or turn it on first.
      ```
      Bruno Bianchi was created by step 61, at Not Signed Up; sign-up-bulk creates every driver it
      names, and signs up none it did not create.
    - **Log:** `⛔ the “Sign up fake drivers” form refused for <you> — The list was not signed up — 2 problem(s). **No drivers were added.**`

67. **Do:** `/test-mode nationality`

    **Expect:**
    - **Ack:** `Turning test-mode nationality on`
    - **Then:**
      ```
      ✅ Test-mode nationality: **ON**.
      The signup wizard asks a fake driver's nationality.
      ```
    - **Log:**
      ```
      <you> | /test-mode nationality | Success
        test_mode_nationality: enabled
      ```

68. **Do:** `/test-mode roster sign-up-bulk`, paste the following, and submit. Felix Fournier's
    nationality is given as a country, and Xavier Xu's is left blank: a driver may lack one.

    ```
    ID,Driver name,Nationality
    9000000000000000021,Felix Fournier,United Kingdom
    9000000000000000022,Gabriel Gomes,Brazilian
    9000000000000000023,Willem Wouters,German
    9000000000000000024,Xavier Xu,
    ```

    **Expect:**
    - **Ack:** `Signing up 4 fake drivers`
    - **Then:** ``✅ Signed up and approved **4** fake driver(s). Close signups with `/signup close`, then seat each with `/driver assign`.``
    - **Log:**
      ```
      <you> | /test-mode roster sign-up-bulk | Success
        drivers_signed_up: 4
      ```
    - **Posts:** nothing. No signup channel, review panel or role: the helper records each
      signup with the default answers and approves it, leaving the driver Unassigned.

69. **Do:** `/test-mode roster list`

    **Expect:**
    - **Reply**, under `**Fake Driver Roster**`:
      ```
      Name                 Mention                        State                      Nationality    Seats
      --------------------------------------------------------------------------------------------------------------
      Emil Engel           <@9000000000000000001>         Not Signed Up              —              none
      Bruno Bianchi        <@9000000000000000002>         Not Signed Up              —              none
      Carlos Cortes        <@9000000000000000003>         Not Signed Up              —              none
      Dirk Dekker          <@9000000000000000004>         Not Signed Up              —              none
      Felix Fournier       <@9000000000000000021>         Unassigned                 British        none
      Gabriel Gomes        <@9000000000000000022>         Unassigned                 Brazilian      none
      Willem Wouters       <@9000000000000000023>         Unassigned                 German         none
      Xavier Xu            <@9000000000000000024>         Unassigned                 —              none
      ```
      The country is stored as its nationality.
    - **Log:** nothing.

### A signup in progress

70. **Do:** `/signup start user:9000000000000000001`

    **Expect:**
    - **Ack:** `Signing up`
    - **Then:** `✅ **Emil Engel**'s signup channel has been created: <#<Emil Engel's signup channel id>>`
    - **Posts** (Emil Engel's new signup channel, seen by the staff roles alone):
      ```
      Welcome to the signup wizard, **Emil Engel**!
      **Step 1 — Nationality**
      Enter your nationality (e.g. `British`) or country name (e.g. `United Kingdom`), or type `other`.
      ```
      The wizard asks the nationality: the switch is on again (step 67).
    - **Log:**
      ```
      <you> | Signup | Started
        driver: Emil Engel
      ```

71. **Do:** `/test-mode roster remove user_id:9000000000000000001`

    **Expect:**
    - **Reply:** `⛔ **Emil Engel** has a signup in progress. Finish it — approve or reject it — or cancel it first.`
    - **Log:** ``⛔ `/test-mode roster remove` refused for <you> — **Emil Engel** has a signup in progress. Finish it — approve or reject it — or cancel it first.``

72. **Do:** `/signup cancel user:9000000000000000001`. In the form **Cancel signup**, leave
    **Reason (told to the driver)** empty, and submit.

    **Expect:**
    - **Ack:** `Cancelling a signup`
    - **Then:** `✅ Signup cancelled.`
    - **Posts** (Emil Engel's signup channel):
      ```
      **Emil Engel** ❌ Your signup has been cancelled by **<your display name>**.
      This channel will be automatically deleted in 24 hours.
      ```
    - **Log:**
      ```
      <you> | Signup | Cancelled
        driver: Emil Engel
      ```
    - Emil Engel is at Not Signed Up again.

### Into placements

73. **Do:** `/signup close`

    **Expect:** asked at once, with no confirmation: nobody is part way through a signup.
    - **Ack:** `Closing signups`
    - **Then:** `✅ Signups closed.`
    - **Posts** (`#signup`): the message with the **Sign Up** button is deleted, and
      `🔒 Signups are now closed.` posted.
    - **Log:**
      ```
      <you> | /signup close | Success
        drivers_returned_to_not_signed_up: 0
      ```
    - Season #2 is now in placements.

74. **Do:** `/division add name:Division 1 role:@Division 1 tier:1`

    **Expect:**
    - **Ack:** `Adding division **Division 1** (Tier 1)`
    - **Then:**
      ```
      ✅ Division **Division 1** (Tier 1) added.
      Role: <@&<Division 1 role id>>

      **Divisions:**
        📂 **Division 1** (Tier 1) | <@&<Division 1 role id>>
      ```
    - **Log:**
      ```
      <you> | /division add | Success
        division: Division 1, tier: 1
      ```

75. **Do:** `/division add name:Division 2 role:@Division 2 tier:2`

    **Expect:**
    - **Ack:** `Adding division **Division 2** (Tier 2)`
    - **Then:**
      ```
      ✅ Division **Division 2** (Tier 2) added.
      Role: <@&<Division 2 role id>>

      **Divisions:**
        📂 **Division 1** (Tier 1) | <@&<Division 1 role id>>
        📂 **Division 2** (Tier 2) | <@&<Division 2 role id>>
      ```
    - **Log:**
      ```
      <you> | /division add | Success
        division: Division 2, tier: 2
      ```

    Season #2 needs no calendar and no division channels here: nothing in this spec reviews or
    approves it.

76. **Do:** `/team list`

    **Expect:** headed `**Server team list (Season 2 will use this list):**`, then the lines of
    step 38. No log line.

### Seating with `/driver assign`

Each division holds the Fixture's eleven teams of two seats and a Reserve team, all empty.

77. **A driver who never signed up.** **Do:** `/driver assign user:9000000000000000002 division:Division 1 team:ALP`

    **Expect:**
    - **Reply:** `⛔ Driver must be Unassigned or Assigned to be placed (current state: Not Signed Up).`
    - **Log:** ``⛔ `/driver assign` refused for <you> — Driver must be Unassigned or Assigned to be placed (current state: Not Signed Up).``
    - A fake driver made by `roster add` or `add-bulk` is seated only once its signup is approved.

78. **Do**, once for each row below, in this order:
    `/driver assign user:<ID> division:Division 1 team:<Team>`

    | ID | Driver | Team | Full name |
    |---|---|---|---|
    | `9000000000000000021` | Felix Fournier | `ALP` | Alpha Racing |
    | `9000000000000000022` | Gabriel Gomes | `BRV` | Bravo Motorsport |
    | `9000000000000000023` | Willem Wouters | `Reserve` | Reserve |
    | `9000000000000000024` | Xavier Xu | `Reserve` | Reserve |

    **Expect**, for each row:
    - **Ack:** `Assigning **<Driver>** to **<Full name>** in **Division 1**`
    - **Then:** `✅ Assigned **<Driver>** to **<Full name>** in **Division 1**.`
    - **Log:**
      ```
      <you> | /driver assign | Success
        user: <Driver> (<@<ID>>)
        team: <Full name>
        division: Division 1
      ```
    - **Posts:** nothing, and no role given: the placement is unconfirmed until the season's
      placements are approved.

79. **Do:** `/test-mode roster list division:Division 1`

    **Expect:**
    - **Reply**, under `**Fake Driver Roster — Division 1**`:
      ```
      Name                 Mention                        State                      Nationality    Seats
      --------------------------------------------------------------------------------------------------------------
      Felix Fournier       <@9000000000000000021>         Assigned                   British        ALP = Alpha Racing (Division 1)
      Gabriel Gomes        <@9000000000000000022>         Assigned                   Brazilian      BRV = Bravo Motorsport (Division 1)
      Willem Wouters       <@9000000000000000023>         Assigned                   German         Reserve = Reserve (Division 1)
      Xavier Xu            <@9000000000000000024>         Assigned                   —              Reserve = Reserve (Division 1)
      ```
      The four unseated drivers are left out of a list of a division.
    - **Log:** nothing.

### Removing one driver

An unconfirmed seat is no confirmed one: a driver placed in placements and not yet approved is
removed like any other.

80. **Do:** `/test-mode roster remove user_id:abc`

    **Expect:**
    - **Reply:** ``❌ `user_id` must be a numeric Discord user ID.``
    - **Log:** ``⛔ `/test-mode roster remove` refused for <you> — `user_id` must be a numeric Discord user ID.``

81. **Do:** `/test-mode roster remove user_id:9000000000000000099`

    **Expect:**
    - **Reply:** `⛔ No test driver found with that user ID on this server.`
    - **Log:** ``⛔ `/test-mode roster remove` refused for <you> — No test driver found with that user ID on this server.``

82. **A seated driver.** **Do:** `/test-mode roster remove user_id:9000000000000000022`

    **Expect:**
    - **Ack:** `Removing fake driver **Gabriel Gomes**`
    - **Then:** `✅ Removed fake driver **Gabriel Gomes** from **Bravo Motorsport**.`
    - **Log:**
      ```
      <you> | /test-mode roster remove | Success
        driver: Gabriel Gomes
        team: Bravo Motorsport
        user_id: 9000000000000000022
      ```

83. **The same driver again.** **Do:** step 82 again.

    **Expect:** as step 81: the driver is gone.

84. **An unseated driver.** **Do:** `/test-mode roster remove user_id:9000000000000000004`

    **Expect:**
    - **Ack:** `Removing fake driver **Dirk Dekker**`
    - **Then:** `✅ Removed fake driver **Dirk Dekker**.` No team is named: the driver held no seat.
    - **Log:**
      ```
      <you> | /test-mode roster remove | Success
        driver: Dirk Dekker
        user_id: 9000000000000000004
      ```

### Clearing a division

85. **Do:** `/test-mode roster clear division:Division 9`

    **Expect:**
    - **Reply:** `⛔ Division 'Division 9' not found in the active season.`
    - **Log:** ``⛔ `/test-mode roster clear` refused for <you> — Division 'Division 9' not found in the active season.``

86. **A division with nobody to clear.** **Do:** `/test-mode roster clear division:Division 2`

    **Expect:**
    - **Ack:** `Clearing the fake drivers from **Division 2**`
    - **Then:** `ℹ️ No fake drivers found in **Division 2**.`
    - **Log:**
      ```
      <you> | /test-mode roster clear | Nothing changed
        division: Division 2
        reason: no fake drivers to remove
      ```

87. **A division named in another case.** The division is found whatever its case, and named
    back as typed. **Do:** `/test-mode roster clear division:division 1`

    **Expect:**
    - **Ack:** `Clearing the fake drivers from **division 1**`
    - **Then:** `✅ Removed **3** fake driver(s) from **division 1**.`
    - **Log:**
      ```
      <you> | /test-mode roster clear | Success
        division: division 1
        removed_drivers: 3
      ```

88. **Do:** `/test-mode roster list division:Division 1`

    **Expect:**
    - **Reply:** ``ℹ️ No fake drivers in **Division 1**. Use `/test-mode roster add` to create some.``
    - **Log:** nothing.

89. **A clear takes only the seated.** **Do:** `/test-mode roster list`

    **Expect:**
    - **Reply**, under `**Fake Driver Roster**`:
      ```
      Name                 Mention                        State                      Nationality    Seats
      --------------------------------------------------------------------------------------------------------------
      Emil Engel           <@9000000000000000001>         Not Signed Up              —              none
      Bruno Bianchi        <@9000000000000000002>         Not Signed Up              —              none
      Carlos Cortes        <@9000000000000000003>         Not Signed Up              —              none
      ```
    - **Log:** nothing.

---

## Part G — Restoring the base

Season #2 is left in placements, test mode on. Putting the base back is what lets the next spec
start where `00-base.md` left off.

90. **Do:** steps 1 to 4 again: `/test-mode backup status`, `/test-mode backup restore`, press
    **♻️ Restore**, and restart the bot.

    **Expect:** as steps 1 to 4. `bot.prerestore.db` and `scheduler.prerestore.db` now hold
    Season #2 as this part found it, should it be wanted.

91. **Do:** `/test-mode roster list division:Division 1`

    **Expect:** as `00-base.md`'s listing of Division 1: the 24 Division 1 drivers of the
    Fixture, each `Assigned`, every seat as the base left it. Season #1 is ongoing again, and
    Season #2 and the cancellation are gone.

92. **Do:** `/team list`

    **Expect:** as `00-base.md`'s `/team list`: the Fixture's eleven teams and `Reserve`, `ALP`
    back on `@Alpha Racing`.

Discord is not restored with the database, so a few things stand on the server that the base
does not record, and a few the base records are gone:

- the cancellation notices of step 31 stay in their channels, and the calendars posted at the
  base's approval, which the base still records, were deleted then. A later spec that posts a
  calendar again finds the recorded one gone and carries on (`standing_posts` passes over a
  message already deleted);
- in `#signup`, the `🔒 Signups are now closed.` of step 73 stands, and the notice the base
  records was deleted by step 57;
- Emil Engel's signup channel (step 70) is not the base's: the deletion step 72 armed for it a
  day on is a scheduled job, and the restore puts back the base's jobs, which hold none for it.
  Delete it by hand.
  > UNVERIFIED: that the restored scheduler drops the deletion step 72 armed; where the channel
  > deletes itself a day on regardless, nothing needs doing.

Clear the channels by hand for tidiness if you wish.

The base is as `00-base.md` left it, its backup still saved and locked. `@Lima Racing` stays on
the server: it was made by hand, and the restore does not reach Discord. Delete it if you wish.
