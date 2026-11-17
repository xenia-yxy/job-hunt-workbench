"""锁定 2026-11-13 口径：本科 / 助理 / 管培生可投，硬否决不得被白名单洗白。"""
import unittest

from jkit.score import JobRecord, judge


def rec(**kw):
    base = dict(
        title="AI应用助理", salary="7000-12000元", salary_min=7000,
        experience_raw="经验不限", education_raw="本科",
        company_scale="100-299人", company_industry="软件/IT服务",
        jd_duty="岗位职责：参与 AI 产品迭代，协助产品经理写需求文档，欢迎应届生投递。",
        tech_tags=["本科", "AI", "Python"],
    )
    base.update(kw)
    return JobRecord.from_dict(base)


class TestJuniorOpen(unittest.TestCase):
    def test_bachelor_is_exact_match(self):
        v = judge(rec())
        self.assertFalse(v.veto)
        self.assertEqual(v.job_family, "junior")
        self.assertIn("学历本科=精准匹配", v.reasons)
        self.assertGreaterEqual(v.detail["门槛"], 25)

    def test_bachelor_not_penalized_vs_lower_edu(self):
        hi = judge(rec())
        lo = judge(rec(education_raw="大专", tech_tags=["大专", "AI", "Python"]))
        self.assertGreaterEqual(hi.detail["门槛"], lo.detail["门槛"])

    def test_assistant_is_deliverable(self):
        v = judge(rec(title="产品助理"))
        self.assertTrue(v.deliverable, v.as_dict())
        self.assertIn("junior方向分保底18", v.reasons)

    def test_management_trainee_is_deliverable(self):
        v = judge(rec(title="技术管培生", jd_duty="岗位职责：轮岗培养，参与 Agent 应用开发。"))
        self.assertFalse(v.veto)
        self.assertEqual(v.job_family, "junior")
        self.assertTrue(v.deliverable, v.as_dict())

    def test_junior_blank_jd_still_scored(self):
        v = judge(rec(title="AI助理", jd_duty=""))
        self.assertGreaterEqual(v.detail["方向"], 18)


class TestVetoStillWins(unittest.TestCase):
    def test_sales_assistant_blocked(self):
        v = judge(rec(title="销售助理"))
        self.assertTrue(v.veto)
        self.assertFalse(v.deliverable)

    def test_course_consultant_blocked(self):
        v = judge(rec(title="课程顾问助理"))
        self.assertTrue(v.veto)

    def test_internship_blocked(self):
        v = judge(rec(title="AI助理实习生"))
        self.assertTrue(v.veto)

    def test_daily_wage_blocked(self):
        v = judge(rec(salary="150-200元/天"))
        self.assertTrue(v.veto)

    def test_masters_blocked(self):
        v = judge(rec(education_raw="硕士"))
        self.assertTrue(v.veto)

    def test_real_outsourcing_blocked(self):
        v = judge(rec(client_company="华为技术有限公司"))
        self.assertTrue(v.veto)

    def test_waipai_tag_only_reduces(self):
        plain = judge(rec())
        tagged = judge(rec(outsourced_tag=True))
        self.assertFalse(tagged.veto)
        self.assertLess(tagged.detail["公司"], plain.detail["公司"])

    def test_three_year_exp_blocked(self):
        v = judge(rec(experience_raw="3-5年", tech_tags=["3-5年"]))
        self.assertTrue(v.veto)

    def test_trainee_loan_blocked(self):
        v = judge(rec(jd_duty="岗位职责：需缴纳岗前培训费用 19800 元。"))
        self.assertTrue(v.veto)

    def test_hr_assistant_blocked_by_tags(self):
        # 标题「部门助理」看不出方向，标签「招聘」才是真相
        v = judge(rec(title="部门助理/综合服务专员",
                      tech_tags=["本科", "1-3年", "招聘"],
                      tags=["本科", "1-3年", "招聘"]))
        self.assertIn("纯职能助理", v.veto)
        self.assertFalse(v.deliverable)

    def test_tech_assistant_not_blocked(self):
        v = judge(rec(title="项目助理", tech_tags=["本科", "1-3年", "AI项目"],
                      tags=["本科", "1-3年", "AI项目"]))
        self.assertFalse(v.veto)
        self.assertTrue(v.deliverable)

    def test_finance_direction_trainee_blocked(self):
        v = judge(rec(title="财务管培生-华强集团"))
        self.assertTrue(v.veto)

    def test_27_campus_blocked(self):
        v = judge(rec(title="集团管培生【27校招】"))
        self.assertTrue(v.veto)

    def test_intern_blocked_by_default(self):
        v = judge(rec(title="AI产品经理（实习）"))
        self.assertIn("实习", v.veto)

    def test_intern_allowed_in_campus_batch(self):
        v = judge(rec(title="AI产品经理（实习）", jd_duty="参与大模型应用的产品设计，每周5天"),
                  allow_intern=True)
        self.assertFalse(v.veto)


