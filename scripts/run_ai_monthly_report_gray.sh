#!/bin/bash
set -euo pipefail
# 保留一个 v1 回退 / 对照入口；是否发微信由上层调度决定。
export AI_MONTHLY_MODE=v1
exec /bin/bash /Users/lee/.openclaw/daily-digest/scripts/run_ai_periodic_report.sh monthly
