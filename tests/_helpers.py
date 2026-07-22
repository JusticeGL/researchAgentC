"""测试辅助。

MOABB 的 import 在某些环境下会段错误（SIGSEGV，二进制 ABI 不兼容），
直接 `pytest.importorskip("moabb")` 会让崩溃杀死整个 pytest 进程而非干净 skip。
因此在**子进程**里探测可导入性：非零退出（含段错误）即视为不可用并 skip。
"""
import functools
import subprocess
import sys

import pytest


@functools.lru_cache(maxsize=1)
def moabb_importable() -> bool:
    try:
        r = subprocess.run(
            [sys.executable, "-c", "import moabb, mne"],
            capture_output=True, timeout=180,
        )
        return r.returncode == 0
    except Exception:
        return False


def require_moabb():
    if not moabb_importable():
        pytest.skip("moabb/mne 不可用（未安装或导入崩溃，如 SIGSEGV）")
