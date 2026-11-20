---
name: db-diff
description: Show what a branch, pull request, ticket or plan changes in the database against development, and why - every new or dropped table and column, what each holds, what writes and reads it, whether it could have been worked out from data already stored or kept somewhere that already exists, and whether Aakif approved it - as a short report. `approve` asks him to sign off on what is not approved. With no target it covers everything in flight (the backend main checkout, every backend worktree, his open backend PRs). Read-only on code, GitHub and Linear. Use when the user asks what database or schema changes a branch, PR or ticket makes or would make, for the DB diff against development, or why a table or column is being added.
---

Target: $ARGUMENTS. A backend PR number or URL; a Linear key (its backend PRs and the worktrees on its branches); a branch name; a worktree path; `plan` (the data changes a plan in this conversation proposes, such as `/pickup` step 1's, before any code exists); nothing means everything in flight: `~/dev/envoyai-backend`, every worktree in `git -C ~/dev/envoyai-backend worktree list`, and Aakif's open backend PRs (`gh pr list -R envoy-ai/envoyai-backend --author @me --state open`). A trailing `page` publishes the report as a page too; a trailing `approve` (or him saying approve after a report) asks for sign-off on what is not approved.

Flow: read-only. It never writes code, never edits an approval (`~/.agents/state/schema-approvals/` is written only by `~/.agents/hooks/schema-guard.py`, from Aakif's own answer), and it asks nothing unless he says approve. The rule it serves is in `Working style` in `~/.agents/AGENTS.md`: a new or dropped table or column is his call, the same as a product change.

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

Short. The research above is for getting the verdicts right, not for printing: no file paths, no line numbers, no ticket recap.

First line: e.g. "3 branches change the database: 13 changes, none approved" or "no database changes in flight".

Then per branch, most unapproved first:

- One header line: branch, PR, approved or not.
- The diagram in one code block, in the format of `diagram.md` in this folder, `branch:` line first.
- One bullet per changed table or column, under about 15 words: what it's for, then the verdict word (`needed`, `derivable: <how>`, `belongs in <place>`, or `cosmetic`). A new table is one bullet; a column inside it gets its own bullet only when it looks derivable. Two branches with the same changes share one block ("same as above").

Nothing else: no "not checked" line unless something that matters couldn't be read, and no question or offer at the end.

With `page`: also publish the report as an Artifact page (load the `artifact-design` skill first), one card per table in the style of `/impact`'s database section (`~/.agents/skills/impact/SKILL.md`), plus each change's why, written to the scratchpad as `db-diff.html` so a rerun updates the same page.

## Approve (only when asked)

Only with a trailing `approve`, or when Aakif says approve after a report. Ask in one call of the client's structured question tool (Claude Code: `AskUserQuestion`), one question per branch (up to the tool's limit; the rest are asked next):

- header `Schema` (the hook only records answers to questions with this header);
- the question names the branch and says in one short sentence what the changes are for;
- an option labelled `Approve`, its preview the branch's diagram with only the changes being asked about (the hook records exactly what that diagram lists, for its `branch:`); a second `Approve, ...` option with a smaller diagram when one change is worth leaving out;
- an option that does not add them (`Don't add`).

Recommend `Don't add` first when a change is derivable or belongs in an existing place; otherwise `Approve` first. Repeat the line the hook prints after the answer. In a client with no structured question tool, ask in chat: he approves by replying "approve schema", which approves what the hook last blocked, so ask that way only for a branch the hook just stopped.
