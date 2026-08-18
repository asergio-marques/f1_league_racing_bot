---
name: "close-out"
description: "Bring docs/wip-specs/, README.md and docs/how-to/ back in step with what was just built, and confirm the test suite is green, before reporting any work on this repo complete. Invoke at the end of every change that altered behaviour, a rule, or a decision — including decisions the user made in conversation."
argument-hint: "Optional: the area changed, e.g. 'image module' or 'signup wizard'"
---

# Close out a change

Run this **before** telling the user work is finished — not after they ask. Three sets of
documents carry the project's knowledge, and all of them go stale silently:

| Document | What it is | Who reads it |
|---|---|---|
| `docs/wip-specs/*.md` | **The source of truth for rules.** What the bot shall do. | The author, and every future session |
| `README.md` | **What a league sees.** Commands, behaviour, authoring conventions. | League admins and managers |
| `docs/how-to/*.md` | **The order to do a job in.** One guide per module, plus the core bot. Becoming the source of truth on behaviour and how to use it. | A league manager setting the bot up |

`specs/NNN-*/` is **derived** — spec-kit's own output for one increment. Never hand-maintain
it, and never copy a rule into it that belongs in a wip-spec.

## The rule

Update by **what changed**, not by the fact that something did.

| What you changed | wip-spec | README | how-to |
|---|---|---|---|
| A new or altered rule the bot must follow | ✅ | only if a league can see it | if it changes a job a manager does |
| A decision the user made in conversation | ✅ **always** | if league-visible | if it changes a step or its order |
| A new command, parameter, or reply a user reads | ✅ | ✅ | ✅ in the owning module's guide |
| An authoring convention (template ids, asset names, file layout) | ✅ | ✅ | ✅ |
| Behaviour that was documented and is now different | ✅ | ✅ | ✅ |
| A changed prerequisite, permission, or order of steps | ✅ | ✅ | ✅ |
| Bug fix restoring documented behaviour | ❌ | ❌ | ❌ |
| Refactor, rename, test-only change, tidy-up | ❌ | ❌ | ❌ |

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

## Updating a how-to guide

**Voice.** Second person, plain prose, written for a league manager who is not a developer
and may not have read anything else. Short paragraphs with bold lead-ins, tables for
permissions and parameters, an opening paragraph saying what turning the thing on actually
gets you.

**A guide owns the *order* of a job.** Its value is the sequence — what to do first, what
cannot be done until something else is done. When a change alters that order, adds a step or
removes one, the guide is wrong even if every individual fact in it still holds.

**One module per guide.** `configuring-the-<module>.md` covers that module only. Core setup —
seasons, divisions, rounds, teams, seated drivers — is named as a prerequisite and linked to
`docs/how-to/configuring-the-core-bot.md`, never explained again. A finding about core
behaviour belongs in the core guide or the README, not in a module's guide.

**Restating a rule the README also carries is intended.** The guides are becoming the source
of truth on behaviour, so duplication between a guide and the README is not something to
prune. Do not delete a guide's prose on the grounds that the README owns the rule.

**Correct both documents.** While the change is part-made the README is still what a league
reads today. Where behaviour turns out to differ from what is written, fix the guide *and*
the README, not one of them.

**Check the glossary and the prerequisites, not just the steps.** Each guide opens with a
"note on N words" and a "Before you start". A renamed concept or a new dependency lands
there, and it is the part most easily missed.

**`test-mode.md` is not a league guide.** It is for maintainers, in a technical register.
Update it when the way test mode substitutes for a live season changes — not because a
league-facing guide changed.

## Then check

1. **Does the constitution still agree?** If the change altered a *governance* rule — one
   the constitution states — it must be amended too, via `/speckit-constitution`. Do not
   edit `.specify/memory/constitution.md` by hand; it carries a version bump and a sync
   impact report.
2. **Does the README claim anything that is no longer true?** Search it for the behaviour
   you changed, not just for the place you expect the text to be.
3. **Does any how-to guide claim anything that is no longer true?** Grep all of
   `docs/how-to/` for the command, setting or behaviour you changed — a guide other than the
   obvious one often mentions it in passing, and a module's guide can be stale about a
   prerequisite it only links to. Read the surrounding steps, not only the matching line: a
   guide can be wrong about the order while every sentence in it is individually true.
4. **Did a decision get made in this session that is written down nowhere?** Re-read the
   session for the user's answers, not just your own edits.
5. **Is the test suite green?** Run `pytest tests/ -q` from the repo root and report the
   result. Every production change carries its unit tests with it; a change reported complete
   without a test run is not complete. Any failure is a real one until confirmed on a clean
   tree.

## Report what you did

State plainly which documents you touched and which you deliberately did not, with the
reason, and give the test suite result. Cover the wip-specs, the README **and** the how-to
guides — naming each one you checked and left alone. "No README or how-to change — this was a
refactor with no visible behaviour; `pytest tests/ -q` green" is a complete and useful
answer. Silence is not.

## Related standing guidance

- `docs/wip-specs/` is the source; `specs/` is derived.
- The how-to guides are becoming the source of truth on behaviour; a guide restating a
  README rule is intended, not duplication to prune.
- Specs state decisions, not rationale.
- Edit the wip-spec when building against it shows it is wrong.
- Verify generated images as PNG, never as SVG in a browser — the rasteriser exposes
  bugs the browser hides.
