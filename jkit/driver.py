"""bsk 驱动 —— 把 CLI 的 context/sync 舞步包起来。

两条硬约束，来自实跑踩坑：
1. 所有页面动作都要带 context，而 context 由 cooperate sync 现取；SPA 渲染期间
   观察会失效（observation_changed），必须重试。
2. 点击「立即沟通」是不可逆动作，**绝不重试**。用 evaluate_once()。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

STALE_REASONS = {
    "observation_changed",
    "cooperation_sync_required",
    "observation_stale",
    "frame_unobservable",
}


class BskError(RuntimeError):
    pass


def find_binary() -> str:
    for key in ("JB_BSK_BIN", "BSK_BIN"):
        v = os.environ.get(key)
        if v and Path(v).exists():
            return v
    found = shutil.which("bsk")
    if found:
        return found
    for cand in (
        Path.home() / ".local" / "bin" / "bsk.exe",
        Path.home() / ".local" / "bin" / "bsk",
        Path("/usr/local/bin/bsk"),
    ):
        if cand.exists():
            return str(cand)
    raise BskError(
        "找不到 bsk。装好后重试，或用 JB_BSK_BIN=/path/to/bsk 指定。\n"
        "安装见 https://github.com/ 的 browser-skill 说明"
    )


class Driver:
    def __init__(self, binary: Optional[str] = None, session: Optional[str] = None,
                 auto_start: bool = True):
        self.binary = binary or find_binary()
        self.session = session
        self.auto_start = auto_start
        # 当前受控标签页。navigate 返回里带 tab_id，早先被丢掉了 —— 结果就是
        # 点击默认打到 Agent Window 的「当时活动标签」，而不是我们导航的那一个：
        # 命令返回成功、页面毫无反应（2026-11-13 实测：文档级捕获 0 事件）。
        self.tab_id: Optional[str] = None
        # 页面请求计数回调：交给 guard 做速率阀。放在 driver 是因为所有导航都经过这里，
        # 漏掉一处就等于速率算不准。
        self.on_request = None

    def _request(self, n: float = 1.0, role: Optional[str] = None) -> None:
        """记一次请求当量。

        2026-11-13 起改加权：搜索全程走页面操作后，一次「点框+填词+回车」如果
        只算 1，速率会被严重低估。权重表在 policy.ACTION_WEIGHT，
        口径统一为「等效整页请求当量/分」，阈值不用改。
        """
        if role is not None:
            try:
                from . import policy
                n = policy.ACTION_WEIGHT.get(role, n)
            except Exception:
                pass
        if callable(self.on_request):
            try:
                self.on_request(n)
            except Exception:
                pass

    # ---------- 底层 ----------

    def _env(self) -> dict:
        env = dict(os.environ)
        env["BSK_AUTO_START"] = "1" if self.auto_start else "0"
        return env

    def _run(self, args: list, timeout: int = 120) -> tuple:
        p = subprocess.run(
            [self.binary] + [a for a in args if a is not None],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=self._env(), timeout=timeout,
        )
        return p.stdout or "", p.stderr or "", p.returncode

    def _json(self, args: list, timeout: int = 60) -> dict:
        out, err, rc = self._run(args + ["--json"], timeout)
        try:
            return json.loads(out)
        except Exception:
            raise BskError("bsk 返回非 JSON（rc=%s）：%s" % (rc, (out or err).strip()[:300]))

    def _tab_args(self) -> list:
        """把当前受控标签页显式传给每一条命令。

        不传的话 bsk 会自己挑「Agent Window 的活动标签」，而那个标签未必是我们
        导航的那个 —— 多开过几个会话之后尤其如此。这是「点击返回 ok 但页面没反应」
        的真正原因，不是选择器问题。
        """
        return ["--tab-id", str(self.tab_id)] if self.tab_id else []

    def _pin_tab(self, payload: dict) -> None:
        tid = (payload or {}).get("tab_id")
        if tid:
            self.tab_id = str(tid)

    # ---------- 环境 ----------

    def version(self) -> str:
        out, err, _ = self._run(["--version"])
        return (out or err).strip().splitlines()[0] if (out or err).strip() else "?"

    def daemon_running(self) -> bool:
        try:
            d = self._json(["status"], timeout=30)
        except BskError:
            return False
        return bool(d.get("daemon") or d.get("ok") or d.get("pid"))

    def browsers(self) -> list:
        """已连接的浏览器实例列表。

        注意：bsk 在 daemon 不可用/未授权时照样 exit 0，但返回的是
        ``{"code":..., "message":...}`` 这种错误对象，不是列表。以前直接
        按列表取下标会 ``KeyError: 0``，把「没有浏览器」伪装成脚本崩溃。
        """
        try:
            d = self._json(["browsers"], timeout=30)
        except BskError:
            return []
        if isinstance(d, list):
            return d
        if isinstance(d, dict):
            for k in ("browsers", "instances", "items", "data"):
                v = d.get(k)
                if isinstance(v, list):
                    return v
        return []

    def pick_browser(self, want: Optional[str] = None) -> Optional[str]:
        """挑一个已连接的浏览器实例。want 可传 instance_id 或 label。"""
        list_ = self.browsers()
        if not list_:
            return None
        if want:
            for b in list_:
                if want in (b.get("instance_id"), b.get("label")):
                    return b.get("instance_id")
            raise BskError("浏览器未连接：%s。当前可用：%s"
                           % (want, ", ".join(b.get("instance_id", "?") for b in list_)))
        return list_[0].get("instance_id")

    # ---------- 会话 ----------

    def session_start(self, name: str, project: str = "jkit",
                      agent_name: str = "job-kit",
                      browser: Optional[str] = None) -> str:
        args = ["session", "start", "--name", name, "--project", project,
                "--agent-name", agent_name]
        if browser:
            args += ["--browser", browser]
        d = self._json(args, timeout=120)
        sid = d.get("session_id")
        if not sid:
            raise BskError("session start 未返回 session_id：%s" % d)
        self.session = sid
        return sid

    def session_exists(self, sid: Optional[str] = None) -> bool:
        """复用会话前必须验活：client 侧的 id 可能已被前一个命令收尾时停掉。"""
        sid = sid or self.session
        if not sid:
            return False
        try:
            d = self._json(["session", "list"], timeout=30)
        except BskError:
            return False
        items = d if isinstance(d, list) else (d.get("sessions") or [])
        for s in items:
            if isinstance(s, dict) and s.get("session_id") == sid:
                return s.get("status", "active") not in ("stopped", "closed")
            if isinstance(s, str) and s == sid:
                return True
        return False

    def session_stop(self, sid: Optional[str] = None) -> None:
        sid = sid or self.session
        if sid:
            self._run(["session", "stop", sid], timeout=30)

    # ---------- 动作 ----------

    def open_tab(self, url: str = "", sid: Optional[str] = None) -> str:
        """开一个本会话专属的新标签页并把它钉住，返回 tab_id。

        为什么不直接 `navigate`：不带 `--tab-id` 时它操作的是「Agent Window 的
        活动标签」，而活动标签可能是用户正在看的任何页面 —— 我们一 navigate 就把
        人家那页冲掉了（2026-11-13 踩过：跑一轮把用户的对话页导航成了 BOSS）。
        新开一个标签，谁都不打扰，而且 tab_id 从一开始就确定，后续 sync / step
        全部指同一条，不会再出现「上下文和动作不是同一个标签」。
        """
        sid = sid or self.session
        args = ["tab", "create", "--session", sid]
        if url:
            args += ["--url", url]
        try:
            d = self._json(args, timeout=120)
        except BskError:
            return ""
        tid = d.get("tab_id") or (d.get("tab") or {}).get("tab_id")
        if tid:
            self.tab_id = str(tid)
        return str(tid or "")

    def sync(self, sid: Optional[str] = None) -> tuple:
        """取协作上下文。空白页下会失败，返回 (None, None)。

        **必须带 `--tab-id`**（2026-11-13 实跑踩到）：不带的话 bsk 拿的是「当前
        活动标签」的上下文，而 ``step`` 是按我们自己的 tab 派发的。两者不是同一个
        标签时，动作会被判成 ``permission_denied / control context no longer valid``
        —— 表现是「点了卡片返回成功、右侧面板纹丝不动」，而错误被 ``_step`` 吞掉，
        上层只能看到「面板没换过来」，极难定位。用户在浏览器里切一下标签就能复现。
        """
        sid = sid or self.session
        for _ in range(2):
            try:
                d = self._json(["cooperate", "sync", "--session", sid]
                               + self._tab_args(), timeout=60)
            except BskError:
                time.sleep(0.4)
                continue
            if d.get("context_id"):
                if not self.tab_id:
                    self._pin_tab(d)
                return d["context_id"], d.get("observation_id")
            time.sleep(0.4)
        return None, None

    def evaluate(self, js: str, retries: int = 3, sid: Optional[str] = None) -> dict:
        """执行页面 JS。快照失效类错误自动重试，其余错误立即返回。"""
        sid = sid or self.session
        last = {"ok": False, "error": "未执行"}
        for i in range(max(1, retries)):
            ctx, obs = self.sync(sid)
            if not ctx:
                last = {"ok": False, "error": "cooperate sync 未返回 context"}
                time.sleep(0.6)
                continue
            try:
                d = self._json(["evaluate", js, "--session", sid] + self._tab_args()
                               + ["--context", ctx, "--observation", obs], timeout=120)
            except BskError as e:
                last = {"ok": False, "error": str(e)}
                time.sleep(0.6)
                continue
            if d.get("ok"):
                self._request(role="evaluate")
                return d
            reason = ((d.get("data") or {}).get("reason") or "")
            code = d.get("code") or ""
            last = d
            # 0.5.0 起「观测过期」报的是 permission_denied + 一句话，reason 字段
            # 未必落在 STALE_REASONS 里。这类抖动重试一次就好，不该整轮失败；
            # 真正的权限问题重试几次也一样失败，所以放宽是安全的。
            retryable = (reason in STALE_REASONS
                         or code == "permission_denied"
                         or "observation" in json.dumps(d, ensure_ascii=False))
            if not retryable:
                return d
            # 短退避：观测失效是「sync 与 evaluate 之间页面变了」，重新取一次
            # 观测基本就能成。等太久反而更容易撞上下一次页面刷新。
            time.sleep(0.3)
        return last

    def evaluate_once(self, js: str, sid: Optional[str] = None) -> dict:
        """不可逆动作用：只跑一次，不重试，避免重复投递。"""
        return self.evaluate(js, retries=1, sid=sid)

    def navigate(self, url: str, sid: Optional[str] = None,
                 retry_bare: bool = False) -> bool:
        """导航。

        空白页(about:)下 sync 会失败，需要裸导航引导一次——但**只有预热时**才允许
        retry_bare。以前失败就立刻裸补一次，等于一次点击发两次页面请求、零间隔，
        这是今天被判定为自动化的典型特征之一。重试前先停 2-3 秒。
        """
        sid = sid or self.session
        ctx, obs = self.sync(sid)
        args = ["navigate", url, "--session", sid] + self._tab_args()
        if ctx:
            args += ["--context", ctx, "--observation", obs]
        out, err, rc = self._run(args + ["--json"], timeout=120)
        self._request(role="navigate")
        if rc == 0:
            try:
                self._pin_tab(json.loads(out))
            except Exception:
                pass
            return True
        if not retry_bare:
            return False
        time.sleep(2.0 + (time.monotonic() % 1.0))
        out2, err2, rc2 = self._run(["navigate", url, "--session", sid, "--json"], 120)
        self._request(role="navigate")
        if rc2 == 0:
            try:
                self._pin_tab(json.loads(out2))
            except Exception:
                pass
        return rc2 == 0

    def wait_element(self, selector: str, timeout: str = "15s",
                     sid: Optional[str] = None) -> dict:
        sid = sid or self.session
        ctx, obs = self.sync(sid)
        args = ["wait-for-element", selector, "--session", sid] + self._tab_args() + [
            "--state", "visible", "--timeout", timeout]
        if ctx:
            args += ["--context", ctx, "--observation", obs]
        try:
            d = self._json(args, timeout=60)
            self._request(role="wait_element")
            return d
        except BskError as e:
            return {"satisfied": False, "error": str(e)}

    def screenshot(self, path: str, sid: Optional[str] = None) -> bool:
        sid = sid or self.session
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        out, err, rc = self._run(["screenshot", "--out", path, "--session", sid]
                                 + self._tab_args() + ["--json"], 60)
        return rc == 0

    # ---------- 原生输入动作 ----------
    #
    # 这几个走 bsk 的原生命中，事件 isTrusted=true，比页面里 element.click() /
    # scrollBy() 更接近真人。失败一律返回 False，由调用方决定降级，不抛错——
    # 拟人动作不该把整轮投递搞挂。

    def _step(self, action: str, params: dict, timeout: int = 60,
              until_visible: Optional[str] = None) -> dict:
        """所有输入动作统一走 `step` 通道。

        为什么不用 `bsk click` / `bsk hover` 这些原子命令：2026-11-13 实测
        （0.4.2 与 0.5.0 都一样）它们会解析出元素、算出坐标、返回成功，但页面
        一个事件都收不到 —— 在 document 上装 capture 监听，记录到 0 条。同一时刻
        `bsk step --action click` 能把右侧面板切过去，`:hover` 也真的挂上了。
        差别在 control policy：`step` 走的是带 target 策略的那条通道。

        所以：原子命令一律不用，动作只走这里。step 语义是「一次动作，永不重试」，
        正好符合投递这种不可逆动作的要求。
        """
        sid = self.session
        ctx, obs = self.sync(sid)
        args = ["step", "--session", sid, "--action", action,
                "--params", json.dumps(params, ensure_ascii=False)]
        args += self._tab_args()
        if until_visible:
            args += ["--until-visible", until_visible]
        if ctx:
            args += ["--context", ctx, "--observation", obs]
        try:
            return self._json(args, timeout)
        except BskError as e:
            return {"ok": False, "error": str(e)[:200]}

    @staticmethod
    def _ok(d: dict) -> bool:
        return bool(d) and d.get("ok") is not False and not d.get("error")

    def hover(self, selector: str, settle: str = "120ms") -> bool:
        ok = self._ok(self._step("hover", {"selector": selector}, 30))
        self._request(role="hover")
        return ok

    def click(self, selector: str) -> bool:
        ok = self._ok(self._step("click", {"selector": selector}, 45))
        self._request(role="click")
        return ok

    def wheel(self, dy: int, selector: Optional[str] = None) -> bool:
        params = {"deltaY": int(dy)}
        if selector:
            params["selector"] = selector
        ok = self._ok(self._step("wheel", params, 30))
        self._request(role="wheel")
        return ok

    def press(self, key: str, selector: Optional[str] = None) -> bool:
        params = {"key": key}
        if selector:
            params["selector"] = selector
        ok = self._ok(self._step("press", params, 30))
        self._request(role="press")
        return ok

    def fill(self, selector: str, value: str) -> bool:
        ok = self._ok(self._step("fill", {"selector": selector, "value": value}, 45))
        self._request(role="fill")
        return ok

    def scroll_to(self, selector: str) -> bool:
        """把元素滚进视口。卡在视口外的位置点不到，必须先滚进去。"""
        ok = self._ok(self._step("scroll-to", {"selector": selector}, 30))
        self._request(role="scroll_to")
        return ok

    def wait_ms(self, ms: int) -> bool:
        """bsk 原生等待（浏览器端计时，比 Python sleep 更贴近真实停留）。"""
        out, err, rc = self._run(["wait-ms", "%dms" % int(ms)], 60)
        self._request(role="wait_ms")
        return rc == 0

    def back(self) -> bool:
        """回退一页。走原生命令（不是鼠标动作，不需要 step 通道）。"""
        sid = self.session
        ctx, obs = self.sync(sid)
        args = ["navigate-back", "--session", sid] + self._tab_args() + [
            "--wait-until", "domcontentloaded"]
        if ctx:
            args += ["--context", ctx, "--observation", obs]
        self._request(role="back")
        try:
            return self._ok(self._json(args, 45))
        except BskError:
            return False

    # ---------- 滚动 ----------
    #
    # 实测（2026-11-13）：bsk wheel 返回 ok，但 BOSS 搜索页 scrollY 纹丝不动 ——
    # 原生 wheel 的落点在视口中心，那里通常是固定头部或不可滚区域。只调 wheel
    # 会造成「看起来在拟人、实际页面没动」，比不滚更糟。所以这里先试 wheel，
    # 一旦确认不动就永久退回 JS scrollBy（一次判定，不反复试探）。

    _wheel_ok = None          # None=未判定 / True=可用 / False=不可用

    def _scroll_y(self) -> int:
        try:
            return int(eval_json(self, "JSON.stringify({y: window.scrollY})").get("y") or 0)
        except Exception:
            return 0

    def _js_scroll(self, dy: int) -> bool:
        js = ("(()=>{const dy=%d,y0=window.scrollY;window.scrollBy(0,dy);"
              "let y1=window.scrollY,inner=null;"
              "if(Math.abs(y1-y0)<10){"
              "const c=[...document.querySelectorAll('div,main,section')].filter(n=>{"
              "const s=getComputedStyle(n);"
              "return (s.overflowY==='auto'||s.overflowY==='scroll')&&n.scrollHeight>n.clientHeight+80;});"
              "const el=c.sort((a,b)=>b.clientHeight-a.clientHeight)[0];"
              "if(el){el.scrollBy(0,dy);inner=el.scrollTop;}}"
              "return JSON.stringify({y0:y0,y1:window.scrollY,inner:inner});})()" % int(dy))
        try:
            d = eval_json(self, js)
        except Exception:
            return False
        moved = abs(int(d.get("y1") or 0) - int(d.get("y0") or 0)) >= 10 or d.get("inner")
        return bool(moved)

    def scroll(self, dy: int) -> bool:
        """滚一屏。返回是否真的动了。"""
        if type(self)._wheel_ok is not False:
            before = self._scroll_y()
            self.wheel(dy)
            if self._scroll_y() != before:
                type(self)._wheel_ok = True
                return True
            type(self)._wheel_ok = False
        return self._js_scroll(dy)


def eval_json(driver: Driver, js: str, retries: int = 5) -> dict:
    """执行返回 JSON 字符串的 JS，并解析成 dict。

    默认给 5 次而不是 3 次：SPA 页面上 sync 与 evaluate 之间页面随时可能重渲染，
    观测作废是常态而非异常。多给两次几乎不增加耗时（成功一次就返回），
    但能避免「一次抖动打断整轮」。
    """
    r = driver.evaluate(js, retries=retries)
    if not r.get("ok"):
        raise BskError("页面脚本执行失败：%s" % json.dumps(r, ensure_ascii=False)[:300])
    v = r.get("value")
    if isinstance(v, str):
        return json.loads(v)
    return v or {}
