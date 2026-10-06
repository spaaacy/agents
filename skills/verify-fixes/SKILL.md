---
name: verify-fixes
description: Fresh-eyes check that the fixes made after a PR review actually fix the findings. Run it in a new conversation after /address-review or any fix round. It re-reads the review threads, finds the fix commits, checks every finding against the code as it is now, runs the tests, and reports what is fixed, what is not, and what the fixes changed that nobody asked for. Read-only on the code. It then drafts a reply for every finding - closing out the ones the fixes really fixed, contesting the ones they did not - plus a PR review that either closes the review or explains what remains open, and sends nothing until the user says post. Use when the user asks to verify, check, double-check, or re-review the fixes for review comments.
---

Target: $ARGUMENTS. A PR number or URL is the PR; `since <sha>` names the last commit the reviewers saw, so everything after it is a fix; pasted text (an /address-review report, a list of commits) is the claim under test. Nothing means the open PR for the current branch (`gh pr view --json number`); if there is none, say so and stop. The repo is the one in the current directory.

This is the second pair of eyes on a fix round, meant to run in a fresh conversation. Nothing said earlier in this conversation and nothing in a pasted report is evidence: the report is the claim being tested, and the agent that wrote the fixes is the one most likely to believe they work. Evidence is GitHub, git, the code at the fix head, and what you run. Read-only on the code: no edits, no commits, no pushes, no resolved threads, no `git stash`, no reset, no new worktrees. The only thing it ever sends anywhere is the thread replies and the review it drafts at the end, and only after the user says post.

Flow: `/address-review` made the fixes this checks. The verdict routes the next step: `Ready to push` closes the review on GitHub (below); `Back to /address-review` sends the open findings to another fix round, and after that round this skill runs again; from round 3 on, or when the loop is going in circles, `/close-review <pr>` replaces this skill and ends the review. CI is not this skill's: `/watch <pr>` keeps the checks green, and a red `Eval sample` is `/fix-evals <pr>`.

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

A finding is one of: a review thread, resolved or not (resolving a thread is part of the claim, not evidence), a point made in a review summary body, or a PR conversation comment that asks for a change. Read the whole thread: a later reply often narrows the ask, and the last reply may be the reviewer withdrawing it. Skip approvals with no content and CI/coverage status bots. Review bots count as reviewers. Note per thread whether it already carries the fix round's own answer and whether an independent verification close-out has already been posted for the same head. The author's fix explanation is a claim to verify, not a substitute for the verification reply. Number the findings in the order a reader would fix them; if an /address-review report was pasted, keep its numbering so the two reports line up.

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
- A fix to a prompt, a tool docstring, a tool result, or an agent's state assembly needs an eval scenario in the fix commits, both polarities where relevant, and the adapter update if state assembly changed. Run the scenario when the target is cheap (`poe evals run --scenario <id> --target <t> --trials 6 --max-judge-calls 0 --store-root <scratch>`), the way `/fix-evals` measures: one trial proves nothing, and a scenario the fix touched is checked beside its should-hold siblings; otherwise say it was not run.
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

## Report

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

## Replies and next steps

Every finding ends with a reply on its own thread, because the thread is where the next reader looks: the ones the fixes really fixed get closed out, the ones they did not get contested. A clean verification still needs closing comments on the fixes and a summary explicitly closing the review. Draft the replies and summary, show the full draft, and send nothing until the user says post. Closing the review means stating the verification outcome in comments, not resolving threads or submitting an approval. Never resolve a thread — that is the reviewer's call — never edit the PR body, never push.

**Close-out replies** — Fixed and Declined-holds. One or two sentences in the finding's own thread, prefixed `Fixed:` with the commit, what was independently checked, and the result, or `Declined:` with the reason that survives the code. Post a verification close-out even when the author or fixing agent already explained the fix: their explanation does not confirm that the review finding was independently checked and is now closed. Avoid duplicating an equivalent verification close-out already posted for the same head; include its link in the draft instead. Withdrawn findings need no additional thread reply — the reviewer closed them.

