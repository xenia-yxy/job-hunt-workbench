"""台账读写。

每个 run 目录下三个 append-only 的 jsonl：cards / details / ledger。
投递不可逆，中途进程被杀不能丢掉已经投出去的记录——所以永远追加，从不批量重写。

台账行里带 ``condition_id`` 与 ``platform``，不再有「线路」概念：条件是一份
外部 JSON，换条件就是换一个 run，历史结论靠 job_id 去重，不需要目录结构配合。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .config import new_run_id, now_iso, read_json, run_dir, write_json

KINDS = ("cards", "details", "ledger")


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return out


def append_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def create_run(ws: Path, conditions, platform: str, mode: str = "live") -> dict:
    """新建一个 run，写入条件快照与空台账。"""
    run_id = new_run_id()
    base = run_dir(ws, run_id)
    while base.exists():
        run_id += "b"
        base = run_dir(ws, run_id)
    base.mkdir(parents=True, exist_ok=True)

    cond = dict(conditions.to_dict())
    cond.update({"platform": platform, "mode": mode, "created_at": now_iso()})
    state = {
        "run_id": run_id,
        "condition_id": conditions.id,
        "platform": platform,
        "mode": mode,
        "state": "RUNNING",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "cursor": 0,
        "processed": [],
        "delivered": 0,
        "stopped_reason": "",
    }
    write_json(base / "condition.json", cond)
    write_json(base / "state.json", state)
    for k in KINDS:
        (base / ("%s.jsonl" % k)).touch()
    return state


def load_state(ws: Path, run_id: str) -> dict:
    d = read_json(run_dir(ws, run_id) / "state.json")
    if d is None:
        raise SystemExit("run 不存在：%s" % run_id)
    return d


def save_state(ws: Path, run_id: str, state: dict) -> None:
    state["updated_at"] = now_iso()
    write_json(run_dir(ws, run_id) / "state.json", state)


def ledger_of(ws: Path, run_id: str) -> list:
    return read_jsonl(run_dir(ws, run_id) / "ledger.jsonl")


def dedupe_key(rec: dict) -> str:
    """去重键：优先 job_id；缺失时退回「公司|标题」，避免漏计。"""
    if rec.get("job_id"):
        return str(rec["job_id"])
    js = rec.get("job_snapshot") or rec
    return "noid:%s|%s" % (js.get("company") or js.get("company_name"),
                           js.get("title"))


def delivered_index(ws: Path) -> dict:
    """全工作区已投递索引：{dedupe_key: run_id}。跨平台、跨条件全局生效。"""
    idx: dict[str, str] = {}
    runs = ws / "runs"
    if not runs.is_dir():
        return idx
    for f in runs.glob("*/ledger.jsonl"):
        for rec in read_jsonl(f):
            outcome = rec.get("outcome") or (rec.get("delivery") or {}).get("outcome")
            if outcome == "delivered":
                idx[dedupe_key(rec)] = f.parent.name
    return idx


def settled_index(ws: Path, latest_runs: int = 50) -> set:
    """已经判过的岗位（任何结论都算），用于流式投递跳过重复判定。

    为什么不能只靠 delivered_index：翻页时同一批卡会重复出现，重复读面板 +
    重复判定既费时间又费请求，而结论不会变（条件没改的前提下）。
    条件变了要重判就加 ``--recheck``。
    """
    out: set[str] = set()
    runs = ws / "runs"
    if not runs.is_dir():
        return out
    dirs = [d for d in runs.iterdir() if d.is_dir()]
    dirs.sort(key=lambda d: d.name, reverse=True)
    for d in dirs[:latest_runs]:
        f = d / "ledger.jsonl"
        if not f.exists():
            continue
        for rec in read_jsonl(f):
            out.add(dedupe_key(rec))
    return out


def append_ledger(ws: Path, run_id: str, records: list) -> int:
    """写台账并推进 state 游标。records 需自带 condition_id。"""
    base = run_dir(ws, run_id)
    n = append_jsonl(base / "ledger.jsonl", records)
    state = load_state(ws, run_id)
    state["cursor"] = state.get("cursor", 0) + n
    state["processed"] = sorted({r.get("job_id") for r in records if r.get("job_id")}
                                | set(state.get("processed") or []))
    state["delivered"] = state.get("delivered", 0) + sum(
        1 for r in records
        if (r.get("delivery") or {}).get("outcome") == "delivered")
    save_state(ws, run_id, state)
    return n
