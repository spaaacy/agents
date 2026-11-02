---
name: product-tldr
description: Explain what a pull request (or every open PR for a Linear ticket) delivers purely from the product perspective, as TL;DR bullet points - what changes for carriers, customers, reps and admins, the defaults and switches, who is left out, and what it takes to ship - with no code, file names or jargon. Read-only. Use when the user asks what a PR delivers, for a product summary or TL;DR of a PR, or what a change means for users.
---

Target: $ARGUMENTS. A PR number or URL is that PR; a Linear key (`ENG-1234`) or URL means every open PR for that ticket across the repos. A bare number is looked up in the current repo first, then `envoyai-web`, `envoyai-chrome-plugin`, `envoyai-chat-ui` (backend PRs are in the 2000s, the others are lower; never guess across repos, ask if two match). Nothing means the open PR for the current branch; if there is none, ask which PR with the client's structured question tool and stop.

Flow: standalone and read-only. It changes nothing in the code, on GitHub, in Linear or in Slack. `/digest` is the plain-English rewrite of a review; this is the plain-English account of what a change delivers. Posting the result anywhere is a separate ask, and anything posted is plain professional English whatever the chat voice.

## Read

- `gh pr view <pr> -R envoy-ai/<repo> --json number,title,body,state,isDraft,baseRefName,headRefName,mergedAt,url`, plus `gh pr diff <pr>` and the changed file list.
- The ticket(s) the PR names, through the Linear tool when one is available: the ask, and any product decisions in the comments.
- A sibling PR the body names (a web or extension PR for the same ticket) only for what it adds that a person sees, such as a settings toggle.
- When the PR folds in other tickets' work, each of those is part of what it delivers.

The PR body can be stale after review rounds. Where the body and the code disagree, the code wins; where a behaviour is unclear from both, say so in the output instead of guessing.

## Output

Exactly this shape, and nothing else:

```
<one sentence: what the PR delivers, in product words>

- <who notices>: <what changes for them>
- ...
- Defaults: <on or off by default, for which organizations, who can switch it and where>
- Left out: <who or what this deliberately does not cover>
- Shipping: <open/draft/merged; what must deploy first; anything that must merge before or after it>
```

Rules:

- Ten bullets at most. One sentence each, under about 25 words. Rank by how many people notice it, carriers and customers first.
- Write about people and what they experience: carriers, reps, admins, the broker, the team. Say what they see, receive, can do, or no longer get.
- Quote customer-facing copy exactly when the PR adds or changes it (an email line, a message, a button label). Keep numbers, timings and limits as the PR states them.
- No file paths, function or class names, table or column names, migrations, metrics, tests, PR internals, or code. Say "the escalation email", not the renderer.
- No jargon; spell out acronyms the first time. A change with no visible effect gets one bullet saying so in a few words (for example "notification emails that fail to send now retry"), or is left out if nobody would notice.
- A PR that carries several tickets gets one bullet per ticket's change, each starting with what it is, not the ticket key; the ticket keys go in the Shipping line.
- Never add benefits the PR does not deliver, and never soften a limit the PR states. Mark anything inferred rather than read with "(inferred)".
