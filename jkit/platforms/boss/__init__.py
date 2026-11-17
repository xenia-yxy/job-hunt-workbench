"""BOSS 直聘适配器。

所有选择器与回执文案来自 2026-11-11 ~ 11-13 的真实运行，不是猜的。
页面改版后只需改 ``selectors.json`` 与本文件的 ``apply_filters``，内核不动。

筛选（先筛后看）实测要点（2026-11-13）：

1. 筛选条是 ``div.c-filter-condition``，里面每个 ``div.condition-filter-select``
   是一个条件；选项在 ``.filter-select-dropdown li``。
2. 触发器**可以原生点击**，选项也可以；这点和搜索页的经验筛选不同（那边只能 JS 点）。
3. 选项元素的 id 会被 Vue 重渲染冲掉，所以定位用「子元素下标 + nth-child」，
   不能靠打 id 再接选择器。
4. 点选后整页会重新拉结果，等 1.5 秒再读 chips 校验。
5. 已生效的条件会从「学历要求」这类占位文案变成「本科」，所以**已经对了就跳过**，
   不去重新点它 —— 重新点反而定位不到。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path

from ..base import Adapter, filter_failed, normalize_filters, register
from ...errors import FilterNotApplied, SelectorMissing

BASE = "https://www.zhipin.com"

_SEL_PATH = Path(__file__).parent / "selectors.json"
_SEL_CACHE = {"mtime": None, "data": {}}

# 顺序有意义：配额最先判，否则会被「已向」之类误判成成功
QUOTA_PATTERNS = (r"已达到沟通上限", r"已与\s*\d+\s*位\s*BOSS\s*沟通", r"休息一下，明天再来")
DELIVERED_PATTERNS = (r"已向\s*BOSS\s*发送消息", r"发送成功", r"已发送")
PLATFORM_BLOCK_PATTERNS = (r"不匹配", r"不符合", r"无法沟通", r"暂不支持")

# 风控/验证文案：命中即停，绝不重试（2026-11-13 实测「账户存在异常行为…」）
RISK_PATTERNS = (
    r"存在异常行为", r"暂时被限制访问", r"访问受限", r"限制访问",
    r"安全验证", r"人机验证", r"请完成验证", r"拖动滑块", r"滑动验证",
    r"操作过于频繁", r"网络环境异常", r"系统检测到异常", r"captcha",
)

#: 读筛选条：每个条件当前显示什么、有哪些选项
FILTER_STATE_JS = r"""(() => {
  const bar = document.querySelector('.c-filter-condition');
  if (!bar) return JSON.stringify({ok: false, reason: 'no_bar'});
  const txt = e => (e ? (e.textContent || '').replace(/\s+/g, ' ').trim() : '');
  const groups = [...bar.children];
  return JSON.stringify({
    ok: true,
    chips: [...bar.querySelectorAll('.current-select')].map(txt).filter(Boolean),
    groups: groups.map((g, i) => ({
      i: i,
      placeholder: txt(g.querySelector('.placeholder-text')),
      text: txt(g).slice(0, 40),
      options: [...g.querySelectorAll('.filter-select-dropdown li')].map(txt),
      // 选中态靠 li.active —— chips 只会显示「求职类型(1)」，读不出具体选了哪个
      selected: [...g.querySelectorAll('.filter-select-dropdown li.active')].map(txt),
    })),
  });
})()"""


def load_selectors() -> dict:
    """读 selectors.json。改版后只改这个 JSON，不动代码。"""
    if not _SEL_PATH.exists():
        raise SelectorMissing("*", "缺少 %s" % _SEL_PATH.name)
    mt = _SEL_PATH.stat().st_mtime
    if _SEL_CACHE["mtime"] != mt:
        _SEL_CACHE["data"] = json.loads(_SEL_PATH.read_text(encoding="utf-8"))
        _SEL_CACHE["mtime"] = mt
    return _SEL_CACHE["data"]


@register
class Boss(Adapter):
    name = "boss"
    tested = True
    label = "BOSS 直聘"
    login_url = BASE + "/web/user/"
    login_markers = ("扫码登录", "登录/注册", "手机号登录")

    card_wait_selector = ".card-area"
    card_list_selector = ".card-area"
    apply_wait_selector = ".op-btn-chat"

    # ---------- 选择器 ----------

    def sel(self, role: str) -> str:
        data = load_selectors()
        node = (data.get("selectors") or {}).get(role)
        if not node or not node.get("sel"):
            raise SelectorMissing(role, "selectors.json 里没有这一项")
        n = node.get("count")
        if n is not None and n != 1:
            raise SelectorMissing(role, "命中 %s 个元素，不唯一" % n)
        return node["sel"]

    def _sel_soft(self, role: str, fallback: str = "") -> str:
        try:
            return self.sel(role)
        except SelectorMissing:
            return fallback

    @property
    def search_input_selector(self) -> str:  # type: ignore[override]
        return self._sel_soft("search.input", 'input[placeholder="搜索职位、公司"]')

    # ---------- URL ----------

    def landing_url(self, city_code: str) -> str:
        """只带城市、不带 query：整轮只整页加载这一次，关键词靠搜索框输入。"""
        return "%s/web/geek/jobs?city=%s" % (BASE, city_code or "101280600")

    def search_url(self, city_code: str, query: str) -> str:
        return "%s/web/geek/jobs?city=%s&query=%s" % (
            BASE, city_code or "101280600", urllib.parse.quote(query))

    # ---------- 卡片 ----------

    def card_click_targets(self, marked: str) -> list[str]:
        # 真人点标题；标题点不到再退回整张卡
        return [marked + " a.job-name", marked]

    # ---------- 筛选 ----------

    def _filter_state(self, drv) -> dict:
        try:
            d = drv.evaluate(FILTER_STATE_JS, retries=4)
        except Exception as e:  # noqa: BLE001
            raise filter_failed(self.name, "读筛选条失败：%s" % str(e)[:120])
        return json.loads(d.get("value") or "{}")

    def _wait_bar(self, drv, tries: int = 6, gap_ms: int = 1200) -> dict:
        """等筛选条渲染出来。

        落地页刚打开时列表和筛选条是异步渲染的，读太早会拿到 ``no_bar``；
        直接报错会把一整个方向白丢（2026-11-16 实测：tech-assistant /
        tech-support 两个方向就是这样 0 条收场的）。
        """
        st: dict = {}
        for i in range(tries):
            st = self._filter_state(drv)
            if st.get("ok"):
                return st
            if i + 1 < tries:
                drv.wait_ms(gap_ms)
        return st

    def _wait_selected(self, drv, label: str, option: str,
                       timeout: float = 6.0, gap_ms: int = 400) -> bool:
        """等某个选项真的变成选中态。BOSS 的 li.active 是结果刷新之后才更新的。"""
        t0 = time.time()
        while time.time() - t0 <= timeout:
            g = self._find_group(self._filter_state(drv).get("groups") or [], label)
            if option in ((g or {}).get("selected") or []):
                return True
            drv.wait_ms(gap_ms)
        return False

    def filter_chips(self, drv) -> list[str]:
        return list(self._filter_state(drv).get("chips") or [])

    def apply_filters(self, drv, wants: dict, human=None) -> dict:
        """逐项设置原生筛选，最后回读校验。设不上就抛错，绝不继续跑。

        实测（2026-11-13 / 11-16）四条硬事实：

        1. chips 只有计数（``工作经验(2)``），读不出具体选项 → 要看 ``li.active``。
        2. 选项元素的 id 会被 Vue 重渲染冲掉 → 用「组下标 + li 下标」定位。
        3. 已选中的项再点一次会取消 → 先看 ``selected``，已经对了就跳过。
        4. 组是**多选**的：工作经验同时选「应届生」+「1年以内」→ chips 显示 (2)，
           两项都 active。所以一个筛选项可以给多个值，逐个点，最后一起校验。
        """
        want = normalize_filters(wants)
        if not want:
            return {"chips": [], "applied": []}

        st = self._wait_bar(drv)
        if not st.get("ok"):
            raise filter_failed(self.name, "筛选条不存在（%s）" % st.get("reason"))

        applied: list[str] = []
        for label, options in want.items():
            g = self._find_group(st.get("groups") or [], label)
            if g is None:
                raise filter_failed(
                    self.name, "找不到筛选项「%s」（当前：%s）"
                    % (label, ", ".join(x.get("placeholder") or "-"
                                        for x in st.get("groups") or [])))
            # 先把所有期望值都对一遍选项表：写错一个就整项报错，
            # 不要「点了一半才发现第 2 个值不存在」，那种半开状态最难收拾。
            opts_all = [o.strip() for o in g.get("options") or []]
            bad = [o for o in options if o not in opts_all]
            if bad:
                raise filter_failed(
                    self.name, "「%s」没有选项 %s（可选：%s）"
                    % (label, "、".join(bad), " / ".join(opts_all[:12])))

            for option in options:
                # 每点一个值都重读一次：Vue 会重渲染下拉，下标必须现算。
                # 不猜「下拉是否已展开」——BOSS 的下拉常驻 DOM 且带尺寸，
                # 任何静态判据都会误判（2026-11-16 实测）。改成「点→立刻校验→
                # 没生效就收起重新展开再点一次」，状态由结果说话。
                for attempt in (1, 2):
                    st = self._filter_state(drv)
                    g = self._find_group(st.get("groups") or [], label)
                    if g is None:
                        raise filter_failed(self.name, "筛选条在设置「%s」时消失了" % label)
                    if option in (g.get("selected") or []):
                        break
                    opts = [o.strip() for o in g.get("options") or []]
                    if option not in opts:
                        raise filter_failed(
                            self.name, "「%s」没有选项「%s」（可选：%s）"
                            % (label, option, " / ".join(opts[:12])))
                    trig = ".c-filter-condition > :nth-child(%d)" % (int(g["i"]) + 1)
                    if human:
                        human.filter_gap()
                    if attempt > 1:
                        # 第二次前先确保是收起状态：点在了展开的下拉上会导致
                        # 后续点击落空，先 Escape 收掉再重开
                        try:
                            drv.press("Escape")
                        except Exception:  # noqa: BLE001
                            pass
                        drv.wait_ms(300)
                    drv.click(trig)
                    drv.wait_ms(500)
                    st = self._filter_state(drv)
                    g = self._find_group(st.get("groups") or [], label) or g
                    opts = [o.strip() for o in g.get("options") or []] or opts
                    if option not in opts:
                        raise filter_failed(
                            self.name, "「%s」展开后没有选项「%s」（可选：%s）"
                            % (label, option, " / ".join(opts[:12])))
                    opt_sel = trig + " .filter-select-dropdown li:nth-child(%d)" % (
                        opts.index(option) + 1)
                    drv.click(opt_sel)
                    if human:
                        human.filter_gap()
                    # 选中态是异步更新的：BOSS 点完要先重新拉结果才把 li 置成
                    # active，固定 sleep 会读到旧状态（2026-11-16 实测：等 1.4s
                    # 仍读到空，再晚一点就有了）。所以轮询到出现为止。
                    if self._wait_selected(drv, label, option, timeout=6.0):
                        break
                else:
                    got = list((self._find_group(
                        self._filter_state(drv).get("groups") or [], label) or {}
                    ).get("selected") or [])
                    raise filter_failed(
                        self.name, "点了两次「%s=%s」仍未选中（当前选中：%s）"
                        % (label, option, " / ".join(got) or "空"))
                applied.append("%s=%s" % (label, option))

        ok, detail = self.verify_filters(drv, want)
        if not ok:
            raise FilterNotApplied("平台 %s 筛选校验失败：%s" % (self.name, detail))
        return {"chips": self.filter_chips(drv), "applied": applied}

    def verify_filters(self, drv, wants: dict) -> tuple[bool, str]:
        """BOSS 的校验必须看 ``li.active``：chips 只有计数，没有具体值。"""
        groups = self._filter_state(drv).get("groups") or []
        missing = []
        for label, options in normalize_filters(wants).items():
            g = self._find_group(groups, label)
            got = list((g or {}).get("selected") or [])
            for option in options:
                if option not in got:
                    missing.append("%s=%s" % (label, option))
        if missing:
            return False, "未生效：" + " / ".join(missing)
        return True, " / ".join(self.filter_chips(drv))

    @staticmethod
    def _find_group(groups: list, label: str):
        """按占位文案或「文本以条件名开头」找条件组。"""
        for g in groups:
            ph = (g.get("placeholder") or "").strip()
            if ph == label or (ph and label in ph):
                return g
        for g in groups:
            if (g.get("text") or "").startswith(label):
                return g
        return None

    # ---------- 风控 ----------

    def page_text(self, drv) -> str:
        """当前页面可见文本，供 guard 查风控关键词。读不到返回空串。"""
        try:
            return str(drv.evaluate(
                "(() => (document.body && document.body.innerText || '').slice(0, 4000))()",
                retries=2).get("value") or "")
        except Exception:  # noqa: BLE001
            return ""

    # ---------- 回执 ----------

    def apply_label(self, state: dict) -> str:
        return str(state.get("chat_label") or "")

    def classify_receipt(self, dialog: str, button_label: str) -> tuple:
        d = (dialog or "").strip()
        b = (button_label or "").strip()

        # 风控优先于一切：命中就要立刻停，不能被「已向」之类文案掩盖
        for pat in RISK_PATTERNS:
            if re.search(pat, d) or re.search(pat, b):
                return "blocked_by_risk", (d or b)[:160]
        for pat in QUOTA_PATTERNS:
            if re.search(pat, d):
                return "blocked_by_quota", d[:160]
        for pat in DELIVERED_PATTERNS:
            if re.search(pat, d):
                return "delivered", d[:160]
        for pat in PLATFORM_BLOCK_PATTERNS:
            if re.search(pat, d):
                return "blocked_by_platform", d[:160]
        if "继续沟通" in b:
            return "already", b
        if not b:
            return "no_button", ""
        if "立即沟通" in b:
            return "unknown", "按钮仍为「立即沟通」，点击未生效"
        return "unknown", d[:160] or b
