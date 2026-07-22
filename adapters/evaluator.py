"""指标提取替换。见 IMPLEMENTATION-P2.md §4.8 第 2 点。

AIDE 默认从 stdout/返回值解析一个 float。我们改成：
  节点脚本调 harness.evaluate_dev() → 写 runs 表 → 返回 run_id；
  本模块从 runs 读回 dev_score（裸 float，仅供搜索排序）交给搜索器。

**永远不返回 test 阶段的分数给搜索器**（搜索器看不到 test，I11/I7）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Union


def dev_score_from_run(run_id: str, metric: str, results_path: Path,
                       subject: Optional[str] = None) -> float:
    """读回单次 dev run 的分数。若该 run 是 test 阶段则拒绝。"""
    from core import results

    rec = results.get_run(run_id, db=results_path)
    if rec.phase != "dev":
        raise ValueError(
            f"搜索器只能看 dev 分数；run {run_id} 是 {rec.phase} 阶段（I11/I7）")
    return results.search_metric_value(run_id, metric, subject=subject, db=results_path)


def pick_primary_run(run_ids: Union[str, Dict[str, str]], primary_pipeline: Optional[str]
                     ) -> str:
    """evaluate_dev 返回 {pipeline_name: run_id}；挑出主 pipeline 的 run_id。"""
    if isinstance(run_ids, str):
        return run_ids
    if primary_pipeline and primary_pipeline in run_ids:
        return run_ids[primary_pipeline]
    # 默认取第一个（确定性：按 key 排序）
    return run_ids[sorted(run_ids)[0]]
