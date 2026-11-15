# 辅助脚本（tools/）

| 脚本 | 用途 | 阶段 |
|---|---|---|
| `verify_template.example.py` | ① 导出白名单敏感串扫描 ② 历史文件 hash 基线与复核 | P0-A 起，每阶段运行 |
| `build_ledger_index.example.py` | 可选：把投递台账索引导出为快照（主实现在服务端内存聚合） | P1 |
| `propose_conditions.example.py` | 可选：从调研快照产出条件草案（只写本地产物目录） | P1 |

## 用法

```bash
# 敏感串扫描（词表默认读 config/local/sensitive_patterns.json）
python tools/verify_template.example.py sensitive

# 采集历史文件 hash 基线
python tools/verify_template.example.py hash-out workbuddy_out/verify/hash-baseline.json

# 每阶段复核
python tools/verify_template.example.py hash-check workbuddy_out/verify/hash-baseline.json

# 离线复核投递台账索引（与 GET /api/ledger-index 同口径；默认只读、只打印）
python tools/build_ledger_index.example.py
python tools/build_ledger_index.example.py --compare service.json
python tools/build_ledger_index.example.py --out workbuddy_out/ledger_index/index.json

# 从调研快照产出条件草案（默认只读；多快照时 --survey 必须显式指定）
python tools/propose_conditions.example.py --survey <SNAPSHOT_DIR>
python tools/propose_conditions.example.py --survey <SNAPSHOT_DIR> --out
```

## 设计约定

- 脚本本身零依赖、零敏感串，可进通用模板仓库。
- 敏感词表放本地实例目录（gitignored），脚本只读、不内嵌实例值。
- hash 基线排除「允许改动清单」，报告新增 / 缺失 / 变更，由人工核对。
- 所有脚本默认只读；只有显式 `hash-out` 写基线文件。
