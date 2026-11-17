"""写死的系统策略 —— 不问用户，也不让条件文件绕过。

为什么单独成一个文件：下面这些不是「用户的筛选偏好」，而是这套工具**提高回复率**
的物理机制。让用户去回答「HR 多久没上线就不投」，等于把产品决策外包给用户，而且
每个人答得都不一样，工具就失去可比性 —— 同一批岗位换个人跑，结果会不同。

区分两类东西：

* ``conditions.py``：**筛什么**（城市、关键词、学历、经验、薪资下限）—— 每轮都能变；
* 这个文件：**怎么跑**（活跃度闸门、节奏、速率阀、预算）—— 写死，条件改不到。

条件文件只能改「投哪些」，改不了「多快、多密、遇到风控怎么办」，这是安全边界。
"""
from __future__ import annotations

# ---------- 回复率机制（写死） ----------

# 门槛取自用户原话：「这周以内或近三天活跃，最低都是这个，在线的话优先」（2026-11-13）。
# 实测 39 条详情页只出现「在线 / 刚刚活跃 / 无标识」三档，3 天档位把「3日内活跃」
# 也纳入（仍然只投近期有动作的 HR），「无标识」照旧按淘汰处理。
HR_ACTIVE_MAX_DAYS = 3

# 「读不到活跃度」按淘汰处理：读不到通常是 HR 长期不上线导致标识不显示，
# 而不是页面 bug——如果是页面改版，doctor 的活跃度自检会先报警。
ACTIVITY_UNKNOWN_IS_DROP = True


# ---------- 准入门槛（2026-11-16 用户明确要求「必须满足」）----------
#
# 用户原话：「必须是要满足要求的，就是 Boss 今日在线。然后就是能回复的，
# 然后是确实能感受到它是真实岗位，然后真实需求的。」
#
# 三个门槛的**可观测性**，实事求是写在这里，不硬造平台没有的信号：
#  1) 今日在线 —— 面板有 .boss-online-tag / .boss-active-time，可硬判：
#     hr_online=True，或 active_days==0（在线/刚刚/今日）才算过。
#  2) 能回复   —— BOSS 岗位页**没有回复率字段**，只能用「今日在线」做代理
#     （在线 = 可即时回，是最强的可观测信号）。不假装能读到回复率。
#  3) 真实需求 —— 面板能读到 JD 全文长度 jd_len；JD 短到只有一句话、
#     又挂着招人，基本是「挂着不招 / 凑数 / 打广告」。用长度 + 外包标记代理。
REQUIRE_TODAY_ONLINE = True

#: JD 全文短于这个字数，按「伪需求」淘汰（120 字连岗位职责都写不完）。
#: 设 0 可关闭这道闸门（保留给「只看标题」的勘察场景）。
MIN_JD_LEN = 120


# ---------- 稳定性机制（写死） ----------

# 连续导航详情页超过这个数，Edge 会「page frame is not responding」，必须换会话
SESSION_RESTART_EVERY = 6

# 配额耗尽后立即停止本轮，不试探、不重试
QUOTA_STOP = True

# 全局去重：跨线路生效，同一 job_id 只投一次
DEDUPE_GLOBAL = True


# ---------- 节奏与安全阀（写死） ----------
#
# 2026-11-13 事故复盘：93 分钟 83 条被限时拦截。真正的问题不是「快」，而是
# 「匀速 + 零页面动作 + 长时段」——11 次/分恒定、详情停 2-4 秒、零滚动零悬停、
# 每 6 条销毁重建一次标签页。下面所有值都是围绕这三类特征写的。
#
# 效率档（fast）的算术：约 220 个候选 × 7.2s ≈ 26 分钟 + 列表页与休息 ≈ 5 分钟
# ≈ 31 分钟投完 100 条，折合 8.4 次页面请求/分，仍在真人「快速翻岗」区间。

