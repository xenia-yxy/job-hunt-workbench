"""右侧详情面板编排 —— 全程停在列表页，不导航、不返回。

真人在这类站上的动作序列（BOSS / 智联同构，2026-11-13 实拍确认）：

    点左侧卡片 → 右侧面板换成这条的详情 → 看完 → 点右上「立即沟通/立即投递」
    → 弹窗「已向对方发送…」→ 点「留在此页」→ 接着往下看下一张

两条硬约束：

1. **动作必须走原生输入**。2026-11-13 实测：``bsk click`` 这类原子命令会算出坐标、
   返回成功，但页面收不到任何事件（document 级捕获 0 条）；``bsk step`` 通道
   才真的把事件送到页面。所以所有动作都经 ``driver._step``，并且**点击后要验证
   效果**（面板真的换了没），不拿返回值当结果。
2. **点卡片失败最多补两次，点投递一次都不补**。切面板无副作用，可以补；
   投递是不可逆动作，重试就可能投两遍。

平台差异全部收在 adapter：卡片怎么定位、按钮叫什么、回执怎么读。
"""
from __future__ import annotations

import json
import time
from typing import Optional

from .errors import PanelMissing, PanelStalled


def _poll_wait(human, sec: float = 0.05) -> None:
    """轮询之间的间隔。

    有 human 就按人的节奏等；但 fast 档（单测 / 干跑）里 ``poll_gap()`` 是空转，
    直接用它会变成 CPU 忙等 —— 那时退回到极短的固定间隔。
    """
    if human is not None and not getattr(human, "fast", False):
        human.poll_gap()
    else:
        time.sleep(sec)


# 弹窗读取 + 给「留在此页」打标记。
#
# 为什么要打标记再点：这个按钮没有稳定 class，只能按文案找；而按文案找元素必须
# 走页面查询，点了又必须走原生点击。中间用临时 id 把两次操作接起来，注入的只是
# 一个 id 属性，不伪造任何点击。
DIALOG_JS = r"""(() => {
  const vis = e => { const r = e.getBoundingClientRect(); return !!(r.width && r.height); };
  const nodes = [...document.querySelectorAll(
    '.deliver-greeting-modal,.dialog-wrap,.dialog-container,.greet-boss-dialog,.boss-dialog,.ui-dialog,[class*="dialog"],[class*="modal"]'
  )].filter(vis).filter(n => (n.innerText || '').trim().length > 4);
  nodes.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
  const dlg = nodes[0] || null;
  let stay = null, label = null;
  if (dlg) {
    // 按钮实际文案是「留在此页继续沟通」，外面还套着 div / span。
    // 取**面积最小的可点元素**，否则标记会打到外层容器上，点了没反应（实测踩过）。
    const cands = [...dlg.querySelectorAll('a,button,span,div')].filter(e => {
      if (!vis(e)) return false;
      const t = (e.innerText || '').trim();
      return t && t.length <= 20 && /留在此页|留在本页/.test(t);
    });
    cands.sort((a, b) => {
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      const pa = ra.width * ra.height, pb = rb.width * rb.height;
      const ta = /^(A|BUTTON)$/.test(a.tagName) ? 0 : 1;
      const tb = /^(A|BUTTON)$/.test(b.tagName) ? 0 : 1;
      return (ta - tb) || (pa - pb);
    });
    const b = cands[0];
    if (b) {
      b.id = '__jkit_stay';
      stay = true;
      label = (b.innerText || '').trim();
    }
  }
  return JSON.stringify({
    dialog: dlg ? (dlg.innerText || '').trim().slice(0, 600) : null,
    stay_ready: stay,
    stay_label: label,
    has_dialog: !!dlg
  });
})()"""


