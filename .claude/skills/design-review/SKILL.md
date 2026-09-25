---
name: "design-review"
description: "Work one module design pass — #283 core, #284 results, #285 attendance, #286 signup, #287 weather or #288 image: inventory the module and review it against docs/design/architecture.md and current Python practice through the review-module-design workflow, correct what diverges commit by commit, and land the module's design file with the last correction. Invoke when the user names one of those issues or modules for a design review."
argument-hint: "A module — core, results, attendance, signup, weather, image — or its issue number, 283–288"
user-invocable: true
disable-model-invocation: false
---

# A module design pass (#283–#288)

Each of these issues asks for the same three things: **review the module's shape against the
architecture, correct what diverges, then write it down** (decided 2026-09-20). The design
file merges in the same pull request as the last of the corrections, so that the document and
the code cannot disagree at the moment they land.

| Module | Issue | Design file | Rules it cites, never restates |
|---|---|---|---|
| core | #283 | `docs/design/core.md` | `docs/wip-specs/core_specification.md` |
| results | #284 | `docs/design/results_module.md` | `docs/wip-specs/results_module_specification.md` |
| attendance | #285 | `docs/design/attendance_module.md` | `docs/wip-specs/attendance_module_specification.md` |
| signup | #286 | `docs/design/signup_module.md` | `docs/wip-specs/signup_module_specification.md` |
| weather | #287 | `docs/design/weather_module.md` | `docs/wip-specs/weather_module_specification.md` |
| image | #288 | `docs/design/image_module.md` | `docs/wip-specs/image_module_specification.md` |

The file names were settled in #282 and are not revisited. **#289 is not this skill's job.**
It reconciles a document with no code behind it, and it comes last.

`$ARGUMENTS` names the module or the issue; strip a leading `#`. If neither is given, ask —
do not pick one. `CLAUDE.md` governs the work itself, and [`fix-issue`](../fix-issue/SKILL.md)
Phases 4 and 7 govern claiming the issue and opening the pull request. Neither is restated
here.

## What is automated, and what is not

The inventory, the review and the verification run through the **`review-module-design`
workflow** (`.claude/workflows/review-module-design.js`). Invoking this skill is the user's
opt-in to running it. Its agents are read-only and cannot reach the user.

The corrections are made in the main session, one commit at a time on one branch, and are
never fanned out to parallel agents. They touch the same files, and the test suite runs one
session at a time.

Every decision, the plan, every issue filed and every push goes through the user. **Do not
enter plan mode while the workflow runs.**

The yardstick is `docs/design/architecture.md`, then `docs/design/core.md` for every module
but core, and then [`python-practices.md`](../architecture-review/python-practices.md) for what
those leave open. Once the architecture has decided something, its decision wins over the
practice file.

## Phase 0 — Resolve, and check the gates

```bash
git fetch origin
git show origin/main:docs/design/architecture.md >/dev/null && gh issue view 282 --json state
git show origin/main:docs/design/core.md >/dev/null && gh issue view 283 --json state   # not for core
gh issue view <N> --json title,state,assignees,labels,body,comments
gh issue develop --list <N>
```

- **Blocked by #282** until `architecture.md` is on `origin/main` and #282 is closed. Every
  pass except core is also **blocked by #283** until `core.md` is on `origin/main` and #283
  is closed. If blocked, stop and say which gate failed. Without them, the pass would invent
  its own idea of the architecture, which is what the ordering exists to prevent.
- **Order.** #283 names the order results, attendance, signup, weather, image. If an earlier
  pass is still open, say so and ask whether to go ahead out of order. The issues block only
  on #283, so it is the user's call.
- Stop if the issue is closed, assigned to someone else, or already has a linked branch — as
  `fix-issue` Phase 1 does.

## Phase 1 — Read

- **The issue, with its comments** (`--json`; plain `--comments` fails here). Its "What the
  document should settle" names the topics this file must cover beyond the six every module
  file covers.
