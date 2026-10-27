#!/bin/zsh
printf '\033]0;Slack → Codex Reviews\007'
exec /Users/aakifmohamed/.pyenv/versions/3.12.8/bin/python3 -u /Users/aakifmohamed/.agents/slack-pr-review/watch.py
