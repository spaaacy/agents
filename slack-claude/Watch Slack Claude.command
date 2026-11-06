#!/bin/zsh
printf '\033]0;Slack → Claude\007'
exec /Users/aakifmohamed/.agents/slack-claude/.venv/bin/python -u /Users/aakifmohamed/.agents/slack-claude/watch.py
