"""开跑前的三项自检：浏览器 daemon、已连接浏览器、条件文件。

为什么单独立一层：这三件事以前是「每次跑之前手工敲一遍」——先看 daemon
在不在、再看浏览器连没连、再逐个试条件能不能加载，试完才敢投。现在全部
收敛成一次调用，失败时给**可执行的下一步**，而不是抛栈。

判定标准只有一条：能跑就跑，跑不了就说清楚缺什么。
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from .conditions_loader import load_conditions
from .driver import Driver


class PreflightError(RuntimeError):
    """自检没过。消息里必须包含「怎么办」，不能只说「失败了」。"""


def ensure_daemon(drv: Driver, wait_sec: float = 40.0, log_path: Optional[Path] = None) -> bool:
    """确保 bsk daemon 在跑。不在就自己起，并等它就绪。

    返回 ``True`` 表示「这次是我们拉起来的」（用于日志），``False`` 表示本来就在跑。
    """
    if drv.daemon_running():
        return False

    # 前台模式会一直占着终端，所以丢到独立进程里去；日志留在 workbuddy 目录下备查。
    log = log_path or Path.home() / ".bsk" / "daemon-boot.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict = {"stdout": open(log, "a", encoding="utf-8"),
                        "stderr": subprocess.STDOUT}
        if os.name == "nt":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP：父进程退出不带走它
            kwargs["creationflags"] = 0x00000008 | 0x00000200
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([drv.binary, "daemon", "start", "--foreground"], **kwargs)
    except Exception as e:  # noqa: BLE001
        raise PreflightError(
            "启动浏览器 daemon 失败：%s\n"
            "  手动兜底：另开一个终端跑 `bsk daemon start --foreground` 并保持不关，"
            "再重跑本命令。" % e)

    t0 = time.time()
    while time.time() - t0 <= wait_sec:
        if drv.daemon_running():
            return True
        time.sleep(1.0)
    raise PreflightError(
        "等了 %.0f 秒 daemon 仍没起来。\n"
        "  1) 手动跑 `bsk daemon start --foreground` 看报错\n"
        "  2) 日志：%s" % (wait_sec, log))


def ensure_browser(drv: Driver, wait_sec: float = 20.0,
                   want: Optional[str] = None) -> Optional[str]:
    """确保至少有一个浏览器实例连着。返回 instance_id（没指定就用第一个）。

    连不上时给出三句话的排查路径 —— 这是过去最常卡住的一步。
    """
    t0 = time.time()
    while True:
        try:
            bid = drv.pick_browser(want)
        except Exception as e:  # noqa: BLE001
            raise PreflightError("指定的浏览器没连上：%s" % e)
        if isinstance(bid, dict):
            bid = bid.get("instance_id")
        if bid:
            return str(bid)
        if time.time() - t0 > wait_sec:
            break
        time.sleep(1.5)

    raise PreflightError(
        "没有已连接的浏览器。检查三件事：\n"
        "  1) 目标浏览器（Chrome / Edge）已打开\n"
        "  2) 已装 BrowserSkill 扩展且显示已连接\n"
        "  3) 该浏览器里已登录招聘站（BOSS / 智联）\n"
        "  都 OK 还是连不上：`bsk browsers --json` 看实际返回。")


def check_conditions(src: str | Path, ws: Path) -> list:
    """逐个条件做「能不能加载 + 关键字段齐不齐」的校验。

    只报错不修 —— 条件是可投性的事实来源，这里静默兜底等于把问题推到投递中段，
    那时候已经花掉几分钟了。
    """
    source = Path(src)
    if not source.is_absolute():
        source = ws / source
    files = _condition_files(source)
    if not files:
        raise PreflightError("没有条件文件：%s\n  期望目录里有 *.json" % src)

    out = []
    bad: list[str] = []
    for f in files:
        try:
            cond = load_conditions(f, root=ws)
            cond.validate()
        except Exception as e:  # noqa: BLE001
            bad.append("  ✗ %s：%s" % (f.name, e))
            continue
        out.append((f, cond))
    if bad:
        raise PreflightError("条件文件有问题：\n" + "\n".join(bad))
    return out


def _condition_files(src: str | Path) -> list[Path]:
    p = Path(src)
    if p.is_dir():
        return sorted(x for x in p.glob("*.json") if x.is_file())
    return [p] if p.exists() else []
