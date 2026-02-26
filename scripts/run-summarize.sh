#!/bin/bash
# Wrapper script for summarize-and-push phase.
# Can be called by OpenClaw cron or manually.
set -euo pipefail

cd /Users/lee/.openclaw/daily-digest

# Load .env file
set -a
source .env
set +a

# Load Azure OpenAI vars from bashrc
eval "$(grep '^export AZURE_OPENAI' ~/.bashrc 2>/dev/null || true)"

exec .venv/bin/python orchestrator.py --summarize-and-push 2>&1
