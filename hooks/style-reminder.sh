#!/bin/bash
# UserPromptSubmit hook: read the communication rules from the shared repo.
exec python3 "$(dirname "$0")/reminder.py" 'Communication style'
