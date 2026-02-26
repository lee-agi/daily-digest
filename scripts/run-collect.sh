#!/bin/bash
# Wrapper script for launchd-triggered data collection.
# launchd does not inherit shell env vars, so we source them here.
set -euo pipefail

cd /Users/lee/.openclaw/daily-digest

# Load .env file
set -a
source .env
set +a

# Load Azure OpenAI vars from bashrc (needed for summarize phase)
# Extract only AZURE_OPENAI_* vars to avoid side effects
eval "$(grep '^export AZURE_OPENAI' ~/.bashrc 2>/dev/null || true)"

exec .venv/bin/python orchestrator.py --collect-only 2>&1
