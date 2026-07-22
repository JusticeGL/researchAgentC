"""节点选择策略。见 IMPLEMENTATION-P2.md §4.2。

UCB 风格：score(n) = normalized(n.dev_score) + c * sqrt(log(N) / (1 + n.expansion_count))
  - 只从 status in (ok, confirmed) 的节点里选
  - fluke 节点不选，但保留在树里（它是有信息量的负面结果）
  - 返回 None 表示应该 draft 一个新根节点

c 写进配置，默认 0.5。参数值不重要，**有一个显式的探索项**才重要。
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence

SELECTABLE = {"ok", "confirmed"}


def select_node(nodes: Sequence, ucb_c: float = 0.5) -> Optional[object]:
    candidates = [n for n in nodes if n.status in SELECTABLE]
    if not candidates:
        return None  # draft 新根节点

    scores = [n.dev_score for n in candidates if n.dev_score is not None]
    lo = min(scores) if scores else 0.0
    hi = max(scores) if scores else 1.0
    span = (hi - lo) or 1.0

    total_expansions = sum(n.expansion_count for n in candidates)
    N = total_expansions + 1

    def ucb(n):
        norm = 0.0 if n.dev_score is None else (n.dev_score - lo) / span
        explore = ucb_c * math.sqrt(math.log(N + 1) / (1 + n.expansion_count))
        return norm + explore

    return max(candidates, key=ucb)
