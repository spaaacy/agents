# Evals (used by /fix)

Triage and fix red agent evals in the backend.

What `/fix` hands here: a PR (that PR's newest `Eval sample` check); `nightly` (the latest scheduled run on `development`); one or more scenario ids (`in_esc_021`, or `in_esc_021.email-prompt` to pin the target), meaning those cells wherever they last failed; a GitHub Actions run id (`run 37505277132`), meaning that run's batch; or pasted text (a step summary, a check title, a sticky `<!-- evalkit-summary -->` comment), which is the claim under test. With no PR in reach, `nightly`. The repo is `envoyai-backend`, worked from the checkout in the current directory.

Two steps, always in order:

1. **Triage** (the default): read the batch, measure, classify, report. Nothing in the repo changes: no edits, no commits, no `gh run rerun`, no `standing_reds.json` entry, no Linear write. Local eval runs are how a red gets classified, so they are allowed here; they spend OpenAI credits, which the user has accepted for this work.
2. **Fix**: follows straight on, with no go to wait for, unless the target said `triage` or step 1 left nothing to fix. It stops only for the user's real calls: product calls, standing reds, and a new Linear ticket.

Flow: `/fix`'s tick hands a red `Eval sample` here and keeps working the PR's other checks, and a red nightly or a named scenario comes here straight from `/fix`; `/pickup`'s test-first loop for agent behaviour, `/address-review`'s fix of a behaviour finding and `/verify-fixes`' check of one all follow the "Measure" section below. After step 2 the `/fix` loop picks the PR back up to see the sample re-run (or `gh run rerun <run> --failed`), and a fix that was part of a review round is checked by `/verify-fixes <pr>`. Nothing here merges or runs `az`, and nothing here creates a Linear ticket or edits one (beyond a comment) without the user's explicit approval.

Why this exists: the gate blames whichever PR sampled a scenario. Over 2026-09-22 to 10-06, 23 eval-relevant PRs produced 123 `Eval sample` check runs and about 110 confirmed cells; roughly half were booking-backend P0s on PRs touching no voice code, and the rest were mostly pre-existing on `development` (`tdr_008`, `tv_012`, `in_reply_023`, `v_li_023`, `in_esc_021`). They were cleared by 29 re-runs (8 went green on the re-run alone, 13 stayed red with a different cell confirming), by at least 9 unrelated pushes (the seed is the head sha, so a new commit draws a new slice and the red simply drops out), by 28 new standing reds against 17 removed, and twice by merging red with a bypass. Five of fifteen nightlies crashed on one harness bug. Reading the failing transcript before touching anything, and measuring base and branch side by side at the same time, is what told pre-existing from real in every case that was actually settled; a single trial, a morning base against an afternoon variant, a re-run as the fix, or a scenario deleted to get green are the mistakes this flow refuses to make.

Families seen in that fortnight (a snapshot to recognise, never a verdict): booking-backend P0 drift on identical code (`bk_requirements_wrong_temperature_decision`, `bk_cv_035`, `bk_v_li_005/008/020`, `bk_v_dd_031`, `bk_v_cv_006/011`, `bk_li_023`, `bk_v_fk_001`; ENG-2138, flag off in production); template dry-run grounding (`tdr_008/021/029`, fixed in #2611); the reply writer restating the carrier's number (`in_reply_023/037`, #2611); the style judge misreading a merged line (`tv_012`, #2600); tracking-loop flakes (`tr_300/304/241/313`); voice-prompt spoken-rate integrity (`v_li_023`, ENG-2202) and `cv_054`; the email analysis escalation class (`in_handoff_003/006/010`, `in_esc_021`, `in_resume_005/018`, bleed-sensitive); seed-time geocoding (#2600); and nightly-only reds nobody owns (`sec_001/002`, `ts_003` on chat, `v_ap_001` on voice, the six booking-audio scenarios that time out every night, which have no standing entries because `standing_reds.json` covers no booking-audio cell).

## Step 1: triage

### Gather the red

For a PR, the check run on the head sha, then the run it came from:

```
gh pr view <pr> --json number,title,url,headRefName,baseRefName,headRefOid,mergeable
gh api "repos/<owner>/<repo>/commits/<sha>/check-runs?check_name=Eval%20sample&filter=all" \
  --jq '.check_runs[] | {id, conclusion, title: .output.title, summary: .output.summary}'      # filter=all keeps every re-run attempt
gh api "repos/<owner>/<repo>/actions/runs?head_sha=<sha>&per_page=10" \
  --jq '.workflow_runs[] | select(.name=="Agent Evals") | {id, run_attempt, conclusion}'          # the check's details_url is the check run, not this
gh run view <run> --json conclusion,jobs --jq '.jobs[] | {name, conclusion, steps: [.steps[] | {name, conclusion}]}'
gh api "repos/<owner>/<repo>/actions/runs/<run>/artifacts" --jq '.artifacts[] | {id, name, created_at, expired}'   # one eval-sample-batch per attempt
gh run download <run> -n eval-sample-batch -D <scratch>/sample/runs/<run date, UTC>   # the sampled batch dir + its <batch>-retry sibling
gh run download <run> -n eval-agent-loop-changed -D <scratch>/loop                   # a whole store; only when the loop step ran
```

The sample artifact holds the batch directories themselves, so download them under `runs/<date>/` to make `<scratch>/sample` a store root the CLI can read (`runs/<date>/<batch>/` is the layout); the loop artifact is already one. Each re-run attempt uploads its own `eval-sample-batch`, so when a sha was re-run pick the attempt by artifact id (`gh api repos/<owner>/<repo>/actions/artifacts/<id>/zip > a.zip`). Artifacts expire after 90 days. Stacked PRs share commits, so the same check run shows under several PRs: dedupe by check-run id; a rebase or force-push drops earlier shas and their check runs from `pulls/<n>/commits`, and the PR's comments are where those numbers survive.

The title reads `<rate> pass · N regressions · M flaky · K known failures · voice: <outcome> · agent loops: <outcome>`: regressions failed twice in a row and are not in `evals/harness/standing_reds.json` (they block); flaky passed on the retry (listed, never a block); known failures are standing reds (exempt); the check is green only when the sample, the voice smoke and the agent-loop step (or its skip) all succeeded. The summary markdown lists each regression with its one-line reason, then the split against the nightly baseline (new failures against already failing on it), then the flaky and known lists. `no batch produced (sample step: cancelled)` is a push that cancelled the in-flight sample, not an eval failure; any other `no batch produced` means the job died before storing a batch: read `gh run view <run> --log-failed` first, that is an infrastructure red. A PR with `mergeable: CONFLICTING` gets no CI at all; say so and stop. The later steps "Run booking voice smoke" (`booking-audio`, one trial, live audio, no retry) and "Run changed scenarios on the agent loops" are skipped while the sample is red, so a green sample can reveal the next red; name the steps that did not run.

For `nightly`: `gh run list --workflow agent-evals.yml --event schedule --limit 10 --json databaseId,conclusion,createdAt,headSha`, then `gh run download <run> -n eval-nightly-summary -D <scratch>/nightly/<date>`. That artifact is one `summary.json` (`pass_rate`, `confirmed`, `flaky`, `standing`, `unconfirmed` = the new failures with their reasons, `errors` with run id and text, `failing_cell_ids`, per-suite counts; the nightly does not upload its batch, so there are no transcripts: a nightly red's transcript means re-running the cell locally). The nightly runs with no `--confirm-failures` and no floor, so a "new" failure there is one trial, to confirm before acting on. Exit 2 means every failure was a run error (the sweep broke, not the agents); standing reds keep it red by design, so the reds to triage are the ones listed before the `(standing: …)` tail. A crash night uploads nothing, and the PR summaries silently keep comparing against the last good night, so check the baseline's date in the check summary before trusting its new-versus-already-failing split. Two nights side by side are two summaries; `poe evals summary <local batch> --compare <scratch>/nightly/<date>/summary.json --store-root <root>` splits a local batch's failures into new against that night and already failing on it. The audit lines `STANDING-RED STALE` (never executed) and `STANDING-RED PASSED` (passed every graded verdict) are claims to re-measure, not instructions: one passing night does not clear a one-in-three flake (`in_esc_021` was removed on such a night and confirmed as a regression the day the removal merged).

Read a store with the CLI, never by opening JSON by hand: `poe evals ls <batch> --failed --store-root <dir>` (failing runs first; `--by-scenario` for per-cell `passed p/n` strips), `poe evals explain <run_id> --store-root <dir>` (verdicts first, the failing expectation and its critique, then the transcript and the world digest), `poe evals summary <batch> --store-root <dir>` (the retry sibling is read with it), `poe evals open <batch> --store-root <dir>` for the viewer, and `poe evals rerun <run_id> --store-root <dir> --max-judge-calls 0` to re-execute exactly one stored run's envelope into a new batch.

### Read the rules and the scenario

`evals/CLAUDE.md`, `evals/docs/runbook.md` ("When evals go red"), the root `CLAUDE.md`'s Agent Evals section, every `CLAUDE.md` from the repo root down to the surfaces the reds name, and the `/review` skill's `repos/envoyai-backend.md`. Then per red, `poe evals show <scenario_id>`: the contract, which targets serve it, whether it is standing, and its stored history and green streaks. Read the scenario JSON itself (`evals/harness/suites/<suite>/scenarios/<id>.json`): priority, polarity, `provenance` (an `incident` scenario is never weakened), `trials` / `min_pass_rate`, `notes`.

### Where to run

Local runs need a checkout on the branch under test and one on untouched `development`, so base and branch can run at the same time. Reuse the worktree already on the PR branch (`git worktree list --porcelain`; on this machine `~/dev/.claude/worktrees/<branch-slug>`), else create one with `git fetch origin && git worktree add <path> <headRefName>` and run `poetry run poe worktree-bootstrap` (`--with-db` for an agent-loop target) inside it; a throwaway `git worktree add <path> origin/development` is the base. Never switch the main checkout's branch, never `poetry install` or `docker compose` from a worktree, never `git stash`. Test secrets decrypt on their own here (the `secrets/test.ejson` key is in `ejson-keys/` and every worktree links that folder), so never hand-seed `secrets/test.json` or run a re-copy loop; an agent-loop target needs the worktree's `.env.worktree` exported (`set -a; . ./.env.worktree; set +a`). The OpenAI key comes from config at call time, never on a command line.

### Measure and classify

For every red cell, in this order:

1. **Read the failing output first.** `explain` the failed run in the CI batch and say in one sentence what the agent did against what the expectation wanted: the tool it called, the field it set, the line it spoke. The failure mode is often not what the check title or the standing reason says (four "prompt gaps" in September were scenarios asserting a contract the prompt deliberately contradicts).
2. **Is it in the diff's reach?** Name the suites the PR's diff can affect (the surface it renders through: a voice prompt change cannot move an email scenario, and a scenario file change always runs). A red outside the reach is pre-existing until measured otherwise, but it is still measured.
3. **Base against branch, side by side.** A red that vanished after an unrelated push is not evidence of anything: the seed is the head sha, so the push drew a different slice. A re-run is a coin flip, not a classification (8 of 21 re-run shas went green, 13 confirmed a different cell). Run the cell on untouched `development` and on the branch at the same time, 12 trials each, judges off (`--max-judge-calls 0`), separate store roots. Compare the pass counts and the failure mode; byte-identical prompt input on both sides (the token count in the envelope) settles whether the branch changed what the model saw. Before running, check the last nightly for the same cell (`gh run view <nightly> --log-failed | grep <scenario>`) and the cell's stored history (`poe evals show`): a red there with the same verdict is pre-existing evidence, not a substitute for the measurement.
4. **Measure where the failure happened.** An intake or analysis extraction red on `email-prompt` is re-measured on the `email` agent loop: the prompt target pastes the JSON schema into the message while the node uses function calling, and the same email has passed 10/10 on one and failed 14/20 on the other. A templated outreach red is measured on `email-template-dry-run`. A red involving a stop time, a timezone, or a date is re-run with CI's geocoder emulated (a scratch wrapper that installs a `GeocodingService` whose `geocode` returns None, clears `get_timezone_from_location`'s cache, then `runpy.run_module("scripts.commands.evals", run_name="__main__")`; `TZ=UTC` alone does not reproduce it). A booking-backend red is measured knowing that model drifts run to run on identical code.

Then exactly one case per cell:

- **Yours**: green on base, red on the branch, or a different failure mode on the branch. The change regressed behaviour. Severity by what a carrier, customer or rep would hit.
- **Spec change**: the scenario asserts the old behaviour and the ticket asked for the new one. The scenario is updated in the same PR; that edit is the spec change, reviewed next to the code.
- **Pre-existing**: red on untouched development at the same rate and in the same way. Split it further from the transcript: the agent does something other than the expectation (a product gap) or the expectation is wrong (it pins a phrasing the agent legitimately paraphrases, contradicts the surface's own instructions, or cannot render on that target, such as a spoken turn on a target that returns a tool call).
- **Flake**: fails in one run and passes in another on identical code, across runs and across days, with no mechanism in the transcript. Needs the numbers (x/12 base, y/12 branch, the nightly history).
- **Infrastructure**: a run error (adapter exception, seeding failure, a crashed batch, `no batch produced`), `poe evals doctor` clean locally. A persistent one is a broken scenario or composition, not noise.
- **Judge**: the critique misreads the transcript (nightly only; the PR sample runs judges off). The run goes to the calibration labels; never a rubric tweak.

### Report

Open with one line: `<#pr | nightly <date>>: <n> reds · <n> yours · <n> spec changes · <n> pre-existing · <n> flakes · <n> infra · <n> judge`. Then per red, in the order a reader would fix them:

```
<n>. <scenario>.<target> [<case> · P<k>] <suite> — <scenario title>
   Fails: <what the agent did against what the expectation wants, from the transcript>
   Base: development <x>/<N> vs branch <y>/<N> at <time>; nightly <date>: <pass | same fail | not run>; standing: <no | the reason>
   Cause: <the mechanism, one or two sentences, or "not found: <what was tried>">
   Plan: <fix the change: … | re-spec the scenario | fix the expectation (prove with trials) | propose a standing red + the owning ticket | trials + floor from the numbers | fix the harness | rerun once>
```

Then **Not verified** (cells not measured locally and why, inferences, steps CI skipped). Then ask with the client's structured question tool (Claude Code: `AskUserQuestion`; otherwise a numbered list in chat with lettered options), in one call, and only when there is something to ask: one question per product call (what the agent should say or do when the prompt and the scenario disagree and the ticket does not settle it), and one multi-select question listing the cells proposed as standing reds (a cell is added only when it is picked there or named in chat; the default is none). Wait for those answers, then go on to step 2; with nothing to ask, go on to step 2 straight away.

## Step 2: fix

### Decisions first

Product calls from step 1 were asked there; put any new one found while planning to the user in one round before touching anything. Use the client's structured question tool when it has one (Claude Code: `AskUserQuestion`, up to 4 questions, 2 to 4 options each, the recommended option first and marked "(Recommended)", each option saying what the carrier, customer, or rep would experience); otherwise ask in chat as a numbered list and stop. Technical choices (where the rule goes, how to word a scenario note, which trial count) are decided here and noted. A standing-red entry is never covered by a general go: it is added only when the user picked the cell in the multi-select question or named it in chat.

### Fix, per case

- **Yours**: the smallest change to the code or prompt that restores the behaviour; the scenario is already the red-first test. Prompt edits follow the repo's rules: email prompts are sections and recipes (`UPDATE_GOLDENS=1` regenerates the goldens, and that diff is the prompt review), voice prompts are edited where they live, and gpt-4.1 follows the instruction closest to the tail, so a rule the model ignores mid-page is restated at the tail rather than rewritten in place. Scope a new rule tightly, keep the one example the loop proves necessary, and never quote the phrasing being forbidden (it bleeds into the output).
- **Spec change**: edit the scenario, both polarities where the behaviour has a should-hold twin, with a `notes` line saying what changed and why; `poe evals validate`, then `poe evals coverage` to regenerate the committed matrices (never hand-edited).
- **Pre-existing, expectation wrong**: the minimal edit to the expectation, proved with live trials and recorded in `notes` and the commit message. An `incident` scenario's intent is never weakened; if the expectation cannot be made right without changing the intent, that is a product call.
- **Pre-existing, product gap**: no code change in this PR unless the user asks for the fix here. Draft the standing-red entry (`{"scenario_id", "target", "reason"}`, the reason naming the observed behaviour in one sentence, the measurement with its sha and date, and the owning ticket; the existing entries are the style guide) and the Linear text: a comment on the open ticket that owns the surface when one exists (posted at the end without asking), otherwise a new ticket in the shape of ENG-2202, which is only ever a draft until the user approves creating it (what is wrong, evidence with the side-by-side numbers, why it has a ticket, done when, which names removing the entry). Never comment on a closed ticket.
- **Flake**: `trials: N` and a `min_pass_rate` floor on the soft expectation, set from CI and nightly numbers together with the local ones, never from local runs alone (a byte-identical prompt has passed 46/50 here and 9/21 in CI). A measured flake is never a standing entry.
- **Infrastructure**: fix the adapter, composition, seed, or evalkit code with a red-first unit test (`tests/suite/evals/harness`, `evalkit/tests`); a one-off runner loss is one `gh run rerun <run> --failed`, reported as such.
- **Judge**: nothing in this PR; the run id goes in the report for the calibration labels.

Never weaken a test, an eval, a judge, a baseline, `standing_reds.json`, or a golden to get green; never delete a scenario to pass; never hand-edit `evals/harness/coverage/` or `legacy_baseline.txt`; never `xfail` an LLM behaviour.

### Measure

The shared rule for every skill that changes agent behaviour, so a fix is a measurement, not a feeling:

- **Never one trial.** Six trials for a first read on a prompt target, twelve side by side for a classification, twenty to thirty interleaved base and variant for a wording decision. Run base and variant at the same time, never a morning base against an afternoon variant: two checkouts, or in one checkout `git show origin/development:<path> > <path>` under a trap that restores the file.
- **Siblings beside it.** Every should-hold twin of the touched scenario, and every scenario rendered through the same prompt section (a tail restatement in the analysis prompt moved the hand-off scenarios: 1/10 to 14/20 failing before its own fix). One trial across the suite to find movers, then six on each mover against base.
- **The right target.** Extraction on the real node or the agent loop, outreach templates on the dry run, time and date behaviour under the emulated geocoder, booking-backend with the drift in mind.
- **No local CI sample before the push.** The sample is CI's: it runs on the push, retries a red cell on its own, and runs the branch's changed scenario files on the agent loops. Running it here first costs an hour and tells nothing the push will not. The touched scenarios and their siblings above are the local bar; then push once and read the check.
- The one command: `ENVIRONMENT=test poetry run poe evals run --scenario <id> --target <t> --trials N --max-judge-calls 0 --store-root <scratch>/<label> --progress`; `explain` the misses, `ls <batch> --failed` for the tally.

### Commit

One commit per red or per group sharing a cause, with the repo's commit convention (`fix(email): …` for a product fix, `fix(evals): …` for a scenario, harness, or standing-red change), the message carrying the before and after numbers; stage specific files only. Run `poe evals validate`, the touched pytest modules (`ENVIRONMENT=test poetry run pytest -m unit tests/suite/<domain>`; `tests/suite/prompts` after any email prompt edit), and the goldens.

### Push, post, report

```
#<pr> evals: <n> fixed · <n> re-specced · <n> floored · <n> proposed standing · <n> left
<n>. <scenario>.<target>: <fixed in <sha> | re-specced in <sha> | floored in <sha> | proposed standing | open>: <x>/<N> -> <y>/<N>; siblings <a>/<b>
evals: <targets, trials, store roots>
tests: <what ran and the result>
decisions I made: <technical calls and why, one line each>
not done: <skipped, blocked, or left for a ticket, and why>

Standing reds proposed (added only when you name them)
<scenario>.<target> — "<reason>"

pushed: <branch> at <head> (<n> commits)
Linear: <comment URL on ENG-xxxx | new ticket drafted, waiting on your approval: "<title>", assignee <name>: <text>>
next: /loop /fix <pr> (or gh run rerun <run> --failed) to see the sample re-run
```

Push the branch from the worktree (`git push origin <headRefName>`, never force, never another branch) and post the Linear comment with the Linear MCP (`save_comment`), with no go to wait for; hold the push, and ask instead, only when a touched test is still red or a touched scenario or sibling is still below its bar. Two things are always asked with the structured question tool, never done on a general go: creating a new Linear ticket (`save_issue` creates nothing until the user approves that ticket, and the ticket it creates always has an assignee, the user unless they named someone else; the same goes for any other change to a ticket, such as its assignee, title, description, labels, priority or state), and the standing-red multi-select for any cell proposed since step 1. On those answers: create only the approved ticket, add only the standing reds the user named, in their own `fix(evals):` commit, push it, and report the ticket URL and the entry ids in one line.
