---
name: verify-fixes
description: Fresh-eyes check that the fixes made after a PR review actually fix the findings. Run it in a new conversation after /address-review or any fix round. It re-reads the review threads, finds the fix commits, checks every finding against the code as it is now, runs the tests, and reports what is fixed, what is not, and what the fixes changed that nobody asked for. Read-only. Use when the user asks to verify, check, double-check, or re-review the fixes for review comments.
---

Target: $ARGUMENTS. A PR number or URL is the PR; `since <sha>` names the last commit the reviewers saw, so everything after it is a fix; pasted text (an /address-review report, a list of commits) is the claim under test. Nothing means the open PR for the current branch (`gh pr view --json number`); if there is none, say so and stop. The repo is the one in the current directory.

This is the second pair of eyes on a fix round, meant to run in a fresh conversation. Nothing said earlier in this conversation and nothing in a pasted report is evidence: the report is the claim being tested, and the agent that wrote the fixes is the one most likely to believe they work. Evidence is GitHub, git, the code at the fix head, and what you run. Read-only: no edits, no commits, no pushes, no GitHub replies, no resolved threads, no `git stash`, no reset, no new worktrees.

## 1. Gather

The PR and its review:

```
gh pr view <pr> --json number,title,url,headRefName,baseRefName,headRefOid,body,author
gh api graphql -F owner=<owner> -F repo=<repo> -F pr=<number> -f query='
query($owner:String!,$repo:String!,$pr:Int!){repository(owner:$owner,name:$repo){pullRequest(number:$pr){
  reviewThreads(first:100){nodes{id isResolved isOutdated path line originalLine
    comments(first:50){nodes{databaseId author{login} body createdAt url originalCommit{oid}}}}}
  reviews(first:50){nodes{author{login} state body submittedAt url commit{oid}}}
  comments(first:100){nodes{author{login} body createdAt url}}}}}'
```

A finding is one of: a review thread, resolved or not (resolving a thread is part of the claim, not evidence), a point made in a review summary body, or a PR conversation comment that asks for a change. Read the whole thread: a later reply often narrows the ask, and the last reply may be the reviewer withdrawing it. Skip approvals with no content and CI/coverage status bots. Review bots count as reviewers. Number the findings in the order a reader would fix them; if an /address-review report was pasted, keep its numbering so the two reports line up.

The code under test is the branch's newest commit, wherever the fixes live: the current checkout if it is on `<headRefName>`, else a worktree on that branch (`git worktree list --porcelain`, the entry with `branch refs/heads/<headRefName>`; on this machine `~/dev/.claude/worktrees/<branch-slug>`), else `origin/<headRefName>` after `git fetch origin`. Read the code from that checkout, never from the diff alone. Uncommitted changes there are not under test: say so and check the committed head. A local head behind its remote means someone pushed on top; say so and check the remote head.

The fix commits are, in order of preference: everything after `since <sha>`; the commits a pasted report names, checked to exist; else everything after the newest commit any reviewer saw (the latest `commit.oid` / `originalCommit.oid` by date):

```
git log --format='%h %ci %an %s' <reviewed>..<head>
git log --format='%h %s' origin/<headRefName>..<head>      # the unpushed ones
```

No fix commits at all: report that and stop; there is nothing to verify.

Read the rules the fix round was held to: every `CLAUDE.md` / `AGENTS.md` from the repo root down to the touched paths, a `REVIEW.md` at the root if one exists, the `/review` skill's `repos/<name>.md` for this remote if it exists (`basename -s .git "$(git remote get-url origin)"`), and the ticket the PR names.

## 2. Map fixes to findings

For each finding, find the commit or commits that address it: the paths and lines the thread anchors to, the commit message, the diff. A reply on the thread naming a commit, or a pasted report line saying `fixed in <sha>`, is a pointer to check, never proof. A finding with no commit touching its code path and no posted reply declining it is Unaddressed. A commit that maps to no finding is collateral and goes to section 4.

## 3. Check each finding

Check the finding as the reviewer described it against the code at head, not against the fix commit's diff: the diff shows what changed, the code shows whether the problem is gone.

