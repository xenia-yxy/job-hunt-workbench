"""安全阀、节奏、投递按钮的单测。

这些是 2026-11-13 事故后加的防线，必须被钉住：
  · 冷却期内零请求退出
  · 连续 2 次空列表就熔断
  · 速率超阈值先放慢再熔断
  · 时间预算落后时压缩节奏、到点收工
  · 找不到投递按钮时一次都不点（不可逆动作点两次最危险）
  · 卡片流是「一页一页交出去」，不是先把所有页抓完
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from jkit import feed, guard, humanize, panel, policy
from jkit.conditions import Conditions
from jkit.config import now_iso
from jkit.platforms.boss import Boss


def _ledger_row(job_id="a", account="main", outcome="delivered", at=None):
    return {"job_id": job_id, "at": at or now_iso(), "account": account,
            "outcome": outcome}


class TestGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "runs").mkdir()

    def _ledger(self, rows, run="r1"):
        d = self.tmp / "runs" / run
        d.mkdir(exist_ok=True)
        (d / "ledger.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")

    def test_cooldown_blocks_preflight(self):
        g = guard.Guard(self.tmp, "main")
        g.trip("测试熔断", 30)
        ok, why = guard.Guard(self.tmp, "main").preflight()
        self.assertFalse(ok)
        self.assertIn("冷却", why)

    def test_accounts_are_isolated(self):
        guard.Guard(self.tmp, "main").trip("x", 30)
        ok, _ = guard.Guard(self.tmp, "alt").preflight()
        self.assertTrue(ok, "主账号冷却不应连累另一个账号")

    def test_two_empty_lists_trip(self):
        g = guard.Guard(self.tmp, "main")
        self.assertFalse(g.note_empty())
        self.assertTrue(g.note_empty())
        data = guard.load(self.tmp)
        self.assertIsNotNone(data["accounts"]["main"]["cooldown_until"])
        self.assertFalse(guard.Guard(self.tmp, "main").preflight()[0])

    def test_success_resets_streak(self):
        g = guard.Guard(self.tmp, "main")
        g.note_empty()
        g.note_ok()
        self.assertEqual(guard.load(self.tmp)["accounts"]["main"]["empty_streak"], 0)

    def test_rate_soft_then_hard(self):
        g = guard.Guard(self.tmp, "main")
        for _ in range(policy.RATE_SOFT):
            g.note_request()
        self.assertEqual(g.rate_state(), "soft")
        for _ in range(policy.RATE_HARD - policy.RATE_SOFT):
            g.note_request()
        self.assertEqual(g.rate_state(), "hard")
        with self.assertRaises(guard.GuardTripped):
            g.check_rate()

    def test_daily_budget_counts_only_own_account(self):
        self._ledger([_ledger_row("a", "main"), _ledger_row("b", "main"),
                      _ledger_row("c", "alt")])
        self.assertEqual(guard.Guard(self.tmp, "main").delivered_on(), 2)
        self.assertEqual(guard.Guard(self.tmp, "main").budget_left(), 98)
        self.assertEqual(guard.Guard(self.tmp, "alt").budget_left(), 99)

    def test_legacy_ledger_shape_still_counted(self):
        """老台账用 delivery.outcome / sourcing.account，新台账用顶层字段，两种都要认。"""
        self._ledger([{"job_id": "x", "at": now_iso(),
                       "delivery": {"outcome": "delivered"},
                       "sourcing": {"account": "main"}}])
        self.assertEqual(guard.Guard(self.tmp, "main").delivered_on(), 1)


class TestBudget(unittest.TestCase):
    def test_behind_compresses_pace(self):
        b = guard.Budget(minutes=10, target=100)
        b.t0 = time.time() - 9 * 60          # 已过 90%
        info = b.tick(10)                    # 只完成 10%
        self.assertEqual(info["mult"], 0.7)

    def test_ahead_slows_down(self):
        b = guard.Budget(minutes=10, target=100)
        b.t0 = time.time() - 60              # 只过 10%
        info = b.tick(90)
        self.assertEqual(info["mult"], 1.3)

    def test_expires(self):
        b = guard.Budget(minutes=1, target=10)
        b.t0 = time.time() - 61
        self.assertTrue(b.expired())


class TestHumanizer(unittest.TestCase):
    def test_seed_makes_it_reproducible(self):
        a, b = humanize.Humanizer(seed=7), humanize.Humanizer(seed=7)
        lo, hi = a.span("detail_dwell")
        seq_a = [a.rand("detail_dwell") for _ in range(5)]
        seq_b = [b.rand("detail_dwell") for _ in range(5)]
        self.assertEqual(seq_a, seq_b)
        for v in seq_a:
            self.assertGreaterEqual(v, lo)
            self.assertLessEqual(v, hi)

    def test_dwell_never_below_one_second(self):
        h = humanize.Humanizer(seed=1)
        h.mult = 0.1
        lo, _ = h.span("detail_dwell")
        self.assertGreaterEqual(lo, 1.0)

    def test_fast_mode_does_not_sleep(self):
        h = humanize.Humanizer(fast=True)
        t0 = time.time()
        h.dwell_detail(object())
        h.gap()
        h.after_apply()
        self.assertLess(time.time() - t0, 0.05)


class _NoBtnDrv:
    """面板里没有可点的投递按钮。"""

    def __init__(self, label=None, ready=False):
        self.clicks = []
        self.label = label
        self.ready = ready

    def click(self, sel):
        self.clicks.append(sel)
        return True

    def scroll_to(self, sel):
        return True

    def press(self, key, selector=None):
        return True

    def evaluate(self, js, retries=3):
        return {"ok": True, "value": json.dumps(
            {"ok": True, "active_idx": 0, "chat_label": self.label,
             "chat_ready": self.ready, "apply_label": self.label,
             "apply_ready": self.ready})}


class TestApplyNoButton(unittest.TestCase):
    def test_missing_button_never_clicks(self):
        drv = _NoBtnDrv()
        res = panel.Panel(drv, Boss()).apply()
        self.assertEqual(res["outcome"], "no_button")
        self.assertEqual(drv.clicks, [], "没有按钮就一次都不能点，何况重试")

    def test_already_chatted_is_not_reapplied(self):
        """按钮变成「继续沟通」说明聊过了：算 already，不再点一次。"""
        drv = _NoBtnDrv(label="继续沟通", ready=True)
        res = panel.Panel(drv, Boss()).apply()
        self.assertEqual(res["outcome"], "already")
        self.assertEqual(drv.clicks, [])


class _ListDrv:
    def __init__(self):
        self.reads = 0

    def evaluate(self, js, retries=3):
        self.reads += 1
        return {"ok": True, "value": json.dumps(
            {"cards": [{"job_id": "j%d" % i, "title": "AI产品经理",
                        "company": "c", "salary": "1-2万"} for i in range(3)]})}


class _Flow:
    last_state: dict = {}

    def __init__(self):
        self.searched = []
        self.more = 0

    def search(self, q):
        self.searched.append(q)
        return {}

    def load_more(self):
        self.more += 1
        self.last_state = {"n": 6}
        return {}


class TestCollectPages(unittest.TestCase):
    def test_yields_page_by_page_lazily(self):
        drv = _ListDrv()
        flow = _Flow()
        cond = Conditions.load()
        cond.queries = ["q1", "q2"]
        cond.pages_per_query = 2
        gen = feed.collect_pages(drv, Boss(), flow, cond)
        first = next(gen)
        self.assertEqual(first["page"], 1)
        self.assertEqual(len(first["cards"]), 3)
        self.assertEqual(flow.searched, ["q1"], "抓完一页就该交出去，不能先把两页全抓了")

    def test_dedupes_same_job_across_words(self):
        drv = _ListDrv()
        flow = _Flow()
        cond = Conditions.load()
        cond.queries = ["q1", "q2"]
        cond.pages_per_query = 1
        batches = list(feed.collect_pages(drv, Boss(), flow, cond))
        self.assertEqual(len(batches), 2)
        self.assertEqual(len(batches[0]["fresh"]), 3)
        self.assertEqual(len(batches[1]["fresh"]), 0, "同一个 job_id 不该重复交出去")


if __name__ == "__main__":
    unittest.main()
