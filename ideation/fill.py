"""契约填充层（核心）。见 IMPLEMENTATION-P4.md §4.5。

把想法填进第一期的 Contract schema。**必填字段任一为空 → status="incomplete"，
附缺失字段列表；incomplete 的想法不进排名，但保留在 ideas 表里（I22）。**

I22 用"结构上做不到"代替"规定不许做"：一个想法如果说不清用哪个数据集、什么划分、
成功阈值多少、什么条件算失败，它就不是一个可执行研究计划，系统不让它进候选池。

> success_threshold 和 kill_criteria 是最容易填不出来的两项，**这是特征不是 bug**。
> 让它停在 incomplete，报告给人，人来补 —— 或者放弃它。

reproduced_run_ids 此时必然为空（还没跑过），是唯一允许留空的必填字段：
它在实验阶段第一步被填上，freeze() 时才校验非空（第一期 §5.4）。
LLM 从想法文本抽结构字段这一步以依赖注入传入（extract_fn），本模块只做**完整性判定**。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# extract_fn(idea_text) -> 结构字段 dict（可用 LLM）
ExtractFn = Callable[[str], dict]

# 必填清单（缺一不可）。reproduced_run_ids 不在此列（唯一允许留空）。
REQUIRED_FIELDS = [
    "datasets", "split_protocol", "paradigm", "baselines", "primary_metric",
    "success_threshold", "direction", "n_seeds", "kill_criteria",
    "preregistered_ablations", "novelty_evidence",
]


@dataclass
class ContractDraft:
    """未冻结的契约草案。字段对齐第一期 Contract，但允许缺失（用于 incomplete 判定）。"""
    contract_id: str
    idea_id: str
    question: Optional[str] = None
    hypothesis: Optional[str] = None
    datasets: List[str] = field(default_factory=list)
    split_protocol: Optional[str] = None
    paradigm: Optional[str] = None
    baselines: List[dict] = field(default_factory=list)   # 每条至少 {name, cite_key, impl}
    primary_metric: Optional[str] = None
    success_threshold: Optional[float] = None
    direction: Optional[str] = None
    n_seeds: Optional[int] = None
    kill_criteria: List[str] = field(default_factory=list)
    preregistered_ablations: List[dict] = field(default_factory=list)  # 每条 {id, description, falsifies}
    novelty_evidence: List[str] = field(default_factory=list)
    novelty_note: str = ""
    budget: Optional[object] = None            # 供 feasibility/cost 使用；可后补
    reproduced_run_ids: List[str] = field(default_factory=list)  # 唯一允许留空
    status: str = "incomplete"
    missing_fields: List[str] = field(default_factory=list)


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def missing_required(draft: ContractDraft) -> List[str]:
    """返回缺失的必填字段名列表。空 → 完整。"""
    missing: List[str] = []
    for name in REQUIRED_FIELDS:
        if _is_empty(getattr(draft, name)):
            missing.append(name)
            continue
        # 结构性子校验
        if name == "baselines":
            if not any((b.get("name") and (b.get("impl") or b.get("cite_key")))
                       for b in draft.baselines):
                missing.append(name)
        elif name == "preregistered_ablations":
            # 每条必须写明"它能证伪什么"（falsifies）
            if not any((a.get("id") and (a.get("falsifies") or "").strip())
                       for a in draft.preregistered_ablations):
                missing.append(name)
    return missing


def fill_contract(idea, novelty, fields: Optional[dict] = None,
                  extract_fn: Optional[ExtractFn] = None,
                  contract_id: Optional[str] = None) -> ContractDraft:
    """把 idea + 结构字段填成 ContractDraft，并判定完整性（I22）。

    fields：已解析好的结构字段（离线/测试直接传）。若为 None 则用 extract_fn 从文本抽。
    novelty：NoveltyReport；其 evidence 直接作为 novelty_evidence（I23 已保证每条能解析出 ID）。
    """
    idea_id = idea if isinstance(idea, str) else idea.idea_id
    text = idea if isinstance(idea, str) else idea.text
    if fields is None:
        if extract_fn is None:
            fields = {}
        else:
            fields = dict(extract_fn(text) or {})

    novelty_evidence = list(getattr(novelty, "evidence", []) or [])
    draft = ContractDraft(
        contract_id=contract_id or f"draft_{idea_id}",
        idea_id=idea_id,
        question=fields.get("question"),
        hypothesis=fields.get("hypothesis"),
        datasets=list(fields.get("datasets", []) or []),
        split_protocol=fields.get("split_protocol"),
        paradigm=fields.get("paradigm"),
        baselines=list(fields.get("baselines", []) or []),
        primary_metric=fields.get("primary_metric"),
        success_threshold=fields.get("success_threshold"),
        direction=fields.get("direction"),
        n_seeds=fields.get("n_seeds") or (fields.get("stat_plan") or {}).get("n_seeds"),
        kill_criteria=list(fields.get("kill_criteria", []) or []),
        preregistered_ablations=list(fields.get("preregistered_ablations", []) or []),
        novelty_evidence=novelty_evidence,
        novelty_note=fields.get("novelty_note", getattr(novelty, "notes", "")),
        budget=fields.get("budget"),
    )
    draft.missing_fields = missing_required(draft)
    draft.status = "complete" if not draft.missing_fields else "incomplete"
    return draft


def rankable(drafts: List[ContractDraft]) -> List[ContractDraft]:
    """只有 complete 的 draft 进排名（I22）。incomplete 的仍保留在库里，但这里被过滤掉。"""
    return [d for d in drafts if d.status == "complete"]
