"""投递条件契约 —— 系统里**唯一**的「筛什么」入口。

为什么单独成模块：以前条件散在三处（`lanes/*/lane.json` 的声明式规则、
`fields.py` 的必填字段清单、`score.py` 里写死的学历/经验阈值），改一个口径要
同时改三处，还经常漏。现在只有一个 JSON：

    {
      "id": "cond-ai-product-sz",
      "queries": ["AI产品经理", "产品助理"],
      "city": "深圳",
      "city_code": {"boss": "101280600", "zhilian": "765"},
      "filters": {
        "boss":    {"求职类型": "全职", "学历要求": "本科", "工作经验": "应届生"},
        "zhilian": {"学历要求": "本科", "工作经验": "无经验"}
      },
      "profile": {"education": "本科", "exp_years": 0, "graduation": "2026"},
      "deliver": {"min_grade": "B", "max": null, "daily_limit": 100}
    }

这份 JSON 可以由外部生成（Agent、表格导出、另一个系统），内核只负责
「按条件去筛、按条件去判」，不猜、不补、不写死。

`filters` 是**平台原生筛选**，必须在逐条读卡之前应用 —— 这是「先筛后看」的
硬约束：先在全量结果里逐条开面板，等于把算力和时间浪费在注定不投的岗上。
筛选值用页面上的中文文案（`本科`、`应届生`），适配器负责把它翻译成 DOM 操作；
平台不认识的筛选项一律报错，不静默跳过。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields as dc_fields
from pathlib import Path
from typing import Any

from .errors import ConditionError

#: 内置默认口径 = 本人当前条件（2026-11-13）。外部传 JSON 时整份覆盖。
DEFAULT = {
    "id": "cond-default",
    "queries": ["AI产品经理", "产品助理", "AI应用开发", "大模型应用开发", "Agent开发"],
    "city": "深圳",
    "city_code": {"boss": "101280600", "zhilian": "765"},
    "filters": {
        "boss": {"求职类型": "全职", "学历要求": "本科",
                 "工作经验": ["应届生", "1年以内"]},
        "zhilian": {"学历": "本科", "经验": "1年以下"},
    },
    "profile": {
        "education": "本科",
        "exp_years": 0,
        "exp_ceiling_years": 3,      # 岗位要求超过这个年限 = 硬否决
        "graduation": "2026",
        "salary_floor": None,        # 留空 = 不按薪资淘汰
        "allow_intern": False,
        "tech_stack": [
            "Vue3", "React", "Next.js", "TypeScript", "SpringBoot", "NestJS",
            "Node", "Docker", "Python", "Java", "MySQL", "Redis",
            "LangChain", "LangGraph", "RAG", "Agent", "MCP", "Prompt", "AIGC",
        ],
        # 只要互联网 / AI 应用方向。传统行业命中就否决 —— 宁可少投，不乱投。
        "industry_deny": [
            "医疗器械", "医疗科技", "医疗", "生物", "制药", "医药", "医院", "诊所",
            "半导体", "芯片", "晶圆", "集成电路", "电子元器件",
            "精密", "模具", "注塑", "五金", "机械", "机床", "钢结构",
            "化工", "涂料", "钢铁", "水泥", "建材", "纺织", "服装", "食品", "农业",
            "调香", "香精", "香料", "日化", "化妆品", "饮料", "酒业", "烟草",
            "家具", "家电", "玩具", "印刷", "包装", "安防", "门禁", "楼宇",
            "电力", "电气", "仪器仪表", "自动化设备", "无人机", "锂电",
            "房地产", "物业", "装饰", "建筑", "工程监理", "工程造价",
            "餐饮", "酒店", "旅游", "美容", "健身", "物流", "快递", "货运", "仓储",
            "保险", "证券", "银行", "信贷", "理财",
            "教育培训", "K12", "课程顾问",
            "汽车零部件", "新能源电池", "光伏", "储能",
        ],
        "industry_allow": [
            "互联网", "软件", "信息技术", "人工智能", "AI", "云计算", "大数据",
            "SaaS", "计算机", "科技", "网络", "信息服务",
        ],
        # 方向白名单：标题必须命中其一才投（2026-11-16 用户口径）。
        # 不含「工程师 / 技术 / 助理 / 管培生」这类泛词 —— 那会把结构工程师、
        # 融资管培生、总经办管培生全放进来，用户明确说「不要乱投」。
        "direction_allow": [
            "AI", "人工智能", "大模型", "LLM", "Agent", "智能体", "RAG", "AIGC",
            "Prompt", "算法", "机器学习", "深度学习", "视觉", "多模态", "模型",
            "产品经理", "产品助理", "产品专员", "产品设计", "产品运营",
            "技术助理", "技术专员", "研发助理",
            "售前", "解决方案", "技术支持", "售后", "客户成功", "实施", "交付",
            "FDE", "FAE", "开发", "研发", "程序员", "软件", "前端", "后端",
            "全栈", "测试", "运维", "系统", "网络", "数据库", "云计算",
            "Java", "Python", "计算机", "数据分析",
        ],
    },
    "deliver": {
        "min_grade": "B",
        "max": None,
        "daily_limit": 100,
        "budget_min": 40,
        "pace": "fast",
        "pages_per_query": 1,
    },
}


def apply_profile(data: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
    """把 `profile_ref` 引用的画像展开成条件里的显式字段。

    为什么要这一层：城市、城市码、学历/经验筛选、技术栈、届别这些是**求职者属性**，
    不是岗位族差异。以前 6 份条件各抄一遍，改一次技术栈要动 6 个文件还必漏。
    现在条件只写「本族不同的部分」，共用部分从画像取。

    合并顺序（后者覆盖前者）：
        画像 conditions_profile → 条件 profile → 条件 profile_overrides

    城市 / 城市码从画像 identity 补，平台原生筛选项从 target_filters 补；
    条件自己写了就不动 —— 保留单份条件可以覆盖画像的能力。
    没有 `profile_ref` 或没传 profile 时原样返回，老格式文件行为不变。
    """
    if not isinstance(data, dict) or not data.get("profile_ref") or not profile:
        return data

    out = dict(data)
    prof: dict[str, Any] = {}
    prof.update(profile.get("conditions_profile") or {})
    prof.update(data.get("profile") or {})
    prof.update(data.get("profile_overrides") or {})
    if prof:
        out["profile"] = prof

    ident = profile.get("identity") or {}
    if not out.get("city") and ident.get("city"):
        out["city"] = ident["city"]
    if not out.get("city_code") and ident.get("city_code"):
        out["city_code"] = ident["city_code"]
    if not out.get("filters") and profile.get("target_filters"):
        out["filters"] = profile["target_filters"]
    return out


@dataclass
class Conditions:
    """一份投递条件。字段与 JSON 一一对应，不做隐式默认填充。"""

    id: str = "cond-default"
    queries: list[str] = field(default_factory=list)
    city: str = ""
    city_code: dict[str, str] = field(default_factory=dict)
    filters: dict[str, dict[str, Any]] = field(default_factory=dict)

    # profile
    education: str = ""
    exp_years: float = 0
    exp_ceiling_years: float = 3
    graduation: str = ""
    salary_floor: int | None = None
    allow_intern: bool = False
    tech_stack: list[str] = field(default_factory=list)

    # 行业口径：只要互联网 / AI 应用方向，传统行业（医疗、制造、芯片…）一律不投。
    # 只在「公司名 + 公司行业 + 标题」上匹配，不看 JD 正文 —— JD 里写
    # 「服务于医疗、金融等行业」是业务范围，不等于本公司是医疗公司。
    industry_deny: list[str] = field(default_factory=list)
    industry_allow: list[str] = field(default_factory=list)

    # 方向白名单：标题必须命中其中之一，否则不投。
    # 只管「是不是我们要的方向」，深浅由五维打分决定。留空 = 不按方向筛。
    direction_allow: list[str] = field(default_factory=list)

    # deliver
    min_grade: str = "B"
    max_deliver: int | None = None
    daily_limit: int = 100
    budget_min: float = 40
    pace: str = "fast"
    pages_per_query: int = 1

    # ---------- 构造 ----------

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Conditions":
        """嵌套 JSON -> 扁平对象。缺省项用 DEFAULT 补，未知键直接报错（防手误）。"""
        merged = _deep_merge(json.loads(json.dumps(DEFAULT)), d or {})
        prof = merged.get("profile") or {}
        dlv = merged.get("deliver") or {}
        known = {f.name for f in dc_fields(cls)}
        kw: dict[str, Any] = {
            "id": merged.get("id") or "cond",
            "queries": _as_list(merged.get("queries")),
            "city": str(merged.get("city") or ""),
            "city_code": {str(k): str(v) for k, v in (merged.get("city_code") or {}).items()},
            "filters": {str(k): {str(a): _filter_values(b) for a, b in (v or {}).items()}
                        for k, v in (merged.get("filters") or {}).items()},
            "education": str(prof.get("education") or ""),
            "exp_years": _num(prof.get("exp_years"), 0),
            "exp_ceiling_years": _num(prof.get("exp_ceiling_years"), 3),
            "graduation": str(prof.get("graduation") or ""),
            "salary_floor": _opt_int(prof.get("salary_floor")),
            "allow_intern": bool(prof.get("allow_intern")),
            "tech_stack": _as_list(prof.get("tech_stack")),
            "industry_deny": _as_list(prof.get("industry_deny")),
            "industry_allow": _as_list(prof.get("industry_allow")),
            "direction_allow": _as_list(prof.get("direction_allow")),
            "min_grade": str(dlv.get("min_grade") or "B").upper(),
            "max_deliver": _opt_int(dlv.get("max")),
            "daily_limit": int(_num(dlv.get("daily_limit"), 100)),
            "budget_min": _num(dlv.get("budget_min"), 40),
            "pace": str(dlv.get("pace") or "fast"),
            "pages_per_query": int(_num(dlv.get("pages_per_query"), 1)),
        }
        unknown = set(kw) - known
        if unknown:
            raise ConditionError("内部字段不匹配：%s" % ", ".join(sorted(unknown)))
        return cls(**kw)

    @classmethod
    def load(cls, path: str | Path | None = None,
             profile: dict[str, Any] | None = None) -> "Conditions":
        """从 JSON 载入；不传路径就用内置默认口径。

        `profile` 传入画像 dict 时，若条件里有 `profile_ref` 就先展开再构造，
        这样「画像只存一份、条件只管本族差异」才成立；不传则按老格式原样处理。
        """
        if not path:
            return cls.from_dict(DEFAULT)
        p = Path(path).expanduser()
        if not p.exists():
            raise ConditionError("条件文件不存在：%s" % p)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ConditionError("条件文件不是合法 JSON：%s（%s）" % (p, e))
        if not isinstance(data, dict):
            raise ConditionError("条件文件顶层必须是对象：%s" % p)
        cond = cls.from_dict(apply_profile(data, profile))
        cond.validate()
        return cond

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "queries": list(self.queries),
            "city": self.city,
            "city_code": dict(self.city_code),
            "filters": {k: {a: (list(b) if isinstance(b, (list, tuple)) else b)
                            for a, b in (v or {}).items()}
                        for k, v in self.filters.items()},
            "profile": {
                "education": self.education,
                "exp_years": self.exp_years,
                "exp_ceiling_years": self.exp_ceiling_years,
                "graduation": self.graduation,
                "salary_floor": self.salary_floor,
                "allow_intern": self.allow_intern,
                "tech_stack": list(self.tech_stack),
                "industry_deny": list(self.industry_deny),
                "industry_allow": list(self.industry_allow),
                "direction_allow": list(self.direction_allow),
            },
            "deliver": {
                "min_grade": self.min_grade,
                "max": self.max_deliver,
                "daily_limit": self.daily_limit,
                "budget_min": self.budget_min,
                "pace": self.pace,
                "pages_per_query": self.pages_per_query,
            },
        }

    # ---------- 平台视图 ----------

    def for_platform(self, platform: str) -> dict[str, Any]:
        """某平台这一轮要用的部分：城市码 + 原生筛选。没有该平台配置就报错。

        筛选值可能是单值（``"应届生"``）或多值（``["应届生", "1年以内"]``），
        原样交给适配器，由 ``platforms.base.normalize_filters`` 统一归一化 ——
        条件层不猜平台的单选/多选差异。
        """
        if platform not in self.city_code:
            raise ConditionError(
                "条件 %s 没给平台 %s 的 city_code（已有：%s）"
                % (self.id, platform, ", ".join(sorted(self.city_code)) or "无"))
        filters = {}
        for label, v in (self.filters.get(platform) or {}).items():
            filters[label] = list(v) if isinstance(v, (list, tuple)) else v
        return {
            "city": self.city,
            "city_code": self.city_code[platform],
            "filters": filters,
        }

    def validate(self) -> None:
        """开跑前的硬校验：缺词、缺城市、等级写错都不许开跑。"""
        if not self.queries:
            raise ConditionError("queries 为空：至少要有一个搜索词")
        if not self.city:
            raise ConditionError("city 为空：不指定城市会投到别的城市去")
        if self.min_grade not in ("A", "B", "C"):
            raise ConditionError("min_grade 只能是 A/B/C，收到 %r" % self.min_grade)
        if self.exp_ceiling_years <= 0:
            raise ConditionError("exp_ceiling_years 必须 > 0")
        if self.salary_floor is not None and self.salary_floor < 0:
            raise ConditionError("salary_floor 不能为负")
        for p in self.filters:
            if p not in self.city_code:
                raise ConditionError(
                    "filters 里出现了未给 city_code 的平台 %r" % p)
            for label, v in (self.filters.get(p) or {}).items():
                if isinstance(v, (list, tuple)):
                    if not v:
                        raise ConditionError(
                            "filters.%s.%s 是空列表：要么写值，要么整项删掉" % (p, label))
                    if any(not str(x).strip() for x in v):
                        raise ConditionError(
                            "filters.%s.%s 里有空值：%r" % (p, label, v))
                elif not isinstance(v, str):
                    raise ConditionError(
                        "filters.%s.%s 只能是字符串或字符串数组，收到 %r"
                        % (p, label, v))


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    return [str(x).strip() for x in v if str(x).strip()]


def _filter_values(v: Any) -> str | list[str]:
    """筛选项的值：单值写 ``"应届生"``，多值写 ``["应届生", "1年以内"]``。

    多值 = 同一组的多个选项**同时生效**（BOSS/智联的工作经验都是多选组）。
    这里只做形态保留，不把 list 拍平成字符串 —— 拍平后适配器会当成一个
    名叫 ``['应届生']`` 的选项去找，报「没有该选项」，最难查。
    """
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return str(v).strip()


def _num(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _opt_int(v: Any) -> int | None:
    if v in (None, "", "null"):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
