# AI 早报月报｜2026-02

- 覆盖周期：2026-02-01 ~ 2026-02-28
- 原始条目数：40
- 代表条目数：40
- 主要来源：github 16 / reddit 7 / zhihu 7 / cn_tech_blog 6 / producthunt 2 / x_twitter 1

## 本期结论
- llama.cpp 发布更新，支持 Hugging Face 模型元数据加载与增量下载缓存修复。
- 视觉前端修复工具 theORQL 推出，强调“截图→代码→浏览器验证→可审 diff”的闭环。
- Google gemini-cli 多版本迭代，涉及 plan 模式测试、认证握手与编辑体验改进。
- Gradio 组件持续更新，新增 HTML 上传与 Gallery 样式修复。
- 社区讨论聚焦本地模型实践与对 OpenAI 的情绪波动，同时有轻量化扩散模型教学项目。

## 重点主题与案例
### fix（2 条，2 个来源）
- [github] [Release] ggml-org/llama.cpp b8180
  - 摘要：<details open> tests : model metadata loading from huggingface (#19796) * Add model metadata loading from huggingface for use with other tests * Add i
  - 原文：https://github.com/ggml-org/llama.cpp/releases/tag/b8180
- [producthunt] theORQL
  - 摘要：theORQL is vision-enabled frontend AI. It takes UI screenshots, maps UI → code, triggers real browser interactions, and visually verifies the fix in C
  - 原文：https://www.producthunt.com/products/stop-coding-blind-ai-that-sees-the-ui?utm_campaign=producthunt-api&utm_medium=api-v2&utm_source=Application%3A+Lee+%28ID%3A+276949%29

### pull（10 条，1 个来源）
- [github] [Release] google-gemini/gemini-cli v0.31.0
  - 摘要：## What's Changed * Use ranged reads and limited searches and fuzzy editing improvements by @gundermanc in https://github.com/google-gemini/gemini-cli
  - 原文：https://github.com/google-gemini/gemini-cli/releases/tag/v0.31.0
- [github] [Release] google-gemini/gemini-cli v0.32.0-preview.0
  - 摘要：## What's Changed * feat(plan): add integration tests for plan mode by @Adib234 in https://github.com/google-gemini/gemini-cli/pull/20214 * fix(acp): 
  - 原文：https://github.com/google-gemini/gemini-cli/releases/tag/v0.32.0-preview.0

### zhihu（7 条，1 个来源）
- [zhihu] 为什么几个朋友自从被字节腾讯阿里裁员以后再也不说话了？
  - 摘要：何止被裁员后不说话，若后续长期找不到工作，无法重回巅峰，渐渐都可能会从社交圈中消失。 不仅是裁员，脱发/皮肤病/意外残疾/脑梗/心梗/离婚/衰老/亲人生离死别，任何一场无声的「人生剥夺与失去」，都可能让一个曾在朋友圈中谈笑人生，激扬文字的人渐渐变得沉默。很多没体验过「失去」的人未曾意识到：一个人「能
  - 原文：https://www.zhihu.com/question/659273262/answer/1951329419084953387
- [zhihu] 为什么要调查李铁?
  - 摘要：李铁操作太骚了。 你妈给你200，让你去超市买酱油。你花20买一桶酱油，然后自己贪下来180。看你拿着20的酱油回家，你妈若有所思，但是没说你什么。 另一种情况，你妈给你200，让你买酱油，你说我腿脚不好，没有那个能力，买不了。你妈骂了你几句。找别人去了。 铁子是怎么操作的呢 你妈给你200让你去超
  - 原文：https://www.zhihu.com/question/568964835/answer/2006418377237869294

### reddit（7 条，1 个来源）
- [reddit] Is Qwen3.5 a coding game changer for anyone else?
  - 摘要：I've been playing with local LLMs for nearly 2 years on a rig with 3 older GPUs and 44 GB total VRAM, starting with Ollama, but recently using llama.c
  - 原文：https://reddit.com/r/LocalLLaMA/comments/1rgtxry/is_qwen35_a_coding_game_changer_for_anyone_else/
- [reddit] Wow.
  - 摘要：I hope everyone moves to Claude after this news. ✌️
  - 原文：https://reddit.com/r/OpenAI/comments/1rgxpsd/wow/

### blog（6 条，1 个来源）
- [cn_tech_blog] 量子位编辑作者招聘
  - 摘要：<img src="https://mmbiz.qpic.cn/sz_mmbiz_jpg/A6fTew8FFGGgwlh0QoCvUY6MIMesKX8sf2Zzu0qahSx9NLDadNH9VKIoMCa6sGP2hlmzicIeGzzaxFke1PicACyzySWtHNe8N6ekxiaZP
  - 原文：http://mp.weixin.qq.com/s?__biz=MzIzNjc1NzUzMw==&mid=2247871217&idx=4&sn=b96564d5c2984dd32dceb60143aee3af&chksm=e98ab7bb6c6f70f75ad7bc3c4af57d8ab8c6c63295b8294e1404af4d6cb501f42e73c5559b8d&scene=0&xtrack=1#rd
- [cn_tech_blog] 破解RL样本效率难题！让AI一次性提炼环境常识，后续零调用成本
  - 摘要：<img src="https://mmbiz.qpic.cn/mmbiz_jpg/YicUhk5aAGtCKW4hLiccx3aRZSHmgvZP8icSSvm9iaC7CzVLT7ZibBLblc4S8S0Nj6fmpTu0QC9RGP8mRAx6qurMryg/300?wxtype=jpeg&
  - 原文：http://mp.weixin.qq.com/s?__biz=MzIzNjc1NzUzMw==&mid=2247871217&idx=2&sn=bf8338e8efe059b27b8acae8247f763b&chksm=e9d15d7202ed32e77a6293ab3f88e857df1a7c21d8407ae6b33d7b697c10bd81816b79b3bc8c&scene=0&xtrack=1#rd

## 建议精读（原文入口）
- [github] [Release] ggml-org/llama.cpp b8180
  - 提示：<details open> tests : model metadata loading from huggingface (#19796) * Add model metadata loading from huggingface fo
  - 原文：https://github.com/ggml-org/llama.cpp/releases/tag/b8180
- [producthunt] theORQL
  - 提示：theORQL is vision-enabled frontend AI. It takes UI screenshots, maps UI → code, triggers real browser interactions, and 
  - 原文：https://www.producthunt.com/products/stop-coding-blind-ai-that-sees-the-ui?utm_campaign=producthunt-api&utm_medium=api-v2&utm_source=Application%3A+Lee+%28ID%3A+276949%29
- [github] [Release] google-gemini/gemini-cli v0.31.0
  - 提示：## What's Changed * Use ranged reads and limited searches and fuzzy editing improvements by @gundermanc in https://githu
  - 原文：https://github.com/google-gemini/gemini-cli/releases/tag/v0.31.0
- [github] [Release] google-gemini/gemini-cli v0.32.0-preview.0
  - 提示：## What's Changed * feat(plan): add integration tests for plan mode by @Adib234 in https://github.com/google-gemini/gemi
  - 原文：https://github.com/google-gemini/gemini-cli/releases/tag/v0.32.0-preview.0
- [github] [Release] google-gemini/gemini-cli v0.33.0-nightly.20260228.1ca5c05d0
  - 提示：## What's Changed * docs(plan): update documentation regarding supporting editing of plan files during plan approval by 
  - 原文：https://github.com/google-gemini/gemini-cli/releases/tag/v0.33.0-nightly.20260228.1ca5c05d0
- [github] [Release] gradio-app/gradio gradio@6.8.0
  - 提示：### Features - [#12909](https://github.com/gradio-app/gradio/pull/12909) [`362fba6`](https://github.com/gradio-app/gradi
  - 原文：https://github.com/gradio-app/gradio/releases/tag/gradio%406.8.0
- [github] [Release] gradio-app/gradio @gradio/html@0.11.0
  - 提示：### Features - [#12909](https://github.com/gradio-app/gradio/pull/12909) [`362fba6`](https://github.com/gradio-app/gradi
  - 原文：https://github.com/gradio-app/gradio/releases/tag/%40gradio/html%400.11.0
- [github] [Release] gradio-app/gradio @gradio/gallery@0.17.2
  - 提示：### Fixes - [#12927](https://github.com/gradio-app/gradio/pull/12927) [`ca84f3e`](https://github.com/gradio-app/gradio/c
  - 原文：https://github.com/gradio-app/gradio/releases/tag/%40gradio/gallery%400.17.2

## 后续观察
- llama.cpp 与 gemini-cli 的后续版本是否带来更稳定的本地/CLI 生产力提升。
- 视觉驱动的 UI 自动修复工具在真实工程落地效果与安全性。
- 轻量扩散模型与本地模型在开发者圈的实际采用和生态扩展情况。
- 关注 MoE 专家分化与世界模型在实际任务中的落地效果与评测标准。
