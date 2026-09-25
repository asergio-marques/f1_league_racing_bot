---
name: "fix-issue"
description: "Take a GitHub issue from the tracker through analysis and a plan checked against the architecture, the design files and the specs, then claim it — assign the issue and create its fix branch through GitHub so the branch is linked to the issue — and build it through the work-issue workflow: the failing tests first, then build, review and test until they pass, with the user deciding at each gate. Invoke when the user names an issue number to fix."
argument-hint: "The issue number, e.g. 123 or #123"
user-invocable: true
disable-model-invocation: false
---

# Fix a tracked issue

The user has named an issue to fix. Work it in phases, in order: **read it, verify it against
the code, plan the fix, have the plan checked and approved, claim it on GitHub, build it through
the workflow, close out, and open the pull request — labelled — once the user says so.**

The issue number is `$ARGUMENTS`. Strip a leading `#`. If no number was given, ask for one
before doing anything else — do not guess from the tracker.

`CLAUDE.md` governs how the work itself is done — testing, British English, the documentation
layout. Follow it; this skill does not restate it. Two of its rules bear directly on this job:

- GitHub issues are **the register of defects** — what is wrong, never what shall be done. Do
  not read an issue as a rule.
- **Where a wip-spec and the implementation disagree, the implementation wins** by default.
  Verify against `src/` before quoting a spec. The product owner in the `work-issue` workflow
  does not apply the default itself: it brings each disagreement to the user.

## Phase 1 — Read the issue

```bash
gh issue view <N> --json title,state,assignees,labels,body,comments
```

(`gh issue view --comments` fails on this repository. Use `--json`.)

Take from it: the behaviour a league sees, the reproduction path, the labels (the module label
names the spec and the how-to guide that own the area), and the priority. Read the comments — a
later one often narrows or contradicts the opening description.

If the issue is already closed, already has a linked branch (`gh issue develop --list <N>`), or
is assigned to somebody, say so and stop for a decision rather than piling on.

## Phase 2 — Verify it against the code

**Do not plan a fix for a defect you have not seen in `src/`.** Issues here are hand-written
from a league's experience and some are months old. Find the function and file the issue names,
read it, and establish for yourself:

- **Does it still reproduce?** The code may have moved on. If it has, report that with the
  evidence that settles it and ask whether to close the issue, rather than inventing a fix.
- **Is the issue's diagnosis right?** The reported line is often a symptom. Trace to the cause.
- **What does the owning wip-spec say, and is the spec itself wrong?** If building shows the
  spec wrong, correcting it is part of the fix, not a separate errand.
- **What tests cover this today, and why did they not catch it?** A defect that passed the
  suite means the suite has a hole; closing that hole is part of the fix.

Raise a **new** issue for any *separate* defect you turn up along the way — draft it in the
message and wait for an explicit yes before filing. Do not widen this fix to cover it.

## Phase 3 — Plan the fix, check it, and have it approved (Gate 1)

Draft the plan. It must carry:

1. **The root cause**, in a sentence, in terms of what a league sees.
2. **The change**, file by file, with the functions touched.
3. **The tests** — the named test that fails before the fix and passes after, plus any existing
   test the change invalidates.
4. **The commit points.** Name each one, and err well on the side of more.
5. **The documents that need updating** — the owning wip-spec, `README.md`, the module's how-to
   guide — or a stated "none, this restores documented behaviour".
6. **The branch name you propose**, prefixed by what the change is: `fix/`, `hotfix/`,
   `feature/` or `docs/`, matching the convention already on the remote.
7. **The spec rules it follows, and what a league will see**, from the check below.
8. **Architecture and design**, from the check below: the rules the fix touches, the listed
   breaches it removes, with their ratchet lines deleted in the same commit, that it adds none, and
   the changes each touched module's design file needs, or that the module has none yet.

**Then check the draft through the `work-issue` workflow** (`.claude/workflows/work-issue.js`).
Invoking this skill is the user's opt-in to running it. Its checkers are read-only and cannot
reach the user. **Do not enter plan mode while it runs:** plan mode reaches running agents and
halts them.

```
Workflow({ name: "work-issue", args: { stage: "check", issue: <N>, commit: "<short sha>", modules: [<module>, ...], plan: "<the draft>" } })
```

If the name does not resolve, pass `scriptPath: ".claude/workflows/work-issue.js"` instead.
`modules` names the module of every folder under `src/leaguebot/` the plan touches, and the
module of the issue's label: `core`, `results`, `attendance`, `signup`, `weather`, `image`,
`steward` or `stats`. Three checkers run side by side:

