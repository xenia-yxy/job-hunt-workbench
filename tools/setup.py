#!/usr/bin/env python3
"""一键初始化：把示例配置落到 config/local、建好数据文件、生成本地主应用页面。

设计原则（和整个项目一致）：
  · 不猜文件名——目标路径全部来自配置本身，配置读不到就直接退出，不设默认值
  · 不覆盖已有内容——config/local 里已有的文件一律保留，除非显式 --force
  · 幂等——重复执行只会补齐缺的东西，不会把用户数据写坏

用法：
    python tools/setup.py            # 首次初始化（保留已有的 local 配置与数据）
    python tools/setup.py --force    # 用示例覆盖 config/local 与 rules/local（不动数据）
    python tools/setup.py --demo     # 用仓库自带的演示配置与演示数据（适合先跑通看效果）

跑完直接 `node server.js`（或 npm start），浏览器打开 http://localhost:8787/workbench
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def die(msg: str) -> None:
    print("FAIL: " + msg, file=sys.stderr)
    raise SystemExit(2)


def read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def find_root() -> Path:
    for d in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
        if (d / "config").is_dir() and (d / "server.js").exists():
            return d
    die("找不到项目根（需要同时存在 config/ 与 server.js）")
    return ROOT  # 不可达，仅用于类型收敛


def collect_sources(use_demo: bool) -> list[tuple[Path, str, str]]:
    """返回 [(源文件, 目标子目录, 目标文件名), ...]

    config/demo/*.json 是「开箱即用」的中性配置；config/*.example.json 是结构示例。
    两者都按「去掉 .example / 保持原名」落到 config/local/。
    """
    out: list[tuple[Path, str, str]] = []
    demo_dir = ROOT / "config" / "demo"
    cfg_dir = ROOT / "config"
    rule_dir = ROOT / "rules"

    if use_demo and demo_dir.is_dir():
        for f in sorted(demo_dir.glob("*.json")):
            if f.name == "ledger.json":
                continue          # 演示台账是数据，不是配置，别复制到 config/local
            out.append((f, "config/local", f.name))
    else:
        for f in sorted(cfg_dir.glob("*.example.json")):
            out.append((f, "config/local", f.name.replace(".example.json", ".json")))

    for f in sorted(rule_dir.glob("*.example.json")):
        out.append((f, "rules/local", f.name.replace(".example.json", ".json")))
    return out


def copy_configs(pairs, force: bool) -> tuple[int, int]:
    copied = kept = 0
    for src, sub, name in pairs:
        dst = ROOT / sub / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and not force:
            kept += 1
            continue
        shutil.copyfile(src, dst)
        copied += 1
    return copied, kept


def load_local(name: str):
    for f in (ROOT / "config" / "local" / (name + ".json"),
              ROOT / "config" / (name + ".example.json")):
        if f.exists():
            v = read_json(f)
            if v is not None:
                return v
    return None


def ensure_dirs_and_data(app: dict, force: bool, demo: bool = False) -> tuple[bool, int]:
    files = app.get("files") or {}
    if not files.get("data"):
        die("配置缺少 files.data，无法确定数据文件位置")

    # 数据文件与其派生 CSV 的目录一并建好
    for key in ("data", "backup", "csv_main", "csv_campus"):
        rel = files.get(key)
        if rel:
            (ROOT / rel).parent.mkdir(parents=True, exist_ok=True)

    # 投递事件流目录
    runs = (app.get("ledger") or {}).get("runs_dir") or "runs"
    (ROOT / runs).mkdir(parents=True, exist_ok=True)

    # 页面内 AI 任务产物目录
    wb = load_local("workbuddy") or {}
    (ROOT / (wb.get("output_root") or "workbuddy_out")).mkdir(parents=True, exist_ok=True)

    data_path = ROOT / files["data"]
    # --demo：用仓库自带的演示台账（编造的记录），让页面一打开就有东西看
    demo_seed = ROOT / "config" / "demo" / "ledger.json"
    if demo and demo_seed.exists():
        shutil.copyfile(demo_seed, data_path)
        try:
            cur = json.loads(data_path.read_text(encoding="utf-8"))
            return True, sum(len(v) for v in cur.values() if isinstance(v, list))
        except Exception:
            return True, 0

    if data_path.exists() and not force:
        try:
            cur = json.loads(data_path.read_text(encoding="utf-8"))
            total = sum(len(v) for v in cur.values() if isinstance(v, list))
        except Exception:
            total = -1
        return False, total

    ch = load_local("channels") or {}
    keys = [c["key"] for c in (ch.get("channels") or []) if c.get("key") and c.get("enabled") is not False]
    if not keys:
        die("channels 配置为空：至少要有一个 enabled 渠道，否则数据文件没有表结构")
    data_path.write_text(
        json.dumps({k: [] for k in keys}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    return True, 0


def run_build(app: dict) -> None:
    build = app.get("build") or {}
    tpl_dir = build.get("template_dir")
    if not tpl_dir:
        print("SKIP: 配置里没有 build.template_dir，跳过程序页面生成")
        return
    script = ROOT / tpl_dir / "build.js"
    if not script.exists():
        print(f"SKIP: 找不到构建脚本 {script.relative_to(ROOT)}")
        return
    if shutil.which("node") is None:
        print("SKIP: 本机没有 node，跳过页面生成（装好 Node.js 后执行 npm run build）")
        return
    print("· 生成主应用页面 …")
    r = subprocess.run(["node", str(script)], cwd=str(ROOT))
    if r.returncode != 0:
        die("页面生成失败：" + str(script.relative_to(ROOT)))


def main() -> None:
    ap = argparse.ArgumentParser(description="一键初始化求职工作台")
    ap.add_argument("--force", action="store_true", help="用示例覆盖 config/local 与 rules/local（不动已有数据）")
    ap.add_argument("--demo", action="store_true", help="使用仓库自带的演示配置（config/demo）")
    args = ap.parse_args()

    root = find_root()
    if root != ROOT:
        pass  # 以找到的根为准，脚本放在哪一层都能跑

    print("项目根：" + str(root))
    pairs = collect_sources(args.demo)
    if not pairs:
        die("没有找到任何示例配置（config/*.example.json 或 config/demo/*.json）")

    copied, kept = copy_configs(pairs, args.force)
    print(f"· 配置：复制 {copied} 个，保留 {kept} 个（config/local、rules/local）")

    app = load_local("app")
    if not app:
        die("config/local/app.json 不存在或不是合法 JSON")

    created, total = ensure_dirs_and_data(app, args.force, args.demo)
    files = app.get("files") or {}
    if created and args.demo:
        print(f"· 数据：写入演示台账 {files.get('data')}（{total} 条编造记录，可直接改可删）")
    elif created:
        print(f"· 数据：新建空台账 {files.get('data')}")
    else:
        print(f"· 数据：沿用已有 {files.get('data')}（{total} 条记录）")

    run_build(app)

    port = app.get("port") or 8787
    print("")
    print("完成。下一步：")
    print("  启动服务：  node server.js        （或 npm start）")
    print("  打开工作台：http://localhost:%s/workbench" % port)
    print("  我的档案：  http://localhost:%s/工作台-档案.html" % port)
    print("  台账主应用：http://localhost:%s/app" % port)
    print("  跑自检：    npm test")
    if not args.demo:
        print("")
        print("提示：档案（年龄/学历/届别/城市/技能/期望薪资）先传一份简历自动带出来，")
        print("      或者打开「我的档案」手填；identity.json 里的占位身份只影响任务指令话术。")


if __name__ == "__main__":
    main()
