"""机械可行性计算。见 IMPLEMENTATION-P4.md §4.4。

**这一轴大部分可以算，不用问模型。** 报告把机械部分和 LLM 部分分开呈现：

机械（对照真实资源，I25）：
  - 数据集是否在 harness 支持列表里          → 读 harness.data 的别名表
  - 被试数 / 通道数 / 采样率 / 类别数          → 从 MOABB metadata 读（best-effort）
  - 单次训练时间估计                           → 结果库里同契约历史 run 的 wall_clock 中位数
  - 总成本估计                                 → 复用第二期 loop/cost.py
  - 与 draft.budget 比对                       → 悲观档超预算即 infeasible

LLM（唯一需要模型的一项）：实现难度（是否依赖难获取/未开源的组件）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

# difficulty_fn(draft) -> {"level": "low|medium|high", "note": str}
DifficultyFn = Callable[[object], dict]
MetadataFn = Callable[[str], Optional[dict]]

VERDICTS = ("feasible", "infeasible", "unknown")


@dataclass
class FeasibilityReport:
    verdict: str                                  # feasible|infeasible|unknown
    mechanical: Dict[str, object] = field(default_factory=dict)
    llm: Dict[str, object] = field(default_factory=dict)   # 与机械部分分开呈现
    single_run_minutes: Optional[float] = None
    cost: Optional[object] = None                 # loop.cost.CostEstimate
    reasons: List[str] = field(default_factory=list)


def _supported_datasets() -> set:
    """harness 支持的数据集名 —— 直接读 harness.data 的别名表，不修改 harness（避免动 harness_hash / I6）。"""
    try:
        from harness import data as hdata
        return set(hdata._DATASET_ALIASES.keys())
    except Exception:
        return set()


def _read_dataset_metadata(name: str) -> Optional[dict]:
    """best-effort 从 MOABB 读元数据；moabb 缺失/离线时返回 None（不报错）。"""
    try:
        from harness import data as hdata

        ds = hdata.get_dataset(name)
        subjects = list(getattr(ds, "subject_list", []) or [])
        meta: dict = {"n_subjects": len(subjects) or None}
        ev = getattr(ds, "event_id", None)
        if ev:
            meta["n_classes"] = len(ev)
        return meta
    except Exception:
        return None


def _get(draft, name, default=None):
    if isinstance(draft, dict):
        return draft.get(name, default)
    return getattr(draft, name, default)


def feasibility(draft, results_path: Optional[Path] = None,
                cfg: Optional[dict] = None,
                difficulty_fn: Optional[DifficultyFn] = None,
                metadata_fn: Optional[MetadataFn] = None) -> FeasibilityReport:
    """机械可行性 + 一项 LLM 实现难度。draft 需带 .datasets / .budget / .contract_id。"""
    from loop import cost

    metadata_fn = metadata_fn or _read_dataset_metadata
    datasets = list(_get(draft, "datasets", []) or [])
    supported = _supported_datasets()

    mechanical: Dict[str, object] = {}
    reasons: List[str] = []

    # 数据集支持性
    unsupported = [d for d in datasets if d not in supported] if supported else []
    dataset_ok = bool(datasets) and not unsupported
    mechanical["datasets"] = datasets
    mechanical["datasets_supported"] = dataset_ok
    if not datasets:
        reasons.append("draft 未指定数据集")
    if unsupported:
        reasons.append(f"数据集不在 harness 支持列表：{unsupported}")

    # 数据集元数据（best-effort）
    mechanical["dataset_metadata"] = {d: metadata_fn(d) for d in datasets}

    # 单次 run 时长：来自结果库真实 wall_clock 中位数（I25）
    single_run_minutes = None
    contract_id = _get(draft, "contract_id")
    if results_path is not None and contract_id:
        single_run_minutes = cost.median_single_run_minutes(Path(results_path), contract_id)
        mechanical["single_run_minutes_source"] = "results_db_median"
    mechanical["single_run_minutes"] = single_run_minutes

    # 成本预估 + 预算比对
    cost_est = None
    budget = _get(draft, "budget")
    over_budget = False
    if cfg is not None and single_run_minutes is not None and budget is not None:
        try:
            cost_est = cost.estimate(draft, cfg, single_run_minutes)
            over_budget = cost_est.exceeds(budget)
            mechanical["cost_optimistic"] = cost_est.optimistic._asdict()
            mechanical["cost_median"] = cost_est.median._asdict()
            mechanical["cost_pessimistic"] = cost_est.pessimistic._asdict()
            mechanical["over_budget"] = over_budget
            if over_budget:
                reasons.append("悲观档成本超出 draft.budget")
        except Exception as e:  # cfg 结构不全等
            mechanical["cost_error"] = str(e)

    # LLM 部分（唯一需要模型的一项）：实现难度
    llm: Dict[str, object] = {}
    if difficulty_fn is not None:
        try:
            llm = dict(difficulty_fn(draft) or {})
        except Exception as e:
            llm = {"error": str(e)}

    # 综合裁决（只用机械信号；LLM 难度仅呈现，不决定 feasible/infeasible）
    if over_budget or unsupported:
        verdict = "infeasible"
    elif not datasets:
        verdict = "unknown"
    else:
        verdict = "feasible"

    return FeasibilityReport(
        verdict=verdict, mechanical=mechanical, llm=llm,
        single_run_minutes=single_run_minutes, cost=cost_est, reasons=reasons)