class Panel:
    """列表页 + 右侧面板的编排器。平台差异全部走 adapter。"""

    def __init__(self, drv, adapter, human=None, guard=None):
        self.drv = drv
        self.adapter = adapter
        self.human = human
        self.guard = guard
        self.stats = {"native_clicks": 0, "click_retries": 0, "panel_reads": 0}
        #: 最近一次读到的面板状态，失败时拼进报错，省得再去翻截图猜
        self._last_panel: dict = {}

    # ---------- 读 ----------

    def state(self) -> dict:
        """读一次面板。字段见各平台 ``scripts/panel.js``。"""
        self.stats["panel_reads"] += 1
        try:
            d = self.drv.evaluate(self.adapter.script("panel"), retries=3)
            d = json.loads(d.get("value") or "{}")
        except Exception as e:  # noqa: BLE001
            raise PanelMissing("面板读取失败：%s" % str(e)[:160])
        if not d.get("ok"):
            raise PanelMissing("面板不存在：%s" % (d.get("reason") or "?"))
        return d

    def read_dialog(self) -> dict:
        try:
            d = self.drv.evaluate(DIALOG_JS, retries=2)
            return json.loads(d.get("value") or "{}")
        except Exception:  # noqa: BLE001
            return {"has_dialog": False, "dialog": None, "stay_ready": False}

    # ---------- 点卡片 ----------

    def open_card(self, idx: int, expect_id: str = "", timeout: float = 6.0) -> dict:
        """点第 idx 张卡并把面板等到它。返回切换后的面板状态。

        三次机会：点标题 → 点整卡 → 再点标题。全失败就抛错，不再多试 ——
        同一张卡反复点本身就是异常行为。

        为什么每次都重新打标记：列表会随滚动/换词重排，提前算好的选择器会指到
        别的卡上。标记是当次现打的，天然跟得上。
        """
        last = None
        seen = ""
        clicked = []
        for attempt in range(3):
            if attempt:
                self.stats["click_retries"] += 1
            if self.human:
                self.human.pause("before_click")
            # 不打 hover：bsk 的 hover 每次要 5.7 秒（它自己等 hover 态稳定），
            # 100 条就是十分钟白等；悬停只是观感，点击才是真动作。
            marked = self.adapter.mark_card(self.drv, idx)
            targets = self.adapter.card_click_targets(marked)
            sel = targets[min(attempt, len(targets) - 1)]
            res = self.drv.click(sel)
            clicked.append("%s→%s" % (sel, "ok" if res else "fail"))
            if res:
                self.stats["native_clicks"] += 1
            got, saw_panel = self._wait_panel(idx, expect_id, timeout)
            if got is not None:
                return got
            if not saw_panel:
                raise PanelMissing("右侧详情面板不存在（点了 %s）" % sel)
            last = sel
            seen = self._last_panel
        raise PanelStalled(
            "点了 3 次卡片，面板还是没换到第 %d 张（点：%s；面板停在 idx=%s id=%s）"
            % (int(idx) + 1, " / ".join(clicked), seen.get("active_idx"),
               (seen.get("active_id") or "")[:12] or "-"))

    def _wait_panel(self, idx: int, expect_id: str = "",
                    timeout: float = 6.0):
        """等面板切到第 idx 张。返回 (状态 | None, 是否至少读到过一次面板)。

        判据优先用「岗位 id」，其次用「当前卡下标」：id 由卡片自己的链接反查，
        列表重排也认得出；标题会截断、同名岗位也多，不能比。
        """
        t0 = time.time()
        saw = False
        misses = 0
        self._last_panel = {}
        while time.time() - t0 <= timeout:
            try:
                s = self.state()
            except PanelMissing:
                s = {}
            if s:
                saw, misses = True, 0
                self._last_panel = s
                aid = str(s.get("active_id") or "")
                if expect_id and aid:
                    same = (aid == expect_id)
                else:
                    same = int(s.get("active_idx", -1)) == int(idx)
                if same:
                    return s, True
            else:
                # 面板连续读不到：如果**从头到尾**都没读到过，那是页面级故障，
                # 早点交回去别空耗 6 秒；但读到过再读不到，只是面板正在重渲染
                # （点卡后面板容器会先被移除再挂回来），必须等满超时。
                misses += 1
                if misses >= 3 and not saw:
                    break
            _poll_wait(self.human)
        return None, saw

    # ---------- 投递 ----------

    def apply(self) -> dict:
        """点投递按钮并读回执。**一次，不重试。**

        返回 {"outcome": delivered|already|blocked_*|unknown|no_button, ...}
        """
        before = self.state()
        label = self.adapter.apply_label(before)
        st = self.adapter.apply_state(label)
        if st == "none":
            return {"outcome": "no_button", "evidence": "面板里没有可点的投递按钮"}
        if st == "already":
            return {"outcome": "already", "evidence": "按钮为「%s」" % label}

        btn = self.adapter.apply_wait_selector
        if not btn:
            return {"outcome": "no_button", "evidence": "平台没声明 apply_wait_selector"}
        if self.human:
            self.human.pause("before_click")
        self.drv.scroll_to(btn)
        self.drv.click(btn)
        self.stats["native_clicks"] += 1

        dlg = self._wait_dialog()
        after_label = ""
        try:
            after_label = self.adapter.apply_label(self.state())
        except PanelMissing:
            pass

        outcome, evidence = self.adapter.classify_receipt(
            dlg.get("dialog"), after_label or label)
        if outcome == "unknown" and dlg.get("dialog"):
            evidence = dlg["dialog"][:160]
        return {"outcome": outcome, "evidence": evidence,
                "dialog": dlg.get("dialog"), "label": after_label or label,
                "stay_ready": dlg.get("stay_ready", False),
                "stay_label": dlg.get("stay_label")}

    def _wait_dialog(self, timeout: float = 2.4) -> dict:
        t0 = time.time()
        last = {}
        while time.time() - t0 <= timeout:
            last = self.read_dialog()
            if last.get("has_dialog"):
                return last
            _poll_wait(self.human, 0.05)
        return last or {"has_dialog": False}

    def stay_here(self, dlg: Optional[dict] = None) -> str:
        """点掉弹窗上的「留在此页」。

        不点掉的话遮罩一直盖着页面，下一张卡根本点不动 —— 这是旧流程里藏着的 bug
        （以前投完直接 navigate 走，弹窗随页面销毁，所以没暴露）。
        """
        info = dlg if dlg is not None else self.read_dialog()
        if not info.get("has_dialog"):
            return "no_dialog"
        if info.get("stay_ready"):
            ok = self.drv.click("#__jkit_stay")
            if self.human:
                self.human.pause("after_apply")
            # 弹窗有淡出动画，点完立刻读还会看到它。给 1.5 秒等它消失。
            if self._wait_dialog_gone(1.5):
                return "stayed" if ok else "stayed_unverified"
        # 没有「留在此页」按钮就按 Escape 关掉，一样能继续
        self.drv.press("Escape")
        if self.human:
            self.human.pause("after_apply")
        return "escape" if self._wait_dialog_gone(1.2) else "close_failed"

    def _wait_dialog_gone(self, timeout: float = 1.5) -> bool:
        t0 = time.time()
        while time.time() - t0 <= timeout:
            if not self.read_dialog().get("has_dialog"):
                return True
            _poll_wait(self.human, 0.05)
        return False
