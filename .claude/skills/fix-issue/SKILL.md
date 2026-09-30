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

**One issue per session.** This session re-reads everything it has read at every step, so an
issue started here carries the whole of the last one's stages with it. If this session has already
worked an issue, tell the user and suggest a fresh session before starting; and once this issue's
pull request is open, say that the next one belongs in a fresh session too.

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
3. **The tests** — every test to be added, modified or deleted, each with the scenario it tests:
   the named tests that fail before the fix and pass after; each existing test the change alters,
   whether its expectation or a call the change rewrites; and each the change makes obsolete. The
   tests stage makes all of them, and the build may change no test after Gate 2, so a test left out
   here stops the build later.
4. **The commit points.** Name each one, and err well on the side of more.
5. **The documents that need updating** — the owning wip-spec, `README.md`, the module's how-to
   guide — or a stated "none, this restores documented behaviour".
6. **The branch name you propose**, prefixed by what the change is: `fix/`, `hotfix/`,
   `feature/` or `docs/`, matching the convention already on the remote.
7. **The spec rules it follows, and what a league will see**, from the check below.
8. **Architecture and design**, from the check below: the rules the fix touches, the listed
   breaches it removes, with their ratchet lines deleted in the same commit, that it adds none, and
   the changes each touched module's design file needs, or that the module has none yet.

**Propose the light path where the change is mechanical, or only applies a decided rule.** A
mechanical change qualifies where all of these hold: no rule a league sees is added or changed; no
reply, command or post is new or reworded; and the schema does not change. A traceback kept, a
helper reused, a refactor, and a test-only or tooling change qualify. A change that only applies a
decided rule qualifies too, where every rule a league sees in it is already decided and written in a
wip-spec or the constitution, and the change applies it site by site, as recording each refusal in
the log channel applies the core specification's rule. A new or reworded rule, a new command, or a
schema change still does not qualify. Say so in the plan, and the user chooses the path at Gate 1.
On the light path:

- no check runs: items 7 and 8 say "none: the change is mechanical", or for a decided rule, item 7
  names the rule applied and where it is written; the architecture's rules are held by the tests
  that run with the suite;
- after Gate 1 and the claim, the issue is built by hand in its checkout instead of through Phase
  5: each change with its tests in the same commit, then the full suite behind the lock, and mypy;
- one fresh `code-reviewer` agent, through the `Agent` tool, reviews the whole diff against the
  plan, and what it finds is fixed, each in a commit of its own;
- then Phase 6 and Phase 7, as for any issue.

**A plan may split between the two paths.** Where only some commit points apply a decided rule, the
plan marks them, and the user may choose at Gate 1 to build those by hand, with one independent
review, and the rest through the workflow. Name them in `handBuilt` on every stage: the build
treats them as done rather than missing, and its reviewers leave their commits to that review.
Where they depend on nothing the workflow builds, build them first, and record `base` after them;
otherwise build them after Gate 3, before close-out.

Where the user declines the light path at Gate 1, the plan has not been checked: run the check
below, and hold Gate 1 again on the plan it returns. Where the build shows the change is not
mechanical after all, because a league would see it, stop and tell the user: the issue goes back to
the full workflow from Phase 3.

**Otherwise, check the draft through the `work-issue` workflow** (`.claude/workflows/work-issue.js`).
Invoking this skill is the user's opt-in to running it. Its checkers are read-only and cannot
reach the user. **Do not enter plan mode while it runs:** plan mode reaches running agents and
halts them.

```
Workflow({ name: "work-issue", args: { stage: "check", issue: <N>, commit: "<short sha>", modules: [<module>, ...], plan: "<the draft>" } })
```

If the name does not resolve, pass `scriptPath: ".claude/workflows/work-issue.js"` instead.
`modules` names the module of every folder under `src/leaguebot/` the plan touches, and the
module of the issue's label: `core`, `results`, `attendance`, `signup`, `weather`, `image`,
`steward` or `stats`. Three checkers run, the first two side by side and the product owner after
them:

- **the issue reviewer**, against `docs/design/architecture.md` and its ratchet lists;
- **the issue reviewer again**, against each module's design file, where a module has one; where
  none has, no agent runs and the check says so;
- **the product owner**, against the wip-specs, the constitution, the README and the guides. It
  alone judges a spec rule, and it asks where the documents do not settle something. It also
  settles, in the same pass, the business questions the first two met.

Settle what it returns before the user sees the plan:

- **`failed` is not empty:** resume the run. A missing check is a hole in the plan.
- **A breach the plan would add** changes the plan, as does every item of `planChanges`.
- **A breach the plan can remove** goes into it where the change already touches that code.
  Otherwise it stays with the issue its ratchet line names.
