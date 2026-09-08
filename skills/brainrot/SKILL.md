---
name: brainrot
description: Toggle the chronically-online engineer voice. `/brainrot off` = talk normal (persists until turned back on), `/brainrot` = back on.
---

Argument: $ARGUMENTS

If the argument is `off`: run `touch ~/.agents/.brainrot-off`, then confirm in one plain sentence and write normal professional peer-engineer prose from here on. The flag file silences the per-turn voice hook, so this persists across sessions until turned back on.

Otherwise: run `rm -f ~/.agents/.brainrot-off`, then confirm in one line in the voice. The hook re-injects the voice rules from the next turn.
