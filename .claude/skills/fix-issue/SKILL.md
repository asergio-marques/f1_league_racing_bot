---
name: "fix-issue"
description: "Take a GitHub issue from the tracker through analysis and a reviewed plan, then claim it — assign the issue and create its fix branch through GitHub so the branch is linked to the issue — before implementing. Invoke when the user names an issue number to fix."
argument-hint: "The issue number, e.g. 123 or #123"
user-invocable: true
disable-model-invocation: false
---

# Fix a tracked issue

The user has named an issue to fix. Work it in phases, in order: **read it, verify it against
the code, plan the fix and have the plan approved, claim it on GitHub, then build.**

The issue number is `$ARGUMENTS`. Strip a leading `#`. If no number was given, ask for one
before doing anything else — do not guess from the tracker.

`CLAUDE.md` governs how the work itself is done — testing, British English, the documentation
layout. Follow it; this skill does not restate it. Two of its rules bear directly on this job:

- GitHub issues are **the register of defects** — what is wrong, never what shall be done. Do
  not read an issue as a rule.
- **Where a wip-spec and the implementation disagree, the implementation wins.** Verify against
  `src/` before quoting a spec.

## Phase 1 — Read the issue

```bash
gh issue view <N> --comments
```

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

## Phase 3 — Plan the fix, and get the plan approved

Present the plan through plan mode (`ExitPlanMode`). It must carry:

1. **The root cause**, in a sentence, in terms of what a league sees.
2. **The change**, file by file, with the functions touched.
3. **The tests** — the named test that fails before the fix and passes after, plus any existing
   test the change invalidates.
4. **The commit points.** Name each one, and err well on the side of more.
5. **The documents that need updating** — the owning wip-spec, `README.md`, the module's how-to
   guide — or a stated "none, this restores documented behaviour".
6. **The branch name you propose**, prefixed by what the change is: `fix/`, `hotfix/`,
   `feature/` or `docs/`, matching the convention already on the remote.

Nothing is created on GitHub until this plan is approved.

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

## Phase 5 — Build it

Work the approved plan, committing at the points it named. Testing follows `CLAUDE.md`: a
targeted subset as you go, the full `pytest tests/ -q` at the end, and the `rasteriser` marker
by hand if the fix touches the image module.

## Phase 6 — Close out

Invoke the `close-out` skill before reporting the work complete. It is mandatory, and it covers
the wip-specs, the README, the how-to guides and the test-suite run.

A bug fix that restores documented behaviour needs no document change — but that is an outcome
`close-out` reaches, not a reason to skip it. Any **decision the user made in conversation**
while planning this fix is a project rule from that moment and belongs in the wip-spec,
whatever the issue said.

Reference the issue by number in the closing report, and say plainly whether it is fixed,
partly fixed, or turned out not to reproduce.
