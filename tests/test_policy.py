"""准入门槛的行为测试：今日在线 + 真实需求。

2026-11-16 用户口径：「必须 Boss 今日在线，然后能回复，然后确实真实需求」。
- 今日在线：active_days == 0 或 hr_online → 过；1 天前 → 淘汰；读不到 → 淘汰。
- 真实需求：MIN_JD_LEN 是下限，JD 太短按伪需求处理（闸门在 apply 里）。
"""
import unittest

from jkit import policy


class TestActivityGateTodayOnly(unittest.TestCase):
    def test_today_online_passes(self):
        ok, why = policy.activity_gate(0, False, 0)
        self.assertTrue(ok)
        self.assertIn("0", why)

    def test_yesterday_dropped(self):
        ok, _ = policy.activity_gate(1, False, 0)
        self.assertFalse(ok)

    def test_online_passes_regardless_of_days(self):
        ok, human = policy.activity_gate(9, True, 0)
        self.assertTrue(ok)
        self.assertEqual(human, "在线")

    def test_unknown_dropped(self):
        ok, why = policy.activity_gate(None, False, 0)
        self.assertFalse(ok)
        self.assertIn("读不到", why)

    def test_three_day_window_when_not_today_only(self):
        """旧口径保留：max_days=3 时 3 日内仍放行，4 天淘汰。"""
        self.assertTrue(policy.activity_gate(3, False, 3)[0])
        self.assertFalse(policy.activity_gate(4, False, 3)[0])


class TestKnobs(unittest.TestCase):
    def test_require_today_is_on(self):
        self.assertTrue(policy.REQUIRE_TODAY_ONLINE)

    def test_min_jd_len_positive(self):
        self.assertGreater(policy.MIN_JD_LEN, 0)


if __name__ == "__main__":
    unittest.main()
