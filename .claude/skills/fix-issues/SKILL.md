---
name: "fix-issues"
description: "Work several tracked issues in parallel, one branch and one pull request each, chosen by severity label rather than by number. Analysis runs concurrently in isolated worktrees, and each issue is checked and built through the work-issue workflow; every plan, every gate and every question still reaches the user, and the test suite still runs one session at a time. Invoke when the user names a severity — Critical, High, Medium or Low — rather than an issue number."
argument-hint: "One or more severity labels, e.g. High, or Critical High"
user-invocable: true
disable-model-invocation: false
---

# Work several issues in parallel

The user has named a **severity**, not an issue. Choose a batch of issues carrying that severity,
show the batch for approval, then run them concurrently at **one issue = one branch = one pull
request**.

`$ARGUMENTS` holds one or more of `Critical`, `High`, `Medium`, `Low`, in any case and any order. If
none was given, ask which severity to work — do not guess, and do not fall back to "the most urgent".

This skill orchestrates. The per-issue work is [`fix-issue`](../fix-issue/SKILL.md) and its
phases are followed as written, the `work-issue` workflow and its gates included, except where a
subagent cannot follow them — each such deviation is
named below, with its reason, at the point it applies. `CLAUDE.md` governs the work itself: British
English, testing, the documentation layout. Neither document is restated here.

## What parallelises, and what does not

Three constraints shape every stage below. Do not design around them a second time.

**Analysis parallelises; the test suite does not.** `pytest_sessionstart` in `tests/conftest.py`
removes every schema-template directory except its own, so a second pytest session destroys the
first's templates mid-run. `pytest.ini` adds that `tmp_path_retention_count = 0` suppresses the
in-use lock file, so concurrent runs also clear each other's `tmp_path` trees. `/tmp` is a 923 MB
tmpfs on the Pi and one run's scratch has measured 817 MB. Either failure arrives dishonestly, as a
mass failure scattered across unrelated modules rather than as a lock error. Stage 4 serialises
pytest for this reason and the reason is not negotiable.

**A subagent has no channel to the user.** It cannot call `ExitPlanMode` and cannot call
`AskUserQuestion`. So this skill is the broker: agents draft and stop, you present, the user decides,
you resume the agent with `SendMessage`, which keeps its analysis context intact. An agent must never
be told to proceed on its own judgement where `fix-issue` asks for approval.

**Issues name their own files.** Almost every issue in this tracker carries a **Where it is**
section giving concrete `src/` paths. That is what makes Stage 0 possible without spawning anything.

## Stage 0 — Choose the batch

Read-only. Nothing is created, assigned or spawned in this stage.

**Gather.** For each severity given:

```bash
gh issue list --state open --label <Severity> --limit 200 \
  --json number,title,labels,assignees,body
```

Union the results. Every issue carries exactly one severity label, so there are no duplicates within
a single severity, but a user naming two severities may produce a longer list than they expect — say
how many candidates there were.

**Every issue type with the label is eligible**, bugs, `tech-debt` and `feature-request` alike
(decided 2026-09-17). A feature-request is not a defect, so Stage 1 adapts its questions rather than
excluding it; see the note there.

**Exclude, and record a reason for every exclusion.** The user sees the rejects as well as the picks.

| Exclude | How to tell |
|---|---|
| Already assigned | `assignees` is non-empty |
| Already has a branch | The number appears in a remote branch as `(fix\|feature\|hotfix\|docs)/<N>-` |
| Already has a pull request | `gh pr list --state open --json number,title,headRefName,body` |
| Linked branch not yet pushed | `gh issue develop --list <N>`, on survivors only |
| Deferred behind stewarding | See below |

`git branch -r` settles the branch check in one call — the `<prefix>/<number>-<slug>` convention
holds across the whole remote. Run `gh issue develop --list` only on what survives the cheaper
checks, to keep the call count bounded.

