"""AI 参与度披露。见 IMPLEMENTATION-P3.md §4.6。

AI_CONTRIBUTION.md **确定性生成**，来源全部是 audit 表和结果库 —— 不经 LLM。
多数会议和期刊要求披露 AI 参与程度；这份文档是原料，不是终稿。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional


def _audit_rows(audit_db: Path):
    import sqlite3

    con = sqlite3.connect(audit_db)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT gate_type, subject_id, field, decision, reason, decided_at "
            "FROM audit ORDER BY decided_at").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _models_from_runs(results_path: Optional[Path], contract_id: Optional[str]) -> List[str]:
    """从 runs 汇总用过的模型标识（若有 env_hash / artifacts 记录则列出；否则标注未知）。"""
    if results_path is None or not Path(results_path).exists():
        return ["（无结果库）"]
    from core import results

    kwargs = {"db": results_path}
    if contract_id:
        kwargs["contract_id"] = contract_id
    runs = results.query_runs(**kwargs)
    if not runs:
        return ["（无 run）"]
    # 本系统不直接存 LLM 模型名；披露 env_hash 作为可审计指纹
    env = sorted({r.env_hash for r in runs if r.env_hash})
    return [f"env_hash={h[:12]}…" for h in env] or ["（未记录模型指纹）"]


def generate(contract, audit_db: Path, results_path: Optional[Path] = None,
             agent_model: str = "（未指定）",
             out_path: Optional[Path] = None) -> str:
    """从 audit + results 确定性生成 AI_CONTRIBUTION.md。"""
    rows = _audit_rows(audit_db) if audit_db and Path(audit_db).exists() else []
    models = _models_from_runs(results_path, getattr(contract, "contract_id", None))

    # 人工 reject / edit 摘要
    human_edits = [r for r in rows if r["decision"] in ("reject", "edit")]
    if human_edits:
        edit_lines = [
            f"- [{r['decided_at']}] {r['gate_type']}/{r['field']}={r['decision']}: "
            f"{r['reason'] or '（无理由）'}"
            for r in human_edits
        ]
    else:
        edit_lines = ["- （无 reject / edit 记录）"]

    gate_types = sorted({r["gate_type"] for r in rows})
    stages = [
        ("选题", "人工", "—"),
        ("实验搜索", f"agent ({agent_model})", "契约审批、新颖性裁决"),
        ("消融", f"agent ({agent_model})", "消融启动 / ablation_extension"),
        ("写作", f"agent ({agent_model})", "审稿意见处理 / citation_unverifiable"),
    ]

    lines = [
        "# AI 参与度披露",
        "",
        "## 各阶段分工",
        "| 阶段 | 执行者 | 人工卡点 |",
        "|---|---|---|",
    ]
    for stage, who, gate in stages:
        lines.append(f"| {stage} | {who} | {gate} |")

    lines += [
        "",
        "## 使用的模型",
        f"- 写作/搜索 agent 声明：`{agent_model}`",
        "- 运行环境指纹（来自结果库）：",
    ]
    for m in models:
        lines.append(f"  - {m}")

    lines += [
        "",
        "## 人工决策记录",
        "以下为 audit 表中所有 reject / edit 的摘要：",
        *edit_lines,
        "",
        f"audit 中出现的卡点类型：{', '.join(gate_types) if gate_types else '（空）'}",
        "",
        "## 契约",
        f"- contract_id: `{contract.contract_id}`",
        f"- version: `{contract.version}`",
        f"- content_hash: `{contract.content_hash or contract.content_hash_value()}`",
        f"- frozen_at: `{contract.frozen_at or '（未冻结）'}`",
        "",
        "## 复现",
        "```",
        "make reproduce",
        "```",
        "",
        "> 投稿前请查目标期刊的 AI 披露政策；本文档是原料，不是终稿。",
        "",
    ]
    text = "\n".join(lines)
    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    return text
