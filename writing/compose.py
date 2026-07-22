"""写作编排。见 IMPLEMENTATION-P3.md §4.5。

写作 agent 只能看到 WritingInput 里的东西，**不给结果库查询工具**——
给了它就会去翻数据找故事，这正是要防的（HARKing）。它拿到的是已经生成好的
results_summary（确定性代码产出），任务只是"填解释性文字"，不是"找创新点"。

数字用 {{run:...}}/{{agg:...}} 模板；引用用 \\cite{key}；强论断句必须带 [claim:id]。
Abstract 首句由模板 + hypothesis_held 确定性渲染，不经 LLM。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

# 写作 agent 允许使用的工具白名单。**刻意不包含任何结果库查询工具。**
# 见 test_writing_agent_has_no_results_query_tool。
ALLOWED_AGENT_TOOLS = ["emit_section_text", "cite_lookup"]

# 明确禁止出现在写作 agent 工具清单里的能力（查库 = 找故事）
FORBIDDEN_AGENT_TOOLS = [
    "query_runs", "get_run", "get_metric", "agg", "search_metric_value",
    "results", "results_query", "sql",
]

_TOKEN_RE = re.compile(r"<<([A-Z_]+)>>")


def agent_tools() -> List[str]:
    return list(ALLOWED_AGENT_TOOLS)


def assert_no_results_query_tool(tools: Optional[List[str]] = None) -> None:
    tools = tools if tools is not None else agent_tools()
    bad = [t for t in tools if t in FORBIDDEN_AGENT_TOOLS]
    if bad:
        raise AssertionError(f"写作 agent 不得拥有结果库查询工具：{bad}")


# ---------------------------------------------------------------------------
# WritingInput（agent 只能看到这些）
# ---------------------------------------------------------------------------
try:
    from pydantic import BaseModel

    class WritingInput(BaseModel):
        model_config = {"arbitrary_types_allowed": True, "frozen": True}

        hypothesis: str
        hypothesis_held: Optional[bool]
        template: str
        results_summary: str
        claim_ids: List[str]
        figure_ids: List[str]
        lessons: List[str]
except Exception:  # pragma: no cover - pydantic 恒在
    WritingInput = None  # type: ignore


def writing_task_description(contract, hypothesis_held: Optional[bool],
                            template: str) -> str:
    """agent 的任务描述——**不要改写这段话**（§4.5）。"""
    held = {True: "成立", False: "不成立", None: "无结论"}[hypothesis_held]
    return (
        f"契约里的假设是「{contract.hypothesis}」，实验结论是「{held}」。\n"
        f"按 {template} 的骨架，填写解释性文字。\n"
        f"你不能提出契约里没有的结论。你不能写任何数字——数字用 "
        f"{{{{run:...}}}} / {{{{agg:...}}}} 模板。\n"
        f"你不能写任何引用——引用用 \\cite{{key}}，key 必须来自提供的文献库。")


# ---------------------------------------------------------------------------
# 结果摘要（确定性代码生成，不是 agent 查库）
# ---------------------------------------------------------------------------
def build_results_summary(contract, tags: Dict[str, List[str]],
                          results_path: Path) -> str:
    """由确定性代码从结果库生成结果表文本，供 agent 作为上下文（不给它查询能力）。

    用 results.search_metric_value（sanctioned 的裸值读取，非 Metric.unwrap —— 遵守 I3）
    聚合成 mean/std，仅作 agent 上下文；论文正文数字仍只能经 render 的 {{agg:...}} 产出。
    """
    import statistics

    from core import results as results_mod

    lines = ["结果摘要（自动生成，只读）："]
    for tag, run_ids in tags.items():
        vals = []
        for rid in run_ids:
            try:
                vals.append(results_mod.search_metric_value(
                    rid, contract.primary_metric, db=results_path))
            except Exception:
                pass
        if not vals:
            lines.append(f"- {tag}: 无数据")
            continue
        mean = statistics.fmean(vals)
        std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        lines.append(f"- {tag}: n={len(vals)}, mean={mean:.4f}, std={std:.4f}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 模板 token 填充（确定性）
# ---------------------------------------------------------------------------
def build_token_map(contract, decision, *, contract_hash: str,
                    stats: Optional[dict] = None,
                    lessons: Optional[List] = None,
                    include_ablation: bool = True) -> Dict[str, str]:
    from writing import templates

    stats = stats or {}
    lessons = list(lessons or [])
    deadends = [l for l in lessons if getattr(l, "kind", None) == "deadend"]

    def bullets(items, empty="（无）"):
        items = list(items)
        return "\n".join(f"- {t}" for t in items) if items else empty

    limitations_items = [getattr(l, "text", str(l)) for l in lessons]
    if not limitations_items:
        limitations_items = ["本研究在预注册协议内完成，未见需单列的额外限制；"
                             "台账为空表示搜索过程未积累失败教训。"]

    dataset = contract.datasets[0] if contract.datasets else "数据集"
    ablation_section = (
        "移除 CSP 后准确率下降到 {{agg:abl_a1.acc}}，"
        "说明性能来自空间滤波而非分类器 [claim:mech_a1]。"
        if include_ablation else "（本文未包含消融。）")

    return {
        "TITLE": stats.get("title", contract.question[:80]),
        "ABSTRACT_FIRST_SENTENCE": templates.abstract_first_sentence(
            contract, decision.hypothesis_held),
        "DATASET": dataset,
        "SPLIT": contract.split_protocol,
        "METRIC": contract.primary_metric,
        "HYPOTHESIS": contract.hypothesis,
        "NOVELTY_NOTE": contract.novelty_note or "（契约未填 novelty_note）",
        "CONTRACT_HASH": (contract_hash or "")[:16],
        "N_NODES": str(stats.get("n_nodes", "N/A")),
        "BUDGET_USED": stats.get("budget_used", "N/A"),
        "DEV_TEST_GAP": stats.get("dev_test_gap",
                                  "dev/test gap 不适用（未开测试集）。"),
        "DEADEND_DIRECTIONS": bullets(getattr(l, "text", str(l)) for l in deadends),
        "LIMITATIONS": bullets(limitations_items),
        "ABLATION_SECTION": ablation_section,
    }


def fill_tokens(template_text: str, token_map: Dict[str, str]) -> str:
    """替换 <<TOKEN>>。未提供的 token 留空并附注，绝不遗留 <<...>> 到成品。"""
    def repl(m):
        key = m.group(1)
        return token_map.get(key, f"[未填充:{key}]")
    return _TOKEN_RE.sub(repl, template_text)


def compose_paper(contract, decision, *, contract_hash: str,
                  stats: Optional[dict] = None, lessons: Optional[List] = None,
                  out_path: Optional[Path] = None,
                  confirm_terminal_state: Optional[str] = None) -> dict:
    """选模板 → 填 token → 写出 paper markdown + 同名 .meta.json（供 C16）。

    只做确定性 token 填充；agent-fill 区域保留为 HTML 注释（不影响渲染/检查）。
    """
    from writing import templates

    tmpl = templates.load_template(decision.template)
    include_ablation = decision.template == "positive"
    token_map = build_token_map(contract, decision, contract_hash=contract_hash,
                                stats=stats, lessons=lessons,
                                include_ablation=include_ablation)
    markdown = fill_tokens(tmpl, token_map)

    meta = {
        "template": decision.template,
        "hypothesis_held": decision.hypothesis_held,
        "confirm_terminal_state": confirm_terminal_state,
        "contract_id": contract.contract_id,
        "contract_hash": contract_hash,
    }
    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
        out_path.with_suffix(out_path.suffix + ".meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"markdown": markdown, "meta": meta}