**Issues deferred behind the stewarding module are not eligible.** Stewarding is the next large
module and is unbuilt; its specification file is still empty. Defects in penalties, appeals,
verdicts and driver bans are deferred behind it deliberately, so planning a fix for one now is work
thrown away. Recognise them by subject, not by a fixed list of numbers — the list moves as issues
are filed, and lifts entirely once stewarding is built. Say which issues this rule removed, so the
user can overrule it for a specific issue if they judge it severable.

**Work out what each survivor touches.** Take the `src/`, `tests/`, `tools/` and `docs/` paths named
in its body — the **Where it is** section first, then any path named inline — and the functions it
names. This is the file set.

**Rank, then pick greedily.** Order by severity where several were given, then by how concretely the
issue is specified: an issue naming its files and its wrong behaviour is ready to work, one
describing only a symptom is not. Take issues in that order, skipping any whose file set intersects
one already taken, until **three** are chosen. Three is the default because the Pi has four cores and
1.8 GB of RAM, and because three plans is a reasonable amount to ask someone to review at once; the
user may name a different number when invoking.

Disjoint file sets are what keep the branches from conflicting at merge. Do **not** substitute the
module label for this test — `core` covers around half the open tracker, and two `core` issues
routinely touch nothing in common.

**Flag, never silently drop.** Two kinds of issue are shown in the batch with a warning rather than
removed, because the judgement is the user's:

- **Too large for one branch** — a feature-request amounting to a project of its own.
- **Blocked on a decision** — the body itself says the intended behaviour is undecided. It can still
  be worked, but it will come back as a question before it comes back as a plan.

**Present the batch and wait.** Show the chosen issues with their number, title, severity, file set
and why each was chosen; then every exclusion with its reason. Wait for approval or amendment. An
amendment may swap an issue in — re-run the disjointness check against the new set and say so if it
now overlaps.

## Stage 1 — Analyse, in parallel

One agent per chosen issue:

```
Agent(isolation: "worktree", run_in_background: true)
```

The worktree is what makes this safe: each agent gets its own working directory, `HEAD` and index
over a shared object store, and git allows a branch to be checked out in only one worktree at a
time. Roughly 50 MB of tracked files each; the object store is shared, not copied.

Each agent follows `fix-issue` Phases 1 and 2 and drafts Phase 3, under standing orders that must be
written into its prompt:

- **Do not call `ExitPlanMode`.** Return the plan as the report. *(Deviation from `fix-issue`
  Phase 3: a subagent has no approval channel. The plan still reaches the user — through Stage 2.)*
- **Touch nothing on GitHub.** No assignment, no branch, no comment. Claiming is Stage 3, and only
  after approval.
- **Make no commits and run no pytest.** Analysis only.
- **File nothing.** A separate defect found along the way is reported in the return, not filed —
  filing needs the user's explicit yes, which the agent cannot obtain.

**For a `feature-request`, Phase 2's question changes.** "Does it still reproduce" does not apply to
something that was never built. Ask instead: is it already built, is it still wanted, and what does
the owning wip-spec say about it — bearing in mind that where a wip-spec and the implementation
disagree, the implementation wins.

The report must carry `fix-issue` Phase 3's first six items in full: the root cause in terms of
what a league sees, the change file by file, the named test that fails before and passes after,
the commit points, the documents owed, and the proposed branch name. Anything less goes back to the
agent before it reaches the user. Items 7 and 8 come from the check in Stage 2, not from the agent.

An agent that finds the defect does not reproduce returns that finding and its evidence instead of a
plan. That is a success, not a failure.

## Stage 2 — Check, then review, one at a time, through the user

**Check each returned plan before the user sees it**, through the `work-issue` workflow's check
stage, exactly as `fix-issue` Phase 3 does: the architecture, the design files and the specs, the
product owner's questions to the user first, and items 7 and 8 added to the plan from its result.
Keep each check's result for the issue's build. Run the checks one at a time, as the plans come
back: each is three read-only agents, and the Pi runs two at once.

Bring each checked plan to the user in plan mode, singly: that is its Gate 1. Bring each question
through `AskUserQuestion`. Relay the answers to the owning agent with `SendMessage`, which resumes
it with its analysis intact — a fresh `Agent` call would start cold and re-read everything.

