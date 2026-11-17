"""主链路验收：**先筛选，再逐卡判投**。

这组测试钉的是 2026-11-13 用户提的那个问题：

> 它本来有一个筛选的地方，但你没做这个筛选，然后你在做下面的判断的时候，
> 其实范围就变扩大了，浪费算力也浪费时间。

所以核心断言只有三条：

1. 筛选必须发生在**读第一张卡之前**（顺序错 = 在全量池里捞）；
2. 筛选没设上就整轮停下，一张卡都不读、一条都不投；
3. 判定通过才投，投完必须点「留在此页」，且全程不跳详情页。
"""
import json
import tempfile
import unittest
from pathlib import Path

from jkit import apply as apply_mod
from jkit.conditions import Conditions
from jkit.platforms.boss import Boss


class FakeBossPage:
    """够用的 BOSS 页面替身：筛选条 / 列表 / 面板 / 弹窗。

    只模拟测试关心的部分，但**顺序相关的动作全部记录**（events），
    否则「先筛选还是先读卡」这种问题测不出来。
    """

    def __init__(self, cards, filter_groups=None, deliver_ok=True,
                 hr_active_days=0, hr_active_raw="今日回复", hr_online=False,
                 hr_reply_count="", hr_high_reply=False):
        self.cards = cards
        self.filter_groups = filter_groups if filter_groups is not None else [
            {"i": 0, "placeholder": "求职类型", "text": "求职类型", "options": ["全职", "实习", "兼职"]},
            {"i": 1, "placeholder": "学历要求", "text": "学历要求", "options": ["本科", "大专", "硕士"]},
            {"i": 2, "placeholder": "工作经验", "text": "工作经验", "options": ["应届生", "1年以内", "1-3年"]},
        ]
        for g in self.filter_groups:
            g.setdefault("selected", [])
        self.chips = []
        self.deliver_ok = deliver_ok
        self.events = []
        self.clicks = []
        self.nav = []
        self.q = ""
        self.active_idx = -1
        self.pending_idx = 0
        self.dialog = None
        self.labels = {}          # job_id -> 按钮文案（每条独立，投过的才变「继续沟通」）
        # HR 活跃度 / 回复信号（默认「今日回复」= 今日在线 + 能回复，既有测试照旧通过）
        self.hr_active_days = hr_active_days
        self.hr_active_raw = hr_active_raw
        self.hr_online = hr_online
        self.hr_reply_count = hr_reply_count
        self.hr_high_reply = hr_high_reply
        self.session = "s1"
        self.on_request = None
        self._open_group = None
        self._hash = 0
        #: 已有钉住的标签页时 land 直接导航它；为 None 时必须新开，
        #: 绝不能 navigate 用户正在看的那个标签
        self.tab_id = "t-fake"
        self.opened_tab = None

    # ---- 驱动接口 ----

    def open_tab(self, url="", sid=None):
        self.opened_tab = url
        self.tab_id = "t-fake"
        self.events.append(("open_tab", url))
        return self.tab_id

    def navigate(self, url, retry_bare=False):
        self.nav.append(url)
        self.events.append(("navigate", url))
        return True

    def wait_element(self, selector, timeout="15s"):
        return {"satisfied": True}

    def wait_ms(self, ms):
        return True

    def scroll(self, n=1):
        return True

    def scroll_to(self, sel):
        return True

    def press(self, key, selector=None):
        if str(key).lower() == "enter":
            self._hash += 1        # 换词后结果内容变了
            self.events.append(("search", self.q))
        return True

    def fill(self, sel, text):
        self.q = text
        return True

    def session_start(self, *a, **kw):
        return self.session

    def session_stop(self, *a, **kw):
        return True

    def session_exists(self, sid):
        return True

    def pick_browser(self, name=None):
        return "b1"

    def screenshot(self, path):
        return ""

    def _filter_state(self):
        return {"ok": bool(self.filter_groups), "chips": list(self.chips),
                "groups": [dict(g) for g in self.filter_groups]}

    def _panel_state(self):
        if self.active_idx < 0:
            return {"ok": False, "reason": "no_panel"}
        c = self.cards[self.active_idx]
        return {"ok": True, "active_idx": self.active_idx, "active_id": c["job_id"],
                "title": c["title"], "salary": c["salary"],
                "company_raw": c["company"], "jd_brief": c.get("jd", ""),
                "city_raw": "深圳", "header_line": "",
                "hr_info": "HR · %s" % (self.hr_active_raw or "无标识"),
                "hr_active_raw": self.hr_active_raw,
                "hr_online": self.hr_online,
                "hr_active_days": self.hr_active_days,
                "hr_reply_count": self.hr_reply_count,
                "hr_high_reply": self.hr_high_reply,
                "chat_label": self.label_of(c["job_id"]), "chat_ready": True,
                "apply_label": self.label_of(c["job_id"]), "apply_ready": True,
                "url": "https://x/job_detail/%s.html" % c["job_id"]}

    def label_of(self, job_id: str) -> str:
        return self.labels.get(job_id, "立即沟通")

    def _dialog_state(self):
        if not self.dialog:
            return {"has_dialog": False, "dialog": None, "stay_ready": False}
        return {"has_dialog": True, "dialog": self.dialog, "stay_ready": True,
                "stay_label": "留在此页继续沟通"}

    def evaluate(self, js, retries=3):
        if "stay_ready" in js:
            return {"ok": True, "value": json.dumps(self._dialog_state())}
        if "c-filter-condition" in js and "chips" in js:
            return {"ok": True, "value": json.dumps(self._filter_state())}
        if "ids_hash" in js:
            return {"ok": True, "value": json.dumps({
                "href": "https://x/web/geek/jobs", "q": self.q,
                "n": len(self.cards), "ids_hash": "h%d" % self._hash,
                "input_value": self.q, "loading": False, "empty_hint": False,
                "doc_h": 3000, "scroll_y": 0})}
        if "job-detail-container" in js:
            return {"ok": True, "value": json.dumps(self._panel_state())}
        if "__jkit_card" in js:
            import re
            m = re.search(r"\]\[(\d+)\]", js)
            self.pending_idx = int(m.group(1)) if m else 0
            return {"ok": True, "value": "ok"}
        if "cards" in js and "card-area" in js:
            self.events.append(("read_cards", len(self.cards)))
            return {"ok": True, "value": json.dumps({
                "n": len(self.cards), "active_idx": self.active_idx,
                "cards": [dict(c, idx=i) for i, c in enumerate(self.cards)],
            })}
        return {"ok": True, "value": "{}"}

    def click(self, sel):
        self.clicks.append(sel)
        # 选项选择器里也含触发器前缀，必须先判选项，否则永远只会「打开下拉」
        if ".filter-select-dropdown li:nth-child" in sel:
            idx = int(sel.split("li:nth-child(")[1].split(")")[0]) - 1
            g = self.filter_groups[self._open_group]
            opt = g["options"][idx]
            # 真实 BOSS（2026-11-16 实测）是多选组：点不同值会累加，
            # 再点同一个值会取消。这里照抄，多值筛选才测得出来。
            if opt in g["selected"]:
                g["selected"].remove(opt)
            else:
                g["selected"].append(opt)
            self.chips = [c for c in self.chips if c != opt]
            self.chips.append(opt)
            self.events.append(("filter", g["placeholder"], opt))
            return True
        if "c-filter-condition > :nth-child" in sel:
            self._open_group = int(sel.split(":nth-child(")[1].split(")")[0]) - 1
            return True
        if "job-name" in sel or sel == "#__jkit_card":
            self.active_idx = self.pending_idx
            self.events.append(("open", self.active_idx))
            return True
        if sel == ".op-btn-chat":
            jid = self.cards[self.active_idx]["job_id"]
            self.events.append(("apply", jid))
            if self.deliver_ok:
                self.dialog = "已向BOSS发送消息"
                self.labels[jid] = "继续沟通"
            return True
        if sel == "#__jkit_stay":
            self.events.append(("stay",))
            self.dialog = None
            return True
        return True


