---
name: "fix-issues"
description: "Work several tracked issues in parallel, one branch and one pull request each, chosen by severity label rather than by number. Analysis runs concurrently in isolated worktrees; every plan and every question still reaches the user, and the test suite still runs one session at a time. Invoke when the user names a severity — Critical, High, Medium or Low — rather than an issue number."
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

This skill orchestrates. The per-issue work is [`fix-issue`](../fix-issue/SKILL.md) and its six
phases are followed as written, except where a subagent cannot follow them — each such deviation is
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

The report must carry `fix-issue` Phase 3's six items in full: the root cause in terms of what a
league sees, the change file by file, the named test that fails before and passes after, the commit
points, the documents owed, and the proposed branch name. Anything less goes back to the agent
before it reaches the user.

An agent that finds the defect does not reproduce returns that finding and its evidence instead of a
plan. That is a success, not a failure.

## Stage 2 — Review, one at a time, through the user

Bring each returned plan to the user in plan mode, singly. Bring each question through
`AskUserQuestion`. Relay the answers to the owning agent with `SendMessage`, which resumes it with
its analysis intact — a fresh `Agent` call would start cold and re-read everything.

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
may hold other work, and would then own the branch the agent's worktree needs, since git permits one
checkout of a branch at a time. The agent takes it up in its own worktree instead:

```bash
git fetch origin && git checkout <approved-branch-name>
```

If the link is missing, do not carry on down an unlinked branch — say so and stop that issue.

## Stage 4 — Build: parallel edits, one test run at a time

Agents implement their approved plans concurrently and commit at the points their plans named.

**Stage every path by name. `git add -A`, `git add .` and `git commit -a` are forbidden in this
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
full run at the end:

```bash
flock -w 3600 /tmp/f1-pytest.lock python3 -m pytest tests/ -q
```

This is the whole mechanism by which "never run two pytest sessions at once" survives having several
agents at work. An agent waits for the lock; it does not skip the run, and it does not open a second
terminal. The timeout is an hour because the full suite takes about eight minutes and an agent may
be behind two others.

Scratch stays on `/tmp` deliberately. Serialising means only one run's `tmp_path` trees exist at a
time, which is what the tmpfs has room for; pointing `TMPDIR` at the SD card instead would trade a
solved problem for a slower one on a disk that is already near full.

**An image-module issue cannot be finished in a worktree.** `resources/league/` holds the league's
own artwork — around 1,400 files — and all but nine `.gitkeep` files are gitignored, so a fresh
worktree has the empty skeleton. CI has no artwork either and the suite is green there, so an
ordinary run in a worktree is sound. A `-m rasteriser` run is not: it would pass without ever
touching the assets whose rendering it claims to check. Run the rasteriser marker by hand in the main
checkout, where the artwork lives, and verify its output as PNG rather than as SVG in a browser.

## Stage 5 — Close out, and deliver

Each agent invokes the `close-out` skill for its own issue, in its own worktree, before reporting
that issue complete. It is mandatory per issue — one batch-wide close-out at the end would not know
what each branch changed.

**Push and open the pull request only on the user's explicit yes**, one pull request per issue,
closing its number. **Label each as it is opened**, from its own issue: one work type, one severity
and one module, plus `internal` when the change touches nothing a league sees — the rule and the
file test are in `CONTRIBUTING.md`, "Pull requests", and the required check `pr-label-check`
refuses a pull request without them. The `fix-issue` skill's Phase 7 gives the commands. Report per
issue and say plainly whether each is fixed, partly fixed, or turned out not to reproduce.

Keep the worktree of any issue that ended red or unfinished, so the failure can be inspected. Remove
only the worktrees of issues that merged cleanly, and only once the user has seen the result.
