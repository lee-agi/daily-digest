Re-run the daily digest pipeline: collect items from the past $N hours across all sources, generate an LLM summary report, and optionally push to RSS/Feishu.

## Arguments
- $N: Lookback hours (default: 24)
- $PUSH: Whether to push to RSS/Feishu — 0 = dry-run (no push), 1 = push (default: 0)

## Instructions

Before running, show the user the resolved CLI parameters in a concise block, for example:
```
📋 digest-run 参数:
  --lookback-hours  24        (N=$N)
  --dry-run         true      (PUSH=$PUSH → dry-run)
  --no-enrich       true      (default)
```

Then run the following command via Bash:

```bash
bash scripts/run.sh --full {{ARGS}} --lookback-hours {{N}}
```

Where:
- `{{N}}` = `$N` if provided, otherwise `24`
- `{{ARGS}}` = `--dry-run` if `$PUSH` is `0` or not provided; omit `--dry-run` if `$PUSH` is `1`

After the command completes:
1. Show a summary of the output: how many items collected, from which sources, report file path
2. If there were errors or warnings, highlight them
