"""自动投递主链路 —— 落地 → 先筛选 → 逐词搜索 → 逐卡判 → 投 → 留在此页。

顺序是硬约束，不能换：

  1. land()            整页加载 1 次（只带城市）
  2. apply_filters()   **先套平台原生筛选**，失败即停
  3. search(q)         页面内换词
  4. read_cards()      读这一页的卡
  5. 卡片层判定        标题/学历/经验这类本地就能判的，先淘汰，省掉开面板
  6. 点卡读面板        右侧面板换过去（不跳详情页）
  7. 详情层判定        score.judge，全字段
  8. 投递              面板就在眼前，点按钮；弹窗点「留在此页」
  9. 下一张

为什么第 2 步必须在第 4 步之前：不做平台原生筛选，就是在全量结果里逐条开面板。
一个词 30 张卡，其中 20 张注定不投（学历/经验不符），先筛掉它们能省掉 2/3 的
面板读取——这是时间，也是风控面。

为什么不「先读完所有详情再统一点投」：那样要先花几十秒读十几条 JD，才发现只有
四条能投。真正的顺序是「进一个、判一个、能投当场投掉、马上进下一个」。中途被杀
也只丢一条，不丢一批。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from . import feed, guard as guard_mod, humanize, panel as panel_mod, policy, score, searchflow, store
from .config import now_iso, run_dir, write_json
from .errors import JkitError
from .platforms.base import filter_line


def _ledger_row(rec: dict, v: score.Verdict, run_id: str, cond, platform: str,
                outcome: str, evidence: str = "", stay: str = "",
                hr_level: str = "") -> dict:
    """一行台账。字段固定，方便后面直接统计/复盘。"""
    return {
        "at": now_iso(),
        "run_id": run_id,
        "condition_id": cond.id,
        "platform": platform,
        "job_id": rec.get("job_id") or "",
        "title": rec.get("title") or "",
        "company": rec.get("company_name") or "",
        "salary": rec.get("salary") or "",
        "exp": rec.get("experience_raw") or "",
        "edu": rec.get("education_raw") or "",
        "city": rec.get("city_raw") or "",
        "company_scale": rec.get("company_scale") or "",
        "company_industry": rec.get("company_industry") or "",
        "client_company": rec.get("client_company") or "",
        "outsourced_tag": bool(rec.get("outsourced_tag")),
        "score": v.score,
        "grade": v.grade,
        "job_family": v.job_family,
        "deliverable": v.deliverable,
        "veto": v.veto,
        "reasons": v.reasons,
        "detail": v.detail,
        "outcome": outcome,
        "evidence": evidence,
        "stay": stay,
        "hr_level": hr_level,
        "jd_len": len(rec.get("jd_duty") or ""),
        "jd_brief": (rec.get("jd_duty") or "")[:400],
        "job_snapshot": {"title": rec.get("title"), "company": rec.get("company_name"),
                         "salary": rec.get("salary")},
    }


def run(ws: Path, cond, adapter, drv, *, session: Optional[str] = None,
        browser: Optional[str] = None, dry_run: bool = False,
        max_deliver: Optional[int] = None, recheck: bool = False,
        account: str = "main", ignore_guard: bool = False,
        budget_min: Optional[float] = None, pace: Optional[str] = None,
        fast: bool = False, seed: Optional[int] = None,
        daily_limit: Optional[int] = None, limit: Optional[int] = None) -> dict:
    cond.validate()
    state = store.create_run(ws, cond, adapter.name, mode="live")
    run_id = state["run_id"]
    (ws / "runs" / ".current").write_text(run_id, encoding="utf-8")
    ev_dir = run_dir(ws, run_id)

    max_deliver = max_deliver if max_deliver is not None else cond.max_deliver
    budget_min = budget_min if budget_min is not None else cond.budget_min
    pace = pace or cond.pace

    # 安全阀：冷却/超额时零请求退出。挂在 driver 上是为了让每一次导航都计数。
    g = guard_mod.Guard(ws, account=account, daily_limit=daily_limit or cond.daily_limit)
    drv.on_request = g.note_request
    ok, why = g.preflight()

    budget = guard_mod.Budget(minutes=budget_min, target=max_deliver or 0)
    hum = humanize.Humanizer(pace, seed=seed, fast=fast, budget=budget)

    flow = searchflow.SearchFlow(drv, adapter, cond, human=hum, guard=g,
                                 run_id=run_id, ev_dir=ev_dir)
    pnl = panel_mod.Panel(drv, adapter, human=hum, guard=g)

    print("run %s ｜ 条件 %s ｜ 平台 %s ｜ 账号 %s ｜ 节奏 %s"
          % (run_id, cond.id, adapter.label or adapter.name, g.account, hum.profile),
          flush=True)
    print("安全阀：%s（%.0f 分钟预算%s）"
          % (why, budget.minutes, "，目标 %d 条" % max_deliver if max_deliver else ""),
          flush=True)
    print("筛选条件：%s" % (filter_line(cond.filters.get(adapter.name)) or "未配置"),
          flush=True)
    print("准入门槛：%s｜%s｜%s"
          % ("今日在线（在线/刚刚/今日）" if policy.REQUIRE_TODAY_ONLINE
             else "%d 日内活跃" % policy.HR_ACTIVE_MAX_DAYS,
             "JD≥%d字" % policy.MIN_JD_LEN if policy.MIN_JD_LEN else "JD不限",
             "能回复=以今日在线代理"), flush=True)
    print("搜索词：%s\n" % " / ".join(cond.queries), flush=True)

    if not ok and not ignore_guard:
        state.update({"stopped_reason": "guard_preflight"})
        store.save_state(ws, run_id, state)
        return {"run_id": run_id, "blocked": True, "reason": why,
                "stats": {"delivered": 0, "checked": 0}}

    # 整轮只开一个标签页，中途不销毁重建 —— 每 6 条换一次会话本身就是异常特征。
    # 复用用户会话时**不负责关闭**：那是用户的标签页，关了要重新扫码。
    owned_session = not session
    if session:
        drv.session = session
    else:
        drv.session_start("jkit · %s" % adapter.name, browser=browser)

    done = set(store.delivered_index(ws))
    # 历史评分没有条件版本与有效期，不能安全复用为「不再处理」。
    # 只用真实成功记录跨轮去重；本轮重复卡仍由 settled 防重。
    # 因此试跑/旧条件的淘汰记录不会阻断随后真投。
    settled = set()

    stats = {"checked": 0, "delivered": 0, "drop": 0, "skip": 0, "dry_run": 0,
             "stop": 0, "already": 0, "dup": 0, "no_button": 0,
             "drop_inactive": 0, "drop_thin_jd": 0}
    total_ms = 0
    detail_ms = 0
    stopped = ""
    stop_streak = 0
    pages_seen = 0
    delivered_companies: set[str] = set()

    def write(rows: list[dict]) -> None:
        if rows:
            store.append_ledger(ws, run_id, rows)

    try:
        # ---- 1) 落地 + 2) 先筛选：顺序不能换 ----
        flow.land()
        flow.apply_filters()
        if not flow.filters:
            print("  ⚠ 本轮没有原生筛选，等于在全量结果里逐条看，耗时与风控面都会变大",
                  flush=True)

        # ---- 3) 逐词逐页 ----
        for batch in feed.collect_pages(drv, adapter, flow, cond, limit=limit):
            pages_seen += 1
            rows: list[dict] = []
            # 换词/翻页会整体重排列表，上一页的定位失败不该累积成「页面级故障」——
            # 「连续失败」只在同一批卡里有意义（2026-11-16 实测：换词后的重排
            # 会把正常的一轮误判成 3 连败直接停掉）。
            stop_streak = 0
            for rec in batch["fresh"]:
                if max_deliver and stats["delivered"] >= max_deliver:
                    stopped = "max"
                    break
                if budget.expired():
                    stopped = "budget"
                    break
                key = store.dedupe_key(rec)
                # 已判过的卡以前是「静默跳过」：不打印、不写台账，结果就是页面上一片空白，
                # 让人以为筛选坏了。现在两种跳过都写一行 skip（带原因），并限量打印。
                if key in settled:
                    pre = score.judge(rec, cond)
                    rows.append(_ledger_row(rec, pre, run_id, cond, adapter.name, "skip",
                                            evidence="历史已判，未重判（加 --recheck 可重判）"))
                    settled.add(key)
                    stats["skip"] += 1
                    stats["already"] += 1
                    if stats["already"] <= 6:
                        print("  ↷ %-24s 已判过，跳过（--recheck 可重判）"
                              % (rec.get("title") or "")[:24], flush=True)
                    elif stats["already"] == 7:
                        print("  ↷ …后续同类跳过不再逐条打印", flush=True)
                    continue
                if key in done:
                    pre = score.judge(rec, cond)
                    rows.append(_ledger_row(rec, pre, run_id, cond, adapter.name, "skip",
                                            evidence="已投递过，跳过"))
                    print("  └ 已投过，跳过：%s" % (rec.get("title") or "")[:24], flush=True)
                    stats["skip"] += 1
                    stats["dup"] += 1
                    settled.add(key)
                    continue

                # ---- 5) 卡片层：本地就能判的先淘汰，不开面板 ----
                pre = score.judge(rec, cond)
                if pre.veto:
                    rows.append(_ledger_row(rec, pre, run_id, cond, adapter.name, "drop",
                                            evidence="卡片层：" + pre.veto))
                    settled.add(key)
                    stats["drop"] += 1
                    print("  ✗ %-24s %-12s %s" % ((rec.get("title") or "")[:24],
                                                  rec.get("salary") or "", pre.veto), flush=True)
                    continue

                # ---- 6) 点卡读面板 ----
                t0 = time.time()
                raw_idx = rec.get("idx")
                idx = -1 if raw_idx is None else int(raw_idx)
                st = None
                err = None
                try:
                    st = pnl.open_card(idx, expect_id=rec.get("job_id") or "")
                except JkitError as e:
                    err = e
                    # 换词/滚动后列表重排，旧下标会指到别的卡。按 job_id 重新
                    # 定位一次再点 —— 这是「单卡失败」里最常见、也最好救的一种，
                    # 不该直接算成页面故障（2026-11-16 实测：tech-support 就是
                    # 这样连续失败到停轮的）。
                    idx2 = None
                    try:
                        idx2 = feed.locate_card(drv, adapter, rec.get("job_id") or "")
                    except Exception:  # noqa: BLE001
                        idx2 = None
                    if idx2 is not None and idx2 != idx:
                        try:
                            st = pnl.open_card(idx2, expect_id=rec.get("job_id") or "")
                            idx, err = idx2, None
                        except JkitError as e2:
                            err = e2
                if st is None:
                    e = err
                    if isinstance(e, panel_mod.PanelMissing):
                        ev = "面板不存在：%s" % str(e)[:100]
                    elif isinstance(e, panel_mod.PanelStalled):
                        ev = "面板没换过来：%s" % str(e)[:100]
                    else:
                        ev = "单卡定位失败：%s" % str(e)[:100]
                    rows.append(_ledger_row(rec, pre, run_id, cond, adapter.name, "stop",
                                            evidence=ev))
                    settled.add(key)
                    stats["stop"] += 1
                    stop_streak += 1
                    if stop_streak >= 5:
                        stopped = "script"
                        break
                    continue

                stats["checked"] += 1
                detail_ms += int((time.time() - t0) * 1000)
                rec2 = feed.detail_record(rec, st)

                if hum is not None:
                    hum.dwell_detail(drv)

                # ---- 准入门槛 1+2：今日在线 + 能回复（用户要求「必须」）----
                # 老口径是「3 日内活跃」，本轮收紧到「今日在线」：
                # 只有 hr_online=True，或 active_days==0（在线/刚刚/今日）才放行。
                # 智联能读到真正的回复信号（今日回复 N 次 / 高回复率）——
                # 有回复 = 今天在线且会回，直接当「今日在线 + 能回复」放行。
                # BOSS 没有回复字段，只能退回「今日在线」当代理，如实标注。
                max_days = 0 if policy.REQUIRE_TODAY_ONLINE else policy.HR_ACTIVE_MAX_DAYS
                reply_sig = (bool(rec2.get("hr_reply_count"))
                             or bool(rec2.get("hr_high_reply"))
                             or ("回复" in str(rec2.get("hr_active_raw") or "")))
                online = bool(rec2.get("hr_online")) or reply_sig
                ok_hr, why_hr = policy.activity_gate(
                    rec2.get("hr_active_days"), online, max_days)
                if ok_hr and reply_sig:
                    why_hr = "有回复信号(今日回复/高回复率)"
                hr_level = (rec2.get("hr_active_raw")
                            or ("在线" if rec2.get("hr_online") else "")
                            or ("今日回复" if rec2.get("hr_reply_count") else "")
                            or ("高回复率" if rec2.get("hr_high_reply") else ""))
                if not ok_hr:
                    rows.append(_ledger_row(rec2, pre, run_id, cond, adapter.name, "drop",
                                            evidence=why_hr, hr_level=hr_level))
                    settled.add(key)
                    stats["drop"] += 1
                    stats["drop_inactive"] += 1
                    print("  ✗ %-24s %s" % ((rec2.get("title") or "")[:24], why_hr), flush=True)
                    continue

                # ---- 准入门槛 3：真实需求（JD 太短 = 挂着不招/凑数）----
                # 外包已由 score 的 client_company 硬否决处理；这里只管「JD 空壳」。
                jd_len = int(rec2.get("jd_len") or len(rec2.get("jd_duty") or ""))
                if policy.MIN_JD_LEN and jd_len and jd_len < policy.MIN_JD_LEN:
                    why_thin = "JD 仅 %d 字（<%d），疑似伪需求/挂着不招" % (
                        jd_len, policy.MIN_JD_LEN)
                    rows.append(_ledger_row(rec2, pre, run_id, cond, adapter.name, "drop",
                                            evidence=why_thin, hr_level=hr_level))
                    settled.add(key)
                    stats["drop"] += 1
                    stats["drop_thin_jd"] += 1
                    print("  ✗ %-24s %s" % ((rec2.get("title") or "")[:24], why_thin), flush=True)
                    continue

                # ---- 7) 详情层：全字段判定 ----
                v = score.judge(rec2, cond)
                if not v.deliverable:
                    rows.append(_ledger_row(rec2, v, run_id, cond, adapter.name, "drop",
                                            evidence=v.veto or "未达投递线 %s" % cond.min_grade,
                                            hr_level=hr_level))
                    settled.add(key)
                    stats["drop"] += 1
                    print("  ✗ %-24s %5s %s %s" % ((rec2.get("title") or "")[:24], v.score,
                                                   v.grade, v.veto or ""), flush=True)
                    continue

                # ---- 同公司只投一次 ----
                comp = (rec2.get("company_name") or "").strip()
                if comp and comp in delivered_companies:
                    print("  └ 本轮已投过「%s」，跳过同公司第二条" % comp[:16], flush=True)
                    settled.add(key)
                    stats["skip"] += 1
                    continue

                # ---- 8) 投递 ----
                if dry_run:
                    rows.append(_ledger_row(rec2, v, run_id, cond, adapter.name, "dry_run",
                                            evidence="判定通过，未投", hr_level=hr_level))
                    settled.add(key)
                    stats["dry_run"] += 1
                    print("  · %-24s %5s %s（dry-run）" % ((rec2.get("title") or "")[:24],
                                                           v.score, v.grade), flush=True)
                    continue

                res = pnl.apply()
                stay = pnl.stay_here()
                outcome = res.get("outcome") or "unknown"
                if outcome == "already":
                    stats["already"] += 1
                elif outcome == "delivered":
                    stats["delivered"] += 1
                    if comp:
                        delivered_companies.add(comp)
                elif outcome == "no_button":
                    stats["no_button"] += 1
                rows.append(_ledger_row(rec2, v, run_id, cond, adapter.name, outcome,
                                        evidence=str(res.get("evidence") or "")[:200],
                                        stay=stay, hr_level=hr_level))
                settled.add(key)
                print("  %s %-24s %5s %s | %s | %s" % (
                    "✓" if outcome == "delivered" else "=",
                    (rec2.get("title") or "")[:24], v.score, v.grade,
                    outcome, str(res.get("evidence") or "")[:30]), flush=True)

                if outcome == "blocked_by_risk":
                    g.trip("投递页风控：%s" % str(res.get("evidence"))[:60],
                           policy.RISK_COOLDOWN_MIN)
                    stopped = "risk"
                    print("\n投递页出现风控提示，立即停止（不重试）。", flush=True)
                    break
                if outcome == "blocked_by_quota":
                    stopped = "quota"
                    print("\n配额耗尽，停止本轮。", flush=True)
                    break
                if outcome in ("blocked_by_platform", "unknown"):
                    stop_streak += 1
                    if stop_streak >= 5:
                        stopped = "script"
                        print("\n连续 %d 次投递异常，停止本轮。" % stop_streak, flush=True)
                        break
                else:
                    stop_streak = 0

                if g.rate_state() == "soft":
                    hum.mult = max(hum.mult, 1.3)
                g.check_rate()
                hum.gap()
                hum.rest_if_due(stats["delivered"])
                info = hum.tick(stats["delivered"])
                if info.get("mult", 1.0) != 1.0:
                    print("  （预算反馈：剩余 %.1f 分钟，节奏系数 %.1f）"
                          % (info.get("left_min", 0), info["mult"]), flush=True)

            write(rows)
            total_ms += 0
            if stopped:
                break

    except guard_mod.RiskBlocked as e:
        stopped = "risk"
        print("\n风控拦截：%s。已写入冷却，本轮立即停止。" % e, flush=True)
    except guard_mod.GuardTripped as e:
        stopped = "guard"
        print("\n安全阀触发：%s" % e, flush=True)
    except JkitError as e:
        stopped = "error"
        print("\n停止：%s" % e, flush=True)
        if e.hint:
            print("修复：%s" % e.hint, flush=True)
    finally:
        if owned_session:
            try:
                drv.session_stop(drv.session)
            except Exception:  # noqa: BLE001
                pass

    spent = time.time() - budget.t0
    g.finish(stats["delivered"], stopped)
    n = max(1, stats["checked"])

    # P-C：候选耗尽提示。正常跑完全部候选，但设了目标且没投够——以前 stopped=""
    # 显示"完成"，用户不知道是候选不够还是出错。这里显式标 exhausted 并打印 X/N。
    # dry_run 是试跑，不计入数量闭环。
    if not stopped and max_deliver and not dry_run and stats["delivered"] < max_deliver:
        stopped = "exhausted"
        print("\n目标 %d 条，成功 %d 条，候选耗尽（已扫完全部候选，无更多满足条件者）"
              % (max_deliver, stats["delivered"]), flush=True)

    print("\n本轮：读取 %d ｜ 投出 %d ｜ 淘汰 %d ｜ 跳过 %d ｜ 干跑 %d"
          % (stats["checked"], stats["delivered"], stats["drop"], stats["skip"],
             stats["dry_run"]))
    if stats["skip"]:
        print("  跳过细分：历史已判 %d ｜ 已投过 %d ｜ 其它 %d"
              % (stats["already"], stats["dup"],
                 max(0, stats["skip"] - stats["already"] - stats["dup"])))
    if stats["drop_inactive"] or stats["drop_thin_jd"]:
        print("  淘汰细分：今日不在线 %d ｜ JD太短(伪需求) %d ｜ 其它 %d"
              % (stats["drop_inactive"], stats["drop_thin_jd"],
                 max(0, stats["drop"] - stats["drop_inactive"] - stats["drop_thin_jd"])))
    print("耗时 %.1f 分钟（每条 %.1fs）｜ 请求 %.1f 次/分 ｜ 整页加载 %d｜筛选 %d｜换词滚动 %d"
          % (spent / 60, total_ms / 1000.0 / n,
             (stats["checked"] + pages_seen) / max(spent / 60, 0.01),
             flow.full_loads, 1 if flow.filters else 0, flow.xhr_loads))
    if stopped:
        print("停止原因：%s" % {
            "max": "已达目标", "budget": "时间预算用完", "quota": "配额耗尽",
            "risk": "命中风控", "guard": "安全阀熔断", "script": "页面连续失败",
            "error": "链路异常", "exhausted": "候选耗尽（未投够）"}.get(stopped, stopped))
    print("台账：%s" % (ev_dir / "ledger.jsonl"))

    summary = {
        "run_id": run_id, "at": now_iso(), "condition_id": cond.id,
        "platform": adapter.name, "account": g.account, "pace": hum.profile,
        "filters": cond.filters.get(adapter.name) or {},
        "applied_filters": flow.filters,
        "queries": list(cond.queries), "stats": stats, "stopped": stopped or "done",
        "target": max_deliver, "completed": stats["delivered"],
        "shortfall": (max(0, max_deliver - stats["delivered"]) if max_deliver else 0),
        "spent_sec": round(spent, 1), "pages": pages_seen,
        "full_loads": flow.full_loads, "xhr_loads": flow.xhr_loads,
        "avg_ms": {"total": total_ms // n, "detail": detail_ms // n},
    }
    write_json(ev_dir / "summary.json", summary)
    state.update({"state": "DONE", "stopped_reason": stopped or "done"})
    store.save_state(ws, run_id, state)
    return {"run_id": run_id, "stats": stats, "stopped": stopped, "spent_sec": spent}
