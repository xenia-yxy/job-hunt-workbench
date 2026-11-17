"""jkit 命令行入口 —— 一个主命令：自动投递。

    apply       落地 → 先筛选 → 逐词搜索 → 逐卡判 → 投 → 留在此页（--dry-run 只判不投）
    conditions  校验 / 打印条件 JSON（外部生成的条件先在这里过一遍）
    platforms   看平台适配状态，或读当前页面已生效的筛选项（排障用）
    init        建工作区

没有 lane / scan / judge / submit / report：条件是一份 JSON，台账在 runs/ 下，
不需要一套目录结构和流水线去配合。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__, analyze as analyze_mod, apply as apply_mod, batch as batch_mod
from .conditions import ConditionError, Conditions
from .conditions_loader import load_conditions
from .config import find_workspace, now_iso, run_dir, workspace_config, write_json
from .driver import BskError, Driver
from .errors import JkitError
from .platforms import all_platforms, get as get_platform

TEMPLATES = Path(__file__).parent / "templates"


def _ws() -> Path:
    return find_workspace()


# ---------- init ----------

def cmd_init(args) -> int:
    ws = Path(args.dir or ".").expanduser().resolve()
    ws.mkdir(parents=True, exist_ok=True)

    created = []
    if not (ws / "jkit.toml").exists():
        (ws / "jkit.toml").write_text(
            "[jkit]\nversion = \"%s\"\ndefault_platform = \"boss\"\n" % __version__,
            encoding="utf-8")
        created.append("jkit.toml")

    for sub in ("runs", "conditions"):
        p = ws / sub
        if not p.exists():
            p.mkdir(parents=True)
            created.append(sub + "/")

    tpl = TEMPLATES / "conditions.json"
    if tpl.exists() and not (ws / "conditions" / "default.json").exists():
        shutil.copyfile(tpl, ws / "conditions" / "default.json")
        created.append("conditions/default.json（改这份，或另存一份）")

    acc = ws / "accounts.json"
    if not acc.exists():
        write_json(acc, {"accounts": [{
            "account_id": "main",
            "label": "主账号",
            "daily_limit": 150,
            "browser_binding": {"browser_id": None,
                                "note": "不写死；每次用 bsk browsers 取实例 id"},
            "status": "active",
        }]})
        created.append("accounts.json")

    gi = ws / ".gitignore"
    if not gi.exists():
        gi.write_text("runs/\n__pycache__/\n*.pyc\n.current\n", encoding="utf-8")
        created.append(".gitignore")

    print("工作区：%s" % ws)
    for c in created:
        print("  + %s" % c)
    if not created:
        print("  （已存在，未改动）")
    print("\n下一步：改 conditions/default.json，然后 jkit apply --platform boss")
    return 0


# ---------- conditions ----------

def cmd_conditions(args) -> int:
    path = args.file or None
    try:
        cond = load_conditions(path)
    except ConditionError as e:
        print("条件不合法：%s" % e, file=sys.stderr)
        return 2
    if args.check:
        print("✓ 条件 %s 通过校验（%d 个词，城市 %s）"
              % (cond.id, len(cond.queries), cond.city))
    print(json.dumps(cond.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_inspect(args) -> int:
    """把「这份条件到底怎么筛」摊平成一份 JSON，供工作台的投递条件页渲染。

    只读：不落盘、不改条件。条件本身 + 引擎里实际生效的规则一起给出来，
    避免页面上的阈值和 score.py 里的阈值各写一份、改了不同步。
    """
    from . import score as sc
    from .conditions_loader import candidate_paths

    ws = _ws()
    path = args.file or None
    try:
        cond = load_conditions(path)
    except ConditionError as e:
        print("条件不合法：%s" % e, file=sys.stderr)
        return 2

    # family_id 不在 Conditions 字段里（它只是路由用的标签），从原始 JSON 取
    family_id = ""
    for p in candidate_paths(ws, path):
        if p.exists():
            try:
                family_id = (json.loads(p.read_text(encoding="utf-8")) or {}).get("family_id") or ""
            except Exception:
                family_id = ""
            break
    family = family_id or "other"
    # family_id 是族契约的 slug（it-support），不是判分词表的键（tech_support）。
    # 两者靠 config/local/families.json 的 score_family 对应，避免在这里猜。
    score_family = ""
    fp = ws / "config" / "local" / "families.json"
    if family_id and fp.exists():
        try:
            fam = (json.loads(fp.read_text(encoding="utf-8")) or {}).get("families") or {}
            score_family = (fam.get(family_id) or {}).get("score_family") or ""
        except Exception:
            score_family = ""
    if not score_family:
        score_family = sc.job_family_of(cond.queries[0]) if cond.queries else "other"

    out = {
        "schema": "inspect/v1",
        "source": str(next((p for p in candidate_paths(ws, path) if p.exists()), "")),
        "conditions": cond.to_dict(),
        "family_id": family_id,
        "rules": {
            "weights": {"direction": 40, "gate": 25, "company": 20, "pay": 10, "fresh": 5},
            "bands": {"A": sc.GRADE_A, "B": sc.GRADE_B},
            "deliver_line": cond.min_grade,
            "direction_family": score_family,
            "direction_table": dict(sorted(sc.direction_words(score_family).items(),
                                           key=lambda kv: -kv[1])),
            "veto_title": [[k, w] for k, w in sc.VETO_TITLE],
            "veto_jd": [[k, w] for k, w in sc.VETO_JD],
            "salary_floor": cond.salary_floor,
            "exp_ceiling_years": cond.exp_ceiling_years,
            "exp_years": cond.exp_years,
            "education": cond.education,
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ---------- analyze ----------

def cmd_analyze(args) -> int:
    """把画像与市场数据对一遍，输出方向推荐（确定性计算，不调模型）。"""
    out = analyze_mod.run(scope=args.scope)
    if args.json_out:
        analyze_mod.write(out, args.json_out)
    top = out["families"][0] if out["families"] else None
    print("分析 %d 个方向（共 %d）｜推荐 %d 个%s"
          % (out["stats"]["families"], out["stats"]["all_families"],
             len(out["recommended"]),
             ("｜首选 " + top["label"] + " " + str(top["score"])) if top else ""))
    if not args.json_out:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ---------- platforms ----------

def cmd_platforms(args) -> int:
    if args.chips:
        adapter = get_platform(args.chips)
        drv = Driver(session=args.session, auto_start=False)
        if args.tab:
            drv.tab_id = str(args.tab)
        chips = adapter.filter_chips(drv)
        print("%s 当前筛选项：" % adapter.label)
        for c in chips:
            print("  · %s" % c)
        return 0
    print("%-10s %-12s %s" % ("平台", "名称", "状态"))
    for a in all_platforms():
        print("%-10s %-12s %s" % (a.name, a.label or "-",
                                  "已实测" if a.tested else "未实测，不可用"))
    if args.verify:
        print("\n自检（需要浏览器停在列表页）：--chips <平台> 读当前筛选项")
    return 0


# ---------- apply ----------

def cmd_apply(args) -> int:
    ws = _ws()
    try:
        cond = load_conditions(args.conditions)
    except ConditionError as e:
        raise SystemExit("条件不合法：%s" % e)
    cond.validate()

    platform = args.platform or workspace_config(ws).get("default_platform") or "boss"
    adapter = get_platform(platform)
    if not adapter.tested:
        raise SystemExit("平台 %s 未实测，不能 live 跑。" % adapter.name)

    drv = Driver(session=args.session, auto_start=not args.no_auto_start)
    if getattr(args, "tab", None):
        drv.tab_id = str(args.tab)
    session = args.session
    if session and not drv.session_exists(session):
        print("  [提示] 会话 %s 已不存在，新建一个" % session)
        session = None
    browser = None
    if not session:
        browser = drv.pick_browser(args.browser)
        if not browser and not args.no_auto_start:
            raise SystemExit(
                "没有已连接的浏览器。装好 BrowserSkill 扩展并保持浏览器打开，"
                "或显式传 --session。")

    res = apply_mod.run(
        ws, cond, adapter, drv,
        session=session, browser=browser, dry_run=args.dry_run,
        max_deliver=args.max, recheck=args.recheck, account=args.account,
        ignore_guard=args.ignore_guard, budget_min=args.budget_min,
        pace=args.pace, fast=args.fast, seed=args.seed,
        daily_limit=args.daily_limit, limit=args.limit)
    if res.get("blocked"):
        print("\n未开跑：%s" % res.get("reason"))
        return 3

    # --json-out：把本轮结果整理成一份结构化文件，供页面/后续分析直接复用。
    # 逐条判定本来就在 runs/<run_id>/ledger.jsonl，这里只是把「台账 + 汇总」打包成
    # 一个稳定入口，免得调用方去猜 run_id、再去翻文件。
    if args.json_out:
        items = []
        ledger = run_dir(ws, res["run_id"]) / "ledger.jsonl"
        if ledger.exists():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        write_json(Path(args.json_out), {
            "ok": True,
            "mode": "dry_run" if args.dry_run else "deliver",
            "run_id": res["run_id"],
            "condition_id": cond.id,
            "platform": adapter.name,
            "stats": res.get("stats") or {},
            "stopped": res.get("stopped") or "done",
            "spent_sec": round(res.get("spent_sec") or 0, 1),
            "items": items,
        })
    return 0


# ---------- apply-all ----------

def cmd_apply_all(args) -> int:
    """多方向按配额分摊：读目录下所有条件，每个方向各投 N 条。"""
    ws = _ws()
    platform = args.platform or workspace_config(ws).get("default_platform") or "boss"
    adapter = get_platform(platform)
    if not adapter.tested:
        raise SystemExit("平台 %s 未实测，不能跑。" % adapter.name)

    drv = Driver(session=args.session, auto_start=not args.no_auto_start)
    if getattr(args, "tab", None):
        drv.tab_id = str(args.tab)
    session = args.session
    if session and not drv.session_exists(session):
        print("  [提示] 会话 %s 已不存在，新建一个" % session)
        session = None
    browser = None
    if not session:
        browser = drv.pick_browser(args.browser)
        if not browser and not args.no_auto_start:
            raise SystemExit(
                "没有已连接的浏览器。装好 BrowserSkill 扩展并保持浏览器打开，"
                "或显式传 --session。")

    batch_mod.run_batch(
        ws, args.conditions_dir, adapter, drv,
        per_condition=args.per_condition, session=session, browser=browser,
        dry_run=args.dry_run, budget_min=args.budget_min, pace=args.pace,
        fast=args.fast, seed=args.seed, daily_limit=args.daily_limit,
        limit=args.limit, account=args.account, recheck=args.recheck)
    return 0


# ---------- deliver ----------

def cmd_deliver(args) -> int:
    """一键投递：自检（daemon / 浏览器 / 条件）→ 逐方向投 → 汇总。

    用户口径（2026-11-16）：「每次跑就行了，不要每次都又调」。这条命令就是
    为此存在 —— 启动前的准备工作全部自动化，失败时给可执行的下一步。
    """
    from . import deliver as deliver_mod
    from .preflight import PreflightError

    ws = _ws()
    platform = args.platform or workspace_config(ws).get("default_platform") or "boss"
    only = [x for x in (args.only or "").split(",") if x.strip()] or None
    src = args.conditions or args.conditions_dir
    try:
        deliver_mod.run(
            ws=ws, conditions=src, platform=platform,
            per_condition=args.per_condition, budget_min=args.budget_min,
            dry_run=args.dry_run, recheck=args.recheck, only=only,
            limit=args.limit, pace=args.pace, fast=args.fast,
            daily_limit=args.daily_limit, account=args.account,
            browser=args.browser, session=args.session, tab=args.tab,
            auto_start=not args.no_auto_start, ai_judge=args.ai_judge)
    except PreflightError as e:
        print("\n启动前自检没过：\n%s" % e, file=sys.stderr)
        return 2
    except JkitError as e:
        print("\n投递中断：%s" % e, file=sys.stderr)
        return 1
    return 0


# ---------- 装配 ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jkit", description="多平台自动投递（先筛选，再逐条判投）")
    p.add_argument("--version", action="version", version="jkit %s" % __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="建工作区")
    s.add_argument("--dir", default=".")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("conditions", help="校验 / 打印条件 JSON")
    s.add_argument("file", nargs="?", help="条件文件；不传用内置默认口径")
    s.add_argument("--check", action="store_true", help="只校验并打印一行结论")
    s.set_defaults(func=cmd_conditions)

    s = sub.add_parser("inspect", help="摊平条件 + 实际生效规则（JSON，供页面渲染）")
    s.add_argument("file", nargs="?", help="条件文件；不传用内置默认口径")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("analyze", help="画像 × 市场 → 方向推荐（确定性计算）")
    s.add_argument("--scope", default="active", choices=["active", "all"],
                   help="active=只分析在投方向（默认）, all=全部岗位族")
    s.add_argument("--json-out", dest="json_out", default="",
                   help="把结果写成 JSON（页面/后续分析直接用）")
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("platforms", help="平台状态 / 读当前筛选项")
    s.add_argument("--chips", metavar="PLATFORM", help="读该平台当前已生效的筛选项")
    s.add_argument("--session")
    s.add_argument("--tab")
    s.add_argument("--verify", action="store_true")
    s.set_defaults(func=cmd_platforms)

    s = sub.add_parser("apply", help="主链路：落地→筛选→搜索→判→投")
    s.add_argument("--conditions", default="", help="条件 JSON 路径；不传用内置默认")
    s.add_argument("--platform", default="", help="boss / zhilian（默认读 jkit.toml）")
    s.add_argument("--max", type=int, default=None, help="本轮最多投出多少条")
    s.add_argument("--limit", type=int, default=None, help="本轮最多处理多少张卡")
    s.add_argument("--dry-run", dest="dry_run", action="store_true", help="只判不投")
    s.add_argument("--recheck", action="store_true",
                   help="重判已判过的岗位（默认跳过：翻页时同一批卡会重复出现）")
    s.add_argument("--account", default="main", help="账号标签：日预算/冷却按账号分开")
    s.add_argument("--budget-min", dest="budget_min", type=float, default=None,
                   help="本轮时间预算（分钟，默认取条件里的 budget_min）")
    s.add_argument("--pace", default=None, choices=["fast", "safe"], help="节奏档位")
    s.add_argument("--fast", action="store_true", help="零等待旁路：测试用")
    s.add_argument("--seed", type=int, default=None, help="随机种子，便于复现")
    s.add_argument("--daily-limit", dest="daily_limit", type=int, default=None)
    s.add_argument("--ignore-guard", dest="ignore_guard", action="store_true",
                   help="忽略冷却/日上限强行开跑（不建议）")
    s.add_argument("--browser")
    s.add_argument("--session")
    s.add_argument("--tab", help="钉住这个标签页（不传则新开一个，不动用户当前页）")
    s.add_argument("--no-auto-start", dest="no_auto_start", action="store_true")
    s.add_argument("--json-out", dest="json_out", default="",
                   help="把本轮汇总 + 逐条判定写成一份 JSON（页面/后续分析直接用）")
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("apply-all", help="多方向按配额分摊：每个条件各投 N 条")
    s.add_argument("--conditions-dir", default="config/local/conditions",
                   help="条件目录（默认 config/local/conditions）")
    s.add_argument("--per-condition", type=int, default=5,
                   help="每个岗位方向最多投几条（默认 5）")
    s.add_argument("--recheck", action="store_true",
                   help="忽略历史判定，按当前规则重判（改规则后必用）")
    s.add_argument("--platform", default="", help="boss / zhilian（默认读 jkit.toml）")
    s.add_argument("--dry-run", dest="dry_run", action="store_true", help="只判不投")
    s.add_argument("--budget-min", dest="budget_min", type=float, default=40.0,
                   help="整轮总预算分钟，平分给各岗位（默认 40）")
    s.add_argument("--account", default="main", help="账号标签：日预算/冷却按账号分开")
    s.add_argument("--limit", type=int, default=None, help="每个方向最多处理多少张卡")
    s.add_argument("--daily-limit", dest="daily_limit", type=int, default=None)
    s.add_argument("--pace", default=None, choices=["fast", "safe"], help="节奏档位")
    s.add_argument("--fast", action="store_true", help="零等待旁路：测试用")
    s.add_argument("--seed", type=int, default=None, help="随机种子，便于复现")
    s.add_argument("--browser")
    s.add_argument("--session")
    s.add_argument("--tab", help="钉住这个标签页（不传则新开一个）")
    s.add_argument("--no-auto-start", dest="no_auto_start", action="store_true")
    s.set_defaults(func=cmd_apply_all)

    s = sub.add_parser("deliver", help="一键投递：自检 + 逐方向投 + 汇总（日常就用这条）")
    s.add_argument("--conditions", default="", help="单个条件文件；不传则跑目录下全部")
    s.add_argument("--conditions-dir", default="config/local/conditions",
                   help="条件目录（默认 config/local/conditions）")
    s.add_argument("--platform", default="", help="boss / zhilian（默认读 jkit.toml）")
    s.add_argument("--per", dest="per_condition", type=int, default=5,
                   help="每个方向最多投几条（默认 5）")
    s.add_argument("--budget-min", dest="budget_min", type=float, default=30.0,
                   help="整轮总预算分钟（默认 30）")
    s.add_argument("--dry-run", dest="dry_run", action="store_true", help="只判不投")
    s.add_argument("--recheck", action="store_true",
                   help="忽略历史判定，按当前规则重判（改过规则才需要）")
    s.add_argument("--only", default="", help="只跑这些方向，逗号分隔（如 it-support,presales）")
    s.add_argument("--limit", type=int, default=None, help="每个方向最多处理多少张卡")
    s.add_argument("--daily-limit", dest="daily_limit", type=int, default=None)
    s.add_argument("--pace", default=None, choices=["fast", "safe"])
    s.add_argument("--fast", action="store_true", help="零等待旁路：测试用")
    s.add_argument("--account", default="main", help="账号标签：日预算/冷却按账号分开")
    s.add_argument("--ai-judge", dest="ai_judge", action="store_true",
                   help="对规则放行的记录再做一次 AI 复核（默认关闭）")
    s.add_argument("--browser")
    s.add_argument("--session")
    s.add_argument("--tab", help="钉住这个标签页（不传则新开一个）")
    s.add_argument("--no-auto-start", dest="no_auto_start", action="store_true")
    s.set_defaults(func=cmd_deliver)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except JkitError as e:
        # 页面操作链路失败一律停，不退回 URL：退回会把问题藏起来
        print("\n停止：%s" % e, file=sys.stderr)
        if e.hint:
            print("修复：%s" % e.hint, file=sys.stderr)
        return int(getattr(e, "code", 10))
    except BskError as e:
        print("驱动错误：%s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断（已发出的投递不会回滚，台账里已逐条落盘）", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
