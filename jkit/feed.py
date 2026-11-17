"""岗位数据归一 —— 从页面读到的东西，变成判定器和台账认得的形状。

两个平台的字段名不一样（BOSS 卡片叫 ``exp`` / ``company``，智联叫
``experience_raw`` / ``company_name``；面板里又各有一套），所以这里做唯一的
翻译层。判定器只认 :class:`jkit.score.JobRecord` 的字段名，不认识平台。

``collect_pages`` 是主链路的「货架」：逐词、逐页把卡片递出来，每次只递一页。
上层处理完一页再看下一页 —— 中途停下只丢一页，不丢一批。
"""
from __future__ import annotations

import json
import re
from typing import Iterator, Optional

from .errors import ResultStalled

# ---------- 页面原文 -> 结构化（解析只在这一处）----------
#
# 2026-11-13 实测 BOSS 面板原文，别再靠猜：
#   hr_active_raw = "3月内活跃"     （无标识时为空）
#   hr_online_raw = "在线" / null    （.boss-online-tag 只在真在线时存在）
#   header_line   = "日语翻译\n-K\n深圳\n在校/应届\n本科"（职位 / 薪资 / 城市 / 经验 / 学历）
#
# 薪资那行在「面议」时是 "-K"，不带数字 —— 解析不出来就当没有，不要填 0，
# 否则「薪资下限」这道门槛会把所有面议岗位误杀。

_EDU_RE = re.compile(r"^(初中|中专|中技|高中|大专|本科|硕士|博士|MBA|学历不限)$")
_EXP_RE = re.compile(
    r"(在校\s*/?\s*应届|应届毕业生|应届|经验不限|无经验|"
    r"\d+\s*-\s*\d+\s*年|\d+\s*年以内|\d+\s*年以上|\d+\s*年)")
_SALARY_RE = re.compile(r"(K|k|元|薪|面议)")
#: 城市只可能是 2-4 个汉字的短词；带「经理/专员/工程师」的显然还能是职位名，
#: 不能因为它是短词就当成城市（实测面板第一段是职位名，标点被吃掉的列表里
#: 很容易串位）。
_CITY_RE = re.compile(r"^[\u4e00-\u9fa5]{2,4}$")
_NOT_CITY = ("经理", "专员", "助理", "工程", "顾问", "主管", "总监", "老师",
             "翻译", "客服", "运营", "销售", "实习", "岗位", "招聘", "不限",
             "应届", "面议", "全职", "兼职")

#: 活跃度词 -> 天数当量。单位按「最多也就这么久」估，宁可高估（更容易被判超期）。
_UNIT_DAYS = {"分钟": 0, "小时": 0, "天": 1, "日": 1, "周": 7, "月": 30, "年": 365}


def active_days(raw: str) -> Optional[int]:
    """把「刚刚活跃 / 今日活跃 / 3日内活跃 / 本周活跃 / 3月内活跃」翻成天数。

    读不出来返回 None（由 policy 决定当淘汰还是放行），**不要**返回 0——
    0 的意思是「今天有动作」，和「不知道」是两件事。
    """
    t = (raw or "").strip()
    if not t:
        return None
    if "在线" in t:
        return 0
    if "刚刚" in t or "今日" in t or "今天" in t:
        return 0
    if "昨日" in t or "昨天" in t:
        return 1
    if "本周" in t or "这周" in t:
        return 7
    if "本月" in t or "半月" in t:
        return 15
    if "半年" in t:
        return 180
    m = re.search(r"(\d+)\s*(分钟|小时|天|日|周|月|年)", t)
    if m:
        return int(m.group(1)) * _UNIT_DAYS.get(m.group(2), 1)
    return None


def parse_header(line: str) -> dict:
    """拆 BOSS 面板顶部那行混排文本，补齐卡片上缺的经验/学历/城市/薪资。

    只做「补空」用：卡片上已经有值的，以卡片为准（卡片是列表页原文，
    面板那行在改版时更容易变）。
    """
    parts = [p.strip() for p in str(line or "").replace("\r", "\n").split("\n")]
    parts = [p for p in parts if p]
    out: dict = {}
    for p in parts[1:]:                       # 第 0 段是职位名，跳过
        if "education_raw" not in out and _EDU_RE.match(p):
            out["education_raw"] = p
        elif "experience_raw" not in out and _EXP_RE.search(p):
            out["experience_raw"] = _EXP_RE.search(p).group(1).replace(" ", "")
        elif "salary" not in out and _SALARY_RE.search(p):
            out["salary"] = p if re.search(r"\d", p) else ""
        elif ("city_raw" not in out and _CITY_RE.match(p)
              and not any(w in p for w in _NOT_CITY)):
            out["city_raw"] = p
    return {k: v for k, v in out.items() if v}


