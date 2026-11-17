"""平台注册表。新增平台在这里 import 一次即可，内核不改。"""
from .base import Adapter, all_platforms, get, register

from . import boss        # noqa: F401  已实测（2026-11-13，30 条验收）
from . import zhilian     # noqa: F401  已实测（2026-11-13，10 条实投）

__all__ = ["Adapter", "all_platforms", "get", "register"]
