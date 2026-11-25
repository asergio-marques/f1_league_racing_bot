# 10 — Signing fake drivers up, and the `/driver` commands

Restores the base and runs the whole of a signup mid-season, as a league would, on fake drivers:
`/signup open` while Season #1 is ongoing; three fake drivers created by name with
`/test-mode roster add`; each signup started with `/signup start` and answered question by
question in the driver's own channel by you, a league admin, standing in for a driver who has no
account; the review panel, with one signup approved, one rejected and one sent back for a
correction and approved once corrected; `/test-mode roster sign-up-bulk`, the quick route; then
`/signup close`, `/driver assign`, a refused `/driver assign-bulk` and the mid-season confirmation of the placements; and last
`/driver sack` and `/driver move` on fake drivers. Every post and line of the signup names a fake
driver by its test name, never by a mention. Read [the README](README.md) first for the step format and how to
report a failure, and run [`00-base.md`](00-base.md) first: this spec reuses its **Fixture**
throughout, every name, value and driver, and its conventions for the acknowledgement and the
log line.

**It ends by restoring the base**, so the next spec finds it as `00-base.md` left it.

## What this spec assumes of the base

`/signup open` is refused without at least one availability slot, and the signup settings are
fixed once a season's configuration is confirmed (`signup_settings.open_to_change`), so the slot
must already be in the base. `/test-mode roster sign-up-bulk` refuses a nationality while
`/test-mode nationality` is off, so the base, which signs its 45 drivers up with theirs, leaves it
on. Step 5 checks the slot; step 11's reply shows the switch was on, as it turns it off.

The base's one availability slot is `Monday 20:00 UTC` (its Fixture), and it leaves the signup
settings at their defaults: nationality required, Time Trial, time image required.

## Conventions in this spec

As in `00-base.md`: every command is run in `#bot-commands`, as you, unless the step says
otherwise; **Ack:** is the `<doing>` part of the change queue's acknowledgement
(`⏳ <doing>. This message will be updated when it is done; …`), and **Then:** the outcome it is
updated to; `<you>` in a log line is your display name and mention, and `<your name>` your display
name alone.

**A refusal** is answered to you alone and writes one line to `#bot-log`:
``⛔ <what> refused for <you> — <the reply's first line, without its mark>``. `<what>` is the
command in backticks, a form by its title in curly quotes, a button as the step gives it. A step
writes **Reply:** and **Log:** for it. A refusal changes nothing and posts nothing.

**Under test mode, `/signup` and `/driver` take a driver as text**, a mention or an ID, and offer
the server's drivers by name as you type (`signup_test_cog`, `driver_test_cog`). Type the ID the
step gives; picking the name from the list sends the same ID.

**The fake drivers of this spec.** `/test-mode roster add` gives a fake driver the next ID after
the highest held (`test_roster_service._next_synthetic_id_on`), and the base's IDs run
to 9000000000000000045, so the three created at step 8 are given the IDs below.
`/test-mode roster sign-up-bulk` writes the IDs its list names.

| ID | Driver | Created by | Becomes |
|---|---|---|---|
| 9000000000000000046 | Wade Walsh | `roster add`, step 8 | approved (step 29); `BRV`, then moved to `CHL` |
| 9000000000000000047 | Xavi Ximenez | `roster add`, step 8 | rejected (step 32) |
| 9000000000000000048 | Yuri Yashin | `roster add`, step 8 | a correction round, then approved (step 37); `BRV` |
| 9000000000000000049 | Zeno Zappa | `sign-up-bulk`, step 39 | `CHL`, then sacked |
| 9000000000000000050 | Vito Vargas | `sign-up-bulk`, step 39 | turned down with `/driver reject` |

Division 2 has three racing seats free (`BRV` empty, one in `CHL`) and four drivers are approved,
so one is turned down rather than seated in Reserve: a reserve seat is made on demand, but a third
reserve may outgrow the lineup template's reserve block, which would withhold the confirmation.

**A signup channel** is made for each driver, named from its test name: lower-cased, every
character but a letter, a digit, `_` and `-` made `-`, then `-signup`
(`WizardService._create_wizard_channel`): `#wade-walsh-signup`, `#xavi-ximenez-signup`,
`#yuri-yashin-signup`. It is made in the category of `#signup`, seen by the bot, `@League Manager`
and `@League Admin`; a fake driver has no member to let in.

**You answer for the fake driver** (`signup_views.may_answer`): under test mode a league admin
may press the buttons of a fake driver's wizard and type its answers in its channel. Every line
names you as the one who acted, and the driver by its test name beneath.

- **A typed answer** is marked ⏳ at once; the mark comes off as the next question is posted.
  A typed answer that is taken writes no line and gets no reply. One the wizard turns away is
  answered in the channel with the reason, and writes one line,
  ``⛔ the <step> step of <driver>'s signup wizard refused for <you> — <reason>``.