#: 面板字段 -> 统一字段。只搬判定/台账真正用到的，不多搬。
_DETAIL_MAP = (
    ("salary_min", "salary_min"),
    ("salary_max", "salary_max"),
    ("city_raw", "city_raw"),
    ("experience_raw", "experience_raw"),
    ("education_raw", "education_raw"),
    ("company_name", "company_name"),
    ("company_scale", "company_scale"),
    ("company_industry", "company_industry"),
    ("company_financing", "company_financing"),
    ("company_open_jobs", "company_open_jobs"),
    ("client_company", "client_company"),
    ("outsourced_tag", "outsourced_tag"),
    ("jd_duty", "jd_duty"),
    ("jd_brief", "jd_duty"),          # BOSS 面板叫 jd_brief，智联叫 jd_duty
    ("company_raw", "company_raw"),   # BOSS 面板给「公司名 · 融资 · 规模」，下面再拆
    ("tech_tags", "tech_tags"),
    ("hr_active_raw", "hr_active_raw"),
    ("hr_active_days", "hr_active_days"),
    ("hr_online", "hr_online"),
    # 智联独有：真正的「回复」信号（「今日回复 N 次」「高回复率」）。
    # BOSS 面板没有这两个字段，读不到就留空，由 apply 决定退回「今日在线」代理。
    ("hr_reply_count", "hr_reply_count"),
    ("hr_high_reply", "hr_high_reply"),
    ("hr_name", "hr_name"),
    ("hr_title", "hr_title"),
)


def card_record(card: dict) -> dict:
    """一张列表卡 -> 统一记录。卡片层能判的先判，判不了的留空等面板。

    注意 ``idx`` 是「第几张卡」，投递时靠它重新定位；换词后列表会重排，
    所以 idx 只在**同一页、同一时刻**有效，不能跨页面复用。
    """
    tags = list(card.get("tags") or [])
    return {
        "job_id": card.get("job_id") or "",
        "title": card.get("title") or "",
        "salary": card.get("salary") or "",
        "company_name": card.get("company") or card.get("company_name") or "",
        "experience_raw": card.get("experience_raw") or card.get("exp") or "",
        "education_raw": card.get("education_raw") or card.get("edu") or "",
        "city_raw": card.get("area") or card.get("city_raw") or "",
        "tags": tags,
        "tech_tags": [t for t in tags if not _is_meta_tag(t)],
        "jd_duty": card.get("jd") or "",
        "idx": card.get("idx"),
        "from": "card",
    }


def _is_meta_tag(t: str) -> bool:
    """经验/学历/地点这类标签不是技能，别混进 tech_tags 去凑方向分。"""
    import re
    return bool(re.fullmatch(
        r"(经验不限|无经验|应届|在校/?应届|\d+-\d+年|\d+年以内|\d+年以上|"
        r"本科|大专|硕士|博士|中专|中技|高中|初中|学历不限|"
        r"深圳|广州|上海|北京|杭州|东莞|珠海|惠州|.*区)", (t or "").strip()))