**Contest replies** — Not fixed, Partly fixed, Unaddressed, Declined-wrong. One to three sentences in the finding's own thread, prefixed `Not fixed:` / `Partly fixed:` / `Still open:` / `Declined, but:`, saying what still happens and where. Keeping it in the thread keeps the ask in one place for the next round. These are drafted even when the thread already claims the finding is fixed — that claim is the thing being corrected, so say so plainly and without arguing. A finding with no thread (a review-body point, a conversation comment) becomes an inline comment or a summary sentence instead.

Then split on the verdict, and be explicit either way: the reader should never have to ask "so what now".

**Nothing left** (`Ready to push`): draft the close-out replies and a closing summary review, even when no new issues were found and every fix already has an author reply. Put `Ready to push` on one line above the draft, then ask the post question below. The summary names the verified head, confirms that the reviewed findings are addressed and no blocking findings remain, briefly states validation and material limits, and explicitly says "Review closed." Do not imply that pending CI passed or unrun tests ran. No new inline findings or next-fix instructions are needed. If equivalent verification replies already exist for this head, link them and still draft the closing summary; only an already-posted equivalent closing summary for the same head removes the need to post it again.

**Something left** (`Back to /address-review` or `Rethink`): the next fix round will be another fresh agent, and /address-review reads findings from the PR's review threads, not from this conversation, so what you found has to be on GitHub before it can be fixed. Lay the steps out in order, then the draft:

```
Next
1. post the replies and review below (say "post")
2. /address-review <pr> fix in a fresh conversation, for #<n>, #<m> and the new findings
3. /verify-fixes <pr> again after that round (from round 3 on: /close-review <pr> instead)
```

The summary and any new findings ride along, in the shape /post-review uses, in plain professional English whatever the chat voice, short and without restating the diff. The summary is read by the PR author and the reviewers, who may not know this check ran, so it says in one sentence that this was a check of the fix round.

- **Summary comment**, 2 to 5 sentences, for every completed verification: on a clean round, use the closing summary above; otherwise state what the fix round got right, what remains open, what the fixes introduced, and what to do first. No headers, no tables.
- **New findings** above Polish: one inline comment each, prefixed `Blocking:` or `Should fix:`, anchored on a line that is in `gh pr diff <pr>`; a line outside the diff moves to the nearest changed line in that file or into the summary. Polish becomes a one-line `Nit:` only when it has a line and is worth the click; otherwise one summary sentence. Merge findings on the same line.

Show the whole draft as (Summary for both clean and open rounds; Inline only when there are new findings):

```
Summary
<body>

Thread replies
<path>:<line> (thread <databaseId>)  [Fixed: | Not fixed: | …] <reply>

Inline
<path>:<line>  <comment>
```

Then ask with the client's structured question tool (Claude Code: `AskUserQuestion`; otherwise a numbered list in chat with lettered options): post it as drafted (Recommended), or edit first. An edit answer names the change: apply it, show the draft again, and ask again. Nothing is posted without that answer. On post: each thread reply goes to its own thread, `gh api repos/{owner}/{repo}/pulls/{number}/comments/{databaseId}/replies -f body=...`; the summary and inline comments go out as ONE review, `gh api repos/{owner}/{repo}/pulls/{number}/reviews --input review.json` with `{"event":"COMMENT","body":"<summary>","comments":[{"path":"...","line":N,"side":"RIGHT","body":"..."}]}` (the JSON written to the scratchpad, never inline on the command line; `COMMENT` only, never APPROVE or REQUEST_CHANGES). A clean round posts its closing summary as a `COMMENT` review with `"comments": []`, plus the close-out thread replies; it never submits `APPROVE` or resolves threads. A 422 on an inline comment moves it into the summary as a bullet, retried once. Print the review URL, or the reply URLs when there is no review.

Whatever the verdict: change no code and do not start fixing what you found; that is the next /address-review round's job.