- **the issue reviewer**, against `docs/design/architecture.md` and its ratchet lists;
- **the issue reviewer again**, against each module's design file, or against the architecture
  alone where the module has none yet;
- **the product owner**, against the wip-specs, the constitution, the README and the guides. It
  alone judges a spec rule, and it asks where the documents do not settle something.

Settle what it returns before the user sees the plan:

- **`failed` is not empty:** resume the run. A missing check is a hole in the plan.
- **A breach the plan would add** changes the plan, as does every item of `planChanges`.
- **A breach the plan can remove** goes into it where the change already touches that code.
  Otherwise it stays with the issue its ratchet line names.
- **A change a design file needs** is a document owed.
- **`questions` go to the user through `AskUserQuestion`, before the plan.** Put the product
  owner's first, as it framed them, with its recommendation first. Every answer is a project rule
  from that moment, and a spec correction it calls for is a document owed. Where an answer changes
  the plan's substance, check the plan again.
- **`specRulesToSettle`** lists every spec rule the plan would change, or that is unclear or at odds
  with the code. Each must reach the user as a question: where the product owner framed none for
  one, frame it yourself from the entry.
- **`citations`**, the rules the product owner cited rather than ask, go into item 7, so that the
  user sees each one and can overrule it.

Keep the check's result: the build is handed items 7 and 8 from it.

**Then present the plan through plan mode: this is Gate 1.** The approval dialog does not always
show the plan, so also send the plan file with `SendUserFile` and paste it. Nothing is created on
GitHub until the plan is approved.

## Phase 4 — Claim the issue on GitHub

Only after the plan is approved, and in this order.

**Confirm the active account first**, and that it is the one that should own this work:

```bash
gh api user --jq .login
```

More than one account may be authenticated on a host. If the active one is not the right owner,
stop and say so — do not assign, do not create a branch, do not switch account yourself.

**Assign the active account:**

```bash
gh issue edit <N> --add-assignee @me
```

**Create the branch through GitHub, so it is linked to the issue.** This is the point of the
step: a branch created locally and pushed is *not* linked, and the issue will not show it.
Never substitute `git checkout -b` plus a push:

```bash
gh issue develop <N> --base main --name <approved-branch-name> --checkout
```

`--checkout` fetches and switches locally, which is the only local branch work this skill does.
Check the working tree is clean and you are on `main` before running it.

**Confirm the link took**, and report both:

```bash
gh issue develop --list <N>
git branch --show-current
```

If the link is missing, do not carry on down an unlinked branch — say so.

**Record where the branch starts**, straight after the checkout and before anything is committed:
`git rev-parse HEAD`. That is the `base` every stage is handed, so its reviewers read this issue's
work and nothing else. Take it again after any rebase.

## Phase 5 — Build it, through the workflow

The `work-issue` workflow builds the fix, in two stages with a gate between them. Build nothing by
hand. Pass every stage the same arguments:

- `issue`, `modules`, and `plan`: the approved plan, in full;
- `worktree`: the checkout's absolute path. That is the main checkout, which `--checkout` put on
  the branch;
- `python`: the absolute path of that checkout's `.venv/bin/python`;
- `branch`, and `base`: the commit Phase 4 recorded;
- `criteria`: the plan's item 7, and `checks`: its item 8;
- `decisions`: every answer the user has given on this issue, word for word, with its date. A
  spec change an answer calls for is written by the build, as a document owed;
- `citations`: for the build, the tests stage's `citations`, so that rules cited there carry on.

**Leave the checkout alone while a stage runs:** its builder is working in it.

### The tests stage, then Gate 2

```
Workflow({ name: "work-issue", args: { stage: "tests", ... } })
```

The builder writes and commits only the tests the plan says fail before the fix, each marked
`xfail(strict=True)` so that every commit stays green. The tester shows each test's real
failure, and the product owner and the issue reviewer judge whether each fails for the right
reason, and whether together they pin every spec rule the fix touches and everything a league
should see. Skip the stage only where the approved plan names no test that fails before the fix,
as for a pure move, and said so.

When it returns `passed`, **Gate 2**: put the tests to the user through `AskUserQuestion`,
with the product owner's `summary`, which says in plain terms what each test checks, and every
rule it cited. The options are to approve them or to change them. A change goes into
`decisions`, and the stage runs again with this result as `previous`.

### The build stage

```
Workflow({ name: "work-issue", args: { stage: "build", ... } })
```

