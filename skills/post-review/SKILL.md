---
name: post-review
description: Turn the most recent /review into a GitHub PR review (one summary comment plus inline comments), show the full draft first, and post only after the user says so.
---

Target: the PR number or URL in $ARGUMENTS, else the PR the most recent /review in this conversation was run on. This needs a completed /review in the conversation; if there is none, say so and stop (do not run one silently).

Everything drafted here is read by another engineer on GitHub, so it is plain professional English regardless of any chat voice. Short, direct, no jargon, no severity theater, no "I verified by reading X", no restating the diff back to the author.

## Step 1: draft, then stop

Build the review from the findings and show the whole draft in the chat, in exactly the shape it will be posted, then stop and wait. Do not post until the user replies with "post" (or edits and then says post). Any other reply is an edit request: apply it and show the draft again.

**Summary comment** (the review body), 3 to 6 sentences:
- Line one: the call. "Ready once the two items below are fixed." / "Needs a rethink on X before the rest is worth polishing." / "Looks good, two nits."
- The one thing that matters most and what it does to a user, carrier, customer, or on-call engineer.
- If the change drifted from the ticket, say what was asked vs what shipped in one sentence.
- Close with what to do first. If there are polish items, one sentence: "Nits inline: a, b, c."
No headers, no tables, no severity counts.

**Inline comments**, one per Blocking and Should fix finding, anchored on the exact line from the review:
- 1 to 3 sentences. Start with what breaks and for whom, then the fix in a few words. A suggestion block only when the fix is a self-contained few-line change.
- Prefix with `Blocking:` or `Should fix:` and nothing else. Polish items become one-line `Nit:` comments only when they have a line and are worth the author's click; otherwise they live in the summary sentence.
- Merge findings that point at the same line into one comment.
- Anchor lines must be in the PR diff. Check with `gh pr diff <pr>`; if the review's line is not in the diff, move the comment to the nearest changed line in that file or fold it into the summary. Never post a comment on an unchanged line.

Show the draft as:

```
Summary
<body>

Inline
<path>:<line>  <comment>
...
```

## Step 2: post (only after "post")

Post as ONE pull-request review so the summary and inline comments arrive together, using `gh api` with a JSON file:

```
gh api repos/{owner}/{repo}/pulls/{number}/reviews --input review.json
```

with `{"event":"COMMENT","body":"<summary>","comments":[{"path":"...","line":N,"side":"RIGHT","body":"..."}]}`. Write the JSON to the scratchpad, never inline on the command line. Use `event: COMMENT`, never APPROVE or REQUEST_CHANGES; the human decides that. If GitHub rejects a comment line (422), drop that comment into the summary as a bullet and retry once. Then print the review URL from the response. It posts under the user's own GitHub account.
