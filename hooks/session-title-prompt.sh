#!/bin/bash
# UserPromptSubmit hook: `/review <target>` retitles the session to review: <#PR|branch>.
exec python3 "$(dirname "$0")/session_title.py" prompt
