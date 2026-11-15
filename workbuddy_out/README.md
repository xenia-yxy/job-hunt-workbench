# workbuddy_out —— 外部 AI 会话的产物目录

工作台**不调用模型**。所有分析、话术、面试准备、复盘都在你自己的 AI 会话里跑，
结果以 Markdown 落到这里，工作台只做「列文件名 + 读 Markdown」，不解析、不改写。

```
workbuddy_out/
├── analysis/          岗位分析、方向判断
├── outreach/          招呼语、跟进话术
├── interview_prep/    面试准备
├── effect_review/     投递效果复盘
├── condition_drafts/  筛选条件草案（approved: false，人工审阅后才生效）
└── ledger_index/      台账索引导出
```

目录名与是否启用由 `config/local/workbuddy.json` 的 `result_dirs` 决定；
任务模板与输出路径由 `config/local/workbuddy.json` 的 `tasks_ref` 指向 `templates/workbuddy/tasks/*.md`。

本文件保留在仓库里是为了让目录结构可见；`workbuddy_out/` 下的实际产物已被 `.gitignore` 排除。
