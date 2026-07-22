"""上下文构建器。见 IMPLEMENTATION-P2.md §4.5。

按优先级注入，超预算从低优先级砍起：
  P1 契约关键字段：question / hypothesis / primary_metric / threshold /
                   direction / split_protocol / kill_criteria
  P2 active lessons 全部
  P3 父节点完整代码
  P4 该分支祖先链的结果表（node_id, change_description, dev_score），最多 K=15 条
  P5 兄弟节点的 change_description（供 agent 自己避重）

结束前 assert token_count <= budget_tokens（I14）。

**不注入**：全量 runs 表、其他分支的详细内容、历史 stdout、
上一次的完整报错（只给最后 50 行）。
"""
from __future__ import annotations

import re
from typing import List, Optional, Sequence

# 刻意不接受任何"全量 runs 表"参数：历史 run 的查询是 agent 的工具（results.query_runs），
# 不是 prompt 里的一段文本。见 test_context_excludes_full_runs_table。


def count_tokens(text: str) -> int:
    """粗略 token 估计：词数与 chars/4 取较大者。足够用于预算断言。"""
    words = len(re.findall(r"\S+", text))
    return max(words, len(text) // 4)


def _contract_block(contract) -> str:
    kc = contract.kill_criteria
    return (
        "## 契约关键字段\n"
        f"- question: {contract.question}\n"
        f"- hypothesis: {contract.hypothesis}\n"
        f"- primary_metric: {contract.primary_metric}\n"
        f"- success_threshold: {contract.success_threshold}\n"
        f"- direction: {contract.direction}\n"
        f"- split_protocol: {contract.split_protocol}\n"
        f"- kill_criteria: {kc}\n"
    )


def _lessons_block(active_lessons) -> str:
    if not active_lessons:
        return ""
    lines = ["## 经验台账（active）"]
    for l in active_lessons:
        lines.append(f"- [{l.kind}] {l.text}")
    return "\n".join(lines) + "\n"


def _parent_code_block(parent_code: str) -> str:
    if not parent_code:
        return ""
    return "## 父节点代码\n```python\n" + parent_code + "\n```\n"


def _ancestor_block(ancestor_rows, k: int) -> str:
    if not ancestor_rows:
        return ""
    lines = ["## 祖先链结果（node_id / change / dev_score）"]
    for r in list(ancestor_rows)[:k]:
        nid = r.get("node_id") if isinstance(r, dict) else r.node_id
        desc = r.get("change_description") if isinstance(r, dict) else r.change_description
        ds = r.get("dev_score") if isinstance(r, dict) else r.dev_score
        lines.append(f"- {nid}: {desc} (dev_score={ds})")
    return "\n".join(lines) + "\n"


def _siblings_block(sibling_descriptions) -> str:
    if not sibling_descriptions:
        return ""
    lines = ["## 兄弟节点改动（避重用）"]
    for d in sibling_descriptions:
        lines.append(f"- {d}")
    return "\n".join(lines) + "\n"


def _last_error_block(last_error: str, max_lines: int = 50) -> str:
    if not last_error:
        return ""
    tail = "\n".join(last_error.splitlines()[-max_lines:])
    return "## 上次报错（最后 50 行）\n```\n" + tail + "\n```\n"


def build_context(contract, *, parent_code: str = "",
                  active_lessons: Sequence = (),
                  ancestor_rows: Sequence = (),
                  sibling_descriptions: Sequence = (),
                  last_error: str = "",
                  budget_tokens: int = 24000,
                  ancestor_k: int = 15) -> str:
    """按优先级拼装上下文，超预算从低优先级砍起，最终 assert 不超预算。"""
    p1 = _contract_block(contract)
    p2 = _lessons_block(active_lessons)
    p3 = _parent_code_block(parent_code)
    p4 = _ancestor_block(ancestor_rows, ancestor_k)
    p5 = _siblings_block(sibling_descriptions)
    p_err = _last_error_block(last_error)

    # 优先级从高到低：P1 必留；其余按 P2 > P3 > P4 > P5 > err 依次可砍
    ordered = [("P1", p1), ("P2", p2), ("P3", p3), ("P4", p4), ("P5", p5), ("ERR", p_err)]

    def assemble(blocks):
        return "\n".join(b for _, b in blocks if b)

    # 从低优先级开始丢弃，直到不超预算
    kept = list(ordered)
    while count_tokens(assemble(kept)) > budget_tokens and len(kept) > 1:
        # 丢弃最低优先级的非 P1 块
        for i in range(len(kept) - 1, 0, -1):
            if kept[i][1]:
                kept.pop(i)
                break
        else:
            break

    result = assemble(kept)
    # P1 单独超预算的兜底：截断 parent_code 等已被砍光后仍超，硬截断
    if count_tokens(result) > budget_tokens:
        result = result[: budget_tokens * 4]
    assert count_tokens(result) <= budget_tokens, "上下文超出 token 预算（I14）"
    return result