- **A wizard button** is acknowledged to you alone, **Ack:** `Taking a signup answer`, and
  **Then:** `✅ Your answer has been taken.` The platform, driver type, team and teammate buttons
  write a line, `<you> | Signup | <answer>` with `  driver: <driver>` beneath; No Notes writes
  none.
- **Every question but the button ones carries a Cancel Signup button**, and the button ones carry
  it beside their own. This spec never presses it.

---

## Part A — Restoring the base

1. **Do:** `/test-mode backup status`

   **Expect:**
   - **Reply:**
     ```
     📦 **Saved backup**
       Taken: <timestamp: when 00-base.md saved the backup>
       Size: <size: as 00-base.md gave it> KB
       Readable: yes
       Locked: locked by <your display name> at <when 00-base.md locked it, ISO 8601>
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
     ✅ The backup is staged. **Restart the bot** and it will come back on the restored database — under a service it will restart itself; from a terminal, stop it and run it again. Once it is back, reload Discord (Ctrl+R on desktop) before using the `/driver` and `/signup` commands, which the restart may change.
     The database being replaced was copied to `bot.prerestore.db` first, so this can be walked back by hand if it was not what you wanted.
     ```
   - **Log:**
     ```
     <you> | /test-mode backup restore | Success
       restore: staged; the bot must be restarted to come back on it
     ```

4. **Restart the bot.** From a terminal, stop it and run `python -m leaguebot` again; under a
   service, restart the service (`systemctl restart <unit>`). Then **reload Discord** (Ctrl+R on desktop): the restart swaps `/driver` and `/signup` to suit the
   restored state, and a client loaded before it may still offer the other forms.

   **Expect:**
   - **Host log:** `Started on a restored database.`, before `Logged in as <bot user> (id=<bot id>)`.
   - `bot.staged.db` and `scheduler.staged.db` are gone; `bot.bkup.db`, `scheduler.bkup.db` and
     `backup.lock` are still there.
   - Nothing posted on Discord.

5. **Do:** `/signup config view`, then `/signup time-slot list`

   **Expect:**
   - **Reply** to the first, an embed titled `Signup Module Configuration` with the fields
     **Channel** `<#<signup id>>`, **Base Role** `<@&<Members role id>>`, **Driver Role**
     `<@&<Driver role id>>`, **Signups Open** `No`, **Nationality Required** `ON`, **Time Type**
     `Time Trial` and **Time Image Required** `ON`. (**Nationality Required** is the league's own
     setting; under test mode the wizard follows `/test-mode nationality` instead.)
   - **Reply** to the second:
     ```
     **Availability Time Slots**
     #1 — Monday 20:00 UTC
     ```
     and a line for each further slot the base holds. `No availability slots configured.` means
     the base cannot open signups: step 6 will be refused, and `00-base.md` must be corrected.
   - **Log:** nothing; both are views.

---

## Part B — Opening signups mid-season

6. Season #1 is ongoing, so the window opens on it as it runs: the season moves from Ongoing to
   Ongoing, signups open (`season_lifecycle_service.WINDOW_OPENS_FROM`). Test mode opens a window
   as a running league does.

   **Do:** `/signup open track_ids:12`

   **Expect:**
   - **Ack:** `Opening signups`
   - **Then:** `✅ Signups opened. Button posted in <#<signup id>>.`
   - **Posts** (`#signup`): an embed titled `🏁 Driver Signups Are Open!`, with a **Sign Up**
     button, whose text reads:
     ```
     **Available time slots:**
     • Monday 20:00 UTC

     **Tracks:**
     • Silverstone Circuit

     **Time type:** Time Trial
     **Time image proof:** Required
     **Nationality:** Required

     <@&<Members role id>> — click below to sign up!
     ```
     The `🔒 Signups are now closed.` notice the base's close left in `#signup` is deleted once
     the embed stands, where an earlier spec has not already taken it down
     (`02-roster-changes.md` step 57 does); a notice already gone is passed over silently.
   - **Log:**
     ```
     <you> | /signup open | Success
       track_ids: 12
     ```

7. **A real member is not let in.** A server under test may hold no real driver.

   **Do:** in `#signup`, press **Sign Up** yourself.

   **Expect:**
   - **Reply:** `⛔ Signups are closed while this server is in test mode. Ask an admin to turn it off.`
   - **Log:** `⛔ the “Sign Up” button refused for <you> — Signups are closed while this server is in test mode. Ask an admin to turn it off.`

---

## Part C — The fake drivers

