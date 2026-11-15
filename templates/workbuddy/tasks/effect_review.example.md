---
id: effect_review
title: 效果复盘与建议清单
trigger: 手动触发（如每周一次）
inputs:
  - period
  - channel_keys
  - source_refs
  - status_summary
output_path: "{{output_root}}/effect_review/{{period}}.md"
output_schema:
  - 各渠道 / 各岗位方向 / 各简历版本的投递与回复概况
  - 哪些条件带来的后续机会更多（只陈述可观察到的事实）
  - 下一期搜索词与筛选条件的**建议清单**（不自动生效）
  - 数据缺口与证据不足声明
prompt_ref: templates/prompts/job_analysis.example.md
enabled: true
---

# 任务指令

请基于工作台数据做一次效果复盘，结果写入 `{{output_path}}`。

- 周期：`{{period}}`
- 渠道范围：`{{channel_keys}}`
- 状态汇总：`{{status_summary}}`
- 来源引用：`{{source_refs}}`

## 硬约束

- 结论必须能下钻到原始记录；读不到就标「证据缺失」，不推测。
- 输出是**建议清单**，不得自动修改任何条件或历史记录。
- 不把平台广告量当作市场空缺总量，不把活跃度混进匹配度。
