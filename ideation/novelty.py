"""检索式新颖性门。见 IMPLEMENTATION-P4.md §4.3。

新颖性**必须基于检索**（I23），绝不交给 LLM 直觉判断（§2）。流程：
  1. 从 idea 抽 2–3 个检索 query（这一步允许用 LLM：query_fn）
  2. 走 core.corpus.search 取 top-k 最近邻
  3. 对每篇最近邻问一次：这篇是否已经做了 idea 描述的事 → done|partial|different
  4. 返回 verdict（novel|incremental|done）+ evidence（corpus key 列表）

**核心那句：evidence 为空 → verdict 强制为 unknown，该轴不评分。**
"检索不到"和"不存在"是两回事：BCI 大量工作在付费墙后，corpus 未必覆盖。
让系统诚实地说"我不知道"，比让它猜"很新颖"有用得多（§4.3）。

I23 的强制点：evidence 里每个 key 都必须能在 corpus 解析出 DOI/arXiv ID
（corpus.has_resolved_id），解析不出的 key 不计入 evidence。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

# query_fn(idea_text) -> [query, ...]（可用 LLM）；judge_fn(idea_text, paper) -> done|partial|different
QueryFn = Callable[[str], List[str]]
JudgeFn = Callable[[str, object], str]
SearchFn = Callable[[Path, str, int], List[str]]

VERDICTS = ("novel", "incremental", "done", "unknown")
_JUDGE_VALUES = ("done", "partial", "different")


@dataclass
class NoveltyReport:
    verdict: str                       # novel|incremental|done|unknown
    evidence: List[str] = field(default_factory=list)      # 已解析出 ID 的 corpus key
    judgements: Dict[str, str] = field(default_factory=dict)  # key -> done|partial|different
    scored: bool = True                # unknown 时 False：该轴不评分
    notes: str = ""


def _default_search(db: Path, query: str, k: int) -> List[str]:
    from core import corpus

    return corpus.search(db, query, k=k)


def _default_queries(idea_text: str) -> List[str]:
    """无 query_fn 时的确定性回退：取首句 + 全文，最多两条。"""
    head = idea_text.strip().split("\n", 1)[0].strip()
    qs = [q for q in (head, idea_text.strip()) if q]
    return qs[:2]


def novelty_gate(idea, corpus_db: Path, k: int = 15,
                 query_fn: Optional[QueryFn] = None,
                 search_fn: Optional[SearchFn] = None,
                 judge_fn: Optional[JudgeFn] = None) -> NoveltyReport:
    """检索式新颖性判定。LLM 部分（query_fn/judge_fn）以依赖注入传入，离线可测。

    idea：带 .text 属性的对象（RawIdea）或直接 str。
    """
    from core import corpus

    text = idea if isinstance(idea, str) else idea.text
    query_fn = query_fn or _default_queries
    search_fn = search_fn or _default_search

    # 1) 抽 query → 2) 检索最近邻
    queries = [q for q in query_fn(text) if q and q.strip()]
    hit_keys: List[str] = []
    for q in queries:
        for key in search_fn(corpus_db, q, k):
            if key not in hit_keys:
                hit_keys.append(key)

    # I23：只有能解析出 DOI/arXiv ID 的 key 才算证据
    evidence = [key for key in hit_keys if corpus.has_resolved_id(corpus_db, key)]

    if not evidence:
        return NoveltyReport(
            verdict="unknown", evidence=[], judgements={}, scored=False,
            notes="检索未命中（或命中项无法解析出 ID），需人工判断。"
                  "检索不到不等于新颖 —— 该轴不评分。")

    # 3) 逐篇判定
    if judge_fn is None:
        # 没有裁判 → 有命中但无法判定关系，诚实降级为 unknown（仍不判 novel）
        return NoveltyReport(
            verdict="unknown", evidence=evidence, judgements={}, scored=False,
            notes="检索有命中但未提供关系裁判（judge_fn），需人工判断。")

    judgements: Dict[str, str] = {}
    for key in evidence:
        paper = corpus.get(corpus_db, key)
        verdict = judge_fn(text, paper)
        if verdict not in _JUDGE_VALUES:
            verdict = "different"
        judgements[key] = verdict

    # 4) 聚合：任一 done → done；否则有 partial → incremental；否则 novel
    if any(v == "done" for v in judgements.values()):
        verdict = "done"
    elif any(v == "partial" for v in judgements.values()):
        verdict = "incremental"
    else:
        verdict = "novel"
    return NoveltyReport(verdict=verdict, evidence=evidence, judgements=judgements,
                         scored=True, notes=f"基于 {len(evidence)} 篇最近邻的检索式判定。")
