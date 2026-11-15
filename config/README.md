# 配置分层（config/）

本目录是**用户实例配置**与**通用模板骨架**的分界点。

## 三层加载优先级

1. `config/local/<name>.json` — 工作区实例值，**整目录 gitignored**，不进通用模板仓库。
2. default instance — 项目内已存在的实例数据/常量（岗位族规则文件、既有数据文件的实际顶层键、投递条件的默认口径）。
3. `config/<name>.example.json` — 通用模板骨架，值全部为 `<...>` 占位符或中性默认值，**要求零敏感串**。

规则数据同理：`rules/local/` > default instance > `rules/*.example.json`。
加载函数统一为 `loadLocal()` / `loadRule()`，找不到时返回 `null`，由调用方决定是降级还是报错。

## 缺配置的行为（P2 已实现）

- 关键配置（channels / app / routes / identity）缺失 → `node server.js` 打印可读清单并退出非 0，不静默启动、不占用端口。
- 可选规则缺失 → 回退 `*.example.json` 的中性默认值并打印 WARN。
- 城市码等实例值**绝不静默兜底**：`jkit/conditions_loader.py` 在 `from_dict` 之前显式校验
  `city` / `city_code` / `queries` / `filters`，缺失即抛 `ConditionError`。

## 文件说明

| 文件 | 作用 |
|---|---|
| `channels.example.json` | **单一源**：渠道键、显示名、类型、CSV 列名与顺序、概率渠道加分。服务端、清理脚本、构建脚本、前端、概率引擎共用 |
| `app.example.json` | 端口、CORS、输出文件名、localStorage 前缀、静态缓存头；`sources` 是只读数据源，`artifact_scans` 是段内产物的自动发现规则 |
| `routes.example.json` | 工作台导航：`groups` 是首页 + 五段流程，`items` 每段一个 `role: "primary"` 主视图，其余产物写进它的 `variants`（全为项目根相对路径） |
| `workbuddy.example.json` | 任务指令接口位：adapter / output_root / tasks_ref / result_dirs |
| `identity.example.json` | 身份占位骨架；真实值只在 local 或由会话从事实源读取 |
| `conditions.example.json` | 投递条件六块结构骨架，城市码为占位符 |
| `resume_versions.example.json` | 简历版本索引骨架；只建索引，不移动或改写简历文件 |

## channels 的驱动范围（P2 收敛完成）

`config/local/channels.json` 一处改动，同时驱动：

- `server.js`：`KEYS` / `emptyData` / `readData` 顶层键兜底 / CSV 渠道名 / CSV 行顺序（`csv_order`） / 校招渠道判定 / `/api/config`。
- `cleanup.py`：遍历渠道集合与「声明键 ∪ 数据实际顶层键」兜底。
- `runs/20261114-app/build.js`：注入到 `window.__WB_CONFIG__.channels`。
- `runs/20261114-app/app.template.html`：筛选下拉、渠道标签、新增记录的类型与批次标签、CSV 导出。
- `prob.js`：渠道加分与文案（读每个渠道的 `prob` 段）。

`type` 取 `campus | platform | custom`，替代所有针对具体渠道键的特判。
`csv_order` 显式声明 CSV 行顺序，留空则按 `order` 排序。

## 命名区分（重要）

- `config/` ≠ `jkit/config.py`：后者是工作区定位逻辑，前者是用户实例配置。
- 根 `templates/` ≠ `jkit/templates/`：后者是平台脚本模板，前者是通用 example 骨架。
- 平台适配知识（选择器、按钮文案、城市码表、登录地址）留在平台目录，不整体上抽到本目录。
- `localStorage` 前缀由 `app.storage_namespace` 提供；历史键名保持不变，避免旧浏览器缓存失效。

## 迁移原则

现有实例值是**复制**进 `local/`，不是移动；原文件与历史数据一字不改。
条件变更一律新建 id / run，不回填历史快照。
