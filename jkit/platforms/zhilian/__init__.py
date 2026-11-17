"""智联招聘适配器。

页面形态和 BOSS 一致：左列卡片 + 右侧详情面板，当前卡带 ``.job-card--active``。
所以内核的「列表页 + 右侧面板 + 留在此页」链路可以原样复用，这个文件只负责
把智联的 DOM 和文案翻译成内核认得的字段。

所有选择器来自 2026-11-13 的真实运行（华富洋 / 开鸿 / 恩斯迈 / 睿服 等 10 条实投），
不是猜的。要点：

1. **搜索不要拼 kw 参数**：首页搜索按钮是 ``target=_blank``，会新开标签页；
   结果页用 ``input.query-sug__input`` 输入 + 回车，同标签内换词。
2. **城市用落地 URL 的 ``jl`` 参数锁定**（深圳 = 765）：这是入口，不是投递动作；
   真人也是在城市选择器里切，等价。
3. **筛选项名与 BOSS 不同**（2026-11-16 实测）：智联是「学历」「经验」「工作性质」，
   不是 BOSS 的「学历要求」「工作经验」。经验档位是
   ``全部 / 经验不限 / 1年以下 / 1-3年 / 3-5年 / 5-10年 / 10年以上``，
   **没有「无经验」这一项** —— 「1年以下」本身就含 0 年经验的岗位，所以条件里
   给「1年以下」。经验组是**单选**（点一个替换一个），所以只能给一个值；
   BOSS 那边才是多选（可同时选「应届生」+「1年以内」）。
   另外智联 AI/技术支持岗很少标「1 年以下」，**筛选到 0 条不算错误**，
   照实报告后换下一个词。
4. **是否已投只看 ``.job-detail-summary__apply`` 的文案**。旁边的
   ``.job-detail-summary__prechat``（「先聊聊 / 继续沟通」）跟投递状态无关，
   拿它判断会误判。
5. **外包分两种**：``客户公司信息`` 区块 = 真外包（硬否决）；
   卡片上的 ``tag_JD_waipai`` 只是外派标（减分，不否决）。
6. 投递弹窗是 ``.deliver-greeting-modal``，文案「已向对方发送简历和打招呼语」，
   点「留在此页」关掉，否则遮罩挡住下一张卡。
"""
from __future__ import annotations

import json
import re
import urllib.parse

from ..base import Adapter, filter_failed, normalize_filters, register
from ...errors import FilterNotApplied

#: 2026-11-16 实测：``sou.zhaopin.com`` 已是空壳（body 只有 9 个字），
#: 有列表和筛选条的是 ``www.zhaopin.com/jobs/``。用错域名会一路 0 条。
BASE = "https://www.zhaopin.com"

QUOTA_PATTERNS = (r"已达.{0,6}上限", r"投递次数已用完", r"今日投递.{0,4}上限")
DELIVERED_PATTERNS = (r"已向对方发送", r"简历投递成功", r"投递成功", r"已发送简历")
PLATFORM_BLOCK_PATTERNS = (r"不匹配", r"不符合", r"无法投递", r"暂不支持")
RISK_PATTERNS = (
    r"安全验证", r"人机验证", r"请完成验证", r"拖动滑块", r"滑动验证",
    r"操作过于频繁", r"网络环境异常", r"系统检测到异常", r"访问受限", r"captcha",
)

#: 读筛选条：每个触发器的当前文本 + 展开后可见面板里的选项
FILTER_STATE_JS = r"""(() => {
  const txt = e => (e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : '');
  const vis = e => { const r = e.getBoundingClientRect(); return !!(r.width && r.height); };
  const trigs = [...document.querySelectorAll('.filter-select-box__trigger')].filter(vis);
  const panels = [...document.querySelectorAll('.filter-select-box__panel')].filter(vis);
  const items = [];
  for (const p of panels) {
    for (const it of p.querySelectorAll('.filter-select-box__item')) items.push(txt(it));
  }
  const btns = [...document.querySelectorAll('button')]
    .filter(e => vis(e) && /^(确定|确认|完成)$/.test(txt(e)))
    .map(txt);
  return JSON.stringify({
    ok: trigs.length > 0,
    triggers: trigs.map((e, i) => ({i: i, text: txt(e)})),
    items: items,
    confirm: btns.length > 0,
  });
})()"""

