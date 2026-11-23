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
and after the attendance sanctions a penalty review applies (`lineup_posting`), every one of which needs a
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

---

## Part C — Ending Season #1

25. **Do:** `/season cancel confirm:yes`

    **Expect:**
    - **Reply:** ``❌ Type exactly `CONFIRM` in the `confirm` field to proceed.``
    - **Log:** ``⛔ `/season cancel` refused for <you> — Type exactly `CONFIRM` in the `confirm` field to proceed.``

26. **Abort is not the way out of an ongoing season.** **Do:** `/season abort confirm:CONFIRM`

    **Expect:**
    - **Reply:** ``❌ `/season abort` is available only before a season's placements are first confirmed. An ongoing season is cancelled with `/season cancel`.``
    - **Log:** ``⛔ `/season abort` refused for <you> — `/season abort` is available only before a season's placements are first confirmed. An ongoing season is cancelled with `/season cancel`.``

27. **Do:** `/season cancel confirm:CONFIRM`

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
        cancelled, and the calendar `00-base.md` step 32 posted deleted.
      - Nothing in `#dN-lineup`, `#dN-standings` or `#dN-attendance`: their pictures stay.
      > UNVERIFIED: the order of the weather and results notes within a division (both are planned
      > after the check-in notice, in the order the modules signed up), and how the calendar
      > draws a cancelled round.
    - **Roles:** none taken. Fake drivers hold none.
    - **Log:**
      ```
      <you> | /season cancel | Success
      ```
      > UNVERIFIED: whether any line follows it. A round called off carries its check-in's audit
      > beneath the line where it had one (`_audit_lines`); the base posted no check-in, so none
      > is expected.
    - **Test mode is off**, and every fake driver deleted, as the season's end does it. **The saved
      backup is kept**: `bot.bkup.db`, `scheduler.bkup.db` and `backup.lock` are still in the bot
      host folder.

---

## Part D — No season live

Season #1 is cancelled and no season is live. Test mode is off, so every test-mode command but
the toggle is refused; the team list is open.

### Test mode, off

28. **Do:** `/test-mode backup status`

    **Expect:**
    - **Reply:** ``⛔ The backup commands run only while the server is in **test mode**. They copy and replace the whole database, which is not something to do to a league that is running. Turn test mode on with `/test-mode toggle` first.``
    - **Log:** nothing: the status is a view. The backup is still on disk (step 27); step 46 sees
      it again once test mode is back on.

29. **Do:** `/test-mode roster add driver_name:Adam Archer team_name:ALP division:Division 1`

    **Expect:**
    - **Reply:** `⛔ This command is only available when test mode is enabled.`
    - **Log:** ``⛔ `/test-mode roster add` refused for <you> — This command is only available when test mode is enabled.``

30. **Do:** `/test-mode roster list division:Division 1`

    **Expect:**
    - **Reply:** `⛔ This command is only available when test mode is enabled.`
    - **Log:** nothing.

31. **Do:** `/test-mode nationality`

    **Expect:**
    - **Reply:** `⛔ This command is only available when test mode is enabled.`
    - **Log:** ``⛔ `/test-mode nationality` refused for <you> — This command is only available when test mode is enabled.``

32. **Do:** `/test-mode toggle`

    **Expect:**
    - **Reply:** ``⛔ Test mode can only be switched while a season is in configuration. Start one with `/season setup`; a season whose configuration is confirmed keeps test mode as it stands until the season ends.``
    - **Log:** ``⛔ `/test-mode toggle` refused for <you> — Test mode can only be switched while a season is in configuration. Start one with `/season setup`; a season whose configuration is confirmed keeps test mode as it stands until the season ends.``

33. **Do:** `/team lineup`

    **Expect:**
    - **Reply:** `⛔ No season is being raced, so no lineup is confirmed.`
    - **Log:** nothing.

### The team list, open

34. **Do:** `/team list`

    **Expect:** as `00-base.md` step 17: headed `**Server team list:**`, the eleven teams `ALP`
    to `KLO`, then `Reserve`. No log line.

