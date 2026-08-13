# Contract: posting two graphics where the text path posts one message

The textual standings are one message carrying both championships. The graphics are two. This
contract states how the two shapes reconcile, and what a failure of one does to the other.

## The choke point

`results_post_service.post_standings` is the single entry point — five call sites route through it,
covering all seven occasions FR-049 enumerates. The image branch lives **inside** it, as the results
branch lives inside `post_session_results`. No call site changes.

## The split

`post_standings` today composes one `content` string from both sections and posts or edits it. It is
separated into:

1. **Section formatting** — `format_driver_standings` and `format_team_standings`, each producing one
   championship's section with its own sub-heading. Both already exist; what changes is that the
   composition of the two moves out of the formatter's caller.
2. **Composition and posting** — the text path joins both sections into one message as it does today;
   the image path posts two messages; a fallback posts one section alone.

Without this split a single failing championship could only fall back by reposting both, duplicating
whatever the surviving graphic already drew — which FR-052 forbids.

## Order and identity

| | Message | Id column |
|---|---|---|
| First | Driver standings | `standings_message_id` |
| Second | Constructor standings | `constructor_standings_message_id` |

Both are written on the row of the top-ranked driver, as the existing id already is. Both are written
on **every** posting, textual or graphic, so the two flows never disagree about which message is
which; the textual flow leaves the second null.

Each message carries its heading and its lifecycle label as **message text**, and its table as an
attachment. The lifecycle label is *also* drawn on the graphic — XIV.16 as amended at v4.5.0 makes the
split non-exclusive, so a picture forwarded away from its message still says which phase it stands
after.

## Replacement, not edit

An attachment cannot be introduced into a message already posted, so the image flow **deletes and
reposts** wherever the textual flow edits in place.

**Ordering is load-bearing (FR-048):** produce the replacement first, delete the old only once it has
been produced — whether the replacement is a graphic or a textual fallback. The existing code deletes
first in one branch (when content will not fit a single message); that ordering is inverted for the
image path and left as it stands for the text path, which replaces no attachment.

## Failure, per championship

The unit of failure is one graphic (XIV.4). Each championship answers for itself:

| Situation | Driver standings | Constructor standings |
|---|---|---|
| Both render | graphic | graphic |
| Drivers fails, uncommanded | **text, drivers section alone** | graphic |
| Constructors fails, uncommanded | graphic | **text, constructors section alone** |
| Either fails, commanded | command rejected, nothing posted | command rejected, nothing posted |
| Both fail, uncommanded | text | text |

Where both fail, the two sections between them are the whole of what the text path normally posts —
so the league reads each championship exactly once either way, which is the point of the grain rule.

**A cancelled round posts nothing at all**, the toggle notwithstanding (FR-050).

**A Discord-side posting failure** — rate limit, permissions, outage — enqueues the **textual**
standings for retry, not the image (FR-056). The generation succeeded; it is the delivery that did
not.

**One division's failure** never touches another's (FR-055).

## Reporting

| Outcome | Where |
|---|---|
| Notice (fallback asset, truncation, missing nationality) | logging channel, naming season, division, round and **championship**; and alongside a triggering command's output |
| Problem, uncommanded | logging channel; the text fallback is posted |
| Problem, commanded | the invoking user, and the logging channel; nothing is posted |

Never in a division's standings channel, which drivers read (FR-053).

`/images test standings` is the one exception to the fallback rule: a fatal error is reported to the
invoking league manager and nothing is posted (FR-064).
