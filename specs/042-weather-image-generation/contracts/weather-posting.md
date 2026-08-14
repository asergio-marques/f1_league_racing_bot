# Contract: Selection, the Chain, and Fallback

**Feature**: `042-weather-image-generation` | Constitution XIV.7, XIV.8 (v4.7.0), XIV.10

---

## 1. Template selection — the selecting datum

```
weather_template_key(phase, round_format) -> str
```

A pure function of its two arguments and nothing else (FR-012, XIV.10 as amended at v4.7.0).

| Phase | Sprint round | Every other format |
|---|---|---|
| 1 | `weather_p1_template` | `weather_p1_template` |
| 2 | `weather_p2_sprint_template` | `weather_p2_template` |
| 3 | `weather_p3_sprint_template` | `weather_p3_template` |
| — (mystery round) | `weather_mystery_template` | — |

**Three things the selection must not do**, each named because each is a natural temptation:

- **Not** read the number of sessions the round actually holds. It gives the same answer for every format
  the bot can schedule today and the wrong one the day a format is added. The format is the datum; the
  session count is a consequence.
- **Not** read any configuration beyond the one naming the six templates.
- **Not** fall back to the other slot when the selected one is unconfigured or invalid. That would draw a
  sprint round's four sessions on a canvas authored for two — the exact fault XIV.3's sibling test exists
  to catch, reached by the module's own hand.

A mystery round reaches phase 1's horizon only, and takes the mystery key there; it runs no phase and so
reaches neither phase 2 nor phase 3 selection.

---

## 2. The chain across occasions

The textual flow's lifecycle, inherited entire (FR-044, XIV.8 as amended at v4.7.0).

| Occasion | Posts | Deletes |
|---|---|---|
| Phase 1 horizon | phase 1 message | — |
| Phase 2 horizon | phase 2 message | the phase 1 message |
| Phase 3 horizon | phase 3 message | the phase 2 message |
| Post-race cleanup | — | the phase 3 message |
| Phase 1 horizon, mystery round | the notice (phase `0`) | — |

### 2a. Produce before destroy — a correction to the text path

**Today** `phase2_service` and `phase3_service` call `delete_forecast_message` for the previous phase and
*then* `post_forecast` for the current one. This violates XIV.8 and FR-045, and the fallback path is where
it bites: a failed phase 3 render would have already deleted the phase 2 forecast before falling back,
leaving the division with nothing during the window in which something has already gone wrong.

**Required ordering**, for both paths:

```
1. produce the replacement   (the graphic, or the text a fallback substituted)
2. post it
3. only then delete the previous phase's message
4. persist the new message id under this phase
```

The image branch adds no ordering of its own — it inherits the corrected one. See research R5 and the
plan's Complexity Tracking.

### 2b. The manner of a message is no part of the chain

FR-046. A message posted as text may be deleted by an occasion posted as a graphic, and the reverse. Each
occasion reads which message stands for the phase before it — from `forecast_messages`, which records a
message id and not how it was drawn — and deletes that.

This is what makes a mixed-manner round legitimate: a phase 2 that fell back to text may be followed by a
phase 3 posted as a picture, and the phase 2 message is deleted exactly as it would have been. Without it,
one failed render would strand the chain for the rest of the round, and a single graphic's failure would
cost far more than XIV.4 allows.

### 2c. Test mode

Deletions remain suppressed while test mode is active, for the image flow exactly as for the textual one
(FR-047). The suppression lives in the deletion path both flows call, so this is inherited rather than
implemented.

---

## 3. The message the graphic rides on

| Type | Message text | Attachment |
|---|---|---|
| Phases 1–3 | the division role mention, **and nothing besides** | the PNG |
| Mystery notice | **nothing** — no role mention | the PNG |

The heading the textual forecast carries appears on neither the message nor the graphic;
`phase_description` stands in its place (FR-042). The mystery notice's message carries no mention because
its textual counterpart carries none — the conditions are unknown to every participant alike.

---

## 4. Fallback and reporting

| Trigger | Outcome |
|---|---|
| Fatal problem, posting triggered by **no command** | The phase's forecast is posted in the textual manner (FR-055) |
| Fatal problem, posting triggered by **a command** | The command is rejected, nothing is posted, the caller is told what is at fault (FR-056) |
| Fatal problem in `/images test weather-*` | Reported to the invoking league manager, no image posted — there is no textual counterpart to fall back to (FR-058) |
| **Posting** fails for a Discord reason | The **textual** forecast is enqueued for retry. A generated image is never enqueued (FR-057) |
| Non-fatal notice | The graphic is posted; the notice reaches the log channel naming season, division, round and phase, and no forecast channel (FR-059) |

**Grain.** The unit of failure is one graphic: one division's phase failing affects neither the phases
after it nor the same phase of any other division (FR-049). A fallback substitutes one message.

**The invalidation notice stays text.** The message posted when an amendment invalidates a round's
forecasts is not a weather graphic and is unaffected by the toggle (FR-054).

---

## 5. Preconditions the graphic must not add

FR-051, XIV.7. Every draw, every persisted phase result and every calculation-log entry completes exactly
as it would with the module disabled, and a failed render finds that work already done. The graphic is
downstream of every state change it depicts.

Concretely, the image branch is reached **after** the phase service has computed, persisted and logged.
Enabling or disabling the `weather` toggle changes what the forecast channel receives and changes nothing
about what is drawn, what is stored, or what the log records (SC-008).

**No posting, no graphic.** Where the source module would post nothing — no forecast channel configured,
the channel unreachable, a mystery round at the phase 2 horizon — nothing is generated and nothing is
posted, whatever the toggle says (FR-050, FR-053).
