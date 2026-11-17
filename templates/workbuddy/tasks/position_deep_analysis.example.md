---
id: position_deep_analysis
title: 方向深度分析（画像 × 市场 × 岗位想法）
trigger: 在「岗位判断与分析」页点「交给 WorkBuddy 深析」后，从任务总线取件执行
inputs:
  - scope
  - note
  - source_refs
output_path: "{{output_root}}/analysis/position-deep-{{job_id}}.md"
output_schema:
  - 一句话结论：当前最该投哪个方向，为什么
  - 每个在投方向：市场友好度 + 本人 must 命中 + 薪资达标 + 想法匹配，逐项给可举证依据
  - 求职想法的真实解读：用户写的想法里，哪些是硬约束、哪些是偏好、哪些与数据冲突
  - 缺口与补齐动作：每个方向 2–3 个可执行动作
  - 数据边界声明：哪些结论来自快照、哪些 JD 样本不足、哪些读不到
prompt_ref: templates/prompts/job_analysis.example.md
enabled: true
---

# 任务指令

请在 WorkBuddy 会话中执行本任务，结果回写两条：
1. 人话报告写入 `{{output_path}}`；
2. 结构化摘要通过 `POST http://127.0.0.1:8787/api/job/{{job_id}}/result` 回写任务总线，
   body 为 `{"ok":true,"summary":"一句话结论","artifacts":["{{output_path}}"],
   "data":{"schema":"analyze-deep/v1","families":[...],"note_read":"..."}}`。
   失败时回写 `{"ok":false,"error":"原因"}`。

- 任务 ID：`{{job_id}}`
- 分析范围：`{{scope}}`（active=只看在投方向，all=全部岗位族）
- 用户补充想法：`{{note}}`（可为空；为空则以画像里的 intent.raw 为准）

## 取数（只读，不采不投不回复）

- 画像：`GET /api/profile`（身份、技术栈、求职想法 intent.raw、薪资下限）
- 方向与市场：`GET /api/families`（族级市场友好度、门槛通过率、薪资、样本量）
- 基线打分：`GET /api/jobs?kind=position-analysis&limit=1`，取最近一次 done 的 `result.data`
- 门槛项 / 能力项 / JD 原文：`GET /api/atlas?family=<族 id>`（按需逐族取）
- 基线分析算法见 `jkit/analyze.py` 顶部注释（市场 35% + 技能 25% + 薪资 15% + 想法 25%）

## 硬约束

- 只依据上述只读接口与本地事实；不得编造经历、年限、薪资、业绩数字。
- 你的价值是**解读与判断**，不是重算分数：基线分照抄并注明来源，重点讲清"为什么"、想法的真实诉求、以及数据支撑不住的地方。
- JD 原文 ≤1 条的族必须声明"参考度低"，不拿少量样本当全貌。
- 市场友好度是族级快照，不是个人录用概率；单条岗位能不能投看投递段试跑，两者不同量纲。
- 输出仅用于本人私下判断，不自动发送给任何第三方；不触发投递、沟通、收藏等平台动作。
- 只读分析，不改任何条件 / 历史运行数据。
