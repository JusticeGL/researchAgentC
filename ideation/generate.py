"""独立生成器。见 IMPLEMENTATION-P4.md §4.1。

I24 的实现：每个 (model, seed) 组合是一个**独立**的 llm_fn 调用，
参数里根本没有"其他想法"这一项，函数内部也不把已生成的想法回灌给下一次调用。
价值全在跨模型/跨种子的分布差异，不在对话 —— 依赖式采样会让每轮熵持续下降，
而多样性恰恰是这一步唯一需要的东西（§2）。

LLM 调用以依赖注入传入（llm_fn）：离线测试注入假模型；live 时注入真实后端。
去重**直接复用**第二期 loop/dedup.py（§4.2），不写第二套。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional

# llm_fn(model, prompt, temperature, seed) -> 一段想法文本
LLMFn = Callable[[str, str, float, Optional[str]], str]


@dataclass(frozen=True)
class RawIdea:
    idea_id: str
    model: str
    seed: Optional[str]      # 种子文献 key 或领域描述；None 表示纯 domain 生成
    text: str
    query_hint: Optional[str] = None


def _idea_id(model: str, seed: Optional[str], text: str) -> str:
    h = hashlib.sha1(f"{model}|{seed}|{text}".encode("utf-8")).hexdigest()[:12]
    return f"idea_{h}"


def _build_prompt(domain: Optional[str], seed: Optional[str], k: int) -> str:
    """纯函数：只由 (domain, seed) 决定，绝不含任何"其他想法"。

    这是 I24 的关键 —— 提示词的构造无法看到别的生成结果。
    """
    parts = [
        "你是一个 BCI/EEG 方向的研究想法生成器。",
        "提出一个**可执行**的研究计划想法，要能落到具体数据集、划分协议、"
        "基线、主指标、成功阈值、失败判据上。",
        f"请给出 {k} 条彼此不同的想法，每条一段，用空行分隔。",
    ]
    if domain:
        parts.append(f"领域方向：{domain}")
    if seed:
        parts.append(f"种子文献（corpus key）：{seed}")
    return "\n".join(parts)


def _split_ideas(raw: str) -> List[str]:
    """把一次调用返回的文本切成多条想法（按空行）。"""
    chunks = [c.strip() for c in raw.split("\n\n")]
    return [c for c in chunks if c]


def generate(seed_papers: Optional[List[str]], domain: Optional[str],
             models: List[str], n_per_model: int, temperature: float,
             llm_fn: LLMFn) -> List[RawIdea]:
    """每个 (model, seed) 组合一个独立调用。

    不共享 context，不传入其他人的输出，不做多轮（I24）。
    seed_papers 为空/None 时退化为纯 domain 生成，每个模型仍是独立调用。
    """
    if not models:
        raise ValueError("至少需要一个生成器模型")
    seeds: List[Optional[str]] = list(seed_papers) if seed_papers else [None]

    out: List[RawIdea] = []
    for model in models:
        for seed in seeds:
            # 每次调用的输入只有 (domain, seed, n, temperature)；
            # 已生成的 out 绝不作为参数传入 —— 这是 I24 的字面实现。
            prompt = _build_prompt(domain, seed, n_per_model)
            raw = llm_fn(model, prompt, temperature, seed)
            for text in _split_ideas(raw)[:n_per_model]:
                out.append(RawIdea(
                    idea_id=_idea_id(model, seed, text),
                    model=model, seed=seed, text=text))
    return out


# ---------------------------------------------------------------------------
# 去重：复用第二期 loop/dedup.py（§4.2），不写第二套
# ---------------------------------------------------------------------------
@dataclass
class _DedupShim:
    """把 RawIdea 适配成 loop.dedup.check_duplicate 期望的节点接口。"""
    node_id: str
    change_description: str
    dev_score: Optional[float] = None
    status: str = "ok"


def dedup_ideas(ideas: List[RawIdea], threshold: float = 0.92) -> List[RawIdea]:
    """用第二期的 embedding + 阈值逻辑去重。保序，保留每个语义簇的首个想法。"""
    from loop import dedup

    kept: List[RawIdea] = []
    shims: List[_DedupShim] = []
    for idea in ideas:
        res = dedup.check_duplicate(idea.text, shims, threshold=threshold)
        if res.is_duplicate:
            continue
        kept.append(idea)
        shims.append(_DedupShim(idea.idea_id, idea.text))
    return kept
