---
name: screenshots
description: Capture real screenshots of what a frontend pull request (envoyai-web, envoyai-chrome-plugin, envoyai-chat-ui) changes on screen, rendered by the PR's own code with example data, before and after; post them on the PR's Linear ticket and point the PR body's Screenshots section at them. Runs on its own at the end of /pickup and of an /address-review round that changed the UI. Use when the user asks for screenshots on a PR, or to add or refresh them.
---

Target: $ARGUMENTS. A PR number or URL is that PR; a Linear key (`ENG-1234`) or URL means every open frontend PR for that ticket. A bare number is looked up in `envoyai-web`, then `envoyai-chrome-plugin`, then `envoyai-chat-ui` (ask if two match). Nothing means the PR this conversation is about (`Shared skill arguments` in `~/.agents/AGENTS.md`). A backend PR has no screens here: say so and stop.

Flow: `/pickup` runs this right after it opens a frontend draft PR, and `/address-review` after a round that changed something on screen; `/impact` uses its Capture section for its page. Its only writes are one comment on the PR's Linear ticket (a comment needs no approval) and the PR body's Screenshots section. Screenshots are never committed or pushed anywhere, not to a checkout, the PR branch or any other branch (Aakif's call, 2026-11-17): the ticket holds them. Anything posted is plain professional English whatever the chat voice.

## Which screens

Read the PR's diff and its ticket. Every page, panel, dialog, setting, card or state the change adds or alters gets a screenshot, each in the state that shows the change (the new toggle on, the empty state, the error, the state after a click), scoped to the part that changed (the panel or dialog, not the whole window) unless the layout itself is the change. "After" always; "before" too, captured at the PR's base, when the screen existed.

A PR that changes nothing anyone sees (a refactor, tests, types, an API client) posts no comment; its Screenshots section says "No visible change." and the report says why.

## Capture

Every screenshot is the real screen, rendered by the PR's own code. Never draw a mockup or recreate a screen in HTML; a screen that could not be captured is listed as not shown, with why. Save the PNGs to `~/.agents/state/screenshots/<repo>-<pr>/` (or the folder the caller names), named `<n>-<slug>-before.png` / `-after.png`.

- Web (`envoyai-web`): its Playwright setup runs the real app with no backend (`e2e/README.md`: mock tier, `mockLoggedIn` in `e2e/support/mock-api.ts`, `test` from `e2e/support/fixtures.ts`, `appPath()` for the `/app` base path).
  1. Make a throwaway checkout of the PR's head: `git -C ~/dev/envoyai-web fetch origin <head> && git -C ~/dev/envoyai-web worktree add --detach ~/dev/.claude/worktrees/shots-web-<pr> <head sha>`, then `cp -Rc ~/dev/envoyai-web/node_modules` into it (a copy-on-write clone, never a symlink: vite refuses files reached through a link). When the change lives in chat-ui, build the chat-ui PR's head and link it the way `CHAT_UI.md` says.
  2. Write one throwaway spec there, `e2e/zz-shots-capture.spec.ts` (never committed): sign in a mock admin, stub every `/api/*` call the page makes with example data shaped like the real responses (reuse the mocks in the PR's own e2e specs when it has them; the generated API types say the shapes), drive the page into the changed state, and save `locator.screenshot()` PNGs at a 1280×800 viewport.
  3. Run it: `npx playwright test e2e/zz-shots-capture.spec.ts --reporter=line` from that checkout (it boots its own dev server on port 5174; one run at a time). Look at every PNG and fix the spec until each shows the intended state; a blank or error page is a broken spec, not a screenshot.
  4. For "before", run the same spec in a throwaway checkout of the PR's base sha.
- Chat-ui (`envoyai-chat-ui`): through the web page that renders the changed component, as above, with the chat-ui PR's build linked in.
- Extension (`envoyai-chrome-plugin`): it has no browser harness. Show a change through the web page that renders the same chat-ui component when there is one; an extension-only screen is listed as not shown.

Remove every throwaway checkout when done (`git worktree remove --force <path>`: they hold nothing but the throwaway spec).

## Post them on the ticket

The ticket is the key in the PR's title or branch. A PR with no ticket gets nothing posted: list the file paths in the report and stop.

Upload each PNG on its own, finishing one before preparing the next: the Linear tool's `prepare_file_upload` (filename, `image/png`, the exact size from `stat -f%z <file>`), then straight away `curl -sS -o /dev/null -w '%{http_code}' -X PUT --data-binary @<file>` to `uploadRequest.url` with every header in `uploadRequest.headers` sent verbatim. The signed URL dies after 60 seconds and anything but a 200 means the upload failed: prepare that file again. Keep the markdown each upload returns.

One comment per PR on the ticket, updated in place on a later run: look for it with `list_comments` by its first line, then `save_comment` with its `id` to replace it, or without one to create it:

```
Screenshots: envoyai-web#980 at <head sha, 12 characters> (example data)

**Load Details › Offers, a held offer**
Before:
<image markdown>
After:
<image markdown>

Not shown: <screen> (<why>)
```

Label every screenshot with where it is in the app. Leave out the Before line for a new screen and the Not shown line when everything was captured.

## Point the PR body at it

Read the body (`gh pr view <pr> --json body,url`). Replace its `## Screenshots` section (up to the next `## ` heading, or up to the attribution line) or, when it has none, add one just above the attribution line (else at the end):

```
## Screenshots

Screenshots of <the screens, in a few words> are on the Linear ticket: [<KEY>](<comment url>).
```

The comment URL is the one `save_comment` returns, else the issue URL plus `#comment-<first 8 characters of the comment id>`. Write it with `gh pr edit <pr> --body-file <file>`; nothing else in the body changes.

Without a Linear upload tool (a client whose Linear tools are read-only), keep the files, list their paths, and say the ticket and the body were not updated.

## Report

```
#<pr> screenshots — <n> posted on <KEY> (<comment url>)
- <label>: before + after | after only | not shown (<why>)
body: Screenshots section added | updated | "No visible change."
```
