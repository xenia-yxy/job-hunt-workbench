# data —— 台账数据

`data/ledger.json` 是整个系统的**唯一真相**：页面上的增删改查都写它，两份 CSV 是每次写盘后自动重算的派生文件。

- 首次 `python tools/setup.py` 会建一个空台账（按 `channels.json` 的渠道建空数组）。
- 备份：每次写入前把上一版复制到 `ledger.backup.json`；页面「↺ 恢复上次备份」用它。
- 想清空重来：删掉整个 `data/` 再跑一次 setup 即可，不会影响代码与文档。
- 这两份 CSV 用 Excel 打开也不影响写盘——CSV 只是派生品，写失败只在界面提示，台账数据照常落盘。

`.gitignore` 已排除本目录，不会把你的求职记录提交上去。
