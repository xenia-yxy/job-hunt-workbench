---
id: track_sync
title: 抓取今日跟踪动态（只读）
trigger: 在「跟踪与回复」页点「抓取今日动态」后，从任务总线取件执行
inputs:
  - channels
  - note
output_path: "{{output_root}}/track/今日动态-{{job_id}}.md"
output_schema:
  - 今日有动静的记录：公司 / 岗位 / 渠道 / 阶段 / 最后消息 / 时间 / 动态类型 / 建议动作
  - 动态分类：面试邀请 / 等我回复 / 双方沟通 / 新消息 / 无变化
  - 数据边界声明：哪些来自主表快照、哪些现场抓到、哪些渠道登录失效没抓到
prompt_ref: templates/prompts/job_analysis.example.md
enabled: true
---

# 任务指令

请在 WorkBuddy 会话中执行本任务。**只读抓取、只展示、不改台账、不发任何消息**，结果回写两条：

1. 人话报告写入 `{{output_path}}`；
2. 结构化摘要通过 `POST http://127.0.0.1:8787/api/job/{{job_id}}/result` 回写任务总线，
   body 为：

```json
{
  "ok": true,
  "summary": "今天 5 条有动静：2 个面试邀请、1 条等我回复",
  "artifacts": ["{{output_path}}"],
  "data": {
    "schema": "track-daily/v1",
    "captured_at": "2026-11-16T15:00:00+08:00",
    "channels": ["boss"],
    "daily": [
      {
        "company": "示例公司",
        "role": "岗位名",
        "channel": "boss",
        "stage": "面试",
        "kind": "interview",
        "last_msg": "约周五面试",
        "at": "2026-11-16 10:30",
        "advice": "按备战六工序准备",
        "source": "现场抓取"
      }
    ],
    "notes": ["智联登录失效，本轮未抓"]
  }
}
```

失败时回写 `{"ok":false,"error":"原因"}`。

- 任务 ID：`{{job_id}}`
- 抓取渠道：`{{channels}}`（boss / zhilian；只抓这里列出的）
- 用户补充：`{{note}}`（可为空）

## 取数（严格只读，绝不投递 / 沟通 / 回复 / 收藏）

- **主表快照**：`GET /api/data`，字段 `campus` / `zhilian` / `boss` / `boss2`，每条含 `company` / `role` / `stage` / `last_msg` / `messages[]` / `captured_at`。先按本地时间过滤出"今天"有更新的记录（今天=运行当天本地时区）。
- **现场抓取**（可选，登录态可用时）：复用只读采集器，不写回主表：
  - BOSS：`python runs/cleanup/fetch_kept_chats.py` 里的 `Driver` + `CHAT_URL` / `LIST_JS` / `DETAIL_JS` 只读取消息列表与会话详情；登录失效就跳过，不重试登录。
  - 智联：`jkit/zhilian.py` 的卡片只读接口，无 JD 全文。
- 现场抓到的动态与主表快照合并：同一家公司以现场为最新，标注 `source:现场抓取`；只在主表出现的标 `source:主表快照`。
- 动态分类 `kind` 取：`interview`（面试/测评邀请）/ `reply`（等我回复）/ `talk`（双方沟通）/ `new`（仅新消息）/ `none`（无变化，通常不列入 daily）。

## 硬约束

- **不改台账**：不写 `unified_data.json`、不动 CSV、不调用 `PUT /api/data`；本任务只在报告与回写里给"建议更新哪些记录"，由用户在页面确认后自行处理。
- **不发消息**：不点发送、不回招呼、不投递、不收藏；全程只读 DOM。
- **不编造**：公司、岗位、时间、消息一律来自接口或现场 DOM；读不到就写"信息不足"，不推测面试时间或薪资。
- **登录失效即跳过**：某个渠道进不去就记入 `notes`，不反复尝试、不触发登录流程。
- 岗位名只来自会话抬头 / 投递记录 / 邮件，拿不到写「岗位待确认」，绝不编造。
- 只用于本人私下跟踪；不自动发送给任何第三方。
