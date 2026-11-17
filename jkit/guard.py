"""跨轮次安全阀：冷却、速率窗口、空列表熔断、日预算、岗位池游标。

为什么单独一个模块：这些状态必须跨进程、跨轮次存活——进程被杀、浏览器重启、
隔天再跑，都要记得「刚才被限流过」。放进 runs/<run_id>/state.json 会随这一轮
结束而失效，所以落在工作区级的 runs/guard.json（与 delivery_progress.json 同级）。

2026-11-13 事故复盘对应关系：
  · 12:44 开始出现「列表未出现」，代码只打日志继续翻 → 现在连续 2 次即 note_empty + 熔断
  · 11 次/分的恒定请求密度 → 现在滚动 60 秒窗口，超过 10 次/分放慢、超过 14 次分熔断
  · 命中「账户存在异常行为」后还在重试 → 现在命中即冷却 6 小时，绝不试探
"""
from __future__ import annotations

import datetime
import json
import time
from pathlib import Path
from typing import Optional

from . import policy, store
from .config import TZ, now_iso, read_json

GUARD = Path("runs") / "guard.json"

# 页面级风控文案（命中即停）。BOSS 的拦截页/弹窗都带这些词。
RISK_TEXT_PATTERNS = (
    "存在异常行为", "暂时被限制访问", "访问受限", "限制访问",
    "安全验证", "人机验证", "请完成验证", "拖动滑块", "滑动验证",
    "操作过于频繁", "网络环境异常", "系统检测到异常", "captcha",
)


