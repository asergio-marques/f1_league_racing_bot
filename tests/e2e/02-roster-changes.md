# 02 — Roster and team changes

Everything test mode lets a league do to its roster and its teams without a real driver:
`/test-mode roster add`, `add-bulk`, `remove`, `clear` and `list`, `/test-mode nationality` and
`/test-mode set-former-driver`, and `/team add`, `modify`, `remove` and `lineup`, each with the
refusals and the edge cases the code has, not only the path that works. Read
[the README](README.md) first for the step format and how to report a failure, and run
[`00-base.md`](00-base.md) first: this spec reuses its **Fixture** throughout, every name, value
and driver, and its conventions for the acknowledgement and the log line.

## Where this spec starts, and why

**It starts by restoring the base, like every other spec, and ends by restoring it again.**

Fake drivers are seated, removed and cleared **only while the season is in placements**
(`TestModeCog._refuse_roster_change_outside_placements`, `test_roster_changes.standing_refusal_on`).
The base is past that: `00-base.md` ends with Season #1 approved and ongoing, so the restored
base is where the refusals once running are shown (Part B), and nothing more.

To reach placements again the spec ends Season #1 and builds a second season up to its
placements, which `src/` allows only one way from the base:

- **`/season abort`** is refused: it abandons only a season whose placements were never
  confirmed (`ABORT_ONLY_BEFORE`), and step 22 shows the refusal.
- **`/season complete`** is refused while rounds are still to be run, and would delete the saved
  backup if it went through: completing a test season discards it.
- **`/season cancel`** ends an ongoing season, switches test mode off and deletes the fake
  drivers, and **keeps the saved backup**: a season cancelled is one abandoned
  (`cancellation_changes`, `end`). That is the route taken (Part C).

With no season live the team list is open, which is where `/team add`, `modify` and `remove`
are shown (Part D). A new season, Season #2, is then set up, given test mode and confirmed into
placements (Part E), and the roster commands are run there (Part F). Part G restores the base,
so the next spec finds it as `00-base.md` left it.

The other way — running `00-base.md` only as far as its step 29 and branching from there — was
rejected: it starts from no database file, so it costs the base itself, and the next spec could
restore nothing until `00-base.md` had been run again in full.

**The base keeps only one backup, and two things here would cost it.** Never turn test mode
off with `/test-mode toggle` while this spec runs, and never `/season complete`: either deletes
the saved backup, lock and all, and then Part G has nothing to restore. (`/test-mode backup save`
cannot overwrite it: the backup is locked, `00-base.md` step 35.) Should either happen, run
`00-base.md` again before the next spec.

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
       Taken: <timestamp: the moment of 00-base.md step 33>
       Size: <size: as in 00-base.md step 33> KB
       Readable: yes
       Locked: locked by <your display name> at <the moment of 00-base.md step 34, ISO 8601>
     ```
     A `Readable:` other than `yes`, or the reply
     ``📭 There is no saved backup. Take one with `/test-mode backup save`.``, means the base must
     be built again: run `00-base.md`.
   - **Log:** nothing; it is a view.

2. **Do:** `/test-mode backup restore`

   **Expect:**
   - **Reply**, with two buttons, **♻️ Restore** and **Cancel**:
     ```
     ⚠️ **Restore the backup taken <relative timestamp: when 00-base.md step 33 ran>?**
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

Season #1 is ongoing. The roster is fixed now: test mode replicates the live flow, where drivers
are seated in placements. The team list is fixed too, but a team's role is not.

### The roster commands refused

5. **Do:** `/test-mode roster add driver_name:Yann Young team_name:BRV division:Division 2 nationality:French`

   **Expect:**
   - **Reply:** `⛔ The test roster can only be changed while the season is in placements.`
   - **Log:** ``⛔ `/test-mode roster add` refused for <you> — The test roster can only be changed while the season is in placements.``
   - No change asked: Division 2's `Bravo Motorsport` stays empty.

6. **Do:** `/test-mode roster add-bulk`

   **Expect:**
   - **Reply:** `⛔ The test roster can only be changed while the season is in placements.` No form
     opens: the stage is judged before it would.
   - **Log:** ``⛔ `/test-mode roster add-bulk` refused for <you> — The test roster can only be changed while the season is in placements.``

7. **Do:** `/test-mode roster remove user_id:9000000000000000001`

   **Expect:**
   - **Reply:** `⛔ The test roster can only be changed while the season is in placements.`
   - **Log:** ``⛔ `/test-mode roster remove` refused for <you> — The test roster can only be changed while the season is in placements.``

8. **Do:** `/test-mode roster clear division:Division 1`

   **Expect:**
   - **Reply:** `⛔ The test roster can only be changed while the season is in placements.`
   - **Log:** ``⛔ `/test-mode roster clear` refused for <you> — The test roster can only be changed while the season is in placements.``

9. **Listing is left free.** **Do:** `/test-mode roster list division:Division 2`

   **Expect:** as `00-base.md` step 29: headed `**Fake Driver Roster — Division 2**`, the 21
   Division 2 drivers, Aaron Abbott alone in `Charlie GP`, none in `Bravo Motorsport`. Steps 5
   to 8 changed nothing. No log line.

### Other refusals of a running season

10. **`/test-mode set-former-driver` takes a server member**, and no fake driver is one, so the
    only account it can be pointed at is a real member's, and no real member holds a driver
    profile here. **Do:** `/test-mode set-former-driver user:<yourself> value:True`

    **Expect:**
    - **Reply:** `⛔ No driver profile found for user <your id> on this server.`
    - **Log:** ``⛔ `/test-mode set-former-driver` refused for <you> — No driver profile found for user <your id> on this server.``

