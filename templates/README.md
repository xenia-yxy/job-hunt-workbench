# 通用模板骨架（templates/）

本目录存放**可进通用模板仓库**的 example 骨架：值全部为 `<...>` 占位符或中性示例，要求零敏感串。

> 命名区分：根 `templates/` ≠ `jkit/templates/`。后者是平台脚本模板（含平台知识与实例值），**不进通用模板仓库**。

## 目录

| 路径 | 内容 |
|---|---|
| `accounts.example.json` | 账号池配置结构（无真实账号、无浏览器标识） |
| `conditions.example.json` | 投递条件空白模板（与 `config/conditions.example.json` 的契约一致） |
| `selectors.example.json` | 平台适配器结构说明；真实值留在各平台目录 |
| `resume_facts.example.md` | 简历事实源骨架（只记录可举证事实，禁止推断） |
| `prompts/*.example.md` | 提示词骨架，用 `{{...}}` 占位符 |
| `workbuddy/tasks/*.example.md` | WorkBuddy 任务指令模板（接口位） |

## WorkBuddy 接口位约定

机制固定、内容可换：

- `adapter`：本期只启用 `clipboard`；`command` / `webhook` / `mcp` 为预留扩展位，不实现。
- `output_root`：会话结果的相对输出根目录，页面只读列举与展示。
- `tasks_ref`：任务模板目录；`result_dirs`：结果子目录名到键的映射。
- 任务模板用 front matter 声明 `id / title / trigger / inputs / output_path / output_schema / prompt_ref / enabled`，
  正文用 `{{company}}` / `{{role}}` / `{{record_id}}` / `{{identity.name}}` 等占位符，由会话注入真实值。
- 模板本身**不含任何真实身份、学校、联系方式、招呼语、城市码、模型端点或密钥**。

## 只读契约（P1 已实现）

| 接口 | 返回 | 不返回 |
|---|---|---|
| `GET /api/workbuddy/tasks` | 模板文件名、大小、mtime 与 front matter 元数据 | 模板正文 |
| `GET /api/workbuddy/results` | `result_dirs` 下结果文件名、大小、mtime | 结果正文 |
| `GET /api/ledger-index` | 投递事件索引、批次统计、条件快照**路径字符串** | 条件快照内容、任何写操作 |

三个接口都幂等（响应不含当前时间）、零落盘、`Cache-Control: no-store`。

## 使用方式

1. 复制 `*.example.*` 到 `config/local/` 或 `rules/local/`，填入实例值。
2. 在工作台选中一条记录，点「复制任务指令」→ 在 WorkBuddy 会话中粘贴执行。
3. 会话把结果写入 `output_root` 下对应子目录，工作台只读展示。
4. 主表记录可通过可选 `source_refs[]`（`kind` = `workbuddy_result` / `resume_version` /
   `survey_snapshot` / `ledger_event`）引用结果，**绝不批量回填历史记录**。
