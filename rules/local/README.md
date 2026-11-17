# rules/local —— 你的实例规则

`.gitignore` 已排除，不进 git。首次使用 `python tools/setup.py` 会从 `rules/*.example.json` 复制到这里。

| 文件 | 作用 |
|---|---|
| `prob.json` | 录取概率规则：岗位词表、权重、分级文案、进度修正 |
| `job_family.json` | 岗位族分类规则（server.js 与 cleanup.py 共用同一份） |
| `cleanup.json` | 台账清理口径：什么算「有沟通有后续」，分值权重 |

改这里等于改判断口径，改完刷新页面即可生效（服务端每次请求重新读盘，不缓存）。
