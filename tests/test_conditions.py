"""条件契约的单测。

条件是这个系统里唯一「外部可改」的东西，它错了整轮就白跑。所以要钉住：
缺词/缺城市不许开跑、平台视图按平台取、未知平台要报错、JSON 能来回转。
"""
import json
import tempfile
import unittest
from pathlib import Path

from jkit.conditions import DEFAULT, Conditions
from jkit.errors import ConditionError


class TestLoad(unittest.TestCase):
    def test_default_is_valid(self):
        c = Conditions.load()
        self.assertTrue(c.queries)
        self.assertEqual(c.city, "深圳")
        self.assertIn("boss", c.city_code)

    def test_roundtrip(self):
        c = Conditions.load()
        again = Conditions.from_dict(c.to_dict())
        self.assertEqual(c.to_dict(), again.to_dict())

    def test_partial_json_merges_with_default(self):
        """只给两个字，其余沿用默认 —— 用户不必每次写全。"""
        c = Conditions.from_dict({"queries": ["AI产品经理"], "city": "广州"})
        self.assertEqual(c.queries, ["AI产品经理"])
        self.assertEqual(c.city, "广州")
        self.assertEqual(c.min_grade, "B")
        self.assertGreater(len(c.city_code), 0)

    def test_file_load(self):
        d = Path(tempfile.mkdtemp())
        p = d / "c.json"
        p.write_text(json.dumps({
            "id": "cond-x", "queries": ["q1"], "city": "深圳",
            "city_code": {"boss": "101280600"},
            "filters": {"boss": {"学历要求": "本科"}},
        }, ensure_ascii=False), encoding="utf-8")
        c = Conditions.load(p)
        self.assertEqual(c.id, "cond-x")
        self.assertEqual(c.filters["boss"]["学历要求"], "本科")

    def test_missing_file_raises(self):
        with self.assertRaises(ConditionError):
            Conditions.load("/nonexistent/x.json")

    def test_bad_json_raises(self):
        d = Path(tempfile.mkdtemp())
        p = d / "c.json"
        p.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ConditionError):
            Conditions.load(p)


class TestValidate(unittest.TestCase):
    def test_no_queries_blocked(self):
        c = Conditions.load()
        c.queries = []
        with self.assertRaises(ConditionError):
            c.validate()

    def test_no_city_blocked(self):
        c = Conditions.load()
        c.city = ""
        with self.assertRaises(ConditionError):
            c.validate()

    def test_bad_grade_blocked(self):
        c = Conditions.load()
        c.min_grade = "S"
        with self.assertRaises(ConditionError):
            c.validate()

    def test_filters_must_match_city_code(self):
        c = Conditions.load()
        c.filters["liepin"] = {"学历要求": "本科"}
        with self.assertRaises(ConditionError):
            c.validate()


class TestPlatformView(unittest.TestCase):
    def test_filters_are_per_platform(self):
        c = Conditions.load()
        boss = c.for_platform("boss")
        zl = c.for_platform("zhilian")
        self.assertEqual(boss["filters"].get("求职类型"), "全职")
        self.assertEqual(boss["filters"].get("工作经验"), ["应届生", "1年以内"],
                         "BOSS 工作经验是多选组，两个档都要点上")
        # 智联的筛选项名与 BOSS 不同：页面写「学历」「经验」，不是「学历要求」「工作经验」
        self.assertEqual(zl["filters"].get("学历"), "本科")
        self.assertEqual(zl["filters"].get("经验"), "1年以下")
        self.assertNotIn("学历要求", zl["filters"], "智联没有「学历要求」这一项")
        self.assertEqual(boss["city_code"], "101280600")
        self.assertEqual(zl["city_code"], "765")

    def test_unknown_platform_raises(self):
        c = Conditions.load()
        with self.assertRaises(ConditionError):
            c.for_platform("liepin")

    def test_platform_without_filters_is_ok(self):
        """平台有城市码但没配筛选：允许，只是这一轮不筛。"""
        c = Conditions.load()
        c.filters = {}
        self.assertEqual(c.for_platform("boss")["filters"], {})


class TestMultiValueFilters(unittest.TestCase):
    """筛选值支持单值与多值：多值 = 同一组多个选项同时生效。

    这条链路最容易出的错是「把列表拍平成字符串」，于是适配器拿
    ``"['应届生', '1年以内']"`` 去找一个不存在的选项，报「没有该选项」。
    """

    def test_multi_value_survives_from_dict(self):
        c = Conditions.from_dict({
            "queries": ["AI产品经理"], "city": "深圳",
            "city_code": {"boss": "101280600"},
            "filters": {"boss": {"工作经验": ["应届生", "1年以内"]}},
        })
        self.assertEqual(c.filters["boss"]["工作经验"], ["应届生", "1年以内"])

    def test_single_value_stays_string(self):
        c = Conditions.from_dict({
            "queries": ["AI产品经理"], "city": "深圳",
            "city_code": {"boss": "101280600"},
            "filters": {"boss": {"工作经验": "应届生"}},
        })
        self.assertEqual(c.filters["boss"]["工作经验"], "应届生")

    def test_for_platform_copies_list(self):
        """平台视图必须给副本，调用方改它不能污染条件本体。"""
        c = Conditions.load()
        c.filters = {"boss": {"工作经验": ["应届生", "1年以内"]}}
        got = c.for_platform("boss")["filters"]["工作经验"]
        got.append("1-3年")
        self.assertEqual(c.filters["boss"]["工作经验"], ["应届生", "1年以内"])

    def test_empty_list_rejected(self):
        c = Conditions.load()
        c.filters = {"boss": {"工作经验": []}}
        with self.assertRaises(ConditionError):
            c.validate()

    def test_non_string_rejected(self):
        c = Conditions.load()
        c.filters = {"boss": {"工作经验": 2}}
        with self.assertRaises(ConditionError):
            c.validate()

    def test_to_dict_keeps_list(self):
        c = Conditions.load()
        c.filters = {"boss": {"工作经验": ["应届生", "1年以内"]}}
        self.assertEqual(c.to_dict()["filters"]["boss"]["工作经验"],
                         ["应届生", "1年以内"])

    def test_filter_line_formats_multi(self):
        from jkit.platforms.base import filter_line, normalize_filters
        self.assertEqual(
            filter_line({"工作经验": ["应届生", "1年以内"], "学历要求": "本科"}),
            "工作经验=应届生/1年以内 / 学历要求=本科")
        self.assertEqual(normalize_filters({"a": "x"}), {"a": ["x"]})
        self.assertEqual(normalize_filters({"a": "", "b": []}), {})


if __name__ == "__main__":
    unittest.main()
