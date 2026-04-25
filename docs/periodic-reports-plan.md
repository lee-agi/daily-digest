# AI 早报 / Futu 周期性报告规划

更新时间：2026-04-25

## 已确定需求

1. AI 早报每天推送后，在微信追加一条提醒消息。
2. AI 早报每周六上午生成周报，并附进一步调研建议。
3. AI 早报与 Futu 研究都需要扩展为：
   - 月报
   - 季报
   - 半年报
   - 年报

## 当前已落地

- AI 早报每日两段式调度：06:30 collect + 07:00 summarize-and-push
- AI 周报 v1 脚本：`scripts/generate_periodic_digest.py --period weekly`
- AI 周报调度候选：`launchd/com.openclaw.digest-weekly.plist`

## 后续建议的统一产物目录

- `~/.openclaw/daily-digest/reports/ai/weekly/`
- `~/.openclaw/daily-digest/reports/ai/monthly/`
- `~/.openclaw/daily-digest/reports/ai/quarterly/`
- `~/.openclaw/daily-digest/reports/ai/semiannual/`
- `~/.openclaw/daily-digest/reports/ai/annual/`
- `workspace/reports/futu/monthly/`
- `workspace/reports/futu/quarterly/`
- `workspace/reports/futu/semiannual/`
- `workspace/reports/futu/annual/`

## 推荐固定栏目

### AI 报告
- 核心趋势
- 本周期最值得读
- 值得继续调研
- 应降低关注的噪音主题
- 下周期观察名单

### Futu 报告
- 本周期判断复盘
- 仓位 / 预算 / 风险变化
- 有效与失效的判断
- 下周期配置建议
- 值得继续研究的标的 / 问题清单

## 实施顺序

### 第一阶段
- 每日 AI 早报推送后微信提醒
- AI 周报自动化

### 第二阶段
- AI 月报
- Futu 月报

### 第三阶段
- AI 季报 / 半年报 / 年报
- Futu 季报 / 半年报 / 年报
- 统一模板与调度框架
