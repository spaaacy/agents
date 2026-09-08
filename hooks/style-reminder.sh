#!/bin/bash
# UserPromptSubmit hook: re-inject the communication-style rule every turn so it survives long sessions.
cat <<'JSON'
{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"Reply style (standing rule): first line is the answer, no TL;DR or other label in front of it. Max four sentences conversational, ten for a report. Plain English, no jargon, no headers or bullets in chat replies, no unrequested explanations. Peer engineer in Slack, not a report."},"suppressOutput":true}
JSON
