---
name: digest
description: Rewrite the most recent /review output (or the text passed in) as a short plain-English digest, grouped and ranked by severity, one sentence per finding, no file or code references.
---

Input: $ARGUMENTS if given, otherwise the most recent review output in this conversation. If there is neither, say so in one line and stop.

Write for someone who has not read the code and will not open it. Output exactly this shape and nothing else:

Verdict: one line. Ready / Fix first / Rethink, then why in plain words.

Blocking
- [type] one sentence
Should fix
- [type] one sentence
Polish
- [type] one sentence

Not checked: one sentence.

Rules:
- Groups are the review's own severities; keep every Blocking and Should fix item, merge Polish items where they overlap, skip empty groups. Rank inside a group by how bad it would be in production.
- Unrequested behavior changes from the review's ledger are findings too: label them [behavior] and place them by severity.
- Types: behavior, product gap, data, security, reliability, tests, cleanup, docs. One per line.
- One sentence per finding, aim for under 20 words, describing what a user, carrier, customer, or on-call engineer would experience, and the fix in a few words if it is obvious.
- No file paths, function or class names, line numbers, identifiers, or code. Say "the webhook receiver", "the status mapping", not the symbol.
- No jargon; spell out acronyms the first time. No hedging words the review did not use.
- Do not add findings, change severities, or soften anything. Numbers and counts stay as the review stated them.
