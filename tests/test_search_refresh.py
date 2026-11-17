"""搜索刷新回归：URL变更不能证明旧卡已被替换。仅离线状态序列。"""
import unittest
from unittest.mock import patch

from jkit.searchflow import SearchFlow
from jkit.errors import ResultStalled


class SearchRefreshTests(unittest.TestCase):
    def flow(self, states):
        flow = object.__new__(SearchFlow)
        iterator = iter(states)
        flow.state = lambda: next(iterator)
        flow.human = None
        flow._check_risk = lambda state: None
        flow._fail = lambda exc, tag: (_ for _ in ()).throw(exc)
        return flow

    @patch("jkit.searchflow.time.sleep")
    def test_url_and_container_change_do_not_accept_old_cards(self, _sleep):
        old = {"q": "old", "ids_hash": "old-ids", "n": 15}
        stale = {"q": "IT支持", "ids_hash": "old-ids", "n": 15,
                 "container_same": False}
        fresh = {"q": "IT支持", "ids_hash": "new-ids", "n": 10}
        flow = self.flow([stale, fresh, fresh])
        self.assertEqual(flow.wait_new({"q": "IT支持"}, old), fresh)

    @patch("jkit.searchflow.time.sleep")
    def test_empty_hint_with_old_cards_is_not_empty_result(self, _sleep):
        old = {"q": "old", "ids_hash": "old", "n": 15}
        stale = {"q": "new", "ids_hash": "old", "n": 15, "empty_hint": True}
        empty = {"q": "new", "ids_hash": "", "n": 0, "empty_hint": True}
        flow = self.flow([stale, empty, empty])
        self.assertEqual(flow.wait_new({"q": "new"}, old), empty)

    @patch("jkit.searchflow.time.time", side_effect=[0, 2])
    def test_stale_results_timeout(self, _clock):
        state = {"q": "new", "ids_hash": "same", "n": 10}
        flow = self.flow([state])
        with self.assertRaises(ResultStalled):
            flow.wait_new({"q": "new"}, {"q": "old", "ids_hash": "same"}, timeout=1)
