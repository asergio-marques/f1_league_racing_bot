---
name: "architecture-review"
description: "Work issue #282, the architecture pass: survey the bot's shape, review it against current Python application practice through the review-architecture workflow, settle the target architecture with the user one decision at a time, then build its enforcement and write docs/design/architecture.md. Invoke when the user names #282 or asks to start or resume the architecture review."
argument-hint: "Optional: resume, to pick up a pass whose branch already exists"
user-invocable: true
disable-model-invocation: false
---

# The architecture pass (#282)

#282 asks for three things, in this order: **decide the target architecture, correct what
contradicts it, then write it down** as `docs/design/architecture.md` (decided 2026-09-20). It
heads a programme. #283 (core) follows it, then the five module passes #284–#288, which use
the [`design-review`](../design-review/SKILL.md) skill, and then #289 (stewarding). Every one
of them is judged against what this pass settles. A rule left vague here gets re-derived six
times.

`CLAUDE.md` governs the work itself. [`fix-issue`](../fix-issue/SKILL.md) governs claiming the
issue and opening the pull request. Its Phases 4 and 7 are followed as written and are not
restated here.

## What is automated, and what is not

The survey, the review and the verification run through the **`review-architecture`
workflow** (`.claude/workflows/review-architecture.js`). Invoking this skill is the user's
opt-in to running it. Its agents are the project's `design-surveyor`, `python-design-reviewer`
and `design-verifier`. All three are read-only, and none can reach the user.

Everything else comes back through you. That means every decision, the plan, every issue
filed and every push. A subagent's recommendation is not a decision, and a reply from the
user that changes the subject is not an approval.

**Do not enter plan mode while the workflow runs.** Plan mode propagates to running agents
and halts them mid-task. Wait for the workflow to return.

[`python-practices.md`](python-practices.md) in this directory is the yardstick. It encodes
current Python application practice, sized for a single-league bot on a Pi. Read it before
Phase 4. You will be explaining its trade-offs to the user.

## Phase 1 — Read, and check the ground

```bash
gh issue view 282 --json title,state,assignees,labels,body,comments
gh issue develop --list 282
git fetch origin && git status --porcelain && git branch --show-current
```

(`gh issue view --comments` fails on this repository. Use `--json`.)

Stop and say so if #282 is closed, or assigned to anyone but the active account, or already
has a linked branch — unless the argument is `resume` and the branch is yours. To resume,
check that branch out and read `docs/design/architecture.md` on it. Its sections, with their
*Rejected:* lines, are the record of what was settled, so do not ask those questions again. Then carry on from the
first phase whose output is missing. With no branch yet, the record is any comment on #282
whose lines are marked "Decided" (see Phase 4), and those decisions are not asked again
either.

Only tracked changes count as a dirty tree. Untracked files — league artwork, tooling not yet
committed — are carried across the branch checkout in Phase 6. Under CLAUDE.md's
stage-by-name rule they are never staged, so they never reach #282's commits.

Read the parts of the repository #282 names: the documentation layout in `CLAUDE.md`, and
`docs/design/steward_module.md` for the register a design file is written in.

## Phase 2 — Measure

```bash
sha=$(git rev-parse --short HEAD)
python3 tools/architecture_survey.py > /tmp/architecture-survey-$sha.md
```

The tool writes down #282's "Methods" paragraph, so its figures can be compared with the
issue's. Put the two side by side in a short table — figure, #282's value at `fa43fd21`, the
value now — and show it to the user before Phase 3. #282's figures were corrected on
2026-09-25 to match the tool's definitions, so at `fa43fd21` the two agree. The one exception
is the channel-write row: the issue excludes the router's own call, while the tool's table
includes it (131 against 132). If anything else differs at that commit, a definition has
changed, and that must be settled before today's figures are read as drift.

## Phase 3 — Review, through the workflow

Before launching, tell the user it fans out about twelve agents, and up to eighteen if the
completeness check finds gaps.

```
Workflow({ name: "review-architecture", args: { commit: "<sha>", survey: "<the survey's full text>" } })
```

If the name does not resolve, pass `scriptPath: ".claude/workflows/review-architecture.js"`
instead.

The workflow reviews five concerns — layering, composition, modules, runtime,
output-and-failure. It sends each concern's findings to a verifier that tries to refute them,
and has a critic check that #282's list is covered. When it returns:

- **`failedConcerns` is not empty:** resume with `resumeFromRunId` before going on. A missing
  concern is a hole in the architecture, not a quiet corner.
- **Refuted findings** are dropped. Tell the user how many there were, with two or three
  examples.
- **Uncertain findings:** settle each yourself by reading the cited lines, before it reaches
  the user.
- **Spot-check at least one confirmed finding per concern** against the code. A remedy you
  have not verified must not be recommended.
- **`suspectedDefects`** are things a league would see. They go into the Phase 8 batch as
  drafted bugs. None is fixed in this pass.
- **Unworked gaps** named in the workflow's log are reported, not dropped.

## Phase 4 — Settle the decisions, one question at a time

The six candidate decisions in #282, plus any new one the reviewers raised, go to the user
through `AskUserQuestion`:

- **Lead with what the bot does today and what goes wrong because of it.** The pattern's name
  comes after.
- **Put the recommended option first**, marked `(Recommended)`. In each option's description,
  give its cost and what breaks if it is chosen.
