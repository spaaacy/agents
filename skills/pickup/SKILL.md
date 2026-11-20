---
name: pickup
description: Pick up a Linear ticket or project in two steps. First gather the full context - the ticket and its whole comment thread, the PRs already up, the code as it stands, past work on it - and lay out how the change would work from the product side, like /impact (who notices, defaults, what is left out, the new tables or columns, shipping), changing nothing. Then, only after the user says go, create a worktree per ticket, build it test-first, and open a draft PR. Use when the user points at a Linear ticket or project, or asks to pick up, start, or work on one.
---

Target: `$ARGUMENTS`. An issue key (`ENG-2130`), a project key (`P-ENG-69`), a project name, or a Linear URL. A trailing `go` / `work` / `start`, or a list of keys, means step 2. Nothing means the ticket this conversation is about (`Shared skill arguments` in `~/.agents/AGENTS.md`); only when there is none, ask which ticket and stop.

Two steps, always in order, each ending with a stop:

1. **Context** (the default): read everything, lay out how it would work, change nothing — no branch, no worktree, no edits, no Linear writes, no PR.
2. **Work**: runs only when the user says so after step 1 ("go", "start", or this skill again with keys). If step 1 has not run in this conversation, run it first and stop there.

Flow: this is the first skill in the PR lifecycle. It ends with a draft PR (a frontend one with its screenshots, through `/screenshots`); the author then runs `/review <pr>` on it, `/fix <pr>` keeps it level with `development` and its required checks green while reviewers read it, and fixes any red in its `Eval sample` the PR itself caused, their comments are worked with `/address-review <pr>` and checked with `/verify-fixes <pr>`, the user merges, and `/linear-sweep` moves the ticket once it ships. The worktree this skill creates is the one every later skill reuses, and `/cleanup` removes it once the PR has shipped.
To put the main checkouts on this ticket's stack and remove the approved worktrees, use `/spotlight <ticket>`.

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

## Lay out how it would work, then stop

The report is how the change would work once shipped, from the product side, the way `/impact` describes a finished PR (`~/.agents/skills/impact/SKILL.md`): who notices, what changes for them, what is left alone. It is the plan Aakif says go to, so step 2 builds what it describes. Exactly this shape:

```
<KEY> — <title> · <state> · <n> PRs up · <n> calls for you
<one sentence: what picking this up would deliver, in product words>

- <who notices>: <what changes for them>
- ...
- Defaults: <on or off by default, for which organizations, who can switch it and where>
- Left out: <what this deliberately does not cover, including parts of the ask it drops>
- Your call: <one product decision, stated as the recommendation the bullets above assume>
- Already there: <code or PRs, merged or open, that already cover part of the ask, and which part>
- Data changes: <each new table or column: its name and what it holds; "none" when nothing new is stored>
- Shipping: <the repos and PRs it would take in build order, what must deploy first, a backfill to run after the deploy, a flag>
- Not read: <links that could not be opened, environments not checked>
```

Rules:

- Present tense, as if shipped ("reps see", not "reps would see"). Rank by how many people notice, carriers and customers first. One sentence per bullet, under about 25 words, twelve bullets at most; drop Already there and Not read when empty.
- People and what they experience, never code: no file paths, function, class or module names, tests, metrics or PR internals. Quote carrier- or customer-facing copy exactly when the ticket specifies it, and keep its numbers, timings and limits.
- Your call: one bullet per product decision (what a carrier, customer or rep sees; defaults; retention; rollout), each phrased as the recommendation the bullets above already assume. Technical choices are never calls; step 2 makes and notes them.
- Data changes is the one place a table or column is named: every new table and every new column the work adds (or removes), in backticks with a few words on what it holds, one bullet each when there are several. Indexes, constraints and type changes are left out, and so is a value stored in an existing settings record, which is not a new column. These are planned, so a name that could still change is marked "(planned)".
- Never promise more than the ask, never soften a limit the ticket states, and mark anything inferred rather than read with "(inferred)".
- A project lays out each open ticket as one sentence in work order, then its Your call, Data changes and Shipping lines; a ticket is still built one at a time.