- **`architecture.md` in full.** Note this module's entries in the enforcement tests' ratchet
  lists, and the open issues for this module. Those are this pass's to remove.
- **`core.md`**, for every module but core.
- **The module's wip-spec.** A design file cites it and never restates it: by `[REQ-ID]`
  where the spec has requirement IDs (only the stewarding spec does today), and by section
  heading otherwise.
- **The issues the pass names as its cases** — #189 for results; #238 for attendance; #127,
  #248 and #427 for signup; #425 for weather; #164 for image; #155 for core. They show the
  classes of defect this pass is meant to catch.
- **`docs/design/steward_module.md`**, for the register.

## Phase 2 — Measure

```bash
sha=$(git rev-parse --short HEAD)
python3 tools/architecture_survey.py --module <module> > /tmp/design-survey-<module>-$sha.md
```

Set the figures the issue quotes beside today's and report any drift. Keep the file: Phase 11
compares against it.

## Phase 3 — Review, through the workflow

Tell the user it fans out about ten agents, and up to sixteen if the completeness check finds
gaps.

```
Workflow({ name: "review-module-design", args: { module: "<module>", commit: "<sha>", survey: "<the survey's full text>" } })
```

If the name does not resolve, pass `scriptPath: ".claude/workflows/review-module-design.js"`
instead.

The workflow inventories the module in three aspect groups and reviews three concerns against
the whole inventory. It sends each concern's findings to a verifier that tries to refute them,
and has a critic check that everything the design file owes is supported. When it returns:

- **`failedConcerns` or `missingAspects` is not empty:** resume with `resumeFromRunId`. The
  design file is written from the inventory, so a missing aspect is a missing section.
- **Refuted findings** are dropped, with a count and two or three examples for the user.
  **Uncertain** ones are settled by you, from the code, before they go further.
- **Spot-check at least one confirmed finding per concern** yourself.
- **`forArchitecture`** findings are proposals to amend `architecture.md` or `core.md`, and
  go to the user as decisions.
- **`suspectedDefects`** are things a league would see. They go into the Phase 9 batch as
  drafted bugs. A design pass changes no rule a league experiences.
