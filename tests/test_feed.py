"""页面原文 -> 结构化。这些解析错一次，整批岗位就会被误杀或误放，
所以每条都拿真实页面原文当样本（2026-11-13 实拍 BOSS 面板）。
"""
import json
import unittest

from jkit import feed, policy


class TestActiveDays(unittest.TestCase):
    def test_online_is_zero(self):
        self.assertEqual(feed.active_days("在线"), 0)
        self.assertEqual(feed.active_days("刚刚活跃"), 0)
        self.assertEqual(feed.active_days("今日活跃"), 0)

    def test_day_week_month_year(self):
        self.assertEqual(feed.active_days("3日内活跃"), 3)
        self.assertEqual(feed.active_days("本周活跃"), 7)
        self.assertEqual(feed.active_days("3月内活跃"), 90)
        self.assertEqual(feed.active_days("半年前活跃"), 180)

    def test_unreadable_is_none_not_zero(self):
        # 0 的意思是「今天有动作」，和「不知道」必须是两件事
        self.assertIsNone(feed.active_days(""))
        self.assertIsNone(feed.active_days("活跃"))

    def test_real_panel_value_fails_the_gate(self):
        # 2026-11-13 实测：面板给的就是「3月内活跃」
        ok, why = policy.activity_gate(feed.active_days("3月内活跃"), False)
        self.assertFalse(ok, why)
        ok, _ = policy.activity_gate(feed.active_days("3日内活跃"), False)
        self.assertTrue(ok)


class TestParseHeader(unittest.TestCase):
    LINE = "日语翻译\n-K\n深圳\n在校/应届\n本科"

    def test_reads_city_exp_edu(self):
        h = feed.parse_header(self.LINE)
        self.assertEqual(h["city_raw"], "深圳")
        self.assertEqual(h["education_raw"], "本科")
        self.assertEqual(h["experience_raw"], "在校/应届")
        self.assertNotIn("salary", h)          # "-K" 是面议，不能填 0 去撞薪资门槛

    def test_skips_title_part(self):
        # 第一段是职位名，即使它长得像学历也不能被当成学历/城市
        h = feed.parse_header("深圳\n城市经理\n3-5年\n15-25K")
        self.assertEqual(h["experience_raw"], "3-5年")
        self.assertEqual(h["salary"], "15-25K")
        self.assertNotIn("city_raw", h)

    def test_empty_line_is_safe(self):
        self.assertEqual(feed.parse_header(""), {})


class TestDetailRecord(unittest.TestCase):
    def test_panel_activity_fills_days_and_online(self):
        rec = feed.detail_record({"job_id": "j1", "title": "AI产品经理"},
                                 {"hr_active_raw": "3月内活跃", "active_id": "j1"})
        self.assertEqual(rec["hr_active_days"], 90)
        self.assertFalse(rec["hr_online"])

    def test_online_tag_wins(self):
        rec = feed.detail_record({"job_id": "j2"}, {"hr_online_raw": "在线"})
        self.assertTrue(rec["hr_online"])
        self.assertEqual(rec["hr_active_days"], 0)

    def test_card_value_not_overwritten(self):
        rec = feed.detail_record(
            {"job_id": "j3", "education_raw": "大专", "city_raw": "广州"},
            {"header_line": "岗位\n10-15K\n深圳\n应届\n本科"})
        self.assertEqual(rec["education_raw"], "大专")
        self.assertEqual(rec["city_raw"], "广州")
        self.assertEqual(rec["salary"], "10-15K")

    def test_company_raw_split(self):
        rec = feed.detail_record({"job_id": "j4"},
                                 {"company_raw": "深圳市启星优创科技 · 招聘者"})
        self.assertEqual(rec["company_name"], "深圳市启星优创科技")
        rec2 = feed.detail_record({"job_id": "j5"},
                                  {"company_raw": "某某科技 · 已上市 · 1000-9999人"})
        self.assertEqual(rec2["company_name"], "某某科技")
        self.assertEqual(rec2["company_scale"], "1000-9999人")


class _ListDrv:
    """只回答「读卡片」的假驱动。"""

    def __init__(self, cards):
        self.cards = cards
        self.calls = 0

    def evaluate(self, js, retries=3):
        self.calls += 1
        return {"value": json.dumps({"n": len(self.cards), "cards": self.cards})}


class TestLocateCard(unittest.TestCase):
    """换词/滚动后列表重排，必须按 job_id 重新定位 —— 旧下标会点到别的卡。"""

    def _adapter(self):
        from jkit.platforms import get
        return get("boss")

    def test_finds_new_index(self):
        drv = _ListDrv([
            {"idx": 0, "job_id": "a", "title": "A"},
            {"idx": 1, "job_id": "b", "title": "B"},
            {"idx": 2, "job_id": "c", "title": "C"},
        ])
        self.assertEqual(feed.locate_card(drv, self._adapter(), "c"), 2)

    def test_returns_none_when_gone(self):
        drv = _ListDrv([{"idx": 0, "job_id": "a", "title": "A"}])
        self.assertIsNone(feed.locate_card(drv, self._adapter(), "zzz"))

    def test_empty_job_id_is_none(self):
        drv = _ListDrv([{"idx": 0, "job_id": "a", "title": "A"}])
        self.assertIsNone(feed.locate_card(drv, self._adapter(), ""))
        self.assertEqual(drv.calls, 0, "没有 job_id 时不该白读一次列表")


if __name__ == "__main__":
    unittest.main()
