---
name: impact
description: Explain what a pull request (or every open PR for a Linear ticket) delivers purely from the product perspective, as TL;DR bullet points - what changes for carriers, customers, reps and admins, the defaults and switches, who is left out, the new tables or columns, and what it takes to ship - with no code, file names or jargon. Read-only. A `schema` mode instead draws a backend PR's migration: its new tables and columns, as a diagram. Use when the user asks what a PR delivers, for a product summary or TL;DR of a PR, what a change means for users, or to see or visualize a PR's schema or database changes.
---

Target: $ARGUMENTS. A PR number or URL is that PR; a Linear key (`ENG-1234`) or URL means every open PR for that ticket across the repos. A bare number is looked up in the current repo first, then `envoyai-web`, `envoyai-chrome-plugin`, `envoyai-chat-ui` (backend PRs are in the 2000s, the others are lower; never guess across repos, ask if two match). Nothing means the PR or ticket this conversation is about (`Shared skill arguments` in `~/.agents/AGENTS.md`), else the open PR for the current branch; if there is none, ask which PR with the client's structured question tool and stop. A trailing `schema` (or `db`, `tables`, or the user asking to see or visualize the schema or database changes) runs schema mode, at the end, instead of the summary.

Flow: standalone and read-only. It changes nothing in the code, on GitHub, in Linear or in Slack. This is the plain-English account of what a change delivers. Posting the result anywhere is a separate ask, and anything posted is plain professional English whatever the chat voice.

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
- Data changes: <each new table or column: its name and what it holds; "none" when nothing new is stored>
- Shipping: <open/draft/merged; what must deploy first; anything that must merge before or after it>
```

Rules:

- Eleven bullets at most, the Data changes line included. One sentence each, under about 25 words. Rank by how many people notice it, carriers and customers first.
- Write about people and what they experience: carriers, reps, admins, the broker, the team. Say what they see, receive, can do, or no longer get.
- Quote customer-facing copy exactly when the PR adds or changes it (an email line, a message, a button label). Keep numbers, timings and limits as the PR states them.
- Data changes is the one place a table or column is named: every table and column the PR's migration adds (or removes), in backticks with a few words on what it holds, one bullet each when there are several. Indexes, constraints and type changes are left out, and so is a value stored in an existing settings record, which is not a new column. Read it from the migration in the diff, not the PR body.
- Elsewhere, no file paths, function or class names, table or column names, migrations, metrics, tests, PR internals, or code. Say "the escalation email", not the renderer.
- No jargon; spell out acronyms the first time. A change with no visible effect gets one bullet saying so in a few words (for example "notification emails that fail to send now retry"), or is left out if nobody would notice.
- A PR that carries several tickets gets one bullet per ticket's change, each starting with what it is, not the ticket key; the ticket keys go in the Shipping line.
- Never add benefits the PR does not deliver, and never soften a limit the PR states. Mark anything inferred rather than read with "(inferred)".

## Schema mode

A picture of what the PR's migration does to the database, built to answer "what new tables and columns does this add, and what goes in them". Same read-only rules. Only backend PRs carry migrations; for a ticket, draw each of its backend PRs.

### Read

- The migration: the files the PR adds or changes under `alembic/versions/`, the `upgrade()` function only (`gh pr diff <pr> -R envoy-ai/envoyai-backend`, or `git show origin/<head>:<path>` after a fetch). No migration: say "No migration in #<n>, so no tables or columns change." and stop.
- Per table it touches: new (`op.create_table`), dropped (`op.drop_table`), or existing with columns added (`op.add_column`) or removed (`op.drop_column`). Per column: name, type, required or optional (`nullable`), its server default, and what it points at (`sa.ForeignKey`).
- The model class for each table at the PR head (the SQLModel `table=True` class whose `__tablename__` matches, or the snake-cased class name): the existing columns around the new ones, and what each new column holds, from its `Field(description=...)`, its comment, the code that writes it, the PR body or the backend `CLAUDE.md`. Say it in a few plain words; mark a meaning you guessed "(inferred)".
- A backfill script the PR adds under `scripts/database/` that fills a new column for old rows: name it on that column ("old rows filled by `backfill_x.py` after the deploy"), since until it runs they read the server default or nothing.

Leave out indexes, constraints, renames, type changes and enum values, except one closing line counting them ("also: 2 indexes, 1 enum value"), so the picture stays about what is stored.

### Draw it

In Claude Code, publish it as an Artifact page (load the `artifact-design` skill first, as the Artifact tool requires), written to the scratchpad as `impact-schema-<repo>-<pr>.html` so running this again in the same conversation updates the same page:

- A header: `#<n> <title>`, open, draft or merged, the migration revision, and counts ("2 new tables · 5 new columns · 1 column removed").
- One card per table, new tables first, then existing tables gaining columns, then anything dropped. A new table's card is marked new and lists every column. An existing table's card shows `id`, the new columns, and the columns they sit beside or point at, with the rest collapsed behind "N other existing columns".
- Each column on one row: name, type, required or optional, default, and the table it points at ("→ load.id"), with what it holds underneath in small text. New columns stand out (an added marker and colour), removed ones are struck through, existing ones are muted.
- A short links list for every new pointer between tables ("offer.accepted_by_user_id → user.id"), and the "also:" line last.

In chat, alongside the link: the first line (what the migration adds, in one sentence), then one bullet per new or removed table or column with what it holds, the same as the summary's Data changes line.

Without an Artifact tool (Codex, or when publishing is refused), draw it in chat as a text diagram in one code block, a box per table:

```
offer  (existing table, +2 columns)
  + held_at               timestamptz       optional   when the offer was held for the team to decide
  + accepted_by_user_id   uuid → user.id    optional   who accepted it
    id, load_id, carrier_id, status … 30 other existing columns
```

