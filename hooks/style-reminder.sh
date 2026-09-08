#!/bin/bash
# UserPromptSubmit hook: re-inject the communication-style rule every turn so it survives long sessions.
cat <<'JSON'
{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"Reply style (standing rule): first line is the answer, no TL;DR or other label in front of it. Max four sentences conversational, ten for a report. Plain English a smart non-coder could follow: no idempotent (say safe to run twice), atomic (all or nothing), deterministic, orthogonal, canonical, declarative, immutable, upstream/downstream, non-trivial, leverage, footprint; spell out acronyms. No headers or bullets in chat replies, no unrequested explanations. Peer engineer in Slack, not a report."},"suppressOutput":true}
JSON
