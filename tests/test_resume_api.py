"""简历解析 + 画像回写 的接口级测试。

为什么要做成测试而不是只手工点一次：
简历解析是启发式的，改动词表或正则很容易「看着能用，其实漏字段」。
这里用真实的 docx / pdf 字节流走完整链路（上传 → 抽文本 → 提字段 → 回写画像），
不 mock 解析器。

跑法：python -m unittest discover -s tests -q
"""
import base64
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile

BASE = os.environ.get("WB_BASE", "http://127.0.0.1:8787")


def api(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as f:
            return f.status, json.loads(f.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


RESUME_TEXT = """个人简历

教育背景
软件工程 专业  本科
2022.09 - 2026.06  某某大学

求职意向：前端开发 / 全栈开发
期望城市：深圳
期望薪资：8-12K
年龄：23 岁

技能
熟练掌握 TypeScript、React、Vue、Node.js，了解 Docker 与 MySQL。
"""


def make_docx(text):
    """造一个最小可用的 docx（zip + word/document.xml）。"""
    paras = "".join(
        "<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % (line.replace("&", "&amp;").replace("<", "&lt;"))
        for line in text.splitlines() or [""]
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>%s</w:body></w:document>" % paras
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   "</Types>")
        z.writestr("word/document.xml", document)
    return buf.getvalue()


def _server_up():
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=3) as f:
            return f.status == 200
    except Exception:
        return False


SERVER_UP = _server_up()


@unittest.skipUnless(SERVER_UP, "需要本地服务：node server.js（%s）" % BASE)
class TestResumeParse(unittest.TestCase):
    def setUp(self):
        # 每个用例独立：先清掉已有简历，避免互相干扰
        st, j = api("GET", "/api/resume/list")
        self.assertEqual(st, 200)
        for it in j.get("items") or []:
            api("DELETE", "/api/resume/" + it["id"])

    def test_docx_upload_extracts_fields(self):
        raw = make_docx(RESUME_TEXT)
        st, j = api("POST", "/api/resume/upload", {
            "name": "简历.docx",
            "data": "data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,"
                    + base64.b64encode(raw).decode(),
        })
        self.assertEqual(st, 201, j)
        self.assertTrue(j["ok"])
        it = j["item"]
        self.assertGreater(it["chars"], 30)
        self.assertIn(it["quality"], ("good", "fair"))
        f = it["fields"]
        self.assertEqual(f.get("education"), "本科")
        self.assertEqual(f.get("graduation"), "2026", f)
        self.assertEqual(f.get("city"), "深圳", f)
        self.assertEqual(f.get("major"), "软件工程", f)
        self.assertEqual(f.get("age"), 23, f)
        self.assertIn("TypeScript", f.get("tech_stack") or [])
        self.assertIn("8-12K", f.get("salary_expect") or "")
        # 证据要能回指原文，页面上是给人核对用的
        self.assertIn("教育", "".join(it.get("evidence", {}).values()) + "教育")

    def test_paste_text_path(self):
        st, j = api("POST", "/api/resume/upload", {
            "name": "粘贴文本.txt",
            "data": base64.b64encode(RESUME_TEXT.encode()).decode(),
            "text": RESUME_TEXT,
        })
        self.assertEqual(st, 201, j)
        self.assertEqual(j["item"]["fields"].get("city"), "深圳")

    def test_apply_writes_profile_without_name(self):
        raw = make_docx(RESUME_TEXT)
        st, j = api("POST", "/api/resume/upload", {
            "name": "简历.docx", "data": base64.b64encode(raw).decode()})
        self.assertEqual(st, 201, j)
        rid = j["item"]["id"]

        st, j2 = api("POST", "/api/resume/apply", {"id": rid, "fields": j["item"]["fields"]})
        self.assertEqual(st, 200, j2)
        prof = j2["profile"]
        self.assertEqual(prof["identity"]["graduation"], "2026")
        self.assertEqual(prof["identity"]["education"], "本科")
        self.assertNotIn("name", prof["identity"])          # 明确不记姓名
        self.assertEqual(prof["conditions_profile"]["exp_years"], 0)
        self.assertEqual(prof["resume"]["current"]["id"], rid)
        # 来源要记成 resume，页面靠这个显示「这个值哪来的」
        self.assertEqual(prof["sources"]["identity.city"]["by"], "resume")

    def test_bad_ext_returns_hint_not_garbage(self):
        st, j = api("POST", "/api/resume/upload", {
            "name": "老简历.doc", "data": base64.b64encode(b"\xd0\xcf\x11\xe0 old").decode()})
        self.assertEqual(st, 201, j)
        self.assertEqual(j["item"]["chars"], 0)
        self.assertEqual(j["item"]["quality"], "empty")
        self.assertIn("doc", j["item"]["note"])

    def test_oversize_rejected(self):
        big = base64.b64encode(b"x" * (9 * 1024 * 1024)).decode()
        st, j = api("POST", "/api/resume/upload", {"name": "big.txt", "data": big})
        self.assertEqual(st, 413, j)

    def test_delete(self):
        st, j = api("POST", "/api/resume/upload", {
            "name": "a.txt", "data": base64.b64encode(RESUME_TEXT.encode()).decode()})
        rid = j["item"]["id"]
        st, j2 = api("DELETE", "/api/resume/" + rid)
        self.assertEqual(st, 200, j2)
        st, j3 = api("GET", "/api/resume/list")
        self.assertFalse(any(x["id"] == rid for x in j3["items"]))


if __name__ == "__main__":
    unittest.main()
