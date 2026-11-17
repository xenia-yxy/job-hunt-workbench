"""搜索链路的页面操作编排（多平台通用）。

2026-11-13 事故复盘后的改造：以前每个搜索词、每一页都 ``navigate`` 一次拼好的
URL。服务端看到的是「从 A 公司详情页直接跳到换了新关键词的结果页」，连续几十次，
而且每次都是整页加载（文档 + JS bundle + 首屏接口）。真人不是这么用的：他在搜索框里
改词、点筛选、往下滚，整轮只整页加载一次。

**顺序是硬约束**：

    land()            整页加载 1 次（只带城市）
    apply_filters()   点开平台原生筛选，逐个点选项，回读校验 —— 必须在读卡之前
    search(q)         点搜索框 → 填词 → 回读校验 → 回车
    load_more()       往下滚，等卡片数增长

不先筛选就等于在全量结果里逐条开面板：慢、且更容易触发风控。所以筛选失败一律
抛错停下，不允许「筛不上也继续跑」。

设计原则：

1. **不兜底**。选择器失效、筛选没设上、列表没刷新，一律抛错退出，绝不退回拼 URL。
2. **刷新判定不能靠 wait_element**。SPA 换词后旧卡片还在 DOM 里，
   ``wait_element`` 会立刻返回 satisfied，读到的是上一批卡。
3. 所有节奏都走 humanize，这里不出现裸 sleep。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from . import policy
from .errors import (FilterNotApplied, LoginRequired, ResultStalled, RiskBlocked,
                     SearchInputRejected, SelectorMissing)


def _now() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


class SearchFlow:
    """搜索页的页面操作编排器。平台差异全部走 adapter。"""

    def __init__(self, drv, adapter, cond, human=None, guard=None,
                 run_id: str = "", ev_dir: Optional[Path] = None):
        self.drv = drv
        self.adapter = adapter
        self.cond = cond
        self.p = cond.for_platform(adapter.name)
        self.human = human
        self.guard = guard
        self.run_id = run_id
        self.ev_dir = Path(ev_dir) if ev_dir else None
        self.failures = (self.ev_dir / "failures.jsonl") if self.ev_dir else None

        self.full_loads = 0        # 整页加载次数（验收指标：整轮应为 1）
        self.xhr_loads = 0         # 页面内换词/滚动/筛选次数
        self.last_state: dict = {}
        self.landed = False
        self.exhausted = False     # 当前 query 已滚到底
        self.filters: dict = {}    # 实际生效的筛选项

    # ---------- 基础设施 ----------

    def _sel(self, role: str) -> str:
        try:
            s = self.adapter.sel(role)
        except Exception as e:  # noqa: BLE001
            raise SelectorMissing("%s：%s" % (role, e), role=role)
        if not s:
            raise SelectorMissing("%s 为空" % role, role=role)
        return s

    def _script(self, name: str, **repl) -> str:
        js = self.adapter.script(name)
        for k, v in repl.items():
            js = js.replace('"__%s__"' % k.upper(), json.dumps(v))
        return js

    def _eval(self, js: str, retries: int = 5) -> dict:
        """读页面状态。

        两层重试：内层每次重新取观测，外层整体再等 0.8 秒重来一轮。
        实测（2026-11-13，bsk 0.5.0）：页面刚导航完的那一两秒，sync 拿到的观测
        基本立刻作废，报 permission_denied / observation changed。5 次内层重试
        都在那两秒里打完，照样全失败 —— 所以必须让外层等页面安静下来再试。
        """
        last = None
        for _ in range(2):
            try:
                d = self.drv.evaluate(js, retries=retries)
                return json.loads(d.get("value") or "{}")
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(0.8)
        raise ResultStalled("页面读取失败：%s" % str(last)[:160])

    def _log(self, msg: str) -> None:
        print("  · %s" % msg, flush=True)

    def _record_failure(self, kind: str, expect=None, actual=None, shot=None) -> None:
        if not self.failures:
            return
        try:
            self.failures.parent.mkdir(parents=True, exist_ok=True)
            with self.failures.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "at": _now(), "kind": kind,
                    "expect": expect, "actual": actual, "screenshot": shot,
                }, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass

    def _screenshot(self, tag: str) -> str:
        if not self.ev_dir:
            return ""
        path = self.ev_dir / ("%s_%s.png" % (tag, int(time.time())))
        try:
            return str(path) if self.drv.screenshot(str(path)) else ""
        except Exception:  # noqa: BLE001
            return ""

    def _fail(self, exc, tag: str) -> None:
        shot = self._screenshot(tag)
        self._record_failure(tag, exc.info.get("expect"), exc.info.get("actual"), shot)
        raise exc

    # ---------- 页面状态 ----------

    def state(self) -> dict:
        return self._eval(self._script("liststate", input=self.adapter.search_input_selector))

    def _arm(self) -> None:
        """记下列表容器引用，供 state 判断「容器是否被重建」。"""
        sel = self.adapter.card_list_selector
        if not sel:
            return
        try:
            self.drv.evaluate(
                "(()=>{window.__jkitList=document.querySelector(%s);"
                "return JSON.stringify({armed:!!window.__jkitList});})()"
                % json.dumps(sel), retries=1)
        except Exception:  # noqa: BLE001
            pass

    def _input_value(self) -> str:
        return str(self.state().get("input_value") or "")

    # ---------- 落地 ----------

    def land(self) -> dict:
        """整轮唯一一次整页加载：只带城市，关键词为空。

        没有钉住的标签页就**新开一个**（见 ``Driver.open_tab``）：绝不 navigate
        用户正在看的那个标签。已有（``--tab`` 传进来的、或上一轮钉住的）就直接导航它。
        """
        url = self.adapter.landing_url(self.p["city_code"])
        if getattr(self.drv, "tab_id", None):
            if not self.drv.navigate(url, retry_bare=True):
                self._fail(ResultStalled("落地页打不开：%s" % url, expect=url), "land")
        else:
            if not self.drv.open_tab(url):
                self._fail(ResultStalled("开不出受控标签页：%s" % url, expect=url), "land")
            self.drv.wait_ms(900)
        self.full_loads += 1
        if self.human:
            self.human.pause("list_dwell")
        try:
            self.drv.wait_element(self.adapter.card_wait_selector, timeout="15s")
        except Exception:  # noqa: BLE001
            pass
        # 导航刚结束的一两秒内观测必失效，先让页面安静下来再读
        self.drv.wait_ms(600)
        s = self.state()
        if int(s.get("n") or 0) == 0:
            self._check_risk(s)
            self._log("落地页暂无列表，直接靠搜索框输入关键词")
        self._arm()
        self.landed = True
        self.last_state = dict(s)
        return s

    def _check_risk(self, s: dict) -> None:
        if self.guard is None:
            return
        try:
            self.guard.probe(self.drv, self.adapter)
        except Exception as e:  # noqa: BLE001
            if "Risk" in type(e).__name__:
                self._record_failure("risk_page", actual=str(e)[:200],
                                     shot=self._screenshot("risk"))
                raise RiskBlocked(str(e)[:200])
            return

    # ---------- 筛选（先筛后看）----------

    def apply_filters(self) -> dict:
        """把平台原生筛选设成条件里的值，并校验。

        条件是外部给的（``Conditions.filters[platform]``）。没配就跳过，
        但会明确打印「未配置」，避免误以为筛过了。
        """
        wants = self.p.get("filters") or {}
        if not wants:
            self._log("条件里没配 %s 的原生筛选，本轮按平台默认结果跑" % self.adapter.name)
            return {}
        try:
            res = self.adapter.apply_filters(self.drv, wants, human=self.human)
        except FilterNotApplied as e:
            self._fail(e, "filter")
            raise
        self.xhr_loads += 1
        self.filters = dict(wants)
        self._log("筛选已生效：%s" % " / ".join(res.get("applied") or []))
        if self.guard:
            self.guard.note_ok()
        self.last_state = dict(self.state())
        return res

    # ---------- 换词 ----------

    def search(self, q: str) -> dict:
        """在搜索框里改词并回车。整轮只有这一步会换 query。"""
        sel = self.adapter.search_input_selector
        if not sel:
            raise SelectorMissing("search.input", "%s 没声明搜索框" % self.adapter.name)
        prev = self.state()
        self._arm()

        ok = (self.human.type_into(self.drv, sel, q, read_back=self._input_value)
              if self.human else self._plain_type(sel, q))
        if not ok:
            self._fail(SearchInputRejected(
                "关键词没进到搜索框（期望「%s」，实得「%s」）"
                % (q, self._input_value()), expect=q, actual=self._input_value()), "input")

        try:
            self.drv.press("Enter", selector=sel)
        except Exception:  # noqa: BLE001
            pass
        try:
            s = self.wait_new({"q": q}, prev)
        except ResultStalled:
            # 回车没触发搜索时，退一步点「搜索」按钮 —— 仍是页面操作，不是拼 URL
            submit = self.adapter.sel("search.submit") if self._has_sel("search.submit") else ""
            if not submit:
                raise
            self._log("回车没触发搜索，改点搜索按钮")
            self.drv.click(submit)
            s = self.wait_new({"q": q}, prev)

        self.xhr_loads += 1
        self.exhausted = False
        self.last_state = dict(s)
        if self.guard:
            self.guard.note_ok()
            self.guard.note_query(self.run_id or "", q)
        return s

    def _has_sel(self, role: str) -> bool:
        try:
            return bool(self.adapter.sel(role))
        except Exception:  # noqa: BLE001
            return False

    def _plain_type(self, sel: str, q: str) -> bool:
        try:
            self.drv.click(sel)
            self.drv.press("Ctrl+A")
            if not self.drv.fill(sel, q):
                return False
        except Exception:  # noqa: BLE001
            return False
        return True

    # ---------- 滚动加载 ----------

    def load_more(self) -> dict:
        """往下滚，等卡片数增长。滚到底就置 exhausted 并原样返回。

        招聘站的结果都是滚动追加（一次 +15 张左右），没有「下一页」按钮。
        所以「翻一页」就是「再滚一屏」，和真人完全一致。
        """
        if self.exhausted:
            return self.last_state
        prev = self.state()
        n0 = int(prev.get("n") or 0)
        doc_h = int(prev.get("doc_h") or 0)
        y = int(prev.get("scroll_y") or 0)

        need = max(600, doc_h - y - 400)
        step = max(400, min(900, need // policy.SPA_MORE_TRIES))

        for attempt in range(policy.SPA_MORE_TRIES):
            try:
                self.drv.scroll(step + attempt * 120)
            except Exception:  # noqa: BLE001
                pass
            if self.human:
                self.human.pause("page_gap")
            s = self._wait_more(n0)
            if s is not None:
                self.xhr_loads += 1
                self.last_state = dict(s)
                if self.guard:
                    self.guard.note_ok()
                return s
            self._log("滚动 %d/%d 没加载出新卡（当前 %d 张）"
                      % (attempt + 1, policy.SPA_MORE_TRIES, n0))

        self.exhausted = True
        self._log("已滚到底（%d 张卡），本词结束" % n0)
        return self.last_state or prev

    def _wait_more(self, n0: int) -> Optional[dict]:
        deadline = time.time() + policy.SPA_MORE_TIMEOUT
        while True:
            s = self.state()
            if int(s.get("n") or 0) > n0:
                return s
            if time.time() > deadline:
                self._check_risk(s)
                return None
            if self.human:
                self.human.poll_gap()
            else:
                time.sleep(0.25)

    # ---------- 等待新结果 ----------

    def wait_new(self, expect: dict, prev: dict,
                 timeout: float = policy.SPA_WAIT_TIMEOUT) -> dict:
        """等换词后的新结果。

        URL 参数对 + 内容确实变了 + 不在加载中，三条同时成立才算到。
        只看其中一条都会误判 —— 旧卡常驻 DOM 是这里唯一的坑。
        """
        t0 = time.time()
        last = {}
        stable_signature = None
        stable_count = 0
        while True:
            last = self.state()
            loading = bool(last.get("loading"))
            url_ok = all((str(last.get(k) or "").strip() == str(v).strip())
                         for k, v in expect.items())
            # URL 先变、旧卡稍后才更新是正常 SPA 时序，URL 改变不能证明结果刷新。
            changed = bool(last.get("ids_hash")) and (
                last.get("ids_hash") != prev.get("ids_hash"))
            empty_ok = bool(last.get("empty_hint")) and int(last.get("n") or 0) == 0
            signature = (last.get("ids_hash"), last.get("n"), empty_ok)
            if not loading and url_ok and (changed or empty_ok):
                stable_count = stable_count + 1 if signature == stable_signature else 1
                stable_signature = signature
                if stable_count >= 2:
                    return last
            else:
                stable_signature = None
                stable_count = 0
            if time.time() - t0 > timeout:
                self._check_risk(last)
                self._fail(ResultStalled(
                    "等新结果超时 %.0fs：期望 %s，实际 q=%s 卡片=%d"
                    % (timeout, expect, last.get("q"), last.get("n")),
                    expect=expect, actual={k: last.get(k) for k in ("q", "n")}), "stall")
            if self.human:
                self.human.poll_gap()
            else:
                time.sleep(0.3)
