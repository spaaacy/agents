---
name: pickup
description: Pick up a Linear ticket or project in two steps. First gather the full context - the ticket and its whole comment thread, the PRs already up, the code as it stands, past work on it - and report, changing nothing. Then, only after the user says go, create a worktree per ticket, build it test-first, and open a draft PR. Use when the user points at a Linear ticket or project, or asks to pick up, start, or work on one.
---

Target: `$ARGUMENTS`. An issue key (`ENG-2130`), a project key (`P-ENG-69`), a project name, or a Linear URL. A trailing `go` / `work` / `start`, or a list of keys, means step 2. Nothing means ask which ticket and stop.

Two steps, always in order, each ending with a stop:

1. **Context** (the default): read everything, report, change nothing — no branch, no worktree, no edits, no Linear writes, no PR.
2. **Work**: runs only when the user says so after step 1 ("go", "start", or this skill again with keys). If step 1 has not run in this conversation, run it first and stop there.

Flow: this is the first skill in the PR lifecycle. It ends with a draft PR; the author then runs `/review <pr>` on it, `/watch <pr>` keeps it green while reviewers read it (an `Eval sample` red there is `/fix-evals <pr>`), their comments are worked with `/address-review <pr>` and checked with `/verify-fixes <pr>`, the user merges, `/deploy` ships it and `/linear-sweep` moves the ticket. The worktree this skill creates is the one every later skill reuses, and `/cleanup` removes it once the PR has shipped.

# Step 1: context

## The ticket

Linear MCP, in this order:

- **Issue**: `get_issue` (description, state, assignee, labels, project, cycle, parent, sub-issues, links, attachments), then `list_comments` — read the **whole** thread. A later comment routinely narrows, widens, or withdraws the original ask, and the description is often stale. Note relations: what blocks it, what it blocks, duplicates.
- **Project**: `get_project`, `list_issues` scoped to it, `list_milestones`, `list_documents` + `get_document`, `get_status_updates`. The issues are the work list — say which are done, which are in flight and with whom, which are open. A project is picked up one ticket at a time.
- **Follow every link** in the description and the comments: a Linear document, a Notion page, a Slack thread, a Datadog dashboard or monitor, a GitHub PR, a spreadsheet. Read them. Anything you cannot open (a Loom, a screenshot, a private link) goes in **Not read** — never guess at its contents.
- Say who filed it and when, and whether it came from a customer, an incident, or a teammate. Quote the sentence that states the actual ask.

## The code as it stands

- **Which repos**: infer from the ticket, its PRs, and the paths it names. Local clones are `~/dev/<repo>`: `envoyai-backend`, `envoyai-web`, `envoyai-chrome-plugin`, `envoyai-chat-ui`, `ci-workflows`. A ticket touching the agent's behaviour is usually backend-only; a ticket about a page or a panel is usually web or plugin plus a backend endpoint.
- `git -C ~/dev/<repo> fetch origin` in each, then read the feature end to end on the default branch: the modules named, their callers and callees, the tests and eval scenarios covering them, the sibling code that already solves a similar problem, and `git log -20 --` on those paths so you know who changed them last and why.
- **Read the rules**: every `CLAUDE.md` / `AGENTS.md` from each repo root down to the touched paths, and the `/review` skill's `repos/<remote>.md` for that repo if one exists (`basename -s .git "$(git remote get-url origin)"`).
- **Say what already exists.** Half the value of this step is discovering the ticket is partly built, already fixed, or describes a thing the code deliberately does. Name the code that already covers part of the ask.

## What is already up

- PRs naming the key, in every affected repo: `gh pr list -R envoy-ai/<repo> --search "<KEY>" --state all --json number,title,state,isDraft,mergedAt,mergeCommit,headRefName,baseRefName,author,url`. Keep the ones whose title, body, or branch carries the key; ignore promotion PRs ("promote development to staging", "Staging to prod").
- For each: open or merged, who wrote it, which branch actually contains it (`git merge-base --is-ancestor <mergeCommit> origin/{development,staging,production}`), whether it has unresolved review threads, and which part of the ask it delivers.
- **Local state**: `git branch -a --list "*<key-lowercase>*"` and `git worktree list --porcelain` in each repo. An existing branch or worktree makes step 2 a resume, not a fresh start — say so, and say whether it is behind its base or has uncommitted work.

## Past work on it

If the tool keeps a memory index for the repo, read it and open every entry naming this key, its neighbours, or the same subsystem. On this machine Claude Code's lives at `~/.claude/projects/-Users-aakifmohamed-dev-<repo>/memory/MEMORY.md`; a worktree session gets its own directory, so read the **main checkout's**. These entries carry decisions and gotchas that are in no ticket and no commit.

## Production evidence

Only when the ticket describes a symptom rather than a feature: `monitoring/incidents/`, the monitor JSON the ticket names, Datadog logs and metrics for the window it names, and the database through the `pg-readonly` MCP for the rows it names. Never run `az` (it needs explicit approval in the conversation, read-only included, and step 1 does not ask for approvals).

## Report, then stop

Open with one line: `<KEY> — <title> · <state> · <n> PRs up · <n> open questions`. For a project: `<PROJECT> — <n> issues · <n> done · <n> in flight · <n> open`.

Then:

```
Ask          — what the ticket wants, one paragraph, in the reporter's words. Name the
               carrier-, customer-, or rep-facing behaviour that changes.
Already done — code that exists, PRs merged or open, which part of the ask each covers.
Where it lands — the files and modules the work touches, each with the rule that governs it
               (single chokepoint, migration workflow, prompt sections + goldens, eval
               scenarios, monitoring, v1 schema). A map, not a plan.
Constraints  — repo rules binding this change, cross-repo deploy order, migrations, flags,
               anything that must ship together.
Open questions — every product decision (what a carrier, customer, or rep sees; defaults;
               retention; rollout) with a recommendation. Technical choices are never
               questions here; they get decided in step 2 and noted.
Not read     — links that could not be opened, environments not checked, things inferred.
Next         — the branch and worktree step 2 would create per ticket, in the order it would
               work them. Then: "say go, or name the tickets, to start."
```

Stop and wait. Do not create a branch, a worktree, or a file, do not comment on Linear or move its state, and do not start work because the context looks clear.

# Step 2: work

Runs on the user's go. A go that names tickets works only those, in the order given.

## Decisions first

Put every open question from step 1, and any new one found while planning, to the user in one round before touching code. Use the client's structured question tool when it has one (Claude Code: `AskUserQuestion`, up to 4 questions, 2–4 options each, the recommended option first and marked "(Recommended)", each option saying what the carrier, customer, or rep would experience). Otherwise ask in chat as a numbered list with lettered options and a recommendation, then stop and wait. Never ask about technical choices — naming, structure, where code lives, which helper to use, how to test it — decide those and note the rationale in the report. No open questions: say so in one line and continue.

## One ticket, one branch, one worktree

Worked in dependency order, each shippable on its own. Never switch or edit a main checkout's branch, and never `git stash`.

- Branch: `<firstname>/<key-lowercase>-<short-slug>` (the repo's convention, e.g. `aakif/eng-2149-verification-load-scope`).
- `git -C ~/dev/<repo> fetch origin && git -C ~/dev/<repo> worktree add ~/dev/.claude/worktrees/<key-lowercase>-<slug> -b <branch> origin/<default-branch>` (`gh repo view --json defaultBranchRef` — development for backend, web and plugin).
- Reuse a worktree already on that branch instead of making a second one. If it has uncommitted work or is behind its remote, report it and ask before touching it.
- **Backend**: `poetry run poe worktree-bootstrap` from inside the worktree, and copy the gitignored `secrets/*.json` across. Never `poetry install` and never `docker compose` from a worktree — both belong to the main checkout. If the bootstrap fails or the repo has grown a requirement it misses, fixing the script is part of the work.
- **web / plugin / chat-ui**: `npm ci` in the worktree; link `chat-ui` per its `CHAT_UI.md` (web is a symlink to the repo root, plugin is an rsync of `dist`, built first).
- Every read, edit, and command after this points at the **worktree path**, absolute. In parallel shell calls never use a bare `cd` — they share one working directory; use absolute paths or a `( cd X && … )` subshell.
- A ticket spanning repos gets one worktree per repo, same branch name in each.

## Test-first wherever there is behaviour to pin

Red before green, every time the change has an observable behaviour:

- **Code behaviour**: write the failing test first, run it, confirm it fails for the reason you expect, then write the code, then run it green. A test written after the fix proves nothing about the bug.
- **Agent or prompt behaviour**: the eval scenario is the test. Add it (both polarities where relevant — should-fire and should-hold), run the target, watch it fail, then change the prompt or the node, then run it again. Measure it the way `/fix-evals` measures (its "Measure" section is the shared rule): several trials, never one; the touched scenario's siblings and should-hold twins re-run beside it; an extraction change measured on the real node or the agent-loop target, not only the prompt target, because the two call the model differently. Never `xfail` an LLM behaviour; probabilistic behaviour gets `trials` + `min_pass_rate`.
- **A production bug**: the reproduction is the test, and an incident-born case becomes a scenario with `provenance: incident` and its `source_id`.
- **Skip it** for a change with no observable behaviour — a rename, a docs edit, a dependency bump, a generated file — and say in the report which tickets skipped it and why.
- Never weaken a test, eval, lint rule, baseline, or type check to make something pass.

## Build

The smallest change that delivers the ticket. No drive-by refactors, no behaviour nobody asked for, no scope beyond the ask — a better idea found on the way is a line in the report or a new ticket, not a bonus commit. Follow the repo rules gathered in step 1: the migration workflow, prompt goldens regenerated deliberately and reviewed as the diff they are, `poe docs-export` for a v1 schema change, eval adapters mirrored when entrypoint state assembly moves, a monitor in the same PR for a new or renamed metric. Run the touched modules' tests as you go and the repo's full check ladder before the PR; a red check is fixed or reported, never skipped. Commit per ticket or per logical step, with the repo's commit convention and the session's attribution lines, staging specific files — never `git add -A`.

## Push and open a draft PR

Push the branch and open a **draft** PR against the repo's default branch, titled by the repo's convention with the ticket key, and linked to the Linear ticket. The body says: what changed and why, the observable behaviour ledger, what was tested and eval'd, and the cross-repo deploy order when more than one repo is involved. Plain professional English, whatever the chat voice is, plus the session's attribution lines.

Never mark it ready for review, never approve, never merge, never enable auto-merge, and never move the Linear ticket's state — those are the user's, always, and a finished PR is not an instruction to merge it.

## Report, then stop

```
<KEY> — draft PR #<n> <url>
did: <what changed, one line per area>
tests: <what ran, red-first or not, and the result>
decisions I made: <technical calls and why, one line each>
not done: <skipped, blocked, or deliberately out of scope, and why>
next: <the next ticket — say go> | /review <pr> before you read it, then /watch <pr>
```

Then stop. Do not start the next ticket without a fresh go.