35. **Do:** `/team add`, and in the form **Add a team** fill **Shorthand** `LMA`, **Full name**
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

36. **What a team may not be.** **Do**, once for each row below: `/team add`, fill the form with
    the row's **Shorthand**, **Full name** and **Role**, and submit. `@Driver` stands in as a role
    no team holds, so that the names alone are judged; none of these rows adds a team, so it is
    never given to one.

    | Shorthand | Full name | Role | Reply |
    |---|---|---|---|
    | `ALP` | `Alpha Two` | `@Driver` | `⛔ A default team named "ALP" already exists.` |
    | `alp` | `Alpha Two` | `@Driver` | `⛔ "alp" and "ALP" both reduce to "alp", so both would draw the same team image. Choose a more distinct name.` |
    | `Reserve` | `Reserve Two` | `@Driver` | `⛔ The team name "Reserve" is protected and cannot be managed.` |
    | `reserve` | `Reserve Two` | `@Driver` | `⛔ "reserve" reduces to "reserve", which is reserved for the Reserve team of every division. Choose another name.` |
    | `A,B` | `Alpha Bravo` | `@Driver` | `⛔ "A,B" holds a comma. A shorthand is typed into the comma-separated rows of a results submission and a test roster, so it cannot hold one.` |
    | `???` | `Question Racing` | `@Driver` | `⛔ "???" holds no letter or digit, so it cannot name an image file. Choose a name with at least one.` |
    | `MKE` | `alpha racing` | `@Driver` | `⛔ "Alpha Racing" is already the full name of a team. A full name names one team only.` |
    | `NOV` | `**November**` | `@Driver` | ``⛔ The formatting `` **November** `` in the full name reads differently as text and on a graphic. Remove it, then try again.`` |
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

37. **Do:** `/team modify team:LMA`

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

38. **Do:** `/team modify team:LIM`, set **Shorthand** to `ALP`, and submit.

    **Expect:**
    - **Reply:** `⛔ A default team named "ALP" already exists.`
    - **Log:** `⛔ the “Modify a team” form refused for <you> — A default team named "ALP" already exists.`
    - `LIM` is unchanged.

39. **The Reserve team cannot be removed.** The autocomplete does not offer it: type it.
    **Do:** `/team remove name:Reserve`

    **Expect:**
    - **Reply:** `⛔ The team name "Reserve" is protected and cannot be managed.`
    - **Log:** ``⛔ `/team remove` refused for <you> — The team name "Reserve" is protected and cannot be managed.``

40. **Do:** `/team remove name:XYZ`

    **Expect:**
    - **Reply:** ``⛔ No team in the server's list has the shorthand `XYZ`.``
    - **Log:** ``⛔ `/team remove` refused for <you> — No team in the server's list has the shorthand `XYZ`.``

41. **Do:** `/team remove name:LIM`

    **Expect:**
    - **Ack:** `Removing **Lima Motorsport**`
    - **Then:** `✅ Team "Lima Motorsport" removed from the server list.`
    - **Log:**
      ```
      <you> | /team remove | Success
        team: Lima Motorsport
        shorthand: LIM
      ```

42. **Do:** `/team list`

    **Expect:** as step 34: the Fixture's eleven teams and `Reserve`, and no `Lima Motorsport`.

---

## Part E — Season #2, up to its placements

A season set up now is numbered one above the highest the league has committed: Season #1 is
cancelled, so this is Season #2.

43. **Do:** `/season setup game_edition:25`

    **Expect:**
    - **Ack:** `Starting the season setup`
    - **Then:**
      ```
      ✅ Season setup started. **Season #2 (F1 25)** is being configured.

      Use `/division add` for each division, then `/round add` for each round.
      When done, run `/season placements-review` to review and approve.
      ```
    - **Log:**
      ```
      <you> | /season setup | Success
        season: Season #2 (F1 25)
      ```

