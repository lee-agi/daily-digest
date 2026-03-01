#!/bin/bash
# Unified wrapper for orchestrator.py.
# Usage: run.sh [--collect-only | --summarize-and-push | --full] [extra args...]
# Default: --full
set -euo pipefail

cd /Users/lee/.openclaw/daily-digest

# Load .env file
set -a
source .env
set +a

# Load secrets (API keys, tokens)
source ~/.secrets 2>/dev/null || true

# Load Azure OpenAI vars from bashrc
eval "$(grep '^export AZURE_OPENAI' ~/.bashrc 2>/dev/null || true)"

MODE="${1:---full}"
shift 2>/dev/null || true
exec .venv/bin/python orchestrator.py "$MODE" "$@" 2>&1
