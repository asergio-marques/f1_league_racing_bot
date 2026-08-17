---
name: "close-out"
description: "Bring docs/wip-specs/ and README.md back in step with what was just built, before reporting any work on this repo complete. Invoke at the end of every change that altered behaviour, a rule, or a decision — including decisions the user made in conversation."
argument-hint: "Optional: the area changed, e.g. 'image module' or 'signup wizard'"
---

# Close out a change

Run this **before** telling the user work is finished — not after they ask. Two documents
carry the project's knowledge, and both go stale silently:

| Document | What it is | Who reads it |
|---|---|---|
| `docs/wip-specs/*.md` | **The source of truth for rules.** What the bot shall do. | The author, and every future session |
| `README.md` | **What a league sees.** Commands, behaviour, authoring conventions. | League admins and managers |

`specs/NNN-*/` is **derived** — spec-kit's own output for one increment. Never hand-maintain
it, and never copy a rule into it that belongs in a wip-spec.

## The rule

Update by **what changed**, not by the fact that something did.

| What you changed | wip-spec | README |
|---|---|---|
| A new or altered rule the bot must follow | ✅ | only if a league can see it |
| A decision the user made in conversation | ✅ **always** | if league-visible |
| A new command, parameter, or reply a user reads | ✅ | ✅ |
| An authoring convention (template ids, asset names, file layout) | ✅ | ✅ |
| Behaviour that was documented and is now different | ✅ | ✅ |
| Bug fix restoring documented behaviour | ❌ | ❌ |
| Refactor, rename, test-only change, tidy-up | ❌ | ❌ |

"Nothing to add" is a valid outcome for a refactor. It is **not** a valid outcome for a
change that altered a rule.

### Decisions made in conversation are the ones that go missing

This is the highest-value part of the rule. When the user answers a question — a poll, a
clarification, a "no, do it this way" — that answer is now a project rule, and the only
record of it is a chat log nobody will read again. Write it into the wip-spec in the same
session, in the document's own voice.

On 2026-08-12 the user introduced a generic `fallback.svg` per asset directory by answering
a spec question. It was built, tested and shipped. The wip-spec still said the field is
simply removed when no asset is found. That is the failure this skill exists to prevent.

## Updating a wip-spec

**Voice.** Formal and declarative — "shall", not "should" or "will". Bullets, nested one
level where a rule has parts. Present tense for what holds, future for what the bot does.

**State the decision, not the reasoning.** The specs record what was decided. Cut the why,
even where a nearby section keeps it — the Lineup section of the image spec models the
opposite and is not the pattern to follow. A short clause of justification is fine where a
reader would otherwise think the rule a mistake; a paragraph is not.

**Pitch it like the attendance or results spec, not the image one.** Those two are the register to aim for: declarative bullets, nested one level, a section per job. The image module spec is far longer and more granular than a wip-spec needs to be — treat it as an outlier rather than the standard to match.

**Put it where it belongs.** These documents have a shape; follow it rather than appending.
The image module spec runs: `## Configuration`, `## Conventions of every graphic`, then one
`## X image generation` per type, each with `### Resolution of the data to be placed`,
`### Handling of mismatches between X and template`, `### Generation and posting`,
`### Test data`. A rule that holds for every graphic goes in Conventions, once — not
repeated into each type.

**Edit it when it is wrong.** Building against a wip-spec is licence to correct it, not
only to report the gap. If implementation showed a rule to be wrong, incomplete, or missing
a field, fix the document as part of the same task. Keep *open questions* — the ones needing
a decision rather than a correction — as questions to the user, not as silent edits.

**Do not duplicate into `specs/`.** Write the rule once, in the wip-spec.

## Updating the README

**Voice.** Plain prose for a league admin who is not a developer. Bold lead-ins for
paragraphs (`**Naming.**`), tables for command parameters, `>` callouts for caveats.

**Be honest about what is live.** The README describes the bot as it is today, not as it is
planned. Where machinery exists but has nothing to act on yet, say so plainly — a reader
who trusts a claim that turns out to be aspirational stops trusting the rest.

**Cover the authoring conventions.** Anything a league must do for the bot to work —
filenames, template ids, asset aspect ratios, fonts — belongs here, because they cannot
read the wip-spec.

## Then check

1. **Does the constitution still agree?** If the change altered a *governance* rule — one
   the constitution states — it must be amended too, via `/speckit-constitution`. Do not
   edit `.specify/memory/constitution.md` by hand; it carries a version bump and a sync
   impact report.
2. **Does the README claim anything that is no longer true?** Search it for the behaviour
   you changed, not just for the place you expect the text to be.
3. **Did a decision get made in this session that is written down nowhere?** Re-read the
   session for the user's answers, not just your own edits.

## Report what you did

State plainly which documents you touched and which you deliberately did not, with the
reason. "No README change — this was a refactor with no visible behaviour" is a complete
and useful answer. Silence is not.

## Related standing guidance

- `docs/wip-specs/` is the source; `specs/` is derived.
- Specs state decisions, not rationale.
- Edit the wip-spec when building against it shows it is wrong.
- Verify generated images as PNG, never as SVG in a browser — the rasteriser exposes
  bugs the browser hides.
