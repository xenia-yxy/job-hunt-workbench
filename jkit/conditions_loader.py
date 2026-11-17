"""投递条件旁路加载器（不改动 jkit/conditions.py 的 DEFAULT）。

三级优先级：显式路径 > config/local/conditions.json > config/conditions.example.json。
加载后先做**显式关键字段校验**再交给 Conditions.from_dict，避免 _deep_merge
从 DEFAULT 静默继承城市码 / 城市 / 搜索词 / 筛选条件。

用法：
  python -m jkit.conditions_loader --check [FILE]
  from jkit.conditions_loader import load_conditions
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .conditions import Conditions, DEFAULT, apply_profile
from .errors import ConditionError

#: 外部条件必须自己给全的四块；缺任何一块都不许从 DEFAULT 继承
REQUIRED = ("city", "city_code", "queries", "filters")
REQUIRED_HINT = "不从 DEFAULT 静默继承城市码与筛选口径"


def workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _read_json(p: Path) -> dict[str, Any] | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def candidate_paths(root: Path, explicit: str | Path | None = None) -> list[Path]:
    if explicit:
        p = Path(explicit)
        return [p if p.is_absolute() else root / p]
    return [
        root / "config" / "local" / "conditions.json",
        root / "config" / "conditions.example.json",
    ]


def resolve_profile(root: Path, data: dict[str, Any], used: Path | None = None) -> dict[str, Any] | None:
    """读 `profile_ref` 指向的画像。

    有引用但读不到就报错——绝不退回「只有本族差异、共用字段全空」的半份条件，
    那会让 apply 用 DEFAULT 的深圳码静默跑起来。
    """
    ref = (data or {}).get("profile_ref")
    if not ref:
        return None
    p = root / "config" / "local" / ("%s.json" % str(ref))
    v = _read_json(p)
    if not isinstance(v, dict) or not v:
        raise ConditionError(
            "条件 %s 引用了画像 profile_ref=%s，但读不到 %s"
            % (used or "(未知)", ref, p))
    return v


def load_conditions(path: str | Path | None = None, root: Path | None = None) -> Conditions:
    """按优先级载入条件；缺关键字段立即抛 ConditionError。"""
    ws = root or workspace_root()
    paths = candidate_paths(ws, path)
    data = None
    used: Path | None = None
    for p in paths:
        v = _read_json(p)
        if isinstance(v, dict) and v:
            data, used = v, p
            break
    if data is None:
        raise ConditionError(
            "未找到可用的条件文件（已尝试：%s）" % ", ".join(str(p) for p in paths))
    # 先展开画像引用，再校验四块必备字段 —— 否则精简后的条件会被判「缺 city」
    data = apply_profile(data, resolve_profile(ws, data, used))
    missing = [k for k in REQUIRED if not data.get(k)]
    if missing:
        raise ConditionError(
            "条件 %s 缺少关键字段 %s；%s" % (used, ", ".join(missing), REQUIRED_HINT))
    cond = Conditions.from_dict(data)
    cond.validate()
    return cond


def self_check(path: str | Path | None = None) -> int:
    """只读自检：打印来源、城市码与搜索词数量；DEFAULT 保持原样。"""
    cond = load_conditions(path)
    print(json.dumps({
        "source": str(path) if path else "auto(local > example)",
        "id": cond.id,
        "city": cond.city,
        "city_code": cond.city_code,
        "queries": len(cond.queries),
        "filters_platforms": sorted(cond.filters),
        "default_untouched": DEFAULT["city_code"].get("boss") == "101280600",
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="投递条件旁路加载器自检")
    ap.add_argument("--check", nargs="?", const="", default=None, metavar="FILE",
                    help="校验条件文件；不带值则按 local > example 自动查找")
    args = ap.parse_args()
    if args.check is not None:
        try:
            raise SystemExit(self_check(args.check or None))
        except ConditionError as e:
            print("ConditionError: %s" % e)
            raise SystemExit(2)
    print(__doc__)
