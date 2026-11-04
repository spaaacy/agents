#!/bin/bash
# UserPromptSubmit hook: read the voice rules from the shared repo.
exec python3 "$(dirname "$0")/reminder.py" 'Voice'
