"""对抗环节（放最后）。见 IMPLEMENTATION-P4.md §4.7。

三个**独立**调用，用不同模型，互相看不到对方的输出：
  Q1「给出这个课题必然失败的最强理由」
  Q2「找出已经做过它的论文」— 必须返回 corpus key，找不到就明确说找不到
  Q3「指出这个计划最弱的一环」

**不汇总、不投票、不打分。三份报告原样呈给人。** 对抗而非共识 —— 这一步的作用是给人
提供反面材料，不是让系统自己下结论（§4.7）。多智能体"讨论"在标准聚合下构成一个鞅，
期望正确率不随轮次提升，故这里刻意不做讨论/共识（§2）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

# llm_fn(model, question, draft) -> 一段回答文本
RedTeamLLMFn = Callable[[str, str, object], str]

_QUESTIONS = [
    ("Q1_failure", "给出这个课题必然失败的最强理由。"),
    ("Q2_prior_work", "找出已经做过这个课题的论文。必须返回 corpus key（如 ang2012csp）；"
                      "如果找不到，就明确说'找不到'，不要编造。"),
    ("Q3_weakest_link", "指出这个研究计划里最弱的一环。"),
]


@dataclass
class RedTeamReport:
    question_id: str
    model: str
    question: str
    response: str
    corpus_keys: List[str] = field(default_factory=list)   # 仅 Q2：已解析出 ID 的 key


def _extract_corpus_keys(text: str, corpus_db: Optional[Path]) -> List[str]:
    """从回答里挑出能在 corpus 解析出 ID 的 token（Q2 专用）。找不到就为空。"""
    if corpus_db is None:
        return []
    from core import corpus

    import re
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text)
    keys: List[str] = []
    for tok in dict.fromkeys(tokens):
        if corpus.has_resolved_id(corpus_db, tok):
            keys.append(tok)
    return keys


def red_team(draft, corpus_db: Optional[Path], llm_fn: RedTeamLLMFn,
             models: List[str]) -> List[RedTeamReport]:
    """三个独立问题，每个一次独立 llm_fn 调用，用不同模型；不汇总、不投票、不打分。"""
    if not models:
        raise ValueError("至少需要一个红队模型")
    reports: List[RedTeamReport] = []
    for i, (qid, question) in enumerate(_QUESTIONS):
        model = models[i % len(models)]
        # 每次调用只喂 (model, question, draft)；绝不传入别的问题的回答 —— 互不可见。
        response = llm_fn(model, question, draft)
        keys = _extract_corpus_keys(response, corpus_db) if qid == "Q2_prior_work" else []
        reports.append(RedTeamReport(qid, model, question, response, keys))
    return reports
