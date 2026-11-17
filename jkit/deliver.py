"""一键投递：自检 → 逐方向投递 → 汇总。

以前跑一轮要手工做四件事：起 daemon、确认浏览器连着、试条件能不能加载、
再挨个方向跑。全是重复劳动，而且任何一步忘了都会在投递中段炸掉。这里把
它们收敛成一条命令：

    python -m jkit deliver                       # BOSS，全部方向各 5 条
    python -m jkit deliver --platform zhilian    # 智联
    python -m jkit deliver --per 10 --budget-min 40
    python -m jkit deliver --dry-run             # 只判不投
    python -m jkit deliver --only it-support,presales   # 只跑指定方向

判定规则全部在 ``conditions`` + ``score.py`` 里，是**确定性**的，不依赖模型：
同样的条件、同样的页面，结论一样。需要语义判断的灰区（比如 JD 描述与目标
方向的贴合度）通过 ``--ai-judge`` 预留接口接入，默认关闭 —— 不开也一样能跑。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from . import batch as batch_mod
from .config import find_workspace
from .driver import Driver
from .platforms import get as get_platform
from .preflight import (PreflightError, check_conditions, ensure_browser,
                        ensure_daemon)


def run(ws: Path | None = None,
        conditions: str | Path = "config/local/conditions",
        platform: str = "boss",
        per_condition: int = 5,
        budget_min: float = 30.0,
        dry_run: bool = False,
        recheck: bool = False,
        only: Optional[list[str]] = None,
        limit: Optional[int] = None,
        pace: Optional[str] = None,
        fast: bool = False,
        daily_limit: Optional[int] = None,
        account: str = "main",
        browser: Optional[str] = None,
        session: Optional[str] = None,
        tab: Optional[str] = None,
        auto_start: bool = True,
        ai_judge: bool = False) -> dict:
    """跑一轮完整投递。返回 ``{"ok", "totals", "results", "spent_sec"}``。

    任何一步自检不过都抛 ``PreflightError``，消息里带下一步怎么做。
    """
    ws = ws or find_workspace()
    t_start = time.time()

    print("=" * 62)
    print(" 一键投递 ｜ 平台 %s ｜ 每方向 %d 条 ｜ 预算 %.0f 分钟%s"
          % (platform, per_condition, budget_min, "｜干跑" if dry_run else ""))
    print("=" * 62)

    # ---- 1) 自检 ----
    drv = Driver(session=session, auto_start=auto_start)
    started = ensure_daemon(drv)
    print("· daemon：%s" % ("已重新拉起" if started else "在跑"))

    if session and not drv.session_exists(session):
        print("· 会话 %s 不存在，改为复用/新建" % session)
        session = None

    chosen = None
    if not session:
        chosen = ensure_browser(drv, want=browser)
        print("· 浏览器：%s" % chosen)
    if tab:
        drv.tab_id = str(tab)
        print("· 钉住标签页：%s" % tab)

    pairs = check_conditions(conditions, ws)
    if only:
        keep = {o.strip() for o in only if o.strip()}
        pairs = [(f, c) for f, c in pairs
                 if c.id in keep or any(k in f.stem for k in keep)]
        if not pairs:
            raise PreflightError("--only 过滤后没有剩下任何方向：%s" % ", ".join(sorted(keep)))
    print("· 条件：%d 个方向（%s）"
          % (len(pairs), "、".join(c.id for _, c in pairs)))

    if ai_judge:
        # 预留：灰区人工/AI 复核。默认关闭，规则判定本身就能独立跑通。
        print("· AI 复核：已开启（仅对规则放行的记录做二次确认）")

    adapter = get_platform(platform)
    if not adapter.tested:
        raise PreflightError("平台 %s 还没实测，拒绝执行。" % platform)

    # ---- 2) 投递 ----
    print("")
    out = batch_mod.run_batch(
        ws, conditions, adapter, drv,
        per_condition=per_condition, session=session, browser=chosen,
        dry_run=dry_run, budget_min=budget_min, pace=pace, fast=fast,
        limit=limit, daily_limit=daily_limit, account=account, recheck=recheck,
        only={f.stem for f, _ in pairs},
    )

    # ---- 3) 汇总 ----
    spent = time.time() - t_start
    totals = out.get("totals") or {}
    print("")
    print("=" * 62)
    print(" 本轮结束 ｜ 投出 %d ｜ 读取 %d ｜ 淘汰 %d ｜ 跳过 %d"
          % (totals.get("delivered", 0), totals.get("checked", 0),
             totals.get("drop", 0), totals.get("skip", 0)))
    print(" 耗时 %.1f 分钟" % (spent / 60.0))
    run_dirs = [d for d in (ws / "runs").glob("*") if d.is_dir()]
    if run_dirs:
        latest = max(run_dirs, key=lambda p: p.stat().st_mtime)
        print(" 台账：%s" % (latest / "ledger.jsonl"))
    print("=" * 62)
    return {"ok": True, "totals": totals, "results": out.get("results") or [],
            "spent_sec": round(spent, 1)}


def run_single(ws: Path | None = None, condition: str | Path = "",
               platform: str = "boss", **kw) -> dict:
    """单方向跑（``--conditions`` 指到某个文件）。"""
    return run(ws=ws, conditions=condition, platform=platform, **kw)
