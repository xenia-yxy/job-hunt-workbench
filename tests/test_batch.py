"""多岗位配额分摊：条件目录的发现与排序。

用户口径（2026-11-16）：「岗位比较多，每个岗位分配一点」。
批量跑的前提是「目录里有哪些条件、按什么顺序跑」必须稳定可复现。
"""
import tempfile
import unittest
from pathlib import Path

from jkit import batch
from jkit.conditions_loader import load_conditions


class TestLoadConditionFiles(unittest.TestCase):
    def test_dir_sorted_and_only_json(self):
        d = Path(tempfile.mkdtemp())
        (d / "b.json").write_text("{}", encoding="utf-8")
        (d / "a.json").write_text("{}", encoding="utf-8")
        (d / "_note.txt").write_text("x", encoding="utf-8")
        got = batch.load_condition_files(d)
        self.assertEqual([p.name for p in got], ["a.json", "b.json"])

    def test_single_file(self):
        f = Path(tempfile.mkdtemp()) / "x.json"
        f.write_text("{}", encoding="utf-8")
        self.assertEqual(batch.load_condition_files(f), [f])

    def test_empty_dir_raises(self):
        d = Path(tempfile.mkdtemp())
        with self.assertRaises(FileNotFoundError):
            batch.load_condition_files(d)

    def test_missing_path_raises(self):
        with self.assertRaises(FileNotFoundError):
            batch.load_condition_files("/nope/definitely-missing.json")


class TestRealConditions(unittest.TestCase):
    """仓库里实到的六份条件必须都能加载并校验通过。"""

    DIR = Path(__file__).resolve().parent.parent / "config" / "local" / "conditions"

    @unittest.skipUnless(DIR.exists(), "实例条件目录不存在（模板轨/干净检出可跳过）")
    def test_six_conditions_load(self):
        files = batch.load_condition_files(self.DIR)
        self.assertGreaterEqual(len(files), 6)
        for f in files:
            cond = load_conditions(f)
            self.assertTrue(cond.queries, "%s 没有搜索词" % f.name)
            self.assertIn("boss", cond.city_code, "%s 缺 boss 城市码" % f.name)


if __name__ == "__main__":
    unittest.main()