class TestProductFamily(unittest.TestCase):
    """产品岗走产品权重：AI产品经理不能因为「没有 Vue/Docker」被判低分。"""

    def test_ai_product_manager_is_product_family(self):
        from jkit.score import job_family_of
        self.assertEqual(job_family_of("AI产品经理"), "product")
        self.assertEqual(job_family_of("大模型产品经理"), "product")
        self.assertEqual(job_family_of("AI应用开发工程师"), "ai_dev")

    def test_product_jd_scores_by_product_evidence(self):
        v = judge(rec(
            title="AI产品经理", education_raw="本科", experience_raw="1-3年",
            tech_tags=["本科", "1-3年"],
            jd_duty="负责智能体产品需求分析与 PRD 撰写，定义数据指标，做竞品分析和用户访谈，"
                    "推动大模型能力在业务落地。"))
        self.assertEqual(v.job_family, "product")
        self.assertFalse(v.veto)
        self.assertGreaterEqual(v.score, 65, v.as_dict())

    def test_product_without_any_product_evidence_scores_low(self):
        v = judge(rec(title="产品经理", education_raw="本科", experience_raw="1-3年",
                      tech_tags=["本科", "1-3年"], jd_duty="负责公司日常事务。"))
        self.assertLess(v.score, 55)


class TestParsers(unittest.TestCase):
    def test_salary(self):
        from jkit.score import parse_salary, parse_scale
        self.assertEqual(parse_salary("1.7-2.3万·16薪"), (17000, 23000))
        self.assertEqual(parse_salary("9000-11000元"), (9000, 11000))
        self.assertEqual(parse_salary("面议"), (None, None))
        self.assertEqual(parse_scale("1000-9999人"), 1000)
        self.assertEqual(parse_scale("20人以下"), 20)


class TestConditionDriven(unittest.TestCase):
    """阈值全部来自条件文件，改条件就改结论 —— 这是「条件从外部给」的验收点。"""

    def _cond(self, **kw):
        from jkit.conditions import Conditions
        c = Conditions.load()
        for k, v in kw.items():
            setattr(c, k, v)
        return c

    def test_salary_floor_from_condition(self):
        base = rec(salary="5000-7000元", salary_min=5000)
        self.assertFalse(judge(base).veto, "默认不设下限，不该被薪资挡掉")
        v = judge(base, self._cond(salary_floor=8000))
        self.assertIn("下限", v.veto)
        self.assertFalse(v.deliverable)

    def test_min_grade_from_condition(self):
        r = rec(title="AI应用开发工程师", salary="9000-14000元", salary_min=9000)
        loose = judge(r, self._cond(min_grade="C"))
        strict = judge(r, self._cond(min_grade="A"))
        self.assertTrue(loose.deliverable, loose.as_dict())
        if strict.grade == "A":
            self.assertTrue(strict.deliverable)
        else:
            self.assertFalse(strict.deliverable,
                             "投递线抬到 A 后，%s 档不该投" % strict.grade)

    def test_allow_intern_from_condition(self):
        r = rec(title="AI产品经理（实习）")
        self.assertTrue(judge(r, self._cond(allow_intern=False)).veto)
        self.assertFalse(judge(r, self._cond(allow_intern=True)).veto)

    def test_exp_ceiling_from_condition(self):
        r = rec(title="AI应用开发工程师", experience_raw="3-5年",
                tech_tags=["3-5年", "本科", "AI", "Python"])
        self.assertTrue(judge(r, self._cond(exp_ceiling_years=3)).veto)
        self.assertFalse(judge(r, self._cond(exp_ceiling_years=6)).veto,
                         "把经验上限放宽到 6 年后，3-5 年的岗就该放行")

    def test_my_education_drives_scoring(self):
        r = rec(education_raw="硕士", tech_tags=["硕士", "AI", "Python"])
        self.assertTrue(judge(r, self._cond(education="本科")).veto)
        self.assertFalse(judge(r, self._cond(education="硕士")).veto,
                         "条件是硕士时，硕士岗不该被硬否决")


