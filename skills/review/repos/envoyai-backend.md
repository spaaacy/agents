# Review instructions (envoyai-backend)

Loaded by the shared `/review` skill when the reviewed checkout's origin remote is `envoyai-backend`. Keep to rules that change review behavior; project context lives in the repo's `CLAUDE.md`.

## Blocking here means

Wrong carrier-facing behavior; a prompt or tool change with no eval scenario in the same PR; a migration that cannot apply on a fresh chain, forks the Alembic head, or contains a manual COMMIT/BEGIN; data written outside a transaction or the event outbox; a compliance, booking, or outreach path that bypasses its single chokepoint (`ComplianceService`, `OfferService.book_offer`, `CarrierOutreachService.initiate_email_outreaches`); a tenant-unscoped query; a str-Enum column stored without `values_callable`.

## Product surfaces to treat as behavior changes

- Anything under `envoy/comms/voice/prompts/**`, `envoy/comms/voice/ai/agents/**`, `envoy/logistics/agents/email_load_booking/**` prompt or node text, and `envoy/logistics/ai_disclosure.py`.
- `LoadBookingModel.from_load_model` and `envoy/logistics/customization` (what the agent may share).
- Negotiation tier math in `envoy/logistics/negotiation/tiers.py` and offer notes / sentinels.
- Bookability and covered-load logic (`is_load_bookable`, `is_load_covered`, `BOOKABLE_LOAD_STATUSES`).
- Compliance block flags and `FLAG_SCOPED_SOURCES`.
- Outreach eligibility, daily caps, schedule, and template grounding rules.
- Escalation result strings (`ESCALATION_RESULT_*`) and notification wording: the clients classify from them.

## Always check

- A change to `_handle_carrier_response`, the voice session bootstrap, or tracking `handle_inbound` updates the mirroring adapter in `evals/harness/adapters/`.
- New enum values ship an `ALTER TYPE ... ADD VALUE IF NOT EXISTS` migration; a value added and used in one migration recreates the type.
- `scripts/check_single_migration_head.py` passes; a re-parented migration is called out in the PR body.
- A `/api/v1` schema change regenerates `openapi-v1.json`; internal features stay off v1.
- Find-then-create upserts take the shared advisory lock key (`envoy/logistics/carrier/upsert_lock.py`, `_load_upsert_lock_keys`) and are backed by a unique index.
- Services called inside a caller's UnitOfWork flush only; side effects go through the outbox or `on_commit`, never fire-and-forget.
- Celery tasks use `@task` on async functions; no hand-rolled event loops.
- No `hasattr`/`getattr` probing; no "Ellie" in identifiers, comments, logs, or metrics.
- Any `az` invocation added to a script or poe target is called out.
- A `CLAUDE.md` sentence made stale by the change is a finding.

## Do not report

Anything ruff, mypy, bandit, the str-enum lint, the import-order hook, or `poe evals validate` already enforces. Generated files under `evals/harness/coverage/`. The content of `openapi-v1.json`, only whether it was regenerated. Pre-existing repo-wide FK/index drift. Formatting of `.svelte` or lockfile churn in sibling repos.
