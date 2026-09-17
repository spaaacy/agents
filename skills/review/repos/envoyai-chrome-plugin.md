# Review instructions (envoyai-chrome-plugin)

Loaded by the shared `/review` skill when the reviewed checkout's origin remote is `envoyai-chrome-plugin`. Keep to rules that change review behavior; project context lives in the repo's `CLAUDE.md`.

## Blocking here means

A content script or UI component calling the backend directly instead of going UI -> `sendMessage()` -> background -> `ApiClient` (the three-layer rule); a static `matches` entry for a customer site instead of the org-driven dynamic registration; a new manifest permission with no entry in `cws/privacy-fields.md`; a manual `npm version` or hand-edited version; server state held outside TanStack query hooks or UI state outside the `src/lib/ui-state/` allow-list; a Pusher subscription anywhere but the offscreen document.

## Product surfaces to treat as behavior changes

- Anything the rep sees in the sidebar: load workspace cards, outreach flows, call monitoring, escalation and notification rendering, onboarding and popup copy.
- Load identification: `content.ts` extraction and the `/identify` request shape.
- Which sites the extension activates on (`content-script-manager.ts`, domain list handling).
- Feature UI belongs in `envoyai-chat-ui`; a feature UI change landing here instead is a scope finding, and a chat-ui bump must pair with its PR.

## Always check

- A new backend call adds all three pieces: the `protocol.ts` message type, the `background.ts` handler, and a service/query-layer caller (never a component).
- `src/lib/generated/api-types.ts` was regenerated from a running backend, not hand-edited, when the backend contract changed.
- New code ships with tests (the PR gate is 90% diff coverage and currently advisory, so the review enforces it).
- No "Ellie" in identifiers, comments, commit messages, or logs; only in display copy.
- Nothing new is built on the vestigial pieces: `src/lib/templates/`, `src/lib/models/schemas.ts`, the `pusher-auth` handler, `build.sh`, `pnpm-lock.yaml`.

## Do not report

Anything eslint, svelte-check, or prettier already enforces. Generated files under `src/lib/generated/`, only whether they were regenerated. Lockfile churn from CI floating `@envoyai/chat-ui`.
