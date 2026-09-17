#!/bin/bash
# UserPromptSubmit hook: /review or /watch <target> retitles the session; a first ticket mention on a ticket-less branch sets dev: <ticket>.
exec python3 "$(dirname "$0")/session_title.py" prompt