#: 按**位置**标记触发器。智联的触发器选中后文本会从「学历」变成「本科」，
#: 条件名消失，再按名字找就找不到了；位置不会变，所以设置阶段一律按下标定位。
MARK_TRIGGER_BY_INDEX_JS = r"""(() => {
  document.querySelectorAll('#__zl_trig').forEach(e => e.removeAttribute('id'));
  const vis = e => { const r = e.getBoundingClientRect(); return !!(r.width && r.height); };
  const trigs = [...document.querySelectorAll('.filter-select-box__trigger')].filter(vis);
  const t = trigs[%d];
  if (!t) return 'none';
  t.id = '__zl_trig';
  t.scrollIntoView({block: 'center'});
  return 'ok';
})()"""

MARK_ITEM_JS = r"""(() => {
  document.querySelectorAll('#__zl_item').forEach(e => e.removeAttribute('id'));
  document.querySelectorAll('#__zl_ok').forEach(e => e.removeAttribute('id'));
  const txt = e => (e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : '');
  const vis = e => { const r = e.getBoundingClientRect(); return !!(r.width && r.height); };
  const want = %s;
  for (const p of [...document.querySelectorAll('.filter-select-box__panel')].filter(vis)) {
    const hit = [...p.querySelectorAll('.filter-select-box__item')]
      .filter(e => txt(e).replace(/\s+/g, '') === want.replace(/\s+/g, ''));
    if (hit.length) {
      hit[0].id = '__zl_item';
      const ok = [...document.querySelectorAll('button')]
        .filter(e => vis(e) && /^(确定|确认|完成)$/.test(txt(e)))[0];
      if (ok) ok.id = '__zl_ok';
      return 'ok';
    }
  }
  return 'none';
})()"""


