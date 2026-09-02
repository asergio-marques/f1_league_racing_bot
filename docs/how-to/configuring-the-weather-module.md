# Setting up the weather module

Turn the weather module on and every round gets a forecast — a chance of rain days ahead, then the conditions session by session, then the final word two hours before the lights go out. The bot works it out itself and posts it on its own; you never ask it for a forecast.

This guide is the **order to do things in**, from switching the module on to a season posting forecasts. It sends you to the reference for the fine print:

- **[Weather Module Commands](../../README.md#weather-module-commands)** in the main README — the three timing commands in full.
- **[Weather Pipeline](../../README.md#weather-pipeline)** — what each phase produces.
- **[Image Module](../../README.md#image-module)** — the settings that turn forecasts into pictures, and [Setting up the image module](configuring-the-image-module.md) for the order to do those in.

You do not need to read those first. Start here.

This guide covers the weather module only. Setting the bot up, creating a season, adding divisions and adding rounds are a job of their own — follow **[Setting up the bot for your league](configuring-the-core-bot.md)** for those. Where weather depends on one of them, it is named and linked, not explained.

---

## A note on three words

**Phase** — one of the three forecasts the bot posts for a round. They are not three different messages sitting side by side: each one **replaces** the one before it, so your drivers only ever have one forecast to look at.

**Horizon** — how long before the race a phase is posted. Phase 1's horizon starts as five days, phase 2's as two days, phase 3's as two hours. These are the only timings you can change.

**Slot** — a stretch of a session with its own weather. A short qualifying has two, so it can dry out halfway through; a long race has three. Phase 3 is the phase that fills them in. How many a session gets is fixed by the kind of session it is, and you cannot change it.

---

## Before you start

**The bot must already be set up, and you need a season to attach forecasts to.** Weather does nothing on its own — it works round by round, so there must be rounds. If you have not got that far, follow [Setting up the bot for your league](configuring-the-core-bot.md) first; this guide picks up from there.

**Who is allowed to run what.** Every command below also has to be run in the bot's usual command channel by someone with the usual bot role.

| Commands | You need |
|---|---|
| `/module enable weather` | **Administrator** |
| `/weather config` — the three timing commands | **Manage Server**, and the module already on |
| `/division weather-channel` | The usual bot role, and the module already on |
| Anything under `/images` | See the image guide |

> **Every command in this guide is gated on the module being on.** Run one before step 1 and you are told the weather module is not enabled — not that you lack a permission. Step 1 genuinely has to come first.

> **The timings can only be set before a season is running.** All three `/weather config` commands are refused while a season is active, and the timings in force are the ones the bot noted when the season was approved. If you are going to change them, change them now — see step 3.

---

## Step 1 — Switch it on

```
/module enable weather
```

Nothing is generated yet. The bot needs a channel to post in and a season to work through, which are the next two steps.

If you turn weather on **after** a season is already running, the bot catches up: it works out any forecast whose moment has already passed and posts it straight away, then schedules the rest. That is deliberate, and it is the one way to bolt weather onto a season in progress.

> **Switching it on can be refused.** If a season is already running and any division has no forecast channel, the bot names those divisions and does nothing. Set their channels first, then try again.

Turning the module off cancels everything still to come. It does **not** delete forecasts already posted, and it does not forget your channels or your timings — turn it back on and they are still there.

---

## Step 2 — Give every division a forecast channel

```
/division weather-channel  name: Division One  channel: #div1-weather
```

Forecasts are posted **per division**, so a league with three divisions sets three channels. There is no server-wide forecast channel and no default — a division without one is a division the bot cannot post to.

**This is the thing that blocks a season.** While weather is on, a season cannot be approved until every division has a forecast channel, and the bot names the ones that are missing.

Each forecast also **pings the division's role**, the one set when the division was created. That is the role your drivers need if they want to be told a forecast has arrived.

The bot's own workings — the numbers behind each draw — go to the log channel instead, never to the forecast channel. Your drivers see the forecast; you see how it was reached.

> **A division copied from another does not inherit its forecast channel.** The copy starts with none, and nothing warns you at the time — it surfaces later as a season that will not approve. Set it explicitly.

---

## Step 3 — Decide when the forecasts land

Three settings, and they are the only real dials the weather module has.

| Command | What it sets | Starts as |
|---|---|---|
| `/weather config phase-1-deadline` | Days before the race that the chance of rain is posted | 5 days |
| `/weather config phase-2-deadline` | Days before the race that the session outlook is posted | 2 days |
| `/weather config phase-3-deadline` | Hours before the race that the final forecast is posted | 2 hours |

Each one must be at least 1, and they have to stay in order — phase 1 earlier than phase 2, phase 2 earlier than phase 3. The bot works that out in hours, so a phase 2 of 2 days and a phase 3 of 48 hours is refused for landing at the same moment. When it refuses, it shows you both values in hours so you can see why.

Every reply also tells you where the other two stand, which saves setting one and forgetting what it now sits next to.

**Do this before the season is approved.** The bot notes the timings at approval and works to those for the whole season. Afterwards the commands are refused outright, and there is no way to shift a running season's forecasts.

> **The wording of the posts does not follow these settings.** The messages say "5 days out", "2 days out" and "2 hours out" whatever you set. Change phase 1 to seven days and the forecast still arrives seven days ahead — but it will describe itself as five. Worth knowing before your drivers ask.

> **An amended round falls back to the standard timings.** If a round's track, time or format is changed after the season is running, its forecasts are rescheduled at 5 days, 2 days and 2 hours regardless of what you set here.

> **So does a restart.** When the bot starts again with forecasts still outstanding, it works out which ones it missed using 5 days, 2 days and 2 hours — not your settings. A league running custom timings loses them quietly every time the bot is restarted.

To see what is currently set, run `/season review` — the weather block lists all three. There is no separate command for reading them back.

---

## Step 4 — Know what a round's format does to its forecast

You choose a round's format when you add the round; this is what each one does to the weather.

| Format | Sessions in the forecast | Slots each |
|---|---|---|
| Normal | Qualifying, Race | 2, 3 |
| Sprint | Sprint Qualifying, Sprint Race, Feature Qualifying, Feature Race | 2, 1, 2, 3 |
| Endurance | Qualifying, Race | 3, 4 |
| Mystery | None — see below | — |

A longer session gets more slots, so its weather has more room to change. None of this is a setting; it follows from the format.

> **Mystery rounds get no weather.** Nothing is worked out and nothing is recorded. At the phase 1 moment your drivers get a fixed notice saying the weather is not decided in advance and the game will set it at race time — and where the image module is on, that notice is drawn from a template of its own rather than posted as text. It pings nobody, because the conditions are as unknown to you as to them. Nothing is posted at the phase 2 and phase 3 moments.

**A round needs a track for weather to be possible.** The chance of rain is drawn from the circuit, so a round with no track recorded cannot be forecast. The bot stops — and says nothing, anywhere you can see. Nothing reaches the forecast channel and nothing reaches the log channel; only the bot's own log file on the host records it. Mystery rounds are the deliberate exception.

---

## Step 5 — Decide between text and pictures

Out of the box, forecasts are text. With the image module on and the weather output switched on, the same three forecasts arrive as pictures instead, on a message carrying just the division role.

```
/module enable images
/images config toggle aspect:Weather forecasts
```

There are **six** weather drawing files, not three: one per phase, a sprint version of phases 2 and 3 — because a sprint weekend has four sessions to show rather than two — and one for the mystery-round notice. The bot picks by the round's format and nothing else.

Follow [Setting up the image module](configuring-the-image-module.md) for the order — the drawing files, the weather symbols, and the flags a forecast draws for the round's country. A forecast leads with the **grand prix** and puts the **circuit** on the line beneath it, and names the country nowhere: that is what the flag is for.

> **A picture never delays or changes a forecast.** The bot works the weather out, saves it and logs it first, and only then draws. If the drawing fails for any reason — the module off, a drawing file the bot will not accept, the converter missing — the text forecast is posted exactly as it always was, and the reason goes to the log channel. Your drivers always get a forecast.

---

## Step 6 — Try it without waiting

You are not going to wait five days to find out whether any of this works.

```
/test-mode toggle
/test-mode advance
```

`advance` fires the next scheduled event due — often the next weather phase, but equally a results collection or a check-in call where those modules are on — straight away, and posts it to the real channels so you see exactly what your drivers will see. Run it again for the next one. `/test-mode review` shows what is still pending. [Testing with test mode](test-mode.md) covers the whole of it.

> **Test mode is only available before your league has real drivers.** The bot refuses to turn it on while any driver is signed up, unassigned, assigned or banned, or while your signup window is open, and while it is on no real driver may sign up or be placed. Former drivers from a finished season do not stand in the way. Do this step before you open signups.

> **Turning test mode off clears out every forecast the bot is holding**, not only the ones it posted while you were testing, and it deletes every fake driver on the server at the same time. On a season that has not started that is exactly what you want. On a season already running it will take down forecasts your drivers were reading, and the bot will not put them back — only the next phase will. Do your testing before the season is approved.

---

## What your drivers see

Three posts per round, each replacing the last, so the channel never fills up with stale forecasts.

**Phase 1 — the chance of rain.** The track and one number, as a percentage. That is the whole forecast at this stage; it says nothing about individual sessions yet.

**Phase 2 — the session outlook.** Every session in the round gets one word: **Sunny**, **Mixed** or **Rain**, with a symbol beside it. Mixed is the interesting one — it means the session is expected to change while it runs.

**Phase 3 — the final forecast.** Every session gets its actual weather, slot by slot, drawn from five: **Clear**, **Light Cloud**, **Overcast**, **Wet** and **Very Wet**. A session whose weather holds steady shows one word; a session that changes shows the sequence joined by arrows, so `Light Cloud → Wet → Very Wet` is a race that turns during the running.

The higher the chance of rain from phase 1, the more rain and mixed sessions phase 2 tends to hand out, and the wetter phase 3 tends to draw. It is a draw, not a decision — a low chance of rain can still produce a wet race, which is the point of the thing.

**Three other kinds of message** can appear in the same channel:

- **A mystery round notice**, at the phase 1 moment, pinging nobody.
- **An invalidation notice**, if a round's track, time or format is changed after a forecast has gone out. It tells drivers the old forecasts no longer count, and a fresh one follows automatically.
- **A cancellation notice**, if a round, a division or the whole season is called off. All three are posted to the forecast channel, and they arrive whether or not the weather module is on.

**And one disappears.** Twenty-four hours after a race starts, the bot deletes that round's final forecast, so the channel holds the forecast that matters and nothing else.

---

## What you cannot change

Worth knowing so you do not go looking for the setting.

| What | Why not |
|---|---|
| How likely rain is at each circuit | Set per circuit and packaged with the bot. Silverstone is wetter than Bahrain and there is no command to change either. `/track list` shows the circuits the bot knows |
| The weather words themselves | Sunny, Mixed and Rain for a session; Clear, Light Cloud, Overcast, Wet and Very Wet for a slot |
| How many slots a session gets | Fixed by the kind of session — see the table in step 4 |
| The wording and symbols of the posts | Fixed. The symbols become pictures you can replace once the image module is on |
| When the final forecast is tidied away | Always 24 hours after the race starts |
| Re-rolling a round's weather | There is no command for it. Amending the round is what clears the old forecast and draws a new one |

The bot keeps a full record of every draw in the log channel, so a forecast that looks surprising can always be checked rather than argued about.

---

## Checklist before a season

Worth running through before the season is approved.

- [ ] `/module enable weather` has been run
- [ ] Every division has a forecast channel, including any you created by copying another
- [ ] Every division has the role you want pinged
- [ ] The log channel is one you can read, since every calculation goes there
- [ ] The three timings are what you want, and `/season review` shows them
- [ ] Every non-mystery round has a track
- [ ] Rounds you meant to be mystery rounds are set as mystery, and the rest are not
- [ ] If you want pictures: the image module is on, weather is toggled on, and all six weather drawing files pass
- [ ] You have watched at least one full round go by with `/test-mode advance`

---

## If something looks wrong

| What you see | Usually means |
|---|---|
| No forecast at all for a division | No forecast channel set for it, or the module is off |
| No forecast for one round only | It is a mystery round — that is intended. Otherwise the round has no track, and nothing anywhere will tell you so: check the round with `/season review` |
| A season that will not approve | A division is missing its forecast channel. The bot names which |
| The post says "5 days out" but arrived earlier or later | Known: the wording is fixed and does not follow your timing settings. The timing itself is correct |
| A round's forecasts went back to 5 days / 2 days / 2 hours | Known: amending a round resets its timings to the standard ones, and so does restarting the bot with forecasts outstanding |
| `/weather config` refused | Either a season is running, or the value would put the phases out of order. The reply says which |
| Two invalidation notices for one change | Amending more than one thing at once posts one per change |
| Text where you expected a picture | The forecast worked and the drawing did not. The log channel names the reason — most often a drawing file or the converter |
| A forecast that seems too wet for the stated chance of rain | Normal. The chance of rain shifts the odds; it does not decide the outcome |
| Nothing in the log channel either | The module is off, or the season is not approved yet — nothing is scheduled until it is |