def _cards(n=3, company=None, jd=None):
    """默认 JD 写成真实岗位的长度（>120 字），否则会被「真实需求」闸门当伪需求淘汰。"""
    long_jd = jd or (
        "岗位职责：负责 AI 产品的需求分析与方案设计，输出 PRD 与原型；"
        "跟进大模型应用（Agent、RAG、Prompt）的落地与效果评估；"
        "与研发、设计、运营协作推动版本迭代；负责数据指标监控与竞品分析，持续优化产品体验。"
        "任职要求：本科及以上学历，计算机或相关专业，2026 届应届生优先；"
        "熟悉产品方法论，逻辑清晰，有良好的沟通与文档能力；"
        "有 AI 相关项目或实习经历者优先；具备较强的学习能力与责任心，愿意在快节奏团队中成长。"
    )
    out = []
    for i in range(n):
        out.append({
            "job_id": "j%d" % i,
            "title": "AI产品经理" if i % 2 == 0 else "AI应用开发工程师",
            "salary": "12000-18000元",
            "company": company or ("公司%d" % i),
            "tags": ["1-3年", "本科", "AI", "Python"],
            "exp": "1-3年", "edu": "本科",
            "jd": long_jd,
        })
    return out


def _cond(**kw):
    c = Conditions.load()
    c.queries = ["AI产品经理"]
    c.pages_per_query = 1
    for k, v in kw.items():
        setattr(c, k, v)
    return c