class RiskBlocked(RuntimeError):
    """页面出现风控/验证文案。调用方应立即收尾，不重试。"""

    def __init__(self, reason: str, sample: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.sample = sample


class GuardTripped(RuntimeError):
    """速率或连续空列表触发熔断。"""


def now_dt() -> datetime.datetime:
    return datetime.datetime.now(TZ)


def parse_dt(s: Optional[str]) -> Optional[datetime.datetime]:
    if not s:
        return None
    try:
        d = datetime.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def load(ws: Path) -> dict:
    d = read_json(ws / GUARD, default=None) or {}
    if not isinstance(d, dict):
        d = {}
    d.setdefault("version", 1)
    d.setdefault("accounts", {})
    d.setdefault("pool", {})
    return d


def save(ws: Path, data: dict) -> None:
    """原子写：先写 .tmp 再 replace，避免进程被杀时留下半截 JSON。"""
    p = ws / GUARD
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def risk_hit(text: str) -> Optional[str]:
    """页面文本是否命中风控关键词。返回命中的那个词，便于写日志。"""
    t = text or ""
    for pat in RISK_TEXT_PATTERNS:
        if pat in t:
            return pat
    return None


class Guard:
    """一轮投递的安全闸门。所有写操作立刻落盘，不攒到最后。"""

    def __init__(self, ws: Path, account: str = "main",
                 daily_limit: Optional[int] = None):
        self.ws = ws
        self.account = (account or "main").strip() or "main"
        self.data = load(ws)
        acc = self.data["accounts"].setdefault(self.account, {})
        acc.setdefault("empty_streak", 0)
        acc.setdefault("daily", {})
        acc.setdefault("risk_hits", [])
        acc.setdefault("cooldown_until", None)
        self.acc = acc
        self.daily_limit = int(daily_limit or policy.DAILY_LIMIT)
        self._req: list = []          # 本地请求时间戳（不落盘，跨进程重置）
        self._daily_cache: Optional[dict] = None
        self.tripped: Optional[str] = None

    # ---------- 日预算 ----------

    def _daily(self) -> dict:
        if self._daily_cache is not None:
            return self._daily_cache
        cnt: dict = {}
        for f in sorted((self.ws / "runs").glob("*/ledger.jsonl")):
            for row in store.read_jsonl(f):
                outcome = row.get("outcome") or (row.get("delivery") or {}).get("outcome")
                if outcome != "delivered":
                    continue
                # 历史台账没有账号字段，按主账号计（改造前只有主账号在跑）
                acct = (row.get("account") or (row.get("sourcing") or {}).get("account")
                        or "main")
                if acct != self.account:
                    continue
                day = str(row.get("at") or "")[:10]
                if day:
                    cnt[day] = cnt.get(day, 0) + 1
        self._daily_cache = cnt
        return cnt

    def delivered_on(self, day: Optional[str] = None) -> int:
        day = day or now_dt().date().isoformat()
        return int(self._daily().get(day, 0))

    def budget_left(self, day: Optional[str] = None) -> int:
        return max(0, self.daily_limit - self.delivered_on(day))

    # ---------- 冷却与熔断 ----------

    def cooldown_until(self) -> Optional[datetime.datetime]:
        d = parse_dt(self.acc.get("cooldown_until"))
        if d and d > now_dt():
            return d
        return None

    def preflight(self) -> tuple:
        """开跑前自检，返回 (可否开跑, 说明)。冷却中或超额时零请求直接退出。"""
        until = self.cooldown_until()
        if until:
            mins = int((until - now_dt()).total_seconds() // 60) + 1
            return False, "冷却中（%s 解除，约 %d 分钟）：%s" % (
                until.isoformat(timespec="minutes"), mins,
                self.acc.get("cooldown_reason") or "上次触发安全阀")
        left = self.budget_left()
        if left <= 0:
            return False, "今日已达账号日上限 %d 条（%s）" % (
                self.daily_limit, self.account)
        return True, "可跑，今日剩余额度 %d 条" % left

    def trip(self, reason: str, minutes: Optional[int] = None) -> None:
        minutes = int(minutes or policy.COOLDOWN_MIN)
        until = now_dt() + datetime.timedelta(minutes=minutes)
        self.acc["cooldown_until"] = until.isoformat(timespec="seconds")
        self.acc["cooldown_reason"] = reason
        self.tripped = reason
        self.acc.setdefault("risk_hits", []).append(
            {"at": now_iso(), "kind": "trip", "reason": reason, "minutes": minutes})
        save(self.ws, self.data)

    def record_risk(self, sample: str, reason: str) -> None:
        self.acc.setdefault("risk_hits", []).append(
            {"at": now_iso(), "kind": "page", "reason": reason, "sample": (sample or "")[:200]})
        save(self.ws, self.data)

    # ---------- 速率窗口 ----------

    def note_request(self, n: float = 1.0) -> None:
        """记一次请求当量。

        2026-11-13 起 driver 会按动作类型传权重（policy.ACTION_WEIGHT），
        口径统一为「等效整页请求当量/分」，所以这里存 (时间, 权重) 而不是
        展开成 n 条时间戳。
        """
        t = time.time()
        self._req.append((t, float(n)))
        self._req = [x for x in self._req if t - x[0] <= 60]

    def rate_per_min(self) -> float:
        t = time.time()
        self._req = [x for x in self._req if t - x[0] <= 60]
        return round(sum(w for _, w in self._req), 2)

    def rate_state(self) -> str:
        """ok / soft（放慢） / hard（熔断）。"""
        r = self.rate_per_min()
        if r >= policy.RATE_HARD:
            return "hard"
        if r >= policy.RATE_SOFT:
            return "soft"
        return "ok"

    def check_rate(self) -> None:
        if self.rate_state() == "hard":
            self.trip("请求速率 %d 次/分 ≥ %d" % (self.rate_per_min(), policy.RATE_HARD), 15)
            raise GuardTripped("请求速率过高（%.0f 次/分），已熔断 15 分钟"
                               % self.rate_per_min())

    # ---------- 空列表 ----------

    def note_empty(self) -> bool:
        """记一次「列表未出现」。返回 True 表示已触发熔断。"""
        n = int(self.acc.get("empty_streak") or 0) + 1
        self.acc["empty_streak"] = n
        save(self.ws, self.data)
        if n >= policy.EMPTY_TRIP:
            self.trip("连续 %d 次列表为空（疑似软限流）" % n, policy.COOLDOWN_MIN)
            return True
        return False

    def note_ok(self) -> None:
        if self.acc.get("empty_streak"):
            self.acc["empty_streak"] = 0
            save(self.ws, self.data)

    # ---------- 页面探针 ----------

    def probe(self, drv, adapter) -> Optional[str]:
        """读当前页面文本查风控词。命中即记冷却并抛 RiskBlocked。"""
        try:
            text = adapter.page_text(drv) or ""
        except Exception:
            return None
        hit = risk_hit(text)
        if hit:
            self.record_risk(text[:300], hit)
            self.trip("风控页：%s" % hit, policy.RISK_COOLDOWN_MIN)
            raise RiskBlocked("风控页：%s" % hit, text[:200])
        return None

    # ---------- 搜索词记忆 ----------
    #
    # 记住某个 run 今天搜过哪些词，方便下次接着搜；不参与任何判定。
    def note_query(self, run_id: str, query: str) -> None:
        today = now_dt().date().isoformat()
        rec = self.data.setdefault("queries", {}).setdefault(run_id or "default", {})
        if rec.get("date") != today:
            rec["date"] = today
            rec["done"] = []
        done = rec.setdefault("done", [])
        if query not in done:
            done.append(query)
        save(self.ws, self.data)

    # ---------- 收尾 ----------

    def finish(self, delivered: int, stopped_reason: str = "") -> None:
        today = now_dt().date().isoformat()
        if delivered:
            self._daily_cache = None
            self.acc.setdefault("daily", {})[today] = self.delivered_on(today)
        self.acc["last_run"] = {"at": now_iso(), "delivered": int(delivered),
                                "stopped_reason": stopped_reason,
                                "rate_per_min": round(self.rate_per_min(), 1)}
        if stopped_reason:
            self.acc["last_stopped_reason"] = stopped_reason
        save(self.ws, self.data)


class Budget:
    """时间预算闭环：落后就压缩节奏，超前就放慢，到点收工。

    用户口径是「100 条要在 30-40 分钟内跑完，冲突时保时间」——所以这里不需要
    精确排程，只要保证到 40 分钟一定收尾，且中途能自我纠偏。
    """

    def __init__(self, minutes: Optional[float] = None, target: int = 0):
        self.minutes = float(minutes or policy.TIME_BUDGET_MIN)
        self.target = int(target or 0)
        self.t0 = time.time()

    def elapsed(self) -> float:
        return time.time() - self.t0

    def left(self) -> float:
        return max(0.0, self.minutes * 60 - self.elapsed())

    def expired(self) -> bool:
        return self.left() <= 0

    def tick(self, delivered: int) -> dict:
        """返回 {"mult": 节奏系数, "behind": 落后比例, "left_min": 剩余分钟}。"""
        spent = max(0.02, self.elapsed() / (self.minutes * 60))
        if not self.target:
            return {"mult": 1.0, "behind": 0.0, "left_min": self.left() / 60}
        # 刚开始的一分钟样本太小，别急着加速/减速
        if self.elapsed() < 60 or delivered < 5:
            return {"mult": 1.0, "behind": 0.0, "left_min": round(self.left() / 60, 1)}
        ratio = (delivered / float(self.target)) / spent
        if ratio < 0.85:
            mult = 0.7
        elif ratio > 1.15:
            mult = 1.3
        else:
            mult = 1.0
        return {"mult": mult, "behind": round(1 - ratio, 3),
                "left_min": round(self.left() / 60, 1)}
