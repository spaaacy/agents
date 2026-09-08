#!/bin/bash
# UserPromptSubmit hook: read the voice and toggle state from the shared repo.
exec python3 "$(dirname "$0")/reminder.py" 'Voice (default on)'
