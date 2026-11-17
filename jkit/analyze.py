"""把「我」和「市场」对一遍，给出一张可排序、可解释的方向推荐表。

为什么要有这一步：族级市场友好度（metrics2）只回答「这个方向在市场上怎么样」，
不回答「我能不能进」。两者要合起来看才有用——一个族市场分很高但我一条 must 要求
都不命中，投了也是白投；反过来市场分一般但我命中 80%，反而值得先投。

这里做的是**确定性计算**，不调模型：
  综合 = 0.35 × 族市场友好度（市场视角）
       + 0.25 × must 要求命中率（我 vs 这个族的要求清单）
       + 0.15 × 薪资达标（中位薪 vs 我的下限）
       + 0.25 × 想法命中（你写的求职想法里是否点名了这个方向）

想法项权重和技能项一样高，因为「市场好 + 我能干 + 我不想干」不该排第一。

技能项确实与市场友好度里的 skill 维度同源，权重不追求统计严格——它只用于排序，
每一项都在输出里写清楚，页面照原样展示，不做二次加工。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import find_workspace, now_iso, write_json
from .errors import ConditionError

WEIGHTS = {"market": 0.35, "skill": 0.25, "pay": 0.15, "intent": 0.25}

#: 结论阈值：与 A/B/C 分档不是一回事，这里判的是「现在投还是先补」
VERDICT_NOW = 65
VERDICT_PATCH = 55


def _load(p: Path) -> Any:
    if not p.exists():
        raise ConditionError("缺少分析所需数据：%s" % p)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConditionError("%s 不是合法 JSON（%s）" % (p, e))


def _defaults(ws: Path) -> dict:
    """数据源路径一律从 config 读，代码里不写死实例目录。"""
    out = {"metrics": "", "req": "", "families": "", "profile": ""}
    ap = ws / "config" / "local" / "app.json"
    if ap.exists():
        try:
            src = (json.loads(ap.read_text(encoding="utf-8")) or {}).get("sources") or {}
        except json.JSONDecodeError:
            src = {}
        out["metrics"] = src.get("job_families") or ""
    out["families"] = "config/local/families.json"
    out["profile"] = "config/local/profile.json"
    if out["metrics"]:
        out["req"] = str(Path(out["metrics"]).parent / "family_req.json")
    return out


def _must_items(req: dict) -> list[dict]:
    tiers = req.get("tiers") or {}
    items: list[dict] = []
    for key in ("core", "common"):
        for t in tiers.get(key, []) or []:
            if t.get("lvl") == "must":
                items.append(t)
    return items


def _pay_units(v) -> float | None:
    """中位薪单位自适应：数据集里既可能是「万/月」(17.0)，也可能是元 (17000)。"""
    if v in (None, ""):
        return None
    f = float(v)
    return f * 10000 if f < 200 else f


def _norm(s: str) -> str:
    """去掉空白和标点，只留可比较的字：用户写「IT 技术支持」，族名是「IT技术支持」。"""
    return "".join(ch for ch in str(s or "") if ch.isalnum()).lower()


def _intent_hit(label: str, intent: str) -> bool | None:
    """想法命中：整串或核心词出现在 intent 里。没有 intent 文本时返回 None（不参与计分）。"""
    if not intent:
        return None
    n_intent = _norm(intent)
    if not n_intent:
        return None
    if _norm(label) and _norm(label) in n_intent:
        return True
    core = [p for p in label.replace("／", "/").split("/") if p]
    return any(_norm(c) and _norm(c) in n_intent for c in core)


def _pay_score(median: float | None, floor: float | None) -> tuple[float | None, bool | None]:
    if not floor or median is None:
        return None, None
    if median >= floor:
        return 1.0, True
    if median >= floor * 0.85:
        return 0.6, False
    return 0.0, False


def run(ws: Path | None = None, metrics: str = "", req: str = "",
        families: str = "", profile: str = "", scope: str = "active") -> dict:
    ws = ws or find_workspace()
    d = _defaults(ws)
    mpath = ws / (metrics or d["metrics"])
    rpath = ws / (req or d["req"])
    fpath = ws / (families or d["families"])
    ppath = ws / (profile or d["profile"])

    metrics_doc = _load(mpath)
    req_doc = _load(rpath) if rpath.exists() else {}
    fam_doc = _load(fpath) if fpath.exists() else {}
    prof_doc = _load(ppath) if ppath.exists() else {}

    cprof = prof_doc.get("conditions_profile") or {}
    ident = prof_doc.get("identity") or {}
    intent = (prof_doc.get("intent") or {}).get("raw") or ""
    floor = cprof.get("salary_floor")
    floor = float(floor) if floor not in (None, "") else None

    fam_meta = fam_doc.get("families") or {}
    by_label: dict[str, dict] = {}
    for fid, v in fam_meta.items():
        label = (v or {}).get("label")
        if label:
            by_label[label] = {"id": fid, **(v or {})}

    rows: list[dict] = []
    for label, mm in (metrics_doc.get("families") or {}).items():
        mm = mm or {}
        meta = by_label.get(label) or {}
        rq = req_doc.get(label) or {}
        must = _must_items(rq)
        wsum = sum(float(t.get("rate") or 0) for t in must)
        hit = sum(float(t.get("rate") or 0) for t in must if t.get("mine"))
        skill_fit = (hit / wsum) if wsum else None
        gaps = [t.get("name") for t in sorted(
            [t for t in must if not t.get("mine")],
            key=lambda x: -float(x.get("rate") or 0))][:5]

        median = _pay_units(mm.get("salary_open_median"))
        pay, pay_ok = _pay_score(median, floor)
        intent_hit = _intent_hit(label, intent)

        parts: list[tuple[float, float]] = []          # (权重, 0~1 得分)
        parts.append((WEIGHTS["market"], min(100.0, float(mm.get("match") or 0)) / 100.0))
        if skill_fit is not None:
            parts.append((WEIGHTS["skill"], skill_fit))
        if pay is not None:
            parts.append((WEIGHTS["pay"], pay))
        if intent_hit is not None:
            parts.append((WEIGHTS["intent"], 1.0 if intent_hit else 0.0))
        wtotal = sum(w for w, _ in parts) or 1.0
        score = 100.0 * sum(w * s for w, s in parts) / wtotal

        if score >= VERDICT_NOW:
            verdict = "现在就能投"
        elif score >= VERDICT_PATCH:
            verdict = "补一下再投"
        else:
            verdict = "先不投"

        dims = mm.get("dims") or {}
        why = []
        if skill_fit is not None:
            why.append("must 要求命中 %.0f%%" % (skill_fit * 100))
        if dims.get("thr") is not None:
            why.append("门槛通过率 %.0f%%" % float(dims["thr"]))
        if pay_ok is False and floor:
            why.append("中位薪 %s 低于下限 %s" % (median, int(floor)))
        if intent_hit:
            why.append("你的想法里点到了这个方向")
        if gaps:
            why.append("缺 " + "、".join(str(g) for g in gaps[:3]))

        rows.append({
            "id": meta.get("id") or "",
            "label": label,
            "active": bool(meta.get("active")),
            "conditions": meta.get("conditions"),
            "market_match": round(float(mm.get("match") or 0), 1),
            "band": mm.get("band") or "",
            "sample_n": mm.get("n"),
            "open": mm.get("open"),
            "skill_fit": None if skill_fit is None else round(skill_fit * 100, 1),
            "must_total": len(must),
            "gaps": gaps,
            "median_pay": median,
            "median_pay_wan": None if median is None else round(median / 10000, 1),
            "growth": (dims or {}).get("growth"),
            "pay_ok": pay_ok,
            "intent_hit": intent_hit,
            "score": round(score, 1),
            "verdict": verdict,
            "why": "；".join(why),
        })

    # 默认只看「在投方向」（families.json 里 active 的族）：22 族全排一遍，
    # 结果会被开发类岗位占满，反而看不出该投哪几个。要看全景传 scope=all。
    active_rows = [r for r in rows if r["active"]]
    total_rows = len(rows)
    if scope != "all" and active_rows:
        rows = active_rows
    rows.sort(key=lambda r: (-r["score"], -(r["market_match"] or 0)))
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    return {
        "schema": "analyze/v1",
        "at": now_iso(),
        "inputs": {
            "city": ident.get("city") or "",
            "education": cprof.get("education") or ident.get("education") or "",
            "salary_floor": floor,
            "tech_stack_n": len(cprof.get("tech_stack") or []),
            "intent": intent,
            "metrics": str(mpath.relative_to(ws)).replace("\\", "/"),
            "req": str(rpath.relative_to(ws)).replace("\\", "/") if rpath.exists() else "",
        },
        "weights": WEIGHTS,
        "verdict_thresholds": {"now": VERDICT_NOW, "patch": VERDICT_PATCH},
        "families": rows,
        "recommended": [r["id"] for r in rows if r["id"] and r["score"] >= VERDICT_PATCH][:6],
        "scope": "all" if scope == "all" else "active",
        "stats": {"families": len(rows), "all_families": total_rows,
                  "with_req": sum(1 for r in rows if r["skill_fit"] is not None)},
        "_note": "综合分只用于排序：市场 35%（族市场友好度）+ 技能 25%（must 要求加权命中）+ "
                 "薪资 15%（中位薪 vs 你的下限）+ 想法 25%（你的求职想法是否点名这个方向）。"
                 "缺失项按剩余权重归一化，不补默认值；阈值 65=现在就能投，55=补一下再投。",
    }


def write(out: dict, path: str) -> None:
    write_json(Path(path), out)
