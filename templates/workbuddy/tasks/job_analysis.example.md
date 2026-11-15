---
id: job_analysis
title: 岗位分析
trigger: 在工作台选中一条跟踪记录后
inputs:
  - record_id
  - company
  - role
  - salary
  - stage
  - status
  - source_refs
output_path: "{{output_root}}/analysis/{{record_id}}.md"
output_schema:
  - 这是什么岗（一句话定性 + 与哪段经历同构）
  - 匹配度（0-100 + 3 条可举证依据）
  - 要补的洞（2-3 个准备动作）
  - 自我介绍开口（1 句，只用事实源）
  - 反问（2 个具体问题）
  - 下一步（1 个最小启动动作）
prompt_ref: templates/prompts/job_analysis.example.md
enabled: true
---

# 任务指令

请基于以下记录做岗位分析，结果写入 `{{output_path}}`。

- 记录 ID：`{{record_id}}`
- 公司：`{{company}}`
- 岗位：`{{role}}`
- 薪资：`{{salary}}`
- 阶段 / 状态：`{{stage}}` / `{{status}}`
- 来源引用：`{{source_refs}}`
- 身份事实：由 `{{identity.source_ref}}` 读取，不在指令中内嵌

## 硬约束

- 只依据事实源与已给出的岗位信息；不得编造经历、年限、职级、薪资、业绩数字。
- 信息不足时直接写「信息不足，需看完整 JD」，不脑补。
- 不用无事实支撑的程度词与角色词。
- 输出仅用于本人私下判断，不自动发送给任何第三方。
