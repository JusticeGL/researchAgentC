"""成本预估。见 IMPLEMENTATION-P2.md §4.9。

契约批准（第一期 contract_review 卡点）时展示三档：乐观 / 中位 / 悲观。
single_run_minutes 应来自第一期真实 baseline run 的中位数，不要拍脑袋。
"""
from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass
from typing import List, Optional
from pathlib import Path

CostBand = namedtuple("CostBand", ["usd", "gpu_hours", "wall_clock_h"])


@dataclass
class CostEstimate:
    optimistic: CostBand
    median: CostBand
    pessimistic: CostBand

    def exceeds(self, budget) -> bool:
        """悲观档任一维度超预算即视为有超支风险。"""
        return (self.pessimistic.usd > budget.usd
                or self.pessimistic.gpu_hours > budget.gpu_hours
                or self.pessimistic.wall_clock_h > budget.wall_clock_h)


def median_single_run_minutes(results_path: Path, contract_id: str,
                              default: float = 5.0) -> float:
    """从已有 baseline run 的 wall_clock_s 取中位数（分钟）。无数据则回退 default。"""
    from core import results

    runs = results.query_runs(db=results_path, contract_id=contract_id, phase="dev")
    mins = sorted(r.wall_clock_s / 60.0 for r in runs
                  if getattr(r, "wall_clock_s", None))
    if not mins:
        return default
    return mins[len(mins) // 2]


def estimate(contract, cfg: dict, single_run_minutes: float,
             usd_per_1k_tokens: float = 0.01, gpu_hourly_usd: float = 2.0,
             avg_tokens_per_step: int = 8000) -> CostEstimate:
    """三档成本预估。cfg 为 config/loop.yaml 解析结果。"""
    max_nodes = cfg["search"]["max_nodes"]
    drafts = cfg["aide"]["num_drafts"]
    debug_depth = cfg["aide"]["max_debug_depth"]
    n_seeds = cfg["confirm"]["n_seeds"]

    steps_per_node = drafts + debug_depth

    def band(node_factor: float, confirm_count: int) -> CostBand:
        nodes = max_nodes * node_factor
        llm_tokens = nodes * steps_per_node * avg_tokens_per_step
        llm_usd = llm_tokens / 1000.0 * usd_per_1k_tokens
        train_gpu_h = nodes * single_run_minutes / 60.0
        confirm_gpu_h = confirm_count * n_seeds * single_run_minutes / 60.0
        gpu_h = train_gpu_h + confirm_gpu_h
        usd = llm_usd + gpu_h * gpu_hourly_usd
        wall_h = gpu_h  # 串行近似
        return CostBand(round(usd, 2), round(gpu_h, 3), round(wall_h, 3))

    return CostEstimate(
        optimistic=band(0.4, 1),
        median=band(0.7, 2),
        pessimistic=band(1.0, 4),
    )
