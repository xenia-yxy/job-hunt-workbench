"""平台适配协议 —— 一个平台要能真用，必须实现这几件事。

少一件就老实标 ``tested = False``，不要在文档里写成「支持」。
新增平台 = 新建一个子包并注册，内核不动。

契约里最容易被忽略、但最影响成败的是 **``apply_filters``**：
招聘站的搜索结果是全量的，不先套平台原生筛选，就要在几万条里逐条开面板，
既慢又容易触发风控。顺序必须是「落地 → 筛选 → 换词 → 读卡 → 判 → 投」，
筛选失败一律抛错停下，不允许「筛不上就跳过继续跑」。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..errors import FilterNotApplied, SelectorMissing


def _js_literal(v) -> str:
    return json.dumps(v, ensure_ascii=False)


class Adapter:
    """平台适配基类。未实现的接口一律抛错，不提供「大概能用」的默认值。"""

    name = "base"
    tested = False
    label = ""
    login_url = ""
    login_markers = ()

    #: 列表里「有卡」的判据（等元素用）
    card_wait_selector = ""
    #: 卡片列表项选择器：按序号定位卡片（`querySelectorAll(...)[i]`）
    card_list_selector = ""
    #: 投递按钮选择器
    apply_wait_selector = ""
    #: 搜索框
    search_input_selector = ""

    # ---------- 页面脚本 / 选择器表 ----------

    def script(self, name: str) -> str:
        """返回页面内执行的 JS 源码，name ∈ {cards, panel, liststate}。

        JS 只负责把页面原文带回来，不做成败判断 —— 状态判定统一在 Python 侧，
        那里可单测。
        """
        p = Path(__file__).parent / self.name / "scripts" / ("%s.js" % name)
        if not p.exists():
            raise SelectorMissing("%s 缺少页面脚本 %s.js" % (self.name, name),
                                  hint="补 %s，或确认平台包完整" % p.name)
        return p.read_text(encoding="utf-8").strip()

    def sel(self, role: str) -> str:
        raise SelectorMissing(role, "该平台未提供选择器表")

    # ---------- URL ----------

    def landing_url(self, city_code: str) -> str:
        """落地页：只带城市、不带关键词。整轮只整页加载这一次。"""
        raise NotImplementedError

    def search_url(self, city_code: str, query: str) -> str:
        """搜索直链。只给排障 / 平台自检用，主链路走页面内输入。"""
        raise NotImplementedError

    # ---------- 卡片定位 ----------

    def mark_card(self, drv, idx: int, mark: str = "__jkit_card") -> str:
        """把第 idx 张卡打上临时 id 并滚到视口中间，返回可点击的选择器。

        为什么统一走「打标记」而不是拼 CSS：列表里的卡片常常不是容器的直接
        子元素（BOSS 的卡外面还套着 li），``:nth-child`` 会错位；打完标记再点
        是两者都稳的做法，顺带把「卡被顶部筛选条压住点不到」一起解决。
        """
        if not self.card_list_selector:
            raise SelectorMissing("card.list", "%s 没声明 card_list_selector" % self.name)
        js = """(() => {
          document.querySelectorAll('#%s').forEach(e => e.removeAttribute('id'));
          const c = [...document.querySelectorAll(%s)][%d];
          if (!c) return 'none';
          c.id = '%s';
          c.scrollIntoView({block: 'center'});
          return 'ok';
        })()""" % (mark, _js_literal(self.card_list_selector), int(idx), mark)
        if (drv.evaluate(js, retries=3).get("value") or "") != "ok":
            raise SelectorMissing("card[%d]" % idx,
                                  "列表里找不到第 %d 张卡（当前结构变了？）" % (idx + 1))
        return "#" + mark

    def card_click_targets(self, marked: str) -> list[str]:
        """点击目标，按优先级排列；后一个是前一个的降级。"""
        return [marked]

    # ---------- 筛选（先筛后看）----------

    def filter_chips(self, drv) -> list[str]:
        """当前已生效的筛选项文本，用于校验。"""
        return []

    def apply_filters(self, drv, wants: dict, human=None) -> dict:
        """把平台原生筛选设成 ``wants``（{筛选项名: 选项文案 | 选项文案列表}）。

        值支持单值（``"应届生"``）和多值（``["应届生", "1年以内"]``）两种写法；
        多值即「同一组的多个选项同时生效」（BOSS/智联的工作经验、学历都是多选组）。

        实现方必须：设完之后**回读校验**，不一致就抛 FilterNotApplied。
        这里不提供默认实现 —— 每个平台的筛选交互都不一样，猜出来的实现
        比没有更危险。
        """
        raise NotImplementedError("平台 %s 未实现 apply_filters" % self.name)

    def verify_filters(self, drv, wants: dict) -> tuple[bool, str]:
        """默认校验：每个期望选项都要出现在当前 chips 里。

        多选筛选（一个条件对应多个标签）也能过；顺序无关。
        """
        chips = self.filter_chips(drv)
        line = " / ".join(chips)
        missing = [v for vals in normalize_filters(wants).values()
                   for v in vals if v not in line]
        if missing:
            return False, "期望 %s，页面 chips=%s" % ("、".join(missing), line or "空")
        return True, line

    # ---------- 投递 ----------

    def apply_label(self, state: dict) -> str:
        """从面板状态里取「投递按钮」的文案。不同平台字段名不同。"""
        return str(state.get("apply_label") or state.get("chat_label") or "")

    #: 可以投的按钮文案；已投的文案。各平台只有这两组，差异在这里收口。
    APPLY_READY = ("立即沟通", "立即投递", "立即联系")
    APPLY_DONE = ("继续沟通", "继续投递", "已投递", "已沟通")

    def apply_state(self, label: str) -> str:
        """按钮状态：ready（可投）/ already（投过）/ none（没有按钮）。"""
        b = (label or "").strip()
        if not b:
            return "none"
        if any(w in b for w in self.APPLY_DONE):
            return "already"
        if any(w in b for w in self.APPLY_READY):
            return "ready"
        return "none"

    def classify_receipt(self, dialog: str, button_label: str) -> tuple:
        """把弹窗文案 + 按钮文案翻译成投递状态。

        返回 (state, evidence)。state ∈
          delivered / blocked_by_quota / blocked_by_platform / blocked_by_risk /
          already / no_button / unknown
        """
        raise NotImplementedError

    # ---------- 登录 / 风控 ----------

    def page_text(self, drv) -> str:
        """当前页面可见文本，供 guard 查风控关键词。读不到返回空串。"""
        return ""

    def needs_login(self, page_text: str) -> bool:
        return any(m in (page_text or "") for m in self.login_markers)


_REGISTRY: dict[str, Adapter] = {}


def register(adapter_cls):
    inst = adapter_cls()
    _REGISTRY[inst.name] = inst
    return adapter_cls


def get(platform: str) -> Adapter:
    if platform not in _REGISTRY:
        raise SystemExit("未知平台：%s（可选：%s）"
                         % (platform, ", ".join(sorted(_REGISTRY)) or "无"))
    a = _REGISTRY[platform]
    if not a.tested:
        raise SystemExit("平台 %s 尚未实测，拒绝执行。先补适配器并跑通一次真实投递。"
                         % platform)
    return a


def all_platforms() -> list[Adapter]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def filter_failed(platform: str, detail: str) -> FilterNotApplied:
    """统一的筛选失败异常，省得每个适配器各写一遍文案。"""
    return FilterNotApplied("平台 %s 筛选没生效：%s" % (platform, detail))


def normalize_filters(wants: dict | None) -> dict:
    """``{筛选项: 值 | [值...]}`` -> ``{筛选项: [值...]}``。

    旧条件文件全是单值写法，保持兼容；空值/空列表整项丢掉 —— 否则会变成
    「点了触发器但没选项可选」的空转。顺序保留，多选组的点选顺序按条件里写的来。
    """
    out: dict = {}
    for label, v in (wants or {}).items():
        if isinstance(v, (list, tuple)):
            vals = [str(x).strip() for x in v if str(x).strip()]
        else:
            s = str(v).strip() if v is not None else ""
            vals = [s] if s else []
        if vals:
            out[str(label)] = vals
    return out


def filter_line(wants: dict | None) -> str:
    """``{筛选项: 值|值}`` -> 「工作经验=应届生/1年以内」，日志与报错共用一句话。"""
    norm = normalize_filters(wants)
    return " / ".join("%s=%s" % (k, "/".join(v)) for k, v in norm.items())
