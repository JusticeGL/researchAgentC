"""冻结实验台。agent 只读；只增不改（见 CLAUDE.md 硬规则）。

不变式 I6：harness 内容变更使实验作废。
  - harness_hash() 对 harness/**/*.py 的内容算 SHA256（排序后合并）
  - 每次 record_run 必须传 harness_hash
  - checker C9 校验全库被引用 run 的 harness_hash 一致且等于当前 harness_hash()

harness_hash() 刻意不 import moabb —— 它必须在任何环境下都能算出，
否则 checker 的 I6 校验会因缺依赖而失效。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List

_HARNESS_DIR = Path(__file__).resolve().parent


def _iter_py_files() -> List[Path]:
    files = []
    for p in sorted(_HARNESS_DIR.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        files.append(p)
    return files


def harness_hash() -> str:
    """对 harness/**/*.py 的内容算 SHA256：按相对路径排序，逐文件混入 (路径 + 内容)。"""
    h = hashlib.sha256()
    for p in _iter_py_files():
        rel = p.relative_to(_HARNESS_DIR).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(p.read_bytes())
        h.update(b"\x00")
    return h.hexdigest()