11. **Do:** `/season setup game_edition:25`

    **Expect:**
    - **Reply:** `❌ A season is currently active for this server. Complete it before starting a new one.`
    - **Log:** ``⛔ `/season setup` refused for <you> — A season is currently active for this server. Complete it before starting a new one.``

### The team list, fixed

12. **Do:** `/team add`

    **Expect:**
    - **Reply:** ``⛔ The team list is fixed for Season 1 now that its configuration has been confirmed. `/team add` is available again once the season has ended, or while a new season is in configuration.``
      No form opens.
    - **Log:** ``⛔ `/team add` refused for <you> — The team list is fixed for Season 1 now that its configuration has been confirmed. `/team add` is available again once the season has ended, or while a new season is in configuration.``

13. **Do:** `/team remove name:KLO`

    **Expect:**
    - **Reply:** ``⛔ The team list is fixed for Season 1 now that its configuration has been confirmed. `/team remove` is available again once the season has ended, or while a new season is in configuration.``
    - **Log:** ``⛔ `/team remove` refused for <you> — The team list is fixed for Season 1 now that its configuration has been confirmed. `/team remove` is available again once the season has ended, or while a new season is in configuration.``

### A team's role, changed while the season runs

14. **Do:** `/team modify team:ALP`

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

15. **A role another team holds.** **Do:** `/team modify team:ALP`, choose `@Bravo Motorsport` in
    **Role**, and submit.

    **Expect:**
    - **Reply:** `⛔ <@&<Bravo Motorsport role id>> is already the role of "BRV". A role belongs to one team only. Nothing was written.`
    - **Log:** `⛔ the “Modify a team” form refused for <you> — <@&<Bravo Motorsport role id>> is already the role of "BRV". A role belongs to one team only. Nothing was written.`

16. **Do:** `/team modify team:ALP`. The form opens with **Role** filled with `@Lima Racing`, as
    step 14 left it. Choose `@Alpha Racing`, and submit.

    **Expect:**
    - **Ack:** `Changing **Alpha Racing**`
    - **Then:**
      ```
      ✅ Team "Alpha Racing" updated.
        Role: <@&<Alpha Racing role id>>
      ```
    - **Log:** as step 14, with `role: Alpha Racing (<@&<Alpha Racing role id>>)`.
    - `@Lima Racing` is free again, for Part D.

17. **A form submitted as it stands.** **Do:** `/team modify team:ALP`, and submit the form
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

18. **The Reserve team.** The autocomplete does not offer it: type it. **Do:** `/team modify team:Reserve`

    **Expect:**
    - **Reply:** ``⛔ The Reserve team's role is set with `/team reserve-role`, and its names are not a league's to change.`` No form opens.
    - **Log:** ``⛔ `/team modify` refused for <you> — The Reserve team's role is set with `/team reserve-role`, and its names are not a league's to change.``

19. **Do:** `/team modify team:XYZ`

    **Expect:**
    - **Reply:** ``⛔ No team in the server's list has the shorthand `XYZ`.``
    - **Log:** ``⛔ `/team modify` refused for <you> — No team in the server's list has the shorthand `XYZ`.``

### The lineups

**No command in this spec reposts a lineup.** The bot posts a division's lineup at the season's
approval, at a mid-season confirmation of placements, on `/driver move`, `release` and `sack`,
and after a stewarding verdict that moves a driver (`lineup_posting`), every one of which needs a
real driver or belongs to another spec. A change of a team's role plans role moves only, a test
roster change posts nothing, and in placements no lineup is posted until approval. `/team lineup`
draws the lineup on demand, which is what this section checks.

20. **Do:** `/team lineup`

    **Expect:**
    - **Reply**, seen by you alone: `**Division 1**`, a line break, `**Division 2**`, with two PNG
      pictures attached, Division 1's lineup then Division 2's, each as `00-base.md` step 30
      describes it: Division 2's `Bravo Motorsport` with both seats blank and `Charlie GP` with
      one. Where a picture drew with notices, a second reply, to you alone, headed
      `⚠️ **Image notices**`, naming `lineup — Division 1` and `lineup — Division 2` with their
      notices.
    - **Posts:** nothing in `#d1-lineup` or `#d2-lineup`.
    - **Log:** nothing; it is a view.

21. **A division by its tier, shown to the channel.** **Do:** `/team lineup division:2 public:True`

    **Expect:**
    - **Posts** (`#bot-commands`, public): `**Division 2**`, with Division 2's lineup attached.
      Any image notices come in a reply to you alone, never in the channel.
    - **Log:** nothing.

22. **Do:** `/team lineup division:Division 9 public:True`

    **Expect:**
    - **Reply**, seen by you alone: ``⛔ Division `Division 9` not found.`` The public
      "thinking…" is taken down first, so nothing stays in the channel.
    - **Log:** nothing.

### Refused for the channel and for the role

23. **The wrong channel.** In `#d1-lineup`, not `#bot-commands`:

    **Do:** `/test-mode roster clear division:Division 1`

    **Expect:**
    - **Reply:** `⛔ This command can only be used in the configured interaction channel.`
    - **Log:** ``⛔ `/test-mode roster clear` refused for <you> — This command can only be used in the configured interaction channel.``

    **Then do**, in `#d1-lineup` still: `/test-mode roster list division:Division 1`

    **Expect:**
    - **Reply:** `⛔ This command can only be used in the configured interaction channel.`
    - **Log:** nothing: a view records nothing, its refusal included.

24. **Without the league admin role.** In Discord, take `@League Admin` off yourself. Back in
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
