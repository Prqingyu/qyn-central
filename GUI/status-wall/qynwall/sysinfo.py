"""平台差异的**唯一**收敛点。

Windows 开发机 与 Linux 中控机 的差异只在这里处理；
其余模块一律不出现 sys.platform / os.name 判断。

⚠️ 注意本模块不能叫 platform —— 会遮蔽标准库的 platform 模块。
"""

from __future__ import annotations

import os
import sys

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

PLATFORM_LABEL = "windows" if IS_WINDOWS else ("linux" if IS_LINUX else sys.platform)


def root_disk() -> str:
    """系统盘挂载点。Windows 是 C:\\，Linux 是 /。"""
    if IS_WINDOWS:
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


def cpu_load1() -> float | None:
    """1 分钟平均负载。

    Windows 没有 load average 这个概念，os.getloadavg() 会抛 OSError。
    拿不到就返回 None —— 页面上显示为"—"，不是 0（0 是"负载为零"，含义不同）。
    """
    try:
        return round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        return None
