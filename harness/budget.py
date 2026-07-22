"""预算记账。见 IMPLEMENTATION.md §5.3。

记 wall_clock / gpu_hours / 估算 cost。超单节点上限抛 BudgetExceeded。
纯 Python，不依赖 MOABB，可离线测试。
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional


class BudgetExceeded(Exception):
    pass


@dataclass
class BudgetUsage:
    wall_clock_s: float = 0.0
    gpu_hours: float = 0.0
    cost_usd: float = 0.0


@contextmanager
def budget(contract, node_id: Optional[str] = None, gpu_hourly_usd: float = 2.0,
           has_gpu: bool = False):
    """上下文管理器：进入记开始时间，退出算耗时。

    超单节点 GPU 上限（contract.budget.per_node_gpu_hours）抛 BudgetExceeded。
    CPU 环境 gpu_hours=0；cost 以 gpu_hours * 单价估算。
    """
    usage = BudgetUsage()
    start = time.time()
    try:
        yield usage
    finally:
        elapsed = time.time() - start
        usage.wall_clock_s = elapsed
        usage.gpu_hours = (elapsed / 3600.0) if has_gpu else 0.0
        usage.cost_usd = usage.gpu_hours * gpu_hourly_usd
        per_node = getattr(getattr(contract, "budget", None), "per_node_gpu_hours", None)
        if per_node is not None and usage.gpu_hours > per_node:
            raise BudgetExceeded(
                f"节点 {node_id} 用了 {usage.gpu_hours:.3f} GPU-h > 单节点上限 {per_node} GPU-h"
            )