8. **Do**, once for each name, in this order: `/test-mode roster add driver_name:Wade Walsh`,
   `/test-mode roster add driver_name:Xavi Ximenez`, `/test-mode roster add driver_name:Yuri Yashin`

   **Expect**, for each, `<name>` and `<ID>` being the row's in the table of the conventions:
   - **Ack:** `Adding fake driver **<name>**`
   - **Then:**
     ```
     ✅ Added fake driver **<name>**.
     Mention string (copy-paste into results): `<@<ID>>`
     ```
   - **Log:**
     ```
     <you> | /test-mode roster add | Success
       driver: <name>
     ```
   Each is created at Not Signed Up, with no seat and no nationality: what its signup gives it is
   all it will hold.

---

## Part D — Starting the signups

9. **Do:** `/signup start user:9000000000000000046`

   **Expect:**
   - **Ack:** `Signing up`
   - **Then:** `✅ **Wade Walsh**'s signup channel has been created: <#<wade-walsh-signup id>>`
   - **Posts:** a channel `#wade-walsh-signup`, in the category of `#signup`, holding the welcome,
     with a **Cancel Signup** button:
     ```
     Welcome to the signup wizard, **Wade Walsh**!
     **Step 1 — Nationality**
     Enter your nationality (e.g. `British`) or country name (e.g. `United Kingdom`), or type `other`.
     ```
     Nobody is mentioned.
   - **Log:**
     ```
     <you> | Signup | Started
       driver: Wade Walsh
     ```

10. **Do:** `/signup start user:9000000000000000047`

    **Expect:** as step 9, with `Xavi Ximenez` and `#xavi-ximenez-signup`: its welcome names
    **Xavi Ximenez** and asks **Step 1 — Nationality**.

11. **The nationality switch.** Under test mode `/test-mode nationality` stands in for the league's
    own setting, and the wizard reads it as a driver's channel is made, keeping it for the whole
    signup (`wizard_changes.start_change`, `create_channel`).

    **Do:** `/test-mode nationality`

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

12. **Do:** `/signup start user:9000000000000000048`

    **Expect:** as step 9, with `Yuri Yashin` and `#yuri-yashin-signup`, except that the welcome
    skips nationality and opens on the platform, with four buttons **Steam**, **EA**, **Xbox**,
    **PlayStation** and a **Cancel Signup** button:
    ```
    Welcome to the signup wizard, **Yuri Yashin**!
    **Step 2 — Platform**
    Select your platform using the buttons below.
    ```

13. **Do:** `/test-mode nationality`

    **Expect:** as step 11, turned back on:
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
    Wade's and Xavi's wizards still ask nationality and Yuri's still does not: each kept the switch
    as its channel was made.

14. **Refusals.** **Do**, one after another:
    1. `/signup start user:9000000000000000046` — Wade Walsh, already signing up;
    2. `/signup start user:9000000000000000001` — Adam Archer, seated in the base;
    3. `/signup start user:<your own Discord ID>` — a real member, with no driver profile.

    **Expect:**
    1. - **Reply:** `⛔ **Wade Walsh** already has a signup in progress.`
       - **Log:** ``⛔ `/signup start` refused for <you> — **Wade Walsh** already has a signup in progress.``
    2. - **Reply:** `⛔ **Adam Archer**'s signup has already been approved.`
       - **Log:** ``⛔ `/signup start` refused for <you> — **Adam Archer**'s signup has already been approved.``
    3. - **Reply:** ``⛔ No driver found for `<your own Discord ID>`. Give a mention or an ID of any account the driver has held.``
       - **Log:** ``⛔ `/signup start` refused for <you> — No driver found for `<your own Discord ID>`. Give a mention or an ID of any account the driver has held.``

    A real member is never signed up this way. `/signup start` refuses a real driver by name
    (`⛔ **<name>** is a real driver: only a fake driver is signed up this way. …`), but a server
    in test mode holds no real driver to name, so a real member's ID finds no driver at all.

---

## Part E — Wade Walsh's wizard, question by question

Every answer in this part is given in `#wade-walsh-signup`, as you.

15. **A refused answer.** **Do:** type `Martian`

    **Expect:**
    - **Posts** (`#wade-walsh-signup`):
      ``❌ Invalid nationality. Please enter your full nationality (e.g. `British`) or country name (e.g. `United Kingdom`), or type `other`.``
      The question stands; `Martian` keeps its message, its ⏳ taken off.
    - **Log:** ``⛔ the Nationality step of Wade Walsh's signup wizard refused for <you> — Invalid nationality. Please enter your full nationality (e.g. `British`) or country name (e.g. `United Kingdom`), or type `other`.``

16. **Do:** type `British`

    **Expect:**
    - **Posts**, with the four platform buttons and **Cancel Signup**:
      ```
      **Step 2 — Platform**
      Select your platform using the buttons below.
      ```
    - **Log:** nothing.

17. **Do:** press **Steam**.

    **Expect:**
    - **Ack:** `Taking a signup answer`; **Then:** `✅ Your answer has been taken.`
    - **Posts**, with **Cancel Signup**:
      ```
      **Step 3 — Platform ID**
      Enter your platform username / gamertag.
      ```
    - **Log:**
      ```
      <you> | Signup | Platform: Steam
        driver: Wade Walsh
      ```

