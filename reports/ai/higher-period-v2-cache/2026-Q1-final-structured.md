# AI 早报季报｜2026-Q1

- 覆盖周期：2026-01-01 ~ 2026-03-31
- 纳入月报数：3
- 覆盖月份：2026-01 / 2026-02 / 2026-03

## 本期结论
- 1 月数据缺失凸显监测链路问题，2–3 月虽恢复产出但需警惕趋势断层与偏差。
- 工具链与开发体验持续加速：从 2 月的 CLI/组件补丁到 3 月的代理/工作流生态爆发。
- “本地化 + 轻量多模态 + 效率/成本”在 2 月成主线，3 月延展为企业级工具化落地。
- AI 从辅助工具向内容/分析服务迁移，3 月进一步体现为企业产品与代理化能力增强。
- 平台治理与内容真实性在 3 月显著升温，成为影响生态演化的新变量。

## 月度切片与代表案例
### 2026-01
- 月度结论：本月暂无足够数据。
- 月报：/Users/lee/.openclaw/daily-digest/reports/ai/monthly-v2/ai-monthly-v2-2026-01.md

### 2026-02
- 月度结论：llama.cpp 发布更新，支持 Hugging Face 模型元数据加载与增量下载缓存修复。
- [github] [Release] ggml-org/llama.cpp b8180
  - 摘要：<details open> tests : model metadata loading from huggingface (#19796) * Add model metadata loading from huggingface for use with other tests * Add incremental
  - 原文：https://github.com/ggml-org/llama.cpp/releases/tag/b8180
- [producthunt] theORQL
  - 摘要：theORQL is vision-enabled frontend AI. It takes UI screenshots, maps UI → code, triggers real browser interactions, and visually verifies the fix in Chrome befo
  - 原文：https://www.producthunt.com/products/stop-coding-blind-ai-that-sees-the-ui?utm_campaign=producthunt-api&utm_medium=api-v2&utm_source=Application%3A+Lee+%28ID%3A+276949%29
- 月报：/Users/lee/.openclaw/daily-digest/reports/ai/monthly-v2/ai-monthly-v2-2026-02.md

### 2026-03
- 月度结论：ChatGPT 生态继续扩张，从高星提示库到 Langflow/LangChain 等代理工作流平台，社区工具化趋势明显。
- [github] f/prompts.chat: f.k.a. Awesome ChatGPT Prompts. Share, discover, and collect prompts from the community. Free and open source — self-host for your organization with complete privacy.
  - 摘要：f.k.a. Awesome ChatGPT Prompts. Share, discover, and collect prompts from the community. Free and open source — self-host for your organization with complete pr
  - 原文：https://github.com/f/prompts.chat
- [github] langflow-ai/langflow: Langflow is a powerful tool for building and deploying AI-powered agents and workflows.
  - 摘要：Langflow is a powerful tool for building and deploying AI-powered agents and workflows.
  - 原文：https://github.com/langflow-ai/langflow
- 月报：/Users/lee/.openclaw/daily-digest/reports/ai/monthly-v2/ai-monthly-v2-2026-03.md

## 建议精读（原文入口）
- [2026-02] [github] [Release] ggml-org/llama.cpp b8180
  - 提示：<details open> tests : model metadata loading from huggingface (#19796) * Add model metadata loading from huggingface for use with other tests * Add incremental
  - 原文：https://github.com/ggml-org/llama.cpp/releases/tag/b8180
- [2026-02] [producthunt] theORQL
  - 提示：theORQL is vision-enabled frontend AI. It takes UI screenshots, maps UI → code, triggers real browser interactions, and visually verifies the fix in Chrome befo
  - 原文：https://www.producthunt.com/products/stop-coding-blind-ai-that-sees-the-ui?utm_campaign=producthunt-api&utm_medium=api-v2&utm_source=Application%3A+Lee+%28ID%3A+276949%29
- [2026-02] [github] [Release] google-gemini/gemini-cli v0.31.0
  - 提示：## What's Changed * Use ranged reads and limited searches and fuzzy editing improvements by @gundermanc in https://github.com/google-gemini/gemini-cli/pull/1924
  - 原文：https://github.com/google-gemini/gemini-cli/releases/tag/v0.31.0
- [2026-02] [github] [Release] google-gemini/gemini-cli v0.32.0-preview.0
  - 提示：## What's Changed * feat(plan): add integration tests for plan mode by @Adib234 in https://github.com/google-gemini/gemini-cli/pull/20214 * fix(acp): update aut
  - 原文：https://github.com/google-gemini/gemini-cli/releases/tag/v0.32.0-preview.0
- [2026-02] [zhihu] 为什么几个朋友自从被字节腾讯阿里裁员以后再也不说话了？
  - 提示：何止被裁员后不说话，若后续长期找不到工作，无法重回巅峰，渐渐都可能会从社交圈中消失。 不仅是裁员，脱发/皮肤病/意外残疾/脑梗/心梗/离婚/衰老/亲人生离死别，任何一场无声的「人生剥夺与失去」，都可能让一个曾在朋友圈中谈笑人生，激扬文字的人渐渐变得沉默。很多没体验过「失去」的人未曾意识到：一个人「能参与讨论」某类话题的
  - 原文：https://www.zhihu.com/question/659273262/answer/1951329419084953387
- [2026-02] [zhihu] 为什么要调查李铁?
  - 提示：李铁操作太骚了。 你妈给你200，让你去超市买酱油。你花20买一桶酱油，然后自己贪下来180。看你拿着20的酱油回家，你妈若有所思，但是没说你什么。 另一种情况，你妈给你200，让你买酱油，你说我腿脚不好，没有那个能力，买不了。你妈骂了你几句。找别人去了。 铁子是怎么操作的呢 你妈给你200让你去超市买酱油。 铁子自己
  - 原文：https://www.zhihu.com/question/568964835/answer/2006418377237869294
- [2026-02] [reddit] Is Qwen3.5 a coding game changer for anyone else?
  - 提示：I've been playing with local LLMs for nearly 2 years on a rig with 3 older GPUs and 44 GB total VRAM, starting with Ollama, but recently using llama.cpp. I've u
  - 原文：https://reddit.com/r/LocalLLaMA/comments/1rgtxry/is_qwen35_a_coding_game_changer_for_anyone_else/
- [2026-02] [reddit] Wow.
  - 提示：I hope everyone moves to Claude after this news. ✌️
  - 原文：https://reddit.com/r/OpenAI/comments/1rgxpsd/wow/
- [2026-03] [github] f/prompts.chat: f.k.a. Awesome ChatGPT Prompts. Share, discover, and collect prompts from the community. Free and open source — self-host for your organization with complete privacy.
  - 提示：f.k.a. Awesome ChatGPT Prompts. Share, discover, and collect prompts from the community. Free and open source — self-host for your organization with complete pr
  - 原文：https://github.com/f/prompts.chat
- [2026-03] [github] langflow-ai/langflow: Langflow is a powerful tool for building and deploying AI-powered agents and workflows.
  - 提示：Langflow is a powerful tool for building and deploying AI-powered agents and workflows.
  - 原文：https://github.com/langflow-ai/langflow

## 后续观察
- 修复数据采集链路与多源补充，建立异常月告警，确保趋势可比性。
- 跟踪工具链（CLI/代理/工作流）对企业场景的实际增益与治理风险。
- 监测平台治理与内容真实性政策变化，对生态分发与商业化影响进行评估。
