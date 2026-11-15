# config/local —— 工作区实例配置（不提交）

本目录已被 `.gitignore` 排除。它是**你自己的**配置，永远是私有的。

## 怎么开始

把上层的 `*.example.json` 复制成同名（去掉 `.example`）放进本目录，再按注释填值：

```bash
cd config
for f in *.example.json; do cp "$f" "local/${f%.example.json}.json"; done
```

必需的四份（缺任意一份，服务会直接退出非 0，这是刻意设计）：

| 文件 | 作用 |
|---|---|
| `app.json` | 端口、产物目录、数据源路径、扫描规则 |
| `routes.json` | 工作台侧栏与视图条（五段入口在这里定义） |
| `channels.json` | 求职渠道清单与顺序 |
| `identity.json` | 你自己的事实源（姓名、城市、技能、期望） |

## 为什么分开放

`*.example.json` 是**骨架**，会随仓库更新；`local/*.json` 是**你的值**，不会被覆盖也不会被提交。
两者不要混：直接改 example，下次拉取更新就会冲突。
