"""岗位匹配判定（平台无关）—— 唯一的 veto + score 口径源。

为什么单独成模块：之前 BOSS 用 lanes/*.json 的声明式规则，智联用
runs/_zhilian/score.py 的一次性脚本，两边口径会漂：同一条「AI 产品助理」
在 BOSS 被白名单放过、在智联被低分埋掉。现在判定收敛到这里，
平台适配层只负责把页面字段归一成 :class:`JobRecord` 传进来。

## 用户口径（2026-11-13 18:45 原话「本科，助理，管培生，这些岗位都可以投递」）

1. **本科**：本人是 2026 届本科。学历写「本科」= 精准匹配，给门槛加分；
   「大专 / 学历不限」= 门槛更低，同样可投；只有「硕士 / 博士 / MBA」硬否决。
   （旧逻辑反了：给大专/中专/高中加分，本科不加分 —— 已修正。）
2. **助理**：产品助理 / 应用助理 / 研发助理 / 算法助理等 junior 岗位族**可投**，
   且视为应届友好（门槛分加成 + 方向分保底）。销售助理/客服助理/招生助理
   仍由 VETO_TITLE 先行否决，不能被 junior 白名单洗白。
3. **管培生**：管理培训生 / 储备干部 / 见习生 / 培训生同属 junior 岗位族，可投。
   但「销售管培生」「门店管培生」会被销售类 veto 挡掉。

## 条件从哪来

阈值不写死：`jkit.conditions.Conditions` 从外部 JSON 载入（学历、经验上限、
薪资下限、是否收实习、投递等级线），`judge(rec, cond)` 只按它判。
本模块保留的只是**词表**（哪些标题/JD 词属于硬否决、哪些技能词值多少分），
因为那是平台无关的常识，不是用户的筛选条件。

## 仍然硬否决（与上述放宽不冲突）

实习/兼职/日结、按天或按小时计薪、真外包（客户公司信息区块）、
销售/电话销售/招生/课程顾问/客服/猎头、车间轮岗、培训贷/押金、
异地 base、要求 3 年以上经验、要求硕士及以上学历、硬件/嵌入式/机械/视觉算法
等与本人技术栈（Vue3/React/Next + SpringBoot/NestJS + Docker + AI 工程）无关的岗位。

纯函数、零 IO、零网络，可单测复现。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .conditions import Conditions

#: 等级排序，用于 min_grade 比较
GRADE_ORDER = {"A": 3, "B": 2, "C": 1}

# ---------- 岗位族 ----------

#: junior 岗位族：用户明确「都可以投递」的初级岗位形态。
#: 命中后不再因为「职级低 / 技能词少」被埋掉，但**不能**覆盖硬否决。
JUNIOR_FAMILY = (
    "助理", "管培生", "管理培训生", "储备干部", "见习", "培训生",
    "应届生", "校招生",
)

#: 与本人技术栈明显无关、或属于纯线下/销售/体力岗的标题词。
VETO_TITLE = (
    ("实习", "实习岗"), ("兼职", "兼职"), ("日结", "日结"),
    ("标注", "数据标注"), ("招生", "招生/教培销售"), ("课程顾问", "课程顾问(销售)"),
    ("咨询顾问", "咨询顾问(销售)"), ("电话销售", "电话销售"), ("销售", "销售岗"),
    ("客服", "客服"), ("猎头", "猎头代招"),
    ("硬件", "硬件岗"), ("嵌入式", "嵌入式"), ("单片机", "单片机"),
    ("PLC", "PLC"), ("机械", "机械岗"), ("摄像", "摄像"),
    ("剪辑", "剪辑"), ("主播", "主播"), ("经纪人", "经纪人"),
    ("BD", "BD(销售)"), ("美工", "美工"), ("电商运营", "电商运营"),
    # 管培生/助理放宽后，必须按「培养方向」再收一道：本人是 2026 届软件工程本科，
    # 投 AI / 产品 / 研发 / 技术交付，不投财务、酒店、生产工艺、企划营销方向。
    ("财务", "财务方向"), ("审计", "审计方向"), ("税务", "税务方向"),
    ("会计", "会计方向"), ("酒店", "酒店方向"), ("生产工艺", "生产工艺方向"),
    ("工艺方向", "工艺方向"), ("市场营销方向", "市场营销方向"),
    ("企划", "企划/营销方向"), ("货运物流", "货运物流方向"),
    ("27校招", "27届校招(本人26届)"), ("27届", "27届校招(本人26届)"),
    ("2027届", "27届校招(本人26届)"),
    # 纯职能岗：与 AI / 技术 / 产品方向无关，放宽 junior 岗位族不等于无差别全投。
    ("推广", "推广/营销岗"), ("招聘", "招聘/HR岗"), ("行政", "行政岗"),
    ("人事", "人事岗"), ("前台", "前台"), ("文员", "文员"),
    # 内容 / 运营 / 外贸类（2026-11-16 用户口径：只要 AI、技术、产品、售前售后）。
    # 这些岗标题里常带「AI」字样（AI视频制作、AI短剧、AI运营），极易误投。
    ("视频", "视频/内容制作"), ("短剧", "短剧/内容制作"), ("漫剧", "短剧/内容制作"),
    ("制作", "内容制作"), ("剪辑", "内容制作"), ("拍摄", "内容制作"),
    ("运营", "运营岗"), ("营销", "营销岗"), ("内容", "内容岗"), ("文案", "内容岗"),
    ("主播", "主播"), ("带货", "直播带货"),
    ("外贸", "外贸/跟单"), ("跟单", "外贸/跟单"), ("报关", "外贸/跟单"),
    ("单证", "外贸/跟单"), ("采购", "采购岗"),
    ("教师", "教师岗"), ("老师", "教师岗"), ("助教", "教师岗"), ("讲师", "教师岗"),
    ("采集", "数据采集/标注"), ("标注", "数据标注"),
    ("设计师", "设计岗"), ("美工设计", "设计岗"),
)

#: JD 正文里的隐性条款 / 硬性坑。
VETO_JD = (
    ("车间轮岗", "车间轮岗"), ("基层轮岗", "基层轮岗"),
    ("外包", "疑似外包"), ("培训费", "疑似培训贷"), ("押金", "押金"),
    ("岗前培训", "岗前培训收费"),
    ("base 广西", "异地 base"), ("base广西", "异地 base"),
    ("常驻外地", "异地 base"), ("需长期出差", "长期出差"),
)

#: 学历档位。「岗位要求高于我的学历」才否决 —— 我的学历由 conditions 给，
#: 所以条件是本科时硕博岗被挡，条件是硕士时硕士岗放行、博士岗仍挡。
EDU_RANK = {"初中": 1, "中专": 2, "中技": 2, "高中": 2, "大专": 3,
            "本科": 4, "硕士": 5, "博士": 6, "MBA": 6}
EDU_LOWER = ("大专", "不限", "中专", "高中", "中技", "初中")

#: 技能词权重（本人技术栈 + AI 工程）。
TECH = {
    "vue": 3, "react": 3, "next.js": 3, "next": 1.5, "springboot": 3, "spring boot": 3,
    "nestjs": 3, "node": 2, "nodejs": 2, "docker": 2, "python": 2, "java": 2,
    "typescript": 2, "javascript": 1.5, "mysql": 1.5, "redis": 1.5,
    "langchain": 4, "langgraph": 4, "rag": 3.5, "agent": 3.5, "智能体": 3.5,
    "mcp": 4, "prompt": 2.5, "提示词": 2.5, "向量": 2.5, "llm": 2.5, "大模型": 2.5,
    "微服务": 1.5, "全栈": 2, "restful": 1, "git": 1, "linux": 1,
    "aigc": 2, "ai coding": 2.5, "知识库": 2,
}

FRESH = ("应届", "无经验", "在校", "实习经历", "毕业生", "培养", "带教",
         "1年以内", "欢迎投递", "26届", "2026届")

#: 纯职能方向：常只出现在卡片标签里（标题写「部门助理/综合服务专员」看不出来）。
#: 放宽 junior 岗位族后必须补这一层，否则 HR/行政助理会被当成「助理」投出去。
#: 只在 junior 族生效，且要求标签里同时没有任何技术/AI 词，避免误杀技术助理。
OFFTRACK_TAGS = ("招聘", "行政", "人事", "后勤", "文员", "前台", "财务",
                 "审计", "税务", "市场营销", "企划", "酒店", "餐饮", "客服")
TECH_SIGNAL = ("AI", "人工智能", "大模型", "LLM", "Agent", "智能体", "RAG", "算法",
               "Python", "Java", "Vue", "React", "Node", "SpringBoot", "开发",
               "软件", "数据", "产品", "项目", "技术", "研发", "测试", "运维",
               "Prompt", "LangChain", "Docker", "数据库", "SQL")

#: 产品岗方向词权重（2026-11-13 新增）。
#
# 为什么必须单列：产品岗 JD 里几乎没有 Vue/Docker 这类词，用开发栈打分会让
# 「AI产品经理（多模态Agent方向）」这种最贴的岗掉到 40 分 C 档 —— 方向维度
# 是"岗位要什么"而不是"我会什么"，产品岗要的是需求/PRD/指标/竞品这些证据。
PRODUCT = {
    "产品经理": 3, "产品设计": 3, "产品规划": 3, "产品方案": 3, "需求分析": 3.5,
    "需求文档": 3, "prd": 4, "原型": 3, "流程图": 2, "用户研究": 3, "用户访谈": 3,
    "竞品": 2.5, "数据指标": 2.5, "指标": 1.5, "ab测试": 3, "商业化": 2.5,
    "解决方案": 2.5, "客户": 2, "交付": 2, "落地": 2,
    "agent": 3.5, "智能体": 3.5, "大模型": 3, "llm": 3, "rag": 3,
    "prompt": 2.5, "提示词": 2.5, "工作流": 2, "知识库": 2.5, "多模态": 2.5,
    "b端": 2, "saas": 2, "paas": 1.5, "中台": 2, "mvp": 2,
}

#: 技术支持 / 售后 / 运维支持 方向词权重（2026-11-16 新增）。
#:
# 为什么单列：这类岗 JD 里几乎不写 Vue / Docker，用开发栈打分会让
# 「IT技术支持」「售后技术支持」这种最对口的岗掉到 C 档被埋掉。
# 它们真正要的是「排障 + 客户 + 响应 + 系统维护」这套证据。
SUPPORT = {
    "技术支持": 4, "it支持": 3.5, "售后": 3, "故障排查": 3.5, "排障": 3,
    "工单": 3, "sla": 3, "远程支持": 3, "驻场": 2.5, "响应": 2,
    "客户": 2, "安装部署": 2.5, "部署": 2, "调试": 2.5, "培训": 1.5,
    "文档": 1.5, "知识库": 2, "运维": 2.5, "系统维护": 2.5, "服务器": 2,
    "windows": 2, "linux": 2.5, "网络": 2, "tcp/ip": 2, "sql": 2,
    "数据库": 2, "虚拟化": 2, "ad域": 2, "itil": 2, "监控": 1.5,
    "zabbix": 2, "脚本": 1.5, "shell": 1.5, "python": 2,
}

#: 项目经理 / 交付管理 方向词权重（2026-11-16 新增）。
#:
# 「AI项目经理」标题里有 AI，先判 ai_dev 就会拿开发栈打分 → 压到 C 档。
# 项目经理要的是进度 / 风险 / 协调 / 验收这套证据，不是写代码的能力。
PM = {
    "项目经理": 4, "项目管理": 4, "pmp": 2, "进度": 2.5, "里程碑": 2.5,
    "风险": 2, "跨部门": 2.5, "协调": 2, "排期": 2.5, "验收": 2,
    "交付": 2.5, "需求": 2, "资源": 1.5, "复盘": 1.5, "周报": 1.5,
    "沟通": 1.5, "客户": 2, "敏捷": 2, "scrum": 2, "落地": 2,
    "ai": 2, "大模型": 2.5, "agent": 2, "智能体": 2,
}

#: 交付 / FDE / 实施 方向词权重（2026-11-16 新增）。
#:
# FDE（Forward Deployed Engineer）是「在客户现场写代码 + 交付方案」的混合岗：
# 光用技术支持词表会漏掉「方案设计」，光用产品词表会漏掉「部署排障」。
# 所以取两者并集、同词取大值，让它两边都能吃到分。
DELIVERY = dict(SUPPORT)
for _k, _w in PRODUCT.items():
    DELIVERY[_k] = max(_w, DELIVERY.get(_k, 0))
for _k, _w in {"方案设计": 3, "现场": 2.5, "客户现场": 3, "集成": 2.5,
               "对接": 2, "上线": 2, "培训交付": 2.5, "问题闭环": 2.5}.items():
    DELIVERY[_k] = max(_w, DELIVERY.get(_k, 0))

#: 方向词表按「岗位族」路由：product / tech_support / project_mgmt / delivery
#: 各用各的；其余族（开发类）仍用 TECH 开发栈词表。
FAMILY_TABLES = {
    "product": PRODUCT,
    "delivery": DELIVERY,
    "tech_support": SUPPORT,
    "project_mgmt": PM,
}


def direction_words(family: str) -> dict[str, float]:
    """某族实际参与打分的词表；未登记的族回落到开发栈 TECH。"""
    return dict(FAMILY_TABLES.get(family) or TECH)


#: 五维满分：方向 40 / 门槛 25 / 公司 20 / 待遇 10 / 应届友好 5
GRADE_A, GRADE_B = 70, 55


@dataclass
class JobRecord:
    """平台适配层交给判定器的标准化字段（字段契约）。

    只填读得到的字段；缺值一律留空，由规则决定 drop / pass，
    不许用「猜一个默认值」把 unknown 伪装成通过。
    """

    title: str = ""
    salary: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    city_raw: str = ""
    experience_raw: str = ""
    education_raw: str = ""
    company_name: str = ""
    company_scale: str = ""
    company_industry: str = ""
    company_financing: str = ""
    company_open_jobs: int | None = None
    client_company: str = ""      # 非空 = 真外包（智联「客户公司信息」区块）
    outsourced_tag: bool = False  # 卡片 waipai 标：只减分，不否决
    jd_duty: str = ""
    tech_tags: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    job_id: str = ""
    platform: str = ""
    url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "JobRecord":
        known = {f for f in cls.__dataclass_fields__}
        kw = {k: v for k, v in d.items() if k in known}
        kw["raw"] = dict(d)
        return cls(**kw)


@dataclass
class Verdict:
    score: float
    grade: str                 # A / B / C
    deliverable: bool          # 是否进入投递队列
    veto: str                  # 非空 = 被硬否决，含原因
    job_family: str            # junior / ai_dev / product / other
    reasons: list[str]
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score, "grade": self.grade,
            "deliverable": self.deliverable, "veto": self.veto,
            "job_family": self.job_family, "reasons": self.reasons,
            "detail": self.detail,
        }


def parse_salary(s: str) -> tuple[int | None, int | None]:
    """'1.7-2.3万' -> (17000, 23000)；'8-12K' -> (8000, 12000)；'9000-11000元' -> (9000, 11000)；'面议' -> (None, None)

    必须认 ``K``：BOSS 上 89% 的岗位写的是「8-12K」这种格式（263 条样本里 234 条）。
    旧版只认「万 / 元」，把 8-12K 解析成 8~12 元，待遇分恒为 1 分，
    等于给每条岗白扣 7-9 分 —— 大量本该 B 档的岗因此掉进 C 档投不出去。
    """
    s = re.sub(r"·\s*\d+\s*薪", "", s or "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*(万|千|[kK]|元)?", s)
    if not m:
        m2 = re.search(r"(\d+(?:\.\d+)?)\s*(万|千|[kK]|元)", s)
        if not m2:
            return None, None
        unit = m2.group(2)
        v = float(m2.group(1)) * (10000 if unit == "万" else (1000 if unit in ("k", "K", "千") else 1))
        return int(v), int(v)
    a, b, unit = float(m.group(1)), float(m.group(2)), m.group(3) or "元"
    mul = 10000 if unit == "万" else (1000 if unit in ("k", "K", "千") else 1)
    return int(a * mul), int(b * mul)


def parse_scale(s: str) -> int | None:
    """'300-499人' -> 300；'1000-9999人' -> 1000；'20人以下' -> 20"""
    m = re.search(r"(\d+)\s*-\s*(\d+)\s*人", s or "")
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*人(以下|以上)", s or "")
    return int(m.group(1)) if m else None


def exp_of(tags: list[str]) -> str:
    for t in tags or []:
        if "年" in t and ("-" in t or "以上" in t or "以下" in t):
            return t
    return ""


def edu_rank(edu: str) -> int:
    """岗位要求里最高的一档学历。读不出（不限/空）返回 0，表示不设门槛。"""
    e = (edu or "").strip()
    if not e or "不限" in e:
        return 0
    hit = [r for k, r in EDU_RANK.items() if k in e]
    return max(hit) if hit else 0


def exp_floor_years(exp: str) -> int | None:
    """'3-5年' -> 3；'1年以内' -> 0；'5年以上' -> 5；'应届/不限' -> 0；读不出 -> None。

    取**下限**而不是上限：岗位写「1-3年」，应届生也能投；写「3-5年」，
    门槛就是 3 年。拿上限判会把 1-3 年的岗全误杀。
    """
    e = (exp or "").strip()
    if not e:
        return None
    if re.search(r"应届|在校|无经验|经验不限|不限经验", e):
        return 0
    m = re.search(r"(\d+)\s*-\s*(\d+)\s*年", e)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*年(?:以内|以下)", e)
    if m:
        return 0
    m = re.search(r"(\d+)\s*年(?:以上|及以上|\+)", e)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*年", e)
    return int(m.group(1)) if m else None


def normalize_edu(rec: JobRecord) -> str:
    edu = rec.education_raw or ""
    if edu:
        return edu
    for t in rec.tags or []:
        if re.fullmatch(r"(初中|中专|中技|高中|大专|本科|硕士|博士|MBA|不限|学历不限)", t):
            return t
    return ""


def job_family_of(title: str) -> str:
    """只做分类，不做否决；顺序：junior > project_mgmt > product > tech_support > ai_dev > other。

    顺序都是踩出来的，不是随手排的：

    - **junior 最先**：「技术助理」含「助理」，先判 junior 才能吃到
      「助理/管培生本来就要应届」的门槛加成与方向分保底。
    - **project_mgmt 要在 product 之前**：「交付经理」既含「交付」也含「经理」，
      它要的是进度/风险/验收这套证据，不是产品经理的需求/PRD。
    - **product 要在 ai_dev 之前**：「AI产品经理」标题里有 AI，先判 ai_dev
      会拿开发栈打分（实测把 56 分的岗压到 40 分 C 档）。
    - **tech_support 要在 ai_dev 之前**：「AI技术支持」同理。
    """
    if any(k in title for k in JUNIOR_FAMILY):
        return "junior"
    if re.search(r"项目经理|项目管理|PMO|交付经理|实施经理", title, re.I):
        return "project_mgmt"
    if re.search(r"交付工程师|交付顾问|FDE|实施工程师|实施顾问|现场应用|驻场", title, re.I):
        return "delivery"
    if re.search(r"产品经理|产品专员|产品助理|产品设计|产品规划|解决方案|售前|交付|实施", title, re.I):
        return "product"
    if re.search(r"技术支持|售后|运维|客户成功|IT支持|FAE", title, re.I):
        return "tech_support"
    if re.search(r"AI|人工智能|大模型|LLM|Agent|智能体|RAG|算法", title, re.I):
        return "ai_dev"
    return "other"


def judge(rec: JobRecord | dict[str, Any], cond: Conditions | None = None,
          allow_intern: bool | None = None) -> Verdict:
    """对一条岗位做 veto + 五维打分。

    所有阈值来自 ``cond``；不传则用内置默认口径（本人 2026-11-13 的条件）。
    ``allow_intern`` 只为兼容旧调用保留：显式传了就盖过条件里的 allow_intern，
    用于「校招/实习批次」临时勘察。
    """
    cond = cond or Conditions()
    intern_ok = cond.allow_intern if allow_intern is None else bool(allow_intern)
    my_edu = (cond.education or "本科").strip()

    if isinstance(rec, dict):
        rec = JobRecord.from_dict(rec)

    title = rec.title or ""
    jd = rec.jd_duty or ""
    tags = list(rec.tech_tags or rec.tags or [])
    exp = exp_of(tags) or (rec.experience_raw or "")
    edu = normalize_edu(rec)
    family = job_family_of(title)
    reasons: list[str] = []

    def vetoed(why: str) -> Verdict:
        return Verdict(0, "C", False, why, family, [why], {})

    # ---- 硬否决（顺序即优先级；junior 白名单不得覆盖这一段）----
    for key, why in VETO_TITLE:
        if key == "实习" and intern_ok:
            continue
        if key in title:
            return vetoed("标题:" + why)
    if "元/天" in (rec.salary or "") or "元/时" in (rec.salary or ""):
        return vetoed("按天/时计薪")
    if edu_rank(edu) > edu_rank(my_edu):
        return vetoed("学历要" + edu)
    if rec.client_company:
        return vetoed("外包(客户:" + str(rec.client_company) + ")")
    for key, why in VETO_JD:
        if key in jd:
            return vetoed("JD:" + why)
    # 经验门槛：岗位最低要求超过「我的年限 + 上限」就否决。
    # 默认 exp_years=0 / ceiling=3 → 1-3 年可投，3-5 年否决（与既有口径一致）。
    lo = exp_floor_years(exp)
    if lo is not None and lo >= cond.exp_ceiling_years and lo > cond.exp_years:
        return vetoed("经验要" + exp)
    # ---- 行业口径：只要互联网 / AI 应用方向 ----
    # 只在「公司名 + 公司行业 + 标题」上匹配，**不看 JD 正文**：
    # JD 里写「服务于医疗、金融等行业」是业务范围，不等于本公司是医疗公司，
    # 拿 JD 判行业会把互联网公司的行业解决方案岗全误杀（2026-11-16 实测）。
    if cond.industry_deny:
        hay = " ".join([rec.company_name or "", rec.company_industry or "",
                        rec.company_financing or "", title])
        hit = [k for k in cond.industry_deny if k and k in hay]
        if hit:
            return vetoed("行业:" + hit[0])
    # ---- 方向白名单：标题必须落在 AI / 技术 / 产品 / 售前售后 里 ----
    # 2026-11-16 用户口径：只要 AI 应用、开发、AI 产品，加售前售后与计算机相关；
    # 融资管培生、总经办管培生、结构工程师这类即使公司是互联网也不投。
    # 放在行业判定之后：先按公司排除传统行业，再按标题排除非技术方向。
    if cond.direction_allow:
        t_low = title.lower()
        if not any(k and k.lower() in t_low for k in cond.direction_allow):
            return vetoed("方向不符:" + (title[:14] or "无标题"))

    # junior 族 + 标签全是纯职能词 + 没有任何技术信号 = HR/行政/财务助理，不投。
    if family == "junior":
        tag_line = " ".join(str(t) for t in (rec.tags or rec.tech_tags or []))
        if (any(k in tag_line for k in OFFTRACK_TAGS)
                and not any(k.lower() in tag_line.lower() for k in TECH_SIGNAL)):
            return vetoed("标签:纯职能助理(非技术方向)")

    # ---- 薪资下限（条件里留空 = 不按薪资淘汰）----
    lo_pay = rec.salary_min if rec.salary_min is not None else parse_salary(rec.salary)[0]
    if cond.salary_floor and lo_pay is not None and lo_pay < cond.salary_floor:
        return vetoed("薪资 %d < 下限 %d" % (lo_pay, cond.salary_floor))

    # ---- 用户口径：本科 / 助理 / 管培生 ----
    if edu and my_edu in edu:
        reasons.append("学历%s=精准匹配" % my_edu)
    elif edu and any(k in edu for k in EDU_LOWER):
        reasons.append("学历门槛更低=可投")
    if family == "junior":
        reasons.append("junior岗位族(助理/管培生)=可投且应届友好")

    # ---- 1) 方向 40 ----
    head = title + " " + " ".join(str(t) for t in tags)
    lowjd = jd.lower()
    lowhead = head.lower()
    ai_head = 12 if re.search(r"AI|人工智能|大模型|LLM|Agent|智能体|RAG|算法", head, re.I) else 0
    # 方向表按「岗位要什么」选，不按「我会什么」选。
    # product / tech_support / project_mgmt 三类岗的 JD 里基本不出现 Vue/Docker，
    # 用开发栈打分会把最对口的岗压到 C 档（实测产品岗 56→40、技术支持 52→31）。
    DIRECTION_TABLE = FAMILY_TABLES
    if family in DIRECTION_TABLE:
        phits = [(k, w) for k, w in DIRECTION_TABLE[family].items()
                 if k in lowhead or k in lowjd]
        direction = min(40, 10 + min(30, sum(w for _, w in phits)))
        hits = phits
    else:
        hits = [(k, w) for k, w in TECH.items() if k in lowhead or k in lowjd]
        direction = min(40, ai_head + min(28, sum(w for _, w in hits)))
    # junior 岗 JD 常写得很短、技能词少；给保底，不让它因为「词少」掉到 C。
    if family == "junior":
        direction = max(direction, 18)
        reasons.append("junior方向分保底18")

    # ---- 2) 门槛余量 25 ----
    if lo is None or lo <= cond.exp_years:
        exp_s = 25
    elif lo <= cond.exp_years + 1:
        exp_s = 18
    else:
        exp_s = 10
    if edu and my_edu in edu:
        exp_s = min(25, exp_s + 3)          # 学历精准匹配
    elif edu and any(k in edu for k in EDU_LOWER):
        exp_s = min(25, exp_s + 2)          # 门槛更低
    if family == "junior":
        exp_s = min(25, exp_s + 2)          # 助理/管培生本来就要应届
    gate = min(25, exp_s)

    # ---- 3) 公司质量 20 ----
    scale = parse_scale(rec.company_scale)
    if scale is None:
        comp_s = 10
    elif scale >= 1000:
        comp_s = 16
    elif scale >= 500:
        comp_s = 15
    elif scale >= 100:
        comp_s = 13
    elif scale >= 20:
        comp_s = 9
    else:
        comp_s = 4
    ind = rec.company_industry or ""
    if re.search(r"软件|互联网|计算机|人工智能|IT|信息技术", ind):
        comp_s += 3
    elif re.search(r"人力资源|劳务|中介", ind):
        comp_s -= 6
    if rec.company_financing == "已上市":
        comp_s += 1
    if rec.outsourced_tag:
        comp_s -= 6                          # 外派标：减分不减命
    if rec.company_open_jobs and rec.company_open_jobs > 150:
        comp_s -= 4                          # 在招职位异常多 = 疑似人力外包
    company = max(0, min(20, comp_s))

    # ---- 4) 待遇 10 ----
    pay_lo = lo_pay
    if pay_lo is None:
        pay = 5
    elif pay_lo >= 20000:
        pay = 10
    elif pay_lo >= 15000:
        pay = 9
    elif pay_lo >= 12000:
        pay = 8
    elif pay_lo >= 10000:
        pay = 7
    elif pay_lo >= 8000:
        pay = 5
    elif pay_lo >= 6000:
        pay = 3
    else:
        pay = 1

    # ---- 5) 应届友好 5 ----
    fresh = 5 if any(k in jd for k in FRESH) or family == "junior" else 0

    total = round(direction + gate + company + pay + fresh, 1)
    grade = "A" if total >= GRADE_A else ("B" if total >= GRADE_B else "C")
    # 投递线由条件决定：min_grade=B → A/B 投，C 不投。
    deliverable = GRADE_ORDER[grade] >= GRADE_ORDER.get(cond.min_grade, 2)
    detail = {
        "方向": round(direction, 1), "门槛": gate, "公司": company,
        "待遇": pay, "应届": fresh,
        "技能命中": [k for k, _ in hits][:10],
        "经验": exp or "-", "学历": edu or "-",
        "公司规模": rec.company_scale or "-",
        "投递线": cond.min_grade,
    }
    return Verdict(total, grade, deliverable, "", family, reasons, detail)
