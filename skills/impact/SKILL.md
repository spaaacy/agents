---
name: impact
description: Show what a pull request (or every open PR for a Linear ticket) delivers from the product side, as one page - what changes for carriers, customers, reps and admins, the defaults and switches, who is left out, what it takes to ship, real screenshots of every screen or email it changes (before and after), and a diagram of its new tables and columns - with no code or jargon in the words. Read-only on the repos. `text` gives the short chat summary only; `schema` only the database diagram. Run again on the same PR, it reports only what changed since the last run. Use when the user asks what a PR delivers, for a product summary or TL;DR of a PR, what a change means for users, what it looks like, or to see a PR's schema or database changes.
---

Target: $ARGUMENTS. A PR number or URL is that PR; a Linear key (`ENG-1234`) or URL means every open PR for that ticket across the repos. A bare number is looked up in the current repo first, then `envoyai-web`, `envoyai-chrome-plugin`, `envoyai-chat-ui` (backend PRs are in the 2000s, the others are lower; never guess across repos, ask if two match). Nothing means the PR or ticket this conversation is about (`Shared skill arguments` in `~/.agents/AGENTS.md`), else the open PR for the current branch; if there is none, ask which PR with the client's structured question tool and stop.

Modes, from a trailing word or the user's ask:
- default: the page (below), plus a short summary in chat.
- `text` (or `brief`, `quick`): the chat summary only, no page and no screenshots.
- `schema` (or `db`, `tables`, or asking to see the schema or database changes): the page with only the header and the database section.

Flow: standalone, and read-only on the code, GitHub, Linear and Slack. Its only writes are temporary checkouts for screenshots, removed at the end, and the page it publishes (private to the user until they share it). Posting the result anywhere is a separate ask, and anything posted is plain professional English whatever the chat voice.

## Read

- `gh pr view <pr> -R envoy-ai/<repo> --json number,title,body,state,isDraft,baseRefName,headRefName,headRefOid,baseRefOid,mergedAt,url`, plus `gh pr diff <pr>` and the changed file list.
- The ticket(s) the PR names, through the Linear tool when one is available: the ask, and any product decisions in the comments.
- The ticket's other PRs (`gh pr list -R envoy-ai/<repo> --search "<KEY>" --state all` in the web, extension and chat-ui repos, plus any the body names). A web, extension or chat-ui sibling is where most of what a person sees lives, so its screens belong on the page too.
- When the PR folds in other tickets' work, each of those is part of what it delivers.

The PR body can be stale after review rounds. Where the body and the code disagree, the code wins; where a behaviour is unclear from both, say so instead of guessing.

## What changes (the words)

Write these first; the page's top section and the chat summary are both made of them.

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

## What it looks like (real screenshots)

Whenever the PR, or a sibling PR for the same ticket, changes something a person sees, the page shows the actual screen, rendered by the PR's own code. Never draw a mockup or recreate a screen in HTML and present it as the UI; a screen that could not be captured is listed under "Not shown", with why.

Pick the screens from the diff and the ticket: every page, panel, dialog, setting, card or state the change adds or alters (a new toggle, the empty state, the error, the state after a click), each captured in the state that shows the change. One screenshot per change, scoped to the part that changed (the panel or dialog, not the whole window) unless the layout itself is the change. When the screen existed before, capture it at the PR's base too, so the page shows before and after side by side.