Each round, the builder builds or fixes, and commits. Then four checkers look at the branch side
by side: the issue reviewer against the architecture, the design, the issue and the plan; the
code reviewer for defects in the code; the product owner against the spec rules and the
acceptance criteria; and the tester, who runs the whole suite and mypy behind the test lock. A
design verifier follows the issue reviewer wherever the branch changes a design file. The rounds
repeat until nothing material is open, no question is, and the suite and mypy are green, for at
most three rounds a run. Tell the user it takes about ten agents for a fix that passes on its
second round.

### What a stage returns

- **`question`:** put `escalations` to the user through `AskUserQuestion`, the business ones as
  the product owner framed them, its recommendation first. Add the answers to `decisions`, and
  run the same stage again with this result as `previous`, so its rounds carry on. An escalation
  that carries `finding` is a dispute the user rules on: pass their choice as
  `rulings: { "<finding>": "fix" }` or `"leave"`, and a finding left as built is closed.
- **`unfinished`:** run it again once, with `previous`. A second `unfinished` goes to the user,
  with `openMaterial` and `lastFailures`.
- **`failed`:** read `failure`. A host at fault, such as a full `/tmp`, is repaired and the stage
  run again. A builder that returned nothing, or a checkout on the wrong branch, is looked into
  before anything runs again.
- **`separateDefects`** are drafts for the tracker. Draft each in full, wait for an explicit
  yes, and file only what is approved.
- **`minor`** findings go to the user before the next gate, as a short list. Those they want made
  go back to the build stage in `decisions`, with this result as `previous`: nothing is fixed by
  hand, however small.
- **An empty `summary`** on a `passed` result means the product owner left it out twice. Ask a
  `product-owner` agent for it through the `Agent` tool, with the branch and the criteria, before
  the gate.

A checker that returned nothing is never read as a pass: the stage cannot pass without it.

**For the image module, look at the pictures yourself.** The build's suite includes the
`rasteriser` tests wherever Inkscape is installed, but no agent looks at what they draw. Run
`pytest tests/ -q -m rasteriser` in the main checkout, where the league's artwork is, and check the
output as PNG, never as SVG in a browser. CI deselects the marker, so nothing else catches a break.

### Gate 3 — acceptance

When the build returns `passed` and the minor findings are settled with the user, put the result to the user
through `AskUserQuestion`. Show the product owner's acceptance `summary`: what a league will now
see, criterion by criterion, with the test that proves each, and every rule it cited. The options
are to accept it or to reject it.

**On a rejection, ask what should change in terms of behaviour**, and take the answer in the
user's own words. It is a decided rule. Add it to `decisions`, amend the plan with it, and go
back to Phase 3's check, passing `worktree` and `base` as well, so that the amendment is checked
against the branch as built. Then come Gate 1, the tests stage, Gate 2, the build and Gate 3
again. The claim is not repeated, and the commits already made stay.

On acceptance, go on to Phase 6.

## Phase 6 — Close out

Invoke the `close-out` skill before reporting the work complete. It is mandatory, and it covers
the wip-specs, the README, the how-to guides and the test-suite run.

A bug fix that restores documented behaviour needs no document change — but that is an outcome
`close-out` reaches, not a reason to skip it. Any **decision the user made in conversation**
while planning this fix is a project rule from that moment and belongs in the wip-spec,
whatever the issue said. That includes every answer the user gave to a business question at a
gate or mid-build, and every behaviour change they asked for at Gate 3: each goes into the
wip-spec, and into the README and the module's guide where a league sees it. The build has written
these as documents owed, and its product owner has checked them; close-out confirms none was
missed. A rule the product owner cited needs nothing, being written down already. Where the user
ruled that the code was right and a spec wrong, the spec's correction is owed the same way.

Reference the issue by number in the closing report, and say plainly whether it is fixed,
partly fixed, or turned out not to reproduce.

## Phase 7 — Open the pull request

**Only on the user's explicit yes.** Push the branch and open the pull request against `main`,
and **label it as you open it** — a pull request here is refused by the required check
`pr-label-check` until its labels come from the issues it tracks. The rule is in
`CONTRIBUTING.md`, under "Pull requests"; in short:

- **Name the issue in the body.** `Closes #<N>` for a full fix, `Part of #<N>` for a partial one.
- **Copy one label from each group on the issue** — its work type, its severity and its module.
- **Add `internal`** when the change touches nothing a league sees, and never otherwise. The
  file test is in `CONTRIBUTING.md`, and the check applies it.

```bash
gh pr create --base main --label <work-type> --label <severity> --label <module> [--label internal] ...
python3 tools/check_pr_labels.py <PR>
```

The second command runs the check by hand, so a missing label is found before the workflow
reports it. Correct one with `gh pr edit <PR> --add-label` or `--remove-label`.
