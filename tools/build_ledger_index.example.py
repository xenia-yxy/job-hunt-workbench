#!/usr/bin/env python3
"""离线构建投递台账只读索引（与 GET /api/ledger-index 同口径）。

默认只读：只把摘要打印到 stdout，不写任何文件。
只有显式 --out 才写快照，且输出路径被限制在 workbuddy_out/ 下。

用法：
  python tools/build_ledger_index.example.py
  python tools/build_ledger_index.example.py --out workbuddy_out/ledger_index/index.json
  python tools/build_ledger_index.example.py --compare service.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path: Path):
    """与 jkit/store.py::read_jsonl 一致：空行跳过、坏行跳过。"""
    out = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return out


def outcome_of(rec: dict) -> str:
    """与 jkit/store.py::delivered_index 一致。"""
    return str(rec.get("outcome") or (rec.get("delivery") or {}).get("outcome") or "unknown")


def dedupe_key(rec: dict) -> str:
    """与 jkit/store.py::dedupe_key 逐值一致。"""
    if rec.get("job_id"):
        return str(rec["job_id"])
    js = rec.get("job_snapshot") or rec
    return "noid:%s|%s" % (js.get("company") or js.get("company_name"), js.get("title"))


def build(root: Path) -> dict:
    runs_dir = root / "runs"
    files = sorted((p for p in runs_dir.glob("*/ledger.jsonl") if p.is_file()),
                   key=lambda p: p.parent.name) if runs_dir.is_dir() else []
    runs, events, outcomes = [], [], {}
    lines = parsed = bad = delivered = 0

    for fp in files:
        run_id = fp.parent.name
        raw_lines = [s for s in fp.read_text(encoding="utf-8", errors="replace").splitlines() if s.strip()]
        recs = read_jsonl(fp)
        r_outcomes: dict[str, int] = {}
        r_delivered = 0
        for rec in recs:
            oc = outcome_of(rec)
            outcomes[oc] = outcomes.get(oc, 0) + 1
            r_outcomes[oc] = r_outcomes.get(oc, 0) + 1
            if oc == "delivered":
                delivered += 1
                r_delivered += 1
            snap = rec.get("job_snapshot") or {}
            events.append({
                "run_id": run_id,
                "dedupe_key": dedupe_key(rec),
                "job_id": str(rec.get("job_id") or ""),
                "outcome": oc,
                "delivered": oc == "delivered",
                "verdict": str(rec.get("verdict") or ""),
                "reason": str(rec.get("reason") or ""),
                "at": str(rec.get("at") or rec.get("updated_at") or rec.get("observed_at") or ""),
                "company": str(snap.get("company") or snap.get("company_name") or ""),
                "title": str(snap.get("title") or snap.get("role") or ""),
                "condition_id": str(rec.get("condition_id") or ""),
                "source_query": str(rec.get("source_query") or ""),
            })
        n_lines = len(raw_lines)
        lines += n_lines
        parsed += len(recs)
        bad += n_lines - len(recs)
        cond_rel = "runs/%s/condition.json" % run_id
        runs.append({
            "run_id": run_id,
            "file": fp.relative_to(root).as_posix(),
            "lines": n_lines,
            "parsed": len(recs),
            "bad_lines": n_lines - len(recs),
            "delivered": r_delivered,
            "outcomes": r_outcomes,
            "condition_ref": cond_rel,
            "condition_exists": (root / cond_rel).exists(),
            "condition_note": "旧格式快照，仅展示原文路径（不解析、不回填）",
        })

    return {
        "schema": "ledger_index/v1",
        "readonly": True,
        "source_root": "runs",
        "runs": runs,
        "events": events,
        "totals": {
            "files": len(files),
            "runs": len(runs),
            "lines": lines,
            "parsed": parsed,
            "bad_lines": bad,
            "delivered": delivered,
            "outcomes": outcomes,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="投递台账只读索引（离线，与服务端口径一致）")
    ap.add_argument("--root", default=str(ROOT), help="项目根目录")
    ap.add_argument("--out", default="", help="写出快照；仅允许 workbuddy_out/ 下的相对路径")
    ap.add_argument("--events", action="store_true", help="打印摘要时一并输出事件条数说明")
    ap.add_argument("--compare", default="", help="与服务端返回的 JSON 比对 totals")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    idx = build(root)
    t = idx["totals"]

    if args.compare:
        other = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        ot = other.get("totals", {})
        keys = ("files", "runs", "lines", "parsed", "bad_lines", "delivered")
        diffs = {k: (t.get(k), ot.get(k)) for k in keys if t.get(k) != ot.get(k)}
        print("COMPARE", "PASS" if not diffs else "FAIL", "diffs=", json.dumps(diffs, ensure_ascii=False))
        if diffs:
            return 2

    print(json.dumps({
        "totals": t,
        "events": len(idx["events"]) if args.events else "(use --events)",
    }, ensure_ascii=False, indent=1))

    if args.out:
        out = Path(args.out)
        if out.is_absolute() or ".." in out.parts or (out.parts and out.parts[0] != "workbuddy_out"):
            print("FAIL: --out 只允许 workbuddy_out/ 下的相对路径", out)
            return 2
        target = root / out
        if target.exists():
            print("SKIP: 快照已存在（只新增，不覆盖）：", target)
            return 0
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        print("WROTE", target.relative_to(root).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
