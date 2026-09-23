---
name: close-review
description: The last look at a fix round, meant to end the review instead of starting another one. Checks that the previous round's findings are fixed the way /verify-fixes does, then raises a new finding only when it would break production on a normal path, and declares the review closed otherwise. Use when the user wants to close, wrap up, or finish a PR review after a fix round, or says the review is going in circles.
---

Target: $ARGUMENTS, read exactly as /verify-fixes reads it: a PR number or URL, `since <sha>` for the last commit the reviewers saw, pasted text as the claim under test; nothing means the open PR for the current branch. The repo is the one in the current directory.

Why this exists: every fresh look at a branch finds something, so a loop of review, fix, verify never ends on its own. This skill ends it. It runs the same check as /verify-fixes on what the previous round asked for, then holds anything new to a bar that only a real production bug clears. The output is a decision to close, or one last short list. It is never a fresh review, and finding nothing new is the expected outcome, not a failure of effort.

Read-only on the code, like /verify-fixes: no edits, no commits, no pushes, no resolved threads, no stash, no reset. The only thing it sends anywhere is the review it may draft at the end, after the user says post.

## 1. Check the previous round

Read `~/.agents/skills/verify-fixes/SKILL.md` and follow its sections 1 to 3 as written: gather the PR and its threads, find the code under test and the fix commits, map commits to findings, check each finding against the code at head, run the tests. Same verdicts (Fixed / Partly fixed / Not fixed / Unaddressed / Declined-holds / Declined-wrong / Withdrawn), same evidence rules. Its section 4 (what the fixes did besides fixing) is replaced by the bar below.

Count the rounds: round 1 is the first review; every batch of author commits that followed reviewer comments is another. Say it in the report, and read any earlier closing pass on the PR (a review comment that says it was the closing pass) before deciding anything: what it left out on purpose is settled unless the code around it changed since.

## 2. The bar for anything new

Read the diff of the fix commits, and raise a new finding only when every one of these holds:

1. **In scope.** It is in code the fix commits touched, or a fix made it reachable. The rest of the PR was reviewed already, and pre-existing code was never in scope.
2. **Blocking, by the /review definition.** Wrong behavior a user, carrier, or customer hits on a normal path; data loss or corruption; a security or tenant-scoping hole; a migration that cannot apply; a check the fix round weakened (a deleted or loosened test, a skip or xfail, a baseline or goldens edit); or an unrequested behavior change the fixes introduced.
3. **Real input.** It fires on input the system actually produces. An edge case that needs a rare precondition, a malformed input nothing sends, or two failures at once is not raised, whatever its severity would be on paper. It becomes one line under "seen, not raised" with the precondition named, so the reader can pull it in if they disagree.
4. **Not already settled.** A thread where a reviewer or the author said no with a reason is settled, and so is a product call the user made and anything an earlier closing pass left out. Re-raising a settled point in different words is exactly the circle this skill exists to break.
5. **Confirmed against the code**, not the diff: you can name the input, the path, and what breaks, from reading the callers or running it.

Everything else (Should fix, Polish, style, footprint, duplication, docs currency, a test that could be tighter, "while you're here") is not raised. Count it in one line by category and say it was left out on purpose. Do not lower the bar because the list came out empty.

From round 3 on, a raised finding must also carry one sentence a human reviewer would use to block the merge with it. If that sentence cannot be written, the finding is not raised.

## Report

Open with one line: `#<pr> round <k>: close` or `#<pr> round <k>: one more round, <n> open · <n> raised`.

Per previous finding, the /verify-fixes line: verdict, the fix sha and what it changed, what was checked, what is left (only when something is).

**Raised** — each new finding that cleared the bar: `path:line`, the input, what breaks and for whom, the smallest fix, and from round 3 the blocking sentence. "None" is a line.

**Seen, not raised** — one line per category with its count, then one line per edge case: `edge: <path:line> <what> — needs <precondition>`.

**Tests** and **Not verified** as in /verify-fixes.

## Next steps

**Close**: every previous finding is Fixed, Declined-holds, or Withdrawn, and nothing was raised. Say `Review closed: push and request approval` in one line and stop. No draft, no list, no offers, no "you might also". If the fix commits are unpushed, say so in that same line; pushing is the user's action.

**One more round**: something is still open or something was raised. Lay out the steps and draft the review exactly as the /verify-fixes next-steps section does: thread replies for still-open findings, inline comments for raised ones, one summary, posted as one COMMENT review only after the user says post. Two additions: the summary comment says in one sentence that this was the closing pass and the listed items are the last ones, and it ends with one sentence naming what was seen and left out on purpose, so the next fresh agent treats those as settled. Then stop.

Whatever the outcome, change no code and do not start fixing what you found.
