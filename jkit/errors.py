"""异常族 —— 一处定义，CLI 一处翻译成退出码。

原则：**失败就停，不兜底**。选择器失效、筛选没设上、列表没刷新，全都抛出去，
绝不退回拼 URL 或「当作成功」。兜底会把问题藏起来，下一轮再犯一次。
"""
from __future__ import annotations


class JkitError(RuntimeError):
    """所有可预期的失败。子类只改 code / hint，不额外带状态。"""

    code = 10
    hint = ""

    def __init__(self, msg: str, hint: str = "", **info):
        self.info = info
        super().__init__(msg)
        if hint:
            self.hint = hint


class SelectorMissing(JkitError):
    """页面选择器缺失、不唯一，或平台没提供这一项。"""

    code = 10
    hint = "页面可能改版：跑 `python -m jkit platforms --verify` 看漂移"


class FilterNotApplied(JkitError):
    """平台原生筛选没设上。必须在读卡之前停下 —— 否则是在全量池里逐条看。"""

    code = 11
    hint = "筛选没生效：确认条件里的筛选项文案与页面一致（`jkit platforms --chips` 可读当前值）"


class ResultStalled(JkitError):
    """换词 / 滚动 / 回列表后，列表没刷新。"""

    code = 12
    hint = "看 runs/<run_id>/evidence 下的截图与 failures.jsonl"


class SearchInputRejected(JkitError):
    """关键词没进到搜索框。"""

    code = 15
    hint = "受控输入拦截了 fill：改用键盘输入，或检查 search.input 选择器"


class LoginRequired(JkitError):
    """登录态失效。"""

    code = 14
    hint = "在浏览器里重新扫码登录后再跑"


class RiskBlocked(JkitError):
    """命中风控页 / 验证码。"""

    code = 13
    hint = "本轮立即停止，按提示的解封时间之后再动；不要重试"


class PanelMissing(JkitError):
    """右侧详情面板不存在（页面级故障，不是「还没切过来」）。"""

    code = 16
    hint = "跑 `jkit platforms --verify` 确认 detail.panel 选择器"


class PanelStalled(JkitError):
    """点了卡片但面板没换到目标那条。"""

    code = 17
    hint = "确认卡片选择器与 active 判据还有效（adapter.card_sel / panel 的 active_idx）"


class ApplyBlocked(JkitError):
    """投递被拦（风控 / 配额 / 平台限制）。不可逆动作，不重试。"""

    code = 18
    hint = "本轮停止；配额类次日再跑，风控类按提示时间等待"


class ConditionError(ValueError):
    """条件不合法（缺城市、缺搜索词、等级写错）。"""
