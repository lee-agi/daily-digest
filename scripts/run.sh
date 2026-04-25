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
# --no-enrich by default: main pipeline runs fast without enrichment blocking.
# Run enrichment separately: python orchestrator.py --enrich-only
.venv/bin/python orchestrator.py "$MODE" --no-enrich "$@" 2>&1

if [ "$MODE" = "--collect-only" ] && [ "${AI_DIGEST_COLLECT_NOTIFY:-0}" = "1" ]; then
  MSG="AI 早报采集已完成\n下一步：07:00 自动总结与推送"
  /bin/bash /Users/lee/.openclaw/workspace/scripts/send_weixin_message.sh "$MSG"
fi