44. **Do:** `/team list`

    **Expect:**
    - **Reply:**
      ```
      ⚠️ Season 2 divisions differ from the server list.

      **Server list:**
        Alpha Racing — `ALP` → <@&<Alpha Racing role id>>
        …one line per team, as in step 34, Reserve last…
        Reserve — `Reserve` → <@&<Reserve role id>>

      **Season 2 effective teams:**
        *(empty)*
      ```
      Season #2 has no division yet, so it holds no team, and the list reads as differing from
      the server's (`TeamCog.team_list`). Step 54 shows it agreeing once the divisions exist.
    - **Log:** nothing.

45. **Do:** `/test-mode toggle`

    **Expect:**
    - **Ack:** `Turning test mode on`
    - **Then:** ``✅ Test mode **enabled**. Use `/test-mode advance` to step through phases, or `/test-mode review` to inspect season status.``
      No `📌 Added default point configs` line: the server already holds `Standard` and
      `Half Points` from Season #1, and test mode seeds them only on a server holding none
      (`seed_test_configs_on`). Nor are they attached to Season #2: step 49 attaches one.
    - **Log:**
      ```
      <you> | /test-mode toggle | Success
        test_mode: enabled
      ```

46. **The backup survived the cancellation.** **Do:** `/test-mode backup status`

    **Expect:** as step 1: taken at `00-base.md` step 33, readable, locked. No log line.

47. **Configuration is not placements.** **Do:** `/test-mode roster add-bulk`

    **Expect:**
    - **Reply:** `⛔ The test roster can only be changed while the season is in placements.` No
      form opens.
    - **Log:** ``⛔ `/test-mode roster add-bulk` refused for <you> — The test roster can only be changed while the season is in placements.``

48. **Do:** `/results config append name:Half Pionts`

    **Expect:**
    - **Reply:** ``❌ Config **Half Pionts** does not exist on this server, so nothing was attached. Check the spelling, or create it with `/results config add`.``
    - **Log:** ``⛔ `/results config append` refused for <you> — Config **Half Pionts** does not exist on this server, so nothing was attached. Check the spelling, or create it with `/results config add`.``

49. **Do:** `/results config append name:Standard`

    **Expect:**
    - **Ack:** `Attaching config **Standard** to the current season`
    - **Then:** `✅ Config **Standard** attached to the current season.`
    - **Log:**
      ```
      <you> | /results config append | Success
        config: Standard
      ```

50. **Do:** `/season config-review`

    **Expect:** as `00-base.md` step 20, with `Season #2 — F1 25` in the first message's heading
    and with the same remark on the acknowledgement left standing. In particular no message
    opening `⛔ **The configuration cannot be confirmed yet.**`: without step 49 it would carry
    ``No points configuration is attached to this season — `/results config append`.``
    > UNVERIFIED: how the results section of the report lists `Standard` alone, where `00-base.md`
    > had both configurations attached.

51. **Do:** within five minutes, press **✅ Confirm configuration** on the question.

    **Expect:**
    - **Ack:** `Confirming season 2's configuration`
    - **Then:** ``✅ Season #2's configuration is confirmed. The season is now in placements — build its divisions and calendar, place its drivers, then run `/season placements-review`.``
      Placements, not the signup window: test mode skips it.
    - **Posts:** every message step 50 posted in `#bot-commands` is deleted.
    - **Log:**
      ```
      <you> | /season config-review | Confirmed
        season: Season #2
        stage: PLACEMENTS
      ```

52. **Do:** `/division add name:Division 1 role:@Division 1 tier:1`

    **Expect:** as `00-base.md` step 22.

53. **Do:** `/division add name:Division 2 role:@Division 2 tier:2`

    **Expect:** as `00-base.md` step 23.

    Season #2 needs no calendar and no division channels here: nothing in this spec reviews or
    approves it.

54. **Do:** `/team list`

    **Expect:** headed `**Server team list (Season 2 will use this list):**`, then the lines of
    step 34. No log line.
