"""真人节奏：停留、滚动、悬停、逐字输入、休息，以及时间预算闭环。

三条设计约束：

1. **唯一节奏源**。所有 sleep 都从这里出，一处调参、一处测试。散落在 pipeline
   里的 time.sleep 会让「快一点/慢一点」变成十处改动。
2. **用原生动作**。bsk 的 wheel / hover / click / press 是真实输入事件
   （isTrusted=true）；JS 里的 element.click() 是合成事件，行为序列再像也过不了
   事件可信度这一层。所以滚动、悬停走 drv.wheel/drv.hover，不用 evaluate。
3. **随机必须可注入 seed**，`fast=True` 时 sleep 归零、手势全部跳过——否则 65 条
   pytest 会因为真实等待跑成分钟级。

用户口径（2026-11-13）：「停留可以短，但滑动这些要有；休息别太长，100 条要在
30-40 分钟内跑完」。所以 fast 档是「短停留 + 有动作 + 短休息」，不是「零动作」。
"""
from __future__ import annotations

import random
import time
from typing import Optional

from . import policy


class Humanizer:
    def __init__(self, profile: str = policy.DEFAULT_PACE, seed: Optional[int] = None,
                 fast: bool = False, budget=None):
        self.profile = profile
        self.p = policy.pace(profile)
        self.rng = random.Random(seed)
        self.fast = bool(fast)
        self.budget = budget
        self.mult = 1.0          # 由 Budget 闭环调整：落后压缩、超前放慢
        self.slept = 0.0         # 累计等待秒数，用于耗时报告

    # ---------- 基础 ----------

    def span(self, key: str) -> tuple:
        lo, hi = self.p[key]
        lo, hi = lo * self.mult, hi * self.mult
        # 任何档位都不把详情停留压到 1 秒以下：太快本身就是机器特征
        if key == "detail_dwell":
            lo, hi = max(1.0, lo), max(1.2, hi)
        return lo, hi

    def rand(self, key: str) -> float:
        lo, hi = self.span(key)
        return self.rng.uniform(lo, hi) if hi > lo else lo

    def sleep(self, sec: float) -> None:
        if self.fast or sec <= 0:
            return
        time.sleep(sec)
        self.slept += sec

    def pause(self, key: str) -> float:
        sec = self.rand(key)
        self.sleep(sec)
        return sec

    def tick(self, delivered: int) -> dict:
        """接预算反馈。返回 Budget.tick 的结果，供日志打印。"""
        if not self.budget:
            return {"mult": self.mult}
        info = self.budget.tick(delivered)
        self.mult = float(info.get("mult") or 1.0)
        return info

    # ---------- 手势 ----------

    def _scroll_steps(self, drv, times: int) -> bool:
        ok = True
        for _ in range(max(0, int(times))):
            dy = int(self.rand("scroll_px"))
            try:
                # drv.scroll 会先试原生 wheel，确认滚不动再退回 JS；
                # 直接用 wheel 会出现「返回成功但页面没动」的假动作。
                mover = getattr(drv, "scroll", None) or drv.wheel
                if not mover(dy):
                    ok = False
            except Exception:
                ok = False
            self.sleep(self.rand("scroll_gap"))
        return ok

    def _dwell(self, drv, turns_key: str, dwell_key: str) -> None:
        """滚几下 + 停留，两者合计约等于 dwell 区间（不是叠加）。"""
        if self.fast:
            return
        target = self.rand(dwell_key)
        t0 = time.time()
        n = self.rng.randint(*self.p[turns_key])
        self._scroll_steps(drv, n)
        remain = target - (time.time() - t0)
        if remain > 0:
            self.sleep(remain)
        else:
            # 滚动本身已经超过停留目标，补一个短停避免「滚完立刻跳走」
            self.sleep(self.rand("scroll_gap"))

    def dwell_detail(self, drv) -> None:
        """详情页：滚动 1–2 下，总停留 1.2–2.6 秒（fast 档）。"""
        self._dwell(drv, "detail_scrolls", "detail_dwell")

    def browse_list(self, drv) -> None:
        """列表页：滚 1–2 下再抓卡/翻页，避免「零交互」特征。"""
        self._dwell(drv, "list_scrolls", "list_dwell")

    def hover_click(self, drv, selector: str) -> bool:
        """原生悬停 → 短停 → 原生点击（不在这里点，点击由调用方用 evaluate_once 做）。

        返回是否悬停成功，失败不阻断（选择器失效时照旧走原路径）。
        """
        if self.fast or not selector:
            return False
        try:
            ok = drv.hover(selector)
        except Exception:
            return False
        self.pause("before_click")
        return bool(ok)

    def after_apply(self) -> None:
        self.pause("after_apply")

    def gap(self) -> None:
        self.pause("item_gap")

    def page_gap(self) -> None:
        self.pause("page_gap")

    def query_gap(self) -> None:
        self.pause("query_gap")

    def rest_if_due(self, count: int, every_key: str = "rest_every") -> bool:
        every = int(self.p.get(every_key) or 0)
        if not every or count <= 0 or count % every:
            return False
        self.sleep(self.rand("rest_for"))
        return True

    def filter_gap(self) -> None:
        self.pause("filter_gap")

    def poll_gap(self) -> None:
        """等 SPA 出新结果时每次轮询前的小停顿。"""
        lo, hi = policy.SPA_POLL
        self.sleep(self.rng.uniform(lo, hi))

    # ---------- 输入 ----------

    def type_into(self, drv, sel: str, text: str, read_back=None) -> bool:
        """在指定输入框里填词，并**回读校验**。不按回车 —— 由调用方决定回车还是点搜索。

        为什么这样写：
          · 中文要过输入法，逐字 press 打不出来；真实用户中文也是 IME 一次性
            commit，等价于 fill 触发的 input 事件。所以用「fill + 按字数停顿」
            模拟打字耗时。
          · `fast` 只关掉 sleep，**不关掉动作**。以前 fast 直接 return False，
            导致单测和 dry-run 里搜索框根本没被碰过，等于绕过整条链路。

        返回 False 表示「词没进到框里」，调用方应报错退出，不要硬按回车。
        """
        if not sel or not text:
            return False
        try:
            drv.click(sel)
            self.sleep(self.rand("filter_gap") * 0.5)
            drv.press("Ctrl+A")
            if not drv.fill(sel, text):
                return False
            self.sleep(min(1.2, len(text) * self.rand("type_per_char")))
        except Exception:
            return False
        if read_back is not None:
            # 真实浏览器的受控 input 在 fill 之后**不是立刻**反映到 DOM：
            # fast 模式把拟人停顿关成了 0，如果这里立刻回读，会读到空值、
            # 误报「关键词没进搜索框」（2026-11-16 实测：wait 700ms 后正常）。
            # 所以回读留一个**最小同步等待 + 重试**——fast 只省拟人停顿，
            # 不能省掉必要的 DOM 同步。
            ok = False
            for _ in range(8):
                try:
                    if (read_back() or "").strip() == text.strip():
                        ok = True
                        break
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.12)
            if not ok:
                return False
        return True