def detail_record(card_rec: dict, detail: dict) -> dict:
    """卡片记录 + 右侧面板读数 -> 合并记录。面板字段优先（更完整）。"""
    rec = dict(card_rec)
    for src, dst in _DETAIL_MAP:
        v = detail.get(src)
        if v not in (None, "", []):
            rec[dst] = v
    # 面板标题/薪资比卡片可信（卡片可能截断）
    for k in ("title", "salary"):
        if detail.get(k):
            rec[k] = detail[k]
    # 面板顶部混排行补空缺：卡片标签缺失时（智联常见）靠它兜底
    for k, v in parse_header(detail.get("header_line") or "").items():
        if not rec.get(k):
            rec[k] = v
    # 活跃度：面板给的是原文（"3月内活跃"），天数由这里算。
    # 以前这步在有 fields.py 时做过，重写时漏了 —— 结果全部岗位被
    # policy 判成「活跃度读不到」直接淘汰，一条都投不出去。
    if rec.get("hr_active_days") is None:
        rec["hr_active_days"] = active_days(detail.get("hr_active_raw") or "")
    online_txt = str(detail.get("hr_online_raw") or "") + str(detail.get("hr_active_raw") or "")
    rec["hr_online"] = ("在线" in online_txt) or bool(detail.get("hr_online"))
    if rec["hr_online"]:
        rec["hr_active_days"] = 0
    # BOSS 的 company_raw 有两种形状：
    #   "公司名 · 招聘者"            （面板给的，第一段就是公司名）
    #   "公司名 · 已上市 · 1000-9999人"（列表页给的，后面两段是公司画像）
    # 两种都取第一段当公司名，剩下的能认出规模就补规模。
    if detail.get("company_raw"):
        seg = [x.strip() for x in str(detail["company_raw"]).split("·") if x.strip()]
        if seg and not rec.get("company_name"):
            rec["company_name"] = seg[0]
        for s in seg[1:]:
            if re.fullmatch(r"(\d+-\d+人|\d+人以上|少于\d+人)", s):
                rec.setdefault("company_scale", s)
    rec["job_id"] = detail.get("job_id") or detail.get("active_id") or rec.get("job_id") or ""
    rec["url"] = detail.get("url") or rec.get("url") or ""
    rec["from"] = "panel"
    return rec


def read_cards(drv, adapter) -> list[dict]:
    """读一次当前列表。卡片数为 0 是合法状态（这个词没结果），不算错误。

    有的平台（智联）卡片标题是懒渲染组件，刚进页面时只有薪资没有标题；
    这时候读出来的 title 全空，判定层会整批当「无标题」淘汰掉。所以标题
    全空时等一等再读，最多 3 次 —— 只等「有卡但没标题」这一种情况。
    """
    out: list[dict] = []
    for attempt in range(3):
        out = _read_cards_once(drv, adapter)
        if not out or any(c.get("title") for c in out):
            return out
        if attempt < 2:
            drv.wait_ms(800)
    return out


def _read_cards_once(drv, adapter) -> list[dict]:
    try:
        d = drv.evaluate(adapter.script("cards"), retries=4)
        payload = json.loads(d.get("value") or "{}")
    except Exception as e:  # noqa: BLE001
        raise ResultStalled("读列表卡失败：%s" % str(e)[:160])
    return [card_record(c) for c in payload.get("cards") or []]


def locate_card(drv, adapter, job_id: str) -> Optional[int]:
    """按 ``job_id`` 在当前列表里重新定位卡片下标。

    换词/滚动后列表会整体重排，之前读到的 ``idx`` 就指向别的卡了 —— 继续用
    旧下标只会点空，白白攒出「连续失败」。``job_id`` 是稳定的，拿它重算。
    """
    if not job_id:
        return None
    for c in read_cards(drv, adapter):
        if (c.get("job_id") or "") == job_id:
            try:
                return int(c.get("idx"))
            except (TypeError, ValueError):
                return None
    return None


def collect_pages(drv, adapter, flow, cond,
                  limit: Optional[int] = None) -> Iterator[dict]:
    """逐词逐页产出 ``{"query", "page", "cards"}``。

    顺序固定：先 land + 筛选（由调用方在循环外做），再逐词 search，再逐页读。
    每个词读完 ``pages_per_query`` 页就换下一个词；滚动滚不动就提前结束该词。
    """
    seen_ids: set[str] = set()
    for q in cond.queries:
        print("\n===== [%s] %s =====" % (adapter.label or adapter.name, q), flush=True)
        flow.search(q)
        for page in range(1, max(1, cond.pages_per_query) + 1):
            cards = read_cards(drv, adapter)
            fresh = []
            for c in cards:
                key = c.get("job_id") or "%s|%s" % (c.get("title"), c.get("company_name"))
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                fresh.append(c)
            print("  第 %d 页：%d 张卡（新增 %d）" % (page, len(cards), len(fresh)), flush=True)
            yield {"query": q, "page": page, "cards": cards, "fresh": fresh}
            if limit and len(seen_ids) >= limit:
                return
            if page >= max(1, cond.pages_per_query):
                break
            prev_n = int((flow.last_state or {}).get("n") or len(cards))
            flow.load_more()
            if int((flow.last_state or {}).get("n") or 0) <= prev_n:
                print("  （滚不出新卡，本词结束）", flush=True)
                break
