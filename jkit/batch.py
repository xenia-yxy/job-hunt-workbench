"""多岗位按配额分摊 —— 一次跑几个方向，每个方向各投 N 条。

用户口径（2026-11-16）：「岗位比较多，每个岗位分配一点」。
单次 ``apply`` 只吃一个条件文件，会把全部额度给一个方向；这里把
``config/local/conditions/*.json`` 轮流跑一遍，每个文件各投 N 条。

为什么不做成「一次落地、多条件轮转」：那要把 searchflow 的筛选/换词
拆成可复用步骤，改动面大；而每个条件各跑一次落地（约 1 秒）在整轮
几分钟的预算里可以忽略，换来的是**每个方向的台账独立、可单独复盘**。

平台策略：默认 boss（用户「先优先投 Boss」），智联用 ``--platform zhilian``。
整轮只开一个会话，各条件复用同一标签页 —— 反复开新页本身就是异常特征。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import apply as apply_mod
from .conditions_loader import load_conditions


def load_condition_files(src: str | Path) -> list[Path]:
    """目录 -> 目录下所有 ``*.json``（按文件名排序，顺序稳定可复现）；文件 -> 就它自己。"""
    p = Path(src)
    if p.is_dir():
        files = sorted(x for x in p.glob("*.json") if x.is_file())
        if not files:
            raise FileNotFoundError("目录里没有条件文件：%s" % p)
        return files
    if not p.exists():
        raise FileNotFoundError("条件文件不存在：%s" % p)
    return [p]


def run_batch(ws: Path, src: str | Path, adapter, drv, *,
              per_condition: Optional[int] = None,
              session: Optional[str] = None,
              browser: Optional[str] = None,
              dry_run: bool = False,
              budget_min: Optional[float] = None,
              pace: Optional[str] = None,
              fast: bool = False,
              seed: Optional[int] = None,
              daily_limit: Optional[int] = None,
              limit: Optional[int] = None,
              account: str = "main",
              recheck: bool = False,
              only: Optional[set] = None) -> dict:
    """按目录里的条件逐个跑，每个条件最多投 ``per_condition`` 条。

    返回 ``{"results": [{condition, run_id, stats, stopped}, ...], "totals": {...}}``。
    中途某个条件失败不终止整批 —— 记下原因继续下一个，最后一起汇总。
    """
    files = load_condition_files(src)
    if only:
        files = [f for f in files if f.stem in only]
        if not files:
            raise FileNotFoundError("--only 过滤后没有剩下任何方向")
    n = len(files)
    per_budget = (budget_min / n) if budget_min else None

    print("批量投递：%d 个方向 × 每个最多 %s 条 ｜ 平台 %s ｜ 会话 %s"
          % (n, per_condition if per_condition else "不限",
             adapter.label or adapter.name, session or "新开"),
          flush=True)
    if per_budget:
        print("整轮预算 %.0f 分钟，每个方向约 %.1f 分钟\n"
              % (budget_min, per_budget), flush=True)

    owned = not session
    if session:
        drv.session = session
    else:
        drv.session_start("jkit batch · %s" % adapter.name, browser=browser)
        session = drv.session

    results: list[dict] = []
    totals = {"delivered": 0, "checked": 0, "drop": 0, "dry_run": 0}
    try:
        for i, f in enumerate(files, 1):
            print("===== [%d/%d] %s =====" % (i, n, f.name), flush=True)
            try:
                cond = load_conditions(f, root=ws)
            except Exception as e:  # noqa: BLE001
                print("  ✗ 条件不可用，跳过：%s" % e, flush=True)
                results.append({"condition": f.name, "error": str(e), "stats": {}})
                continue

            try:
                res = apply_mod.run(
                    ws, cond, adapter, drv, session=session,
                    dry_run=dry_run, max_deliver=per_condition,
                    budget_min=per_budget, pace=pace, fast=fast, seed=seed,
                    daily_limit=daily_limit, limit=limit, account=account,
                    recheck=recheck)
            except Exception as e:  # noqa: BLE001
                print("  ✗ 本轮异常，继续下一个方向：%s" % e, flush=True)
                results.append({"condition": cond.id, "error": str(e), "stats": {}})
                continue

            st = res.get("stats") or {}
            for k in totals:
                totals[k] += int(st.get(k) or 0)
            results.append({
                "condition": cond.id,
                "run_id": res.get("run_id"),
                "stats": st,
                "stopped": res.get("stopped") or ("guard" if res.get("blocked") else "done"),
            })
            print("", flush=True)
    finally:
        if owned:
            try:
                drv.session_stop(session)
            except Exception:  # noqa: BLE001
                pass

    print("===== 批量汇总 =====")
    for r in results:
        st = r.get("stats") or {}
        if r.get("error"):
            print("  %-28s 跳过：%s" % (r["condition"], r["error"][:40]))
        else:
            print("  %-28s 投出 %-3d 读取 %-3d 淘汰 %-3d 干跑 %-3d（%s）"
                  % (r["condition"], st.get("delivered", 0), st.get("checked", 0),
                     st.get("drop", 0), st.get("dry_run", 0), r.get("stopped")))
    print("  合计：投出 %d ｜ 读取 %d ｜ 淘汰 %d ｜ 干跑 %d"
          % (totals["delivered"], totals["checked"], totals["drop"], totals["dry_run"]))

    return {"results": results, "totals": totals}
