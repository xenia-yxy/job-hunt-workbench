#!/usr/bin/env python3
"""从调研快照产出「条件草案」——只给建议，不自动生效。

设计约束：
  - 只读 survey/** 下已经存在的快照，不改写、不回填任何调研数据。
  - 草案默认 approved:false，只写 workbuddy_out/condition_drafts/，且只新增不覆盖。
  - 不自动落地到 config/local/conditions.json，不创建 run / condition_id。
  - 不编造搜索词、城市码与阈值：queries 只取快照里已有的字符串，filters 留空，city 为 null。
  - 安全阀（限速 / 冷却 / 日上限 / 空跑熔断）由 jkit/policy.py 与 jkit/guard.py 定义，草案不得覆盖或绕过。

用法：
  python tools/propose_conditions.example.py --survey <相对 survey 的快照目录>
  python tools/propose_conditions.example.py --survey <目录> --out
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path("workbuddy_out") / "condition_drafts"


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def pick_survey(root: Path, explicit: str) -> Path:
    if explicit:
        d = Path(explicit)
        if not d.is_absolute():
            d = root / d
        if not (d / "metrics2.json").exists():
            raise SystemExit("FAIL: 指定目录缺 metrics2.json：%s" % d)
        return d
    cands = sorted({p.parent for p in (root / "survey").glob("*/metrics2.json")})
    if not cands:
        raise SystemExit("FAIL: 未找到 survey/*/metrics2.json；请用 --survey 显式指定快照目录。")
    if len(cands) > 1:
        print("发现多个调研快照，请用 --survey 明确指定（不自动猜测）：")
        for c in cands:
            print("  -", c.relative_to(root).as_posix())
        raise SystemExit(2)
    return cands[0]


def build(root: Path, survey: Path, top_n: int, top_skills: int) -> dict:
    metrics = load_json(survey / "metrics2.json") or {}
    skills = load_json(survey / "skill_stats.json") or {}
    queries_raw = load_json(survey / "queries.json") or {}

    families = metrics.get("families") or {}
    rows = []
    for name, v in families.items():
        if not isinstance(v, dict):
            continue
        rows.append({
            "name": name,
            "rank": v.get("rank"),
            "match": v.get("match"),
            "band": v.get("band"),
            "n": v.get("n"),
            "open_rate": v.get("open_rate"),
            "salary_open_median": v.get("salary_open_median"),
            "junior": v.get("junior"),
            "evidence": "metrics2.json / families",
        })
    rows.sort(key=lambda x: (
        x["rank"] is None,
        x["rank"] if isinstance(x["rank"], int) else 9999,
        -(x["match"] or 0),
        x["name"],
    ))
    top = rows[:top_n]

    must = skills.get("must") or {}
    focus = [{"name": k, "jd_count": v, "evidence": "skill_stats.json / must"}
             for k, v in sorted(must.items(), key=lambda kv: (-kv[1], kv[0]))[:top_skills]]

    # 搜索词只抄录快照里已有的字符串，不生成、不扩写；selected 全为 False
    queries = []
    if isinstance(queries_raw, dict):
        for k, v in queries_raw.items():
            if isinstance(v, dict) and v.get("query"):
                queries.append({
                    "key": k,
                    "family": v.get("family", ""),
                    "query": v["query"],
                    "selected": False,
                    "evidence": "queries.json",
                })

    rel = survey.relative_to(root).as_posix()
    stamp = hashlib.sha256(
        (rel + "|" + json.dumps([r["name"] for r in top], ensure_ascii=False)).encode("utf-8")
    ).hexdigest()[:10]

    return {
        "schema": "condition_draft/v1",
        "id": "draft-" + stamp,
        "approved": False,
        "_note": "草案，不自动生效。需人工或 WorkBuddy 会话审阅后，另新建条件文件与新的 run / condition_id。",
        "generated_from": {
            "survey_dir": rel,
            "metrics": rel + "/metrics2.json",
            "skill_stats": rel + "/skill_stats.json",
            "queries": rel + "/queries.json",
        },
        "evidence": {
            "total": metrics.get("total"),
            "total_open": metrics.get("total_open"),
            "families_total": len(rows),
            "families_selected": [r["name"] for r in top],
            "jd_total": skills.get("total_jd"),
        },
        "suggested": {
            "families": top,
            "skill_focus": focus,
            "queries": queries,
            "filters": {},
            "city": None,
            "city_code": None,
            "_city_note": "城市与城市码必须显式配置；缺失即报错，禁止静默兜底。",
        },
        "guardrails": {
            "policy_ref": "jkit/policy.py",
            "guard_ref": "jkit/guard.py",
            "note": "限速、冷却、日上限、空跑熔断由安全阀定义，条件草案不得覆盖或绕过；平台 selectors 与城市码表留在平台适配器目录。",
        },
        "next_step": "审阅后由人工或 WorkBuddy 会话新建条件文件与 run；本脚本不写 config/local/conditions.json，不触发任何采集或投递。",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="从调研快照产出条件草案（只读，默认不写盘）")
    ap.add_argument("--root", default=str(ROOT), help="项目根目录")
    ap.add_argument("--survey", default="", help="调研快照目录（含 metrics2.json）；多快照时必须显式指定")
    ap.add_argument("--top", type=int, default=5, help="建议岗位族数量")
    ap.add_argument("--top-skills", type=int, default=10, help="建议关注技能数量")
    ap.add_argument("--out", action="store_true", help="写出草案到 workbuddy_out/condition_drafts/（只新增不覆盖）")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    survey = pick_survey(root, args.survey)
    draft = build(root, survey, max(1, args.top), max(0, args.top_skills))

    if args.out:
        target = root / OUT_DIR / (draft["id"] + ".json")
        if target.exists():
            print("SKIP: 草案已存在（只新增，不覆盖）：", target.relative_to(root).as_posix())
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
            print("WROTE", target.relative_to(root).as_posix())
    print(json.dumps({
        "id": draft["id"],
        "approved": draft["approved"],
        "survey_dir": draft["generated_from"]["survey_dir"],
        "families_selected": draft["evidence"]["families_selected"],
        "queries_available": len(draft["suggested"]["queries"]),
        "skill_focus": len(draft["suggested"]["skill_focus"]),
        "city": draft["suggested"]["city"],
        "written": bool(args.out),
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