**Every plan passes Gate 1 before any issue's tests or build start.** Plan mode reaches every
running agent, a workflow's builders included, and halts them mid-edit.

Nothing here is inferred. A reply that changes the subject is not approval, and an agent waits until
its own plan is answered. A plan the user rejects ends that agent; a plan they amend goes back for
redrafting, not straight to Stage 3.

A "does not reproduce" finding goes to the user as a decision: close the issue, or keep it open with
what was learned recorded on it.

## Stage 3 — Claim, one issue at a time

Only for a plan the user has approved, and in this order.

```bash
gh api user --jq .login            # must be the account that should own this work
gh issue edit <N> --add-assignee @me
gh issue develop <N> --base main --name <approved-branch-name>
gh issue develop --list <N>        # confirm the link took
```

More than one account may be authenticated on the host. If the active one is not the right owner,
stop — do not assign, do not create a branch, do not switch account.

**`--checkout` is deliberately omitted.** *(Deviation from `fix-issue` Phase 4.)* The point of
`gh issue develop` is that the branch is created **through GitHub and linked to the issue**; a branch
created locally and pushed is not linked. That linkage happens with or without `--checkout`. What
`--checkout` would additionally do is move the main checkout's `HEAD` — which belongs to the user,
may hold other work, and would then own the branch the build's worktree needs, since git permits one
checkout of a branch at a time.

If the link is missing, do not carry on down an unlinked branch — say so and stop that issue.

**Once the link is confirmed, make the build's worktree by hand**, from the main checkout:

```bash
git fetch origin
git worktree add .claude/worktrees/fix-<N> <approved-branch-name>
```

The analysis agent's own worktree is gone by now: an isolated worktree its agent left unchanged is
removed as soon as the agent stops. This one takes up the branch `gh issue develop` created, as a
local branch tracking it, and leaves the user's `HEAD` alone. Its absolute path is the `worktree`
handed to the workflow, and `git -C <worktree> rev-parse HEAD`, taken now, before anything is
committed, is the `base`: never `git merge-base` from the main checkout, whose `HEAD` is the user's.
After a rebase onto `origin/main`, it is `git -C <worktree> merge-base HEAD origin/main`.

## Stage 4 — Build: through the workflow, one test run at a time

**Each issue is built by the `work-issue` workflow, exactly as `fix-issue` Phase 5 builds one**:
the tests stage, Gate 2, the build stage and Gate 3, with the arguments listed there. The
`worktree` is the issue's own `.claude/worktrees/fix-<N>`, by absolute path; the `python` is the
main checkout's `.venv/bin/python`; `criteria` and `checks` come from its Stage 2 check; and
`testsHead` is as `fix-issue` names it. The issues' runs go on concurrently, one run per issue at a
time. Tell the user each build takes about ten agents, and that the Pi runs two of a workflow's
agents at once.

**Each issue's Gate 2 file is written to the main checkout's `.claude/gates/<N>-gate-2.md`,** never
to the issue's worktree, so that every gate file stands in one place in the user's workspace, and a
worktree removed takes none with it. A build that stops with `testChanges` goes back through its
own tests stage and Gate 2, as `fix-issue` says, while the other issues' runs carry on.

**Every gate and every question goes to the user through `AskUserQuestion`, one issue at a
time,** never through plan mode, which would halt every other issue's build. A rejection at Gate 3,
or an answer that amends a plan, sends that issue back to the Stage 2 check. Its amended plan
waits for plan mode until no build is running.

The workflow's builders and testers keep two rules the whole batch depends on.

**Every path is staged by name. `git add -A`, `git add .` and `git commit -a` are forbidden in this
workflow** (decided 2026-09-17). Three separate things here are ignored or untracked and a blanket
stage sweeps up all of them: the league's own artwork under `resources/league/`, the worktrees under
`.claude/worktrees/` — each a full checkout carrying its own `.git`, which commits as a bare gitlink
pointing at a commit in a repository nobody else can reach — and whatever the other agents have in
flight. The hazard is worst precisely here, because a blanket stage cannot tell an agent's own change
from its neighbour's, and the resulting commit is on the wrong branch by the time anyone notices.
Name the files:

