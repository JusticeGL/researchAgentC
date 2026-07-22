"""分轴独立打分。见 IMPLEMENTATION-P4.md §4.6。

**五个轴，独立呈现，绝不合并成一个总分（I27）。** 合并分数会被行文流畅度主导，
且掩盖各轴真实信息。四个轴主体是机械计算，只有"预期效应量"靠模型估计 ——
而且要求它必须给出依据文献的 corpus key（否则该轴不评分）。

  novelty        ← novelty_gate 的 verdict，机械
  feasibility    ← feasibility 报告，主体机械
  measurability  ← 契约填充完整度，完全机械（缺几个字段就扣几分）
  data_access    ← 数据集是否公开可下载，机械
  effect_size    ← LLM 估计，必须附依据文献的 corpus key

**输出是一张五列的表，不是一个数。** 排序由人在卡点上做，或用一个**你自己写在配置里**
的显式加权公式 —— 但那个权重必须是你写的，不是模型给的。本模块不提供任何合并方法。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ideation.fill import REQUIRED_FIELDS

# effect_size_fn(draft) -> {"value": float, "evidence": [corpus key,...], "rationale": str}
EffectSizeFn = Callable[[object], dict]

AXES = ("novelty", "feasibility", "measurability", "data_access", "effect_size")


@dataclass
class AxisScore:
    axis: str
    value: Optional[float]              # None 表示该轴不评分（如证据不足）
    label: str = ""
    rationale: str = ""
    evidence: List[str] = field(default_factory=list)
    scored: bool = True


@dataclass
class AxisScores:
    """五轴，**没有** overall/total/combined 字段 —— 结构上不存在合并分（I27）。"""
    novelty: AxisScore
    feasibility: AxisScore
    measurability: AxisScore
    data_access: AxisScore
    effect_size: AxisScore

    def as_rows(self) -> List[AxisScore]:
        """五列的表，供卡点展示。**不返回单一数字，不做加权。**"""
        return [self.novelty, self.feasibility, self.measurability,
                self.data_access, self.effect_size]


_NOVELTY_VALUE = {"novel": 1.0, "incremental": 0.5, "done": 0.0}
_FEASIBILITY_VALUE = {"feasible": 1.0, "infeasible": 0.0}


def _novelty_axis(novelty) -> AxisScore:
    verdict = getattr(novelty, "verdict", "unknown")
    if not getattr(novelty, "scored", True) or verdict == "unknown":
        return AxisScore("novelty", None, "unknown",
                         "检索未命中，需人工判断（不评分）",
                         list(getattr(novelty, "evidence", [])), scored=False)
    return AxisScore("novelty", _NOVELTY_VALUE.get(verdict, 0.0), verdict,
                     getattr(novelty, "notes", ""),
                     list(getattr(novelty, "evidence", [])))


def _feasibility_axis(feas) -> AxisScore:
    verdict = getattr(feas, "verdict", "unknown")
    if verdict == "unknown":
        return AxisScore("feasibility", None, "unknown",
                         "; ".join(getattr(feas, "reasons", [])), scored=False)
    return AxisScore("feasibility", _FEASIBILITY_VALUE.get(verdict, 0.0), verdict,
                     "; ".join(getattr(feas, "reasons", [])))


def _measurability_axis(draft) -> AxisScore:
    """完全机械：填满的必填字段占比。"""
    missing = set(getattr(draft, "missing_fields", []) or [])
    filled = [f for f in REQUIRED_FIELDS if f not in missing]
    value = len(filled) / len(REQUIRED_FIELDS)
    return AxisScore("measurability", round(value, 4),
                     "complete" if not missing else "incomplete",
                     f"{len(filled)}/{len(REQUIRED_FIELDS)} 必填字段已填"
                     + (f"，缺：{sorted(missing)}" if missing else ""))


def _data_access_axis(feas) -> AxisScore:
    """机械：数据集是否在 harness 支持列表（≈ 公开可下载）。"""
    ok = bool(getattr(feas, "mechanical", {}).get("datasets_supported"))
    return AxisScore("data_access", 1.0 if ok else 0.0,
                     "public" if ok else "restricted",
                     "数据集在 harness 支持列表" if ok else "数据集不受支持或未指定")


def _effect_size_axis(draft, effect_size_fn: Optional[EffectSizeFn]) -> AxisScore:
    """唯一 LLM 轴：必须附依据文献的 corpus key，否则不评分。"""
    if effect_size_fn is None:
        return AxisScore("effect_size", None, "unscored",
                         "未提供 effect_size 估计器（需依据文献的 corpus key）", scored=False)
    try:
        out = dict(effect_size_fn(draft) or {})
    except Exception as e:
        return AxisScore("effect_size", None, "error", str(e), scored=False)
    evidence = [k for k in (out.get("evidence") or []) if k]
    if not evidence:
        return AxisScore("effect_size", None, "unscored",
                         "效应量估计未附依据文献 corpus key，该轴不评分",
                         scored=False)
    return AxisScore("effect_size", out.get("value"), out.get("label", "estimated"),
                     out.get("rationale", ""), evidence)


def score(draft, novelty, feasibility,
          effect_size_fn: Optional[EffectSizeFn] = None) -> AxisScores:
    """五次独立取值，永不合并（I27）。"""
    return AxisScores(
        novelty=_novelty_axis(novelty),
        feasibility=_feasibility_axis(feasibility),
        measurability=_measurability_axis(draft),
        data_access=_data_access_axis(feasibility),
        effect_size=_effect_size_axis(draft, effect_size_fn),
    )
