# Review instructions (envoyai-web)

Loaded by the shared `/review` skill when the reviewed checkout's origin remote is `envoyai-web`. Keep to rules that change review behavior; project context lives in the repo's `CLAUDE.md`.

## Blocking here means

A regenerated `svelte-check-baseline.json` that hides a new error rather than following a fix; a secret or token in client code or a `PUBLIC_*` variable; a settings or admin surface reachable without the owner/admin or `is_superuser` gate; a `+page.server.ts` doing substantive work outside the Microsoft OAuth callback; new feature work inside the legacy workflow builder (`/workflows`, `/workflow-runs`, `src/lib/components/workflow/`, `stores/workflow-*`).

## Product surfaces to treat as behavior changes

- Load board and load detail, calls, analytics, scorecard and ROI numbers and their filters.
- Every org-settings tab, especially the agent ("Ellie"), Compliance, Negotiation, Voice & AI, and Integrations tabs, since those drive backend behavior.
- Public tokenized pages (`(open)/`): driver check-in, invitation acceptance, SMS consent. Copy and flow changes there are customer- and driver-facing.
- Notification and escalation rendering, and anything classified from backend result strings.

## Always check

- A new API call follows the layers: `ENDPOINTS` constant, `src/lib/api/*` module, Zod schema or generated type, TanStack query hook, then the component.
- `src/lib/generated/` was regenerated from a running backend after a backend schema change, never hand-edited.
- Cross-repo ordering is stated: a backend-dependent change says which backend PR must deploy first.
- Tests sit beside the code in `src/**/*.test.ts` (Vitest); Playwright specs in `e2e/` follow `e2e/README.md` and stay in MOCK tier.
- No "Ellie" in identifiers, data keys, comments, or API paths; display text may keep it.
- Chat and load-workspace UI changes belong in `envoyai-chat-ui`; this repo only wires transport, realtime, and storage.

## Do not report

Anything eslint, prettier, or `npm run check:ci` already enforces, and pre-existing svelte-check errors already in the baseline (ENG-1977). Generated files, only whether they were regenerated. Lockfile churn from the floated `@envoyai/chat-ui` dist-tag.