- **Where reviewers disagreed on a candidate, show both stances.**
- **Ask up to four questions per call**, and ask follow-ups in later calls.
- **Ask only what is genuinely the user's to decide.** A finding with a conventional answer
  is decided by you, and the plan says so.
- **A tooling choice is always the user's.** That covers import-linter against an `ast` test,
  ruff, and `pyproject.toml`, each with its cost ([practices §11](python-practices.md#11-tooling-for-the-record)).

An answer is a project decision from the moment it is given. It is an engineering decision, so
its home is `architecture.md`, never a wip-spec (CLAUDE.md, decided 2026-08-27). Keep the
answers in the plan until the first commit puts them in the file. The options the user turned
down become that file's "*Rejected:*" lines, and the file is their record.

**If the pass stops before the first commit, the decisions exist only in this conversation.**
This covers a tentative start, and a plan that is not approved. Before the session ends, offer
to record them as a comment on #282, one line per decision, each marked "Decided <date>". Post
it only on the user's explicit yes. That comment is what a later session reads instead of
asking again.

## Phase 5 — Plan, and have it approved

Present the plan in plan mode (`EnterPlanMode`, then `ExitPlanMode`). The approval dialog does
not always show the plan, so also send the plan file with `SendUserFile` and paste it. The plan
carries:

1. **The decisions**, one line each, as the user settled them.
2. **Every confirmed divergence, and who owns it.** Give a table assigning each one to #282
   itself (a cross-cutting rule or its enforcement), to the module pass whose files it touches
   (#283–#288, by `classify()`), or to a new tech-debt issue with the reason it is too large
   for its pass.
3. **The enforcement.** Name each test, the rule it pins, and its ratchet list of today's
   breaches, where each entry names the issue that will remove it
   ([practices §1](python-practices.md#1-layers-and-which-way-a-dependency-points)).
4. **What #282 corrects itself.** At minimum, the docstrings the survey caught claiming what
   the code does not do — `OutputRouter`'s "single chokepoint", unless the decision makes it
   true.
5. **The outline of `architecture.md`.** Map each of #282's "What the document should settle"
   items to a section. Write each decision in the section it governs; there is no list of
   decisions at the head and no divergence section (CLAUDE.md, the `docs/design/` row).
6. **The commit points.** Err well on the side of more. The decisions land first. A deletion
   or a fix is never bundled into a bigger commit.
7. **The documents owed.** These are `architecture.md`, and the `CLAUDE.md` documentation
   table if `docs/design/` stops being one file per module. The constitution changes only
   through `/speckit-constitution`, if a governance rule moved. List any edits to the bodies
   of #283–#289, to be shown as diffs and approved separately.
8. **The branch name**, following `fix-issue`'s convention.

Nothing is created on GitHub until this plan is approved.

## Phase 6 — Claim the issue

Follow `fix-issue` Phase 4 exactly: confirm the account, assign the issue, create the linked
branch through `gh issue develop`, and confirm the link took.

## Phase 7 — Build

Work in the order the plan committed to, at its commit points.

- **Run tests one session at a time, behind the lock:**
  `flock -w 3600 /tmp/f1-pytest.lock .venv/bin/python -m pytest tests/ -q`, for subsets and
  full runs alike. Run `.venv/bin/mypy` before any commit that touches `src/`.
- **Stage every path by name**, from `git status --porcelain`.
- **Write enforcement tests the way the suite's rule-pinning tests are written.** Model them
  on `tests/unit/test_migration_steps.py` and `test_coverage_scope.py`: a module docstring
  saying which decision they pin and why, deterministic order (`sorted()`, never `rglob`
  order), and nothing that depends on the host. A ratchet entry for a new tech-debt issue
  gets its number in Phase 8, before the pull request opens.
- **`architecture.md` is a target, not a description of today.** Write it in the register of
  `steward_module.md`. It restates no functional rule. It points to the rules `CLAUDE.md`
  holds instead of copying them, and a threshold copied into it drifts. It cites other
  documentation only, never an issue or a commit, and keeps no register of where the code
  falls short: the ratchet lists and the tracker hold that.

## Phase 8 — The divergence batch

#282 wants divergences brought back as **one reviewed batch at the end**, not filed as they
are found. Verify each one against `src/` again. Then draft every issue in full in one
message: its title, its labels (work type, severity and module, per `CONTRIBUTING.md`) and a
body in the tracker's style. Include the Phase 3 suspected defects as `bug` drafts, and the
diffs to any existing issue body.

Wait for an explicit yes. File only what is approved. Then write the new numbers into the
ratchet lists (never into `architecture.md`), and commit.

## Phase 9 — Second review

Review `architecture.md` yourself first, against the seven checks in
`.claude/agents/design-verifier.md`. Then give it to a **fresh** `design-verifier` agent (job 2)
through the `Agent` tool, in the foreground. Fix what it confirms, and run it again if the
fixes were more than wording. Tell the user what it found.

## Phase 10 — Close out

Fetch. If `origin/main` has moved, rebase before measuring anything. Then invoke the
`close-out` skill. It is mandatory, and it decides which documents beyond `architecture.md`
are owed.

Run the survey once more. Report the figures that moved, and the ratchet lists' sizes, which
are the baseline #283–#288 will shrink.

## Phase 11 — The pull request

Only on the user's explicit yes, by `fix-issue` Phase 7: `Closes #282` if every part of the
issue is done, and `Part of #282` otherwise. The labels are copied from #282, and `internal`
is decided by `CONTRIBUTING.md`'s file test.
