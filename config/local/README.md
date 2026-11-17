# config/local —— 你的实例配置

这个目录是**本地私有**的，`.gitignore` 已排除，不会进 git。

首次使用运行一次：

```bash
python tools/setup.py
```

它会把 `config/*.example.json` 复制成这里的同名文件（已存在则保留，不覆盖）。

| 文件 | 作用 | 什么时候必须改 |
|---|---|---|
| `app.json` | 端口、数据文件名、页面路由、构建目录、只读数据源 | 想换端口或改文件名时 |
| `channels.json` | 投递渠道（校招 / BOSS / 智联 …）、CSV 表头、渠道概率加分 | 用自己的渠道清单时 |
| `identity.json` | 我是谁：姓名、届别、学校、城市、招呼语、技术栈 | 首次使用，必改 |
| `routes.json` | 工作台侧栏导航与每段主视图 | 想调整流程结构时 |
| `workbuddy.json` | 任务指令的接口位与输出目录 | 一般不用改 |
| `profile.json` | 画像（由页面「保存画像」写入，setup 不生成） | 页面里填 |
| `conditions.json` | 投递条件索引 | 用投递功能时 |
| `resume_versions.json` | 简历版本 → 文件映射 | 有简历产物时 |
| `inbox_bridge.json` / `pipe_bridge.json` | 本机管道令牌与端口 | 用管道时才需要 |

原则：`config/local` 里的值优先于 `*.example.json`；代码里没有任何默认文件名，缺项会直接报错退出，而不是悄悄用错文件。