- **Prose that disagrees with the code**, from the inventory, is a correction owed. Usually
  the docstring is wrong (#127 found exactly that). It is a wip-spec edit only if the spec
  describes behaviour the code does not have, and then it goes through `close-out`.

## Phase 4 — Questions

Only genuine decisions go to the user, through `AskUserQuestion`. These include: an
amendment to the architecture; a correction whose size is borderline, where the choice is to
make it here or defer it to a new tech-debt issue; and a finding whose options trade off
real things. Lead with what the bot does and what goes wrong because of it. Put the
recommended option first, and give each option's cost and failure mode. An answer is recorded
in the design file, which is where the "why" lives, and is never recorded in a wip-spec. If the
pass stops before that file is committed, offer to record the answers as a comment on the
issue, each marked "Decided <date>", and post it only on an explicit yes.

## Phase 5 — Plan, and have it approved

Present the plan in plan mode. Also send the plan file with `SendUserFile` and paste it. The
plan carries:

1. **The corrections, file by file.** For each: the functions touched, and the test that pins
   it. That is a named test that fails before and passes after, or, for a pure move, the
   existing tests that must stay green and the ratchet entry it removes.
2. **The order.** Pure moves and renames go first, each green on its own, with behaviour
   preserved throughout.
3. **What is deferred** to new tech-debt issues, and why each is too large for this pass.
4. **Amendments to `architecture.md` or `core.md`**, where the user took any.
5. **The outline of the design file.** List the six contents — the tables and what each row
   means; the services and what each owns; what is registered with the scheduler and what a
   restart owes it; what is posted, where, and how the message is found again; how the module
   fails; and the constraints a later reader might tune away. Add the issue's own topics, map
   each to the inventory facts behind it. There is no divergence section (CLAUDE.md, the
  `docs/design/` row).
6. **The commit points.** Err well on the side of more. The design file comes last, in the
   same pull request as the final correction.
7. **The documents owed.** These are the design file, any docstrings the inventory found
   wrong, and `RULES` in `tools/coverage_by_module.py` whenever a file moves or is renamed.
   A file matching no rule is gated as `UNASSIGNED` and fails CI.
8. **The branch name**, following `fix-issue`'s convention.

## Phase 6 — Claim the issue

Follow `fix-issue` Phase 4 exactly.

## Phase 7 — Correct

- **One correction per commit**, with its tests, staged by name from `git status --porcelain`.
  Use `git mv` for moves, so history follows the file.
- **Run tests behind the lock:** `flock -w 3600 /tmp/f1-pytest.lock .venv/bin/python -m pytest
  tests/ -q`, for subsets and full runs alike. Run `.venv/bin/mypy` before each commit that
  touches `src/`. After a move, also run `python3 tools/coverage_by_module.py` on a fresh
  report and confirm nothing lands `UNASSIGNED`.
- **Remove this module's entries from the ratchet lists as you correct them.** The
  enforcement test fails on an entry whose breach has gone, which is how it tells you.
- **For the image module:** run `pytest tests/ -q -m rasteriser` by hand, in the main checkout
  (a worktree has no league artwork), on a host with Inkscape. Verify the output as PNG,
  never as SVG in a browser. CI deselects the marker, so nothing else catches a break.

## Phase 8 — Write the design file

Write it once the corrections are in, from the inventory, and re-check each claim at the
branch's final commit, not at the survey's.

- **Title and opening.** The title is "# <Module> — why the code is shaped as it is". The
  opening says what the file holds, that it cites rules instead of restating them, and that it
  is corrected at close-out.
- **Cover the six contents and the issue's own topics**, or say why one is absent.
- **Reference `architecture.md`, and `core.md` for modules; never restate either.** For core,
  describe its implementation of the cross-cutting mechanisms as core's own code, and leave
  the rules those mechanisms serve to `architecture.md`.
- **Record a rejected alternative only where something preserves it**: a docstring, an issue,
  a commit or a test. Check it against that record, but cite only documentation (a docstring
  by name, a spec section, another design file), never an issue number or a commit. Where the
  record is gone, describe the shape as it was built and leave the rationale out. Never
  reconstruct an argument nobody made. The choices the user made in Phase 4 are recorded here,
  and this file is their record.
- **Write each decision where it applies.** A decision and its "*Rejected:*" lines go in the
  section they govern, never in a list of decisions at the head, and with no provenance: not
  who took it, when or how, only the decision and its reasons.
- **A constraint on a single function** stays in that function's docstring, pinned by a named
  test. The file holds the shape.
- **No register of shortfalls, and no issue numbers.** What still diverges after the pass is
  held by the ratchet lists and the tracker, not the file.

## Phase 9 — The divergence batch

Bring the divergences back as one reviewed batch at the end of the pass, as the issue says.
Verify each against `src/` again. Draft every issue in full in one message — its title, its
labels (work type, severity and module) and its body — together with the suspected defects as
`bug` drafts and the diffs to any existing issue. Wait for an explicit yes, and file only what
is approved. Then write the numbers into the design file and into any ratchet entries, and
commit.

## Phase 10 — Second review

Review the file yourself first, against the seven checks in `.claude/agents/design-verifier.md`.
Then give it to a **fresh** `design-verifier` agent (job 2), in the foreground. Fix what it
confirms, and run it again if the fixes were more than wording. Tell the user what it found.

## Phase 11 — Measure again, and close out

Run the survey for the module again, and report before and after for every figure the issue
quoted and every ratchet entry removed. Fetch; if `origin/main` has moved, rebase before
measuring coverage. Then invoke the `close-out` skill. Production code changed, so it owes a
coverage comparison before and after.

## Phase 12 — The pull request

Only on the user's explicit yes, by `fix-issue` Phase 7: `Closes #<N>`, the labels copied from
the issue, and `internal` decided by `CONTRIBUTING.md`'s file test. Include the survey's
before-and-after figures as one line of the body.
