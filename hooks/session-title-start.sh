#!/bin/bash
# SessionStart hook: title the session from the branch (dev: <ticket|#PR>) unless already named.
exec python3 "$(dirname "$0")/session_title.py" session-start