18. **Do:** type `WadeW`

    **Expect:**
    - **Posts**, with **Cancel Signup**:
      ```
      **Step 4 — Availability**
      Available slots:
        `1` — Monday 20:00 UTC
      Enter the IDs of the slots you can attend (e.g. `1 3 5`).
      ```
      and a line for each further slot.
    - **Log:** nothing.

19. **Do:** type `1`

    **Expect:**
    - **Posts**, with two buttons **Full-Time Driver** and **Reserve Driver**, and **Cancel Signup**:
      ```
      **Step 5 — Driver Type**
      Select your driver type using the buttons below.
      ```
    - **Log:** nothing.

20. **Do:** press **Full-Time Driver**.

    **Expect:**
    - **Ack:** `Taking a signup answer`; **Then:** `✅ Your answer has been taken.`
    - **Posts**, with a button for each of the eleven racing teams by full name (no Reserve),
      **No Preference** and **Cancel Signup**:
      ```
      **Step 6 — 1st Preferred Team**
      Select your 1st preferred team, or press **No Preference** to skip.
      ```
    - **Log:**
      ```
      <you> | Signup | Driver type: Full-Time Driver
        driver: Wade Walsh
      ```

21. **Do:** press **Bravo Motorsport**.

    **Expect:**
    - **Ack:** `Taking a signup answer`; **Then:** `✅ Your answer has been taken.`
    - **Posts**, with the ten teams left, **No Preference** and **Cancel Signup**:
      ```
      **Step 6 — 2nd Preferred Team** *(so far: Bravo Motorsport)*
      Select your 2nd preferred team, or press **No Preference** to finish.
      ```
    - **Log:**
      ```
      <you> | Signup | Preferred team: Bravo Motorsport
        driver: Wade Walsh
      ```

22. **Do:** press **No Preference** on the question step 21 posted.

    **Expect:**
    - **Ack:** `Taking a signup answer`; **Then:** `✅ Your answer has been taken.`
    - **Posts**, with **No Preference** and **Cancel Signup**:
      ```
      **Step 7 — Preferred Teammate**
      Enter your preferred teammate's username, or press **No Preference**.
      ```
    - **Log:** the line names the pick kept:
      ```
      <you> | Signup | Preferred teams: Bravo Motorsport
        driver: Wade Walsh
      ```

23. **Do:** press **No Preference**.

    **Expect:**
    - **Ack:** `Taking a signup answer`; **Then:** `✅ Your answer has been taken.`
    - **Posts**, with **Cancel Signup**:
      ```
      **Step 8 — Time Trial (1/1: Silverstone Circuit)**
      Enter your time in `M:ss.mmm` format (e.g. `1:23.456`). **(attach screenshot)**
      ```
    - **Log:**
      ```
      <you> | Signup | Preferred teammate: no preference
        driver: Wade Walsh
      ```

24. **A time with no screenshot.** **Do:** type `1:27.456`, attaching nothing.

    **Expect:**
    - **Posts:** `❌ A screenshot of your lap time is required. Please attach an image along with your time.`
    - **Log:** `⛔ the Lap Times step of Wade Walsh's signup wizard refused for <you> — A screenshot of your lap time is required. Please attach an image along with your time.`

25. **Do:** type `1:27.456`, attaching any image.

    **Expect:**
    - **Posts**, with **No Notes** and **Cancel Signup**:
      ```
      **Step 9 — Additional Notes**
      Press **No Notes** or type any extra notes (max 50 chars).
      ```
    - **Log:** nothing.

26. **The last answer.** **Do:** type `Prefers evening races`

    **Expect:** the signup is submitted, and the review panel is posted where the driver answered.
    - **Posts** (`#wade-walsh-signup`), with three buttons **Approve**, **Request Changes** and
      **Reject**, mentioning nobody:
      ```
      **📋 Signup Review**
      **Driver:** Wade Walsh
      **Display name:** Wade Walsh
      **Nationality:** British
      **Platform:** Steam
      **Platform ID:** WadeW
      **Availability:** Monday 20:00 UTC
      **Driver type:** Full-Time Driver
      **Preferred teams:** Bravo Motorsport
      **Preferred teammate:** No Preference
      **Lap times:**
        • Silverstone Circuit: `1:27.456`
      **Notes:** Prefers evening races

      Please wait for an admin to validate your signup.
      ```
      The ⏳ comes off `Prefers evening races` once the panel stands.
    - **Log:**
      ```
      <you> | Signup | Submitted
        driver: Wade Walsh
      ```

---

## Part F — Xavi Ximenez's and Yuri Yashin's wizards

