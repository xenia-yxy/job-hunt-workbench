---
id: interview_prep
title: 面试准备
trigger: 记录进入「面试 / 测评」阶段时
inputs:
  - record_id
  - company
  - role
  - job_family
  - source_refs
output_path: "{{output_root}}/interview_prep/{{record_id}}.md"
output_schema:
  - 岗位定性（这个岗到底要什么能力）
  - 可举证经历映射（事实源行 → JD 要求）
  - 口述稿（1 分钟 / 3 分钟各一版）
  - 高频追问与应答要点
  - 实战场景补强清单
  - 反问清单
prompt_ref: templates/prompts/job_analysis.example.md
enabled: true
---

# 任务指令

请为以下岗位生成面试准备材料，结果写入 `{{output_path}}`。

- 记录 ID：`{{record_id}}`
- 公司 / 岗位：`{{company}}` / `{{role}}`
- 岗位分类：`{{job_family}}`
- 来源引用：`{{source_refs}}`（若有调研快照，用于对齐岗位真实要求）

## 硬约束

- 口述稿只用事实源里可举证的内容。
- 缺口写成「待补」，不粉饰成已有经历。
- 只读分析，不触发任何平台动作。
