---
name: db-diff
description: Show what a branch, pull request, ticket or plan changes in the database against development, and why - every new or dropped table and column, what each holds, what writes and reads it, whether it could have been worked out from data already stored or kept somewhere that already exists, and whether Aakif approved it - then ask him to approve the ones he has not. With no target it covers everything in flight (the backend main checkout, every backend worktree, his open backend PRs). Read-only on code, GitHub and Linear. Use when the user asks what database or schema changes a branch, PR or ticket makes or would make, for the DB diff against development, or why a table or column is being added.
---

Target: $ARGUMENTS. A backend PR number or URL; a Linear key (its backend PRs and the worktrees on its branches); a branch name; a worktree path; `plan` (the data changes a plan in this conversation proposes, such as `/pickup` step 1's, before any code exists); nothing means everything in flight: `~/dev/envoyai-backend`, every worktree in `git -C ~/dev/envoyai-backend worktree list`, and Aakif's open backend PRs (`gh pr list -R envoy-ai/envoyai-backend --author @me --state open`). A trailing `page` publishes the report as a page too.

Flow: read-only. It never writes code, never edits an approval (`~/.agents/state/schema-approvals/` is written only by `~/.agents/hooks/schema-guard.py`, from Aakif's own answer), and its only question is the approval at the end. The rule it serves is in `Working style` in `~/.agents/AGENTS.md`: a new or dropped table or column is his call, the same as a product change.

Only the backend has a database; the other repos are left out.

## Find the changes

`git -C ~/dev/envoyai-backend fetch origin development` once, first, so "against development" means the current development.

- A checked-out branch (main checkout or worktree): `python3 ~/.agents/hooks/schema-guard.py --list <dir>`. It prints the branch, its base (where it left development), every table and column the branch adds or drops in its migrations (committed, staged, changed and untracked files alike), what is approved and what is missing, using the same reader the hook blocks with, so the two never disagree. `--committed` limits it to what a push would carry.
- A PR or branch not checked out: `git -C ~/dev/envoyai-backend fetch origin <headRefName>`, list the migration files it adds or changes (`git diff --name-only $(git merge-base origin/development FETCH_HEAD) FETCH_HEAD -- alembic/versions`), and read each through the same reader: `git -C ~/dev/envoyai-backend show FETCH_HEAD:<path> | python3 ~/.agents/hooks/schema-guard.py --parse`. Its approvals: `cat ~/.agents/state/schema-approvals/envoyai-backend/<branch with / as __>.json` (missing file = nothing approved).
- What is coming but not written yet: a `table=True` model the branch adds, or a `Field` it adds to one, with no migration carrying it yet (the branch's diff of `envoy/` and `tms-ingestion/` against its base). List these as "in the model, no migration yet": they are the next migration's columns.
- `plan`: the Data changes lines of the plan in this conversation, marked "(planned)".

For each change, read the migration's `upgrade()` for the details the diagram needs: type, required or optional (`nullable`), server default, what it points at (`ForeignKey`), and for a new table every column. Read the model class at the branch head for the columns around a new one.

## Why, for each change

Find out, from the code and the record, never from the name alone:

- What it holds, in a few plain words: its `Field(description=...)`, its comment, the code that writes it.
- Who asked and why: the ticket the branch names, its comments, the PR body, and this conversation.
- What writes it and what reads it: `rg -n "\b<column>\b"` across `envoy/`, `tms-ingestion/`, `scripts/` and `alembic/` at the branch head. Name the file:line of each writer and reader. A column nothing reads yet says so.
- Could it have been avoided? Say which holds, with the evidence:
  - derivable: it can be worked out from data already stored (a sum or max over existing rows, a status or timestamp already recorded, a join, the `events` table while it lasts). Store only what cannot be derived.
  - belongs somewhere that exists: an org setting goes in its settings group (a JSON column, no migration), a person's choice about one load in `load_settings`, a fact about an offer on the offer row.
  - needed: what only a new column or table can hold, and why (a fact that would otherwise expire, a person's decision nothing else records, a one-to-many the existing rows can't carry).
- Old rows: what rows written before the deploy read until something fills them (the server default, or nothing), and whether a backfill script and its runbook ticket exist.
- A drop: what still reads it (`rg`), and whether code that stopped writing it is already live (a drop ships a release after the code, per the backend CLAUDE.md).
- Approved: yes with the date and how (the record's `approved_at`, `via`), or not approved.

Mark anything inferred rather than read "(inferred)".

## Report, in chat

First line: what is in flight, e.g. "3 branches change the database: 9 changes, 6 not approved" or "no database changes in flight".

Then per branch, most unapproved changes first:

- A header line: the branch, its PR and ticket when there are any, and how far it is behind development.
- The diagram in one code block, in the format of `diagram.md` in this folder, `branch:` line first, so it can go straight into an approval question.
- One bullet per change: `<change>: <what it holds>. why: <the reason, with the ticket>. writes <file:line>, reads <file:line>. <needed: ... | derivable: how | belongs in <place>>. <approved <date> | not approved>.`

End with one line of what was not checked. No length cap beyond one line per bullet: Aakif asked for every change.

With `page`: also publish the report as an Artifact page (load the `artifact-design` skill first), one card per table in the style of `/impact`'s database section (`~/.agents/skills/impact/SKILL.md`), plus each change's why, written to the scratchpad as `db-diff.html` so a rerun updates the same page.

## Ask for what is not approved

When this conversation is with Aakif and anything is not approved, ask in one call of the client's structured question tool (Claude Code: `AskUserQuestion`), one question per branch (up to the tool's limit; the rest are asked next):

- header `Schema` (the hook only records answers to questions with this header);
- the question names the branch and says, per change, why it is needed in one short clause, plus the avoidable ones' alternative;
- an option labelled `Approve`, its preview the branch's diagram from the report with only the changes being asked about (the hook records exactly what that diagram lists, for its `branch:`);
- an option that does not add them (`Don't add`, saying what happens instead: derive it, or keep it in the existing place).

Recommend `Don't add` first when a change is derivable or belongs in an existing place; otherwise `Approve` first. The hook confirms what it recorded right after the answer; repeat that line in the reply. In a client with no structured question tool, ask in chat: he approves by replying "approve schema", which approves what the hook last blocked, so ask that way only for a branch the hook just stopped.

With nobody to ask (a Slack bot, a background run), end with the report.
