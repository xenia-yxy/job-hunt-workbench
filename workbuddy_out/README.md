# workbuddy_out —— 外部 AI 会话的产物目录

页面不自己调用大模型。需要分析时，页面给出一段「任务指令」，你把它贴到 WorkBuddy / 任意 AI 会话里执行，
结果写回这里的子目录，页面再读回来展示。

目录分工由 `config/local/workbuddy.json` 的 `result_dirs` 决定，默认：

| 目录 | 内容 |
|---|---|
| `analysis/` | 岗位族 / 方向分析结果 |
| `outreach/` | 招呼语、跟进话术 |
| `interview_prep/` | 面试准备 |
| `effect_review/` | 投递效果复盘 |
| `condition_drafts/` | 条件草稿 |
| `ledger_index/` | 投递台账索引 |

`.gitignore` 已排除本目录，产物不进 git。