- Reconstruct the failure the reviewer described (the input, the state, the path) and walk it end to end through the code as it is now: the changed function, its callers, the sibling code that does the same job, the tests. Try to make the original problem happen again, then look for where it moved: a check added at one call site while another still lacks it, a fix on the happy path with the error path unchanged, a fix that assumes a value the caller never sets, a rename that left the old name reachable.
- Run what can be run: the tests the fix commits added or changed, then the tests for the touched modules, with the repo's runner and markers (backend: `ENVIRONMENT=test poetry run pytest tests/suite/<domain> -m unit`; never a bare `pytest tests/`). A test added by the fix counts as coverage only if it would fail without the fix: read what it asserts against what the fix changed; a test that mocks the fixed function or asserts nothing the finding described is not coverage. A small reproduction script in the scratchpad is fine when the path is cheap to drive.
- A fix to a prompt, a tool docstring, a tool result, or an agent's state assembly needs an eval scenario in the fix commits, both polarities where relevant, and the adapter update if state assembly changed. Run the scenario when the target is cheap (`poe evals run --scenario <id> --target <t>`); otherwise say it was not run.
- A decline (a posted reply or a pasted report line giving a reason for no change) is checked the same way: does the reason survive the code? A product call the user decided holds unless the code contradicts the decision as stated.

Verdicts, exactly one per finding:
- **Fixed**: the described problem cannot happen at head. Say what was read and run.
- **Partly fixed**: the fix covers part of the trigger or scope. Say what is left.
- **Not fixed**: a commit claims it, but the problem still occurs or moved.
- **Unaddressed**: no fix commit and no decline.
- **Declined, holds** / **Declined, wrong**: the fixer said no with a reason, and the reason does or does not survive the code.
- **Withdrawn**: the reviewer took it back in the thread.

Anything still open carries the review's severity tiers (Blocking / Should fix / Polish), ranked by production blast radius, not by how loud the comment was. If a verdict rests on something inferred rather than run or read, say what it changes to if the inference is wrong.

## 4. What the fixes did besides fixing

Read the whole diff of the fix commits as `/review` reads a PR, scoped to the fix round:

- **Behavior ledger.** Every observable behavior the fix commits change that no finding asked for: defaults, conditions, ordering, removed branches, return values, error handling, human-facing copy. Each is UNREQUESTED unless a finding or the ticket traces to it.
- **Weakened checks.** A test deleted, an assertion loosened, a skip or xfail added, a baseline, goldens, or standing-reds file edited, a lint or type ignore added, a fixture changed so an old assertion passes vacuously, an eval scenario softened. Each is Blocking; the fix round was not allowed to do any of it.
- **Stale artifacts.** Generated files the fix should have regenerated (API specs, eval coverage matrices, prompt goldens), a `CLAUDE.md` sentence the fix makes stale, a migration head fork.
- **The new code itself.** Edge cases, error paths, nullability, transaction boundaries, tenant scoping of what the fix added.
- **Mergeability.** The branch still merges cleanly against its base; if pushed, what CI says (`gh pr checks <pr>`).

Try to disprove each candidate against the code and drop anything you cannot confirm. Pre-existing issues the fix commits did not touch are out of scope unless a fix made them reachable.

## Report, then stop

Open with one line: `#<pr> fix round: <n> fixed · <n> partly · <n> not fixed · <n> unaddressed · <n> declined · <n> new`.

Per finding:

```
<n>. [<verdict> · <severity if still open>] <reviewer> on <path>:<line>
   Asked: <the ask in one plain sentence>
   Fix: <sha> <what it changed> | none | declined: <the reason given>
   Checked: <what was read and run, with the result>
   Left: <what still needs doing>            (only when something is)
```

**Introduced by the fixes** — Blocking / Should fix / Polish, each `path:line` — the issue, its trigger and consequence, the smallest fix, and how it was verified. "None found" is a line.

**Tests** — what ran and the result, and which findings the added tests actually cover.

**Not verified** — tests and evals not run, anything inferred rather than checked, and what changes if the inference is wrong.

**Verdict** — one line: `Ready to push` (every finding Fixed, Declined-holds, or Withdrawn, and nothing new above Polish); `Back to /address-review: #<n>, #<m>` (the open findings and the new ones, by number); or `Rethink: <why>` (the fix round changed behavior nobody asked for, or the fix sits in the wrong place).

Then stop. Change nothing, post nothing, and do not start fixing what you found; that is the next /address-review round's job.