PACE_PROFILES = {
    "fast": {
        # 详情页：停留短，但必须有滚动 —— 用户口径「时间可以短，滑动不能少」。
        # 2026-11-13 实测：上述参数跑出 11 次/分，和出事那天同档，所以整体放慢一档，
        # 目标 8-9 次/分；100 条约 18-20 分钟，仍在 30-40 分钟预算内。
        "detail_dwell": (1.6, 3.2),
        "detail_scrolls": (1, 2),
        "list_dwell": (0.8, 1.8),
        "list_scrolls": (1, 2),
        "scroll_px": (300, 600),
        "scroll_gap": (0.18, 0.42),
        "before_click": (0.30, 0.80),
        "after_apply": (1.0, 2.2),
        "item_gap": (1.0, 2.8),
        "page_gap": (0.8, 1.8),
        "query_gap": (20, 45),
        "type_per_char": (0.07, 0.16),
        "filter_gap": (0.6, 1.4),
        "rest_every": 25,
        "rest_for": (12, 30),
        "query_rest_every": 6,
    },
    "safe": {
        "detail_dwell": (6.0, 12.0),
        "detail_scrolls": (2, 3),
        "list_dwell": (2.0, 4.0),
        "list_scrolls": (2, 3),
        "scroll_px": (300, 700),
        "scroll_gap": (0.25, 0.6),
        "before_click": (0.6, 1.6),
        "after_apply": (2.0, 4.5),
        "item_gap": (3.0, 7.0),
        "page_gap": (2.0, 4.0),
        "query_gap": (25, 60),
        "type_per_char": (0.12, 0.28),
        "filter_gap": (1.5, 3.0),
        "rest_every": 10,
        "rest_for": (60, 120),
        "query_rest_every": 4,
    },
}

DEFAULT_PACE = "fast"

# 每轮时间预算（分钟）。到点无论投了多少都收尾——用户口径「保时间，跑多少算多少」
TIME_BUDGET_MIN = 40

# 单账号日投递上限。超过即 preflight 拒绝开跑，避免撞平台 150 条配额
DAILY_LIMIT = 100

# 速率阀：滚动 60 秒窗口内的页面请求数
RATE_SOFT = 10        # 超过就放慢（间隔系数抬到 1.3）
RATE_HARD = 14        # 持续超标即熔断，不再试探

# 连续「列表未出现」多少次判定为软限流（今天就是漏了这一层，硬跑了 20 分钟）
EMPTY_TRIP = 2

# 软限流熔断冷却分钟数
COOLDOWN_MIN = 30

# 命中风控页的冷却分钟数（4-6 小时，按平台给的解封时间近似）
RISK_COOLDOWN_MIN = 360

# ---------- 页面操作（SPA）链路 ----------
#
# 2026-11-13 起搜索改成「整轮只整页加载一次，之后换词/翻页/设筛选都在页面内」。
# 问题：guard 的速率阀只数 navigate/click/back，页面内动作权重不同，不加权会
# 严重低估真实压力。所以统一折算成「等效整页请求当量」，阈值保持不变。
ACTION_WEIGHT = {
    "navigate": 1.0,
    "back": 0.6,
    "click": 0.25,
    "fill": 0.25,
    "press": 0.20,
    "hover": 0.10,
    "wheel": 0.10,
    "scroll_to": 0.10,
    "focus": 0.05,
    "evaluate": 0.05,
    "wait_element": 0.05,
    "wait_ms": 0.02,
}

# 等 SPA 出结果的轮询间隔（秒）；每次轮询本身要 0.3-0.6s，等效约 0.8s/次
SPA_POLL = (0.25, 0.45)

# 换词/翻页后等新列表的超时（秒）。超时先查风控词，再报错退出，不重试
SPA_WAIT_TIMEOUT = 12.0

# 滚动加载：一次滚多少、等多久、重试几次（实测一次滚 ~1500px 追加 15 张卡）
SPA_MORE_TIMEOUT = 6.0
SPA_MORE_TRIES = 3



def pace(name: str = DEFAULT_PACE) -> dict:
    """取节奏档位。未知名字退回默认档，不抛错（配置写错不该让整轮跑不起来）。"""
    return dict(PACE_PROFILES.get(name) or PACE_PROFILES[DEFAULT_PACE])


def activity_gate(days, online: bool, max_days: int = HR_ACTIVE_MAX_DAYS) -> tuple:
    """活跃度闸门。返回 (通过, 理由)。

    这是投递前的第一道确定性判定，先于任何条件规则。
    """
    if online:
        return True, "在线"
    if days is None:
        return (not ACTIVITY_UNKNOWN_IS_DROP), "活跃度读不到"
    if days <= max_days:
        return True, "%d 天内有动作" % days
    return False, "HR %d 天没上线，超过 %d 天门槛" % (days, max_days)