27. **Do**, in `#xavi-ximenez-signup`, one answer at a time, each once the question before it has
    been asked:

    | Question | Answer |
    |---|---|
    | Step 1 — Nationality | type `Spanish` |
    | Step 2 — Platform | press **EA** |
    | Step 3 — Platform ID | type `XaviX` |
    | Step 4 — Availability | type `1` |
    | Step 5 — Driver Type | press **Reserve Driver** |
    | Step 7 — Preferred Teammate | type `Wade Walsh` |
    | Step 8 — Time Trial (1/1: Silverstone Circuit) | type `1:28.000`, attaching an image |
    | Step 9 — Additional Notes | press **No Notes** |

    **Expect:** the questions as in Part E, in that order. **A reserve is asked no team:** step 6
    is passed over, and **Step 7 — Preferred Teammate** follows **Reserve Driver** at once
    (`wizard_answers._next`). The log lines, each with `  driver: Xavi Ximenez` beneath:
    `<you> | Signup | Platform: EA`, `<you> | Signup | Driver type: Reserve Driver`, and once the
    last answer is in, `<you> | Signup | Submitted`; No Notes writes none. Then the panel, in
    `#xavi-ximenez-signup`:
    ```
    **📋 Signup Review**
    **Driver:** Xavi Ximenez
    **Display name:** Xavi Ximenez
    **Nationality:** Spanish
    **Platform:** EA
    **Platform ID:** XaviX
    **Availability:** Monday 20:00 UTC
    **Driver type:** Reserve Driver
    **Preferred teams:** No Preference
    **Preferred teammate:** Wade Walsh
    **Lap times:**
      • Silverstone Circuit: `1:28.000`
    **Notes:** None

    Please wait for an admin to validate your signup.
    ```

28. **Do**, in `#yuri-yashin-signup`, its first question being the platform (step 12):

    | Question | Answer |
    |---|---|
    | Step 2 — Platform | press **PlayStation** |
    | Step 3 — Platform ID | type `YuriY` |
    | Step 4 — Availability | type `1` |
    | Step 5 — Driver Type | press **Full-Time Driver** |
    | Step 6 — 1st Preferred Team | press **No Preference** |
    | Step 7 — Preferred Teammate | press **No Preference** |
    | Step 8 — Time Trial (1/1: Silverstone Circuit) | type `1:29.000`, attaching an image |
    | Step 9 — Additional Notes | press **No Notes** |

    **Expect:** the questions as in Part E. The log lines, each with `  driver: Yuri Yashin`
    beneath: `<you> | Signup | Platform: PlayStation`, `<you> | Signup | Driver type: Full-Time Driver`,
    `<you> | Signup | Preferred team: no preference`, `<you> | Signup | Preferred teammate: no preference`,
    then `<you> | Signup | Submitted`. The panel, in `#yuri-yashin-signup`:
    ```
    **📋 Signup Review**
    **Driver:** Yuri Yashin
    **Display name:** Yuri Yashin
    **Nationality:** Not collected
    **Platform:** PlayStation
    **Platform ID:** YuriY
    **Availability:** Monday 20:00 UTC
    **Driver type:** Full-Time Driver
    **Preferred teams:** No Preference
    **Preferred teammate:** No Preference
    **Lap times:**
      • Silverstone Circuit: `1:29.000`
    **Notes:** None

    Please wait for an admin to validate your signup.
    ```

---

## Part G — The review

### Approving

29. **Do:** on Wade Walsh's panel in `#wade-walsh-signup`, press **Approve**.

    **Expect:**
    - **Ack:** `Approving a signup`
    - **Then:** `✅ Signup approved.` and nothing beneath it: a fake driver is granted no role, and
      no note is made of it (`review_changes.approve_change`, `grant_role`).
    - **Posts** (`#wade-walsh-signup`):
      `✅ Your signup has been approved by **<your name>**! You are now an Unassigned driver. This channel will be automatically deleted in 24 hours.`
    - **Roles:** none granted.
    - **Log:**
      ```
      <you> | Signup | Approved
        driver: Wade Walsh
      ```
      and no line reading `The driver role could not be granted: …`.

30. **A second press.** **Do:** press **Approve** on the same panel again.

    **Expect:**
    - **Reply:** `⛔ This signup has already been actioned.`
    - **Log:** `⛔ the “Approve” button of Wade Walsh's signup review refused for <you> — This signup has already been actioned.`

### Rejecting

31. The reason for a rejection is typed in the driver's channel, not given in a form: the press
    waits five minutes for your next message there (`signup_views._ask_for_reason`).

    **Do:** on Xavi Ximenez's panel in `#xavi-ximenez-signup`, press **Reject**.

    **Expect:**
    - **Reply:** `Please type the reason for rejecting this signup in this channel. Your message will be automatically deleted.`
    - **Log:** `<you> | the “Reject” button of Xavi Ximenez's signup review | Asked for a reason`

