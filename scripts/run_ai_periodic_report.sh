#!/bin/bash
set -euo pipefail
cd /Users/lee/.openclaw/daily-digest
set -a
source .env
set +a
source ~/.secrets 2>/dev/null || true
PERIOD="${1:?period required}"
PY="/Users/lee/.openclaw/daily-digest/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="python3"
fi

COMPLETED_FLAG=""
if [ "$PERIOD" != "weekly" ]; then
  COMPLETED_FLAG="--completed-period"
fi

MODE="v1"
case "$PERIOD" in
  monthly)
    MODE="${AI_MONTHLY_MODE:-v2}"
    ;;
  quarterly)
    MODE="${AI_QUARTERLY_MODE:-v2}"
    ;;
  semiannual)
    MODE="${AI_SEMIANNUAL_MODE:-v2}"
    ;;
  annual)
    MODE="${AI_ANNUAL_MODE:-v2}"
    ;;
  weekly)
    MODE="v1"
    ;;
  *)
    MODE="v1"
    ;;
esac

if [ "$PERIOD" = "monthly" ] && [ "$MODE" = "v2" ]; then
  REPORT_PATH="$($PY /Users/lee/.openclaw/daily-digest/scripts/generate_monthly_digest_v2.py \
    --date "$(date +%F)" \
    $COMPLETED_FLAG \
    --max-items "${AI_MONTHLY_V2_MAX_ITEMS:-160}" \
    --max-clusters "${AI_MONTHLY_V2_MAX_CLUSTERS:-10}" \
    --batch-size "${AI_MONTHLY_V2_BATCH_SIZE:-4}" \
    --batch-timeout "${AI_MONTHLY_V2_BATCH_TIMEOUT:-150}" \
    --final-timeout "${AI_MONTHLY_V2_FINAL_TIMEOUT:-210}" \
    ${AI_MONTHLY_V2_NO_CACHE:-})"
elif [ "$MODE" = "v2" ] && [ "$PERIOD" != "weekly" ]; then
  REPORT_PATH="$($PY /Users/lee/.openclaw/daily-digest/scripts/generate_higher_period_digest_v2.py \
    --period "$PERIOD" \
    --date "$(date +%F)" \
    $COMPLETED_FLAG \
    --monthly-max-items "${AI_MONTHLY_V2_MAX_ITEMS:-160}" \
    --monthly-max-clusters "${AI_MONTHLY_V2_MAX_CLUSTERS:-10}" \
    --monthly-batch-size "${AI_MONTHLY_V2_BATCH_SIZE:-4}" \
    --batch-timeout "${AI_HIGHER_V2_BATCH_TIMEOUT:-150}" \
    --final-timeout "${AI_HIGHER_V2_FINAL_TIMEOUT:-210}" \
    --high-batch-size "${AI_HIGHER_V2_BATCH_SIZE:-3}" \
    ${AI_HIGHER_V2_NO_CACHE:-})"
else
  REPORT_PATH="$($PY /Users/lee/.openclaw/daily-digest/scripts/generate_periodic_digest.py --period "$PERIOD" --date "$(date +%F)" $COMPLETED_FLAG)"
fi
echo "$REPORT_PATH"
SUMMARY_LINE=$(REPORT_PATH="$REPORT_PATH" $PY - <<'PY'
import os
from pathlib import Path
p = Path(os.environ['REPORT_PATH'])
text = p.read_text(encoding='utf-8', errors='ignore').splitlines()
summary = ''
in_core = False
for line in text:
    s = line.strip()
    if s == '## 核心观察':
        in_core = True
        continue
    if in_core and s.startswith('## '):
        break
    if in_core and s.startswith('- '):
        summary = s[2:].strip()
        break
if not summary:
    for line in text:
        s = line.strip()
        if s.startswith('- '):
            summary = s[2:].strip()
            break
print(summary[:120])
PY
)
WX_TO=$($PY - <<'PY'
import yaml
from pathlib import Path
cfg = yaml.safe_load(Path('/Users/lee/.openclaw/daily-digest/config.yaml').read_text(encoding='utf-8'))
wx = (cfg.get('distribution') or {}).get('weixin') or {}
print(wx.get('to',''))
PY
)
WX_ACCOUNT=$($PY - <<'PY'
import yaml
from pathlib import Path
cfg = yaml.safe_load(Path('/Users/lee/.openclaw/daily-digest/config.yaml').read_text(encoding='utf-8'))
wx = (cfg.get('distribution') or {}).get('weixin') or {}
print(wx.get('account_id',''))
PY
)
if [ "${AI_PERIODIC_DISABLE_NOTIFY:-0}" = "1" ]; then
  exit 0
fi

if [ "${AI_PERIODIC_DISABLE_NOTIFY:-0}" != "1" ] && [ -n "$WX_TO" ] && [ -n "$WX_ACCOUNT" ]; then
  MSG="AI 早报${PERIOD}报告已生成：$(basename "$REPORT_PATH")"
  if [ -n "$SUMMARY_LINE" ]; then
    MSG="$MSG
核心提示：$SUMMARY_LINE"
  fi
  openclaw message send --json --channel openclaw-weixin --account "$WX_ACCOUNT" --target "$WX_TO" --message "$MSG" >/dev/null 2>&1 || true
fi
