# 规则数据（rules/）

与 `config/` 的分工：`config/` 放**运行参数**（端口、渠道、文件名、接口位），`rules/` 放**判定规则**（词表、阈值、权重、分级、文案）。

## 分层

- `rules/local/*.json` — 工作区实例规则，gitignored，不进通用模板仓库。
- default instance — 项目内现有的规则文件与代码常量。
- `rules/*.example.json` — 通用模板骨架：**结构完整、可直接运行的中性默认值**，零敏感串。

加载优先级：`local > example`。模板仓库只有 example 层也能跑起来，但得到的是中性默认判定，
需要使用者把自己的词表、分级文案与权重填进 `local/`。

## 状态（P2 已完成，不再是骨架）

| 文件 | 内容 | 实例层 |
|---|---|---|
| `prob.example.json` / `local/prob.json` | 概率引擎的词表、权重、阈值、clamp、折扣、7 级分级、状态集合、全部文案模板 | `local` 从原 `prob.js` 常量**逐值等价**迁入 |
| `cleanup.example.json` / `local/cleanup.json` | 清理阈值、状态/阶段/备注集合、6 项权重、多消息阈值、返回文案 | `local` 从原 `cleanup.py` 常量逐值等价迁入 |
| `job_family.example.json` / `local/job_family.json` | 岗位族关键词规则、preferred / low、fallback | `local` 从现有岗位族规则文件复制 |

`prob.js` 与 `cleanup.py` 现在只保留**引擎逻辑**，不含实例值：
- `prob.js` 是 UMD 工厂 `factory(rules, channels)`；Node 端由 `server.js` 传规则与渠道，浏览器端由 `build.js` 注入 `window.__WB_CONFIG__`。
- 文案通过 `{{placeholder}}` 模板变量插值；正则统一按 `regex_flags` 编译。
- 渠道加分与文案**不在本目录**，由 `config/*channels.json` 的 `prob` 段单一提供。

## 逐值等价验证（P2 断点）

```bash
# 概率：对全量主表记录跑 apply，与改前基线逐字段比对
node -e "..."   # 见 workbuddy_out/verify/verify_p2.py

# 清理：dry-run 全文与改前基线逐字比对
python cleanup.py --no-http

# 只改分值即可生效，且改回完全还原
# levels.reply.add: 20 -> 99 -> 20
```

实测结果：概率 21 条记录 diff = 0；清理 dry-run 全文一致；副本 PUT 后两份 CSV 与基线逐字一致。

## 边界

- 概率/清理规则可以配置化，但**安全阀默认值**（限速、冷却、日上限、熔断）属于平台安全边界，保留在内核且不可被条件文件绕过。
- 平台适配器里的选择器、按钮文案、城市码表属于平台知识，留在其平台目录，不搬到本目录。
- 规则改动只影响判定值；台账 append-only、主表写盘流程、备份与原子写一行未动。
