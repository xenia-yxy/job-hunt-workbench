---
id: outreach
title: 消息回复草稿
trigger: 记录处于「待我回复」时
inputs:
  - record_id
  - company
  - role
  - hr_name
  - last_msg
  - source_refs
output_path: "{{output_root}}/outreach/{{record_id}}.md"
output_schema:
  - 对方在问什么（逐条拆问题）
  - 建议回复（纯文本，可直接复制）
  - 每条回复的事实依据（指向事实源行）
  - 风险提示（薪资 / 承诺 / 主观陈述需人工确认）
prompt_ref: templates/prompts/job_analysis.example.md
enabled: true
---

# 任务指令

请为以下会话生成回复草稿，结果写入 `{{output_path}}`，**只出草稿、不代发**。

- 记录 ID：`{{record_id}}`
- 公司 / 岗位：`{{company}}` / `{{role}}`
- 对方：`{{hr_name}}`
- 最后消息：`{{last_msg}}`
- 来源引用：`{{source_refs}}`

## 硬约束

- 对外文本只能取自事实源；禁止编造联系方式、经历、数字。
- 涉及薪资、时间承诺、主观意愿的措辞必须标注「需人工确认」。
- 不调用任何平台接口、不发送消息。
