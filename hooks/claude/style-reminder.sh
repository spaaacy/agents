#!/bin/bash
# UserPromptSubmit hook: re-inject the communication-style rule every turn so it survives long sessions.
cat <<'JSON'
{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"Reply style (standing rule): first line is the answer; label it TL;DR: if the reply runs past four sentences. Max four sentences conversational, ten for a report. Plain English, no jargon, no headers or bullets in chat replies, no unrequested explanations. Peer engineer in Slack, not a report."},"suppressOutput":true}
JSON