```bash
git add src/leaguebot/core/services/driver_service.py tests/core/test_driver_service.py
```

`git status --porcelain` before each commit, and stage from what it shows. An agent that cannot name
every path it is committing does not yet know what it changed.

**Every pytest invocation is wrapped in the lock**, targeted subsets while iterating as much as the
full run at the end. The workflow starts each one detached and waits on its process, since one
shell call is cut off after ten minutes and a wait for the lock can outlast that; run your own
close-out runs the same way:

```bash
cd <worktree> && rm -f LOG LOG.exit && nohup bash -c 'flock -E 75 -w 3600 /tmp/f1-pytest.lock env PYTHONPATH=src <main checkout>/.venv/bin/python -m pytest tests/ -q > LOG 2>&1; echo $? > LOG.exit' > /dev/null 2>&1 & echo $!
timeout 540 tail --pid=<that pid> -f /dev/null; cat LOG.exit 2>/dev/null || echo still running
```

LOG is a file under `/tmp` named for the run, and the first line clears it and its exit file, so
that an earlier run's exit code is never read as this one's. The second line is repeated until the
exit code appears; 75 is flock giving up on the lock.

The interpreter is the main checkout's virtualenv, which carries the pins, and `PYTHONPATH=src` makes
it test the worktree's own code rather than the checkout it was installed from (CLAUDE.md, Testing).

This is the whole mechanism by which "never run two pytest sessions at once" survives having several
agents at work. An agent waits for the lock; it does not skip the run, and it does not open a second
terminal. The timeout is an hour because a builder or tester may be waiting behind two other issues'
full runs.

Scratch stays on `/tmp` deliberately. Serialising means only one run's `tmp_path` trees exist at a
time, which is what the tmpfs has room for; pointing `TMPDIR` at the SD card instead would trade a
solved problem for a slower one on a disk that is already near full.

**An image-module issue cannot be finished in a worktree.** `resources/league/` holds the league's
own artwork — around 1,400 files — and all but nine `.gitkeep` files are gitignored, so a fresh
worktree has the empty skeleton. The build's suite includes the `rasteriser` tests wherever Inkscape
is installed, and in a worktree they run on that skeleton. CI has no artwork either and the suite is green there, so an
ordinary run in a worktree is sound, given `PYTHONPATH=src` so that it tests the worktree's own code
rather than the checkout the shared virtualenv was installed from (CLAUDE.md, Testing). A `-m rasteriser` run is not: it would pass without ever
touching the assets whose rendering it claims to check. Run the rasteriser marker by hand in the main
checkout, where the artwork lives, and verify its output as PNG rather than as SVG in a browser.

## Stage 5 — Close out, and deliver

**You run the `close-out` skill for each issue**, in that issue's worktree, once the user has
accepted it at Gate 3, and one issue at a time: its suite runs go behind the lock like any other.
It is mandatory per issue — one batch-wide close-out at the end would not know what each branch
changed. It is yours rather than a subagent's because you alone hold the user's answers, and
close-out writes each one into the wip-spec, as `fix-issue` Phase 6 says.

**Push and open the pull request only on the user's explicit yes**, one pull request per issue,
closing its number. **Label each as it is opened**, from its own issue: one work type, one severity
and one module, plus `internal` when the change touches nothing a league sees — the rule and the
file test are in `CONTRIBUTING.md`, "Pull requests", and the required check `pr-label-check`
refuses a pull request without them. The `fix-issue` skill's Phase 7 gives the commands. Report per
issue and say plainly whether each is fixed, partly fixed, or turned out not to reproduce.

Keep the worktree of any issue that ended red or unfinished, so the failure can be inspected. Remove
only the worktrees of issues that merged cleanly, and only once the user has seen the result.