class _Base(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp())
        (self.ws / "runs").mkdir()

    def _run(self, drv, cond=None, **kw):
        return apply_mod.run(self.ws, cond or _cond(), Boss(), drv,
                             session="s1", fast=True, **kw)

    @staticmethod
    def _kinds(drv):
        return [e[0] for e in drv.events]


class TestFilterFirst(_Base):
    def test_filters_applied_before_reading_cards(self):
        drv = FakeBossPage(_cards())
        self._run(drv)
        kinds = self._kinds(drv)
        self.assertIn("filter", kinds, "这一轮压根没做筛选")
        self.assertIn("read_cards", kinds)
        self.assertLess(kinds.index("filter"), kinds.index("read_cards"),
                        "筛选必须在读卡之前 —— 否则就是在全量池里逐条看")

    def test_all_three_filters_applied(self):
        drv = FakeBossPage(_cards())
        self._run(drv)
        got = {(e[1], e[2]) for e in drv.events if e[0] == "filter"}
        self.assertIn(("学历要求", "本科"), got)
        self.assertIn(("工作经验", "应届生"), got)
        self.assertIn(("求职类型", "全职"), got)

    def test_filter_failure_stops_whole_run(self):
        """筛选条结构变了 → 整轮停下，一张卡都不读、一条都不投。"""
        drv = FakeBossPage(_cards(), filter_groups=[])
        res = self._run(drv)
        self.assertEqual(res["stats"]["delivered"], 0)
        self.assertEqual(res["stats"]["checked"], 0)
        kinds = self._kinds(drv)
        self.assertNotIn("read_cards", kinds)
        self.assertNotIn("apply", kinds)

    def test_multi_value_filter_selects_every_value(self):
        """工作经验给多值 → 「应届生」「1年以内」都要点上，缺一个就是筛窄了。"""
        cond = _cond()
        cond.filters = {"boss": {"求职类型": "全职", "学历要求": "本科",
                                 "工作经验": ["应届生", "1年以内"]}}
        drv = FakeBossPage(_cards())
        self._run(drv, cond)
        got = {(e[1], e[2]) for e in drv.events if e[0] == "filter"}
        self.assertIn(("工作经验", "应届生"), got)
        self.assertIn(("工作经验", "1年以内"), got)
        # 选中项是累加，不是替换 —— 单选实现的适配器会在这里露馅
        g = [x for x in drv.filter_groups if x["placeholder"] == "工作经验"][0]
        self.assertEqual(sorted(g["selected"]), ["1年以内", "应届生"])

    def test_multi_value_filter_skips_already_selected(self):
        """已经生效的值不再点（再点会取消，反而筛丢了）。"""
        cond = _cond()
        cond.filters = {"boss": {"工作经验": ["应届生", "1年以内"]}}
        drv = FakeBossPage(_cards())
        drv.filter_groups = [
            {"i": 0, "placeholder": "工作经验", "text": "工作经验",
             "options": ["应届生", "1年以内", "1-3年"], "selected": ["应届生"]},
        ]
        self._run(drv, cond)
        touched = [e[2] for e in drv.events if e[0] == "filter"]
        self.assertEqual(touched, ["1年以内"], "已生效的「应届生」不该再点")
        self.assertEqual(sorted(drv.filter_groups[0]["selected"]), ["1年以内", "应届生"])

    def test_multi_value_clicks_each_value_once(self):
        """多值 = 每个值各点一次；只有没生效才重试，正常不该重复点同一个值。

        （2026-11-16 真实页面：连续点同一组时下拉状态不可靠，所以策略是
        「点→立刻校验→没生效才收起重试」，正常路径下每个值只点一次。）
        """
        cond = _cond()
        cond.filters = {"boss": {"工作经验": ["应届生", "1年以内"]}}
        drv = FakeBossPage(_cards())
        self._run(drv, cond)
        li_clicks = [c for c in drv.clicks if "filter-select-dropdown li" in c]
        self.assertEqual(len(li_clicks), 2,
                         "两个值各点一次即可，实际点了 %d 次" % len(li_clicks))
        self.assertEqual(sorted(drv.filter_groups[2]["selected"]), ["1年以内", "应届生"])

    def test_multi_value_with_unknown_option_stops_run(self):
        """多值里有一个平台不认 → 整项报错，不能半开状态往下跑。"""
        cond = _cond()
        cond.filters = {"boss": {"工作经验": ["应届生", "两年以内"]}}
        drv = FakeBossPage(_cards())
        res = self._run(drv, cond)
        self.assertEqual(res["stats"]["checked"], 0)
        self.assertNotIn("read_cards", self._kinds(drv))
        self.assertNotIn("apply", self._kinds(drv))
        self.assertEqual(drv.events and [e for e in drv.events if e[0] == "filter"], [],
                         "有一个值不存在时，一个选项都不该点")

    def test_no_detail_navigation(self):
        drv = FakeBossPage(_cards())
        self._run(drv)
        self.assertEqual(drv.nav, ["https://www.zhipin.com/web/geek/jobs?city=101280600"],
                         "整轮只该有落地页那一次整页加载")
        self.assertFalse(any("job_detail" in u for u in drv.nav))

    def test_no_pinned_tab_opens_one_instead_of_hijacking(self):
        """没钉标签页时只能新开，绝不能 navigate —— 那会把用户正在看的页面冲掉。"""
        drv = FakeBossPage(_cards())
        drv.tab_id = None
        self._run(drv)
        self.assertEqual(drv.opened_tab,
                         "https://www.zhipin.com/web/geek/jobs?city=101280600")
        self.assertEqual(drv.nav, [], "不该去 navigate 任何已有标签页")