Capture each one the way the Capture section of `~/.agents/skills/screenshots/SKILL.md` says (web, chat-ui and extension; throwaway checkouts of the PR's head and base, never a mockup), saving the PNGs to the scratchpad. When the ticket already has a `Screenshots: <repo>#<pr> at <sha>` comment from that skill for the PR's current head, its images can be used instead of capturing again (download each with `curl -L` and embed it). Two kinds that skill does not cover:

- Extension screens with no web counterpart: use the screenshots in the PR body or comments (download each with `curl -L -H "Authorization: token $(gh auth token)" <url>`, since the page cannot load GitHub's images), else list it under "Not shown".
- Emails and messages the backend sends (an award email, a carrier-facing text): render them with the PR's own code and example data (the function the code calls, fed the way its tests feed it, run in a throwaway backend checkout with `poetry run python`), then screenshot the HTML with Playwright (`page.setContent(html)`). A plain-text message is quoted on the page as text, exactly.

Label every screenshot with where it is ("Load Details › Offers, a held offer") and mark it "example data".

## The database (when the PR has a migration)

Only backend PRs carry migrations; for a ticket, draw each of its backend PRs. No migration: the page's database section says "No database changes" in one line.

- The migration: the files the PR adds or changes under `alembic/versions/`, the `upgrade()` function only.
- Per table it touches: new (`op.create_table`), dropped (`op.drop_table`), or existing with columns added (`op.add_column`) or removed (`op.drop_column`). Per column: name, type, required or optional (`nullable`), its server default, and what it points at (`sa.ForeignKey`).
- The model class for each table at the PR head (the SQLModel `table=True` class whose `__tablename__` matches): the existing columns around the new ones, and what each new column holds, from its `Field(description=...)`, its comment, the code that writes it, the PR body or the backend `CLAUDE.md`. A JSON column's settings model gives the keys it holds, their values and defaults. Say it in a few plain words; mark a meaning you guessed "(inferred)".
- A backfill script the PR adds under `scripts/database/` that fills a new column for old rows: name it on that column, since until it runs old rows read the server default or nothing.
- Leave out indexes, constraints, renames, type changes and enum values, except one closing line counting them ("also: 2 indexes, 1 enum value"). Something the migration deliberately leaves for a later release (a column kept so older servers keep working) gets one line too.

## The page

In Claude Code, publish one Artifact page per run (load the `artifact-design` skill first, as the Artifact tool requires), written to the scratchpad as `impact-<repo>-<pr>.html` (`impact-<KEY>.html` for a ticket) so running this again in the same conversation updates the same page. Screenshots go in as `data:` PNG URIs (keep the page under the 16MB limit: scope screenshots to what changed). Sections, top to bottom:

- Header: `#<n> <title>` linked to the PR (every PR for a ticket), open, draft or merged, the head commit it was written at (short sha), the ticket, and the one-sentence summary.
- Since the last version (a run again only, below): the differences from the earlier run, as a short box under the header.
- What changes: the bullets above, grouped by who notices, then Defaults, Left out and Shipping as their own short lines.
- What it looks like: each change's screenshot, before and after side by side when the screen existed (stacked on a phone), with its label and "example data"; emails and messages as rendered. Left out when nothing visible changes.
- The database: the header counts ("2 new tables · 5 new columns · 1 column removed") and the migration revision, then one card per table, new tables first, then existing tables gaining columns, then anything dropped. A new table's card lists every column; an existing table's shows its id, the new columns, and the columns they sit beside or point at, with the rest folded behind "N other existing columns". Each column on one row: name, type, required or optional, default, the table it points at ("→ load.id"), and what it holds underneath; new columns stand out (an added marker and colour), removed ones are struck through, existing ones are muted; a JSON column lists the keys it holds. Then the new links between tables ("offer.accepted_by_user_id → user.id") and the "also:" line.
- Not shown: screens that could not be captured and why, and anything inferred rather than read.

In chat, alongside the link: the one-sentence summary and the bullets, cut to the five that matter most, and one line on what the page shows ("before/after of the Offers panel and the award email, 2 new columns").

## Run again on the same PR

When this conversation already has an impact for the same PR or ticket (an earlier run's chat summary, or its page in the scratchpad), the run reports only what changed since that run, never the whole thing again:

- Compare the new words against what the earlier run said (its bullets, examples, messages, database section, Defaults, Left out and Shipping), not against the code, and name both head commits.
- Chat: the first line names the PR and the two heads (`#3191 since the last /impact (7810fbf → beb8f69)`), then one bullet per difference, starting with what happened to it: Added, Dropped (with why, e.g. "now matches production"), or Changed (old → new in a few words). Defaults, Data changes and Shipping appear only when they changed. When nothing changed for people, say so in one line. Unchanged bullets are never restated.
- The page: still the full current picture, updated in place at the same URL (it is the reference people share), with the same differences in a "Since the last version" box under the header, naming both heads.
- `text` mode follows the same rule. A conversation with no earlier run for this PR or ticket is a first run.

Without an Artifact tool (Codex, or when publishing is refused): the full bullets in chat, the database as a text diagram in one code block (a box per table, as below), and the screenshots saved under `~/.agents/state/impact/<repo>-<pr>/` with their paths listed, one line each with its label.

```
offer  (existing table, +2 columns)
  + held_at               timestamptz       optional   when the offer was held for the team to decide
  + accepted_by_user_id   uuid → user.id    optional   who accepted it
    id, load_id, carrier_id, status … 30 other existing columns
```