32. **Do:** within five minutes, type in `#xavi-ximenez-signup`: `No free seat for a reserve this season`

    **Expect:**
    - **Ack** (a second private message beneath the press's): `Rejecting a signup`
    - **Then:** `✅ Signup rejected.`
    - **Posts** (`#xavi-ximenez-signup`): your message is deleted, and then:
      ```
      **Xavi Ximenez** ❌ Your signup has been rejected by **<your name>**.
      **Reason:** No free seat for a reserve this season
      This channel will be automatically deleted in 24 hours.
      ```
    - **Log:**
      ```
      <you> | Signup | Rejected
        driver: Xavi Ximenez
        reason: No free seat for a reserve this season
      ```
    Xavi Ximenez is back at Not Signed Up.

### A correction round

33. **Do:** on Yuri Yashin's panel in `#yuri-yashin-signup`, press **Request Changes**.

    **Expect:**
    - **Reply:** `Please type the reason for requesting changes in this channel. Your message will be automatically deleted.`
    - **Log:** `<you> | the “Request Changes” button of Yuri Yashin's signup review | Asked for a reason`

34. **Do:** within five minutes, type in `#yuri-yashin-signup`: `Give your in-game name`

    **Expect:**
    - **Ack:** `Asking for changes to a signup`
    - **Then:** `✅ Correction requested.`
    - **Posts** (`#yuri-yashin-signup`): your message is deleted, and then, with nine buttons
      **Nationality**, **Platform**, **Platform ID**, **Availability**, **Driver Type**,
      **Preferred Teams**, **Preferred Teammate**, **Lap Times** and **Notes**:
      ```
      **Yuri Yashin** **<your name>** has requested a correction.
      Please select the parameter to correct (5-minute window):
      ```
    - **Log:**
      ```
      <you> | Signup | Correction requested
        driver: Yuri Yashin
        reason: Give your in-game name
      ```

35. **Do:** within five minutes of the choices appearing, press **Platform ID**.

    **Expect:**
    - **Ack:** `Choosing what a driver is to correct`
    - **Then:** `✅ Re-collecting **platform id**.`
    - **Posts** (`#yuri-yashin-signup`), with **Cancel Signup**:
      ```
      **Yuri Yashin** Please re-submit your **platform id**:
      **Reason:** Give your in-game name
      **Step 3 — Platform ID**
      Enter your platform username / gamertag.
      ```
    - **Log:** `<you> | the “Platform ID” button of Yuri Yashin's signup review | Correction requested: Platform ID`

36. **Do:** type `YuriY77`

    **Expect:** the correction is the last answer: the signup is submitted again and a fresh panel
    posted.
    - **Posts** (`#yuri-yashin-signup`): the panel of step 28 again, with
      `**Platform ID:** YuriY77` and every other answer as before, and its three buttons.
    - **Log:**
      ```
      <you> | Signup | Correction submitted: Platform ID
        driver: Yuri Yashin
      ```

37. **Do:** press **Approve** on step 36's panel, the fresh one.

    **Expect:**
    - **Ack:** `Approving a signup`; **Then:** `✅ Signup approved.`
    - **Posts:** the approved notice of step 29, in `#yuri-yashin-signup`.
    - **Log:**
      ```
      <you> | Signup | Approved
        driver: Yuri Yashin
      ```

38. **The first panel.** **Do:** press **Approve** on step 28's panel, the one posted before the
    correction.

    **Expect:**
    - **Reply:** `⛔ This signup has already been actioned.`
    - **Log:** `⛔ the “Approve” button of Yuri Yashin's signup review refused for <you> — This signup has already been actioned.`

    Wade Walsh and Yuri Yashin are approved and Unassigned; Xavi Ximenez is Not Signed Up.

---

## Part H — The quick route: `/test-mode roster sign-up-bulk`

39. `sign-up-bulk` creates each driver, records the signup the wizard would have with default
    answers (Steam, its name as platform ID, every slot, Full-Time Driver, no teams, teammate,
    times or notes) and approves it, all in one save, with no channel, panel, role or post
    (`signup/services/fake_signups.py`). It runs only while signups are open.

    **Do:** `/test-mode roster sign-up-bulk`. In the form **Sign up fake drivers**, paste the
    following into **Paste ID,Driver name,Nationality**, and submit.

    ```
    ID,Driver name,Nationality
    9000000000000000049,Zeno Zappa,Italian
    9000000000000000050,Vito Vargas,Brazilian
    ```

    **Expect:**
    - **Ack:** `Signing up 2 fake drivers`
    - **Then:** ``✅ Signed up and approved **2** fake driver(s). Close signups with `/signup close`, then seat each with `/driver assign`.``
    - **Posts:** nothing; no channel is made.
    - **Log:**
      ```
      <you> | /test-mode roster sign-up-bulk | Success
        drivers_signed_up: 2
      ```

---

## Part I — Closing the window, and placing the drivers

40. Nobody is mid-signup, so the close asks no confirmation. Four approved drivers are not yet
    placed, so the season moves to Ongoing, placements rather than back to Ongoing
    (`season_lifecycle_service.advance_on_window_close_on`).

    **Do:** `/signup close`

    **Expect:**
    - **Ack:** `Closing signups`
    - **Then:** `✅ Signups closed.`
    - **Posts** (`#signup`): the embed of step 6 is deleted, and `🔒 Signups are now closed.` is
      posted.
    - **Log:**
      ```
      <you> | /signup close | Success
        drivers_returned_to_not_signed_up: 0
      ```

41. **`sign-up-bulk` refused, signups closed.** **Do:** `/test-mode roster sign-up-bulk`, and
    submit the form with:

    ```
    ID,Driver name,Nationality
    9000000000000000051,Tobias Thorne,German
    ```

    **Expect:**
    - **Reply:**
      ```
      ⛔ The list was not signed up — 1 problem(s). **No drivers were added.**
      • Signups are not open: fake drivers are signed up only while the window is open.
      ```
    - **Log:** `⛔ the “Sign up fake drivers” form refused for <you> — The list was not signed up — 1 problem(s). **No drivers were added.**`

42. **Do**, one after another:
    - `/driver assign user:9000000000000000046 division:Division 2 team:BRV`
    - `/driver assign user:9000000000000000048 division:Division 2 team:BRV`
    - `/driver assign user:9000000000000000049 division:Division 2 team:CHL`

    **Expect**, for each, `<name>`, `<ID>` and `<team>` being the driver's and the team's full
    name (`Bravo Motorsport`, `Bravo Motorsport`, `Charlie GP`):
    - **Ack:** `Assigning **<name>** to **<team>** in **Division 2**`
    - **Then:** `✅ Assigned **<name>** to **<team>** in **Division 2**.`
    - **Posts:** nothing: an unconfirmed placement posts no lineup and grants no role.
    - **Log:**
      ```
      <you> | /driver assign | Success
        user: <name> (<@<ID>>)
        team: <team>
        division: Division 2
      ```

43. **`assign-bulk` refused, nothing placed.** Wade Walsh and Yuri Yashin already hold a seat in
    Division 2, so a list placing either there again is refused, each such line named, and no
    driver of the list is placed (`PlacementService.judge_assign_paste_on`).

    **Do:** `/driver assign-bulk`, and submit the form with:

    ```
    <@9000000000000000046>, Division 2, CHL
    <@9000000000000000048>, Division 2, CHL
    ```

    **Expect:**
    - **Reply:**
      ```
      ⛔ The list was not placed — 2 problem(s). **No drivers were placed.**
      • Line 1: Driver is already assigned to a team in **Division 2**.
      • Line 2: Driver is already assigned to a team in **Division 2**.
      ```
    - **Log:**
      ```
      ⛔ `/driver assign-bulk` refused for <you> — The list was not placed — 2 problem(s). **No drivers were placed.**
      Line 1: Driver is already assigned to a team in **Division 2**.
      Line 2: Driver is already assigned to a team in **Division 2**.
      ```
    - **Posts:** nothing. Both drivers keep the seats of step 42.

44. **Do:** `/driver reject user:9000000000000000050`

    **Expect:**
    - **Ack:** `Turning down **Vito Vargas**`
    - **Then:** `✅ Turned down **Vito Vargas**. They are no longer signed up.`
    - **Log:**
      ```
      <you> | /driver reject | Success
        user: Vito Vargas (<@9000000000000000050>)
      ```
    Every signup is now settled: three placed, two turned down.

45. **Do:** `/season placements-review`

    **Expect:** the mid-season review, of the placements alone (`SeasonCog._review_mid_season_placements`).
    - **Ack:** `Posting the review of /season placements-review`
    - **Then:** as `00-base.md` step 35: the acknowledgement updated with an image notices section
      where the lineup drew with notices, else left standing, with the ERROR in the host's log
      that step records.
    - **Posts** (`#bot-commands`, public), in this order:
      - `🎨 Drawing the graphics for this review — one moment.`, deleted once the picture is up;
      - `🏎️ **Lineup**` with a PNG: Division 2's lineup as it will stand, `Bravo Motorsport`
        holding Wade Walsh and Yuri Yashin and `Charlie GP` Aaron Abbott and Zeno Zappa, each by
        name;
      - the report, opening:
        ```
        **Placements Review (Season #1) — mid-season**

        **Drivers to confirm**
          Wade Walsh → **Bravo Motorsport** in **Division 2**
          Yuri Yashin → **Bravo Motorsport** in **Division 2**
          Zeno Zappa → **Charlie GP** in **Division 2**
        ```
        then Division 1's lineup as text, which no new driver touches: `📂 **Division 1** — lineup
        once confirmed`, then a line per team, `  **<shorthand>**: <@<ID>> (<driver>), <@<ID>> (<driver>)`,
        e.g. `  **ALP**: <@9000000000000000001> (Adam Archer), <@9000000000000000002> (Bruno Bianchi)`;
      - last, the question, with a **✅ Confirm placements** button:
        ```
        📋 **Do you confirm these placements?**
        Reviewed by <@<your id>>. Confirmation is open to them or to a league admin for the next 5 minutes. Confirming grants the new drivers their roles and posts each affected lineup.
        ```
    - No line headed `⚠️ **Unsettled signups**`, and no private reply opening
      `⛔ **Every signup must be settled before placements are confirmed.**`.
    - **Log:** `<you> | /season placements-review | Review posted`

    Each mention is of an account no member holds, so Discord shows it as an unknown user; the
    test name beside it names the driver.

46. **Do:** within five minutes, press **✅ Confirm placements**.

    **Expect:**
    - **Ack:** `Confirming season 1's placements`
    - **Then:** `✅ 3 placement(s) confirmed. The season is ongoing again.`
    - **Posts:** every message of step 45 is deleted. `#d2-lineup`: Division 2's lineup as a
      picture, as step 45 drew it, and the lineup the base posted there is deleted. Nothing in
      `#d1-lineup`.
    - **Roles:** none granted: the drivers are fake.
    - **Log:**
      ```
      <you> | /season placements-review | Confirmed
        season: Season #1
        placements: 3
      ```
    Season #1 is ongoing again, and every Division 2 racing seat is taken: `Bravo Motorsport` by
    Wade Walsh and Yuri Yashin, `Charlie GP` by Aaron Abbott and Zeno Zappa.

---

## Part J — The `/driver` commands on fake drivers

47. **A real member's ID.** Under test mode `/driver` takes text, and a real member with no
    driver profile names no driver.

    **Do:** `/driver move user:<your own Discord ID> from_division:Division 2 team:CHL`

    **Expect:**
    - **Reply:** ``⛔ No driver found for `<your own Discord ID>`. Give a mention or an ID of any account the driver has held.``
    - **Log:** ``⛔ `/driver move` refused for <you> — No driver found for `<your own Discord ID>`. Give a mention or an ID of any account the driver has held.``

48. **Do:** `/driver sack user:9000000000000000049`

    **Expect:**
    - **Ack:** `Sacking **Zeno Zappa**`
    - **Then:** `✅ **Zeno Zappa** has been sacked. All roles and season assignments removed.`
    - **Posts** (`#d2-lineup`): Division 2's lineup again, `Charlie GP` with Aaron Abbott alone,
      and step 46's deleted.
    - **Log:**
      ```
      <you> | /driver sack | Success
        user: Zeno Zappa (<@9000000000000000049>)
      ```
    Zeno Zappa is back at Not Signed Up.

49. **Do:** `/driver move user:9000000000000000046 from_division:Division 2 team:CHL`

    **Expect:**
    - **Ack:** `Moving **Wade Walsh** to **Charlie GP** in **Division 2**`
    - **Then:** `✅ Moved **Wade Walsh** from **Bravo Motorsport** in **Division 2** to **Charlie GP** in **Division 2**.`
    - **Posts** (`#d2-lineup`): Division 2's lineup once, `Bravo Motorsport` with Yuri Yashin
      alone and `Charlie GP` with Aaron Abbott and Wade Walsh, and step 48's deleted.
    - **Log:**
      ```
      <you> | /driver move | Success
        user: Wade Walsh (<@9000000000000000046>)
        from: Bravo Motorsport, Division 2
        to: Charlie GP, Division 2
      ```

---

## Part K — Restoring the base

50. **Do:** steps 1 to 4 again: `/test-mode backup status`, `/test-mode backup restore` and
    **♻️ Restore**, restart the bot and reload Discord.

    **Expect:** as steps 1 to 4: still `Readable: yes` and locked; `Started on a restored
    database.` in the host's log.

51. **Do:** `/signup config view`

    **Expect:** as step 5: **Signups Open** `No`. The window, the five fake drivers and their
    signups, and every placement of Part I are gone from the bot.

Discord is not restored with the database. Left behind by this spec, for you to delete by hand if
you wish: the channels `#wade-walsh-signup`, `#xavi-ximenez-signup` and `#yuri-yashin-signup`,
whose deletion a day on the restore has taken away with the scheduler's store; the
`🔒 Signups are now closed.` notice of step 40 in `#signup`, which the base does not record; and
the Division 2 lineup of step 49 in `#d2-lineup`, the base's own having been deleted at step 46.
A later spec that opens signups, or posts Division 2's lineup, finds the message the base recorded
gone and carries on (`standing_posts` passes over a message already deleted).

The base is as `00-base.md` left it, its backup still saved and locked.
