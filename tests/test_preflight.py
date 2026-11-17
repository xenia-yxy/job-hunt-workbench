"""开跑前自检的单测。

这三项（daemon / 浏览器 / 条件）是「每次跑之前要手工确认」的活，做成自动
之后必须钉住：该报错就报错、该给下一步就给下一步，不能把问题留到投递中段。
"""
import json
import tempfile
import unittest
from pathlib import Path

from jkit import preflight


class _Drv:
    """只回答「有没有浏览器」的假驱动。"""

    def __init__(self, browsers=None):
        self._browsers = browsers or []
        self.errors = []

    def pick_browser(self, want=None):
        if not self._browsers:
            return None
        return self._browsers[0]


class TestEnsureBrowser(unittest.TestCase):
    def test_returns_first_when_present(self):
        d = _Drv([{"instance_id": "abc"}])
        self.assertEqual(preflight.ensure_browser(d, wait_sec=0), "abc")

    def test_no_browser_raises_with_hint(self):
        d = _Drv([])
        with self.assertRaises(preflight.PreflightError) as cm:
            preflight.ensure_browser(d, wait_sec=0)
        msg = str(cm.exception)
        self.assertIn("没有已连接的浏览器", msg)
        self.assertIn("BrowserSkill", msg, "必须给出可执行的下一步，不能只说失败")


class TestCheckConditions(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp())
        (self.ws / "config" / "local").mkdir(parents=True)

    def _write(self, name, data):
        p = self.ws / "config" / "local" / name
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def _good(self, **kw):
        d = {
            "queries": ["AI产品经理"], "city": "深圳",
            "city_code": {"boss": "101280600"},
            "filters": {"boss": {"学历要求": "本科"}},
        }
        d.update(kw)
        return d

    def test_directory_with_good_files(self):
        self._write("a.json", self._good())
        self._write("b.json", self._good(queries=["技术支持"]))
        pairs = preflight.check_conditions("config/local", self.ws)
        self.assertEqual(len(pairs), 2)

    def test_empty_directory_raises(self):
        with self.assertRaises(preflight.PreflightError) as cm:
            preflight.check_conditions("config/local", self.ws)
        self.assertIn("没有条件文件", str(cm.exception))

    def test_bad_condition_reports_file_name(self):
        self._write("ok.json", self._good())
        self._write("bad.json", self._good(queries=[]))   # 缺搜索词
        with self.assertRaises(preflight.PreflightError) as cm:
            preflight.check_conditions("config/local", self.ws)
        msg = str(cm.exception)
        self.assertIn("bad.json", msg)
        self.assertNotIn("ok.json：", msg, "好文件不该被列进错误里")

    def test_missing_city_rejected(self):
        self._write("a.json", self._good(city=""))
        with self.assertRaises(preflight.PreflightError):
            preflight.check_conditions("config/local", self.ws)

    def test_single_file_path(self):
        p = self._write("one.json", self._good())
        pairs = preflight.check_conditions(p, self.ws)
        self.assertEqual(len(pairs), 1)


if __name__ == "__main__":
    unittest.main()