Then ask, in one call of the client's structured question tool (Claude Code: `AskUserQuestion`; Codex: `request_user_input`; otherwise a numbered list in chat with lettered options), the go plus each Your call decision as its own question, the recommended option first, up to the tool's limit: the go is build it as laid out (Recommended), build only the tickets named, or stop here. When Data changes names a table or column, the same call carries one more question for the schema, never folded into the go (`Working style` in `~/.agents/AGENTS.md`): header `Schema`, per change why it has to be stored and why it can't be derived from data already stored or kept somewhere that exists, an `Approve` option whose preview is the diagram in `~/.agents/skills/db-diff/diagram.md` with `branch:` set to the branch step 2 will create (named below), and an option that builds without it, saying what is done instead. The go covers no table or column: if the schema question is answered no, step 2 builds without them or stops to ask how. Decisions that did not fit are asked first thing in step 2. If an answer changes what the layout says, reprint the bullets that changed before building. Wait for the answer. Do not create a branch, a worktree, or a file, do not comment on Linear or move its state, and do not start work because the context looks clear.

# Step 2: work

Runs on the user's go. A go that names tickets works only those, in the order given.

## Decisions first

The calls answered with the go are settled. Put the ones that did not fit in that round, and any new one found while planning, to the user in one round before touching code. Use the client's structured question tool when it has one (Claude Code: `AskUserQuestion`, up to 4 questions, 2–4 options each, the recommended option first and marked "(Recommended)", each option saying what the carrier, customer, or rep would experience). Otherwise ask in chat as a numbered list with lettered options and a recommendation, then stop and wait. Never ask about technical choices — naming, structure, where code lives, which helper to use, how to test it — decide those and note the rationale in the report. No open questions: say so in one line and continue.

## One ticket, one branch, one worktree

Worked in dependency order, each shippable on its own. Never switch a main checkout's branch, and never `git stash`. A ticket whose PR a main checkout is already on is worked there, not in a new worktree (`Working style` in `~/.agents/AGENTS.md`).