@register
class Zhilian(Adapter):
    name = "zhilian"
    tested = True
    label = "智联招聘"
    login_url = "https://passport.zhaopin.com/login"
    login_markers = ("请登录", "立即登录", "扫码登录")

    card_wait_selector = ".job-list-panel .job-card"
    card_list_selector = ".job-list-panel .job-card"
    apply_wait_selector = ".job-detail-summary__apply"
    search_input_selector = "input.query-sug__input"

    # ---------- URL ----------

    def landing_url(self, city_code: str) -> str:
        # 落地页带城市码，关键词留空；换词在页面内完成
        return "%s/jobs/?jl=%s" % (BASE, city_code or "765")

    def search_url(self, city_code: str, query: str) -> str:
        return "%s/jobs/?jl=%s&kw=%s" % (
            BASE, city_code or "765", urllib.parse.quote(query))

    # ---------- 卡片 ----------

    def card_click_targets(self, marked: str) -> list[str]:
        # 智联整张卡可点，标题没有独立链接；直接点卡
        return [marked]

    def apply_label(self, state: dict) -> str:
        return str(state.get("apply_label") or "")

    # ---------- 筛选 ----------

    def _filter_state(self, drv) -> dict:
        try:
            d = drv.evaluate(FILTER_STATE_JS, retries=4)
        except Exception as e:  # noqa: BLE001
            raise filter_failed(self.name, "读筛选条失败：%s" % str(e)[:120])
        return json.loads(d.get("value") or "{}")

    def filter_chips(self, drv) -> list[str]:
        return [t.get("text") or "" for t in self._filter_state(drv).get("triggers") or []]

    def _wait_bar(self, drv, tries: int = 6, gap_ms: int = 1200) -> dict:
        """等筛选条渲染出来：落地页刚打开时列表和筛选条都是异步的。"""
        st: dict = {}
        for i in range(tries):
            st = self._filter_state(drv)
            if st.get("ok"):
                return st
            if i + 1 < tries:
                drv.wait_ms(gap_ms)
        return st

    def apply_filters(self, drv, wants: dict, human=None) -> dict:
        """按「条件名 → 选项文案（可多值）」逐项设置。每项设完当场回读触发器文本。

        两条实测约束（2026-11-16）：

        1. 智联的触发器**选中后文本会从「学历」变成「本科」**，条件名消失。
           所以定位一律走「位置下标」，不靠文案 —— 文案只有第一次能用。
        2. 多选浮层点完选项不会自动收起，必须最后点一次「确定」；经验组是
           单选（点一个替换一个），给它多值必然只留最后一个，所以校验会报错。
        """
        want = normalize_filters(wants)
        if not want:
            return {"chips": [], "applied": []}

        st = self._wait_bar(drv)
        if not st.get("ok"):
            raise filter_failed(self.name, "筛选条不存在（页面可能还没加载完）")

        applied: list[str] = []
        for label, options in want.items():
            idx = self._locate(st, label, options)
            if idx is None:
                raise filter_failed(
                    self.name, "找不到筛选项「%s」（当前：%s）"
                    % (label, " / ".join(t.get("text") or "?" for t in st.get("triggers") or [])))

            cur_text = self._text_at(st, idx)
            todo = [o for o in options if o not in cur_text]
            if not todo:
                applied.extend("%s=%s(已生效)" % (label, o) for o in options)
                continue

            if human:
                human.filter_gap()
            if (drv.evaluate(MARK_TRIGGER_BY_INDEX_JS % idx, retries=3).get("value")
                    or "") != "ok":
                raise filter_failed(self.name, "点不开「%s」筛选（下标 %d）" % (label, idx))
            drv.click("#__zl_trig")
            drv.wait_ms(700)

            for option in todo:
                if (drv.evaluate(MARK_ITEM_JS % json.dumps(option), retries=3).get("value")
                        or "") != "ok":
                    raise filter_failed(
                        self.name, "「%s」里没有选项「%s」（可选：%s）"
                        % (label, option,
                           " / ".join(self._filter_state(drv).get("items") or []) or "空"))
                drv.click("#__zl_item")
                drv.wait_ms(420)

            # 多选浮层要点「确定」才提交；单选会自动收起，没有按钮就不点
            st2 = self._filter_state(drv)
            if st2.get("confirm"):
                try:
                    drv.click("#__zl_ok")
                except Exception:  # noqa: BLE001
                    pass
            drv.wait_ms(2200)
            if human:
                human.filter_gap()

            st = self._filter_state(drv)
            idx2 = self._locate(st, label, options)
            got = self._text_at(st, idx2) if idx2 is not None else ""
            miss = [o for o in options if o not in got]
            if miss:
                raise filter_failed(
                    self.name, "点了「%s」但触发器是「%s」，缺 %s"
                    % (label, got or "空", "、".join(miss)))
            applied.extend("%s=%s" % (label, o) for o in options)

        ok, detail = self.verify_filters(drv, want)
        if not ok:
            raise FilterNotApplied("平台 %s 筛选校验失败：%s" % (self.name, detail))
        return {"chips": self.filter_chips(drv), "applied": applied}

    @staticmethod
    def _locate(st: dict, label: str, accepted: list[str] | None = None) -> int | None:
        """找触发器下标。先按条件名找（未选状态），再按已选值精确找（已选状态）。

        智联选中后触发器文本就是值本身（「本科」「1年以下」），条件名不在里面了 ——
        只按名字找会在「设置完第一项之后的第二项」上直接失败。
        """
        trigs = st.get("triggers") or []
        for t in trigs:
            text = (t.get("text") or "").strip()
            if text.startswith(label) or label in text:
                return int(t.get("i", 0))
        vals = {v.strip() for v in (accepted or []) if v and v.strip()}
        if vals:
            for t in trigs:
                if (t.get("text") or "").strip() in vals:
                    return int(t.get("i", 0))
        return None

    @staticmethod
    def _text_at(st: dict, idx: int | None) -> str:
        if idx is None:
            return ""
        for t in st.get("triggers") or []:
            if int(t.get("i", -1)) == int(idx):
                return (t.get("text") or "").strip()
        return ""

    def verify_filters(self, drv, wants: dict) -> tuple[bool, str]:
        """智联的 chips 就是触发器文本，能直接看到具体值，逐值核对即可。"""
        st = self._filter_state(drv)
        missing = []
        for label, options in normalize_filters(wants).items():
            idx = self._locate(st, label, options)
            got = self._text_at(st, idx)
            for option in options:
                if option not in got:
                    missing.append("%s=%s" % (label, option))
        if missing:
            return False, "未生效：" + " / ".join(missing)
        return True, " / ".join(self.filter_chips(drv))

    # ---------- 风控 ----------

    def page_text(self, drv) -> str:
        try:
            return str(drv.evaluate(
                "(() => (document.body && document.body.innerText || '').slice(0, 4000))()",
                retries=2).get("value") or "")
        except Exception:  # noqa: BLE001
            return ""

    # ---------- 回执 ----------

    def classify_receipt(self, dialog: str, button_label: str) -> tuple:
        d = (dialog or "").strip()
        b = (button_label or "").strip()

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
        if "继续沟通" in b or "已投递" in b:
            return "already", b
        if not b:
            return "no_button", ""
        if "立即投递" in b:
            return "unknown", "按钮仍为「立即投递」，点击未生效"
        return "unknown", d[:160] or b
