---
name: address-review
description: Work through the review comments left on a pull request. It verifies every finding against the current code and reports which are real, asks the user only the product calls, then fixes the confirmed findings, pushes the branch, and posts a reply on every finding, without stopping for a go. Use when the user asks to verify, check, triage, or address PR review comments or feedback.
---

Target: $ARGUMENTS. A PR number or URL is the PR; a trailing `check` (or a request to only verify) stops after step 1. Nothing means the PR this conversation is about (`Shared skill arguments` in `~/.agents/AGENTS.md`), else the open PR for the current branch (`gh pr view --json number`); if there is none, say so and stop. The repo is the one in the current directory.

Two steps, always in order:

1. **Verify**: read every open finding, check it against the code as it is now, report. Nothing changes during this step: no edits, no commits, no GitHub replies, no resolved threads.
2. **Address**: follows straight on, with no go to wait for, unless the target said `check` or step 1 found nothing to fix (then step 1's report ends the run). Its one stop is the product calls, asked in a single round before any code changes.

Flow: this is the author's side of a review round. The findings come from reviewers' threads (or a `/review` the user points at); `/verify-fixes <pr>` in a fresh conversation is the second pair of eyes on what this skill fixed, and sends the next round back here (`Back to /address-review`) until its closing pass, from round 3 on, ends it. `/fix <pr>` owns CI in the meantime: a red `Eval sample` that no finding covers, and a check that is red for an unrelated reason, are `/fix`'s, not this round's. Where this skill works (a worktree on the PR branch) is where `/pickup` started and where `/cleanup` finishes.

## Step 1: verify

### Gather

```
gh pr view <pr> --json number,title,url,headRefName,baseRefName,headRefOid,body,author
gh pr diff <pr>
```

Inline review threads, with resolved and outdated state (the REST comments API cannot tell you either):

```
gh api graphql -F owner=<owner> -F repo=<repo> -F pr=<number> -f query='
query($owner:String!,$repo:String!,$pr:Int!){repository(owner:$owner,name:$repo){pullRequest(number:$pr){
  reviewThreads(first:100){nodes{id isResolved isOutdated path line originalLine
    comments(first:50){nodes{databaseId author{login} body createdAt url}}}}
  reviews(first:50){nodes{author{login} state body submittedAt url}}
  comments(first:100){nodes{author{login} body createdAt url}}}}}'
```

A finding is one of: an unresolved review thread (the whole thread, replies included, because a later reply often narrows or withdraws the ask), a point made in a review summary body, or a PR conversation comment that asks for a change. Skip resolved threads, approvals with no content, CI/coverage status bots, and comments the PR author already answered with a fix or a reasoned no that the reviewer accepted. Review bots count as reviewers. If the user also points at a `/review` in this conversation, include its findings, labelled as such.

Number the findings in the order a reader would fix them, and keep that numbering for the rest of the conversation, including step 2.

### Check each one

Read the rules first: every `CLAUDE.md` / `AGENTS.md` from the repo root down to the touched paths, a `REVIEW.md` at the root if one exists, and the `/review` skill's `repos/<name>.md` for this remote if it exists (`basename -s .git "$(git remote get-url origin)"`). Read the ticket the PR names.

Check each finding against the head of the branch, not against the diff the reviewer saw. The code may have moved, and an outdated thread may be fixed already or may still apply at a new line. Try to prove the reviewer right and try to prove them wrong: read the code path end to end, the callers, the tests, and the sibling code that solves the same problem. Run the relevant test or a small reproduction when it is cheap. Do not take the reviewer's word for it, and do not wave it off because the code looks deliberate.

Give each finding exactly one verdict:
- **Confirmed**: the problem is real as described.
- **Partly**: real, but the scope, trigger, or proposed fix is wrong. Say which part holds.
- **Already fixed**: a later commit on the branch handles it. Name the commit.
- **Not an issue**: the reviewer misread the code, or the input they describe cannot happen. Give the evidence, not an opinion.
- **Product call**: whether to change it depends on what the product should do, not on whether the code is right. This covers carrier- or customer-facing behavior and copy, defaults, what the AI agent says or does, data retention, rollout, and scope. Technical choices (naming, structure, where code lives, which helper to use, how to test it) are never a product call; decide those yourself in step 2.

Rank severity by what breaks in production and for whom, not by how loud the comment is: Blocking, Should fix, Polish. A nit stays Polish even when a senior reviewer wrote it. If a verdict rests on something inferred rather than run or read, say what it changes to if the inference is wrong.

### Report

Open with one line: `<n> findings: <n> confirmed · <n> partly · <n> product calls · <n> not an issue · <n> already fixed`. Then per finding:

```
<n>. [<verdict> · <severity>] <reviewer> on <path>:<line>
   Said: <the ask in one plain sentence>
   Found: <what the code actually does, with the evidence: file:line read, test run, commit>
   Plan: <the smallest fix, or "no change: <why>", or "your call: <the question>">
```

End with **Not verified** (anything inferred rather than checked, tests not run) and the product questions step 2 will ask, then go on to step 2.

## Step 2: address

### Decisions first

Before touching code, put every product call from step 1, and any new one found while planning, to the user in one round. Use the client's structured question tool when it has one (Claude Code: `AskUserQuestion`, up to 4 questions per call, 2 to 4 options each, the recommended option first and marked "(Recommended)", each option saying what the carrier, customer, or rep would experience). With no such tool, ask in chat as a numbered list, each question with lettered options and a recommendation, then stop and wait for the answers. Never ask about technical choices; make them and note the rationale in the report. If there are no product calls, say so in one line and continue.

### Where to work

Never switch or edit the main checkout's branch. Reuse a worktree already on the PR branch (`git worktree list --porcelain`), else create one with `git fetch origin && git worktree add <path> <headRefName>` (on this machine: `~/dev/.claude/worktrees/<branch-slug>`) and follow the repo's worktree setup (backend: `poetry run poe worktree-bootstrap`; never `poetry install` or `docker compose` from a worktree). If the branch has uncommitted changes or is behind its remote, stop and report; never `git stash`, never reset.

### Fix

Work the Confirmed and Partly findings, plus the product calls the user said yes to, in step 1's order. For each one:
- Make the smallest change that fixes the confirmed problem. No drive-by refactors, no fixes for findings that were ruled Not an issue, and no behavior change nobody asked for. If the fix turns out to need a product decision after all, stop that finding and ask.
- Follow the repo's rules for the kind of change: tests for changed behavior, eval scenarios for prompt or agent behavior changes, regenerated generated files, migrations per the repo's workflow. A finding about agent or prompt behaviour (a missing scenario, a reviewer citing an eval red, a prompt rule the reviewer wants changed) is fixed under the "Measure" section of `~/.agents/skills/fix/evals.md`: the scenario first, several trials, the should-hold siblings beside it, and an A/B against the base prompt when a sibling moves; a single green trial is not a fix.
- Never weaken a test, eval, lint rule, baseline, or type check to make a finding go away.
- Run the tests for the touched modules. A red test is fixed or reported, never skipped.
- Commit per finding, or per group of findings that touch the same lines, with the repo's commit convention; the message says what changed, not "address review". Stage specific files only.

Write a short reply for every finding, in plain professional English whatever the chat voice is: fixed findings name the commit and what changed in one sentence; Not an issue and declined product calls give the reason in one or two sentences, without arguing.

### Push, post, report

Push the branch from the worktree first (`git push origin <headRefName>`, never force, never a different branch), so every commit a reply names is on GitHub, then post each reply to its own thread (`gh api repos/<owner>/<repo>/pulls/<number>/comments/<databaseId>/replies -f body=...` for inline threads, `gh pr comment` otherwise). No go is needed for either. Hold both, and ask with the structured question tool instead, only when pushing would put broken work in front of reviewers: a touched test is still red, a fix was left half-done, or the branch moved on the remote since step 1.

```
#<pr> — <n> fixed · <n> declined · <n> left for you
<n>. <fixed in <sha> | declined | open>: <one line>
tests: <what ran and the result>
decisions I made: <technical calls and why, one line each>
not done: <anything skipped or blocked, and why>
pushed: <branch> at <head> (<n> commits)
replies: <the reply URLs>
next: /verify-fixes <pr> in a fresh conversation
```

Resolve threads, re-request review, or edit the PR body only when the user asks for that too.