- Branch: `<firstname>/<key-lowercase>-<short-slug>` (the repo's convention, e.g. `aakif/eng-2149-verification-load-scope`).
- `git -C ~/dev/<repo> fetch origin && git -C ~/dev/<repo> worktree add --no-track ~/dev/.claude/worktrees/<key-lowercase>-<slug> -b <branch> origin/<default-branch>` (`gh repo view --json defaultBranchRef` — development for backend, web and plugin). `--no-track` matters: without it the branch follows the default branch, a later `git pull` on it pulls `development`, and `/spotlight` refuses to switch a main checkout onto it. The first push sets its real upstream (below).
- Reuse a worktree already on that branch instead of making a second one. If it has uncommitted work or is behind its remote, report it and ask before touching it.
- **Backend**: `poetry run poe worktree-bootstrap` from inside the worktree, and copy the gitignored `secrets/*.json` across. Never `poetry install` and never `docker compose` from a worktree — both belong to the main checkout. If the bootstrap fails or the repo has grown a requirement it misses, fixing the script is part of the work.
- **web / plugin / chat-ui**: `npm ci` in the worktree; link `chat-ui` per its `CHAT_UI.md` (web is a symlink to the repo root, plugin is an rsync of `dist`, built first).
- Every read, edit, and command after this points at the **worktree path**, absolute. In parallel shell calls never use a bare `cd` — they share one working directory; use absolute paths or a `( cd X && … )` subshell.
- A ticket spanning repos gets one worktree per repo, same branch name in each.

## Test-first wherever there is behaviour to pin

Red before green, every time the change has an observable behaviour:

- **Code behaviour**: write the failing test first, run it, confirm it fails for the reason you expect, then write the code, then run it green. A test written after the fix proves nothing about the bug.
- **Agent or prompt behaviour**: the eval scenario is the test. Add it (both polarities where relevant — should-fire and should-hold), run the target, watch it fail, then change the prompt or the node, then run it again. Measure it the way the "Measure" section of `~/.agents/skills/fix/evals.md` says (the shared rule): several trials, never one; the touched scenario's siblings and should-hold twins re-run beside it; an extraction change measured on the real node or the agent-loop target, not only the prompt target, because the two call the model differently. Never `xfail` an LLM behaviour; probabilistic behaviour gets `trials` + `min_pass_rate`.
- **A production bug**: the reproduction is the test, and an incident-born case becomes a scenario with `provenance: incident` and its `source_id`.
- **Skip it** for a change with no observable behaviour — a rename, a docs edit, a dependency bump, a generated file — and say in the report which tickets skipped it and why.
- Never weaken a test, eval, lint rule, baseline, or type check to make something pass.
- Run checks the way `Running checks` in `~/.agents/AGENTS.md` says: one batched test call per round, `-n 8` on the backend, only the failing ids while fixing, long runs in the background.

## Build

The smallest change that delivers the ticket as laid out in step 1. If the code forces something the layout did not say (a carrier, customer or rep sees something different, a default changes, a new table or column it did not list), stop that ticket and ask before building it. No drive-by refactors, no behaviour nobody asked for, no scope beyond the ask — a better idea found on the way is a line in the report or a drafted ticket the user approves before it is created (with an assignee, the user unless they name someone else; never unassigned), not a bonus commit. Follow the repo rules gathered in step 1: the migration workflow, prompt goldens regenerated deliberately and reviewed as the diff they are, `poe docs-export` for a v1 schema change, eval adapters mirrored when entrypoint state assembly moves, a monitor in the same PR for a new or renamed metric. Run the touched modules' tests as you go, the way `Running checks` in `~/.agents/AGENTS.md` says; CI runs the full ladder and the eval sample on the PR. A red check is fixed or reported, never skipped. Commit per ticket or per logical step, with the repo's commit convention and the session's attribution lines, staging specific files — never `git add -A`.

## Push and open a draft PR

Push the branch with `git -C <worktree> push -u origin <branch>`, so the branch follows its own remote branch (what `/fix` pulls and `/spotlight` checks), and open a **draft** PR against the repo's default branch, titled by the repo's convention with the ticket key, which is what links it to the Linear ticket (the ticket itself is never edited: no state, assignee, title or description change without the user's approval; a comment is fine). The body says: what changed and why, the observable behaviour ledger, what was tested and eval'd, and the cross-repo deploy order when more than one repo is involved. A PR that adds a backfill gets its runbook ticket drafted and asked about in this round (`Working style` in `~/.agents/AGENTS.md`), and the body links it once it exists. Plain professional English, whatever the chat voice is, plus the session's attribution lines.

A draft PR in `envoyai-web`, `envoyai-chrome-plugin` or `envoyai-chat-ui` then gets its screenshots, with no go to wait for: follow `~/.agents/skills/screenshots/SKILL.md` for it (real before/after screens posted on the ticket, the body's Screenshots section pointing there).

Never mark it ready for review, never approve, never merge, never enable auto-merge, and never move the Linear ticket's state — those are the user's, always, and a finished PR is not an instruction to merge it.

## Report, then stop

```
<KEY> — draft PR #<n> <url>
did: <what changed, one line per area>
tests: <what ran, red-first or not, and the result>
screenshots: <n posted on the ticket | no visible change | not a frontend PR>
decisions I made: <technical calls and why, one line each>
not done: <skipped, blocked, or deliberately out of scope, and why>
next: <the next ticket — say go> | /review <pr> before you read it, then /loop /fix <pr>
```

Then stop. Do not start the next ticket without a fresh go.