class TestDeliverFlow(_Base):
    def test_dry_run_history_does_not_block_live(self):
        from unittest.mock import patch
        from jkit import store
        historical = self.ws / "runs" / "20000101-0000"
        historical.mkdir()
        rec = dict(_cards(1)[0], outcome="dry_run")
        (historical / "ledger.jsonl").write_text(
            json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertTrue(store.settled_index(self.ws))
        drv = FakeBossPage(_cards(1))
        with patch.object(store, "settled_index", side_effect=AssertionError("不应复用旧评分")):
            result = self._run(drv)
        self.assertEqual(result["stats"]["delivered"], 1)

    def test_successful_delivery_history_still_prevents_repeat(self):
        historical = self.ws / "runs" / "20000101-0000"
        historical.mkdir()
        rec = dict(_cards(1)[0], outcome="delivered")
        (historical / "ledger.jsonl").write_text(
            json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        drv = FakeBossPage(_cards(1))
        result = self._run(drv)
        self.assertEqual(result["stats"]["delivered"], 0)
        self.assertNotIn("apply", self._kinds(drv))

    def test_delivered_then_stay(self):
        drv = FakeBossPage(_cards(1))
        res = self._run(drv)
        kinds = self._kinds(drv)
        self.assertIn("apply", kinds)
        self.assertIn("stay", kinds, "投完必须点「留在此页」，否则遮罩挡住下一张")
        self.assertLess(kinds.index("apply"), kinds.index("stay"))
        self.assertEqual(res["stats"]["delivered"], 1)

    def test_same_company_only_once(self):
        drv = FakeBossPage(_cards(3, company="同一家"))
        res = self._run(drv)
        self.assertEqual(res["stats"]["delivered"], 1, "同一轮同一公司只投一条")

    def test_card_level_veto_never_opens_panel(self):
        cards = _cards(1)
        cards[0]["title"] = "销售助理"
        drv = FakeBossPage(cards)
        self._run(drv)
        kinds = self._kinds(drv)
        self.assertNotIn("open", kinds, "卡片层就能判死的岗，不该开面板")
        self.assertNotIn("apply", kinds)

    def test_dry_run_never_applies(self):
        drv = FakeBossPage(_cards(2))
        res = self._run(drv, dry_run=True)
        self.assertNotIn("apply", self._kinds(drv))
        self.assertEqual(res["stats"]["dry_run"], 2)

    def test_already_contacted_is_skipped(self):
        drv = FakeBossPage(_cards(1))
        drv.labels["j0"] = "继续沟通"    # 这条之前聊过，面板一打开就是「继续沟通」
        res = self._run(drv)
        self.assertEqual(res["stats"]["already"], 1)
        self.assertEqual(res["stats"]["delivered"], 0)
        self.assertNotIn("apply", self._kinds(drv))

    def test_ledger_written_with_condition_id(self):
        drv = FakeBossPage(_cards(1))
        cond = _cond()
        res = self._run(drv, cond)
        led = (self.ws / "runs" / res["run_id"] / "ledger.jsonl").read_text(encoding="utf-8")
        rows = [json.loads(x) for x in led.splitlines() if x.strip()]
        self.assertTrue(rows)
        self.assertEqual(rows[0]["condition_id"], cond.id)
        self.assertEqual(rows[0]["platform"], "boss")
        self.assertIn("score", rows[0])


class TestAdmissionGates(_Base):
    """准入门槛：今日在线 / 真实需求（JD 长度）。

    用户口径（2026-11-16）：「必须 Boss 今日在线，然后能回复，
    然后是确实真实岗位、真实需求」。
    """

    def test_today_offline_is_dropped(self):
        """昨天活跃、没在线、也没回复信号 → 淘汰（今日在线是硬门槛）。"""
        drv = FakeBossPage(_cards(1), hr_active_days=1,
                           hr_active_raw="1天前活跃", hr_online=False,
                           hr_reply_count="", hr_high_reply=False)
        res = self._run(drv)
        self.assertEqual(res["stats"]["delivered"], 0)
        self.assertEqual(res["stats"]["drop_inactive"], 1)
        self.assertNotIn("apply", self._kinds(drv))

    def test_online_passes(self):
        """HR 在线 → 通过活跃度闸门（即便读不到活跃天数、没有回复信号）。"""
        drv = FakeBossPage(_cards(1), hr_active_days=None,
                           hr_active_raw="", hr_online=True)
        res = self._run(drv)
        self.assertEqual(res["stats"]["delivered"], 1)

    def test_reply_signal_counts_as_online(self):
        """智联式「今日回复 N 次」：活跃天数读不到，也按今日在线+能回复放行。"""
        drv = FakeBossPage(_cards(1), hr_active_days=None,
                           hr_active_raw="今日回复", hr_online=False,
                           hr_reply_count="5")
        res = self._run(drv)
        self.assertEqual(res["stats"]["delivered"], 1)

    def test_thin_jd_is_dropped(self):
        """JD 只有一句话（<120 字）= 伪需求/挂着不招 → 不投。"""
        drv = FakeBossPage(_cards(1, jd="招人，待遇好，欢迎投递。"))
        res = self._run(drv)
        self.assertEqual(res["stats"]["delivered"], 0)
        self.assertEqual(res["stats"]["drop_thin_jd"], 1)
        self.assertNotIn("apply", self._kinds(drv))


class TestTargetClosedLoop(_Base):
    """P-C：目标数量闭环——投够才停；候选耗尽要明确标记，不能伪装成"完成"。"""

    def test_delivers_up_to_target_then_stops(self):
        """候选比目标多：投够 max 条即停，不多投。"""
        drv = FakeBossPage(_cards(6))
        res = self._run(drv, max_deliver=2)
        self.assertEqual(res["stats"]["delivered"], 2)
        self.assertEqual(res["stopped"], "max")

    def test_exhausted_when_candidates_less_than_target(self):
        """候选不足目标：全部投出后标 exhausted，summary 有 shortfall。"""
        drv = FakeBossPage(_cards(3))
        res = self._run(drv, max_deliver=5)
        self.assertEqual(res["stats"]["delivered"], 3)
        self.assertEqual(res["stopped"], "exhausted")
        run_id = res["run_id"]
        summary = json.loads((self.ws / "runs" / run_id / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["target"], 5)
        self.assertEqual(summary["completed"], 3)
        self.assertEqual(summary["shortfall"], 2)

    def test_dry_run_never_marks_exhausted(self):
        """试跑不计入数量闭环，不标 exhausted。"""
        drv = FakeBossPage(_cards(2))
        res = self._run(drv, max_deliver=5, dry_run=True)
        self.assertEqual(res["stats"]["delivered"], 0)
        self.assertNotEqual(res["stopped"], "exhausted")


if __name__ == "__main__":
    unittest.main()
