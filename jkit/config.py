"""工作区定位与账号 / run 读写。

工作区是用户自己的求职状态目录（条件、台账、账号），不进仓库。
靠 jkit.toml 向上查找识别。
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any, Optional

TZ = datetime.timezone(datetime.timedelta(hours=8))
MARKER = "jkit.toml"


def now_iso() -> str:
    return datetime.datetime.now(TZ).isoformat(timespec="seconds")


def now_ms() -> str:
    return datetime.datetime.now(TZ).isoformat(timespec="milliseconds")


def find_workspace(start: Optional[Any] = None) -> Path:
    cur = Path(start or os.getcwd()).expanduser().resolve()
    for d in (cur, *cur.parents):
        if (d / MARKER).exists():
            return d
    raise SystemExit("找不到工作区。先运行 `jkit init`，或在工作区目录内执行。")


def workspace_config(ws: Path) -> dict:
    """读 jkit.toml。只支持扁平的 key = "value"，够用就行，不引依赖。"""
    p = ws / MARKER
    out = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("[") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------- 账号 ----------

def load_accounts(ws: Path) -> dict:
    return read_json(ws / "accounts.json", {"accounts": []}) or {"accounts": []}


def pick_account(ws: Path, account_id: Optional[str] = None) -> dict:
    accounts = load_accounts(ws).get("accounts", [])
    if account_id:
        for a in accounts:
            if a.get("account_id") == account_id:
                return a
        raise SystemExit("账号不存在：%s" % account_id)
    if not accounts:
        raise SystemExit("没有可用账号，先运行 `jkit init`")
    return accounts[0]


# ---------- run ----------

def new_run_id() -> str:
    return datetime.datetime.now(TZ).strftime("%Y%m%d-%H%M")


def run_dir(ws: Path, run_id: str) -> Path:
    return ws / "runs" / run_id


def list_runs(ws: Path) -> list:
    base = ws / "runs"
    if not base.is_dir():
        return []
    return sorted(d.name for d in base.iterdir()
                  if d.is_dir() and (d / "ledger.jsonl").exists())