- **A change a design file needs** is a document owed.
- **`questions` go to the user through `AskUserQuestion`, before the plan.** Put the product
  owner's first, as it framed them, with its recommendation first. Two checkers can meet the same
  question and ask it twice: merge those into one before the user sees them. Every answer is a project rule
  from that moment, and a spec correction it calls for is a document owed. Where an answer changes
  the plan's substance, check the plan again, passing the last check's result as `previous`: each
  checker is given the plan as it last checked it and its own earlier result, and judges what the
  amendment changes rather than starting over. A checker whose earlier result was lost checks in
  full. **A plan is re-checked once at most.** Where a second amendment would need a third check,
  the plan is still moving: ask the user instead whether to narrow the scope to the issue as filed,
  and check once more only on their word.
- **`specRulesToSettle`** lists every spec rule the plan would change, or that is unclear or at odds
  with the code. Each must reach the user as a question: where the product owner framed none for
  one, frame it yourself from the entry.
- **`citations`**, the rules the product owner cited rather than ask, go into item 7, so that the
  user sees each one and can overrule it.
- **`assumed`** are reversible calls (wording, a log line's form, naming) the checkers took on
  their recommendation rather than ask. List them in the plan under "Assumed", each with what was
  assumed, for the user to overrule at Gate 1; never ask them one by one.
- **`followUps`** are what the plan does not need: the same fault elsewhere, a neighbouring gap, a
  rule the issue does not name. They never widen the plan. Draft each for the tracker, show the
  drafts beside the plan, and file only what the user approves.

Keep the check's result, saved as "What a stage returns" says: the build is handed items 7 and 8
from it, and a re-check is handed it as `previous`.

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
work and nothing else. After a rebase it is the commit the branch now sits on: straight after
rebasing onto `origin/main`, that is `git merge-base HEAD origin/main`, never `HEAD`.

## Phase 5 — Build it, through the workflow

The `work-issue` workflow builds the fix, in two stages with a gate between them. Build nothing by
hand. Pass every stage the same arguments:

- `issue`, `modules`, and `plan`: the approved plan, in full;
- `worktree`: the checkout's absolute path. That is the main checkout, which `--checkout` put on
  the branch;
- `python`: the absolute path of that checkout's `.venv/bin/python`;
- `branch`, and `base`: the commit Phase 4 recorded;
- `criteria`: the plan's item 7, and `checks`: its item 8, which the workflow leaves out of a
  prompt wherever the plan already holds them word for word;
- `handBuilt`: where the plan is split, the commit points built by hand, by the names the plan
  gives them;
- `decisions`: every answer the user has given on this issue, word for word, with its date. A
  spec change an answer calls for is written by the build, as a document owed;
- `citations`: for the build, the tests stage's `citations`, so that rules cited there carry on;
- `provisional`: for the build, the tests stage's `provisional`, less any the user overruled, so that
  the calls taken there bind the build too;
- `testsHead`: for the build, the commit at which the user last approved the tests at a Gate 2:
  the last of the tests stage's `commits` then, in this pass or, where a pass after a rejection at
  Gate 3 skipped the stage, an earlier one. It is `base` only where no tests stage has run on the
  branch at all. After a rebase it is that commit as the branch now carries it. From it the build
  changes no test but those the next paragraph allows. Pass it to a tests stage run again once the
  build has begun too: it then accepts each test whose marker the build has removed as passing;
- `models` and `efforts`, each `{role: value}`, only to override a role's default. Every stage,
  the check included, takes them. The roles are `testsBuilder` (the tests stage's builder),
  `builder` (the build's), `issue`, `code`, `product`, `design`, `tester` and `triage`. By default
  Sonnet runs the build's builder, and the tester at low effort, and Opus runs every other role, all
  at high effort but the tester. A model is `opus`, `sonnet` or `haiku`, and `fable` is refused; an
  effort is `low`, `medium` or `high`, and none above high is taken.

**Leave the checkout alone while a stage runs:** its builder is working in it.

### The tests stage, then Gate 2

```
Workflow({ name: "work-issue", args: { stage: "tests", ... } })
```

The builder makes every change to `tests/` the work needs, and no production code: it adds the
tests the plan says fail before the fix, changes the existing tests the fix alters, deletes those
it makes obsolete, and changes the fixtures and helpers they need. Each test that fails before the
fix is marked `xfail(strict=True)`, so that every commit stays green; one that passes already, as
after a rejection at Gate 3, is left unmarked, and the tester checks that it passes. The ratchet
lines the plan names are the one thing left to the build, which deletes each with its breach.

The builder lists every test it adds, modifies, deletes or moves, each with the scenario it sets
up, what it expects, what it did before where it is modified, and why it goes where it is deleted;
and every fixture, helper or value it changes, with what it now does. It returns only what it
changes in the list, which the workflow keeps and merges. The tester runs
`tools/changed_tests.py` against the branch, and the round is not green until the list matches it
entry for entry. The tester shows each test's real failure, and the product owner and the issue
reviewer judge whether each fails for the right reason, whether each entry says what its test
does, and whether together they pin every spec rule the fix touches and everything a league should
see. Skip the stage only where the approved plan changes no test at all, and said so.

When it returns `passed`, **Gate 2**, which is shown in a file of its own:

1. Write the result's `report`, word for word, to `.claude/gates/<N>-gate-2.md` in the main
   checkout, replacing any earlier one. It lists every entry, labelled A1, M1, D1, MV1 and S1 on,
   with its scenario, then the product owner's summary and every rule cited. Where the summary is
   empty, get it as "What a stage returns" says and put it in place of the line that says so.
2. Send the file to the user with `SendUserFile`.
3. Ask through `AskUserQuestion`, naming the file and giving the result's `counts` ("15 added,
   1 modified, 1 deleted, 0 moved; 6 supporting"). The options are to approve the tests or to
   change them. A change goes into `decisions`, in the user's words, with the node id written
   beside each label they name, and the stage runs again with this result as
   `previous`. The file it writes next marks each entry new or changed since, and lists any gone.

### The build stage

```
Workflow({ name: "work-issue", args: { stage: "build", ... } })
```

Each round, the builder builds or fixes, and commits. Then four checkers look at the branch side
by side: the issue reviewer against the architecture, the design, the issue and the plan; the
code reviewer for defects in the code; the product owner against the spec rules and the
acceptance criteria; and the tester, who runs the whole suite and mypy behind the test lock. A
design verifier follows the issue reviewer wherever the branch changes a design file. The rounds
repeat until nothing material is open, no question is, and the suite and mypy are green, within
the stage's round budget (see `capped` below). A builder carries out at most three commit points, or three findings, in
one piece, and hands the rest to a fresh builder, which carries on in the same round; the round is
reviewed once, after its last piece. A question a piece asks goes at once to the checker whose
ground it is: where every one is answered from a written rule or taken on its recommendation, the
next piece carries on with the answers, which the next gate lists among the calls taken; one that
needs the user ends the pieces. A proposed test change does not end them: the round's proposals
reach the user together. Tell the user it takes about ten agents for a fix that passes on its second
round, one more for each piece a builder hands off, and one or two for each piece whose questions
are settled mid-round, one for each kind of question it asks;
the design verifier and, in a tests round that cannot pass, the issue reviewer are left out of a
round they have nothing to do in.

**The build changes no test the user did not approve at Gate 2,** but four kinds. From `testsHead`
it may remove the issue's markers, delete the ratchet lines the plan names, rewrite the imports and
patched paths that a move of the plan's rewrites, and adjust a stub, fake, fixture or exact-call
assertion that the plan's own change breaks, to the new shape and no further. It lists each
adjustment in `adjusted`, the code reviewer judges each, and Gate 3 shows each to the user (decided
2026-09-30, #483); nothing else under `tests/`. The tester runs `tools/changed_tests.py` from `testsHead` each round, and any other
change goes back to the builder to revert. A test change the build needs, including one a checker's
finding calls for, is proposed in `testChanges`, and the stage stops for the user.

### What a stage returns

**Save every result to a file, and read into the conversation only what the next step needs.** A
result runs to tens of kilobytes, and whatever this session reads it re-reads at every later step,
to the end of the issue. When a stage returns, copy its whole result from the task's output file to
`.claude/gates/<N>-<stage>.json` in the main checkout (gitignored), a new name for each run, and
print from it only what is acted on: `status` and `failure`; `escalations`, `testChanges` and
`provisional`; the ids and titles of `openMaterial` and `minor`, with the `why` and `fix` of those put to the user;
`counts`; `adjusted`; and `report` or `summary`, written straight to the gate's file. A result is too large to
pass back inline, so where a run needs one as `previous`, or a tests stage's `citations`, copy the
workflow script into `.claude/gates/`, write the saved JSON in place of `ARGS.previous` or
`ARGS.citations`, and run the copy by `scriptPath`: the Workflow tool runs a script only from the
working directory. Leave out the result's `report` and `lastTest` as you bake it in: the workflow
reads neither from `previous`, and they are most of its size. Keep everything else, `listSeen` and
`shown` included: they are small, and without them the next run sends its reviewers the whole list
again and the next Gate 2 loses its marks.

- **`question`:** put `escalations` to the user through `AskUserQuestion`, the business ones as
  the product owner framed them, its recommendation first. Merge any two that ask the same thing.
  One marked `unframed` comes as its asker wrote it, since no checker settled it: frame it for the
  user in plain terms first. Add the answers to `decisions`, and run the same stage again with
  this result as `previous`, so its rounds carry on. An escalation that carries `finding` is a
  dispute the user rules on: pass their choice as `rulings: { "<finding>": "fix" }` or `"leave"`.
  Every such dispute needs a ruling, or the stage refuses to run; a finding left as built is
  closed. `rulings` carries the last result's disputes and minor findings only, never an older
  run's. Where `failure` names the host as well, repair it before the stage runs again.
- **`testChanges`**, on a `question` result from the build, are the test changes it needs and has
  not made. Put each to the user through `AskUserQuestion`, in the question itself: the test, the
  change, its scenario, what it expects, and why the build needs it. The options are to make it, to
  change it, or to refuse it and build without it. Record each answer in `decisions`, answer any
  `escalations` beside them, and then:
  - where one is to be made, run the **tests stage** again with its own last result as `previous`,
    to make it; hold Gate 2 again on the file it writes, where the new entries are marked; and then
    run the build again with its last result as `previous` and the new `testsHead`;
  - where every one is refused, run the build again with its last result as `previous`.
- **`provisional`** are the reversible calls the stage took on a checker's recommendation rather
  than stop for: wording, a log line's form, naming. They bind the builder until overruled. Show
  `provisionalNew`, the calls no gate has shown yet, at the next gate, where the Gate 2 report
  already lists them by id, and let the user overrule any in the same answer. An overruled call's
  answer goes into `decisions`, and its id into `overruled` on the stage's next run, which drops
  it; the run always gets a round to apply it. Never ask them one by one.
- **`capped`:** the stage has spent its round budget (`tests` 3, `build` 4, counted across all its
  runs) without passing. **`stalled`:** it stopped early because it was not converging, two rounds
  running opening as many material findings as they closed, or failing the same way twice; `failure`
  says which. Either goes to the user as one question, with what is still open, and three choices:
  finish on the light path by hand (recommended), allow one more round, or accept the branch as it
  stands and draft what is open as follow-ups. One more round is a run with `previous` and, for a
  capped stage, `roundBudget` one above its `lastRound`, which later runs keep; for a stalled one,
  `maxRounds: 1`. Never raise the budget without the user's word. A run the user starts in any
  other way, after answering its questions or asking for changes at its gate, always gets a round.
- **`unfinished`:** run it again once, with `previous`. A second `unfinished` goes to the user,
  with `openMaterial` and `lastFailures`. A finding there that the user once wanted made, though
  it was found minor, can still be left: pass it as `"leave"` in `rulings`.
- **`failed`:** read `failure`. A host at fault, such as a full `/tmp`, is repaired and the stage
  run again. A builder that returned nothing, or a checkout on the wrong branch, is looked into
  before anything runs again.
- **`separateDefects`** are drafts for the tracker. Draft each in full, wait for an explicit
  yes, and file only what is approved.
- **`minor`** findings are left by default. List them once, in the next gate's question, and let
  the user name any they want made: those go back to the same stage, with this result as
  `previous` and each as `rulings: { "<id>": "fix" }`, which makes it owed and has its checker
  confirm it. Pass every other as `"leave"` in that run, so that none is listed twice. Nothing is
  fixed by hand, however small.
- **An empty `summary`** on a `passed` result means the product owner left it out twice. Ask a
  `product-owner` agent for it through the `Agent` tool, with the branch and the criteria, before
  the gate. At Gate 2, give it the result's `tests` with their labels, and have it cite them.

A checker that returned nothing is never read as a pass: the stage cannot pass without it.

**For the image module, look at the pictures yourself.** The build's suite includes the
`rasteriser` tests wherever Inkscape is installed, but no agent looks at what they draw. Run
`pytest tests/ -q -m rasteriser` in the main checkout, where the league's artwork is, and check the
output as PNG, never as SVG in a browser. CI deselects the marker, so nothing else catches a break.

### Gate 3 — acceptance

When the build returns `passed`, put the result to the user through `AskUserQuestion`. Show the
product owner's acceptance `summary`: what a league will now see, criterion by criterion, with the
test that proves each, and every rule it cited. List beside it, once, every `provisional` call,
every `minor` finding, and every test in `adjusted`, with why the build changed it after Gate 2,
for the user to overrule or name in the same answer. The options are to
accept it or to reject it.

**On a rejection, ask what should change in terms of behaviour**, and take the answer in the
user's own words. It is a decided rule. Add it to `decisions`, amend the plan with it, and go
back to Phase 3's check, passing `worktree` and `base` as well, so that the amendment is checked
against the branch as built, and the last check's result as `previous`, so that each checker judges
what the amendment changes. Then come Gate 1, the tests stage, Gate 2, the build and Gate 3
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