class TestIndustryScope(unittest.TestCase):
    """用户口径（2026-11-16）：只投互联网 / AI 应用方向，传统行业不要乱投。"""

    def _cond(self):
        from jkit.conditions import Conditions
        return Conditions.load()

    def test_medical_company_is_vetoed(self):
        r = rec(title="医疗器械技术支持", company_name="深圳迈瑞医疗器械有限公司")
        v = judge(r, self._cond())
        self.assertTrue(v.veto)
        self.assertIn("行业", v.veto)

    def test_chip_company_is_vetoed(self):
        r = rec(company_name="广东某某半导体有限公司", company_industry="半导体")
        self.assertTrue(judge(r, self._cond()).veto)

    def test_internet_company_passes(self):
        r = rec(company_name="深圳市云智慧科技有限公司", company_industry="互联网")
        self.assertFalse(judge(r, self._cond()).veto)

    def test_jd_industry_words_do_not_veto(self):
        """JD 写「服务医疗/金融行业」是业务范围，不能把公司当医疗公司 —— 否则
        互联网公司的行业解决方案岗会被全误杀。"""
        r = rec(company_name="深圳某某软件有限公司", company_industry="互联网",
                jd_duty=("岗位职责：为医疗、金融行业客户提供 AI 解决方案，"
                         "负责需求分析与落地交付，欢迎应届生投递。") * 3)
        self.assertFalse(judge(r, self._cond()).veto)

    def test_deny_list_comes_from_conditions(self):
        """黑名单是数据不是代码：清空它就该不筛行业。"""
        c = self._cond()
        c.industry_deny = []
        r = rec(company_name="深圳迈瑞医疗器械有限公司")
        self.assertFalse(judge(r, c).veto)


class TestDirectionWhitelist(unittest.TestCase):
    """用户口径（2026-11-16）：只要 AI / 开发 / AI 产品 + 售前售后 + 计算机相关。

    互联网公司里的「融资管培生」「总经办管培生」也不能投 —— 行业对了不等于方向对。
    """

    def _cond(self):
        from jkit.conditions import Conditions
        return Conditions.load()

    def test_off_direction_titles_are_vetoed(self):
        bad = [
            "融资/政府关系储备校招管培生（J04113）",
            "总经办项目管培生（跨境）",
            "FCT助理结构工程师",
            "市场部管培生",
            "商务拓展专员",
        ]
        for title in bad:
            with self.subTest(title=title):
                v = judge(rec(title=title), self._cond())
                self.assertTrue(v.veto, "「%s」不该投" % title)
                self.assertIn("方向", v.veto)

    def test_on_direction_titles_pass(self):
        good = [
            "AI全栈技术管培生（校招/社招都可）",
            "售前解决方案工程师",
            "技术支持工程师",
            "AI产品经理",
            "大模型应用开发",
            "IT运维工程师",
        ]
        for title in good:
            with self.subTest(title=title):
                v = judge(rec(title=title), self._cond())
                self.assertNotIn("方向", v.veto or "", "「%s」属于目标方向，不该被方向挡" % title)

    def test_content_ops_titles_vetoed_by_title_rule(self):
        """标题里带 AI 也救不了内容/运营岗（AI视频制作、AI运营）。"""
        for title in ("AI视频制作", "AI漫剧制作", "用户洞察与 AI 运营专员"):
            with self.subTest(title=title):
                self.assertTrue(judge(rec(title=title), self._cond()).veto)

    def test_whitelist_is_data(self):
        """白名单来自条件：清空即不按方向筛。"""
        c = self._cond()
        c.direction_allow = []
        self.assertFalse(judge(rec(title="市场部管培生"), c).veto)


if __name__ == "__main__":
    unittest.main()
